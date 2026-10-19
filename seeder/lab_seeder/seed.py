"""Seeds a playground into Gravitee: OpenFGA stores, Access Management, API Management, the link from Gamma to Access
Management, then Gamma (the AI catalog, workspaces, MCP proxies, agents and their governance, authorization, dashboards).
`--remove` deletes what the seed created, and only that, from the last module to the first. What the platform refuses to delete
(a module gives its record back) is kept in the record: the rest is removed all the same.

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
import time
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
from . import schedule
from . import common
from .common import SeedError, created, has_seed, log, save_created
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
MODULES = (openfga, am) + AFTER_AM
# What a module reads of the work of the others is its DEPENDS. That order is the one a seed used to run in (SEED_WORKERS=1
# still runs it), and the modules that do not depend on one another run at the same time: Access Management, API Management and
# Gamma are three chains, joined where one reads the work of another. A removal goes the other way round.


def graph(seeding=False):
    """({name: module}, {name: the names it needs}) of the modules in order. A module that does not say what it depends on depends
    on every one before it. A seed starts a module together with those it says it can start MEANWHILE (it waits for what it needs
    of them, step by step, with common.reached); a removal keeps the whole of its dependencies."""
    named, needs = {}, {}
    for index, module in enumerate((openfga, am) + AFTER_AM):
        name = getattr(module, "__name__", f"module-{index}").rsplit(".", 1)[-1]
        declared = getattr(module, "DEPENDS", None)
        needs[name] = declared if isinstance(declared, tuple) else tuple(named)
        meanwhile = getattr(module, "MEANWHILE", ())
        if seeding and isinstance(meanwhile, tuple):
            needs[name] = tuple(each for each in needs[name] if each not in meanwhile)
        named[name] = module
    return named, needs


def gamma_modules():
    """The Gamma modules the playground's seed fills: those its folders ask for, and none for one that seeds no Gamma."""
    return {name for module in AFTER_AM if has_seed(*getattr(module, "FOLDERS", ()))
            for name in getattr(module, "GAMMA", ())}


def told(spent, took):
    """How long the seed took and what running steps together saved, when it saved something worth saying."""
    busy = [f"{name} {seconds:.0f} s" for name, seconds in sorted(spent.items(), key=lambda each: -each[1]) if seconds >= 1]
    total = sum(spent.values())
    if busy and total - took >= 1:
        log("seed", f"Took {took:.0f} s; the steps add up to {total:.0f} s ({', '.join(busy)})")


def main():
    removing = "--remove" in sys.argv
    log("seed", "Removing what the playground created" if removing else "Seeding the playground")
    try:
        common.configure()
        common.defer_waits()
        if removing:
            records, kept = created(), []
            named, needs = graph()
            schedule.run(list(named), needs, lambda name: kept.extend(named[name].remove(records) or []), back=True)
            # a module may give back the records of what the platform refused to delete
            save_created(kept)
            gone = len(records) - len(kept)
            log("seed", f"✓ {gone} item{'s' if gone != 1 else ''} removed" + (f", {len(kept)} kept" if kept else ""))
            return 0
        found = controls.begin()
        refills.begin()
        if found:
            log("seed", f"Protections {controls.mode()}: {controls.named(found)}")
        require_modules(gamma_modules())
        stores, (named, needs), spent, begun = {}, graph(seeding=True), {}, time.monotonic()

        def seed(name):
            started = time.monotonic()
            if named[name] is openfga:
                stores.update(openfga.seed() or {})
            elif named[name] is am:
                am.seed(stores)
            else:
                named[name].seed()
            spent[name] = time.monotonic() - started
        schedule.run(list(named), needs, seed)
        if not common.waits_for_gateway() and os.getenv("GATEWAY_INTERNAL_URL"):
            log("seed", "The gateway takes what was deployed within seconds; SEED_WAIT_GATEWAY=1 waits for it")
        common.run_later()   # the waits that nothing depended on: the gateway has taken what was deployed
        refills.finish()
        controls.finish()
        told(spent, time.monotonic() - begun)
    except SeedError as e:
        log("seed", f"✗ {e}")
        return 1
    except Exception:  # noqa: BLE001 - shown in the container logs
        traceback.print_exc()
        return 1
    log("seed", "✓ Playground ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
