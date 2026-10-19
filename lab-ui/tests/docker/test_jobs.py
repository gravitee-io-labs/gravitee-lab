"""Jobs: one at a time, the last ones kept, a failure remembered until the item is started or stopped again, and
how far a job is: from what each stage waits for, known before it begins, moved by what its commands print and by
what Docker says of the containers. Nothing is run: a command prints lines taken from real output, and Docker is a
fixture that follows them. Then "Start the stack" replayed as Compose printed it."""

import threading
import time
import unittest
from unittest import mock

from lab_ui.docker import compose, jobs
from lab_ui.library import model
from lab_ui import paths
from lab_ui.server import stack
from lab_ui.docker.jobs import KEPT, Job, JobFailed, Jobs, awaited

from .. import Case

KAFKA, EVENTS, PLATFORM = "addon:kafka", "setup:events", "platform"


def ended(title, ok, key=None, **changes):
    """A job that ended, well or not."""
    job = Job(title, [], key=key, verb="start", changes=changes)
    job.done, job.ok = True, ok
    return job


def prepull(cmd):
    """Whether a command is the pull that comes before an `up`: it prints nothing here, the images are there."""
    return "--ignore-pull-failures" in cmd


def replay(job, *outputs, code=0, refresh=None):
    """Runs a job whose commands print these outputs in turn (a list of lines each) instead of running: its summary
    after each line. `refresh(job, line)` runs after each line is read, as the state's refresh would."""
    summaries, printed = [], iter(outputs)

    def command(cmd, **options):
        if prepull(cmd):
            return mock.Mock(stdout=iter([]), wait=lambda: 0, returncode=0)

        def lines():
            for line in next(printed):
                yield line + "\n"
                if refresh:
                    refresh(job, line)
                summaries.append(job.summary())
        return mock.Mock(stdout=lines(), wait=lambda: code, returncode=code)
    with mock.patch.object(jobs.subprocess, "Popen", command):
        job.run()
    return summaries


def progress(summaries):
    """How far the job was after each line, to the thousandth."""
    return [None if summary["progress"] is None else round(summary["progress"], 3) for summary in summaries]


def container(state="running", health="", code=0):
    """A container as compose.ps() answers it."""
    return {"state": state, "health": health, "exit_code": code, "status": "", "oom": False, "restarts": 0,
            "name": "", "image": ""}


class Docker:
    """What Docker says of a project's containers as a job goes: each moves as Compose's lines say (made, running,
    healthy, stopped, removed), and the test finishes the health checks Compose does not wait for (`healthy`).
    `checked` names the services Docker checks the health of, as their file or their image asks: once started, their
    health is starting until the test says it passed. `observe` gives it to the job as the state's refresh does:
    asked after the lines read so far."""

    def __init__(self, project, containers=None, checked=()):
        self.project, self.containers, self.checked = project, dict(containers or {}), set(checked)
        self.services = {service["container"]: name for name, service in project["services"].items()}

    def follow(self, line):
        found = jobs.UNIT.fullmatch(line)
        name = found and found.group(1) == "Container" and self.services.get(found.group(2))
        if not name:
            return
        status = found.group(3)
        if status in ("Created", "Recreated"):
            self.containers[name] = container("created")
        elif status in ("Started", "Restarting"):
            self.containers[name] = container(health="starting" if name in self.checked else "")
        elif status == "Healthy":
            self.healthy(name)
        elif status == "Exited":
            self.containers[name] = container("exited", code=0)
        elif status == "Stopped":
            self.containers[name] = container("exited", code=143)
        elif status == "Removed":
            self.containers.pop(name, None)

    def healthy(self, *names):
        for name in names:
            self.containers[name] = container(health="healthy")

    def steps(self):
        return model.container_steps(self.project, self.containers)

    def observe(self, job):
        job.observe(self.steps(), time.monotonic())

    def refresh(self, job, line):
        """After a line: Docker follows it, then the state's refresh tells the job."""
        self.follow(line)
        self.observe(job)


def services(*specs):
    """A project's services for a job's units: (name, one-shot), each in a container gio-<name>."""
    return {name: {"container": f"gio-{name}", "oneshot": oneshot,
                   "owner": {"kind": "platform", "id": "lab", "name": "Lab"}}
            for name, oneshot in specs}


SERVICES = services(("mongodb", False), ("redis", False), ("reader-ui", False), ("alert-engine", False),
                    ("apim-gateway", False), ("openfga-migrate", True))
LAB = {"services": SERVICES}
# What Docker checks the health of: neither Reader UI nor the migration
CHECKED = {"mongodb", "redis", "alert-engine", "apim-gateway"}


