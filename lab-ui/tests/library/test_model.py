"""model: service states, the fold, notes by priority, the primary action and the progress of what a job changes, on
the small project of test_layers with hand-made containers. No disk, no Docker. The situations of the UX audits
(findings a#, s#, w#, d#) are replayed where they say what the Lab UI shows."""

import json
import unittest
from unittest import mock

from lab_ui.docker import compose
from lab_ui.library import layers, model, project as projects
from lab_ui.gravitee import license
from lab_ui import paths

from .. import Case
from .test_layers import (API, APPS, CONSOLE, DATA, GATEWAY, INSPECTOR, JADE, KAZOO, PLATFORM, REDPANDA,
                          _service, cloud, lab, licensed, on, own, row, up)


def container(state="running", health="healthy", code=0, status="Up 5 minutes (healthy)", oom=False, restarts=0):
    return {"state": state, "health": health, "status": status, "exit_code": code, "oom": oom, "restarts": restarts,
            "name": "", "image": ""}


def healthy(*names):
    return {name: container() for name in names}


def exited(code, **more):
    return container("exited", "", code, f"Exited ({code}) 2 minutes ago", **more)


def stopped(*names):
    """These services stopped cleanly, from the Stack."""
    return {name: exited(143) for name in names}


CORE = healthy("apim-gateway", "apim-management-api", "mongodb")
WELCOME = {**healthy("agent-lens", "hello-app"), "seed": exited(0)}


def job(title, start=(), stop=(), key=None, done=False, ok=None, progress=None, containers=None, settled=False,
        jid="j1"):
    """The summary of a job (jobs.Job.summary) that starts and stops these [kind, id] pairs: how far it is, how far
    each container it waits for is along it, and whether it settled."""
    return {"id": jid, "title": title, "for": key, "verb": "start", "done": done, "ok": ok, "progress": progress,
            "containers": dict(containers or {}), "settled": settled,
            "changes": {"start": [list(p) for p in start], "stop": [list(p) for p in stop]}}


def failed(jid="j1", verb="start", error="dependency failed to start: container lab-kazoo-ui exited (1)"):
    """What jobs.Jobs.failed_job says of the last job on an item when it failed."""
    return {"id": jid, "verb": verb, "error": error}


class Services(Case):

    def test_states(self):
        cases = [
            (None, False, False, ("absent", "Not created", False)),
            (container(), False, False, ("healthy", "Healthy", True)),
            (container(health=""), False, False, ("running", "Running", True)),
            (container(health="starting"), False, False, ("starting", "Starting", True)),
            (container(health="unhealthy"), False, False, ("issue", "Unhealthy", True)),
            # Restarted more than once, its last start within the minute (compose.RESTART_WINDOW)
            (container(health="", restarts=3, status="Up 20 seconds"), False, False, ("issue", "Keeps restarting", True)),
            (container(health="starting", restarts=2, status="Up 20 seconds (health: starting)"), False, False,
             ("issue", "Keeps restarting", True)),
            (container(health="", restarts=1), False, False, ("running", "Running", True)),
            (container(restarts=3), False, False, ("healthy", "Healthy", True)),
            (container("restarting"), False, False, ("issue", "Restarting", True)),
            (container("paused"), False, False, ("issue", "Paused", True)),
            (container("dead"), False, False, ("issue", "Dead", False)),
            (container("removing"), False, False, ("issue", "Removing", False)),
            (container("created"), False, True, ("starting", "Starting", False)),
            # Made and never started, no job on its way: said so, not "Stopped"
            (container("created"), False, False, ("stopped", "Not started", False)),
            # A one-shot that ended well is done: neither running nor stopped, its own key
            (exited(0), True, False, ("done", "Done", False)),
            (exited(0), False, False, ("stopped", "Stopped", False)),
            (exited(1), False, False, ("issue", "Exited (1)", False)),
            (exited(1), True, False, ("issue", "Exited (1)", False)),
            (exited(130), False, False, ("stopped", "Stopped", False)),
            (exited(143), False, False, ("stopped", "Stopped", False)),
            (exited(137), False, False, ("stopped", "Stopped", False)),
            # A one-shot stopped or killed before its end did not do its part: Docker stopped it while it ran
            (exited(143), True, False, ("issue", "Exited (143)", False)),
            (exited(137), True, False, ("issue", "Exited (137)", False)),
            (exited(137, oom=True), False, False, ("issue", "Out of memory", False)),
        ]
        for found, oneshot, job, (key, label, running) in cases:
            with self.subTest(container=found, oneshot=oneshot, job=job):
                self.assertEqual(model.service_state(found, oneshot, job),
                                 {"key": key, "label": label, "running": running})

    def test_uptime(self):
        for status, text in (("Up 5 minutes (healthy)", "5 min"), ("Up About an hour (healthy)", "1 h"),
                             ("Up About a minute (health: starting)", "1 min"), ("Up Less than a second", "< 1 min"),
                             ("Up 1 second", "< 1 min"), ("Up 30 seconds (unhealthy)", "< 1 min"),
                             ("Up 7 hours", "7 h"), ("Up 3 days", "3 d"),
                             ("Up 2 weeks (healthy)", "14 d"), ("Up 5 months", "5 mo"), ("Up 2 years", "2 y"),
                             ("Exited (0) 2 minutes ago", "2 min ago"),
                             ("Restarting (1) 2 seconds ago", "< 1 min ago"),
                             ("Exited (137) About an hour ago", "1 h ago"), (None, "")):
            self.assertEqual(model.uptime(status), text)

    def test_where_each_container_is_as_a_job_measures_it(self):
        # Ready as the Lab UI judges it; a one-shot only once it ended well. What runs unhealthy or keeps restarting
        # is not on its way: an issue
        project = lab()
        containers = {"apim-gateway": container(), "apim-management-api": container(health="starting"),
                      "mongodb": container(health="unhealthy"), "kazoo": container(health=""),
                      "kazoo-ui": container(health="", restarts=3), "kazoo-desk": container("created", ""),
                      "jade": exited(143), "orchard-migrate": exited(0), "seed": container(health=""),
                      "hello-app": exited(1), "redpanda": container("restarting", "")}
        steps = model.container_steps(project, containers)
        self.assertEqual({name: steps[f"lab-{name}"] for name in containers}, {
            "apim-gateway": "ready", "apim-management-api": "running", "mongodb": "issue", "kazoo": "ready",
            "kazoo-ui": "issue", "kazoo-desk": "created", "jade": "stopped", "orchard-migrate": "ready",
            "seed": "running", "hello-app": "stopped", "redpanda": "issue"})
        self.assertEqual((steps["lab-kiosk"], len(steps)), ("absent", len(project["services"])))

    def test_up(self):
        project = lab()
        containers = {**healthy("apim-gateway", "kazoo-ui", "hello-app"), "kiosk": exited(143),
                      "orchard-migrate": exited(0), "stray": container()}
        self.assertEqual(model.up(project, containers), {
            "platform": True, "groups": set(), "addons": {"kazoo"}, "playgrounds": {"welcome"},
            # What runs, what has a container, the one-shots that ended well
            "services": {"apim-gateway", "kazoo-ui", "hello-app", "stray"}, "created": set(containers),
            "ready": {"apim-gateway", "kazoo-ui", "hello-app", "stray"}, "done": {"orchard-migrate"}})
        self.assertEqual(model.up(project, {}), {"platform": False, "groups": set(), "addons": set(),
                                                 "playgrounds": set(), "services": set(), "created": set(),
                                                 "ready": set(), "done": set()})
        # Ready as a job judges it: not while its health check has not passed, nor a one-shot that runs
        project["services"]["orchard-migrate"]["oneshot"] = True
        waiting = {**healthy("hello-app", "orchard-migrate"), "apim-gateway": container(health="starting")}
        self.assertEqual((model.up(project, waiting)["services"], model.up(project, waiting)["ready"]),
                         ({"hello-app", "apim-gateway", "orchard-migrate"}, {"hello-app"}))
        # A group runs when all its services do; a one-shot does not have to
        project["services"]["mongodb"]["oneshot"] = True
        self.assertEqual(model.up(project, healthy("apim-gateway", "apim-management-api"))["groups"],
                         {"api-management", "databases"})


def states(*containers):
    return [model.service_state(found, False, False) for found in containers]


class Fold(Case):

    def test_each_rule_first_match_wins(self):
        done = model.service_state(exited(0), True, False)
        mixed = states(container(), exited(1), container(health="starting"))
        cases = [
            ("the job starts it", states(exited(1)), True, "start", "starting", "Starting"),
            ("the job stops it", states(container()), True, "stop", "stopping", "Stopping"),
            ("not on, nothing up", states(exited(1), None), False, None, "off", ""),
            ("not on, no service", [], False, None, "off", ""),
            ("a service in issue", mixed, True, None, "issue", "Issue"),
            ("a service starting", states(container(), container(health="starting"), None), True, None,
             "starting", "Starting"),
            ("all healthy, running or done", [*states(container(), container(health="")), done], True, None,
             "running", "Running"),
            ("up without being on", states(container()), False, None, "running", "Running"),
            ("nothing runs", [*states(exited(143), None), done], True, None, "stopped", "Stopped"),
            ("never created", states(None, None), True, None, "stopped", "Stopped"),
            # Nothing at all is never running: a playground without apps of its own, for one; nor is what
            # only did its part, a seed alone
            ("on, no service", [], True, None, "stopped", "Stopped"),
            ("only what did its part", [done], True, None, "stopped", "Stopped"),
            # Stopped from the Stack, or never created, among what runs: not an issue
            ("running and stopped mixed", states(container(), exited(143)), True, None, "partly", "Partly running"),
            ("running and never created", states(container(), None), True, None, "partly", "Partly running"),
            ("partly up without being on", states(container(), exited(0)), False, None, "partly", "Partly running"),
        ]
        for name, found, is_on, change, key, label in cases:
            with self.subTest(name):
                self.assertEqual(model.fold(found, is_on, change), {"key": key, "label": label})

    def test_setup(self):
        def members(*found):
            return [{"on": is_on, "state": {"key": key}} for is_on, key in found]
        cases = [
            (members((True, "running"), (True, "starting")), "starting"),
            (members((True, "stopping"), (True, "issue")), "stopping"),
            (members((True, "running"), (False, "running")), "running"),   # whoever started them
            (members((True, "running"), (True, "issue")), "issue"),
            # All on: none runs is stopped, some running partly
            (members((True, "stopped"), (True, "stopped")), "stopped"),
            (members((True, "running"), (True, "stopped")), "partly"),
            (members((True, "partly"), (True, "running")), "partly"),
            # Not all on: off, its note says when some are
            (members((False, "off"), (True, "issue")), "off"),
            (members((True, "running"), (False, "off")), "off"),
            ([], "off"),
        ]
        for found, key in cases:
            with self.subTest(found=found):
                self.assertEqual(model.setup_fold(found)["key"], key)

    def test_platform_is_its_groups(self):
        cases = [
            (["running", "running"], "running"),
            (["running", "stopped"], "running"),   # a group stopped on purpose is not an issue of the platform
            (["partly", "running"], "running"),    # nor is a container stopped on purpose
            (["partly", "stopped"], "running"),
            (["running", "issue"], "issue"),
            (["issue", "starting"], "starting"),
            (["stopped", "stopped"], "stopped"),
        ]
        for keys, expected in cases:
            with self.subTest(keys=keys):
                groups = [{"state": {"key": key}} for key in keys]
                self.assertEqual(model.platform_fold(groups)["key"], expected)


class Built(Case):
    """The state of the small project of test_layers, built by model.build() from what a test gives."""

    def setUp(self):
        self.patch(paths, "default_ns", return_value="fixture")
        self.project = lab()
        self.start(mock.patch.dict(license._known, packs=None))   # no license model: a feature is said by its id
        self.same = self.patch(compose, "same_image", return_value=False)   # another image id is another image

    def build(self, explicit=(), playground=None, containers=CORE, job=None, failures=None, features=None,
              docker_error=None, missing=(), locked=None, memory=None, setups=(), created=None, saved=None,
              license_status=None, **more):
        state = on(self.project, explicit, playground, missing, locked, setups)
        found = failures or {}
        return model.build(self.project, state, containers, job, lambda kind, uid: found.get((kind, uid)),
                           features, {"error": docker_error, "memory": memory}, created or {}, saved=saved,
                           license_status=license_status, **more)

    def license(self, status="none", **more):
        return {"status": status, "expires": None, "days_left": None, "date": None, "problem": None,
                "digest": "saved", **more}

    def item(self, kind, uid, **scenario):
        body = self.build(**scenario)
        return next(item for item in body[kind + "s"] if item["id"] == uid)

    def note(self, kind, uid, **scenario):
        item = self.item(kind, uid, **scenario)
        return item["note"], item["note_tone"]


