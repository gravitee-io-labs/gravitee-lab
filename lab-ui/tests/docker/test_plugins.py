"""plugins: where a product's plugins are published, what its services load, and the steps that make it load them
again. No Docker, no download: what they would answer is given."""

import hashlib
import io
import unittest
import zipfile
from unittest import mock

from lab_ui.docker import compose, plugins
from lab_ui import paths
from lab_ui.docker.jobs import Job, JobFailed

from .. import Case
from .test_jobs import Docker, container, progress, replay

PROJECT = {
    "products": {"apim": {"name": "API Management", "folder": "plugins/apim",
                          "services": {"gateway": "/opt/gateway/plugins"}, "lib": {"gateway": "/opt/gateway/lib"}}},
    "services": {"gateway": {"container": "gio-gateway", "image": "graviteeio/apim-gateway:4", "label": "Gateway",
                             "oneshot": False,
                             "owner": {"kind": "platform", "id": "api-management", "name": "API Management"}}},
}
APIM = PROJECT["products"]["apim"]


class Catalog(Case):

    def test_sources_of_a_product(self):
        self.assertEqual(plugins._sources("am"), ["graviteeio-am/plugins/", "graviteeio-ee/am/plugins/",
                                                  "graviteeio-ee/plugins/", "plugins/"])

    def test_a_plugin_published_twice_is_the_one_of_the_most_specific_source(self):
        folders = {"graviteeio-apim/plugins/": ["graviteeio-apim/plugins/policies/"],
                   "graviteeio-ee/apim/plugins/": [], "graviteeio-ee/plugins/": ["graviteeio-ee/plugins/policies/"],
                   "plugins/": ["plugins/policies/"],
                   "graviteeio-apim/plugins/policies/": ["graviteeio-apim/plugins/policies/gravitee-policy-x/"],
                   "graviteeio-ee/plugins/policies/": ["graviteeio-ee/plugins/policies/gravitee-policy-x/",
                                                        "graviteeio-ee/plugins/policies/gravitee-policy-y/"],
                   "plugins/policies/": ["plugins/policies/gravitee-policy-y/", "plugins/policies/gravitee-policy-z/"]}
        with mock.patch.object(plugins, "_bucket_list", lambda prefix: (folders[prefix], [])):
            found = {p["name"]: (p["path"], p["edition"]) for p in plugins._catalog("apim")}
        self.assertEqual(found, {
            "gravitee-policy-x": ("graviteeio-apim/plugins/policies/gravitee-policy-x/", "community"),
            "gravitee-policy-y": ("graviteeio-ee/plugins/policies/gravitee-policy-y/", "enterprise"),
            "gravitee-policy-z": ("plugins/policies/gravitee-policy-z/", "community")})

    def test_latest_release(self):
        published = [{"version": "1.9.0"}, {"version": "1.10.0"}, {"version": "2.0.0-alpha.1"}]
        self.assertEqual(plugins._latest("x", published)["version"], "1.10.0")
        with self.assertRaises(JobFailed):
            plugins._latest("x", [{"version": "2.0.0-alpha.1"}])

    def test_refresh_reads_the_catalog_and_the_version_lists_again(self):
        for prefix in ("plugin-catalog:", "plugin-versions:"):
            compose.CACHE.drop(prefix)
            self.addCleanup(compose.CACHE.drop, prefix)
        path = "plugins/policies/gravitee-policy-x/"
        zips = [(f"{path}gravitee-policy-x-1.0.0.zip", None, 1)]

        def published():
            return [v["version"] for v in plugins.versions(path)]
        with mock.patch.object(plugins, "_bucket_list", lambda prefix: ([], zips if prefix == path else [])):
            plugins.catalog(PROJECT, "apim")
            self.assertEqual(published(), ["1.0.0"])
            zips.append((f"{path}gravitee-policy-x-1.1.0.zip", None, 1))
            plugins.catalog(PROJECT, "apim")
            self.assertEqual(published(), ["1.0.0"], "kept until a refresh")
            plugins.catalog(PROJECT, "apim", refresh=True)
            self.assertEqual(published(), ["1.0.0", "1.1.0"])

    def test_what_is_not_known_is_refused(self):
        for call in (lambda: plugins.installed(PROJECT, "nope"), lambda: plugins.catalog(PROJECT, "nope"),
                     lambda: plugins.install_steps(PROJECT, "nope", [])):
            self.assertRaises(paths.NotFound, call)
        for path in ("../etc/", "plugins/policies/x", None, "graviteeio-ee/apim/plugins/policies/../x/"):
            with self.assertRaises(ValueError):
                plugins.versions(path)