class Running(Case):

    def setUp(self):
        self.jobs = Jobs()

    def finished(self, job):
        deadline = time.time() + 5
        while not job.done and time.time() < deadline:
            time.sleep(0.005)
        self.assertTrue(job.done, f"{job.title} did not end")
        return job

    def start(self, title, steps, **how):
        return self.jobs.start(title, steps, key="addon:kafka", verb="start", **how)

    def test_what_a_command_prints_is_read_as_utf8_whatever_the_terminal_says(self):
        asked = []

        def command(cmd, **options):
            asked.append(options)
            return mock.Mock(stdout=iter(["✓ Démo\n"]), wait=lambda: 0, returncode=0)
        job = Job("Start Kafka", [["docker", "compose", "up"]], key="addon:kafka", verb="start")
        with mock.patch.object(jobs.subprocess, "Popen", command):
            job.run()
        self.assertEqual((job.ok, job.lines[-1]), (True, "✓ Démo"))
        self.assertEqual((asked[0]["encoding"], asked[0]["errors"]), ("utf-8", "replace"))

    def test_one_at_a_time(self):
        release = threading.Event()
        first = self.start("Start Kafka", [lambda job: release.wait(5)])
        with self.assertRaisesRegex(RuntimeError, "'Start Kafka' is still running"):
            self.start("Start Jade", [])
        self.assertEqual((self.jobs.current, len(self.jobs.jobs)), (first, 1))
        release.set()
        self.assertTrue(self.finished(first).ok)
        second = self.finished(self.start("Start Jade", []))
        self.assertEqual((self.jobs.current, self.jobs.jobs), (second, [first, second]))

    def test_a_job_says_what_it_acts_on_and_what_it_does(self):
        # The button that started it shows it by them; a job that would not say is refused
        job = self.finished(self.jobs.start("Pull images", [], key="platform", verb="pull"))
        self.assertEqual({key: job.summary()[key] for key in ("for", "verb")}, {"for": "platform", "verb": "pull"})
        with self.assertRaises(TypeError):
            self.jobs.start("Pull images", [])

    def test_the_last_ones_are_kept(self):
        started = [self.finished(self.start(f"Job {number}", [])) for number in range(KEPT + 2)]
        self.assertEqual(self.jobs.jobs, started[2:])
        self.assertEqual(self.jobs.get(started[-1].id), started[-1])
        with self.assertRaises(paths.NotFound):
            self.jobs.get(started[0].id)

    def test_steps_run_in_order_until_one_fails(self):
        ran = []

        def refuse(job):
            raise JobFailed("The seed failed")
        job = self.finished(self.start("Start", [lambda job: ran.append(1), refuse, lambda job: ran.append(3)]))
        self.assertEqual((ran, job.ok, job.lines, job.summary()["error"]), ([1], False, ["The seed failed"],
                                                                           "The seed failed"))

    def test_a_job_is_started_with_the_phases_it_declares(self):
        # Without them a job of functions has nothing to measure
        job = self.finished(self.start("Plugins", [lambda job: job.step("Download")], phases=["Download"]))
        self.assertEqual((job.ok, job.summary()["progress"]), (True, 1))
        self.assertIsNone(self.finished(self.start("Plugins", [lambda job: job.step("Download")])).progress)

    def test_what_stages_wait_for_comes_with_them(self):
        # One entry per stage, in their order; a stage left out waits for nothing it can name
        waits = [awaited(SERVICES, start=["redis"])]
        self.assertEqual(Job("Start", [UP, UP], waits=waits).summary()["containers"], {"gio-redis": 0})
        with self.assertRaisesRegex(ValueError, "what 2 stages wait for, for 1 stages"):
            Job("Start", [UP], waits=waits * 2)

    def test_run_steps_stops_when_the_job_is_canceled(self):
        job, ran = Job("Plugins", []), []
        job.run_steps([lambda job: ran.append(1), lambda job: job.cancel(), lambda job: ran.append(3)])
        self.assertEqual(ran, [1])


UP = ["docker", "compose", "up", "-d"]
STOP = ["docker", "compose", "stop"]
# Start Reader UI, Stop Reader UI and Restart Alert Engine, as Compose printed them
STARTS = [" Container gio-mongodb Running ", " Container gio-redis Running ", " Container gio-reader-ui Creating ",
          " Container gio-reader-ui Created ", " Container gio-redis Waiting ", " Container gio-redis Healthy ",
          " Container gio-reader-ui Starting ", " Container gio-reader-ui Started ",
          " Container gio-mongodb Healthy "]
STOPS = [" Container gio-redis Running ", " Container gio-reader-ui Stopping ",
         " Container gio-reader-ui Stopped ", " Container gio-reader-ui Removing ",
         " Container gio-reader-ui Removed ", " Container gio-redis Waiting "]
RESTARTS = [" Container gio-alert-engine Restarting ", " Container gio-alert-engine Started "]
# Start Welcome, shortened: an image is built, the gateway is recreated, a migration runs and ends. The
# network line is what Compose prints when it creates one
BUILDS = [" Image gravitee-lab/hello-agent Building ", "#1 [internal] load local bake definitions",
          "#42 [hello-agent 4/4] RUN pip install .", "#42 CACHED", " Image gravitee-lab/hello-agent Built ",
          " Network gio_default Creating ", " Container gio-mongodb Running ", " Container gio-apim-gateway Recreate ",
          " Container gio-openfga-migrate Creating ", " Container gio-openfga-migrate Created ",
          " Container gio-apim-gateway Recreated ", " Container gio-mongodb Waiting ",
          " Container gio-apim-gateway Starting ", " Container gio-apim-gateway Started ",
          " Container gio-openfga-migrate Starting ", " Container gio-openfga-migrate Started ",
          " Container gio-openfga-migrate Exited ", " Container gio-apim-gateway Healthy "]
