"""layers: closures, conflicts and every plan on a small hand-built project, a deploy to a remote place included; the
on file, the settings an add-on lacks and the job of a plan in a temporary folder. No Docker."""

import itertools
import json
import os
import sys
import types
import unittest
from unittest import mock

from lab_ui.docker import compose, envfiles
from lab_ui.library import layers, project as projects, versions
from lab_ui import paths
from lab_ui.docker.jobs import Job, JobFailed, awaited

from .. import Case, fixtures
from .test_project import read

SERVICE_LABELS = {"apim-gateway": "Gateway", "apim-management-api": "Management API", "kazoo": "Kazoo broker",
                  "kazoo-ui": "Kazoo UI", "orchard-migrate": "Orchard migration", "seed": "Seed"}
EXPORTER = "both take the gateway's exporter"


def _service(name, owner, oneshot=False, role=None, console=None):
    consoles = [{"label": console, "url": f"http://localhost/{name}", "legacy": False}] if console else []
    return {"label": SERVICE_LABELS.get(name, name), "description": "", "container": f"lab-{name}", "owner": owner, "image": "",
            "raw_image": "graviteeio/apim-gateway:1" if name == "apim-gateway" else "", "tag": "", "registry": None, "image_tags": None, "ports": [], "consoles": consoles,
            "oneshot": oneshot, "depends_on": [], "built": False, "role": role, "needs_license": False}


def _addon(uid, name, services, needs=(), conflicts=None, restarts=(), required=(), requires=None):
    settings = [{"key": key, "default": None, "required": True, "comment": "", "secret": True} for key in required]
    folder = f".lab/catalogs/local/addons/{uid[6:]}" if paths.personal(uid) else f".lab/catalogs/fixture/addons/{uid}"
    return {"id": uid, "kind": "addon", "name": name, "personal": paths.personal(uid), "category": "Other",
            "description": "", "requires": requires or {}, "conflicts": conflicts or {}, "needs": list(needs),
            "services": list(services), "restarts": list(restarts),
            "file": f"{folder}/compose.yml", "env_file": f"{folder}/.env", "settings": settings}


def _setup(uid, name, includes, members):
    return {"id": uid, "kind": "setup", "name": name, "personal": paths.personal(uid), "category": "",
            "description": "", "includes": includes, "members": members,
            "file": f".lab/catalogs/local/setups/{uid[6:]}.yml" if paths.personal(uid) else f".lab/catalogs/fixture/setups/{uid}.yml"}


def lab():
    """A small project: Kazoo Desk needs Kazoo, AI Agent Lens conflicts with Jade, Redstart with Kazoo,
    Hybrid gateway has a required setting, the welcome brings AI Agent Lens."""
    addons = [_addon("kazoo", "Kazoo", ["kazoo", "kazoo-ui"], restarts=["apim-gateway"]),
              _addon("kazoo-desk", "Kazoo Desk", ["kazoo-desk"], ["kazoo"], restarts=["apim-management-api"]),
              _addon("jade", "Jade", ["jade"], restarts=["apim-gateway"]),
              _addon("kiosk", "Kiosk", ["kiosk"]),
              _addon("agent-lens", "AI Agent Lens", ["agent-lens"], conflicts={"jade": EXPORTER},
                     restarts=["apim-gateway"]),
              _addon("redstart", "Redstart", ["redstart"], conflicts={"kazoo": "same ports"}),
              _addon("orchard", "Orchard", ["orchard", "orchard-migrate"]),
              _addon("hyssop", "Hybrid gateway", ["hyssop-gateway"], required=["HYSSOP_CLOUD_TOKEN"])]
    welcome = {"id": "welcome", "kind": "playground", "name": "Welcome", "personal": False, "category": "AI",
                "description": "", "tags": [], "source": None, "duration": None, "first_start": None,
                "requires": {"license": ["feature-1"]}, "accounts": [], "setups": [],
                "includes": ["agent-lens"], "addons": ["agent-lens"],
                "services": ["hello-app", "seed"], "apps": ["hello-app"], "seed": "seed",
                "restarts": ["apim-gateway"], "file": ".lab/catalogs/fixture/playgrounds/welcome/compose.yml", "guide": "0123456789ab",
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
            "setups": {"overlook": _setup("overlook", "Overlook", ["jade", "kiosk"],
                                          ["jade", "kiosk"]),
                       "events": _setup("events", "Events", ["kazoo-desk"], ["kazoo", "kazoo-desk"]),
                       "inspect": _setup("inspect", "Inspect", ["agent-lens"], ["agent-lens"]),
                       "local/mine": _setup("local/mine", "Mine", ["kiosk"], ["kiosk"])},
            "playgrounds": {"welcome": welcome}, "services": services, "links": links,
            "core_variables": [], "problems": []}


def second(project):
    """A second playground, Demo, that brings Kiosk: its app and its seed."""
    project["playgrounds"]["demo"] = {**project["playgrounds"]["welcome"], "id": "demo", "name": "Demo",
                                      "addons": ["kiosk"], "restarts": [], "services": ["demo-app", "demo-seed"],
                                      "apps": ["demo-app"], "seed": "demo-seed",
                                      "file": ".lab/catalogs/fixture/playgrounds/demo/compose.yml"}
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
                                               "file": ".lab/catalogs/local/playgrounds/sandbox/compose.yml"}


def on(project, explicit=(), playground=None, missing=(), locked=None, setups=()):
    """What layers.current() answers for this on file. `setups`: the ones it lists, `explicit` holding their
    includes. `missing`: the add-ons that lack settings, or {each: the names of those settings}."""
    brought = project["playgrounds"][playground]["addons"] if playground else []
    return {"playground": playground, "explicit": sorted(explicit), "setups": sorted(setups),
            "effective": sorted(layers.closure(project, [*explicit, *brought])),
            "missing": list(missing), "unset": missing if isinstance(missing, dict) else {}, "problems": [], "locked": locked}


def cloud(project):
    """The Hybrid gateway as a platform of Cloud runs it, with a Redis of its own, and Hotels, a playground that
    deploys to it: an app, and a seed that depends on the core as a seed does for This machine. The plan of a place
    is given the id of the gateway's add-on, and does not read its `gateway` key."""
    hyssop = project["addons"]["hyssop"]
    hyssop.update(services=["hyssop-redis", "apim-hyssop-gateway"],
                  gateway={"platform": "cloud", "service": "apim-hyssop-gateway", "token": "HYSSOP_CLOUD_TOKEN",
                           "domains": ["localhost:8090", "apim-hyssop-gateway:8082"]})
    owner = {"kind": "addon", "id": "hyssop", "name": "Hybrid gateway"}
    del project["services"]["hyssop-gateway"]
    project["services"].update({name: _service(name, owner) for name in hyssop["services"]})
    project["services"]["apim-hyssop-gateway"]["depends_on"] = ["hyssop-redis"]
    project["playgrounds"]["hotels"] = {**project["playgrounds"]["welcome"], "id": "hotels", "name": "Hotels",
                                        "deploys_to": ["cloud"], "requires": {}, "includes": [], "addons": [],
                                        "restarts": [], "services": ["hotels-api", "hotels-seed"],
                                        "apps": ["hotels-api"], "seed": "hotels-seed",
                                        "file": ".lab/catalogs/fixture/playgrounds/hotels/compose.yml"}
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
            "ready": running, "created": running | done | made}   # (one set: what a test makes run is ready)


def row(kind, uid, name):
    return {"kind": kind, "id": uid, "name": name}


KAZOO, CONSOLE = row("addon", "kazoo", "Kazoo"), row("addon", "kazoo-desk", "Kazoo Desk")
JADE, KIOSK = row("addon", "jade", "Jade"), row("addon", "kiosk", "Kiosk")
INSPECTOR, REDSTART = row("addon", "agent-lens", "AI Agent Lens"), row("addon", "redstart", "Redstart")
PLATFORM, APPS = row("platform", "platform", "Gravitee platform"), row("playground", "welcome", "Welcome apps")
DATA = row("data", "welcome", "What Welcome created in Gravitee")
DATABASES, MANAGEMENT = row("group", "databases", "Databases"), row("group", "api-management", "API Management")
GATEWAY, API = row("service", "apim-gateway", "Gateway"), row("service", "apim-management-api", "Management API")


class Sets(Case):

    def setUp(self):
        self.project = lab()

    def test_closure_adds_what_is_needed(self):
        self.assertEqual(layers.closure(self.project, ["kazoo-desk", "jade"]),
                         {"kazoo-desk", "kazoo", "jade"})

    def test_closure_leaves_out_what_the_project_does_not_have(self):
        self.assertEqual(layers.closure(self.project, ["kazoo", "gone"]), {"kazoo"})

    def test_conflicts_are_symmetric(self):
        clash = [{"a": "agent-lens", "b": "jade", "reason": EXPORTER}]
        self.assertEqual(layers.conflicts(self.project, ["jade", "agent-lens", "kiosk"]), clash)
        self.assertEqual(layers.conflicts(self.project, ["agent-lens", "jade"]), clash)
        self.assertEqual(layers.conflicts(self.project, ["kazoo", "redstart"]),   # said by the second of the two
                         [{"a": "kazoo", "b": "redstart", "reason": "same ports"}])
        self.assertEqual(layers.conflicts(self.project, ["jade", "kazoo"]), [])


class ServiceOf(Case):

    SERVICES = {
        "almond-portal-beet": {"raw_image": "${PORTAL_REGISTRY:-${APIM_REGISTRY:-graviteeio}}/apim-portal-ui:${PORTAL_VERSION:-${APIM_VERSION:-4}}",
                                "tag": "4", "registry": "graviteeio", "owner": {"name": "Almond Bio"}},
        "apim-gateway": {"raw_image": "${APIM_GATEWAY_VERSION:-${APIM_VERSION:-4}-debian}".join(["graviteeio/apim-gateway:", ""]),
                         "tag": "4-debian", "registry": None, "owner": {"name": "API Management"}},
        "apim-management-api": {"raw_image": "graviteeio/apim-management-api:${APIM_VERSION:-4}", "tag": "4", "registry": None,
                                "owner": {"name": "API Management"}},
    }

    def test_a_variable_belongs_to_the_service_that_sets_it_not_to_one_that_only_follows_it(self):
        # The portals of a playground follow API Management's version: sorting before it, they must not take its name
        project = {"services": self.SERVICES}
        self.assertEqual(layers.service_of(project, "APIM_VERSION")[0], "apim-management-api")
        self.assertEqual(layers.saved_setting(project, "APIM_VERSION"), ("4", "API Management"))
        self.assertEqual(layers.service_of(project, "PORTAL_VERSION")[0], "almond-portal-beet")
        self.assertEqual(layers.service_of(project, "APIM_GATEWAY_VERSION")[0], "apim-gateway")

    def test_a_variable_that_only_defaults_another_one_still_finds_a_service(self):
        services = {"kiosk": {"raw_image": "kiosk:${KIOSK_VERSION:-${ELASTICSEARCH_VERSION:-9}}", "tag": "9", "registry": None,
                               "owner": {"name": "Kiosk"}}}
        self.assertEqual(layers.service_of({"services": services}, "ELASTICSEARCH_VERSION")[0], "kiosk")
        self.assertIsNone(layers.service_of({"services": services}, "NO_SUCH_VERSION"))

    def test_an_image_line_is_read_once_whatever_is_asked_of_it(self):
        # The state asks of every playground's versions on each poll: each line of the project is read once, not once a question
        from lab_ui.docker import images
        layers._sets.cache_clear()
        read = mock.Mock(side_effect=images._parts)
        self.patch(images, "_parts", read)
        project = {"services": self.SERVICES}
        for _ in range(20):
            for variable in ("APIM_VERSION", "PORTAL_VERSION", "APIM_GATEWAY_VERSION", "PORTAL_REGISTRY", "NO_SUCH_VERSION"):
                layers.service_of(project, variable)
        self.assertEqual(read.call_count, len(self.SERVICES))
        self.assertEqual(layers.service_of(project, "APIM_VERSION")[0], "apim-management-api")


