"""catalogs/settings.py: `repo-settings`, the audit of a repository against the standard and the requests that bring it there.
No test reaches GitHub. GitHub is a table of recorded replies (tests/data/settings: `user-private-free` is the personal Free
private repository of the standard's, `public-standard` and `org-private` are repositories that are at the
standard, built from the documented shapes), served by a transport that records every call. The rulesets the module applies
are the Lab's template; the test data holds a copy of the four managed files (the template is written by another package)."""

import copy
import io
import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import gitx, settings
from lab_ui.catalogs.settings import FAIL, MANUAL, NA, OK, WARN, Reply

from .. import Case, gitfixture

DATA = Path(__file__).resolve().parents[1] / "data" / "settings"
TEMPLATE = DATA / "rulesets"
SOURCE = Path(settings.__file__).read_text(encoding="utf-8")


def normalize(path):
    """A path without the paging, which is how the fixtures name a list."""
    base, _, query = path.partition("?")
    keep = [part for part in query.split("&") if part and not part.startswith(("per_page=", "page="))]
    return base + ("?" + "&".join(keep) if keep else "")


def load(name):
    """(repository, {"GET path": Reply}) of a fixture, a copy for the test to change."""
    data = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return data["repo"], {key: Reply(item["code"], copy.deepcopy(item["body"])) for key, item in data["replies"].items()}


def covers(*ids):
    """Marks a test as the one of these identifiers."""
    def mark(test):
        test.covers = ids
        return test
    return mark


class Hub:
    """GitHub as a table of replies; a path that is not in it is a 404. Every call is recorded. With `live`, a write changes
    the table the way GitHub changes, so that an audit after it sees the result. `refuse` maps (method, end of the path) to
    the Reply GitHub gives instead."""

    SETTINGS = ("/actions/permissions", "/actions/permissions/selected-actions", "/actions/permissions/workflow",
                "/actions/permissions/artifact-and-log-retention", "/actions/permissions/access",
                "/actions/permissions/fork-pr-workflows-private-repos", "/actions/permissions/fork-pr-contributor-approval")

    def __init__(self, repo, table, live=False, refuse=None):
        self.repo, self.table, self.live, self.refuse, self.calls = repo, table, live, refuse or {}, []
        self.base = f"repos/{repo}"
        self.store = {}
        for key, reply in table.items():
            found = re.fullmatch(rf"GET {re.escape(self.base)}/rulesets/(\d+)", key)
            if found:
                self.store[int(found.group(1))] = copy.deepcopy(reply.body)

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "GET":
            return self.table.get(f"GET {normalize(path)}") or Reply(404, {"message": "Not Found"})
        for (kind, end), reply in self.refuse.items():
            if method == kind and path.endswith(end):
                return reply
        if self.live:
            return self.change(method, path[len(self.base):], body)
        return Reply(200, {})

    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]

    def put(self, rest, body):
        key = f"GET {self.base}{rest}"
        old = self.table.get(key)
        merged = dict(old.body) if old and isinstance(old.body, dict) else {}
        merged.update(body or {})
        self.table[key] = Reply(200, merged)

    def change(self, method, rest, body):
        meta = self.table[f"GET {self.base}"].body
        if method == "PATCH":
            for key, value in body.items():
                if key == "security_and_analysis":
                    meta.setdefault(key, {}).update(value)
                else:
                    meta[key] = value
        elif rest == "/topics":
            meta["topics"] = list(body["names"])
        elif rest in self.SETTINGS:
            self.put(rest, body)
        elif rest in ("/vulnerability-alerts", "/automated-security-fixes", "/private-vulnerability-reporting",
                      "/immutable-releases"):
            self.table[f"GET {self.base}{rest}"] = Reply(200, None if rest == "/vulnerability-alerts" else {"enabled": True, "paused": False})
        elif rest.endswith("/enable"):
            listing = self.table[f"GET {self.base}/actions/workflows"].body
            for item in listing["workflows"]:
                if item["path"].endswith(rest.split("/")[-2]):
                    item["state"] = "active"
        elif rest.startswith("/environments/") and method == "PUT":
            listing = self.table.setdefault(f"GET {self.base}/environments", Reply(200, {"environments": []})).body
            listing["environments"].append({"name": rest.split("/")[2], "deployment_branch_policy": body["deployment_branch_policy"]})
            self.table.setdefault(f"GET {self.base}{rest}/deployment-branch-policies", Reply(200, {"branch_policies": []}))
        elif rest.endswith("/deployment-branch-policies"):
            self.table[f"GET {self.base}{rest}"].body["branch_policies"].append({"name": body["name"], "type": body["type"]})
        elif rest == "/rulesets" or rest.startswith("/rulesets/"):
            number = max([0, *self.store]) + 1 if method == "POST" else int(rest.rsplit("/", 1)[1])
            self.store[number] = {**copy.deepcopy(body), "id": number, "source_type": "Repository", "source": self.repo}
            self.sync()
            return Reply(200, {"id": number})
        return Reply(200, {})

    def sync(self):
        """The listing, the details and the effective rules of the default branch, made from the rulesets stored."""
        listing, effective = [], []
        for number, ruleset in sorted(self.store.items()):
            self.table[f"GET {self.base}/rulesets/{number}"] = Reply(200, copy.deepcopy(ruleset))
            listing.append({key: ruleset[key] for key in ("id", "name", "target", "source_type", "source", "enforcement")})
            if ruleset["target"] == "branch" and ruleset["enforcement"] == "active":
                effective += [{**copy.deepcopy(rule), "ruleset_source_type": "Repository", "ruleset_id": number}
                              for rule in ruleset["rules"]]
        self.table[f"GET {self.base}/rulesets"] = Reply(200, listing)
        self.table[f"GET {self.base}/rules/branches/main"] = Reply(200, effective)

    def rulesets(self):
        return sorted((ruleset["name"], ruleset["target"]) for ruleset in self.store.values())

    def reset_rulesets(self):
        """The repository has no ruleset at all."""
        for key in [key for key in self.table if re.fullmatch(rf"GET {re.escape(self.base)}/rulesets/\d+", key)]:
            del self.table[key]
        self.store = {}
        self.sync()


class Scene:
    """A fixture and the transport that serves it, with what a test needs to change a reply."""

    def __init__(self, name, live=False, refuse=None, loaded=None):
        self.repo, self.table = loaded if loaded is not None else load(name)
        self.hub = Hub(self.repo, self.table, live, refuse)

    def path(self, rest=""):
        return f"repos/{self.repo}{rest}"

    def set(self, rest, body=None, code=200):
        self.table[f"GET {self.path(rest)}"] = Reply(code, body)

    def get(self, rest=""):
        return self.table[f"GET {self.path(rest)}"]

    def edit(self, rest="", **changes):
        body = copy.deepcopy(self.get(rest).body)
        body.update(changes)
        self.set(rest, body)

    def remove(self, rest="", *keys):
        body = copy.deepcopy(self.get(rest).body)
        for key in keys:
            body.pop(key, None)
        self.set(rest, body)

    def drop(self, rest):
        self.table.pop(f"GET {self.path(rest)}", None)

    def audit(self, **options):
        return settings.audit(self.repo, self.hub, options)

    def finding(self, id, **options):
        return {finding.id: finding for finding in self.audit(**options)}[id]

    def statuses(self, **options):
        return {finding.id: finding.status for finding in self.audit(**options)}

    def sets_standard(self, **changes):
        import base64
        body = json.loads(base64.b64decode(self.get("/contents/.github/standard.json?ref=main").body["content"]))
        body.update(changes)
        raw = (json.dumps(body, sort_keys=True) + "\n").encode()
        self.set("/contents/.github/standard.json?ref=main", {"encoding": "base64", "size": len(raw),
                                                               "content": base64.b64encode(raw).decode()})


def requests_of(finding):
    """[(method, path, body)] of the requests a finding asks for."""
    return [(r["method"], r["path"], r["body"]) for r in finding.fix] if isinstance(finding.fix, list) else []


class Base(Case):
    def setUp(self):
        self.patch(settings, "RULESETS", TEMPLATE)


class Transport(Case):
    """Gh: the `gh` command, through a run that a test replaces."""

    def gh(self, *answers):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            answer = answers[len(calls) - 1]
            if isinstance(answer, Exception):
                raise answer
            return subprocess.CompletedProcess(argv, answer[0], answer[1], answer[2])
        return settings.Gh(run), calls

    def test_a_read_is_an_argument_list_to_github_com(self):
        gh, calls = self.gh((0, '{"private": true}', ""))
        self.assertEqual(Reply(200, {"private": True}), gh("GET", "repos/acme/lab-hub"))
        argv, kwargs = calls[0]
        self.assertEqual(["gh", "api", "--hostname", "github.com", "--method", "GET", "-H", "Accept: application/vnd.github+json",
                          "-H", "X-GitHub-Api-Version: 2022-11-28", "repos/acme/lab-hub"], argv)
        self.assertNotIn("input", kwargs)
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])         # gh asks no question of a terminal
        self.assertEqual(30, kwargs["timeout"])
        self.assertNotIn("shell", kwargs)

    def test_a_write_sends_its_body_on_standard_input(self):
        gh, calls = self.gh((0, "", ""))
        self.assertEqual(Reply(200, None), gh("PATCH", "repos/acme/lab-hub", {"allow_forking": False}))
        argv, kwargs = calls[0]
        self.assertEqual(["--input", "-"], argv[argv.index("--input"):argv.index("--input") + 2])
        self.assertEqual({"allow_forking": False}, json.loads(kwargs["input"]))
        self.assertNotIn('{"allow_forking"', " ".join(argv))

    def test_a_refusal_keeps_its_status_and_what_github_said(self):
        gh, _ = self.gh((1, '{"message": "Not Found", "status": "404"}', "gh: Not Found (HTTP 404)"),
                        (1, "", "gh: Upgrade to GitHub Pro (HTTP 403)"))
        self.assertEqual(Reply(404, {"message": "Not Found", "status": "404"}), gh("GET", "repos/acme/lab-hub/x"))
        self.assertEqual(403, gh("GET", "repos/acme/lab-hub/y").code)

    def test_a_missing_gh_and_a_missing_sign_in_are_one_line_each(self):
        gh, _ = self.gh(FileNotFoundError())
        with self.assertRaises(settings.Unusable) as raised:
            gh("GET", "repos/acme/lab-hub")
        self.assertNotIn("\n", str(raised.exception))
        self.assertIn("gh auth login", str(raised.exception))
        for stderr, code in (("To get started with GitHub CLI, please run:  gh auth login", 4), ("gh: Bad credentials (HTTP 401)", 1)):
            gh, _ = self.gh((code, "", stderr))
            with self.assertRaises(settings.Unusable) as raised:
                gh("GET", "repos/acme/lab-hub")
            self.assertEqual("Not signed in to GitHub. Run gh auth login, then run this again.", str(raised.exception))

    def test_a_slow_github_is_no_answer_and_a_flood_is_cut(self):
        gh, _ = self.gh(subprocess.TimeoutExpired("gh", 30), (0, "x" * (settings.REPLY_LIMIT + 1), ""))
        self.assertEqual(0, gh("GET", "repos/acme/lab-hub").code)
        self.assertEqual(0, gh("GET", "repos/acme/lab-hub").code)

    def test_a_reply_nested_deeper_than_python_reads_is_no_trace(self):
        gh, _ = self.gh((0, "[" * 20000 + "]" * 20000, ""), (1, "{" * 20000, "gh: Server Error (HTTP 500)"))
        for expected in (200, 500):
            reply = gh("GET", "repos/acme/lab-hub")
            self.assertEqual(expected, reply.code)

    def test_only_reads_and_writes_that_create_or_update_can_be_sent(self):
        gh, calls = self.gh()
        for method in ("DELETE", "HEAD", "OPTIONS", "get", ""):
            with self.assertRaises(ValueError):
                gh(method, "repos/acme/lab-hub")
        for path in ("../x", "-x", "repos/a b", "repos/a;b", "https://example.invalid/x", "repos/a\nb", ""):
            with self.assertRaises(ValueError):
                gh("GET", path)
        self.assertEqual([], calls)


class OwnWorkflows(unittest.TestCase):
    """The Lab's own workflows run under the list of actions it sets on a repository: GitHub's own, docker/*, and its own."""

    def test_every_action_the_lab_uses_is_on_its_list(self):
        root = Path(__file__).resolve().parents[3] / ".github"
        used = {match.group(1) for path in root.rglob("*.y*ml")
                for match in re.finditer(r"^\s*-?\s*uses:\s*([^\s@#]+)", path.read_text(encoding="utf-8"), re.M)}
        self.assertTrue(used, "the workflows are read")
        for action in sorted(used):
            if not action.startswith("./"):
                self.assertTrue(action.split("/")[0] in ("actions", "github") or settings.within(action, settings.DOCKER_PATTERNS), action)


class PlanLimits(unittest.TestCase):
    """`n.a.` is for exactly the refusals that GitHub words as limits of the plan."""

    def test_the_plans_own_words(self):
        for code, text in ((403, "Upgrade to GitHub Pro or make this repository public to enable this feature."),
                           (403, "Upgrade to GitHub Team to enable this feature."),
                           (422, "Fork PR approval is not allowed for private repositories."),
                           (403, "Make this repository public to enable this feature.".replace("Make", "make"))):
            self.assertTrue(settings.plan_limit(Reply(code, {"message": text})), text)

    def test_nothing_else_is_a_limit_of_the_plan(self):
        for code, body in ((403, {"message": "Resource not accessible by integration"}),
                           (403, {"message": "Must have admin rights to Repository."}),
                           (403, {"message": "API rate limit exceeded"}),
                           (404, {"message": "Not Found"}),
                           (404, {"message": "Upgrade to GitHub Pro"}),     # the words, but not a refusal
                           (422, {"message": "Validation Failed"}),
                           (500, {"message": "make this repository public"}),
                           (403, None), (403, "Upgrade to GitHub Pro"), (403, {"message": ["Upgrade to GitHub"]}),
                           (200, {"message": "Upgrade to GitHub Pro"}), (0, {"message": "make this repository public"})):
            self.assertFalse(settings.plan_limit(Reply(code, body)), (code, body))


class Fixtures(Base):
    """The recorded replies say what the standard says they say."""

    def test_the_personal_repository_is_the_one_of_section_1_1(self):
        scene = Scene("user-private-free")
        meta = scene.get().body
        self.assertEqual((True, "User", True), (meta["private"], meta["owner"]["type"], meta["permissions"]["admin"]))
        self.assertEqual((True, True, True, False, "COMMIT_OR_PR_TITLE", "COMMIT_MESSAGES", True),
                         tuple(meta[k] for k in ("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge",
                                                 "delete_branch_on_merge", "squash_merge_commit_title",
                                                 "squash_merge_commit_message", "allow_forking")))
        self.assertEqual(403, scene.get("/rulesets").code)
        self.assertEqual(403, scene.get("/rules/branches/main").code)
        self.assertEqual({"enabled": True, "allowed_actions": "all", "sha_pinning_required": False}, scene.get("/actions/permissions").body)
        self.assertEqual({"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
                         scene.get("/actions/permissions/workflow").body)
        self.assertEqual("none", scene.get("/actions/permissions/access").body["access_level"])
        self.assertEqual(422, scene.get("/actions/permissions/fork-pr-contributor-approval").code)
        fork = scene.get("/actions/permissions/fork-pr-workflows-private-repos").body
        self.assertEqual({"run_workflows_from_fork_pull_requests": False, "send_write_tokens_to_workflows": False,
                          "send_secrets_and_variables": False, "require_approval_for_fork_pr_workflows": False}, fork)
        self.assertEqual({"days": 90, "maximum_allowed_days": 400}, scene.get("/actions/permissions/artifact-and-log-retention").body)
        self.assertEqual({"enabled": False, "enforced_by_owner": False}, scene.get("/immutable-releases").body)
        self.assertEqual(422, scene.get("/actions/organization-secrets").code)
        self.assertEqual(404, scene.get("/private-vulnerability-reporting").code)
        self.assertEqual([1], [len(scene.get("/collaborators?permission=push").body)])

    def test_no_fixture_names_an_owner_a_catalog_or_a_credential(self):
        banned = re.compile(r"dobl1|gravitee|ghp_|github_pat_|BEGIN [A-Z ]*PRIVATE KEY|@[a-z0-9.-]+\.(?:com|org|io)\b", re.I)
        for path in sorted(DATA.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                found = banned.search(text.replace("gravitee-lab-catalog", "").replace("Lab catalog standard", ""))
                self.assertIsNone(found, f"{path.name}: {found and found.group(0)}")

    def test_the_rulesets_of_the_test_data_are_the_managed_files_of_the_standard(self):
        loaded, why = settings.load_rulesets(TEMPLATE)
        self.assertEqual("", why)
        self.assertEqual({"main": "catalog-main", "tags": "catalog-tags", "push": "catalog-push", "paths": "catalog-protected-paths"},
                         {key: body["name"] for key, body in loaded.items()})
        for body in loaded.values():
            self.assertNotIn("_managed", body)
        raw = json.loads((TEMPLATE / "default-branch.json").read_text())
        self.assertTrue(raw["_managed"].startswith("Managed by the Lab catalog standard 1: "))
        self.assertEqual(1, settings.template_level(TEMPLATE))
        checks = [r for r in loaded["main"]["rules"] if r["type"] == "required_status_checks"][0]["parameters"]
        self.assertEqual([{"context": "Catalog CI result", "integration_id": 15368}], checks["required_status_checks"])
        self.assertEqual([{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}], loaded["tags"]["bypass_actors"])

    def test_the_rulesets_of_the_test_data_are_those_of_section_4_3(self):
        loaded, _ = settings.load_rulesets(TEMPLATE)
        self.assertEqual({"name": "catalog-main", "target": "branch", "enforcement": "active", "bypass_actors": [],
                          "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                          "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "required_linear_history"},
                                    {"type": "required_signatures"},
                                    {"type": "pull_request", "parameters": {
                                        "required_approving_review_count": 1, "dismiss_stale_reviews_on_push": True,
                                        "require_code_owner_review": True, "require_last_push_approval": True,
                                        "required_review_thread_resolution": True, "allowed_merge_methods": ["squash"]}},
                                    {"type": "required_status_checks", "parameters": {
                                        "strict_required_status_checks_policy": True, "do_not_enforce_on_create": False,
                                        "required_status_checks": [{"context": "Catalog CI result", "integration_id": 15368}]}}]},
                         loaded["main"])
        self.assertEqual({"name": "catalog-tags", "target": "tag", "enforcement": "active",
                          "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
                          "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}},
                          "rules": [{"type": "creation"}, {"type": "deletion"}, {"type": "non_fast_forward"}]}, loaded["tags"])
        self.assertEqual({"name": "catalog-push", "target": "push", "enforcement": "active", "bypass_actors": [],
                          "rules": [{"type": "file_path_restriction", "parameters": {"restricted_file_paths": [
                              "**/.env", "**/.env.local", "**/.state/**", "**/secrets/**"]}},
                                    {"type": "file_extension_restriction", "parameters": {"restricted_file_extensions": [
                                        "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.csr"]}},
                                    {"type": "max_file_size", "parameters": {"max_file_size": 5}}]}, loaded["push"])
        self.assertEqual({"name": "catalog-protected-paths", "target": "push", "enforcement": "active",
                          "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
                          "rules": [{"type": "file_path_restriction", "parameters": {"restricted_file_paths": [
                              ".github/workflows/**", ".github/actions/**", ".github/rulesets/**", ".github/CODEOWNERS",
                              ".github/standard.json", ".catalog/**", "Makefile"]}}]}, loaded["paths"])

    def test_the_template_is_read_from_the_template_and_nowhere_else(self):
        loaded, why = settings.load_rulesets(DATA / "does-not-exist")
        self.assertEqual({}, loaded)
        self.assertIn("could not be read", why)
        folder = self.folder()
        for name in ("default-branch.json", "tags.json", "push.json", "protected-paths.json"):
            (folder / name).write_text((TEMPLATE / name).read_text(), encoding="utf-8")
        self.assertEqual("", settings.load_rulesets(folder)[1])
        (folder / "tags.json").write_text("[1]", encoding="utf-8")
        self.assertIn("is not a ruleset", settings.load_rulesets(folder)[1])
        (folder / "tags.json").write_text("x" * (settings.RULESET_LIMIT + 1), encoding="utf-8")
        self.assertIn("is over", settings.load_rulesets(folder)[1])
        (folder / "tags.json").unlink()
        outside = folder.parent / "outside.json"
        outside.write_text((TEMPLATE / "tags.json").read_text(), encoding="utf-8")
        self.addCleanup(outside.unlink)
        os.symlink(outside, folder / "tags.json")
        self.assertIn("is not in the template's folder", settings.load_rulesets(folder)[1])