SEEDS = ["[seed] Seeding the playground", "[apim] ✓ API updated: ACME Hello API", "[seed] ✓ Playground ready"]
# Pull images: an image that is built is skipped, one that two services run is pulled once, a layer's lines are not
# streamed
PULL = [*compose.COMPOSE, "pull", "--ignore-buildable"]
PULLS = [" Image gravitee-lab/hello-agent Skipped Image can be built", " Image busybox:1.36 Pulling ",
         " Image redis:8 Pulling ", "3f1c0a7d9e2b Pull complete 0B", " Image busybox:1.36 Pulled ",
         " Image redis:8 Pulled "]


class Progress(Case):

    def job(self, steps, *waits, **how):
        return Job("Test", steps, waits=list(waits), **how)

    def test_what_a_stage_waits_for_counts_from_the_start(self):
        # Reader UI is all it waits for: the database it needs, running already, is not counted. One not named yet
        # counts 0; made, it is a fifth of the way; running, half way
        job = self.job([UP], awaited(SERVICES, start=["reader-ui"]))
        self.assertEqual(progress(replay(job, STARTS)), [0, 0, 0, 0.2, 0.2, 0.2, 0.2, 0.5, 0.5])
        self.assertEqual(job.summary()["containers"], {"gio-reader-ui": 0.5})
        # Ready is Docker's to say: a health check may come with its image, which Compose does not show. Here none
        Docker(LAB, {"reader-ui": container()}, CHECKED).observe(job)
        self.assertEqual((job.progress, job.summary()["containers"]), (1, {"gio-reader-ui": 1}))

    def test_a_container_with_a_health_check_is_ready_once_healthy(self):
        # Running is half way: "Waiting" says its health is awaited, "Healthy" that it is ready. Reader UI, which
        # has no health check, is ready once Docker says it runs
        job = self.job([UP], awaited(SERVICES, start=["redis", "reader-ui"]))
        self.assertEqual(progress(replay(job, STARTS)), [0, 0.25, 0.25, 0.35, 0.35, 0.6, 0.6, 0.75, 0.75])
        Docker(LAB, {"redis": container(health="healthy"), "reader-ui": container()}, CHECKED).observe(job)
        self.assertEqual(job.progress, 1)

    def test_an_image_s_own_health_check_is_waited_for(self):
        # Compose says it started and shows no health check: Docker runs the one of its image, and says when it passed
        job = self.job([UP], awaited(SERVICES, start=["reader-ui"]))
        docker = Docker(LAB, checked={"reader-ui"})
        lines = [" Container gio-reader-ui Created ", " Container gio-reader-ui Started "]
        self.assertEqual(progress(replay(job, lines, refresh=docker.refresh)), [0.2, 0.5])
        docker.observe(job)
        self.assertEqual((job.ok, job.progress), (True, 0.5))
        docker.healthy("reader-ui")
        docker.observe(job)
        self.assertEqual(job.progress, 1)

    def test_docker_says_the_health_that_compose_does_not_wait_for(self):
        # Compose returns once Alert Engine runs; its health check passes later, read with the state
        job = self.job([UP], awaited(SERVICES, start=["alert-engine", "reader-ui"]))
        docker = Docker(LAB, checked=CHECKED)
        lines = [" Container gio-alert-engine Starting ", " Container gio-alert-engine Started ",
                 " Container gio-reader-ui Started "]
        self.assertEqual(progress(replay(job, lines, refresh=docker.refresh)), [0.1, 0.25, 0.75])
        self.assertEqual((job.ok, job.progress), (True, 0.75))   # ended well, not there yet
        docker.observe(job)
        self.assertEqual(job.progress, 0.75)
        docker.healthy("alert-engine")
        docker.observe(job)
        self.assertEqual((job.progress, job.summary()["containers"]), (1, {"gio-alert-engine": 1,
                                                                          "gio-reader-ui": 1}))

    def test_what_docker_said_before_the_container_ran_again_does_not_count(self):
        # A restart: Docker still shows the container it restarts, healthy, until it runs again
        job, asked = self.job([UP], awaited(SERVICES, start=["alert-engine"])), []

        def refresh(job, line):
            if "Restarting" in line:
                asked.append(time.monotonic())   # asked now, answered once the restart started it
            else:
                job.observe({"gio-alert-engine": "ready"}, asked[0])
        self.assertEqual(progress(replay(job, RESTARTS, refresh=refresh)), [0.2, 0.5])
        job.observe({"gio-alert-engine": "ready"}, asked[0])   # ended since: still asked before
        self.assertEqual(job.progress, 0.5)
        job.observe({"gio-alert-engine": "ready"}, time.monotonic())
        self.assertEqual(job.progress, 1)

    def test_a_stop(self):
        # Stopping is half way, stopped or removed is there; removing keeps what stopping gave
        job = self.job([STOP], awaited(SERVICES, stop=["reader-ui"]))
        self.assertEqual(progress(replay(job, STOPS)), [0, 0.5, 1, 1, 1, 1])
        # What is stopped already is there once its stage began, as Docker says it; a stage not begun is not. One that
        # runs is not, whatever its health
        job = self.job([STOP], awaited(SERVICES, stop=["redis", "reader-ui"]))
        job.observe({"gio-redis": "stopped", "gio-reader-ui": "absent"}, time.monotonic())
        self.assertIsNone(job.progress)
        docker = Docker(LAB, {"redis": container("exited", code=143), "reader-ui": container(health="unhealthy")})
        self.assertEqual(progress(replay(job, STOPS, refresh=docker.refresh)), [0.5, 0.75, 1, 1, 1, 1])

    def test_a_restart(self):
        job = self.job([UP], awaited(SERVICES, start=["alert-engine"]))
        self.assertEqual(progress(replay(job, RESTARTS)), [0.2, 0.5])

    def test_a_one_shot_is_ready_once_it_ended_well(self):
        job = self.job([UP], awaited(SERVICES, start=["openfga-migrate"]))
        lines = [" Container gio-openfga-migrate Created ", " Container gio-openfga-migrate Started "]
        docker = Docker(LAB)
        self.assertEqual(progress(replay(job, lines, refresh=docker.refresh)), [0.2, 0.5])
        docker.containers["openfga-migrate"] = container("exited", code=0)
        docker.observe(job)
        self.assertEqual(job.progress, 1)

    def test_a_pull_is_measured_by_its_images(self):
        # Each image alike, known before the pull begins, whichever way the file writes it
        images = awaited({}, images=["busybox:1.36", "docker.io/library/redis:8"])
        self.assertEqual(set(images), {("Image", "busybox:1.36"), ("Image", "redis:8")})
        job = self.job([PULL], images)
        self.assertEqual(progress(replay(job, PULLS)), [0, 0, 0, 0, 0.5, 1])
        self.assertNotIn("3f1c0a7d9e2b Pull complete 0B", job.lines)

    def test_an_image_that_up_pulls_or_builds_is_waited_for_too(self):
        # Compose names it before any container: what is created from it waits for it. Build output says nothing
        job = self.job([UP], awaited(SERVICES, start=["apim-gateway", "openfga-migrate"]))
        self.assertEqual(progress(replay(job, BUILDS)),
                         [0, 0, 0, 0, 0.333, 0.333, 0.333, 0.333, 0.333, 0.4, 0.467, 0.467, 0.467, 0.567, 0.567, 0.667,
                          0.833, 1])
        # An opaque stage stays so: what it prints is not what it waits for
        job = self.job([UP])
        self.assertEqual(set(progress(replay(job, BUILDS))), {None})

    def test_an_update_pulls_then_starts(self):
        # Each command is half of the job; Reader UI is there once Docker says it is ready
        job = self.job([PULL, UP], awaited({}, images=["busybox:1.36", "redis:8"]),
                       awaited(SERVICES, start=["reader-ui"]))
        self.assertEqual(progress(replay(job, PULLS, STARTS)),
                         [0, 0, 0, 0, 0.25, 0.5, 0.5, 0.5, 0.5, 0.6, 0.6, 0.6, 0.6, 0.75, 0.75])
        Docker(LAB, {"reader-ui": container()}, CHECKED).observe(job)
        self.assertEqual(job.progress, 1)

    def test_one_command_that_names_nothing_is_not_measured_while_it_runs(self):
        # Logging in to a registry: nothing tells how far it is until it ends
        job = Job("Log in", [["az", "acr", "login", "--name", "graviteeio"]])
        self.assertEqual((progress(replay(job, ["Login Succeeded"])), job.summary()["progress"]), ([None], 1))

    def test_each_command_of_a_job_without_phases_is_a_stage(self):
        # A command that names nothing counts once it is over; what a function runs is not measured
        steps = [UP, lambda job: job.run_steps([UP]), UP]
        job = self.job(steps, None, awaited(SERVICES, start=["alert-engine"]))
        self.assertEqual(progress(replay(job, SEEDS, RESTARTS, RESTARTS)), [None, None, None, 0.5, 0.5, 0.6, 0.75])
        self.assertEqual((job.ok, job.summary()["progress"], job.summary()["phases"]), (True, 0.75, []))

    def test_declared_phases_are_the_stages(self):
        def phase(name, cmd):
            def run(job):
                job.step(name)
                job.exec(cmd)
            return run
        steps = [lambda job: job.step("Add-ons"), phase("Apps and services", UP), phase("Seed", ["docker", "logs"])]
        job = self.job(steps, awaited(SERVICES, start=["apim-gateway", "openfga-migrate"]), None,
                       phases=["Apps and services", "Seed"])
        read = replay(job, BUILDS, SEEDS)
        self.assertEqual(progress(read)[:15], [0, 0, 0, 0, 0.167, 0.167, 0.167, 0.167, 0.167, 0.2, 0.233, 0.233,
                                               0.233, 0.283, 0.283])
        self.assertEqual(progress(read)[15:], [0.333, 0.417, 0.5, 0.5, 0.5, 0.5])   # the seed prints nothing to count
        built, seeding = read[4], read[-1]
        self.assertEqual([(p["name"], round(p["progress"], 3)) for p in built["phases"]],
                         [("Add-ons", 1), ("Apps and services", 0.333)])
        self.assertEqual([(p["name"], p["progress"]) for p in seeding["phases"]],
                         [("Add-ons", 1), ("Apps and services", 1), ("Seed", None)])
        self.assertEqual((job.summary()["progress"], [p["progress"] for p in job.summary()["phases"]]), (1, [1, 1, 1]))

    def test_a_phase_that_is_not_declared_is_a_label(self):
        def seed(job):
            job.step("Seed")
            job.exec(UP)
        job = self.job([seed], phases=[])
        read = replay(job, RESTARTS)
        self.assertEqual([(s["progress"], s["phases"][0]["progress"]) for s in read], [(None, None), (None, None)])
        # Nothing to measure: no progress, even once it ended well
        self.assertEqual((job.ok, job.summary()["progress"], job.summary()["containers"]), (True, None, {}))

    def test_a_step_reports_its_own_units(self):
        seen = []

        def download(job):
            job.unit(("Download", "a"), 1)   # outside its stage: not measured
            seen.append(job.progress)
            job.step("Download")
            for name, fraction in (("a", 0.5), ("a", 1), ("b", 0), ("b", 0.5)):
                job.unit(("Download", name), fraction)
                seen.append(job.progress)
            job.step("Stop")   # the downloads are over: in full
            seen.append(job.progress)
        downloads = {("Download", "a"): "download", ("Download", "b"): "download"}
        job = self.job([download], downloads, phases=["Download", "Stop", "Start"])
        job.run()
        self.assertEqual([None if p is None else round(p, 3) for p in seen], [None, 0.083, 0.167, 0.167, 0.25, 0.333])
        self.assertEqual(job.summary()["progress"], 1)

    def test_what_a_stage_learns_once_it_runs_is_added(self):
        # A service that started since the job began stops too, so that it loads the plugins downloaded: the stage
        # waits for it from then on. The job does not go back meanwhile
        seen = []

        def plugins(job):
            def said():
                seen.append((round(job.summary()["phases"][0]["progress"], 3), round(job.progress, 3)))
            job.step("Plugins")
            job.unit(("Download", "x"), 1)
            said()
            job.expect({("Container", "gio-redis"): "stop", ("Download", "x"): "download"})
            said()
            job.unit(("Container", "gio-mongodb"), 1)
            said()
            job.unit(("Container", "gio-redis"), 1)
            said()
        job = self.job([plugins], {("Download", "x"): "download", **awaited(SERVICES, stop=["mongodb"])},
                       phases=["Plugins"])
        job.run()
        self.assertEqual(seen, [(0.5, 0.5), (0.333, 0.5), (0.667, 0.667), (1, 1)])
        self.assertEqual(job.progress, 1)

    def test_a_container_stopped_then_started_is_half_way_once_stopped(self):
        # Its stop then its start (plugins loaded again): each stage that waits for it is half of its way
        def phase(name, cmd):
            def run(job):
                job.step(name)
                job.exec(cmd)
            return run
        job = self.job([phase("Stop", STOP), phase("Start", UP)], awaited(SERVICES, stop=["apim-gateway"]),
                       awaited(SERVICES, start=["apim-gateway"]), phases=["Stop", "Start"])
        read = replay(job, [" Container gio-apim-gateway Stopping ", " Container gio-apim-gateway Stopped "],
                      [" Container gio-apim-gateway Starting ", " Container gio-apim-gateway Started ",
                       " Container gio-apim-gateway Healthy "])
        self.assertEqual([round(s["containers"]["gio-apim-gateway"], 3) for s in read], [0.25, 0.5, 0.6, 0.75, 1])
        self.assertEqual(progress(read), [0.25, 0.5, 0.6, 0.75, 1])

    def test_a_job_that_ended_well_settles_once_nothing_is_on_its_way(self):
        # Alert Engine passes its health check later; Redis turns unhealthy, which it will not get over by itself
        job = self.job([UP], awaited(SERVICES, start=["alert-engine", "redis"]))
        replay(job, [" Container gio-alert-engine Started ", " Container gio-redis Started "])
        job.observe({"gio-alert-engine": "running", "gio-redis": "issue"}, time.monotonic())
        self.assertEqual((job.progress, job.settled), (0.5, False))   # Alert Engine is still on its way
        # What Docker said before the job ended is not where it left them
        job.observe({"gio-alert-engine": "ready", "gio-redis": "issue"}, time.monotonic() - 60)
        self.assertEqual((job.progress, job.settled), (0.5, False))
        job.observe({"gio-alert-engine": "ready", "gio-redis": "issue"}, time.monotonic())
        self.assertEqual((job.progress, job.settled, job.summary()["settled"]), (0.75, True, True))
        # Settled, it stays so
        job.observe({"gio-alert-engine": "ready", "gio-redis": "running"}, time.monotonic())
        self.assertTrue(job.settled)
        # One that keeps restarting, exited or is gone is not on its way either; one made, or that runs, is
        for step, settled in (("issue", True), ("stopped", True), ("absent", True), ("created", False),
                              ("running", False)):
            job = self.job([UP], awaited(SERVICES, start=["redis"]))
            replay(job, [" Container gio-redis Started "])
            job.observe({"gio-redis": step}, time.monotonic())
            self.assertEqual(job.settled, settled, step)
        # Nor does a job that runs or failed settle
        job = self.job([UP], awaited(SERVICES, start=["redis"]))
        job.observe({"gio-redis": "issue"}, time.monotonic())
        replay(job, [" Container gio-redis Started "], code=1)
        job.observe({"gio-redis": "issue"}, time.monotonic())
        self.assertEqual((job.ok, job.settled), (False, False))

    def test_a_job_that_fails_stays_where_it_was(self):
        # Whatever Docker says next: Alert Engine running would count once healthy, had the job gone on
        job = self.job([UP, UP], awaited(SERVICES, start=["alert-engine"]), awaited(SERVICES, start=["redis"]))
        replay(job, RESTARTS, code=1)
        self.assertEqual((job.ok, job.summary()["progress"]), (False, 0.25))
        job.observe({"gio-alert-engine": "ready", "gio-redis": "ready"}, time.monotonic())
        self.assertEqual(job.progress, 0.25)

    def test_a_command_that_fails_says_why_with_its_last_line(self):
        # Compose's reason, said once; an earlier line that starts with "Error" is not it
        failed = [" Container gio-apim-gateway Error ", "Error response from daemon: a warning", "",
                  "dependency failed to start: container gio-apim-gateway is unhealthy"]
        job = Job("Test", [UP])
        replay(job, failed, code=1)
        self.assertEqual((job.error(), job.lines[-1]), (failed[-1], failed[-1]))
        job = Job("Test", [UP])
        replay(job, [], code=1)   # nothing printed
        self.assertEqual((job.error(), job.lines), ("Failed with exit code 1", ["$ docker compose up -d",
                                                                                "Failed with exit code 1"]))


