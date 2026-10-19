"""`make smoke`: the Lab UI against the running stack. Starts it on a free port, reads every page's data and checks
its shape, then stops it. Nothing is started, stopped or saved."""

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

LAB_UI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB_UI))
from lab_ui.docker import compose  # noqa: E402
from lab_ui import paths  # noqa: E402 - after the path

STATE = {"docker", "wait", "blind", "license", "job", "platform", "products", "categories", "playgrounds", "setups",
         "addons", "services", "links", "problems", "running", "root", "skills"}
ITEM = {"kind", "id", "name", "personal", "held", "category", "description", "file", "env_file", "settings", "on",
        "up", "state", "progress", "note", "note_tone", "note_title", "note_target", "note_file", "links", "action",
        "secondary", "failed_job", "login"}
OF_KIND = {"playgrounds": {"tags", "duration", "first_start", "source", "chapters", "guide", "accounts", "requires",
                           "setups", "addons", "includes", "services", "apps", "seed", "created", "stop", "reseed",
                           "brings", "recreates", "tabs"},
           "setups": {"includes", "members"},
           "addons": {"services", "needs", "conflicts", "restarts", "brings", "recreates"}}
ACTION = {"do", "label", "enabled", "reason", "dialog"}
PRODUCT = {"id", "name", "deployed", "deployed_state", "channel", "stable", "prerelease", "latest", "status"}
SERVICE = {"label", "container", "image", "tag", "running_tag", "registry", "ports", "built", "role", "state", "uptime",
           "on", "behind", "needs_license", "note", "note_tone", "note_title", "note_target", "note_file", "brings",
           "recreates"}
LINK = {"label", "url", "port", "service", "section", "legacy", "up"}
TYPES = {".css": "text/css", ".js": "text/javascript"}
FAILED = []


def head(body):
    """The start of an answer, as text."""
    return body[:200].decode(errors="replace")