class State(Built):

    # --- States ---------------------------------------------------------------------------------------------------

    def test_on_and_not_running_is_stopped(self):
        # On, its containers stopped cleanly or never created: Stopped, never off
        for containers in ({**CORE, **stopped("kazoo", "kazoo-ui")}, CORE):
            item = self.item("addon", "kazoo", explicit=["kazoo"], containers=containers)
            self.assertEqual((item["on"], item["state"]["key"]), (True, "stopped"))
        self.assertEqual(self.item("addon", "kazoo", containers=CORE)["state"]["key"], "off")

    def test_a_playground_without_a_service_of_its_own_is_what_it_brings(self):
        # Sandbox runs nothing itself: it folds over Whoami while it is on, and is never running by itself
        own(self.project)
        sandbox = {"playground": "local/sandbox"}
        cases = [({**CORE, **healthy("whoami")}, sandbox, "running", True),
                 ({**CORE, **stopped("whoami")}, sandbox, "stopped", False),
                 ({}, sandbox, "stopped", False),
                 # Off, Whoami turned on by itself and running: Sandbox is off, nothing of it runs
                 ({**CORE, **healthy("whoami")}, {"explicit": ["local/whoami"]}, "off", False)]
        for containers, scenario, key, is_up in cases:
            with self.subTest(containers=containers, scenario=scenario):
                item = self.item("playground", "local/sandbox", containers=containers, **scenario)
                self.assertEqual((item["state"]["key"], item["up"]), (key, is_up))
        item = self.item("playground", "local/sandbox", containers={**CORE, **stopped("whoami")}, **sandbox)
        self.assertEqual((item["action"]["do"], item["action"]["label"]), ("start", "Start"))

    def test_a_playground_with_a_seed_and_no_apps_is_what_it_brings(self):
        # Its seed did its part, Whoami was stopped from the Stack: it is stopped, not running next to a Turn off
        own(self.project)
        self.project["playgrounds"]["local/sandbox"].update(services=["sandbox-seed"], seed="sandbox-seed")
        self.project["services"]["sandbox-seed"] = {**self.project["services"]["seed"], "container": "lab-sb-seed",
                                                    "owner": {"kind": "playground", "id": "local/sandbox"}}
        sandbox = {"playground": "local/sandbox"}
        item = self.item("playground", "local/sandbox", containers={**CORE, **stopped("whoami"),
                                                                    "sandbox-seed": exited(0)}, **sandbox)
        self.assertEqual((item["state"]["key"], item["up"], item["note"], item["action"]["label"],
                          item["secondary"]["label"]), ("stopped", False, "Stopped", "Start", "Turn off"))
        item = self.item("playground", "local/sandbox", containers={**CORE, **healthy("whoami"),
                                                                    "sandbox-seed": exited(0)}, **sandbox)
        self.assertEqual((item["state"]["key"], item["up"], item["action"]["label"]), ("running", True, "Stop"))
        # Its seed failed: that is its issue
        item = self.item("playground", "local/sandbox", containers={**CORE, **healthy("whoami"),
                                                                    "sandbox-seed": exited(1)}, **sandbox)
        self.assertEqual((item["state"]["key"], item["note"]), ("issue", "Seed exited (1)"))

    def test_a_seed_that_failed_or_that_docker_stopped_runs_again_at_its_start(self):
        # It exited (1) after it recorded what it created, or Docker Desktop restarted in the middle of a first seed and its
        # apps came back without it: its row says why, and its Start, the natural click, sets it up again, seed included
        created = {"welcome": [{"what": "APIs", "count": 3}]}
        for code in (1, 137, 143):
            with self.subTest(code=code):
                containers = {**CORE, **WELCOME, "seed": exited(code)}
                item = self.item("playground", "welcome", playground="welcome", containers=containers, created=created)
                self.assertEqual((item["state"]["key"], item["note"], item["action"]["label"], item["action"]["dialog"]),
                                 ("issue", f"Seed exited ({code})", "Start", None))
                states = {name: model.service_state(found, name == "seed", False) for name, found in containers.items()}
                self.assertTrue(model.seed_failed(states, self.project["playgrounds"]["welcome"]))
        states["seed"] = model.service_state(exited(0), True, False)
        self.assertFalse(model.seed_failed(states, self.project["playgrounds"]["welcome"]))

    def test_a_container_stopped_on_purpose_is_partly_running(self):
        # Kazoo UI stopped from the Stack: Kazoo runs partly, said muted, never red; its button stays Stop
        item = self.item("addon", "kazoo", explicit=["kazoo"], containers={**CORE, "kazoo": container(),
                                                                           "kazoo-ui": exited(143)})
        self.assertEqual((item["state"], item["note"], item["note_tone"], item["action"]["do"]),
                         ({"key": "partly", "label": "Partly running"}, "Kazoo UI stopped", "", "stop"))
        # The Developer Portal of a group stopped: the group runs partly, the platform runs
        body = self.build(containers={**CORE, "apim-management-api": exited(143)})
        management = body["platform"]["groups"][0]
        self.assertEqual((management["state"]["key"], management["note"], management["note_tone"],
                          body["platform"]["state"]["key"]), ("partly", "Management API stopped", "", "running"))
        # A real failure stays an issue, red
        body = self.build(containers={**CORE, "apim-management-api": exited(1)})
        self.assertEqual([(g["state"]["key"], g["note"], g["note_tone"]) for g in body["platform"]["groups"]][0],
                         ("issue", "Management API exited (1)", "err"))
        self.assertEqual(body["platform"]["state"]["key"], "issue")

    def test_the_platform_counts_the_containers_that_run_of_those_that_are_meant_to(self):
        # The sidebar's chip says the platform runs while one group does; the count is what the Dashboard sets next to it
        counts = lambda **more: self.build(**more)["platform"]["containers"]   # noqa: E731
        self.assertEqual(counts(containers=CORE), {"running": 3, "total": 3})
        self.assertEqual(counts(containers={}), {"running": 0, "total": 3})
        self.assertEqual(counts(containers={**CORE, "apim-management-api": exited(143)}), {"running": 2, "total": 3})
        # A container that keeps restarting runs, its state is the issue
        keeps = {**CORE, "apim-gateway": container(health="", restarts=3)}
        self.assertEqual(counts(containers=keeps), {"running": 3, "total": 3})
        # What an add-on or a playground runs is not the platform's
        self.assertEqual(counts(explicit=["kazoo"], containers={**CORE, **healthy("kazoo", "kazoo-ui")}),
                         {"running": 3, "total": 3})

    def test_a_group_counts_its_own_containers_as_the_platform_counts_all_of_them(self):
        groups = lambda **more: [g["containers"] for g in self.build(**more)["platform"]["groups"]]   # noqa: E731
        self.assertEqual(groups(containers=CORE), [{"running": 2, "total": 2}, {"running": 1, "total": 1}])
        stopped = groups(containers={**CORE, "apim-management-api": exited(143)})
        self.assertEqual(stopped, [{"running": 1, "total": 2}, {"running": 1, "total": 1}])
        self.assertEqual(groups(containers={}), [{"running": 0, "total": 2}, {"running": 0, "total": 1}])
        body = self.build(containers={**CORE, "apim-management-api": exited(143)})
        self.assertEqual(sum(g["containers"]["running"] for g in body["platform"]["groups"]), body["platform"]["containers"]["running"])

    def test_a_one_shot_of_the_platform_counts_only_while_it_runs_and_a_service_held_for_a_license_never(self):
        self.project["services"]["mongodb-init"] = _service("mongodb-init", {"kind": "platform", "id": "databases",
                                                                              "name": "Databases"}, oneshot=True)
        self.project["groups"][1]["services"].append("mongodb-init")
        counts = lambda **more: self.build(**more)["platform"]["containers"]   # noqa: E731
        self.assertEqual(counts(containers={**CORE, "mongodb-init": exited(0)}), {"running": 3, "total": 3})
        self.assertEqual(counts(containers=CORE), {"running": 3, "total": 3})
        self.assertEqual(counts(containers={**CORE, "mongodb-init": container(health="")}), {"running": 4, "total": 4})
        licensed(self.project, False)
        self.assertEqual(counts(containers=CORE), {"running": 3, "total": 3})   # the Alert Engine is held
        self.project["license_usable"] = True
        self.assertEqual(counts(containers=CORE), {"running": 3, "total": 4})   # a license: it should run

    def test_setups(self):
        # All its add-ons on and none running: Stopped. Some on: off, and it says so
        stopped_all = {**CORE, **stopped("kazoo", "kazoo-ui", "kazoo-desk")}
        events = self.item("setup", "events", explicit=["kazoo-desk"], containers=stopped_all)
        self.assertEqual((events["on"], events["state"]["key"], events["note"]), (True, "stopped", "Stopped"))
        observability = self.item("setup", "observability", explicit=["jade"], containers=CORE)
        self.assertEqual((observability["on"], observability["state"]["key"], observability["note"],
                          observability["note_title"]), (False, "off", "Partly on", "On: Jade"))
        partly = self.item("setup", "events", explicit=["kazoo-desk"],
                           containers={**CORE, **healthy("kazoo", "kazoo-ui"), "kazoo-desk": exited(143)})
        self.assertEqual((partly["state"]["key"], partly["note"], partly["note_tone"]),
                         ("partly", "kazoo-desk stopped", ""))   # the lab names it by its id

    # --- Notes, by priority ----------------------------------------------------------------------------------------

    def test_failed_comes_first(self):
        failures = {("addon", "kazoo"): failed()}
        broken = {**CORE, "kazoo": container(), "kazoo-ui": exited(1)}
        item = self.item("addon", "kazoo", explicit=["kazoo"], containers=broken, failures=failures)
        # Its tooltip is the job's last line: why it failed
        self.assertEqual((item["state"]["key"], item["note"], item["note_tone"], item["note_title"],
                          item["failed_job"]),
                         ("issue", "Failed", "err", "dependency failed to start: container lab-kazoo-ui exited (1)",
                          "j1"))
        item = self.item("addon", "kazoo", explicit=["kazoo"], failures=failures)
        self.assertEqual((item["state"]["key"], item["note"], item["action"]["label"]), ("stopped", "Failed", "Start"))

    def test_a_canceled_job_is_said_muted(self):
        # A cancel is no failure: "Canceled", without the error tone
        item = self.item("addon", "jade", explicit=["jade"],
                         failures={("addon", "jade"): failed(error="Canceled")})
        self.assertEqual((item["note"], item["note_tone"], item["note_title"], item["failed_job"]),
                         ("Canceled", "", "", "j1"))

    def test_a_failed_stop_keeps_its_note_and_its_stop(self):
        # The on file was written, the containers still run: the stop failed. Its row says Failed, not "Started
        # outside the Lab UI", and its button is Stop again, the retry
        failures = {("addon", "kazoo"): failed(verb="stop", error="Error response from daemon: busy")}
        item = self.item("addon", "kazoo", containers={**CORE, **healthy("kazoo", "kazoo-ui")}, failures=failures)
        self.assertEqual((item["on"], item["up"], item["note"], item["note_tone"], item["note_title"]),
                         (False, True, "Failed", "err", "Error response from daemon: busy"))
        self.assertEqual((item["action"]["do"], item["action"]["label"], item["action"]["dialog"], item["secondary"]),
                         ("stop", "Stop", None, None))
        # A canceled one alike, muted
        failures = {("addon", "kazoo"): failed(verb="stop", error="Canceled")}
        item = self.item("addon", "kazoo", containers={**CORE, **healthy("kazoo", "kazoo-ui")}, failures=failures)
        self.assertEqual((item["note"], item["action"]["label"]), ("Canceled", "Stop"))
        # A playground's apps still running after its stop failed
        failures = {("playground", "welcome"): failed(verb="stop")}
        item = self.item("playground", "welcome", containers={**CORE, **WELCOME}, failures=failures)
        self.assertEqual((item["note"], item["action"]["do"]), ("Failed", "stop"))
        # Nothing of it runs any more: there is nothing to stop again
        item = self.item("addon", "kazoo", containers=CORE, failures={("addon", "kazoo"): failed(verb="stop")})
        self.assertEqual((item["note"], item["action"]["do"]), ("Failed", "start"))

    def test_a_setup_says_the_failed_stop_of_its_add_ons(self):
        # The stop of Kazoo failed, which turned off Kazoo Desk with it: Events, whose add-ons both still run, is
        # not something started outside the Lab UI: Failed, and Stop again
        failures = {("addon", "kazoo"): failed(jid="j9", verb="stop", error="Error response from daemon: busy"),
                    ("addon", "kazoo-desk"): failed(jid="j9", verb="stop", error="Error response from daemon: busy")}
        running = {**CORE, **healthy("kazoo", "kazoo-ui", "kazoo-desk")}
        item = self.item("setup", "events", containers=running, failures=failures)
        self.assertEqual((item["note"], item["note_tone"], item["failed_job"], item["action"]["label"]),
                         ("Failed", "err", "j9", "Stop"))
        # A failed start of one of them is not its own
        failures = {("addon", "kazoo"): failed(jid="j9")}
        item = self.item("setup", "events", containers=CORE, failures=failures)
        self.assertEqual((item["note"], item["failed_job"]), ("", None))

    def test_on_its_way_says_nothing_even_after_a_failure(self):
        # Another job starts it: its row says Starting, and Show output still opens the failure
        events = job("Start Events", start=[["addon", "kazoo"]], key="setup:events", jid="j2")
        item = self.item("addon", "kazoo", explicit=["kazoo-desk"], job=events,
                         failures={("addon", "kazoo"): failed()})
        self.assertEqual((item["state"]["key"], item["note"], item["failed_job"]), ("starting", "", "j1"))

    def test_failed_goes_once_it_runs(self):
        item = self.item("addon", "kazoo", explicit=["kazoo"], containers={**CORE, **healthy("kazoo", "kazoo-ui")},
                         failures={("addon", "kazoo"): failed()})
        self.assertEqual((item["note"], item["failed_job"]), ("", None))

    def test_issue_names_the_service(self):
        broken = {**CORE, "kazoo": container(), "kazoo-ui": exited(1)}
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo"], containers=broken),
                         ("Kazoo UI exited (1)", "err"))
        self.assertEqual(self.note("setup", "events", explicit=["kazoo-desk"],
                                   containers={**broken, **healthy("kazoo-desk")}), ("Kazoo UI exited (1)", "err"))
        # An issue comes before the reason its action is blocked
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo-desk"], missing=["kazoo"],
                                   containers={**broken, **healthy("kazoo-desk")}), ("Kazoo UI exited (1)", "err"))

    def test_one_shot_without_a_container_is_left_out(self):
        # It ended and was pruned: neither an issue of what it belongs to, nor the service a note names
        running = {**CORE, **healthy("orchard")}
        item = self.item("addon", "orchard", explicit=["orchard"], containers=running)
        self.assertEqual((item["state"]["key"], item["note"]), ("running", ""))
        failed_migration = {**running, "orchard-migrate": exited(1)}
        self.assertEqual(self.note("addon", "orchard", explicit=["orchard"], containers=failed_migration),
                         ("Orchard migration exited (1)", "err"))
        half = {**CORE, **healthy("kazoo")}   # a service that should run counts, with or without a container
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo"], containers=half), ("Kazoo UI not created", ""))
        self.project["setups"]["local/mine"]["members"] = ["orchard", "kazoo"]
        half = {**running, "kazoo": container(), "kazoo-ui": exited(143)}
        self.assertEqual(self.note("setup", "local/mine", explicit=["kazoo", "orchard"], containers=half),
                         ("Kazoo UI stopped", ""))
        # Alone, it is all there is to look at: on and not run yet, its add-on is stopped
        self.project["addons"]["orchard"]["services"] = ["orchard-migrate"]
        self.assertEqual(self.item("addon", "orchard", explicit=["orchard"])["state"]["key"], "stopped")

    def test_blocked_gives_its_reason(self):
        # A short label in the warn tone that leads to the add-on that lacks them, its name in the explanation
        item = self.item("addon", "kazoo-desk", missing=["kazoo"])
        self.assertEqual((item["note"], item["note_tone"], item["note_title"], item["note_target"]),
                         ("Needs settings", "warn", "Kazoo needs settings", {"kind": "addon", "id": "kazoo"}))

    def test_a_locked_on_file_is_not_a_note(self):
        # Said once in the banner (a problem of the state); the button carries the reason
        self.assertEqual(self.note("addon", "jade", locked=layers.LOCKED), ("", ""))
        action = self.item("addon", "jade", locked=layers.LOCKED)["action"]
        self.assertEqual((action["enabled"], action["reason"]), (False, layers.LOCKED))

    def test_on_and_not_running_starts_with_the_platform(self):
        # What is on and does not run says so; with the platform, once it is started
        down = {**stopped("apim-gateway", "apim-management-api", "mongodb", "kazoo", "kazoo-ui")}
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo"], containers=down),
                         ("Starts with the platform", ""))
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo"], containers={}),
                         ("Starts with the platform", ""))
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo"], containers=CORE), ("Stopped", ""))
        self.assertEqual(self.note("playground", "welcome", playground="welcome", containers=down),
                         ("Starts with the platform", ""))
        # Before what brought it: it is the fact to know first
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo-desk"], containers=down),
                         ("Starts with the platform", ""))
        # Without Docker, what is on is still read from the files: it says so, as after a reboot.
        # What is off says nothing
        item = self.item("addon", "kazoo", explicit=["kazoo"], containers={}, docker_error="down")
        self.assertEqual((item["on"], item["state"]["key"], item["note"]),
                         (True, "stopped", "Starts with the platform"))
        self.assertEqual(self.item("addon", "jade", explicit=["kazoo"], containers={}, docker_error="down")["state"],
                         {"key": "off", "label": ""})
        # Without the core file, what is on is not known either: nothing is said
        self.project["core_loaded"] = False
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo"], containers={}), ("", ""))

    def test_a_row_does_not_say_what_its_start_would_stop(self):
        # The dialog of its start says it, once, before anything stops: a row that said it too would say it on every
        # other item of the list
        self.project["addons"]["redpanda"]["conflicts"] = {"kazoo": "same ports", "jade": "", "kiosk": ""}
        running = {**CORE, **healthy("jade", "kazoo", "kazoo-ui", "kazoo-desk", "kiosk")}
        item = self.item("addon", "redpanda", explicit=["jade", "kazoo-desk", "kiosk"], containers=running)
        self.assertEqual((item["note"], item["note_tone"], item["note_title"]), ("", "", ""))
        self.assertTrue(item["action"]["dialog"], "the start still asks, and says what it stops")
        running = {**CORE, **healthy("jade", "kiosk")}   # what the license lacks is still said
        self.assertEqual(self.note("playground", "welcome", explicit=["jade", "kiosk"], containers=running,
                                   features={"feature-0"}), ("Not in the license", "warn"))

    def test_via_what_brought_it(self):
        # It links to what brought it
        running = {**CORE, **healthy("kazoo", "kazoo-ui", "kazoo-desk")}
        item = self.item("addon", "kazoo", explicit=["kazoo-desk"], containers=running)
        self.assertEqual((item["note"], item["note_target"]), ("via Kazoo Desk", {"kind": "addon",
                                                                                     "id": "kazoo-desk"}))
        self.assertEqual(self.note("addon", "kazoo-desk", explicit=["kazoo-desk"], containers=running), ("", ""))
        running = {**CORE, **WELCOME}
        item = self.item("addon", "agent-lens", playground="welcome", containers=running)
        self.assertEqual((item["note"], item["note_target"]), ("via Welcome", {"kind": "playground",
                                                                                "id": "welcome"}))
        # A setup too, by being on, brought by the playground or by an add-on
        item = self.item("setup", "inspect", playground="welcome", containers=running)
        self.assertEqual((item["note"], item["note_target"]), ("via Welcome", {"kind": "playground",
                                                                                "id": "welcome"}))
        turned_on = {"explicit": ["agent-lens"], "playground": "welcome", "containers": running}
        self.assertEqual(self.note("setup", "inspect", **turned_on), ("", ""))
        self.project["setups"]["just-kazoo"] = {**self.project["setups"]["inspect"], "id": "just-kazoo",
                                                "name": "Just Kazoo", "includes": ["kazoo"], "members": ["kazoo"]}
        running = {**CORE, **healthy("kazoo", "kazoo-ui", "kazoo-desk")}
        item = self.item("setup", "just-kazoo", explicit=["kazoo-desk"], containers=running)
        self.assertEqual((item["note"], item["note_target"]), ("via Kazoo Desk", {"kind": "addon",
                                                                                     "id": "kazoo-desk"}))
        # Its stop would change nothing: what uses it is said
        self.assertEqual((item["action"]["enabled"], item["action"]["reason"]),
                         (False, "Used by Events, Kazoo Desk"))

    def test_started_outside_the_lab_ui(self):
        # Running without being on: its note says the fact, its button turns it on, its menu stops it
        item = self.item("addon", "kiosk", containers={**CORE, **healthy("kiosk")})
        self.assertEqual((item["on"], item["up"], item["state"]["key"], item["note"], item["note_target"]),
                         (False, True, "running", "Started by hand", None))
        self.assertEqual(item["note_title"], "Not from the Lab UI, so the platform does not start it.")
        self.assertEqual((item["action"]["do"], item["action"]["label"], item["secondary"]["label"]),
                         ("start", "Turn on", "Stop"))
        # A setup whose add-ons all run, none of them on; its tooltip names them
        item = self.item("setup", "observability", containers={**CORE, **healthy("jade", "kiosk")})
        self.assertEqual((item["note"], item["note_title"], item["action"]["label"]),
                         ("Started by hand",
                          "Jade, Kiosk were not started from the Lab UI, so the platform does not start them.", "Turn on"))
        # One of them running alone, the other off: the setup is off, and says why its menu has Stop, as an add-on
        # does
        item = self.item("setup", "observability", containers={**CORE, **healthy("jade")})
        self.assertEqual((item["state"]["key"], item["note"], item["note_title"], item["action"]["label"],
                          item["secondary"]["label"]),
                         ("off", "Started by hand",
                          "Jade was not started from the Lab UI, so the platform does not start it.", "Turn on", "Stop"))
        # What of it is on runs as it should: that is not outside the Lab UI
        item = self.item("setup", "observability", explicit=["jade"], containers={**CORE, **healthy("jade")})
        self.assertEqual((item["note"], item["action"]["label"]), ("Partly on", "Start"))

    def test_license_lacks_a_feature(self):
        item = self.item("playground", "welcome", features={"feature-0"})
        self.assertEqual((item["note"], item["note_tone"], item["note_title"]),
                         ("Not in the license", "warn", "Uses: feature-1"))
        self.assertEqual(self.note("playground", "welcome", features={"feature-1"}), ("", ""))
        self.assertEqual(self.note("playground", "welcome", features=None), ("", ""))

    # --- The primary action ----------------------------------------------------------------------------------------

    def test_action_table(self):
        kazoo_on = {**CORE, **healthy("kazoo", "kazoo-ui")}
        console_on = {**kazoo_on, **healthy("kazoo-desk")}
        kazoo = job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo", jid="j2")
        both = [KAZOO, CONSOLE]
        cases = [   # item, scenario -> do, label, enabled, reason, the stops and starts of its dialog
            # It restarts the gateway, which runs: it asks
            ("off", "addon", "kazoo", {}, "start", "Start", True, None, ([], [KAZOO])),
            ("off, restarting nothing", "addon", "kiosk", {}, "start", "Start", True, None, None),
            ("running", "addon", "kazoo", {"explicit": ["kazoo"], "containers": kazoo_on}, "stop", "Stop", True, None,
             ([KAZOO], [])),
            ("on, stopped", "addon", "kazoo", {"explicit": ["kazoo"]}, "start", "Start", True, None, None),
            ("on, partly running", "addon", "kazoo", {"explicit": ["kazoo"], "containers": {**CORE,
                                                                                            **healthy("kazoo")}},
             "stop", "Stop", True, None, ([KAZOO], [])),
            ("on, issue", "addon", "kazoo", {"explicit": ["kazoo"], "containers": {**CORE, **healthy("kazoo"),
                                                                                   "kazoo-ui": exited(1)}},
             "start", "Start", True, None, None),
            ("stop takes what needs it", "addon", "kazoo", {"explicit": ["kazoo-desk"], "containers": console_on},
             "stop", "Stop", True, None, (both, [])),
            ("stop takes what only it needed", "addon", "kazoo-desk",
             {"explicit": ["kazoo-desk"], "containers": console_on}, "stop", "Stop", True, None, (both, [])),
            ("start stops a conflict", "addon", "redpanda", {"explicit": ["kazoo"], "containers": kazoo_on},
             "start", "Start", True, None, ([KAZOO], [REDPANDA])),
            # Only what it needs starts: the platform stays as it is
            ("start leaves the platform as it is", "addon", "kazoo", {"containers": {}}, "start", "Start", True, None,
             None),
            ("start leaves a group stopped by itself", "addon", "kazoo",
             {"containers": healthy("apim-gateway", "apim-management-api")}, "start", "Start", True, None,
             ([], [KAZOO])),
            ("running without being on", "addon", "kiosk", {"containers": {**CORE, **healthy("kiosk")}},
             "start", "Turn on", True, None, None),
            # Without its settings it cannot be turned on: its Start waits for them, its note opens them
            ("lacks settings", "addon", "hybrid", {"missing": ["hybrid"]}, "start", "Start", False, "Needs settings", None),
            ("lacks settings, whatever else waits", "addon", "hybrid",
             {"missing": ["hybrid"], "job": kazoo, "docker_error": "down", "locked": layers.LOCKED},
             "start", "Start", False, "Needs settings", None),
            # ... and while it runs all the same (started outside the Lab UI), it is stopped from its row
            ("lacks settings and runs", "addon", "hybrid",
             {"missing": ["hybrid"], "containers": {**CORE, **healthy("hybrid-gateway")}},
             "stop", "Stop", True, None, None),
            ("on with missing settings", "addon", "hybrid", {"explicit": ["hybrid"], "missing": ["hybrid"]},
             "start", "Start", False, "Hybrid gateway needs settings", None),
            ("needs one with missing settings", "addon", "kazoo-desk", {"missing": ["kazoo"]},
             "start", "Start", False, "Kazoo needs settings", ([], both)),
            ("own changes", "addon", "kazoo", {"locked": layers.LOCKED, "job": kazoo, "docker_error": "down"},
             "start", "Start", False, ".lab/on.yml has your own changes", ([], [KAZOO])),
            ("docker down", "addon", "kazoo", {"explicit": ["kazoo"], "containers": {}, "docker_error": "down"},
             "start", "Start", False, "Docker is not reachable", None),
            # What the job starts can be stopped meanwhile: nothing of it runs yet, it turns it off
            ("the job starts it", "addon", "kazoo", {"explicit": ["kazoo"], "job": kazoo},
             "stop", "Turn off", False, "Wait for Start Kazoo", ([], [])),
            ("another job runs", "addon", "jade", {"job": kazoo}, "start", "Start", False, "Wait for Start Kazoo",
             ([], [JADE])),
            ("docker down, a job runs", "addon", "jade", {"job": kazoo, "docker_error": "down"},
             "start", "Start", False, "Docker is not reachable", ([], [JADE])),
            ("a job that ended", "addon", "jade", {"job": {**kazoo, "done": True, "ok": True, "progress": 1}},
             "start", "Start", True, None, ([], [JADE])),
            ("setup off", "setup", "events", {}, "start", "Start", True, None, ([], both)),
            ("setup running", "setup", "events", {"explicit": ["kazoo-desk"], "containers": console_on},
             "stop", "Stop", True, None, (both, [])),
            # What the playground uses, its stop keeps: nothing to stop, and it says why
            ("setup running through another", "setup", "inspect",
             {"playground": "welcome", "containers": {**CORE, **WELCOME}}, "stop", "Stop", False,
             "Used by Welcome", ([], [])),
            ("playground off", "playground", "welcome", {}, "start", "Start", True, None, ([], [APPS, INSPECTOR])),
            ("playground running", "playground", "welcome",
             {"playground": "welcome", "containers": {**CORE, **WELCOME}}, "stop", "Stop", True, None,
             ([APPS, INSPECTOR], [])),
            ("playground running, its add-on turned on", "playground", "welcome",
             {"playground": "welcome", "explicit": ["agent-lens"], "containers": {**CORE, **WELCOME}},
             "stop", "Stop", True, None, ([APPS], [])),
            ("playground on, apps stopped", "playground", "welcome",
             {"playground": "welcome", "containers": {**CORE, **healthy("agent-lens")}},
             "start", "Start", True, None, None),
        ]
        for name, kind, uid, scenario, do, label, enabled, reason, dialog in cases:
            with self.subTest(name):
                action = self.item(kind, uid, **scenario)["action"]
                self.assertEqual({key: action[key] for key in ("do", "label", "enabled", "reason")},
                                 {"do": do, "label": label, "enabled": enabled, "reason": reason})
                shown = action["dialog"] and (action["dialog"]["stops"], action["dialog"]["starts"])
                self.assertEqual(shown, dialog)

    def test_a_playground_already_on_runs_again_without_its_seed(self):
        # On and seeded, the platform stopped: its start runs it with the platform, without its seed or a build
        down = stopped("apim-gateway", "apim-management-api", "mongodb", "agent-lens", "hello-app")
        created = {"welcome": [{"what": "APIs", "count": 3}]}
        item = self.item("playground", "welcome", playground="welcome", containers=down, created=created)
        self.assertEqual((item["action"]["label"], item["action"]["dialog"]["starts"]),
                         ("Start", [PLATFORM, APPS, INSPECTOR]))
        # Its seed never recorded anything: its start sets it up again, seed included
        plan = layers.plan(self.project, on(self.project, playground="welcome"), model.up(self.project, down),
                           "playground", "welcome", "start", fresh=True)
        self.assertIn("seed", plan["services"]["start"])

    def test_wait(self):
        # Why nothing can be started now, for whatever waits on the page: Docker first, then the running job
        kazoo = job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo", jid="j2")
        cases = [({}, None), ({"job": kazoo}, "Wait for Start Kazoo"),
                 ({"job": {**kazoo, "done": True, "ok": True, "progress": 0.5}}, None),
                 ({"job": kazoo, "docker_error": "down"}, "Docker is not reachable")]
        for scenario, wait in cases:
            with self.subTest(scenario):
                self.assertEqual(self.build(**scenario)["wait"], wait)

    def test_docker_memory(self):
        # Short memory is said whatever is on, also while the platform is stopped; enough of it says nothing
        short, enough = 4 * 2 ** 30, 16 * 2 ** 30
        warned = {"error": None, "warning": "Docker memory: 4.0 GB, 8 GB+ recommended"}
        self.assertEqual(self.build(explicit=["kazoo"], memory=short)["docker"], warned)
        self.assertEqual(self.build(memory=short)["docker"], warned)
        self.assertEqual(self.build(memory=short, containers=stopped("apim-gateway", "mongodb"))["docker"], warned)
        self.assertIsNone(self.build(explicit=["kazoo"], memory=enough)["docker"]["warning"])
        self.assertEqual(self.build(explicit=["kazoo"], docker_error="down")["docker"],
                         {"error": "down", "warning": None})

    def test_core_file_that_does_not_load(self):
        # Compose reads nothing without it: Start and Stop say why before the click, the settings an add-on lacks first
        self.project["core_loaded"] = False
        action = self.item("addon", "kazoo")["action"]
        self.assertEqual((action["enabled"], action["reason"]), (False, "docker-compose.yml does not load"))
        self.assertEqual(self.item("addon", "hybrid", missing=["hybrid"])["action"]["reason"], "Needs settings")
        start = self.build()["platform"]["start"]
        self.assertEqual((start["enabled"], start["reason"]), (False, "docker-compose.yml does not load"))

    def test_dialog_rows(self):
        running = {**CORE, **healthy("kazoo", "kazoo-ui", "kazoo-desk")}
        dialog = self.item("addon", "kazoo", explicit=["kazoo-desk"], containers=running)["action"]["dialog"]
        self.assertEqual(dialog, {"starts": [], "stops": [KAZOO, CONSOLE], "turns_off": [], "keeps": [],
                                  "restarts": [GATEWAY, API]})
        item = self.item("playground", "welcome", playground="welcome", containers={**CORE, **WELCOME})
        self.assertEqual(item["action"]["dialog"]["keeps"], [DATA])
        self.assertEqual(item["stop"], {"stops": [APPS, INSPECTOR], "turns_off": [], "keeps": [DATA],
                                        "delete": {"enabled": True, "reason": None}})

    def test_what_the_seed_needs_must_run(self):
        # Re-seed, and the stop that deletes what the seed created, wait for the services the seed depends on: the
        # first that does not run says why
        self.project["services"]["seed"]["depends_on"] = ["apim-management-api", "orchard"]
        self.project["playgrounds"]["welcome"]["addons"] = ["agent-lens", "orchard"]
        down = stopped("apim-gateway", "apim-management-api", "mongodb")
        item = self.item("playground", "welcome", playground="welcome", containers=down)
        said = {"enabled": False, "reason": "Management API is not running"}
        self.assertEqual((item["reseed"], item["stop"]["delete"]), (said, said))
        item = self.item("playground", "welcome", playground="welcome", containers={**CORE, **WELCOME})
        said = {"enabled": False, "reason": "orchard is not running"}   # the lab names it by its id
        self.assertEqual((item["reseed"], item["stop"]["delete"]), (said, said))
        running = {**CORE, **WELCOME, **healthy("orchard"), "orchard-migrate": exited(0)}
        item = self.item("playground", "welcome", playground="welcome", containers=running)
        self.assertEqual(item["reseed"], {"enabled": True, "reason": None})
        # What they depend on in turn: the management API runs, MongoDB, which it needs, does not
        self.project["services"]["apim-management-api"]["depends_on"] = ["mongodb"]
        item = self.item("playground", "welcome", playground="welcome", containers={**running, **stopped("mongodb")})
        self.assertEqual(item["reseed"], {"enabled": False, "reason": "mongodb is not running"})
        # Off, it has nothing to seed again; while a job runs, it waits like the rest
        self.assertEqual(self.item("playground", "welcome", containers=running)["reseed"],
                         {"enabled": False, "reason": "Welcome is off"})
        busy = job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo")
        self.assertEqual(self.item("playground", "welcome", playground="welcome", containers=running,
                                   job=busy)["reseed"], {"enabled": False, "reason": "Wait for Start Kazoo"})
        # Without a seed, neither
        own(self.project)
        item = self.item("playground", "local/sandbox", playground="local/sandbox")
        self.assertEqual((item["reseed"], item["stop"]["delete"]), (None, None))

    def test_the_stack_s_start(self):
        # What it runs beyond what runs; a dialog once that is more than the core; Start shows while there is any
        #
        down = stopped("apim-gateway", "apim-management-api", "mongodb", "kazoo", "kazoo-ui")
        start = self.build(explicit=["kazoo"], containers=down)["platform"]["start"]
        self.assertEqual(start, {"needed": True, "starts": [PLATFORM, KAZOO], "restarts": [], "enabled": True,
                                 "reason": None,
                                 "dialog": {"starts": [PLATFORM, KAZOO], "stops": [], "turns_off": [], "keeps": [],
                                            "restarts": []}})
        start = self.build(containers=down)["platform"]["start"]
        self.assertEqual((start["needed"], start["starts"], start["dialog"]), (True, [PLATFORM], None))
        start = self.build(explicit=["kazoo"], containers={**CORE, **healthy("kazoo", "kazoo-ui")})["platform"]["start"]
        self.assertEqual((start["needed"], start["starts"], start["dialog"]), (False, [], None))
        # Kazoo UI stopped from the Stack: the Stack's Start would run it
        start = self.build(explicit=["kazoo"], containers={**CORE, **healthy("kazoo"),
                                                           "kazoo-ui": exited(143)})["platform"]["start"]
        self.assertEqual((start["needed"], start["starts"]), (True, [KAZOO]))
        busy = job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo")
        start = self.build(containers=down, job=busy)["platform"]["start"]
        self.assertEqual((start["enabled"], start["reason"]), (False, "Wait for Start Kazoo"))

    def test_the_stack_s_update_and_restart(self):
        # Both act on what runs: Update makes it again with the images it pulls, Restart restarts it as it is. Neither
        # starts what was stopped, and what Update makes again is not known before its pull: it never asks
        running = {**CORE, **healthy("kazoo", "kazoo-ui")}
        platform = self.build(explicit=["kazoo"], containers=running)["platform"]
        self.assertEqual(platform["update"], {"needed": True, "enabled": True, "reason": None})
        self.assertEqual(platform["restart"], {"needed": True, "enabled": True, "reason": None})
        # Nothing of what is on runs: neither is offered, and each says why
        down = stopped("apim-gateway", "apim-management-api", "mongodb", "kazoo", "kazoo-ui")
        platform = self.build(explicit=["kazoo"], containers=down)["platform"]
        nothing = {"needed": False, "enabled": False, "reason": "Nothing runs"}
        self.assertEqual((platform["restart"], platform["update"]), (nothing, nothing))
        # An add-on that is on runs alone: both act on it; one that runs without being on does not count
        platform = self.build(explicit=["kazoo"], containers={**down, **healthy("kazoo")})["platform"]
        self.assertEqual(platform["restart"], {"needed": True, "enabled": True, "reason": None})
        self.assertFalse(self.build(containers={**down, **healthy("kiosk")})["platform"]["restart"]["needed"])
        # With the user's own on file, what only it adds counts: Compose reads it
        locked = self.build(containers={**down, "mine": container()}, locked=layers.LOCKED)["platform"]
        self.assertTrue(locked["restart"]["needed"])
        # They wait like any action
        busy = job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo")
        platform = self.build(job=busy)["platform"]
        self.assertEqual((platform["update"]["enabled"], platform["update"]["reason"], platform["restart"]["reason"]),
                         (False, "Wait for Start Kazoo", "Wait for Start Kazoo"))

    def test_what_reset_turns_off(self):
        # The playground that is on: its seed's work goes with the volumes. Not in an on file with the user's own
        # changes, which Reset does not write
        self.assertEqual(self.build(playground="welcome")["platform"]["reset_turns_off"], "Welcome")
        self.assertIsNone(self.build(explicit=["kazoo"])["platform"]["reset_turns_off"])
        self.assertIsNone(self.build(playground="welcome", locked=layers.LOCKED)["platform"]["reset_turns_off"])

    def test_what_a_start_on_the_stack_brings(self):
        # A group's, a service's, an add-on's or a playground's Start on the Stack starts first what it depends on
        # that does not run: said before the click. The hello app depends on the gateway, which depends on the
        # management API and MongoDB; Kazoo Desk on the management API
        services = self.project["services"]
        services["hello-app"]["depends_on"] = ["apim-gateway"]
        services["apim-gateway"]["depends_on"] = ["apim-management-api", "mongodb"]
        services["kazoo-desk"]["depends_on"] = ["apim-management-api", "kazoo"]
        body = self.build(explicit=["kazoo-desk"], playground="welcome", containers=healthy("mongodb"))
        management, databases = body["platform"]["groups"]
        playground = body["playgrounds"][0]
        self.assertEqual((body["services"]["hello-app"]["brings"], playground["brings"]),
                         ([row("group", "api-management", "API Management")],) * 2)
        self.assertEqual((management["brings"], databases["brings"]), ([], []))
        console = next(a for a in body["addons"] if a["id"] == "kazoo-desk")
        self.assertEqual(console["brings"], [API, KAZOO])
        # What runs, or is off, it does not bring
        body = self.build(explicit=["kazoo-desk"], playground="welcome", containers={**CORE, **healthy("kazoo")})
        self.assertEqual(next(a for a in body["addons"] if a["id"] == "kazoo-desk")["brings"], [])
        body = self.build(containers={})
        self.assertEqual([g["brings"] for g in body["platform"]["groups"]], [[row("group", "databases",
                                                                                     "Databases")], []])

    # --- Items and the rest of the body ----------------------------------------------------------------------------

    def test_job_marks_what_it_starts_and_stops(self):
        events = job("Start Events", start=[["addon", "kazoo"], ["platform", None]], stop=[["addon", "jade"]],
                     key="setup:events", progress=0.25, containers={"lab-kazoo": 1}, jid="j3")
        body = self.build(explicit=["kazoo-desk", "jade"], containers={**CORE, **healthy("jade")}, job=events)
        found = {item["id"]: item["state"] for item in body["addons"]}
        self.assertEqual(found["kazoo"], {"key": "starting", "label": "Starting"})
        self.assertEqual(found["jade"], {"key": "stopping", "label": "Stopping"})
        self.assertEqual(found["kazoo-desk"]["key"], "stopped")
        self.assertEqual(body["platform"]["state"]["key"], "starting")
        self.assertEqual([group["state"]["key"] for group in body["platform"]["groups"]], ["starting", "starting"])
        self.assertEqual(next(s for s in body["setups"] if s["id"] == "events")["state"]["key"], "starting")
        # What the job changes and what it said of each container are in the items, and how far it is beside the state while
        # it runs: the page gets the rest
        self.assertEqual(body["job"], {"id": "j3", "title": "Start Events", "for": "setup:events", "verb": "start", "done": False,
                                       "ok": None})
        ended = {**events, "done": True, "ok": True, "progress": 1}
        self.assertEqual(sorted(self.build(explicit=["kazoo-desk", "jade"], job=ended)["job"]),
                         ["done", "for", "id", "ok", "progress", "title", "verb"], "once it ended, all of it")

    def test_how_far_a_running_job_is_is_not_in_the_state_which_changes_only_with_the_lab(self):
        # Its progress, that of its phases and its last line move at every byte a pull brings: they come beside the state
        # (Job.live, api.state), and the state built at two moments of a pull is the same
        running = {**job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo", progress=0.1),
                   "phases": [{"name": "Pull", "started": 1.0, "progress": 0.1}], "detail": "Image a Pulling"}
        later = {**running, "progress": 0.4, "phases": [{"name": "Pull", "started": 1.0, "progress": 0.4}],
                 "detail": "Image b Pulling"}
        first, second = (self.build(explicit=["kazoo"], job=one) for one in (running, later))
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["job"]["phases"], [{"name": "Pull", "started": 1.0}])
        self.assertEqual(next(a for a in first["addons"] if a["id"] == "kazoo")["progress"], "job")

    def test_a_container_that_just_started_does_not_change_the_state_at_each_second_of_a_running_job(self):
        # A Start waits for its containers' health checks; Docker words their age to the second for their first minute
        running = {**job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo", progress=0.6),
                   "phases": [{"name": "Start", "started": 1.0, "progress": 0.6}], "detail": "Container kazoo Started"}

        def at(age):
            return {**CORE, "kazoo": container(health="starting", status=f"Up {age} (health: starting)"),
                    "seed": container("exited", "", 0, f"Exited (0) {age} ago")}

        bodies = {json.dumps(self.build(explicit=["kazoo"], job=running, containers=at(age)), sort_keys=True)
                  for age in ("Less than a second", "1 second", "3 seconds", "4 seconds", "59 seconds")}
        self.assertEqual(len(bodies), 1, "the state changed at each second while the job runs")
        self.assertEqual(json.loads(bodies.pop())["services"]["kazoo"]["uptime"], "< 1 min")

    def test_what_a_job_stops_keeps_stop_and_says_nothing_else(self):
        # The on file no longer lists it and its containers still run: neither "started outside" nor a Start
        stopping = job("Stop Kazoo", stop=[["addon", "kazoo"]], key="addon:kazoo", jid="j5")
        body = self.build(containers={**CORE, **healthy("kazoo", "kazoo-ui")}, job=stopping)
        kazoo = next(a for a in body["addons"] if a["id"] == "kazoo")
        self.assertEqual((kazoo["state"]["key"], kazoo["note"], kazoo["action"]["do"]), ("stopping", "", "stop"))
        # Nor does another row say that its start would stop it
        kiosk = next(a for a in body["addons"] if a["id"] == "kiosk")
        self.assertEqual((kiosk["note"], kiosk["action"]["label"]), ("", "Start"))

    def test_on_without_running_offers_turn_off_as_well(self):
        # Its button starts it; its menu turns it off, the row's Stop
        down = {**CORE, **stopped("kazoo", "kazoo-ui")}
        kazoo = self.item("addon", "kazoo", explicit=["kazoo"], containers=down)
        self.assertEqual(kazoo["action"]["do"], "start")
        # The gateway runs with its settings: it restarts, and that is said first
        self.assertEqual(kazoo["secondary"], {"do": "stop", "label": "Turn off", "enabled": True, "reason": None,
                                              "dialog": {"starts": [], "stops": [], "turns_off": [KAZOO], "keeps": [],
                                                         "restarts": [GATEWAY]}})
        # It asks first like any stop that takes something else, and waits like any action
        jade = job("Start Jade", start=[["addon", "jade"]], key="addon:jade", jid="j6")
        kazoo = self.item("addon", "kazoo", explicit=["kazoo-desk"], containers=down, job=jade)
        self.assertEqual((kazoo["secondary"]["label"], kazoo["secondary"]["enabled"], kazoo["secondary"]["reason"]),
                         ("Turn off", False, "Wait for Start Jade"))
        self.assertEqual(kazoo["secondary"]["dialog"]["turns_off"], [KAZOO, CONSOLE])
        outside = self.item("addon", "kiosk", containers={**CORE, **healthy("kiosk")})
        self.assertEqual((outside["action"]["do"], outside["secondary"]["do"]), ("start", "stop"))
        events = self.item("setup", "events", explicit=["kazoo-desk"], containers=down)
        self.assertEqual((events["action"]["do"], events["secondary"]["label"]), ("start", "Turn off"))
        # A setup some add-ons of which are on: its menu turns them off, asking first
        observability = self.item("setup", "observability", explicit=["jade"], containers={**CORE,
                                                                                             **healthy("jade")})
        self.assertEqual((observability["secondary"]["label"], observability["secondary"]["dialog"]["stops"]),
                         ("Stop", [JADE]))
        # A playground too, on its row and its page: its apps do not run, what it brings and runs stops with it
        playground = self.item("playground", "welcome", playground="welcome",
                               containers={**CORE, **healthy("agent-lens")})
        self.assertEqual((playground["secondary"]["label"], playground["secondary"]["dialog"]["stops"],
                          playground["secondary"]["dialog"]["turns_off"]), ("Turn off", [INSPECTOR], [APPS]))
        # Nothing to stop for what is off, or runs: its primary action is the Stop
        self.assertIsNone(self.item("addon", "jade")["secondary"])
        self.assertIsNone(self.item("addon", "kazoo", explicit=["kazoo"],
                                    containers={**CORE, **healthy("kazoo", "kazoo-ui")})["secondary"])

    def test_job_on_a_group_or_a_service(self):
        both = job("Restart Gateway", start=[["service", "apim-gateway"]], stop=[["group", "databases"]],
                   key="service:apim-gateway", jid="j4")
        body = self.build(explicit=["kazoo"], containers={**CORE, **healthy("kazoo", "kazoo-ui")}, job=both)
        self.assertEqual([(group["id"], group["state"]["key"]) for group in body["platform"]["groups"]],
                         [("api-management", "starting"), ("databases", "stopping")])
        self.assertEqual(body["platform"]["state"]["key"], "starting")
        self.assertEqual(next(a for a in body["addons"] if a["id"] == "kazoo")["state"]["key"], "running")

    # --- Progress: how far the last job is with what it starts or stops, while it runs and until it is there --------

    def progress(self, **scenario):
        """The progress of the platform, of each of its groups and of every item: {(kind, id): progress}."""
        body = self.build(**scenario)
        found = {(item["kind"], item["id"]): item["progress"]
                 for kind in ("addons", "setups", "playgrounds") for item in body[kind]}
        found.update({("group", group["id"]): group["progress"] for group in body["platform"]["groups"]})
        return {**found, ("platform", "platform"): body["platform"]["progress"]}

    def test_progress_of_what_the_job_starts(self):
        # Start Events: the setup is what the job is for, it is as far as the job ("job": the page reads it beside the
        # state); an add-on, the mean of its containers the job waits for, each known from the start. Kazoo Desk, which the
        # job brought where it goes, runs: nothing of it is measured any more, though the job runs
        events = job("Start Events", start=[["addon", "kazoo"], ["addon", "kazoo-desk"]], key="setup:events",
                     progress=0.375, containers={"lab-kazoo": 0.5, "lab-kazoo-ui": 0, "lab-kazoo-desk": 1})
        console = {**CORE, **healthy("kazoo-desk")}
        found = self.progress(explicit=["kazoo-desk"], containers=console, job=events)
        self.assertEqual((found["addon", "kazoo"], found["addon", "kazoo-desk"], found["setup", "events"]),
                         (0.25, None, "job"))
        self.assertEqual(self.item("addon", "kazoo-desk", explicit=["kazoo-desk"], containers=console,
                                   job=events)["state"]["key"], "running")
        # What it leaves alone says nothing
        self.assertEqual({found[key] for key in found if key[1] not in ("kazoo", "kazoo-desk", "events")}, {None})
        # Nothing measured yet: what it is for is as far as the job all the same, which says None beside the state (once the
        # job ended, its own progress is in the state: test_jobs.Replay); without a job, nothing is measured
        self.assertEqual(self.progress(explicit=["kazoo-desk"], job={**events, "progress": None})["setup", "events"], "job")
        self.assertEqual(set(self.progress(explicit=["kazoo-desk"]).values()), {None})

    def test_what_the_job_brought_where_it_goes_is_there_while_it_runs(self):
        # Start the stack: MongoDB is ready, the gateway's health check runs. Databases runs, nothing of it measured;
        # API Management and the platform are on their way with the job
        stack = job("Start the stack", start=[["platform", "platform"]], key="platform", progress=0.6,
                    containers={"lab-mongodb": 1, "lab-apim-management-api": 1, "lab-apim-gateway": 0.5})
        checking = {**CORE, "apim-gateway": container(health="starting")}
        body = self.build(containers=checking, job=stack)
        self.assertEqual([(g["id"], g["state"]["key"], g["progress"]) for g in body["platform"]["groups"]],
                         [("api-management", "starting", 0.75), ("databases", "running", None)])
        self.assertEqual((body["platform"]["state"]["key"], body["platform"]["progress"]), ("starting", "job"))
        # A container it does not wait for is on its way until the job ends; one it stops, until it measured it there
        stopping = job("Stop Kazoo", stop=[["addon", "kazoo"], ["addon", "jade"]], key="addon:kazoo",
                       containers={"lab-kazoo": 1, "lab-kazoo-ui": 0.5})
        found = self.build(containers={**CORE, "kazoo": exited(143), "kazoo-ui": container(), "jade": exited(143)},
                           job=stopping)
        self.assertEqual({a["id"]: a["state"]["key"] for a in found["addons"] if a["id"] in ("kazoo", "jade")},
                         {"kazoo": "stopping", "jade": "stopping"})
        stopping["containers"]["lab-kazoo-ui"] = 1
        found = self.build(containers={**CORE, "kazoo": exited(143), "kazoo-ui": exited(143)}, job=stopping)
        self.assertEqual(next(a for a in found["addons"] if a["id"] == "kazoo")["state"]["key"], "off")

    def test_progress_of_the_platform_and_its_groups(self):
        # Stop the stack: the platform is what the job is for; a group, the mean of its containers
        stopping = job("Stop the stack", stop=[["platform", "platform"], ["addon", "kazoo"]], key="platform",
                       progress=0.6, containers={"lab-apim-gateway": 1, "lab-apim-management-api": 0,
                                                 "lab-mongodb": 0.5, "lab-kazoo": 1, "lab-kazoo-ui": 0})
        found = self.progress(explicit=["kazoo"], containers={**CORE, **healthy("kazoo", "kazoo-ui")}, job=stopping)
        self.assertEqual((found["group", "api-management"], found["group", "databases"], found["platform", "platform"],
                          found["addon", "kazoo"]), (0.5, 0.5, "job", 0.5))
        # One service of a group restarts with an add-on: the group and the platform say how far that one is
        kazoo = job("Start Kazoo", start=[["addon", "kazoo"], ["service", "apim-gateway"]], key="addon:kazoo",
                    progress=0.3, containers={"lab-kazoo": 0.2, "lab-kazoo-ui": 0.2, "lab-apim-gateway": 0.5})
        found = self.progress(explicit=["kazoo"], job=kazoo)
        self.assertEqual((found["addon", "kazoo"], found["group", "api-management"], found["group", "databases"],
                          found["platform", "platform"]), ("job", 0.5, None, 0.5))

    def test_progress_counts_what_the_job_waits_for(self):
        # A migration counts as soon as the job waits for it, before it was created; a playground that is what the
        # job is for has the job's own progress, its seed and its phases included
        welcome = job("Start Welcome", start=[["addon", "orchard"], ["playground", "welcome"]],
                       key="playground:welcome", progress=0.4,
                       containers={"lab-orchard": 1, "lab-orchard-migrate": 0, "lab-hello-app": 1, "lab-seed": 0})
        found = self.progress(explicit=["orchard"], playground="welcome", job=welcome)
        self.assertEqual((found["addon", "orchard"], found["playground", "welcome"]), (0.5, "job"))
        # What it does not wait for is not counted: Orchard restarted alone, its migration left as it is
        restart = job("Restart the stack", start=[["addon", "orchard"]], key="platform",
                      containers={"lab-orchard": 0.5})
        self.assertEqual(self.progress(explicit=["orchard"], job=restart)["addon", "orchard"], 0.5)

    def test_progress_goes_on_after_the_job_until_what_it_started_is_there(self):
        # Start Kazoo ended: Compose returned while the broker's health check runs; the gateway it restarted is healthy
        ended = job("Start Kazoo", start=[["addon", "kazoo"], ["service", "apim-gateway"]], key="addon:kazoo",
                    done=True, ok=True, progress=0.833,
                    containers={"lab-kazoo": 0.5, "lab-kazoo-ui": 1, "lab-apim-gateway": 1})
        checking = {**CORE, "kazoo": container(health="starting"), "kazoo-ui": container(health="")}
        item = self.item("addon", "kazoo", explicit=["kazoo"], containers=checking, job=ended)
        self.assertEqual((item["state"]["key"], item["progress"]), ("starting", 0.833))
        # The setup it belongs to is on its way with it: the mean of its containers
        found = self.progress(explicit=["kazoo"], containers=checking, job=ended)
        self.assertEqual({key: value for key, value in found.items() if value is not None},
                         {("addon", "kazoo"): 0.833, ("setup", "events"): 0.75})
        # Its state says it runs: it is there, nothing of it is measured any more
        self.assertEqual(set(self.progress(explicit=["kazoo"], containers={**CORE, **healthy("kazoo", "kazoo-ui")},
                                           job=ended).values()), {None})
        # What else the job started, still checking its health, goes on: the gateway, its group, the platform
        restarting = {**ended, "containers": {**ended["containers"], "lab-apim-gateway": 0.5}}
        gateway = {**CORE, **healthy("kazoo", "kazoo-ui"), "apim-gateway": container(health="starting")}
        found = self.progress(explicit=["kazoo"], containers=gateway, job=restarting)
        self.assertEqual((found["addon", "kazoo"], found["group", "api-management"], found["platform", "platform"]),
                         (None, 0.5, 0.5))
        # Every container it waits for is ready, or it settled short of that: one it waits for will not get there by
        # itself. What starts later is not its doing. One that failed or was canceled measures nothing more
        for over in ({**ended, "progress": 1}, {**ended, "settled": True}, {**ended, "ok": False}):
            self.assertEqual(set(self.progress(explicit=["kazoo"], containers=checking, job=over).values()), {None})

    def test_a_playground_whose_start_switches_the_versions_keeps_its_note_and_can_start(self):
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.12.20")
        welcome["requires"] = {"images": {"APIM_VERSION": "4.13.0-alpha.3"}}
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.5"}]
        item = self.item("playground", "welcome", containers=CORE)
        self.assertEqual((item["action"]["enabled"], item["action"].get("warns")), (True, True), "it starts, and does what its note says")
        self.assertEqual((item["note"], item["note_tone"]), (model.NEWER, "warn"))
        # Each image as it is and as it was tested, as when it only offers to switch
        self.assertEqual([(cause["kind"], cause["have"], cause["to"], cause["older"]) for cause in item["note_causes"]],
                         [("switch", "4.12.20", "4.13.0-alpha.5", False)])
        self.assertEqual([row["id"] for row in item["action"]["dialog"]["versions"]], ["APIM"])
        welcome["tested"] = []   # nothing to switch to: it waits for the Images page
        item = self.item("playground", "welcome", containers=CORE)
        self.assertEqual((item["action"]["enabled"], item["action"].get("warns"), item["note"]), (False, None, model.NEWER))

    def test_a_playground_on_versions_it_was_not_tested_with_offers_the_start_that_switches_them(self):
        welcome, gateway = self.project["playgrounds"]["welcome"], self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.13.0-alpha.3}", tag="4.13.0-alpha.3")
        welcome["requires"] = {"images": {"APIM_VERSION": "4.13.0-alpha.3"}}
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.5"}]
        item = self.item("playground", "welcome", containers=CORE)
        self.assertEqual((item["action"]["enabled"], item["action"].get("warns")), (True, True))
        # The note of a switch it must make, in the same words: each image as it is and as it was tested
        self.assertEqual((item["note"], item["note_tone"]), (model.UNTESTED, "warn"))
        self.assertEqual([(cause["kind"], cause["have"], cause["to"], cause["older"]) for cause in item["note_causes"]],
                         [("switch", "4.13.0-alpha.3", "4.13.0-alpha.5", False)])
        # What it offers is the dialog of the start that switches: the same steps as when it must
        offer = item["action"]["offer"]
        self.assertEqual(([row["id"] for row in offer["versions"]], [row["id"] for row in offer["starts"]]), (["APIM"], ["welcome", "agent-lens"]))
        self.assertNotIn("versions", item["action"]["dialog"] or {}, "its own start keeps the versions")
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.3"}]   # tested with them: nothing to say
        item = self.item("playground", "welcome", containers=CORE)
        self.assertEqual((item["note"], item["action"].get("warns"), item["action"].get("offer")), ("", None, None))

    def test_playground_counts_its_seed_once_it_ran(self):
        running = {**CORE, **healthy("agent-lens", "hello-app")}
        cases = ((None, "running", ""), (exited(0), "running", ""), (exited(1), "issue", "Seed exited (1)"))
        for seed, key, note in cases:
            containers = {**running, **({"seed": seed} if seed else {})}
            item = self.item("playground", "welcome", playground="welcome", containers=containers)
            self.assertEqual((item["state"]["key"], item["note"]), (key, note))

    def test_items(self):
        running = {**CORE, **healthy("kazoo", "kazoo-ui", "jade")}
        body = self.build(explicit=["kazoo", "jade"], containers=running)
        self.assertEqual([item["name"] for item in body["addons"]],
                         ["AI Agent Lens", "Hybrid gateway", "Jade", "Kazoo", "Kazoo Desk", "Kiosk",
                          "Orchard", "Redpanda"])
        addons = {item["id"]: item for item in body["addons"]}
        self.assertEqual(addons["kazoo-desk"]["needs"], [{"id": "kazoo", "name": "Kazoo"}])
        self.assertEqual(addons["jade"]["conflicts"], {"agent-lens": "both take the gateway's exporter"})
        # The core services it changes, by their labels, and those that what it needs changes: they come with it
        self.assertEqual([addons[uid]["restarts"] for uid in ("kazoo", "kazoo-desk", "kiosk")],
                         [["Gateway"], ["Gateway", "Management API"], []])
        self.assertEqual(addons["kazoo"]["links"], [
            {"label": "Kazoo", "url": "http://localhost/kazoo", "port": None, "up": True, "service": "kazoo"},
            {"label": "Kazoo", "url": "http://localhost/kazoo-ui", "port": None, "up": True, "service": "kazoo-ui"}])
        self.assertEqual((addons["hybrid"]["env_file"], addons["hybrid"]["settings"]),
                         (".lab/catalogs/fixture/addons/hybrid/.env", ["HYBRID_CLOUD_TOKEN"]))
        setup = next(item for item in body["setups"] if item["id"] == "observability")
        # A setup with one add-on turned on is partly on: off, it says which
        self.assertEqual((setup["on"], setup["up"], setup["state"]["key"], setup["note"], setup["note_title"]),
                         (False, True, "off", "Partly on", "On: Jade"))
        self.assertEqual(setup["members"], [{"id": "jade", "name": "Jade"}, {"id": "kiosk", "name": "Kiosk"}])
        # Its services are those of its add-ons: what the end-to-end run waits for once it starts it
        self.assertEqual(setup["services"], [*addons["jade"]["services"], *addons["kiosk"]["services"]])
        playground = body["playgrounds"][0]
        # What belongs to this machine says so, whatever its kind
        self.assertEqual([item["id"] for kind in ("addons", "setups", "playgrounds") for item in body[kind]
                          if item["personal"]], ["local/mine"])
        self.assertEqual(playground["addons"], [{"id": "agent-lens", "name": "AI Agent Lens"}])
        self.assertEqual(playground["setups"], [])
        # The setups it includes are named; one that does not load is left out, its problem says why
        self.project["playgrounds"]["welcome"]["setups"] = ["observability", "gone"]
        self.assertEqual(self.build()["playgrounds"][0]["setups"], [{"id": "observability", "name": "Observability"}])
        self.assertEqual([link["label"] for link in playground["links"]], ["Hello", "AI Agent Lens"])
        self.assertEqual((playground["apps"], playground["guide"], playground["created"]), (["hello-app"], "0123456789ab", []))

    def test_a_service_named_like_another_says_what_it_belongs_to(self):
        # Two gateways: in what an action restarts, each with its group
        self.project["services"]["hybrid-gateway"]["label"] = "Gateway"
        self.project["services"]["hybrid-gateway"]["owner"]["name"] = "Hybrid gateway"
        kazoo = self.item("addon", "kazoo")
        self.assertEqual((kazoo["restarts"], kazoo["action"]["dialog"]["restarts"]),
                         (["Gateway (API Management)"], [row("service", "apim-gateway", "Gateway (API Management)")]))

    def test_items_by_name_a_number_by_its_value(self):
        for uid, name in (("lab-10", "Lab 10"), ("lab-2", "Lab 2")):
            self.project["setups"][uid] = {**self.project["setups"]["inspect"], "id": uid, "name": name}
        self.assertEqual([setup["name"] for setup in self.build()["setups"]],
                         ["Events", "Inspect", "Lab 2", "Lab 10", "Mine", "Observability"])

    def test_legacy_consoles_come_last(self):
        # A playground's own consoles come before those of its add-ons, a legacy one after them all
        for link in self.project["links"]:
            link["legacy"] = link["service"] == "hello-app"
        playground = self.build()["playgrounds"][0]
        self.assertEqual([link["label"] for link in playground["links"]], ["AI Agent Lens", "Hello"])

    def test_a_playground_lists_the_platform_consoles_its_guide_opens(self):
        # After its own and its add-ons', in the order it names them
        owner = self.project["services"]["apim-management-api"]["owner"]["name"]
        for label, service in (("APIM Console", "apim-management-api"), ("Gateway", "apim-gateway")):
            self.project["links"].append({"label": label, "url": "http://localhost/x", "port": None,
                                          "service": service, "section": owner, "legacy": False})
        self.project["playgrounds"]["welcome"]["consoles"] = ["apim-management-api", "apim-gateway"]
        playground = self.build()["playgrounds"][0]
        self.assertEqual([link["label"] for link in playground["links"]],
                         ["Hello", "AI Agent Lens", "APIM Console", "Gateway"])

    def test_a_playground_declares_the_tabs_of_its_uis(self):
        # The consoles it lists, in order, each with the state of the service behind it
        owner = self.project["services"]["apim-management-api"]["owner"]["name"]
        self.project["links"].append({"label": "APIM Console", "url": "http://localhost/x", "port": None,
                                      "service": "apim-management-api", "section": owner, "legacy": False})
        welcome = self.project["playgrounds"]["welcome"]
        welcome.update(consoles=["hello-app", "apim-management-api", "apim-gateway"])
        healthy_state = {"key": "healthy", "label": "Healthy", "running": True}
        tabs = self.item("playground", "welcome", playground="welcome", containers={**CORE, **healthy("hello-app")})["tabs"]
        self.assertEqual(tabs, [
            {"id": "hello-app", "label": "Hello", "url": "http://localhost/hello-app", "service": "hello-app",
             "up": True, "state": healthy_state},
            {"id": "apim-management-api", "label": "APIM Console", "url": "http://localhost/x",
             "service": "apim-management-api", "up": True, "state": healthy_state}])
        # What does not run says so, in its state's words
        stopped_app = self.item("playground", "welcome", playground="welcome", containers=CORE)["tabs"]
        self.assertEqual([tab["up"] for tab in stopped_app], [False, True])
        self.assertEqual(stopped_app[0]["state"]["running"], False)
        self.assertEqual(stopped_app[1]["state"], healthy_state)
        # Without a list there is no tab, whatever the playground runs
        welcome.update(consoles=[])
        self.assertEqual(self.item("playground", "welcome", containers=CORE)["tabs"], [])

    def test_a_console_a_unit_puts_on_a_core_service_never_changes_its_address_and_is_the_tab_of_its_playground(self):
        # Gamma's console as the core gives it, and as the welcome and Kazoo would point it, each with its unit (`by`)
        owner = self.project["services"]["apim-management-api"]["owner"]["name"]

        def gamma(url, by=None):
            return {"label": "Gamma Console", "url": url, "port": "8086", "service": "apim-management-api", "section": owner,
                    "legacy": False, "console": "gamma", "labels": {"io.gravitee.console.gamma.name": "Gamma Console",
                                                                    "io.gravitee.console.gamma.url": url},
                    **({"by": by} if by else {})}
        self.project["links"] += [gamma("http://localhost:8086"),
                                  gamma("http://localhost:8086/welcome", {"kind": "playground", "id": "welcome", "name": "Welcome"}),
                                  gamma("http://localhost:8086/kazoo", {"kind": "addon", "id": "kazoo", "name": "Kazoo"})]
        self.project["playgrounds"]["welcome"]["consoles"] = ["apim-management-api"]

        def urls(body):
            return [link["url"] for link in body["links"] if link["service"] == "apim-management-api"]
        self.assertEqual(urls(self.build()), ["http://localhost:8086"])
        self.assertEqual(urls(self.build(playground="welcome")), ["http://localhost:8086"], "whatever is on")
        self.assertEqual(urls(self.build(explicit=["kazoo"])), ["http://localhost:8086"])
        self.assertEqual(sorted(self.build(explicit=["kazoo"])["links"][0]),
                         ["label", "legacy", "password", "port", "section", "service", "up", "url", "username"], "the page gets no layer")
        # The playground's own row says what runs; its tab is where it opens, before it is on too
        welcome = self.item("playground", "welcome")
        self.assertEqual([link["url"] for link in welcome["links"] if link["service"] == "apim-management-api"],
                         ["http://localhost:8086"])
        self.assertEqual([tab["url"] for tab in welcome["tabs"]], ["http://localhost:8086/welcome"])

    def test_a_service_listed_as_a_console_and_run_by_the_playground_is_listed_once_among_its_links(self):
        # Its own app is a console it lists: the list of what to open names it once, and has no flag of a tab
        self.project["playgrounds"]["welcome"]["consoles"] = ["hello-app"]
        links = self.item("playground", "welcome", playground="welcome", containers={**CORE, **healthy("hello-app")})["links"]
        self.assertEqual([link["label"] for link in links], ["Hello", "AI Agent Lens"])
        self.assertTrue(all("tab" not in link for link in links))

    def test_registry_label(self):
        # Where a group's images come from, as Images names it; nothing for Docker Hub
        for registry, label in ((None, None), ("graviteeio", None), ("docker.io/graviteeio", None),
                                ("graviteeio.azurecr.io", "Azure"), ("localhost:5000/team", "localhost:5000")):
            with self.subTest(registry=registry):
                self.assertEqual(model._registry_label(registry), label)
        self.project["groups"][0]["registry"] = "graviteeio.azurecr.io"
        self.assertEqual([group["registry_label"] for group in self.build()["platform"]["groups"]], ["Azure", None])

    def test_what_is_of_this_machine_says_what_holds_it(self):
        own(self.project)
        self.project["playgrounds"]["local/sandbox"]["setups"] = ["local/mine"]

        def held(kind, uid, **scenario):
            return self.item(kind, uid, **scenario)["held"]
        # What the playground includes is not deleted
        used = {"delete": "Used by Sandbox"}
        self.assertEqual((held("addon", "local/whoami"), held("setup", "local/mine")), (used, used))
        self.assertEqual(held("playground", "local/sandbox"), {"delete": ""})
        # On, the playground and the add-on it brings stay where they are; a setup only when the on file names it. Deleting is
        # turned off first while nothing of it runs, stopped first while it runs
        off = {"delete": "Turn it off first"}
        running = {"playground": "local/sandbox", "explicit": ["kiosk"]}
        self.assertEqual((held("playground", "local/sandbox", **running), held("addon", "local/whoami", **running)),
                         (off, off))
        stop = {"delete": "Stop it first"}
        running["containers"] = {**CORE, **healthy("whoami", "kiosk")}
        self.assertEqual((held("playground", "local/sandbox", **running), held("addon", "local/whoami", **running)),
                         (stop, stop))
        self.assertEqual(held("setup", "local/mine", **running), used)
        self.assertEqual(held("setup", "local/mine", explicit=["kiosk"], setups=["local/mine"]), off)
        self.assertEqual(held("setup", "local/mine", explicit=["kiosk"], setups=["local/mine"],
                              containers={**CORE, **healthy("kiosk")}), stop)
        # What is shared is changed in the project
        self.assertIsNone(held("addon", "kazoo"))

    def test_a_problem_names_files_relative_to_the_project(self):
        problem = model._problem({"file": ".lab/catalogs/local/addons/x/compose.yml",
                                  "message": f"open {paths.ROOT}/.lab/catalogs/local/addons/x/.env: permission denied"})
        self.assertEqual(problem["message"], "open .lab/catalogs/local/addons/x/.env: permission denied")

    def test_body(self):
        self.project["problems"] = [{"file": ".lab/catalogs/fixture/addons/x/compose.yml", "message": "broken"},
                                    {"file": "docker-compose.yml", "message": "x-gravitee must be a mapping"},
                                    {"file": ".lab/catalogs/local/setups/mine.yml", "message": "broken"},
                                    {"file": ".lab/catalogs/local/addons/x/compose.yml", "message": "broken"},
                                    {"file": ".lab/catalogs/local/playgrounds/x/compose.yml", "message": "broken"}]
        state = on(self.project, ["kazoo"])
        state["problems"] = [{"file": ".lab/on.yml", "message": "addons/y no longer exists"}]
        containers = {**CORE, "kazoo": container(status="Up 2 hours (healthy)"), "apim-gateway": exited(1),
                      "mongodb": dict(container(), ports=["27017"])}
        self.project["core_variables"] = [{"key": "GRAVITEE_LICENSE", "default": "", "required": False,
                                           "comment": "Enterprise license", "secret": True}]
        docker = {"error": None, "memory": 16 * 2 ** 30}
        body = model.build(self.project, state, containers, None, lambda kind, uid: None, None, docker,
                           {"welcome": [{"what": "APIs", "count": 2}]})
        json.dumps(body)   # nothing in it that the page cannot be sent
        self.assertEqual(sorted(body), ["addons", "blind", "categories", "docker", "job", "license", "links",
                                        "platform", "platforms", "playgrounds", "problems", "products", "running",
                                        "services", "setups", "unread", "wait"])
        # A file the editor opens can be repaired from where the problem shows; the on file opens too
        self.assertEqual(body["problems"], [dict(problem, editable=True)
                                            for problem in self.project["problems"] + state["problems"]])
        self.assertEqual(body["running"], {"explicit": ["kazoo"]})
        self.assertEqual((body["docker"], body["wait"]), ({"error": None, "warning": None}, None))
        self.assertEqual(body["playgrounds"][0]["created"], [{"what": "APIs", "count": 2}])
        kazoo, ui = body["services"]["kazoo"], body["services"]["kazoo-ui"]
        self.assertEqual((kazoo["state"]["key"], kazoo["uptime"], kazoo["on"]), ("healthy", "2 h", True))
        self.assertEqual(sorted(kazoo), ["behind", "brings", "built", "container", "description", "image", "label", "name",
                                         "needs_license", "note", "note_file", "note_target", "note_title", "note_tone",
                                         "on", "ports", "recreates", "registry", "role", "running_tag", "state", "tag",
                                         "targets", "uptime"])
        # A playground's seed says so: its Re-seed runs it again, not the Stack
        self.assertEqual((kazoo["role"], body["services"]["seed"]["role"]), (None, "seed"))
        self.assertEqual((ui["state"]["key"], ui["uptime"], ui["on"]), ("absent", "", True))
        self.assertFalse(body["services"]["kiosk"]["on"])
        # What the container publishes, else what the service's file says
        self.assertEqual((body["services"]["mongodb"]["ports"], kazoo["ports"]), (["27017"], []))
        platform = body["platform"]
        self.assertEqual((platform["name"], platform["state"], platform["license_var"]),
                         ("Gravitee platform", {"key": "issue", "label": "Issue"}, "GRAVITEE_LICENSE"))
        # Its files, like a group's: the core's, with every setting of the core
        self.assertEqual((platform["file"], platform["env_file"], platform["settings"]),
                         ("docker-compose.yml", ".env", ["GRAVITEE_LICENSE"]))
        self.assertEqual([(g["id"], g["state"]["key"], g["note"], g["note_tone"]) for g in platform["groups"]],
                         [("api-management", "issue", "Gateway exited (1)", "err"), ("databases", "running", "", "")])
        self.assertEqual(sorted(platform), ["apply", "behind", "containers", "env_file", "file", "gateways", "groups",
                                            "license_var", "name", "progress", "reset_turns_off", "restart",
                                            "settings", "start", "state", "update"])
        self.assertEqual({link["service"]: link["up"] for link in body["links"] if link["section"] == "Kazoo"},
                         {"kazoo": True, "kazoo-ui": False})
        # In the server's order, the legacy ones last, each saying whether it is one
        self.assertEqual(sorted(body["links"][0]), ["label", "legacy", "password", "port", "section", "service", "up", "url", "username"])
        # Every row says the same things
        rows = [item for kind in ("addons", "setups", "playgrounds") for item in body[kind]]
        self.assertEqual({key for item in rows for key in ("note_target", "note_file", "secondary", "login")}
                         - set(rows[0]), set())


