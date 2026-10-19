"""OpenFGA: stores from seed/openfga/*.yaml, in the OpenFGA store file format (`fga store import`).

    name: Documents
    model: |                # the model in the OpenFGA language, or model_file: a .fga file next to it
      model
        schema 1.1
      type user
    tuples:
      - {user: "user:anne", relation: viewer, object: "doc:1"}

The model is converted with the official OpenFGA CLI. Re-running is safe: a store is found by name, the model is
only written when it changed, and existing tuples are kept.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from .common import Api, SeedError, files, load, log, owned, record, setting, wait_until

SECTION = "openfga"
DEPENDS = ()   # the modules whose work this one reads on the platform: none, so it runs at the start (schedule.py)
# What it records
ORDER = ["store"]


def to_json(dsl):
    """The model as the API expects it, converted by `fga model transform`."""
    with tempfile.NamedTemporaryFile("w", suffix=".fga", encoding="utf-8") as f:
        f.write(dsl)
        f.flush()
        p = subprocess.run(["fga", "model", "transform", "--file", f.name, "--input-format", "fga"],
                           capture_output=True, encoding="utf-8")
    if p.returncode != 0:
        raise SeedError(f"OpenFGA model: {p.stderr.strip() or p.stdout.strip()}")
    return json.loads(p.stdout)


def canonical(model):
    """Type definitions and conditions only, without empty fields: what makes two models equal."""
    def clean(value):
        # Children first: an object can become empty once its empty fields are gone. "this": {} means a direct relation.
        if isinstance(value, dict):
            out = {}
            for k, v in sorted(value.items()):
                v = clean(v)
                if v not in (None, {}, [], "") or k == "this":
                    out[k] = v
            return out
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    types = sorted(model.get("type_definitions", []), key=lambda t: t.get("type", ""))
    return json.dumps(clean({"types": types, "conditions": model.get("conditions") or {}}), sort_keys=True)


def seed():
    """Returns {store name: {"id", "model_id"}} for the AM authorization engines that use them."""
    paths = files("openfga")
    if not paths:
        return {}
    api = Api(setting("OPENFGA_API_URL"))
    wait_until(SECTION, "OpenFGA", lambda: api.get("/stores"))
    stores = {}
    for path in paths:
        spec = load(path)
        name = spec.get("name") or path.stem
        dsl = spec.get("model") or (Path(path).parent / spec["model_file"]).read_text(encoding="utf-8")
        model = to_json(dsl)

        store = next((s for s in api.get("/stores").json().get("stores", []) if s["name"] == name), None)
        if store:
            log(SECTION, f"Store '{name}' exists")
        else:
            store = api.post("/stores", json={"name": name}).json()
            record(SECTION, "store", "OpenFGA stores", name, store["id"])
            log(SECTION, f"✓ Store '{name}' created")

        latest = api.get(f"/stores/{store['id']}/authorization-models", params={"page_size": 1}).json()
        latest = (latest.get("authorization_models") or [None])[0]
        if latest and canonical(latest) == canonical(model):
            model_id = latest["id"]
            log(SECTION, "  Model unchanged")
        else:
            model_id = api.post(f"/stores/{store['id']}/authorization-models", json=model).json()["authorization_model_id"]
            log(SECTION, "  ✓ Model written")

        tuples = [{"user": t["user"], "relation": t["relation"], "object": t["object"]} for t in spec.get("tuples", [])]
        for i in range(0, len(tuples), 100):   # the API takes at most 100 tuples per write
            api.post(f"/stores/{store['id']}/write", json={
                "writes": {"tuple_keys": tuples[i:i + 100], "on_duplicate": "ignore"},
                "authorization_model_id": model_id,
            })
        if tuples:
            log(SECTION, f"  ✓ {len(tuples)} tuples")
        stores[name] = {"id": store["id"], "model_id": model_id}
    return stores


def remove(records):
    stores = owned(records, SECTION, ORDER)
    if not stores:
        return
    api = Api(setting("OPENFGA_API_URL"))
    for x in stores:
        api.delete(f"/stores/{x['id']}")
        log(SECTION, f"✓ Store '{x['name']}' deleted")