class Installed(Case):

    def setUp(self):
        self.folder(plugins)
        for prefix in ("local:plugins-installed:", "plugin-manifests:"):
            compose.CACHE.drop(prefix)
            self.addCleanup(compose.CACHE.drop, prefix)
        # The image each service's files are read from, by its id (compose.image_key)
        self.image = self.patch(compose, "image_key", return_value="sha256:running")
        self.patch(compose, "loaded", return_value=None)   # Compose cannot say which services it reads: all of them count

    def test_zips_by_plugin_name(self):
        lines = "1700 /opt/p/gravitee-policy-x-2.1.0.zip\nstat: nope\n1800 /opt/p/a-b-1.0.0-alpha.1.zip"
        found = plugins._zips(plugins._stat_lines(lines))
        self.assertEqual(found["gravitee-policy-x"],
                         {"version": "2.1.0", "file": "gravitee-policy-x-2.1.0.zip", "mtime": 1700})
        self.assertEqual(found["a-b"]["version"], "1.0.0-alpha.1")

    def test_manifest_of_a_zip(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("plugin.properties", "id=policy-x\r\nname=X\r\nfeature=apim-policy-x\r\n")
        path = self.root / "gravitee-policy-x-2.1.0.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("gravitee-policy-x-2.1.0.jar", inner.getvalue())
        self.assertEqual(plugins._zip_manifest(path), ("policy-x", "apim-policy-x"))
        (self.root / "bad-1.0.0.zip").write_text("no", encoding="utf-8")
        self.assertEqual(plugins._zip_manifest(self.root / "bad-1.0.0.zip"), (None, None))

    def test_bundled_plugins_are_read_in_the_container(self):
        asked = []

        def run(cmd, timeout=120, cwd=None):
            asked.append(cmd[:2])
            if cmd[-2] == plugins.STAT_SCRIPT:
                return 0, "1700 /opt/gateway/plugins/gravitee-policy-x-2.1.0.zip\n", ""
            return 0, "gravitee-policy-x-2.1.0.zip policy-x apim-policy-x\n", ""
        self.patch(compose, "run", run)
        self.assertEqual(plugins.installed(PROJECT, "apim"), {
            "bundled": {"gravitee-policy-x": {"gateway": "2.1.0"}}, "bundled_error": None, "pull": None, "ext": {},
            "services": ["gateway"], "features": {"gravitee-policy-x": "apim-policy-x"},
            "feature_by_id": {"policy-x": "apim-policy-x"}})
        self.assertEqual(asked, [["docker", "exec"], ["docker", "exec"]])

    def test_what_an_image_brings_is_kept_by_the_image_the_container_runs(self):
        # The tag saved changed, the container still runs the old image: what it brings is read again once it runs
        # the new one, not kept from the old one under the new name
        reads = []

        def run(cmd, timeout=120, cwd=None):
            if cmd[-2] == plugins.STAT_SCRIPT:
                return 0, "1700 /opt/gateway/plugins/gravitee-policy-x-2.1.0.zip\n", ""
            reads.append(self.image.return_value)
            return 0, f"gravitee-policy-x-2.1.0.zip policy-x {self.image.return_value}\n", ""
        self.patch(compose, "run", run)
        self.assertEqual(plugins.installed(PROJECT, "apim")["features"], {"gravitee-policy-x": "sha256:running"})
        compose.CACHE.drop("local:")   # a job ended: the container was made again from the new image
        self.image.return_value = "sha256:new"
        self.assertEqual(plugins.installed(PROJECT, "apim")["features"], {"gravitee-policy-x": "sha256:new"})
        compose.CACHE.drop("local:")
        plugins.installed(PROJECT, "apim")
        self.assertEqual(reads, ["sha256:running", "sha256:new"])   # each image read once

    def test_bundled_plugins_that_cannot_be_read_are_unknown_not_none(self):
        # No container, and the image is not here (or Docker took too long): nothing is said to be bundled
        self.patch(compose, "run", side_effect=[
            (1, "", "Error response from daemon: No such container: gio-gateway\n"),
            (124, "", "docker did not answer within 60 s"), (1, "", "Error: No such container: gio-gateway\n"),
            (125, "", "docker: Error response from daemon: No such image: graviteeio/apim-gateway:4.\n")] * 2)
        found = plugins.installed(PROJECT, "apim")
        self.assertEqual((found["bundled"], found["bundled_error"], found["pull"]),
                         ({}, "Bundled plugins unknown: docker did not answer within 60 s", None))
        # Not kept: asked again
        self.assertEqual(plugins.installed(PROJECT, "apim")["bundled_error"],
                         "Bundled plugins unknown: docker did not answer within 60 s")
        self.assertEqual(compose.run.call_count, 8)

    def test_an_image_that_is_not_here_or_docker_down_says_so_in_plain_words(self):
        # Not Docker's own words, nor its help hint: the image to pull, which the page offers
        missing = ("docker: Error response from daemon: No such image: graviteeio/apim-gateway:4\n\n"
                   "Run 'docker run --help' for more information\n")
        self.patch(compose, "shell", return_value=(125, "", missing))
        self.assertEqual(plugins._bundled(PROJECT, APIM),
                         ({}, "graviteeio/apim-gateway:4 is not pulled", "graviteeio/apim-gateway:4"))
        compose.shell.return_value = (1, "", "Cannot connect to the Docker daemon at unix:///x.sock. Is the docker "
                                             "daemon running?\n")
        self.assertEqual(plugins._bundled(PROJECT, APIM), ({}, "Docker is not reachable", None))
        compose.shell.return_value = (1, "", "")
        self.assertEqual(plugins._bundled(PROJECT, APIM)[1], "Bundled plugins unknown: exit code 1")

    def test_two_images_not_here_are_said_by_the_first(self):
        # The reason and the image to pull name the same one, the first: not one image named, the other pulled
        project = {**PROJECT, "services": {**PROJECT["services"], "api": {
            **PROJECT["services"]["gateway"], "container": "gio-api", "image": "graviteeio/apim-management-api:4"}}}
        target = {**APIM, "services": {"gateway": "/opt/gateway/plugins", "api": "/opt/api/plugins"}}
        self.patch(compose, "shell", side_effect=lambda service, args, timeout=120: (
            125, "", f"docker: Error response from daemon: No such image: {service['image']}\n"))
        self.assertEqual(plugins._bundled(project, target),
                         ({}, "graviteeio/apim-gateway:4 is not pulled", "graviteeio/apim-gateway:4"))

    def test_a_service_of_an_addon_that_is_off_is_neither_read_nor_pulled(self):
        # The hybrid gateway has no container, its image is not here, and Compose does not read its file: asking Docker
        # for its plugins fails, and `pull` of its service says "no such service". Nothing starts it, so it does not count
        project = {**PROJECT, "services": {**PROJECT["services"], "hybrid": {
            **PROJECT["services"]["gateway"], "container": "gio-hybrid", "image": "graviteeio/apim-gateway:4.12.21"}},
            "products": {"apim": {**APIM, "services": {"gateway": "/opt/gateway/plugins", "hybrid": "/opt/hybrid/plugins"}}}}
        missing = "docker: Error response from daemon: No such image: graviteeio/apim-gateway:4.12.21\n"

        def shell(service, args, timeout=120):
            if service["container"] == "gio-hybrid":
                return 125, "", missing
            return 0, "1700 /opt/gateway/plugins/gravitee-policy-x-2.1.0.zip\n", ""
        self.patch(compose, "shell", shell)
        self.assertEqual(plugins.installed(project, "apim")["bundled_error"], "graviteeio/apim-gateway:4.12.21 is not pulled")
        found = plugins.installed(project, "apim", {"gateway", "mongodb"})
        self.assertEqual((found["bundled_error"], found["services"], list(found["bundled"])),
                         (None, ["gateway"], ["gravitee-policy-x"]))
        self.patch(compose, "loaded", return_value={"gateway", "mongodb"})
        self.patch(compose, "containers", lambda: ({}, None))
        self.assertEqual(plugins.install_steps(project, "apim", ["gravitee-policy-x"]), ([], [], {}))

    def running(self, containers):
        self.patch(compose, "containers", lambda: containers)
        return plugins._running(APIM)

    def files(self):
        """What the shared folder of the product holds, hidden files included."""
        folder = self.root / "plugins/apim"
        return sorted(f.name for f in folder.iterdir()) if folder.is_dir() else []

    def fetched(self, then=None):
        """A _download() without the network: it writes the .part file it answers. `then(job, plugin)` runs once it
        is written."""
        def download(job, folder, plugin, version):
            folder.mkdir(parents=True, exist_ok=True)
            part = folder / f".{version['file']}.part"
            part.write_text(version["file"], encoding="utf-8")
            job.lines.append(version["file"])
            if then:
                then(job, plugin)
            return part
        return download

    def applied(self, install, remove):
        """The job apply() starts, run without its commands: (job, what else JOBS.start got, the commands)."""
        jobs = self.patch(plugins, "JOBS")
        plugins.apply(PROJECT, "apim", install, remove)
        title, steps, more = self.started(jobs)
        job, commands = Job(title, steps, phases=more["phases"], waits=more["waits"]), []
        job.exec = commands.append
        job.run()
        self.assertEqual((title, more["key"], more["verb"], job.ok), ("Apply plugins", "plugins:apim", "apply", True))
        return job, more, commands

    def test_unload_stops_what_runs(self):
        self.assertEqual(self.running(({"gateway": {"state": "running"}}, None)), ["gateway"])
        stop, clear = plugins._unload_steps(APIM, ["gateway"])
        self.assertEqual((stop, callable(clear)), ([*compose.COMPOSE, "stop", "gateway"], True))

    def test_unload_when_nothing_runs_or_docker_does_not_answer(self):
        (self.root / "plugins/apim/.work").mkdir(parents=True)
        for containers in (({"gateway": {"state": "exited"}}, None), ({}, "Cannot connect to the Docker daemon")):
            self.assertEqual(self.running(containers), [], containers)
        job = Job("Plugins", plugins._unload_steps(APIM, []))
        job.run()
        self.assertEqual((job.ok, job.lines), (True, ["Cleared apim/.work", "Not running: loaded on next start"]))
        self.assertFalse((self.root / "plugins/apim/.work").exists())

    def test_apply_stops_and_starts_a_product_that_runs(self):
        added = self.root / "plugins/apim/gravitee-policy-old-1.0.0.zip"
        added.parent.mkdir(parents=True)
        added.write_text("zip", encoding="utf-8")
        self.running(({"gateway": {"state": "running"}}, None))
        job, more, commands = self.applied([], [added.name])
        # Nothing is downloaded: the stop and the start are what takes time. It shows as starting meanwhile
        self.assertEqual((more["phases"], [phase["name"] for phase in job.phases], more["changes"]),
                         (["Stop", "Start"], ["Stop", "Start"], {"start": [["service", "gateway"]]}))
        self.assertEqual(more["waits"], [{("Container", "gio-gateway"): "stop"},
                                         {("Container", "gio-gateway"): "start"}])
        self.assertEqual(commands, [[*compose.COMPOSE, "stop", "gateway"],
                                    [*compose.COMPOSE, "up", "-d", "--no-deps", "gateway"]])
        self.assertEqual((job.lines, added.exists()), ([f"Removed {added.name}", "▸ Stop", "▸ Start"], False))

    def test_apply_downloads_for_a_product_that_does_not_run(self):
        file = "gravitee-policy-x-1.2.0.zip"
        picked = ({"name": "gravitee-policy-x"}, {"file": file})
        self.patch(plugins, "_pick", lambda project, product, item: picked)
        self.patch(plugins, "_download", self.fetched())
        self.running(({}, None))
        job, more, commands = self.applied([{"path": "plugins/policies/x/", "version": "1.2.0"}], [])
        phase = f"Download {file}"
        self.assertEqual((more["phases"], more["changes"], commands), ([phase], {"start": []}, []))
        self.assertEqual(more["waits"], [{("Download", "gravitee-policy-x"): "download"}])
        self.assertEqual(job.lines, [f"▸ {phase}", file, f"Added {file}", "Not running: loaded on next start"])
        self.assertEqual(self.files(), [file])
        with self.assertRaisesRegex(ValueError, "Nothing to apply"):
            plugins.apply(PROJECT, "apim", [], [])

    def apply_job(self, picked, remove=(), download=None):
        """The job apply() starts for these downloads ({path: (plugin, version)}) and the added files to remove, not
        run yet: (job, the commands it will have run)."""
        self.patch(plugins, "_pick", lambda project, product, item: picked[item["path"]])
        self.patch(plugins, "_download", download or self.fetched())
        jobs = self.patch(plugins, "JOBS")
        plugins.apply(PROJECT, "apim", [{"path": path} for path in picked], list(remove))
        title, steps, more = self.started(jobs)
        job, commands = Job(title, steps, phases=more["phases"], waits=more["waits"]), []
        job.exec = commands.append
        return job, commands

    def added(self, *names):
        """Plugins added to the shared folder before the test, as files."""
        for name in names:
            self.write(f"plugins/apim/{name}", "zip")

    PICKED = {"a": ({"name": "gravitee-policy-a"}, {"file": "gravitee-policy-a-1.0.0.zip"}),
              "b": ({"name": "gravitee-policy-b"}, {"file": "gravitee-policy-b-2.0.0.zip"})}

    def test_the_folder_changes_at_once_right_before_the_stop(self):
        # The downloads go to .part files that no product loads; once all of them are there, the removed file goes,
        # the new version takes the place of the old one, and the gateway stops at once
        self.added("gravitee-policy-old-1.0.0.zip", "gravitee-policy-b-1.0.0.zip")
        before = self.files()
        seen = {}
        self.running(({"gateway": {"state": "running"}}, None))
        job, commands = self.apply_job(self.PICKED, ["gravitee-policy-old-1.0.0.zip"],
                                       self.fetched(lambda job, plugin: seen.setdefault(plugin["name"], self.files())))
        job.exec = lambda command: (commands.append(command), seen.setdefault(command[-2], self.files()))
        job.run()
        self.assertTrue(job.ok, job.lines)
        # While it downloads, the folder holds what it held, and the downloads so far as hidden files
        self.assertEqual(seen["gravitee-policy-a"], [".gravitee-policy-a-1.0.0.zip.part", *before])
        self.assertEqual(seen["gravitee-policy-b"], [".gravitee-policy-a-1.0.0.zip.part",
                                                     ".gravitee-policy-b-2.0.0.zip.part", *before])
        # As the stop begins, the folder holds what the product loads next
        self.assertEqual(seen["stop"], ["gravitee-policy-a-1.0.0.zip", "gravitee-policy-b-2.0.0.zip"])
        self.assertEqual(commands, [[*compose.COMPOSE, "stop", "gateway"],
                                    [*compose.COMPOSE, "up", "-d", "--no-deps", "gateway"]])
        self.assertEqual(job.lines[-5:], ["Removed gravitee-policy-old-1.0.0.zip", "Added gravitee-policy-a-1.0.0.zip",
                                          "Added gravitee-policy-b-2.0.0.zip in place of gravitee-policy-b-1.0.0.zip",
                                          "▸ Stop", "▸ Start"])

    def test_a_download_that_fails_changes_nothing(self):
        # The second download fails: nothing was removed nor replaced, what was downloaded goes, nothing stops
        self.added("gravitee-policy-old-1.0.0.zip", "gravitee-policy-b-1.0.0.zip")
        before = self.files()
        fetched = self.fetched()

        def download(job, folder, plugin, version):
            if plugin["name"] == "gravitee-policy-b":
                raise JobFailed("gravitee-policy-b-2.0.0.zip: checksum mismatch, download discarded")
            return fetched(job, folder, plugin, version)
        self.running(({"gateway": {"state": "running"}}, None))
        job, commands = self.apply_job(self.PICKED, ["gravitee-policy-old-1.0.0.zip"], download)
        job.run()
        self.assertEqual((job.ok, job.error()), (False, "gravitee-policy-b-2.0.0.zip: checksum mismatch, download "
                                                        "discarded"))
        self.assertEqual((self.files(), commands), (before, []))

    def test_a_cancel_before_the_folder_changed_changes_nothing(self):
        self.added("gravitee-policy-b-1.0.0.zip")
        self.running(({"gateway": {"state": "running"}}, None))
        job, commands = self.apply_job(self.PICKED, download=self.fetched(lambda job, plugin: job.cancel()))
        job.run()
        self.assertEqual((job.ok, job.error()), (False, "Canceled"))
        self.assertEqual((self.files(), commands), (["gravitee-policy-b-1.0.0.zip"], []))

    def test_apply_counts_each_download_then_the_stop_and_the_start(self):
        # Each download by its bytes, then the gateway stopped and started again: it is ready once healthy, half way
        # along the job once stopped
        picked = {"a": ({"name": "gravitee-policy-a"}, {"file": "gravitee-policy-a-1.0.0.zip"}),
                  "b": ({"name": "gravitee-policy-b"}, {"file": "gravitee-policy-b-2.0.0.zip"})}
        seen = []

        def download(job, plugin):   # half the file, then all of it
            for fraction in (0.5, 1):
                job.unit(("Download", plugin["name"]), fraction)
                seen.append(job.progress)
        self.patch(plugins, "_pick", lambda project, product, item: picked[item["path"]])
        self.patch(plugins, "_download", self.fetched(download))
        self.running(({"gateway": {"state": "running"}}, None))
        jobs = self.patch(plugins, "JOBS")
        plugins.apply(PROJECT, "apim", [{"path": "a"}, {"path": "b"}], [])
        title, steps, more = self.started(jobs)
        self.assertEqual(more["phases"], ["Download gravitee-policy-a-1.0.0.zip",
                                          "Download gravitee-policy-b-2.0.0.zip", "Stop", "Start"])
        job, docker = Job(title, steps, **more), Docker(PROJECT, {"gateway": container(health="healthy")}, {"gateway"})
        read = replay(job, [" Container gio-gateway Stopping ", " Container gio-gateway Stopped "],
                      [" Container gio-gateway Starting ", " Container gio-gateway Started "], refresh=docker.refresh)
        self.assertEqual([phase["name"] for phase in job.phases], more["phases"])
        self.assertEqual(seen + progress(read), [0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.8, 0.875])
        self.assertEqual([round(summary["containers"]["gio-gateway"], 3) for summary in read], [0.25, 0.5, 0.6, 0.75])
        docker.healthy("gateway")
        docker.observe(job)
        self.assertEqual((job.ok, job.progress), (True, 1))

    def test_a_download_says_how_far_it_is(self):
        content, file = b"0123456789" * 4, "gravitee-policy-x-1.2.0.zip"
        url = f"{plugins.DOWNLOADS}/plugins/policies/gravitee-policy-x/{file}"
        answers = {url: content, url + ".sha1": f"{hashlib.sha1(content).hexdigest()}  {file}\n".encode()}
        self.patch(plugins.urllib.request, "urlopen", lambda asked, timeout: io.BytesIO(answers[asked]))
        self.patch(plugins, "CHUNK", 10)
        job, folder, reported = Job("Plugins", []), self.root / "plugins/apim", []
        job.unit = lambda name, fraction: reported.append((name, fraction))
        plugin = {"name": "gravitee-policy-x", "path": "plugins/policies/gravitee-policy-x/"}
        part = plugins._download(job, folder, plugin, {"file": file, "size": 40})
        unit = ("Download", "gravitee-policy-x")   # by its name: known before its version is chosen
        self.assertEqual(reported, [(unit, 0.25), (unit, 0.5), (unit, 0.75), (unit, 1)])
        # A hidden file until it is put in place: what the product loads did not change
        self.assertEqual((part.read_bytes(), job.lines[-1]), (content, "  SHA-1 verified"))
        self.assertEqual(self.files(), [f".{file}.part"])
        plugins._place(job, folder, plugin, {"file": file}, part)
        self.assertEqual((self.files(), (folder / file).read_bytes()), ([file], content))
        reported.clear()
        part = plugins._download(job, folder, plugin, {"file": file, "size": 0})   # a listing without the size
        self.assertEqual({fraction for _, fraction in reported}, {1})
        plugins._place(job, folder, plugin, {"file": file}, part)
        self.assertEqual((self.files(), job.lines[-1]), ([file], f"Added {file}"))

    def test_nothing_is_left_of_a_download_that_fails(self):
        file, folder = "gravitee-policy-x-1.2.0.zip", self.root / "plugins/apim"
        url = f"{plugins.DOWNLOADS}/plugins/policies/gravitee-policy-x/{file}"
        plugin = {"name": "gravitee-policy-x", "path": "plugins/policies/gravitee-policy-x/"}

        class Broken(io.BytesIO):
            def read(self, size=-1):
                raise TimeoutError("timed out")
        for answers, error in (({url: b"zip", url + ".sha1": b"0000  x\n"}, JobFailed),
                               ({url: Broken()}, TimeoutError)):
            opened = {asked: answer if isinstance(answer, io.BytesIO) else io.BytesIO(answer)
                      for asked, answer in answers.items()}
            self.patch(plugins.urllib.request, "urlopen", lambda asked, timeout: opened[asked])
            with self.assertRaises(error):
                plugins._download(Job("Plugins", []), folder, plugin, {"file": file, "size": 3})
            self.assertEqual(list(folder.iterdir()), [], error)

    def test_a_missing_plugin_is_downloaded_then_the_product_stops(self):
        present = {"bundled": {"gravitee-policy-a": {}}, "bundled_error": None, "ext": {}}
        published = [{"name": "gravitee-policy-x", "path": "plugins/policies/gravitee-policy-x/"}]
        commands = []
        self.patch(plugins, "installed", lambda project, product, only=None: present)
        self.patch(plugins, "catalog", lambda *args: published)
        self.patch(plugins, "versions", lambda path: [{"version": "1.2.0", "file": "gravitee-policy-x-1.2.0.zip"},
                                                      {"version": "1.10.0", "file": "gravitee-policy-x-1.10.0.zip"}])
        self.patch(plugins, "_download", self.fetched())
        self.patch(compose, "containers", lambda: ({"gateway": {"state": "running"}}, None))
        steps, stopped, waits = plugins.install_steps(PROJECT, "apim", ["gravitee-policy-a", "gravitee-policy-x"])
        job = Job("Start Welcome", steps)
        job.exec = lambda command: commands.append((command, self.files()))
        job.run()
        # The latest release, in place as the gateway stops
        self.assertEqual((job.ok, commands), (True, [([*compose.COMPOSE, "stop", "gateway"],
                                                      ["gravitee-policy-x-1.10.0.zip"])]))
        # What its phase waits for, known before it begins: the download, and the stop of what loads it
        self.assertEqual((stopped, waits), (["gateway"], {("Download", "gravitee-policy-x"): "download",
                                                         ("Container", "gio-gateway"): "stop"}))
        # Nothing is missing: no step, so that the job does not count a phase for it
        self.assertEqual(plugins.install_steps(PROJECT, "apim", ["gravitee-policy-a"]), ([], [], {}))

    UNKNOWN = {"bundled": {}, "bundled_error": "Bundled plugins unknown: No such image", "ext": {}}
    PULLED = [" Image graviteeio/apim-gateway:4 Pulling ", " Image graviteeio/apim-gateway:4 Pulled "]

    def plugins_phase(self, names, *outputs):
        """The Plugins phase of a playground's start that needs these plugins, its commands printing these outputs:
        what it waits for, and how far the phase was after each line and each thing a step counted."""
        steps, stopped, waits = plugins.install_steps(PROJECT, "apim", names)
        job, seen = Job("Start Welcome", [lambda job: job.step("Plugins"), *steps], phases=["Plugins"],
                        waits=[waits]), []
        said, unit = lambda: seen.append(round(job.summary()["phases"][0]["progress"], 3)), job.unit
        job.unit = lambda *counted: (unit(*counted), said())
        replay(job, *outputs, refresh=lambda job, line: said())
        return job, stopped, waits, seen

    def test_what_the_images_bring_is_read_once_pulled_when_it_is_not_known(self):
        # The images are not here: before the pull, any plugin not added may be missing, and the gateway that runs
        # may stop to load it. Bundled after all: nothing is downloaded, nothing stops, and both count as done
        answers = [self.UNKNOWN, {**self.UNKNOWN, "bundled": {"gravitee-policy-a": {}}, "bundled_error": None}]
        self.patch(plugins, "installed", lambda project, product, only=None: answers.pop(0))
        self.patch(compose, "containers", lambda: ({"gateway": {"state": "running"}}, None))
        job, stopped, waits, seen = self.plugins_phase(["gravitee-policy-a"], self.PULLED)
        self.assertEqual((stopped, waits), (["gateway"], {("Download", "gravitee-policy-a"): "download",
                                                         ("Container", "gio-gateway"): "stop",
                                                         ("Image", "graviteeio/apim-gateway:4"): "image"}))
        # The pull is the one command it ran
        self.assertEqual((job.ok, seen, job.progress), (True, [0, 0.333, 0.667, 1], 1))
        self.assertEqual(job.lines[:2], ["▸ Plugins", " ".join(["$", *compose.COMPOSE, "pull", "gateway"])])
        # Still unknown once pulled: the job fails with the reason
        answers[:] = [self.UNKNOWN, self.UNKNOWN]
        job = self.plugins_phase(["gravitee-policy-a"], self.PULLED)[0]
        self.assertEqual((job.ok, job.error()), (False, "Bundled plugins unknown: No such image"))

    def test_what_the_images_lack_once_pulled_was_counted_from_the_start(self):
        # They bring one of the two plugins: the other is downloaded, then the gateway stops. The phase never goes
        # back as it learns what there is to do
        answers = [self.UNKNOWN, {**self.UNKNOWN, "bundled": {"gravitee-policy-a": {}}, "bundled_error": None}]
        self.patch(plugins, "installed", lambda project, product, only=None: answers.pop(0))
        self.patch(plugins, "catalog", lambda *args: [{"name": "gravitee-policy-x", "path": "x/"}])
        self.patch(plugins, "versions", lambda path: [{"version": "1.0.0", "file": "gravitee-policy-x-1.0.0.zip"}])
        self.patch(plugins, "_download", self.fetched(lambda job, plugin: [
            job.unit(("Download", plugin["name"]), fraction) for fraction in (0.5, 1)]))
        self.patch(compose, "containers", lambda: ({"gateway": {"state": "running"}}, None))
        stops = [" Container gio-gateway Stopping ", " Container gio-gateway Stopped "]
        job, _, waits, seen = self.plugins_phase(["gravitee-policy-a", "gravitee-policy-x"], self.PULLED, stops)
        self.assertEqual(len(waits), 4)   # both plugins, the stop and the image
        self.assertEqual((job.ok, seen), (True, [0, 0.25, 0.5, 0.625, 0.75, 0.875, 1]))

    def test_a_plugin_cache_that_cannot_be_cleared_fails_the_job(self):
        (self.root / "plugins/apim/.work").mkdir(parents=True)
        self.patch(plugins.shutil, "rmtree", side_effect=PermissionError("Permission denied"))
        job = Job("Plugins", plugins._unload_steps(APIM, []))
        job.run()
        self.assertEqual((job.ok, job.error()), (False, "apim/.work could not be cleared: Permission denied"))


if __name__ == "__main__":
    unittest.main()