def made(config_hash="saved", image_id="sha256:saved", **more):
    """A container that runs, made from this configuration and running this image."""
    return {**container(**more), "config_hash": config_hash, "image_id": image_id}


# What is saved: what Compose would make each service from, and the image each tag names here
SAVED = {"hashes": {name: "saved" for name in ("apim-gateway", "apim-management-api", "mongodb", "kazoo", "kazoo-ui",
                                               "hello-app", "agent-lens")},
         "images": {"mongodb": "sha256:saved"}}
AS_SAVED = {name: made() for name in ("apim-gateway", "apim-management-api", "mongodb")}


class Behind(Built):
    """What runs behind what is saved: "Apply needed", and the Apply that makes it again."""

    def test_what_runs_behind_what_is_saved(self):
        # Another configuration, or another image than its tag names here (a tag that moved with a pull)
        containers = {**AS_SAVED, "apim-gateway": made("old"), "mongodb": made(image_id="sha256:old"),
                      "kazoo": made("old", state="exited", code=143), "orchard-migrate": made("old", health=""),
                      "kiosk": made("old")}
        # Not compared: what does not run (it takes what is saved as it starts), a one-shot, what Compose does not
        # read (Kiosk is off: no hash), what is not known
        self.assertEqual(model.behind(self.project, containers, SAVED), {"apim-gateway", "mongodb"})
        self.assertEqual(model.behind(self.project, containers, None), set())
        self.assertEqual(model.behind(self.project, {"apim-gateway": container()}, SAVED), set())
        # Kiosk is off and runs another image than its tag names: Apply would leave it as it is, so it is not said
        # to need one. Nor is anything while what Compose reads is not known
        saved = {"hashes": SAVED["hashes"], "images": {**SAVED["images"], "kiosk": "sha256:saved"}}
        self.assertEqual(model.behind(self.project, {"kiosk": made(image_id="sha256:old")}, saved), set())
        self.assertEqual(model.behind(self.project, containers, {"hashes": {}, "images": SAVED["images"]}), set())

    def test_a_service_that_joins_another_namespace_is_compared_by_its_image_only(self):
        # pid: service:<name>: Compose labels the container with the hash of a configuration that names the other
        # container by id, which is never the hash it gives the file. Its image is still compared
        self.project["services"]["mongodb"]["joins"] = True
        self.assertEqual(model.behind(self.project, {**AS_SAVED, "mongodb": made("old")}, SAVED), set())
        self.assertEqual(model.behind(self.project, {**AS_SAVED, "mongodb": made("old", image_id="sha256:old")}, SAVED),
                         {"mongodb"})

    def test_an_image_built_again_from_unchanged_sources_is_not_behind(self):
        # A build that changed nothing gives the tag another id for the same image: Compose keeps it running, and so
        # no Apply could make it again
        containers = {**AS_SAVED, "mongodb": made(image_id="sha256:old")}
        self.same.return_value = True
        self.assertEqual(model.behind(self.project, containers, SAVED), set())
        self.same.assert_called_once_with(self.project["services"]["mongodb"]["container"], "sha256:saved",
                                          "sha256:old")

    def test_a_service_says_it_with_the_tag_saved(self):
        # The Stack shows the tag it runs next to the one saved
        self.project["services"]["mongodb"].update(tag="8.3.12", image="mongo:8.3.12")
        containers = {**AS_SAVED, "mongodb": {**made("old"), "image": "mongo:8.3.11"},
                      "apim-gateway": {**made(), "image": "graviteeio/apim-gateway:4.12-debian"}}
        body = self.build(containers=containers, saved=SAVED)
        mongodb, gateway = body["services"]["mongodb"], body["services"]["apim-gateway"]
        self.assertEqual({key: mongodb[key] for key in ("tag", "running_tag", "behind", "note", "note_tone",
                                                        "note_title")},
                         {"tag": "8.3.12", "running_tag": "8.3.11", "behind": True, "note": "Apply needed",
                          "note_tone": "warn", "note_title": "8.3.12 is saved, 8.3.11 still runs."})
        self.assertEqual((gateway["behind"], gateway["note"], gateway["running_tag"]), (False, "", "4.12-debian"))
        # What does not run has no running tag
        self.assertIsNone(self.build(containers={}, saved=SAVED)["services"]["mongodb"]["running_tag"])

    def test_each_cause_says_why_in_its_row_its_group_and_the_head(self):
        # Another tag saved, the same tag on another image, a configuration that changed (its settings known, by name)
        self.project["services"]["mongodb"].update(tag="8.3.12", image="mongo:8.3.12")
        self.project["services"]["apim-management-api"].update(tag="4.13.0", image="g/api:4.13.0")
        containers = {**AS_SAVED, "mongodb": {**made("old"), "image": "mongo:8.3.11"},
                      "apim-management-api": {**made(image_id="sha256:old"), "image": "g/api:4.13.0"},
                      "apim-gateway": {**made("old"), "image": "g/gateway:4.12-debian"}}
        self.project["services"]["apim-gateway"]["tag"] = "4.12-debian"
        saved = {**SAVED, "images": {**SAVED["images"], "apim-management-api": "sha256:saved"}}
        body = self.build(containers=containers, saved=saved,
                          changed={"apim-gateway": {"settings": ["GRAVITEE_LICENSE"], "labels": False}})
        said = {name: body["services"][name]["note_title"] for name in ("mongodb", "apim-management-api", "apim-gateway")}
        self.assertEqual(said, {"mongodb": "8.3.12 is saved, 8.3.11 still runs.",
                                "apim-management-api": "Image 4.13.0 changed since it started.",
                                "apim-gateway": "Its settings changed since it started: GRAVITEE_LICENSE."})
        # A group says it for its services; the banner's rows and the head say it for all of them, one line for each
        # owner that names it
        management, databases = body["platform"]["groups"]
        self.assertEqual(management["note_title"],
                         "Management API: image 4.13.0 changed since it started; "
                         "Gateway: its settings changed since it started: GRAVITEE_LICENSE.")
        self.assertEqual(databases["note_title"], "8.3.12 is saved, 8.3.11 still runs.")
        self.assertEqual([row["reason"] for row in body["platform"]["behind"]],
                         ["API Management: Management API (image 4.13.0 changed since it started); "
                          "Gateway (its settings changed since it started: GRAVITEE_LICENSE).",
                          "Databases: 8.3.12 is saved, 8.3.11 still runs."])
        self.assertNotIn("reason", body["platform"])
        # The page draws these notes as the badge
        self.assertTrue(all(body["services"][name]["note_apply"] for name in said))
        self.assertTrue(management["note_apply"])

    def test_a_group_whose_every_service_is_behind_for_one_cause_is_named_by_its_name(self):
        for name in ("apim-gateway", "apim-management-api"):
            self.project["services"][name].update(tag="4.12.20", image=f"g/{name}:4.12.20")
        containers = {**AS_SAVED, **{name: {**made("old"), "image": f"g/{name}:4.13.0-alpha.4"}
                                     for name in ("apim-gateway", "apim-management-api")}}
        body = self.build(containers=containers, saved=SAVED)
        # On its own row the owner is not named again; on the head and the banner's row it is
        self.assertEqual(body["platform"]["groups"][0]["note_title"], "4.12.20 is saved, 4.13.0-alpha.4 still runs.")
        self.assertEqual([row["reason"] for row in body["platform"]["behind"]],
                         ["API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs."])

    def test_a_row_that_does_not_run_behind_has_no_badge_and_no_reason(self):
        body = self.build(containers=AS_SAVED, saved=SAVED)
        self.assertEqual(body["platform"]["behind"], [])
        self.assertNotIn("note_apply", body["services"]["mongodb"])

    def test_items_and_groups_say_apply_needed(self):
        containers = {**AS_SAVED, "apim-gateway": made("old"), "kazoo": made("old"), "kazoo-ui": made()}
        body = self.build(explicit=["kazoo"], containers=containers, saved=SAVED)
        kazoo = next(a for a in body["addons"] if a["id"] == "kazoo")
        self.assertEqual((kazoo["note"], kazoo["note_tone"], kazoo["note_title"]),
                         ("Apply needed", "warn", "Kazoo broker: its configuration changed since it started."))
        management = body["platform"]["groups"][0]
        self.assertEqual((management["note"], management["note_tone"], management["note_title"]),
                         ("Apply needed", "warn", "Gateway: its configuration changed since it started."))
        # The banner names what runs behind by group and add-on, each with why; the Stack head and the banner have its
        # Apply
        self.assertEqual(body["platform"]["behind"], [
            {**row("group", "api-management", "API Management"),
             "reason": "API Management: Gateway (its configuration changed since it started).",
             "causes": [{"kind": "config", "services": ["Gateway"]}], "count": 1},
            {**KAZOO, "reason": "Kazoo: Kazoo broker (its configuration changed since it started).",
             "causes": [{"kind": "config", "services": ["Kazoo broker"]}], "count": 1}])
        # The same as data, for the popover of a row: the cause, the services it is about when they are not all of the row's
        self.assertEqual((kazoo["note_causes"], management["note_causes"]),
                         ([{"kind": "config", "services": ["Kazoo broker"]}], [{"kind": "config", "services": ["Gateway"]}]))
        self.assertEqual(body["platform"]["apply"], {
            "needed": True, "restarts": [GATEWAY, KAZOO], "starts": [], "enabled": True, "reason": None,
            "dialog": {"starts": [], "stops": [], "turns_off": [], "keeps": [], "restarts": [GATEWAY, KAZOO]}})
        # The Stack's Start makes it again as it starts: it says so
        self.assertEqual(body["platform"]["start"]["restarts"], [GATEWAY, KAZOO])
        # A setup that is on says it too; one partly on says that first
        events = next(item for item in body["setups"] if item["id"] == "events")
        self.assertEqual(events["note"], "Partly on")
        self.assertEqual(next(item for item in self.build(explicit=["kazoo-desk"], containers={
            **containers, "kazoo-desk": made()}, saved=SAVED)["setups"] if item["id"] == "events")["note"],
            "Apply needed")

    def test_an_apply_says_what_goes_back_and_what_loses_its_license(self):
        # A tag saved older than the one that runs goes back with Apply (data a newer version wrote may not open in it); with no
        # license saved, what still runs one loses it: both are said in its dialog before the click
        behind = [{"kind": "group", "id": "api-management", "name": "API Management",
                   "causes": [{"kind": "tag", "saved": "4.12.20", "running": "4.13.0-alpha.4", "older": True, "services": []}]},
                  {"kind": "group", "id": "databases", "name": "Databases",
                   "causes": [{"kind": "tag", "saved": "9.5.2", "running": "9.5.3", "older": True, "services": ["Elasticsearch"]},
                              {"kind": "tag", "saved": "8.4.0", "running": "8.3.11", "older": False, "services": ["MongoDB"]}]}]
        c = {"project": self.project, "behind": {"apim-gateway"}, "unlicensing": ["apim-gateway", "mongodb"]}
        found = model._consequences(c, behind)
        self.assertEqual(found["goes_back"], [
            {"kind": "group", "id": "api-management", "name": "API Management 4.13.0-alpha.4 → 4.12.20"},
            {"kind": "group", "id": "databases", "name": "Elasticsearch 9.5.3 → 9.5.2"}], "a newer version saved is no going back")
        self.assertEqual(found["loses_license"], [row("group", "api-management", "API Management")], "what it makes again and reads a license")
        self.assertEqual(model._consequences({**c, "unlicensing": []}, []), {}, "nothing to say, no row")

    def test_stronger_notes_come_first(self):
        # An issue is said before; what the job changes is on its way, not behind
        containers = {**AS_SAVED, "apim-gateway": made("old"), "apim-management-api": exited(1)}
        group = self.build(containers=containers, saved=SAVED)["platform"]["groups"][0]
        self.assertEqual((group["note"], group["note_tone"]), ("Management API exited (1)", "err"))
        restarting = job("Restart the stack", start=[["platform", "platform"]], key="platform")
        body = self.build(containers={**AS_SAVED, "apim-gateway": made("old")}, saved=SAVED, job=restarting)
        self.assertEqual((body["platform"]["behind"], body["services"]["apim-gateway"]["note"]), ([], ""))
        # Nothing behind: no Apply
        body = self.build(containers=AS_SAVED, saved=SAVED)
        self.assertEqual((body["platform"]["behind"], body["platform"]["apply"]["needed"],
                          body["platform"]["start"]["dialog"]), ([], False, None))

    def test_the_apply_says_what_it_starts_again(self):
        # What failed starts again; what stopped, or has no container, stays as it is
        containers = {**AS_SAVED, "apim-gateway": made("old"), "kazoo": exited(1),
                      "kazoo-ui": exited(143)}
        apply = self.build(explicit=["kazoo"], containers=containers, saved=SAVED)["platform"]["apply"]
        self.assertEqual((apply["restarts"], apply["starts"]), ([GATEWAY], [KAZOO]))
        # Kazoo runs alone, the platform stopped: Apply makes it again as saved, as the Stack's Start would
        body = self.build(explicit=["kazoo"], containers={"kazoo": made("old")}, saved=SAVED)
        apply = body["platform"]["apply"]
        self.assertEqual((apply["needed"], apply["restarts"], apply["starts"]), (True, [KAZOO], []))
        self.assertEqual(([found["id"] for found in body["platform"]["behind"]], body["platform"]["start"]["restarts"]),
                         (["kazoo"], [KAZOO]))
        # While nothing of what is on runs, it does nothing: what was saved applies at the next start; no Apply
        body = self.build(explicit=["kazoo"], containers={"kazoo": exited(143)}, saved=SAVED)
        self.assertEqual(body["platform"]["apply"]["needed"], False)
        # It waits like any action
        busy = job("Start Jade", start=[["addon", "jade"]], key="addon:jade")
        apply = self.build(containers={**AS_SAVED, "apim-gateway": made("old")}, saved=SAVED, job=busy)
        self.assertEqual((apply["platform"]["apply"]["enabled"], apply["platform"]["apply"]["reason"]),
                         (False, "Wait for Start Jade"))

    def test_an_item_s_start_makes_again_what_of_it_runs_behind(self):
        # Kazoo on, its broker runs behind, Kazoo UI was stopped: its Start starts Kazoo UI and makes the broker
        # again as saved, and asks first
        containers = {**AS_SAVED, "kazoo": made("old"), "kazoo-ui": exited(143)}
        body = self.build(explicit=["kazoo"], containers=containers, saved=SAVED)
        kazoo = next(a for a in body["addons"] if a["id"] == "kazoo")
        plan = layers.plan(self.project, on(self.project, ["kazoo"]), model.up(self.project, containers), "addon",
                           "kazoo", "start", behind={"kazoo"})
        self.assertEqual((plan["restarts"], plan["services"]["start"]),
                         ([row("service", "kazoo", "Kazoo broker")], ["kazoo", "kazoo-ui"]))
        self.assertEqual(kazoo["action"]["do"], "stop")   # it runs partly: its Stop, its menu has nothing else
        # Nothing behind: the start names only what does not run
        plan = layers.plan(self.project, on(self.project, ["kazoo"]), model.up(self.project, containers), "addon",
                           "kazoo", "start")
        self.assertEqual((plan["restarts"], plan["services"]["start"]), ([], ["kazoo-ui"]))

    def test_what_the_stack_s_starts_make_again(self):
        # A group's, a service's, an add-on's containers: what of them and of what they depend on runs behind
        self.project["services"]["kazoo-ui"]["depends_on"] = ["kazoo"]
        containers = {**AS_SAVED, "apim-gateway": made("old"), "kazoo": made("old"), "kazoo-ui": exited(143)}
        body = self.build(explicit=["kazoo"], containers=containers, saved=SAVED)
        self.assertEqual(body["platform"]["groups"][0]["recreates"], [GATEWAY])
        self.assertEqual(body["services"]["kazoo-ui"]["recreates"], [KAZOO])   # an add-on's, by its name
        kazoo = next(a for a in body["addons"] if a["id"] == "kazoo")
        self.assertEqual(kazoo["recreates"], [KAZOO])
        self.assertEqual(body["playgrounds"][0]["recreates"], [])


