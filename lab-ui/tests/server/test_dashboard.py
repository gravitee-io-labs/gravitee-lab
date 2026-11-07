"""dashboard: Docker's sizes and answers as `docker info`, `system df` and `stats` print them, the channels of a version, the
products against the versions a registry lists, the whole answer with its caches, and the load of
the containers. No Docker, no registry: what they would answer is given."""

import json
import threading
import time
import unittest
from urllib.error import URLError

from lab_ui.docker import compose
from lab_ui.server import dashboard

from .. import Case

INFO = {"ServerVersion": "29.7.2", "OperatingSystem": "Docker Desktop", "OSType": "linux", "NCPU": 6,
        "MemTotal": 8320028672, "Name": "docker-desktop", "ServerErrors": None}
DOWN = "Cannot connect to the Docker daemon at unix:///Users/me/.docker/run/docker.sock. Is the docker daemon running?"
# `docker system df --format '{{json .}}'`
DF = "\n".join(json.dumps(row) for row in [
    {"Active": "20", "Reclaimable": "42.7GB (72%)", "Size": "58.89GB", "TotalCount": "122", "Type": "Images"},
    {"Active": "19", "Reclaimable": "131.1kB (0%)", "Size": "2.243GB", "TotalCount": "20", "Type": "Containers"},
    {"Active": "5", "Reclaimable": "5.479GB (92%)", "Size": "5.944GB", "TotalCount": "73", "Type": "Local Volumes"},
    {"Active": "35", "Reclaimable": "9.709GB", "Size": "18.62GB", "TotalCount": "1630", "Type": "Build Cache"}])
USAGE = {"images": {"size": 58_890_000_000, "reclaimable": 42_700_000_000},
         "containers": {"size": 2_243_000_000, "reclaimable": 131_100},
         "volumes": {"size": 5_944_000_000, "reclaimable": 5_479_000_000},
         "cache": {"size": 18_620_000_000, "reclaimable": 9_709_000_000},
         "total": {"size": 85_697_000_000, "reclaimable": 57_888_131_100}}
# What `docker stats --no-stream --format '{{json .}}'` prints
STATS = ('{"CPUPerc":"1.50%","ID":"6f2efa4c29cd","MemUsage":"195.8MiB / 7.749GiB","Name":"backend"}\n'
         '{"CPUPerc":"3.20%","ID":"49aa4daf77df","MemUsage":"741.2MiB / 7.749GiB","Name":"gateway"}\n'
         '{"CPUPerc":"0.25%","ID":"3448b35c0e4a","MemUsage":"1.106GiB / 7.749GiB","Name":"redis"}\n')


def waited(test, until):
    """Waits for something another thread does."""
    deadline = time.time() + 5
    while not until() and time.time() < deadline:
        time.sleep(0.005)
    test.assertTrue(until())


class Sizes(Case):
    def test_a_size_as_docker_writes_it(self):
        for text, size in (("58.89GB", 58_890_000_000), ("131.1kB", 131_100), ("0B", 0),
                           ("42.7GB (72%)", 42_700_000_000), ("731.8MiB", round(731.8 * 1024 ** 2)),
                           ("7.749GiB", round(7.749 * 1024 ** 3)), ("1.5 TB", 1_500_000_000_000), ("", 0), (None, 0),
                           ("--", 0), ("12 parsecs", 0)):
            self.assertEqual(dashboard.size(text), size, text)

    def test_what_docker_holds(self):
        self.assertEqual(dashboard.parse_df(DF), USAGE)

    def test_what_is_missing_counts_for_nothing(self):
        found = dashboard.parse_df('{"Type":"Images","TotalCount":"3","Size":"1GB","Reclaimable":"0B"}\nnot json\n')
        self.assertEqual(found["images"], {"size": 1_000_000_000, "reclaimable": 0})
        nothing = {"size": 0, "reclaimable": 0}
        self.assertEqual((found["volumes"], found["cache"], found["containers"]), (nothing, nothing, nothing))
        self.assertEqual(found["total"], {"size": 1_000_000_000, "reclaimable": 0})
        self.assertIsNone(dashboard.parse_df(""))
        self.assertIsNone(dashboard.parse_df("Cannot connect to the Docker daemon"))

    def test_what_is_unused_never_exceeds_what_the_type_takes(self):
        # Docker rounds both columns on their own: 1.0004GB is "1GB" in one and "1.001GB" in the other
        found = dashboard.parse_df('{"Type":"Local Volumes","TotalCount":"2","Size":"1GB","Reclaimable":"1.001GB (100%)"}')
        self.assertEqual(found["volumes"]["reclaimable"], 1_000_000_000)
        self.assertEqual(found["total"], {"size": 1_000_000_000, "reclaimable": 1_000_000_000})


