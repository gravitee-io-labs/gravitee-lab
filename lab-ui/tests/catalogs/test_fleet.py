"""catalogs/fleet.py: the loops over the topic. GitHub is a fake `gh` that lists repositories, the open pull requests and takes
the pull request the upgrade opens; the repositories themselves are bare git repositories on this machine (tests/gitfixture.py),
cloned, committed to and pushed to for real with the git that `gitx` runs. The Lab's `standard` module is written by another
package: a stub with its two functions stands in for it. The Lab checkout that runs the command (which says
whether the pin is on its `origin/main`, and gives the log between two pins) is a table of answers, and in two tests a real
repository made on this machine. A test that needs git and finds none prints a skip line."""

import copy
import io
import json
import os
import re
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import fleet, gitx, settings
from lab_ui.catalogs.settings import FAIL, Reply

from .. import Case, gitfixture
from .test_settings import TEMPLATE, load, normalize

OLD, NEW = "a" * 40, "b" * 40
TOPIC = "gravitee-lab-catalog"
TODAY = "20261017"
BRANCH = f"standard/2-{TODAY}"


def listed(name, topics=(TOPIC,), archived=False, branch="main"):
    return {"full_name": name, "default_branch": branch, "topics": list(topics), "archived": archived, "private": True}


class Github:
    """A `gh` for one owner: the repositories it lists, the pull requests open on each, and what was posted."""

    def __init__(self, owner="acme", kind="User", me="acme", repos=(), pulls=None, refuse_pulls=None):
        self.owner, self.kind, self.me, self.repos, self.pulls = owner, kind, me, list(repos), pulls or {}
        self.refuse_pulls = refuse_pulls
        self.calls, self.posted = [], []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        path = normalize(path)
        found = re.fullmatch(r"repos/([^/]+/[^/]+)/pulls", path)
        if method == "POST" and found and self.refuse_pulls:
            return self.refuse_pulls
        if method == "POST" and found:
            self.posted.append((found.group(1), copy.deepcopy(body)))
            number = 40 + len(self.posted)
            return Reply(200, {"number": number, "html_url": f"https://github.com/{found.group(1)}/pull/{number}"})
        if path == f"users/{self.owner}":
            return Reply(200, {"login": self.owner, "type": self.kind})
        if path == "user":
            return Reply(200, {"login": self.me})
        listing = {"Organization": f"orgs/{self.owner}/repos?type=all",
                   "User": "user/repos?affiliation=owner" if self.me == self.owner else f"users/{self.owner}/repos?type=owner"}[self.kind]
        if path == listing:
            return Reply(200, copy.deepcopy(self.repos))
        found = re.fullmatch(r"repos/([^/]+/[^/]+)/pulls\?state=open", path)
        if found:
            return Reply(200, copy.deepcopy(self.pulls.get(found.group(1), [])))
        return Reply(404, {"message": "Not Found"})