ROW = ("origin", "readonly", "display_file", "blocked", "needs_catalogs", "compat", "twin", "uses_machine")   # what an item says of its place
STOP = "Mounts the Docker socket"


class Catalogs(Built):
    """What the state says of an item of a catalog: where it comes from, whether it can start, what it lacks, its
    twin; and the note that says it, in the place the others leave it."""

    def mark(self, uid, kind="addon", **fields):
        self.project[kind + "s"][uid].update(fields)
        return self.project[kind + "s"][uid]

    def catalogs(self, **rows):
        self.project["catalogs"] = {ns: dict({"name": ns.title(), "kind": "git"}, **row) for ns, row in rows.items()}

    def test_an_item_of_nowhere_says_nothing_of_a_place(self):
        item = self.item("addon", "kazoo")
        self.assertEqual({key: item[key] for key in ROW}, {
            "origin": None, "readonly": False, "display_file": ".lab/catalogs/fixture/addons/kazoo/compose.yml", "blocked": None,
            "needs_catalogs": [], "compat": "ok", "twin": None, "uses_machine": []})

    def test_an_item_of_a_catalog_says_where_it_comes_from_and_what_it_uses_of_this_machine(self):
        origin = {"id": "acme-hub", "name": "Acme Hub", "version": "1.4.0"}
        uses = [{"service": "k3s", "what": ["runs privileged"], "why": "Runs containerd"}]
        self.mark("kazoo", catalog="acme-hub", origin=origin, readonly=True, display_file="addons/kazoo/compose.yml",
                  compat="new", uses_machine=uses, verdict={"state": "ok"}, twin={"id": "local/kazoo", "name": "Personal"})
        item = self.item("addon", "kazoo")
        self.assertEqual({key: item[key] for key in ROW}, {
            "origin": origin, "readonly": True, "display_file": "addons/kazoo/compose.yml", "blocked": None,
            "needs_catalogs": [], "compat": "new", "twin": {"id": "local/kazoo", "name": "Personal"}, "uses_machine": uses})
        self.assertNotIn("verdict", item, "the policy's whole verdict stays in the project")
        self.assertNotIn("catalog", item)
        json.dumps(item)

    def test_an_item_of_this_machine_says_its_path_in_the_local_catalog_and_opens_its_own_file(self):
        """What a person reads is never the place of the local catalog on the disk; the file the editor opens is."""
        self.mark("kazoo", catalog="local", personal=True, file=".lab/catalogs/local/addons/kazoo/compose.yml",
                  display_file="addons/kazoo/compose.yml")
        item = self.item("addon", "kazoo")
        self.assertEqual((item["file"], item["display_file"]),
                         (".lab/catalogs/local/addons/kazoo/compose.yml", "addons/kazoo/compose.yml"))

    # --- The note ---------------------------------------------------------------------------------------------------

    def test_a_blocked_item_says_blocked_with_its_reasons_and_who_to_ask(self):
        self.catalogs(acme={"name": "Acme Hub"})
        self.mark("kazoo", catalog="acme", blocked={"reasons": [STOP, "Publishes a port to the network"]})
        item = self.item("addon", "kazoo")
        self.assertEqual((item["note"], item["note_tone"], item["note_title"], item["note_target"], item["note_file"]),
                         ("Blocked", "err", STOP, None, None))
        self.assertEqual(item["note_causes"], [{"kind": "blocked", "reasons": [STOP, "Publishes a port to the network"],
                                                "owner": "Acme Hub", "folder": False}])

    def test_a_blocked_item_of_a_folder_catalog_is_the_person_s_to_change(self):
        self.catalogs(mine={"name": "My catalog", "kind": "folder"})
        self.mark("kazoo", catalog="mine", blocked={"reasons": [STOP]})
        self.assertEqual(self.item("addon", "kazoo")["note_causes"],
                         [{"kind": "blocked", "reasons": [STOP], "owner": "My catalog", "folder": True}])

    def test_an_item_of_this_machine_that_brings_a_blocked_one_names_no_owner(self):
        self.catalogs(local={"name": "Personal", "kind": "machine"})
        own(self.project)
        self.mark("local/whoami", catalog="local", personal=True, blocked={"reasons": ["Needs Kazoo, which is blocked"]})
        cause = self.item("addon", "local/whoami")["note_causes"][0]
        self.assertEqual((cause["owner"], cause["folder"]), ("", False))

    def test_a_blocked_item_cannot_start_and_says_the_first_reason(self):
        self.mark("kazoo", blocked={"reasons": [STOP, "Publishes a port to the network"]})
        action = self.item("addon", "kazoo")["action"]
        self.assertEqual((action["do"], action["enabled"], action["reason"]), ("start", False, STOP))
        # An item that runs is stopped like any other, blocked or not
        running = self.item("addon", "kazoo", explicit=["kazoo"], containers={**CORE, **healthy("kazoo", "kazoo-ui")})
        self.assertEqual((running["action"]["do"], running["action"]["enabled"]), ("stop", True))

    def test_an_item_that_lacks_a_catalog_says_so_by_the_reason_of_the_first_it_lacks(self):
        need = {"id": "partner", "name": "Partner Bank", "url": "https://example.invalid/partner.git", "min": "2.0.0", "have": None}
        cases = [
            ("missing", "Needs a catalog", "warn", "Needs the catalog Partner Bank", None),
            ("downloading", "Downloading Partner Bank", "", "Downloading Partner Bank", "partner"),
            ("failed", "Needs Partner Bank", "warn", "Needs Partner Bank", "partner"),
            ("old", "Needs Partner Bank 2.0 or later", "warn", "Needs Partner Bank 2.0 or later", "partner"),
        ]
        for reason, text, tone, title, target in cases:
            with self.subTest(reason=reason):
                self.mark("kazoo", needs_catalogs=[dict(need, reason=reason)])
                item = self.item("addon", "kazoo")
                self.assertEqual((item["note"], item["note_tone"], item["note_title"], item["note_target"]),
                                 (text, tone, title, {"kind": "catalog", "id": target}))
                self.assertEqual(item["note_causes"], [{"kind": "catalog", "id": "partner", "name": "Partner Bank",
                                                        "min": "2.0.0", "reason": reason}])
                self.assertEqual((item["action"]["enabled"], item["action"]["reason"]), (False, title))

    def test_an_item_whose_unit_is_gone_from_a_catalog_names_the_unit(self):
        self.mark("kazoo", needs_catalogs=[{"id": "partner", "name": "Partner Bank", "min": None, "have": "1.0.0",
                                            "reason": "gone", "unit": "Ledger"}])
        item = self.item("addon", "kazoo")
        self.assertEqual((item["note"], item["note_target"]), ("Needs Ledger, which is no longer in Partner Bank",
                                                               {"kind": "catalog", "id": "partner"}))
        self.assertEqual(item["note_causes"][0]["unit"], "Ledger")

    def test_every_catalog_it_lacks_is_a_cause(self):
        self.mark("kazoo", needs_catalogs=[{"id": "a-one", "name": "A", "min": None, "reason": "missing"},
                                           {"id": "b-two", "name": "B", "min": "1.0.0", "reason": "old"}])
        self.assertEqual([(cause["id"], cause["reason"]) for cause in self.item("addon", "kazoo")["note_causes"]],
                         [("a-one", "missing"), ("b-two", "old")])

    def test_what_the_catalogs_say_comes_after_a_failure_and_an_issue_and_before_the_rest(self):
        blocked = {"reasons": [STOP]}
        need = [{"id": "partner", "name": "Partner Bank", "min": None, "reason": "missing"}]
        self.mark("hybrid", blocked=blocked, needs_catalogs=need)
        # Settings it lacks, which come after
        self.assertEqual(self.note("addon", "hybrid", missing=["hybrid"]), ("Blocked", "err"))
        self.mark("hybrid", blocked=None)
        self.assertEqual(self.note("addon", "hybrid", missing=["hybrid"]), ("Needs a catalog", "warn"))
        self.mark("hybrid", needs_catalogs=[])
        self.assertEqual(self.note("addon", "hybrid", missing=["hybrid"]), ("Needs settings", "warn"))
        # A failure and an issue come first
        self.mark("kazoo", blocked=blocked, needs_catalogs=need)
        failure = {("addon", "kazoo"): failed(error="boom")}
        self.assertEqual(self.note("addon", "kazoo", failures=failure), ("Failed", "err"))
        issue = {**CORE, "kazoo": exited(1), "kazoo-ui": container()}
        self.assertEqual(self.note("addon", "kazoo", explicit=["kazoo"], containers=issue), ("Kazoo broker exited (1)", "err"))
        # And the item that waits for a job says nothing
        self.assertEqual(self.note("addon", "kazoo", job=job("Start Kazoo", start=[["addon", "kazoo"]], key="addon:kazoo")),
                         ("", ""))

    def test_what_blocks_an_item_waits_for_the_settings_in_the_reason_of_its_start(self):
        self.mark("hybrid", needs_catalogs=[{"id": "partner", "name": "Partner Bank", "min": None, "reason": "missing"}])
        self.assertEqual(self.item("addon", "hybrid", missing=["hybrid"])["action"]["reason"], "Needs the catalog Partner Bank")
        self.mark("hybrid", needs_catalogs=[], blocked={"reasons": [STOP]})
        self.assertEqual(self.item("addon", "hybrid", missing=["hybrid"])["action"]["reason"], STOP)

    def test_a_personal_item_that_a_catalog_holds_too_says_so(self):
        own(self.project)
        self.mark("local/whoami", personal=True, twin={"id": "whoami", "name": "Acme Hub"})
        item = self.item("addon", "local/whoami")
        self.assertEqual((item["note"], item["note_tone"], item["note_target"]), ("Also in Acme Hub", "", {"kind": "addon", "id": "whoami"}))
        self.assertEqual(item["twin"], {"id": "whoami", "name": "Acme Hub"})
        # The catalog's side has the twin and no note: its Start says why it waits (the layers' own reason)
        self.mark("kazoo", twin={"id": "local/kazoo", "name": "Personal"})
        self.assertEqual(self.item("addon", "kazoo")["note"], "")

    def test_the_license_it_needs_is_said_before_the_twin(self):
        own(self.project)
        self.mark("local/whoami", personal=True, twin={"id": "whoami", "name": "Acme Hub"}, requires={"license": ["feature-1"]})
        item = self.item("addon", "local/whoami", license_status=self.license(), features=set())
        self.assertEqual(item["note"], "Needs a license")

    # --- The body ---------------------------------------------------------------------------------------------------

    def test_the_gateway_of_each_kind_of_platform_is_in_the_platform_when_the_project_chose_one(self):
        self.assertIsNone(self.build()["platform"]["gateways"], "a project that did not choose says nothing")
        self.project["platform"] = {"gateways": {"cloud": "hybrid"}}
        self.assertEqual(self.build()["platform"]["gateways"], {"cloud": "hybrid"})

    def test_what_the_catalogs_and_the_first_start_say_is_in_the_body_as_it_is_given(self):
        body = self.build()
        self.assertFalse({"git", "catalogs", "content"} & set(body), "left out when not given")
        given = {"git": model.git_state((2, 43, 2)), "catalogs": [{"id": "acme-hub"}],
                 "content": {"key": "ready", "line": "", "code": None, "progress": None, "address": "", "offer": None}}
        body = self.build(**given)
        self.assertEqual({key: body[key] for key in given}, given)
        json.dumps(body)

    def test_git_is_said_as_the_page_reads_it(self):
        self.assertEqual(model.git_state((2, 43, 2)), {"available": True, "version": "2.43.2"})
        self.assertEqual(model.git_state(None), {"available": False, "version": None})

    def test_a_problem_of_a_file_of_a_git_catalog_is_not_one_the_editor_opens(self):
        self.catalogs(partner={"kind": "git"}, shared={"kind": "folder"})
        self.project["problems"] = [{"file": ".lab/catalogs/partner/addons/x/compose.yml", "message": "broken"},
                                    {"file": ".lab/catalogs/shared/addons/x/compose.yml", "message": "broken"},
                                    {"file": ".lab/catalogs/local/addons/x/compose.yml", "message": "broken"},
                                    {"file": ".lab/catalogs/fixture/addons/x/compose.yml", "message": "broken"}]
        self.assertEqual([problem["editable"] for problem in self.build()["problems"]], [False, True, True, True])


