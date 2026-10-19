"""Gravitee Cloud: the few calls the Lab makes. The management API of an account is called with an Account Token, which
opens the paths under /accounts/{id} of its own account, by its UUID, and nothing else: the list of accounts, /me and an
account's hrid answer 401, so the id is something the person gives. Cloud Gate, the API Management of Cloud, is called
with a Cloud Token, and one cheap read proves it. What a token says about itself is read without being checked. A
failure is a CloudError in plain words. A token goes in the Authorization header and nowhere else: no message, repr or
error holds it, nor the token that a new hybrid gateway is answered with."""

import base64
import http.client
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

AUTH = "Bearer"       # the Cloud's documentation says Bearer, its OpenAPI specification says HTTP basic
PATH = "/management"
GATE = "/apim/rest"   # the management API of API Management, as Cloud Gate serves it (no /management prefix)
TIMEOUT = 15          # seconds the Cloud has to answer
MAX_ANSWER = 4 << 20  # the largest answer read
NAME = re.compile(r"[a-zA-Z0-9\s\-_.]{2,128}", re.ASCII)   # a gateway's name, as the Cloud checks it
TOKEN = re.compile(r"[\x21-\x7e]+")              # printable ASCII: a header holds nothing else
REGION = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")   # one label of a host name: where Cloud Gate is
LOCAL = ("localhost", "127.0.0.1", "::1")
SCOPE = "APIM"        # the scope of the hybrid gateways the Lab creates: an API Management gateway


class CloudError(ValueError):
    """What the Cloud refused or did not answer, in plain words; the Lab UI answers it as a bad request. `kind` is
    "invalid" when it answered no (a token it refuses, rights it lacks, something it does not know) and
    "unreachable" when it gave no usable answer. `status` is the HTTP status it answered, when it did."""

    def __init__(self, message, kind="unreachable", status=None):
        super().__init__(message)
        self.kind, self.status = kind, status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not followed: the token must not go to the address it names."""

    def redirect_request(self, *args, **more):
        return None


def base(url):
    """The address of a Cloud, as the client calls it: http or https, a host, no credentials, query or trailing slash,
    and without the /management that the client adds."""
    try:
        parts = urlsplit((url or "").strip())
        parts.port   # a port that is no number raises here
    except (ValueError, AttributeError):
        parts = None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname \
            or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("The address must be http:// or https:// followed by a host, without credentials")
    if parts.scheme == "http" and parts.hostname not in LOCAL:
        raise ValueError("The address must be https://, as the token travels with every call")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/').removesuffix(PATH)}"


def _id(value):
    """An id as one segment of a path: the Cloud's ids hold no slash, and a dot segment would walk out of the path."""
    if not isinstance(value, str) or value in ("", ".", ".."):
        raise CloudError("Not an id of Gravitee Cloud", "invalid")
    return quote(value, safe="")


def _text(value):
    return value if isinstance(value, str) and value else None


def claims(token):
    """What a token says about itself, read without being checked: {kind, organization, environment, expiry, gateway,
    cpg}. `kind` is "cloud" (a Cloud Token: scopes automation and rest), "gateway" (the token of a hybrid gateway:
    scopes reports and sync, and `gateway` is its id), "account" (a JWT of none of those, made for another audience
    than Cloud Gate, which is how an Account Token is taken) or "unknown" (not a JWT). `expiry` is seconds since the
    epoch. `environment` is the one of the token, None when it names none or several. `cpg` is the region of the Cloud
    that issued it ("eu"), which names where Cloud Gate is: only one label of a host name is taken, so that no claim
    can send a token to another address. A claim that is missing or of another type is None. It never raises, and
    nothing else of the token is answered."""
    parts = token.split(".") if isinstance(token, str) else []
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))) \
            if len(parts) == 3 else None
    except (ValueError, RecursionError):   # a ValueError: not base64, not UTF-8, not JSON
        payload = None
    if not isinstance(payload, dict):
        return {"kind": "unknown", "organization": None, "environment": None, "expiry": None, "gateway": None,
                "cpg": None}
    scopes, envs, meta, expiry = payload.get("scopes"), payload.get("envs"), payload.get("meta"), payload.get("exp")
    region, scopes = payload.get("cpg"), scopes if isinstance(scopes, list) else []
    found = {"organization": _text(payload.get("org")),
             "environment": _text(envs[0]) if isinstance(envs, list) and len(envs) == 1 else None,
             "expiry": expiry if isinstance(expiry, int) and not isinstance(expiry, bool) else None,
             "gateway": _text(meta.get("GATEWAYID")) if isinstance(meta, dict) else None,
             "cpg": region if isinstance(region, str) and REGION.fullmatch(region) else None}
    if found["gateway"] or any(scope in ("sync", "reports") for scope in scopes):
        kind = "gateway"
    elif any(scope in ("automation", "rest") for scope in scopes):
        kind = "cloud"
    else:
        kind = "account" if _text(payload.get("aud")) not in (None, "CloudGate") else "unknown"
    return {"kind": kind, **found}