# "Start the stack" as Compose printed it after the command: twelve
# services, each with a health check; Compose waits only for those another one depends on, and returns once the last
# ones run
START_THE_STACK = [f" Container gio-{name} {status} " for name, status in (
    ("elasticsearch", "Starting"), ("alert-engine", "Starting"), ("mongodb", "Starting"), ("redis", "Starting"),
    ("alert-engine", "Started"), ("mongodb", "Started"), ("mongodb", "Waiting"), ("redis", "Started"),
    ("elasticsearch", "Started"), ("elasticsearch", "Waiting"), ("mongodb", "Waiting"), ("mongodb", "Healthy"),
    ("am-management-api", "Starting"), ("mongodb", "Healthy"), ("am-management-api", "Started"),
    ("am-management-api", "Waiting"), ("mongodb", "Waiting"), ("am-management-api", "Waiting"),
    ("mongodb", "Healthy"), ("am-management-api", "Healthy"), ("am-management-ui", "Starting"),
    ("am-management-api", "Healthy"), ("am-gateway", "Starting"), ("am-management-ui", "Started"),
    ("am-gateway", "Started"), ("elasticsearch", "Healthy"), ("apim-management-api", "Starting"),
    ("apim-management-api", "Started"), ("apim-management-api", "Waiting"), ("apim-management-api", "Waiting"),
    ("apim-management-api", "Waiting"), ("elasticsearch", "Waiting"), ("apim-management-api", "Waiting"),
    ("redis", "Waiting"), ("mongodb", "Waiting"), ("mongodb", "Healthy"), ("elasticsearch", "Healthy"),
    ("redis", "Healthy"), ("apim-management-api", "Healthy"), ("apim-management-ui", "Starting"),
    ("apim-management-api", "Healthy"), ("apim-gateway", "Starting"), ("apim-management-api", "Healthy"),
    ("apim-portal-ui", "Starting"), ("apim-management-api", "Healthy"), ("gamma-ui", "Starting"),
    ("gamma-ui", "Started"), ("apim-management-ui", "Started"), ("apim-portal-ui", "Started"),
    ("apim-gateway", "Started"))]
