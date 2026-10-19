"""dashboard: Docker's sizes and answers as `docker info`, `system df` and `stats` print them, the host's memory and
disk, the channels of a version, the products and the gateway of a platform against the versions a registry lists, the
whole answer with its caches, and the life of the one `docker stats` process. No Docker, no registry: what they would
answer is given."""

import json
import queue
import re
import threading
import time
import unittest
from collections import namedtuple
from urllib.error import URLError

from lab_ui.docker import compose
from lab_ui.server import dashboard
from lab_ui import paths

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
USAGE = {"images": {"count": 122, "size": 58_890_000_000, "reclaimable": 42_700_000_000},
         "containers": {"count": 20, "running": 19, "size": 2_243_000_000, "reclaimable": 131_100},
         "volumes": {"count": 73, "size": 5_944_000_000, "reclaimable": 5_479_000_000},
         "cache": {"count": 1630, "size": 18_620_000_000, "reclaimable": 9_709_000_000},
         "total": {"size": 85_697_000_000, "reclaimable": 57_888_131_100}}
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                6620.
Pages active:                            699376.
Pages inactive:                          697572.
Pages speculative:                          833.
Pages throttled:                              0.
Pages wired down:                        237073.
"""
MEMINFO = "MemTotal:       16384000 kB\nMemFree:          100000 kB\nMemAvailable:    8192000 kB\nBuffers: 1 kB\n"
# What `docker stats --format '{{json .}}'` prints: the first screen sends the cursor home, the next ones erase before
# that, every line may end with an erase to the end of the line, and a blank line ends the screen
FIRST = ('\x1b[H{"CPUPerc":"1.50%","ID":"6f2efa4c29cd","MemUsage":"195.8MiB / 7.749GiB","Name":"backend"}\x1b[K\n',
         '{"CPUPerc":"3.20%","ID":"49aa4daf77df","MemUsage":"741.2MiB / 7.749GiB","Name":"gateway"}\x1b[K\n',
         '{"CPUPerc":"0.25%","ID":"3448b35c0e4a","MemUsage":"1.106GiB / 7.749GiB","Name":"redis"}\x1b[K\n',
         '\x1b[K\n')
NEXT = ('\x1b[J\x1b[H{"CPUPerc":"6.00%","ID":"6f2efa4c29cd","MemUsage":"200MiB / 7.749GiB","Name":"backend"}\x1b[K\n',
        '{"CPUPerc":"6.00%","ID":"49aa4daf77df","MemUsage":"800MiB / 7.749GiB","Name":"gateway"}\x1b[K\n',
        '\x1b[K\n')


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
        self.assertEqual(found["images"], {"count": 3, "size": 1_000_000_000, "reclaimable": 0})
        nothing = {"count": 0, "size": 0, "reclaimable": 0}
        self.assertEqual((found["volumes"], found["cache"], found["containers"]),
                         (nothing, nothing, {**nothing, "running": 0}))
        self.assertEqual(found["total"], {"size": 1_000_000_000, "reclaimable": 0})
        self.assertIsNone(dashboard.parse_df(""))
        self.assertIsNone(dashboard.parse_df("Cannot connect to the Docker daemon"))

    def test_what_is_unused_is_what_docker_says_for_each_type_and_the_total_is_their_sum(self):
        # `docker system df` on a real machine: the sizes are in powers of 1000, the unused column has a percentage
        # except for the build cache, and the containers' is the writable layer of the ones that are stopped
        real = "\n".join(json.dumps(row) for row in [
            {"Active": "20", "Reclaimable": "42.14GB (71%)", "Size": "58.9GB", "TotalCount": "126", "Type": "Images"},
            {"Active": "20", "Reclaimable": "1.085MB (0%)", "Size": "2.244GB", "TotalCount": "22", "Type": "Containers"},
            {"Active": "5", "Reclaimable": "5.479GB (92%)", "Size": "5.948GB", "TotalCount": "73", "Type": "Local Volumes"},
            {"Active": "35", "Reclaimable": "9.846GB", "Size": "18.69GB", "TotalCount": "1653", "Type": "Build Cache"}])
        found = dashboard.parse_df(real)
        self.assertEqual([found[kind]["reclaimable"] for kind in dashboard.TYPES],
                         [42_140_000_000, 1_085_000, 5_479_000_000, 9_846_000_000])
        self.assertEqual(found["total"], {"size": 85_782_000_000, "reclaimable": 57_466_085_000})
        self.assertEqual(found["containers"]["running"], 20, "the containers that run, not the ones that exist")
        self.assertEqual(found["volumes"]["count"], 73)

    def test_what_is_unused_never_exceeds_what_the_type_takes(self):
        # Docker rounds both columns on their own: 1.0004GB is "1GB" in one and "1.001GB" in the other
        found = dashboard.parse_df('{"Type":"Local Volumes","TotalCount":"2","Size":"1GB","Reclaimable":"1.001GB (100%)"}')
        self.assertEqual(found["volumes"]["reclaimable"], 1_000_000_000)
        self.assertEqual(found["total"], {"size": 1_000_000_000, "reclaimable": 1_000_000_000})


class Docker(Case):
    """What Docker says of itself, kept a minute, and read again when asked."""

    def setUp(self):
        self.addCleanup(compose.CACHE.drop, "local:dashboard:")
        compose.CACHE.drop("local:dashboard:")
        self.answers = {"info": (0, json.dumps(INFO), ""), "version": (0, "v5.4.0\n", ""), "df": (0, DF, "")}
        self.run = self.patch(dashboard.compose, "run", side_effect=self.answer)

    def answer(self, command, **more):
        kind = {"info": "info", "version": "version", "df": "system"}
        return self.answers[next(name for name, word in kind.items() if word in command)]

    def asked(self, word):
        return sum(word in call.args[0] for call in self.run.call_args_list)

    def test_the_engine_as_docker_info_says_it(self):
        self.assertEqual(dashboard._info(), {"engine": "29.7.2", "os": "Docker Desktop", "cpus": 6,
                                            "memory": 8320028672})
        self.assertEqual(self.run.call_args.args[0], ["docker", "info", "--format", "{{json .}}"])

    def test_the_compose_version_has_no_v(self):
        self.assertEqual(dashboard._compose_version(), "5.4.0")
        self.answers["version"] = (1, "", "unknown command")
        compose.CACHE.drop("local:dashboard:")
        self.assertIsNone(dashboard._compose_version())

    def test_what_docker_holds_comes_with_when_it_was_read(self):
        before = int(time.time())
        found = dashboard._usage()
        self.assertEqual({key: found[key] for key in USAGE}, USAGE)
        self.assertGreaterEqual(found["read_at"], before)
        self.assertEqual(self.run.call_args.args[0], ["docker", "system", "df", "--format", "{{json .}}"])

    def test_docker_that_does_not_answer_is_said_in_its_words_and_asked_again(self):
        self.answers["info"] = (1, json.dumps({**INFO, "ServerVersion": "", "ServerErrors": [DOWN]}), "")
        self.answers["df"] = (1, "", DOWN)
        with self.assertRaisesRegex(RuntimeError, "Cannot connect to the Docker daemon"):
            dashboard._info()
        self.assertIsNone(dashboard._usage())
        self.answers["info"] = (1, "", "Client: x\nerror during connect: Get docker.sock: EOF\n")
        with self.assertRaisesRegex(RuntimeError, "error during connect"):   # the last line it printed
            dashboard._info()
        self.answers["info"] = (127, "", "")
        with self.assertRaisesRegex(RuntimeError, "Docker is not reachable"):
            dashboard._info()
        self.assertEqual(self.asked("info"), 3)   # a failure is not kept
        self.answers["info"] = (0, json.dumps(INFO), "")
        self.assertEqual(dashboard._info()["engine"], "29.7.2")

    def test_a_minute(self):
        self.assertEqual((dashboard.DOCKER_TTL, dashboard.LATEST_TTL), (60, 3600))
        for _ in range(3):
            dashboard._info(), dashboard._compose_version(), dashboard._usage()
        self.assertEqual((self.asked("info"), self.asked("version"), self.asked("system")), (1, 1, 1))
        self.patch(dashboard, "DOCKER_TTL", 0)
        dashboard._info(), dashboard._usage()
        self.assertEqual((self.asked("info"), self.asked("system")), (2, 2))

    def test_the_cores_docker_has(self):
        self.assertEqual(dashboard.cores(), 6)
        compose.CACHE.drop("local:dashboard:")
        self.answers["info"] = (1, "", DOWN)
        self.patch(dashboard.os, "cpu_count", return_value=11)
        self.assertEqual(dashboard.cores(), 11)   # the machine's when Docker does not say
        self.patch(dashboard.os, "cpu_count", return_value=None)
        self.assertEqual(dashboard.cores(), 1)


class Machine(Case):
    def test_macos_from_sysctl_and_vm_stat(self):
        self.patch(dashboard.platform, "system", return_value="Darwin")
        run = self.patch(dashboard.compose, "run", side_effect=lambda command, **more: {
            "sysctl": (0, "38654705664\n", ""), "vm_stat": (0, VM_STAT, "")}[command[0]])
        # Free, inactive and speculative pages: what a program can have without swapping
        self.assertEqual(dashboard._memory(), (38654705664, (6620 + 697572 + 833) * 16384))
        self.assertEqual([call.args[0] for call in run.call_args_list], [["sysctl", "-n", "hw.memsize"], ["vm_stat"]])

    def test_macos_that_does_not_say(self):
        self.patch(dashboard.platform, "system", return_value="Darwin")
        self.patch(dashboard.compose, "run", side_effect=lambda command, **more: {
            "sysctl": (0, "38654705664\n", ""), "vm_stat": (127, "", "not found")}[command[0]])
        self.assertEqual(dashboard._memory(), (38654705664, None))
        self.patch(dashboard.compose, "run", return_value=(0, "not a number", ""))
        self.assertEqual(dashboard._memory(), (None, None))

    def test_linux_and_wsl_from_proc_meminfo(self):
        self.patch(dashboard.platform, "system", return_value="Linux")
        self.folder()
        self.patch(dashboard, "MEMINFO", self.root / self.write("meminfo", MEMINFO))
        self.assertEqual(dashboard._memory(), (16384000 * 1024, 8192000 * 1024))
        self.assertEqual(dashboard.parse_meminfo("MemTotal: 5 kB\n"), (5 * 1024, None))
        self.patch(dashboard, "MEMINFO", self.root / "missing")
        self.assertEqual(dashboard._memory(), (None, None))

    def test_another_system_is_not_read(self):
        self.patch(dashboard.platform, "system", return_value="Windows")
        run = self.patch(dashboard.compose, "run")
        self.assertEqual(dashboard._memory(), (None, None))
        run.assert_not_called()

    def test_the_machine(self):
        Usage = namedtuple("Usage", "total used free")
        self.patch(dashboard, "_memory", return_value=(100, 40))
        self.patch(dashboard.os, "cpu_count", return_value=11)
        disk = self.patch(dashboard.shutil, "disk_usage", return_value=Usage(1000, 700, 300))
        self.assertEqual(dashboard.machine(), {"cpus": 11, "memory_total": 100, "memory_available": 40,
                                              "disk_total": 1000, "disk_free": 300})
        self.assertEqual(disk.call_args.args[0], dashboard.Path.home())   # the disk that holds the home folder


class Versions(Case):
    def test_a_tag_that_names_a_build(self):
        for tag, found in (("4.12.21", ((4, 12, 21), None)), ("3.0.2", ((3, 0, 2), None)),
                           ("4.13.0-alpha.8", ((4, 13, 0), "alpha.8")), ("4.13.0-rc1", ((4, 13, 0), "rc1")),
                           ("v1.21.0", ((1, 21, 0), None)), ("4.8.37-hotfix.1", ((4, 8, 37), "hotfix.1"))):
            self.assertEqual(dashboard.parse(tag), found, tag)

    def test_the_system_an_image_is_built_on_is_not_part_of_it(self):
        for tag, found in (("4.12.21-debian", ((4, 12, 21), None)), ("4.12.8-noble", ((4, 12, 8), None)),
                           ("4.12.20-chainguard", ((4, 12, 20), None)),
                           ("4.12.20-chainguard-fips", ((4, 12, 20), None)),
                           ("4.13.0-alpha.8-debian", ((4, 13, 0), "alpha.8")),
                           ("4.8.37-hotfix.1-noble", ((4, 8, 37), "hotfix.1"))):
            self.assertEqual(dashboard.parse(tag), found, tag)

    def test_a_tag_is_told_from_its_system(self):
        for tag, found in (("4.12.21-debian", ("4.12.21", "debian")), ("4.12.20-chainguard-fips", ("4.12.20", "chainguard-fips")),
                           ("4.13.0-alpha.8", ("4.13.0-alpha.8", "")), ("latest", ("latest", "")), ("debian", ("debian", ""))):
            self.assertEqual(dashboard.bare(tag), found, tag)

    def test_the_systems_are_those_of_the_images_page(self):
        source = (paths.WEB_DIR / "js" / "core.js").read_text(encoding="utf-8")
        listed = re.search(r"^export const VARIANTS = \[(.*?)\];", source, re.M).group(1)
        self.assertEqual(dashboard.VARIANTS, tuple(re.findall(r'"([^"]+)"', listed)))

    def test_a_tag_that_moves_names_no_version(self):
        for tag in ("latest", "latest-debian", "4", "4.12", "4.12-debian", "4.13.0-alpha", "4.13.0-alpha-debian",
                    "nightly", "4.9.0-kafka-desk", "sha256-abc", "", "master-latest"):
            self.assertIsNone(dashboard.parse(tag), tag)

    def test_versions_are_ordered_as_semver_orders_them(self):
        ordered = ["3.0.2", "4.12.20", "4.12.21", "4.13.0-alpha.2", "4.13.0-alpha.10", "4.13.0-beta.1", "4.13.0-rc.1",
                   "4.13.0", "4.13.1-alpha.1", "4.13.1"]
        self.assertEqual(sorted(reversed(ordered), key=lambda tag: dashboard._order(dashboard.parse(tag))), ordered)

    def test_the_latest_of_each_channel_among_the_tags_of_a_registry(self):
        tags = ["4.13.0-alpha-debian", "4.13.0-alpha.4-debian", "4.13.0-alpha", "4.13.0-alpha.4", "4.13.0-alpha.3",
                "4.10-debian", "4.10.32", "latest", "4", "4.12", "4.12.21-debian", "4.12.21", "4.12.20", "4.11.29",
                "4.13.0-alpha.10-chainguard", "4.9.36"]
        self.assertEqual(dashboard.latest(tags), ("4.12.21", "4.13.0-alpha.10"))
        self.assertEqual(dashboard.latest(["3.0", "3", "latest", "3.0.2", "3.0.1", "nightly", "2.3.3"]), ("3.0.2", None))

    def test_a_pre_release_below_the_stable_one_is_no_news(self):
        self.assertEqual(dashboard.latest(["4.13.0", "4.13.0-rc.2", "4.13.0-alpha.8", "4.12.21"]), ("4.13.0", None))
        self.assertEqual(dashboard.latest(["4.13.0-alpha.8"]), (None, "4.13.0-alpha.8"))
        self.assertEqual(dashboard.latest(["latest", "4.12", "nightly"]), (None, None))
        self.assertEqual(dashboard.latest([]), (None, None))

    def test_the_channel_a_version_is_of(self):
        for tag, found in (("4.12.21", "stable"), ("4.12.21-debian", "stable"), ("4.13.0-alpha.8", "pre-release"),
                           ("4.13.0-alpha.8-debian", "pre-release"), ("4.8.37-hotfix.1", "pre-release"),
                           ("latest", "stable"), ("4.12", "stable"), ("", "stable"), (None, "stable")):
            self.assertEqual(dashboard.channel(tag), found, tag)

    def test_a_version_against_the_latest_of_its_own_channel(self):
        stable, pre = "4.12.21", "4.13.0-alpha.4"
        for deployed, found in (
                ("4.12.20", ("newer", "4.12.21")), ("4.12.21", ("latest", "4.12.21")),
                ("4.12.21-debian", ("latest", "4.12.21")), ("4.12.22", ("latest", "4.12.22")),
                ("4.11.29", ("newer", "4.12.21")),
                # A stable version is never compared with a pre-release
                ("4.13.0-alpha.3", ("newer", "4.13.0-alpha.4")), ("4.13.0-alpha.3-debian", ("newer", "4.13.0-alpha.4")),
                ("4.13.0-alpha.4", ("latest", "4.13.0-alpha.4")), ("4.13.0-alpha.9", ("latest", "4.13.0-alpha.9")),
                ("latest", ("unknown", None)), ("4.12", ("unknown", None)), ("", ("unknown", None)),
                (None, ("unknown", None))):
            self.assertEqual(dashboard.judge(deployed, stable, pre), found, deployed)

    def test_a_pre_release_with_none_above_the_stable_one_follows_the_stable_one(self):
        self.assertEqual(dashboard.judge("4.13.0-alpha.2", "4.13.0", None), ("newer", "4.13.0"))
        # The registry lists nothing above the stable one: what runs is the highest version known
        self.assertEqual(dashboard.judge("4.13.0-alpha.2", "4.12.21", None), ("latest", "4.13.0-alpha.2"))

    def test_what_a_registry_did_not_say_is_unknown(self):
        self.assertEqual(dashboard.judge("4.12.20", None, "4.13.0-alpha.1"), ("unknown", None))
        self.assertEqual(dashboard.judge("4.12.20", None, None), ("unknown", None))
        self.assertEqual(dashboard.judge("4.13.0-alpha.1", None, None), ("unknown", None))
        self.assertEqual(dashboard.judge("4.13.0-alpha.1", None, "4.13.0-alpha.2"), ("newer", "4.13.0-alpha.2"))


APIM = {"kind": "platform", "id": "api-management", "name": "API Management"}
AM = {"kind": "platform", "id": "access-management", "name": "Access Management"}
AE = {"kind": "platform", "id": "alert-engine", "name": "Alert Engine"}
DATABASES = {"kind": "platform", "id": "databases", "name": "Databases"}
HYBRID = {"kind": "addon", "id": "hybrid", "name": "Hybrid gateway"}
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
    "apim-hybrid-gateway": (HYBRID, "${APIM_REGISTRY:-graviteeio}/apim-gateway:${HYBRID_GATEWAY_VERSION:-4.12.20}",
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
                    "HYBRID_GATEWAY_VERSION": ["4.13.0-alpha.1-debian", "4.12.21-debian", "4.12.20-debian"]}
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
        self.assertEqual(sorted(rows[0]), ["channel", "deployed", "deployed_state", "id", "latest", "name",
                                           "prerelease", "stable", "status"])

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
        self.assertEqual({(row["stable"], row["prerelease"], row["status"]) for row in rows}, {(None, None, "unknown")})
        _, pending = dashboard.products(self.project, containers)   # no second read while one is under way
        self.assertTrue(pending)
        block.set()
        self.settle()
        # Three reads of this project, one per component (a read still under way for another test is not counted)
        self.assertEqual(len([call for call in self.tags.call_args_list if call.args[0] is self.project]), 3)
        rows, pending = dashboard.products(self.project, containers)   # the page asks again, and finds them
        self.assertFalse(pending)
        self.assertEqual(rows[0]["stable"], "4.12.21")

    def test_the_status_of_each_product(self):
        containers = running(**{"apim-management-api": "4.12.20", "am-gateway": "4.13.0-alpha.7",
                                "alert-engine": "3.0.2"})
        dashboard.products(self.project, containers)
        self.settle()
        rows, pending = dashboard.products(self.project, containers)
        self.assertFalse(pending)
        self.assertEqual([(row["channel"], row["stable"], row["prerelease"], row["latest"], row["status"])
                          for row in rows], [
            ("stable", "4.12.21", "4.13.0-alpha.4", "4.12.21", "newer"),
            ("pre-release", "4.12.8", "4.13.0-alpha.7", "4.13.0-alpha.7", "latest"),
            ("stable", "3.0.2", None, "3.0.2", "latest")])

    def test_a_pre_release_is_followed_along_its_own_channel(self):
        containers = running(**{"apim-management-api": "4.13.0-alpha.3", "am-gateway": "4.13.0-alpha.7"})
        dashboard.products(self.project, containers)
        self.settle()
        rows, _ = dashboard.products(self.project, containers)
        self.assertEqual([(row["channel"], row["status"], row["latest"]) for row in rows[:2]],
                         [("pre-release", "newer", "4.13.0-alpha.4"), ("pre-release", "latest", "4.13.0-alpha.7")])
        self.assertEqual(rows[0]["stable"], "4.12.21")   # the stable one is information

    def test_a_product_that_does_not_run_is_judged_by_the_version_configured(self):
        dashboard.products(self.project, {})
        self.settle()
        rows, _ = dashboard.products(self.project, {})
        self.assertEqual([(row["deployed"], row["deployed_state"], row["status"], row["latest"]) for row in rows], [
            ("4.12.20", "configured", "newer", "4.12.21"), ("4.12.7", "configured", "newer", "4.12.8"),
            ("3.0.2", "configured", "latest", "3.0.2")])

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
        self.assertEqual({(row["stable"], row["prerelease"], row["status"]) for row in rows}, {(None, None, "unknown")})
        self.assertEqual(self.tags.call_count, 3)   # not before a minute has passed
        self.patch(dashboard, "RETRY", 0)
        self.tags.side_effect = self.listing
        dashboard.products(self.project, {})
        self.settle()
        self.assertEqual(self.tags.call_count, 6)
        self.assertEqual(dashboard.products(self.project, {})[0][0]["stable"], "4.12.21")

    def test_a_registry_that_does_not_know_the_repository_is_quiet_too(self):
        self.tags.side_effect = RuntimeError("Not published on Docker Hub")
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
        for prefix in ("local:dashboard:", "dashboard:"):
            compose.CACHE.drop(prefix)
            self.addCleanup(compose.CACHE.drop, prefix)
        self.patch(dashboard, "_flight", None)
        self.answers = {"info": (0, json.dumps(INFO), ""), "version": (0, "v5.4.0\n", ""), "df": (0, DF, "")}
        self.run = self.patch(dashboard.compose, "run", side_effect=lambda command, **more: self.answers[
            next(name for name, word in {"info": "info", "version": "version", "df": "system"}.items()
                 if word in command)])
        self.containers = self.patch(dashboard.compose, "containers", return_value=(running(**{
            "apim-management-api": "4.12.21", "alert-engine": "3.0.2"}), None))
        self.patch(dashboard.images, "tags", side_effect=lambda p, component, registry=None: [{"name": "4.12.21"}])
        self.patch(dashboard, "machine", return_value={"cpus": 11})

    def asked(self, word):
        return sum(word in call.args[0] for call in self.run.call_args_list)

    def test_the_answer(self):
        found = dashboard.read(self.project)
        # What runs and what is on is the state's, which the page draws live: none of it is read here
        self.assertEqual(set(found), {"docker", "machine", "data", "products", "pending"})
        self.assertEqual(found["docker"], {"engine": "29.7.2", "compose": "5.4.0", "os": "Docker Desktop", "cpus": 6,
                                           "memory": 8320028672})
        self.assertEqual({key: found["data"][key] for key in USAGE}, USAGE)
        self.assertIsInstance(found["data"]["read_at"], int)
        self.assertEqual(found["machine"], {"cpus": 11})
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
        self.assertIsNone(dashboard.read(self.project)["data"])
        self.assertEqual(self.asked("system"), 1, "the read under way is not started again")
        release.set()
        waited(self, lambda: dashboard._flight.done())
        found = dashboard.read(self.project)
        self.assertEqual(found["data"]["images"]["count"], 122)
        self.assertEqual(self.asked("system"), 1)

    def test_a_refresh_waits_for_what_docker_holds_as_long_as_it_takes(self):
        dashboard.read(self.project)
        release, _ = self.slow_df()
        self.patch(dashboard, "DF_WAIT", 0)
        threading.Timer(0.1, release.set).start()
        found = dashboard.read(self.project, refresh=True)
        self.assertEqual(found["data"]["images"]["count"], 122)
        self.assertEqual(self.asked("system"), 2)

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
        self.assertEqual(found["machine"], {"cpus": 11})
        self.assertEqual({row["deployed_state"] for row in found["products"]}, {"configured"})

    def test_what_docker_info_says_is_the_error_when_nothing_else_does(self):
        self.answers["info"] = (1, json.dumps({"ServerVersion": "", "ServerErrors": [DOWN]}), "")
        found = dashboard.read(self.project)
        self.assertEqual(found["docker"]["error"], DOWN)
        self.assertEqual(found["data"]["images"]["count"], 122)   # what Docker could still say

    def test_the_project_that_does_not_load_has_no_products(self):
        found = dashboard.read({"groups": [], "addons": {}, "setups": {}, "playgrounds": {}, "services": {}})
        self.assertEqual((found["products"], found["pending"]), ([], False))


class FakeProcess:
    """A `docker stats` that prints what it is given and then waits to be killed, or ends on its own."""

    def __init__(self, lines=(), ends=False):
        self.lines, self.killed, self.waited = queue.Queue(), False, False
        for line in lines:
            self.lines.put(line)
        if ends:
            self.lines.put(None)
        self.stdout = iter(self.lines.get, None)

    def kill(self):
        self.killed = True
        self.lines.put(None)

    def wait(self):
        self.waited = True


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Load(Case):
    """The samples of `docker stats` and the load they make."""

    def setUp(self):
        self.clock = Clock()
        self.stats = dashboard.Stats(clock=self.clock)

    def feed(self, lines):
        for line in lines:
            self.stats.feed(line)

    def load(self, cores):
        """The load without its time."""
        found = self.stats.read(cores)
        return {key: value for key, value in found.items() if key not in ("at", "top")}

    def test_a_sample_is_what_lies_between_two_screens(self):
        self.stats.since = self.clock()
        self.feed(FIRST)
        memory = round(195.8 * 1024 ** 2) + round(741.2 * 1024 ** 2) + round(1.106 * 1024 ** 3)
        self.assertEqual(self.load(4), {"cpu_percent": 1.2, "memory_used": memory, "containers": 3})
        self.clock.now += 0.5
        self.feed(NEXT[:2])   # the screen is not finished: the last sample stays
        self.assertEqual(self.stats.read(4)["containers"], 3)
        self.feed(NEXT[2:])
        self.assertEqual(self.load(4), {"cpu_percent": 3.0, "memory_used": 1000 * 1024 ** 2, "containers": 2})

    def test_each_container_says_its_own_load_from_the_heaviest_in_memory(self):
        self.stats.since = self.clock()
        self.feed(FIRST)
        self.assertEqual(self.stats.read(4)["top"], [{"name": "redis", "cpu": 0.1, "memory": round(1.106 * 1024 ** 3)},
                                                      {"name": "gateway", "cpu": 0.8, "memory": round(741.2 * 1024 ** 2)},
                                                      {"name": "backend", "cpu": 0.4, "memory": round(195.8 * 1024 ** 2)}])

    def test_the_cursor_sent_home_ends_a_screen_that_has_no_blank_line(self):
        self.feed(line.replace("\x1b[K", "") for line in FIRST[:3])
        self.feed([NEXT[0]])
        self.assertEqual(self.stats.read(1)["containers"], 3)   # the new screen has one line so far
        self.assertEqual(list(self.stats.batch), ["6f2efa4c29cd"])

    def test_a_container_that_comes_twice_is_a_new_sample(self):
        self.feed(['{"CPUPerc":"10%","ID":"a","MemUsage":"1MiB / 2GiB"}\n',
                   '{"CPUPerc":"20%","ID":"b","MemUsage":"1MiB / 2GiB"}\n',
                   '{"CPUPerc":"30%","ID":"a","MemUsage":"2MiB / 2GiB"}\n'])
        self.assertEqual((self.stats.read(1)["cpu_percent"], self.stats.read(1)["containers"]), (30.0, 2))
        self.assertEqual(self.stats.batch, {"a": (30.0, 2 * 1024 ** 2, "a")})

    def test_what_is_not_a_container_is_ignored(self):
        self.stats.since = self.clock()
        self.feed(["Error response from daemon: x\n", "\x1b[J\n", "\n", "[1, 2]\n", '"text"\n', '{"ID": ""}\n',
                   '{"CPUPerc":"--","ID":"a","MemUsage":"0B / 0B","Name":"a"}\n', "\x1b[K\n"])
        self.assertEqual(self.load(1), {"cpu_percent": 0.0, "memory_used": 0, "containers": 1})

    def test_only_the_containers_that_count_make_the_load(self):
        # What else Docker runs is not the lab's load; every container counts while the names are not known
        self.stats.names = {"backend", "redis"}
        self.feed(FIRST)
        memory = round(195.8 * 1024 ** 2) + round(1.106 * 1024 ** 3)
        self.assertEqual(self.load(4), {"cpu_percent": 0.4, "memory_used": memory, "containers": 2})
        self.stats.names = None
        self.feed(NEXT)
        self.assertEqual(self.stats.read(4)["containers"], 2)
        self.stats.names = set()
        self.feed(FIRST)
        self.assertEqual(self.stats.read(4)["containers"], 2)   # the sample before stands: no line of it counted

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

    def test_the_load_is_a_share_of_the_cores_docker_has(self):
        self.feed(['{"CPUPerc":"150%","ID":"a","MemUsage":"1MiB / 2GiB"}\n',
                   '{"CPUPerc":"250%","ID":"b","MemUsage":"1MiB / 2GiB"}\n', "\n"])
        self.assertEqual(self.stats.read(8)["cpu_percent"], 50.0)
        self.assertEqual(self.stats.read(6)["cpu_percent"], 66.7)
        self.assertEqual(self.stats.read(2)["cpu_percent"], 100.0)   # from 0 to 100
        self.assertEqual(self.stats.read(1)["cpu_percent"], 100.0)

    def test_none_while_the_first_sample_is_on_its_way_then_zeros_when_nothing_comes(self):
        self.stats.since = self.clock()
        self.assertIsNone(self.stats.read(4))
        self.clock.now += dashboard.STALE - 0.1
        self.assertIsNone(self.stats.read(4))
        self.clock.now += 0.2   # docker stats says nothing without a container, or Docker stopped
        self.assertEqual(self.load(4), {"cpu_percent": 0.0, "memory_used": 0, "containers": 0})
        self.assertIsInstance(self.stats.read(4)["at"], int)

    def test_a_sample_that_stopped_coming_is_no_load(self):
        self.feed(FIRST)
        self.clock.now += dashboard.STALE - 0.1
        self.assertEqual(self.stats.read(4)["containers"], 3)
        self.clock.now += 0.2
        self.assertEqual(self.stats.read(4)["containers"], 0)


class Process(Case):
    """The one `docker stats`: started by the first page, killed with the last, never left behind."""

    def setUp(self):
        self.spawned = []
        self.next = lambda: FakeProcess()
        self.stats = dashboard.Stats(self.spawn)

    def spawn(self):
        self.spawned.append(self.next())
        return self.spawned[-1]

    def stop(self):
        self.stats.thread.join(5)
        self.assertFalse(self.stats.thread.is_alive())

    def test_the_first_page_starts_it_and_the_last_kills_it(self):
        self.stats.join()
        waited(self, lambda: len(self.spawned) == 1)
        self.stats.join()
        self.stats.leave()
        time.sleep(0.05)
        self.assertEqual(len(self.spawned), 1)   # one for all the pages
        self.assertFalse(self.spawned[0].killed)
        self.stats.leave()
        self.stop()
        self.assertEqual((self.spawned[0].killed, self.spawned[0].waited), (True, True))   # and reaped
        self.assertEqual(self.stats.clients, 0)

    def test_the_next_page_starts_another(self):
        self.stats.join()
        waited(self, lambda: len(self.spawned) == 1)
        self.stats.leave()
        self.stop()
        self.stats.join()
        waited(self, lambda: len(self.spawned) == 2)
        self.assertFalse(self.spawned[1].killed)
        self.stats.leave()
        self.stop()
        self.assertTrue(all(process.killed and process.waited for process in self.spawned))

    def test_a_page_that_comes_and_goes_at_once_leaves_nothing_behind(self):
        for _ in range(20):
            self.stats.join()
            self.stats.leave()
            self.stop()
        self.assertTrue(all(process.killed and process.waited for process in self.spawned))

    def test_the_lines_it_prints_make_the_load(self):
        self.next = lambda: FakeProcess([*FIRST, *NEXT])
        self.stats.join()
        waited(self, lambda: self.stats.last is not None and self.stats.last[3] == 2)
        found = self.stats.read(6)
        self.assertEqual((found["cpu_percent"], found["containers"]), (2.0, 2))
        self.stats.leave()
        self.stop()

    def test_a_line_that_comes_after_the_last_page_left_is_not_for_the_next(self):
        gate = threading.Event()

        class Late(FakeProcess):
            def __init__(self):
                super().__init__()
                self.stdout = self.later()

            def later(self):
                gate.wait(5)   # printed once its page has gone and another has come
                yield FIRST[0]
        self.next = Late
        self.stats.join()
        waited(self, lambda: len(self.spawned) == 1)
        first = self.stats.thread
        self.stats.leave()
        self.next = lambda: FakeProcess()
        self.stats.join()
        gate.set()
        first.join(5)
        self.assertFalse(first.is_alive())
        self.assertEqual((self.stats.batch, self.stats.last), ({}, None))
        self.stats.leave()
        self.stop()

    def test_it_is_started_again_when_it_ends_on_its_own(self):
        self.patch(dashboard, "RESTART", 0)
        self.next = lambda: FakeProcess(ends=True)
        self.stats.join()
        waited(self, lambda: len(self.spawned) >= 3)   # Docker stopped: it is tried again
        self.stats.leave()
        self.stop()
        self.assertTrue(all(process.waited for process in self.spawned))

    def test_the_process_is_a_docker_stats_of_json_lines_read_as_utf8(self):
        popen = self.patch(dashboard.subprocess, "Popen")
        dashboard._stats_process()
        self.assertEqual(popen.call_args.args[0], ["docker", "stats", "--format", "{{json .}}"])
        self.assertEqual((popen.call_args.kwargs["encoding"], popen.call_args.kwargs["errors"]), ("utf-8", "replace"))
        self.assertEqual(popen.call_args.kwargs["stdin"], dashboard.subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