def _plain(text, secret=""):
    """What the Cloud wrote, on one short line, without the token."""
    text = " ".join(str(text).split())
    return (text.replace(secret, "…") if secret else text)[:200]


def _unreachable(error, host):
    """Why a request did not get an answer, in a few words."""
    reason = getattr(error, "reason", error)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return f"{host} does not answer in time"
    if isinstance(reason, socket.gaierror):
        return f"Cannot find {host}"
    if isinstance(reason, ConnectionRefusedError):
        return f"{host} refuses the connection"
    if isinstance(reason, ssl.SSLError):
        return f"The certificate of {host} is not trusted"
    return f"Cannot reach {host}"


class _Client:
    """A service of Gravitee Cloud and the token to call it with."""

    prefix = ""      # what the paths of the service start with
    forbidden = ""   # what a 403 means to this service

    def __init__(self, url, token):
        self.url = base(url)
        self.host = urlsplit(self.url).netloc
        token = token.strip() if isinstance(token, str) else ""
        if not TOKEN.fullmatch(token):
            raise ValueError("The token is empty, or has spaces or characters that cannot be sent")
        self._token = token

    def __repr__(self):
        return f"{type(self).__name__}({self.url!r})"

    def _scheme(self):
        return "Bearer"

    def _refused(self, error):
        """The CloudError for an answer that is an error."""
        code = error.code
        if code == 401:
            return CloudError("Gravitee Cloud refuses this token", "invalid", code)
        if code == 403:
            return CloudError(self.forbidden, "invalid", code)
        if code == 404:
            return CloudError("Not found in Gravitee Cloud", "invalid", code)
        if code == 400:
            try:
                said = json.loads(error.read(2000)).get("message")
            except (ValueError, AttributeError, OSError):
                said = None
            return CloudError("Gravitee Cloud refuses it" + (f": {_plain(said, self._token)}" if said else ""),
                              "invalid", code)
        if 300 <= code < 400:
            return CloudError(f"{self.host} sends the request to another address: check the URL", status=code)
        return CloudError(f"{self.host} answered an error (HTTP {code})", status=code)

    def _open(self, method, path, body=None, accept="application/json"):
        """What the service answers, as bytes: empty when it says nothing."""
        headers = {"Authorization": f"{self._scheme()} {self._token}", "Accept": accept,
                   "User-Agent": "Gravitee-Lab-UI"}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        request = urllib.request.Request(self.url + self.prefix + path, data, headers, method=method)
        # A proxy of the environment is not asked for this machine's own address, which it cannot reach
        proxies = [urllib.request.ProxyHandler({})] if urlsplit(self.url).hostname in LOCAL else []
        try:
            with urllib.request.build_opener(_NoRedirect, *proxies).open(request, timeout=TIMEOUT) as response:
                return response.read(MAX_ANSWER + 1)
        except urllib.error.HTTPError as error:
            with error:   # closes the connection it holds
                refused = self._refused(error)
            raise refused from None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as error:
            # A ValueError: a host name that is none, as in "a..b"
            raise CloudError(_unreachable(error, self.host)) from None

    def _text(self, path):
        """What the service answers to a read as plain text, "" when it says nothing."""
        return self._open("GET", path, accept="text/plain").decode("utf-8", "replace").strip()

    def _call(self, method, path, body=None):
        """The JSON the service answers, None when the answer is empty."""
        text = self._open(method, path, body)
        if not text.strip():
            return None
        try:
            if len(text) > MAX_ANSWER:
                raise ValueError   # more than a Cloud ever says
            return json.loads(text)
        except (ValueError, RecursionError):
            raise CloudError(f"{self.host} does not answer like Gravitee Cloud") from None