class Trouble(Built):
    """What keeps restarting, the license, docker-compose.yml that does not load, a registry refusing a pull."""

    def test_an_entry_of_the_on_file_that_compose_cannot_read_makes_what_reads_the_files_wait_on_it(self):
        # Its file broke outside the Lab, or is gone: every command that reads the on file would fail on it, so every
        # action that runs one says why before the click, as for a core that does not load
        line = "addons/local/mine/compose.yml does not load"
        state = on(self.project, ["kazoo"])
        body = model.build(self.project, {**state, "unread": line}, {**CORE}, None, lambda kind, uid: None, None,
                           {"error": None, "memory": None}, {})
        platform = body["platform"]
        self.assertEqual((body["wait"], body["unread"]), (None, line), "the page holds what reads the files on it")
        self.assertEqual([(platform[key]["enabled"], platform[key]["reason"]) for key in ("start", "apply", "restart", "update")],
                         [(False, line)] * 4)
        kazoo = next(item for item in body["addons"] if item["id"] == "kazoo")
        self.assertEqual((kazoo["action"]["do"], kazoo["action"]["enabled"], kazoo["action"]["reason"]), ("start", False, line))
        self.assertIsNone(self.build(explicit=["kazoo"])["unread"])

    def test_an_entry_of_the_on_file_that_is_gone_leaves_what_goes_by_project_name_usable(self):
        # Stop all, an item's Stop, Remove containers and Reset run `docker compose -p <name> ...`, which reads no
        # file: a gone entry does not make them fail, so the lab that runs can still be stopped from the page
        line = "addons/local/mine/compose.yml does not exist"
        state = on(self.project, ["kazoo"])
        body = model.build(self.project, {**state, "unread": line}, {**CORE, "kazoo": container(), "kazoo-ui": container()},
                           None, lambda kind, uid: None, None, {"error": None, "memory": None}, {})
        kazoo = next(item for item in body["addons"] if item["id"] == "kazoo")
        self.assertEqual(kazoo["action"]["do"], "stop")
        self.assertEqual((kazoo["action"]["enabled"], kazoo["action"]["reason"]), (True, None))
        # The page's Stop all, Remove containers and Reset wait on state.wait (paintBusy, platformMenu)
        self.assertIsNone(body["wait"])

    def test_what_keeps_restarting_gets_stop(self):
        # Its start would change nothing: up leaves running containers as they are
        restarting = container(health="", restarts=4)
        item = self.item("addon", "kazoo", explicit=["kazoo"], containers={**CORE, "kazoo": restarting,
                                                                           "kazoo-ui": container()})
        self.assertEqual((item["state"]["key"], item["note"], item["action"]["do"], item["action"]["label"]),
                         ("issue", "Kazoo broker keeps restarting", "stop", "Stop"))
        # One that exited, or has no container, is started again by it
        for other in (exited(1), None):
            containers = {**CORE, "kazoo": restarting, **({"kazoo-ui": other} if other else {})}
            item = self.item("addon", "kazoo", explicit=["kazoo"], containers=containers)
            self.assertEqual(item["action"]["do"], "start", other)
        # So is one that runs behind what is saved: its start makes it again
        item = self.item("addon", "kazoo", explicit=["kazoo"], saved=SAVED,
                         containers={**CORE, "kazoo": {**restarting, "config_hash": "old"}, "kazoo-ui": container()})
        self.assertEqual(item["action"]["do"], "start")

    def test_what_needs_a_license_says_what_it_lacks(self):
        # One word per meaning, each with its tone, each opening the core's settings
        cases = [(self.license("none"), set(), ("Needs a license", "warn", "Uses: feature-1")),
                 (self.license("unreadable"), set(), ("Needs a license", "warn", "The license cannot be read")),
                 (self.license("invalid"), set(), ("Needs a license", "warn",
                                                   "The license is not signed by GraviteeSource")),
                 (self.license("expired", date="1 Jan 2020"), set(),
                  ("License expired", "err", "Expired on 1 Jan 2020")),
                 (self.license("valid"), {"feature-0"}, ("Not in the license", "warn", "Uses: feature-1")),
                 (self.license("expiring"), {"feature-1"}, ("", "", ""))]
        for status, features, said in cases:
            with self.subTest(status=status["status"]):
                item = self.item("playground", "welcome", features=features, license_status=status)
                self.assertEqual((item["note"], item["note_tone"], item["note_title"]), said)
                self.assertEqual(item["note_file"], ".env" if said[0] else None)
        # What needs none says nothing of it
        self.assertEqual(self.note("addon", "kiosk", license_status=self.license("none")), ("", ""))

    def test_start_waits_for_a_license_that_all_of_the_unit_needs(self):
        welcome = self.project["playgrounds"]["welcome"]
        cases = [(self.license("none"), set(), "Needs a license"),
                 (self.license("invalid"), set(), "Needs a license"),
                 (self.license("expired", date="1 Jan 2020"), set(), "License expired"),
                 (self.license("valid"), {"feature-0"}, "Not in the license"),
                 (self.license("valid"), {"feature-0", "feature-1"}, None),
                 (self.license("valid"), None, None)]   # what the license allows is not known: nothing is refused
        for status, features, reason in cases:
            with self.subTest(status=status["status"], features=features):
                action = self.item("playground", "welcome", features=features, license_status=status)["action"]
                self.assertEqual((action["enabled"], action["reason"]), (reason is None, reason))
        # What needs a license for a part only starts, its note says which part
        welcome["requires"] = {"license": {"feature-1": "Step 4", "feature-2": "Step 2"}}
        action = self.item("playground", "welcome", features=set(), license_status=self.license("none"))["action"]
        self.assertEqual((action["enabled"], action["reason"]), (True, None))
        # What needs none starts
        self.assertTrue(self.item("addon", "kiosk", license_status=self.license("none"))["action"]["enabled"])

    def test_what_needs_a_license_says_the_names_of_its_packs(self):
        packs = {"Agent management": {"feature-1", "feature-2"}, "Agent mesh": {"feature-2"},
                 "Authorization management": {"feature-3"}}
        self.start(mock.patch.dict(license._known, packs=packs))
        self.project["playgrounds"]["welcome"]["requires"] = {"license": ["feature-1", "feature-2", "feature-3"]}
        item = self.item("playground", "welcome", license_status=self.license("none"))
        self.assertEqual((item["note"], item["note_title"]),
                         ("Needs a license", "Uses: Agent management, Authorization management"))
        # What it lacks: feature-2 alone is in the smaller pack
        item = self.item("playground", "welcome", features={"feature-1"}, license_status=self.license("valid"))
        self.assertEqual((item["note"], item["note_title"]),
                         ("Not in the license", "Uses: Agent mesh, Authorization management"))
        # What a plugin needs is said alike
        self.assertEqual(model.license_note(self.license("valid"), set(), ["feature-2", "feature-9"])["note_title"],
                         "Uses: Agent mesh, feature-9")

    def test_a_license_needed_for_a_part_of_a_playground_says_which(self):
        welcome = self.project["playgrounds"]["welcome"]
        welcome["requires"] = {"license": {"feature-1": "Step 4", "feature-2": "Step 2"}}
        cases = [(self.license("none"), set(), ("License needed for step 4 and step 2", "Uses: feature-1, feature-2")),
                 (self.license("invalid"), set(), ("License needed for step 4 and step 2",
                                                   "The license is not signed by GraviteeSource")),
                 (self.license("valid"), {"feature-2"}, ("License needed for step 4",
                                                         "Not in the license: feature-1")),
                 (self.license("expired", date="1 Jan 2020"), set(), ("License expired", "Expired on 1 Jan 2020")),
                 (self.license("valid"), {"feature-1", "feature-2"}, ("", ""))]
        for status, features, said in cases:
            with self.subTest(status=status["status"], features=features):
                item = self.item("playground", "welcome", features=features, license_status=status)
                self.assertEqual((item["note"], item["note_title"]), said)
        # One for the whole playground: the note says the license, not a part
        welcome["requires"] = {"license": {"feature-1": "Step 4", "feature-2": None}}
        self.assertEqual(self.note("playground", "welcome", license_status=self.license("none")),
                         ("Needs a license", "warn"))
        self.assertEqual(self.note("playground", "welcome", features={"feature-2"},
                                   license_status=self.license("valid")), ("License needed for step 4", "warn"))
        # A name stays as it is written
        welcome["requires"] = {"license": {"feature-1": "OpenFGA step", "feature-2": "Step 1", "feature-3": "Step 2"}}
        self.assertEqual(self.note("playground", "welcome", license_status=self.license("none"))[0],
                         "License needed for OpenFGA step, step 1 and step 2")

    def test_the_license_as_the_banner_and_the_chip_say_it(self):
        expired = self.license("expired", problem="The license expired on 1 Jan 2020: Enterprise features are off",
                               expires="2020-01-01T00:00:00+00:00", days_left=-2000)
        self.assertEqual(self.build(license_status=expired)["license"], {
            "status": "expired", "expires": "2020-01-01T00:00:00+00:00", "days_left": -2000,
            "problem": "The license expired on 1 Jan 2020: Enterprise features are off", "tone": "err",
            "applied": True, "reason": ""})
        self.assertEqual(self.build(license_status=self.license("none"))["license"]["tone"], "")
        self.assertEqual(self.build(license_status=self.license("expiring"))["license"]["tone"], "warn")
        self.assertIsNone(self.build()["license"])
        # Another license saved than the one the gateway runs: not applied yet
        running = {**CORE, "apim-gateway": {**container(), "variable": "old"}}
        found = self.build(license_status=self.license("valid"), containers=running)["license"]
        self.assertEqual((found["applied"], found["tone"]), (False, "warn"))
        self.assertEqual(found["reason"], "The license saved differs from the one that runs in Gateway.")
        running["apim-gateway"]["variable"] = "saved"
        self.assertTrue(self.build(license_status=self.license("valid"), containers=running)["license"]["applied"])
        # No license saved while one still runs: it says none is saved, not that another one is
        running["apim-gateway"]["variable"] = "old"
        found = self.build(license_status=self.license("none"), containers=running)["license"]
        self.assertEqual(found["reason"], "No license is saved, but Gateway still runs one.")

    def test_a_core_file_that_does_not_load_is_like_docker_down(self):
        # No state is said, actions wait with its reason
        self.project["core_loaded"] = False
        body = self.build(explicit=["kazoo"], containers={**CORE, "kazoo": exited(1)})
        self.assertEqual((body["blind"], body["wait"]), (True, "docker-compose.yml does not load"))
        kazoo = next(a for a in body["addons"] if a["id"] == "kazoo")
        self.assertEqual((kazoo["state"]["key"], kazoo["note"], kazoo["action"]["reason"]),
                         ("stopped", "", "docker-compose.yml does not load"))
        self.assertEqual(body["services"]["kazoo"]["state"]["key"], "absent")
        # Docker down says it first
        self.assertEqual(self.build(docker_error="down")["wait"], "Docker is not reachable")
        self.project["core_loaded"] = True
        self.assertEqual(self.build()["blind"], False)
        self.assertEqual(self.build(docker_error="down")["blind"], True)

    def test_a_registry_that_refused_the_pull(self):
        # Said in plain words; the row's menu offers Log in to that registry
        failures = {("playground", "welcome"): {**failed(error="Not logged in to Azure"),
                                                 "login": "graviteeio.azurecr.io"}}
        item = self.item("playground", "welcome", failures=failures)
        self.assertEqual((item["note"], item["note_tone"], item["note_title"], item["login"], item["failed_job"]),
                         ("Not logged in to Azure", "err", "", "graviteeio.azurecr.io", "j1"))
        self.assertIsNone(self.item("playground", "welcome")["login"])