class License(Case):
    """What needs a license that is not usable (`needs_license`) is not started: not named, not waited for, not an
    item to start. With a usable license it is a service as any other."""

    def setUp(self):
        self.project = lab()
        licensed(self.project, False)
        self.state = on(self.project, ["kazoo"])

    def test_it_is_not_among_what_up_names(self):
        self.assertNotIn("alert-engine", layers.up_services(self.project, None, ["kazoo"]))
        self.assertTrue(layers.held_back(self.project, "alert-engine"))
        self.assertFalse(layers.held_back(self.project, "kazoo"))
        self.assertFalse(layers.held_back(self.project, "unknown"))   # the user's own on file may add one
        self.project["license_usable"] = True
        self.assertIn("alert-engine", layers.up_services(self.project, None, ["kazoo"]))
        self.assertFalse(layers.held_back(self.project, "alert-engine"))

    def test_an_apply_starts_it_once_the_license_is_usable(self):
        # Alert Engine was held back, so it has no container; a license is saved since, and what runs reads it: the
        # apply makes the gateway again and starts Alert Engine, which it would not for another service with none
        states = {"apim-gateway": {"key": "running", "running": True}, "kazoo": {"key": "running", "running": True},
                  "alert-engine": {"key": "absent", "running": False}}
        self.project["license_usable"] = True
        on_ = layers.up_services(self.project, None, ["kazoo"])
        found = layers.applies(self.project, on_, states, {"apim-gateway"})
        self.assertEqual(found["names"], ["alert-engine", "apim-gateway"])
        self.assertEqual([row_["name"] for row_ in found["starts"]], ["Alert Engine"])
        # Held back still: not named
        self.project["license_usable"] = False
        on_ = layers.up_services(self.project, None, ["kazoo"])
        self.assertEqual(layers.applies(self.project, on_, states, {"apim-gateway"})["names"], ["apim-gateway"])
        # Another service with no container is not created
        self.project["license_usable"] = True
        states["kazoo-ui"] = {"key": "absent", "running": False}
        on_ = layers.up_services(self.project, None, ["kazoo", "kazoo-ui"])
        self.assertNotIn("kazoo-ui", layers.applies(self.project, on_, states, {"apim-gateway"})["names"])

    def test_the_stack_s_start_does_not_count_it(self):
        # Everything else runs: nothing is left to start, the platform is not asked to
        running = up("kazoo", "agent-lens", playgrounds=["welcome"])
        state = on(self.project, ["kazoo"], "welcome")
        self.assertEqual(layers.platform_starts(self.project, state, running), [])
        self.assertEqual(layers.platform_starts(self.project, state, up(platform=False))[0], PLATFORM)
        # With a license it is a group to start, as any other
        self.project["license_usable"] = True
        alert = row("group", "alert-engine", "Alert Engine")
        self.assertEqual(layers.platform_starts(self.project, state, running), [alert])

    def test_a_start_runs_and_waits_for_the_others_only(self):
        pairs = {"start": [["platform", "platform"], ["addon", "kazoo"]]}
        everything = {"apim-gateway", "apim-management-api", "mongodb", "kazoo", "kazoo-ui"}
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

    def plan(self, state, running, kind, uid, do, fresh=False):
        return layers.plan(self.project, state, running, kind, uid, do, fresh=fresh)

    def check(self, plan, **expected):
        rows = {"starts": [], "stops": [], "turns_off": [], "keeps": [], "restarts": [], "blocked": None, **expected}
        self.assertEqual({key: plan[key] for key in rows}, rows)

    # --- A start runs the item, what it brings and needs, and restarts the core services it changes ----------------

    def test_start_with_a_dependency(self):
        plan = self.plan(on(self.project), up(), "addon", "kazoo-desk", "start")
        self.check(plan, starts=[KAZOO, CONSOLE], restarts=[GATEWAY, API], playground=None, explicit=["kazoo-desk"])
        self.assertEqual((plan["do"], plan["kind"], plan["id"], plan["label"]),
                         ("start", "addon", "kazoo-desk", "Start"))
        # By name: what starts and what restarts, nothing else
        self.assertEqual(plan["services"], {"remove": [], "start": ["apim-gateway", "apim-management-api", "kazoo",
                                                                    "kazoo-desk", "kazoo-ui"]})

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

    def test_what_a_plan_stops_keeps_and_leaves_alone(self):
        second(self.project)
        for why, state, running, kind, uid, do, expected in (
                ("a start stops what needs the add-on in conflict", on(self.project, ["kazoo-desk"]), up("kazoo", "kazoo-desk"),
                 "addon", "redstart", "start",
                 {"starts": [REDSTART], "stops": [KAZOO, CONSOLE], "restarts": [GATEWAY, API], "explicit": ["redstart"]}),
                ("a start leaves the running playground alone", on(self.project, playground="welcome"),
                 up("agent-lens", playgrounds=["welcome"]), "addon", "kazoo", "start",
                 {"starts": [KAZOO], "restarts": [GATEWAY], "playground": "welcome", "explicit": ["kazoo"]}),
                ("an add-on's start leaves the apps of the playground as they are", on(self.project, playground="welcome"),
                 up("agent-lens", playgrounds=["demo"]), "addon", "kazoo", "start",
                 {"starts": [KAZOO], "restarts": [GATEWAY], "playground": "welcome", "explicit": ["kazoo"]}),
                ("a stop takes what needs it", on(self.project, ["kazoo-desk", "kiosk"]), up("kazoo", "kazoo-desk", "kiosk"),
                 "addon", "kazoo", "stop",
                 {"stops": [KAZOO, CONSOLE], "restarts": [GATEWAY, API], "explicit": ["kiosk"]}),
                ("a stop takes the playground that needs it", on(self.project, ["kiosk"], "welcome"),
                 up("agent-lens", "kiosk", playgrounds=["welcome"]), "addon", "agent-lens", "stop",
                 {"stops": [APPS, INSPECTOR], "keeps": [DATA], "restarts": [GATEWAY], "explicit": ["kiosk"]}),
                ("a stop starts nothing: Kiosk is on and does not run", on(self.project, ["jade", "kiosk"]), up("jade"),
                 "addon", "jade", "stop", {"stops": [JADE], "restarts": [GATEWAY], "explicit": ["kiosk"]}),
                ("a playground's stop keeps the add-on turned on by itself", on(self.project, ["agent-lens"], "welcome"),
                 up("agent-lens", playgrounds=["welcome"]), "playground", "welcome", "stop",
                 {"stops": [APPS], "keeps": [DATA], "restarts": [GATEWAY], "explicit": ["agent-lens"]})):
            with self.subTest(why):
                self.check(self.plan(state, running, kind, uid, do), **{"playground": None, **expected})

    def test_start_stops_the_playground_in_conflict(self):
        state = on(self.project, ["kiosk"], "welcome")
        plan = self.plan(state, up("agent-lens", "kiosk", playgrounds=["welcome"]), "addon", "jade", "start")
        self.check(plan, starts=[JADE], stops=[APPS, INSPECTOR], keeps=[DATA], restarts=[GATEWAY],
                   playground=None, explicit=["jade", "kiosk"])
        # Its apps and its seed go with the add-on only it brought
        self.assertEqual(plan["services"]["remove"], ["agent-lens", "hello-app", "seed"])

    def test_start_setup_turns_its_includes_on(self):
        plan = self.plan(on(self.project), up(), "setup", "events", "start")
        self.check(plan, starts=[KAZOO, CONSOLE], restarts=[GATEWAY, API], playground=None, explicit=["kazoo-desk"])
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "setup", "overlook", "start")
        self.check(plan, starts=[JADE, KIOSK], stops=[APPS, INSPECTOR], keeps=[DATA], restarts=[GATEWAY],
                   playground=None, explicit=["jade", "kiosk"])

    def test_start_again_restarts_what_a_start_that_failed_left_behind(self):
        # Kazoo's start turned it on, then failed before the gateway was made again: on, Kazoo no longer comes, yet
        # its start restarts the gateway, which runs behind what is saved; nothing else of the platform
        state, running = on(self.project, ["kazoo"]), up()
        plan = layers.plan(self.project, state, running, "addon", "kazoo", "start", behind={"apim-gateway", "mongodb"})
        self.check(plan, starts=[KAZOO], restarts=[GATEWAY], playground=None, explicit=["kazoo"])
        self.check(self.plan(state, running, "addon", "kazoo", "start"), starts=[KAZOO], playground=None,
                   explicit=["kazoo"])

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

    def test_a_start_goes_to_the_versions_it_was_tested_with_when_the_saved_ones_are_too_old(self):
        """Its start is not blocked: it changes the versions (`versions`, rows the page must accept), and makes again what runs on them."""
        welcome = self.project["playgrounds"]["welcome"]
        gateway, access = self.project["services"]["apim-gateway"], self.project["services"]["mongodb"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.12.20")
        access.update(raw_image="graviteeio/am-gateway:${AM_VERSION:-4.12.7}", tag="4.12.7",
                      owner={"kind": "platform", "id": "access-management", "name": "Access Management"})
        welcome["requires"] = {"images": {"APIM_VERSION": "4.13.0-alpha.3", "AM_VERSION": "4.13"}}
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.1"}, {"APIM_VERSION": "4.13.0-alpha.5", "AM_VERSION": "4.13.0"}]
        self.assertEqual(layers.switch(self.project, welcome), {"APIM_VERSION": "4.13.0-alpha.5", "AM_VERSION": "4.13.0"})
        plan = self.plan(on(self.project), up(), "playground", "welcome", "start")
        self.assertIsNone(plan["blocked"])
        self.assertEqual([row["name"] for row in plan["versions"]],
                         ["Access Management 4.12.7 → 4.13.0", f"{gateway['owner']['name']} 4.12.20 → 4.13.0-alpha.5"])
        self.assertIn("mongodb", [row["id"] for row in plan["restarts"]], "what runs on a version that changes is made again")
        self.assertIn("versions", layers.dialog(plan))
        self.assertTrue(layers.asks(plan))
        with self.assertRaisesRegex(RuntimeError, "Would also change Access Management"):
            layers.check(self.project, plan, [])
        layers.check(self.project, plan, [f"version:{row['id']}" for row in plan["versions"]])
        # The latest set it was tested with does not meet what it asks, or there is none: it waits for the Images page, as it did
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.5"}]
        self.assertIsNone(layers.switch(self.project, welcome))
        self.assertIn("choose them on the Images page", self.plan(on(self.project), up(), "playground", "welcome", "start")["blocked"])
        welcome["tested"] = []
        self.assertIsNone(layers.switch(self.project, welcome))
        # Nothing too old: nothing to switch
        gateway["tag"], access["tag"] = "4.13.0-alpha.5", "4.13.0"
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.5", "AM_VERSION": "4.13.0"}]
        self.assertIsNone(layers.switch(self.project, welcome))
        self.assertNotIn("versions", self.plan(on(self.project), up(), "playground", "welcome", "start"))

    def test_a_switch_takes_the_registry_of_the_versions_it_goes_to(self):
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="${APIM_REGISTRY:-graviteeio}/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.12.20", registry="graviteeio")
        welcome["requires"] = {"images": {"APIM_VERSION": "amp-demo-latest"}}
        welcome["tested"] = [{"APIM_VERSION": "amp-demo-latest", "APIM_REGISTRY": "registry.example.test"}]
        self.assertEqual(layers.switch(self.project, welcome), {"APIM_VERSION": "amp-demo-latest", "APIM_REGISTRY": "registry.example.test"})
        rows = self.plan(on(self.project), up(), "playground", "welcome", "start")["versions"]
        self.assertEqual([(row["id"], row["name"]) for row in rows],
                         [("APIM", f"{gateway['owner']['name']} 4.12.20 → amp-demo-latest from registry.example.test")], "one row for the image")

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

    def test_a_build_without_numbers_is_taken_as_the_newest(self):
        """Going to a demo build is going forward; leaving one for a release goes back: what the build wrote may not open on it."""
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.13.0-alpha.5")
        welcome["tested"] = [{"APIM_VERSION": "amp-demo-latest"}]
        found = layers.untested(self.project, welcome)
        self.assertEqual((found["images"][0]["tested"], found["images"][0]["goes_back"]), ("amp-demo-latest", False))
        gateway["tag"] = "amp-demo-latest"
        self.assertIsNone(layers.untested(self.project, welcome), "the build it was tested on")
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.5"}]
        self.assertTrue(layers.untested(self.project, welcome)["images"][0]["goes_back"], "from the build to a release")
        for have, to, back in (("4.13.0", "4.12.20", True), ("4.12.20", "4.13.0", False), ("master-latest", "latest", False),
                               ("4.13.0-debian", "4.13.0", False)):
            self.assertEqual(layers._goes_back(have, to), back, (have, to))

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

    def test_the_system_an_image_is_built_on_is_not_part_of_its_version(self):
        # Every system the Images page offers: 4.13.1-chainguard is 4.13.1, a release, not a preview of it
        for tag in ("4.13.1-debian", "4.13.1-chainguard", "4.13.1-chainguard-fips", "4.13.1-noble", "4.13.1-jammy",
                    "4.13.1-alpine"):
            self.assertTrue(versions.at_least(tag, "4.13.1"), tag)
            self.assertTrue(versions.at_least(tag, "4.13"), tag)
            self.assertTrue(layers.conform(tag), tag)
            self.assertTrue(layers._tested(tag, "4.13.1"), tag)
            self.assertFalse(layers._goes_back(tag, "4.13.1"), tag)
        self.assertFalse(versions.at_least("4.13.0-chainguard", "4.13.1"))
        self.assertTrue(layers._goes_back("4.13.2-noble", "4.13.1"))
        # Saved on one of them, the exact release it asks: nothing is too old, nothing to switch to, nothing untested
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.13.1}", tag="4.13.1-chainguard")
        welcome["requires"] = {"images": {"APIM_VERSION": "4.13.1"}}
        welcome["tested"] = [{"APIM_VERSION": "4.13.1"}]
        self.assertEqual((layers.too_old(self.project, welcome), layers.switch(self.project, welcome),
                          layers.untested(self.project, welcome)), (None, None, None))
        self.assertIsNone(self.plan(on(self.project), up(), "playground", "welcome", "start")["blocked"])

    def test_three_images_that_are_too_old_are_listed_as_a_sentence(self):
        saved = {"A_VERSION": ("1", "Alpha"), "B_VERSION": ("1", "Beta"), "C_VERSION": ("1", "Gamma")}
        self.patch(layers, "saved_setting", lambda project, variable: saved[variable])
        unit = {"requires": {"images": {"A_VERSION": "2", "B_VERSION": "2", "C_VERSION": "2"}}}
        self.assertEqual(layers.too_old(self.project, unit), "Needs Alpha 2 or later, Beta 2 or later and Gamma 2 "
                                                             "or later: choose them on the Images page")

    def test_start_with_missing_settings_is_blocked(self):
        plan = self.plan(on(self.project, missing=["hyssop"]), up(), "addon", "hyssop", "start")
        self.assertEqual(plan["blocked"], "Hybrid gateway needs settings")
        plan = self.plan(on(self.project, missing=["kazoo"]), up(), "setup", "events", "start")
        self.assertEqual(plan["blocked"], "Kazoo needs settings")
        plan = self.plan(on(self.project, missing=["hyssop"]), up(), "addon", "kazoo", "start")
        self.assertIsNone(plan["blocked"])

    def test_retry_leaves_the_on_file(self):
        # On, it starts what of it does not run and what it needs: not the rest of what is on
        state = on(self.project, ["kazoo-desk"])
        plan = self.plan(state, up("kazoo"), "addon", "kazoo-desk", "start")
        self.check(plan, starts=[CONSOLE], playground=None, explicit=["kazoo-desk"])
        plan = self.plan(state, up(), "addon", "kazoo", "start")   # on through Kazoo Desk: not made explicit
        self.check(plan, starts=[KAZOO], playground=None, explicit=["kazoo-desk"])

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
        # Kazoo Desk depends on Kazoo and the management API, which depends on MongoDB. API Management stopped from
        # the Stack: the management API starts first, and says so; MongoDB runs, the gateway stays stopped
        services = self.project["services"]
        services["kazoo-desk"]["depends_on"] = ["apim-management-api", "kazoo"]
        services["apim-management-api"]["depends_on"] = ["mongodb"]
        plan = self.plan(on(self.project), up(stopped=["api-management"]), "addon", "kazoo-desk", "start")
        self.check(plan, starts=[API, KAZOO, CONSOLE], playground=None, explicit=["kazoo-desk"])
        self.assertEqual(plan["services"]["start"], ["apim-management-api", "kazoo", "kazoo-desk", "kazoo-ui"])
        # Nothing runs: MongoDB too, its whole group; then, all of the platform starting, the platform
        plan = self.plan(on(self.project), up(platform=False), "addon", "kazoo-desk", "start")
        self.check(plan, starts=[API, DATABASES, KAZOO, CONSOLE], playground=None, explicit=["kazoo-desk"])
        services["kazoo-desk"]["depends_on"] = ["apim-gateway", "kazoo"]
        services["apim-gateway"]["depends_on"] = ["apim-management-api"]
        plan = self.plan(on(self.project), up(platform=False), "addon", "kazoo-desk", "start")
        self.check(plan, starts=[PLATFORM, KAZOO, CONSOLE], playground=None, explicit=["kazoo-desk"])
        # What an add-on that is off makes a core service depend on does not count: it is not part of what runs
        services["apim-gateway"]["depends_on"] = ["apim-management-api", "jade"]
        plan = self.plan(on(self.project), up(platform=False), "addon", "kazoo-desk", "start")
        self.assertNotIn("jade", plan["services"]["start"])

    def test_a_start_waits_for_what_it_depends_on_that_runs_and_is_not_ready(self):
        # Kazoo Desk depends on the management API, which runs and whose health check has not passed yet: `up
        # --no-deps` names it, so that Compose waits for it before Kazoo Desk, as its depends_on asks. It is no row: it
        # runs. One that runs behind what is saved is not named (it would be made again), nor one that is ready
        services = self.project["services"]
        services["kazoo-desk"]["depends_on"] = ["apim-management-api", "kazoo"]
        services["apim-management-api"]["depends_on"] = ["mongodb"]
        self.project["addons"]["kazoo-desk"]["restarts"] = []   # (it changes nothing of the management API here)
        state, running = on(self.project, ["kazoo"]), up("kazoo")
        running["ready"] = running["ready"] - {"apim-management-api"}
        plan = self.plan(state, running, "addon", "kazoo-desk", "start")
        self.check(plan, starts=[CONSOLE], playground=None, explicit=["kazoo", "kazoo-desk"])
        self.assertEqual(plan["services"]["start"], ["apim-management-api", "kazoo-desk"])
        plan = layers.plan(self.project, state, running, "addon", "kazoo-desk", "start", behind={"apim-management-api"})
        self.assertEqual(plan["services"]["start"], ["kazoo-desk"])
        plan = self.plan(state, up("kazoo"), "addon", "kazoo-desk", "start")
        self.assertEqual(plan["services"]["start"], ["kazoo-desk"])

    def test_the_one_shots_what_starts_depends_on_run_again_first(self):
        # Orchard's migration ran, then Orchard and its database stopped: its start runs the migration again before
        # it, and the database before the migration, as Compose would without --no-deps; a migration has nothing more
        # to do the second time. Whether it ran or not, alike
        services, orchard = self.project["services"], self.project["addons"]["orchard"]
        services["orchard-db"] = _service("orchard-db", services["orchard"]["owner"])
        orchard["services"].append("orchard-db")
        services["orchard"]["depends_on"] = ["orchard-migrate"]
        services["orchard-migrate"]["depends_on"] = ["orchard-db"]
        state = on(self.project, ["orchard"])
        running = up("orchard")
        running["services"] -= {"orchard", "orchard-db"}
        for found in (running, up()):
            plan = self.plan(state, found, "addon", "orchard", "start")
            self.assertEqual(plan["services"]["start"], ["orchard", "orchard-db", "orchard-migrate"])
        # What a start restarts runs its one-shots first too: Kazoo restarts the gateway, which depends on one here
        services["apim-gateway"]["depends_on"] = ["orchard"]
        running = up("orchard")
        running["services"].add("orchard-db")
        plan = self.plan(on(self.project, ["orchard"]), running, "addon", "kazoo", "start")
        self.assertEqual(plan["services"]["start"], ["apim-gateway", "kazoo", "kazoo-ui", "orchard-migrate"])

    # --- A stop removes what goes by name and restarts the core services it changed --------------------------------

    def test_stop_add_on(self):
        state = on(self.project, ["jade", "kiosk"])
        plan = self.plan(state, up("jade", "kiosk"), "addon", "jade", "stop")
        self.check(plan, stops=[JADE], restarts=[GATEWAY], playground=None, explicit=["kiosk"])
        # Its container removed, then the gateway made again without its settings
        self.assertEqual((plan["label"], plan["services"]), ("Stop", {"remove": ["jade"], "start": ["apim-gateway"]}))

    def test_turn_off_what_is_on_and_does_not_run(self):
        # The platform stopped from the Stack, Kazoo Desk on: its stop turns it off with Kazoo, removing their
        # stopped containers; nothing restarts, nothing starts
        state = on(self.project, ["kazoo-desk"])
        running = up(platform=False, stopped=["api-management", "databases"], created=["kazoo", "kazoo-desk"])
        plan = self.plan(state, running, "addon", "kazoo-desk", "stop")
        self.check(plan, turns_off=[KAZOO, CONSOLE], playground=None, explicit=[])
        self.assertEqual((plan["label"], plan["services"]),
                         ("Turn off", {"remove": ["kazoo", "kazoo-desk", "kazoo-ui"], "start": []}))

    def test_a_stop_takes_the_item_that_runs_without_being_on(self):
        # A stop of Kazoo failed once the on file was written: off, it still runs. Its stop again removes it
        plan = self.plan(on(self.project), up("kazoo"), "addon", "kazoo", "stop")
        self.check(plan, stops=[KAZOO], playground=None, explicit=[])
        self.assertEqual((plan["label"], plan["services"]), ("Stop", {"remove": ["kazoo", "kazoo-ui"], "start": []}))

    def test_stop_setup_stops_its_members(self):
        state = on(self.project, ["jade", "kazoo", "kazoo-desk", "kiosk"])   # Kazoo is on by itself too
        running = up("jade", "kazoo", "kazoo-desk", "kiosk")
        self.check(self.plan(state, running, "setup", "overlook", "stop"), stops=[JADE, KIOSK],
                   restarts=[GATEWAY], playground=None, explicit=["kazoo", "kazoo-desk"])
        self.check(self.plan(state, running, "setup", "events", "stop"), stops=[KAZOO, CONSOLE],
                   restarts=[GATEWAY, API], playground=None, explicit=["jade", "kiosk"])

    def test_a_setup_s_stop_keeps_what_something_else_that_is_on_uses(self):
        # Mine (Kazoo, Kiosk) and Events (Kazoo Desk, Kazoo) were both started: Events' stop keeps Kazoo
        self.project["setups"]["local/mine"].update(includes=["kazoo", "kiosk"], members=["kazoo", "kiosk"])
        state = on(self.project, ["kazoo", "kazoo-desk", "kiosk"])
        plan = self.plan(state, up("kazoo", "kazoo-desk", "kiosk"), "setup", "events", "stop")
        self.check(plan, stops=[CONSOLE], keeps=[KAZOO], restarts=[API], playground=None, explicit=["kazoo", "kiosk"])
        # All of it used by what else is on: nothing to stop, and it says what uses it
        state = on(self.project, playground="welcome")
        plan = self.plan(state, up("agent-lens", playgrounds=["welcome"]), "setup", "inspect", "stop")
        self.check(plan, keeps=[INSPECTOR], blocked="Used by Welcome", playground="welcome", explicit=[])
        self.project["setups"]["just-kazoo"] = {**self.project["setups"]["inspect"], "id": "just-kazoo",
                                                "name": "Just Kazoo", "includes": ["kazoo"], "members": ["kazoo"]}
        plan = self.plan(on(self.project, ["kazoo-desk"]), up("kazoo", "kazoo-desk"), "setup", "just-kazoo",
                         "stop")   # Kazoo Desk needs it, and Events, which was started, has it
        self.check(plan, keeps=[KAZOO], blocked="Used by Events, Kazoo Desk", playground=None,
                   explicit=["kazoo-desk"])
        # Another setup whose add-ons only this one turned on does not keep them
        state = on(self.project, ["jade", "kiosk"])
        self.project["setups"]["local/mine"].update(includes=["kiosk"], members=["kiosk"])
        plan = self.plan(state, up("jade", "kiosk"), "setup", "overlook", "stop")
        self.check(plan, stops=[JADE, KIOSK], restarts=[GATEWAY], playground=None, explicit=[])

    def test_a_setup_s_stop_says_what_was_turned_on_by_itself(self):
        # Jade turned on by itself, Kiosk off: Overlook is not on, Jade is not the setup
        plan = self.plan(on(self.project, ["jade"]), up("jade"), "setup", "overlook", "stop")
        self.check(plan, stops=[JADE], restarts=[GATEWAY], playground=None, explicit=[])
        self.assertEqual((plan["itself"], plan["label"]), ({"setup:overlook"}, "Stop"))
        with self.assertRaisesRegex(RuntimeError, "Would also stop Jade"):
            layers.check(self.project, plan, [])
        # On, an add-on of it turned on by itself is not the setup either: Kazoo, Events having turned on Kazoo Desk
        state = on(self.project, ["kazoo", "kazoo-desk"])
        plan = self.plan(state, up("kazoo", "kazoo-desk"), "setup", "events", "stop")
        self.assertEqual(plan["itself"], {"setup:events", "addon:kazoo-desk"})

    def test_labels(self):
        # Stop while it runs, Turn off while it is on and does not, Turn on for what runs without being on
        cases = [(["kazoo"], up("kazoo"), "addon", "kazoo", "stop", "Stop"),
                 (["kazoo"], up(), "addon", "kazoo", "stop", "Turn off"),
                 ([], up("kiosk"), "addon", "kiosk", "start", "Turn on"),
                 ([], up(), "addon", "kiosk", "start", "Start"),
                 (["jade"], up("jade", "kiosk"), "setup", "overlook", "start", "Turn on"),
                 (["jade"], up("jade"), "setup", "overlook", "start", "Start"),
                 # One of its add-ons runs without being on, the other is off: as an add-on that runs
                 ([], up("kiosk"), "setup", "overlook", "start", "Turn on"),
                 (["jade"], up(), "setup", "overlook", "stop", "Turn off")]
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
        state = on(self.project, ["kazoo-desk"], "welcome")
        self.assertEqual(layers.platform_starts(self.project, state, up(platform=False)),
                         [PLATFORM, APPS, INSPECTOR, KAZOO, CONSOLE])
        # Databases stopped from the Stack, and Kazoo UI: the group, and Kazoo. A one-shot is left out: it runs once
        state = on(self.project, ["kazoo-desk", "orchard"], "welcome")
        running = up("agent-lens", "kazoo", "kazoo-desk", "orchard", playgrounds=["welcome"],
                     stopped=["databases"])
        running["services"].discard("kazoo-ui")
        running["done"].discard("orchard-migrate")
        self.assertEqual(layers.platform_starts(self.project, state, running), [DATABASES, KAZOO])
        running = up("agent-lens", "kazoo", "kazoo-desk", "orchard", playgrounds=["welcome"])
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
        state = on(self.project, ["kazoo-desk"])
        plan = self.plan(state, up("kazoo", "kazoo-desk"), "addon", "kazoo", "stop")
        self.assertEqual(plan["itself"], {"addon:kazoo"})
        # What it accepts is named with its kind: an id alone may be an add-on's and a playground's
        for accept in ([], ["kazoo-desk"], ["playground:kazoo-desk"]):
            with self.assertRaisesRegex(RuntimeError, "Would also stop Kazoo Desk"):
                layers.check(self.project, plan, accept)
        layers.check(self.project, plan, ["addon:kazoo-desk"])
        # What it turns off as well
        plan = self.plan(state, up(), "addon", "kazoo", "stop")
        with self.assertRaisesRegex(RuntimeError, "Would also turn off Kazoo Desk"):
            layers.check(self.project, plan, [])
        layers.check(self.project, plan, ["addon:kazoo-desk"])
        blocked = self.plan(on(self.project, missing=["hyssop"]), up(), "addon", "hyssop", "start")
        with self.assertRaisesRegex(RuntimeError, "Hybrid gateway needs settings"):
            layers.check(self.project, blocked, ["addon:hyssop"])

    def test_check_takes_a_setups_members_as_the_setup_itself(self):
        state = on(self.project, ["kazoo-desk"], "welcome")
        plan = self.plan(state, up("kazoo", "kazoo-desk", "agent-lens"), "setup", "events", "stop")
        self.assertEqual(plan["itself"], {"setup:events", "addon:kazoo", "addon:kazoo-desk"})
        layers.check(self.project, plan, [])
        # A setup's stop keeps what the playground uses: the playground does not go with it
        plan = self.plan(state, up("kazoo", "kazoo-desk", "agent-lens"), "setup", "inspect", "stop")
        with self.assertRaisesRegex(RuntimeError, "Used by Welcome"):
            layers.check(self.project, plan, ["playground:welcome"])

    def changes(self, state, running, kind, uid, do):
        return layers.changes(self.project, self.plan(state, running, kind, uid, do))

    def test_changes_are_what_starts_restarts_and_stops(self):
        state = on(self.project, ["jade", "kiosk"])
        self.assertEqual(self.changes(state, up("jade", "kiosk"), "addon", "agent-lens", "start"),
                         {"start": [["addon", "agent-lens"], ["service", "apim-gateway"]],
                          "stop": [["addon", "jade"]]})
        self.assertEqual(self.changes(state, up("jade", "kiosk"), "setup", "overlook", "stop"),
                         {"start": [["service", "apim-gateway"]], "stop": [["addon", "jade"], ["addon", "kiosk"]]})
        # What is turned off is removed: it shows as stopping
        self.assertEqual(self.changes(state, up("jade"), "setup", "overlook", "stop"),
                         {"start": [["service", "apim-gateway"]], "stop": [["addon", "jade"], ["addon", "kiosk"]]})

    def test_changes_of_a_start_again_name_the_item(self):
        # On and partly up, nothing of the item is among what starts: its row must still say Starting
        state = on(self.project, ["kazoo"], "welcome")
        running = up("agent-lens", "kazoo", playgrounds=["welcome"])
        self.assertEqual(self.changes(state, running, "addon", "kazoo", "start"),
                         {"start": [["addon", "kazoo"]], "stop": []})
        self.assertEqual(self.changes(state, running, "playground", "welcome", "start"),
                         {"start": [["playground", "welcome"]], "stop": []})
        state = on(self.project, ["jade", "kiosk"])
        self.assertEqual(self.changes(state, up("jade", "kiosk"), "setup", "overlook", "start"),
                         {"start": [["addon", "jade"], ["addon", "kiosk"]], "stop": []})
        # A member that starts already says so for the setup: the one that runs is left as it is
        self.assertEqual(self.changes(on(self.project, ["jade"]), up("jade"), "setup", "overlook", "start"),
                         {"start": [["addon", "kiosk"]], "stop": []})

    def test_the_services_a_job_s_changes_cover(self):
        # The platform, a group, an add-on, a playground, a service; one stopped and started again restarts
        pairs = {"start": [["group", "databases"], ["addon", "kazoo"], ["service", "apim-gateway"]],
                 "stop": [["playground", "welcome"], ["service", "apim-gateway"]]}
        self.assertEqual(layers.changing(self.project, pairs),
                         {"mongodb": "start", "kazoo": "start", "kazoo-ui": "start", "apim-gateway": "start",
                          "hello-app": "stop", "seed": "stop"})
        self.assertEqual(set(layers.changing(self.project, {"start": [["platform", "platform"]]})),
                         {"apim-gateway", "apim-management-api", "mongodb"})
        self.assertEqual(layers.changing(self.project, None), {})

    def test_what_a_command_waits_for(self):
        # What it starts among what it names, everything it covers when it names nothing; all that it stops. A
        # container that starts is there once ready, a one-shot once it ended well
        pairs = {"start": [["playground", "welcome"], ["addon", "orchard"], ["service", "apim-gateway"]],
                 "stop": [["addon", "jade"]]}
        self.assertEqual(layers.awaits(self.project, pairs, ["hello-app", "orchard", "orchard-migrate"]), {
            ("Container", "lab-jade"): "stop", ("Container", "lab-hello-app"): "start",
            ("Container", "lab-orchard"): "start", ("Container", "lab-orchard-migrate"): "once"})
        self.assertEqual(set(layers.awaits(self.project, pairs)), {
            ("Container", f"lab-{name}") for name in ("jade", "hello-app", "seed", "orchard", "orchard-migrate",
                                                      "apim-gateway")})

    def test_each_command_of_a_plan_waits_for_what_it_changes(self):
        # Kazoo Desk starts with Kazoo, restarts the gateway and the management API: the one `up` names them all.
        # The playground's app, which does not run, is left as it is
        services = self.project["services"]
        state = on(self.project, ["kiosk"], "welcome")
        plan = self.plan(state, up("kiosk"), "addon", "kazoo-desk", "start")
        self.assertEqual(layers.waits(self.project, plan), [awaited(services, start=[
            "apim-gateway", "apim-management-api", "kazoo", "kazoo-desk", "kazoo-ui"])])
        # A start that stops what is in conflict: what goes is removed by name first, then what starts
        state = on(self.project, ["kazoo-desk"])
        plan = self.plan(state, up("kazoo", "kazoo-desk"), "addon", "redstart", "start")
        self.assertEqual(layers.waits(self.project, plan), [
            awaited(services, stop=["kazoo", "kazoo-desk", "kazoo-ui"]),
            awaited(services, start=["apim-gateway", "apim-management-api", "redstart"])])
        # A stop while the platform is not up removes by name; with nothing to remove there is no command
        plan = self.plan(on(self.project, ["kiosk"]), up("kiosk", platform=False), "addon", "kiosk", "stop")
        self.assertEqual(layers.waits(self.project, plan), [{("Container", "lab-kiosk"): "stop"}])
        plan = self.plan(on(self.project, ["jade"]), up(platform=False), "addon", "kazoo", "stop")
        self.assertEqual((plan["services"], layers.waits(self.project, plan)), ({"remove": [], "start": []}, []))


