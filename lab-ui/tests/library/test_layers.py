"""layers: closures, conflicts and every plan on a small hand-built project, a deploy to a remote place included; the
on file, the settings an add-on lacks and the job of a plan in a temporary folder. No Docker."""

import hashlib
import inspect
import itertools
import json
import os
import sys
import types
import unittest
from unittest import mock

from lab_ui.docker import compose, envfiles
from lab_ui.library import layers, layout, project as projects
from lab_ui import paths
from lab_ui.docker.jobs import Job, awaited

from .. import Case, fixtures
from ..docker.test_jobs import Docker, container, progress, replay
from .test_project import read

SERVICE_LABELS = {"apim-gateway": "Gateway", "apim-management-api": "Management API", "kafka": "Kafka broker",
                  "kafka-ui": "Kafka UI", "openfga-migrate": "OpenFGA migration", "seed": "Seed"}
# The services Docker checks the health of (test_jobs.Docker): ready once healthy, the others once they run
HEALTH_CHECKED = {"apim-gateway", "apim-management-api", "mongodb", "kafka", "jade", "redpanda", "openfga",
                  "hybrid-gateway"}
EXPORTER = "both take the gateway's exporter"


def _service(name, owner, oneshot=False, role=None, console=None):
    consoles = [{"label": console, "url": f"http://localhost/{name}", "legacy": False}] if console else []
    return {"label": SERVICE_LABELS.get(name, name), "description": "", "container": f"lab-{name}", "owner": owner, "image": "",
            "raw_image": "graviteeio/apim-gateway:1" if name == "apim-gateway" else "", "tag": "", "registry": None, "image_tags": None, "ports": [], "consoles": consoles,
            "oneshot": oneshot, "depends_on": [], "built": False, "role": role, "needs_license": False}


def _addon(uid, name, services, needs=(), conflicts=None, restarts=(), required=(), requires=None):
    settings = [{"key": key, "default": None, "required": True, "comment": "", "secret": True} for key in required]
    return {"id": uid, "kind": "addon", "name": name, "personal": paths.personal(uid), "category": "Other",
            "description": "", "requires": requires or {}, "conflicts": conflicts or {}, "needs": list(needs),
            "services": list(services), "restarts": list(restarts),
            "file": f"addons/{uid}/compose.yml", "env_file": f"addons/{uid}/.env", "settings": settings}


def _setup(uid, name, includes, members):
    return {"id": uid, "kind": "setup", "name": name, "personal": paths.personal(uid), "category": "",
            "description": "", "includes": includes, "members": members, "file": f"setups/{uid}.yml"}


def lab():
    """A small project: Kafka Desk needs Kafka, AI Agent Lens conflicts with Jade, Redpanda with Kafka,
    Hybrid gateway has a required setting, the welcome brings AI Agent Lens."""
    addons = [_addon("kafka", "Kafka", ["kafka", "kafka-ui"], restarts=["apim-gateway"]),
              _addon("kafka-desk", "Kafka Desk", ["kafka-desk"], ["kafka"], restarts=["apim-management-api"]),
              _addon("jade", "Jade", ["jade"], restarts=["apim-gateway"]),
              _addon("kiosk", "Kiosk", ["kiosk"]),
              _addon("agent-lens", "AI Agent Lens", ["agent-lens"], conflicts={"jade": EXPORTER},
                     restarts=["apim-gateway"]),
              _addon("redpanda", "Redpanda", ["redpanda"], conflicts={"kafka": "same ports"}),
              _addon("openfga", "OpenFGA", ["openfga", "openfga-migrate"]),
              _addon("hybrid", "Hybrid gateway", ["hybrid-gateway"], required=["HYBRID_CLOUD_TOKEN"])]
    welcome = {"id": "welcome", "kind": "playground", "name": "Welcome", "personal": False, "category": "AI",
                "description": "", "tags": [], "source": None, "duration": None, "first_start": None,
                "requires": {"license": ["feature-1"]}, "accounts": [], "setups": [],
                "includes": ["agent-lens"], "addons": ["agent-lens"],
                "services": ["hello-app", "seed"], "apps": ["hello-app"], "seed": "seed",
                "restarts": ["apim-gateway"], "file": "playgrounds/welcome/compose.yml", "guide": "0123456789ab",
                "chapters": []}
    groups = {"API Management": ["apim-gateway", "apim-management-api"], "Databases": ["mongodb"]}
    services = {name: _service(name, {"kind": "platform", "id": paths.slug(group), "name": group})
                for group, names in groups.items() for name in names}
    for addon in addons:
        owner = {"kind": "addon", "id": addon["id"], "name": addon["name"]}
        services.update({name: _service(name, owner, oneshot=name.endswith("-migrate"), console=addon["name"])
                         for name in addon["services"]})
    owner = {"kind": "playground", "id": "welcome", "name": "Welcome"}
    services.update({"hello-app": _service("hello-app", owner, console="Hello"),
                     "seed": _service("seed", owner, oneshot=True, role="seed")})
    links = [{"label": c["label"], "url": c["url"], "port": None, "service": name, "section": s["owner"]["name"],
              "legacy": False} for name, s in services.items() for c in s["consoles"]]
    return {"name": "Gravitee platform", "categories": ["AI", "Other"], "core_loaded": True,
            "license_var": "GRAVITEE_LICENSE", "license_services": ["apim-gateway", "apim-management-api"],
            "license_usable": True,
            "groups": [{"id": paths.slug(name), "name": name, "services": names, "version": None, "registry": None,
                        "product": None, "file": "docker-compose.yml", "env_file": ".env", "settings": []}
                       for name, names in groups.items()],
            "products": {}, "addons": {addon["id"]: addon for addon in addons},
            "setups": {"observability": _setup("observability", "Observability", ["jade", "kiosk"],
                                               ["jade", "kiosk"]),
                       "events": _setup("events", "Events", ["kafka-desk"], ["kafka", "kafka-desk"]),
                       "inspect": _setup("inspect", "Inspect", ["agent-lens"], ["agent-lens"]),
                       "local/mine": _setup("local/mine", "Mine", ["kiosk"], ["kiosk"])},
            "playgrounds": {"welcome": welcome}, "services": services, "links": links,
            "core_variables": [], "problems": []}


def second(project):
    """A second playground, Demo, that brings Kiosk: its app and its seed."""
    project["playgrounds"]["demo"] = {**project["playgrounds"]["welcome"], "id": "demo", "name": "Demo",
                                      "addons": ["kiosk"], "restarts": [], "services": ["demo-app", "demo-seed"],
                                      "apps": ["demo-app"], "seed": "demo-seed",
                                      "file": "playgrounds/demo/compose.yml"}
    owner = {"kind": "playground", "id": "demo", "name": "Demo"}
    project["services"].update({"demo-app": _service("demo-app", owner),
                                "demo-seed": _service("demo-seed", owner, oneshot=True, role="seed")})


def licensed(project, usable):
    """The platform with an Alert Engine of its own group, which exits without a usable license: `usable` says whether
    there is one."""
    owner = {"kind": "platform", "id": "alert-engine", "name": "Alert Engine"}
    project["services"]["alert-engine"] = {**_service("alert-engine", owner), "needs_license": True}
    project["groups"].append({"id": "alert-engine", "name": "Alert Engine", "services": ["alert-engine"],
                              "version": None, "registry": None, "product": None, "file": "docker-compose.yml",
                              "env_file": ".env", "settings": []})
    project["license_usable"] = usable


def own(project):
    """What belongs to this machine: the add-on Whoami, and the playground Sandbox, which brings it."""
    project["addons"]["local/whoami"] = _addon("local/whoami", "Whoami", ["whoami"])
    project["services"]["whoami"] = _service("whoami", {"kind": "addon", "id": "local/whoami", "name": "Whoami"})
    project["playgrounds"]["local/sandbox"] = {**project["playgrounds"]["welcome"], "id": "local/sandbox",
                                               "name": "Sandbox", "personal": True, "addons": ["local/whoami"],
                                               "services": [], "apps": [], "seed": None, "restarts": [],
                                               "file": "playgrounds/local/sandbox/compose.yml"}


def on(project, explicit=(), playground=None, missing=(), locked=None, setups=()):
    """What layers.current() answers for this on file. `setups`: the ones it lists, `explicit` holding their
    includes."""
    brought = project["playgrounds"][playground]["addons"] if playground else []
    return {"playground": playground, "explicit": sorted(explicit), "setups": sorted(setups),
            "effective": sorted(layers.closure(project, [*explicit, *brought])),
            "missing": list(missing), "problems": [], "locked": locked}


def cloud(project):
    """The Hybrid gateway as a platform of Cloud runs it, with a Redis of its own, and Hotels, a playground that
    deploys to it: an app, and a seed that depends on the core as a seed does for This machine. The plan of a place
    is given the id of the gateway's add-on, and does not read its `gateway` key."""
    hybrid = project["addons"]["hybrid"]
    hybrid.update(services=["hybrid-redis", "apim-hybrid-gateway"],
                  gateway={"platform": "cloud", "service": "apim-hybrid-gateway", "token": "HYBRID_CLOUD_TOKEN",
                           "domains": ["localhost:8090", "apim-hybrid-gateway:8082"]})
    owner = {"kind": "addon", "id": "hybrid", "name": "Hybrid gateway"}
    del project["services"]["hybrid-gateway"]
    project["services"].update({name: _service(name, owner) for name in hybrid["services"]})
    project["services"]["apim-hybrid-gateway"]["depends_on"] = ["hybrid-redis"]
    project["playgrounds"]["hotels"] = {**project["playgrounds"]["welcome"], "id": "hotels", "name": "Hotels",
                                        "deploys_to": ["cloud"], "requires": {}, "includes": [], "addons": [],
                                        "restarts": [], "services": ["hotels-api", "hotels-seed"],
                                        "apps": ["hotels-api"], "seed": "hotels-seed",
                                        "file": "playgrounds/hotels/compose.yml"}
    owner = {"kind": "playground", "id": "hotels", "name": "Hotels"}
    project["services"].update({"hotels-api": _service("hotels-api", owner),
                                "hotels-seed": {**_service("hotels-seed", owner, oneshot=True, role="seed"),
                                                "depends_on": ["apim-management-api"]}})


def up(*addons, platform=True, playgrounds=(), stopped=(), created=(), project=None):
    """What model.up() answers: the platform with its groups but the `stopped` ones, these add-ons and playgrounds,
    their services running, their one-shots ended well. What runs or ended has a container, so do the services of
    the platform's stopped groups, and those of the add-ons and playgrounds `created` names: they are stopped. Of
    this `project`, or of the small one."""
    if project is None:
        project = lab()
        own(project)
        second(project)
    services = project["services"]
    groups = {group["id"]: group["services"] for group in project["groups"]}
    units = {**project["addons"], **project["playgrounds"]}
    mine = [name for uid in [*addons, *playgrounds] for name in units[uid]["services"]]
    running = {name for gid, names in groups.items() if platform and gid not in stopped for name in names}
    running |= {name for name in mine if not services[name]["oneshot"]}
    done = {name for name in mine if services[name]["oneshot"]}
    made = {name for gid in stopped for name in groups[gid]}
    made |= {name for uid in created for name in units[uid]["services"]}
    return {"platform": platform, "groups": set(groups) - set(stopped) if platform else set(),
            "addons": set(addons), "playgrounds": set(playgrounds), "services": running, "done": done,
            "created": running | done | made}


def row(kind, uid, name):
    return {"kind": kind, "id": uid, "name": name}


KAFKA, CONSOLE = row("addon", "kafka", "Kafka"), row("addon", "kafka-desk", "Kafka Desk")
JADE, KIOSK = row("addon", "jade", "Jade"), row("addon", "kiosk", "Kiosk")
INSPECTOR, REDPANDA = row("addon", "agent-lens", "AI Agent Lens"), row("addon", "redpanda", "Redpanda")
PLATFORM, APPS = row("platform", "platform", "Gravitee platform"), row("playground", "welcome", "Welcome apps")
DATA = row("data", "welcome", "What Welcome created in Gravitee")
DATABASES, MANAGEMENT = row("group", "databases", "Databases"), row("group", "api-management", "API Management")
GATEWAY, API = row("service", "apim-gateway", "Gateway"), row("service", "apim-management-api", "Management API")


class Sets(Case):

    def setUp(self):
        self.project = lab()

    def test_closure_adds_what_is_needed(self):
        self.assertEqual(layers.closure(self.project, ["kafka-desk", "jade"]),
                         {"kafka-desk", "kafka", "jade"})

    def test_closure_leaves_out_what_the_project_does_not_have(self):
        self.assertEqual(layers.closure(self.project, ["kafka", "gone"]), {"kafka"})

    def test_conflicts_are_symmetric(self):
        clash = [{"a": "agent-lens", "b": "jade", "reason": EXPORTER}]
        self.assertEqual(layers.conflicts(self.project, ["jade", "agent-lens", "kiosk"]), clash)
        self.assertEqual(layers.conflicts(self.project, ["agent-lens", "jade"]), clash)
        self.assertEqual(layers.conflicts(self.project, ["kafka", "redpanda"]),   # said by the second of the two
                         [{"a": "kafka", "b": "redpanda", "reason": "same ports"}])
        self.assertEqual(layers.conflicts(self.project, ["jade", "kafka"]), [])


class ServiceOf(Case):

    SERVICES = {
        "apelor-portal-beam": {"raw_image": "${PORTAL_REGISTRY:-${APIM_REGISTRY:-graviteeio}}/apim-portal-ui:${PORTAL_VERSION:-${APIM_VERSION:-4}}",
                                "tag": "4", "registry": "graviteeio", "owner": {"name": "Apelor Bio"}},
        "apim-gateway": {"raw_image": "${APIM_GATEWAY_VERSION:-${APIM_VERSION:-4}-debian}".join(["graviteeio/apim-gateway:", ""]),
                         "tag": "4-debian", "registry": None, "owner": {"name": "API Management"}},
        "apim-management-api": {"raw_image": "graviteeio/apim-management-api:${APIM_VERSION:-4}", "tag": "4", "registry": None,
                                "owner": {"name": "API Management"}},
    }

    def test_a_variable_belongs_to_the_service_that_sets_it_not_to_one_that_only_follows_it(self):
        # The portals of a playground follow API Management's version: sorting before it, they must not take its name
        project = {"services": self.SERVICES}
        self.assertEqual(layers.service_of(project, "APIM_VERSION")[0], "apim-management-api")
        self.assertEqual(layers.saved_image(project, "APIM_VERSION"), ("4", "API Management"))
        self.assertEqual(layers.service_of(project, "PORTAL_VERSION")[0], "apelor-portal-beam")
        self.assertEqual(layers.service_of(project, "APIM_GATEWAY_VERSION")[0], "apim-gateway")

    def test_a_variable_that_only_defaults_another_one_still_finds_a_service(self):
        services = {"kiosk": {"raw_image": "kiosk:${KIOSK_VERSION:-${ELASTICSEARCH_VERSION:-9}}", "tag": "9", "registry": None,
                               "owner": {"name": "Kiosk"}}}
        self.assertEqual(layers.service_of({"services": services}, "ELASTICSEARCH_VERSION")[0], "kiosk")
        self.assertIsNone(layers.service_of({"services": services}, "NO_SUCH_VERSION"))


class License(Case):
    """What needs a license that is not usable (`needs_license`) is not started: not named, not waited for, not an
    item to start. With a usable license it is a service as any other."""

    def setUp(self):
        self.project = lab()
        licensed(self.project, False)
        self.state = on(self.project, ["kafka"])

    def test_it_is_not_among_what_up_names(self):
        self.assertNotIn("alert-engine", layers.up_services(self.project, None, ["kafka"]))
        self.assertTrue(layers.held_back(self.project, "alert-engine"))
        self.assertFalse(layers.held_back(self.project, "kafka"))
        self.assertFalse(layers.held_back(self.project, "unknown"))   # the user's own on file may add one
        self.project["license_usable"] = True
        self.assertIn("alert-engine", layers.up_services(self.project, None, ["kafka"]))
        self.assertFalse(layers.held_back(self.project, "alert-engine"))

    def test_an_apply_starts_it_once_the_license_is_usable(self):
        # Alert Engine was held back, so it has no container; a license is saved since, and what runs reads it: the
        # apply makes the gateway again and starts Alert Engine, which it would not for another service with none
        states = {"apim-gateway": {"key": "running", "running": True}, "kafka": {"key": "running", "running": True},
                  "alert-engine": {"key": "absent", "running": False}}
        self.project["license_usable"] = True
        on_ = layers.up_services(self.project, None, ["kafka"])
        found = layers.applies(self.project, on_, states, {"apim-gateway"})
        self.assertEqual(found["names"], ["alert-engine", "apim-gateway"])
        self.assertEqual([row_["name"] for row_ in found["starts"]], ["Alert Engine"])
        # Held back still: not named
        self.project["license_usable"] = False
        on_ = layers.up_services(self.project, None, ["kafka"])
        self.assertEqual(layers.applies(self.project, on_, states, {"apim-gateway"})["names"], ["apim-gateway"])
        # Another service with no container is not created
        self.project["license_usable"] = True
        states["kafka-ui"] = {"key": "absent", "running": False}
        on_ = layers.up_services(self.project, None, ["kafka", "kafka-ui"])
        self.assertNotIn("kafka-ui", layers.applies(self.project, on_, states, {"apim-gateway"})["names"])

    def test_the_stack_s_start_does_not_count_it(self):
        # Everything else runs: nothing is left to start, the platform is not asked to
        running = up("kafka", "agent-lens", playgrounds=["welcome"])
        state = on(self.project, ["kafka"], "welcome")
        self.assertEqual(layers.platform_starts(self.project, state, running), [])
        self.assertEqual(layers.platform_starts(self.project, state, up(platform=False))[0], PLATFORM)
        # With a license it is a group to start, as any other
        self.project["license_usable"] = True
        alert = row("group", "alert-engine", "Alert Engine")
        self.assertEqual(layers.platform_starts(self.project, state, running), [alert])

    def test_a_start_runs_and_waits_for_the_others_only(self):
        pairs = {"start": [["platform", "platform"], ["addon", "kafka"]]}
        everything = {"apim-gateway", "apim-management-api", "mongodb", "kafka", "kafka-ui"}
        self.assertEqual(set(layers.changing(self.project, pairs)), everything)
        self.assertEqual({unit[1] for unit in layers.awaits(self.project, pairs)},
                         {f"lab-{name}" for name in everything})
        self.project["license_usable"] = True
        self.assertEqual(set(layers.changing(self.project, pairs)), everything | {"alert-engine"})
        self.assertIn(("Container", "lab-alert-engine"), layers.awaits(self.project, pairs))

    def test_the_platform_starts_whole_without_it(self):
        # A group that cannot start is not part of what the platform is: the dialog of a start says "Gravitee platform"
        # as before, not each of the groups that start
        plan = layers.plan(self.project, self.state, up(platform=False), "playground", "welcome", "start")
        self.assertEqual(plan["starts"][0], PLATFORM)
        self.assertNotIn("alert-engine", plan["services"]["start"])
        self.assertIn("apim-gateway", plan["services"]["start"])
        # With a license the platform includes it, and the rows are the same
        self.project["license_usable"] = True
        plan = layers.plan(self.project, self.state, up(platform=False), "playground", "welcome", "start")
        self.assertEqual((plan["starts"][0], "alert-engine" in plan["services"]["start"]), (PLATFORM, True))
        # Part of the groups started: each group that starts is named, as it was
        self.project["license_usable"] = False
        names = {"apim-gateway", "apim-management-api"}
        self.assertEqual(layers.start_rows(self.project, names), [row("group", "api-management", "API Management")])
        self.assertEqual(layers.start_rows(self.project, names | {"mongodb"}), [PLATFORM])
        # Alone, it is the group it is, not the platform
        self.assertEqual(layers.start_rows(self.project, {"alert-engine"}), [row("group", "alert-engine", "Alert Engine")])

    def test_a_stop_still_covers_it(self):
        # What the Lab UI does not start it does not leave running either: a stop reaches every container there is
        pairs = {"stop": [["platform", "platform"]]}
        self.assertEqual(layers.changing(self.project, pairs)["alert-engine"], "stop")
        self.assertIn(("Container", "lab-alert-engine"), layers.awaits(self.project, pairs))

    def test_what_depends_on_it_starts_without_it(self):
        self.project["services"]["apim-gateway"]["depends_on"] = ["alert-engine"]
        on_top = set(layers.up_services(self.project, None, []))
        self.assertEqual(layers.depends(self.project, on_top, ["apim-gateway"]), set())
        self.project["license_usable"] = True
        on_top = set(layers.up_services(self.project, None, []))
        self.assertEqual(layers.depends(self.project, on_top, ["apim-gateway"]), {"alert-engine"})


