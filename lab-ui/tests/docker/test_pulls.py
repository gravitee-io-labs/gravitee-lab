"""The images a job pulls: Compose's JSON progress, as it printed it for a pull, read layer by layer. Nothing is run: a
command prints the lines, and the clock is a list of instants."""

import json
import unittest
from unittest import mock

from lab_ui.docker import compose, jobs
from lab_ui.docker.jobs import Job, awaited
from lab_ui.docker.pulls import Pulls

from .test_jobs import prepull

IMAGE = "Image graviteeio/apim-gateway:4.12.0"
UP = [*compose.COMPOSE, "-p", "gravitee", "up", "-d", "--no-deps", "apim-gateway", "redis"]


def said(*events):
    """Lines as Compose prints them with --progress json."""
    return [json.dumps(event) for event in events]


def layer(name, text, details="0B", **more):
    return {"id": name, "parent_id": IMAGE, "status": "Working", "text": text, "details": details, **more}


def downloading(name, current, total):
    return layer(name, "Downloading", f"{current}B", current=current, total=total, percent=current * 100 // total)


PULL = [
    {"id": IMAGE, "status": "Working", "text": "Pulling"},
    layer("aaaaaaaaaaaa", "Pulling fs layer"),
    layer("bbbbbbbbbbbb", "Pulling fs layer"),
    downloading("aaaaaaaaaaaa", 1_000_000, 4_000_000),
    downloading("bbbbbbbbbbbb", 500_000, 1_000_000),
    downloading("aaaaaaaaaaaa", 4_000_000, 4_000_000),
    layer("aaaaaaaaaaaa", "Download complete", status="Done", percent=100),
    downloading("bbbbbbbbbbbb", 1_000_000, 1_000_000),
    layer("bbbbbbbbbbbb", "Download complete", status="Done", percent=100),
    layer("aaaaaaaaaaaa", "Extracting", "1B", current=1),
    layer("aaaaaaaaaaaa", "Pull complete", status="Done", percent=100),
    layer("bbbbbbbbbbbb", "Pull complete", status="Done", percent=100),
    {"id": IMAGE, "status": "Done", "text": "Pulled"},
]


class Layers(unittest.TestCase):
    def test_an_image_is_followed_by_the_bytes_of_its_layers(self):
        pulls = Pulls(lambda: 0)
        seen = []
        for event in PULL:
            seen.append(pulls.event(event))
        # What a plain progress would print, nothing for a layer
        self.assertEqual([line for line in seen if line], [f"{IMAGE} Pulling", f"{IMAGE} Pulled"])
        view = pulls.view()["images"][0]
        self.assertEqual((view["image"], view["state"], view["current"], view["total"]),
                         ("graviteeio/apim-gateway:4.12.0", "pulled", 5_000_000, 5_000_000))

    def test_the_state_goes_from_downloading_to_extracting(self):
        pulls, states = Pulls(), []
        for event in PULL[:-1]:
            pulls.event(event)
            states.append(pulls.view()["images"][0]["state"])
        self.assertEqual(states[:8], ["downloading"] * 8)
        self.assertEqual(states[8:], ["extracting"] * 4)   # every layer is down, being unpacked

    def test_how_far_an_image_is_follows_what_is_known_of_its_size(self):
        pulls = Pulls()
        for event in PULL[:4]:
            pulls.event(event)
        self.assertEqual(pulls.fraction("graviteeio/apim-gateway:4.12.0"), 0.25)
        self.assertIsNone(pulls.fraction("redis:8"))
        self.assertIsNone(Pulls().view())

    def test_the_speed_is_what_the_last_seconds_downloaded(self):
        clock = [0.0]
        pulls = Pulls(lambda: clock[0])
        pulls.event({"id": IMAGE, "text": "Pulling"})
        self.assertIsNone(pulls.speed())
        for second, current in ((1, 1_000_000), (2, 3_000_000), (3, 5_000_000)):
            clock[0] = second
            pulls.event(downloading("aaaaaaaaaaaa", current, 10_000_000))
        self.assertEqual(pulls.speed(), 2_000_000)
        clock[0] = 20   # nothing came for a long time
        self.assertIsNone(pulls.speed())

    def test_an_image_that_is_there_is_not_said(self):
        pulls = Pulls()
        pulls.event({"id": "Image redis:8", "text": "Pulling"})
        self.assertIsNone(pulls.event({"id": "Image redis:8", "status": "Done", "text": "Skipped"}))
        self.assertIsNone(pulls.event({"id": "built", "status": "Done", "text": "Skipped", "details": "No image"}))
        self.assertIsNone(pulls.view())

    def test_an_image_that_is_refused_is_said_unless_the_pull_may_fail(self):
        refused = {"id": "Image ghcr.io/acme/x:1", "status": "Error", "text": "Error",
                   "details": "error from registry: denied\ndenied"}
        self.assertEqual(Pulls().event(refused), "Image ghcr.io/acme/x:1 Error error from registry: denied denied")
        pulls = Pulls()
        self.assertIsNone(pulls.event(refused, quiet=True))
        self.assertEqual(pulls.view()["images"][0]["state"], "failed")


class Command(unittest.TestCase):
    def test_an_up_is_preceded_by_a_pull_of_what_is_missing(self):
        self.assertEqual(jobs._pull_before(UP),
                         [*compose.COMPOSE, "-p", "gravitee", "pull", "--policy", "missing", "--ignore-buildable",
                          "--ignore-pull-failures", "apim-gateway", "redis"])

    def test_what_an_up_brings_with_it_is_pulled_too(self):
        # Without --no-deps the up starts what its services need: their images are pulled as well. A file called up
        # is not the command
        up = [*compose.COMPOSE, "-f", "up", "up", "-d", "kafka"]
        self.assertEqual(jobs._pull_before(up), [*compose.COMPOSE, "-f", "up", "pull", "--policy", "missing",
                                                 "--ignore-buildable", "--ignore-pull-failures", "--include-deps", "kafka"])
        self.assertEqual(jobs._pull_before([*compose.COMPOSE, "up", "-d"]),
                         [*compose.COMPOSE, "pull", "--policy", "missing", "--ignore-buildable",
                          "--ignore-pull-failures"])

    def test_other_commands_are_left_as_they_are(self):
        for cmd in ([*compose.COMPOSE, "stop", "redis"], [*compose.COMPOSE, "pull"], ["docker", "logs", "x"],
                    ["docker", "compose", "up", "-d"]):
            self.assertIsNone(jobs._pull_before(cmd))
        self.assertEqual(jobs._command([*compose.COMPOSE, "-p", "pull", "up"]), (6, "up"))
        self.assertEqual(jobs._json([*compose.COMPOSE, "pull"]), ["docker", "compose", "--progress", "json", "pull"])


class Run(unittest.TestCase):
    def run_job(self, cmd, *outputs, waits=None, code=0):
        """A job whose commands print these outputs in turn (lines), with the commands it started."""
        printed, started = iter(outputs), []

        def command(started_cmd, **options):
            started.append(started_cmd)
            return mock.Mock(stdout=iter(line + "\n" for line in next(printed)), wait=lambda: code, returncode=code)
        job = Job("Start the stack", [cmd], key="platform", verb="start", waits=waits)
        with mock.patch.object(jobs.subprocess, "Popen", command):
            job.run()
        return job, started

    def test_what_an_up_needs_is_pulled_first_and_told_as_a_plain_progress_does(self):
        waits = [awaited({"apim-gateway": {"container": "gio-apim-gateway", "oneshot": False}}, start=["apim-gateway"],
                         images=["graviteeio/apim-gateway:4.12.0"])]
        job, started = self.run_job(UP, said(*PULL), ["Container gio-apim-gateway  Started"], waits=waits)
        self.assertEqual([cmd[3:5] for cmd in started], [["json", "-p"], ["plain", "-p"]])
        self.assertTrue(prepull(started[0]) and not prepull(started[1]))
        self.assertEqual(job.lines[1:], [f"{IMAGE} Pulling", f"{IMAGE} Pulled", "Container gio-apim-gateway  Started"])
        self.assertEqual(job.pull_view()["images"][0]["state"], "pulled")
        self.assertTrue(job.ok)

    def test_the_bytes_of_a_download_move_the_progress_before_it_is_pulled(self):
        waits = [awaited({}, images=["graviteeio/apim-gateway:4.12.0"])]
        job = Job("Pull images", [[*compose.COMPOSE, "pull"]], key="platform", verb="pull", waits=waits)
        seen = []

        def command(cmd, **options):
            def lines():
                for event in PULL:
                    yield json.dumps(event) + "\n"
                    seen.append(job.progress)
            return mock.Mock(stdout=lines(), wait=lambda: 0, returncode=0)
        with mock.patch.object(jobs.subprocess, "Popen", command):
            job.run()
        self.assertEqual(seen[3], 0.225)   # a quarter of the bytes
        self.assertEqual(seen[8], 0.9)   # all downloaded, not yet pulled
        self.assertEqual((seen[-1], job.progress), (1, 1))

    def test_a_pull_that_fails_is_not_the_end_of_the_job_that_follows_it(self):
        refused = {"id": "Image ghcr.io/acme/x:1", "status": "Error", "text": "Error", "details": "denied"}
        job, started = self.run_job(UP, said(refused) + ["a warning"], ["Container gio-redis  Started"], code=0)
        self.assertEqual(job.lines[1:], ["Container gio-redis  Started"])   # the up says what it could not do
        self.assertTrue(job.ok)

    def test_an_explicit_pull_that_fails_says_why(self):
        refused = {"id": "Image ghcr.io/acme/x:1", "status": "Error", "text": "Error", "details": "denied"}
        job, _ = self.run_job([*compose.COMPOSE, "pull", "x"], said(refused), code=1)
        self.assertEqual((job.ok, job.lines[-1]), (False, "Image ghcr.io/acme/x:1 Error denied"))

    def test_a_job_canceled_while_it_pulls_does_not_start_what_follows(self):
        printed = []

        def command(cmd, **options):
            printed.append(cmd)
            job.cancel()
            return mock.Mock(stdout=iter([]), wait=lambda: 0, returncode=0, poll=lambda: 0)
        job = Job("Start the stack", [UP], key="platform", verb="start")
        with mock.patch.object(jobs.subprocess, "Popen", command):
            job.run()
        self.assertEqual((len(printed), job.ok, job.error()), (1, False, "Canceled"))