class Docker(Case):
    """What Docker holds, kept a minute and read again when asked; what it says of itself is the state's reading
    (compose.docker_info)."""

    def setUp(self):
        for prefix in ("local:dashboard:", "docker:"):
            compose.CACHE.drop(prefix)
            self.addCleanup(compose.CACHE.drop, prefix)
        self.answers = {"info": (0, json.dumps(INFO), ""), "version": (0, "v5.4.0\n", ""), "df": (0, DF, "")}
        self.run = self.patch(dashboard.compose, "run", side_effect=self.answer)

    def answer(self, command, **more):
        kind = {"info": "info", "version": "version", "df": "system"}
        return self.answers[next(name for name, word in kind.items() if word in command)]

    def asked(self, word):
        return sum(word in call.args[0] for call in self.run.call_args_list)

    def test_what_docker_holds_comes_with_when_it_was_read(self):
        before = int(time.time())
        found = dashboard._usage()
        self.assertEqual({key: found[key] for key in USAGE}, USAGE)
        self.assertGreaterEqual(found["read_at"], before)
        self.assertEqual(self.run.call_args.args[0], ["docker", "system", "df", "--format", "{{json .}}"])

    def test_docker_that_does_not_say_what_it_holds_is_asked_again(self):
        self.answers["df"] = (1, "", DOWN)
        self.assertIsNone(dashboard._usage())
        self.assertIsNone(dashboard._usage())
        self.assertEqual(self.asked("system"), 2)   # a failure is not kept
        self.answers["df"] = (0, DF, "")
        self.assertEqual(dashboard._usage()["images"]["size"], 58_890_000_000)

    def test_a_minute(self):
        self.assertEqual((dashboard.DOCKER_TTL, dashboard.LATEST_TTL), (60, 3600))
        for _ in range(3):
            dashboard.cores(), dashboard._usage()
        self.assertEqual((self.asked("info"), self.asked("system")), (1, 1))
        self.patch(dashboard, "DOCKER_TTL", 0)
        dashboard._usage()
        self.assertEqual(self.asked("system"), 2)

    def test_the_cores_docker_has(self):
        self.assertEqual(dashboard.cores(), 6)
        compose.CACHE.drop("docker:")
        self.answers["info"] = (1, "", DOWN)
        self.patch(dashboard.os, "cpu_count", return_value=11)
        self.assertEqual(dashboard.cores(), 11)   # the machine's when Docker does not say
        self.patch(dashboard.os, "cpu_count", return_value=None)
        self.assertEqual(dashboard.cores(), 1)