class Plans(Case):

    def setUp(self):
        self.project = lab()
        self.patch(layout, "waiting", return_value=False)   # whatever tree this runs in: no old layout is being moved

    def plan(self, state, running, kind, uid, do, fresh=False):
        return layers.plan(self.project, state, running, kind, uid, do, fresh=fresh)

    def check(self, plan, **expected):
        rows = {"starts": [], "stops": [], "turns_off": [], "keeps": [], "restarts": [], "blocked": None, **expected}
        self.assertEqual({key: plan[key] for key in rows}, rows)

    # --- A start runs the item, what it brings and needs, and restarts the core services it changes ----------------

    def test_start_with_a_dependency(self):
        plan = self.plan(on(self.project), up(), "addon", "kafka-desk", "start")
        self.check(plan, starts=[KAFKA, CONSOLE], restarts=[GATEWAY, API], playground=None, explicit=["kafka-desk"])
        self.assertEqual((plan["do"], plan["kind"], plan["id"], plan["label"]),
                         ("start", "addon", "kafka-desk", "Start"))
        # By name: what starts and what restarts, nothing else
        self.assertEqual(plan["services"], {"remove": [], "start": ["apim-gateway", "apim-management-api", "kafka",
                                                                    "kafka-desk", "kafka-ui"]})

    def test_start_stops_the_explicit_add_on_in_conflict(self):
        state = on(self.project, ["jade", "kiosk"])
        plan = self.plan(state, up("jade", "kiosk"), "addon", "agent-lens", "start")
        self.check(plan, starts=[INSPECTOR], stops=[JADE], restarts=[GATEWAY], playground=None,
                   explicit=["agent-lens", "kiosk"])
        # What goes is removed by name
        self.assertEqual(plan["services"], {"remove": ["jade"], "start": ["agent-lens", "apim-gateway"]})

    def test_start_turns_off_what_is_on_and_does_not_run(self):
        # The platform stopped from the Stack, Jade on: it does not run, it is turned off. Nothing restarts
        plan = self.plan(on(self.project, ["jade"]), up(platform=False, created=["jade"]), "addon",
                         "agent-lens", "start")
        self.check(plan, starts=[INSPECTOR], turns_off=[JADE], playground=None, explicit=["agent-lens"])
        self.assertEqual(plan["services"], {"remove": ["jade"], "start": ["agent-lens"]})

    def test_start_stops_what_needs_the_add_on_in_conflict(self):
        state = on(self.project, ["kafka-desk"])
        plan = self.plan(state, up("kafka", "kafka-desk"), "addon", "redpanda", "start")
        self.check(plan, starts=[REDPANDA], stops=[KAFKA, CONSOLE], restarts=[GATEWAY, API], playground=None,
                   explicit=["redpanda"])

    def test_start_stops_the_playground_in_conflict(self):
        state = on(self.project, ["kiosk"], "welcome")
        plan = self.plan(state, up("agent-lens", "kiosk", playgrounds=["welcome"]), "addon", "jade", "start")
        self.check(plan, starts=[JADE], stops=[APPS, INSPECTOR], keeps=[DATA], restarts=[GATEWAY],
                   playground=None, explicit=["jade", "kiosk"])
        # Its apps and its seed go with the add-on only it brought
        self.assertEqual(plan["services"]["remove"], ["agent-lens", "hello-app", "seed"])

    def test_start_setup_turns_its_includes_on(self):
        plan = self.plan(on(self.project), up(), "setup", "events", "start")
        self.check(plan, starts=[KAFKA, CONSOLE], restarts=[GATEWAY, API], playground=None, explicit=["kafka-desk"])
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "setup", "observability", "start")
        self.check(plan, starts=[JADE, KIOSK], stops=[APPS, INSPECTOR], keeps=[DATA], restarts=[GATEWAY],
                   playground=None, explicit=["jade", "kiosk"])

    def test_start_leaves_the_running_playground_alone(self):
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "addon", "kafka", "start")
        self.check(plan, starts=[KAFKA], restarts=[GATEWAY], playground="welcome", explicit=["kafka"])

    def test_start_again_restarts_what_a_start_that_failed_left_behind(self):
        # Kafka's start turned it on, then failed before the gateway was made again: on, Kafka no longer comes, yet
        # its start restarts the gateway, which runs behind what is saved; nothing else of the platform
        state, running = on(self.project, ["kafka"]), up()
        plan = layers.plan(self.project, state, running, "addon", "kafka", "start", behind={"apim-gateway", "mongodb"})
        self.check(plan, starts=[KAFKA], restarts=[GATEWAY], playground=None, explicit=["kafka"])
        self.check(self.plan(state, running, "addon", "kafka", "start"), starts=[KAFKA], playground=None,
                   explicit=["kafka"])

    def test_a_playground_waits_for_the_image_versions_it_needs(self):
        # On older images it would end on its seed after the whole setup: its Start says what to choose first
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        welcome["requires"] = {"images": {"APIM_VERSION": "4.13"}}
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.12.20")
        owner = gateway["owner"]["name"]
        plan = self.plan(on(self.project), up(), "playground", "welcome", "start")
        self.assertEqual(plan["blocked"], f"Needs {owner} 4.13 or later: choose it on the Images page")
        # A preview is before its release: it is not a 4.13, unless the playground lists it as tested
        gateway["tag"] = "4.13.0-alpha.3"
        self.assertEqual(self.plan(on(self.project), up(), "playground", "welcome", "start")["blocked"],
                         f"Needs {owner} 4.13 or later: choose it on the Images page")
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.3"}]
        self.assertIsNone(self.plan(on(self.project), up(), "playground", "welcome", "start")["blocked"])
        welcome["tested"] = []
        gateway["tag"] = "4.13.0"
        self.assertIsNone(self.plan(on(self.project), up(), "playground", "welcome", "start")["blocked"])
        # A stop never waits for it
        gateway["tag"] = "4.12.20"
        self.assertIsNone(self.plan(on(self.project, playground="welcome"), up(), "playground", "welcome", "stop")["blocked"])

    def test_every_image_that_is_too_old_is_named_at_once(self):
        # One visit to the Images page, not one for each image
        welcome = self.project["playgrounds"]["welcome"]
        gateway, access = self.project["services"]["apim-gateway"], self.project["services"]["mongodb"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.12.20")
        access.update(raw_image="graviteeio/am-gateway:${AM_VERSION:-4.12.7}", tag="4.12.7",
                      owner={"kind": "platform", "id": "access-management", "name": "Access Management"})
        welcome["requires"] = {"images": {"APIM_VERSION": "4.13.0-alpha.3", "AM_VERSION": "4.13"}}
        both = "Needs API Management 4.13.0-alpha.3 or later and Access Management 4.13 or later: choose them on " \
               "the Images page"
        self.assertEqual(layers.too_old(self.project, welcome), both)
        self.assertEqual(self.plan(on(self.project), up(), "playground", "welcome", "start")["blocked"], both)
        gateway["tag"] = "4.13.0-alpha.4"
        self.assertEqual(layers.too_old(self.project, welcome),
                         "Needs Access Management 4.13 or later: choose it on the Images page")
        access["tag"] = "4.13.0-alpha.7"
        self.assertEqual(layers.too_old(self.project, welcome), "Needs Access Management 4.13 or later: choose it on the Images page")
        access["tag"] = "4.13.0"
        self.assertIsNone(layers.too_old(self.project, welcome))
        # An image no service uses is not asked for
        welcome["requires"] = {"images": {"NOT_USED_VERSION": "9"}}
        self.assertIsNone(layers.too_old(self.project, welcome))

    def test_a_preview_is_tested_only_as_it_is_and_a_release_with_the_releases_of_its_minor_version(self):
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}")
        for tested, cases in (
                # Previews, which say nothing of what comes after them
                ([{"APIM_VERSION": "4.13.0-alpha.1"}, {"APIM_VERSION": "4.13.0-alpha.3"}],
                 (("4.13.0-alpha.1", True), ("4.13.0-alpha.3", True), ("4.13.0-alpha.3-debian", True), ("4.13.0-alpha.2", False),
                  ("4.13.0", False), ("4.14.2", False), ("4.12.21", False), ("5.0.0", False), ("amp-demo-latest", False),
                  ("amp-demo-latest-debian", False), ("latest", False), ("4.13.0-amp-demo-SNAPSHOT", False))),
                # A release: the releases of its minor version
                ([{"APIM_VERSION": "4.13.0"}],
                 (("4.13.0", True), ("4.13.0-debian", True), ("4.13.7", True), ("4.13.7-debian", True), ("4.14.0", False),
                  ("4.12.21", False), ("5.0.0", False), ("4.13.0-alpha.3", False), ("4.13.0-amp-demo-SNAPSHOT", False),
                  ("master-latest", False)))):
            welcome["tested"] = tested
            for tag, fine in cases:
                gateway["tag"] = tag
                self.assertEqual(layers.untested(self.project, welcome) is None, fine, f"{tested} {tag}")

    def test_the_stack_goes_to_the_latest_set_it_was_tested_with(self):
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="amp-demo-latest")
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.1"}, {"APIM_VERSION": "4.13.0-alpha.3"}]
        owner = gateway["owner"]["name"]
        self.assertEqual(layers.untested(self.project, welcome), {
            "images": [{"variable": "APIM_VERSION", "name": owner, "have": "amp-demo-latest", "tested": "4.13.0-alpha.3",
                        "goes_back": True}],
            "to": {"APIM_VERSION": "4.13.0-alpha.3"}})
        # On an older version it does not go back: what it wrote opens on a later one
        gateway["tag"] = "4.12.21"
        self.assertFalse(layers.untested(self.project, welcome)["images"][0]["goes_back"])
        # On a later one it does
        gateway["tag"] = "4.14.0"
        self.assertTrue(layers.untested(self.project, welcome)["images"][0]["goes_back"])

    def test_a_version_without_numbers_to_go_to_may_be_going_back(self):
        # A playground built on a demo build: another version is a switch to a build that says nothing of its numbers
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.13.0-alpha.5")
        welcome["tested"] = [{"APIM_VERSION": "amp-demo-latest"}]
        found = layers.untested(self.project, welcome)
        self.assertEqual((found["images"][0]["tested"], found["images"][0]["goes_back"]), ("amp-demo-latest", True))
        gateway["tag"] = "amp-demo-latest"
        self.assertIsNone(layers.untested(self.project, welcome), "the build it was tested on")

    def test_a_set_can_name_the_registry_a_version_is_in(self):
        # A tag is in one registry: the same tag in another is not what was tested, and a switch goes to both
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="${APIM_REGISTRY:-graviteeio}/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.13.0-alpha.5",
                       registry="graviteeio")
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.5", "APIM_REGISTRY": "graviteeio"}]
        self.assertIsNone(layers.untested(self.project, welcome))
        gateway["registry"] = "graviteeio.azurecr.io"
        found = layers.untested(self.project, welcome)
        self.assertEqual([(one["variable"], one["have"], one["tested"], one["goes_back"]) for one in found["images"]],
                         [("APIM_REGISTRY", "graviteeio.azurecr.io", "graviteeio", False)])
        self.assertEqual(found["to"], {"APIM_REGISTRY": "graviteeio"})
        gateway.update(registry="graviteeio.azurecr.io", tag="amp-demo-latest")
        self.assertEqual(sorted(layers.untested(self.project, welcome)["to"]), ["APIM_REGISTRY", "APIM_VERSION"])

    def test_a_set_is_tested_as_a_whole(self):
        # An image that is tested alone is not a stack that was: the sets are what was checked together
        welcome = self.project["playgrounds"]["welcome"]
        gateway, access = self.project["services"]["apim-gateway"], self.project["services"]["mongodb"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}")
        access.update(raw_image="graviteeio/am-gateway:${AM_VERSION:-4.12.7}",
                      owner={"kind": "platform", "id": "access-management", "name": "Access Management"})
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.3", "AM_VERSION": "4.13.0-alpha.4"},
                             {"APIM_VERSION": "4.13.0-alpha.7", "AM_VERSION": "4.13.0-alpha.7"}]
        for apim, am, fine in (("4.13.0-alpha.3", "4.13.0-alpha.4", True), ("4.13.0-alpha.7", "4.13.0-alpha.7", True),
                               ("4.13.0-alpha.3", "4.13.0-alpha.7", False), ("4.13.0-alpha.7", "4.13.0-alpha.4", False)):
            gateway["tag"], access["tag"] = apim, am
            self.assertEqual(layers.untested(self.project, welcome) is None, fine, f"{apim} {am}")
        # Not together: the whole of the latest set, and only what differs is changed
        gateway["tag"], access["tag"] = "4.13.0-alpha.3", "4.13.0-alpha.7"
        found = layers.untested(self.project, welcome)
        self.assertEqual(found["to"], {"APIM_VERSION": "4.13.0-alpha.7"})
        self.assertEqual([one["variable"] for one in found["images"]], ["APIM_VERSION"])

    def test_a_playground_that_names_nothing_has_nothing_to_be_untested_on(self):
        welcome = self.project["playgrounds"]["welcome"]
        welcome["tested"], welcome["requires"] = [], {}
        self.assertIsNone(layers.untested(self.project, welcome))
        # nor is an image no service uses
        welcome["tested"] = [{"NOT_USED_VERSION": "9"}]
        self.assertIsNone(layers.untested(self.project, welcome))

    def test_a_version_that_is_not_a_release_is_untested_unless_the_playground_names_it(self):
        # Major.minor.patch is the only version taken as it is: a pre-release, a demo build and a moving tag are not, whatever
        # their numbers say, and fine only as the playground says them (required or tested). A release is not asked about.
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}")
        welcome["tested"] = []
        owner = gateway["owner"]["name"]
        for asked, cases in (("4.12", (("4.13.0", True), ("4.13.7-debian", True), ("4.13.0-alpha.5", False), ("amp-demo-latest", False),
                                       ("latest", False), ("4.13", False), ("4.13.0-amp-demo-SNAPSHOT", False))),
                             ("4.13.0-alpha.3", (("4.13.0-alpha.3", True), ("4.13.0-alpha.3-debian", True), ("4.13.0-alpha.5", False),
                                                 ("4.13.0", True))),
                             ("amp-demo-latest", (("amp-demo-latest", True), ("latest", False)))):
            welcome["requires"] = {"images": {"APIM_VERSION": asked}}
            for tag, fine in cases:
                gateway["tag"] = tag
                self.assertEqual(layers.untested(self.project, welcome) is None, fine, f"{asked} {tag}")
        # It has no version to go to, and says so
        welcome["requires"] = {"images": {"APIM_VERSION": "4.13"}}
        gateway["tag"] = "4.13.0-alpha.5"
        self.assertEqual(layers.untested(self.project, welcome), {
            "images": [{"variable": "APIM_VERSION", "name": owner, "have": "4.13.0-alpha.5", "tested": None, "goes_back": False}],
            "to": {}})
        # Tested, it is named: the playground says it works on it
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.5"}]
        self.assertIsNone(layers.untested(self.project, welcome))

    def test_three_images_that_are_too_old_are_listed_as_a_sentence(self):
        saved = {"A_VERSION": ("1", "Alpha"), "B_VERSION": ("1", "Beta"), "C_VERSION": ("1", "Gamma")}
        self.patch(layers, "saved_image", lambda project, variable: saved[variable])
        unit = {"requires": {"images": {"A_VERSION": "2", "B_VERSION": "2", "C_VERSION": "2"}}}
        self.assertEqual(layers.too_old(self.project, unit), "Needs Alpha 2 or later, Beta 2 or later and Gamma 2 "
                                                             "or later: choose them on the Images page")

    def test_start_with_missing_settings_is_blocked(self):
        plan = self.plan(on(self.project, missing=["hybrid"]), up(), "addon", "hybrid", "start")
        self.assertEqual(plan["blocked"], "Hybrid gateway needs settings")
        plan = self.plan(on(self.project, missing=["kafka"]), up(), "setup", "events", "start")
        self.assertEqual(plan["blocked"], "Kafka needs settings")
        plan = self.plan(on(self.project, missing=["hybrid"]), up(), "addon", "kafka", "start")
        self.assertIsNone(plan["blocked"])

    def test_retry_leaves_the_on_file(self):
        # On, it starts what of it does not run and what it needs: not the rest of what is on
        state = on(self.project, ["kafka-desk"])
        plan = self.plan(state, up("kafka"), "addon", "kafka-desk", "start")
        self.check(plan, starts=[CONSOLE], playground=None, explicit=["kafka-desk"])
        plan = self.plan(state, up(), "addon", "kafka", "start")   # on through Kafka Desk: not made explicit
        self.check(plan, starts=[KAFKA], playground=None, explicit=["kafka-desk"])

    def test_start_leaves_what_was_started_outside_the_lab_ui(self):
        # Kiosk runs without being on: Jade's start leaves it running
        plan = self.plan(on(self.project), up("kiosk"), "addon", "jade", "start")
        self.check(plan, starts=[JADE], restarts=[GATEWAY], playground=None, explicit=["jade"])
        self.assertEqual(plan["services"], {"remove": [], "start": ["apim-gateway", "jade"]})

    def test_nothing_stopped_on_purpose_comes_back(self):
        # Databases was stopped from the Stack: a start or a stop that does not need it leaves it stopped
        plan = self.plan(on(self.project), up(stopped=["databases"]), "addon", "jade", "start")
        self.check(plan, starts=[JADE], restarts=[GATEWAY], playground=None, explicit=["jade"])
        state = on(self.project, ["jade", "kiosk"])
        plan = self.plan(state, up("jade", "kiosk", stopped=["databases"]), "addon", "jade", "stop")
        self.check(plan, stops=[JADE], restarts=[GATEWAY], playground=None, explicit=["kiosk"])
        self.assertEqual(layers.changes(self.project, plan),
                         {"start": [["service", "apim-gateway"]], "stop": [["addon", "jade"]]})
        # Nor a core service the item changes: stopped, it takes the change when it starts
        plan = self.plan(on(self.project), up(stopped=["api-management"]), "addon", "jade", "start")
        self.check(plan, starts=[JADE], playground=None, explicit=["jade"])

    def test_a_start_runs_what_it_depends_on_that_does_not_run(self):
        # Kafka Desk depends on Kafka and the management API, which depends on MongoDB. API Management stopped from
        # the Stack: the management API starts first, and says so; MongoDB runs, the gateway stays stopped
        services = self.project["services"]
        services["kafka-desk"]["depends_on"] = ["apim-management-api", "kafka"]
        services["apim-management-api"]["depends_on"] = ["mongodb"]
        plan = self.plan(on(self.project), up(stopped=["api-management"]), "addon", "kafka-desk", "start")
        self.check(plan, starts=[API, KAFKA, CONSOLE], playground=None, explicit=["kafka-desk"])
        self.assertEqual(plan["services"]["start"], ["apim-management-api", "kafka", "kafka-desk", "kafka-ui"])
        # Nothing runs: MongoDB too, its whole group; then, all of the platform starting, the platform
        plan = self.plan(on(self.project), up(platform=False), "addon", "kafka-desk", "start")
        self.check(plan, starts=[API, DATABASES, KAFKA, CONSOLE], playground=None, explicit=["kafka-desk"])
        services["kafka-desk"]["depends_on"] = ["apim-gateway", "kafka"]
        services["apim-gateway"]["depends_on"] = ["apim-management-api"]
        plan = self.plan(on(self.project), up(platform=False), "addon", "kafka-desk", "start")
        self.check(plan, starts=[PLATFORM, KAFKA, CONSOLE], playground=None, explicit=["kafka-desk"])
        # What an add-on that is off makes a core service depend on does not count: it is not part of what runs
        services["apim-gateway"]["depends_on"] = ["apim-management-api", "jade"]
        plan = self.plan(on(self.project), up(platform=False), "addon", "kafka-desk", "start")
        self.assertNotIn("jade", plan["services"]["start"])

    def test_the_one_shots_what_starts_depends_on_run_again_first(self):
        # OpenFGA's migration ran, then OpenFGA and its database stopped: its start runs the migration again before
        # it, and the database before the migration, as Compose would without --no-deps; a migration has nothing more
        # to do the second time. Whether it ran or not, alike
        services, openfga = self.project["services"], self.project["addons"]["openfga"]
        services["openfga-db"] = _service("openfga-db", services["openfga"]["owner"])
        openfga["services"].append("openfga-db")
        services["openfga"]["depends_on"] = ["openfga-migrate"]
        services["openfga-migrate"]["depends_on"] = ["openfga-db"]
        state = on(self.project, ["openfga"])
        running = up("openfga")
        running["services"] -= {"openfga", "openfga-db"}
        for found in (running, up()):
            plan = self.plan(state, found, "addon", "openfga", "start")
            self.assertEqual(plan["services"]["start"], ["openfga", "openfga-db", "openfga-migrate"])
        # What a start restarts runs its one-shots first too: Kafka restarts the gateway, which depends on one here
        services["apim-gateway"]["depends_on"] = ["openfga"]
        running = up("openfga")
        running["services"].add("openfga-db")
        plan = self.plan(on(self.project, ["openfga"]), running, "addon", "kafka", "start")
        self.assertEqual(plan["services"]["start"], ["apim-gateway", "kafka", "kafka-ui", "openfga-migrate"])

    # --- A stop removes what goes by name and restarts the core services it changed --------------------------------

    def test_stop_add_on(self):
        state = on(self.project, ["jade", "kiosk"])
        plan = self.plan(state, up("jade", "kiosk"), "addon", "jade", "stop")
        self.check(plan, stops=[JADE], restarts=[GATEWAY], playground=None, explicit=["kiosk"])
        # Its container removed, then the gateway made again without its settings
        self.assertEqual((plan["label"], plan["services"]), ("Stop", {"remove": ["jade"], "start": ["apim-gateway"]}))

    def test_stop_takes_what_needs_it(self):
        state = on(self.project, ["kafka-desk", "kiosk"])
        plan = self.plan(state, up("kafka", "kafka-desk", "kiosk"), "addon", "kafka", "stop")
        self.check(plan, stops=[KAFKA, CONSOLE], restarts=[GATEWAY, API], playground=None, explicit=["kiosk"])

    def test_stop_takes_the_playground_that_needs_it(self):
        state = on(self.project, ["kiosk"], "welcome")
        running = up("agent-lens", "kiosk", playgrounds=["welcome"])
        plan = self.plan(state, running, "addon", "agent-lens", "stop")
        self.check(plan, stops=[APPS, INSPECTOR], keeps=[DATA], restarts=[GATEWAY], playground=None,
                   explicit=["kiosk"])

    def test_turn_off_what_is_on_and_does_not_run(self):
        # The platform stopped from the Stack, Kafka Desk on: its stop turns it off with Kafka, removing their
        # stopped containers; nothing restarts, nothing starts
        state = on(self.project, ["kafka-desk"])
        running = up(platform=False, stopped=["api-management", "databases"], created=["kafka", "kafka-desk"])
        plan = self.plan(state, running, "addon", "kafka-desk", "stop")
        self.check(plan, turns_off=[KAFKA, CONSOLE], playground=None, explicit=[])
        self.assertEqual((plan["label"], plan["services"]),
                         ("Turn off", {"remove": ["kafka", "kafka-desk", "kafka-ui"], "start": []}))

    def test_a_stop_starts_nothing(self):
        # Kiosk is on and does not run: Jade's stop leaves it as it is
        state = on(self.project, ["jade", "kiosk"])
        plan = self.plan(state, up("jade"), "addon", "jade", "stop")
        self.check(plan, stops=[JADE], restarts=[GATEWAY], playground=None, explicit=["kiosk"])

    def test_a_stop_takes_the_item_that_runs_without_being_on(self):
        # A stop of Kafka failed once the on file was written: off, it still runs. Its stop again removes it
        plan = self.plan(on(self.project), up("kafka"), "addon", "kafka", "stop")
        self.check(plan, stops=[KAFKA], playground=None, explicit=[])
        self.assertEqual((plan["label"], plan["services"]), ("Stop", {"remove": ["kafka", "kafka-ui"], "start": []}))

    def test_stop_setup_stops_its_members(self):
        state = on(self.project, ["jade", "kafka", "kafka-desk", "kiosk"])   # Kafka is on by itself too
        running = up("jade", "kafka", "kafka-desk", "kiosk")
        self.check(self.plan(state, running, "setup", "observability", "stop"), stops=[JADE, KIOSK],
                   restarts=[GATEWAY], playground=None, explicit=["kafka", "kafka-desk"])
        self.check(self.plan(state, running, "setup", "events", "stop"), stops=[KAFKA, CONSOLE],
                   restarts=[GATEWAY, API], playground=None, explicit=["jade", "kiosk"])

    def test_a_setup_s_stop_keeps_what_something_else_that_is_on_uses(self):
        # Mine (Kafka, Kiosk) and Events (Kafka Desk, Kafka) were both started: Events' stop keeps Kafka
        self.project["setups"]["local/mine"].update(includes=["kafka", "kiosk"], members=["kafka", "kiosk"])
        state = on(self.project, ["kafka", "kafka-desk", "kiosk"])
        plan = self.plan(state, up("kafka", "kafka-desk", "kiosk"), "setup", "events", "stop")
        self.check(plan, stops=[CONSOLE], keeps=[KAFKA], restarts=[API], playground=None, explicit=["kafka", "kiosk"])
        # All of it used by what else is on: nothing to stop, and it says what uses it
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "setup", "inspect", "stop")
        self.check(plan, keeps=[INSPECTOR], blocked="Used by Welcome", playground="welcome", explicit=[])
        self.project["setups"]["just-kafka"] = {**self.project["setups"]["inspect"], "id": "just-kafka",
                                                "name": "Just Kafka", "includes": ["kafka"], "members": ["kafka"]}
        plan = self.plan(on(self.project, ["kafka-desk"]), up("kafka", "kafka-desk"), "setup", "just-kafka",
                         "stop")   # Kafka Desk needs it, and Events, which was started, has it
        self.check(plan, keeps=[KAFKA], blocked="Used by Events, Kafka Desk", playground=None,
                   explicit=["kafka-desk"])
        # Another setup whose add-ons only this one turned on does not keep them
        state = on(self.project, ["jade", "kiosk"])
        self.project["setups"]["local/mine"].update(includes=["kiosk"], members=["kiosk"])
        plan = self.plan(state, up("jade", "kiosk"), "setup", "observability", "stop")
        self.check(plan, stops=[JADE, KIOSK], restarts=[GATEWAY], playground=None, explicit=[])

    def test_a_setup_s_stop_says_what_was_turned_on_by_itself(self):
        # Jade turned on by itself, Kiosk off: Observability is not on, Jade is not the setup
        plan = self.plan(on(self.project, ["jade"]), up("jade"), "setup", "observability", "stop")
        self.check(plan, stops=[JADE], restarts=[GATEWAY], playground=None, explicit=[])
        self.assertEqual((plan["itself"], plan["label"]), ({"setup:observability"}, "Stop"))
        with self.assertRaisesRegex(RuntimeError, "Would also stop Jade"):
            layers.check(self.project, plan, [])
        # On, an add-on of it turned on by itself is not the setup either: Kafka, Events having turned on Kafka Desk
        state = on(self.project, ["kafka", "kafka-desk"])
        plan = self.plan(state, up("kafka", "kafka-desk"), "setup", "events", "stop")
        self.assertEqual(plan["itself"], {"setup:events", "addon:kafka-desk"})

    def test_labels(self):
        # Stop while it runs, Turn off while it is on and does not, Turn on for what runs without being on
        cases = [(["kafka"], up("kafka"), "addon", "kafka", "stop", "Stop"),
                 (["kafka"], up(), "addon", "kafka", "stop", "Turn off"),
                 ([], up("kiosk"), "addon", "kiosk", "start", "Turn on"),
                 ([], up(), "addon", "kiosk", "start", "Start"),
                 (["jade"], up("jade", "kiosk"), "setup", "observability", "start", "Turn on"),
                 (["jade"], up("jade"), "setup", "observability", "start", "Start"),
                 # One of its add-ons runs without being on, the other is off: as an add-on that runs
                 ([], up("kiosk"), "setup", "observability", "start", "Turn on"),
                 (["jade"], up(), "setup", "observability", "stop", "Turn off")]
        for explicit, running, kind, uid, do, label in cases:
            with self.subTest(uid=uid, do=do, label=label):
                self.assertEqual(self.plan(on(self.project, explicit), running, kind, uid, do)["label"], label)
        state = on(self.project, playground="welcome")
        self.assertEqual(self.plan(state, up(), "playground", "welcome", "stop")["label"], "Turn off")
        self.assertEqual(self.plan(state, up(playgrounds=["welcome"]), "playground", "welcome", "stop")["label"],
                         "Stop")

    # --- Playgrounds ---------------------------------------------------------------------------------------------

    def test_playground_start(self):
        # Set up (`fresh`): its app and its seed, the add-on it brings, and the platform, which it runs with
        state = on(self.project, ["jade", "kiosk"])
        plan = self.plan(state, up("jade", "kiosk"), "playground", "welcome", "start", fresh=True)
        self.check(plan, starts=[APPS, INSPECTOR], stops=[JADE], restarts=[GATEWAY], playground="welcome",
                   explicit=["kiosk"])
        self.assertEqual(plan["services"], {"remove": ["jade"],
                                            "start": ["agent-lens", "apim-gateway", "hello-app", "seed"]})
        plan = self.plan(on(self.project, missing=["agent-lens"]), up(), "playground", "welcome", "start")
        self.assertEqual(plan["blocked"], "AI Agent Lens needs settings")
        # Nothing runs: all of the platform starts with it
        plan = self.plan(on(self.project), up(platform=False), "playground", "welcome", "start", fresh=True)
        self.check(plan, starts=[PLATFORM, APPS, INSPECTOR], playground="welcome", explicit=[])

    def test_playground_start_stops_the_other_one(self):
        second(self.project)
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "playground", "demo", "start",
                         fresh=True)
        self.check(plan, starts=[row("playground", "demo", "Demo apps"), KIOSK], stops=[APPS, INSPECTOR],
                   keeps=[DATA], restarts=[GATEWAY], playground="demo", explicit=[])

    def test_playground_start_again(self):
        # On and seeded: what of it does not run, without its seed
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens"), "playground", "welcome", "start")
        self.check(plan, starts=[APPS], playground="welcome", explicit=[])
        self.assertEqual((plan["services"]["start"], plan["fresh"]), (["hello-app"], False))

    def test_a_playground_is_set_up_while_it_is_off_or_never_seeded(self):
        state = on(self.project, playground="welcome")
        self.assertTrue(layers.fresh(self.project, on(self.project), "welcome", []))
        self.assertFalse(layers.fresh(self.project, state, "welcome", [{"what": "APIs", "count": 3}]))
        self.assertTrue(layers.fresh(self.project, state, "welcome", []))   # never seeded, or reset since
        # Its seed failed after it recorded something: its start runs it again
        self.assertTrue(layers.fresh(self.project, state, "welcome", [{"what": "APIs", "count": 3}], True))
        own(self.project)   # Sandbox has no seed: on, it is set up
        self.assertFalse(layers.fresh(self.project, on(self.project, playground="local/sandbox"), "local/sandbox", []))
        self.assertFalse(layers.fresh(self.project, on(self.project, playground="local/sandbox"), "local/sandbox", [],
                                      True))

    def test_a_playground_starts_the_platform_only_while_none_of_it_runs(self):
        # Databases stopped from the Stack, API Management runs: the playground's start leaves Databases as it is, unless
        # its app or its seed needs it; while nothing of the platform runs, all of it starts with it
        running = up(stopped=["databases"])
        plan = self.plan(on(self.project), running, "playground", "welcome", "start", fresh=True)
        self.check(plan, starts=[APPS, INSPECTOR], restarts=[GATEWAY], playground="welcome", explicit=[])
        self.project["services"]["seed"]["depends_on"] = ["mongodb"]
        plan = self.plan(on(self.project), running, "playground", "welcome", "start", fresh=True)
        self.check(plan, starts=[DATABASES, APPS, INSPECTOR], restarts=[GATEWAY], playground="welcome", explicit=[])
        plan = self.plan(on(self.project), up(platform=False), "playground", "welcome", "start", fresh=True)
        self.assertEqual(plan["starts"], [PLATFORM, APPS, INSPECTOR])

    def test_one_app_is_not_all_of_them(self):
        # The apps of a playground are one row only when they all start: else each by its own, its seed too.
        # Being its own, they ask nothing more of its start
        welcome, services = self.project["playgrounds"]["welcome"], self.project["services"]
        welcome.update(apps=["hello-app", "hello-web"], services=["hello-app", "hello-web", "seed"])
        services["hello-web"] = _service("hello-web", services["hello-app"]["owner"])
        web, seed = row("service", "hello-web", "hello-web"), row("service", "seed", "Seed")
        self.assertEqual(layers.start_rows(self.project, ["hello-app", "hello-web", "seed"]), [APPS])
        self.assertEqual(layers.start_rows(self.project, ["hello-web", "seed"]), [web, seed])
        running = up("agent-lens", playgrounds=["welcome"])
        running["services"].discard("hello-web")
        plan = self.plan(on(self.project, playground="welcome"), running, "playground", "welcome", "start")
        self.assertEqual((plan["starts"], layers.asks(plan)), ([web], False))
        self.assertEqual(layers.changes(self.project, plan),
                         {"start": [["service", "hello-web"], ["playground", "welcome"]], "stop": []})

    def test_playground_stop(self):
        state = on(self.project, ["kiosk"], "welcome")
        running = up("agent-lens", "kiosk", playgrounds=["welcome"])
        plan = self.plan(state, running, "playground", "welcome", "stop")
        self.check(plan, stops=[APPS, INSPECTOR], keeps=[DATA], restarts=[GATEWAY], playground=None,
                   explicit=["kiosk"])
        self.assertEqual(plan["services"], {"remove": ["agent-lens", "hello-app", "seed"],
                                            "start": ["apim-gateway"]})

    def test_a_playground_s_stop_again_takes_the_add_ons_it_brought(self):
        # Its stop failed once the on file was written: off, its app and AI Agent Lens still run. Its Stop again
        # removes them both, the add-on said first
        plan = self.plan(on(self.project), up("agent-lens", playgrounds=["welcome"]), "playground", "welcome",
                         "stop")
        self.check(plan, stops=[APPS, INSPECTOR], keeps=[DATA], playground=None, explicit=[])
        self.assertEqual((plan["label"], plan["services"]),
                         ("Stop", {"remove": ["agent-lens", "hello-app", "seed"], "start": []}))
        with self.assertRaisesRegex(RuntimeError, "Would also stop AI Agent Lens"):
            layers.check(self.project, plan, [])
        # On, what it brings goes as it always did; one turned on by itself stays
        state = on(self.project, ["agent-lens"], "welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "playground", "welcome", "stop")
        self.assertEqual(plan["stops"], [APPS])

    def test_playground_stop_keeps_the_add_on_turned_on_explicitly(self):
        state = on(self.project, ["agent-lens"], "welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "playground", "welcome", "stop")
        self.check(plan, stops=[APPS], keeps=[DATA], restarts=[GATEWAY], playground=None,
                   explicit=["agent-lens"])

    def test_apps_and_data_only_of_a_playground_that_has_them(self):
        own(self.project)   # Sandbox: neither apps nor a seed
        state = on(self.project, playground="local/sandbox")
        plan = self.plan(state, up("local/whoami"), "addon", "jade", "start")
        self.check(plan, starts=[JADE], restarts=[GATEWAY], playground="local/sandbox", explicit=["jade"])
        plan = self.plan(state, up("local/whoami"), "playground", "local/sandbox", "stop")
        itself, whoami = row("playground", "local/sandbox", "Sandbox"), row("addon", "local/whoami", "Whoami")
        self.check(plan, stops=[itself, whoami], playground=None, explicit=[])   # it keeps nothing: it has no seed
        # What it brings runs no more: it is turned off
        plan = self.plan(state, up(), "playground", "local/sandbox", "stop")
        self.check(plan, turns_off=[itself, whoami], playground=None, explicit=[])

    def test_an_add_on_s_start_leaves_the_apps_of_the_playground_as_they_are(self):
        second(self.project)
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["demo"]), "addon", "kafka", "start")
        self.check(plan, starts=[KAFKA], restarts=[GATEWAY], playground="welcome", explicit=["kafka"])

    def test_platform_not_up(self):
        # An add-on starts alone: the platform is not what it depends on. A stop removes it, and starts nothing
        plan = self.plan(on(self.project), up(platform=False), "addon", "jade", "start")
        self.check(plan, starts=[JADE], playground=None, explicit=["jade"])
        state = on(self.project, ["jade", "kiosk"])
        plan = self.plan(state, up(platform=False, created=["jade"]), "addon", "jade", "stop")
        self.check(plan, turns_off=[JADE], playground=None, explicit=["kiosk"])
        self.assertEqual(plan["services"], {"remove": ["jade"], "start": []})

    def test_what_the_stack_s_start_runs(self):
        # Nothing runs: the platform, the playground's apps, the add-ons that are on
        state = on(self.project, ["kafka-desk"], "welcome")
        self.assertEqual(layers.platform_starts(self.project, state, up(platform=False)),
                         [PLATFORM, APPS, INSPECTOR, KAFKA, CONSOLE])
        # Databases stopped from the Stack, and Kafka UI: the group, and Kafka. A one-shot is left out: it runs once
        state = on(self.project, ["kafka-desk", "openfga"], "welcome")
        running = up("agent-lens", "kafka", "kafka-desk", "openfga", playgrounds=["welcome"],
                     stopped=["databases"])
        running["services"].discard("kafka-ui")
        running["done"].discard("openfga-migrate")
        self.assertEqual(layers.platform_starts(self.project, state, running), [DATABASES, KAFKA])
        running = up("agent-lens", "kafka", "kafka-desk", "openfga", playgrounds=["welcome"])
        self.assertEqual(layers.platform_starts(self.project, state, running), [])

    def test_own_changes_in_the_on_file_block_everything(self):
        state = on(self.project, ["jade"], locked=layers.LOCKED)
        for do in ("start", "stop"):
            self.assertEqual(self.plan(state, up("jade"), "addon", "jade", do)["blocked"],
                             ".lab/on.yml has your own changes")

    def test_unknown_item(self):
        with self.assertRaises(paths.NotFound):
            self.plan(on(self.project), up(), "addon", "nope", "start")

    def test_check_refuses_what_the_page_did_not_show(self):
        state = on(self.project, ["kafka-desk"])
        plan = self.plan(state, up("kafka", "kafka-desk"), "addon", "kafka", "stop")
        self.assertEqual(plan["itself"], {"addon:kafka"})
        # What it accepts is named with its kind: an id alone may be an add-on's and a playground's
        for accept in ([], ["kafka-desk"], ["playground:kafka-desk"]):
            with self.assertRaisesRegex(RuntimeError, "Would also stop Kafka Desk"):
                layers.check(self.project, plan, accept)
        layers.check(self.project, plan, ["addon:kafka-desk"])
        # What it turns off as well
        plan = self.plan(state, up(), "addon", "kafka", "stop")
        with self.assertRaisesRegex(RuntimeError, "Would also turn off Kafka Desk"):
            layers.check(self.project, plan, [])
        layers.check(self.project, plan, ["addon:kafka-desk"])
        blocked = self.plan(on(self.project, missing=["hybrid"]), up(), "addon", "hybrid", "start")
        with self.assertRaisesRegex(RuntimeError, "Hybrid gateway needs settings"):
            layers.check(self.project, blocked, ["addon:hybrid"])

    def test_check_takes_a_setups_members_as_the_setup_itself(self):
        state = on(self.project, ["kafka-desk"], "welcome")
        plan = self.plan(state, up("kafka", "kafka-desk", "agent-lens"), "setup", "events", "stop")
        self.assertEqual(plan["itself"], {"setup:events", "addon:kafka", "addon:kafka-desk"})
        layers.check(self.project, plan, [])
        # A setup's stop keeps what the playground uses: the playground does not go with it
        plan = self.plan(state, up("kafka", "kafka-desk", "agent-lens"), "setup", "inspect", "stop")
        with self.assertRaisesRegex(RuntimeError, "Used by Welcome"):
            layers.check(self.project, plan, ["playground:welcome"])

    def changes(self, state, running, kind, uid, do):
        return layers.changes(self.project, self.plan(state, running, kind, uid, do))

    def test_changes_are_what_starts_restarts_and_stops(self):
        state = on(self.project, ["jade", "kiosk"])
        self.assertEqual(self.changes(state, up("jade", "kiosk"), "addon", "agent-lens", "start"),
                         {"start": [["addon", "agent-lens"], ["service", "apim-gateway"]],
                          "stop": [["addon", "jade"]]})
        self.assertEqual(self.changes(state, up("jade", "kiosk"), "setup", "observability", "stop"),
                         {"start": [["service", "apim-gateway"]], "stop": [["addon", "jade"], ["addon", "kiosk"]]})
        # What is turned off is removed: it shows as stopping
        self.assertEqual(self.changes(state, up("jade"), "setup", "observability", "stop"),
                         {"start": [["service", "apim-gateway"]], "stop": [["addon", "jade"], ["addon", "kiosk"]]})

    def test_changes_of_a_start_again_name_the_item(self):
        # On and partly up, nothing of the item is among what starts: its row must still say Starting
        state = on(self.project, ["kafka"], "welcome")
        running = up("agent-lens", "kafka", playgrounds=["welcome"])
        self.assertEqual(self.changes(state, running, "addon", "kafka", "start"),
                         {"start": [["addon", "kafka"]], "stop": []})
        self.assertEqual(self.changes(state, running, "playground", "welcome", "start"),
                         {"start": [["playground", "welcome"]], "stop": []})
        state = on(self.project, ["jade", "kiosk"])
        self.assertEqual(self.changes(state, up("jade", "kiosk"), "setup", "observability", "start"),
                         {"start": [["addon", "jade"], ["addon", "kiosk"]], "stop": []})
        # A member that starts already says so for the setup: the one that runs is left as it is
        self.assertEqual(self.changes(on(self.project, ["jade"]), up("jade"), "setup", "observability", "start"),
                         {"start": [["addon", "kiosk"]], "stop": []})

    def test_the_services_a_job_s_changes_cover(self):
        # The platform, a group, an add-on, a playground, a service; one stopped and started again restarts
        pairs = {"start": [["group", "databases"], ["addon", "kafka"], ["service", "apim-gateway"]],
                 "stop": [["playground", "welcome"], ["service", "apim-gateway"]]}
        self.assertEqual(layers.changing(self.project, pairs),
                         {"mongodb": "start", "kafka": "start", "kafka-ui": "start", "apim-gateway": "start",
                          "hello-app": "stop", "seed": "stop"})
        self.assertEqual(set(layers.changing(self.project, {"start": [["platform", "platform"]]})),
                         {"apim-gateway", "apim-management-api", "mongodb"})
        self.assertEqual(layers.changing(self.project, None), {})

    def test_what_a_command_waits_for(self):
        # What it starts among what it names, everything it covers when it names nothing; all that it stops. A
        # container that starts is there once ready, a one-shot once it ended well
        pairs = {"start": [["playground", "welcome"], ["addon", "openfga"], ["service", "apim-gateway"]],
                 "stop": [["addon", "jade"]]}
        self.assertEqual(layers.awaits(self.project, pairs, ["hello-app", "openfga", "openfga-migrate"]), {
            ("Container", "lab-jade"): "stop", ("Container", "lab-hello-app"): "start",
            ("Container", "lab-openfga"): "start", ("Container", "lab-openfga-migrate"): "once"})
        self.assertEqual(set(layers.awaits(self.project, pairs)), {
            ("Container", f"lab-{name}") for name in ("jade", "hello-app", "seed", "openfga", "openfga-migrate",
                                                      "apim-gateway")})

    def test_each_command_of_a_plan_waits_for_what_it_changes(self):
        # Kafka Desk starts with Kafka, restarts the gateway and the management API: the one `up` names them all.
        # The playground's app, which does not run, is left as it is
        services = self.project["services"]
        state = on(self.project, ["kiosk"], "welcome")
        plan = self.plan(state, up("kiosk"), "addon", "kafka-desk", "start")
        self.assertEqual(layers.waits(self.project, plan), [awaited(services, start=[
            "apim-gateway", "apim-management-api", "kafka", "kafka-desk", "kafka-ui"])])
        # A start that stops what is in conflict: what goes is removed by name first, then what starts
        state = on(self.project, ["kafka-desk"])
        plan = self.plan(state, up("kafka", "kafka-desk"), "addon", "redpanda", "start")
        self.assertEqual(layers.waits(self.project, plan), [
            awaited(services, stop=["kafka", "kafka-desk", "kafka-ui"]),
            awaited(services, start=["apim-gateway", "apim-management-api", "redpanda"])])
        # A stop while the platform is not up removes by name; with nothing to remove there is no command
        plan = self.plan(on(self.project, ["kiosk"]), up("kiosk", platform=False), "addon", "kiosk", "stop")
        self.assertEqual(layers.waits(self.project, plan), [{("Container", "lab-kiosk"): "stop"}])
        plan = self.plan(on(self.project, ["jade"]), up(platform=False), "addon", "kafka", "stop")
        self.assertEqual((layers.commands(plan), layers.waits(self.project, plan)), ([], []))