GROUPS = {"Databases": ["elasticsearch", "mongodb", "redis"], "Alert Engine": ["alert-engine"],
          "API Management": ["apim-gateway", "apim-management-api", "apim-management-ui", "apim-portal-ui",
                             "gamma-ui"],
          "Access Management": ["am-gateway", "am-management-api", "am-management-ui"]}
# The health checks Compose does not wait for pass that many refreshes after their container started: Alert Engine
# while the others start, the consoles and the gateways once Compose returned (50 lines)
HEALTHY_AFTER = {"alert-engine": 25, "am-management-ui": 30, "am-gateway": 33, "gamma-ui": 6, "apim-management-ui": 6,
                 "apim-portal-ui": 7, "apim-gateway": 9}


def platform():
    """The platform as project.build() describes it: four groups, nothing on top. Docker
    checks the health of every service."""
    services = {name: {"label": name, "description": "", "container": f"gio-{name}", "image": f"graviteeio/{name}:4.12.20",
                       "raw_image": "", "tag": "4.12.20", "registry": None, "image_tags": None, "ports": [],
                       "consoles": [], "oneshot": False, "depends_on": [], "built": False,
                       "role": None, "needs_license": False,
                       "owner": {"kind": "platform", "id": paths.slug(group), "name": group}}
                for group, names in GROUPS.items() for name in names}
    groups = [{"id": paths.slug(group), "name": group, "services": names, "version": "4.12.20", "registry": None,
               "product": None, "file": "docker-compose.yml", "env_file": ".env", "settings": []}
              for group, names in GROUPS.items()]
    return {"name": "Gravitee platform", "categories": [], "core_loaded": True, "license_var": None, "license_usable": True,
            "groups": groups,
            "products": {}, "addons": {}, "setups": {}, "playgrounds": {}, "services": services, "links": [],
            "core_variables": [], "problems": []}