class ThisMachine(Case):
    """Without a place, a plan says nothing of one."""

    def test_a_local_plan_says_nothing_of_a_place(self):
        project = lab()
        plan = layers.plan(project, on(project), up(platform=False), "playground", "welcome", "start", fresh=True)
        self.assertFalse(set(layers.PLACE_ROWS) & set(plan))
        self.assertEqual(layers.dialog(plan), {key: plan[key] for key in layers.ROWS})


DEVELOPMENT = {"id": "my-account/development", "name": "Development (My Account)"}
TEST = {"id": "client-lab/test", "name": "Test (Client lab)"}
HYSSOP, HOTELS = row("addon", "hyssop", "Hybrid gateway"), row("playground", "hotels", "Hotels apps")
ENVIRONMENT = row("environment", DEVELOPMENT["id"], DEVELOPMENT["name"])


def place(environment=DEVELOPMENT, here=True, serving=None):
    """A remote place as the caller gives it: the add-on of the gateway of that kind, whether the gateway set to run
    here is this place's, and the place it serves otherwise."""
    return {**environment, "gateway": "hyssop", "here": here, "serving": serving}


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
        nor a group, not another service as a row, a service it starts or removes, a container it waits for,
        or what its job's changes cover."""
        allowed = set(layers.local_needs(self.project, self.project["playgrounds"]["hotels"]))
        rows = [found for part in (*layers.ROWS, *layers.PLACE_ROWS) for found in plan[part]]
        for found in rows:
            self.assertNotIn(found["kind"], ("platform", "group"), found)
            self.assertFalse(found["kind"] == "service" and found["id"] in self.core and found["id"] not in allowed, found)
        named = {*plan["services"]["start"], *plan["services"]["remove"]}
        moves = layers.changing(self.project, layers.changes(self.project, plan))
        waited = {container for found in layers.waits(self.project, plan) for _, container in found}
        for name in set(self.core) - allowed:
            self.assertNotIn(name, named)
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
        services["hotels-api"]["depends_on"] = []
        self.assertEqual(layers.local_needs(self.project, self.project["playgrounds"]["hotels"]), [])

    # --- What it starts ---------------------------------------------------------------------------------------------

    def test_nothing_runs(self):
        # The gateway's add-on and the apps start, by name; the platform does not, nor the seed, which runs apart. The
        # on file keeps the playground and the gateway, which is turned on like an add-on turned on by hand
        plan = self.plan(on(self.project), self.up(platform=False), fresh=True)
        self.check(plan, starts=[HOTELS, HYSSOP], playground="hotels", explicit=["hyssop"])
        self.assertEqual(plan["services"], {"remove": [], "start": ["apim-hyssop-gateway", "hotels-api",
                                                                    "hyssop-redis"]})
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
        state = on(self.project, ["hyssop"], "hotels")
        plan = self.plan(state, self.up("hyssop", playgrounds=["hotels"]))
        self.check(plan, playground="hotels", explicit=["hyssop"])
        self.assertEqual(plan["services"], {"remove": [], "start": []})
        self.assertTrue(layers.asks(plan))
        # Its apps do not run: they start, it does not
        plan = self.plan(state, self.up("hyssop"))
        self.check(plan, starts=[HOTELS], playground="hotels", explicit=["hyssop"])
        self.assertEqual(plan["services"]["start"], ["hotels-api"])
        # Its Redis alone runs: the gateway starts, said as the add-on
        running = self.up("hyssop", playgrounds=["hotels"])
        running["services"].discard("apim-hyssop-gateway")
        self.check(self.plan(state, running), starts=[HYSSOP], playground="hotels", explicit=["hyssop"])

    def test_a_gateway_that_does_not_run_starts_whatever_it_serves(self):
        # Set to serve another place, stopped: it starts, and the job gives it this place's token. Nothing is restarted
        plan = self.plan(on(self.project), self.up(), where=place(here=False, serving=TEST))
        self.check(plan, starts=[HOTELS, HYSSOP], playground="hotels", explicit=["hyssop"])

    def test_a_gateway_that_serves_another_place_is_made_again(self):
        # It runs for Test: it restarts with this place's token, and the place says what it serves and stops serving
        state = on(self.project, ["hyssop"], "hotels")
        running = self.up("hyssop", playgrounds=["hotels"])
        plan = self.plan(state, running, where=place(here=False, serving=TEST))
        self.check(plan, restarts=[HYSSOP], serves=[ENVIRONMENT], stops_serving=[row("environment", TEST["id"],
                                                                                    TEST["name"])],
                   playground="hotels", explicit=["hyssop"])
        # Both of its services, by name, without what they depend on; its Redis, unchanged, is left as it is
        self.assertEqual(plan["services"], {"remove": [], "start": ["apim-hyssop-gateway", "hyssop-redis"]})
        self.assertEqual(layers.starting(plan["services"]["start"]),
                         [[*compose.COMPOSE, "up", "-d", "--no-deps", "apim-hyssop-gateway", "hyssop-redis"]])
        # Its row says Starting, and the playground's, as for any start that runs what runs again
        self.assertEqual(layers.changes(self.project, plan), {"start": [["addon", "hyssop"], ["playground", "hotels"]],
                                                              "stop": []})
        self.assertTrue(layers.asks(plan))
        self.assertAlone(plan)
        # Its apps do not run: they start besides, the gateway is not said twice
        plan = self.plan(state, self.up("hyssop"), where=place(here=False, serving=TEST))
        self.check(plan, starts=[HOTELS], restarts=[HYSSOP], serves=[ENVIRONMENT],
                   stops_serving=[row("environment", TEST["id"], TEST["name"])], playground="hotels",
                   explicit=["hyssop"])
        # What it served is not known (a gateway made in the Cloud console): nothing is said to stop
        plan = self.plan(state, running, where=place(here=False))
        self.check(plan, restarts=[HYSSOP], serves=[ENVIRONMENT], playground="hotels", explicit=["hyssop"])

    def test_a_gateway_that_runs_behind_what_is_saved_is_not_restarted_twice(self):
        # Its own services among those that run behind: the swap is the one row that remakes them
        state = on(self.project, ["hyssop"], "hotels")
        plan = self.plan(state, self.up("hyssop", playgrounds=["hotels"]), where=place(here=False, serving=TEST),
                         behind={"apim-hyssop-gateway", "hyssop-redis", "hotels-api"})
        self.check(plan, restarts=[HYSSOP, row("service", "hotels-api", "hotels-api")], serves=[ENVIRONMENT],
                   stops_serving=[row("environment", TEST["id"], TEST["name"])], playground="hotels",
                   explicit=["hyssop"])

    def test_a_gateway_that_serves_this_place_and_runs_behind_what_is_saved_is_made_again_as_saved(self):
        # As any add-on a playground brings: its services that run behind, by their own rows, with no notes
        state = on(self.project, ["hyssop"], "hotels")
        plan = self.plan(state, self.up("hyssop", playgrounds=["hotels"]), behind={"apim-hyssop-gateway"})
        self.check(plan, restarts=[row("service", "apim-hyssop-gateway", "apim-hyssop-gateway")],
                   playground="hotels", explicit=["hyssop"])
        self.assertEqual(plan["services"]["start"], ["apim-hyssop-gateway"])

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
        self.project["addons"]["hyssop"]["restarts"] = ["apim-gateway"]
        self.project["addons"]["kiosk"]["restarts"] = ["apim-management-api"]
        services["hotels-api"]["depends_on"] = ["mongodb", "alert-engine"]
        services["hotels-seed"]["depends_on"] = core
        services["kiosk"]["depends_on"] = ["mongodb"]
        services["hyssop-redis"]["depends_on"] = ["mongodb"]
        self.project["playgrounds"]["welcome"]["restarts"] = ["apim-gateway"]

    def test_no_core_service_is_in_the_plan_of_a_remote_place(self):
        # Over every service of the core, in every situation: of what is on, of what runs, of the place and the start
        self.worst()
        states = [on(self.project), on(self.project, playground="hotels"), on(self.project, ["hyssop"], "hotels"),
                  on(self.project, ["jade", "kiosk"], "welcome"), on(self.project, ["kazoo"], "demo")]
        runnings = [self.up(platform=False), self.up(), self.up(stopped=["databases"]), self.up("hyssop"),
                    self.up("hyssop", "kiosk", playgrounds=["hotels"]),
                    self.up("jade", "kiosk", "agent-lens", playgrounds=["welcome"]),
                    self.up("hyssop", platform=False, created=["hotels"])]
        places = [place(), place(here=False, serving=TEST), place(TEST, here=False)]
        behind = [frozenset(), frozenset(self.core), frozenset({*self.core, "hotels-api", "hyssop-redis", "kiosk",
                                                               "apim-hyssop-gateway"})]
        for state, running, where, late, fresh in itertools.product(states, runnings, places, behind, (True, False)):
            plan = self.plan(state, running, where=where, behind=late, fresh=fresh)
            self.assertAlone(plan)
            self.assertIn("hyssop", plan["explicit"])
        # The same over the gateway's own start, the swap
        for state, running, where in itertools.product(states, runnings, places):
            self.assertAlone(self.plan(state, running, "addon", "hyssop", where=where))

    def test_the_core_services_another_playground_restarts_are_not_restarted(self):
        # Welcome is on with AI Agent Lens, which configures the gateway: on this machine, Hotels' start stops them
        # and restarts the gateway, to take the change off; to a remote place it stops them, and the gateway is left
        # as it is, which the Stack says (Apply needed) and the Stack's Apply makes right
        state = on(self.project, playground="welcome")
        running = self.up("agent-lens", playgrounds=["welcome"])
        local = layers.plan(self.project, state, running, "playground", "hotels", "start")
        self.assertEqual(local["restarts"], [GATEWAY])
        plan = self.plan(state, running)
        self.check(plan, starts=[HOTELS, HYSSOP], stops=[APPS, INSPECTOR], keeps=[DATA], playground="hotels",
                   explicit=["hyssop"])
        self.assertEqual(plan["services"]["remove"], ["agent-lens", "hello-app", "seed"])
        self.assertAlone(plan)

    def test_what_is_in_conflict_with_the_gateway_goes_as_with_any_add_on(self):
        self.project["addons"]["kiosk"]["conflicts"] = {"hyssop": "same ports"}
        state = on(self.project, ["kiosk"])
        plan = self.plan(state, self.up("kiosk"))
        self.check(plan, starts=[HOTELS, HYSSOP], stops=[KIOSK], playground="hotels", explicit=["hyssop"])
        self.assertEqual(plan["services"]["remove"], ["kiosk"])

    # --- Blocked, asked, said ---------------------------------------------------------------------------------------

    def test_the_gateway_s_token_is_not_asked_for(self):
        # The job writes it from the one the Lab keeps; the person types none. Another add-on still asks for its own
        state = on(self.project, missing=["hyssop"])
        self.assertIsNone(self.plan(state, self.up())["blocked"])
        self.assertIsNone(self.plan(state, self.up(), "addon", "hyssop", where=place(here=False))["blocked"])
        self.assertEqual(layers.plan(self.project, state, self.up(), "addon", "hyssop", "start")["blocked"],
                         "Hybrid gateway needs settings")
        self.project["playgrounds"]["hotels"]["addons"] = ["kiosk"]
        state = on(self.project, missing=["hyssop", "kiosk"])
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
        plan = self.plan(on(self.project, ["hyssop"], "hotels"), self.up("hyssop", playgrounds=["hotels"]))
        self.assertTrue(layers.asks(plan))
        self.assertEqual(layers.dialog(plan), {**{key: plan[key] for key in layers.ROWS},
                                               "deploys_to": [ENVIRONMENT], "serves": [], "stops_serving": []})
        # The gateway alone has no place to deploy to: the swap is all it says
        plan = self.plan(on(self.project, ["hyssop"]), self.up("hyssop"), "addon", "hyssop", where=place(here=False))
        self.assertEqual((plan["deploys_to"], [row["kind"] for row in plan["restarts"]], layers.asks(plan)),
                         ([], ["addon"], True))
        # Not running, the gateway's start is the add-on's, and says nothing more
        plan = self.plan(on(self.project), self.up(), "addon", "hyssop", where=place(here=False, serving=TEST))
        self.check(plan, starts=[HYSSOP], deploys_to=[], playground=None, explicit=["hyssop"])
        self.assertFalse(layers.asks(plan))

    def test_the_gateway_outlives_the_playground_s_stop(self):
        # The on file after the deploy holds it on its own: the playground's stop leaves it, and does not name it
        plan = self.plan(on(self.project), self.up(platform=False))
        state = on(self.project, plan["explicit"], plan["playground"])
        running = self.up("hyssop", playgrounds=["hotels"])
        stop = layers.plan(self.project, state, running, "playground", "hotels", "stop")
        self.assertEqual((stop["stops"], stop["turns_off"], stop["keeps"], stop["restarts"]),
                         ([HOTELS], [], [row("data", "hotels", "What Hotels created in Gravitee")], []))
        self.assertEqual((stop["explicit"], stop["playground"]), (["hyssop"], None))
        self.assertEqual(stop["services"]["remove"], ["hotels-api", "hotels-seed"])
        # A place changes nothing of a stop
        again = layers.plan(self.project, state, running, "playground", "hotels", "stop", place=place())
        self.assertEqual(again, stop)

    # --- What a place is for ----------------------------------------------------------------------------------------

    def test_the_plan_is_given_the_gateway_and_does_not_read_its_key(self):
        state, running = on(self.project), self.up(platform=False)
        before = self.plan(state, running)
        del self.project["addons"]["hyssop"]["gateway"]
        self.assertEqual(self.plan(state, running), before)

    def test_a_place_is_for_a_playground_an_add_on_a_setup_and_the_gateway(self):
        state, running = on(self.project), self.up()
        for kind, uid in (("setup", "events"), ("addon", "kiosk"), ("addon", "kazoo"), ("playground", "hotels")):
            with self.subTest(uid=uid):
                plan = self.plan(state, running, kind, uid)
                self.assertAlone(plan)
                self.assertIn("hyssop", plan["explicit"])
                self.assertEqual(plan["deploys_to"], [ENVIRONMENT])
                self.assertIsNone(plan["blocked"])
        with self.assertRaises(paths.NotFound):
            self.plan(state, running, where={**place(), "gateway": "nope"})
        with self.assertRaises(paths.NotFound):
            self.plan(state, running, uid="nope")

    def test_what_an_add_on_sets_on_the_gateway_restarts_the_gateway_of_the_platform_not_the_one_of_this_machine(self):
        # Kazoo sets the gateway: where it goes to a place, the hybrid gateway that runs is made again to take it; the
        # gateway of this machine, which runs too, is not touched
        running = self.up("hyssop", "kazoo", platform=True)
        plan = self.plan(on(self.project, ["hyssop"]), running, "addon", "kazoo", where=place())
        restarting = {row["id"] for row in plan["restarts"]}
        self.assertIn(self.project["addons"]["hyssop"]["gateway"]["service"], restarting)
        self.assertNotIn("apim-gateway", restarting)
        self.assertNotIn("apim-gateway", {*plan["services"]["start"], *plan["services"]["remove"]})


    def test_an_item_that_runs_on_a_platform_restarts_the_gateway_of_that_platform_when_it_stops(self):
        running = self.up("hyssop", "kazoo", platform=True)
        state = {**on(self.project, ["hyssop", "kazoo"]), "targets": {"addon:kazoo": "cloud"}}
        stop = layers.plan(self.project, state, running, "addon", "kazoo", "stop")
        restarting = {row["id"] for row in stop["restarts"]}
        self.assertNotIn("apim-gateway", restarting, "the gateway of this machine was never set by it")
        # On this machine it is what it was: the gateway it set is made again
        local = layers.plan(self.project, on(self.project, ["hyssop", "kazoo"]), running, "addon", "kazoo", "stop")
        self.assertIn("apim-gateway", {row["id"] for row in local["restarts"]})