# The plans of this project on main, before a place existed, and whether each asks, in the situations of snapshot()
MAIN_PLANS = {"off": "9939a5beb335", "platform": "39ab5b96b5d0", "busy": "c8faae29dbe6", "stopped": "623a2540c1f4",
              "behind": "6f0363b10dde"}


def snapshot(**extra):
    """The fingerprint of the plan of every add-on, setup and playground, started and stopped (a playground's start
    set up or not), and whether it asks, in a few situations of the small project: {situation: digest}. The JSON keeps
    the order of the keys and the rows, so a plan that changes in any way changes it."""
    project = lab()
    own(project)
    second(project)
    situations = {
        "off": (on(project), up(platform=False), frozenset()),
        "platform": (on(project), up(), frozenset()),
        "busy": (on(project, ["jade", "kiosk", "kafka"], "welcome"),
                 up("jade", "kiosk", "kafka", "agent-lens", playgrounds=["welcome"]), frozenset()),
        "stopped": (on(project, ["kafka-desk", "kiosk"], "demo"),
                    up(platform=False, created=["kafka", "kafka-desk", "kiosk"]), frozenset()),
        "behind": (on(project, ["jade"], missing=["hybrid"]), up("jade"), frozenset({"apim-gateway", "hello-app"}))}
    found = {}
    for situation, (state, running, behind) in situations.items():
        plans = [layers.plan(project, state, running, kind, uid, do, fresh=fresh, behind=behind, **extra)
                 for kind in ("addon", "setup", "playground") for uid in sorted(project[kind + "s"])
                 for do in ("start", "stop") for fresh in ((False, True) if kind == "playground" else (False,))]
        text_of = json.dumps([plans, [layers.asks(plan) for plan in plans]], default=sorted)
        found[situation] = hashlib.sha256(text_of.encode()).hexdigest()[:12]
    return found