class Replay(Case):
    """Start the stack, line by line, with Docker following the lines and the state read after each: how far the
    job is, and the platform, the groups and the button of the Stack as the state says them."""

    def setUp(self):
        self.project = platform()
        self.state = {"playground": None, "explicit": [], "setups": [], "effective": [], "missing": [],
                      "problems": [], "locked": None}
        self.patch(stack.layers, "current", return_value=self.state)
        self.patch(stack.compose, "project_name", return_value="gravitee")
        jobs_ = self.patch(stack, "JOBS")
        stack.action(self.project, "start")
        title, steps, more = self.started(jobs_)
        self.job = Job(title, steps, more["key"], more["verb"], more["changes"], waits=more["waits"])
        # The stack was stopped: every container exited
        self.docker = Docker(self.project, {name: container("exited", code=143) for name in self.project["services"]},
                             checked=self.project["services"])
        self.since, self.curve, self.named = {}, [], []

    def body(self):
        """The state the page reads now."""
        return model.build(self.project, self.state, self.docker.containers, self.job.summary(), lambda *_: None,
                           None, {"error": None, "memory": None}, {})

    def refresh(self, job=None, line=None):
        """A refresh of the state: after a line Docker follows it, the health checks that are due pass, and the job
        hears of it before the state is built."""
        if line:
            self.docker.follow(line)
            name = line.split()[1][len("gio-"):]
            self.named += [] if name in self.named else [name]
            if line.split()[2] == "Started":
                self.since[name] = len(self.curve)
        for name, after in HEALTHY_AFTER.items():
            if name in self.since and len(self.curve) - self.since[name] >= after:
                self.docker.healthy(name)
        self.docker.observe(self.job)
        body = self.body()
        ready = sum(container["health"] == "healthy" for container in self.docker.containers.values())
        self.curve.append({"job": self.job.progress, "platform": body["platform"]["progress"],
                           "state": body["platform"]["state"]["key"], "ready": ready, "named": len(self.named),
                           "groups": [(group["progress"], group["state"]["key"])
                                      for group in body["platform"]["groups"]]})

    def test_start_the_stack(self):
        replay(self.job, START_THE_STACK, refresh=self.refresh)
        self.job.done, returned = True, len(self.curve)   # as Jobs runs it
        while self.curve[-1]["ready"] < 12:
            self.refresh()
        self.refresh()   # once more: the state says it runs
        jobs_progress = [point["job"] for point in self.curve]
        self.assertEqual(jobs_progress, sorted(jobs_progress), "it never goes down")
        # The first four containers named: a fifth of the way each, half once running, ready once healthy
        first_four = [point["job"] for point in self.curve if point["named"] <= 4]
        self.assertEqual((round(first_four[3], 3), round(max(first_four), 3)), (0.067, 0.208))
        # Compose returned with six health checks still running: three quarters of the way, not all of it
        self.assertEqual((self.job.ok, self.curve[returned - 1]["job"]), (True, 0.75))
        # All of it once every service is ready, and only then
        self.assertEqual([point["job"] == 1 for point in self.curve], [point["ready"] == 12 for point in self.curve])
        # The platform's progress is the job's, while it runs and after, until the state says it runs
        for point in self.curve[:-2]:
            self.assertEqual((point["platform"], point["state"]), (point["job"], "starting"))
        self.assertEqual([(point["platform"], point["state"]) for point in self.curve[-2:]],
                         [(None, "running")] * 2)
        # A group is the mean of its containers while it starts: Databases runs long before the gateways, once Redis
        # is healthy, and then nothing of it is measured, though the job still runs. No group says Starting at 100%
        databases = [(None if share is None else round(share, 3), state)
                     for share, state in (point["groups"][0] for point in self.curve)]
        self.assertEqual(databases[36:38], [(0.833, "starting"), (None, "running")])
        self.assertEqual(set(databases[37:]), {(None, "running")})
        self.assertEqual((returned > 37, self.curve[37]["state"]), (True, "starting"))
        self.assertEqual([group for point in self.curve for group in point["groups"]
                          if group[1] == "starting" and group[0] >= 1], [])