APIM = {"kind": "platform", "id": "api-management", "name": "API Management"}
AM = {"kind": "platform", "id": "access-management", "name": "Access Management"}
AE = {"kind": "platform", "id": "alert-engine", "name": "Alert Engine"}
DATABASES = {"kind": "platform", "id": "databases", "name": "Databases"}
HYSSOP = {"kind": "addon", "id": "hyssop", "name": "Hybrid gateway"}
# name: (owner, image as written, its tag, the registry a variable chose)
SERVICES = {
    "mongodb": (DATABASES, "mongo:${MONGODB_VERSION:-8.0.29}", "8.0.29", None),
    "apim-management-api": (APIM, "${APIM_REGISTRY:-graviteeio}/apim-management-api:${APIM_VERSION:-4.12.20}",
                            "4.12.20", "graviteeio"),
    "gamma-ui": (APIM, "${APIM_REGISTRY:-graviteeio}/gamma-ui:${APIM_VERSION:-4.12.20}", "4.12.20", "graviteeio"),
    "apim-gateway": (APIM, "${APIM_REGISTRY:-graviteeio}/apim-gateway:${APIM_GATEWAY_VERSION:-${APIM_VERSION:-4.12.20}"
                     "-debian}", "4.12.20-debian", "graviteeio"),
    "am-gateway": (AM, "${AM_REGISTRY:-graviteeio}/am-gateway:${AM_VERSION:-4.12.7}", "4.12.7", "graviteeio"),
    "am-management-api": (AM, "${AM_REGISTRY:-graviteeio}/am-management-api:${AM_VERSION:-4.12.7}", "4.12.7",
                          "graviteeio"),
    "alert-engine": (AE, "${AE_REGISTRY:-graviteeio}/ae-engine:${AE_VERSION:-3.0.2}", "3.0.2", "graviteeio"),
    "apim-hyssop-gateway": (HYSSOP, "${APIM_REGISTRY:-graviteeio}/apim-gateway:${HYSSOP_GATEWAY_VERSION:-4.12.20}",
                            "4.12.20", "graviteeio"),
}


def project():
    services = {name: {"label": name, "owner": owner, "raw_image": raw, "image": f"{registry or 'library'}/x:{tag}",
                       "tag": tag, "registry": registry, "image_tags": None}
                for name, (owner, raw, tag, registry) in SERVICES.items()}
    return {"groups": [{"id": "api-management"}, {"id": "access-management"}, {"id": "alert-engine"},
                       {"id": "databases"}], "addons": {}, "setups": {}, "playgrounds": {}, "services": services}


def running(**tags):
    """The containers of services that run these tags: {service: tag}."""
    return {name: {"state": "running", "image": f"graviteeio/{name}:{tag}"} for name, tag in tags.items()}