class ThisMachine(Case):
    """Without a place, or with This machine, a plan is the one it always was."""

    def setUp(self):
        self.patch(layout, "waiting", return_value=False)   # whatever tree this runs in: no old layout is being moved

    def test_the_plans_are_those_of_main(self):
        self.assertEqual(snapshot(), MAIN_PLANS)

    def test_this_machine_is_no_place(self):
        for place in (None, layers.MACHINE, {"id": layers.MACHINE, "name": "This machine"}):
            with self.subTest(place=place):
                self.assertEqual(snapshot(place=place), MAIN_PLANS)

    def test_a_local_plan_says_nothing_of_a_place(self):
        project = lab()
        plan = layers.plan(project, on(project), up(platform=False), "playground", "welcome", "start", fresh=True)
        self.assertFalse(set(layers.PLACE_ROWS) & set(plan))
        self.assertEqual(layers.dialog(plan), {key: plan[key] for key in layers.ROWS})


DEVELOPMENT = {"id": "my-account/development", "name": "Development (My Account)"}
TEST = {"id": "client-lab/test", "name": "Test (Client lab)"}
HYBRID, HOTELS = row("addon", "hybrid", "Hybrid gateway"), row("playground", "hotels", "Hotels apps")
ENVIRONMENT = row("environment", DEVELOPMENT["id"], DEVELOPMENT["name"])


def place(environment=DEVELOPMENT, here=True, serving=None):
    """A remote place as the caller gives it: the add-on of the gateway of that kind, whether the gateway set to run
    here is this place's, and the place it serves otherwise."""
    return {**environment, "gateway": "hybrid", "here": here, "serving": serving}


class Places(Case):
    """The plan of a deploy to a remote place: a lab without the core, with the gateway's add-on turned on."""

    def setUp(self):
        self.project = lab()
        licensed(self.project, True)
        own(self.project)
        second(self.project)
        cloud(self.project)
        self.core = [name for name, service in self.project["services"].items()
                     if service["owner"]["kind"] == "platform"]
        self.patch(layout, "waiting", return_value=False)   # whatever tree this runs in: no old layout is being moved

    def up(self, *addons, **kwargs):
        return up(*addons, project=self.project, **kwargs)

    def plan(self, state, running, kind="playground", uid="hotels", where=None, **kwargs):
        return layers.plan(self.project, state, running, kind, uid, "start", place=where or place(), **kwargs)

    def check(self, plan, **expected):
        rows = {"starts": [], "stops": [], "turns_off": [], "keeps": [], "restarts": [], "deploys_to": [ENVIRONMENT],
                "serves": [], "stops_serving": [], "blocked": None, **expected}
        self.assertEqual({key: plan[key] for key in rows}, rows)

    def assertAlone(self, plan):
        """Nothing of the core is in the plan but what the playground needs of it (layers.local_needs): not the platform
        nor a group, not another service as a row, a service it starts or removes, a command, a container it waits for,
        or what its job's changes cover."""
        allowed = set(layers.local_needs(self.project, self.project["playgrounds"]["hotels"]))
        rows = [found for part in (*layers.ROWS, *layers.PLACE_ROWS) for found in plan[part]]
        for found in rows:
            self.assertNotIn(found["kind"], ("platform", "group"), found)
            self.assertFalse(found["kind"] == "service" and found["id"] in self.core and found["id"] not in allowed, found)
        named = {*plan["services"]["start"], *plan["services"]["remove"]}
        words = {word for command in layers.commands(plan) for word in command}
        moves = layers.changing(self.project, layers.changes(self.project, plan))
        waited = {container for found in layers.waits(self.project, plan) for _, container in found}
        for name in set(self.core) - allowed:
            self.assertNotIn(name, named)
            self.assertNotIn(name, words)
            self.assertNotIn(name, moves)
            self.assertNotIn(f"lab-{name}", waited)

    def test_what_the_playground_needs_of_the_platform_starts_with_it_and_nothing_else_of_it(self):
        # Access Management and its database run here whichever platform runs API Management: what the apps depend on,
        # and what that depends on, but the gateway and the control plane of API Management, which the platform runs
        services = self.project["services"]
        services["hotels-api"]["depends_on"] = ["alert-engine", "apim-gateway", "apim-management-api"]
        services["alert-engine"]["depends_on"] = ["mongodb"]
        self.assertEqual(layers.local_needs(self.project, self.project["playgrounds"]["hotels"]),
                         ["alert-engine", "mongodb"])
        plan = self.plan(on(self.project), self.up(platform=False), fresh=True)
        rows = {(found["kind"], found["id"]) for found in plan["starts"]}
        self.assertLessEqual({("service", "alert-engine"), ("service", "mongodb")}, rows)
        self.assertFalse({("platform", "platform"), ("service", "apim-gateway"), ("service", "apim-management-api")} & rows)
        self.assertLessEqual({"alert-engine", "mongodb"}, set(plan["services"]["start"]))
        self.assertFalse({"apim-gateway", "apim-management-api"} & set(plan["services"]["start"]))
        # Nothing to start of it once it runs
        plan = self.plan(on(self.project), self.up(platform=False), fresh=True)
        services["hotels-api"]["depends_on"] = []
        self.assertEqual(layers.local_needs(self.project, self.project["playgrounds"]["hotels"]), [])

    # --- What it starts ---------------------------------------------------------------------------------------------

    def test_nothing_runs(self):
        # The gateway's add-on and the apps start, by name; the platform does not, nor the seed, which runs apart. The
        # on file keeps the playground and the gateway, which is turned on like an add-on turned on by hand
        plan = self.plan(on(self.project), self.up(platform=False), fresh=True)
        self.check(plan, starts=[HOTELS, HYBRID], playground="hotels", explicit=["hybrid"])
        self.assertEqual(plan["services"], {"remove": [], "start": ["apim-hybrid-gateway", "hotels-api",
                                                                    "hybrid-redis"]})
        self.assertEqual((plan["do"], plan["kind"], plan["id"], plan["label"], plan["fresh"]),
                         ("start", "playground", "hotels", "Start", True))
        self.assertAlone(plan)
        # On this machine, the same start is the platform, the apps and the seed
        local = layers.plan(self.project, on(self.project), self.up(platform=False), "playground", "hotels", "start",
                            fresh=True)
        self.assertEqual(local["starts"], [PLATFORM, HOTELS])
        self.assertIn("hotels-seed", local["services"]["start"])

    def test_the_core_makes_no_difference(self):
        # It runs, or part of it, or none: the plan is the same
        state, plans = on(self.project), []
        for running in (self.up(platform=False), self.up(), self.up(stopped=["databases"]),
                        self.up(stopped=["databases", "alert-engine"], created=["hotels"])):
            plans.append(self.plan(state, running, fresh=True))
        self.assertTrue(all(plan == plans[0] for plan in plans))

    def test_the_gateway_that_serves_this_place_is_left_as_it_is(self):
        # It runs, and so do the apps: nothing starts, nothing restarts, and it still names the place
        state = on(self.project, ["hybrid"], "hotels")
        plan = self.plan(state, self.up("hybrid", playgrounds=["hotels"]))
        self.check(plan, playground="hotels", explicit=["hybrid"])
        self.assertEqual(plan["services"], {"remove": [], "start": []})
        self.assertTrue(layers.asks(plan))
        self.assertEqual(layers.commands(plan), [])
        # Its apps do not run: they start, it does not
        plan = self.plan(state, self.up("hybrid"))
        self.check(plan, starts=[HOTELS], playground="hotels", explicit=["hybrid"])
        self.assertEqual(plan["services"]["start"], ["hotels-api"])
        # Its Redis alone runs: the gateway starts, said as the add-on
        running = self.up("hybrid", playgrounds=["hotels"])
        running["services"].discard("apim-hybrid-gateway")
        self.check(self.plan(state, running), starts=[HYBRID], playground="hotels", explicit=["hybrid"])

    def test_a_gateway_that_does_not_run_starts_whatever_it_serves(self):
        # Set to serve another place, stopped: it starts, and the job gives it this place's token. Nothing is restarted
        plan = self.plan(on(self.project), self.up(), where=place(here=False, serving=TEST))
        self.check(plan, starts=[HOTELS, HYBRID], playground="hotels", explicit=["hybrid"])

    def test_a_gateway_that_serves_another_place_is_made_again(self):
        # It runs for Test: it restarts with this place's token, and the place says what it serves and stops serving
        state = on(self.project, ["hybrid"], "hotels")
        running = self.up("hybrid", playgrounds=["hotels"])
        plan = self.plan(state, running, where=place(here=False, serving=TEST))
        self.check(plan, restarts=[HYBRID], serves=[ENVIRONMENT], stops_serving=[row("environment", TEST["id"],
                                                                                    TEST["name"])],
                   playground="hotels", explicit=["hybrid"])
        # Both of its services, by name, without what they depend on; its Redis, unchanged, is left as it is
        self.assertEqual(plan["services"], {"remove": [], "start": ["apim-hybrid-gateway", "hybrid-redis"]})
        self.assertEqual(layers.commands(plan), [[*compose.COMPOSE, "up", "-d", "--no-deps", "apim-hybrid-gateway",
                                                  "hybrid-redis"]])
        # Its row says Starting, and the playground's, as for any start that runs what runs again
        self.assertEqual(layers.changes(self.project, plan), {"start": [["addon", "hybrid"], ["playground", "hotels"]],
                                                              "stop": []})
        self.assertTrue(layers.asks(plan))
        self.assertAlone(plan)
        # Its apps do not run: they start besides, the gateway is not said twice
        plan = self.plan(state, self.up("hybrid"), where=place(here=False, serving=TEST))
        self.check(plan, starts=[HOTELS], restarts=[HYBRID], serves=[ENVIRONMENT],
                   stops_serving=[row("environment", TEST["id"], TEST["name"])], playground="hotels",
                   explicit=["hybrid"])
        # What it served is not known (a gateway made in the Cloud console): nothing is said to stop
        plan = self.plan(state, running, where=place(here=False))
        self.check(plan, restarts=[HYBRID], serves=[ENVIRONMENT], playground="hotels", explicit=["hybrid"])

    def test_a_gateway_that_runs_behind_what_is_saved_is_not_restarted_twice(self):
        # Its own services among those that run behind: the swap is the one row that remakes them
        state = on(self.project, ["hybrid"], "hotels")
        plan = self.plan(state, self.up("hybrid", playgrounds=["hotels"]), where=place(here=False, serving=TEST),
                         behind={"apim-hybrid-gateway", "hybrid-redis", "hotels-api"})
        self.check(plan, restarts=[HYBRID, row("service", "hotels-api", "hotels-api")], serves=[ENVIRONMENT],
                   stops_serving=[row("environment", TEST["id"], TEST["name"])], playground="hotels",
                   explicit=["hybrid"])

    def test_a_gateway_that_serves_this_place_and_runs_behind_what_is_saved_is_made_again_as_saved(self):
        # As any add-on a playground brings: its services that run behind, by their own rows, with no notes
        state = on(self.project, ["hybrid"], "hotels")
        plan = self.plan(state, self.up("hybrid", playgrounds=["hotels"]), behind={"apim-hybrid-gateway"})
        self.check(plan, restarts=[row("service", "apim-hybrid-gateway", "apim-hybrid-gateway")],
                   playground="hotels", explicit=["hybrid"])
        self.assertEqual(plan["services"]["start"], ["apim-hybrid-gateway"])

    def test_the_seed_is_never_among_what_up_names(self):
        # Whether the start sets the playground up there is the caller's to say, a place at a time (fresh): the seed
        # runs apart. On this machine, a fresh start puts it among them
        for fresh in (True, False):
            plan = self.plan(on(self.project, playground="hotels"), self.up(), fresh=fresh)
            self.assertEqual(plan["fresh"], fresh)
            self.assertNotIn("hotels-seed", plan["services"]["start"])
        local = layers.plan(self.project, on(self.project, playground="hotels"), self.up(), "playground", "hotels",
                            "start", fresh=True)
        self.assertIn("hotels-seed", local["services"]["start"])

    # --- The core is not part of it ---------------------------------------------------------------------------------

    def worst(self):
        """Everything a deploy could reach the core by: the playground and its Redis restart core services, its app and
        its seed depend on them, it brings an add-on that restarts one and depends on one, and another playground that
        is on does the same."""
        services, core = self.project["services"], self.core
        self.project["playgrounds"]["hotels"].update(restarts=core, addons=["kiosk"])
        self.project["addons"]["hybrid"]["restarts"] = ["apim-gateway"]
        self.project["addons"]["kiosk"]["restarts"] = ["apim-management-api"]
        services["hotels-api"]["depends_on"] = ["mongodb", "alert-engine"]
        services["hotels-seed"]["depends_on"] = core
        services["kiosk"]["depends_on"] = ["mongodb"]
        services["hybrid-redis"]["depends_on"] = ["mongodb"]
        self.project["playgrounds"]["welcome"]["restarts"] = ["apim-gateway"]

    def test_no_core_service_is_in_the_plan_of_a_remote_place(self):
        # commands() names the project from the core file, which compose.project_name() reads through the Docker CLI: given
        # here, so the test needs none
        self.patch(compose, "project_name", lambda: "lab")
        # Over every service of the core, in every situation: of what is on, of what runs, of the place and the start
        self.worst()
        states = [on(self.project), on(self.project, playground="hotels"), on(self.project, ["hybrid"], "hotels"),
                  on(self.project, ["jade", "kiosk"], "welcome"), on(self.project, ["kafka"], "demo")]
        runnings = [self.up(platform=False), self.up(), self.up(stopped=["databases"]), self.up("hybrid"),
                    self.up("hybrid", "kiosk", playgrounds=["hotels"]),
                    self.up("jade", "kiosk", "agent-lens", playgrounds=["welcome"]),
                    self.up("hybrid", platform=False, created=["hotels"])]
        places = [place(), place(here=False, serving=TEST), place(TEST, here=False)]
        behind = [frozenset(), frozenset(self.core), frozenset({*self.core, "hotels-api", "hybrid-redis", "kiosk",
                                                               "apim-hybrid-gateway"})]
        count = 0
        for state, running, where, late, fresh in itertools.product(states, runnings, places, behind, (True, False)):
            plan = self.plan(state, running, where=where, behind=late, fresh=fresh)
            self.assertAlone(plan)
            self.assertIn("hybrid", plan["explicit"])
            count += 1
        self.assertEqual(count, 5 * 7 * 3 * 3 * 2)
        # The same over the gateway's own start, the swap
        for state, running, where in itertools.product(states, runnings, places):
            self.assertAlone(self.plan(state, running, "addon", "hybrid", where=where))

    def test_the_core_services_another_playground_restarts_are_not_restarted(self):
        # commands() names the project from the core file, which compose.project_name() reads through the Docker CLI: given
        # here, so the test needs none
        self.patch(compose, "project_name", lambda: "lab")
        # Welcome is on with AI Agent Lens, which configures the gateway: on this machine, Hotels' start stops them
        # and restarts the gateway, to take the change off; to a remote place it stops them, and the gateway is left
        # as it is, which the Stack says (Apply needed) and the Stack's Apply makes right
        state = on(self.project, playground="welcome")
        running = self.up("agent-lens", playgrounds=["welcome"])
        local = layers.plan(self.project, state, running, "playground", "hotels", "start")
        self.assertEqual(local["restarts"], [GATEWAY])
        plan = self.plan(state, running)
        self.check(plan, starts=[HOTELS, HYBRID], stops=[APPS, INSPECTOR], keeps=[DATA], playground="hotels",
                   explicit=["hybrid"])
        self.assertEqual(plan["services"]["remove"], ["agent-lens", "hello-app", "seed"])
        self.assertAlone(plan)

    def test_what_is_in_conflict_with_the_gateway_goes_as_with_any_add_on(self):
        self.project["addons"]["kiosk"]["conflicts"] = {"hybrid": "same ports"}
        state = on(self.project, ["kiosk"])
        plan = self.plan(state, self.up("kiosk"))
        self.check(plan, starts=[HOTELS, HYBRID], stops=[KIOSK], playground="hotels", explicit=["hybrid"])
        self.assertEqual(plan["services"]["remove"], ["kiosk"])

    # --- Blocked, asked, said ---------------------------------------------------------------------------------------

    def test_the_gateway_s_token_is_not_asked_for(self):
        # The job writes it from the one the Lab keeps; the person types none. Another add-on still asks for its own
        state = on(self.project, missing=["hybrid"])
        self.assertIsNone(self.plan(state, self.up())["blocked"])
        self.assertIsNone(self.plan(state, self.up(), "addon", "hybrid", where=place(here=False))["blocked"])
        self.assertEqual(layers.plan(self.project, state, self.up(), "addon", "hybrid", "start")["blocked"],
                         "Hybrid gateway needs settings")
        self.project["playgrounds"]["hotels"]["addons"] = ["kiosk"]
        state = on(self.project, missing=["hybrid", "kiosk"])
        self.assertEqual(self.plan(state, self.up())["blocked"], "Kiosk needs settings")
        # An on file of your own changes blocks everything, here too
        self.assertEqual(self.plan(on(self.project, locked=layers.LOCKED), self.up())["blocked"], layers.LOCKED)

    def test_the_core_s_images_are_not_this_place_s(self):
        # Cloud runs its own version: the images the core would need are no reason to wait
        self.project["playgrounds"]["hotels"]["requires"] = {"images": {"APIM_VERSION": "4.13"}}
        gateway = self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.12.20")
        local = layers.plan(self.project, on(self.project), self.up(), "playground", "hotels", "start")
        self.assertIn("Needs", local["blocked"])
        self.assertIsNone(self.plan(on(self.project), self.up())["blocked"])

    def test_a_remote_place_always_asks_and_says_where(self):
        plan = self.plan(on(self.project, ["hybrid"], "hotels"), self.up("hybrid", playgrounds=["hotels"]))
        self.assertTrue(layers.asks(plan))
        self.assertEqual(layers.dialog(plan), {**{key: plan[key] for key in layers.ROWS},
                                               "deploys_to": [ENVIRONMENT], "serves": [], "stops_serving": []})
        self.assertEqual(ENVIRONMENT["kind"], "environment")
        # The gateway alone has no place to deploy to: the swap is all it says
        plan = self.plan(on(self.project, ["hybrid"]), self.up("hybrid"), "addon", "hybrid", where=place(here=False))
        self.assertEqual((plan["deploys_to"], [row["kind"] for row in plan["restarts"]], layers.asks(plan)),
                         ([], ["addon"], True))
        # Not running, the gateway's start is the add-on's, and says nothing more
        plan = self.plan(on(self.project), self.up(), "addon", "hybrid", where=place(here=False, serving=TEST))
        self.check(plan, starts=[HYBRID], deploys_to=[], playground=None, explicit=["hybrid"])
        self.assertFalse(layers.asks(plan))

    def test_the_gateway_outlives_the_playground_s_stop(self):
        # The on file after the deploy holds it on its own: the playground's stop leaves it, and does not name it
        plan = self.plan(on(self.project), self.up(platform=False))
        state = on(self.project, plan["explicit"], plan["playground"])
        running = self.up("hybrid", playgrounds=["hotels"])
        stop = layers.plan(self.project, state, running, "playground", "hotels", "stop")
        self.assertEqual((stop["stops"], stop["turns_off"], stop["keeps"], stop["restarts"]),
                         ([HOTELS], [], [row("data", "hotels", "What Hotels created in Gravitee")], []))
        self.assertEqual((stop["explicit"], stop["playground"]), (["hybrid"], None))
        self.assertEqual(stop["services"]["remove"], ["hotels-api", "hotels-seed"])
        # A place changes nothing of a stop
        again = layers.plan(self.project, state, running, "playground", "hotels", "stop", place=place())
        self.assertEqual(again, stop)

    # --- What a place is for ----------------------------------------------------------------------------------------

    def test_the_plan_is_given_the_gateway_and_does_not_read_its_key(self):
        state, running = on(self.project), self.up(platform=False)
        before = self.plan(state, running)
        del self.project["addons"]["hybrid"]["gateway"]
        self.assertEqual(self.plan(state, running), before)

    def test_a_place_is_for_a_playground_an_add_on_a_setup_and_the_gateway(self):
        state, running = on(self.project), self.up()
        for kind, uid in (("setup", "events"), ("addon", "kiosk"), ("addon", "kafka"), ("playground", "hotels")):
            with self.subTest(uid=uid):
                plan = self.plan(state, running, kind, uid)
                self.assertAlone(plan)
                self.assertIn("hybrid", plan["explicit"])
                self.assertEqual(plan["deploys_to"], [ENVIRONMENT])
                self.assertIsNone(plan["blocked"])
        with self.assertRaises(paths.NotFound):
            self.plan(state, running, where={**place(), "gateway": "nope"})
        with self.assertRaises(paths.NotFound):
            self.plan(state, running, uid="nope")

    def test_what_an_add_on_sets_on_the_gateway_restarts_the_gateway_of_the_platform_not_the_one_of_this_machine(self):
        # Kafka sets the gateway: where it goes to a place, the hybrid gateway that runs is made again to take it; the
        # gateway of this machine, which runs too, is not touched
        running = self.up("hybrid", "kafka", platform=True)
        plan = self.plan(on(self.project, ["hybrid"]), running, "addon", "kafka", where=place())
        restarting = {row["id"] for row in plan["restarts"]}
        self.assertIn(self.project["addons"]["hybrid"]["gateway"]["service"], restarting)
        self.assertNotIn("apim-gateway", restarting)
        self.assertNotIn("apim-gateway", {*plan["services"]["start"], *plan["services"]["remove"]})


    def test_an_item_that_runs_on_a_platform_restarts_the_gateway_of_that_platform_when_it_stops(self):
        running = self.up("hybrid", "kafka", platform=True)
        state = {**on(self.project, ["hybrid", "kafka"]), "targets": {"addon:kafka": "cloud"}}
        stop = layers.plan(self.project, state, running, "addon", "kafka", "stop")
        restarting = {row["id"] for row in stop["restarts"]}
        self.assertNotIn("apim-gateway", restarting, "the gateway of this machine was never set by it")
        # On this machine it is what it was: the gateway it set is made again
        local = layers.plan(self.project, on(self.project, ["hybrid", "kafka"]), running, "addon", "kafka", "stop")
        self.assertIn("apim-gateway", {row["id"] for row in local["restarts"]})