class Standard:
    """The Lab's `standard` module, as the fleet calls it: a plan, then the rewrite of the managed files."""

    LEVEL = 2

    def __init__(self, owned=(), touch=None, rewrite=True, ref=NEW):
        self.owned, self.touch, self.rewrite, self.ref, self.calls, self.links = list(owned), touch, rewrite, ref, [], []

    def upgrade_plan(self, tree, options):
        self.calls.append(("plan", tree, copy.deepcopy(options)))
        return {"owned": list(self.owned), "files": []}

    def upgrade_apply(self, root, plan):
        root = Path(root)
        self.calls.append(("apply", root))
        self.links.append({str(path.relative_to(root)): path.is_symlink() for path in root.rglob("*") if ".git" not in path.parts})
        if self.touch:
            self.touch(root)
        if not self.rewrite:
            return []
        data = json.loads((root / ".github" / "standard.json").read_text(encoding="utf-8"))
        data["standard"], data["lab"]["ref"] = 2, self.ref
        (root / ".github" / "standard.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        ci = root / ".github" / "workflows" / "ci.yml"
        ci.write_text(ci.read_text(encoding="utf-8") + "# level 2\n", encoding="utf-8")
        return [".github/standard.json", ".github/workflows/ci.yml"]


def files(ref=OLD):
    return {
        "catalog.yml": "x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: Acme Hub\n  version: 1.0.0\n  lab: 1\n",
        "README.md": "A catalog.\n",
        ".github/standard.json": json.dumps({"form": "inline", "lab": {"ref": ref, "repository": "acme-labs/lab"},
                                              "platforms": ["linux/amd64"], "standard": 1}, indent=2, sort_keys=True) + "\n",
        ".github/workflows/ci.yml": "name: ci\non: pull_request\n",
        ".github/dependabot.yml": "version: 2\n",
        ".github/link": gitfixture.Symlink("workflows/ci.yml"),
        "addons/echo/compose.yml": "x-gravitee: {kind: addon, name: Echo}\nservices: {}\n",
    }


class Fleet(Case):
    def setUp(self):
        self.patch(settings, "RULESETS", TEMPLATE)


class Audit(Fleet):
    """audit_fleet: every catalog of an owner, as one table."""

    def hub(self, repos, edit=None):
        """A `gh` for the owner acme-labs (an organization) over the recorded replies of two repositories; `edit` changes the
        table of replies."""
        github = Github(owner="acme-labs", kind="Organization", repos=repos)
        table = {}
        for name in ("public-standard", "org-private"):
            table.update(load(name)[1])
        table["GET repos/acme-labs/lab"] = Reply(200, {"private": True})
        if edit:
            edit(table)
        calls = []

        def runner(method, path, body=None):
            calls.append((method, path))
            if method == "GET" and f"GET {normalize(path)}" in table:
                return table[f"GET {normalize(path)}"]
            return github(method, path, body)
        runner.calls = calls
        return runner

    def test_the_catalogs_of_an_owner_are_the_repositories_with_the_topic(self):
        repos = [listed("acme/zeta"), listed("acme/alpha"), listed("acme/plain", topics=()), listed("acme/old", archived=True),
                 listed("other/stranger"), {"full_name": "acme/odd", "topics": "x"}, "text", None, listed("acme/bad name")]
        for kind, me, path in (("User", "acme", "user/repos?affiliation=owner"), ("User", "someone", "users/acme/repos?type=owner"),
                               ("Organization", "someone", "orgs/acme/repos?type=all")):
            github = Github(owner="acme", kind=kind, me=me, repos=repos)
            self.assertEqual([{"repo": "acme/alpha", "branch": "main", "archived": False}, {"repo": "acme/old", "branch": "main", "archived": True},
                              {"repo": "acme/zeta", "branch": "main", "archived": False}], fleet.catalogs_of("acme", github), kind)
            self.assertIn(path, [normalize(call[1]) for call in github.calls])
            self.assertEqual({"GET"}, {call[0] for call in github.calls})
        for bad in ("", "a/b", "-x", "a b", None):
            with self.assertRaises(settings.Usage):
                fleet.catalogs_of(bad, Github())
        with self.assertRaises(settings.Unusable) as raised:
            fleet.catalogs_of("nobody", Github(owner="acme"))
        self.assertIn("nobody is not a user or an organization on github.com", str(raised.exception))

    def test_audit_fleet_audits_each_and_prints_one_table(self):
        runner = self.hub([listed("acme-labs/lab-internal"), listed("acme-labs/lab-hub"), listed("acme-labs/other", topics=("x",)),
                           listed("acme-labs/lab-gone", archived=True), listed("acme-labs/lab-hidden")])
        rows = fleet.audit_fleet("acme-labs", runner, {})
        self.assertEqual(["acme-labs/lab-gone", "acme-labs/lab-hidden", "acme-labs/lab-hub", "acme-labs/lab-internal"], [row.repo for row in rows])
        self.assertEqual(["archived, not audited", "acme-labs/lab-hidden was not found, or you cannot read it (HTTP 404: Not Found)."],
                         [row.error for row in rows[:2]])
        self.assertEqual(["", ""], [row.error for row in rows[2:]])
        self.assertEqual("acme-labs", rows.owner)
        text = fleet.render_audit(rows)
        lines = text.splitlines()
        self.assertEqual("Catalog standard for 4 repositories of acme-labs", lines[0])
        self.assertEqual(1, len([line for line in lines if re.match(r"^  id +lab-gone +lab-hidden +lab-hub +lab-internal$", line)]))      # one table
        self.assertEqual([], [line for line in lines if line != line.rstrip()])
        row = [line for line in lines if line.startswith("  rules.push ")][0]
        self.assertEqual(["rules.push", "-", "-", "n.a.", "ok"], row.split())
        self.assertEqual(["repo.merge", "-", "-", "ok", "ok"], [line for line in lines if line.startswith("  repo.merge ")][0].split())
        self.assertIn("acme-labs/lab-hub: 27 ok, 2 n.a., 4 manual", lines)
        self.assertIn("acme-labs/lab-internal: 29 ok, 1 n.a., 4 manual", lines)
        self.assertEqual([], [call for call in runner.calls if call[0] != "GET"])
        self.assertEqual(text, fleet.render_audit(fleet.audit_fleet("acme-labs", self.hub([listed("acme-labs/lab-internal"), listed("acme-labs/lab-hub"),
                                                                                           listed("acme-labs/lab-gone", archived=True),
                                                                                           listed("acme-labs/lab-hidden")]), {})))
        self.assertEqual(1, fleet.exit_code(rows))                  # a repository that cannot be read
        self.assertEqual(0, fleet.exit_code([row for row in rows if row.repo.endswith(("lab-hub", "lab-internal", "lab-gone"))]))

    def test_the_exit_code_is_the_one_of_each_repository(self):
        runner = self.hub([listed("acme-labs/lab-hub"), listed("acme-labs/lab-internal")])
        rows = fleet.audit_fleet("acme-labs", runner, {})
        self.assertEqual((0, 0), (fleet.exit_code(rows), fleet.exit_code(rows, True)))
        runner = self.hub([listed("acme-labs/lab-hub"), listed("acme-labs/lab-internal")])
        weak = fleet.audit_fleet("acme-labs", runner, {"as": "public"})
        self.assertEqual(1, fleet.exit_code(weak))                  # the private one still forbids forking
        self.assertEqual(FAIL, {f.id: f for f in weak[1].findings}["repo.forking"].status)

    def test_the_table_in_json_and_as_annotations(self):
        runner = self.hub([listed("acme-labs/lab-internal"), listed("acme-labs/lab-hub"), listed("acme-labs/lab-hidden")])
        rows = fleet.audit_fleet("acme-labs", runner, {"as": "public"})
        document = json.loads(fleet.render_audit(rows, "json"))
        self.assertEqual(["owner", "repositories"], list(document))
        self.assertEqual(["acme-labs/lab-hidden", "acme-labs/lab-hub", "acme-labs/lab-internal"], [r["repository"] for r in document["repositories"]])
        self.assertEqual(["error", "findings", "not_applicable", "repository", "summary"], list(document["repositories"][0]))
        lines = fleet.render_audit(rows, "github").splitlines()
        self.assertTrue(lines[0].startswith("::error title=gravitee-lab-catalog::acme-labs/lab-hidden: acme-labs/lab-hidden was not found"))
        self.assertTrue(any(line.startswith("::error title=repo.forking::acme-labs/lab-internal: A public repository can be forked") for line in lines))
        self.assertTrue(all(re.match(r"^::(error|warning) title=[a-z_.-]+::", line) for line in lines))
        self.assertEqual("No repository of acme has the topic gravitee-lab-catalog", fleet.render_audit(fleet.Fleet(owner="acme")))

    def test_the_command_line_audits_the_fleet(self):
        runner = self.hub([listed("acme-labs/lab-hub"), listed("acme-labs/lab-internal")])
        out, err = io.StringIO(), io.StringIO()
        code = settings.main(["--fleet", "acme-labs", "--strict"], runner=runner, stdout=out, stderr=err)
        self.assertEqual((0, ""), (code, err.getvalue()))
        self.assertTrue(out.getvalue().startswith("Catalog standard for 2 repositories of acme-labs\n"))
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(1, settings.main(["--fleet", "acme-labs", "--as", "public", "--format", "github"], runner=runner, stdout=out, stderr=err))
        self.assertIn("title=repo.forking", out.getvalue())
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(2, settings.main(["--fleet", "a b"], runner=runner, stdout=out, stderr=err))
        self.assertEqual(["OWNER is a user or an organization of github.com."], err.getvalue().splitlines())

    def test_a_standard_json_nested_too_deep_does_not_end_the_audit_of_the_fleet(self):
        """4 KB of brackets in a `.github/standard.json` a contributor merged: Python 3.9 stops reading it with a
        RecursionError, which used to leave every repository after it unaudited."""
        import base64
        raw = b"[" * 2040 + b"]" * 2040

        def nest(table):
            table["GET repos/acme-labs/lab-hub/contents/.github/standard.json?ref=main"] = Reply(
                200, {"encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode()})
        runner = self.hub([listed("acme-labs/lab-hub"), listed("acme-labs/lab-internal")], nest)
        rows = fleet.audit_fleet("acme-labs", runner, {})
        self.assertEqual([("acme-labs/lab-hub", ""), ("acme-labs/lab-internal", "")], [(row.repo, row.error) for row in rows])
        self.assertTrue(all(row.findings for row in rows))
        self.assertEqual("manual", {f.id: f for f in rows[0].findings}["actions.nightly"].status)
        self.assertTrue(fleet.render_audit(rows))

    def test_a_missing_sign_in_ends_the_audit_of_the_fleet(self):
        def signed_out(method, path, body=None):
            raise settings.Unusable("Not signed in to GitHub. Run gh auth login, then run this again.")
        err = io.StringIO()
        self.assertEqual(2, settings.main(["--fleet", "acme"], runner=signed_out, stdout=io.StringIO(), stderr=err))
        self.assertEqual("Not signed in to GitHub. Run gh auth login, then run this again.\n", err.getvalue())


class Upgrade(Fleet):
    """upgrade_fleet: one pull request for each repository, from a clone that is removed."""

    def setUp(self):
        super().setUp()
        gitfixture.require()
        gitfixture.isolate(self)
        self.root = self.folder()
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.remotes, self.gitcalls = {}, []
        # The Lab checkout that runs the command, as a table of answers: is the pin on its origin/main, and what its log says
        # between two pins (the default is a checkout that does not hold the old pin, so there is no log)
        self.lab, self.labcalls, self.lab_answers = self.root / "lab-checkout", [], {"merge-base": 0, "log": (128, "")}
        self.fake_lab = True

    def remote(self, name, content=None, **more):
        self.remotes[name] = gitfixture.make_repo(self.root / "remotes" / f"{name.replace('/', '-')}.git", content or files(), **more)
        return self.remotes[name]

    def git(self, args, cwd=None, timeout=300):
        """The maintainer's git: their name and email, and the hardening of gitx. What is asked of the Lab checkout is
        answered from the table, unless the test made a real one."""
        if cwd is not None and Path(cwd) == self.lab:
            self.labcalls.append(list(args))
            if self.fake_lab:
                if args[0] == "merge-base":
                    return gitx.Result(self.lab_answers["merge-base"], b"", "")
                code, text = self.lab_answers["log"]
                return gitx.Result(code, text.encode(), "")
            return gitx.run(args, cwd=cwd, timeout=timeout)
        self.gitcalls.append(list(args))
        return gitx.run(args, cwd=cwd, timeout=timeout, env=gitfixture.IDENTITY)

    def run_fleet(self, github, standard, names=None, ref=NEW, **seams):
        for name in names if names is not None else [r["full_name"] for r in github.repos if r["full_name"] not in self.remotes]:
            self.remote(name)
        seams.setdefault("git", self.git)
        seams.setdefault("lab", str(self.lab))
        return fleet.upgrade_fleet(github.owner, github, ref, standard=standard, tree=lambda root: ("tree", Path(root)),
                                   remote=lambda repo: gitfixture.url(self.remotes[repo]), workdir=str(self.workdir),
                                   today=lambda: TODAY, **seams)

    def tip(self, name, branch="main"):
        return gitfixture.tip(self.remotes[name], branch)

    def branches(self, name):
        return gitfixture.git("for-each-ref", "--format=%(refname:short)", "refs/heads", repo=self.remotes[name]).split()

    def test_one_pull_request_for_each_repository(self):
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal"), listed("acme/plain", topics=())])
        standard = Standard()
        results = self.run_fleet(github, standard, names=["acme/lab-hub", "acme/lab-internal"])
        self.assertEqual([("acme/lab-hub", "opened", "https://github.com/acme/lab-hub/pull/41"),
                          ("acme/lab-internal", "opened", "https://github.com/acme/lab-internal/pull/42")],
                         [(r.repo, r.status, r.detail) for r in results])
        self.assertEqual(["acme/lab-hub", "acme/lab-internal"], [repo for repo, _ in github.posted])
        title = "Update to catalog standard 2"
        for repo, body in github.posted:
            self.assertEqual({"title", "head", "base", "body"}, set(body))
            self.assertEqual((title, BRANCH, "main"), (body["title"], body["head"], body["base"]))
            self.assertEqual("Brings the managed files to catalog standard 2.\n\nLab pin: aaaaaaa to bbbbbbb\n\n"
                             f"https://github.com/acme-labs/lab/compare/{OLD}...{NEW}", body["body"])
        self.assertEqual(2, len([call for call in github.calls if call[0] == "POST"]))
        self.assertEqual([], [call for call in github.calls if call[0] not in ("GET", "POST")])

    def test_the_branch_holds_one_commit_of_the_maintainer_with_nothing_else_in_it(self):
        github = Github(repos=[listed("acme/lab-hub")])
        before = self.remote("acme/lab-hub")
        main = self.tip("acme/lab-hub")
        self.run_fleet(github, Standard(owned=[".github/dependabot.yml"]))
        self.assertEqual(main, self.tip("acme/lab-hub"))                       # main is not touched
        self.assertEqual(["main", BRANCH], sorted(self.branches("acme/lab-hub"), key=lambda name: name != "main"))
        branch = gitfixture.git("rev-list", "--count", f"main..{BRANCH}", repo=before).strip()
        self.assertEqual("1", branch)
        message = gitfixture.git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", BRANCH, repo=before)
        self.assertTrue(message.startswith("Test Author <author@example.test>\nTest Author <author@example.test>\nUpdate to catalog standard 2\n"))
        self.assertIn(f"https://github.com/acme-labs/lab/compare/{OLD}...{NEW}", message)
        self.assertNotRegex(message, r"(?i)co-authored|signed-off|generated with|claude|anthropic")
        changed = gitfixture.git("diff", "--name-only", "main", BRANCH, repo=before).split()
        self.assertEqual([".github/standard.json", ".github/workflows/ci.yml"], changed)         # the owned file is as it was
        # A link of the catalog is still a link: the clone made none, and the commit changed none
        mode = gitfixture.git("ls-tree", BRANCH, ".github/link", repo=before)
        self.assertTrue(mode.startswith("120000 blob "), mode)

    def test_the_standard_is_asked_in_a_clone_without_links_and_with_the_new_pin(self):
        github = Github(repos=[listed("acme/lab-hub")])
        standard = Standard()
        self.run_fleet(github, standard)
        plan, apply = standard.calls
        self.assertEqual(("plan", NEW), (plan[0], plan[2]["lab_ref"]))
        self.assertEqual("tree", plan[1][0])
        self.assertEqual(plan[1][1], apply[1])
        self.assertFalse(any(standard.links[0].values()), standard.links[0])
        self.assertIn(".github/link", standard.links[0])                       # a plain file, with the text of the link
        self.assertFalse(apply[1].exists())                                    # and the clone is gone

    def test_owned_files_are_skipped_and_said(self):
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal")])
        results = self.run_fleet(github, Standard(owned=[".github/dependabot.yml", ".github/CODEOWNERS"]))
        self.assertEqual([[".github/CODEOWNERS", ".github/dependabot.yml"]] * 2, [r.owned for r in results])
        lines = fleet.render_upgrade(results).splitlines()
        self.assertEqual("acme/lab-hub       opened  https://github.com/acme/lab-hub/pull/41  owned files skipped: "
                         ".github/CODEOWNERS, .github/dependabot.yml", lines[0])
        files_plan = Standard()
        files_plan.upgrade_plan = lambda tree, options: {"files": [{"path": ".github/Makefile", "action": "owned"},
                                                                    {"path": ".github/a.yml", "action": "update"}]}
        self.assertEqual([[".github/Makefile"]], [r.owned for r in self.run_fleet(Github(repos=[listed("acme/third")]), files_plan)])

    def test_nothing_to_change_is_current(self):
        github = Github(repos=[listed("acme/lab-hub")])
        results = self.run_fleet(github, Standard(owned=[".github/dependabot.yml"], rewrite=False))
        self.assertEqual([("acme/lab-hub", "current", "")], [(r.repo, r.status, r.detail) for r in results])
        self.assertEqual("acme/lab-hub  current  owned files skipped: .github/dependabot.yml", fleet.render_upgrade(results))
        self.assertEqual([], github.posted)
        self.assertEqual(["main"], self.branches("acme/lab-hub"))
        self.assertEqual(0, fleet.upgrade_exit_code(results))

    def test_a_repository_whose_default_branch_moved_while_it_ran_is_left_alone(self):
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal")])
        for name in ("acme/lab-hub", "acme/lab-internal"):
            self.remote(name)

        def moves(root):
            if "lab-hub" in str(self.gitcalls[0]):
                gitfixture.commit(self.remotes["acme/lab-hub"], {"README.md": "Somebody pushed.\n"}, "Push")
        standard = Standard(touch=moves)
        before = self.tip("acme/lab-hub")
        results = self.run_fleet(github, standard)
        self.assertEqual(["moved", "opened"], [r.status for r in results])
        self.assertEqual("main moved while the upgrade ran; run it again", results[0].detail)
        self.assertEqual(1, fleet.upgrade_exit_code(results))
        self.assertEqual(["main"], self.branches("acme/lab-hub"))               # nothing was pushed
        self.assertNotEqual(before, self.tip("acme/lab-hub"))
        self.assertEqual(["acme/lab-internal"], [repo for repo, _ in github.posted])

    def test_nothing_is_stored(self):
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal"), listed("acme/lab-old")])
        home = Path(os.environ["HOME"])
        known = {path for path in home.rglob("*")}
        calls = []

        class Flaky(Standard):
            def upgrade_apply(self, root, plan):
                calls.append(root)
                if len(calls) == 2:
                    raise ValueError("this one cannot be upgraded")
                return super().upgrade_apply(root, plan)
        results = self.run_fleet(github, Flaky())
        self.assertEqual(["opened", "failed", "opened"], [r.status for r in results])
        self.assertEqual("ValueError: this one cannot be upgraded", results[1].detail)
        self.assertEqual([], list(self.workdir.iterdir()))                      # every clone is gone, the one that failed too
        self.assertEqual(known, {path for path in home.rglob("*")})             # and nothing was left in the home folder
        self.assertEqual(["main"], self.branches("acme/lab-internal"))
        self.assertEqual(1, fleet.upgrade_exit_code(results))
        self.assertTrue(all(not call.exists() for call in calls))

    def test_the_content_of_a_catalog_is_never_changed(self):
        def touches(root):
            (root / "addons" / "echo" / "compose.yml").write_text("services: {evil: {privileged: true}}\n", encoding="utf-8")
        github = Github(repos=[listed("acme/lab-hub")])
        results = self.run_fleet(github, Standard(touch=touches))
        self.assertEqual("failed", results[0].status)
        self.assertEqual("the upgrade changed the content of the catalog: addons/echo/compose.yml", results[0].detail)
        self.assertEqual(([], ["main"]), (github.posted, self.branches("acme/lab-hub")))

    def test_a_pull_request_of_an_earlier_upgrade_is_not_followed_by_a_second(self):
        mine = {"number": 12, "head": {"ref": "standard/1-20260901", "repo": {"full_name": "acme/lab-hub"}}}
        fork = {"number": 13, "head": {"ref": "standard/9", "repo": {"full_name": "stranger/lab-internal"}}}
        other = {"number": 14, "head": {"ref": "feature/x", "repo": {"full_name": "acme/lab-old"}}}
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal"), listed("acme/lab-old")],
                        pulls={"acme/lab-hub": [mine], "acme/lab-internal": [fork], "acme/lab-old": [other]})
        results = self.run_fleet(github, Standard())
        self.assertEqual([("acme/lab-hub", "open", "pull request #12 is still open"), ("acme/lab-internal", "opened"), ("acme/lab-old", "opened")],
                         [(r.repo, r.status, r.detail) if r.status == "open" else (r.repo, r.status) for r in results])
        self.assertEqual(["acme/lab-internal", "acme/lab-old"], [repo for repo, _ in github.posted])      # a fork's branch does not count
        self.assertEqual(0, len([a for a in self.gitcalls if "lab-hub" in " ".join(a)]))                  # and the open one was not cloned

    def test_a_branch_of_that_name_is_never_reused(self):
        github = Github(repos=[listed("acme/lab-hub")])
        remote = self.remote("acme/lab-hub")
        gitfixture.git("branch", BRANCH, "main", repo=remote)
        gitfixture.git("branch", BRANCH + "-2", "main", repo=remote)
        results = self.run_fleet(github, Standard())
        self.assertEqual("opened", results[0].status)
        self.assertEqual(f"{BRANCH}-3", github.posted[0][1]["head"])
        self.assertEqual(["main", BRANCH, BRANCH + "-2", BRANCH + "-3"], sorted(self.branches("acme/lab-hub"), key=lambda n: (n != "main", n)))

    def test_an_archived_repository_is_skipped(self):
        github = Github(repos=[listed("acme/lab-hub", archived=True)])
        results = self.run_fleet(github, Standard(), names=["acme/lab-hub"])
        self.assertEqual([("acme/lab-hub", "skipped", "archived")], [(r.repo, r.status, r.detail) for r in results])
        self.assertEqual([], self.gitcalls)

    def test_a_default_branch_that_is_not_a_name_is_not_used(self):
        github = Github(repos=[listed("acme/lab-hub", branch="--upload-pack=x"), listed("acme/lab-internal", branch=None)])
        results = self.run_fleet(github, Standard(), names=["acme/lab-hub", "acme/lab-internal"])
        self.assertEqual(["failed", "failed"], [r.status for r in results])
        self.assertEqual([], self.gitcalls)

    def test_a_tree_git_refuses_is_a_failure_of_that_repository(self):
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal")])
        self.remotes["acme/lab-hub"] = gitfixture.make_dotgit_repo(self.root / "remotes" / "bad.git")
        results = self.run_fleet(github, Standard())
        self.assertEqual(["failed", "opened"], [r.status for r in results])
        self.assertTrue(results[0].detail.startswith("git clone failed: "), results[0].detail)
        self.assertEqual([], list(self.workdir.iterdir()))

    def test_the_commit_is_unsigned_whatever_the_configuration_of_the_maintainer_says(self):
        (Path(os.environ["HOME"]) / ".gitconfig").write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent/gpg\n", encoding="utf-8")
        github = Github(repos=[listed("acme/lab-hub")])
        self.assertEqual("opened", self.run_fleet(github, Standard())[0].status)

    def test_a_maintainer_with_no_name_and_email_is_told_so(self):
        (Path(os.environ["HOME"]) / ".gitconfig").write_text("[user]\n\tuseConfigOnly = true\n", encoding="utf-8")      # git guesses nothing
        github = Github(repos=[listed("acme/lab-hub")])

        def theirs(args, cwd=None, timeout=300):          # the git of a maintainer who has set no identity; the Lab is answered as ever
            return self.git(args, cwd=cwd, timeout=timeout) if cwd and Path(cwd) == self.lab else gitx.run(args, cwd=cwd, timeout=timeout)
        results = self.run_fleet(github, Standard(), git=theirs)
        self.assertEqual("failed", results[0].status)
        self.assertEqual("Git has no name and email to commit with. Set user.name and user.email, then run this again.", results[0].detail)
        self.assertEqual([], github.posted)

    def test_a_pull_request_that_is_refused_leaves_the_branch_and_says_so(self):
        github = Github(repos=[listed("acme/lab-hub")], refuse_pulls=Reply(422, {"message": "Validation Failed"}))
        results = self.run_fleet(github, Standard())
        self.assertEqual("failed", results[0].status)
        self.assertEqual(f"the branch {BRANCH} is pushed and the pull request was refused: HTTP 422: Validation Failed", results[0].detail)

    def test_the_lab_commit_is_a_full_commit(self):
        github = Github(repos=[listed("acme/lab-hub")])
        for ref in ("", "latest", "main", "abc123", "A" * 40, "a" * 39, "a" * 41, None, "a" * 40 + "\n"):
            with self.assertRaises(settings.Usage):
                fleet.upgrade_fleet("acme", github, ref, standard=Standard())
        self.assertEqual([], github.calls)

    def test_the_lab_modules_are_the_defaults_when_none_is_given(self):
        """`standard.upgrade_plan`, `standard.upgrade_apply` and `tree.FolderTree` are what runs when no stand-in is given."""
        import types
        from unittest import mock
        standard = Standard()
        module = types.SimpleNamespace(upgrade_plan=standard.upgrade_plan, upgrade_apply=standard.upgrade_apply, LEVEL=2)
        github = Github(repos=[listed("acme/lab-hub")])
        self.remote("acme/lab-hub")
        with mock.patch.object(fleet, "standards", module), mock.patch.object(fleet, "FolderTree", lambda root: ("tree", Path(root))):
            results = fleet.upgrade_fleet("acme", github, NEW, git=self.git, remote=lambda repo: gitfixture.url(self.remotes[repo]),
                                          workdir=str(self.workdir), today=lambda: TODAY, lab=str(self.lab))
        self.assertEqual(["opened"], [r.status for r in results])
        self.assertEqual(["plan", "apply"], [call[0] for call in standard.calls])

    def test_the_command_line(self):
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal")])
        for name in ("acme/lab-hub", "acme/lab-internal"):
            self.remote(name)
        seams = dict(standard=Standard(), tree=lambda root: root, remote=lambda repo: gitfixture.url(self.remotes[repo]),
                     workdir=str(self.workdir), today=lambda: TODAY, git=self.git, lab=str(self.lab))
        out, err = io.StringIO(), io.StringIO()
        code = fleet.main(["--fleet", "acme", "--lab-ref", NEW], runner=github, stdout=out, stderr=err, **seams)
        self.assertEqual((0, ""), (code, err.getvalue()))
        self.assertEqual(["acme/lab-hub       opened  https://github.com/acme/lab-hub/pull/41",
                          "acme/lab-internal  opened  https://github.com/acme/lab-internal/pull/42"], out.getvalue().splitlines())
        out, err = io.StringIO(), io.StringIO()
        code = fleet.main(["--fleet", "acme", "--lab-ref", NEW, "--format", "json"], runner=github, stdout=out, stderr=err,
                          **{**seams, "standard": Standard(rewrite=False)})
        self.assertEqual(0, code)
        self.assertEqual(["current", "current"], [r["status"] for r in json.loads(out.getvalue())])
        for argv in (["--fleet", "acme"], ["--lab-ref", NEW], ["--fleet", "acme", "--lab-ref", "main"], ["--fleet", "acme", "--lab-ref", NEW, "--x"]):
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(2, fleet.main(argv, runner=github, stdout=out, stderr=err, **seams), argv)
            self.assertEqual((1, ""), (len(err.getvalue().splitlines()), out.getvalue()), argv)

    def test_no_pull_request_is_merged_forced_or_opened_from_a_fork(self):
        github = Github(repos=[listed("acme/lab-hub")])
        self.run_fleet(github, Standard())
        self.assertEqual({("GET", "users/acme"), ("GET", "user"), ("GET", "user/repos?affiliation=owner&per_page=100"),
                          ("GET", "repos/acme/lab-hub/pulls?state=open&per_page=100"), ("POST", "repos/acme/lab-hub/pulls")},
                         {(call[0], call[1]) for call in github.calls})
        pushes = [args for args in self.gitcalls if "push" in args]
        self.assertEqual(1, len(pushes))
        self.assertEqual(f"HEAD:refs/heads/{BRANCH}", pushes[0][-1])
        self.assertNotIn("--force", " ".join(pushes[0]))
        self.assertFalse([a for a in pushes[0] if a.startswith(("+", "-f"))])

    # ------------------------------------------------------------------ what an upgrade may change

    def test_files_that_are_not_managed_are_never_committed(self):
        """Every managed file lives in `.github/` or `.catalog/`. The upgrade is another package's code: what it
        writes elsewhere is not a thing to put in a pull request that a maintainer reads as 'the standard'."""
        for number, path in enumerate(("catalog.yml", "README.md", "evil.sh", "Makefile", ".githubx/x.yml", ".catalogs/x", "docs/a.md")):
            def touches(root, path=path):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("changed by the upgrade\n", encoding="utf-8")
            name = f"acme/lab-{number}"
            github = Github(repos=[listed(name)])
            results = self.run_fleet(github, Standard(touch=touches))
            self.assertEqual(("failed", f"the upgrade changed files that are not managed: {path}"), (results[0].status, results[0].detail), path)
            self.assertEqual(([], ["main"]), (github.posted, self.branches(name)), path)
        # Inside the managed folders is the upgrade's own business, a new file or a removed one
        def inside(root):
            (root / ".catalog").mkdir()
            (root / ".catalog" / "standard.mk").write_text("dev:\n", encoding="utf-8")
            (root / ".github" / "dependabot.yml").unlink()
        self.assertEqual("opened", self.run_fleet(Github(repos=[listed("acme/lab-inside")]), Standard(touch=inside))[0].status)
        # And the content folders are named first when both were touched
        def both(root):
            (root / "evil.sh").write_text("x\n", encoding="utf-8")
            (root / "addons" / "echo" / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.assertEqual("the upgrade changed the content of the catalog: addons/echo/compose.yml",
                         self.run_fleet(Github(repos=[listed("acme/lab-both")]), Standard(touch=both))[0].detail)

    def test_a_rename_does_not_hide_a_file_that_is_not_managed(self):
        """`git status` pairs a staged rename in two entries, the second without a status: read as every other entry it loses
        three characters, and `abc.github/z` would pass for `.github/z`."""
        def renames(root):
            gitx.run(["mv", "abc.github/z", ".github/y"], cwd=str(root), env=gitfixture.IDENTITY)
        github = Github(repos=[listed("acme/lab-hub")])
        self.remote("acme/lab-hub", {**files(), "abc.github/z": "a file of the catalog\n"})
        results = self.run_fleet(github, Standard(touch=renames))
        self.assertEqual(("failed", "the upgrade changed files that are not managed: abc.github/z"), (results[0].status, results[0].detail))
        self.assertEqual([], github.posted)

    # ------------------------------------------------------------------ the Lab commit (SD11)

    SD11 = ("The Lab commit bbbbbbb is not on the Lab's main branch. Fix: pin a commit of the Lab's main "
            "(catalog.py upgrade --lab-ref COMMIT).")

    def test_the_lab_commit_has_to_be_on_the_labs_main_before_anything_is_listed(self):
        github = Github(repos=[listed("acme/lab-hub")])
        self.remote("acme/lab-hub")
        for answer in (1, 128, 2):                      # not an ancestor, a commit the checkout does not hold, git failing
            self.lab_answers["merge-base"], self.labcalls[:] = answer, []
            with self.assertRaises(settings.Unusable) as raised:
                self.run_fleet(github, Standard())
            self.assertEqual(self.SD11, str(raised.exception))
            self.assertEqual([["merge-base", "--is-ancestor", NEW, "origin/main"]], self.labcalls)
            self.assertEqual(([], [], []), (github.calls, self.gitcalls, list(self.workdir.iterdir())))      # nothing was asked or made
            self.assertEqual(["main"], self.branches("acme/lab-hub"))
        self.lab_answers["merge-base"] = 0
        self.assertEqual("opened", self.run_fleet(github, Standard())[0].status)

    def test_the_command_refuses_a_lab_commit_that_is_not_on_the_labs_main_with_one_line(self):
        github = Github(repos=[listed("acme/lab-hub")])
        self.remote("acme/lab-hub")
        self.lab_answers["merge-base"] = 1
        out, err = io.StringIO(), io.StringIO()
        code = fleet.main(["--fleet", "acme", "--lab-ref", NEW], runner=github, stdout=out, stderr=err, standard=Standard(),
                          tree=lambda root: root, remote=lambda repo: gitfixture.url(self.remotes[repo]), workdir=str(self.workdir),
                          today=lambda: TODAY, git=self.git, lab=str(self.lab))
        self.assertEqual((2, "", self.SD11 + "\n"), (code, out.getvalue(), err.getvalue()))
        self.assertEqual([], github.calls)

    def real_lab(self):
        """A Lab of the test's own, a real repository: a start, a guide, an engine change and a change of the action, which is
        the tip of its origin/main, and a commit of a fork that main does not have."""
        lab = gitfixture.make_repo(self.root / "lab.git", {"README.md": "Lab\n", "lab-ui/engine.py": "1\n"}, message="Start the Lab")
        first = gitfixture.tip(lab)
        docs = gitfixture.commit(lab, {"docs/a.md": "a\n"}, "Write a guide")
        engine = gitfixture.commit(lab, {"lab-ui/engine.py": "2\n"}, "Make the engine read the standard")
        action = gitfixture.commit(lab, {".github/actions/lab/action.yml": "x\n"}, "Pin the Lab action @someone #12")
        gitfixture.git("update-ref", "refs/remotes/origin/main", action, repo=lab)
        tree = gitfixture.git("rev-parse", f"{first}^{{tree}}", repo=lab).strip()
        fork = gitfixture.git("commit-tree", tree, "-p", first, "-m", "A commit of a fork", repo=lab, when=gitfixture.BASE_TIME + 5).strip()
        self.lab, self.fake_lab = lab, False
        return {"first": first, "docs": docs, "engine": engine, "action": action, "fork": fork}

    def test_the_lab_commit_is_asked_of_a_real_checkout(self):
        lab = self.real_lab()
        github = Github(repos=[listed("acme/lab-hub")])
        self.remote("acme/lab-hub", files(lab["first"]))
        for name in ("fork", "docs"):
            if name == "docs":
                continue
            with self.assertRaises(settings.Unusable) as raised:
                self.run_fleet(github, Standard(ref=lab[name]), ref=lab[name])
            self.assertEqual(f"The Lab commit {lab[name][:7]} is not on the Lab's main branch. Fix: pin a commit of the Lab's main "
                             "(catalog.py upgrade --lab-ref COMMIT).", str(raised.exception))
        with self.assertRaises(settings.Unusable):
            self.run_fleet(github, Standard(ref="c" * 40), ref="c" * 40)               # a commit the Lab does not hold at all
        # A Lab that is not a repository, and a folder that is gone, say nothing about the commit: the answer is the same line
        for where in (self.root / "not-a-repository", self.root / "nowhere"):
            where.parent.mkdir(exist_ok=True)
            if where.name == "not-a-repository":
                where.mkdir()
            with self.assertRaises(settings.Unusable) as raised:
                self.run_fleet(github, Standard(ref=lab["action"]), ref=lab["action"], lab=str(where))
            self.assertEqual(f"The Lab commit {lab['action'][:7]} is not on the Lab's main branch. Fix: pin a commit of the Lab's main "
                             "(catalog.py upgrade --lab-ref COMMIT).", str(raised.exception))
        self.assertEqual(([], []), (github.calls, [call for call in self.gitcalls if call[0] != "merge-base"]))      # nothing else was run
        # The tip of main, and one before it, are on it. The pull request carries the Lab's own log of lab-ui and .github
        # between the pins (not the guide: it touches neither), as code, so that a subject names nobody and links nothing
        results = self.run_fleet(github, Standard(ref=lab["action"]), ref=lab["action"])
        self.assertEqual("opened", results[0].status)
        body = github.posted[0][1]["body"]
        short = lambda commit: commit[:7]  # noqa: E731
        self.assertIn(f"git log --oneline {lab['first']}..{lab['action']} -- lab-ui .github\n\n    ", body)
        lines = body.split("-- lab-ui .github\n\n")[1].splitlines()
        self.assertEqual(["Pin the Lab action @someone #12", "Make the engine read the standard"],
                         [line.strip().split(" ", 1)[1] for line in lines])
        self.assertEqual([short(lab["action"]), short(lab["engine"])], [line.strip().split(" ", 1)[0][:7] for line in lines])
        self.assertNotIn("Write a guide", body)
        self.assertTrue(all(line.startswith("    ") for line in lines))
        message = gitfixture.git("log", "-1", "--format=%B", BRANCH, repo=self.remotes["acme/lab-hub"])
        self.assertIn(f"Lab pin: {lab['first'][:7]} to {lab['action'][:7]}", message)
        self.assertIn(f"git log --oneline {lab['first']}..{lab['action']} -- lab-ui .github\n\n    ", message)
        self.assertIn("Make the engine read the standard", message)
        self.assertNotIn("Write a guide", message)
        self.assertNotRegex(message, r"(?i)co-authored|signed-off|generated with|claude|anthropic")
        # the tip of main is on main, and so is anything before it
        self.assertEqual([["merge-base", "--is-ancestor", lab["action"], "origin/main"]],
                         [call for call in self.labcalls if call[0] == "merge-base"][-1:])

    def test_the_message_of_a_pin_carries_the_log_of_the_lab(self):
        self.lab_answers["log"] = (0, "cccc333 Fix the check\nbbbb222 Add the guard @someone #12\n\x1b[31mdddd444 Colour\x1b[0m \u202e\n")
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal")])
        results = self.run_fleet(github, Standard())
        self.assertEqual(["opened", "opened"], [r.status for r in results])
        log = ("    cccc333 Fix the check\n    bbbb222 Add the guard @someone #12\n    [31mdddd444 Colour [0m")
        for _, body in github.posted:
            self.assertTrue(body["body"].endswith(f"-- lab-ui .github\n\n{log}"), body["body"])
            self.assertIn(f"https://github.com/acme-labs/lab/compare/{OLD}...{NEW}\n\ngit log --oneline {OLD}..{NEW} -- lab-ui .github\n", body["body"])
            self.assertNotIn("\x1b", body["body"])             # what the Lab's log says is words, not terminal commands
            self.assertNotIn("\u202e", body["body"])
        self.assertEqual(1, len([call for call in self.labcalls if call[0] == "log"]))            # asked once for the two
        self.assertEqual(["log", "--oneline", "--no-decorate", "-n", "50", f"{OLD}..{NEW}", "--", "lab-ui", ".github"],
                         [call for call in self.labcalls if call[0] == "log"][0])
        for repo in ("acme/lab-hub", "acme/lab-internal"):
            message = gitfixture.git("log", "-1", "--format=%B", BRANCH, repo=self.remotes[repo]).rstrip("\n")
            self.assertTrue(message.startswith("Update to catalog standard 2\n\nLab pin: aaaaaaa to bbbbbbb\nhttps://github.com/acme-labs/lab/compare/"))
            self.assertTrue(message.endswith(f"git log --oneline {OLD}..{NEW} -- lab-ui .github\n\n{log}"), message)

    def test_the_log_of_the_lab_is_at_most_fifty_lines_and_is_left_out_when_the_lab_cannot_give_it(self):
        self.lab_answers["log"] = (0, "".join(f"{number:07x} Change {number}\n" for number in range(80)))
        github = Github(repos=[listed("acme/lab-hub")])
        self.run_fleet(github, Standard())
        logged = github.posted[0][1]["body"].split("-- lab-ui .github\n\n")[1].splitlines()
        self.assertEqual(50, len(logged))
        self.assertEqual("    0000000 Change 0", logged[0])
        # The Lab checkout does not hold the old pin: the compare link is all there is, and the pull request is still opened
        self.lab_answers["log"] = (128, "")
        github = Github(repos=[listed("acme/lab-internal")])
        self.run_fleet(github, Standard())
        body = github.posted[0][1]["body"]
        self.assertEqual("Brings the managed files to catalog standard 2.\n\nLab pin: aaaaaaa to bbbbbbb\n\n"
                         f"https://github.com/acme-labs/lab/compare/{OLD}...{NEW}", body)
        self.lab_answers["log"] = (0, "\n")
        github = Github(repos=[listed("acme/third")])
        self.run_fleet(github, Standard())
        self.assertNotIn("git log", github.posted[0][1]["body"])

    def test_no_log_is_asked_when_the_pin_did_not_move(self):
        self.lab_answers["log"] = (0, "cccc333 Fix the check\n")
        github = Github(repos=[listed("acme/lab-hub")])
        self.remote("acme/lab-hub", files(NEW))
        self.run_fleet(github, Standard(rewrite=True))
        self.assertEqual([], [call for call in self.labcalls if call[0] == "log"])
        self.assertNotIn("git log", github.posted[0][1]["body"])

    def test_the_upgrade_in_github_annotations(self):
        results = [fleet.Upgraded("acme/a", "opened", "https://github.com/acme/a/pull/41", [".github/dependabot.yml"]),
                   fleet.Upgraded("acme/b", "current", "", []), fleet.Upgraded("acme/c", "open", "pull request #12 is still open", []),
                   fleet.Upgraded("acme/d", "failed", "bad\nname::error title=x::pwned%0A", []),
                   fleet.Upgraded("acme/e", "moved", "main moved while the upgrade ran; run it again", []),
                   fleet.Upgraded("acme/f", "skipped", "archived", [])]
        lines = fleet.render_upgrade(results, "github").splitlines()
        self.assertEqual(["::notice title=upgrade::acme/a: opened https://github.com/acme/a/pull/41; owned files skipped: .github/dependabot.yml",
                          "::notice title=upgrade::acme/c: open pull request #12 is still open",
                          "::error title=upgrade::acme/d: failed bad%0Aname::error title=x::pwned%250A",
                          "::error title=upgrade::acme/e: moved main moved while the upgrade ran; run it again",
                          "::notice title=upgrade::acme/f: skipped archived"], lines)
        self.assertEqual("", fleet.render_upgrade([fleet.Upgraded("acme/b", "current", "", [])], "github"))
        self.assertEqual("", fleet.render_upgrade([], "github"))
        github = Github(repos=[listed("acme/lab-hub"), listed("acme/lab-internal")])
        for name in ("acme/lab-hub", "acme/lab-internal"):
            self.remote(name)
        seams = dict(standard=Standard(), tree=lambda root: root, remote=lambda repo: gitfixture.url(self.remotes[repo]),
                     workdir=str(self.workdir), today=lambda: TODAY, git=self.git, lab=str(self.lab))
        out, err = io.StringIO(), io.StringIO()
        code = fleet.main(["--fleet", "acme", "--lab-ref", NEW, "--format", "github"], runner=github, stdout=out, stderr=err, **seams)
        self.assertEqual((0, ""), (code, err.getvalue()))
        self.assertEqual(["::notice title=upgrade::acme/lab-hub: opened https://github.com/acme/lab-hub/pull/41",
                          "::notice title=upgrade::acme/lab-internal: opened https://github.com/acme/lab-internal/pull/42"], out.getvalue().splitlines())


class Reading(Fleet):
    """`.github/standard.json` of a clone is whatever the catalog merged: it is read once, small, in the clone, as JSON."""

    def test_the_standard_of_a_clone(self):
        folder = self.folder()
        (folder / ".github").mkdir()
        target = folder / ".github" / "standard.json"
        target.write_text('{"standard": 1, "form": "inline"}', encoding="utf-8")
        self.assertEqual({"standard": 1, "form": "inline"}, fleet._read_standard(folder))
        for text in ("[1]", "x", "", '{"a": ', "[" * 2040 + "]" * 2040, "[" * 100000, '{"a": ' * 600 + "1" + "}" * 600,
                     '{"standard": 1, "x": "' + "y" * settings.STANDARD_LIMIT + '"}'):
            target.write_text(text, encoding="utf-8")
            self.assertEqual({}, fleet._read_standard(folder), text[:20])
        target.write_bytes(b'{"standard": "\xff"}')
        self.assertEqual({}, fleet._read_standard(folder))
        real = json.loads                                   # and the parser stopping at its depth, which Python 3.9 does early

        def stops(text, *args, **kwargs):
            if isinstance(text, str) and text.startswith("[[[["):
                raise RecursionError("maximum recursion depth exceeded while decoding a JSON array from a unicode string")
            return real(text, *args, **kwargs)
        target.write_text("[[[[1]]]]", encoding="utf-8")
        with mock.patch.object(json, "loads", stops):
            self.assertEqual({}, fleet._read_standard(folder))
        target.unlink()
        self.assertEqual({}, fleet._read_standard(folder))
        outside = folder.parent / "outside-standard.json"
        outside.write_text('{"standard": 9}', encoding="utf-8")
        self.addCleanup(outside.unlink)
        os.symlink(outside, target)
        self.assertEqual({}, fleet._read_standard(folder))               # a link is not followed out of the clone