class Cloud(_Client):
    """A Cloud and the Account Token to call it with."""

    prefix = PATH
    forbidden = "This token is not allowed to do that: it needs an account owner"

    def _scheme(self):
        return AUTH

    def _list(self, path):
        """A list of objects, as the Cloud answers one."""
        found = self._call("GET", path)
        if not isinstance(found, list) or not all(isinstance(item, dict) for item in found):
            raise CloudError(f"{self.host} does not answer like Gravitee Cloud")
        return found

    def account(self, account):
        """An account by its id: {id, hrid, name, profile...}. Its hrid is no id here, the Cloud answers 401 to it."""
        found = self._call("GET", f"/accounts/{_id(account)}")
        if not isinstance(found, dict) or not isinstance(found.get("id"), str):
            raise CloudError(f"{self.host} does not answer like Gravitee Cloud")
        return found

    def environments(self, account):
        """The environments of an account: [{id, name, organizationId...}]. An item has no hrid and no status."""
        return self._list(f"/accounts/{_id(account)}/environments")

    def gateways(self, account):
        """The gateways of an account, hosted or hybrid, with their connection status."""
        return self._list(f"/accounts/{_id(account)}/gateways?withStatus=true")

    def subscription(self, account):
        """What the account's plan allows: {gammaEnabled, maxNumberSaasGateways...}, the entitlements the Cloud says of
        itself."""
        found = self._call("GET", f"/accounts/{_id(account)}/subscription")
        if not isinstance(found, dict):
            raise CloudError(f"{self.host} does not answer like Gravitee Cloud")
        return found

    def organizations(self, account):
        """The organizations of an account, with the access points of each: [{id, name, accessPoints: [{target, scope}]}]."""
        return self._list(f"/accounts/{_id(account)}/organizations")

    def license(self, account, organization):
        """The license of an organization, as the signed text the Lab reads like any other (license.decode)."""
        return self._text(f"/accounts/{_id(account)}/organizations/{_id(organization)}/license")

    def create_gateway(self, account, environment, name, domains):
        """A hybrid gateway of API Management: (the gateway, its Cloud Token). The Cloud shows the token once, here:
        a gateway answered without one is removed again, it could not be run."""
        found = self._call("POST", f"/accounts/{_id(account)}/gateways", {
            "environmentId": environment, "name": name, "scope": SCOPE, "type": "HYBRID", "domains": domains})
        gateway, token = (found.get("gateway"), found.get("token")) if isinstance(found, dict) else (None, None)
        if not isinstance(gateway, dict) or not isinstance(gateway.get("id"), str):
            raise CloudError(f"{self.host} does not answer like Gravitee Cloud")
        if not isinstance(token, str) or not token:
            self.delete_gateway(account, gateway["id"])
            raise CloudError(f"{self.host} gave no token for the new gateway")
        return gateway, token

    def delete_gateway(self, account, gateway):
        self._call("DELETE", f"/accounts/{_id(account)}/gateways/{_id(gateway)}")


class Gate(_Client):
    """Cloud Gate, the API Management of a Cloud, and a Cloud Token to call it with. Its address is the one the record
    of the platform keeps."""

    forbidden = "This Cloud Token cannot read API Management"

    def read(self, organization, environment):
        """The one cheap question that tells whether the token opens an environment: the first API of it, if any.
        The Cloud answers 401 to a token it refuses, that expired or that is for another environment."""
        found = self._call("GET", f"{GATE}/v2/organizations/{_id(organization)}/environments/{_id(environment)}"
                                  "/apis?perPage=1")
        if not isinstance(found, dict):
            raise CloudError(f"{self.host} does not answer like Gravitee Cloud")

    def plugins(self, organization):
        """What the Management API of the Cloud has installed, by kind: {policy, endpoint, entrypoint, resource}, each
        {plugin id: whether it is deployed, which is whether the license of the organization has its feature}. Cloud Gate
        answers the v2 routes of the Management API as an API Management does."""
        found = {}
        for kind, route in (("policy", "policies"), ("endpoint", "endpoints"), ("entrypoint", "entrypoints"),
                            ("resource", "resources")):
            items = self._call("GET", f"{GATE}/v2/organizations/{_id(organization)}/plugins/{route}")
            if not isinstance(items, list):
                raise CloudError(f"{self.host} does not answer like Gravitee Cloud")
            found[kind] = {item["id"]: item.get("deployed") is not False
                           for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)}
        return found

    def license(self, organization):
        """The license of the organization as the Management API says it: {tier, packs, features, expiresAt}."""
        found = self._call("GET", f"{GATE}/v2/organizations/{_id(organization)}/license")
        if not isinstance(found, dict):
            raise CloudError(f"{self.host} does not answer like Gravitee Cloud")
        return found

    def check(self):
        """Proves a Cloud Token and answers its claims, which name its organization and its environment: one read of
        that environment. A token that is no Cloud Token, or has expired, is refused without asking. Whether it is for
        the environment a person chose is for the caller to compare."""
        found = claims(self._token)
        if found["kind"] == "gateway":
            raise CloudError("This is a gateway token, not a Cloud Token", "invalid")
        if found["kind"] == "account":
            raise CloudError("This is an Account Token, not a Cloud Token", "invalid")
        if found["kind"] != "cloud" or not (found["organization"] and found["environment"]):
            raise CloudError("This is not a Cloud Token", "invalid")
        if found["expiry"] is not None and found["expiry"] <= time.time():
            raise CloudError("This Cloud Token has expired", "invalid")
        self.read(found["organization"], found["environment"])
        return found