NO_RULES = "needs GitHub Pro, an organization or a public repository: rules cannot be enforced here"


def as_public(**more):
    return {"as": "public", **more}


class Audit(Base):
    """One test for each identifier of the table: what it passes, and what it fails or cannot say."""

    # ------------------------------------------------------------------ repo.*

    @covers("repo.default_branch")
    def test_repo_default_branch(self):
        scene = Scene("public-standard")
        self.assertEqual(OK, scene.finding("repo.default_branch").status)
        scene.edit(default_branch="master")
        found = scene.finding("repo.default_branch")
        self.assertEqual(FAIL, found.status)
        self.assertEqual('default_branch="master"', found.now)
        self.assertIn("branches/master/rename", found.fix)
        self.assertIn("new_name=main", found.fix)
        self.assertIsInstance(found.fix, str)            # words: the audit does not rename a branch
        scene.remove("", "default_branch")
        self.assertEqual(MANUAL, scene.finding("repo.default_branch").status)

    @covers("repo.merge")
    def test_repo_merge(self):
        self.assertEqual(OK, Scene("public-standard").finding("repo.merge").status)
        found = Scene("user-private-free").finding("repo.merge")
        self.assertEqual(FAIL, found.status)
        for text in ("allow_merge_commit=true", "allow_rebase_merge=true", "delete_branch_on_merge=false",
                     'squash_merge_commit_title="COMMIT_OR_PR_TITLE"', 'squash_merge_commit_message="COMMIT_MESSAGES"'):
            self.assertIn(text, found.now)
        self.assertNotIn("allow_squash_merge", found.now)       # that one is right
        self.assertEqual([("PATCH", "repos/acme/lab-hub", {
            "allow_merge_commit": False, "allow_rebase_merge": False, "delete_branch_on_merge": True, "allow_update_branch": True,
            "has_wiki": False, "squash_merge_commit_title": "PR_TITLE", "squash_merge_commit_message": "BLANK"})], requests_of(found))
        scene = Scene("public-standard")
        scene.remove("", "has_wiki", "allow_auto_merge")
        found = scene.finding("repo.merge")
        self.assertEqual(MANUAL, found.status)
        self.assertIn("has_wiki", found.now)

    @covers("repo.topic")
    def test_repo_topic(self):
        scene = Scene("user-private-free")
        found = scene.finding("repo.topic")
        self.assertEqual(FAIL, found.status)
        self.assertEqual([("PUT", "repos/acme/lab-hub/topics", {"names": ["gravitee-lab-catalog"]})], requests_of(found))
        scene.edit(topics=["zeta", "alpha"])
        self.assertEqual({"names": ["alpha", "gravitee-lab-catalog", "zeta"]}, requests_of(scene.finding("repo.topic"))[0][2])
        scene.edit(topics=["ok", "Bad Name\n", 5, None, "x" * 80, ["a"]])           # only what can be a topic is sent back
        self.assertEqual({"names": ["gravitee-lab-catalog", "ok"]}, requests_of(scene.finding("repo.topic"))[0][2])
        self.assertEqual(OK, Scene("public-standard").finding("repo.topic").status)
        scene.remove("", "topics")
        self.assertEqual(MANUAL, scene.finding("repo.topic").status)
        scene.set("/topics", {"names": ["gravitee-lab-catalog"]})
        self.assertEqual(OK, scene.finding("repo.topic").status)

    @covers("repo.forking")
    def test_repo_forking(self):
        private = Scene("org-private")
        self.assertEqual(OK, private.finding("repo.forking").status)
        private.edit(allow_forking=True)
        found = private.finding("repo.forking")
        self.assertEqual((FAIL, "allow_forking=true"), (found.status, found.now))
        self.assertEqual([("PATCH", "repos/acme-labs/lab-internal", {"allow_forking": False})], requests_of(found))
        self.assertEqual(OK, Scene("public-standard").finding("repo.forking").status)
        # The go-public gate: a repository that is still private must allow forking once it is public
        gate = Scene("org-private")
        found = gate.finding("repo.forking", **as_public())
        self.assertEqual(FAIL, found.status)
        self.assertEqual("A public repository can be forked", found.title)
        self.assertIsInstance(found.fix, str)         # switching it on while the repository is private is a person's step
        gate.edit(allow_forking=True)
        self.assertEqual(OK, gate.finding("repo.forking", **as_public()).status)
        private.remove("", "allow_forking")
        self.assertEqual(MANUAL, private.finding("repo.forking").status)

    @covers("repo.immutable_releases")
    def test_repo_immutable_releases(self):
        self.assertEqual(OK, Scene("public-standard").finding("repo.immutable_releases").status)
        mine = Scene("user-private-free").finding("repo.immutable_releases")
        self.assertEqual((WARN, "enabled=false"), (mine.status, mine.now))      # until V19 shows what it does on the plan
        self.assertEqual([("PUT", "repos/acme/lab-hub/immutable-releases", None)], requests_of(mine))
        org = Scene("org-private")
        org.set("/immutable-releases", {"enabled": False, "enforced_by_owner": False})
        self.assertEqual(FAIL, org.finding("repo.immutable_releases").status)
        org.set("/immutable-releases", {"message": "Upgrade to GitHub Pro to enable this feature."}, 403)
        self.assertEqual(NA, org.finding("repo.immutable_releases").status)
        org.set("/immutable-releases", {"message": "Not Found"}, 404)
        self.assertEqual(MANUAL, org.finding("repo.immutable_releases").status)

    # ------------------------------------------------------------------ actions.*

    @covers("actions.pinning")
    def test_actions_pinning(self):
        self.assertEqual(OK, Scene("public-standard").finding("actions.pinning").status)
        found = Scene("user-private-free").finding("actions.pinning")
        self.assertEqual((FAIL, "sha_pinning_required=false"), (found.status, found.now))
        # The setting is sent with the others as they are now, so that it cannot undo them
        self.assertEqual([("PUT", "repos/acme/lab-hub/actions/permissions",
                           {"enabled": True, "allowed_actions": "all", "sha_pinning_required": True})], requests_of(found))
        scene = Scene("public-standard")
        scene.set("/actions/permissions", {"message": "Resource not accessible by integration"}, 403)
        self.assertEqual(MANUAL, scene.finding("actions.pinning").status)

    @covers("actions.listed")
    def test_actions_listed(self):
        self.assertEqual(OK, Scene("public-standard").finding("actions.listed").status)
        org = Scene("org-private")
        org.edit("/actions/permissions", allowed_actions="all")
        found = org.finding("actions.listed")
        self.assertEqual((FAIL, 'enabled=true, allowed_actions="all"'), (found.status, found.now))
        self.assertEqual([("PUT", "repos/acme-labs/lab-internal/actions/permissions",
                           {"enabled": True, "allowed_actions": "selected", "sha_pinning_required": True}),
                          ("PUT", "repos/acme-labs/lab-internal/actions/permissions/selected-actions",
                           {"github_owned_allowed": True, "verified_allowed": False, "patterns_allowed": ["docker/*"]})],
                         requests_of(found))
        # On a personal private repository the list may not be enforced (V3): a warning, and words, not requests
        mine = Scene("user-private-free").finding("actions.listed")
        self.assertEqual((WARN, 'enabled=true, allowed_actions="all"'), (mine.status, mine.now))
        self.assertIsInstance(mine.fix, str)
        self.assertIn("docker/*", mine.fix)
        for change in ({"patterns_allowed": ["docker/*", "evil/*"]}, {"verified_allowed": True}, {"github_owned_allowed": False}):
            scene = Scene("public-standard")
            scene.edit("/actions/permissions/selected-actions", **change)
            self.assertEqual(FAIL, scene.finding("actions.listed").status, change)
        # A narrower list is within docker/*, and is the safer one: the apply does not widen it (it does not touch what is ok)
        for patterns in ([], ["docker/*"], ["docker/setup-compose-action@*"], ["docker/login-action@" + "a" * 40],
                         ["docker/login-action@v3", "docker/build-push-action@*"], ["docker/*", "docker/login-action@*"]):
            scene = Scene("public-standard")
            scene.edit("/actions/permissions/selected-actions", patterns_allowed=patterns)
            found = scene.finding("actions.listed")
            self.assertEqual((OK, None), (found.status, found.fix), patterns)
        # A pattern that is not under the owner is not within it, whatever it starts with
        for patterns in (["docker*"], ["*"], ["*/x"], ["dockerx/y"], ["docker/"], ["docker"], ["Docker/*"], ["evil/docker/*"],
                         ["docker/x,evil/y"], ["docker/x evil/y"], ["docker/../evil/x"], ["docker/x\nevil/*"], [""], [5], [None],
                         [["docker/*"]], ["docker/*", "!docker/x"], ["x" * 300]):
            scene = Scene("public-standard")
            scene.edit("/actions/permissions/selected-actions", patterns_allowed=patterns)
            found = scene.finding("actions.listed")
            self.assertEqual(FAIL, found.status, patterns)
            self.assertEqual(["docker/*"], requests_of(found)[1][2]["patterns_allowed"], patterns)
        scene = Scene("public-standard")
        scene.edit("/actions/permissions", enabled=False)
        self.assertEqual(FAIL, scene.finding("actions.listed").status)
        scene = Scene("public-standard")
        scene.set("/actions/permissions/selected-actions", {"message": "Not Found"}, 404)
        self.assertEqual(MANUAL, scene.finding("actions.listed").status)

    def test_the_list_of_actions_names_the_lab_in_the_reusable_form(self):
        scene = Scene("org-private")
        scene.sets_standard(form="reusable")
        scene.edit("/actions/permissions", allowed_actions="all")
        self.assertEqual(["acme-labs/lab/*", "docker/*"], requests_of(scene.finding("actions.listed"))[1][2]["patterns_allowed"])
        # The Lab's own workflows at a commit are within `acme-labs/lab/*`; the owner's other repositories are not
        scene = Scene("org-private")
        scene.sets_standard(form="reusable")
        scene.edit("/actions/permissions/selected-actions", patterns_allowed=["acme-labs/lab/.github/workflows/ci.yml@" + "a" * 40])
        self.assertEqual(OK, scene.finding("actions.listed").status)
        scene.edit("/actions/permissions/selected-actions", patterns_allowed=["acme-labs/other/*"])
        self.assertEqual(FAIL, scene.finding("actions.listed").status)
        scene = Scene("org-private")                                    # and in the inline form the Lab is not listed at all
        scene.edit("/actions/permissions/selected-actions", patterns_allowed=["acme-labs/lab/*"])
        self.assertEqual(FAIL, scene.finding("actions.listed").status)
        scene = Scene("org-private")
        scene.sets_standard(form="reusable", lab=None)          # the Lab is not named: the list cannot be made
        scene.edit("/actions/permissions", allowed_actions="all")
        found = scene.finding("actions.listed")
        self.assertEqual(MANUAL, found.status)
        self.assertIsNone(found.fix)

    @covers("actions.token")
    def test_actions_token(self):
        self.assertEqual(OK, Scene("public-standard").finding("actions.token").status)
        for change in ({"can_approve_pull_request_reviews": True}, {"default_workflow_permissions": "write"}):
            scene = Scene("org-private")
            scene.edit("/actions/permissions/workflow", **change)
            found = scene.finding("actions.token")
            self.assertEqual(FAIL, found.status, change)
            self.assertEqual([("PUT", "repos/acme-labs/lab-internal/actions/permissions/workflow",
                               {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False})], requests_of(found))
        scene = Scene("org-private")
        scene.drop("/actions/permissions/workflow")
        self.assertEqual(MANUAL, scene.finding("actions.token").status)

    @covers("actions.fork")
    def test_actions_fork_on_a_private_repository(self):
        self.assertEqual(OK, Scene("user-private-free").finding("actions.fork").status)         # forks do not run at all
        self.assertEqual(OK, Scene("org-private").finding("actions.fork").status)
        scene = Scene("org-private")
        scene.edit("/actions/permissions/fork-pr-workflows-private-repos", send_write_tokens_to_workflows=True)
        found = scene.finding("actions.fork")
        self.assertEqual(FAIL, found.status)
        self.assertIn("send_write_tokens_to_workflows=true", found.now)
        body = requests_of(found)[0][2]
        # The key is send_write_tokens_to_workflows: send_write_tokens is not one GitHub knows
        self.assertEqual({"run_workflows_from_fork_pull_requests": False, "send_write_tokens_to_workflows": False,
                          "send_secrets_and_variables": False, "require_approval_for_fork_pr_workflows": True}, body)
        self.assertNotIn("send_write_tokens", body)
        scene = Scene("org-private")
        scene.edit("/actions/permissions/fork-pr-workflows-private-repos", send_secrets_and_variables=True)
        self.assertEqual(FAIL, scene.finding("actions.fork").status)
        scene.edit("/actions/permissions/fork-pr-workflows-private-repos", send_secrets_and_variables=False,
                   run_workflows_from_fork_pull_requests=True, require_approval_for_fork_pr_workflows=False)
        self.assertEqual(FAIL, scene.finding("actions.fork").status)         # they run, and nobody approves
        scene.edit("/actions/permissions/fork-pr-workflows-private-repos", require_approval_for_fork_pr_workflows=True)
        self.assertEqual(OK, scene.finding("actions.fork").status)           # they run after approval, with nothing
        scene.remove("/actions/permissions/fork-pr-workflows-private-repos", "send_write_tokens_to_workflows")
        self.assertEqual(MANUAL, scene.finding("actions.fork").status)

    def test_actions_fork_on_a_public_repository(self):
        scene = Scene("public-standard")
        self.assertEqual(OK, scene.finding("actions.fork").status)
        scene.set("/actions/permissions/fork-pr-contributor-approval", {"approval_policy": "all_external_contributors"})
        self.assertEqual(OK, scene.finding("actions.fork").status)
        scene.set("/actions/permissions/fork-pr-contributor-approval", {"approval_policy": "first_time_contributors_new_to_github"})
        found = scene.finding("actions.fork")
        self.assertEqual(FAIL, found.status)
        self.assertEqual([("PUT", "repos/acme-labs/lab-hub/actions/permissions/fork-pr-contributor-approval",
                           {"approval_policy": "first_time_contributors"})], requests_of(found))
        # The go-public gate on a repository that is still private: GitHub says it cannot be set yet
        gate = Scene("user-private-free").finding("actions.fork", **as_public())
        self.assertEqual(NA, gate.status)
        self.assertEqual("A first-time contributor's run waits for a maintainer", gate.title)

    @covers("actions.access")
    def test_actions_access(self):
        self.assertEqual(OK, Scene("org-private").finding("actions.access").status)
        scene = Scene("org-private")
        scene.set("/actions/permissions/access", {"access_level": "organization"})
        found = scene.finding("actions.access")
        self.assertEqual((WARN, 'access_level="organization"'), (found.status, found.now))
        self.assertEqual([("PUT", "repos/acme-labs/lab-internal/actions/permissions/access", {"access_level": "none"})], requests_of(found))
        scene.set("/actions/permissions/access", {"message": "Not Found"}, 404)
        self.assertEqual(MANUAL, scene.finding("actions.access").status)
        self.assertNotIn("actions.access", Scene("public-standard").statuses())       # a public repository has no such setting

    @covers("actions.retention")
    def test_actions_retention(self):
        self.assertEqual(OK, Scene("public-standard").finding("actions.retention").status)
        found = Scene("user-private-free").finding("actions.retention")
        self.assertEqual((WARN, "days=90"), (found.status, found.now))
        self.assertEqual([("PUT", "repos/acme/lab-hub/actions/permissions/artifact-and-log-retention", {"days": 30})], requests_of(found))
        scene = Scene("public-standard")
        scene.set("/actions/permissions/artifact-and-log-retention", {"message": "Not Found"}, 404)
        self.assertEqual(MANUAL, scene.finding("actions.retention").status)

    @covers("actions.secrets")
    def test_actions_secrets(self):
        found = Scene("org-private").finding("actions.secrets")
        self.assertEqual((OK, "No secret but LAB_DEPLOY_KEY"), (found.status, found.title))         # the Lab is private
        self.assertEqual(OK, Scene("user-private-free").finding("actions.secrets").status)
        scene = Scene("org-private")
        scene.set("/dependabot/secrets", {"total_count": 0, "secrets": []})
        found = scene.finding("actions.secrets")
        self.assertEqual(WARN, found.status)
        self.assertIn("not in the Dependabot store", found.now)
        scene = Scene("org-private")
        scene.set("/actions/secrets", {"secrets": [{"name": "LAB_DEPLOY_KEY"}, {"name": "REGISTRY_TOKEN"}]})
        found = scene.finding("actions.secrets")
        self.assertEqual(WARN, found.status)
        self.assertIn("Actions also holds REGISTRY_TOKEN", found.now)
        scene.set("/actions/secrets", {"message": "Must have admin rights to Repository."}, 403)
        self.assertEqual(MANUAL, scene.finding("actions.secrets").status)

    def test_a_catalog_that_requires_another_holds_a_second_read_key_in_both_stores(self):
        needs = {"standard": 1, "form": "inline", "lab": {"repository": "acme-labs/lab", "ref": "a" * 40}, "platforms": ["linux/amd64"],
                 "needs": {"hub": {"repository": "acme/hub", "ref": "b" * 40}}}
        scene = Scene("org-private")
        found = scene.finding("actions.secrets", standard=needs)
        self.assertEqual(WARN, found.status)
        self.assertIn("NEEDS_DEPLOY_KEY is not in the Actions store", found.now)
        both = {"secrets": [{"name": "LAB_DEPLOY_KEY"}, {"name": "NEEDS_DEPLOY_KEY"}]}
        scene.set("/actions/secrets", both)
        scene.set("/dependabot/secrets", both)
        self.assertEqual(OK, scene.finding("actions.secrets", standard=needs).status)
        self.assertEqual(WARN, scene.finding("actions.secrets").status, "without a need, the second key is one secret too many")

    def test_a_public_lab_needs_no_key_and_the_go_public_gate_fails_on_it(self):
        scene = Scene("org-private")
        scene.table["GET repos/acme-labs/lab"] = Reply(200, {"private": False})
        found = scene.finding("actions.secrets")
        self.assertEqual((WARN, "No secret"), (found.status, found.title))
        self.assertIn("the Lab is public", found.now)
        self.assertIn("gh secret delete LAB_DEPLOY_KEY", found.fix)
        gate = Scene("org-private").finding("actions.secrets", **as_public())
        self.assertEqual(FAIL, gate.status)
        self.assertEqual(OK, Scene("public-standard").finding("actions.secrets").status)

    @covers("actions.org_secrets")
    def test_actions_org_secrets(self):
        mine = Scene("user-private-free")
        found = mine.finding("actions.org_secrets")
        self.assertEqual((NA, "the repository belongs to a user"), (found.status, found.now))        # GitHub answers 422
        self.assertIn("actions.org_secrets", mine.audit().inapplicable)
        self.assertEqual(OK, Scene("org-private").finding("actions.org_secrets").status)
        scene = Scene("org-private")
        scene.set("/actions/organization-secrets", {"total_count": 1, "secrets": [{"name": "LEAKY_TOKEN"}]})
        found = scene.finding("actions.org_secrets")
        self.assertEqual((FAIL, "also: LEAKY_TOKEN"), (found.status, found.now))
        self.assertEqual(OK, scene.finding("actions.org_secrets", allow_org_secrets=["LEAKY_TOKEN"]).status)
        scene = Scene("org-private")
        scene.set("/actions/organization-variables", {"total_count": 1, "variables": [{"name": "WIDE_OPEN"}]})
        found = scene.finding("actions.org_secrets")
        self.assertEqual((FAIL, "also: WIDE_OPEN"), (found.status, found.now))
        scene.set("/actions/organization-variables", {"message": "Resource not accessible"}, 403)
        self.assertEqual(MANUAL, scene.finding("actions.org_secrets").status)
        scene = Scene("org-private")
        scene.set("/actions/organization-secrets", {"message": "Validation Failed"}, 422)      # on an organization it is not the answer
        self.assertEqual(MANUAL, scene.finding("actions.org_secrets").status)

    @covers("actions.runners")
    def test_actions_runners(self):
        self.assertEqual(OK, Scene("user-private-free").finding("actions.runners").status)
        self.assertEqual(OK, Scene("org-private").finding("actions.runners").status)
        scene = Scene("org-private")
        scene.set("/actions/runners", {"total_count": 1, "runners": [{"name": "box"}]})
        found = scene.finding("actions.runners")
        self.assertEqual(FAIL, found.status)
        self.assertIn("1 self-hosted runner", found.now)
        scene = Scene("org-private")
        scene.table["GET orgs/acme-labs/actions/runner-groups/1/runners"] = Reply(200, {"total_count": 2, "runners": [{}, {}]})
        found = scene.finding("actions.runners")
        self.assertEqual((FAIL, "self-hosted runners in Default"), (found.status, found.now))
        scene = Scene("org-private")
        scene.table["GET orgs/acme-labs/actions/runner-groups?visible_to_repository=lab-internal"] = Reply(403, {"message": "Not allowed"})
        found = scene.finding("actions.runners")
        self.assertEqual(MANUAL, found.status)
        self.assertEqual("Runner groups: Selected repositories, never a catalog; Allow public repositories off", found.fix)
        scene.set("/actions/runners", {"message": "Not Found"}, 404)
        self.assertEqual(MANUAL, scene.finding("actions.runners").status)

    @covers("actions.oidc")
    def test_actions_oidc(self):
        found = Scene("org-private").finding("actions.oidc")
        self.assertEqual(MANUAL, found.status)
        self.assertIn("job_workflow_ref", found.fix)
        self.assertIn("repo:ORG/*", found.fix)
        mine = Scene("user-private-free")
        self.assertEqual(NA, mine.finding("actions.oidc").status)
        self.assertIn("actions.oidc", mine.audit().inapplicable)

    @covers("actions.nightly")
    def test_actions_nightly(self):
        self.assertEqual(OK, Scene("public-standard").finding("actions.nightly").status)
        scene = Scene("public-standard")
        scene.set("/actions/workflows", {"workflows": [{"id": 12, "path": ".github/workflows/nightly.yml", "state": "disabled_inactivity"}]})
        found = scene.finding("actions.nightly")
        self.assertEqual((FAIL, 'state="disabled_inactivity"'), (found.status, found.now))
        self.assertEqual([("PUT", "repos/acme-labs/lab-hub/actions/workflows/nightly.yml/enable", None)], requests_of(found))
        scene.set("/actions/workflows", {"workflows": [{"id": 11, "path": ".github/workflows/ci.yml", "state": "active"}]})
        found = scene.finding("actions.nightly")
        self.assertEqual((FAIL, "nightly.yml is not there"), (found.status, found.now))
        self.assertIn("catalog.py upgrade", found.fix)
        scene.sets_standard(standard=1)                      # standard 1 has no nightly.yml
        found = scene.finding("actions.nightly")
        self.assertEqual(NA, found.status)
        self.assertIn("actions.nightly", scene.audit().inapplicable)
        mine = Scene("user-private-free")
        self.assertEqual(NA, mine.finding("actions.nightly").status)          # no standard.json: not a catalog yet
        scene = Scene("public-standard")
        scene.set("/actions/workflows", {"message": "Not Found"}, 404)
        self.assertEqual(MANUAL, scene.finding("actions.nightly").status)
        scene = Scene("public-standard")
        scene.set("/contents/.github/standard.json?ref=main", {"message": "Not Found"}, 403)
        self.assertEqual(MANUAL, scene.finding("actions.nightly").status)
        self.assertEqual(NA, Scene("public-standard").finding("actions.nightly", standard={"standard": 1}).status)
        self.assertEqual(MANUAL, Scene("public-standard").finding("actions.nightly", standard={"form": "inline"}).status)

    # ------------------------------------------------------------------ lab.*, environments.*, packages.*

    @covers("lab.key")
    def test_lab_key(self):
        found = Scene("org-private").finding("lab.key")
        self.assertEqual(MANUAL, found.status)
        self.assertEqual("LAB_DEPLOY_KEY is in the Actions and Dependabot stores", found.now)
        self.assertIn('ssh-keygen -t ed25519 -N \'\' -C "lab-read-acme-labs/lab-internal"', found.fix)
        self.assertIn('gh repo deploy-key add "$d/key.pub" --repo acme-labs/lab --title "acme-labs/lab-internal"', found.fix)
        self.assertIn("gh secret set LAB_DEPLOY_KEY --repo acme-labs/lab-internal --app dependabot", found.fix)
        self.assertEqual("LAB_DEPLOY_KEY is in no store", Scene("user-private-free").finding("lab.key").now)
        self.assertIn("--repo OWNER/LAB", Scene("user-private-free").finding("lab.key").fix)       # the Lab is not known
        self.assertIn("A public Lab needs no key", Scene("public-standard").finding("lab.key").fix)
        # A standard.json is whatever a contributor merged: nothing of it reaches a command that is not a name
        scene = Scene("org-private")
        scene.sets_standard(lab={"repository": "x; rm -rf ~", "ref": "0" * 40})
        self.assertIn("--repo OWNER/LAB", scene.finding("lab.key").fix)
        self.assertNotIn("rm -rf ~", scene.finding("lab.key").fix)

    @covers("environments.policy")
    def test_environments_policy(self):
        self.assertEqual(OK, Scene("public-standard").finding("environments.policy").status)
        mine = Scene("user-private-free").finding("environments.policy")
        self.assertEqual(NA, mine.status)
        scene = Scene("public-standard")
        scene.set("/environments", {"total_count": 0, "environments": []})
        found = scene.finding("environments.policy")
        self.assertEqual((FAIL, "publish does not exist"), (found.status, found.now))
        self.assertEqual([("PUT", "repos/acme-labs/lab-hub/environments/publish",
                           {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}),
                          ("POST", "repos/acme-labs/lab-hub/environments/publish/deployment-branch-policies", {"name": "main", "type": "branch"}),
                          ("POST", "repos/acme-labs/lab-hub/environments/publish/deployment-branch-policies", {"name": "v*", "type": "tag"})],
                         requests_of(found))
        scene = Scene("public-standard")
        scene.set("/environments/publish/deployment-branch-policies", {"branch_policies": [{"name": "main", "type": "branch"}]})
        found = scene.finding("environments.policy")
        self.assertEqual((FAIL, "publish does not allow v*"), (found.status, found.now))
        self.assertEqual([("POST", "repos/acme-labs/lab-hub/environments/publish/deployment-branch-policies", {"name": "v*", "type": "tag"})],
                         requests_of(found))
        scene = Scene("public-standard")
        scene.set("/environments/publish/deployment-branch-policies", {"branch_policies": [
            {"name": "main", "type": "branch"}, {"name": "v*", "type": "tag"}, {"name": "*", "type": "branch"}]})
        found = scene.finding("environments.policy")
        self.assertEqual(FAIL, found.status)
        self.assertIn("also deploys from *", found.now)
        self.assertIsNone(found.fix)                       # nothing is removed: a person does it
        scene = Scene("public-standard")
        scene.set("/environments", {"environments": [{"name": "publish", "deployment_branch_policy": None}]})
        found = scene.finding("environments.policy")
        self.assertEqual(FAIL, found.status)
        self.assertIn("publish deploys from any branch", found.now)
        self.assertIsNone(found.fix)                       # an environment that exists is not rewritten: its reviewers would go with it
        scene = Scene("public-standard")
        scene.set("/environments", {"environments": [
            {"name": "publish", "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}},
            {"name": "e2e", "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}]})
        scene.set("/environments/e2e/deployment-branch-policies", {"branch_policies": [{"name": "main", "type": "branch"}]})
        self.assertEqual(OK, scene.finding("environments.policy").status)
        scene.set("/environments/e2e/deployment-branch-policies", {"branch_policies": [{"name": "main", "type": "branch"}, {"name": "dev", "type": "branch"}]})
        self.assertEqual(FAIL, scene.finding("environments.policy").status)
        scene.set("/environments", {"message": "Upgrade to GitHub Pro to enable this feature."}, 403)
        self.assertEqual(NA, scene.finding("environments.policy").status)
        scene.set("/environments", {"message": "Not Found"}, 404)
        self.assertEqual(MANUAL, scene.finding("environments.policy").status)

    @covers("packages.actions_access")
    def test_packages_actions_access(self):
        found = Scene("public-standard").finding("packages.actions_access")
        self.assertEqual(MANUAL, found.status)
        self.assertIn("Manage Actions access", found.fix)
        self.assertEqual(NA, Scene("user-private-free").finding("packages.actions_access").status)

    # ------------------------------------------------------------------ rules.*

    def dropping(self, id, kind):
        scene = Scene("public-standard")
        self.assertEqual(OK, scene.finding(id).status)
        scene.set("/rules/branches/main", [rule for rule in scene.get("/rules/branches/main").body if rule["type"] != kind])
        found = scene.finding(id)
        self.assertEqual((FAIL, "missing"), (found.status, found.now))
        method, path, body = requests_of(found)[0]
        self.assertEqual(("PUT", "repos/acme-labs/lab-hub/rulesets/1001", "catalog-main"), (method, path, body["name"]))
        self.assertNotIn("_managed", body)
        self.assertEqual(NA, Scene("user-private-free").finding(id).status)
        self.assertEqual(NO_RULES, Scene("user-private-free").finding(id).now)

    @covers("rules.deletion")
    def test_rules_deletion(self):
        self.dropping("rules.deletion", "deletion")

    @covers("rules.non_fast_forward")
    def test_rules_non_fast_forward(self):
        self.dropping("rules.non_fast_forward", "non_fast_forward")

    @covers("rules.required_linear_history")
    def test_rules_required_linear_history(self):
        self.dropping("rules.required_linear_history", "required_linear_history")

    @covers("rules.required_signatures")
    def test_rules_required_signatures(self):
        self.dropping("rules.required_signatures", "required_signatures")

    def weaken(self, scene, kind, **params):
        rules = copy.deepcopy(scene.get("/rules/branches/main").body)
        for rule in rules:
            if rule["type"] == kind:
                rule.setdefault("parameters", {}).update(params)
        scene.set("/rules/branches/main", rules)

    @covers("rules.pull_request")
    def test_rules_pull_request(self):
        self.assertEqual(OK, Scene("public-standard").finding("rules.pull_request").status)
        scene = Scene("public-standard")
        self.weaken(scene, "pull_request", require_code_owner_review=False)
        found = scene.finding("rules.pull_request")
        self.assertEqual((FAIL, "require_code_owner_review=false"), (found.status, found.now))        # it names the key
        for change, text in (({"required_approving_review_count": 0}, "required_approving_review_count=0"),
                             ({"allowed_merge_methods": ["merge", "squash"]}, "allowed_merge_methods="),
                             ({"dismiss_stale_reviews_on_push": False}, "dismiss_stale_reviews_on_push=false"),
                             ({"require_last_push_approval": False}, "require_last_push_approval=false"),
                             ({"required_review_thread_resolution": False}, "required_review_thread_resolution=false")):
            scene = Scene("public-standard")
            self.weaken(scene, "pull_request", **change)
            found = scene.finding("rules.pull_request")
            self.assertEqual(FAIL, found.status, change)
            self.assertIn(text, found.now)
        scene = Scene("public-standard")
        self.weaken(scene, "pull_request", required_approving_review_count=2)           # stricter is fine
        self.assertEqual(OK, scene.finding("rules.pull_request").status)
        # Two rulesets apply together: the strictest of each key counts
        scene = Scene("public-standard")
        rules = copy.deepcopy(scene.get("/rules/branches/main").body)
        weak = next(rule for rule in rules if rule["type"] == "pull_request")
        rules.append({**copy.deepcopy(weak), "ruleset_id": 9, "parameters": {**weak["parameters"], "require_code_owner_review": False}})
        scene.set("/rules/branches/main", rules)
        self.assertEqual(OK, scene.finding("rules.pull_request").status)
        scene.set("/rules/branches/main", [rule for rule in rules if rule["type"] != "pull_request"])
        self.assertEqual((FAIL, "missing"), (scene.finding("rules.pull_request").status, scene.finding("rules.pull_request").now))

    @covers("rules.checks")
    def test_rules_checks(self):
        self.assertEqual(OK, Scene("public-standard").finding("rules.checks").status)
        # A check any app can post does not count: it has to be bound to GitHub Actions
        for bound in ({"context": "Catalog CI result"}, {"context": "Catalog CI result", "integration_id": 99},
                      {"context": "Catalog CI result", "integration_id": None}):
            scene = Scene("public-standard")
            self.weaken(scene, "required_status_checks", required_status_checks=[bound])
            found = scene.finding("rules.checks")
            self.assertEqual(FAIL, found.status, bound)
            self.assertEqual("Catalog CI result is required but any app can post it", found.now)
        scene = Scene("public-standard")
        self.weaken(scene, "required_status_checks", required_status_checks=[{"context": "Other", "integration_id": 15368}])
        self.assertEqual("Catalog CI result is not required", scene.finding("rules.checks").now)
        scene = Scene("public-standard")
        self.weaken(scene, "required_status_checks", strict_required_status_checks_policy=False)
        self.assertEqual((FAIL, "strict_required_status_checks_policy=false"),
                         (scene.finding("rules.checks").status, scene.finding("rules.checks").now))
        scene = Scene("public-standard")
        scene.set("/rules/branches/main", [r for r in scene.get("/rules/branches/main").body if r["type"] != "required_status_checks"])
        self.assertEqual((FAIL, "missing"), (scene.finding("rules.checks").status, scene.finding("rules.checks").now))
        # --required-check: the engine audits its own check
        scene = Scene("public-standard")
        self.assertEqual("E2E result is not required", scene.finding("rules.checks", required_checks=["E2E result"]).now)
        self.weaken(scene, "required_status_checks", required_status_checks=[
            {"context": "Catalog CI result", "integration_id": 15368}, {"context": "E2E result", "integration_id": 15368}])
        self.assertEqual(OK, scene.finding("rules.checks", required_checks=["E2E result", "Catalog CI result"]).status)
        scene.set("/rules/branches/main", [r for r in scene.get("/rules/branches/main").body if r["type"] != "required_status_checks"])
        body = requests_of(scene.finding("rules.checks", required_checks=["E2E result", "build"]))[0][2]
        checks = [r for r in body["rules"] if r["type"] == "required_status_checks"][0]["parameters"]["required_status_checks"]
        self.assertEqual([{"context": "E2E result", "integration_id": 15368}, {"context": "build", "integration_id": 15368}], checks)

    @covers("rules.bypass")
    def test_rules_bypass(self):
        self.assertEqual(OK, Scene("public-standard").finding("rules.bypass").status)
        solo = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
        scene = Scene("public-standard")
        detail = copy.deepcopy(scene.get("/rulesets/1001").body)
        detail["bypass_actors"] = [solo]
        scene.set("/rulesets/1001", detail)
        found = scene.finding("rules.bypass")
        self.assertEqual((WARN, "one administrator can merge their own pull request"), (found.status, found.now))
        self.assertEqual([], requests_of(found)[0][2]["bypass_actors"])        # without --solo the fix takes it away
        found = scene.finding("rules.bypass", solo=True)
        self.assertEqual((WARN, None), (found.status, found.fix))              # asked for, and it is there
        # --solo with none: the ruleset is applied with exactly one administrator, in pull request mode
        none = Scene("public-standard")
        found = none.finding("rules.bypass", solo=True)
        self.assertEqual(FAIL, found.status)
        self.assertEqual([solo], requests_of(found)[0][2]["bypass_actors"])
        for actors in ([{"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}],
                       [solo, {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
                       [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]):
            scene = Scene("public-standard")
            detail = copy.deepcopy(scene.get("/rulesets/1001").body)
            detail["bypass_actors"] = actors
            scene.set("/rulesets/1001", detail)
            self.assertEqual(FAIL, scene.finding("rules.bypass").status, actors)
        scene = Scene("public-standard")
        scene.remove("/rulesets/1001", "bypass_actors")             # a reader who is not an administrator is not told
        found = scene.finding("rules.bypass")
        self.assertEqual(MANUAL, found.status)
        self.assertIn("administrators only", found.now)
        scene = Scene("public-standard")
        scene.set("/rules/branches/main", [{"type": "deletion"}])
        self.assertEqual(MANUAL, scene.finding("rules.bypass").status)

    @covers("rules.tags")
    def test_rules_tags(self):
        self.assertEqual(OK, Scene("public-standard").finding("rules.tags").status)
        scene = Scene("public-standard")
        scene.set("/rulesets", [item for item in scene.get("/rulesets").body if item["target"] != "tag"])
        found = scene.finding("rules.tags")
        self.assertEqual((FAIL, "no active tag ruleset"), (found.status, found.now))
        method, path, body = requests_of(found)[0]
        self.assertEqual(("POST", "repos/acme-labs/lab-hub/rulesets", "catalog-tags"), (method, path, body["name"]))
        scene = Scene("public-standard")
        detail = copy.deepcopy(scene.get("/rulesets/1002").body)
        detail["rules"] = [rule for rule in detail["rules"] if rule["type"] != "deletion"]
        scene.set("/rulesets/1002", detail)
        found = scene.finding("rules.tags")
        self.assertEqual((FAIL, "missing: deletion"), (found.status, found.now))
        self.assertEqual("PUT", requests_of(found)[0][0])                  # the ruleset of that name is replaced
        scene = Scene("public-standard")
        listing = copy.deepcopy(scene.get("/rulesets").body)
        listing[1]["enforcement"] = "evaluate"
        scene.set("/rulesets", listing)
        self.assertEqual("no active tag ruleset", scene.finding("rules.tags").now)
        # A ruleset that guards some tags only is not one that guards every tag. Neither is `refs/tags/*` nor `refs/tags/**`:
        # GitHub's fnmatch stops a `*` at a slash, so a tag such as v1/x is outside them. `~ALL` and the recursive form are not
        for conditions in ({"ref_name": {"include": ["refs/tags/v1.*"], "exclude": []}},
                           {"ref_name": {"include": ["~ALL"], "exclude": ["refs/tags/v2.0.0"]}},
                           {"ref_name": {"include": ["refs/tags/*"], "exclude": []}},
                           {"ref_name": {"include": ["refs/tags/**"], "exclude": []}}, {}, None):
            scene = Scene("public-standard")
            detail = copy.deepcopy(scene.get("/rulesets/1002").body)
            detail["conditions"] = conditions
            scene.set("/rulesets/1002", detail)
            self.assertEqual((FAIL, "the tag ruleset does not reach every tag"),
                             (scene.finding("rules.tags").status, scene.finding("rules.tags").now), conditions)
        for conditions in ({"ref_name": {"include": ["refs/tags/**/*"], "exclude": []}},
                           {"ref_name": {"include": ["refs/tags/v1.*", "~ALL"], "exclude": []}}):
            scene = Scene("public-standard")
            detail = copy.deepcopy(scene.get("/rulesets/1002").body)
            detail["conditions"] = conditions
            scene.set("/rulesets/1002", detail)
            self.assertEqual(OK, scene.finding("rules.tags").status, conditions)

    def tag_ruleset(self, scene, **changes):
        """The details of the tag ruleset, changed."""
        detail = copy.deepcopy(scene.get("/rulesets/1002").body)
        detail.update(changes)
        scene.set("/rulesets/1002", detail)

    def test_only_an_administrator_bypasses_the_tag_ruleset(self):
        """The title says an administrator: a ruleset that lets the write role make a tag lets it start `publish.yml`."""
        admin = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        for actors in ([{"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
                       [admin, {"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}],
                       [{"actor_id": 5, "actor_type": "Integration", "bypass_mode": "always"}],
                       [{"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}], ["x"], [None], [[5]]):
            scene = Scene("public-standard")
            self.tag_ruleset(scene, bypass_actors=actors)
            found = scene.finding("rules.tags")
            self.assertEqual((FAIL, "bypass for someone but an administrator"), (found.status, found.now), actors)
            method, path, body = requests_of(found)[0]
            self.assertEqual(("PUT", "repos/acme-labs/lab-hub/rulesets/1002", [admin]), (method, path, body["bypass_actors"]))
        for actors in ([admin], [], [{**admin, "bypass_mode": "pull_request"}]):
            scene = Scene("public-standard")
            self.tag_ruleset(scene, bypass_actors=actors)
            self.assertEqual(OK, scene.finding("rules.tags").status, actors)
        # A reader who is not an administrator is not sent the list: the audit cannot say, and does not say ok
        for hidden in ("absent", None, "admin", {"actor_id": 5}):
            scene = Scene("public-standard")
            if hidden == "absent":
                scene.remove("/rulesets/1002", "bypass_actors")
            else:
                self.tag_ruleset(scene, bypass_actors=hidden)
            found = scene.finding("rules.tags")
            self.assertEqual((MANUAL, "bypass_actors are returned to administrators only"), (found.status, found.now), hidden)
        # What one ruleset leaves open another can close: a rule counts when a ruleset that only an administrator bypasses has it
        scene = Scene("public-standard")
        self.tag_ruleset(scene, bypass_actors=[{"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"}])
        listing = copy.deepcopy(scene.get("/rulesets").body)
        listing.append({**listing[1], "id": 1003, "name": "tags-of-the-organization"})
        scene.set("/rulesets", listing)
        strict = copy.deepcopy(scene.get("/rulesets/1002").body)
        strict.update(id=1003, name="tags-of-the-organization", bypass_actors=[])
        scene.set("/rulesets/1003", strict)
        self.assertEqual(OK, scene.finding("rules.tags").status)
        strict["rules"] = [rule for rule in strict["rules"] if rule["type"] != "creation"]            # but not for a rule it lacks
        scene.set("/rulesets/1003", strict)
        found = scene.finding("rules.tags")
        self.assertEqual((FAIL, "bypass for someone but an administrator"), (found.status, found.now))
        scene.set("/rulesets/1003", {**strict, "bypass_actors": None})            # and what is only hidden is hidden
        self.assertEqual(FAIL, scene.finding("rules.tags").status)                # the visible one still lets someone through
        self.tag_ruleset(scene, bypass_actors=[{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}])
        self.assertEqual(OK, scene.finding("rules.tags").status)

    def test_the_bypass_of_a_push_ruleset_is_no_bypass_of_the_pull_request(self):
        """GitHub lists the rules of the push rulesets among those of the branch: on an internal repository the administrator who
        may change the files that run with a token must not read as someone who may merge past the review."""
        solo = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
        scene = Scene("public-standard")
        detail = copy.deepcopy(scene.get("/rulesets/1001").body)
        detail["bypass_actors"] = [solo]
        scene.set("/rulesets/1001", detail)
        scene.set("/rulesets/1004", {"id": 1004, "name": "catalog-protected-paths", "target": "push", "enforcement": "active",
                                     "bypass_actors": [{**solo, "bypass_mode": "always"}], "rules": []})
        rules = copy.deepcopy(scene.get("/rules/branches/main").body)
        rules.append({"type": "file_path_restriction", "ruleset_id": 1004, "ruleset_source_type": "Repository",
                      "ruleset_source": "acme-labs/lab-hub", "parameters": {"restricted_file_paths": [".github/**"]}})
        scene.set("/rules/branches/main", rules)
        found = scene.finding("rules.bypass", solo=True)
        self.assertEqual((WARN, None), (found.status, found.fix))

    def test_the_template_says_dot_pem_and_github_is_asked_for_star_dot_pem(self):
        """GitHub refuses a push ruleset whose extensions do not start with `*.` (422): the managed file keeps the extensions as a
        person writes them, and the ruleset that is requested and compared has them the way GitHub has them."""
        real = Path(settings.__file__).resolve().parents[2] / "templates" / "catalog" / "managed" / ".github" / "rulesets"
        template = json.loads((real / "push.json").read_text(encoding="utf-8"))
        rule = next(rule for rule in template["rules"] if rule["type"] == "file_extension_restriction")
        self.assertTrue(all(each.startswith(".") for each in rule["parameters"]["restricted_file_extensions"]))
        loaded = settings.load_rulesets(real)[0]["push"]
        rule = next(rule for rule in loaded["rules"] if rule["type"] == "file_extension_restriction")
        self.assertTrue(all(each.startswith("*.") for each in rule["parameters"]["restricted_file_extensions"]))

    @covers("rules.push")
    def test_rules_push(self):
        self.assertEqual(OK, Scene("org-private").finding("rules.push").status)
        public = Scene("public-standard")
        self.assertEqual(NA, public.finding("rules.push").status)
        self.assertIn("rules.push", public.audit().inapplicable)                # a public repository cannot have it: not a gap
        personal = Scene("public-standard")
        personal.edit(private=True, visibility="private", owner={"login": "acme", "type": "User"})
        found = personal.finding("rules.push")
        self.assertEqual(NA, found.status)
        self.assertIn("organization plan", found.now)
        self.assertNotIn("rules.push", personal.audit().inapplicable)            # a plan that falls short: --strict counts it
        scene = Scene("org-private")
        scene.set("/rulesets", [item for item in scene.get("/rulesets").body if item["name"] != "catalog-push"])
        found = scene.finding("rules.push")
        self.assertEqual((FAIL, "no push ruleset named catalog-push"), (found.status, found.now))
        self.assertEqual(("POST", "repos/acme-labs/lab-internal/rulesets"), requests_of(found)[0][:2])
        for edit, text in ((lambda rule: rule["parameters"].update(max_file_size=50) if rule["type"] == "max_file_size" else None, "max_file_size=50"),
                           (lambda rule: rule["parameters"]["restricted_file_extensions"].remove("*.pem")
                            if rule["type"] == "file_extension_restriction" else None, "restricted_file_extensions="),
                           (lambda rule: rule["parameters"]["restricted_file_paths"].remove("**/.env")
                            if rule["type"] == "file_path_restriction" else None, "restricted_file_paths=")):
            scene = Scene("org-private")
            detail = copy.deepcopy(scene.get("/rulesets/2003").body)
            for rule in detail["rules"]:
                edit(rule)
            scene.set("/rulesets/2003", detail)
            found = scene.finding("rules.push")
            self.assertEqual(FAIL, found.status, text)
            self.assertIn(text, found.now)
            self.assertEqual("PUT", requests_of(found)[0][0])
        scene = Scene("org-private")
        detail = copy.deepcopy(scene.get("/rulesets/2003").body)
        detail["rules"] = [rule for rule in detail["rules"] if rule["type"] != "max_file_size"]
        scene.set("/rulesets/2003", detail)
        self.assertEqual("max_file_size missing", scene.finding("rules.push").now)
        scene = Scene("org-private")
        listing = copy.deepcopy(scene.get("/rulesets").body)
        listing[2]["enforcement"] = "evaluate"
        scene.set("/rulesets", listing)
        self.assertEqual("enforcement=evaluate", scene.finding("rules.push").now)
        scene = Scene("org-private")
        scene.set("/rulesets", {"message": "Upgrade to GitHub Team to enable this feature."}, 403)
        self.assertEqual(NA, scene.finding("rules.push").status)
        # Nobody pushes past the rule: not a team, not the write role, not even an administrator (the template has none)
        for actors in ([{"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}],
                       [{"actor_id": 4, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
                       [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}], ["x"]):
            scene = Scene("org-private")
            detail = copy.deepcopy(scene.get("/rulesets/2003").body)
            detail["bypass_actors"] = actors
            scene.set("/rulesets/2003", detail)
            found = scene.finding("rules.push")
            self.assertEqual((FAIL, "bypass for someone"), (found.status, found.now), actors)
            method, path, body = requests_of(found)[0]
            self.assertEqual(("PUT", "repos/acme-labs/lab-internal/rulesets/2003", []), (method, path, body["bypass_actors"]))
        for hidden in ("absent", None, "everyone"):          # a reader who is not an administrator is not sent the list
            scene = Scene("org-private")
            if hidden == "absent":
                scene.remove("/rulesets/2003", "bypass_actors")
            else:
                detail = copy.deepcopy(scene.get("/rulesets/2003").body)
                detail["bypass_actors"] = hidden
                scene.set("/rulesets/2003", detail)
            found = scene.finding("rules.push")
            self.assertEqual((MANUAL, "bypass_actors are returned to administrators only"), (found.status, found.now), hidden)
        scene = Scene("org-private")
        scene.remove("/rulesets/2003", "bypass_actors")
        detail = copy.deepcopy(scene.get("/rulesets/2003").body)
        detail["rules"] = [rule for rule in detail["rules"] if rule["type"] != "max_file_size"]
        scene.set("/rulesets/2003", detail)
        self.assertEqual((FAIL, "max_file_size missing"), (scene.finding("rules.push").status, scene.finding("rules.push").now))

    @covers("rules.protected_paths")
    def test_rules_protected_paths(self):
        scene = Scene("org-private")
        self.assertEqual(OK, scene.finding("rules.protected_paths").status)
        self.assertEqual(NA, Scene("public-standard").finding("rules.protected_paths").status)
        detail = copy.deepcopy(scene.get("/rulesets/2004").body)
        detail["bypass_actors"] = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},
                                   {"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}]
        scene.set("/rulesets/2004", detail)
        found = scene.finding("rules.protected_paths")
        self.assertEqual((FAIL, "bypass for someone but an administrator"), (found.status, found.now))
        scene = Scene("org-private")
        detail = copy.deepcopy(scene.get("/rulesets/2004").body)
        detail["rules"][0]["parameters"]["restricted_file_paths"].remove(".catalog/**")
        scene.set("/rulesets/2004", detail)
        found = scene.finding("rules.protected_paths")
        self.assertEqual(FAIL, found.status)
        self.assertIn("restricted_file_paths=", found.now)
        self.assertNotIn(".catalog/**", found.now)
        scene = Scene("org-private")
        scene.set("/rulesets", [item for item in scene.get("/rulesets").body if item["name"] != "catalog-protected-paths"])
        self.assertEqual((FAIL, "no push ruleset named catalog-protected-paths"),
                         (scene.finding("rules.protected_paths").status, scene.finding("rules.protected_paths").now))
        # The control that closes R1 on the internal catalog: it is not ok when nobody can say who bypasses it. A reader who is
        # not an administrator is not sent the list, and then the rule stays manual (the same answer as rules.bypass)
        for hidden in ("absent", None, "admin"):
            scene = Scene("org-private")
            if hidden == "absent":
                scene.remove("/rulesets/2004", "bypass_actors")
            else:
                detail = copy.deepcopy(scene.get("/rulesets/2004").body)
                detail["bypass_actors"] = hidden
                scene.set("/rulesets/2004", detail)
            found = scene.finding("rules.protected_paths")
            self.assertEqual((MANUAL, "bypass_actors are returned to administrators only"), (found.status, found.now), hidden)
            # and so a second writer is not allowed on the strength of it
            scene.set("/collaborators?permission=push", scene.get("/collaborators?permission=push").body
                      + [{"login": "zed", "permissions": {"push": True}}])
            self.assertEqual(FAIL, scene.finding("people.write", **{"as": "internal"}).status, hidden)
        scene = Scene("org-private")
        scene.remove("/rulesets/2004", "bypass_actors")
        detail = copy.deepcopy(scene.get("/rulesets/2004").body)
        detail["rules"][0]["parameters"]["restricted_file_paths"].remove("Makefile")
        scene.set("/rulesets/2004", detail)
        found = scene.finding("rules.protected_paths")
        self.assertEqual(FAIL, found.status)                    # what can be read is judged whether or not the list can
        self.assertIn("restricted_file_paths=", found.now)

    def test_what_the_rules_cannot_say(self):
        scene = Scene("org-private")
        scene.set("/rules/branches/main", {"message": "Resource not accessible by integration"}, 403)      # not the plan's words
        found = {f.id: f for f in scene.audit()}
        for id in settings.ALL_RULES:
            self.assertEqual(MANUAL, found[id].status, id)
        self.assertEqual("HTTP 403: Resource not accessible by integration", found["rules.checks"].now)
        scene = Scene("org-private")
        scene.set("/rules/branches/main", {"message": "Not Found"}, 404)
        self.assertEqual({MANUAL}, {f.status for f in scene.audit() if f.id in settings.ALL_RULES})
        # A repository with no commit yet: the signature rule would refuse the first one
        scene = Scene("org-private")
        scene.set("/branches/main", {"message": "Branch not found"}, 404)
        found = {f.id: f for f in scene.audit()}
        self.assertEqual({MANUAL}, {found[i].status for i in settings.ALL_RULES})
        self.assertIn("no commit on main yet", found["rules.deletion"].now)
        self.assertIn("Push it first", found["rules.deletion"].fix)

    # ------------------------------------------------------------------ security.*, files.*, people.*

    @covers("security.scanning")
    def test_security_scanning(self):
        self.assertEqual(OK, Scene("public-standard").finding("security.scanning").status)
        scene = Scene("public-standard")
        scene.edit(security_and_analysis={"secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "disabled"}})
        found = scene.finding("security.scanning")
        self.assertEqual((FAIL, "secret_scanning=enabled, secret_scanning_push_protection=disabled"), (found.status, found.now))
        self.assertEqual([("PATCH", "repos/acme-labs/lab-hub", {"security_and_analysis": {
            "secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"}}})], requests_of(found))
        scene = Scene("org-private")
        scene.edit(security_and_analysis={"secret_scanning": {"status": "disabled"}, "secret_scanning_push_protection": {"status": "disabled"}})
        self.assertEqual(WARN, scene.finding("security.scanning").status)           # a paid add-on on a private repository
        # Audited as a public repository it is the same paid add-on on the same private repository: a warning, not a failure
        found = scene.finding("security.scanning", **as_public())
        self.assertEqual((WARN, "secret_scanning=disabled, secret_scanning_push_protection=disabled"), (found.status, found.now))
        self.assertEqual(WARN, scene.finding("security.scanning", **{"as": "internal"}).status)
        scene.edit(visibility="internal")
        self.assertEqual(WARN, scene.finding("security.scanning").status)
        mine = Scene("user-private-free").finding("security.scanning")
        self.assertEqual((NA, "needs a public repository or GitHub Secret Protection"), (mine.status, mine.now))
        scene = Scene("user-private-free")
        scene.edit(permissions={"admin": False, "push": True, "pull": True})
        self.assertEqual(MANUAL, scene.finding("security.scanning").status)

    @covers("security.dependabot")
    def test_security_dependabot(self):
        self.assertEqual(OK, Scene("public-standard").finding("security.dependabot").status)
        found = Scene("user-private-free").finding("security.dependabot")
        self.assertEqual((FAIL, "alerts disabled, security updates disabled"), (found.status, found.now))
        self.assertEqual([("PUT", "repos/acme/lab-hub/vulnerability-alerts", None), ("PUT", "repos/acme/lab-hub/automated-security-fixes", None)],
                         requests_of(found))              # alerts first: the updates need them
        scene = Scene("public-standard")
        scene.set("/automated-security-fixes", {"message": "Not Found"}, 404)
        found = scene.finding("security.dependabot")
        self.assertEqual((FAIL, "security updates disabled"), (found.status, found.now))
        self.assertEqual(1, len(requests_of(found)))
        scene = Scene("public-standard")
        scene.set("/automated-security-fixes", {"enabled": False, "paused": False})
        self.assertEqual(FAIL, scene.finding("security.dependabot").status)
        scene = Scene("public-standard")
        scene.set("/vulnerability-alerts", {"message": "Forbidden"}, 403)
        self.assertEqual(MANUAL, scene.finding("security.dependabot").status)
        scene = Scene("public-standard")
        scene.set("/automated-security-fixes", {"message": "Server Error"}, 500)
        self.assertEqual(MANUAL, scene.finding("security.dependabot").status)

    @covers("security.reporting")
    def test_security_reporting(self):
        self.assertEqual(OK, Scene("public-standard").finding("security.reporting").status)
        scene = Scene("public-standard")
        scene.set("/private-vulnerability-reporting", {"enabled": False})
        found = scene.finding("security.reporting")
        self.assertEqual((FAIL, "disabled"), (found.status, found.now))
        self.assertEqual([("PUT", "repos/acme-labs/lab-hub/private-vulnerability-reporting", None)], requests_of(found))
        scene.set("/private-vulnerability-reporting", {"message": "Forbidden"}, 403)
        self.assertEqual(MANUAL, scene.finding("security.reporting").status)
        private = Scene("org-private")
        self.assertEqual(NA, private.finding("security.reporting").status)
        self.assertIn("security.reporting", private.audit().inapplicable)         # it does not exist on a private repository
        self.assertNotIn("security.reporting", private.audit(**as_public()).inapplicable)     # but the gate wants it

    @covers("files.codeowners")
    def test_files_codeowners(self):
        self.assertEqual(OK, Scene("public-standard").finding("files.codeowners").status)
        found = Scene("user-private-free").finding("files.codeowners")
        self.assertEqual((FAIL, "no CODEOWNERS file"), (found.status, found.now))
        self.assertIn("catalog.py upgrade --adopt", found.fix)
        scene = Scene("public-standard")
        scene.set("/codeowners/errors", {"errors": [{"line": 3, "kind": "Unknown owner", "message": "Unknown owner on line 3: @nobody"}]})
        found = scene.finding("files.codeowners")
        self.assertEqual(FAIL, found.status)
        self.assertEqual("1 error(s), the first at line 3: Unknown owner on line 3: @nobody", found.now)
        # A personal private repository may not ask for a review from the file (V21): a valid file is not counted there
        mine = Scene("user-private-free")
        mine.set("/codeowners/errors", {"errors": []})
        self.assertEqual(NA, mine.finding("files.codeowners").status)
        self.assertNotIn("files.codeowners", mine.audit().inapplicable)
        scene.set("/codeowners/errors", {"message": "Server Error"}, 500)
        self.assertEqual(MANUAL, scene.finding("files.codeowners").status)

    @covers("people.write")
    def test_people_write(self):
        found = Scene("org-private").finding("people.write")
        self.assertEqual((MANUAL, "maintainer-one"), (found.status, found.now))
        two = {"login": "zed", "permissions": {"push": True}}
        scene = Scene("org-private")
        scene.set("/collaborators?permission=push", scene.get("/collaborators?permission=push").body + [two])
        found = scene.finding("people.write")
        self.assertEqual((MANUAL, "maintainer-one, zed"), (found.status, found.now))       # rulesets stand between a branch and a publish
        mine = Scene("user-private-free")
        mine.set("/collaborators?permission=push", mine.get("/collaborators?permission=push").body + [two])
        found = mine.finding("people.write")
        self.assertEqual((FAIL, "acme, zed"), (found.status, found.now))            # no rulesets: write access is publish access
        self.assertIn("one writer", found.fix)
        # The internal catalog: a second writer needs the protected paths rule
        self.assertEqual(MANUAL, scene.finding("people.write", **{"as": "internal"}).status)
        scene.set("/rulesets", [item for item in scene.get("/rulesets").body if item["name"] != "catalog-protected-paths"])
        found = scene.finding("people.write", **{"as": "internal"})
        self.assertEqual(FAIL, found.status)
        self.assertIn("protected paths", found.fix)
        scene = Scene("org-private")
        scene.set("/collaborators?permission=push", [{"login": "reader", "permissions": {"push": False}}, {"login": "x"}, 7, None])
        self.assertEqual("x", scene.finding("people.write").now)
        scene.set("/collaborators?permission=push", [{"login": f"user-{number:02}", "permissions": {"push": True}} for number in range(25)])
        self.assertTrue(scene.finding("people.write").now.endswith(" and 5 more"))
        scene.set("/collaborators?permission=push", {"message": "Must have push access to view repository collaborators."}, 403)
        self.assertEqual(MANUAL, scene.finding("people.write").status)


SAMPLE = {
    # What prints for the personal repository, line by line: id -> (status, title, now). A title that the
    # standard words differently since the sample was made is left out
    "repo.merge": (FAIL, "Squash merges only, with the pull request title and no body; the branch goes with the merge; no auto-merge; no wiki", None),
    "repo.forking": (NA, "Nobody forks a private repository", "the repository belongs to a user: GitHub lets only an organization turn forking off"),
    "repo.immutable_releases": (WARN, "A published release cannot change", "enabled=false"),
    "actions.pinning": (FAIL, "Every action is pinned to a full commit, or the run does not start", "sha_pinning_required=false"),
    "actions.listed": (WARN, "Only listed actions run", 'enabled=true, allowed_actions="all"'),
    "actions.token": (OK, "The token reads by default and cannot approve a pull request", None),
    "actions.fork": (OK, "A fork's pull request gets no write token and no secret", None),
    "actions.access": (OK, "Other repositories cannot call this repository's workflows", None),
    "actions.retention": (WARN, "Logs and artifacts are kept 30 days at most", "days=90"),
    "actions.secrets": (OK, "No secret but LAB_DEPLOY_KEY", None),
    "actions.org_secrets": (NA, "Organization secrets and variables scoped to this repository", "the repository belongs to a user"),
    "actions.runners": (OK, "No self-hosted runner can run this repository's workflows", None),
    "security.scanning": (NA, "Secret scanning and push protection", "needs a public repository or GitHub Secret Protection"),
    "security.reporting": (NA, "Private vulnerability reporting", None),
    "files.codeowners": (FAIL, "CODEOWNERS is valid", "no CODEOWNERS file"),
    "people.write": (MANUAL, "Who can push a branch, and so publish an image", "acme"),
}
EXPECTED_IDS = (
    "repo.default_branch", "repo.merge", "repo.topic", "repo.forking", "repo.immutable_releases", "actions.pinning",
    "actions.listed", "actions.token", "actions.fork", "actions.access", "actions.retention", "actions.secrets",
    "actions.org_secrets", "actions.runners", "actions.oidc", "actions.nightly", "lab.key", "environments.policy",
    "packages.actions_access", "rules.deletion", "rules.non_fast_forward", "rules.required_linear_history",
    "rules.required_signatures", "rules.pull_request", "rules.checks", "rules.bypass", "rules.tags", "rules.push",
    "rules.protected_paths", "security.scanning", "security.dependabot", "security.reporting", "files.codeowners", "people.write")


def squash(text):
    return re.sub(r"\s+", " ", text).strip()


class Whole(Base):
    """The three recorded repositories, as a whole."""

    def test_a_repository_at_the_standard_has_nothing_to_fix(self):
        for name, repo in (("public-standard", "acme-labs/lab-hub"), ("org-private", "acme-labs/lab-internal")):
            scene = Scene(name)
            found = scene.audit()
            self.assertEqual([], [(f.id, f.status) for f in found if f.status in (FAIL, WARN)], name)
            # Every identifier, in the order of the table; a public repository has no `actions.access` to say
            self.assertEqual([i for i in EXPECTED_IDS if i != "actions.access" or name != "public-standard"], [f.id for f in found], name)
            self.assertEqual(0, settings.exit_code(found), name)
            self.assertEqual(repo, found.repo)

    def test_the_personal_repository_says_what_the_plan_cannot_give(self):
        found = Scene("user-private-free").audit()
        self.assertEqual(list(EXPECTED_IDS), [f.id for f in found])               # every identifier, in the order of the table
        self.assertEqual({FAIL: 5, WARN: 3, OK: 6, NA: 18, MANUAL: 2}, settings.summary(found))
        by = {f.id: f for f in found}
        for id, (status, title, now) in SAMPLE.items():
            self.assertEqual(status, by[id].status, id)
            self.assertEqual(title, by[id].title, id)
            if now is not None:
                self.assertEqual(now, by[id].now, id)
        self.assertEqual("private, user-owned", found.about)
        self.assertEqual(1, found.level)

    def test_the_text_is_the_table_of_the_standard(self):
        text = settings.render(Scene("user-private-free").audit())
        lines = text.splitlines()
        self.assertEqual("Catalog standard 1 for acme/lab-hub (private, user-owned)", lines[0])
        wanted = {squash(f"{status} {id} {title}") for id, (status, title, _) in SAMPLE.items() if title}
        have = {squash(line) for line in lines}
        self.assertEqual(set(), wanted - have)
        self.assertIn("n.a. repo.forking Nobody forks a private repository", have)
        self.assertIn("why: the repository belongs to a user", have)
        self.assertIn("fix: PATCH repos/acme/lab-hub", have)
        self.assertEqual("Summary: 5 fail, 3 warn, 6 ok, 18 n.a., 2 manual", lines[-1])
        width = max(len(i) for i in EXPECTED_IDS)
        self.assertIn(f"  {FAIL:<6} {'repo.merge':<{width}} {SAMPLE['repo.merge'][1]}", lines)      # the columns of the sample

    def test_auditing_twice_gives_identical_output(self):
        for name in ("user-private-free", "public-standard", "org-private"):
            first, second = Scene(name), Scene(name)
            for fmt in ("text", "json", "github"):
                self.assertEqual(settings.render(first.audit(), fmt), settings.render(second.audit(), fmt), (name, fmt))
            self.assertEqual(settings.render(first.audit()), settings.render(first.audit()))              # the same hub, asked again
            options = {"as": "public", "solo": True}
            self.assertEqual(settings.render(Scene(name).audit(**options)), settings.render(Scene(name).audit(**options)))

    def test_the_json_is_one_document_with_sorted_keys(self):
        document = json.loads(settings.render(Scene("user-private-free").audit(), "json"))
        self.assertEqual(["about", "findings", "not_applicable", "repository", "standard", "summary"], list(document))
        self.assertEqual(["fix", "id", "now", "status", "title"], list(document["findings"][0]))
        self.assertEqual(list(EXPECTED_IDS), [f["id"] for f in document["findings"]])
        self.assertEqual({"fail": 5, "warn": 3, "ok": 6, "n.a.": 18, "manual": 2}, document["summary"])
        self.assertEqual(["actions.nightly", "actions.oidc", "actions.org_secrets", "repo.forking", "security.reporting"], document["not_applicable"])
        merge = [f for f in document["findings"] if f["id"] == "repo.merge"][0]
        self.assertEqual("PATCH", merge["fix"][0]["method"])

    def test_strict_counts_a_warning_and_a_plan_that_falls_short_but_not_a_rule_that_does_not_apply(self):
        self.assertEqual(0, settings.exit_code(Scene("public-standard").audit(), strict=True))
        self.assertEqual(0, settings.exit_code(Scene("org-private").audit(), strict=True))       # no reporting on a private repository
        self.assertEqual(0, settings.exit_code(Scene("org-private").audit(**{"as": "internal"}), strict=True))
        mine = Scene("user-private-free").audit()
        self.assertEqual(1, settings.exit_code(mine))
        scene = Scene("org-private")
        scene.set("/actions/permissions/artifact-and-log-retention", {"days": 90})
        self.assertEqual(0, settings.exit_code(scene.audit()))
        self.assertEqual(1, settings.exit_code(scene.audit(), strict=True))
        # A plan without rules: not a failure, but the gate fails on it
        scene = Scene("public-standard")
        scene.set("/rules/branches/main", {"message": "Upgrade to GitHub Pro or make this repository public to enable this feature."}, 403)
        self.assertEqual(0, settings.exit_code(scene.audit()))
        self.assertEqual(1, settings.exit_code(scene.audit(), strict=True))
        gate = Scene("org-private").audit(**as_public())
        self.assertEqual(1, settings.exit_code(gate, strict=True))            # still private: reporting cannot be on

    def test_strict_leaves_alone_what_the_plan_sells_and_the_standard_does_not_ask_for(self):
        """Secret scanning is a paid add-on on a private or internal repository and the standard asks for it nowhere there: `--as internal --strict` is clean without it. The go-public gate still lists it."""
        scene = Scene("org-private")
        scene.edit(security_and_analysis={"secret_scanning": {"status": "disabled"}, "secret_scanning_push_protection": {"status": "disabled"}})
        for options in ({}, {"as": "private"}, {"as": "internal"}):
            found = scene.audit(**options)
            self.assertEqual(["security.scanning"], [f.id for f in found if f.status == WARN], options)
            self.assertEqual(0, settings.exit_code(found), options)
            self.assertEqual(0, settings.exit_code(found, strict=True), options)
        gate = scene.audit(**as_public())
        self.assertEqual(WARN, {f.id: f for f in gate}["security.scanning"].status)
        self.assertEqual(1, settings.exit_code(gate, strict=True))
        self.assertNotIn("security.scanning", gate.optional)
        self.assertNotIn("security.scanning", gate.inapplicable)
        # Another warning of the same repository is still counted, and a failure is never left alone
        scene.set("/actions/permissions/artifact-and-log-retention", {"days": 90})
        self.assertEqual(1, settings.exit_code(scene.audit(**{"as": "internal"}), strict=True))
        # A public repository gets it free: a failure
        public = Scene("public-standard")
        public.edit(security_and_analysis={"secret_scanning": {"status": "disabled"}, "secret_scanning_push_protection": {"status": "enabled"}})
        found = public.audit()
        self.assertEqual(FAIL, {f.id: f for f in found}["security.scanning"].status)
        self.assertEqual(1, settings.exit_code(found))
        # The same through the command: it says so, and exits 0
        scene = Scene("org-private")
        scene.edit(security_and_analysis={"secret_scanning": {"status": "disabled"}, "secret_scanning_push_protection": {"status": "disabled"}})
        run = Run(["acme-labs/lab-internal", "--as", "internal", "--strict"], scene)
        self.assertEqual(0, run.code)
        self.assertIn("warn   security.scanning", run.text)
        self.assertEqual(1, Run(["acme-labs/lab-internal", "--as", "public", "--strict"], scene).code)

    def test_a_rule_is_not_a_plan_limit_unless_github_says_so(self):
        scene = Scene("public-standard")
        scene.set("/rules/branches/main", {"message": "Upgrade to GitHub Pro or make this repository public to enable this feature."}, 403)
        self.assertEqual({NA}, {f.status for f in scene.audit() if f.id in settings.ALL_RULES})
        scene.set("/rules/branches/main", {"message": "Must have admin rights to Repository."}, 403)
        self.assertEqual({MANUAL}, {f.status for f in scene.audit() if f.id in settings.ALL_RULES})

    def test_the_effective_rules_alone(self):
        scene = Scene("org-private")
        found = scene.audit(effective_only=True)
        self.assertEqual(["rules.deletion", "rules.non_fast_forward", "rules.required_linear_history", "rules.required_signatures",
                          "rules.pull_request", "rules.checks", "files.codeowners"], [f.id for f in found])
        # What a read-only token reads: the repository, the effective rules and the files, nothing of the settings
        paths = {path for method, path, _ in scene.hub.calls}
        self.assertEqual({"repos/acme-labs/lab-internal", "repos/acme-labs/lab-internal/rules/branches/main",
                          "repos/acme-labs/lab-internal/branches/main", "repos/acme-labs/lab-internal/codeowners/errors"}, paths)
        self.weaken_one(scene)
        self.assertEqual(FAIL, {f.id: f for f in scene.audit(effective_only=True)}["rules.pull_request"].status)

    def weaken_one(self, scene):
        rules = copy.deepcopy(scene.get("/rules/branches/main").body)
        for rule in rules:
            if rule["type"] == "pull_request":
                rule["parameters"]["require_code_owner_review"] = False
        scene.set("/rules/branches/main", rules)

    def test_the_visibility_a_repository_is_audited_as(self):
        private = Scene("org-private")
        found = private.audit(**as_public())
        self.assertEqual("private, organization-owned, audited as public", found.about)
        self.assertEqual("A public repository can be forked", {f.id: f for f in found}["repo.forking"].title)
        self.assertNotIn("actions.access", {f.id for f in found})
        self.assertEqual("public, organization-owned", Scene("public-standard").audit().about)
        for name in ("public-standard",):
            for kind in ("private", "internal"):
                with self.assertRaises(settings.Usage) as raised:
                    Scene(name).audit(**{"as": kind})
                self.assertEqual(f"--as {kind} cannot describe a public repository.", str(raised.exception))
        self.assertEqual(NA, {f.id: f for f in Scene("org-private").audit(**as_public())}["rules.push"].status)
        self.assertEqual(OK, {f.id: f for f in Scene("org-private").audit(**{"as": "internal"})}["rules.push"].status)
        for bad in ("secret", "", 3):
            with self.assertRaises(settings.Usage):
                Scene("org-private").audit(**{"as": bad})

    def test_an_internal_repository_is_audited_as_it_is(self):
        scene = Scene("org-private")
        scene.edit(visibility="internal")
        found = scene.audit()
        self.assertEqual("internal, organization-owned", found.about)
        self.assertEqual([], [(f.id, f.status) for f in found if f.status in (FAIL, WARN)])
        self.assertEqual("Nobody forks a private repository", {f.id: f for f in found}["repo.forking"].title)

    def test_names_and_options_that_are_not_names_are_refused(self):
        scene = Scene("org-private")
        for repo in ("a b/c", "../x/y", "x", "a/b/c", "a/.git", "a/b.git", "a/..", "", "-x/y", "a/b\n", None, 3):
            with self.assertRaises(settings.Usage):
                settings.audit(repo, scene.hub, {})
        for options in ({"required_checks": [""]}, {"required_checks": ["a\nb"]}, {"required_checks": [3]}, {"required_checks": ["x" * 101]},
                        {"allow_org_secrets": [""]}, {"allow_org_secrets": ["a\nb"]}):
            with self.assertRaises(settings.Usage):
                settings.audit(scene.repo, scene.hub, options)
        self.assertEqual([], scene.hub.calls)
        scene.table.pop(f"GET repos/{scene.repo}")
        with self.assertRaises(settings.Refused) as raised:
            scene.audit()
        self.assertEqual("acme-labs/lab-internal was not found, or you cannot read it (HTTP 404: Not Found).", str(raised.exception))
        scene.set("", ["not", "an", "object"])
        with self.assertRaises(settings.Refused):
            scene.audit()


class Degraded(Base):
    def test_without_the_template_the_rulesets_are_not_compared_and_none_is_sent(self):
        self.patch(settings, "RULESETS", DATA / "does-not-exist")
        scene = Scene("org-private")
        scene.set("/rules/branches/main", [r for r in scene.get("/rules/branches/main").body if r["type"] not in ("deletion", "pull_request")])
        found = {f.id: f for f in scene.audit()}
        self.assertEqual("Catalog standard for acme-labs/lab-internal (private, organization-owned)", settings.render(scene.audit()).splitlines()[0])
        self.assertEqual(FAIL, found["rules.deletion"].status)
        self.assertIsInstance(found["rules.deletion"].fix, str)
        self.assertIn("The Lab's rulesets are not here", found["rules.deletion"].fix)
        for id in ("rules.pull_request", "rules.checks", "rules.push", "rules.protected_paths"):
            self.assertEqual(MANUAL, found[id].status, id)
            self.assertIn("could not be read", found[id].now)
        self.assertEqual([], settings.apply(scene.repo, scene.hub, list(found.values()), True))
        self.assertEqual([], scene.hub.writes())

    def test_a_template_that_is_not_a_ruleset_is_not_sent(self):
        folder = self.folder()
        for name in ("default-branch.json", "tags.json", "push.json", "protected-paths.json"):
            (folder / name).write_text((TEMPLATE / name).read_text(), encoding="utf-8")
        (folder / "push.json").write_text(json.dumps({"name": 3, "target": "branch", "rules": []}), encoding="utf-8")
        self.patch(settings, "RULESETS", folder)
        scene = Scene("org-private")
        self.assertEqual(MANUAL, scene.finding("rules.push").status)
        self.assertEqual(MANUAL, scene.finding("rules.pull_request").status)


class Limits(Base):
    def test_a_megabyte_of_text_in_a_reply_is_cut_in_under_a_second(self):
        import base64
        import time
        big = "x" * (1024 * 1024)
        scene = Scene("org-private")
        scene.edit(topics=[big], default_branch="main")
        scene.set("/actions/secrets", {"secrets": [{"name": big}]})
        scene.set("/collaborators?permission=push", [{"login": big}])
        scene.set("/actions/organization-secrets", {"secrets": [{"name": big}]})
        scene.set("/codeowners/errors", {"errors": [{"line": big, "message": big}]})
        scene.set("/contents/.github/standard.json?ref=main", {"encoding": "base64", "size": 10, "content": base64.b64encode(big.encode()).decode()})
        scene.set("/actions/permissions", {"enabled": big, "allowed_actions": big, "sha_pinning_required": big})
        started = time.monotonic()
        found = scene.audit()
        text = settings.render(found, "text") + settings.render(found, "json") + settings.render(found, "github")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertLess(len(text), 100000)
        self.assertEqual("the level of the standard cannot be read: it cannot be read", {f.id: f for f in found}["actions.nightly"].now)

    def test_a_megabyte_of_standard_json_is_not_read(self):
        import base64
        scene = Scene("org-private")
        raw = b'{"standard": 3, "x": "' + b"y" * (1024 * 1024) + b'"}'
        scene.set("/contents/.github/standard.json?ref=main", {"encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode()})
        self.assertEqual(MANUAL, scene.finding("actions.nightly").status)

    def test_brackets_nested_deeper_than_python_reads_are_a_finding_and_not_a_trace(self):
        """A `standard.json` a contributor merged: 4 KB of brackets passes the size guard and takes the parser of Python 3.9
        past its limit; it would end the audit, the nightly one and the audit of the whole fleet."""
        import base64
        for raw in (b"[" * 2040 + b"]" * 2040, b'{"standard": ' + b"[" * 2000 + b"]" * 2000 + b"}", b'{"standard": 1, "lab": ' + b"{" * 100 + b"}" * 100 + b"}"):
            scene = Scene("org-private")
            scene.set("/contents/.github/standard.json?ref=main", {"encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode()})
            self.assertEqual(MANUAL, scene.finding("actions.nightly").status, raw[:20])
            self.assertTrue(scene.audit())
            out, err = io.StringIO(), io.StringIO()
            self.assertIn(settings.main(["acme-labs/lab-internal"], runner=scene.hub, stdout=out, stderr=err), (0, 1))
            self.assertEqual("", err.getvalue())

    def test_a_recursion_error_of_the_parser_is_a_finding_whichever_python_runs(self):
        """Python 3.9 stops at about a thousand brackets and 3.12 and later at more: the parser is made to stop, so that the
        answer is the same on every Python."""
        import base64
        real = json.loads

        def stops(text, *args, **kwargs):
            if isinstance(text, str) and text.startswith("[[[["):
                raise RecursionError("maximum recursion depth exceeded while decoding a JSON array from a unicode string")
            return real(text, *args, **kwargs)
        raw = b"[" * 40 + b"]" * 40
        scene = Scene("org-private")
        scene.set("/contents/.github/standard.json?ref=main", {"encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode()})
        with mock.patch.object(json, "loads", stops):
            found = scene.finding("actions.nightly")
            self.assertEqual((MANUAL, "the level of the standard cannot be read: it is not JSON"), (found.status, found.now))
            self.assertTrue(scene.audit())
            # and a reply of gh that is nested so deep: no trace, the reply is what gh printed
            gh = settings.Gh(lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "[[[[1]]]]", ""))
            self.assertEqual(Reply(200, {"message": "[[[[1]]]]"}), gh("GET", "repos/acme/lab-hub"))

    def test_the_fork_key_is_the_one_github_knows(self):
        self.assertIn("send_write_tokens_to_workflows", SOURCE)
        self.assertNotRegex(SOURCE, r"send_write_tokens(?!_to_workflows)")


class Hostile(Base):
    """What GitHub says is whatever a writer of the repository made it: the audit does not trust its shape, and what it shows
    is cut and has no control character."""

    VALUES = (None, "text", 5, 1.5, True, [], {}, [1, "a", None, [2]], {"message": 5}, {"a": {"b": [1]}}, "x" * 20000)

    def test_a_reply_of_any_shape_is_a_finding_and_not_a_trace(self):
        for name in ("user-private-free", "public-standard", "org-private"):
            repo, table = load(name)
            for key in sorted(table):
                if key == f"GET repos/{repo}":
                    continue
                for value in self.VALUES:
                    for code in (200, 403, 404):
                        scene = Scene(name)
                        scene.table[key] = Reply(code, copy.deepcopy(value))
                        for options in ({"effective_only": True}, {"as": "public", "solo": True}):
                            found = scene.audit(**options)
                            self.assertTrue(all(f.status in settings.STATUSES for f in found), (name, key, value, code))
                            for fmt in ("text", "json", "github"):
                                settings.render(found, fmt)

    def test_a_value_of_any_shape_inside_a_reply_is_a_finding_and_not_a_trace(self):
        """Each leaf of each recorded reply (the first of a list), one at a time, replaced by a value of another kind."""
        def leaves(node, path=()):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from leaves(value, path + (key,))
            elif isinstance(node, list) and node:
                yield from leaves(node[0], path + (0,))
            yield path

        def put(node, path, value):
            cursor = node
            for step in path[:-1]:
                cursor = cursor[step]
            cursor[path[-1]] = value
            return node
        ran = 0
        for name in ("user-private-free", "public-standard", "org-private"):
            repo, table = load(name)
            for key, reply in sorted(table.items()):
                if not isinstance(reply.body, (dict, list)):
                    continue
                for path in leaves(reply.body):
                    if not path:
                        continue
                    for value in (None, "text", [[1]], {"a": [1]}, 7):
                        changed = dict(table)                  # a reply is never changed by an audit: the others are shared
                        changed[key] = Reply(reply.code, put(copy.deepcopy(reply.body), path, value))
                        scene = Scene(name, loaded=(repo, changed))
                        try:
                            found = scene.audit(**{"as": "public", "solo": True})
                        except settings.Unusable:
                            continue            # the repository itself no longer reads as one
                        self.assertTrue(all(f.status in settings.STATUSES for f in found), (name, key, path, value))
                        for fmt in ("text", "json", "github"):
                            settings.render(found, fmt)
                        ran += 1
        self.assertGreater(ran, 2000)

    def test_the_repository_itself_of_any_shape(self):
        for value in self.VALUES:
            scene = Scene("org-private")
            scene.table[f"GET repos/{scene.repo}"] = Reply(200, copy.deepcopy(value))
            if isinstance(value, dict):
                scene.audit()
            else:
                with self.assertRaises(settings.Refused):
                    scene.audit()
        scene = Scene("org-private")
        scene.edit(owner="x", default_branch=5, topics="a", permissions=7, visibility=3, allow_forking="yes")
        self.assertTrue(scene.audit())

    def test_what_is_shown_is_cut_and_has_no_control_character(self):
        scene = Scene("org-private")
        evil = "bad\nname::error title=x::pwned%0A\x1b[31m‮" + "z" * 500
        scene.edit(topics=[evil])
        scene.set("/actions/secrets", {"secrets": [{"name": evil}, {"name": "LAB_DEPLOY_KEY"}]})
        scene.set("/collaborators?permission=push", [{"login": evil, "permissions": {"push": True}}])
        scene.set("/actions/organization-secrets", {"secrets": [{"name": evil}]})
        scene.set("/codeowners/errors", {"errors": [{"line": evil, "message": evil}]})
        found = scene.audit()
        for finding in found:
            self.assertTrue(finding.now.isprintable(), finding.id)
            self.assertLessEqual(len(finding.now), 400, finding.id)
        text = settings.render(found, "text")
        self.assertFalse(re.search(r"[\x00-\x09\x0b-\x1f\x7f‮]", text))
        lines = settings.render(found, "github").splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertRegex(line, r"^::(error|warning) title=[a-z_.]+::")
            self.assertNotIn("\n", line)
        self.assertIn("pwned%250A", "".join(lines))               # a percent sign cannot begin an escape of the workflow command


class Coverage(unittest.TestCase):
    def test_the_identifiers_are_those_of_the_table_of_7_5(self):
        self.assertEqual(EXPECTED_IDS, settings.IDS)
        self.assertEqual(34, len(set(settings.IDS)))

    def test_every_identifier_has_a_test_of_its_own(self):
        covered = {}
        for name in dir(Audit):
            for id in getattr(getattr(Audit, name), "covers", ()):
                covered.setdefault(id, []).append(name)
        self.assertEqual(set(settings.IDS), set(covered))
        self.assertEqual([], [id for id, names in covered.items() if len(names) != 1])

    def test_the_signatures_are_the_frozen_ones(self):
        """Audit(repo, runner, options), apply(repo, runner, findings, yes), audit_fleet(owner, runner, options)
        and upgrade_fleet(owner, runner, lab_ref); what a package adds comes after them and has a default."""
        import inspect
        from lab_ui.catalogs import fleet
        for function, names in ((settings.audit, ["repo", "runner", "options"]), (settings.apply, ["repo", "runner", "findings", "yes"]),
                                (fleet.audit_fleet, ["owner", "runner", "options"]), (fleet.upgrade_fleet, ["owner", "runner", "lab_ref"])):
            parameters = list(inspect.signature(function).parameters.values())
            self.assertEqual(names, [p.name for p in parameters[:len(names)]], function.__name__)
            self.assertTrue(all(p.default is not p.empty or p.kind is p.VAR_KEYWORD for p in parameters[len(names):]), function.__name__)

    def test_a_title_is_one_line_in_sentence_case(self):
        for id, title in settings.TITLES.items():
            self.assertRegex(title, r"^[A-Z]", id)
            self.assertNotRegex(title, r"[\n·]", id)
            self.assertFalse(title.endswith("."), id)


TEMPLATE_RULESETS = settings.load_rulesets(TEMPLATE)[0]
PLAN_WORDS = "Upgrade to GitHub Team to enable this feature."


def crafted(*requests, status=FAIL):
    return [settings.Finding("repo.merge", status, "t", "", list(requests))]


class Apply(Base):
    """`--apply`: the requests, none of them without `yes`, and nothing the module may not do."""

    def no_removal(self, hub):
        self.assertEqual([], [call for call in hub.calls if call[0] not in ("GET", "PATCH", "POST", "PUT")])

    def broken(self):
        """A repository of an organization where almost everything is off, served by a hub that remembers the writes."""
        scene = Scene("org-private", live=True)
        scene.edit(allow_forking=True, allow_merge_commit=True, delete_branch_on_merge=False,
                   squash_merge_commit_message="COMMIT_MESSAGES", topics=[])
        scene.set("/actions/permissions", {"enabled": True, "allowed_actions": "all", "sha_pinning_required": False})
        scene.set("/actions/permissions/workflow", {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True})
        scene.set("/actions/permissions/artifact-and-log-retention", {"days": 90})
        scene.set("/actions/permissions/access", {"access_level": "organization"})
        scene.edit("/actions/permissions/fork-pr-workflows-private-repos", send_write_tokens_to_workflows=True)
        scene.set("/vulnerability-alerts", {"message": "Vulnerability alerts are disabled."}, 404)
        scene.set("/automated-security-fixes", {"message": "Not Found"}, 404)
        scene.set("/immutable-releases", {"enabled": False, "enforced_by_owner": False})
        scene.set("/actions/workflows", {"workflows": [{"id": 12, "path": ".github/workflows/nightly.yml", "state": "disabled_inactivity"}]})
        scene.set("/environments", {"environments": []})
        scene.hub.reset_rulesets()
        return scene

    def test_without_yes_no_request_but_a_read_is_made(self):
        scene = Scene("user-private-free")
        findings = scene.audit()
        applied = settings.apply(scene.repo, scene.hub, findings, False)
        self.assertEqual([], scene.hub.writes())
        self.assertEqual({"planned"}, {item.outcome for item in applied})
        self.assertEqual([("PATCH", "repos/acme/lab-hub"), ("PUT", "repos/acme/lab-hub/topics"),
                          ("PUT", "repos/acme/lab-hub/actions/permissions"), ("PUT", "repos/acme/lab-hub/vulnerability-alerts"),
                          ("PUT", "repos/acme/lab-hub/automated-security-fixes")], [(item.method, item.path) for item in applied])
        self.assertEqual({"allow_merge_commit": False, "allow_rebase_merge": False, "delete_branch_on_merge": True,
                          "allow_update_branch": True, "has_wiki": False, "squash_merge_commit_title": "PR_TITLE",
                          "squash_merge_commit_message": "BLANK"}, applied[0].body)      # the merge findings, one request
        self.assertTrue(all(call[0] == "GET" for call in scene.hub.calls))

    def test_warnings_are_applied_only_when_asked(self):
        scene = Scene("user-private-free")
        findings = scene.audit()
        more = settings.apply(scene.repo, scene.hub, findings, False, warnings=True)
        self.assertEqual({"PUT repos/acme/lab-hub/immutable-releases", "PUT repos/acme/lab-hub/actions/permissions/artifact-and-log-retention"},
                         {f"{item.method} {item.path}" for item in more} - {f"{item.method} {item.path}" for item in
                                                                              settings.apply(scene.repo, scene.hub, findings, False)})
        self.assertNotIn("selected-actions", " ".join(item.path for item in more))       # the list of actions waits for V3

    def test_with_yes_the_requests_are_made_in_order(self):
        scene = Scene("user-private-free")
        findings = scene.audit()
        planned = settings.apply(scene.repo, scene.hub, findings, False)
        done = settings.apply(scene.repo, scene.hub, findings, True)
        self.assertEqual({"done"}, {item.outcome for item in done})
        self.assertEqual([(item.method, item.path, item.body) for item in planned], scene.hub.writes())
        self.no_removal(scene.hub)

    def test_a_second_apply_says_there_is_nothing_to_change(self):
        scene = self.broken()
        findings = scene.audit()
        self.assertGreater(len([f for f in findings if f.status == FAIL]), 10)
        done = settings.apply(scene.repo, scene.hub, findings, True, warnings=True)
        self.assertEqual({"done"}, {item.outcome for item in done}, [item for item in done if item.outcome != "done"])
        self.no_removal(scene.hub)
        again = scene.audit()
        self.assertEqual([], [(f.id, f.status, f.now) for f in again if f.status in (FAIL, WARN)])
        self.assertEqual([], settings.apply(scene.repo, scene.hub, again, True, warnings=True))
        self.assertEqual("Nothing to change", settings.render_applied([]))
        writes = len(scene.hub.writes())
        settings.apply(scene.repo, scene.hub, scene.audit(), True)
        self.assertEqual(writes, len(scene.hub.writes()))

    def test_rulesets_are_made_once_each_and_never_twice(self):
        scene = self.broken()
        for _ in range(3):
            settings.apply(scene.repo, scene.hub, scene.audit(), True)
        self.assertEqual([("catalog-main", "branch"), ("catalog-protected-paths", "push"), ("catalog-push", "push"),
                          ("catalog-tags", "tag")], scene.hub.rulesets())
        self.assertEqual(4, len([call for call in scene.hub.writes() if call[1].endswith("/rulesets")]))
        self.assertEqual(0, len([call for call in scene.hub.writes() if "/rulesets/" in call[1]]))

    def test_a_ruleset_of_that_name_is_replaced_not_added(self):
        scene = Scene("org-private", live=True)
        rules = copy.deepcopy(scene.get("/rules/branches/main").body)
        for rule in rules:
            if rule["type"] == "pull_request":
                rule["parameters"]["require_code_owner_review"] = False
        scene.set("/rules/branches/main", rules)
        findings = scene.audit()
        found = {f.id: f for f in findings}["rules.pull_request"]
        self.assertEqual(("PUT", "repos/acme-labs/lab-internal/rulesets/2001"), requests_of(found)[0][:2])
        done = settings.apply(scene.repo, scene.hub, findings, True)
        self.assertEqual([("PUT", "repos/acme-labs/lab-internal/rulesets/2001")], [(i.method, i.path) for i in done])
        self.assertEqual(4, len(scene.hub.rulesets()))
        self.assertEqual([], [f.id for f in scene.audit() if f.status == FAIL])

    def test_the_ruleset_is_found_again_just_before_it_is_sent(self):
        scene = self.broken()
        findings = scene.audit()
        self.assertEqual({"POST"}, {req[0] for f in findings for req in requests_of(f) if req[1].endswith("/rulesets")})
        # Another maintainer made it between the audit and the apply: the apply replaces it and does not add a second
        scene.hub.store[7] = {"name": "catalog-tags", "target": "tag", "enforcement": "active", "rules": [{"type": "creation"}],
                              "id": 7, "source_type": "Repository", "source": scene.repo, "bypass_actors": []}
        scene.hub.sync()
        done = settings.apply(scene.repo, scene.hub, findings, True)
        tags = [i for i in done if i.path.endswith(("/rulesets", "/rulesets/7")) and i.body["name"] == "catalog-tags"]
        self.assertEqual([("PUT", "repos/acme-labs/lab-internal/rulesets/7")], [(i.method, i.path) for i in tags])
        self.assertEqual(1, len([r for r in scene.hub.rulesets() if r[0] == "catalog-tags"]))

    def test_two_rulesets_of_one_name_or_the_organizations_do_not_make_a_third(self):
        scene = Scene("org-private")
        listing = copy.deepcopy(scene.get("/rulesets").body)
        listing.append({**listing[0], "id": 2005})
        scene.set("/rulesets", listing)
        rules = scene.get("/rules/branches/main").body
        scene.set("/rules/branches/main", [r for r in rules if r["type"] != "deletion"])
        found = {f.id: f for f in scene.audit()}["rules.deletion"]
        self.assertEqual(("PUT", "repos/acme-labs/lab-internal/rulesets/2001"), requests_of(found)[0][:2])
        scene = Scene("org-private")
        listing = copy.deepcopy(scene.get("/rulesets").body)
        listing[0].update(source_type="Organization", id=9001)      # the organization's ruleset of that name is not this repository's
        scene.set("/rulesets", listing)
        scene.set("/rules/branches/main", [r for r in scene.get("/rules/branches/main").body if r["type"] != "deletion"])
        findings = scene.audit()
        self.assertEqual("POST", requests_of({f.id: f for f in findings}["rules.deletion"])[0][0])
        # And when the apply looks again, just before it sends: still not the organization's ruleset
        planned = settings.apply(scene.repo, scene.hub, findings, False)
        self.assertEqual([("POST", "repos/acme-labs/lab-internal/rulesets")], [(i.method, i.path) for i in planned])
        duplicated = Scene("org-private")
        listing = copy.deepcopy(duplicated.get("/rulesets").body)
        listing.insert(0, {**listing[0], "id": 2000, "source_type": "Organization"})
        listing.append({**listing[1], "id": 2005})
        duplicated.set("/rulesets", listing)
        duplicated.set("/rules/branches/main", [r for r in duplicated.get("/rules/branches/main").body if r["type"] != "deletion"])
        planned = settings.apply(duplicated.repo, duplicated.hub, duplicated.audit(), False)
        self.assertEqual([("PUT", "repos/acme-labs/lab-internal/rulesets/2001")], [(i.method, i.path) for i in planned])

    def test_the_rulesets_come_from_the_template_without_their_marker(self):
        scene = self.broken()
        done = settings.apply(scene.repo, scene.hub, scene.audit(), True)
        sent = {item.body["name"]: item.body for item in done if item.path.endswith("/rulesets")}
        self.assertEqual(4, len(sent))
        self.assertEqual({"main": "catalog-main", "tags": "catalog-tags", "push": "catalog-push", "paths": "catalog-protected-paths"},
                         {key: body["name"] for key, body in TEMPLATE_RULESETS.items()})
        for body in TEMPLATE_RULESETS.values():
            self.assertEqual(body, sent[body["name"]])
            self.assertNotIn("_managed", sent[body["name"]])
        self.assertEqual([], sent["catalog-main"]["bypass_actors"])

    def test_solo_adds_exactly_one_administrator_in_pull_request_mode(self):
        scene = self.broken()
        findings = scene.audit(solo=True)
        done = settings.apply(scene.repo, scene.hub, findings, True)
        sent = {item.body["name"]: item.body for item in done if item.path.endswith("/rulesets")}
        self.assertEqual([{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}], sent["catalog-main"]["bypass_actors"])
        self.assertEqual(TEMPLATE_RULESETS["tags"], sent["catalog-tags"])           # the other rulesets are as they are
        self.assertEqual(TEMPLATE_RULESETS["push"], sent["catalog-push"])
        found = {f.id: f for f in scene.audit(solo=True)}["rules.bypass"]
        self.assertEqual((WARN, None), (found.status, found.fix))             # the audit warns while a bypass exists
        self.assertEqual(WARN, {f.id: f for f in scene.audit()}["rules.bypass"].status)
        self.assertEqual([], settings.apply(scene.repo, scene.hub, scene.audit(solo=True), True))

    def test_the_checks_asked_for_are_in_the_ruleset(self):
        scene = self.broken()
        done = settings.apply(scene.repo, scene.hub, scene.audit(required_checks=["E2E result"]), True)
        body = [item.body for item in done if item.path.endswith("/rulesets") and item.body["name"] == "catalog-main"][0]
        checks = [r for r in body["rules"] if r["type"] == "required_status_checks"][0]["parameters"]["required_status_checks"]
        self.assertEqual([{"context": "E2E result", "integration_id": 15368}], checks)
        self.assertEqual([], settings.apply(scene.repo, scene.hub, scene.audit(required_checks=["E2E result"]), True))

    def test_nothing_is_ever_removed(self):
        scene = self.broken()
        settings.apply(scene.repo, scene.hub, scene.audit(), True, warnings=True)
        self.no_removal(scene.hub)
        for module in ("settings.py", "fleet.py"):
            self.assertNotRegex(Path(settings.__file__).with_name(module).read_text(encoding="utf-8"), r"\bDELETE\b", module)
        self.assertEqual((), tuple(m for m in settings.READS + settings.WRITES if m not in ("GET", "PATCH", "POST", "PUT")))
        self.assertEqual([], [method for method, _ in settings.ALLOWED if method not in ("PATCH", "POST", "PUT")])
        for method in ("DELETE", "HEAD", "get"):
            self.assertFalse(settings.allowed("acme/lab-hub", method, "repos/acme/lab-hub/rulesets/1"))

    def test_the_list_of_what_may_be_sent_names_no_secret_variable_person_or_removal(self):
        banned = ("secret", "variable", "collaborator", "invitation", "transfer", "key", "hook", "team", "/forks", "dispatch",
                  "visibility", "rename", "archive", "branches", "protection", "delete", "remove")
        self.assertEqual([], [(method, shape) for method, shape in settings.ALLOWED
                              if any(word in shape.lower() for word in banned) or method not in ("PATCH", "POST", "PUT")])
        self.assertEqual({"allow_forking", "security_and_analysis"} | set(settings.MERGE), set(settings.REPO_KEYS))

    def test_what_may_be_sent(self):
        repo, base = "acme/lab-hub", "repos/acme/lab-hub"
        yes = [("PATCH", base, {"allow_forking": False}), ("PATCH", base, {"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}),
               ("PUT", base + "/topics", {"names": ["a"]}), ("PUT", base + "/actions/permissions", {"enabled": True}),
               ("PUT", base + "/actions/workflows/nightly.yml/enable", None), ("PUT", base + "/actions/workflows/ci-2.yml/enable", None),
               ("PUT", base + "/vulnerability-alerts", None), ("PUT", base + "/environments/publish", {"deployment_branch_policy": {}}),
               ("PUT", base + "/environments/e2e-nightly", {}), ("POST", base + "/environments/publish/deployment-branch-policies", {"name": "v*"}),
               ("POST", base + "/rulesets", {"name": "x"}), ("PUT", base + "/rulesets/123", {"name": "x"})]
        for method, path, body in yes:
            self.assertTrue(settings.allowed(repo, method, path, body), (method, path))
        no = [("PATCH", base, {"private": False}), ("PATCH", base, {"visibility": "public"}), ("PATCH", base, {"name": "other"}),
              ("PATCH", base, {"archived": True}), ("PATCH", base, {"default_branch": "x"}),
              ("PATCH", base, {"security_and_analysis": {"advanced_security": {"status": "enabled"}}}), ("PATCH", base, ["x"]),
              ("PUT", base + "/actions/secrets/LAB_DEPLOY_KEY", {}), ("PUT", base + "/dependabot/secrets/X", {}),
              ("PUT", base + "/actions/variables/X", {}), ("PUT", base + "/environments/publish/secrets/X", {}),
              ("PUT", base + "/collaborators/someone", {}), ("PUT", base + "/invitations/1", {}), ("POST", base + "/transfer", {}),
              ("POST", base + "/forks", {}), ("POST", base + "/hooks", {}), ("POST", base + "/keys", {}), ("PUT", base + "/teams/x", {}),
              ("POST", base + "/dispatches", {}), ("POST", base + "/branches/main/rename", {"new_name": "x"}),
              ("PUT", base + "/environments/evil", {}), ("PUT", base + "/environments/publish/x", {}),
              ("PUT", base + "/rulesets/abc", {}), ("PUT", base + "/rulesets/1/x", {}), ("PUT", base + "/rulesets", {}),
              ("PUT", base + "/actions/workflows/x.sh/enable", None), ("PUT", base + "/actions/workflows/../x.yml/enable", None),
              ("PATCH", "repos/other/repo", {}), ("PATCH", base + "-2", {}), ("PUT", base + "/../other/topics", {}),
              ("PUT", base + "//topics", {}), ("PUT", "orgs/acme/topics", {}), ("PUT", base + "/topics", "x"),
              ("DELETE", base + "/rulesets/1", None)]
        for method, path, body in no:
            self.assertFalse(settings.allowed(repo, method, path, body), (method, path, body))

    def test_a_request_that_may_not_be_sent_is_skipped_and_never_sent(self):
        base = "repos/acme/lab-hub"
        scene = Scene("user-private-free")
        for req in (settings.request("DELETE", base + "/rulesets/1"), settings.request("PUT", base + "/actions/secrets/LAB_DEPLOY_KEY", {}),
                    settings.request("PATCH", base, {"private": False}), settings.request("PUT", base + "/collaborators/x", {}),
                    settings.request("PATCH", "repos/other/repo", {"allow_forking": False})):
            done = settings.apply(scene.repo, scene.hub, crafted(req), True)
            self.assertEqual(["skipped"], [item.outcome for item in done], req)
            self.assertEqual("this is not a request repo-settings makes", done[0].detail)
        self.assertEqual([], scene.hub.writes())

    def test_an_allow_list_is_never_enabled_without_its_patterns(self):
        base = "repos/acme/lab-hub"
        scene = Scene("user-private-free")
        enable = settings.request("PUT", base + "/actions/permissions", {"enabled": True, "allowed_actions": "selected"}, enables_list=True)
        for patterns in (None, []):
            plan = [enable] + ([settings.request("PUT", base + "/actions/permissions/selected-actions", {"patterns_allowed": patterns})]
                               if patterns is not None else [])
            done = settings.apply(scene.repo, scene.hub, crafted(*plan), True)
            self.assertEqual("an allow-list is not enabled without its patterns", done[0].detail)
            self.assertEqual(0, len([w for w in scene.hub.writes() if w[1].endswith("/actions/permissions")]))
        full = [enable, settings.request("PUT", base + "/actions/permissions/selected-actions", {"patterns_allowed": ["docker/*"]})]
        self.assertEqual(["done", "done"], [i.outcome for i in settings.apply(scene.repo, scene.hub, crafted(*full), True)])

    def test_when_a_request_is_refused_the_rest_of_its_fix_is_not_sent(self):
        scene = Scene("org-private", refuse={("PUT", "/actions/permissions"): Reply(422, {"message": "Validation Failed"})})
        scene.edit("/actions/permissions", allowed_actions="all")
        done = settings.apply(scene.repo, scene.hub, scene.audit(), True)
        self.assertEqual([("refused", "PUT", "repos/acme-labs/lab-internal/actions/permissions"),
                          ("skipped", "PUT", "repos/acme-labs/lab-internal/actions/permissions/selected-actions")],
                         [(i.outcome, i.method, i.path) for i in done])
        self.assertEqual("HTTP 422: Validation Failed", done[0].detail)
        self.assertEqual("an earlier request of the same fix was not made", done[1].detail)
        self.assertEqual(1, len(scene.hub.writes()))

    def test_a_request_nobody_answered_is_refused_and_never_reported_done(self):
        """A timeout, a flood and a gh that failed with no HTTP status are Reply(0): the request may not have been made."""
        def times_out(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 30)
        answers = (times_out,
                   lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "x" * (settings.REPLY_LIMIT + 1), ""),
                   lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "error connecting to api.github.com"),
                   lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "dial tcp: lookup api.github.com: no such host"))
        for run in answers:
            scene = Scene("org-private")
            scene.edit("/actions/permissions", allowed_actions="all")
            gh, sent = settings.Gh(run), []

            def runner(method, path, body=None, scene=scene, gh=gh, sent=sent):
                if method == "GET":
                    return scene.hub(method, path, body)
                sent.append((method, path))
                return gh(method, path, body)
            done = settings.apply(scene.repo, runner, scene.audit(), True)
            self.assertEqual([("refused", "PUT", "repos/acme-labs/lab-internal/actions/permissions"),
                              ("skipped", "PUT", "repos/acme-labs/lab-internal/actions/permissions/selected-actions")],
                             [(i.outcome, i.method, i.path) for i in done])
            self.assertTrue(done[0].detail.startswith("no answer: "), done[0].detail)
            self.assertEqual("an earlier request of the same fix was not made", done[1].detail)
            self.assertEqual(1, len(sent))                    # the allow-list was not sent after a request that may not have been made
            # The command says it and fails, and does not call it done
            result = Run(["acme-labs/lab-internal", "--apply", "--yes"], runner=runner)
            self.assertEqual(1, result.code)
            self.assertIn("refused  PUT repos/acme-labs/lab-internal/actions/permissions  no answer: ", result.text)
            self.assertNotIn("done     ", result.text)
        # What GitHub answers with a 2xx is done, whatever the 2xx; anything else is not
        for code, outcome in ((200, "done"), (201, "done"), (204, "done"), (299, "done"), (199, "refused"), (300, "refused"),
                              (302, "refused"), (404, "refused"), (500, "refused"), (-1, "refused")):
            scene = Scene("org-private", refuse={("PUT", "/topics"): Reply(code, None)})
            scene.edit(topics=[])
            done = [i for i in settings.apply(scene.repo, scene.hub, scene.audit(), True) if i.path.endswith("/topics")]
            self.assertEqual([outcome], [i.outcome for i in done], code)

    def test_a_paid_feature_is_never_sent_without_warnings(self):
        """Secret scanning on a private repository is an add-on that is paid for, also under --as public (the gate): it is
        planned and sent only with --warnings."""
        disabled = {"secret_scanning": {"status": "disabled"}, "secret_scanning_push_protection": {"status": "disabled"}}
        for options in ({}, {"as": "public"}, {"as": "internal"}):
            scene = Scene("org-private", live=True)
            scene.edit(security_and_analysis=copy.deepcopy(disabled))
            findings = scene.audit(**options)
            plain = settings.apply(scene.repo, scene.hub, findings, True)
            self.assertEqual([], [i for i in plain if "security_and_analysis" in (i.body or {})], options)
            self.assertEqual([], [w for w in scene.hub.writes() if "security_and_analysis" in (w[2] or {})], options)
            paid = settings.apply(scene.repo, scene.hub, findings, True, warnings=True)
            self.assertEqual(1, len([i for i in paid if "security_and_analysis" in (i.body or {})]), options)
            self.assertEqual({"secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"}},
                             scene.get().body["security_and_analysis"], options)
        scene = Scene("org-private")
        scene.edit(security_and_analysis=copy.deepcopy(disabled))
        run = Run(["acme-labs/lab-internal", "--as", "public", "--apply"], scene)
        self.assertNotIn("secret_scanning", run.text.split("Summary:")[1])             # no request of the dry run carries it
        self.assertIn("secret_scanning", Run(["acme-labs/lab-internal", "--as", "public", "--apply", "--warnings"], scene).text.split("Summary:")[1])

    def test_a_request_the_plan_does_not_allow_is_skipped_and_the_others_go_on(self):
        scene = self.broken()
        scene.hub.refuse = {("POST", "/rulesets"): Reply(403, {"message": PLAN_WORDS})}
        done = settings.apply(scene.repo, scene.hub, scene.audit(), True)
        rulesets = [i for i in done if i.path.endswith("/rulesets")]
        self.assertEqual({"skipped"}, {i.outcome for i in rulesets})
        self.assertIn(PLAN_WORDS, rulesets[0].detail)
        self.assertTrue([i for i in done if i.outcome == "done"])
        self.assertEqual([], [i for i in done if i.outcome == "refused"])

    def test_the_rulesets_that_cannot_be_listed_are_not_sent(self):
        scene = self.broken()
        findings = scene.audit()
        scene.table[f"GET {scene.path('/rulesets')}"] = Reply(500, {"message": "Server Error"})
        done = settings.apply(scene.repo, scene.hub, findings, True)
        rulesets = [i for i in done if i.path.endswith("/rulesets")]
        self.assertEqual({"skipped"}, {i.outcome for i in rulesets})
        self.assertIn("cannot be listed", rulesets[0].detail)
        self.assertEqual([], [w for w in scene.hub.writes() if w[1].endswith("/rulesets")])

    def test_only_a_fix_that_is_a_request_is_applied(self):
        scene = Scene("user-private-free")
        req = settings.request("PUT", "repos/acme/lab-hub/topics", {"names": ["x"]})
        for status in (OK, NA, MANUAL):
            self.assertEqual([], settings.apply(scene.repo, scene.hub, crafted(req, status=status), True), status)
        self.assertEqual([], settings.apply(scene.repo, scene.hub, [settings.Finding("repo.merge", FAIL, "t", "", "words")], True))
        self.assertEqual([], settings.apply(scene.repo, scene.hub, crafted(req, status=WARN), True))
        self.assertEqual(1, len(settings.apply(scene.repo, scene.hub, crafted(req, status=WARN), True, warnings=True)))
        self.assertEqual([], scene.hub.writes()[1:])

    def test_the_requests_are_printed_as_commands_a_person_can_type(self):
        planned = [settings.Applied("PATCH", "repos/acme/lab-hub", {"allow_forking": False, "name": "it's"}, "planned", ""),
                   settings.Applied("PUT", "repos/acme/lab-hub/vulnerability-alerts", None, "planned", "")]
        text = settings.render_applied(planned)
        lines = text.splitlines()
        self.assertEqual("gh api --method PATCH repos/acme/lab-hub --input - <<< '{\"allow_forking\": false, \"name\": \"it'\"'\"'s\"}'", lines[0])
        self.assertEqual("gh api --method PUT repos/acme/lab-hub/vulnerability-alerts", lines[1])
        self.assertEqual("Nothing was changed. To make these requests: add --yes", lines[2])
        done = [settings.Applied("PUT", "repos/acme/lab-hub/topics", {}, "done", ""),
                settings.Applied("PUT", "repos/acme/lab-hub/immutable-releases", None, "refused", "HTTP 403: nope")]
        self.assertEqual(["done     PUT repos/acme/lab-hub/topics", "refused  PUT repos/acme/lab-hub/immutable-releases  HTTP 403: nope"],
                         settings.render_applied(done).splitlines())


class Run:
    """What `main` printed and returned."""

    def __init__(self, argv, scene=None, runner=None, **seams):
        assert scene is not None or runner is not None, "a test never runs the real gh"
        self.out, self.err = io.StringIO(), io.StringIO()
        self.code = settings.main(argv, runner=runner or (scene.hub if scene else None), stdout=self.out, stderr=self.err, **seams)
        self.text, self.error = self.out.getvalue(), self.err.getvalue()


def git_says(url):
    return lambda args, cwd=None: gitx.Result(0, (url + "\n").encode(), "")


class Command(Base):
    """`catalog.py repo-settings`: the arguments, what it prints, and its exit codes."""

    def test_the_audit_of_a_repository(self):
        scene = Scene("user-private-free")
        run = Run(["acme/lab-hub"], scene)
        self.assertEqual((1, ""), (run.code, run.error))
        self.assertTrue(run.text.startswith("Catalog standard 1 for acme/lab-hub (private, user-owned)\n"))
        self.assertTrue(run.text.rstrip().endswith("Summary: 5 fail, 3 warn, 6 ok, 18 n.a., 2 manual"))
        self.assertEqual([], scene.hub.writes())
        self.assertEqual(0, Run(["acme-labs/lab-hub"], Scene("public-standard")).code)
        self.assertEqual(0, Run(["acme-labs/lab-hub", "--strict"], Scene("public-standard")).code)
        self.assertEqual(1, Run(["acme/lab-hub", "--strict"], Scene("user-private-free")).code)

    def test_json_and_github_output(self):
        run = Run(["acme/lab-hub", "--format", "json"], Scene("user-private-free"))
        self.assertEqual(1, run.code)
        self.assertEqual("acme/lab-hub", json.loads(run.text)["repository"])
        run = Run(["acme/lab-hub", "--format", "github"], Scene("user-private-free"))
        lines = run.text.splitlines()
        self.assertEqual(8, len(lines))                      # a line for each failure and each warning, and for nothing else
        self.assertTrue(lines[0].startswith("::error title=repo.merge::Squash merges only"))
        self.assertTrue(all(re.match(r"::(error|warning) title=[a-z_.]+::", line) for line in lines))
        self.assertEqual(0, len(Run(["acme-labs/lab-hub", "--format", "github"], Scene("public-standard")).text.strip()))

    def test_apply_is_a_dry_run_until_yes(self):
        scene = Scene("user-private-free")
        run = Run(["acme/lab-hub", "--apply"], scene)
        self.assertEqual(1, run.code)
        self.assertEqual([], scene.hub.writes())
        self.assertIn("gh api --method PATCH repos/acme/lab-hub --input - <<< '{", run.text)
        self.assertIn("gh api --method PUT repos/acme/lab-hub/vulnerability-alerts\n", run.text)
        self.assertTrue(run.text.rstrip().endswith("Nothing was changed. To make these requests: add --yes"))

    def test_apply_with_yes_and_a_second_apply(self):
        scene = Apply.broken(Apply())
        run = Run(["acme-labs/lab-internal", "--apply", "--yes", "--warnings"], scene)
        self.assertEqual("", run.error)
        self.assertEqual(0, run.code)
        self.assertIn("done     PATCH repos/acme-labs/lab-internal", run.text)
        self.assertNotIn("refused", run.text)
        again = Run(["acme-labs/lab-internal", "--apply", "--yes", "--warnings"], scene)
        self.assertEqual(0, again.code)
        self.assertTrue(again.text.rstrip().endswith("Nothing to change"))
        self.assertEqual(0, Run(["acme-labs/lab-internal", "--strict"], scene).code)

    def test_the_exit_code_after_yes_is_the_one_of_what_is_there_now(self):
        scene = Scene("user-private-free", live=True)
        run = Run(["acme/lab-hub", "--apply", "--yes"], scene)
        self.assertGreater(len(scene.hub.writes()), 3)
        self.assertEqual(1, run.code)               # the CODEOWNERS file is missing, and no request can make it
        again = Run(["acme/lab-hub", "--format", "json"], scene)
        failing = [f["id"] for f in json.loads(again.text)["findings"] if f["status"] == "fail"]
        self.assertEqual(["files.codeowners"], failing)
        scene.set("/codeowners/errors", {"errors": []})
        self.assertEqual(0, Run(["acme/lab-hub", "--apply", "--yes", "--warnings"], scene).code)
        none = Scene("user-private-free")
        none.set("/codeowners/errors", {"errors": []})
        none.table = {key: value for key, value in none.table.items()}
        run = Run(["acme/lab-hub", "--apply", "--yes"], none)
        self.assertEqual(1, run.code)

    def test_the_help(self):
        for argv in (["--help"], ["acme/lab-hub", "-h"]):
            run = Run(argv, Scene("user-private-free"))
            self.assertEqual((0, ""), (run.code, run.error))
            self.assertTrue(run.text.startswith("catalog.py repo-settings [OWNER/REPO or FOLDER]"))
            self.assertIn("Exit 0 when nothing failed", run.text)

    def test_a_request_that_github_refuses_is_a_failure_of_the_command(self):
        scene = Scene("user-private-free", refuse={("PUT", "/topics"): Reply(403, {"message": "Must have admin rights to Repository."})})
        run = Run(["acme/lab-hub", "--apply", "--yes"], scene)
        self.assertEqual(1, run.code)
        self.assertIn("refused  PUT repos/acme/lab-hub/topics  HTTP 403: Must have admin rights to Repository.", run.text)

    def test_json_with_apply_is_one_document(self):
        run = Run(["acme/lab-hub", "--apply", "--format", "json"], Scene("user-private-free"))
        document = json.loads(run.text)
        self.assertEqual(["about", "findings", "not_applicable", "repository", "requests", "standard", "summary"], list(document))
        self.assertEqual({"planned"}, {r["outcome"] for r in document["requests"]})

    def test_the_options_reach_the_audit(self):
        scene = Scene("org-private")
        scene.set("/actions/organization-secrets", {"secrets": [{"name": "MINE"}]})
        self.assertEqual(1, Run(["acme-labs/lab-internal"], scene).code)
        self.assertEqual(0, Run(["acme-labs/lab-internal", "--allow-org-secret", "MINE"], scene).code)
        run = Run(["acme-labs/lab-internal", "--as", "public"], Scene("org-private"))
        self.assertIn("(private, organization-owned, audited as public)", run.text)
        run = Run(["acme-labs/lab-internal", "--required-check", "E2E result", "--required-check", "build"], Scene("org-private"))
        self.assertIn("E2E result is not required", run.text)
        self.assertEqual(1, run.code)
        run = Run(["acme-labs/lab-internal", "--effective-only"], Scene("org-private"))
        self.assertNotIn("repo.merge", run.text)
        self.assertEqual(0, run.code)

    def test_a_command_line_that_makes_no_sense_is_one_line_and_exit_2(self):
        scene = Scene("org-private")
        for argv in (["acme-labs/lab-internal", "--yes"], ["acme-labs/lab-internal", "--warnings"], ["--bogus"],
                     ["acme-labs/lab-internal", "--as", "secret"], ["acme-labs/lab-internal", "--format", "xml"],
                     ["acme-labs/lab-internal", "--required-check", ""], ["acme-labs/lab-internal", "--fleet", "acme-labs"],
                     ["--fleet", "acme-labs", "--apply"], ["a/b", "c/d"], ["--as", "private", "acme-labs/lab-hub"], ["not a repository"]):
            run = Run(argv, Scene("public-standard") if "lab-hub" in " ".join(argv) else scene)
            self.assertEqual(2, run.code, argv)
            self.assertEqual("", run.text, argv)
            self.assertEqual(1, len(run.error.splitlines()), (argv, run.error))
        self.assertEqual([], scene.hub.writes())

    def test_a_name_the_system_cannot_look_up_is_one_line_and_exit_2(self):
        """A path with a component over 255 characters raises ENAMETOOLONG in Path.is_dir on Python 3.9 to 3.12; a name with a
        NUL raises ValueError. Neither is a folder, and neither is OWNER/REPO."""
        scene = Scene("org-private")
        for given in ("x" * 300, "x" * 5000, "a/" + "b" * 300, "./" + "x" * 300, "a\0b", "x" * 255 + "/" + "y" * 255, "-" + "x" * 300):
            run = Run([given], scene)
            self.assertEqual((2, ""), (run.code, run.text), given[:20])
            self.assertEqual(1, len(run.error.splitlines()), (given[:20], run.error))
            with self.assertRaises(settings.Unusable):
                settings.resolve_target(given)
        self.assertEqual([], scene.hub.calls)

    def test_a_folder_that_was_removed_under_the_command_is_one_line_and_exit_2(self):
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        gone = self.folder() / "gone"
        gone.mkdir()
        os.chdir(gone)
        gone.rmdir()
        run = Run([], Scene("public-standard"))
        self.assertEqual((2, ""), (run.code, run.text))
        self.assertEqual(1, len(run.error.splitlines()))
        self.assertEqual(0, Run(["acme-labs/lab-hub"], Scene("public-standard")).code)       # a name needs no folder

    def test_a_missing_gh_a_missing_sign_in_and_a_repository_that_is_not_there_are_one_line_and_exit_2(self):
        def missing(*a, **k):
            raise FileNotFoundError
        run = Run(["acme/lab-hub"], runner=settings.Gh(missing))
        self.assertEqual((2, ""), (run.code, run.text))
        self.assertEqual("The GitHub command-line tool gh is not installed. Install it from https://cli.github.com and run gh auth login.\n",
                         run.error)
        run = Run(["acme/lab-hub"], runner=settings.Gh(lambda argv, **k: subprocess.CompletedProcess(
            argv, 4, "", "To get started with GitHub CLI, please run:  gh auth login")))
        self.assertEqual((2, "Not signed in to GitHub. Run gh auth login, then run this again.\n"), (run.code, run.error))
        scene = Scene("org-private")
        run = Run(["acme-labs/nothing"], scene)
        self.assertEqual((2, "acme-labs/nothing was not found, or you cannot read it (HTTP 404: Not Found).\n"), (run.code, run.error))

    def test_a_folder_is_read_for_its_origin(self):
        folder = self.folder()
        for origin, repo in (("https://github.com/acme-labs/lab-hub.git", "acme-labs/lab-hub"), ("git@github.com:acme-labs/lab-hub.git", "acme-labs/lab-hub"),
                             ("ssh://git@github.com/acme-labs/lab-hub", "acme-labs/lab-hub"), ("https://GitHub.com/acme-labs/lab-hub/", "acme-labs/lab-hub")):
            self.assertEqual(repo, settings.resolve_target(str(folder), git_says(origin)), origin)
            run = Run([str(folder)], Scene("public-standard"), git=git_says(origin))
            self.assertEqual(0, run.code, origin)
            self.assertIn("for acme-labs/lab-hub", run.text)
        run = Run([], Scene("public-standard"), git=git_says("https://github.com/acme-labs/lab-hub.git"), cwd=str(folder))
        self.assertEqual(0, run.code)                         # this folder, when none is named
        # A folder wins over a name that looks like OWNER/REPO
        (folder / "acme-labs" / "lab-hub").mkdir(parents=True)
        run = Run(["acme-labs/lab-hub"], Scene("public-standard"), git=git_says("https://github.com/acme-labs/other.git"), cwd=str(folder))
        self.assertEqual(2, run.code)

    def test_a_host_that_is_not_github_is_one_line_and_exit_2(self):
        folder = self.folder()
        for origin in ("https://git.example.invalid/acme/lab.git", "git@gitlab.example.invalid:acme/lab.git", "https://www.github.com/acme/lab.git",
                       "https://github.com.example.invalid/acme/lab.git"):
            run = Run([str(folder)], Scene("public-standard"), git=git_says(origin))
            self.assertEqual((2, ""), (run.code, run.text), origin)
            self.assertRegex(run.error, r"^[a-z0-9.@-]+ is not github\.com\. repo-settings works for GitHub only\.\n$")
        run = Run(["https://gitlab.example.invalid/acme/lab"], Scene("public-standard"))
        self.assertEqual(2, run.code)
        self.assertEqual(1, len(run.error.splitlines()))

    def test_a_credential_in_the_origin_is_never_shown(self):
        folder = self.folder()
        run = Run([str(folder)], Scene("public-standard"), git=git_says("https://user:s3cr3tvalue@github.com/acme/lab.git"))
        self.assertEqual(2, run.code)
        self.assertEqual("The origin remote holds a credential. Remove it from the address, then run this again.\n", run.error)
        self.assertNotIn("s3cr3t", run.error + run.text)
        for failing in (lambda args, cwd=None: gitx.Result(2, b"", "fatal: No such remote 'origin'"), git_says("")):
            run = Run([str(folder)], Scene("public-standard"), git=failing)
            self.assertEqual((2, 1), (run.code, len(run.error.splitlines())))
            self.assertIn("has no origin remote", run.error)

    def test_the_origin_of_a_real_repository(self):
        gitfixture.require()
        gitfixture.isolate(self)
        folder = self.folder()
        gitfixture.git("init", "--quiet", str(folder))
        gitfixture.git("-C", str(folder), "remote", "add", "origin", "git@github.com:acme-labs/lab-hub.git")
        self.assertEqual("acme-labs/lab-hub", settings.resolve_target(str(folder)))
        run = Run([str(folder)], Scene("public-standard"))
        self.assertEqual((0, ""), (run.code, run.error))
        empty = self.folder()
        gitfixture.git("init", "--quiet", str(empty))
        with self.assertRaises(settings.Unusable) as raised:
            settings.resolve_target(str(empty))
        self.assertIn("has no origin remote", str(raised.exception))

    def test_the_rulesets_of_the_repository_are_never_the_ones_that_are_sent(self):
        scene = Apply.broken(Apply())
        folder = self.folder()
        evil = folder / ".github" / "rulesets"
        evil.mkdir(parents=True)
        for name in ("default-branch.json", "tags.json", "push.json", "protected-paths.json"):
            (evil / name).write_text(json.dumps({"name": "evil", "target": "branch", "enforcement": "disabled",
                                                 "bypass_actors": [{"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}],
                                                 "rules": []}), encoding="utf-8")
        opened = []
        real = io.open

        def spy(file, *a, **k):
            opened.append(os.path.realpath(str(file)) if isinstance(file, (str, os.PathLike)) else None)
            return real(file, *a, **k)
        with mock.patch("builtins.open", spy), mock.patch("io.open", spy):
            run = Run([str(folder), "--apply", "--yes"], scene, git=git_says("https://github.com/acme-labs/lab-internal.git"))
        self.assertEqual(0, run.code)
        sent = [call[2] for call in scene.hub.writes() if call[1].endswith("/rulesets")]
        self.assertEqual(sorted(body["name"] for body in TEMPLATE_RULESETS.values()), sorted(body["name"] for body in sent))
        self.assertNotIn("evil", json.dumps(sent))
        self.assertFalse([path for path in opened if path and path.startswith(os.path.realpath(str(folder)))])
        self.assertTrue([path for path in opened if path and path.startswith(os.path.realpath(str(TEMPLATE)))])


class Isolation(Base):
    """What the module reads and writes: the repository's replies and the Lab's template, nothing else."""

    def watched(self, scene, action):
        opened, touched = [], []
        real = io.open

        def spy(file, mode="r", *a, **k):
            opened.append((os.path.realpath(str(file)) if isinstance(file, (str, os.PathLike)) else None, mode))
            return real(file, mode, *a, **k)

        class Watched(dict):
            def __getitem__(self, key):
                touched.append(key)
                return super().__getitem__(key)

            def get(self, key, default=None):
                touched.append(key)
                return super().get(key, default)

            def __contains__(self, key):
                touched.append(key)
                return super().__contains__(key)

            def __iter__(self):
                touched.append("*")
                return super().__iter__()

            def copy(self):
                touched.append("*")
                return dict(self)

        with mock.patch("builtins.open", spy), mock.patch("io.open", spy), mock.patch.object(os, "environ", Watched(os.environ)):
            action()
        return opened, touched

    def test_the_audit_and_the_apply_read_the_template_and_no_environment_variable(self):
        scene = Apply.broken(Apply())

        def action():
            findings = scene.audit(solo=True)
            settings.apply(scene.repo, scene.hub, findings, True, warnings=True)
            settings.render(scene.audit(), "text")
        opened, touched = self.watched(scene, action)
        root = os.path.realpath(str(TEMPLATE))
        self.assertTrue(opened)
        for path, mode in opened:
            self.assertEqual(root, os.path.dirname(path or ""), path)
            self.assertEqual("rb", mode)                                  # read, and nothing written
        self.assertEqual([], touched)

    def test_the_source_reads_no_environment_variable_and_no_file_but_the_template(self):
        for module in ("settings.py", "fleet.py"):
            text = Path(settings.__file__).with_name(module).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"os\.environ|getenv|environb|putenv|os\.system|shell\s*=\s*True|os\.popen", module)
        # The two reads of the template (the rulesets and their level), and for the fleet the standard.json of its own clone
        code = lambda name: re.sub(r'"""[\s\S]*?"""', "", Path(settings.__file__).with_name(name).read_text(encoding="utf-8"))  # noqa: E731
        self.assertEqual(2, len(re.findall(r"\bopen\(", code("settings.py"))))
        self.assertEqual(1, len(re.findall(r"\bopen\(", code("fleet.py"))))

    def test_the_module_imports_nothing_but_the_standard_library(self):
        for module in ("settings.py", "fleet.py"):
            text = Path(settings.__file__).with_name(module).read_text(encoding="utf-8")
            names = set(re.findall(r"^(?:import|from) ([a-z_0-9]+)", text, re.M))
            self.assertEqual(set(), names - {"argparse", "base64", "binascii", "copy", "importlib", "json", "os", "re", "shlex", "shutil", "subprocess",
                                             "sys", "tempfile", "time", "pathlib", "typing", "urllib"}, module)