class Failures(Case):

    def failure(self, *jobs, kind="addon", uid="kafka"):
        found = Jobs()
        found.jobs = list(jobs)
        return found.failed_job(kind, uid)

    @staticmethod
    def said(job, verb="start", error="", login=None):
        """What failed_job() says of a job that failed: its id, what it did, why in one line, and the registry it
        needed a login to."""
        return {"id": job.id, "verb": verb, "error": error, "login": login}

    def test_the_last_job_on_an_item_that_failed(self):
        failed = ended("Start Kafka", False, KAFKA, start=[["addon", "kafka"]])
        self.assertEqual(self.failure(failed), self.said(failed))
        self.assertIsNone(self.failure(failed, uid="jade"))
        self.assertIsNone(self.failure())

    def test_it_says_what_the_job_did_and_why_it_failed(self):
        # Its reason is its last line, a canceled one says so: the row's note and its tooltip say them
        stop = Job("Stop Kafka", [], key=KAFKA, verb="stop")
        stop.done, stop.ok, stop.lines = True, False, ["$ docker compose rm", "Error response from daemon: busy"]
        self.assertEqual(self.failure(stop), self.said(stop, "stop", "Error response from daemon: busy"))
        canceled = Job("Start Kafka", [["docker", "compose", "up"]], key=KAFKA, verb="start")
        canceled.cancel()
        canceled.run()
        canceled.done = True
        self.assertEqual(self.failure(canceled), self.said(canceled, "start", "Canceled"))

    def test_a_base_image_that_could_not_be_fetched(self):
        # BuildKit's line, said in plain words with the image; Docker's reason stays in the output
        for line in ("failed to solve: node:20-alpine: failed to resolve source metadata for docker.io/library/"
                     'node:20-alpine: failed to do request: Head "https://registry-1.docker.io/v2/library/node/'
                     'manifests/20-alpine": dial tcp: lookup registry-1.docker.io: no such host',
                     "target bakery-web: failed to solve: failed to resolve source metadata for ghcr.io/acme/base:1: "
                     "ghcr.io/acme/base:1: not found"):
            start = Job("Start Bank", [], key=KAFKA, verb="start")
            start.done, start.ok = True, False
            start.lines = [" bakery-web Building ", "#4 ERROR: failed to do request", line, "Failed with exit code 1"]
            image = "node:20-alpine" if "node" in line else "ghcr.io/acme/base:1"
            self.assertEqual(start.summary()["error"], f"Base image {image} could not be fetched")
        start.lines = ["ERROR [bakery-web internal] load metadata for docker.io/library/python:3.14-slim"]
        self.assertEqual(start.error(), "Base image python:3.14-slim could not be fetched")

    def test_a_registry_that_refused_the_pull_for_want_of_a_login(self):
        # Said in plain words, with the registry to log in to: the row says it, its menu offers Log in
        refused = 'Error response from daemon: Head "https://graviteeio.azurecr.io/v2/apim-gateway/manifests/x": ' \
                  "unauthorized: authentication required, visit https://aka.ms/acr/authorization"
        for line in (refused, "pull access denied for graviteeio.azurecr.io/apim-gateway, repository does not exist"):
            start = Job("Start Welcome", [], key=KAFKA, verb="start")
            start.done, start.ok, start.lines = True, False, [" apim-gateway Pulling ", line]
            self.assertEqual(self.failure(start),
                             self.said(start, error="Not logged in to Azure", login="graviteeio.azurecr.io"))
            self.assertEqual(start.summary()["error"], "Not logged in to Azure")
        # Docker Hub's refusal, or another reason, is said as it is
        hub = Job("Start Kafka", [], key=KAFKA, verb="start")
        hub.done, hub.ok, hub.lines = True, False, ["pull access denied for nope/kafka, repository does not exist"]
        self.assertEqual(self.failure(hub)["login"], None)

    def test_a_failed_stop_of_what_stopped_it_too(self):
        # The playground's stop failed once the on file was written: Kafka, which it brought and was stopping, still
        # runs, and was not started outside the Lab UI. The failure is Kafka's too, a stop.
        def stopped(title, key, ok=False):
            stops = [["platform", "platform"] if key == PLATFORM else ["playground", "welcome"], ["addon", "kafka"]]
            job = Job(title, [], key=key, verb="stop", changes={"stop": stops})
            job.done, job.ok, job.lines = True, ok, ["Error response from daemon: busy"]
            return job
        welcome = stopped("Stop Welcome", "playground:welcome")
        self.assertEqual(self.failure(welcome), self.said(welcome, "stop", "Error response from daemon: busy"))
        self.assertEqual(self.failure(welcome, kind="playground", uid="welcome")["id"], welcome.id)
        # It lasts until a job that starts or stops Kafka ends well
        self.assertIsNone(self.failure(welcome, ended("Start Kafka", True, KAFKA, start=[["addon", "kafka"]])))
        # Not the Stack's stop, which turns nothing off; not a failed start of something else; not one that went well
        self.assertIsNone(self.failure(stopped("Stop the stack", PLATFORM)))
        self.assertIsNone(self.failure(ended("Start Events", False, EVENTS, start=[["addon", "kafka"]])))
        self.assertIsNone(self.failure(stopped("Stop Welcome", "playground:welcome", ok=True)))

    def test_it_lasts_until_the_next_job_on_the_item(self):
        failed = ended("Start Kafka", False, KAFKA)
        self.assertIsNone(self.failure(failed, ended("Start Kafka", True, KAFKA)))
        again = ended("Start Kafka", False, KAFKA)
        self.assertEqual(self.failure(failed, again), self.said(again))
        self.assertIsNone(self.failure(failed, Job("Start Kafka", [], key=KAFKA)))   # it runs: not failed yet

    def test_a_job_on_something_else_that_starts_or_stops_the_item_ends_it(self):
        failed = ended("Start Kafka", False, KAFKA)
        for later in (ended("Start Events", True, EVENTS, start=[["addon", "kafka"], ["addon", "kafka-desk"]]),
                      ended("Stop the stack", True, PLATFORM, stop=[["platform", "platform"], ["addon", "kafka"]])):
            self.assertIsNone(self.failure(failed, later), later.title)

    def test_a_job_that_leaves_the_item_alone_or_fails_keeps_it(self):
        failed = ended("Start Kafka", False, KAFKA)
        for later in (ended("Start Jade", True, "addon:jade", start=[["addon", "jade"]]),
                      ended("Start Events", False, EVENTS, start=[["addon", "kafka"]]),
                      Job("Start Events", [], key=EVENTS, changes={"start": [["addon", "kafka"]]})):
            self.assertEqual(self.failure(failed, later), self.said(failed), later.title)


if __name__ == "__main__":
    unittest.main()