class Applies(Case):
    """What applying what was saved makes again and starts again, and what a Stack start makes again."""

    RUNNING, STOPPED = {"key": "running", "running": True}, {"key": "stopped", "running": False}
    FAILED, DONE = {"key": "issue", "running": False}, {"key": "done", "running": False}

    def setUp(self):
        self.project = lab()
        services = self.project["services"]
        services["kazoo-ui"]["depends_on"] = ["kazoo"]
        services["kazoo-desk"]["depends_on"] = ["kazoo-ui", "orchard-migrate"]

    def applies(self, states, behind, on=None):
        on = on if on is not None else layers.up_services(self.project, None, ["kazoo", "kazoo-desk", "orchard"])
        return layers.applies(self.project, on, states, behind)

    def test_what_runs_behind_and_what_failed(self):
        # Orchard runs behind: its migration runs again before it, as Compose would run it without --no-deps, and is
        # said with it
        self.project["services"]["orchard"]["depends_on"] = ["orchard-migrate"]
        states = {"apim-gateway": self.RUNNING, "apim-management-api": self.RUNNING, "mongodb": self.STOPPED,
                  "kazoo": self.RUNNING, "kazoo-ui": self.FAILED, "orchard": self.RUNNING,
                  "orchard-migrate": self.DONE}
        found = self.applies(states, {"apim-gateway", "orchard"})
        self.assertEqual(found, {"names": ["apim-gateway", "kazoo-ui", "orchard", "orchard-migrate"],
                                 "restarts": [GATEWAY, row("addon", "orchard", "Orchard")], "starts": [KAZOO],
                                 "runs": True})
        # Not known: every service that runs is named, Compose makes again what changed
        self.assertEqual(self.applies(states, None)["names"],
                         ["apim-gateway", "apim-management-api", "kazoo", "kazoo-ui", "orchard", "orchard-migrate"])
        # Nothing of Orchard behind: its migration is left as it is
        self.assertEqual(self.applies(states, {"apim-gateway"})["names"], ["apim-gateway", "kazoo-ui"])

    def test_what_failed_waits_for_what_it_needs(self):
        # Kazoo stopped: Kazoo UI would fail again alone, and Kazoo Desk, which needs it, too, and nothing runs
        # the migration Kazoo Desk needs
        states = {"apim-gateway": self.RUNNING, "kazoo": self.STOPPED, "kazoo-ui": self.FAILED,
                  "kazoo-desk": self.FAILED, "orchard-migrate": self.DONE}
        self.assertEqual(self.applies(states, set())["names"], [])
        # Kazoo runs: both start again, after the migration, said with what starts
        states["kazoo"] = self.RUNNING
        found = self.applies(states, set())
        self.assertEqual((found["names"], found["starts"]), (["kazoo-desk", "kazoo-ui", "orchard-migrate"],
                                                             [KAZOO, CONSOLE, row("addon", "orchard", "Orchard")]))
        # What is off does not count: its file may add a dependency to a service that is on
        self.project["services"]["kazoo-ui"]["depends_on"] = ["kazoo", "jade"]
        self.assertEqual(self.applies(states, set())["names"], ["kazoo-desk", "kazoo-ui", "orchard-migrate"])
        # A one-shot whose own dependency does not run would fail: it is not run again, and what needs it starts on
        # what it did before
        self.project["services"]["orchard-migrate"]["depends_on"] = ["orchard"]
        states["orchard"] = self.STOPPED
        self.assertEqual(self.applies(states, set())["names"], ["kazoo-desk", "kazoo-ui"])

    def test_nothing_while_nothing_of_what_is_on_runs(self):
        states = {"kazoo": self.STOPPED, "kazoo-ui": self.FAILED, "apim-gateway": self.STOPPED}
        self.assertEqual(self.applies(states, {"kazoo"}), {"names": [], "restarts": [], "starts": [], "runs": False})
        # An add-on that runs while the platform is stopped is applied: what is saved reaches it
        states["kazoo"] = self.RUNNING
        self.assertEqual(self.applies(states, {"kazoo"}), {"names": ["kazoo", "kazoo-ui"], "restarts": [KAZOO],
                                                          "starts": [KAZOO], "runs": True})

    def test_what_a_stack_start_makes_again(self):
        # What of these services, and of what they depend on among what is on, runs behind what is saved
        state = on(self.project, ["kazoo-desk"])
        self.assertEqual(layers.recreates(self.project, state, {"kazoo", "apim-gateway"}, ["kazoo-desk"]), [KAZOO])
        self.assertEqual(layers.recreates(self.project, state, {"apim-gateway"}, ["apim-gateway"]), [GATEWAY])
        self.assertEqual(layers.recreates(self.project, state, set(), ["kazoo-desk"]), [])

    def test_what_owns_what_runs_behind(self):
        # The banner's "Apply needed: …": the groups in their order, the playground's apps, the add-ons by name
        found = layers.owner_rows(self.project, ["kazoo-ui", "hello-app", "mongodb", "apim-gateway", "jade"])
        self.assertEqual(found, [MANAGEMENT, DATABASES, APPS, JADE, KAZOO])

