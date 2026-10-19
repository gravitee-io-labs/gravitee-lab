"""stack: the commands the platform's actions run and what each waits for, on the project of test_api; how far a
group's, a service's, a restart's and an update's jobs are as Compose and Docker go. No Docker."""

import json
import os
import unittest

from lab_ui.docker import compose
from lab_ui.library import layout, playgrounds
from lab_ui import paths
from lab_ui.server import stack
from lab_ui.docker.jobs import Job, awaited

from .. import Case
from .test_api import PROJECT
from ..docker.test_jobs import Docker, container, progress, replay

DEFINED = PROJECT["services"]
# The services Docker checks the health of (test_jobs.Docker)
HEALTH_CHECKED = {"mongodb", "redis", "kafka", "jade"}


def migrate(**more):
    """A migration of the databases: a one-shot of the platform."""
    return {"role": None, "oneshot": True, "owner": {"kind": "platform", "id": "databases"}, "container": "gio-migrate",
            "image": "mongo:8.0.29", "built": False, "depends_on": [], "needs_license": False, **more}


def unlicensed(usable=False):
    """The project of test_api with an Alert Engine in a group of its own, which exits without a usable license: `usable`
    says whether there is one."""
    engine = migrate(oneshot=False, label="Alert Engine", container="gio-alert-engine", needs_license=True,
                     image="graviteeio/ae-engine:3.0.2", owner={"kind": "platform", "id": "alert-engine"})
    return {**PROJECT, "license_usable": usable, "services": {**DEFINED, "alert-engine": engine},
            "groups": [*PROJECT["groups"], {"id": "alert-engine", "name": "Alert Engine",
                                            "services": ["alert-engine"]}]}