class Products(Case):
    """The Gravitee products of the platform against the registry, which is read in the background."""

    def setUp(self):
        self.project = project()
        for cleanup in (dashboard._failed.clear, dashboard._reading.clear):
            cleanup()
            self.addCleanup(cleanup)
        compose.CACHE.drop("dashboard:")
        self.addCleanup(compose.CACHE.drop, "dashboard:")
        self.hub = {"APIM_VERSION": ["4.13.0-alpha.4", "latest", "4.12.21-debian", "4.12.21", "4.12.20"],
                    "AM_VERSION": ["4.12.8", "4.12.7", "4.13.0-alpha.7-debian"],
                    "AE_VERSION": ["latest", "3.0.2", "3.0.1", "nightly"],
                    "HYSSOP_GATEWAY_VERSION": ["4.13.0-alpha.1-debian", "4.12.21-debian", "4.12.20-debian"]}
        self.listing = lambda p, component, registry=None: [{"name": name, "updated": None}
                                                            for name in self.hub[component]]
        self.tags = self.patch(dashboard.images, "tags", side_effect=self.listing)
        # The reads a test started end before the next one begins: a thread left over would take the keys of the
        # next test off the reads under way (cleanups run last in, first out: this one runs before the patch goes)
        self.addCleanup(self.settle)

    def settle(self):
        waited(self, lambda: not dashboard._reading)

    def test_one_row_per_product_of_the_platform_in_the_order_of_its_groups(self):
        rows, _ = dashboard.products(self.project, {})
        # The databases are not Gravitee's, the hybrid gateway an add-on's, the gateway the product of its group
        self.assertEqual([(row["id"], row["name"]) for row in rows], [
            ("api-management", "API Management"), ("access-management", "Access Management"),
            ("alert-engine", "Alert Engine")])
        # What a row says: no link to a page, no text status
        self.assertEqual(sorted(rows[0]), ["channel", "deployed", "deployed_state", "id", "latest", "name", "status"])

    def test_the_tag_that_runs_else_the_one_configured(self):
        containers = {**running(**{"apim-management-api": "4.13.0-alpha.4"}),
                      "am-gateway": {"state": "exited", "image": "graviteeio/am-gateway:4.12.8"}}
        rows, _ = dashboard.products(self.project, containers)
        self.assertEqual([(row["deployed"], row["deployed_state"]) for row in rows],
                         [("4.13.0-alpha.4", "running"), ("4.12.7", "configured"), ("3.0.2", "configured")])
        rows, _ = dashboard.products(self.project, {})   # Docker does not answer
        self.assertEqual({row["deployed_state"] for row in rows}, {"configured"})

    def test_services_on_another_tag_than_the_rest_show_the_oldest(self):
        rows, _ = dashboard.products(self.project, running(**{"apim-management-api": "4.12.21", "gamma-ui": "4.12.20"}))
        self.assertEqual(rows[0]["deployed"], "4.12.20")
        rows, _ = dashboard.products(self.project, running(**{"apim-management-api": "latest", "gamma-ui": "4.12.20"}))
        self.assertEqual(rows[0]["deployed"], "4.12.20")
        self.assertEqual(dashboard._oldest({"b", "a"}), "a")

    def test_versions_fill_in_without_the_page_waiting_for_them(self):
        containers = running(**{"apim-management-api": "4.12.20", "am-gateway": "4.13.0-alpha.7",
                                "alert-engine": "3.0.2"})
        block = threading.Event()
        self.tags.side_effect = lambda p, component, registry=None: block.wait(5) and [{"name": "4.12.21"}]
        rows, pending = dashboard.products(self.project, containers)
        self.assertTrue(pending)
        self.assertEqual({(row["latest"], row["status"]) for row in rows}, {(None, "unknown")})
        _, pending = dashboard.products(self.project, containers)   # no second read while one is under way
        self.assertTrue(pending)
        block.set()
        self.settle()
        # Three reads of this project, one per component (a read still under way for another test is not counted)
        self.assertEqual(len([call for call in self.tags.call_args_list if call.args[0] is self.project]), 3)
        rows, pending = dashboard.products(self.project, containers)   # the page asks again, and finds them
        self.assertFalse(pending)
        self.assertEqual(rows[0]["latest"], "4.12.21")

    def test_the_status_of_each_product_by_what_it_runs_or_is_configured_with(self):
        for containers, rows in (
                # A release, and a pre-release followed along its own channel
                (running(**{"apim-management-api": "4.12.20", "am-gateway": "4.13.0-alpha.7", "alert-engine": "3.0.2"}),
                 [("4.12.20", "stable", "4.12.21", "newer"), ("4.13.0-alpha.7", "pre-release", "4.13.0-alpha.7", "latest"),
                  ("3.0.2", "stable", "3.0.2", "latest")]),
                (running(**{"apim-management-api": "4.13.0-alpha.3", "am-gateway": "4.13.0-alpha.7"}),
                 [("4.13.0-alpha.3", "pre-release", "4.13.0-alpha.4", "newer"), ("4.13.0-alpha.7", "pre-release", "4.13.0-alpha.7", "latest"),
                  ("3.0.2", "stable", "3.0.2", "latest")]),
                # What does not run is judged by the version configured
                ({}, [("4.12.20", "stable", "4.12.21", "newer"), ("4.12.7", "stable", "4.12.8", "newer"), ("3.0.2", "stable", "3.0.2", "latest")])):
            dashboard.products(self.project, containers)
            self.settle()
            found, pending = dashboard.products(self.project, containers)
            self.assertFalse(pending)
            self.assertEqual([(row["deployed"], row["channel"], row["latest"], row["status"]) for row in found], rows)

    def test_the_registry_asked_is_the_one_the_project_names_by_default(self):
        dashboard.products(self.project, {})
        self.settle()
        self.assertEqual(sorted((call.args[1], call.args[2]) for call in self.tags.call_args_list),
                         [("AE_VERSION", "graviteeio"), ("AM_VERSION", "graviteeio"), ("APIM_VERSION", "graviteeio")])

    def test_a_registry_that_does_not_answer_is_quiet_and_asked_again_later(self):
        self.tags.side_effect = URLError("offline")
        dashboard.products(self.project, {})
        self.settle()
        rows, pending = dashboard.products(self.project, {})
        self.assertFalse(pending)   # nothing is awaited: the page says nothing of it
        self.assertEqual({(row["latest"], row["status"]) for row in rows}, {(None, "unknown")})
        self.assertEqual(self.tags.call_count, 3)   # not before a minute has passed
        self.patch(dashboard, "RETRY", 0)
        self.tags.side_effect = self.listing
        dashboard.products(self.project, {})
        self.settle()
        self.assertEqual(self.tags.call_count, 6)
        self.assertEqual(dashboard.products(self.project, {})[0][0]["latest"], "4.12.21")

    def test_a_registry_that_does_not_know_the_repository_is_quiet_too(self):
        self.tags.side_effect = RuntimeError("Not found on Docker Hub")
        dashboard.products(self.project, {})
        self.settle()
        self.assertEqual(dashboard.products(self.project, {})[1], False)

    def test_what_a_registry_lists_is_kept_an_hour(self):
        dashboard.products(self.project, {})
        self.settle()
        for _ in range(3):
            dashboard.products(self.project, {})
        self.assertEqual(self.tags.call_count, 3)


