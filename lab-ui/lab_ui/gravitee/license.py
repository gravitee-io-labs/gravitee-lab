"""The Gravitee license (license3j, signed by GraviteeSource), read only: what it holds, whether its signature is
genuine, and the features its tier and packs give according to the license model shipped in the product images; the
packs' names a feature is said by; its status as the state says it, without Docker."""

import base64
import datetime
import hashlib
import uuid

from ..docker import compose, envfiles
from ..paths import ENV_FILE

# GraviteeSource public key (gravitee-node License3J), X.509 SubjectPublicKeyInfo after "RSA\0"
PUBLIC_KEY = bytes.fromhex(
    "30820122300d06092a864886f70d01010105000382010f003082010a0282010100d859ecb627f2202f7a39862b62b6ea5be480a032"
    "35b3c8d54eb7a1fe1584c775668f48f1d6307b39b7d7484faf38c6b9bc3cebedb303ee1b9d858bfc93565c09914122e74cf6949d"
    "c5713c2de44c0ed53d6fd7206cd8dd124dea55dd26a325e3839c9215c645fe9a0f47864504b644fc01862af6e2fd37f1bb70ad15"
    "e07cb394de2fd2c44fcba7318766d0f75d09844bb74be61ca7d9be9eade703ccab044ddf928ec5a104d07f89712d6d8fcc1e255e"
    "66bfa9f88cef8e6afacefa79a0de72cbbc908e294c4005615a440dc74665a42ad3aaec837852042bb415e366d3b5e5e704b7dbfc"
    "4632c57193dde307d0cdd80edec1a8a60f458f0651fe72b761ca29670203010001")
MAGIC = bytes([0x21, 0xCE, 0x4E, 0x5E])
FIXED_SIZES = {3: 1, 4: 2, 5: 4, 6: 8, 7: 4, 8: 8, 11: 8, 12: 16}   # license3j feature types with a fixed size
# Prints the name of the gravitee-node-license jar of a lib folder, then the license model it holds
MODEL_SCRIPT = ('j=$(ls {lib}/gravitee-node-license-*.jar 2>/dev/null | head -1); '
                '[ -n "$j" ] && echo "$j" && unzip -p "$j" license-model.yml')

# What a license gives, counted with the license model: None while no product image carries the model
MODEL_FACTS = ("tier_known", "tier_packs", "extra_packs", "packs", "extra_features", "features", "model")

# The statuses of a license that Enterprise services can start with
USABLE = ("valid", "expiring")

# Words of a pack's id that are written in capitals, as the License dialog writes them; a product's id is too
ACRONYMS = ("mfa", "oem")

# What the license last read allows, and the packs of the license model by name ({name: features}), for the pages
# that only need to know what it allows and how a person calls it
_known = {"features": None, "packs": None}


def _der(data, i):
    """One DER element at i: (tag, content, next index)."""
    tag, length = data[i], data[i + 1]
    i += 2
    if length & 0x80:
        n = length & 0x7F
        length, i = int.from_bytes(data[i:i + n], "big"), i + n
    return tag, data[i:i + length], i + length


def _public_key():
    _, spki, _ = _der(PUBLIC_KEY, 0)
    _, _, j = _der(spki, 0)          # algorithm
    _, bits, _ = _der(spki, j)       # BIT STRING: unused-bits byte, then RSAPublicKey
    _, rsa, _ = _der(bits[1:], 0)
    _, n, j = _der(rsa, 0)
    _, e, _ = _der(rsa, j)
    return int.from_bytes(n, "big"), int.from_bytes(e, "big")


def _value(kind, value):
    """A license3j value: text, a date and a UUID are decoded, the other types stay as bytes."""
    if kind == 2:
        return value.decode()
    if kind == 11:
        return datetime.datetime.fromtimestamp(int.from_bytes(value, "big", signed=True) / 1000, datetime.timezone.utc)
    if kind == 12:
        least, most = value[:8], value[8:]
        return str(uuid.UUID(bytes=most + least))
    return value