class StackActions(Case):
    """The commands the platform's actions run."""

    COMPOSE = compose.COMPOSE
    BY_NAME = [*COMPOSE, "-p", "gravitee"]
    ON = [["platform", "platform"], ["addon", "kafka"], ["playground", "welcome"]]
    SERVICES = ["hello-app", "kafka", "kafka-ui", "mongodb", "redis"]   # not Jade, which is off, nor the seed
    # What a start waits for: what `up` names of what is on; a stop, every container of what is on, the seed too
    STARTS = awaited(DEFINED, start=["mongodb", "redis", "kafka", "kafka-ui", "hello-app"])
    STOPS = awaited(DEFINED, stop=["mongodb", "redis", "kafka", "kafka-ui", "hello-app", "welcome-seed"])
    # What a pull waits for: the images of what is on, but those built here
    PULLS = awaited({}, images=["mongo:8.0.29", "redis:8.10.1-alpine", "bitnamilegacy/kafka:3.9",
                                "provectuslabs/kafka-ui:latest"])

    def setUp(self):
        self.state = {"playground": "welcome", "explicit": ["kafka"], "effective": ["kafka"], "locked": None}
        self.patch(stack.layers, "current", return_value=self.state)
        self.patch(layout, "waiting", return_value=False)   # whatever tree this runs in: no old layout is being moved
        self.patch(stack.compose, "project_name", return_value="gravitee")
        self.patch(stack.compose, "ps", return_value={name: {"state": "running"}
                                                      for name in ("mongodb", "redis", "kafka", "kafka-ui")})
        self.saved = self.patch(stack.projects, "saved", return_value={"hashes": {}, "images": {}})
        self.jobs = self.patch(stack, "JOBS")
        # What the playgrounds recorded in a platform is read from the files of this machine, not of the test
        self.patch(stack.places, "deployed", return_value=[])

    def test_start_runs_what_is_on_without_the_seed(self):
        # What was started outside the Lab UI stays: nothing is removed as an orphan
        stack.action(PROJECT, "start")
        self.assertEqual(self.started(self.jobs), (
            "Start the stack", [[*self.COMPOSE, "up", "-d", *self.SERVICES]],
            {"key": "platform", "verb": "start", "changes": {"start": self.ON}, "waits": [self.STARTS]}))

    def test_what_needs_a_license_is_not_started_nor_waited_for_without_one(self):
        # It would exit again and again: the start names it not, waits for it not, and does not fail for it
        stack.action(unlicensed(), "start")
        title, steps, options = self.started(self.jobs)
        self.assertEqual((steps, options["waits"], options["changes"]),
                         ([[*self.COMPOSE, "up", "-d", *self.SERVICES]], [self.STARTS], {"start": self.ON}))
        # With a license it is named and waited for, as any other
        project = unlicensed(True)
        stack.action(project, "start")
        _, steps, options = self.started(self.jobs)
        self.assertEqual(steps, [[*self.COMPOSE, "up", "-d", "alert-engine", *self.SERVICES]])
        self.assertEqual(options["waits"], [awaited(project["services"], start=["alert-engine", *self.SERVICES])])

    def test_what_needs_a_license_is_not_in_an_update_nor_an_apply_without_one(self):
        stack.compose.ps.return_value = {name: {"state": "running"} for name in ("mongodb", "kafka")}
        stack.action(unlicensed(), "update")
        self.assertEqual(self.started(self.jobs)[1][1], [*self.COMPOSE, "up", "-d", "--no-deps", "kafka", "mongodb"])
        # Left running by a license since removed, behind what is saved: an apply makes it again as it was
        stack.compose.ps.return_value = {"alert-engine": {"state": "running", "config_hash": "old"},
                                         "mongodb": {"state": "running"}}
        self.saved.return_value = {"hashes": {"alert-engine": "saved"}, "images": {}}
        stack.action(unlicensed(), "apply")
        self.assertEqual(self.started(self.jobs)[1][-1], [*self.COMPOSE, "up", "-d", "--no-deps", "alert-engine"])

    def test_what_needs_a_license_is_not_named_with_own_changes_in_the_on_file_either(self):
        self.state["locked"] = ".lab/on.yml has your own changes"
        self.patch(stack.compose, "resolved", return_value={"alert-engine": {}, "mine": {}, "mongodb": {}})
        stack.action(unlicensed(), "start")
        self.assertEqual(self.started(self.jobs)[1], [[*self.COMPOSE, "up", "-d", "mine", "mongodb"]])
        stack.action(unlicensed(True), "start")
        self.assertEqual(self.started(self.jobs)[1], [[*self.COMPOSE, "up", "-d", "alert-engine", "mine", "mongodb"]])

    def test_update_pulls_then_makes_again_what_runs(self):
        # What changed is not known before the pull: what runs is named, Compose makes again what got another image,
        # built here or pulled. The playground's app does not run: it is not started
        stack.action(PROJECT, "update")
        pull = [*self.COMPOSE, "pull", "--ignore-buildable"]
        up = [*self.COMPOSE, "up", "-d", "--no-deps", "kafka", "kafka-ui", "mongodb", "redis"]
        runs = ["kafka", "kafka-ui", "mongodb", "redis"]
        self.assertEqual(self.started(self.jobs), ("Update the stack", [pull, up], {
            "key": "platform", "verb": "update", "changes": {"start": [["service", name] for name in runs]},
            "waits": [self.PULLS, awaited(DEFINED, start=runs)]}))
        # Redis stopped from the Stack stays stopped; the playground's app that failed starts again, as an apply starts it
        stack.compose.ps.return_value = {"mongodb": {"state": "running"},
                                         "redis": {"state": "exited", "exit_code": 143},
                                         "hello-app": {"state": "exited", "exit_code": 1}}
        stack.action(PROJECT, "update")
        self.assertEqual(self.started(self.jobs)[1][1], [*self.COMPOSE, "up", "-d", "--no-deps",
                                                         "hello-app", "mongodb"])

    def test_update_with_nothing_running_is_refused(self):
        # It would only pull: Pull images does that
        stack.compose.ps.return_value = {"mongodb": {"state": "exited", "exit_code": 143}}
        with self.assertRaisesRegex(RuntimeError, "^Nothing runs$"):
            stack.action(PROJECT, "update")
        self.jobs.start.assert_not_called()

    def test_stop_down_and_reset_go_by_project_name(self):
        # A job is named as the action that started it, and does what it says
        for name, title, command in (("stop", "Stop the stack", ["stop"]), ("down", "Remove containers", ["down"]),
                                     ("reset", "Reset the stack", ["down", "-v"])):
            stack.action(PROJECT, name)
            started, steps, options = self.started(self.jobs)
            self.assertEqual((started, steps[0], options),
                             (title, [*self.BY_NAME, *command],
                              {"key": "platform", "verb": name, "changes": {"stop": self.ON}, "waits": [self.STOPS]}))
            self.assertEqual(len(steps), 2 if name == "reset" else 1)

    def test_the_dashboards_start_all_and_stop_all_with_this_machine_are_the_platforms(self):
        self.patch(stack.core, "current", return_value={"place": "machine", "chosen": True, "environment": ""})
        stack.action(PROJECT, "start", for_core=True)
        self.assertEqual(self.started(self.jobs)[0], "Start the stack")
        stack.action(PROJECT, "stop", for_core=True)
        self.assertEqual(self.started(self.jobs)[0], "Stop the stack")

    def cloud_core(self, needed=True, here=False):
        """A Cloud core whose Start all starts the Hybrid gateway (a plan of its add-on, as layers.plan makes it) and Kafka's
        UI, which does not run; Start all reads the platform again first (`live`)."""
        self.patch(stack.core, "current", return_value={"place": "my-account/dev", "chosen": True, "environment": "Development"})
        self.patch(stack.core, "name", return_value="Development (My Account)")
        gateway = {"do": "start", "kind": "addon", "id": "hybrid", "starts": [{"kind": "addon", "id": "hybrid", "name": "Hybrid"}],
                   "stops": [], "turns_off": [], "keeps": [], "restarts": [], "serves": [], "blocked": None, "itself": {"addon:hybrid"},
                   "playground": "welcome", "explicit": ["hybrid", "kafka"], "services": {"remove": [], "start": ["hybrid"]}}
        found = {"gateway": gateway, "place": {"id": "my-account/dev", "name": "Development (My Account)", "here": here},
                 "gateway_id": "g1", "others": ["kafka-ui"], "needs_core": [], "needed": needed,
                 "dialog": {"starts": gateway["starts"] + [{"kind": "addon", "id": "kafka", "name": "Kafka"}]}}
        self.patch(stack.layers, "changes", return_value={"start": [["addon", "hybrid"]], "stop": []})
        self.patch(stack.layers, "waits", return_value=[awaited(DEFINED, start=["kafka"])])
        return self.patch(stack.core, "plan", return_value=found)

    def test_a_cloud_cores_start_all_starts_its_gateway_and_what_is_on_and_nothing_of_the_core(self):
        plan = self.cloud_core()
        written = self.patch(stack.layers, "write")
        swap = self.patch(stack.platforms, "swap_gateway")
        stack.action(PROJECT, "start", for_core=True, accept=[])
        self.assertTrue(plan.call_args.kwargs["live"], "the platform is read again at the click")
        title, steps, options = self.started(self.jobs)
        self.assertEqual(title, "Start all for Development (My Account)")
        self.assertEqual(steps[1:3], [[*self.COMPOSE, "up", "-d", "--no-deps", "hybrid"], [*self.COMPOSE, "up", "-d", "--no-deps", "kafka-ui"]])
        self.assertEqual(options["key"], "platform")
        self.assertEqual(options["changes"]["start"], [["addon", "hybrid"], ["service", "kafka-ui"]])
        self.assertEqual(len(options["waits"]), 2, "one wait for each Compose command")
        job = Job(title, [])
        steps[0](job)
        written.assert_called_once_with("welcome", ["hybrid", "kafka"], blocked={})   # no unit of this project is blocked
        swap.assert_called_once_with(PROJECT, "my-account", "g1")
        self.assertEqual(job.lines, ["Starts: Hybrid, Kafka", "Serves: Development (My Account)"])
        self.assertFalse(any(name in str(steps[1:3]) for name in ("mongodb", "redis")), "no service of the core")

    def test_a_cloud_cores_start_all_is_checked_as_the_project_it_reads_and_makes_its_folders(self):
        self.cloud_core()
        self.patch(stack.layers, "write")
        self.patch(stack.platforms, "swap_gateway")
        check, binds = self.patch(stack.layers, "check"), self.patch(stack.layers, "make_binds", return_value=[])
        stack.action(PROJECT, "start", for_core=True, accept=["addon:jade"])
        gateway = stack.core.plan.return_value["gateway"]
        check.assert_called_once_with(PROJECT, gateway, ["addon:jade"])
        steps = self.started(self.jobs)[1]
        steps[0](Job("Start all", []))
        binds.assert_called_once_with(PROJECT, gateway)

    def test_a_cloud_cores_start_all_adds_no_entry_for_a_unit_that_is_blocked(self):
        self.cloud_core()
        self.patch(stack.platforms, "swap_gateway")
        kafka = {**PROJECT["addons"]["kafka"], "file": "addons/kafka/compose.yml",
                 "blocked": {"reasons": ["Mounts the Docker socket"]}}
        project = {**PROJECT, "addons": {**PROJECT["addons"], "kafka": kafka}}
        written = self.patch(stack.layers, "write")
        stack.action(project, "start", for_core=True, accept=[])
        self.started(self.jobs)[1][0](Job("Start all", []))
        self.assertEqual(written.call_args.kwargs, {"blocked": {"addons/kafka/compose.yml": "Mounts the Docker socket"}})

    def test_a_cloud_cores_start_all_with_nothing_to_start_or_unaccepted_stops_is_refused(self):
        self.cloud_core(needed=False)
        with self.assertRaisesRegex(RuntimeError, "Everything runs"):
            stack.action(PROJECT, "start", for_core=True)
        self.jobs.start.assert_not_called()

    def test_a_cloud_cores_stop_all_stops_what_runs_of_it_and_leaves_the_core(self):
        self.cloud_core()
        self.patch(stack.core, "stopping", return_value=["kafka", "kafka-ui"])
        stack.action(PROJECT, "stop", for_core=True)
        self.assertEqual(self.started(self.jobs), ("Stop all for Development (My Account)", [[*self.BY_NAME, "stop", "kafka", "kafka-ui"]],
                                                    {"key": "platform", "verb": "stop", "changes": {"stop": [["service", "kafka"], ["service", "kafka-ui"]]},
                                                     "waits": [awaited(DEFINED, stop=["kafka", "kafka-ui"])]}))
        stack.core.stopping.return_value = []
        with self.assertRaisesRegex(RuntimeError, "Nothing runs"):
            stack.action(PROJECT, "stop", for_core=True)

    def test_reset_turns_the_playground_off_and_empties_what_seeds_recorded(self):
        # Its volumes gone, nothing any seed created is left: the playground is off, its next start seeds it
        written, forgotten = self.patch(stack.layers, "write"), self.patch(stack.playgrounds, "forget")
        stack.action(PROJECT, "reset")
        job = Job("Reset the stack", [self.started(self.jobs)[1][1]])
        job.run()
        written.assert_called_once_with(None, ["kafka"])
        self.assertEqual([call.args for call in forgotten.call_args_list], [("welcome",), ("local/sandbox",)])
        self.assertEqual(job.lines, ["Turned off Welcome"])
        # An on file with the user's own changes is not written: what the seeds recorded is emptied all the same
        self.state["locked"] = ".lab/on.yml has your own changes"
        written.reset_mock()
        stack.action(PROJECT, "reset")
        Job("Reset the stack", [self.started(self.jobs)[1][1]]).run()
        written.assert_not_called()
        self.assertEqual(forgotten.call_count, 4)

    def test_reset_says_which_record_it_could_not_empty(self):
        self.patch(stack.layers, "write")
        refused = {"welcome": False, "local/sandbox": True}
        self.patch(stack.playgrounds, "forget", side_effect=lambda pid: refused[pid])
        stack.action(PROJECT, "reset")
        job = Job("Reset the stack", [self.started(self.jobs)[1][1]])
        job.run()
        self.assertEqual(job.lines, ["Turned off Welcome", "Could not empty what Welcome recorded: its state folder leads out "
                                                         "of its folder"])

    def test_apply_is_refused_while_the_old_layout_is_moving_and_the_rest_of_the_stack_is_not(self):
        self.patch(layout, "waiting", return_value=True)
        with self.assertRaisesRegex(RuntimeError, "^Your settings are being moved. Try again in a moment.$"):
            stack.action(PROJECT, "apply")
        self.jobs.start.assert_not_called()
        for name in ("start", "stop", "down", "reset", "restart", "pull"):
            stack.action(PROJECT, name)
            self.assertEqual(self.jobs.start.call_count, 1, name)
            self.jobs.start.reset_mock()

    def test_reset_says_what_it_leaves_in_the_platforms(self):
        # What a seed created in a platform is still there, and still recorded: only This machine's record is emptied
        self.patch(stack.layers, "write")
        forgotten = self.patch(stack.playgrounds, "forget")
        platform = {"id": "my-account", "name": "My Account", "kind": "cloud", "url": "u", "account": {}}
        self.patch(stack.platforms, "read", return_value=[platform])
        held = {"welcome": [{"place": "my-account/dev", "environment": "Development", "created": []},
                            {"place": "gone/prod", "environment": "Production", "created": []}],
                "local/sandbox": [{"place": "my-account/test", "environment": "Test", "created": []}]}
        deployed = self.patch(stack.places, "deployed", side_effect=lambda pid, created: held.get(pid, []))
        stack.action(PROJECT, "reset")
        job = Job("Reset the stack", [self.started(self.jobs)[1][1]])
        job.run()
        self.assertEqual(job.lines, ["Turned off Welcome", "Stays: Welcome in Development (My Account)",
                                     "Stays: Welcome in Production (gone)", "Stays: Sandbox in Test (My Account)"])
        self.assertEqual(forgotten.call_count, 2, "their record is not among what is emptied")
        self.assertEqual({call.args[1] for call in deployed.call_args_list}, {stack.playgrounds.created})

    def test_a_reset_with_nothing_in_a_platform_says_what_it_always_did(self):
        self.patch(stack.layers, "write")
        self.patch(stack.playgrounds, "forget")
        self.patch(stack.places, "deployed", return_value=[])
        reads = self.patch(stack.platforms, "read", return_value=[])
        stack.action(PROJECT, "reset")
        job = Job("Reset the stack", [self.started(self.jobs)[1][1]])
        job.run()
        self.assertEqual(job.lines, ["Turned off Welcome"])
        reads.assert_not_called()   # nothing recorded, no platform to name

    def test_restart_restarts_what_runs_as_it_is(self):
        # Redis was stopped from the Stack, the playground's app failed, Kafka UI was never created: they stay so. Only
        # what runs is restarted, and only that shows as starting and is waited for
        stack.compose.ps.return_value = {"mongodb": {"state": "running"}, "kafka": {"state": "running"},
                                         "redis": {"state": "exited", "exit_code": 143},
                                         "hello-app": {"state": "exited", "exit_code": 1}}
        stack.action(PROJECT, "restart")
        self.assertEqual(self.started(self.jobs), (
            "Restart the stack", [[*self.COMPOSE, "restart", "kafka", "mongodb"]],
            {"key": "platform", "verb": "restart", "changes": {"start": [["service", "kafka"], ["service", "mongodb"]]},
             "waits": [awaited(DEFINED, start=["kafka", "mongodb"])]}))

    def test_restart_with_nothing_running_is_refused(self):
        # Compose would read an empty list as every service: what was stopped would start
        stack.compose.ps.return_value = {"mongodb": {"state": "exited", "exit_code": 143}}
        with self.assertRaisesRegex(RuntimeError, "^Nothing runs$"):
            stack.action(PROJECT, "restart")
        self.jobs.start.assert_not_called()

    def test_restart_with_own_changes_in_the_on_file_restarts_what_runs_of_what_compose_reads(self):
        # A service only the user's own files add is restarted, not waited for
        self.state["locked"] = ".lab/on.yml has your own changes"
        self.patch(stack.compose, "resolved", return_value={"mongodb": {}, "redis": {}, "mine": {}})
        stack.compose.ps.return_value = {"mongodb": {"state": "running"}, "mine": {"state": "running"},
                                         "redis": {"state": "exited", "exit_code": 143}}
        stack.action(PROJECT, "restart")
        _, steps, options = self.started(self.jobs)
        self.assertEqual((steps, options["waits"]), ([[*self.COMPOSE, "restart", "mine", "mongodb"]],
                                                     [awaited(DEFINED, start=["mongodb"])]))

    def test_pull(self):
        stack.action(PROJECT, "pull")
        self.assertEqual(self.started(self.jobs)[1:], ([[*self.COMPOSE, "pull", "--ignore-buildable"]],
                                                       {"key": "platform", "verb": "pull", "changes": None,
                                                        "waits": [self.PULLS]}))

    def test_own_changes_in_the_on_file_run_as_compose_reads_them(self):
        # Everything the files hold is named, what only the user's own on file adds too, but a seed, which its
        # Re-seed runs: Start and Update do not run it again. What it waits for is what is on that it names
        self.state["locked"] = ".lab/on.yml has your own changes"
        seed = {"labels": {"io.gravitee.role": "seed", "io.gravitee.name": "Seed"}}
        resolved = self.patch(stack.compose, "resolved", return_value={
            **{name: {} for name in self.SERVICES}, "mine": {"labels": {"io.gravitee.name": "Mine"}},
            "welcome-seed": seed})
        stack.action(PROJECT, "start")
        _, steps, options = self.started(self.jobs)
        resolved.assert_called_once_with([])
        self.assertEqual(steps, [[*self.COMPOSE, "up", "-d", "hello-app", "kafka", "kafka-ui", "mine", "mongodb",
                                  "redis"]])
        self.assertEqual(options["waits"], [self.STARTS])
        stack.action(PROJECT, "update")
        self.assertNotIn("welcome-seed", self.started(self.jobs)[1][1])

    # --- Apply: what runs behind what is saved is made again, what failed starts again; nothing else ---

    def apply(self, containers, project=PROJECT, hashes=None):
        """Applies with these containers, what is saved being `hashes`: the job's steps, and its other arguments."""
        stack.compose.ps.return_value = containers
        self.saved.return_value = {"hashes": hashes if hashes is not None else {"kafka": "saved"}, "images": {}}
        stack.action(project, "apply")
        _, steps, options = self.started(self.jobs)
        return steps, options

    def said(self, steps):
        """What the job of an apply says before it runs Compose, if it does."""
        job = Job("Apply changes", [step for step in steps if callable(step)])
        job.run()
        return job.lines

    def test_apply_recreates_what_runs_behind_and_starts_again_what_failed(self):
        # Databases stopped from the Stack, Kafka runs behind, Kafka UI as saved, the playground's app failed, a
        # migration ended: no orphan is removed, nothing else is named
        project = {**PROJECT, "services": {**DEFINED, "migrate": migrate()}}
        steps, options = self.apply({
            "mongodb": {"state": "exited", "exit_code": 143}, "redis": {"state": "running"},
            "migrate": {"state": "exited", "exit_code": 0}, "kafka": {"state": "running", "config_hash": "old"},
            "kafka-ui": {"state": "running", "config_hash": "ui"}, "hello-app": {"state": "exited", "exit_code": 1}},
            project)
        self.assertEqual(steps[1], [*self.COMPOSE, "up", "-d", "--no-deps", "hello-app", "kafka"])
        self.assertEqual(self.said(steps), ["Restarts: Kafka", "Starts: Welcome apps"])
        self.assertEqual(options, {"key": "platform", "verb": "apply",
                                   "changes": {"start": [["service", "hello-app"], ["service", "kafka"]]},
                                   "waits": [awaited(project["services"], start=["hello-app", "kafka"])]})

    def test_apply_does_nothing_while_nothing_of_what_is_on_runs(self):
        # Platform stopped, Kafka behind and running alone: it is made again, as saved
        stopped = {"state": "exited", "exit_code": 143}
        steps, options = self.apply({"mongodb": stopped, "redis": stopped,
                                     "kafka": {"state": "running", "config_hash": "old"}})
        self.assertEqual((self.said(steps), steps[1], options["changes"]),
                         (["Restarts: Kafka"], [*self.COMPOSE, "up", "-d", "--no-deps", "kafka"],
                          {"start": [["service", "kafka"]]}))
        # Nothing of what is on runs: what was saved applies at the next start
        steps, options = self.apply({"mongodb": stopped, "redis": stopped, "kafka": stopped})
        self.assertEqual((self.said(steps), options["changes"], options["waits"]),
                         (["Nothing runs: applied on next start"], {"start": []}, []))
        # Nothing at all: no container, nothing is created
        steps, _ = self.apply({})
        self.assertEqual(self.said(steps), ["Nothing runs: applied on next start"])

    def test_apply_creates_nothing_that_has_no_container(self):
        # Kafka UI was never created, the playground's app was removed: they are left as they are, "absent" like
        # "stopped"
        steps, _ = self.apply({"mongodb": {"state": "running"}, "redis": {"state": "running"},
                               "kafka": {"state": "running", "config_hash": "old"}})
        self.assertEqual(steps[1], [*self.COMPOSE, "up", "-d", "--no-deps", "kafka"])

    def test_apply_with_nothing_behind_says_so(self):
        steps, options = self.apply({"mongodb": {"state": "running"}, "kafka": {"state": "running",
                                                                              "config_hash": "saved"}})
        self.assertEqual((self.said(steps), options["waits"]), (["Nothing to apply"], []))

    def test_apply_starts_what_failed_only_with_what_it_needs_among_what_is_on(self):
        # The playground's app depends on Jade, which is off (an add-on's file makes it so, whether it is on or
        # not), and on Redis: Redis stopped, it would fail again alone; Redis running, it starts again
        app = {**DEFINED["hello-app"], "depends_on": ["jade", "redis"]}
        project = {**PROJECT, "services": {**DEFINED, "hello-app": app}}
        failed = {"mongodb": {"state": "running"}, "hello-app": {"state": "exited", "exit_code": 1}}
        steps, _ = self.apply({**failed, "redis": {"state": "exited", "exit_code": 143}}, project)
        self.assertEqual(self.said(steps), ["Nothing to apply"])
        steps, _ = self.apply({**failed, "redis": {"state": "running"}}, project)
        self.assertEqual(steps[1], [*self.COMPOSE, "up", "-d", "--no-deps", "hello-app"])

    def test_apply_names_what_runs_when_what_is_saved_is_not_known(self):
        # Compose could not say what it makes each service from: it decides what changed among what runs
        steps, _ = self.apply({"mongodb": {"state": "running"}, "kafka": {"state": "running"},
                               "redis": {"state": "exited", "exit_code": 143}}, hashes={})
        self.assertEqual(steps[1], [*self.COMPOSE, "up", "-d", "--no-deps", "kafka", "mongodb"])

    def test_apply_with_own_changes_in_the_on_file_names_what_compose_reads_in_it(self):
        # A service only the user's own files add is not the project's: it is not waited for
        self.state["locked"] = ".lab/on.yml has your own changes"
        resolved = self.patch(stack.compose, "resolved", return_value={"mongodb": {}, "mine": {}})
        steps, options = self.apply({"mongodb": {"state": "running"}, "mine": {"state": "running"}}, hashes={})
        resolved.assert_called_once_with([])
        self.assertEqual((steps[1], options["waits"]),
                         ([*self.COMPOSE, "up", "-d", "--no-deps", "mine", "mongodb"],
                          [awaited(DEFINED, start=["mongodb"])]))

    def test_what_an_apply_would_recreate(self):
        # Of what is saved; with what a change not saved yet makes Compose create; with what a change may recreate
        stack.compose.ps.return_value = {"mongodb": {"state": "running"},
                                         "kafka": {"state": "running", "config_hash": "saved"},
                                         "kafka-ui": {"state": "running", "config_hash": "ui"}}
        self.saved.return_value = {"hashes": {"kafka": "saved", "kafka-ui": "ui"}, "images": {}}
        kafka = {"kind": "addon", "id": "kafka", "name": "Kafka"}
        self.assertEqual(stack.effects(PROJECT), {"restarts": [], "starts": [], "runs": True, "exact": True})
        self.assertEqual(stack.effects(PROJECT, {"kafka": "draft", "kafka-ui": "ui"})["restarts"], [kafka])
        self.assertEqual(stack.effects(PROJECT, None, ["kafka-ui", "jade"])["restarts"], [kafka])
        # What is saved could not be read: every service that runs counts, and that is not exact
        self.saved.return_value = {"hashes": {}, "images": {}}
        mongodb = {"kind": "service", "id": "mongodb", "name": "mongodb"}
        self.assertEqual(stack.effects(PROJECT), {"restarts": [mongodb, kafka], "starts": [], "runs": True,
                                                  "exact": False})
        self.jobs.start.assert_not_called()   # it only says

    def test_group_service_and_addon(self):
        stack.group_action(PROJECT, "databases", "stop")
        self.assertEqual(self.started(self.jobs), (
            "Stop Databases", [[*self.BY_NAME, "stop", "mongodb", "redis"]],
            {"key": "group:databases", "verb": "stop", "changes": {"stop": [["group", "databases"]]},
             "waits": [awaited(DEFINED, stop=["mongodb", "redis"])]}))
        stack.service_action(PROJECT, "jade", "start")
        self.assertEqual(self.started(self.jobs), (
            "Start jade", [[*self.COMPOSE, "up", "-d", "jade"]],
            {"key": "service:jade", "verb": "start", "changes": {"start": [["service", "jade"]]},
             "waits": [{("Container", "gio-jade"): "start"}]}))
        stack.unit_action(PROJECT, "addon", "kafka", "restart")
        self.assertEqual(self.started(self.jobs), (
            "Restart Kafka", [[*self.BY_NAME, "restart", "kafka", "kafka-ui"]],
            {"key": "addon:kafka", "verb": "restart", "changes": {"start": [["addon", "kafka"]]},
             "waits": [awaited(DEFINED, start=["kafka", "kafka-ui"])]}))

    def test_a_start_of_what_needs_a_license_is_refused_without_one(self):
        # Its row says so: a click that reaches the server anyway is told why, and starts nothing
        project = unlicensed()
        for call, name in ((lambda: stack.group_action(project, "alert-engine", "start"), "Alert Engine"),
                           (lambda: stack.service_action(project, "alert-engine", "start"), "alert-engine")):
            with self.assertRaisesRegex(RuntimeError, f"^{name} needs a license$"):
                call()
        self.jobs.start.assert_not_called()
        # A group of other services starts them, and not what needs one
        databases = {**project, "groups": [{"id": "databases", "name": "Databases",
                                            "services": ["mongodb", "alert-engine", "redis"]}]}
        stack.group_action(databases, "databases", "start")
        self.assertEqual(self.started(self.jobs)[1], [[*self.COMPOSE, "up", "-d", "mongodb", "redis"]])
        # Stopping and restarting what is there are fine, with a license or without
        stack.compose.ps.return_value = {"alert-engine": {"state": "running"}}
        stack.group_action(project, "alert-engine", "stop")
        self.assertEqual(self.started(self.jobs)[1], [[*self.BY_NAME, "stop", "alert-engine"]])
        stack.group_action(project, "alert-engine", "restart")
        self.assertEqual(self.started(self.jobs)[1], [[*self.BY_NAME, "restart", "alert-engine"]])
        # With a license it is a service as any other
        stack.group_action(unlicensed(True), "alert-engine", "start")
        self.assertEqual(self.started(self.jobs)[1], [[*self.COMPOSE, "up", "-d", "alert-engine"]])

    def test_a_start_waits_for_what_compose_starts_first(self):
        # The playground's app depends on Jade, Redis and a migration, which depends on MongoDB. MongoDB was stopped
        # from the Stack and the migration ended: `up` runs both first, so the job starts and waits for them too.
        # Redis runs; Jade is off: the configuration Compose reads has it, `up` does not start it
        app = {**DEFINED["hello-app"], "depends_on": ["jade", "migrate", "redis"]}
        project = {**PROJECT, "services": {**DEFINED, "hello-app": app, "migrate": migrate(depends_on=["mongodb"])}}
        stack.compose.ps.return_value = {"mongodb": {"state": "exited", "exit_code": 143},
                                         "redis": {"state": "running"}, "migrate": {"state": "exited", "exit_code": 0}}
        stack.service_action(project, "hello-app", "start")
        self.assertEqual(self.started(self.jobs), (
            "Start hello-app", [[*self.COMPOSE, "up", "-d", "hello-app"]],
            {"key": "service:hello-app", "verb": "start",
             "changes": {"start": [["service", "hello-app"], ["service", "migrate"], ["service", "mongodb"]]},
             "waits": [awaited(project["services"], start=["hello-app", "migrate", "mongodb"])]}))

    def test_the_containers_of_an_add_on_or_a_playground(self):
        # The Stack starts and stops them as a platform group's: on or off, they stay so. A playground's
        # seed runs at its start and with Re-seed: neither a start nor a restart runs it
        project = {**PROJECT, "playgrounds": {"welcome": {"name": "Welcome", "services": ["hello-app",
                                                                                            "welcome-seed"],
                                                           "apps": ["hello-app"]}}}
        stack.unit_action(project, "addon", "kafka", "start")
        self.assertEqual(self.started(self.jobs), (
            "Start Kafka", [[*self.COMPOSE, "up", "-d", "kafka", "kafka-ui"]],
            {"key": "addon:kafka", "verb": "start", "changes": {"start": [["addon", "kafka"]]},
             "waits": [awaited(DEFINED, start=["kafka", "kafka-ui"])]}))
        stack.unit_action(project, "addon", "kafka", "stop")
        self.assertEqual(self.started(self.jobs)[1:], (
            [[*self.BY_NAME, "stop", "kafka", "kafka-ui"]],
            {"key": "addon:kafka", "verb": "stop", "changes": {"stop": [["addon", "kafka"]]},
             "waits": [awaited(DEFINED, stop=["kafka", "kafka-ui"])]}))
        stack.compose.ps.return_value = {"hello-app": {"state": "running"},
                                         "welcome-seed": {"state": "exited", "exit_code": 0}}
        stack.unit_action(project, "playground", "welcome", "start")
        self.assertEqual(self.started(self.jobs)[1], [[*self.COMPOSE, "up", "-d", "hello-app"]])
        stack.unit_action(project, "playground", "welcome", "restart")
        self.assertEqual(self.started(self.jobs)[1], [[*self.BY_NAME, "restart", "hello-app"]])
        stack.unit_action(project, "playground", "welcome", "stop")
        self.assertEqual(self.started(self.jobs)[1], [[*self.BY_NAME, "stop", "hello-app", "welcome-seed"]])
        self.assertEqual(self.started(self.jobs)[2]["key"], "playground:welcome")

    def test_only_services_with_a_container_are_named(self):
        stack.compose.ps.return_value = {"kafka": {"state": "running"}}
        stack.unit_action(PROJECT, "addon", "kafka", "restart")
        _, steps, options = self.started(self.jobs)
        self.assertEqual((steps, options["waits"]), ([[*self.BY_NAME, "restart", "kafka"]],
                                                     [{("Container", "gio-kafka"): "start"}]))

    def test_a_restart_restarts_what_runs_as_it_is(self):
        # Kafka UI and Redis were stopped from the Stack: a restart of the add-on, or of a group on the Stack, leaves
        # them stopped; a stop takes every container there is
        stopped = {"state": "exited", "exit_code": 143}
        stack.compose.ps.return_value = {"kafka": {"state": "running"}, "kafka-ui": stopped,
                                         "mongodb": {"state": "running"}, "redis": stopped}
        stack.unit_action(PROJECT, "addon", "kafka", "restart")
        _, steps, options = self.started(self.jobs)
        self.assertEqual((steps, options["waits"]), ([[*self.BY_NAME, "restart", "kafka"]],
                                                     [{("Container", "gio-kafka"): "start"}]))
        stack.group_action(PROJECT, "databases", "restart")
        self.assertEqual(self.started(self.jobs)[1], [[*self.BY_NAME, "restart", "mongodb"]])
        stack.unit_action(PROJECT, "addon", "kafka", "stop")
        self.assertEqual(self.started(self.jobs)[1], [[*self.BY_NAME, "stop", "kafka", "kafka-ui"]])
        # Nothing of it runs: a restart has nothing to do
        stack.compose.ps.return_value = {"kafka": {"state": "exited", "exit_code": 143}}
        self.jobs.start.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "^Kafka is not running$"):
            stack.unit_action(PROJECT, "addon", "kafka", "restart")
        self.jobs.start.assert_not_called()

    def test_what_does_not_exist_is_refused(self):
        for call in (lambda: stack.action(PROJECT, "explode"), lambda: stack.group_action(PROJECT, "nothing", "stop"),
                     lambda: stack.service_action(PROJECT, "nothing", "stop"),
                     lambda: stack.unit_action(PROJECT, "addon", "nothing", "restart")):
            self.assertRaises(paths.NotFound, call)
        # No service: Compose would restart all; no container
        self.assertRaises(ValueError, stack.unit_action, PROJECT, "addon", "tuning", "restart")
        self.assertRaises(RuntimeError, stack.unit_action, PROJECT, "addon", "jade", "restart")
        # A seed runs again with its playground's Re-seed, which records and follows it
        for do in ("start", "restart"):
            with self.assertRaisesRegex(RuntimeError, "^Re-seed runs it again$"):
                stack.service_action(PROJECT, "welcome-seed", do)
        self.jobs.start.assert_not_called()
        stack.compose.ps.return_value = {"welcome-seed": {"state": "running"}}   # seeding: it may be stopped
        stack.service_action(PROJECT, "welcome-seed", "stop")
        self.jobs.start.assert_called_once()

    # --- How far each job is, as Compose and Docker go ---------------------------------------------------------------

    def ran(self, lines, *more, running=()):
        """The job the last action started, its commands printing these lines in turn, with Docker following them
        from these containers running and healthy: how far it was after each line, and the job."""
        title, steps, options = self.started(self.jobs)
        job = Job(title, steps, **options)
        docker = Docker(PROJECT, {name: container(health="healthy") for name in running}, HEALTH_CHECKED)
        return progress(replay(job, lines, *more, refresh=docker.refresh)), job, docker

    def test_a_group_stops(self):
        stack.group_action(PROJECT, "databases", "stop")
        curve, job, _ = self.ran([" Container gio-mongodb Stopping ", " Container gio-redis Stopping ",
                                  " Container gio-redis Stopped ", " Container gio-mongodb Stopped "],
                                 running=("mongodb", "redis"))
        self.assertEqual((curve, job.progress), ([0.25, 0.5, 0.75, 1], 1))

    def test_a_service_starts_and_is_there_once_healthy(self):
        stack.service_action(PROJECT, "jade", "start")
        curve, job, docker = self.ran([" Container gio-jade Creating ", " Container gio-jade Created ",
                                       " Container gio-jade Starting ", " Container gio-jade Started "])
        self.assertEqual((curve, job.ok, job.progress), ([0, 0.2, 0.2, 0.5], True, 0.5))
        docker.healthy("jade")
        docker.observe(job)
        self.assertEqual(job.progress, 1)

    def test_a_restart_is_there_once_what_it_restarted_is_ready(self):
        # Kafka's health check runs again after the restart: Docker shows it healthy only once it passes
        stack.unit_action(PROJECT, "addon", "kafka", "restart")
        lines = [" Container gio-kafka-ui Restarting ", " Container gio-kafka Restarting ",
                 " Container gio-kafka-ui Started ", " Container gio-kafka Started "]
        curve, job, docker = self.ran(lines, running=("kafka", "kafka-ui"))
        self.assertEqual(curve, [0.1, 0.2, 0.6, 0.75])
        docker.healthy("kafka")
        docker.observe(job)
        self.assertEqual(job.progress, 1)

    def test_an_update_pulls_then_makes_again_what_changed(self):
        # The pull is half of the job, each image alike; then what runs: what got no new image runs on and is there
        # at once, Kafka, which got one, is made again and is there once healthy
        stack.action(PROJECT, "update")
        images = ("mongo:8.0.29", "redis:8.10.1-alpine", "docker.io/bitnamilegacy/kafka:3.9",
                  "provectuslabs/kafka-ui:latest")
        pulls = [f" Image {image} {status} " for status in ("Pulling", "Pulled") for image in images]
        starts = [*(f" Container gio-{name} Running " for name in ("mongodb", "redis", "kafka-ui")),
                  " Container gio-kafka Recreate ", " Container gio-kafka Recreated ",
                  " Container gio-kafka Starting ", " Container gio-kafka Started "]
        curve, job, docker = self.ran(pulls, starts, running=("mongodb", "redis", "kafka", "kafka-ui"))
        self.assertEqual(curve[:8], [0, 0, 0, 0, 0.125, 0.25, 0.375, 0.5])
        self.assertEqual(curve[8:], [0.625, 0.75, 0.875, 0.875, 0.9, 0.9, 0.938])
        docker.healthy("kafka")
        docker.observe(job)
        self.assertEqual((job.ok, job.progress), (True, 1))