class Read(Case):
    """The whole answer, with Docker read at once and kept."""

    def setUp(self):
        self.project = project()
        for cleanup in (dashboard._failed.clear, dashboard._reading.clear):
            cleanup()
            self.addCleanup(cleanup)
        for prefix in ("local:dashboard:", "dashboard:", "docker:"):
            compose.CACHE.drop(prefix)
            self.addCleanup(compose.CACHE.drop, prefix)
        self.answers = {"info": (0, json.dumps(INFO), ""), "version": (0, "v5.4.0\n", ""), "df": (0, DF, "")}
        self.run = self.patch(dashboard.compose, "run", side_effect=lambda command, **more: self.answers[
            next(name for name, word in {"info": "info", "version": "version", "df": "system"}.items()
                 if word in command)])
        self.containers = self.patch(dashboard.compose, "containers", return_value=(running(**{
            "apim-management-api": "4.12.21", "alert-engine": "3.0.2"}), None))
        self.patch(dashboard.images, "tags", side_effect=lambda p, component, registry=None: [{"name": "4.12.21"}])

    def asked(self, word):
        return sum(word in call.args[0] for call in self.run.call_args_list)

    def test_the_answer(self):
        found = dashboard.read(self.project)
        # What runs and what is on is the state's, which the page draws live: none of it is read here
        self.assertEqual(set(found), {"docker", "data", "products", "pending"})
        self.assertEqual(found["docker"], {"engine": "29.7.2", "compose": "5.4.0", "os": "Docker Desktop", "cpus": 6,
                                           "memory": 8320028672})
        self.assertEqual({key: found["data"][key] for key in USAGE}, USAGE)
        self.assertIsInstance(found["data"]["read_at"], int)
        self.assertEqual([row["id"] for row in found["products"]], ["api-management", "access-management",
                                                                    "alert-engine"])
        self.assertTrue(found["pending"])   # the registry is being read: nothing waited for it
        json.dumps(found)   # what the route sends

    def test_docker_is_asked_once_a_minute_and_again_on_refresh(self):
        for _ in range(3):
            dashboard.read(self.project)
        self.assertEqual((self.asked("info"), self.asked("version"), self.asked("system")), (1, 1, 1))
        dashboard.read(self.project, refresh=True)   # a fresh read, which the cache then keeps
        self.assertEqual((self.asked("info"), self.asked("version"), self.asked("system")), (2, 2, 2))
        dashboard.read(self.project)
        self.assertEqual(self.asked("system"), 2)
        self.assertEqual(self.containers.call_count, 5)   # what runs is not kept: it is cheap

    def slow_df(self):
        """`docker system df` that does not answer until the test lets it: (the event that lets it, what it was asked)."""
        release, started, quick = threading.Event(), threading.Event(), self.run.side_effect
        self.addCleanup(release.set)

        def slow(command, **more):
            if "system" in command:
                started.set()
                release.wait(5)
            return quick(command, **more)
        self.run.side_effect = slow
        return release, started

    def test_what_docker_holds_does_not_hold_up_the_rest_and_the_next_read_has_it(self):
        release, started = self.slow_df()
        self.patch(dashboard, "DF_WAIT", 0.05)
        found = dashboard.read(self.project)
        self.assertTrue(started.is_set())
        self.assertIsNone(found["data"])
        self.assertTrue(found["pending"])   # asked for again
        self.assertEqual(found["docker"]["engine"], "29.7.2")   # the rest is there
        self.assertEqual([row["id"] for row in found["products"]], ["api-management", "access-management", "alert-engine"])
        again = dashboard.read({"groups": [], "addons": {}, "setups": {}, "playgrounds": {}, "services": {}})   # no registry to wait for
        self.assertEqual((again["data"], again["pending"]), (None, True))
        self.assertEqual(self.asked("system"), 1, "the read under way is not started again")
        release.set()
        waited(self, lambda: compose.CACHE.kept("local:dashboard:df", dashboard.DOCKER_TTL))
        found = dashboard.read(self.project)
        self.assertEqual(found["data"]["images"]["size"], 58_890_000_000)
        self.assertEqual(self.asked("system"), 1)

    def test_a_refresh_waits_for_what_docker_holds_as_long_as_it_takes(self):
        dashboard.read(self.project)
        release, _ = self.slow_df()
        self.patch(dashboard, "DF_WAIT", 0)
        threading.Timer(0.1, release.set).start()
        found = dashboard.read(self.project, refresh=True)
        self.assertEqual(found["data"]["images"]["size"], 58_890_000_000)
        self.assertEqual(self.asked("system"), 2)

    def test_what_the_read_found_is_the_answer_though_the_cache_lost_it_since(self):
        # What is kept ends its minute while the rest is read: no data and nothing pending would never be asked for again
        self.patch(dashboard, "products", side_effect=lambda *_: (compose.CACHE.drop("local:"), ([], False))[1])
        found = dashboard.read(self.project)
        self.assertEqual((found["data"]["images"]["size"], found["pending"]), (58_890_000_000, False))

    def test_a_job_that_ends_drops_what_docker_held(self):
        dashboard.read(self.project)
        compose.CACHE.drop("local:")   # jobs.py, when a job ends
        dashboard.read(self.project)
        self.assertEqual(self.asked("system"), 2)

    def test_docker_that_does_not_answer_is_the_error_and_the_rest_of_the_answer(self):
        self.answers["info"] = (1, json.dumps({"ServerVersion": "", "ServerErrors": [DOWN]}), "")
        self.answers["df"] = (1, "", DOWN)
        self.containers.return_value = ({}, "Cannot connect to the Docker daemon")
        found = dashboard.read(self.project)
        self.assertEqual(found["docker"], {"engine": None, "compose": "5.4.0", "os": None, "cpus": None,
                                           "memory": None, "error": "Cannot connect to the Docker daemon"})
        self.assertIsNone(found["data"])
        self.assertEqual({row["deployed_state"] for row in found["products"]}, {"configured"})

    def test_what_docker_info_says_is_the_error_when_nothing_else_does(self):
        self.answers["info"] = (1, json.dumps({"ServerVersion": "", "ServerErrors": [DOWN]}), "")
        found = dashboard.read(self.project)
        self.assertEqual(found["docker"]["error"], DOWN)
        self.assertEqual(found["data"]["images"]["size"], 58_890_000_000)   # what Docker could still say

    def test_the_project_that_does_not_load_has_no_products(self):
        found = dashboard.read({"groups": [], "addons": {}, "setups": {}, "playgrounds": {}, "services": {}})
        self.assertEqual((found["products"], found["pending"]), ([], False))