class LabUI:
    """The Lab UI on a free port, asked like the page asks it."""

    def __init__(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.process = subprocess.Popen([sys.executable, str(LAB_UI / "server.py"), "--port", str(self.port),
                                         "--no-open"], stdout=subprocess.DEVNULL)

    def ask(self, path, method="GET", headers=None):
        """(status, headers, body) of a request."""
        request = urllib.request.Request(f"http://localhost:{self.port}{path}", method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def json(self, path, expect=200):
        status, _, body = self.ask(path)
        assert status == expect, f"{path} answered {status}: {head(body)}"
        return json.loads(body)

    def wait(self):
        for _ in range(100):
            try:
                return self.ask("/favicon.ico")
            except OSError:
                assert self.process.poll() is None, "it stopped at once"
                time.sleep(0.1)
        raise AssertionError("no answer within 10 s")

    def stop(self):
        self.process.terminate()
        self.process.wait(10)


def has(data, keys, what):
    assert isinstance(data, dict) and keys <= set(data), f"{what} lacks {sorted(keys - set(data or {}))}"


def static(lab_ui):
    pages = {"/": "text/html"}
    for folder in ("css", "js"):
        for file in sorted((paths.WEB_DIR / folder).rglob("*")):
            if file.suffix in TYPES:
                pages["/" + file.relative_to(paths.WEB_DIR).as_posix()] = TYPES[file.suffix]
    for path, kind in pages.items():
        status, headers, _ = lab_ui.ask(path)
        assert status == 200 and headers["Content-Type"].startswith(kind), f"{path} answered {status}"
    for path in ("/js/../../server.py", "/api/nothing"):   # outside web/, and no route
        assert lab_ui.ask(path)[0] == 404, f"{path} was served"


def state(lab_ui):
    body = lab_ui.json("/api/state")
    has(body, STATE, "the state")
    has(body["docker"], {"error", "warning"}, "what the state says of Docker")
    assert not body["docker"]["error"], f"Docker is not reachable: {body['docker']['error']}"
    assert body["platform"]["state"]["key"] != "stopped", "the platform is stopped: run `make all` first"
    has(body["platform"], {"name", "state", "containers", "progress", "license_var", "groups", "file", "env_file",
                           "settings", "start", "update", "restart", "reset_turns_off", "behind", "apply"},
        "the platform")
    has(body["platform"]["containers"], {"running", "total"}, "the platform's containers")
    has(body["platform"]["start"], {"needed", "starts", "restarts", "enabled", "reason", "dialog"},
        "the platform's Start")
    has(body["platform"]["update"], {"needed", "enabled", "reason", "dialog"}, "the platform's Update")
    has(body["platform"]["restart"], {"needed", "enabled", "reason"}, "the platform's Restart")
    has(body["platform"]["apply"], {"needed", "restarts", "starts", "enabled", "reason", "dialog"},
        "the platform's Apply")
    for group in body["platform"]["groups"]:
        has(group, {"id", "name", "services", "state", "progress", "note", "note_tone", "note_title", "note_target",
                    "note_file", "registry_label", "brings", "recreates"},
            f"group {group.get('id')}")
    if body["license"]:
        has(body["license"], {"status", "expires", "days_left", "problem", "tone", "applied"}, "the license")
    for problem in body["problems"]:
        has(problem, {"file", "message", "editable"}, "a problem")
    if body["job"]:
        has(body["job"], {"id", "title", "for", "verb", "done", "phases", "progress"}, "the job")
    assert body["root"] == str(paths.ROOT), f"the project is not in {body['root']}"
    for what, skill in body["skills"].items():
        assert (paths.ROOT / skill).is_file(), f"the skill for {what} is not in {skill}"
    for kind, more in OF_KIND.items():
        for item in body[kind]:
            has(item, ITEM | more, f"{kind} {item.get('id')}")
            assert item["personal"] == item["id"].startswith("local/"), f"{item['id']} is personal or not"
            has(item["state"], {"key", "label"}, f"the state of {item['id']}")
            has(item["action"], ACTION, f"the action of {item['id']}")
            assert item["action"]["do"] in ("start", "stop", "settings") and item["action"]["label"], item["id"]
            assert item["action"]["label"] in ("Start", "Stop", "Turn on", "Turn off", "Settings"), item["id"]
    for name, service in body["services"].items():
        has(service, SERVICE, f"service {name}")
        has(service["state"], {"key", "label", "running"}, f"the state of service {name}")
    for link in body["links"]:
        has(link, LINK, f"link {link.get('label')}")
    unknown = set(compose.ps()) - set(body["services"])
    assert not unknown, f"containers of unknown services: {sorted(unknown)}"
    return body


def unchanged(lab_ui):
    # An uptime in seconds, or a job that runs, changes the state between two requests: then it is sent again
    status, headers, first = lab_ui.ask("/api/state")
    again, _, body = lab_ui.ask("/api/state", headers={"If-None-Match": headers["ETag"]})
    assert status == 200 and ((again, body) == (304, b"") or (again == 200 and body != first)), \
        f"answered {status} then {again}"


def refusals(lab_ui):
    for what, method, path, headers in (
            ("a POST without the Lab UI's header", "POST", "/api/files/validate", {}),
            ("a foreign Host", "GET", "/api/state", {"Host": "example.com"}),
            ("a page of another site", "GET", "/api/state", {"Sec-Fetch-Site": "cross-site"})):
        status, _, body = lab_ui.ask(path, method, headers)
        assert status == 403, f"{what} went through"
        # As every failure answers
        assert json.loads(body) == {"error": "Forbidden"}, f"{what} was refused with {head(body)}"


def images(lab_ui):
    body = lab_ui.json("/api/images")
    has(body, {"components", "registries"}, "/api/images")
    for component in body["components"]:
        has(component, {"id", "label", "kind", "version", "version_key", "registry", "registry_key", "services",
                        "configured", "running", "pulled", "enabled", "follows"}, f"component {component.get('id')}")
    has(lab_ui.json("/api/registry"), {"host", "account", "registry_login"}, "/api/registry")
    for kind in ("gravitee", "third-party"):   # one of each: they are listed in different places
        component = next((c for c in body["components"] if c["kind"] == kind), None)
        assert component, f"no {kind} component is listed"
        status, _, answer = lab_ui.ask(f"/api/tags?component={component['id']}")
        if status not in (409, 502):   # the registry cannot be asked from here: no Azure CLI session, or unreachable
            assert status == 200, f"tags of {component['id']} answered {status}: {head(answer)}"
            has(json.loads(answer), {"tags", "local"}, "/api/tags")


def dashboard(lab_ui):
    """The Dashboard's data: a row per product with its channel and its latest version, Docker and the machine. What runs
    and what is on is the state's: each group of the platform counts its own containers, and they add up to the
    platform's."""
    body = lab_ui.json("/api/dashboard")
    has(body, {"docker", "machine", "data", "products", "pending"}, "the dashboard")
    has(body["docker"], {"engine", "compose", "os", "cpus", "memory"}, "what the dashboard says of Docker")
    has(body["machine"], {"cpus", "memory_total", "memory_available", "disk_total", "disk_free"}, "the machine")
    assert body["products"], "no product is listed"
    for product in body["products"]:
        has(product, PRODUCT, f"product {product.get('id')}")
        assert product["channel"] in ("stable", "pre-release"), f"{product['id']} is on no channel"
        assert product["status"] in ("latest", "newer", "unknown"), f"{product['id']} has the status {product['status']}"
        assert (product["status"] == "unknown") == (product["latest"] is None), f"{product['id']}: its latest and its status"
    platform = lab_ui.json("/api/state")["platform"]
    for group in platform["groups"]:
        has(group["containers"], {"running", "total"}, f"the containers of {group['id']}")
    for count in ("running", "total"):
        assert sum(group["containers"][count] for group in platform["groups"]) == platform["containers"][count], \
            f"the groups count {count} containers otherwise than the platform"


def sections(lab_ui, path):
    """The sections shown with a file: the core first, each with its files."""
    found = lab_ui.json("/api/files?path=" + quote(path))["sections"]
    for section in found:
        has(section, {"title", "on", "runs", "files"}, f"a section of {path}")
        assert all({"path", "name", "kind"} <= set(file) for file in section["files"]), path
    core = [file["path"] for file in found[0]["files"]]
    assert (found[0]["title"], core) == ("Core", [paths.CORE_FILE, paths.ENV_FILE]), f"{path}: the core is not first"
    return found


def files(lab_ui, addon):
    sections(lab_ui, paths.CORE_FILE)
    for path in (addon["file"], addon["env_file"]):   # the add-on itself comes next, with its settings
        own = sections(lab_ui, path)[1]
        assert [file["path"] for file in own["files"]] == [addon["file"], addon["env_file"]], path
    # The on file opens in the editor while there is one, with everything that is on
    if (paths.ROOT / paths.ON_FILE).is_file():
        assert sections(lab_ui, paths.ON_FILE)[1]["title"] == "What is on", "the on file is not shown"
    for path in (paths.CORE_FILE, addon["file"]):
        opened = lab_ui.json("/api/files/content?path=" + quote(path))
        assert opened["content"] == (paths.ROOT / path).read_text(encoding="utf-8") and opened["version"], path
    lab_ui.json("/api/files/content?path=" + quote(paths.ENV_FILE), expect=400)   # only compose files are opened
    for file in (paths.ENV_FILE, addon["env_file"]):
        form = lab_ui.json("/api/env?file=" + quote(file))
        has(form, {"sections", "version"}, f"the form of {file}")
        for section in form["sections"]:
            has(section, {"title", "kind", "groups"}, f"a section of {file}")
            assert all({"key", "value"} <= set(v) for group in section["groups"] for v in group["vars"]), file


def plugins(lab_ui, product):
    installed = lab_ui.json(f"/api/plugins/installed?product={product}")
    has(installed, {"bundled", "bundled_error", "pull", "ext", "services", "features", "feature_by_id", "licensed"},
        "installed plugins")
    assert installed["bundled"], installed["bundled_error"] or f"no bundled plugin found for {product}"
    status, _, answer = lab_ui.ask(f"/api/plugins/catalog?product={product}")
    if status != 502:   # 502: download.gravitee.io is not reachable from here
        assert status == 200 and json.loads(answer)["plugins"], f"the catalog answered {status}"


def license(lab_ui):
    body = lab_ui.json("/api/license")
    has(body, {"present"}, "/api/license")
    if body.get("readable"):
        has(body, {"valid", "expires", "expired", "tier", "packs", "features", "model", "model_error", "pull"},
            "the license")


def effects(lab_ui):
    """What an apply would recreate, of what is saved: read only."""
    status, _, body = lab_ui.ask("/api/effects", "POST", {"X-Gravitee-Lab-UI": "1"})
    assert status == 200, f"/api/effects answered {status}: {head(body)}"
    has(json.loads(body), {"restarts", "starts", "runs", "exact"}, "/api/effects")


def playground(lab_ui, item):
    pid = item["id"]
    found = lab_ui.json(f"/api/playgrounds/{pid}/check")
    has(found, {"checks", "dialog"}, "the checks of a playground")
    for line in found["checks"]:
        has(line, {"name", "state", "detail"}, "a check line")
    assert isinstance(lab_ui.json(f"/api/playgrounds/{pid}/guide")["markdown"], str), f"the guide of {pid} is not text"
    lab_ui.json("/api/playgrounds/nothing-here/guide", expect=404)
    # Its UIs: each is asked for by its address, nothing else is
    for tab in item["tabs"]:
        has(tab, {"id", "label", "url", "service", "up", "state"}, "a tab of a playground")
        has(lab_ui.json(f"/api/playgrounds/{pid}/frame?url={quote(tab['url'], safe='')}"),
            {"answers", "frameable", "reason"}, "what a UI is for the page")
    lab_ui.json(f"/api/playgrounds/{pid}/frame?url=http%3A%2F%2Fexample.invalid%2F", expect=400)


def twice(lab_ui):
    """A second Lab UI on the same port is told that one runs there: it exits well, opening nothing."""
    done = subprocess.run([sys.executable, str(LAB_UI / "server.py"), "--port", str(lab_ui.port), "--no-open"],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    assert done.returncode == 0, f"it exited with {done.returncode}: {done.stdout}{done.stderr}"
    assert f"already running on http://localhost:{lab_ui.port}" in done.stdout, done.stdout


def run(name, check, *args):
    """Runs a check and prints its line. Returns what the check returned, or None when it failed."""
    try:
        result = check(*args)
    except Exception as error:  # noqa: BLE001 - every failure is a line of the report
        FAILED.append(name)
        # An assert says what is wrong; anything else is named with its type: a KeyError alone is a bare key
        print(f"✗ {name}\n    {error if isinstance(error, AssertionError) and str(error) else repr(error)}")
        return None
    print(f"✓ {name}")
    return result


def checks(lab_ui):
    """What the state lists decides which add-on, product and playground the other pages are asked about."""
    if not run("the Lab UI starts", lab_ui.wait):
        return
    run("static files", static, lab_ui)
    body = run("state", state, lab_ui)
    run("unchanged state is not sent again", unchanged, lab_ui)
    run("requests from elsewhere are refused", refusals, lab_ui)
    run("a second Lab UI on its port is told", twice, lab_ui)
    if not body:
        return
    run("images, registry and tags", images, lab_ui)
    run("dashboard", dashboard, lab_ui)
    with_settings = next((addon for addon in body["addons"] if addon["settings"]), None)
    if with_settings:
        run("files and settings", files, lab_ui, with_settings)
    if body["products"]:
        run("plugins", plugins, lab_ui, body["products"][0]["id"])
    run("license", license, lab_ui)
    run("what an apply would recreate", effects, lab_ui)
    if body["playgrounds"]:
        run("playground checks, guide and UIs", playground, lab_ui, body["playgrounds"][0])


def main():
    lab_ui = LabUI()
    try:
        checks(lab_ui)
    finally:
        lab_ui.stop()
    print(f"\n{len(FAILED)} check{'s' if len(FAILED) != 1 else ''} failed" if FAILED else "\nSmoke OK")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