class Applies(Case):
    """What applying what was saved makes again and starts again, and what a Stack start makes again."""

    RUNNING, STOPPED = {"key": "running", "running": True}, {"key": "stopped", "running": False}
    FAILED, DONE = {"key": "issue", "running": False}, {"key": "done", "running": False}

    def setUp(self):
        self.project = lab()
        services = self.project["services"]
        services["kafka-ui"]["depends_on"] = ["kafka"]
        services["kafka-desk"]["depends_on"] = ["kafka-ui", "openfga-migrate"]

    def applies(self, states, behind, on=None):
        on = on if on is not None else layers.up_services(self.project, None, ["kafka", "kafka-desk", "openfga"])
        return layers.applies(self.project, on, states, behind)

    def test_what_runs_behind_and_what_failed(self):
        # OpenFGA runs behind: its migration runs again before it, as Compose would run it without --no-deps, and is
        # said with it
        self.project["services"]["openfga"]["depends_on"] = ["openfga-migrate"]
        states = {"apim-gateway": self.RUNNING, "apim-management-api": self.RUNNING, "mongodb": self.STOPPED,
                  "kafka": self.RUNNING, "kafka-ui": self.FAILED, "openfga": self.RUNNING,
                  "openfga-migrate": self.DONE}
        found = self.applies(states, {"apim-gateway", "openfga"})
        self.assertEqual(found, {"names": ["apim-gateway", "kafka-ui", "openfga", "openfga-migrate"],
                                 "restarts": [GATEWAY, row("addon", "openfga", "OpenFGA")], "starts": [KAFKA],
                                 "runs": True})
        # Not known: every service that runs is named, Compose makes again what changed
        self.assertEqual(self.applies(states, None)["names"],
                         ["apim-gateway", "apim-management-api", "kafka", "kafka-ui", "openfga", "openfga-migrate"])
        # Nothing of OpenFGA behind: its migration is left as it is
        self.assertEqual(self.applies(states, {"apim-gateway"})["names"], ["apim-gateway", "kafka-ui"])

    def test_what_failed_waits_for_what_it_needs(self):
        # Kafka stopped: Kafka UI would fail again alone, and Kafka Desk, which needs it, too, and nothing runs
        # the migration Kafka Desk needs
        states = {"apim-gateway": self.RUNNING, "kafka": self.STOPPED, "kafka-ui": self.FAILED,
                  "kafka-desk": self.FAILED, "openfga-migrate": self.DONE}
        self.assertEqual(self.applies(states, set())["names"], [])
        # Kafka runs: both start again, after the migration, said with what starts
        states["kafka"] = self.RUNNING
        found = self.applies(states, set())
        self.assertEqual((found["names"], found["starts"]), (["kafka-desk", "kafka-ui", "openfga-migrate"],
                                                             [KAFKA, CONSOLE, row("addon", "openfga", "OpenFGA")]))
        # What is off does not count: its file may add a dependency to a service that is on
        self.project["services"]["kafka-ui"]["depends_on"] = ["kafka", "jade"]
        self.assertEqual(self.applies(states, set())["names"], ["kafka-desk", "kafka-ui", "openfga-migrate"])
        # A one-shot whose own dependency does not run would fail: it is not run again, and what needs it starts on
        # what it did before
        self.project["services"]["openfga-migrate"]["depends_on"] = ["openfga"]
        states["openfga"] = self.STOPPED
        self.assertEqual(self.applies(states, set())["names"], ["kafka-desk", "kafka-ui"])

    def test_nothing_while_nothing_of_what_is_on_runs(self):
        states = {"kafka": self.STOPPED, "kafka-ui": self.FAILED, "apim-gateway": self.STOPPED}
        self.assertEqual(self.applies(states, {"kafka"}), {"names": [], "restarts": [], "starts": [], "runs": False})
        # An add-on that runs while the platform is stopped is applied: what is saved reaches it
        states["kafka"] = self.RUNNING
        self.assertEqual(self.applies(states, {"kafka"}), {"names": ["kafka", "kafka-ui"], "restarts": [KAFKA],
                                                          "starts": [KAFKA], "runs": True})

    def test_what_a_stack_start_makes_again(self):
        # What of these services, and of what they depend on among what is on, runs behind what is saved
        state = on(self.project, ["kafka-desk"])
        self.assertEqual(layers.recreates(self.project, state, {"kafka", "apim-gateway"}, ["kafka-desk"]), [KAFKA])
        self.assertEqual(layers.recreates(self.project, state, {"apim-gateway"}, ["apim-gateway"]), [GATEWAY])
        self.assertEqual(layers.recreates(self.project, state, set(), ["kafka-desk"]), [])

    def test_what_owns_what_runs_behind(self):
        # The banner's "Apply needed: …": the groups in their order, the playground's apps, the add-ons by name
        found = layers.owner_rows(self.project, ["kafka-ui", "hello-app", "mongodb", "apim-gateway", "jade"])
        self.assertEqual(found, [MANAGEMENT, DATABASES, APPS, JADE, KAFKA])