class Platforms(Built):
    """What the state says of the platforms registered here and of where a playground deploys: nothing for a lab with no
    platform, which is as it was; for one with a platform, what it is given."""

    RECORD = {"id": "my-account", "name": "My Account", "kind": "cloud", "url": "https://eu.cloud.gravitee.io",
              "account": {"id": "acc", "hrid": "acme", "name": "Acme"}}
    HOTELS = {"deploys_to": ["cloud"], "cannot": {"other": "Needs Access Management"}}

    def setUp(self):
        super().setUp()
        cloud(self.project)
        # (the console of the service that the gateway add-on replaces is gone with it)
        self.project["links"] = [link for link in self.project["links"] if link["service"] in self.project["services"]]

    def test_no_platform_is_only_empty_keys(self):
        body = self.build()
        self.assertEqual(body["platforms"], [])
        welcome = next(item for item in body["playgrounds"] if item["id"] == "welcome")
        self.assertEqual((welcome["deploys_to"], welcome["deployed"]), ([], []))
        self.assertNotIn("cannot", welcome, "each place says why a playground cannot go there")
        self.assertEqual(next(item for item in body["addons"] if item["id"] == "kazoo")["gateway"], None)
        self.assertEqual(body["services"]["kazoo"]["targets"], {})
        # A state asked with the new keys left out is the state as it always was
        self.assertEqual(body, self.build(platforms=(), deployed=None))

    def test_the_platforms_are_listed_by_what_the_page_needs(self):
        body = self.build(platforms=[self.RECORD, {**self.RECORD, "id": "client-lab", "name": "Client lab"}])
        self.assertEqual(body["platforms"], [
            {"id": "my-account", "name": "My Account", "kind": "cloud", "url": "https://eu.cloud.gravitee.io", "account": {"hrid": "acme"}},
            {"id": "client-lab", "name": "Client lab", "kind": "cloud", "url": "https://eu.cloud.gravitee.io", "account": {"hrid": "acme"}}])

    def test_where_a_playground_can_go_is_what_the_project_derived_for_it(self):
        self.project["playgrounds"]["hotels"].update(self.HOTELS)
        hotels = self.item("playground", "hotels")
        self.assertEqual(hotels["deploys_to"], ["cloud"])
        self.assertEqual(self.item("playground", "hotels", platforms=[self.RECORD])["deploys_to"], ["cloud"])
        self.assertNotIn("cannot", hotels, "the reasons are the places' (places.places), judged against each environment")

    def test_what_a_playground_holds_in_a_place_is_what_it_is_given(self):
        held = [{"place": "my-account/fake-env-dev", "environment": "Development",
                 "created": [{"what": "APIs", "count": 3}]}]
        hotels = self.item("playground", "hotels", created={"hotels": [{"what": "APIs", "count": 12}]},
                           deployed={"hotels": held}, platforms=[self.RECORD])
        self.assertEqual((hotels["deployed"], hotels["created"]), (held, [{"what": "APIs", "count": 12}]))
        self.assertEqual(self.item("playground", "welcome", deployed={"hotels": held})["deployed"], [])

    def test_a_service_says_the_port_inside_it_for_each_it_publishes(self):
        self.project["services"]["kazoo"]["targets"] = {"9092": "9092", "8080": "80"}
        self.assertEqual(self.build()["services"]["kazoo"]["targets"], {"9092": "9092", "8080": "80"})
        # The project reads it from the ports of the configuration Compose runs, its own ports only
        written = {"ports": [{"target": 82}]}
        running = {"ports": [{"target": 82, "published": "8090"}, {"target": 8082, "published": "8091"}],
                   "labels": {}, "image": "x:1"}
        found = projects._service("gateway", written, running, {"kind": "addon", "id": "x"}, "lab")
        self.assertEqual((found["ports"], found["targets"]), (["8090"], {"8090": "82"}))
        self.assertEqual(projects._service("gateway", {}, {"image": "x:1"}, {}, "lab")["targets"], {})

    # --- The hybrid gateway add-on ------------------------------------------------------------------------------------

    def test_the_add_on_that_runs_the_gateway_says_so(self):
        hybrid = self.item("addon", "hybrid")
        self.assertEqual(hybrid["gateway"], {"platform": "cloud", "service": "apim-hybrid-gateway",
                                             "token": "HYBRID_CLOUD_TOKEN",
                                             "domains": ["localhost:8090", "apim-hybrid-gateway:8082"]})

    def test_it_needs_a_hybrid_gateway_once_a_platform_is_registered_and_its_token_is_the_setting_it_lacks(self):
        token = {"hybrid": ["HYBRID_CLOUD_TOKEN"]}
        lacking = self.item("addon", "hybrid", missing=token, platforms=[self.RECORD])
        self.assertEqual((lacking["note"], lacking["note_tone"], lacking["note_target"], lacking["action"]["do"]),
                         ("Needs a hybrid gateway", "", {"kind": "platform", "id": None}, "start"))
        self.assertEqual((lacking["action"]["enabled"], lacking["action"]["reason"]), (False, "Needs a hybrid gateway"))
        # Without a platform it needs settings, as any add-on does
        plain = self.item("addon", "hybrid", missing=token)
        self.assertEqual((plain["note"], plain["note_target"]), ("Needs settings", None))
        # A platform of another kind runs no gateway here
        other = self.item("addon", "hybrid", missing=token, platforms=[{**self.RECORD, "kind": "apim"}])
        self.assertEqual(other["note"], "Needs settings")
        # Another required setting that is saved changes nothing, one that is not keeps the note it had
        self.project["addons"]["hybrid"]["settings"].append(
            {"key": "OTHER", "default": None, "required": True, "comment": "", "secret": False})
        self.assertEqual(self.note("addon", "hybrid", missing=token, platforms=[self.RECORD])[0], "Needs a hybrid gateway")
        self.assertEqual(self.note("addon", "hybrid", missing={"hybrid": ["HYBRID_CLOUD_TOKEN", "OTHER"]}, platforms=[self.RECORD]),
                         ("Needs settings", "warn"))

    def test_an_add_on_that_runs_no_gateway_needs_settings_whatever_is_registered(self):
        self.project["addons"]["kazoo"]["settings"] = [
            {"key": "KAZOO_TOKEN", "default": None, "required": True, "comment": "", "secret": True}]
        self.assertEqual(self.note("addon", "kazoo", missing=["kazoo"], platforms=[self.RECORD]),
                         ("Needs settings", "warn"))

    def test_a_token_that_is_set_is_no_lack(self):
        # It is not in what the on file lacks: its own note and its own Start
        item = self.item("addon", "hybrid", platforms=[self.RECORD])
        self.assertEqual((item["note"], item["action"]["do"]), ("", "start"))

    # --- What an action says first --------------------------------------------------------------------------------------

    def test_the_dialog_of_a_start_in_a_place_says_the_place_too(self):
        # The gateway made again for another place: what it restarts, serves and stops serving, said first
        where = {"id": "my-account/development", "name": "Development (My Account)", "gateway": "hybrid", "here": False,
                 "serving": {"id": "client-lab/test", "name": "Test (Client lab)"}}
        running = up("hybrid", playgrounds=["hotels"], project=self.project)
        plan = layers.plan(self.project, on(self.project, ["hybrid"], "hotels"), running, "addon", "hybrid", "start",
                           place=where)
        action = model._action({"project": self.project, "wait": None, "unread": None}, plan)
        self.assertEqual(action["dialog"], layers.dialog(plan))
        self.assertEqual([found["name"] for found in action["dialog"]["serves"]], ["Development (My Account)"])
        self.assertEqual([found["name"] for found in action["dialog"]["stops_serving"]], ["Test (Client lab)"])
        # The plan of a start on This machine says what it always did
        local = layers.plan(self.project, on(self.project), up(project=self.project), "addon", "kazoo", "start")
        self.assertEqual(sorted(model._action({"project": self.project, "wait": None, "unread": None}, local)["dialog"]),
                         sorted(layers.ROWS))