class Files(Case):
    """The on file and the job, in a project folder that holds only what a test writes."""

    def setUp(self):
        self.folder(paths, compose, envfiles, projects)
        fixtures.default(self)
        self.project = lab()
        units = [*self.project["addons"].values(), *self.project["setups"].values(),
                 *self.project["playgrounds"].values()]
        for unit in units:
            self.write(unit["file"])
        self.write(".lab/catalogs/fixture/addons/hyssop/.env", "HYSSOP_CLOUD_TOKEN=token\n")

    def on_file(self, text):
        self.write(paths.ON_FILE, text)
        return layers.current(self.project)

    def test_nothing_on(self):
        self.assertEqual(layers.current(self.project), {"playground": None, "explicit": [], "setups": [],
                                                        "effective": [], "targets": {}, "missing": [], "unset": {},
                                                        "problems": [], "locked": None, "unread": None,
                                                        "stamp": compose.digest("")})

    KAZOO = ("services:\n  apim-gateway:\n    environment:\n      gravitee_kafka_enabled: \"true\"\n"
             "  kazoo:\n    container_name: gio-apim-gateway-probe\n    depends_on:\n      apim-gateway:\n"
             "        condition: service_healthy\n    environment:\n      HOST: http://apim-gateway:8082\n")
    NAMES = {"cloud": {"apim-gateway": "apim-hyssop-gateway"}}

    def test_an_item_that_runs_on_another_platform_is_its_copy_for_it(self):
        self.write(".lab/catalogs/fixture/addons/kazoo/compose.yml", self.KAZOO)
        layers.write("welcome", ["kazoo", "kiosk"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": "cloud"}, self.NAMES)
        copy = ".lab/targets/cloud/.lab__catalogs__fixture__addons__kazoo__compose.yml"
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - .lab/catalogs/fixture/playgrounds/welcome/compose.yml\n"
                         f'  - {{path: "{copy}", project_directory: ".lab/catalogs/fixture/addons/kazoo"}}\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n')
        made = (self.root / copy).read_text(encoding="utf-8")
        # The service, what depends on it and the host that names it are the platform's; a container name is not
        self.assertIn("  apim-hyssop-gateway:\n    environment:", made)
        self.assertIn("      apim-hyssop-gateway:\n        condition: service_healthy", made)
        self.assertIn("HOST: http://apim-hyssop-gateway:8082", made)
        self.assertIn("container_name: gio-apim-gateway-probe", made)
        self.assertNotIn("\n  apim-gateway:", made)
        # What is on is read as the files it was made of, and where each runs is said
        state = layers.current(self.project)
        self.assertEqual((state["explicit"], state["targets"], state["locked"], state["problems"]),
                         (["kazoo", "kiosk"], {"addon:kazoo": "cloud"}, None, []))

    def test_where_an_item_runs_is_kept_until_it_is_said_again_and_its_copy_follows_its_file(self):
        self.write(".lab/catalogs/fixture/addons/kazoo/compose.yml", self.KAZOO)
        layers.write(None, ["kazoo"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": "cloud"}, self.NAMES)
        self.write(".lab/catalogs/fixture/addons/kazoo/compose.yml", self.KAZOO + "      MORE: yes\n")
        layers.write(None, ["kazoo", "kiosk"])   # another item turned on, nothing said of the kazoo
        self.assertEqual(layers.current(self.project)["targets"], {"addon:kazoo": "cloud"})
        self.assertIn("MORE: yes", (self.root / ".lab/targets/cloud/.lab__catalogs__fixture__addons__kazoo__compose.yml").read_text(encoding="utf-8"))
        self.assertIn("apim-hyssop-gateway", (self.root / ".lab/targets/cloud/.lab__catalogs__fixture__addons__kazoo__compose.yml").read_text(encoding="utf-8"))
        layers.write(None, ["kiosk"])   # turned off: it is no more where it was
        self.assertEqual(layers.current(self.project)["targets"], {})
        layers.write(None, ["kazoo"])   # turned on again: on this machine
        self.assertEqual(layers.current(self.project)["targets"], {})
        self.assertIn("  - .lab/catalogs/fixture/addons/kazoo/compose.yml", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))
        layers.write(None, ["kazoo"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": "cloud"}, self.NAMES)
        layers.write(None, ["kazoo"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": None})   # brought back to this machine
        self.assertEqual(layers.current(self.project)["targets"], {})

    def test_a_start_on_this_machine_brings_back_here_what_ran_on_a_platform(self):
        # Kazoo was deployed (its copy for the platform's gateway), then its containers stopped while it stayed on: its Start
        # on This machine runs it here, written for this machine's gateway, whatever else runs where it runs
        self.write(".lab/catalogs/fixture/addons/kazoo/compose.yml", self.KAZOO)
        layers.write(None, ["kazoo", "kiosk"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": "cloud", ".lab/catalogs/fixture/addons/kiosk/compose.yml": "cloud"},
                     self.NAMES)
        state = layers.current(self.project)
        plan = layers.plan(self.project, state, up(), "addon", "kazoo", "start")
        self.assertEqual(plan["targets"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": None})
        _, (_, write, *_), _ = self.applied(plan)
        write(mock.Mock(lines=[]))
        self.assertEqual(layers.current(self.project)["targets"], {"addon:kiosk": "cloud"})
        self.assertIn("  - .lab/catalogs/fixture/addons/kazoo/compose.yml\n", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))
        # A stop leaves every item where it runs
        stop = layers.plan(self.project, layers.current(self.project), up(), "addon", "kazoo", "stop")
        self.assertEqual(stop["targets"], {})

    def test_a_start_back_on_this_machine_makes_again_what_its_plain_file_changes(self):
        # Kazoo was deployed (its copy sets gravitee_kafka_enabled on apim-hyssop-gateway), then its containers stopped
        # while it stayed on. Its Start on This machine writes the plain file, which sets it on apim-gateway, which runs
        self.write(".lab/catalogs/fixture/addons/kazoo/compose.yml", self.KAZOO)
        layers.write(None, ["kazoo"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": "cloud"}, self.NAMES)
        plan = layers.plan(self.project, layers.current(self.project), up(), "addon", "kazoo", "start")
        self.assertEqual(plan["targets"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": None})
        _, (_, write, command), _ = self.applied(plan)
        write(mock.Mock(lines=[]))
        self.assertIn("  - .lab/catalogs/fixture/addons/kazoo/compose.yml\n", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))
        # As the Start of Kazoo while it is off does, the gateway it configures is made again, and the dialog says so
        self.assertIn("apim-gateway", command)
        self.assertEqual(plan["restarts"], [GATEWAY])

    def test_a_playground_started_back_on_this_machine_makes_its_gateway_again(self):
        cloud(self.project)
        self.write(".lab/catalogs/fixture/playgrounds/welcome/compose.yml", "services:\n  apim-gateway:\n    environment:\n      WELCOME: \"on\"\n"
                   "  hello-app:\n    image: busybox\n")
        layers.write("welcome", ["hyssop"], {".lab/catalogs/fixture/playgrounds/welcome/compose.yml": "cloud"}, self.NAMES)   # deployed to Cloud
        state = layers.current(self.project)
        self.assertEqual(state["targets"], {"playground:welcome": "cloud"})
        # Its app was stopped from Containers; the core and the hybrid gateway run. Start on This machine
        plan = layers.plan(self.project, state, up("hyssop", project=self.project), "playground", "welcome", "start")
        _, (_, write, command), _ = self.applied(plan)
        write(mock.Mock(lines=[]))
        self.assertIn("  - .lab/catalogs/fixture/playgrounds/welcome/compose.yml\n", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))
        self.assertIn("apim-gateway", command)   # its `apim-gateway:` override is this machine's gateway's now
        self.assertIn("apim-hyssop-gateway", command, "and no longer the hybrid gateway's")

    def test_what_a_start_brings_back_on_this_machine_is_made_again(self):
        # Kazoo runs on its Cloud copy (HOST http://apim-hyssop-gateway:8082); Kazoo Desk, which needs it, starts here
        self.write(".lab/catalogs/fixture/addons/kazoo/compose.yml", self.KAZOO)
        layers.write(None, ["kazoo"], {".lab/catalogs/fixture/addons/kazoo/compose.yml": "cloud"}, self.NAMES)
        plan = layers.plan(self.project, layers.current(self.project), up("kazoo"), "addon", "kazoo-desk", "start")
        self.assertEqual(plan["targets"], {".lab/catalogs/fixture/addons/kazoo-desk/compose.yml": None, ".lab/catalogs/fixture/addons/kazoo/compose.yml": None})
        _, (_, write, command), _ = self.applied(plan)
        write(mock.Mock(lines=[]))
        self.assertIn("  - .lab/catalogs/fixture/addons/kazoo/compose.yml\n", (self.root / paths.ON_FILE).read_text(encoding="utf-8"))
        self.assertIn("kazoo", command)          # written for this machine now, still running as written for Cloud
        self.assertIn("apim-gateway", command)   # which Kazoo's plain file configures
        # Kazoo Desk started where Kazoo already ran here only starts
        layers.write(None, ["kazoo"])
        plan = layers.plan(self.project, layers.current(self.project), up("kazoo"), "addon", "kazoo-desk", "start")
        self.assertEqual(plan["services"]["start"], ["apim-management-api", "kazoo-desk"])

    def test_write_then_read(self):
        layers.write("welcome", ["kiosk", "kazoo-desk"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - .lab/catalogs/fixture/playgrounds/welcome/compose.yml\n  - .lab/catalogs/fixture/addons/kazoo-desk/compose.yml\n"
                         "  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        state = layers.current(self.project)
        self.assertEqual((state["playground"], state["explicit"]), ("welcome", ["kazoo-desk", "kiosk"]))
        self.assertEqual(state["effective"], ["agent-lens", "kazoo", "kazoo-desk", "kiosk"])
        self.assertIsNone(state["locked"])
        # Nothing on: an empty file, which Compose needs all the same
        layers.write(None, [])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")
        self.assertEqual(layers.current(self.project)["locked"], None)

    def test_a_fresh_clone_starts_with_nothing_on(self):
        layers.prepare()
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")
        self.assertFalse((self.root / ".env").exists())   # make init makes it, from .env.example

    def test_what_belongs_to_this_machine_is_turned_on_like_the_rest(self):
        own(self.project)
        for path in (".lab/catalogs/local/addons/whoami/compose.yml", ".lab/catalogs/local/playgrounds/sandbox/compose.yml"):
            self.write(path)
        layers.write("local/sandbox", ["kiosk"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - .lab/catalogs/local/playgrounds/sandbox/compose.yml\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        state = layers.current(self.project)
        self.assertEqual((state["playground"], state["explicit"], state["effective"], state["locked"]),
                         ("local/sandbox", ["kiosk"], ["kiosk", "local/whoami"], None))
        layers.write(None, ["local/whoami"])
        self.assertEqual(layers.current(self.project)["explicit"], ["local/whoami"])

    def test_setup_entry_is_its_includes(self):
        state = self.on_file("# mine\ninclude:\n  - .lab/catalogs/fixture/setups/events.yml\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        self.assertEqual((state["explicit"], state["setups"]), (["kazoo-desk", "kiosk"], ["events"]))
        self.assertIsNone(state["locked"])

    def test_entry_whose_file_is_gone(self):
        (self.root / ".lab/catalogs/fixture/addons/jade/compose.yml").unlink()
        state = self.on_file("include:\n  - .lab/catalogs/fixture/addons/jade/compose.yml\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        self.assertEqual(state["explicit"], ["kiosk"])
        self.assertEqual(state["problems"], [{"file": ".lab/on.yml",
                                              "message": ".lab/catalogs/fixture/addons/jade/compose.yml does not exist"}])
        self.assertIsNone(state["locked"])
        # Compose fails on it whatever it is asked: the state waits on it (model._wait)
        self.assertEqual(state["unread"], ".lab/catalogs/fixture/addons/jade/compose.yml does not exist")

    def test_entry_whose_file_does_not_load_locks_the_file(self):
        # Written by the Lab UI, it is not the user's change: a rewrite would drop it, the file says why
        del self.project["addons"]["jade"]
        state = self.on_file("include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n  - .lab/catalogs/fixture/addons/jade/compose.yml\n")
        self.assertEqual((state["explicit"], state["locked"]), (["kiosk"], ".lab/catalogs/fixture/addons/jade/compose.yml does not load"))
        message = ".lab/catalogs/fixture/addons/jade/compose.yml does not load: the catalog's Start and Stop are off"
        self.assertEqual(state["problems"], [{"file": ".lab/on.yml", "message": message}])
        self.assertEqual(state["unread"], ".lab/catalogs/fixture/addons/jade/compose.yml does not load")

    def test_own_changes_lock_the_file(self):
        self.write("mine.yml")
        for text in ("include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\nservices:\n  redis:\n    ports: !reset []\n",
                     "include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n  - mine.yml\n",
                     "include:\n  - path: .lab/catalogs/fixture/addons/kiosk/compose.yml\n    env_file: mine.env\n"):
            state = self.on_file(text)
            self.assertEqual(state["locked"], ".lab/on.yml has your own changes", text)
            self.assertEqual(state["explicit"], ["kiosk"])
            # What it keeps from running, said once, in the banner: the catalog's Start and Stop; the Stack runs it as
            # Compose reads it
            self.assertEqual(state["problems"], [{"file": ".lab/on.yml", "message":
                                                  "has your own changes: the catalog's Start and Stop are off"}])

    def test_own_changes_on_one_line_lock_the_file(self):
        for text in ("include: [.lab/catalogs/fixture/addons/kiosk/compose.yml]\n", "name: mine\n", "services: {}\n"):
            self.assertEqual(self.on_file(text)["locked"], ".lab/on.yml has your own changes", text)

    def test_two_playgrounds_lock_the_file(self):
        second(self.project)
        self.write(".lab/catalogs/fixture/playgrounds/demo/compose.yml")
        state = self.on_file("include:\n  - .lab/catalogs/fixture/playgrounds/welcome/compose.yml\n  - .lab/catalogs/fixture/playgrounds/demo/compose.yml\n")
        self.assertEqual((state["playground"], state["locked"]), ("welcome", layers.LOCKED))

    def test_missing_settings_are_read_from_the_add_on_env(self):
        self.assertEqual(layers.current(self.project)["missing"], [])
        (self.root / ".lab/catalogs/fixture/addons/hyssop/.env").write_text("HYSSOP_CLOUD_TOKEN=\n", encoding="utf-8")   # a required setting without a value
        self.assertEqual(layers.current(self.project)["missing"], ["hyssop"])
        (self.root / ".lab/catalogs/fixture/addons/hyssop/.env").unlink()
        self.assertEqual(layers.current(self.project)["missing"], ["hyssop"])

    def test_a_setting_in_the_root_env_is_no_missing_setting_for_compose_reads_that_file_first(self):
        env = paths.unit_env("addon", "hyssop")
        self.write(env, "HYSSOP_CLOUD_TOKEN=\n")
        self.write(".env", "HYSSOP_CLOUD_TOKEN=token\n")
        self.assertEqual(layers.current(self.project)["missing"], [])
        self.write(".env", "HYSSOP_CLOUD_TOKEN=\n")
        self.write(env, "HYSSOP_CLOUD_TOKEN=token\n")
        self.assertEqual(layers.current(self.project)["missing"], ["hyssop"], "the root's empty value wins, as with Compose")

    def applied(self, plan):
        """The job layers.apply() starts for a plan: (title, steps, its other arguments)."""
        jobs = self.patch(layers, "JOBS")
        self.patch(compose, "project_name", lambda: "lab")
        layers.apply(self.project, plan, "Title")
        return self.started(jobs)

    def test_apply_writes_the_on_file_then_runs_up(self):
        plan = layers.plan(self.project, on(self.project, ["kiosk"], "welcome"), up("kiosk"),
                           "addon", "kazoo-desk", "start")
        title, (_, write, command), more = self.applied(plan)
        self.assertEqual(title, "Title")
        # It waits for what starts and restarts; the welcome, on and not running, is left as it is
        waits = {("Container", f"lab-{name}"): "start" for name in (
            "kazoo", "kazoo-ui", "kazoo-desk", "apim-gateway", "apim-management-api")}
        self.assertEqual(more, {"key": "addon:kazoo-desk", "verb": "start",
                                "changes": {"start": [["addon", "kazoo"], ["addon", "kazoo-desk"],
                                                      ["service", "apim-gateway"],
                                                      ["service", "apim-management-api"]],
                                            "stop": []},
                                "waits": [waits]})
        # By name, without what they depend on: nothing else is started, recreated or removed
        self.assertEqual(command, [*compose.COMPOSE, "up", "-d", "--no-deps", "apim-gateway", "apim-management-api",
                                   "kazoo", "kazoo-desk", "kazoo-ui"])
        job = mock.Mock(lines=[])
        write(job)
        self.assertEqual(layers.current(self.project)["explicit"], ["kazoo-desk", "kiosk"])
        self.assertEqual(job.lines, ["Starts: Kazoo, Kazoo Desk", "Restarts: Gateway, Management API"])

    def test_apply_removes_what_goes_by_name_then_restarts(self):
        # AI Agent Lens in place of Jade: Jade removed by name, then the inspector and the gateway it
        # changes; Kiosk, started outside the Lab UI, stays. An image that declares a VOLUME gets an anonymous one at each
        # start: left behind by the removal, nothing reaches it once its container is gone, a Reset neither. Named volumes
        # are not anonymous: Compose's -v keeps them
        state = on(self.project, ["jade"])
        plan = layers.plan(self.project, state, up("jade", "kiosk"), "addon", "agent-lens", "start")
        _, (_, remove, write, start), more = self.applied(plan)
        self.assertEqual((remove, start), ([*compose.COMPOSE, "-p", "lab", "rm", "--stop", "--force", "-v", "jade"],
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
        # Kazoo runs, Kazoo Desk was stopped: one stops, the other is turned off; both are removed by name
        state = on(self.project, ["kazoo-desk"])
        plan = layers.plan(self.project, state, up("kazoo", platform=False, created=["kazoo-desk"]), "addon",
                           "kazoo", "stop")
        _, (_, command, _), more = self.applied(plan)
        self.assertEqual(command, [*compose.COMPOSE, "-p", "lab", "rm", "--stop", "--force", "-v",
                                   "kazoo", "kazoo-desk", "kazoo-ui"])
        self.assertEqual(more, {"key": "addon:kazoo", "verb": "stop",
                                "changes": {"start": [], "stop": [["addon", "kazoo"], ["addon", "kazoo-desk"]]},
                                "waits": [{("Container", "lab-kazoo"): "stop", ("Container", "lab-kazoo-ui"): "stop",
                                           ("Container", "lab-kazoo-desk"): "stop"}]})

    def test_what_goes_is_removed_before_the_on_file_says_it_is_off(self):
        # The removal fails, or is canceled, or the Lab UI ends there: Kazoo is still on, stopped, never off and still running
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/fixture/addons/kazoo/compose.yml\n")
        state = layers.current(self.project)
        plan = layers.plan(self.project, state, up("kazoo"), "addon", "kazoo", "stop")
        title, steps, more = self.applied(plan)
        job, commands = Job(title, steps, **more), []

        def removal_fails(cmd, quiet=False):
            commands.append(cmd)
            raise JobFailed("Error response from daemon: cannot stop container")
        job.exec = removal_fails
        job.run()
        self.assertEqual((job.ok, len(commands)), (False, 1))
        self.assertEqual(layers.current(self.project)["explicit"], ["kazoo"])
        # Once it is removed, the file says it is off
        job, commands = Job(title, steps, **more), []
        job.exec = commands.append
        job.run()
        self.assertTrue(job.ok, job.lines)
        self.assertEqual(layers.current(self.project)["explicit"], [])

    def test_a_job_does_not_write_the_on_file_over_what_another_job_wrote_since_its_plan(self):
        # Kiosk and Redstart are on; two plans are made from that, then the first job turns Kiosk off: the second, made
        # before, would turn Kiosk on again by writing what it read
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n  - .lab/catalogs/fixture/addons/redstart/compose.yml\n")
        state = layers.current(self.project)
        first, second = (layers.plan(self.project, state, up(), "addon", uid, "stop") for uid in ("kiosk", "redstart"))
        for plan in (first, second):
            title, steps, more = self.applied(plan)
            job = Job(title, steps, **more)
            job.exec = lambda cmd, quiet=False: None
            job.run()
        self.assertEqual(job.error(), layers.CHANGED)
        self.assertEqual(layers.current(self.project)["explicit"], ["redstart"], "what the first job wrote stays")
        # Made again from what is on now, it runs
        title, steps, more = self.applied(layers.plan(self.project, layers.current(self.project), up(), "addon", "redstart",
                                                      "stop"))
        job = Job(title, steps, **more)
        job.run()
        self.assertTrue(job.ok, job.lines)
        self.assertEqual(layers.current(self.project)["explicit"], [])

    def test_a_job_refused_for_a_stale_plan_changes_nothing(self):
        # Welcome runs with Kiosk on and stopped. Start Demo (which replaces Welcome) and Turn off Kiosk are planned from
        # the same on file; the Kiosk job, with no command, runs first. Demo's job is refused before it removes anything
        # of Welcome
        second(self.project)
        self.write(".lab/catalogs/fixture/playgrounds/demo/compose.yml")
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/fixture/playgrounds/welcome/compose.yml\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        state = layers.current(self.project)
        running = up("agent-lens", playgrounds=["welcome"])
        demo = layers.plan(self.project, state, running, "playground", "demo", "start")
        kiosk = layers.plan(self.project, state, running, "addon", "kiosk", "stop")
        title, steps, more = self.applied(kiosk)
        job = Job(title, steps, **more)
        job.exec = lambda cmd, quiet=False: None
        job.run()
        self.assertTrue(job.ok, job.lines)
        title, steps, more = self.applied(demo)
        job, commands = Job(title, steps, **more), []
        job.exec = lambda cmd, quiet=False: commands.append(cmd[-5:])
        job.run()
        self.assertEqual(job.error(), layers.CHANGED)
        self.assertEqual(layers.current(self.project)["playground"], "welcome")
        self.assertEqual(commands, [], "a refused job removes nothing")

    def test_a_setup_s_job_is_the_setup_s(self):
        # Its button shows it, though what starts are its add-ons: it waits for them and what they restart
        plan = layers.plan(self.project, on(self.project), up(), "setup", "events", "start")
        more = self.applied(plan)[2]
        self.assertEqual((more["key"], more["verb"]), ("setup:events", "start"))
        self.assertIn(["addon", "kazoo"], more["changes"]["start"])
        self.assertEqual(more["waits"], [{("Container", f"lab-{name}"): how for name, how in (
            ("kazoo", "start"), ("kazoo-ui", "start"), ("kazoo-desk", "start"), ("apim-gateway", "start"),
            ("apim-management-api", "start"))}])


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
                                         "file": f".lab/catalogs/local/addons/{uid}/compose.yml", "env_file": f".lab/catalogs/local/addons/{uid}/.env",
                                         "catalog": "local", "twin": {"id": uid, "name": "Fixture catalog"}}
    kiosk["twin"] = {"id": f"local/{uid}", "name": "Personal"}


class Gates(Case):
    """The refusals of a start, which the plan says as `blocked`: a stop is refused by none of them."""

    def setUp(self):
        self.project = lab()
        own(self.project)

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
        state, running = on(self.project, ["kiosk"]), up("kiosk", project=self.project)
        self.assertIsNone(self.why("addon", "kiosk", state, running, "stop"))

    def test_what_a_unit_brings_that_is_blocked_blocks_it(self):
        self.block("kazoo", "Publishes a port to the network")
        self.assertEqual(self.why("addon", "kazoo-desk"), "Publishes a port to the network")
        self.block("agent-lens", "Mounts the Docker socket")
        self.assertEqual(self.why("playground", "welcome"), "Mounts the Docker socket")
        self.block("kiosk", "Runs with full privileges")
        self.assertEqual(self.why("setup", "overlook"), "Runs with full privileges", "a setup is its add-ons")

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
        self.project["addons"]["kazoo"]["needs_catalogs"] = [need("downloading")]
        self.assertEqual(self.why("addon", "kazoo-desk"), "Downloading Partner Bank")

    def test_the_reasons_come_in_an_order(self):
        # The file is locked, the policy blocks it, a catalog is lacking, its twin is on, a setting is lacking, a port is
        # taken: each is said once the one before it is gone
        twins(self.project)
        self.project["services"]["kiosk"]["ports"] = ["5601"]
        self.project["services"]["jade"]["ports"] = ["5601"]
        self.block("kiosk", "Mounts the Docker socket")
        self.project["addons"]["kiosk"]["needs_catalogs"] = [need("missing")]
        state = on(self.project, ["jade", "local/kiosk"], missing=["kiosk"], locked=layers.LOCKED)
        running = up(project=self.project)
        said = []
        for step in range(6):
            said.append(layers.plan(self.project, state, running, "addon", "kiosk", "start")["blocked"])
            if step == 0:
                state["locked"] = None
            elif step == 1:
                self.project["addons"]["kiosk"]["blocked"] = None
            elif step == 2:
                self.project["addons"]["kiosk"]["needs_catalogs"] = []
            elif step == 3:
                state = on(self.project, ["jade"], missing=["kiosk"])
            elif step == 4:
                state = on(self.project, ["jade"])
        self.assertEqual(said, [layers.LOCKED, "Mounts the Docker socket", NEEDS["missing"],
                                "Also in Personal: turn it off first", "Kiosk needs settings",
                                "Port 5601 is used by Jade"])

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
        # Kazoo Desk needs Kazoo, the welcome brings AI Agent Lens, the events setup is Kazoo Desk: the other half of
        # any of them would be in the on file with the same service names
        twins(self.project, "kazoo")
        twins(self.project, "agent-lens")
        away = "Also in Personal: turn it off first"
        self.assertEqual(self.why("addon", "kazoo-desk", on(self.project, ["local/kazoo"])), away)
        self.assertEqual(self.why("playground", "welcome", on(self.project, ["local/agent-lens"])), away)
        self.assertEqual(self.why("setup", "events", on(self.project, ["local/kazoo"])), away)
        # Up without being on counts here too
        self.assertEqual(self.why("addon", "kazoo-desk", on(self.project), up("local/kazoo", project=self.project)), away)
        # The half of the item's own comes first, then those of what it brings, in the order they go in the file
        twins(self.project, "kazoo-desk")
        self.project["addons"]["kazoo-desk"]["twin"]["name"] = "Mine"
        both = on(self.project, ["local/kazoo", "local/kazoo-desk"])
        self.assertEqual(self.why("addon", "kazoo-desk", both), "Also in Mine: turn it off first")
        self.assertEqual(self.why("addon", "kazoo-desk", on(self.project, ["local/kazoo"])), away)
        # Nothing of the other half is on, or what is on is what the item brings itself: nothing is against it
        self.assertIsNone(self.why("addon", "kazoo-desk", on(self.project, ["kazoo"])))
        self.assertIsNone(self.why("playground", "welcome", on(self.project, ["kiosk"])))
        # A stop is never refused for it
        self.assertIsNone(self.why("addon", "kazoo-desk", on(self.project, ["local/kazoo"]), do="stop"))

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
        twins(self.project, "kazoo")
        self.project["addons"]["kazoo-desk"]["needs"] = ["kazoo", "local/kazoo"]
        self.assertIsNone(self.why("addon", "kazoo-desk", on(self.project, ["local/kazoo"])))

    def test_the_twin_of_a_playground_and_of_a_setup(self):
        self.project["playgrounds"]["welcome"]["twin"] = {"id": "local/sandbox", "name": "Personal"}
        self.assertEqual(self.why("playground", "welcome", on(self.project, playground="local/sandbox")),
                         "Also in Personal: turn it off first")
        self.assertIsNone(self.why("playground", "welcome"))
        self.project["setups"]["overlook"]["twin"] = {"id": "local/mine", "name": "Personal"}
        self.assertEqual(self.why("setup", "overlook", on(self.project, ["kiosk"], setups=["local/mine"])),
                         "Also in Personal: turn it off first")
        self.assertIsNone(self.why("setup", "overlook"))

    # --- Ports -----------------------------------------------------------------------------------------------------

    def publish(self, **ports):
        """These services publish these host ports."""
        for name, published in ports.items():
            self.project["services"][name]["ports"] = list(published)

    def test_a_port_that_something_on_publishes_is_taken(self):
        self.publish(kiosk=["5601"], **{"kazoo-ui": ["5601"]})
        self.assertEqual(self.why("addon", "kiosk", on(self.project, ["kazoo"])), "Port 5601 is used by Kazoo")
        self.assertIsNone(self.why("addon", "kiosk"), "nothing else is on")

    def test_a_port_that_something_runs_on_is_taken_though_it_is_not_on(self):
        self.publish(kiosk=["5601"], **{"kazoo-ui": ["5601"]})
        self.assertEqual(self.why("addon", "kiosk", running=up("kazoo", project=self.project)),
                         "Port 5601 is used by Kazoo")
        # The platform too: it runs
        self.project["services"]["apim-gateway"]["ports"] = ["8082"]
        self.publish(kiosk=["8082"])
        self.assertEqual(self.why("addon", "kiosk"), "Port 8082 is used by API Management")

    def test_the_first_port_by_number_names_the_item(self):
        self.publish(kiosk=["5601", "800"], **{"kazoo-ui": ["800"], "jade": ["5601"]})
        self.assertEqual(self.why("addon", "kiosk", on(self.project, ["kazoo", "jade"])), "Port 800 is used by Kazoo")

    def test_what_the_item_brings_is_not_a_port_taken_from_it(self):
        # Kazoo Desk and the Kazoo it needs may be asked for the port of one another: they run together
        self.publish(**{"kazoo-desk": ["9000"], "kazoo-ui": ["9000"]})
        self.assertIsNone(self.why("addon", "kazoo-desk"))
        self.assertIsNone(self.why("addon", "kazoo-desk", on(self.project, ["kazoo"]), up("kazoo", project=self.project)))

    def test_what_the_same_plan_turns_off_does_not_hold_its_port(self):
        # Another playground replaces the one that is on: its port is free by then
        second(self.project)
        self.publish(**{"hello-app": ["8080"], "demo-app": ["8080"]})
        state = on(self.project, playground="welcome")
        self.assertIsNone(self.why("playground", "demo", state, up(playgrounds=["welcome"], project=self.project)))
        # An add-on that is on and stays holds it
        self.publish(kazoo=["8080"], **{"kazoo-ui": []})
        self.project["playgrounds"]["demo"]["addons"] = []
        self.assertEqual(self.why("playground", "demo", on(self.project, ["kazoo"], "welcome")), "Port 8080 is used by Kazoo")

    def test_the_twin_is_not_named_for_the_port_it_shares(self):
        twins(self.project)
        self.publish(kiosk=["5601"])
        state = on(self.project, ["kiosk"])
        unit = self.project["addons"]["local/kiosk"]
        self.assertIsNone(layers._port_clash(self.project, state, up("kiosk", project=self.project), "addon", unit))

    def test_a_port_the_platform_publishes_is_taken_while_it_does_not_run(self):
        self.project["services"]["apim-gateway"]["ports"] = ["8082"]
        self.publish(kiosk=["8082"])
        self.assertEqual(self.why("addon", "kiosk", running=up(platform=False, project=self.project)),
                         "Port 8082 is used by API Management")

    def test_a_start_is_refused_when_another_program_uses_a_port_it_publishes(self):
        # A Postgres of the person's own on 5432: the start is refused at the click, before the on file names Kiosk
        from lab_ui.library import machine
        self.project["services"]["kiosk"]["ports"], self.project["services"]["apim-gateway"]["ports"] = ["5432"], ["8082"]
        listening = {5432, 8082}
        self.patch(machine, "_taken", lambda port: port in listening)
        self.patch(machine, "_lab_ports", return_value={8082})   # the lab's own gateway holds 8082
        plan = layers.plan(self.project, on(self.project, ["kazoo"]), up(project=self.project), "addon", "kiosk", "start")
        self.assertIsNone(plan["blocked"], "the state never probes the ports")
        with self.assertRaisesRegex(RuntimeError, "^Port 5432 is used by another program$"):
            layers.check(self.project, plan, [])
        listening.discard(5432)
        layers.check(self.project, plan, [])
        # A stop is never refused for it
        listening.add(5432)
        layers.check(self.project, layers.plan(self.project, on(self.project, ["kiosk"]), up("kiosk", project=self.project),
                                               "addon", "kiosk", "stop"), [])

    def test_a_port_that_is_no_number_is_not_one(self):
        self.publish(kiosk=["8000-8010", "x"], **{"kazoo-ui": ["8000-8010", "x"]})
        self.assertIsNone(self.why("addon", "kiosk", on(self.project, ["kazoo"])))

    def test_a_place_judges_the_item_and_the_gateway_it_starts(self):
        cloud(self.project)
        self.block("hyssop", "Publishes a port to the network")
        place = {"id": "acme/dev", "name": "Dev (Acme)", "gateway": "hyssop", "here": True, "serving": None}
        plan = layers.plan(self.project, on(self.project), up(project=self.project), "playground", "hotels", "start",
                           place=place)
        self.assertEqual(plan["blocked"], "Publishes a port to the network")


class Preflight(Case):
    """A start of what comes from a catalog is checked by the catalogs before it runs."""

    def setUp(self):
        self.project = lab()
        own(self.project)
        for uid, catalog in (("kiosk", "fixture"), ("agent-lens", "fixture"), ("kazoo", "fixture"),
                             ("kazoo-desk", "partner"), ("local/whoami", "local")):
            self.project["addons"][uid]["catalog"] = catalog
        self.project["playgrounds"]["welcome"]["catalog"] = "fixture"
        self.project["playgrounds"]["local/sandbox"].update(catalog="local", includes=[], addons=["local/whoami"])
        self.service = types.ModuleType("service")
        self.service.preflight = mock.Mock()
        self.start(mock.patch.dict(sys.modules, {"lab_ui.catalogs.service": self.service}))

    def plan(self, kind, uid, do="start", state=None):
        return layers.plan(self.project, state or on(self.project), up(project=self.project), kind, uid, do)

    def test_a_start_is_handed_the_units_of_catalogs_it_puts_in_the_on_file(self):
        plan = self.plan("playground", "welcome")
        layers.check(self.project, plan, [])
        self.service.preflight.assert_called_once_with(self.project, [("playground", "welcome"), ("addon", "agent-lens")])

    def test_what_is_on_already_and_what_is_brought_are_in_it_too(self):
        plan = self.plan("addon", "kazoo-desk", state=on(self.project, ["kiosk"]))
        layers.check(self.project, plan, [])
        self.assertEqual(self.service.preflight.call_args.args[1],
                         [("addon", "kazoo-desk"), ("addon", "kazoo"), ("addon", "kiosk")])

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


class From(Case):
    """The row From of the plan of a start."""

    def setUp(self):
        self.project = lab()
        own(self.project)

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
        self.come_from("overlook", "setup", name="Acme Hub", version="1.4.0")
        self.assertEqual(self.plan("setup", "overlook")["from"][0]["name"], "Acme Hub")
        self.come_from("kazoo-desk")
        plan = self.plan("addon", "kazoo-desk")   # it starts Kazoo too
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
        place = {"id": "acme/dev", "name": "Dev (Acme)", "gateway": "hyssop", "here": True, "serving": None}
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

    def test_an_item_the_policy_blocks_is_an_entry_like_any_other_and_locks_nothing(self):
        self.project["addons"]["kiosk"]["blocked"] = {"reasons": ["Mounts the Docker socket"]}
        self.project["addons"]["kiosk"]["services"] = []
        state = self.on_file("include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        self.assertEqual((state["explicit"], state["locked"], state["problems"]), (["kiosk"], None, []))

    def test_a_line_outside_the_catalogs_locks_the_file(self):
        self.write("addons/kiosk/compose.yml")
        state = self.on_file("include:\n  - addons/kiosk/compose.yml\n")
        self.assertEqual((state["explicit"], state["locked"]), ([], layers.LOCKED))

    def test_a_write_keeps_what_it_is_given_and_nothing_else(self):
        self.write(paths.ON_FILE, 'include:\n  - .lab/catalogs/fixture/addons/kazoo/compose.yml\n  - .lab/catalogs/local/addons/whoami/compose.yml\n'
                   '  - {path: ".lab/targets/cloud/addons__hyssop__compose.yml", project_directory: ".lab/catalogs/fixture/addons/hyssop"}\n'
                   "  - .lab/catalogs/fixture/setups/events.yml\n")
        layers.write(None, ["kiosk"])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         "include:\n  - .lab/catalogs/fixture/addons/kiosk/compose.yml\n")
        layers.write(None, [])
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"), "")

    def test_an_item_of_a_catalog_runs_on_a_platform_through_its_copy(self):
        self.write(paths.unit_file("addon", "kazoo"), "services:\n  apim-gateway:\n    environment:\n      A: b\n")
        layers.write(None, ["kazoo"], {paths.unit_file("addon", "kazoo"): "cloud"}, {"cloud": {"apim-gateway": "apim-hyssop"}})
        self.assertEqual((self.root / paths.ON_FILE).read_text(encoding="utf-8"),
                         'include:\n  - {path: ".lab/targets/cloud/.lab__catalogs__fixture__addons__kazoo__compose.yml", '
                         'project_directory: ".lab/catalogs/fixture/addons/kazoo"}\n')
        state = layers.current(self.project)
        self.assertEqual((state["explicit"], state["targets"], state["locked"]), (["kazoo"], {"addon:kazoo": "cloud"}, None))

    def test_the_settings_of_an_add_on_of_a_catalog_are_read_from_a_plain_env_only(self):
        env = self.root / paths.unit_env("addon", "hyssop")
        self.write(paths.unit_env("addon", "hyssop"), "HYSSOP_CLOUD_TOKEN=token\n")
        self.assertEqual(layers.current(self.project)["missing"], [])
        # A link that a catalog shipped, to a file of the person's: the settings it holds are not read
        outside = self.root / "outside.env"
        outside.write_text("HYSSOP_CLOUD_TOKEN=token\n", encoding="utf-8")
        env.unlink()
        os.symlink(outside, env)
        self.assertEqual(layers.current(self.project)["missing"], ["hyssop"])
        env.unlink()
        os.mkfifo(env)   # a pipe is not waited for
        self.assertEqual(layers.current(self.project)["missing"], ["hyssop"])

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
        _, (_, write, *_), _ = self.started(jobs)
        with self.assertRaisesRegex(JobFailed, "^kiosk is blocked: Mounts the Docker socket$"):   # the line of its job
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
        _, (_, write, *_), _ = self.started(jobs)
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
        self.addCleanup(os.umask, os.umask(0o077))
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
        self.project["setups"]["overlook"]["catalog"] = "fixture"
        config = self.binds(self.unit / ".state")
        layers.make_binds(self.project, {"do": "start", "kind": "setup", "id": "overlook", "playground": None,
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


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Real(Case):
    """A playground of the fixture catalogs, read with the real core by Compose (no daemon)."""

    def test_the_folders_a_playground_binds_are_made_in_its_folder_and_nowhere_else(self):
        _, built = read(self)
        folder = self.root / paths.unit_folder("playground", "tour")
        before = sorted(os.listdir(folder))
        self.assertNotIn(".state", before)
        plan = layers.plan(built, layers.current(built), up(platform=False, project=built), "playground", "tour", "start")
        self.assertEqual(layers.make_binds(built, plan), [str(folder / ".state")])
        self.assertEqual(oct(os.stat(folder / ".state").st_mode & 0o777), "0o755")
        self.assertEqual(sorted(os.listdir(folder)), sorted([*before, ".state"]), "the seed is where it was")
        self.assertEqual(layers.make_binds(built, plan), [], "once")


if __name__ == "__main__":
    unittest.main()
