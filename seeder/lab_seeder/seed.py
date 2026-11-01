"""Seeds a playground into Gravitee: OpenFGA stores, Access Management, API Management, the link from Gamma to Access
Management, then Gamma (the AI catalog, workspaces, MCP proxies, agents and their governance, authorization, dashboards).
`--remove` deletes what the seed created, and only that, from the last module to the first, each record dropped as its object
goes. What the platform refuses to delete stays in the record: the rest is removed all the same.

Reads /seed (SEED_DIR) and talks to the platform only through its management APIs, so the same seed works
against the lab or any other platform it can reach: endpoints, credentials and ids come from the environment. The one
exception is a refill (below), which is the lab's.

The controls of seed/controls.yaml (controls.py) are the protections a playground's lessons teach: every run brings them to
the state SEED_PROTECTIONS says (the mode is checked before anything is changed), and the seed ends once the gateway has taken
what they deployed, or fails on a control whose target no module found. The refills of seed/refills.yaml (refills.py) give a
member of an AI workspace their budget back on every run: Re-seed starts what the lessons spend over, as it does the controls.
A refill deletes counters in the core's Redis (REDIS_URL, REDIS_PASSWORD), which a platform other than the lab does not give: there
the seed says it could not refill and goes on, and a budget that is spent stays spent until its period ends.

A module has a SECTION (its log prefix, and the product of its records), `seed()` and `remove(records)`. Its `seed()`
returns before it reads a setting or connects when the folder of the seed that is its own is absent: a playground
pays nothing for what it does not seed.
"""

import os
import sys
import traceback

from . import aim_agents
from . import aim_catalog
from . import aim_dashboards
from . import aim_governance
from . import aim_proxies
from . import aim_workspaces
from . import am
from . import am_link
from . import apim
from . import apim_portal
from . import authz
from . import controls
from . import k8s
from . import openfga
from . import refills
from . import common
from .common import KEYS, SeedError, created, forget, has_seed, log, owned
from .gamma import require_modules

# Each module finds what it needs, by name or id, in what the modules before it made; OpenFGA's stores for AM's
# authorization engine are the one thing handed over. A removal goes the other way: APIM's subscriptions use AM's
# applications, and Gamma's objects use APIM's. The link to Access Management comes before what Gamma registers in it
# (a studio's MCP server, an agent's identity); the agents come after the studios they subscribe to. The resources of
# the Kubernetes operator come last, so that a removal deletes them first: the operator deletes their APIs and
# applications from API Management while that still holds what they use. The portals of the environments come right after
# the APIs their listings name, and a removal deletes them first.
AFTER_AM = (apim, apim_portal, am_link, aim_catalog, aim_workspaces, aim_proxies, aim_agents, aim_governance, authz, aim_dashboards,
            k8s)
MODULES = (openfga, am) + AFTER_AM   # a seed runs them in this order, one after the other, and a removal in the reverse


def gamma_modules():
    """The Gamma modules the playground's seed fills: those its folders ask for, and none for one that seeds no Gamma."""
    return {name for module in AFTER_AM if has_seed(*getattr(module, "FOLDERS", ()))
            for name in getattr(module, "GAMMA", ())}


def key(record):
    return tuple(record[name] for name in KEYS)


def main():
    removing = "--remove" in sys.argv
    log("seed", "Removing what the playground created" if removing else "Seeding the playground")
    try:
        common.configure()
        common.defer_waits()
        if removing:
            records = created()
            for module in reversed(MODULES):   # each forgets a record as its object goes; what the platform refused stays
                module.remove(records)
            known = {key(x) for module in MODULES for x in owned(records, module.SECTION, module.ORDER)}
            forget(*[x for x in records if key(x) not in known])   # of no module of this seeder: nothing can delete it
            left = {key(x) for x in created()}
            kept = sum(key(x) in left for x in records)
            gone = len(records) - kept
            log("seed", f"✓ {gone} item{'s' if gone != 1 else ''} removed" + (f", {kept} kept" if kept else ""))
            return 0
        found = controls.begin()
        refills.begin()
        if found:
            log("seed", f"Protections {controls.mode()}: {controls.named(found)}")
        require_modules(gamma_modules())
        am.seed(openfga.seed() or {})
        for module in AFTER_AM:
            module.seed()
        if not common.waits_for_gateway() and os.getenv("GATEWAY_INTERNAL_URL"):
            log("seed", "The gateway takes what was deployed within seconds; SEED_WAIT_GATEWAY=1 waits for it")
        common.run_later()   # the waits that nothing depended on: the gateway has taken what was deployed
        refills.finish()
        controls.finish()
    except SeedError as e:
        log("seed", f"✗ {e}")
        return 1
    except Exception:  # noqa: BLE001 - its traceback is for the container logs, its last line for the person
        traceback.print_exc()
        sys.stderr.flush()
        log("seed", "✗ The seed stopped on an error it did not expect: the lines above say where")
        return 1
    log("seed", "✓ Playground ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