class Unlicensed(Built):
    """The Alert Engine of the platform exits without a usable license: the Lab UI does not start it, and says so
    where it shows, without an issue."""

    def setUp(self):
        super().setUp()
        licensed(self.project, False)

    def engine(self, license_status=None, **scenario):
        """The state, its Alert Engine group and its service."""
        body = self.build(license_status=license_status or self.license(), **scenario)
        return body, next(g for g in body["platform"]["groups"] if g["id"] == "alert-engine"), \
            body["services"]["alert-engine"]

    def said(self, found):
        return found["note"], found["note_tone"], found["note_title"], found["note_file"]

    def test_it_is_off_with_a_note_and_the_platform_runs(self):
        body, group, service = self.engine()
        waiting = ("Needs a license", "warn", "Not started without one", ".env")
        self.assertEqual(group["state"], {"key": "off", "label": ""})
        self.assertEqual(self.said(group), waiting)
        self.assertEqual((service["on"], service["needs_license"], service["state"]["key"]), (False, True, "absent"))
        self.assertEqual(self.said(service), waiting)
        # Not counted: everything else runs, the platform runs, and has nothing to start
        self.assertEqual(body["platform"]["state"], {"key": "running", "label": "Running"})
        self.assertEqual((body["platform"]["start"]["needed"], body["platform"]["start"]["starts"]), (False, []))
        # The other services say nothing, and the others need no license
        self.assertEqual(self.said(body["services"]["kazoo"])[0], "")
        self.assertFalse(body["services"]["kazoo"]["needs_license"])
        self.assertEqual([g["state"]["key"] for g in body["platform"]["groups"]], ["running", "running", "off"])

    def test_the_note_says_why_the_license_does_not_do(self):
        cases = [(self.license("none"), "Not started without one"),
                 (self.license("unreadable"), "The license cannot be read"),
                 (self.license("invalid"), "The license is not signed by GraviteeSource"),
                 (self.license("expired", date="1 Jan 2020"), "Expired on 1 Jan 2020"),
                 (None, "Not started without one")]   # the core reads no license at all
        for status, title in cases:
            with self.subTest(status=(status or {}).get("status")):
                _, group, service = self.engine(status)
                self.assertEqual((group["note"], group["note_tone"], group["note_title"]),
                                 ("Needs a license", "warn", title))
                self.assertEqual(service["note_title"], title)

    def test_how_its_container_ended_is_no_issue(self):
        # It exited with an error, was made and never started: neither is something to fix, a license is
        for found in (exited(1), container("created", ""), exited(137, oom=True), container("dead")):
            with self.subTest(found=found):
                body, group, service = self.engine(containers={**CORE, "alert-engine": found})
                self.assertNotEqual(service["state"]["key"], "issue")
                self.assertEqual((group["state"]["key"], group["note"], body["platform"]["state"]["key"]),
                                 ("off", "Needs a license", "running"))
        _, _, service = self.engine(containers={**CORE, "alert-engine": exited(1)})
        self.assertEqual((service["state"], service["on"]),
                         ({"key": "stopped", "label": "Stopped", "running": False}, False))

    def test_nothing_running_is_stopped_not_an_issue(self):
        body, group, _ = self.engine(containers={})
        self.assertEqual((group["state"]["key"], body["platform"]["state"]["key"]), ("off", "stopped"))
        self.assertEqual([(g["id"], g["state"]["key"]) for g in body["platform"]["groups"]],
                         [("api-management", "stopped"), ("databases", "stopped"), ("alert-engine", "off")])
        # Its Start is the platform's, which asks for the platform alone
        self.assertEqual(body["platform"]["start"]["starts"], [PLATFORM])

    def test_a_container_that_runs_is_what_it_is(self):
        # The license was removed after it started: it keeps its state and its notes, an issue included
        body, group, service = self.engine(containers={**CORE, "alert-engine": container()})
        self.assertEqual((group["state"]["key"], group["note"], service["on"], service["state"]["key"]),
                         ("running", "", True, "healthy"))
        self.assertEqual(body["platform"]["state"]["key"], "running")
        body, group, service = self.engine(containers={**CORE, "alert-engine": container(health="", restarts=4)})
        self.assertEqual((group["state"]["key"], group["note"], group["note_tone"], service["on"]),
                         ("issue", "alert-engine keeps restarting", "err", True))
        self.assertEqual(body["platform"]["state"]["key"], "issue")

    def test_with_a_usable_license_it_is_a_service_as_any_other(self):
        self.project["license_usable"] = True
        for status in ("valid", "expiring"):
            with self.subTest(status=status):
                body, group, service = self.engine(self.license(status))
                self.assertEqual((group["state"]["key"], group["note"], service["on"], service["needs_license"]),
                                 ("stopped", "", True, True))
                self.assertEqual(body["platform"]["start"]["starts"], [row("group", "alert-engine", "Alert Engine")])
        body, group, _ = self.engine(self.license("valid"), containers={**CORE, "alert-engine": exited(1)})
        self.assertEqual((group["state"]["key"], body["platform"]["state"]["key"]), ("issue", "issue"))

    def test_a_job_that_starts_the_platform_does_not_start_it(self):
        running = job("Start the stack", start=[("platform", "platform")], key="platform")
        _, group, service = self.engine(job=running)
        self.assertEqual((group["state"]["key"], group["progress"], service["on"]), ("off", None, False))

    def test_it_does_not_count_in_a_group_with_other_services(self):
        # A group made of it and of others: they make its state, it keeps its own note on its line
        owner = {"kind": "platform", "id": "databases", "name": "Databases"}
        self.project["services"]["engine-2"] = {**_service("engine-2", owner), "needs_license": True}
        self.project["groups"][1]["services"].append("engine-2")
        body, _, _ = self.engine()
        databases = body["platform"]["groups"][1]
        self.assertEqual((databases["state"]["key"], databases["note"]), ("running", ""))
        self.assertEqual(body["services"]["engine-2"]["note"], "Needs a license")
        self.assertEqual(body["platform"]["state"]["key"], "running")


if __name__ == "__main__":
    unittest.main()