class Files(Case):
    """The on file and the job, in a project folder that holds only what a test writes."""

    def setUp(self):
        self.folder(paths, compose, envfiles)
        self.project = lab()
        units = [*self.project["addons"].values(), *self.project["setups"].values(),
                 *self.project["playgrounds"].values()]
        for unit in units:
            self.write(unit["file"])
        self.write("addons/hybrid/.env", "HYBRID_CLOUD_TOKEN=token\n")

    def on_file(self, text):
        self.write(paths.ON_FILE, text)
        return layers.current(self.project)

    def test_nothing_on(self):
        self.assertEqual(layers.current(self.project), {"playground": None, "explicit": [], "setups": [],
                                                        "effective": [], "targets": {}, "missing": [], "unset": {},
                                                        "problems": [], "locked": None})

    KAFKA = ("services:\n  apim-gateway:\n    environment:\n      gravitee_kafka_enabled: \"true\"\n"
             "  kafka:\n    container_name: gio-apim-gateway-probe\n    depends_on:\n      apim-gateway:\n"
             "        condition: service_healthy\n    environment:\n      HOST: http://apim-gateway:8082\n")
    NAMES = {"cloud": {"apim-gateway": "apim-hybrid-gateway"}}

    def test_an_item_that_runs_on_another_platform_is_its_copy_for_it(self):
        self.write("addons/kafka/compose.yml", self.KAFKA)
        layers.write("welcome", ["kafka", "kiosk"], {"addons/kafka/compose.yml": "cloud"}, self.NAMES)
        copy = ".lab/targets/cloud/addons__kafka__compose.yml"
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - playgrounds/welcome/compose.yml\n"
                         f'  - {{path: "{copy}", project_directory: "addons/kafka"}}\n  - addons/kiosk/compose.yml\n')
        made = (self.root / copy).read_text(encoding="utf-8")
        # The service, what depends on it and the host that names it are the platform's; a container name is not
        self.assertIn("  apim-hybrid-gateway:\n    environment:", made)
        self.assertIn("      apim-hybrid-gateway:\n        condition: service_healthy", made)
        self.assertIn("HOST: http://apim-hybrid-gateway:8082", made)
        self.assertIn("container_name: gio-apim-gateway-probe", made)
        self.assertNotIn("\n  apim-gateway:", made)
        # What is on is read as the files it was made of, and where each runs is said
        state = layers.current(self.project)
        self.assertEqual((state["explicit"], state["targets"], state["locked"], state["problems"]),
                         (["kafka", "kiosk"], {"addon:kafka": "cloud"}, None, []))

    def test_where_an_item_runs_is_kept_until_it_is_said_again_and_its_copy_follows_its_file(self):
        self.write("addons/kafka/compose.yml", self.KAFKA)
        layers.write(None, ["kafka"], {"addons/kafka/compose.yml": "cloud"}, self.NAMES)
        self.write("addons/kafka/compose.yml", self.KAFKA + "      MORE: yes\n")
        layers.write(None, ["kafka", "kiosk"])   # another item turned on, nothing said of the kafka
        self.assertEqual(layers.current(self.project)["targets"], {"addon:kafka": "cloud"})
        self.assertIn("MORE: yes", (self.root / ".lab/targets/cloud/addons__kafka__compose.yml").read_text(encoding="utf-8"))
        self.assertIn("apim-hybrid-gateway", (self.root / ".lab/targets/cloud/addons__kafka__compose.yml").read_text(encoding="utf-8"))
        layers.write(None, ["kiosk"])   # turned off: it is no more where it was
        self.assertEqual(layers.current(self.project)["targets"], {})
        layers.write(None, ["kafka"])   # turned on again: on this machine
        self.assertEqual(layers.current(self.project)["targets"], {})
        self.assertIn("  - addons/kafka/compose.yml", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))
        layers.write(None, ["kafka"], {"addons/kafka/compose.yml": "cloud"}, self.NAMES)
        layers.write(None, ["kafka"], {"addons/kafka/compose.yml": None})   # brought back to this machine
        self.assertEqual(layers.current(self.project)["targets"], {})

    def test_write_then_read(self):
        layers.write("welcome", ["kiosk", "kafka-desk"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - playgrounds/welcome/compose.yml\n  - addons/kafka-desk/compose.yml\n"
                         "  - addons/kiosk/compose.yml\n")
        state = layers.current(self.project)
        self.assertEqual((state["playground"], state["explicit"]), ("welcome", ["kafka-desk", "kiosk"]))
        self.assertEqual(state["effective"], ["agent-lens", "kafka", "kafka-desk", "kiosk"])
        self.assertIsNone(state["locked"])
        # Nothing on: an empty file, which Compose needs all the same
        layers.write(None, [])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")
        self.assertEqual(layers.current(self.project)["locked"], None)

    def test_prepare_makes_the_on_file_and_adds_compose_file_to_env_once(self):
        # .env gets COMPOSE_FILE once, its values kept
        self.write(".env", "GRAVITEE_LICENSE=x")
        layers.prepare()
        layers.prepare()
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")
        self.assertEqual((self.root / ".env").read_text(encoding="utf-8"), "GRAVITEE_LICENSE=x\n" + layers.COMPOSE_FILE_LINES)
        self.assertEqual(envfiles.read(".env")["COMPOSE_FILE"], "docker-compose.yml:.lab/on.yml")
        self.assertEqual(oct((self.root / ".env").stat().st_mode & 0o777), "0o600")

    def test_a_fresh_clone_starts_with_nothing_on(self):
        layers.prepare()
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")
        self.assertFalse((self.root / ".env").exists())   # make init makes it, from .env.example

    def test_what_belongs_to_this_machine_is_turned_on_like_the_rest(self):
        own(self.project)
        for path in ("addons/local/whoami/compose.yml", "playgrounds/local/sandbox/compose.yml"):
            self.write(path)
        layers.write("local/sandbox", ["kiosk"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - playgrounds/local/sandbox/compose.yml\n  - addons/kiosk/compose.yml\n")
        state = layers.current(self.project)
        self.assertEqual((state["playground"], state["explicit"], state["effective"], state["locked"]),
                         ("local/sandbox", ["kiosk"], ["kiosk", "local/whoami"], None))
        layers.write(None, ["local/whoami"])
        self.assertEqual(layers.current(self.project)["explicit"], ["local/whoami"])

    def test_setup_entry_is_its_includes(self):
        state = self.on_file("# mine\ninclude:\n  - setups/events.yml\n  - addons/kiosk/compose.yml\n")
        self.assertEqual((state["explicit"], state["setups"]), (["kafka-desk", "kiosk"], ["events"]))
        self.assertIsNone(state["locked"])

    def test_entry_whose_file_is_gone(self):
        (self.root / "addons/jade/compose.yml").unlink()
        state = self.on_file("include:\n  - addons/jade/compose.yml\n  - addons/kiosk/compose.yml\n")
        self.assertEqual(state["explicit"], ["kiosk"])
        self.assertEqual(state["problems"], [{"file": ".lab/on.yml",
                                              "message": "addons/jade/compose.yml does not exist"}])
        self.assertIsNone(state["locked"])

    def test_entry_whose_file_does_not_load_locks_the_file(self):
        # Written by the Lab UI, it is not the user's change: a rewrite would drop it, the file says why
        del self.project["addons"]["jade"]
        state = self.on_file("include:\n  - addons/kiosk/compose.yml\n  - addons/jade/compose.yml\n")
        self.assertEqual((state["explicit"], state["locked"]), (["kiosk"], "addons/jade/compose.yml does not load"))
        message = "addons/jade/compose.yml does not load: the catalog's Start and Stop are off"
        self.assertEqual(state["problems"], [{"file": ".lab/on.yml", "message": message}])

    def test_own_changes_lock_the_file(self):
        self.write("mine.yml")
        for text in ("include:\n  - addons/kiosk/compose.yml\nservices:\n  redis:\n    ports: !reset []\n",
                     "include:\n  - addons/kiosk/compose.yml\n  - mine.yml\n",
                     "include:\n  - path: addons/kiosk/compose.yml\n    env_file: mine.env\n"):
            state = self.on_file(text)
            self.assertEqual(state["locked"], ".lab/on.yml has your own changes", text)
            self.assertEqual(state["explicit"], ["kiosk"])
            # What it keeps from running, said once, in the banner: the catalog's Start and Stop; the Stack runs it as
            # Compose reads it
            self.assertEqual(state["problems"], [{"file": ".lab/on.yml", "message":
                                                  "has your own changes: the catalog's Start and Stop are off"}])

    def test_own_changes_on_one_line_lock_the_file(self):
        for text in ("include: [addons/kiosk/compose.yml]\n", "name: mine\n", "services: {}\n"):
            self.assertEqual(self.on_file(text)["locked"], ".lab/on.yml has your own changes", text)

    def test_two_playgrounds_lock_the_file(self):
        second(self.project)
        self.write("playgrounds/demo/compose.yml")
        state = self.on_file("include:\n  - playgrounds/welcome/compose.yml\n  - playgrounds/demo/compose.yml\n")
        self.assertEqual((state["playground"], state["locked"]), ("welcome", layers.LOCKED))

    def test_missing_settings_are_read_from_the_add_on_env(self):
        self.assertEqual(layers.current(self.project)["missing"], [])
        (self.root / "addons/hybrid/.env").write_text("HYBRID_CLOUD_TOKEN=\n", encoding="utf-8")   # a required setting without a value
        self.assertEqual(layers.current(self.project)["missing"], ["hybrid"])
        (self.root / "addons/hybrid/.env").unlink()
        self.assertEqual(layers.current(self.project)["missing"], ["hybrid"])

    def applied(self, plan):
        """The job layers.apply() starts for a plan: (title, steps, its other arguments)."""
        jobs = self.patch(layers, "JOBS")
        self.patch(compose, "project_name", lambda: "lab")
        layers.apply(self.project, plan, "Title")
        return self.started(jobs)

    def test_apply_writes_the_on_file_then_runs_up(self):
        plan = layers.plan(self.project, on(self.project, ["kiosk"], "welcome"), up("kiosk"),
                           "addon", "kafka-desk", "start")
        title, (write, command), more = self.applied(plan)
        self.assertEqual(title, "Title")
        # It waits for what starts and restarts; the welcome, on and not running, is left as it is
        waits = {("Container", f"lab-{name}"): "start" for name in (
            "kafka", "kafka-ui", "kafka-desk", "apim-gateway", "apim-management-api")}
        self.assertEqual(more, {"key": "addon:kafka-desk", "verb": "start",
                                "changes": {"start": [["addon", "kafka"], ["addon", "kafka-desk"],
                                                      ["service", "apim-gateway"],
                                                      ["service", "apim-management-api"]],
                                            "stop": []},
                                "waits": [waits]})
        # By name, without what they depend on: nothing else is started, recreated or removed
        self.assertEqual(command, [*compose.COMPOSE, "up", "-d", "--no-deps", "apim-gateway", "apim-management-api",
                                   "kafka", "kafka-desk", "kafka-ui"])
        job = mock.Mock(lines=[])
        write(job)
        self.assertEqual(layers.current(self.project)["explicit"], ["kafka-desk", "kiosk"])
        self.assertEqual(job.lines, ["Starts: Kafka, Kafka Desk", "Restarts: Gateway, Management API"])

    def test_apply_removes_what_goes_by_name_then_restarts(self):
        # AI Agent Lens in place of Jade: Jade removed by name, then the inspector and the gateway it
        # changes; Kiosk, started outside the Lab UI, stays
        state = on(self.project, ["jade"])
        plan = layers.plan(self.project, state, up("jade", "kiosk"), "addon", "agent-lens", "start")
        _, (write, remove, start), more = self.applied(plan)
        self.assertEqual((remove, start), ([*compose.COMPOSE, "-p", "lab", "rm", "--stop", "--force", "jade"],
                                           [*compose.COMPOSE, "up", "-d", "--no-deps", "agent-lens",
                                            "apim-gateway"]))
        self.assertNotIn("--remove-orphans", [word for command in (remove, start) for word in command])
        self.assertEqual(more["waits"], [{("Container", "lab-jade"): "stop"},
                                         {("Container", "lab-agent-lens"): "start",
                                          ("Container", "lab-apim-gateway"): "start"}])
        job = mock.Mock(lines=[])
        write(job)
        self.assertEqual(job.lines, ["Starts: AI Agent Lens", "Stops: Jade", "Restarts: Gateway"])

    def test_apply_stop_while_the_platform_is_not_up_only_removes(self):
        # Kafka runs, Kafka Desk was stopped: one stops, the other is turned off; both are removed by name
        state = on(self.project, ["kafka-desk"])
        plan = layers.plan(self.project, state, up("kafka", platform=False, created=["kafka-desk"]), "addon",
                           "kafka", "stop")
        _, (_, command), more = self.applied(plan)
        self.assertEqual(command, [*compose.COMPOSE, "-p", "lab", "rm", "--stop", "--force",
                                   "kafka", "kafka-desk", "kafka-ui"])
        self.assertEqual(more, {"key": "addon:kafka", "verb": "stop",
                                "changes": {"start": [], "stop": [["addon", "kafka"], ["addon", "kafka-desk"]]},
                                "waits": [{("Container", "lab-kafka"): "stop", ("Container", "lab-kafka-ui"): "stop",
                                           ("Container", "lab-kafka-desk"): "stop"}]})

    def test_a_setup_s_job_is_the_setup_s(self):
        # Its button shows it, though what starts are its add-ons: it waits for them and what they restart
        plan = layers.plan(self.project, on(self.project), up(), "setup", "events", "start")
        more = self.applied(plan)[2]
        self.assertEqual((more["key"], more["verb"]), ("setup:events", "start"))
        self.assertIn(["addon", "kafka"], more["changes"]["start"])
        self.assertEqual(more["waits"], [{("Container", f"lab-{name}"): how for name, how in (
            ("kafka", "start"), ("kafka-ui", "start"), ("kafka-desk", "start"), ("apim-gateway", "start"),
            ("apim-management-api", "start"))}])

    def ran(self, plan, docker, *outputs):
        """The job of a plan, its commands printing these lines in turn, with Docker following them: how far it was
        after each line, and the job."""
        title, steps, more = self.applied(plan)
        job = Job(title, steps, **more)
        return progress(replay(job, *outputs, refresh=docker.refresh)), job

    def test_an_add_on_start_is_there_once_its_containers_are_ready(self):
        # Kafka, its UI and the gateway it restarts: running is half way for the two with a health check
        plan = layers.plan(self.project, on(self.project), up(), "addon", "kafka", "start")
        lines = [" Container lab-mongodb Running ", " Container lab-apim-gateway Recreate ",
                 " Container lab-kafka Creating ", " Container lab-kafka Created ", " Container lab-kafka-ui Created ",
                 " Container lab-apim-gateway Recreated ", " Container lab-kafka Starting ",
                 " Container lab-kafka Started ", " Container lab-kafka Waiting ", " Container lab-kafka Healthy ",
                 " Container lab-kafka-ui Starting ", " Container lab-kafka-ui Started ",
                 " Container lab-apim-gateway Starting ", " Container lab-apim-gateway Started "]
        docker = Docker(self.project, {"mongodb": container(health="healthy"),
                                       "apim-gateway": container(health="healthy")}, HEALTH_CHECKED)
        curve, job = self.ran(plan, docker, lines)
        self.assertEqual(curve, [0, 0, 0, 0.067, 0.133, 0.2, 0.2, 0.3, 0.3, 0.467, 0.467, 0.733, 0.733, 0.833])
        # Compose returned while the gateway's health check runs; Docker says when it passes
        self.assertEqual((job.ok, round(job.progress, 3)), (True, 0.833))
        docker.healthy("apim-gateway")
        docker.observe(job)
        self.assertEqual(job.progress, 1)

    def test_a_stop_is_there_once_its_containers_are_removed(self):
        # What it stops, removed by name, then the gateway it restarts without Kafka's settings: each command half
        plan = layers.plan(self.project, on(self.project, ["kafka"]), up("kafka"), "addon", "kafka", "stop")
        removed = [" Container lab-kafka-ui Stopping ", " Container lab-kafka Stopping ",
                   " Container lab-kafka-ui Stopped ", " Container lab-kafka-ui Removing ",
                   " Container lab-kafka Stopped ", " Container lab-kafka Removing ", " Container lab-kafka Removed ",
                   " Container lab-kafka-ui Removed "]
        restarted = [" Container lab-apim-gateway Recreate ", " Container lab-apim-gateway Recreated ",
                     " Container lab-apim-gateway Starting ", " Container lab-apim-gateway Started ",
                     " Container lab-apim-gateway Healthy "]
        running = {name: container(health="healthy") for name in ("kafka", "kafka-ui", "apim-gateway")}
        curve, job = self.ran(plan, Docker(self.project, running, HEALTH_CHECKED), removed, restarted)
        self.assertEqual(self.applied(plan)[2]["changes"], {"start": [["service", "apim-gateway"]],
                                                            "stop": [["addon", "kafka"]]})
        self.assertEqual(curve, [0.125, 0.25, 0.375, 0.375, 0.5, 0.5, 0.5, 0.5, 0.5, 0.6, 0.6, 0.75, 1])


# ---------------------------------------------------------------------------------------------------------------------
# Catalogs: what stands between an item of a catalog and the platform
# ---------------------------------------------------------------------------------------------------------------------

NEEDS = {"missing": "Needs the catalog Partner Bank", "downloading": "Downloading Partner Bank",
         "failed": "Needs Partner Bank", "gone": "Needs Ledger, which is no longer in Partner Bank",
         "old": "Needs Partner Bank 2.0 or later"}


def need(reason, **more):
    return {"id": "partner", "name": "Partner Bank", "url": "https://example.invalid/partner.git", "min": "2.0.0",
            "have": None, "reason": reason, "unit": "Ledger", **more}


def twins(project, uid="kiosk"):
    """Kiosk of the first catalog and Kiosk of this machine, the same item: each says the other. Another add-on by its id."""
    kiosk = project["addons"][uid]
    project["addons"][f"local/{uid}"] = {**kiosk, "id": f"local/{uid}", "personal": True,
                                         "file": f"addons/local/{uid}/compose.yml", "env_file": f"addons/local/{uid}/.env",
                                         "catalog": "local", "twin": {"id": uid, "name": "Fixture catalog"}}
    kiosk["twin"] = {"id": f"local/{uid}", "name": "Personal"}


class Gates(Case):
    """The refusals of a start, which the plan says as `blocked`: a stop is refused by none of them."""

    def setUp(self):
        self.project = lab()
        own(self.project)
        self.waiting = self.patch(layout, "waiting", return_value=False)

    def block(self, uid, *reasons, kind="addon"):
        self.project[kind + "s"][uid]["blocked"] = {"reasons": list(reasons)}

    def why(self, kind, uid, state=None, running=None, do="start"):
        state = state or on(self.project)
        running = running or up(project=self.project)
        return layers.plan(self.project, state, running, kind, uid, do)["blocked"]

    def test_what_the_policy_blocks_cannot_start_and_says_its_first_reason(self):
        self.block("kiosk", "Mounts the Docker socket", "Runs with full privileges")
        self.assertEqual(self.why("addon", "kiosk"), "Mounts the Docker socket")

    def test_a_stop_is_refused_for_none_of_them(self):
        self.block("kiosk", "Mounts the Docker socket")
        self.project["addons"]["kiosk"]["needs_catalogs"] = [need("missing")]
        self.waiting.return_value = True
        state, running = on(self.project, ["kiosk"]), up("kiosk", project=self.project)
        self.assertIsNone(self.why("addon", "kiosk", state, running, "stop"))

    def test_what_a_unit_brings_that_is_blocked_blocks_it(self):
        self.block("kafka", "Publishes a port to the network")
        self.assertEqual(self.why("addon", "kafka-desk"), "Publishes a port to the network")
        self.block("agent-lens", "Mounts the Docker socket")
        self.assertEqual(self.why("playground", "welcome"), "Mounts the Docker socket")
        self.block("kiosk", "Runs with full privileges")
        self.assertEqual(self.why("setup", "observability"), "Runs with full privileges", "a setup is its add-ons")

    def test_a_catalog_it_lacks_is_said_in_the_words_of_the_page(self):
        for reason, line in NEEDS.items():
            with self.subTest(reason=reason):
                self.project["addons"]["kiosk"]["needs_catalogs"] = [need(reason)]
                self.assertEqual(self.why("addon", "kiosk"), line)
                self.assertEqual(layers.need_line(need(reason)), line, "the one place the words are made: the row reads it here")
        for floor, said in (("2.1.3", "Needs Partner Bank 2.1.3 or later"), (None, "Needs Partner Bank or later")):
            self.assertEqual(layers.need_line(need("old", min=floor)), said)
        self.assertEqual(layers.need_line({"reason": "missing"}), "Needs the catalog a catalog")

    def test_what_it_brings_that_lacks_a_catalog_blocks_it_too(self):
        self.project["addons"]["kafka"]["needs_catalogs"] = [need("downloading")]
        self.assertEqual(self.why("addon", "kafka-desk"), "Downloading Partner Bank")

    def test_the_reasons_come_in_an_order(self):
        # The file is locked, the layout is moving, the policy blocks it, a catalog is lacking, its twin is on, a setting
        # is lacking, a port is taken: each is said once the one before it is gone
        twins(self.project)
        self.project["addons"]["kiosk"].update(catalog="fixture", services=["kiosk"])
        self.project["services"]["kiosk"]["ports"] = ["5601"]
        self.project["services"]["jade"]["ports"] = ["5601"]
        self.block("kiosk", "Mounts the Docker socket")
        self.project["addons"]["kiosk"]["needs_catalogs"] = [need("missing")]
        self.waiting.return_value = True
        state = on(self.project, ["jade", "local/kiosk"], missing=["kiosk"], locked=layers.LOCKED)
        running = up(project=self.project)
        said = []
        for step in range(7):
            said.append(layers.plan(self.project, state, running, "addon", "kiosk", "start")["blocked"])
            if step == 0:
                state["locked"] = None
            elif step == 1:
                self.waiting.return_value = False
            elif step == 2:
                self.project["addons"]["kiosk"]["blocked"] = None
            elif step == 3:
                self.project["addons"]["kiosk"]["needs_catalogs"] = []
            elif step == 4:
                state = on(self.project, ["jade"], missing=["kiosk"])
            elif step == 5:
                state = on(self.project, ["jade"])
        self.assertEqual(said, [layers.LOCKED, layers.WAITING, "Mounts the Docker socket", NEEDS["missing"],
                                "Also in Personal: turn it off first", "Kiosk needs settings",
                                "Port 5601 is used by Jade"])

    def test_nothing_starts_while_the_old_layout_is_moving(self):
        self.waiting.return_value = True
        self.assertEqual(self.why("addon", "kiosk"), "Your settings are being moved. Try again in a moment.")
        self.assertEqual(self.why("playground", "welcome"), layers.WAITING)
        with self.assertRaisesRegex(RuntimeError, "^Your settings are being moved. Try again in a moment.$"):
            layers.ready()
        self.waiting.return_value = False
        layers.ready()

    def test_the_twin_of_an_item_that_is_on_or_up_is_not_started(self):
        twins(self.project)
        # The personal one is refused while the catalog's is on, and the catalog's while the personal one is
        self.assertEqual(self.why("addon", "local/kiosk", on(self.project, ["kiosk"])),
                         "Also in Fixture catalog: turn it off first")
        self.assertEqual(self.why("addon", "kiosk", on(self.project, ["local/kiosk"])),
                         "Also in Personal: turn it off first")
        # Up without being on counts: they share the names of their services
        self.assertEqual(self.why("addon", "local/kiosk", on(self.project), up("kiosk", project=self.project)),
                         "Also in Fixture catalog: turn it off first")
        # Neither on, or the one that is on is the item itself: nothing is against it
        self.assertIsNone(self.why("addon", "kiosk"))
        self.assertIsNone(self.why("addon", "kiosk", on(self.project, ["kiosk"]), up("kiosk", project=self.project)))
        # Its stop is never refused for it
        self.assertIsNone(self.why("addon", "local/kiosk", on(self.project, ["kiosk"]), do="stop"))

    def test_the_twin_of_what_an_item_brings_is_in_the_way_as_the_twin_of_the_item_is(self):
        # Kafka Desk needs Kafka, the welcome brings AI Agent Lens, the events setup is Kafka Desk: the other half of
        # any of them would be in the on file with the same service names
        twins(self.project, "kafka")
        twins(self.project, "agent-lens")
        away = "Also in Personal: turn it off first"
        self.assertEqual(self.why("addon", "kafka-desk", on(self.project, ["local/kafka"])), away)
        self.assertEqual(self.why("playground", "welcome", on(self.project, ["local/agent-lens"])), away)
        self.assertEqual(self.why("setup", "events", on(self.project, ["local/kafka"])), away)
        # Up without being on counts here too
        self.assertEqual(self.why("addon", "kafka-desk", on(self.project), up("local/kafka", project=self.project)), away)
        # The half of the item's own comes first, then those of what it brings, in the order they go in the file
        twins(self.project, "kafka-desk")
        self.project["addons"]["kafka-desk"]["twin"]["name"] = "Mine"
        both = on(self.project, ["local/kafka", "local/kafka-desk"])
        self.assertEqual(self.why("addon", "kafka-desk", both), "Also in Mine: turn it off first")
        self.assertEqual(self.why("addon", "kafka-desk", on(self.project, ["local/kafka"])), away)
        # Nothing of the other half is on, or what is on is what the item brings itself: nothing is against it
        self.assertIsNone(self.why("addon", "kafka-desk", on(self.project, ["kafka"])))
        self.assertIsNone(self.why("playground", "welcome", on(self.project, ["kiosk"])))
        # A stop is never refused for it
        self.assertIsNone(self.why("addon", "kafka-desk", on(self.project, ["local/kafka"]), do="stop"))

    def test_a_twin_that_the_same_plan_turns_off_is_not_in_the_way(self):
        # Sandbox of this machine brings Kiosk of this machine; Demo, of the catalog, brings Kiosk: Demo replaces Sandbox, and
        # Sandbox takes its Kiosk with it
        second(self.project)
        twins(self.project)
        self.project["playgrounds"]["demo"]["includes"] = ["kiosk"]
        self.project["playgrounds"]["local/sandbox"].update(includes=["local/kiosk"], addons=["local/kiosk"])
        sandbox = on(self.project, playground="local/sandbox")
        self.assertEqual(sandbox["effective"], ["local/kiosk"])
        self.assertIsNone(self.why("playground", "demo", sandbox))
        self.assertIsNone(self.why("playground", "demo", sandbox, up("local/kiosk", playgrounds=["local/sandbox"],
                                                                      project=self.project)))
        # Kiosk alone does not turn Sandbox off, and neither does Demo when the Kiosk of this machine is on by itself: the
        # twin is on, and stays
        self.assertEqual(self.why("addon", "kiosk", sandbox), "Also in Personal: turn it off first")
        by_itself = on(self.project, ["local/kiosk"], "local/sandbox")
        self.assertEqual(self.why("playground", "demo", by_itself), "Also in Personal: turn it off first")

    def test_an_item_that_brings_both_halves_is_not_told_to_turn_the_other_off(self):
        twins(self.project, "kafka")
        self.project["addons"]["kafka-desk"]["needs"] = ["kafka", "local/kafka"]
        self.assertIsNone(self.why("addon", "kafka-desk", on(self.project, ["local/kafka"])))

    def test_the_twin_of_a_playground_and_of_a_setup(self):
        self.project["playgrounds"]["welcome"]["twin"] = {"id": "local/sandbox", "name": "Personal"}
        self.assertEqual(self.why("playground", "welcome", on(self.project, playground="local/sandbox")),
                         "Also in Personal: turn it off first")
        self.assertIsNone(self.why("playground", "welcome"))
        self.project["setups"]["observability"]["twin"] = {"id": "local/mine", "name": "Personal"}
        self.assertEqual(self.why("setup", "observability", on(self.project, ["kiosk"], setups=["local/mine"])),
                         "Also in Personal: turn it off first")
        self.assertIsNone(self.why("setup", "observability"))

    # --- Ports -----------------------------------------------------------------------------------------------------

    def publish(self, **ports):
        """These services publish these host ports; the first catalog owns the add-ons."""
        for uid in self.project["addons"]:
            self.project["addons"][uid]["catalog"] = "fixture"
        for name, published in ports.items():
            self.project["services"][name]["ports"] = list(published)

    def test_a_port_that_something_on_publishes_is_taken(self):
        self.publish(kiosk=["5601"], **{"kafka-ui": ["5601"]})
        self.assertEqual(self.why("addon", "kiosk", on(self.project, ["kafka"])), "Port 5601 is used by Kafka")
        self.assertIsNone(self.why("addon", "kiosk"), "nothing else is on")

    def test_a_port_that_something_runs_on_is_taken_though_it_is_not_on(self):
        self.publish(kiosk=["5601"], **{"kafka-ui": ["5601"]})
        self.assertEqual(self.why("addon", "kiosk", running=up("kafka", project=self.project)),
                         "Port 5601 is used by Kafka")
        # The platform too: it runs
        self.project["services"]["apim-gateway"]["ports"] = ["8082"]
        self.publish(kiosk=["8082"])
        self.assertEqual(self.why("addon", "kiosk"), "Port 8082 is used by API Management")

    def test_the_first_port_by_number_names_the_item(self):
        self.publish(kiosk=["5601", "800"], **{"kafka-ui": ["800"], "jade": ["5601"]})
        self.assertEqual(self.why("addon", "kiosk", on(self.project, ["kafka", "jade"])), "Port 800 is used by Kafka")

    def test_what_the_item_brings_is_not_a_port_taken_from_it(self):
        # Kafka Desk and the Kafka it needs may be asked for the port of one another: they run together
        self.publish(**{"kafka-desk": ["9000"], "kafka-ui": ["9000"]})
        self.assertIsNone(self.why("addon", "kafka-desk"))
        self.assertIsNone(self.why("addon", "kafka-desk", on(self.project, ["kafka"]), up("kafka", project=self.project)))

    def test_what_the_same_plan_turns_off_does_not_hold_its_port(self):
        # Another playground replaces the one that is on: its port is free by then
        second(self.project)
        self.publish(**{"hello-app": ["8080"], "demo-app": ["8080"]})
        self.project["playgrounds"]["welcome"]["catalog"] = self.project["playgrounds"]["demo"]["catalog"] = "fixture"
        state = on(self.project, playground="welcome")
        self.assertIsNone(self.why("playground", "demo", state, up(playgrounds=["welcome"], project=self.project)))
        # An add-on that is on and stays holds it
        self.publish(kafka=["8080"], **{"kafka-ui": []})
        self.project["playgrounds"]["demo"]["addons"] = []
        self.assertEqual(self.why("playground", "demo", on(self.project, ["kafka"], "welcome")), "Port 8080 is used by Kafka")

    def test_the_twin_is_not_named_for_the_port_it_shares(self):
        twins(self.project)
        self.publish(kiosk=["5601"])
        state = on(self.project, ["kiosk"])
        unit = self.project["addons"]["local/kiosk"]
        self.assertIsNone(layers._port_clash(self.project, state, up("kiosk", project=self.project), "addon", unit))

    def test_a_unit_that_says_no_catalog_is_not_asked_for_its_ports(self):
        self.publish(kiosk=["5601"], **{"kafka-ui": ["5601"]})
        for uid in self.project["addons"]:
            del self.project["addons"][uid]["catalog"]
        self.assertIsNone(self.why("addon", "kiosk", on(self.project, ["kafka"])))

    def test_a_port_that_is_no_number_is_not_one(self):
        self.publish(kiosk=["8000-8010", "x"], **{"kafka-ui": ["8000-8010", "x"]})
        self.assertIsNone(self.why("addon", "kiosk", on(self.project, ["kafka"])))

    def test_a_place_judges_the_item_and_the_gateway_it_starts(self):
        cloud(self.project)
        self.block("hybrid", "Publishes a port to the network")
        place = {"id": "acme/dev", "name": "Dev (Acme)", "gateway": "hybrid", "here": True, "serving": None}
        plan = layers.plan(self.project, on(self.project), up(project=self.project), "playground", "hotels", "start",
                           place=place)
        self.assertEqual(plan["blocked"], "Publishes a port to the network")


class Preflight(Case):
    """A start of what comes from a catalog is checked by the catalogs before it runs."""

    def setUp(self):
        self.project = lab()
        own(self.project)
        for uid, catalog in (("kiosk", "fixture"), ("agent-lens", "fixture"), ("kafka", "fixture"),
                             ("kafka-desk", "partner"), ("local/whoami", "local")):
            self.project["addons"][uid]["catalog"] = catalog
        self.project["playgrounds"]["welcome"]["catalog"] = "fixture"
        self.project["playgrounds"]["local/sandbox"].update(catalog="local", includes=[], addons=["local/whoami"])
        self.service = types.ModuleType("service")
        self.service.preflight = mock.Mock()
        self.start(mock.patch.dict(sys.modules, {"lab_ui.catalogs.service": self.service}))
        self.patch(layout, "waiting", return_value=False)

    def plan(self, kind, uid, do="start", state=None):
        return layers.plan(self.project, state or on(self.project), up(project=self.project), kind, uid, do)

    def test_a_start_is_handed_the_units_of_catalogs_it_puts_in_the_on_file(self):
        plan = self.plan("playground", "welcome")
        layers.check(self.project, plan, [])
        self.service.preflight.assert_called_once_with(self.project, [("playground", "welcome"), ("addon", "agent-lens")])

    def test_what_is_on_already_and_what_is_brought_are_in_it_too(self):
        plan = self.plan("addon", "kafka-desk", state=on(self.project, ["kiosk"]))
        layers.check(self.project, plan, [])
        self.assertEqual(self.service.preflight.call_args.args[1],
                         [("addon", "kafka-desk"), ("addon", "kafka"), ("addon", "kiosk")])

    def test_the_units_of_this_machine_are_nobodys_catalog(self):
        layers.check(self.project, self.plan("playground", "local/sandbox"), [])
        self.service.preflight.assert_not_called()
        self.project["playgrounds"]["local/sandbox"].update(includes=["kiosk"], addons=["kiosk"])   # it brings an add-on of a catalog
        layers.check(self.project, self.plan("playground", "local/sandbox"), [])
        self.assertEqual(self.service.preflight.call_args.args[1], [("addon", "kiosk")])

    def test_a_unit_that_says_no_catalog_has_nothing_to_verify(self):
        for unit in self.project["addons"].values():
            unit.pop("catalog", None)
        layers.check(self.project, self.plan("addon", "kiosk"), [])
        self.service.preflight.assert_not_called()

    def test_a_stop_is_not_verified(self):
        state = on(self.project, ["kiosk"])
        layers.check(self.project, self.plan("addon", "kiosk", "stop", state), [])
        self.service.preflight.assert_not_called()

    def test_what_it_refuses_is_said_as_it_is(self):
        line = "Kiosk changed on disk after it was installed. Repair Fixture catalog, then start it again."
        self.service.preflight.side_effect = RuntimeError(line)
        with self.assertRaisesRegex(RuntimeError, f"^{line}$"):
            layers.check(self.project, self.plan("addon", "kiosk"), [])

    def test_a_plan_that_is_refused_by_itself_is_never_verified(self):
        self.project["addons"]["kiosk"]["blocked"] = {"reasons": ["Mounts the Docker socket"]}
        with self.assertRaisesRegex(RuntimeError, "^Mounts the Docker socket$"):
            layers.check(self.project, self.plan("addon", "kiosk"), [])
        self.project["addons"]["kiosk"]["blocked"] = None
        with self.assertRaisesRegex(RuntimeError, "^Would also turn off Jade$"):
            layers.check(self.project, self.plan("addon", "agent-lens", state=on(self.project, ["jade"])), [])
        self.service.preflight.assert_not_called()

    def test_a_caller_that_leaves_out_the_project_or_what_the_page_sent_back_is_refused_not_let_through_unverified(self):
        # The form check(plan, accept) skipped the preflight without a word: it is gone, and so is the default of `accept`
        parameters = inspect.signature(layers.check).parameters.values()
        self.assertEqual([(one.name, one.default) for one in parameters],
                         [(name, inspect.Parameter.empty) for name in ("project", "plan", "accept")])
        plan = self.plan("addon", "kiosk")
        for call in (lambda: layers.check(plan, []), lambda: layers.check(self.project, plan), lambda: layers.check(plan)):
            with self.assertRaises(TypeError):
                call()
        self.service.preflight.assert_not_called()

    def test_a_plan_from_a_caller_that_names_no_kind_is_not_verified(self):
        layers.check(self.project, {"blocked": None, "stops": [], "turns_off": [], "itself": set()}, [])
        self.service.preflight.assert_not_called()


class From(Case):
    """The row From of the plan of a start."""

    def setUp(self):
        self.project = lab()
        own(self.project)
        self.patch(layout, "waiting", return_value=False)

    def plan(self, kind="addon", uid="kiosk", do="start", state=None):
        return layers.plan(self.project, state or on(self.project), up(project=self.project), kind, uid, do)

    def come_from(self, uid="kiosk", kind="addon", **origin):
        self.project[kind + "s"][uid]["origin"] = {"id": "partner", "name": "Partner Bank", "version": "2.0.0", **origin}

    def test_an_item_of_another_catalog_says_its_catalog_and_its_version(self):
        self.come_from()
        plan = self.plan()
        self.assertEqual(plan["from"], [{"kind": "catalog", "id": "partner", "name": "Partner Bank"}])
        self.assertEqual(plan["hints"], {"from": "Version 2.0.0"})
        self.assertEqual({key: layers.dialog(plan)[key] for key in ("from", "hints")},
                         {"from": plan["from"], "hints": plan["hints"]})

    def test_it_is_said_for_a_playground_and_a_setup_and_in_a_dialog_that_opens_for_another_reason(self):
        self.come_from("welcome", "playground")
        self.assertEqual(self.plan("playground", "welcome")["hints"], {"from": "Version 2.0.0"})
        self.come_from("observability", "setup", name="Acme Hub", version="1.4.0")
        self.assertEqual(self.plan("setup", "observability")["from"][0]["name"], "Acme Hub")
        self.come_from("kafka-desk")
        plan = self.plan("addon", "kafka-desk")   # it starts Kafka too
        self.assertTrue(layers.asks(plan))
        self.assertEqual(layers.dialog(plan)["from"][0]["id"], "partner")

    def test_the_row_never_makes_a_dialog_open(self):
        self.come_from()
        self.assertFalse(layers.asks(self.plan()))

    def test_the_first_catalog_and_this_machine_say_none(self):
        for uid in ("kiosk", "local/whoami"):
            kind = "addon"
            self.project["addons"][uid]["origin"] = None
            plan = self.plan(kind, uid)
            self.assertNotIn("from", plan)
            self.assertNotIn("hints", plan)
            self.assertEqual(layers.dialog(plan), {key: plan[key] for key in layers.ROWS})

    def test_a_catalog_that_says_no_version_has_no_words_under_it(self):
        for version in (None, ""):
            self.come_from(version=version)
            plan = self.plan()
            self.assertEqual(plan["from"][0]["name"], "Partner Bank")
            self.assertNotIn("hints", plan)

    def test_a_stop_says_none(self):
        self.come_from()
        plan = self.plan(do="stop", state=on(self.project, ["kiosk"]))
        self.assertNotIn("from", plan)

    def test_the_words_of_an_author_are_cut_and_clean(self):
        self.come_from(name="Acme\x1b[31m\nHub " + "x" * 200, version="1.0\x00" + "9" * 100)
        plan = self.plan()
        name, hint = plan["from"][0]["name"], plan["hints"]["from"]
        self.assertTrue(len(name) <= 80 and len(hint) <= 48, (len(name), len(hint)))
        self.assertTrue(all(c.isprintable() for c in name + hint))
        self.assertTrue(name.startswith("Acme [31m Hub x") and name.endswith("…"), name)

    def test_a_place_plan_has_the_row_of_the_item_it_deploys(self):
        cloud(self.project)
        self.come_from("hotels", "playground")
        place = {"id": "acme/dev", "name": "Dev (Acme)", "gateway": "hybrid", "here": True, "serving": None}
        plan = layers.plan(self.project, on(self.project), up(project=self.project), "playground", "hotels", "start",
                           place=place)
        self.assertEqual(plan["from"][0]["name"], "Partner Bank")


DEFAULT = json.dumps({"schema": 1, "catalogs": [{"id": "fixture", "url": "https://example.invalid/fixture.git"}]})


class Entries(Case):
    """The on file in the layout of the catalogs, in a project folder that holds only what a test writes."""

    def setUp(self):
        self.folder(paths, compose, envfiles, projects, files={paths.DEFAULT_FILE: DEFAULT})
        self.project = lab()
        self.project["addons"]["@partner/ledger"] = _addon("@partner/ledger", "Ledger", ["ledger"])
        self.project["services"]["ledger"] = _service("ledger", {"kind": "addon", "id": "@partner/ledger", "name": "Ledger"})
        own(self.project)
        for kind in ("addon", "setup", "playground"):
            for uid, unit in self.project[kind + "s"].items():
                unit["file"] = paths.unit_file(kind, uid)   # where the project says a unit is, once it has catalogs
                self.write(unit["file"])
                if kind == "addon":
                    unit["env_file"] = paths.unit_env(kind, uid)
        self.waiting = self.patch(layout, "waiting", return_value=False)

    def on_file(self, text):
        self.write(paths.ON_FILE, text)
        return layers.current(self.project)

    def test_the_entries_are_catalog_paths_and_are_recognized(self):
        layers.write("welcome", ["kiosk", "@partner/ledger", "local/whoami"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - .lab/catalogs/fixture/playgrounds/welcome/compose.yml\n"
                         "  - .lab/catalogs/partner/addons/ledger/compose.yml\n"
                         "  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n"
                         "  - .lab/catalogs/local/addons/whoami/compose.yml\n")
        state = layers.current(self.project)
        self.assertEqual((state["playground"], state["explicit"], state["locked"], state["problems"]),
                         ("welcome", ["@partner/ledger", "kiosk", "local/whoami"], None, []))
        self.assertEqual(state["effective"], ["@partner/ledger", "agent-lens", "kiosk", "local/whoami"])

    def test_a_setup_of_a_catalog_stands_for_its_add_ons(self):
        state = self.on_file("include:\n  - .lab/catalogs/fixture/setups/events.yml\n")
        self.assertEqual((state["setups"], state["explicit"], state["effective"], state["locked"]),
                         (["events"], ["kafka-desk"], ["kafka", "kafka-desk"], None))

    def test_an_item_the_policy_blocks_is_an_entry_like_any_other_and_locks_nothing(self):
        self.project["addons"]["kiosk"]["blocked"] = {"reasons": ["Mounts the Docker socket"]}
        self.project["addons"]["kiosk"]["services"] = []
        state = self.on_file("include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        self.assertEqual((state["explicit"], state["locked"], state["problems"]), (["kiosk"], None, []))

    def test_an_entry_of_a_catalog_whose_file_does_not_load_locks_the_file_as_before(self):
        del self.project["addons"]["@partner/ledger"]
        state = self.on_file("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        self.assertEqual(state["locked"], ".lab/catalogs/partner/addons/ledger/compose.yml does not load")

    def test_a_line_of_the_old_layout_locks_the_file_once_the_layout_is_moved(self):
        self.write("addons/kiosk/compose.yml")
        state = self.on_file("include:\n  - addons/kiosk/compose.yml\n")
        self.assertEqual((state["explicit"], state["locked"]), ([], layers.LOCKED))

    def test_while_the_layout_is_moving_a_line_of_the_old_layout_is_no_problem_and_locks_nothing(self):
        self.waiting.return_value = True
        # The files are not where the line says: neither a missing file, nor a file that does not load
        state = self.on_file("include:\n  - addons/kiosk/compose.yml\n  - setups/events.yml\n"
                             "  - playgrounds/local/mine/compose.yml\n  - addons/local/whoami/compose.yml\n"
                             "  - .lab/catalogs/fixture/addons/jade/compose.yml\n")
        self.assertEqual((state["explicit"], state["locked"], state["problems"], state["setups"]),
                         (["jade"], None, [], []))

    def test_the_state_asks_whether_the_layout_is_moving_once_and_only_for_a_line_that_looks_like_the_old_ones(self):
        # layout.waiting() reads the disk, and every state poll and every plan reads the on file: it is not asked per line
        self.waiting.return_value = True
        self.on_file("include:\n  - addons/kiosk/compose.yml\n  - setups/events.yml\n  - playgrounds/local/mine/compose.yml\n"
                     "  - addons/local/whoami/compose.yml\n  - addons/kafka/compose.yml\n")
        self.assertEqual(self.waiting.call_count, 1)
        self.waiting.reset_mock()
        self.on_file("include:\n  - .lab/catalogs/fixture/addons/jade/compose.yml\n  - .lab/catalogs/local/addons/whoami/compose.yml\n")
        self.waiting.assert_not_called()
        self.on_file("")
        self.waiting.assert_not_called()

    def test_while_the_layout_is_moving_the_old_line_of_an_item_on_another_platform_is_none_either(self):
        self.waiting.return_value = True
        state = self.on_file('include:\n  - {path: ".lab/targets/cloud/addons__hybrid__compose.yml", '
                             'project_directory: "addons/hybrid"}\n')
        self.assertEqual((state["explicit"], state["locked"], state["problems"], state["targets"]), ([], None, [], {}))

    LEGACY_ON = ('include:\n  - addons/kafka/compose.yml\n  - .lab/catalogs/local/addons/whoami/compose.yml\n'
                 '  - {path: ".lab/targets/cloud/addons__hybrid__compose.yml", project_directory: "addons/hybrid"}\n'
                 "  - setups/events.yml\n  - .lab/catalogs/fixture/addons/jade/compose.yml\n")

    def test_a_write_while_the_layout_is_moving_keeps_the_lines_of_the_old_layout(self):
        # The move translates the file as it finds it, and reads it live: a stop of one item of the person's own, which stays
        # possible, must not drop what they had on
        self.waiting.return_value = True
        state = self.on_file(self.LEGACY_ON)
        self.assertEqual((state["explicit"], state["locked"]), (["jade", "local/whoami"], None))
        plan = layers.plan(self.project, state, up(project=self.project), "addon", "local/whoami", "stop")
        layers.write(plan["playground"], plan["explicit"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - .lab/catalogs/fixture/addons/jade/compose.yml\n  - addons/kafka/compose.yml\n"
                         '  - {path: ".lab/targets/cloud/addons__hybrid__compose.yml", project_directory: "addons/hybrid"}\n'
                         "  - setups/events.yml\n")
        # Nothing else on: the file holds the old lines alone, and is still read as before
        layers.write(None, [])
        self.assertEqual(layers.current(self.project)["locked"], None)
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - addons/kafka/compose.yml\n"
                         '  - {path: ".lab/targets/cloud/addons__hybrid__compose.yml", project_directory: "addons/hybrid"}\n'
                         "  - setups/events.yml\n")
        # Every write after keeps them, and so does a write that adds an entry
        layers.write(None, ["kiosk"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8").splitlines()[:2],
                         ["include:", "  - .lab/catalogs/fixture/addons/kiosk/compose.yml"])
        self.assertEqual(layers._legacy_lines(), ["  - addons/kafka/compose.yml",
                                                  '  - {path: ".lab/targets/cloud/addons__hybrid__compose.yml", '
                                                  'project_directory: "addons/hybrid"}', "  - setups/events.yml"])

    def test_only_a_line_that_names_a_file_of_the_old_layout_is_kept(self):
        self.waiting.return_value = True
        self.on_file("include:\n  - addons/kafka/compose.yml  # mine\n  - './playgrounds/local/mine/compose.yml'\n"
                     "  - mine.yml\n  - addons/kafka/other.yml\n  - ../addons/kafka/compose.yml\n"
                     '  - {path: ".lab/targets/cloud/mine__compose.yml", project_directory: "x"}\n'
                     "  - .lab/catalogs/fixture/addons/jade/compose.yml\n# addons/kafka/compose.yml\n")
        self.assertEqual(layers._legacy_lines(), ["  - addons/kafka/compose.yml  # mine",
                                                  "  - './playgrounds/local/mine/compose.yml'"])

    def test_once_the_layout_is_moved_a_write_keeps_what_it_is_given_and_nothing_else(self):
        self.write(paths.ON_FILE, self.LEGACY_ON)
        self.assertEqual(layers._legacy_lines(), [])
        layers.write(None, ["kiosk"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        layers.write(None, [])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")

    def test_while_the_layout_is_moving_what_is_not_a_line_of_the_old_layout_still_locks(self):
        self.waiting.return_value = True
        self.write("mine.yml")
        state = self.on_file("include:\n  - addons/kiosk/compose.yml\n  - mine.yml\n")
        self.assertEqual(state["locked"], layers.LOCKED)
        self.assertEqual(self.on_file("include:\n  - addons/kiosk/compose.yml\nservices:\n  redis: {}\n")["locked"], layers.LOCKED)

    def test_an_item_of_a_catalog_runs_on_a_platform_through_its_copy(self):
        self.write(paths.unit_file("addon", "kafka"), "services:\n  apim-gateway:\n    environment:\n      A: b\n")
        layers.write(None, ["kafka"], {paths.unit_file("addon", "kafka"): "cloud"}, {"cloud": {"apim-gateway": "apim-hybrid"}})
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         'include:\n  - {path: ".lab/targets/cloud/.lab__catalogs__fixture__addons__kafka__compose.yml", '
                         'project_directory: ".lab/catalogs/fixture/addons/kafka"}\n')
        state = layers.current(self.project)
        self.assertEqual((state["explicit"], state["targets"], state["locked"]), (["kafka"], {"addon:kafka": "cloud"}, None))

    def test_the_settings_of_an_add_on_of_a_catalog_are_read_from_a_plain_env_only(self):
        env = self.root / paths.unit_env("addon", "hybrid")
        self.write(paths.unit_env("addon", "hybrid"), "HYBRID_CLOUD_TOKEN=token\n")
        self.assertEqual(layers.current(self.project)["missing"], [])
        # A link that a catalog shipped, to a file of the person's: the settings it holds are not read
        outside = self.root / "outside.env"
        outside.write_text("HYBRID_CLOUD_TOKEN=token\n", encoding="utf-8")
        env.unlink()
        os.symlink(outside, env)
        self.assertEqual(layers.current(self.project)["missing"], ["hybrid"])
        env.unlink()
        os.mkfifo(env)   # a pipe is not waited for
        self.assertEqual(layers.current(self.project)["missing"], ["hybrid"])

    def test_a_unit_of_one_catalog_brings_a_unit_of_another(self):
        # closure follows the ids the project gives, whatever catalog they come from
        self.project["addons"]["@partner/ledger"]["needs"] = ["kiosk"]
        self.assertEqual(layers.closure(self.project, ["@partner/ledger"]), {"@partner/ledger", "kiosk"})
        self.assertEqual(layers.current(self.project)["effective"], [])

    # --- The writer ------------------------------------------------------------------------------------------------

    BLOCKED = {".lab/catalogs/fixture/addons/kiosk/compose.yml": "Mounts the Docker socket"}

    def test_no_entry_is_added_for_a_unit_that_is_blocked(self):
        layers.write("welcome", ["jade"])
        before = (self.root / paths.ON_FILE).read_text(encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "^kiosk is blocked: Mounts the Docker socket$"):
            layers.write("welcome", ["jade", "kiosk"], blocked=self.BLOCKED)
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), before, "nothing was written")

    def test_an_entry_that_is_there_stays_and_a_removal_never_raises(self):
        layers.write("welcome", ["jade", "kiosk"])
        layers.write("welcome", ["jade", "kiosk"], blocked=self.BLOCKED)   # a stop of something else keeps it
        self.assertIn("kiosk", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))
        layers.write(None, ["kiosk"], blocked=self.BLOCKED)
        layers.write(None, [], blocked=self.BLOCKED)
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")

    def test_no_map_and_an_empty_one_gate_nothing(self):
        for blocked in (None, {}):
            layers.write("welcome", ["kiosk"], blocked=blocked)

    def test_a_unit_of_a_catalog_that_is_not_in_the_map_is_added(self):
        layers.write("welcome", ["kiosk"], blocked={".lab/catalogs/fixture/addons/jade/compose.yml": "No"})
        self.assertIn("kiosk", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))

    def test_the_map_is_every_unit_the_policy_blocks_or_that_lacks_a_catalog(self):
        self.project["addons"]["kiosk"]["blocked"] = {"reasons": ["Mounts the Docker socket", "Runs with full privileges"]}
        self.project["addons"]["jade"]["needs_catalogs"] = [need("missing")]
        self.project["setups"]["events"]["blocked"] = {"reasons": ["Depends on private content"]}
        self.project["playgrounds"]["welcome"]["needs_catalogs"] = [need("old")]
        self.assertEqual(layers.blocked_files(self.project), {
            ".lab/catalogs/fixture/addons/kiosk/compose.yml": "Mounts the Docker socket",
            ".lab/catalogs/fixture/addons/jade/compose.yml": NEEDS["missing"],
            ".lab/catalogs/fixture/setups/events.yml": "Depends on private content",
            ".lab/catalogs/fixture/playgrounds/welcome/compose.yml": NEEDS["old"]})
        self.assertEqual(layers.blocked_files(lab()), {})

    def test_apply_writes_the_on_file_through_the_gate(self):
        self.project["addons"]["kiosk"]["blocked"] = {"reasons": ["Mounts the Docker socket"]}
        plan = layers.plan(self.project, on(self.project), up(project=self.project), "addon", "kiosk", "start")
        self.assertEqual(plan["blocked"], "Mounts the Docker socket")
        plan = {**plan, "blocked": None}   # a plan that was made before the unit was blocked
        jobs = self.patch(layers, "JOBS")
        layers.apply(self.project, plan, "Title")
        _, (write, *_), _ = self.started(jobs)
        with self.assertRaisesRegex(RuntimeError, "^kiosk is blocked: Mounts the Docker socket$"):
            write(mock.Mock(lines=[]))
        self.assertFalse((self.root / paths.ON_FILE).exists())

    def test_apply_makes_the_folders_the_binds_of_what_starts_name(self):
        self.project["playgrounds"]["welcome"]["catalog"] = "fixture"
        folder = self.root / paths.unit_folder("playground", "welcome")
        config = {"services": {"hello-app": {"volumes": [
            {"type": "bind", "source": str(folder / ".state"), "target": "/state"}]}}}
        self.patch(compose, "config_json", return_value=config)
        jobs = self.patch(layers, "JOBS")
        plan = layers.plan(self.project, on(self.project), up(project=self.project), "playground", "welcome", "start")
        layers.apply(self.project, plan, "Title")
        _, (write, *_), _ = self.started(jobs)
        write(mock.Mock(lines=[]))
        self.assertTrue((folder / ".state").is_dir())
        self.assertEqual(layers.current(self.project)["playground"], "welcome")


class Environment(Case):
    """What a catalog needs of .env."""

    LINES = {"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "false", "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "false"}

    def setUp(self):
        self.folder(paths, envfiles)

    def test_the_two_settings_are_appended_once_with_the_mode_of_the_file(self):
        self.write(".env", "GRAVITEE_LICENSE=keep-me")
        self.assertTrue(layers.prepare_catalogs())
        self.assertFalse(layers.prepare_catalogs(), "once")
        text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("GRAVITEE_LICENSE=keep-me\n"), "what was there stays")
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "keep-me", **self.LINES})
        self.assertEqual(text.count("COMPOSE_EXPERIMENTAL_"), 2)
        self.assertEqual(oct((self.root / ".env").stat().st_mode & 0o777), "0o600")
        self.assertEqual(compose.NO_REMOTE, self.LINES, "the same two as every command the Lab runs")

    def test_a_value_the_person_set_is_theirs(self):
        self.write(".env", "COMPOSE_EXPERIMENTAL_GIT_REMOTE=true\n")
        self.assertTrue(layers.prepare_catalogs())
        self.assertEqual(envfiles.read(".env"), {"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "true",
                                                 "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "false"})
        self.assertFalse(layers.prepare_catalogs())

    def test_a_comment_does_not_count_as_a_setting(self):
        self.write(".env", "# COMPOSE_EXPERIMENTAL_GIT_REMOTE=true\n")
        self.assertTrue(layers.prepare_catalogs())
        self.assertEqual(envfiles.read(".env"), self.LINES)

    def test_without_a_dot_env_nothing_is_made(self):
        # make init makes it from .env.example, and a file made here would stop it
        self.assertFalse(layers.prepare_catalogs())
        self.assertFalse((self.root / ".env").exists())

    def test_an_env_that_is_not_utf8_text_is_left_as_it_is_and_is_no_failure_of_the_catalog_that_asked(self):
        # The Add and the Update ask after the catalog is in place, and suppress what a disk can raise (OSError): a ValueError
        # would turn an Add that worked into an answer that says it failed
        for raw in (b"# caf\xe9\nFOO=1\n", b"\xff\xfe\x00A=b\n"):
            with self.subTest(raw=raw):
                (self.root / ".env").write_bytes(raw)
                self.assertFalse(layers.prepare_catalogs())
                self.assertEqual((self.root / ".env").read_bytes(), raw, "the person's bytes are not touched")

    def test_a_lab_with_no_catalog_never_has_them(self):
        self.write(".env", "A=b\n")
        layers.prepare()
        self.assertNotIn("COMPOSE_EXPERIMENTAL_GIT_REMOTE", (self.root / ".env").read_text(encoding="utf-8"))


class Binds(Case):
    """The folders a start makes as the user: inside the folder of its units, and nowhere else."""

    def setUp(self):
        self.folder(paths, compose, envfiles, files={paths.DEFAULT_FILE: DEFAULT})
        self.project = lab()
        for kind, uid in (("playground", "welcome"), ("addon", "agent-lens")):
            unit = self.project[kind + "s"][uid]
            unit.update(catalog="fixture", file=paths.unit_file(kind, uid))
            self.write(unit["file"])
        self.unit = self.root / paths.unit_folder("playground", "welcome")
        self.brought = self.root / paths.unit_folder("addon", "agent-lens")
        self.plan = {"do": "start", "kind": "playground", "id": "welcome", "playground": "welcome", "explicit": []}
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def binds(self, *sources, **more):
        """Compose resolves the files to these binds."""
        volumes = [{"type": "bind", "source": str(source), "target": "/x", **more} for source in sources]
        return self.patch(compose, "config_json", return_value={"services": {"app": {"volumes": volumes}}})

    def test_a_missing_folder_inside_the_folder_of_the_playground_is_made_with_its_parents(self):
        os.umask(0o077)
        self.addCleanup(os.umask, 0o022)
        self.binds(self.unit / ".state", self.unit / "data" / "deep" / "er")
        made = layers.make_binds(self.project, self.plan)
        self.assertEqual(made, [str(self.unit / ".state"), str(self.unit / "data"), str(self.unit / "data/deep"),
                                str(self.unit / "data/deep/er")])
        for folder in made:
            self.assertEqual(oct(os.stat(folder).st_mode & 0o777), "0o755", folder)
        self.assertEqual(layers.make_binds(self.project, self.plan), [], "once")

    def test_the_folder_of_what_it_brings_is_one_of_its_own(self):
        self.binds(self.brought / ".state")
        self.assertEqual(layers.make_binds(self.project, self.plan), [str(self.brought / ".state")])

    def test_what_is_there_is_never_replaced(self):
        (self.unit / ".state").write_text("mine", encoding="utf-8")
        self.binds(self.unit / ".state", self.unit / ".state" / "below")
        self.assertEqual(layers.make_binds(self.project, self.plan), [])
        self.assertEqual((self.unit / ".state").read_text(encoding="utf-8"), "mine")

    def test_nothing_outside_the_folders_is_made(self):
        self.binds(self.outside / "new", self.root / "elsewhere" / "new", "/tmp/lab-bind-not-made",
                   self.unit / ".." / "other" / "new", self.unit / "a" / ".." / ".." / "out", self.unit,
                   str(self.root / "welcome-apart"))
        self.assertEqual(layers.make_binds(self.project, self.plan), [])
        self.assertEqual(sorted(os.listdir(self.outside)), [])
        self.assertFalse((self.root / "elsewhere").exists())

    def test_the_folder_of_another_unit_is_not_this_ones_to_fill(self):
        other = self.root / paths.unit_folder("playground", "demo")
        other.mkdir(parents=True)
        self.binds(other / ".state")
        self.assertEqual(layers.make_binds(self.project, self.plan), [])

    def test_a_link_is_never_followed(self):
        os.symlink(self.outside, self.unit / "state")
        self.binds(self.unit / "state" / "new", self.unit / "state" / "a" / "b")
        self.assertEqual(layers.make_binds(self.project, self.plan), [])
        self.assertEqual(os.listdir(self.outside), [])
        # A folder that a container planted below the first, to lead out
        (self.unit / "data").mkdir()
        os.symlink(self.outside, self.unit / "data" / "out")
        self.binds(self.unit / "data" / "out" / "new", self.unit / "data" / "fresh")
        self.assertEqual(layers.make_binds(self.project, self.plan), [str(self.unit / "data" / "fresh")])
        self.assertEqual(os.listdir(self.outside), [])

    def test_a_file_is_a_source_the_person_provides(self):
        self.binds(self.unit / "secrets" / "key.pem", self.unit / "config.json", self.unit / "seed.d")
        self.assertEqual(layers.make_binds(self.project, self.plan), [])
        self.assertFalse((self.unit / "secrets").exists(), "nor its parent")

    def test_a_name_with_a_dot_in_front_is_a_folder(self):
        self.binds(self.unit / ".state", self.unit / ".cache")
        self.assertEqual(len(layers.make_binds(self.project, self.plan)), 2)

    def test_docker_is_told_not_to_make_it_and_the_lab_does_not_either(self):
        self.binds(self.unit / "secrets", bind={"create_host_path": False})
        self.assertEqual(layers.make_binds(self.project, self.plan), [])
        self.binds(self.unit / "secrets", bind={"create_host_path": True})
        self.assertEqual(layers.make_binds(self.project, self.plan), [str(self.unit / "secrets")])

    def test_only_a_bind_is_made(self):
        self.patch(compose, "config_json", return_value={"services": {"app": {"volumes": [
            {"type": "volume", "source": str(self.unit / "named"), "target": "/x"},
            {"type": "tmpfs", "target": "/y"}, {"type": "bind", "target": "/z"}, "mine:/z", None,
            {"type": "bind", "source": 7, "target": "/w"}]}, "other": None}, "volumes": {}})
        self.assertEqual(layers.make_binds(self.project, self.plan), [])

    def test_a_name_the_system_cannot_take_makes_nothing(self):
        self.binds(self.unit / "a\0b" / "c", self.unit / ("x" * 5000) / "y", self.unit / ".state")
        self.assertEqual(layers.make_binds(self.project, self.plan), [str(self.unit / ".state")])

    def test_a_product_folder_of_the_plugins_may_be_filled(self):
        (self.root / "plugins" / "apim").mkdir(parents=True)
        (self.root / "plugins" / "am").mkdir()
        os.symlink(self.outside, self.root / "plugins" / "linked")
        self.binds(self.root / "plugins" / "apim" / "ext", self.root / "plugins" / "new" / "ext",
                   self.root / "plugins" / "linked" / "ext", self.root / "plugins" / "am")
        self.assertEqual(layers.make_binds(self.project, self.plan), [str(self.root / "plugins" / "apim" / "ext")])
        self.assertEqual(os.listdir(self.outside), [])

    def test_only_a_start_of_units_of_a_catalog_reads_anything(self):
        config = self.patch(compose, "config_json", return_value={"services": {}})
        self.assertEqual(layers.make_binds(self.project, {**self.plan, "do": "stop"}), [])
        self.assertEqual(layers.make_binds(self.project, {}), [])
        del self.project["playgrounds"]["welcome"]["catalog"]
        del self.project["addons"]["agent-lens"]["catalog"]
        self.assertEqual(layers.make_binds(self.project, self.plan), [])
        config.assert_not_called()

    def test_a_setup_has_no_folder_and_no_service(self):
        self.project["setups"]["observability"]["catalog"] = "fixture"
        config = self.binds(self.unit / ".state")
        layers.make_binds(self.project, {"do": "start", "kind": "setup", "id": "observability", "playground": None,
                                         "explicit": ["jade", "kiosk"]})
        config.assert_not_called()   # jade and kiosk say no catalog, a setup has no folder

    def test_when_compose_cannot_read_the_files_nothing_is_said_and_nothing_made(self):
        for error in (compose.ComposeError("services.app.volumes must be a list"), ValueError("not JSON")):
            self.patch(compose, "config_json", side_effect=error)
            self.assertEqual(layers.make_binds(self.project, self.plan), [])

    def test_the_resolved_files_are_those_of_what_the_plan_puts_in_the_on_file(self):
        config = self.binds()
        layers.make_binds(self.project, self.plan)
        config.assert_called_once()
        core, on_top = config.call_args.args[0][1], config.call_args.args[0][3]
        self.assertEqual(core, "docker-compose.yml")
        with open(on_top, encoding="utf-8") as file:
            listed = [line.strip(' -"\n') for line in file.read().split("\n")[1:] if line.strip()]
        self.assertEqual(listed, [str(self.root / paths.unit_file("playground", "welcome")),
                                  str(self.root / paths.unit_file("addon", "agent-lens"))])


# ---------------------------------------------------------------------------------------------------------------------
# The fixture catalogs, as the Lab UI reads them
# ---------------------------------------------------------------------------------------------------------------------

def nothing_up(*services, addons=()):
    """What model.up() answers when nothing of the project runs but these services (started outside the Lab UI), those of
    these add-ons."""
    return {"platform": False, "groups": set(), "addons": set(addons), "playgrounds": set(), "services": set(services),
            "done": set(), "created": set(services)}


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Real(Case):
    """The units of the fixture catalogs, read with the real core by Compose (no daemon): what the gates say of them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures.need_compose()
        holder = cls.holder = Case()   # a case of the class's own: the folder it makes and what it patches last as long
        cls.addClassCleanup(holder.doCleanups)
        cls.inputs, cls.built = read(holder, {"partner": {}, "lab-new": {}, "lab-old": {}}, ("scratch", "sandbox"), {})
        cls.root = holder.root

    def setUp(self):
        self.waiting = self.patch(layout, "waiting", return_value=False)
        self.addCleanup(lambda: (self.root / paths.ON_FILE).exists() and (self.root / paths.ON_FILE).unlink())

    def begin(self, kind, uid, state=None, running=None):
        state = state or layers.current(self.built)
        return layers.plan(self.built, state, running or nothing_up(), kind, uid, "start")

    def test_the_entries_of_every_catalog_are_recognized_and_a_unit_the_policy_blocks_locks_nothing(self):
        layers.write("tour", ["future", "@partner/ledger", "local/scratch", "echo"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "include:\n"
                         "  - .lab/catalogs/fixture/playgrounds/tour/compose.yml\n"
                         "  - .lab/catalogs/partner/addons/ledger/compose.yml\n"
                         "  - .lab/catalogs/fixture/addons/echo/compose.yml\n"
                         "  - .lab/catalogs/fixture/addons/future/compose.yml\n"
                         "  - .lab/catalogs/local/addons/scratch/compose.yml\n")
        state = layers.current(self.built)
        self.assertEqual((state["playground"], state["explicit"], state["locked"], state["problems"]),
                         ("tour", ["@partner/ledger", "echo", "future", "local/scratch"], None, []))

    def test_the_map_of_what_must_not_start_is_what_the_project_says_blocked(self):
        found = layers.blocked_files(self.built)
        self.assertEqual(found, {
            ".lab/catalogs/fixture/addons/future/compose.yml": "Needs Lab level 99, this Lab reads 1 to 1",
            ".lab/catalogs/fixture/setups/pair.yml": "Depends on private content",
            ".lab/catalogs/lab-new/addons/newer/compose.yml": "Needs Lab level 99, this Lab reads 1 to 1",
            ".lab/catalogs/lab-old/addons/older/compose.yml": "Written for an older Lab (level 0)"})

    def test_a_blocked_unit_is_refused_by_the_plan_and_by_the_writer_and_never_for_a_stop(self):
        plan = self.begin("addon", "future")
        self.assertEqual(plan["blocked"], "Needs Lab level 99, this Lab reads 1 to 1")
        with self.assertRaisesRegex(RuntimeError, "^future is blocked: Needs Lab level 99, this Lab reads 1 to 1$"):
            layers.write(None, ["future"], blocked=layers.blocked_files(self.built))
        self.assertFalse((self.root / paths.ON_FILE).exists())
        layers.write(None, ["future"])   # it is on, whoever put it there
        state = layers.current(self.built)
        stop = layers.plan(self.built, state, nothing_up(), "addon", "future", "stop")
        self.assertIsNone(stop["blocked"])
        layers.write(stop["playground"], stop["explicit"], blocked=layers.blocked_files(self.built))   # a removal
        self.assertEqual(layers.current(self.built)["explicit"], [])

    def test_what_a_setup_brings_from_another_catalog_that_is_blocked_blocks_it(self):
        plan = self.begin("setup", "pair")
        self.assertEqual(plan["blocked"], "Depends on private content")

    def test_a_port_the_core_publishes_is_taken_while_it_runs(self):
        self.assertIsNone(self.begin("addon", "port-core")["blocked"], "nothing runs")
        plan = self.begin("addon", "port-core", running=nothing_up("apim-gateway"))
        self.assertEqual(plan["blocked"], "Port 8082 is used by API Management")

    def test_a_port_two_units_publish_is_taken_while_the_first_runs_or_is_on_and_not_when_it_is_turned_off_for_it(self):
        running = nothing_up("fixture-echo", addons=["echo"])
        self.assertEqual(self.begin("addon", "echo-twin", running=running)["blocked"], "Port 8191 is used by Echo")
        # On: the twin declares a conflict with Echo, so starting it turns Echo off, which frees the port
        layers.write(None, ["echo"])
        state = layers.current(self.built)
        plan = self.begin("addon", "echo-twin", state)
        self.assertEqual([row["id"] for row in plan["turns_off"]], ["echo"])
        self.assertIsNone(plan["blocked"])
        # Echo stays when the thing that comes does not conflict with it: the port is taken
        self.assertEqual(self.begin("addon", "echo-core", state)["blocked"], None, "it brings Echo, which is its own")

    def test_a_start_is_verified_by_the_catalogs_for_the_units_of_catalogs_it_puts_in_the_on_file(self):
        service = types.ModuleType("service")
        service.preflight = mock.Mock()
        with mock.patch.dict(sys.modules, {"lab_ui.catalogs.service": service}):
            layers.check(self.built, self.begin("addon", "@partner/ledger"), [])
            service.preflight.assert_called_once_with(self.built, [("addon", "@partner/ledger")])
            service.preflight.reset_mock()
            # This machine's own add-on is nobody's catalog: only the Echo of the first catalog that it brings is verified
            layers.check(self.built, self.begin("addon", "local/scratch"), [])
            service.preflight.assert_called_once_with(self.built, [("addon", "echo")])

    def test_the_folders_a_playground_binds_are_made_in_its_folder_and_nowhere_else(self):
        folder = self.root / paths.unit_folder("playground", "tour")
        self.addCleanup(lambda: __import__("shutil").rmtree(folder / ".state", ignore_errors=True))
        before = sorted(os.listdir(folder))
        self.assertNotIn(".state", before)
        state = layers.current(self.built)
        made = layers.make_binds(self.built, self.begin("playground", "tour", state))
        self.assertEqual(made, [str(folder / ".state")])
        self.assertEqual(oct(os.stat(folder / ".state").st_mode & 0o777), "0o755")
        self.assertEqual(sorted(os.listdir(folder)), sorted([*before, ".state"]), "the seed is where it was")
        self.assertEqual(layers.make_binds(self.built, self.begin("playground", "tour", state)), [], "once")

    def test_the_gateway_the_project_chose_is_the_one_that_declares_it(self):
        self.assertEqual(self.built["platform"], {"gateways": {"cloud": "gateway"}})
        self.assertEqual(layers.closure(self.built, ["echo-core"]), {"echo-core", "echo"})


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class RealTwins(Case):
    """The same item in this machine and in a catalog, as the project finds it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures.need_compose()
        holder = cls.holder = Case()
        cls.addClassCleanup(holder.doCleanups)

        def twin(case):
            text = (fixtures.CATALOGS / "fixture/addons/echo/compose.yml").read_text(encoding="utf-8")
            case.write(".lab/catalogs/local/addons/echo/compose.yml", text)
        cls.inputs, cls.built = read(holder, more=twin)

    def setUp(self):
        self.patch(layout, "waiting", return_value=False)

    def why(self, uid, explicit, running=None):
        state = on(self.built, explicit)
        return layers.plan(self.built, state, running or nothing_up(), "addon", uid, "start")["blocked"]

    def test_what_brings_one_is_not_started_while_the_other_is_on_or_up(self):
        # Echo core includes Echo of the catalog: its file and the one of Echo of this machine would both be in the on file
        self.assertEqual(layers.closure(self.built, ["echo-core"]), {"echo-core", "echo"})
        self.assertIsNone(self.why("echo-core", []))
        self.assertEqual(self.why("echo-core", ["local/echo"]), "Also in Personal: turn it off first")
        self.assertEqual(self.why("echo-core", [], nothing_up("fixture-echo", addons=["local/echo"])),
                         "Also in Personal: turn it off first")
        plan = layers.plan(self.built, on(self.built, ["local/echo"]), nothing_up(), "addon", "echo-core", "start")
        with self.assertRaisesRegex(RuntimeError, "^Also in Personal: turn it off first$"):
            layers.check(self.built, plan, [])
        # Writing is refused for it by the plan, not by the file: the half of the catalog is not in the file with its twin
        self.assertIsNone(self.why("echo-core", ["echo"]), "its own Echo is not the twin")

    def test_one_is_not_started_while_the_other_is_on_or_up(self):
        self.assertEqual(self.built["addons"]["echo"]["twin"], {"id": "local/echo", "name": "Personal"})
        self.assertIsNone(self.why("echo", []))
        self.assertIsNone(self.why("local/echo", []))
        self.assertEqual(self.why("echo", ["local/echo"]), "Also in Personal: turn it off first")
        self.assertEqual(self.why("local/echo", ["echo"]), "Also in Fixture catalog: turn it off first")
        self.assertEqual(self.why("local/echo", [], nothing_up("fixture-echo", addons=["echo"])), "Also in Fixture catalog: turn it off first")


if __name__ == "__main__":
    unittest.main()