class Load(Case):
    """The load of the lab's containers, from one reading of `docker stats --no-stream`."""

    def setUp(self):
        compose.CACHE.drop("dashboard:")
        self.addCleanup(compose.CACHE.drop, "dashboard:")
        self.names = self.patch(dashboard, "lab_names", return_value=None)
        self.ran = self.patch(dashboard.compose, "run", return_value=(0, STATS, ""))

    def read(self, cores=4):
        """The load without its time."""
        return {key: value for key, value in dashboard.load(cores).items() if key not in ("at", "top")}

    def test_what_a_reading_says(self):
        memory = round(195.8 * 1024 ** 2) + round(741.2 * 1024 ** 2) + round(1.106 * 1024 ** 3)
        self.assertEqual(self.read(), {"cpu_percent": 1.2, "memory_used": memory, "containers": 3})
        self.assertEqual(self.ran.call_args.args[0], ["docker", "stats", "--no-stream", "--format", "{{json .}}"])
        self.assertIsInstance(dashboard.load(4)["at"], int)

    def test_each_container_says_its_own_load_from_the_heaviest_in_memory(self):
        self.assertEqual(dashboard.load(4)["top"], [{"name": "redis", "cpu": 0.1, "memory": round(1.106 * 1024 ** 3)},
                                                     {"name": "gateway", "cpu": 0.8, "memory": round(741.2 * 1024 ** 2)},
                                                     {"name": "backend", "cpu": 0.4, "memory": round(195.8 * 1024 ** 2)}])

    def test_a_reading_is_shared_and_read_again_once_it_is_old(self):
        dashboard.load(4), dashboard.load(4)
        self.assertEqual(self.ran.call_count, 1)
        self.patch(dashboard, "LOAD_TTL", 0)
        dashboard.load(4)
        self.assertEqual(self.ran.call_count, 2)

    def test_what_is_not_a_container_is_ignored(self):
        self.ran.return_value = (0, "\n".join(["Error response from daemon: x", "", "[1, 2]", '"text"', '{"ID": ""}',
                                               '{"CPUPerc":"--","ID":"a","MemUsage":"0B / 0B","Name":"a"}']), "")
        self.assertEqual(self.read(1), {"cpu_percent": 0.0, "memory_used": 0, "containers": 1})

    def test_only_the_containers_that_count_make_the_load(self):
        # What else Docker runs is not the lab's load; every container counts while the names are not known
        self.names.return_value = {"backend", "redis"}
        memory = round(195.8 * 1024 ** 2) + round(1.106 * 1024 ** 3)
        self.assertEqual(self.read(), {"cpu_percent": 0.4, "memory_used": memory, "containers": 2})

    def test_nothing_that_runs_and_docker_that_does_not_answer_are_no_load(self):
        for answer in ((0, "", ""), (1, "", "Cannot connect to the Docker daemon")):
            with self.subTest(answer=answer):
                compose.CACHE.drop("dashboard:")
                self.ran.return_value = answer
                self.assertEqual(self.read(), {"cpu_percent": 0.0, "memory_used": 0, "containers": 0})

    def test_the_load_is_a_share_of_the_cores_docker_has(self):
        self.ran.return_value = (0, '{"CPUPerc":"150%","ID":"a","MemUsage":"1MiB / 2GiB"}\n{"CPUPerc":"250%","ID":"b","MemUsage":"1MiB / 2GiB"}\n', "")
        self.assertEqual([dashboard.load(cores)["cpu_percent"] for cores in (8, 6, 2, 1)], [50.0, 66.7, 100.0, 100.0])   # from 0 to 100


class Names(Case):
    def test_the_names_of_the_lab_containers_are_those_of_its_project_kept_a_few_seconds(self):
        compose.CACHE.drop()
        self.addCleanup(compose.CACHE.drop)
        self.patch(dashboard.compose, "project_name", return_value="gravitee")
        ran = self.patch(dashboard.compose, "run", return_value=(0, "gio-a\ngio-b\n", ""))
        self.assertEqual(dashboard.lab_names(), {"gio-a", "gio-b"})
        self.assertEqual(dashboard.lab_names(), {"gio-a", "gio-b"})
        self.assertEqual(ran.call_count, 1)
        self.assertIn("label=com.docker.compose.project=gravitee", ran.call_args.args[0])
        # Not known: every container counts, and it is asked again
        compose.CACHE.drop()
        ran.return_value = (1, "", "Cannot connect")
        self.assertIsNone(dashboard.lab_names())


if __name__ == "__main__":
    unittest.main()