def _signed(features, blobs):
    """RSA (PKCS#1 v1.5) over the digest of the license without its signature, features sorted by name."""
    signature, digest_name = features.get("licenseSignature"), features.get("signatureDigest", "")
    if not isinstance(signature, bytes) or not digest_name:
        return False
    signed = [blobs[name] for name in sorted(blobs) if name != "licenseSignature"]
    unsigned = MAGIC + b"".join(len(blob).to_bytes(4, "big") + blob for blob in signed)
    digest = hashlib.new(digest_name.replace("-", "").lower(), unsigned).digest()
    n, e = _public_key()
    block = pow(int.from_bytes(signature, "big"), e, n).to_bytes((n.bit_length() + 7) // 8, "big")
    return block[:2] == b"\x00\x01" and block.endswith(b"\x00" + digest)


def decode(text):
    """Features of a base64 license3j license, and whether its signature is GraviteeSource's."""
    raw = base64.b64decode(text)
    if raw[:4] != MAGIC:
        raise ValueError("Not a license")
    features, blobs, i = {}, {}, 4
    while i < len(raw):
        size = int.from_bytes(raw[i:i + 4], "big")
        blob, i = raw[i + 4:i + 4 + size], i + 4 + size
        kind, name_len = int.from_bytes(blob[:4], "big"), int.from_bytes(blob[4:8], "big")
        k = 8
        if kind in FIXED_SIZES:
            value_len = FIXED_SIZES[kind]
        else:
            value_len, k = int.from_bytes(blob[8:12], "big"), 12
        name = blob[k:k + name_len].decode()
        blobs[name] = blob
        features[name] = _value(kind, blob[k + name_len:k + name_len + value_len])
    return features, _signed(features, blobs)


def parse_model(text):
    """license-model.yml: {"packs": {pack: [features]}, "tiers": {tier: [packs]}}, with its &anchors and *aliases."""
    model, anchors, section, entry, items = {"packs": {}, "tiers": {}}, {}, None, None, None
    for line in text.split("\n"):
        code = line.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        indent, body = len(code) - len(code.lstrip()), code.strip()
        if indent == 0:
            section = body.rstrip(":") if body.rstrip(":") in model else None
        elif section and body.startswith("- "):
            value = body[2:].strip()
            if items is not None:
                items.extend(anchors.get(value[1:], []) if value.startswith("*") else [value])
        elif section and body.endswith(":") and indent <= 4:
            entry = body[:-1]
        elif section and entry and ":" in body:
            anchor = body.partition(":")[2].strip()
            items = model[section].setdefault(entry, [])
            if anchor.startswith("&"):
                anchors[anchor[1:]] = items
    return model


def _read_model(service, lib, why):
    """The license model in a service's image: read in its container when it runs, else from the image. None when it
    cannot be read, and `why` gets what Docker said."""
    rc, out, err = compose.shell(service, ["-c", MODEL_SCRIPT.format(lib=lib)])
    if rc != 0 or not out:
        why.append(err)
        return None
    jar, _, text = out.partition("\n")
    return {"source": jar.split("/")[-1].removesuffix(".jar"), "service": service["label"], **parse_model(text)}


class Unknown(RuntimeError):
    """No product image could tell the license model: why, and the image to pull when that is what it takes."""

    def __init__(self, message, pull=None):
        super().__init__(message)
        self.pull = pull


def _model(project):
    """The license model of the platform: the first one found in a product's image, in the order of the groups. It is
    kept by the image it was read from (compose.image_key): read again once another one runs. When none can be read,
    Unknown says why: Docker does not answer, the first image is not pulled, or none carries a model."""
    unread = []
    for group in project["groups"]:
        product = project["products"].get(group["product"]) or {}
        for name, lib in product.get("lib", {}).items():
            service, why = project["services"][name], []
            key = f"license-model:{compose.image_key(service)}"
            model = compose.CACHE.get(key, 24 * 3600, lambda: _read_model(service, lib, why))
            if model:
                return model
            unread += [(service["image"], compose.unread(service["image"], said)) for said in why]
    image, reason = next(((image, reason) for image, reason in unread if reason), (None, None))
    if reason == "Docker is not reachable":
        raise Unknown(reason)
    if image:
        raise Unknown(f"Not known until {image} is pulled", pull=image)
    raise Unknown("No product image carries a license model")


def _listed(features, key):
    return [x for x in str(features.get(key, "")).split(",") if x]


def _grants(features, model):
    """What the license gives: the packs of its tier, its own packs and features, and every feature those add up to."""
    tier = features.get("tier")
    tier_packs, extra_packs = model["tiers"].get(tier, []), _listed(features, "packs")
    packs = sorted(set(tier_packs) | set(extra_packs))
    # Legacy licenses list a feature as "<feature>: included"
    extra = set(_listed(features, "features")) | {k for k, v in features.items() if v == "included"}
    return {"tier": tier, "tier_known": tier in model["tiers"],
            "tier_packs": tier_packs, "extra_packs": extra_packs, "packs": packs,
            "extra_features": sorted(extra),
            "features": sorted(extra | {f for p in packs for f in model["packs"].get(p, [])}),
            "model": model}


def _read(project):
    text = envfiles.read(ENV_FILE).get(project["license_var"] or "", "").strip()
    if not text:
        return {"present": False}
    try:
        features, valid = decode(text)
    except (ValueError, IndexError):
        return {"present": True, "readable": False}
    expiry = features.get("expiryDate")
    now = datetime.datetime.now(datetime.timezone.utc)
    facts = {"present": True, "readable": True, "valid": valid,
             "expires": expiry.isoformat() if expiry else None,
             "expired": bool(expiry and expiry < now),
             "days_left": (expiry - now).days if expiry else None,
             "company": features.get("company"), "email": features.get("email"), "id": features.get("licenseId")}
    try:
        model = _model(project)
    except Unknown as error:   # no product image to read the model from: what the license says itself shows
        return {**facts, **dict.fromkeys(MODEL_FACTS), "tier": features.get("tier"), "model_error": str(error),
                "pull": error.pull}
    return {**facts, **_grants(features, model), "model_error": None, "pull": None}


def _day(when):
    """A day as the page writes it, in UTC as the license was issued: "3 Mar 2026"."""
    return f"{when.day} {when:%b %Y}"


def status(project):
    """The license of the project's core .env as status_of() reads it; None without a variable that holds it."""
    return status_of(project["license_var"])


def usable(found):
    """Whether a license as status_of() reads it lets a service that needs one start: genuine and not expired."""
    return bool(found) and found["status"] in USABLE


def status_of(variable):
    """The license a variable of the core .env holds, as the state says it, every few seconds, without Docker:
    {status, expires, days_left, date, problem, digest}. `status`: none, unreadable, invalid (not signed by
    GraviteeSource), expired, expiring (within 30 days) or valid. `problem`: the banner's line, only when it cannot be
    read, is not GraviteeSource's, expired or expires soon. `digest`: what a container that reads the license holds
    when it runs the one saved (compose.digest). None without a variable."""
    if not variable:
        return None
    value = envfiles.read(ENV_FILE).get(variable, "")
    facts = {"expires": None, "days_left": None, "date": None, "problem": None, "digest": compose.digest(value)}
    if not value.strip():
        return {**facts, "status": "none"}
    try:
        features, valid = decode(value.strip())
    except (ValueError, IndexError):
        return {**facts, "status": "unreadable", "problem": "The license cannot be read: Enterprise features are off"}
    if not valid:
        return {**facts, "status": "invalid",
                "problem": "The license is not signed by GraviteeSource: Enterprise features are off"}
    expiry, now = features.get("expiryDate"), datetime.datetime.now(datetime.timezone.utc)
    if not expiry:
        return {**facts, "status": "valid"}
    days = (expiry - now).days
    facts.update(expires=expiry.isoformat(), days_left=days, date=_day(expiry))
    if expiry < now:
        return {**facts, "status": "expired",
                "problem": f"The license expired on {facts['date']}: Enterprise features are off"}
    if days <= 30:
        when = "today" if days < 1 else "in 1 day" if days == 1 else f"in {days} days"
        return {**facts, "status": "expiring", "problem": f"The license expires {when} ({facts['date']})"}
    return {**facts, "status": "valid"}


def unapplied(project, containers, digest):
    """The services that read the license and run another one than the one saved (`digest`, status()), as
    compose.ps(project.LICENSE_KEY) reads the containers: one read without it tells nothing. Pure."""
    return [name for name in project["license_services"]
            if (containers.get(name) or {}).get("state") == "running" and "variable" in containers[name]
            and containers[name]["variable"] != digest]


def applied(project, containers, digest):
    """Whether every service that reads the license and runs, runs the one saved: unapplied() is empty."""
    return not unapplied(project, containers, digest)


def allowed(result):
    """The features a license allows, from what info() answered: none without a license that is genuine and current,
    None when they are not known (no product image to read the license model from)."""
    if not (result.get("readable") and result["valid"] and not result["expired"]):
        return set()
    return None if result["features"] is None else set(result["features"])


def info(project):
    """The license of the core .env: who it is for, until when, and the features it gives. Without a product image
    to read the license model from, what is counted with it (MODEL_FACTS) is None, `model_error` says why ("Docker is
    not reachable", "Not known until <image> is pulled") and `pull` names the image to pull, when that is what it
    takes. The packs of the model are kept by name for named(), read without a license too."""
    result = _read(project)
    # A Gravitee employee gets no invitation to a license or a Cloud account: said by GRAVITEE_EMPLOYEE in the .env, which nothing else shows
    result["employee"] = envfiles.read(ENV_FILE).get("GRAVITEE_EMPLOYEE", "").strip().strip("\"'").lower() in ("true", "1", "yes")
    _known["features"] = allowed(result)
    model = result.get("model")
    if not result["present"]:   # nothing to count with the model, but its packs still name what a row needs
        try:
            model = _model(project)
        except Unknown:
            model = None
    _known["packs"] = _named_packs(model, project) if model else None
    return result


def _title(pack, capitals):
    """A pack's id as the License dialog writes it: "enterprise-mfa-factor" reads "Enterprise MFA factor"."""
    words = pack.split("-")
    return " ".join(word.upper() if word in capitals else word.capitalize() if not i else word
                    for i, word in enumerate(words))


def _named_packs(model, project):
    capitals = {*ACRONYMS, *project["products"]}
    return {_title(pack, capitals): set(features) for pack, features in model["packs"].items()}


def named(features):
    """The packs that give these features, by the names a person reads ("Agent management"), sorted: the fewest that
    hold them all, a pack holding more of them first, then the smaller one (a feature is in several). A feature in
    no pack keeps its id, and so does every one while the license model is not known (Docker down, image not
    pulled). From the model info() last read: never Docker."""
    packs, left, names = _known["packs"] or {}, set(features), []
    while left and packs:
        name = max(sorted(packs), key=lambda pack: (len(left & packs[pack]), -len(packs[pack])))
        if not left & packs[name]:
            break
        names.append(name)
        left -= packs[name]
    return sorted(names) + sorted(left)


def features_cached():
    """The features the license allows, as info() last read them: empty without a license that is genuine and current,
    None before the first read and while they are not known. Never asks Docker: the state is built from it every few
    seconds."""
    return _known["features"]