class ResetRecords(Case):
    """A reset empties what every playground recorded, whichever catalog it comes from, and no more."""

    DEFAULT = json.dumps({"schema": 1, "catalogs": [{"id": "fixture", "url": "https://example.invalid/fixture.git"}]})
    IDS = ("bank", "@acme/bank", "local/mine")

    def setUp(self):
        self.folder(paths, playgrounds, files={paths.DEFAULT_FILE: self.DEFAULT})
        self.patch(stack.layers, "current", return_value={"playground": None, "explicit": [], "locked": None})
        self.patch(stack.places, "deployed", return_value=[])
        self.project = {"playgrounds": {pid: {"id": pid, "name": pid} for pid in self.IDS}}
        self.records = {}
        for pid in self.IDS:
            state = self.root / paths.unit_folder("playground", pid) / ".state"
            self.write(f"{paths.unit_folder('playground', pid)}/.state/created.json", '[{"label": "API"}]')
            self.write(f"{paths.unit_folder('playground', pid)}/.state/cloud/dev/created.json", '[{"label": "API"}]')
            self.records[pid] = state

    def reset(self):
        job = Job("Reset", [])
        stack._forget(self.project)(job)
        return job

    def test_the_record_of_this_machine_is_emptied_for_the_playgrounds_of_every_catalog(self):
        job = self.reset()
        self.assertEqual(job.lines, [])
        for pid, state in self.records.items():
            with self.subTest(pid=pid):
                self.assertEqual((state / "created.json").read_text(encoding="utf-8"), "[]")
                self.assertEqual((state / "cloud/dev/created.json").read_text(encoding="utf-8"), '[{"label": "API"}]',
                                 "what is in a platform stays, with its record")

    def test_a_playground_whose_state_folder_leads_out_of_its_folder_is_left_alone_and_said(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "created.json").write_text('[{"label": "API"}]', encoding="utf-8")
        state = self.records["@acme/bank"]
        for path in sorted(state.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        state.rmdir()
        os.symlink(outside, state)
        job = self.reset()
        self.assertEqual(job.lines, ["Could not empty what @acme/bank recorded: its state folder leads out of its folder"])
        self.assertEqual((outside / "created.json").read_text(encoding="utf-8"), '[{"label": "API"}]')
        self.assertEqual((self.records["bank"] / "created.json").read_text(encoding="utf-8"), "[]")
        self.assertEqual((self.records["local/mine"] / "created.json").read_text(encoding="utf-8"), "[]")


if __name__ == "__main__":
    unittest.main()
