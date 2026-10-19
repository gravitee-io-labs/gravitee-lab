"""catalogs/gitx.py: the grammar of an address, the environment git runs in, how it is run and killed, what its failures
mean and what it says of a remote. No test needs the network: remotes are bare repositories on this machine
(tests/gitfixture.py), and what git cannot be made to do on demand (stall, run forever, ignore SIGTERM) is a script
put first on the PATH. A test that needs git and finds none prints a skip line."""

import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import gitx

from .. import Case, gitfixture

GITHUB = "acme/lab-hub"

# text -> (url, display, host, repo): everything a person may type that is a Git address, and what the page shows
ACCEPTED = {
    "acme/lab-hub": ("https://github.com/acme/lab-hub.git", "github.com/acme/lab-hub", "github.com", "acme/lab-hub"),
    "acme/lab-hub.git": ("https://github.com/acme/lab-hub.git", "github.com/acme/lab-hub", "github.com", "acme/lab-hub"),
    "  acme/lab-hub  ": ("https://github.com/acme/lab-hub.git", "github.com/acme/lab-hub", "github.com", "acme/lab-hub"),
    "acme/.github": ("https://github.com/acme/.github.git", "github.com/acme/.github", "github.com", "acme/.github"),
    "Acme-1/lab_hub.v2": ("https://github.com/Acme-1/lab_hub.v2.git", "github.com/Acme-1/lab_hub.v2", "github.com",
                          "Acme-1/lab_hub.v2"),
    "https://github.com/acme/lab-hub": ("https://github.com/acme/lab-hub", "github.com/acme/lab-hub", "github.com",
                                        "acme/lab-hub"),
    "https://github.com/acme/lab-hub.git": ("https://github.com/acme/lab-hub.git", "github.com/acme/lab-hub",
                                            "github.com", "acme/lab-hub"),
    "https://github.com/acme/lab-hub/": ("https://github.com/acme/lab-hub", "github.com/acme/lab-hub", "github.com",
                                         "acme/lab-hub"),
    "HTTPS://GitHub.COM/acme/lab-hub.git": ("https://github.com/acme/lab-hub.git", "github.com/acme/lab-hub",
                                            "github.com", "acme/lab-hub"),
    "git@github.com:acme/lab-hub.git": ("git@github.com:acme/lab-hub.git", "github.com/acme/lab-hub", "github.com",
                                        "acme/lab-hub"),
    "git@GitHub.com:acme/lab-hub": ("git@github.com:acme/lab-hub", "github.com/acme/lab-hub", "github.com",
                                    "acme/lab-hub"),
    "ssh://git@github.com/acme/lab-hub.git": ("ssh://git@github.com/acme/lab-hub.git", "github.com/acme/lab-hub",
                                              "github.com", "acme/lab-hub"),
    "ssh://git.example.test:2222/team/hub.git": ("ssh://git.example.test:2222/team/hub.git",
                                                 "git.example.test:2222/team/hub", "git.example.test", "team/hub"),
    "https://git.example.test:8443/team/sub/hub.git": ("https://git.example.test:8443/team/sub/hub.git",
                                                       "git.example.test:8443/team/sub/hub", "git.example.test",
                                                       "team/sub/hub"),
    "https://dev.azure.com/org/project/_git/repo": ("https://dev.azure.com/org/project/_git/repo",
                                                    "dev.azure.com/org/project/_git/repo", "dev.azure.com",
                                                    "org/project/_git/repo"),
    "deploy@git.example.test:/srv/git/hub.git": ("deploy@git.example.test:/srv/git/hub.git",
                                                 "git.example.test/srv/git/hub", "git.example.test", "srv/git/hub"),
    "http://localhost:3000/team/hub.git": ("http://localhost:3000/team/hub.git", "localhost:3000/team/hub",
                                           "localhost", "team/hub"),
    "http://127.0.0.1/a/b": ("http://127.0.0.1/a/b", "127.0.0.1/a/b", "127.0.0.1", "a/b"),
    "http://[::1]:8080/a/b": ("http://[::1]:8080/a/b", "[::1]:8080/a/b", "::1", "a/b"),
}

# text -> code: everything the grammar refuses
REFUSED = {
    "": "bad_address",
    "   ": "bad_address",
    "-oProxyCommand=evil": "bad_address",
    "--upload-pack=touch /tmp/pwned": "bad_address",
    "http://github.com/acme/lab-hub": "bad_address",
    "http://example.test/a/b": "bad_address",
    "git://github.com/acme/lab-hub.git": "bad_address",
    "ext::sh -c touch% /tmp/pwned": "bad_address",
    "ext::sh": "bad_address",
    "fd::17/path": "bad_address",
    "file:///etc/passwd": "bad_address",
    "ftp://example.test/a/b": "bad_address",
    "git+ssh://git@github.com/a/b.git": "bad_address",
    "javascript:alert(1)": "bad_address",
    "https://github.com": "bad_address",
    "https://github.com/": "bad_address",
    "https://github.com/a/b?ref=x": "bad_address",
    "https://github.com/a/b#frag": "bad_address",
    "https://github.com/a/../b": "bad_address",
    "https://github.com/a/./b": "bad_address",
    "https://github.com/a/%2e%2e/b": "bad_address",
    "https://github.com//a": "bad_address",
    "https://github.com/a/b/c d": "bad_address",
    "https://exa mple.test/a/b": "bad_address",
    "https://example.test:0/a/b": "bad_address",
    "https://example.test:70000/a/b": "bad_address",
    "https://example.test:80a/a/b": "bad_address",
    "https://example.test:/a/b": "bad_address",
    "https://-evil.test/a/b": "bad_address",
    "https://evil-.test/a/b": "bad_address",
    "https://999.1.1.1/a/b": "bad_address",
    "https://xn--e.test./a/b": "bad_address",
    "https://\u00e9.test/a/b": "bad_address",
    "https://[::1/a/b": "bad_address",
    "https://[nothost]/a/b": "bad_address",
    "ssh://-oProxyCommand=evil/a/b": "bad_address",
    "ssh://git@-oProxyCommand=evil/a/b": "bad_address",
    "ssh://-v@host.test/a/b": "bad_address",
    "ssh://-oProxyCommand=evil@host.test/a/b": "bad_address",
    "ssh://-@host.test/a/b": "bad_address",
    "u@-host.test:a/b": "bad_address",
    "git@-oProxyCommand=evil:a/b": "bad_address",
    "git@github.com": "bad_address",
    "git@github.com:": "bad_address",
    "@github.com:a/b": "bad_address",
    "host.test:a/b": "bad_address",
    "acme": "bad_address",
    "acme/lab hub": "bad_address",
    "acme/lab\nhub": "bad_address",
    "acme/lab\x00hub": "bad_address",
    "acme/lab\x1bhub": "bad_address",
    "acme/lab\x7fhub": "bad_address",
    "acme/lab\u202ehub": "bad_address",
    "acme/lab\u200bhub": "bad_address",
    "acme/lab\u2028hub": "bad_address",
    "acme/..": "bad_address",
    "acme/.git": "bad_address",
    "acme/.": "bad_address",
    "a/b/c": "bad_address",
    "acme/lab\\hub": "bad_address",
    "C:\\catalogs\\hub": "bad_address",
    "acme/h\u00fcb": "bad_address",
    "https://github.com/" + "a" * 5000: "bad_address",
    "https://user:token@github.com/acme/lab-hub": "bad_address_secret",
    "https://token@github.com/acme/lab-hub": "bad_address_secret",
    "https://:x@github.com/acme/lab-hub": "bad_address_secret",
    "https://@github.com/acme/lab-hub": "bad_address_secret",
    "https://x-access-token:ghp_abc@github.com/acme/lab-hub.git": "bad_address_secret",
    "http://user:pw@localhost/a/b": "bad_address_secret",
    "ssh://user:pw@host.test/a/b": "bad_address_secret",
    "user:token@host.test:a/b": "bad_address_secret",
}

BAD_ADDRESS = "Use owner/repo, a Git address such as https://github.com/owner/repo, or the path of a folder."


class Grammar(Case):

    def test_every_accepted_form_is_parsed_to_the_address_the_page_shows(self):
        for text, (url, display, host, repo) in ACCEPTED.items():
            with self.subTest(text):
                self.assertEqual(gitx.parse_address(text), gitx.Address("git", url, "", display, host, repo))

    def test_every_refused_form_is_refused_with_its_code_and_the_line_of_section_11(self):
        lines = {"bad_address": BAD_ADDRESS,
                 "bad_address_secret": "Remove the user name and token from the address. Git uses the sign-in of this machine."}
        for text, code in REFUSED.items():
            with self.subTest(repr(text)[:60]):
                with self.assertRaises(gitx.CatalogError) as raised:
                    gitx.parse_address(text)
                self.assertEqual((raised.exception.code, raised.exception.status), (code, 400))
                self.assertEqual(raised.exception.message, lines[code])

    def test_a_refusal_never_repeats_what_was_typed(self):
        """The line goes to the page and to logs: a token typed by mistake must not come back in it."""
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.parse_address("https://user:s3cr3t-token@github.com/acme/lab-hub")
        self.assertNotIn("s3cr3t", raised.exception.message + str(raised.exception.detail))

    def test_spellings_of_one_repository_have_one_display_host_and_repo(self):
        shown = {gitx.parse_address(text)[3:] for text in (
            "acme/lab-hub", "https://github.com/acme/lab-hub", "https://github.com/acme/lab-hub.git/",
            "git@github.com:acme/lab-hub.git", "ssh://git@github.com/acme/lab-hub.git", "HTTPS://GITHUB.COM/acme/lab-hub")}
        self.assertEqual(shown, {("github.com/acme/lab-hub", "github.com", "acme/lab-hub")})

    def test_an_address_is_parsed_to_itself_again(self):
        """What the lab stores is `url`: reading it back gives the same address, so that a stored catalog is the one that
        was added."""
        for text in ACCEPTED:
            address = gitx.parse_address(text)
            self.assertEqual(gitx.parse_address(address.url), address, text)
        self.patch(gitx, "ALLOW_FILE", True)
        local = gitx.parse_address("file:///srv/git/hub.git")
        self.assertEqual(gitx.parse_address(local.url), local)

    def test_no_accepted_address_can_be_read_as_an_option_or_holds_a_credential(self):
        for text in ACCEPTED:
            address = gitx.parse_address(text)
            self.assertFalse(address.url.startswith("-"), text)
            self.assertNotRegex(address.url, r"^https?://[^/]*@", text)
            self.assertNotRegex(address.url, r"://[^/@]*:[^/@]*@", text)
            self.assertNotRegex(address.url, r"\s", text)

    def test_http_is_for_a_server_on_this_machine_only(self):
        for host in ("localhost", "127.0.0.1", "[::1]", "127.1.2.3"):
            self.assertEqual(gitx.parse_address(f"http://{host}/a/b").kind, "git", host)
        for host in ("example.test", "192.168.1.5", "10.0.0.1", "localhost.example.test", "0.0.0.0"):
            with self.assertRaises(gitx.CatalogError, msg=host):
                gitx.parse_address(f"http://{host}/a/b")

    def test_a_file_address_is_refused_unless_a_test_allows_it(self):
        self.assertFalse(gitx.ALLOW_FILE)
        self.assertEqual(gitx.PROTOCOLS, "https:ssh")
        for text in ("file:///srv/git/hub.git", "file://localhost/srv/git/hub.git"):
            with self.assertRaises(gitx.CatalogError):
                gitx.parse_address(text)
        self.patch(gitx, "ALLOW_FILE", True)
        self.assertEqual(gitx.parse_address("file:///srv/git/hub.git").url, "file:///srv/git/hub.git")
        for text in ("file://evil.test/srv/git/hub.git", "file:///srv/../etc", "file://", "file:///"):
            with self.assertRaises(gitx.CatalogError, msg=text):
                gitx.parse_address(text)

    def test_ssh_form_is_the_ssh_spelling_of_an_https_address(self):
        https = gitx.parse_address("https://github.com/acme/lab-hub.git")
        ssh = gitx.ssh_form(https)
        self.assertEqual(ssh, gitx.Address("git", "git@github.com:acme/lab-hub.git", "", "github.com/acme/lab-hub",
                                           "github.com", "acme/lab-hub"))
        self.assertEqual(gitx.parse_address(ssh.url), ssh, "what it makes is an address the grammar accepts, unchanged")
        self.assertEqual(gitx.ssh_form(gitx.parse_address(GITHUB)).url, "git@github.com:acme/lab-hub.git")
        self.assertEqual(gitx.ssh_form(gitx.parse_address("https://dev.azure.com/org/p/_git/r")).url,
                         "git@dev.azure.com:org/p/_git/r.git")
        self.assertIsNone(gitx.ssh_form(ssh), "already ssh")
        self.assertIsNone(gitx.ssh_form(gitx.parse_address("ssh://git@github.com/acme/lab-hub.git")))
        self.assertIsNone(gitx.ssh_form(gitx.parse_address("https://git.example.test:8443/a/b.git")), "another port")
        self.assertIsNone(gitx.ssh_form(gitx.parse_address("http://localhost:3000/a/b.git")))

    def test_is_ssh(self):
        self.assertTrue(gitx.is_ssh(gitx.parse_address("git@github.com:acme/lab-hub.git")))
        self.assertTrue(gitx.is_ssh(gitx.parse_address("ssh://git@github.com/acme/lab-hub.git")))
        self.assertFalse(gitx.is_ssh(gitx.parse_address(GITHUB)))

    def test_a_branch_is_a_name_that_is_safe_as_an_argument_and_in_a_reference(self):
        for name in ("main", "develop", "release/1.x", "feature/a-b_c+d", "v1.0.0", "a" * 200):
            self.assertEqual(gitx.valid_branch(name), name)
        for name in ("", "-main", "--upload-pack=x", "a..b", "a//b", "a/.b", "a/", "a.", "a.lock", "a b", "a\nb", "a~b",
                     "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b", "@{u}", "a@{u}", "é", "a" * 201, None, 7):
            with self.assertRaises(gitx.CatalogError, msg=repr(name)) as raised:
                gitx.valid_branch(name, gitx.parse_address(GITHUB))
            self.assertEqual(raised.exception.code, "branch_missing")


class Hostile(Case):
    """Whatever is typed, the grammar answers an Address or a CatalogError, and nothing it accepts is dangerous."""

    PIECES = [*"abcAZ09-_.~+/:@\\ \t\n\x00\x1b?#%[]{}()'\"`$&;|<>*!=,^", "https://", "http://", "ssh://", "git@", "file://", "ext::",
              "../", "./", "~", "[::1]", ":443", ".git", "\u00e9", "\u202e", "\u200b", "user:pw@", "::", "/etc", "github.com/"]

    def test_a_thousand_strange_texts_are_an_address_or_a_refusal_and_never_a_crash(self):
        chance = random.Random(2026)
        accepted = 0
        for _ in range(20000):
            text = "".join(chance.choice(self.PIECES) for _ in range(chance.randint(0, 12)))
            try:
                address = gitx.parse_address(text)
            except gitx.CatalogError as refusal:
                self.assertIn(refusal.code, ("bad_address", "bad_address_secret", "folder_missing", "folder_not_catalog",
                                             "folder_not_shared"), repr(text))
                continue
            accepted += 1
            if address.kind == "git":
                self.assertIs(gitx.checked(address), address, repr(text))
                self.assertFalse(re.search(r"\s|^-|://[^/@]*:[^/@]*@|^https?://[^/]*@", address.url), (text, address))
        self.assertGreater(accepted, 0)

    def test_a_very_long_text_is_refused_at_once(self):
        started = time.monotonic()
        for text in ("a" * 100000, "https://" + "a." * 50000 + "b/c", "acme/" + "a" * 5000, "~/" + "a" * 5000, "a" * 4000 + "!"):
            with self.assertRaises(gitx.CatalogError):
                gitx.parse_address(text)
        self.assertLess(time.monotonic() - started, 2)

    def test_git_output_of_any_kind_is_classified_to_a_line(self):
        chance = random.Random(7)
        for _ in range(2000):
            said = "".join(chr(chance.randint(0, 300)) for _ in range(chance.randint(0, 200)))
            error = gitx.classify(said, chance.choice([None, GITHUB_ADDRESS, SSH_ADDRESS]))
            self.assertIn(error.code, gitx.MESSAGES)
            self.assertTrue(error.message)


class Folders(Case):
    """A folder is an address when it is under the home folder, exists and holds a regular catalog.yml."""

    def setUp(self):
        self.folder()
        self.start(mock.patch.dict(os.environ, {"HOME": str(self.root)}))
        self.write("code/hub/catalog.yml", "x-gravitee-catalog: {}\n")

    def refused(self, text, code):
        with self.assertRaises(gitx.CatalogError, msg=text) as raised:
            gitx.parse_address(text)
        self.assertEqual(raised.exception.code, code, text)
        return raised.exception

    def test_a_catalog_folder_is_an_address_and_is_shown_from_the_home_folder(self):
        path = str(self.root / "code" / "hub")
        for text in (path, path + "/", "~/code/hub", "~/code/./hub", "~/code/../code/hub"):
            self.assertEqual(gitx.parse_address(text), gitx.Address("folder", "", path, "~/code/hub", "", ""), text)

    def test_a_relative_folder_starts_with_a_dot(self):
        self.patch(os, "getcwd", lambda: str(self.root / "code"))
        self.assertEqual(gitx.parse_address("./hub").path, str(self.root / "code" / "hub"))
        self.assertEqual(gitx.parse_address("../code/hub").path, str(self.root / "code" / "hub"))
        self.refused("hub", "bad_address")   # owner/repo is not a folder: a name with no slash is no address at all

    def test_a_folder_name_may_hold_spaces(self):
        self.write("my code/the hub/catalog.yml", "x: 1\n")
        found = gitx.parse_address("~/my code/the hub")
        self.assertEqual((found.kind, found.display), ("folder", "~/my code/the hub"))

    def test_a_home_folder_that_is_the_root_shares_everything_under_it(self):
        self.write("catalog.yml", "x: 1\n")
        with mock.patch.dict(os.environ, {"HOME": "/"}):
            found = gitx.parse_address(str(self.root))
            self.assertEqual(found.path, str(self.root))
            self.assertTrue(found.display.startswith("~/"), found.display)

    def test_the_home_folder_itself_is_shown_as_a_tilde(self):
        self.write("catalog.yml", "x: 1\n")
        self.assertEqual(gitx.parse_address("~").display, "~")

    def test_a_folder_that_is_not_there_is_folder_missing(self):
        error = self.refused("~/code/none", "folder_missing")
        self.assertEqual((error.status, error.message), (400, "No folder at ~/code/none."))
        self.refused(str(self.root / "code" / "hub" / "catalog.yml"), "folder_missing")   # a file is not a folder
        self.assertEqual(self.refused("/nonexistent/place", "folder_missing").message, "No folder at /nonexistent/place.")

    def test_a_folder_without_a_regular_catalog_yml_is_not_a_catalog(self):
        self.write("code/empty/README.md", "x")
        error = self.refused("~/code/empty", "folder_not_catalog")
        self.assertEqual((error.status, error.message), (400, "~/code/empty has no catalog.yml, so it is not a catalog."))
        self.write("code/outside.yml", "x: 1\n")
        (self.root / "code" / "linked").mkdir()
        os.symlink(self.root / "code" / "outside.yml", self.root / "code" / "linked" / "catalog.yml")
        self.refused("~/code/linked", "folder_not_catalog")   # a link is never read as the catalog file
        (self.root / "code" / "dir" / "catalog.yml").mkdir(parents=True)
        self.refused("~/code/dir", "folder_not_catalog")

    def test_a_folder_outside_the_home_folder_cannot_be_shared_with_docker(self):
        elsewhere = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(elsewhere, True))
        (elsewhere / "catalog.yml").write_text("x: 1\n")
        error = self.refused(str(elsewhere), "folder_not_shared")
        self.assertEqual((error.status, error.message), (400, "Docker cannot share this folder. Move it under your home folder."))
        os.symlink(elsewhere, self.root / "code" / "away")   # a link out of the home folder is outside it
        self.refused("~/code/away", "folder_not_shared")

    def test_control_characters_are_refused_in_a_folder_too(self):
        for text in ("~/code/hu\nb", "~/code/hu\x00b", "~/code/hu\x1bb", "/tmp/\u202ehub"):
            self.refused(text, "bad_address")


class Environment(Case):

    HOSTILE = {"GIT_DIR": "/x", "GIT_WORK_TREE": "/x", "GIT_INDEX_FILE": "/x", "GIT_SSH": "/x", "GIT_SSH_COMMAND": "evil",
               "GIT_ASKPASS": "/bin/evil", "SSH_ASKPASS": "/bin/evil", "SSH_ASKPASS_REQUIRE": "force",
               "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.sshCommand", "GIT_CONFIG_VALUE_0": "evil",
               "GIT_CONFIG_GLOBAL": "/x", "GIT_EXEC_PATH": "/x", "GIT_TERMINAL_PROMPT": "1", "GCM_INTERACTIVE": "always",
               "GIT_PROXY_COMMAND": "evil", "GIT_ALLOW_PROTOCOL": "ext:file", "LD_PRELOAD": "/x",
               "DYLD_INSERT_LIBRARIES": "/x", "DISPLAY": ":0", "EDITOR": "vi", "PAGER": "less", "GIT_PAGER": "x",
               "LANG": "fr_FR.UTF-8", "LC_ALL": "fr_FR.UTF-8", "LANGUAGE": "fr", "GH_TOKEN": "ghp_x", "GITHUB_TOKEN": "x"}
    PASSED = {"PATH": "/opt/bin:/usr/bin", "HOME": "/home/me", "USER": "me", "SSH_AUTH_SOCK": "/tmp/agent",
              "HTTPS_PROXY": "http://proxy.test:3128", "NO_PROXY": "localhost", "SSL_CERT_FILE": "/etc/ssl/cert.pem",
              "XDG_CONFIG_HOME": "/home/me/.config"}

    def test_the_environment_is_the_few_variables_a_connection_needs_and_the_ones_the_lab_sets(self):
        with mock.patch.dict(os.environ, {**self.HOSTILE, **self.PASSED}, clear=True):
            env = gitx.environment()
        self.assertEqual(env, {**self.PASSED, "GIT_ALLOW_PROTOCOL": "https:ssh", "GIT_TERMINAL_PROMPT": "0",
                               "GIT_ASKPASS": "", "SSH_ASKPASS_REQUIRE": "never", "GCM_INTERACTIVE": "never",
                               "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", "LC_ALL": "C"})

    def test_no_askpass_program_is_left_for_git_or_ssh_to_ask(self):
        with mock.patch.dict(os.environ, self.HOSTILE, clear=True):
            env = gitx.environment()
        self.assertEqual(env["GIT_ASKPASS"], "", "an empty GIT_ASKPASS is read by git as no askpass at all")
        self.assertNotIn("SSH_ASKPASS", env)
        self.assertNotIn("DISPLAY", env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GCM_INTERACTIVE"], "never")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])

    def test_no_variable_of_git_survives_but_the_ones_the_lab_sets(self):
        with mock.patch.dict(os.environ, self.HOSTILE, clear=True):
            env = gitx.environment()
        self.assertEqual(sorted(name for name in env if name.startswith(("GIT_", "GCM_"))),
                         ["GCM_INTERACTIVE", "GIT_ALLOW_PROTOCOL", "GIT_ASKPASS", "GIT_SSH_COMMAND", "GIT_TERMINAL_PROMPT"])

    def test_there_is_always_a_path(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn("/usr/bin", gitx.environment()["PATH"])

    def test_the_protocols_are_read_when_the_environment_is_made(self):
        self.assertEqual(gitx.environment()["GIT_ALLOW_PROTOCOL"], "https:ssh")
        self.patch(gitx, "PROTOCOLS", "https:ssh:file")
        self.assertEqual(gitx.environment()["GIT_ALLOW_PROTOCOL"], "https:ssh:file")

    def test_an_http_loopback_address_adds_http_to_the_protocols_of_its_commands_alone(self):
        self.assertEqual(gitx.address_env(gitx.parse_address("http://localhost:3000/a/b.git")),
                         {"GIT_ALLOW_PROTOCOL": "https:ssh:http"})
        for text in (GITHUB, "git@github.com:a/b.git", "ssh://git@example.test/a/b"):
            self.assertEqual(gitx.address_env(gitx.parse_address(text)), {}, text)
        self.assertEqual(gitx.address_env(None), {})

    def test_signin_hint_is_the_command_for_github_and_the_words_elsewhere(self):
        self.assertEqual(gitx.signin(gitx.parse_address(GITHUB)), "Run gh auth login in a terminal")
        self.assertEqual(gitx.signin(gitx.parse_address("https://git.example.test/a/b.git")),
                         "Sign in with Git on this machine")
        self.assertEqual(gitx.signin(None), "Sign in with Git on this machine")


class Running(Case):
    """`run` and `stream` against scripts that stand in for git."""

    def setUp(self):
        self.patch(gitx, "KILL_GRACE", 0.3)
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.scratch, True))

    def alive(self, pid, within=3.0):
        """Whether a process is still there after waiting up to `within` seconds for it to be gone."""
        end = time.monotonic() + within
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            time.sleep(0.05)
        return True

    def warmed(self, body):
        """A fake git that has been run once, untimed, before the test times it: the first run of a script that was just
        written can take longer than the windows of a test (a Mac scans it first; a loaded machine is slower still)."""
        script = gitfixture.fake_git(self, f"[ -n \"$WARM\" ] && exit 0\n{body}")
        self.assertEqual(gitx.run(["x"], env={"WARM": "1"}, timeout=60).code, 0)
        return script

    def test_the_arguments_are_given_as_they_are_and_no_shell_reads_them(self):
        gitfixture.fake_git(self, "printf '%s\\n' \"$@\"")
        marker = self.scratch / "pwned"
        args = ["ls-remote", f"; touch {marker}", "$(touch %s)" % marker, "`touch %s`" % marker, "a b", "*", "--", "-x"]
        result = gitx.run(args, cwd=self.scratch)
        self.assertEqual(result.code, 0)
        self.assertEqual(result.out.decode().splitlines(), [*gitx.HARDEN, *args])
        self.assertFalse(marker.exists())

    def test_a_command_is_a_list_run_without_a_shell_in_a_session_of_its_own_with_no_input(self):
        gitfixture.fake_git(self, "true")
        real = subprocess.Popen
        with mock.patch.object(gitx.subprocess, "Popen", wraps=real) as popen:
            gitx.run(["status"], cwd=self.scratch)
        (argv,), how = popen.call_args
        self.assertIsInstance(argv, list)
        self.assertFalse(how.get("shell"))
        self.assertTrue(how["start_new_session"])
        self.assertEqual(how["stdin"], subprocess.DEVNULL)
        self.assertEqual(how["cwd"], str(self.scratch))
        self.assertEqual(argv[1:], [*gitx.HARDEN, "status"])

    def test_a_leading_git_in_the_arguments_is_dropped(self):
        gitfixture.fake_git(self, "printf '%s\\n' \"$@\"")
        self.assertEqual(gitx.run(["git", "status"]).out.decode().split(), [*gitx.HARDEN, "status"])

    def test_every_command_gets_the_options_that_stop_a_repository_running_a_program(self):
        self.assertEqual(gitx.HARDEN, ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                                       "-c", "log.showSignature=false"])

    def test_commands_still_running_when_the_lab_exits_are_ended(self):
        """The thread that waits for a download is a daemon: at exit it does not get to clean up, so something else does."""
        pids = self.scratch / "pid"
        gitfixture.fake_git(self, f"sleep 60 &\necho $! > '{pids}'\necho ready >&2\nwait")
        ready, results = threading.Event(), []
        thread = threading.Thread(target=lambda: results.append(gitx.stream(["x"], stall=30, cap=60, on_stderr=lambda text: ready.set())))
        thread.start()
        self.assertTrue(ready.wait(10))
        self.assertEqual(len(gitx._LIVE), 1)
        self.patch(gitx, "_ending", False)   # stop_running sets it for good: the test puts it back
        gitx.stop_running()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.alive(int(pids.read_text())))
        self.assertEqual(gitx._LIVE, set(), "a command that ended is no longer followed")
        self.assertNotEqual(results[0].code, 0)

    def test_commands_at_the_same_time_share_nothing(self):
        gitfixture.fake_git(self, "printf '%s\\n' \"$@\"; sleep 0.02; echo done")
        def work(number):
            return {(number, turn): gitx.run(["x", f"{number}-{turn}"], timeout=30).out.decode().split()[-2:]
                    for turn in range(6)}
        with ThreadPoolExecutor(8) as pool:
            answers = {}
            for found in pool.map(work, range(8)):
                answers.update(found)
        self.assertEqual(answers, {(n, t): [f"{n}-{t}", "done"] for n in range(8) for t in range(6)})
        self.assertEqual(gitx._LIVE, set())

    def test_every_way_a_command_can_end_leaves_no_descriptor_and_no_process(self):
        started, real = [], subprocess.Popen

        def watch(*args, **how):
            process = real(*args, **how)
            started.append(process)
            return process

        def refuse(text):
            raise KeyboardInterrupt()
        before = set(os.listdir("/dev/fd"))
        with mock.patch.object(gitx.subprocess, "Popen", watch):
            gitfixture.fake_git(self, "echo out; echo err >&2")
            gitx.run(["x"])
            gitx.run(["x"], input=b"in")
            gitfixture.fake_git(self, "echo out; exit 4")
            gitx.run(["x"])
            gitfixture.fake_git(self, "sleep 60")
            gitx.run(["x"], timeout=0.2)
            gitx.stream(["x"], stall=0.2, cap=5)
            gitfixture.fake_git(self, "head -c 3000000 /dev/zero")
            with self.assertRaises(gitx.OutputLimit):
                gitx.run(["x"], limit=1000)
            gitfixture.fake_git(self, "echo go >&2; sleep 60")
            with self.assertRaises(KeyboardInterrupt):
                gitx.stream(["x"], stall=30, cap=60, on_stderr=refuse)
        self.assertEqual(len(started), 7)
        self.assertTrue(all(process.returncode is not None for process in started), "every process was waited for")
        self.assertEqual(set(os.listdir("/dev/fd")) - before, set())
        self.assertEqual(gitx._LIVE, set())

    def test_an_interrupt_while_waiting_kills_the_command(self):
        pids = self.scratch / "pid"
        gitfixture.fake_git(self, f"sleep 60 &\necho $! > '{pids}'\necho go >&2\nwait")

        def interrupt(text):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            gitx.stream(["x"], stall=30, cap=60, on_stderr=interrupt)
        self.assertFalse(self.alive(int(pids.read_text())))

    def test_an_argument_that_is_not_text_or_holds_a_nul_is_refused_before_anything_runs(self):
        gitfixture.fake_git(self, "true")
        with mock.patch.object(gitx.subprocess, "Popen", side_effect=AssertionError("ran")):
            for bad in (["a", 1], ["a\0b"], [None], [b"bytes"]):
                with self.assertRaises(TypeError, msg=repr(bad)):
                    gitx.run(bad)

    def test_the_command_has_no_input_so_a_question_gets_the_end_of_the_file(self):
        gitfixture.fake_git(self, "cat; echo done")
        self.assertEqual(gitx.run(["x"], timeout=10).out, b"done\n")

    def test_input_is_given_and_a_large_one_with_a_large_output_does_not_block(self):
        gitfixture.fake_git(self, "cat")
        data = gitfixture.filler(3 * 1024 * 1024)
        result = gitx.run(["x"], input=data, timeout=30)
        self.assertEqual((result.code, len(result.out), result.out == data), (0, len(data), True))
        self.assertEqual(gitx.run(["x"], input="text \u00e9", timeout=10).out.decode(), "text \u00e9")

    def test_the_exit_code_the_output_and_the_errors_are_answered(self):
        gitfixture.fake_git(self, "echo out; echo error line >&2; exit 3")
        self.assertEqual(gitx.run(["x"]), gitx.Result(3, b"out\n", "error line\n"))

    def test_the_command_runs_in_the_environment_of_environment_plus_what_is_asked(self):
        gitfixture.fake_git(self, "env")
        with mock.patch.dict(os.environ, {"GIT_DIR": "/evil", "GIT_ASKPASS": "evil", "SECRET_TOKEN": "t"}):
            lines = gitx.run(["x"], env={"GIT_INDEX_FILE": "/tmp/index"}).out.decode().splitlines()
        self.assertIn("GIT_INDEX_FILE=/tmp/index", lines)
        self.assertIn("GIT_ASKPASS=", lines)
        self.assertIn("GIT_TERMINAL_PROMPT=0", lines)
        self.assertNotIn("GIT_DIR=/evil", lines)
        self.assertFalse([line for line in lines if line.startswith("SECRET_TOKEN")])

    def test_without_a_folder_git_runs_in_a_private_folder_outside_any_repository(self):
        gitfixture.fake_git(self, "pwd -P; echo \"CEILING=$GIT_CEILING_DIRECTORIES\"")
        where, ceiling = gitx.run(["x"]).out.decode().splitlines()
        folder = Path(where)
        self.assertTrue(folder.name.startswith("gravitee-lab-git-"), where)
        self.assertEqual(ceiling, f"CEILING={folder.parent}")
        self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)
        self.assertEqual(gitx.run(["x"]).out.decode().splitlines()[0], where, "one folder for the run")
        self.assertNotIn("lab-ui", where)

    def test_with_a_folder_git_runs_there_and_no_ceiling_is_set(self):
        gitfixture.fake_git(self, "pwd -P; echo \"CEILING=$GIT_CEILING_DIRECTORIES\"")
        self.assertEqual(gitx.run(["x"], cwd=self.scratch).out.decode().splitlines(), [str(self.scratch), "CEILING="])

    def test_a_command_that_outlives_its_time_is_killed_and_answers_a_timeout(self):
        self.warmed("echo partial; echo warning >&2; sleep 60")
        started = time.monotonic()
        result = gitx.run(["x"], timeout=2)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((result.code, result.out), (124, b"partial\n"), "what it printed is kept")
        self.assertTrue(result.err.startswith("warning\n"))
        self.assertIn(gitx.TIMEOUT_MARK, result.err)
        self.assertIn("no answer within 2 s", result.err)
        self.assertEqual(gitx.classify(result.err, gitx.parse_address(GITHUB)).code, "timeout")

    def killed_by_the_caller(self, body):
        """Runs a script that starts a child and says `ready`; the caller raises at once. Answers the pid of the child."""
        pids = self.scratch / "pid"
        gitfixture.fake_git(self, f"sleep 60 &\necho $! > '{pids}'\n{body}")

        def refuse(text):
            raise RuntimeError("enough")
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            gitx.stream(["x"], stall=30, cap=60, on_stderr=refuse)
        self.assertLess(time.monotonic() - started, 8)
        return int(pids.read_text())

    def test_a_command_that_is_stopped_is_killed_with_what_it_started(self):
        self.assertFalse(self.alive(self.killed_by_the_caller("echo ready >&2\nwait")), "the child was left running")

    def test_a_command_that_ignores_sigterm_is_killed_after_the_grace(self):
        pid = self.killed_by_the_caller("trap '' TERM\necho ready >&2\nwait")
        self.assertFalse(self.alive(pid))

    def test_what_a_command_left_behind_is_killed_when_it_is(self):
        """The command ended, a child it started still holds the output: the group is ended with it."""
        self.assertFalse(self.alive(self.killed_by_the_caller("echo ready >&2")))

    def test_a_command_that_ended_is_not_waited_for_because_a_child_holds_its_output(self):
        """An ssh helper that outlives git holds the pipes: git succeeded, and that is the answer, at once."""
        pids = self.scratch / "pid"
        self.warmed(f"sleep 60 &\necho $! > '{pids}'\necho done")
        started = time.monotonic()
        result = gitx.run(["x"], timeout=60)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual((result.code, result.out), (0, b"done\n"))
        self.assertFalse(self.alive(int(pids.read_text())), "what it left behind is killed")

    def test_the_exit_code_of_a_command_that_failed_is_kept_when_a_child_holds_its_output(self):
        self.warmed("sleep 60 &\necho 'fatal: not good' >&2\nexit 7")
        result = gitx.run(["x"], timeout=60)
        self.assertEqual((result.code, result.err), (7, "fatal: not good\n"))
        done = gitx.stream(["x"], stall=30, cap=60)
        self.assertEqual((done.code, done.err), (7, "fatal: not good\n"))

    def test_a_command_that_is_still_running_with_its_output_held_is_cut_at_its_time(self):
        self.warmed("sleep 60 &\necho started\nwait")
        started = time.monotonic()
        result = gitx.run(["x"], timeout=2)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((result.code, result.out), (124, b"started\n"))

    def test_the_output_a_command_wrote_before_it_ended_is_all_read_though_a_child_holds_the_pipes(self):
        self.warmed("sleep 60 &\nhead -c 200000 /dev/zero | tr '\\0' o\nhead -c 100000 /dev/zero | tr '\\0' e >&2")
        result = gitx.run(["x"], timeout=60)
        self.assertEqual((result.code, len(result.out), len(result.err)), (0, 200000, 100000))

    def test_a_fetch_that_sends_nothing_is_killed_at_the_stall(self):
        self.warmed("echo started >&2; sleep 60")
        started = time.monotonic()
        result = gitx.stream(["x"], stall=2, cap=60)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((result.code, result.err.splitlines()[0]), (124, "started"))
        self.assertIn("no byte for 2 s", result.err)

    def test_a_fetch_that_keeps_sending_is_not_a_stall_but_is_cut_at_the_cap(self):
        self.warmed("i=0; while [ $i -lt 10 ]; do printf x >&2; sleep 0.1; i=$((i+1)); done")
        done = gitx.stream(["x"], stall=2, cap=60)
        self.assertEqual((done.code, done.err), (0, "x" * 10), "a second long, a gap of a tenth: not a stall")
        capped = gitx.stream(["x"], stall=2, cap=0.5)
        self.assertEqual(capped.code, 124)
        self.assertIn("still running after 0.5 s", capped.err)

    def test_the_errors_are_given_to_the_caller_as_they_arrive(self):
        gitfixture.fake_git(self, "printf 'one\\r' >&2; sleep 0.2; printf 'two\\n' >&2; sleep 0.2; printf 'three' >&2")
        seen = []
        gitx.stream(["x"], stall=5, cap=30, on_stderr=seen.append)
        self.assertEqual("".join(seen), "one\rtwo\nthree")
        self.assertGreaterEqual(len(seen), 2)

    def test_output_over_the_limit_stops_the_command(self):
        gitfixture.fake_git(self, "head -c 5000000 /dev/zero")
        with self.assertRaises(gitx.OutputLimit):
            gitx.run(["x"], limit=100000)
        self.assertEqual(len(gitx.run(["x"], limit=6000000).out), 5000000)

    def test_only_the_end_of_the_errors_is_kept(self):
        gitfixture.fake_git(self, "head -c 400000 /dev/zero | tr '\\0' e >&2; echo fatal-end >&2; exit 3")
        result = gitx.run(["x"])
        self.assertEqual(result.code, 3)
        self.assertLessEqual(len(result.err), gitx.STDERR_TAIL)
        self.assertTrue(result.err.endswith("eeefatal-end\n"))

    def test_undecodable_output_is_replaced_not_raised(self):
        gitfixture.fake_git(self, "printf '\\377\\376 bad' >&2")
        self.assertIn("\ufffd", gitx.run(["x"]).err)

    def test_without_git_a_command_is_git_missing_and_nothing_runs(self):
        self.start(mock.patch.dict(os.environ, {"PATH": str(self.scratch)}))
        self.assertIsNone(gitx.executable())
        self.assertIsNone(gitx.version())
        with mock.patch.object(gitx.subprocess, "Popen", side_effect=AssertionError("ran")):
            for call in (lambda: gitx.run(["status"]), lambda: gitx.stream(["fetch"]), gitx.require):
                with self.assertRaises(gitx.CatalogError) as raised:
                    call()
                self.assertEqual((raised.exception.code, raised.exception.status), ("git_missing", 400))
                self.assertEqual(raised.exception.message, "Git is not installed. Install it, then add the catalog again.")


# The programs the next class runs in a Python of their own: a Lab UI that exits while git runs. `pids` (argv[1]) is the
# file the fake git writes its process id to, one line for each git it starts
LAB = """
import os, signal, sys, threading, time
from lab_ui.catalogs import gitx

def started():
    end = time.monotonic() + 20
    while not os.path.exists(sys.argv[1]) and time.monotonic() < end:
        time.sleep(0.02)
    time.sleep(0.2)
    print("ready", flush=True)

def on_signals():
    # What the owner of the signals does (compose.stop_on_signals): the handler ends the commands, then lets the signal kill
    def ending(number, frame):
        gitx.stop_running()
        signal.signal(number, signal.SIG_DFL)
        signal.raise_signal(number)
    signal.signal(signal.SIGTERM, ending)
    signal.signal(signal.SIGHUP, ending)
"""
SIGNALED = LAB + """
on_signals()
threading.Thread(target=gitx.run, args=(["x"],), kwargs={"timeout": 120}, daemon=True).start()
started()
time.sleep(60)
"""
LEAVING = LAB + """
threading.Thread(target=gitx.run, args=(["x"],), kwargs={"timeout": 120}, daemon=True).start()
started()
"""
POOLED = LAB + """
from concurrent.futures import ThreadPoolExecutor
pool = ThreadPoolExecutor(1)
for _ in range(3):
    pool.submit(gitx.run, ["x"], None, 120)
started()
"""


class Ending(Case):
    """What the Lab UI does as it exits: the commands it runs end with it, and none is started after."""

    LAB_UI = Path(__file__).resolve().parents[2]

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.scratch, True))
        self.pids = self.scratch / "pids"
        # Long enough that a Lab UI that does not end it waits for it (the test then fails), short enough to end alone
        gitfixture.fake_git(self, f"echo $$ >> '{self.pids}'\nexec sleep 40")
        self.patch(gitx, "_ending", False)   # stop_running sets it for good: the test puts it back
        self.patch(gitx, "KILL_GRACE", 0.3)

    def gone(self, pid, within=5.0):
        end = time.monotonic() + within
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def lab(self, program):
        """A Lab UI that runs `program` (a Python of its own with a git in a command): the process, once it says it is ready."""
        process = subprocess.Popen([sys.executable, "-c", program, str(self.pids)], cwd=self.LAB_UI, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(process.communicate)
        self.addCleanup(process.kill)
        self.assertEqual(process.stdout.readline().strip(), b"ready", "the Lab UI did not start a command")
        self.addCleanup(lambda: [os.kill(int(pid), signal.SIGKILL) for pid in self.running()])
        return process

    def running(self):
        found = []
        for line in self.pids.read_text().split() if self.pids.exists() else []:
            try:
                os.kill(int(line), 0)
                found.append(int(line))
            except ProcessLookupError:
                pass
        return found

    def test_a_terminated_or_hung_up_lab_ui_ends_its_commands_before_it_dies(self):
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name):
                if self.pids.exists():
                    self.pids.unlink()
                process = self.lab(SIGNALED)
                (pid,) = [int(line) for line in self.pids.read_text().split()]
                process.send_signal(number)
                self.assertEqual(process.wait(15), -number, "it died of the signal, after it ended the command")
                self.assertTrue(self.gone(pid), "the command was left running")

    def test_a_lab_ui_that_leaves_while_a_command_runs_ends_it(self):
        process = self.lab(LEAVING)
        (pid,) = [int(line) for line in self.pids.read_text().split()]
        started = time.monotonic()
        self.assertEqual(process.wait(15), 0)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(self.gone(pid), "the command was left running")

    def test_a_lab_ui_that_leaves_while_a_pool_of_threads_runs_commands_does_not_wait_for_them(self):
        """Python waits for the threads of a pool before it calls the functions of atexit: the commands have to end
        before that wait, and the ones still queued must not start."""
        process = self.lab(POOLED)
        (pid,) = [int(line) for line in self.pids.read_text().split()]
        started = time.monotonic()
        self.assertEqual(process.wait(20), 0, "it waited for a command that runs for 40 s")
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue(self.gone(pid))
        self.assertEqual(len(self.pids.read_text().split()), 1, "a command that was queued was started after the end began")

    def test_nothing_is_run_once_the_lab_ui_is_ending(self):
        gitx.stop_running()
        with mock.patch.object(gitx.subprocess, "Popen", side_effect=AssertionError("ran")):
            for call in (lambda: gitx.run(["status"]), lambda: gitx.stream(["fetch"]),
                         lambda: gitx._execute(["git"], None, {}, None, 5, None, None, 100, None)):
                self.assertEqual(call(), gitx.Result(130, b"", "git was not run: the Lab UI is ending"))
        self.assertFalse(self.pids.exists())

    def test_a_command_that_started_as_the_end_began_is_ended_too(self):
        """The end is announced between the start of the process and its entry in the list of those that run: nobody
        saw it, so the command sees the announcement itself."""
        real, started = subprocess.Popen, []

        def announced(*args, **how):
            process = real(*args, **how)
            started.append(process)
            gitx._ending = True
            return process
        with mock.patch.object(gitx.subprocess, "Popen", announced):
            result = gitx.run(["x"], timeout=60)
        self.assertEqual(result.code, 130)
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].returncode, "the command was ended and waited for")
        self.assertTrue(self.gone(started[0].pid), "the command was left running")
        self.assertEqual(gitx._LIVE, set())

    def test_stop_running_may_be_called_by_a_signal_handler_of_the_thread_that_is_starting_a_command(self):
        """A handler runs on the thread it interrupted, which may hold the lock of the list of commands."""
        done = []

        def interrupted():
            with gitx._LIVE_LOCK:
                gitx.stop_running()
                gitx.stop_running()
            done.append(True)
        thread = threading.Thread(target=interrupted, daemon=True)
        thread.start()
        thread.join(10)
        self.assertEqual(done, [True], "stop_running waited for a lock its own thread holds")

    def test_the_commands_that_run_are_ended_with_what_they_started_and_the_name_it_had_still_works(self):
        marker = self.scratch / "child"
        gitfixture.fake_git(self, f"sleep 60 &\necho $! > '{marker}'\necho ready >&2\nwait")
        ready, results = threading.Event(), []
        thread = threading.Thread(target=lambda: results.append(gitx.stream(["x"], stall=30, cap=60,
                                                                           on_stderr=lambda text: ready.set())))
        thread.start()
        self.assertTrue(ready.wait(10))
        gitx.stop_running()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.gone(int(marker.read_text())))
        self.assertNotEqual(results[0].code, 0)


class Version(Case):

    def test_the_version_is_three_numbers(self):
        for said, expected in (("git version 2.43.2", (2, 43, 2)), ("git version 2.39.5 (Apple Git-154)", (2, 39, 5)),
                               ("git version 2.34.1.windows.1", (2, 34, 1)), ("git version 2.25", (2, 25, 0)),
                               ("git version 2.9.5", (2, 9, 5))):
            gitfixture.fake_git(self, f"echo '{said}'")
            self.assertEqual(gitx.version(), expected, said)

    def test_a_git_that_does_not_answer_a_version_is_no_git(self):
        gitfixture.fake_git(self, "echo 'not a version'")
        self.assertIsNone(gitx.version())
        gitfixture.fake_git(self, "echo 'git version 2.40.0'; exit 1")
        self.assertIsNone(gitx.version())

    def test_require_is_the_version_or_git_old(self):
        gitfixture.fake_git(self, "echo 'git version 2.25.0'")
        self.assertEqual(gitx.require(), (2, 25, 0))
        gitfixture.fake_git(self, "echo 'git version 2.20.1'")
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.require()
        self.assertEqual((raised.exception.code, raised.exception.status), ("git_old", 400))
        self.assertEqual(raised.exception.message,
                         "Git 2.20.1 is too old. Install Git 2.25 or later, then add the catalog again.")
        self.assertEqual(gitx.require((2, 20)), (2, 20, 1))

    def mac(self, tools_return, shim=True):
        """A Mac whose git is the shim: `xcode-select -p` answers `tools_return`. The shim is a script that records
        that it was run."""
        ran = self.scratch / "ran"
        script = gitfixture.fake_git(self, f"touch '{ran}'; echo 'git version 2.39.5 (Apple Git-154)'")
        self.patch(gitx, "PLATFORM", "darwin")
        self.patch(gitx, "SHIM", str(script) if shim else "/usr/bin/git")
        asked = []

        def tools(argv, **how):
            asked.append(argv)
            return subprocess.CompletedProcess(argv, tools_return, b"", b"")
        self.patch(gitx.subprocess, "run", tools)
        return ran, asked

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.scratch, True))
        self.patch(gitx, "_TOOLS", {"at": None, "ok": False})   # what an earlier test learned of the tools is not known here

    def test_on_a_mac_without_the_command_line_tools_the_shim_is_never_run(self):
        ran, asked = self.mac(tools_return=2)
        self.assertIsNone(gitx.version())
        self.assertIsNone(gitx.executable())
        self.assertEqual(asked[0], ["xcode-select", "-p"])
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.run(["ls-remote", "--", "https://github.com/a/b.git"])
        self.assertEqual(raised.exception.code, "git_missing")
        with self.assertRaises(gitx.CatalogError):
            gitx.stream(["fetch"])
        self.assertFalse(ran.exists(), "the shim opens an installer: it must not be executed")

    def test_on_a_mac_with_the_tools_the_shim_is_git(self):
        ran, asked = self.mac(tools_return=0)
        self.assertEqual(gitx.version(), (2, 39, 5))
        self.assertTrue(ran.exists())
        self.assertEqual(asked[0], ["xcode-select", "-p"])

    def test_the_tools_are_asked_for_the_shim_only(self):
        ran, asked = self.mac(tools_return=2, shim=False)   # the git found is not /usr/bin/git: Homebrew's, say
        self.assertEqual(gitx.version(), (2, 39, 5))
        self.assertEqual(asked, [])
        self.assertTrue(ran.exists())

    def test_a_link_to_the_shim_is_the_shim(self):
        shim = self.scratch / "shim"
        shim.write_text("#!/bin/sh\ntouch ran\n")
        shim.chmod(0o755)
        link = self.scratch / "bin" / "git"
        link.parent.mkdir()
        os.symlink(shim, link)
        self.start(mock.patch.dict(os.environ, {"PATH": str(link.parent)}))
        self.patch(gitx, "PLATFORM", "darwin")
        self.patch(gitx, "SHIM", str(shim))
        self.patch(gitx.subprocess, "run", lambda argv, **how: subprocess.CompletedProcess(argv, 2, b"", b""))
        self.assertIsNone(gitx.executable())

    def test_other_systems_never_ask_for_the_tools(self):
        ran, asked = self.mac(tools_return=2)
        self.patch(gitx, "PLATFORM", "linux")
        self.assertEqual(gitx.version(), (2, 39, 5))
        self.assertEqual(asked, [])

    def test_xcode_select_that_cannot_run_is_no_git(self):
        ran, asked = self.mac(tools_return=0)

        def broken(argv, **how):
            raise FileNotFoundError("xcode-select")
        self.patch(gitx.subprocess, "run", broken)
        self.assertIsNone(gitx.version())
        self.assertFalse(ran.exists())

        def hangs(argv, **how):
            raise subprocess.TimeoutExpired(argv, 10)
        self.patch(gitx.subprocess, "run", hangs)
        gitx._TOOLS.update({"at": None, "ok": False})
        self.assertIsNone(gitx.version())
        self.assertFalse(ran.exists())

    def test_the_tools_are_asked_once_in_a_while_not_for_every_command(self):
        ran, asked = self.mac(tools_return=0)
        for _ in range(5):
            self.assertIsNotNone(gitx.executable())
        self.assertEqual(len(asked), 1)
        gitx._TOOLS["at"] -= gitx.TOOLS_AGE + 1   # the answer is old: asked again
        self.assertIsNotNone(gitx.executable())
        self.assertEqual(len(asked), 2)

    def test_the_tools_that_come_after_a_first_no_are_found_within_the_age_of_an_answer(self):
        ran, asked = self.mac(tools_return=2)
        self.assertIsNone(gitx.executable())
        self.assertIsNone(gitx.executable())
        self.assertEqual(len(asked), 1, "a no is kept as long as a yes")
        self.patch(gitx.subprocess, "run", lambda argv, **how: subprocess.CompletedProcess(argv, 0, b"", b""))
        gitx._TOOLS["at"] -= gitx.TOOLS_AGE + 1
        self.assertIsNotNone(gitx.executable())
        self.assertFalse(ran.exists(), "asking is not running the shim")


class Redaction(Case):

    def test_the_user_and_the_password_of_an_address_are_removed(self):
        self.assertEqual(gitx.redact("fatal: unable to access 'https://user:tok@github.com/a/b.git/': failed"),
                         "fatal: unable to access 'https://github.com/a/b.git/': failed")
        self.assertEqual(gitx.redact("https://tok@github.com/x and ssh://git@host/y"), "https://github.com/x and ssh://host/y")
        self.assertEqual(gitx.redact("remote: see https://x-access-token:ghp_abc123@github.com/a/b"),
                         "remote: see https://github.com/a/b")

    def test_what_is_not_an_address_is_left_alone_and_redaction_is_idempotent(self):
        for text in ("", "plain text", "git@github.com:a/b.git", "a@b.test", "https://github.com/a/b"):
            self.assertEqual(gitx.redact(text), text)
        once = gitx.redact("https://u:p@h.test/x https://u@h.test/y")
        self.assertEqual(gitx.redact(once), once)
        self.assertEqual(gitx.redact(None), "")


GITHUB_ADDRESS = gitx.parse_address(GITHUB)
SSH_ADDRESS = gitx.parse_address("git@git.example.test:team/hub.git")
OTHER_ADDRESS = gitx.parse_address("https://git.example.test/team/hub.git")

# The end of every failure of an ssh connection, the network's included
TAIL = "fatal: Could not read from remote repository.\n\nPlease make sure you have the correct access rights\nand the repository exists."
REACH = "Cannot reach git.example.test. Check your connection, then try again."

# (what git or ssh said, the address, the code, the status, the line)
GOLDEN = [
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", GITHUB_ADDRESS, "no_access", 400,
     "No access to acme/lab-hub. Run gh auth login in a terminal, then try again."),
    ("fatal: could not read Username for 'https://git.example.test': No such device or address", OTHER_ADDRESS,
     "no_access", 400, "No access to team/hub. Sign in with Git on this machine, then try again."),
    ("remote: Invalid username or password.\nfatal: Authentication failed for 'https://github.com/acme/lab-hub.git/'",
     GITHUB_ADDRESS, "no_access", 400, "No access to acme/lab-hub. Run gh auth login in a terminal, then try again."),
    ("fatal: unable to access 'https://github.com/acme/lab-hub.git/': The requested URL returned error: 403",
     GITHUB_ADDRESS, "no_access", 400, "No access to acme/lab-hub. Run gh auth login in a terminal, then try again."),
    ("fatal: unable to access 'https://git.example.test/team/hub.git/': The requested URL returned error: 401",
     OTHER_ADDRESS, "no_access", 400, "No access to team/hub. Sign in with Git on this machine, then try again."),
    ("remote: HTTP Basic: Access denied\nfatal: Authentication failed for 'https://git.example.test/team/hub.git/'",
     OTHER_ADDRESS, "no_access", 400, "No access to team/hub. Sign in with Git on this machine, then try again."),
    ("remote: Permission to acme/lab-hub.git denied to someone.\nfatal: unable to access 'https://github.com/acme/lab-hub.git/'",
     GITHUB_ADDRESS, "no_access", 400, "No access to acme/lab-hub. Run gh auth login in a terminal, then try again."),
    ("remote: Repository not found.\nfatal: repository 'https://github.com/acme/lab-hub.git/' not found", GITHUB_ADDRESS,
     "not_found", 400, "acme/lab-hub was not found. Check the address, or sign in with Git if it is private."),
    ("ERROR: Repository not found.\nfatal: Could not read from remote repository.", SSH_ADDRESS, "not_found", 400,
     "team/hub was not found. Check the address, or sign in with Git if it is private."),
    ("fatal: unable to access 'https://git.example.test/team/hub.git/': The requested URL returned error: 404",
     OTHER_ADDRESS, "not_found", 400, "team/hub was not found. Check the address, or sign in with Git if it is private."),
    ("fatal: '/srv/git/hub.git' does not appear to be a git repository\nfatal: Could not read from remote repository.",
     SSH_ADDRESS, "not_found", 400, "team/hub was not found. Check the address, or sign in with Git if it is private."),
    ("git@git.example.test: Permission denied (publickey).\nfatal: Could not read from remote repository.", SSH_ADDRESS,
     "ssh_denied", 400, "Permission denied for git.example.test. Add your SSH key to git.example.test, then try again."),
    ("git@github.com: Permission denied (publickey,keyboard-interactive).", GITHUB_ADDRESS, "ssh_denied", 400,
     "Permission denied for github.com. Add your SSH key to github.com, then try again."),
    ("Host key verification failed.\nfatal: Could not read from remote repository.", SSH_ADDRESS, "host_key", 400,
     "git.example.test is not a known SSH host. Connect to it once from a terminal, then try again."),
    ("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\nHost key verification failed.", SSH_ADDRESS, "host_key", 400,
     "git.example.test is not a known SSH host. Connect to it once from a terminal, then try again."),
    ("fatal: unable to access 'https://github.com/acme/lab-hub.git/': Could not resolve host: github.com", GITHUB_ADDRESS,
     "unreachable", 502, "Cannot reach github.com. Check your connection, then try again."),
    ("ssh: Could not resolve hostname git.example.test: nodename nor servname provided, or not known\n"
     "fatal: Could not read from remote repository.", SSH_ADDRESS, "unreachable", 502,
     "Cannot reach git.example.test. Check your connection, then try again."),
    ("ssh: connect to host git.example.test port 22: Connection refused", SSH_ADDRESS, "unreachable", 502,
     "Cannot reach git.example.test. Check your connection, then try again."),
    ("fatal: unable to access 'https://git.example.test/team/hub.git/': Failed to connect to git.example.test port 443 "
     "after 3 ms: Couldn't connect to server", OTHER_ADDRESS, "unreachable", 502,
     "Cannot reach git.example.test. Check your connection, then try again."),
    ("ssh: connect to host git.example.test port 22: No route to host", SSH_ADDRESS, "unreachable", 502,
     "Cannot reach git.example.test. Check your connection, then try again."),
    ("fatal: unable to access 'https://github.com/a/b.git/': Network is unreachable", GITHUB_ADDRESS, "unreachable", 502,
     "Cannot reach github.com. Check your connection, then try again."),
    ("ssh: Could not resolve hostname git.example.test: nodename nor servname provided, or not known\n" + TAIL,
     SSH_ADDRESS, "unreachable", 502, REACH),
    ("ssh: Could not resolve hostname git.example.test: Name or service not known\n" + TAIL, SSH_ADDRESS, "unreachable", 502, REACH),
    ("ssh: connect to host git.example.test port 22: Connection refused\n" + TAIL, SSH_ADDRESS, "unreachable", 502, REACH),
    ("ssh: connect to host git.example.test port 22: No route to host\n" + TAIL, SSH_ADDRESS, "unreachable", 502, REACH),
    ("ssh: connect to host git.example.test port 22: Network is unreachable\n" + TAIL, SSH_ADDRESS, "unreachable", 502, REACH),
    ("Connection closed by 192.0.2.1 port 22\n" + TAIL, SSH_ADDRESS, "unreachable", 502, REACH),
    ("ssh: connect to host git.example.test port 22: Operation timed out\n" + TAIL, SSH_ADDRESS, "timeout", 502,
     "git.example.test did not answer in time. Try again."),
    ("git@git.example.test: Permission denied (publickey).\n" + TAIL, SSH_ADDRESS, "ssh_denied", 400,
     "Permission denied for git.example.test. Add your SSH key to git.example.test, then try again."),
    ("ERROR: Repository not found.\n" + TAIL, SSH_ADDRESS, "not_found", 400,
     "team/hub was not found. Check the address, or sign in with Git if it is private."),
    ("Host key verification failed.\n" + TAIL, SSH_ADDRESS, "host_key", 400,
     "git.example.test is not a known SSH host. Connect to it once from a terminal, then try again."),
    (TAIL, SSH_ADDRESS, "no_access", 400, "No access to team/hub. Sign in with Git on this machine, then try again."),
    ("fatal: the remote end hung up unexpectedly", GITHUB_ADDRESS, "unreachable", 502,
     "Cannot reach github.com. Check your connection, then try again."),
    ("fatal: unable to access 'https://github.com/a/b.git/': Failed to connect to github.com port 443 after 21050 ms: "
     "Connection timed out", GITHUB_ADDRESS, "timeout", 502, "github.com did not answer in time. Try again."),
    ("fatal: unable to access 'https://github.com/a/b.git/': Operation timed out after 30001 milliseconds with 0 bytes "
     "received", GITHUB_ADDRESS, "timeout", 502, "github.com did not answer in time. Try again."),
    ("ssh: connect to host git.example.test port 22: Operation timed out", SSH_ADDRESS, "timeout", 502,
     "git.example.test did not answer in time. Try again."),
    ("git timed out (no byte for 30 s)", OTHER_ADDRESS, "timeout", 502, "git.example.test did not answer in time. Try again."),
    ("fatal: unable to access 'https://git.example.test/team/hub.git/': SSL certificate problem: self-signed certificate "
     "in certificate chain", OTHER_ADDRESS, "certificate", 502,
     "The certificate of git.example.test was refused. A company proxy may be in the way."),
    ("fatal: unable to access 'https://git.example.test/x.git/': server certificate verification failed. CAfile: "
     "/etc/ssl/cert.pem CRLfile: none", OTHER_ADDRESS, "certificate", 502,
     "The certificate of git.example.test was refused. A company proxy may be in the way."),
    ("error: object 3b18e5d6: badTimezone: invalid author/committer line - bad time zone\n"
     "fatal: fsck error in packed object", GITHUB_ADDRESS, "fsck", 400, "This repository is damaged. Ask its owners to check it."),
    ("error: object b029c74: hasDotgit: contains '.git'\nfatal: fsck error in packed object\nfatal: index-pack failed",
     GITHUB_ADDRESS, "fsck", 400, "This repository is damaged. Ask its owners to check it."),
    ("fatal: couldn't find remote ref refs/heads/nope", GITHUB_ADDRESS, "branch_missing", 400,
     "acme/lab-hub has no branch nope. Check the name, or leave it empty for the default."),
    ("fatal: couldn't find remote ref release/1.x", OTHER_ADDRESS, "branch_missing", 400,
     "team/hub has no branch release/1.x. Check the name, or leave it empty for the default."),
    ("fatal: transport 'file' not allowed", GITHUB_ADDRESS, "bad_address", 400, BAD_ADDRESS),
    ("something git never says", GITHUB_ADDRESS, "git_failed", 502,
     "Git could not read acme/lab-hub. Check the address and your connection, then try again."),
    ("", GITHUB_ADDRESS, "git_failed", 502, "Git could not read acme/lab-hub. Check the address and your connection, then try again."),
]


class Classification(Case):

    def test_what_git_says_is_the_line_of_section_11(self):
        for stderr, address, code, status, line in GOLDEN:
            with self.subTest(stderr[:60]):
                error = gitx.classify(stderr, address)
                self.assertEqual((error.code, error.status, error.message), (code, status, line))

    def test_the_words_of_git_never_reach_the_line_and_go_to_the_detail_without_credentials(self):
        said = "fatal: unable to access 'https://user:s3cr3t@github.com/a/b.git/': Could not resolve host: github.com"
        error = gitx.classify(said, GITHUB_ADDRESS)
        self.assertNotIn("fatal", error.message)
        self.assertNotIn("s3cr3t", error.message + error.detail["output"])
        self.assertIn("Could not resolve host", error.detail["output"])

    def test_a_long_output_is_cut_in_the_detail(self):
        error = gitx.classify("x" * 100000 + "\nfatal: could not read Username", GITHUB_ADDRESS)
        self.assertLessEqual(len(error.detail["output"]), 2000)
        self.assertTrue(error.detail["output"].endswith("could not read Username"))

    def test_a_timeout_wins_over_what_the_command_printed_before_it_was_killed(self):
        error = gitx.classify("fatal: Could not resolve host: x\ngit timed out (no byte for 30 s)", GITHUB_ADDRESS)
        self.assertEqual(error.code, "timeout")

    def test_an_error_without_an_address_still_has_a_line(self):
        error = gitx.classify("fatal: could not read Username for 'https://x'", None)
        self.assertEqual(error.message, "No access to this repository. Sign in with Git on this machine, then try again.")
        self.assertEqual(gitx.classify("Could not resolve host: x", None).message,
                         "Cannot reach the host. Check your connection, then try again.")

    def test_a_folder_is_named_by_its_display(self):
        folder = gitx.Address("folder", "", "/h/hub", "~/hub", "", "")
        self.assertEqual(gitx.classify("fatal: bad object", folder).detail["output"], "fatal: bad object")
        self.assertEqual(gitx.fail("not_catalog", folder).message, "~/hub has no catalog.yml, so it is not a catalog.")

    def test_every_code_gitx_can_raise_has_the_status_and_line_of_section_11(self):
        expected = {
            "git_missing": (400, "Git is not installed. Install it, then add the catalog again."),
            "git_old": (400, "Git  is too old. Install Git 2.25 or later, then add the catalog again."),
            "bad_address": (400, BAD_ADDRESS),
            "bad_address_secret": (400, "Remove the user name and token from the address. Git uses the sign-in of this machine."),
            "no_access": (400, "No access to acme/lab-hub. Run gh auth login in a terminal, then try again."),
            "not_found": (400, "acme/lab-hub was not found. Check the address, or sign in with Git if it is private."),
            "ssh_denied": (400, "Permission denied for github.com. Add your SSH key to github.com, then try again."),
            "host_key": (400, "github.com is not a known SSH host. Connect to it once from a terminal, then try again."),
            "unreachable": (502, "Cannot reach github.com. Check your connection, then try again."),
            "timeout": (502, "github.com did not answer in time. Try again."),
            "certificate": (502, "The certificate of github.com was refused. A company proxy may be in the way."),
            "too_large": (400, "This catalog is larger than the Lab accepts. Ask its owners to make it smaller."),
            "fsck": (400, "This repository is damaged. Ask its owners to check it."),
            "not_catalog": (400, "acme/lab-hub has no catalog.yml, so it is not a catalog."),
            "branch_missing": (400, "acme/lab-hub has no branch . Check the name, or leave it empty for the default."),
            "folder_missing": (400, "No folder at ."),
            "folder_not_catalog": (400, " has no catalog.yml, so it is not a catalog."),
            "folder_not_shared": (400, "Docker cannot share this folder. Move it under your home folder."),
            "git_failed": (502, "Git could not read acme/lab-hub. Check the address and your connection, then try again."),
        }
        self.assertEqual(set(gitx.MESSAGES), set(expected))
        for code, (status, line) in expected.items():
            error = gitx.fail(code, GITHUB_ADDRESS)
            self.assertEqual((error.code, error.status, error.message), (code, status, line), code)

    def test_a_line_states_the_cause_and_ends_with_the_next_step(self):
        for code, (_, line) in gitx.MESSAGES.items():
            self.assertTrue(line.endswith("."), code)
            self.assertNotIn("\u00b7", line)
            self.assertNotIn("...", line)

    def test_the_error_is_a_catalog_error_with_its_facts(self):
        error = gitx.fail("git_old", have="2.20.1")
        self.assertIsInstance(error, gitx.CatalogError)
        self.assertEqual((error.code, error.status, error.detail), ("git_old", 400, {}))
        self.assertEqual(str(error), error.message)
        self.assertEqual(gitx.fail("fsck", output="why").detail, {"output": "why"})


class Remote(Case):
    """ls-remote and the push probe against bare repositories on this machine."""

    def setUp(self):
        gitfixture.require()
        gitfixture.isolate(self)
        self.folder()
        self.repo = gitfixture.make_repo(self.root / "remotes" / "hub.git", gitfixture.catalog_files())
        self.address = gitx.parse_address(gitfixture.url(self.repo))

    def test_a_file_address_names_the_repository_by_its_path(self):
        self.assertEqual(self.address.kind, "git")
        self.assertEqual(self.address.host, "localhost")
        self.assertEqual(self.address.repo, "hub")
        self.assertEqual(self.address.url, gitfixture.url(self.repo))

    def test_the_default_branch_and_its_head(self):
        self.assertEqual(gitx.ls_remote(self.address), {"branch": "main", "head": gitfixture.tip(self.repo)})

    def test_the_default_branch_is_the_one_head_points_at_not_a_name_the_lab_knows(self):
        other = gitfixture.make_repo(self.root / "remotes" / "other.git", gitfixture.catalog_files(), branch="trunk/x")
        found = gitx.ls_remote(gitx.parse_address(gitfixture.url(other)))
        self.assertEqual(found, {"branch": "trunk/x", "head": gitfixture.tip(other)})

    def test_a_named_branch(self):
        gitfixture.git("update-ref", "refs/heads/release/1.x", gitfixture.tip(self.repo), repo=self.repo)
        gitfixture.commit(self.repo, {"README.md": "newer\n"})
        self.assertEqual(gitx.ls_remote(self.address, "release/1.x"),
                         {"branch": "release/1.x", "head": gitfixture.tip(self.repo, "release/1.x")})
        self.assertNotEqual(gitx.ls_remote(self.address, "release/1.x")["head"], gitx.ls_remote(self.address)["head"])

    def test_a_branch_the_remote_lacks_is_branch_missing(self):
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(self.address, "nope")
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("branch_missing", "hub has no branch nope. Check the name, or leave it empty for the default."))

    def test_a_branch_that_is_not_a_name_is_refused_before_git_runs(self):
        with mock.patch.object(gitx, "run", side_effect=AssertionError("ran")):
            for name in ("--upload-pack=x", "a..b", "a b", "\t", 7, ["main"]):
                with self.assertRaises(gitx.CatalogError, msg=name) as raised:
                    gitx.ls_remote(self.address, name)
                self.assertEqual(raised.exception.code, "branch_missing")

    def test_no_branch_is_none_an_empty_text_or_spaces_and_the_default_branch_is_asked(self):
        """The Branch field of the page is optional: what it sends when left empty is not a branch named ''."""
        default = gitx.ls_remote(self.address)
        for name in ("", " ", "   "):
            self.assertEqual(gitx.ls_remote(self.address, name), default, repr(name))
        self.assertEqual(gitx.ls_remote(self.address, " main "), default, "spaces around a name are not part of it")
        for name in (None, "", "  "):
            self.assertIsNone(gitx.asked_branch(name), repr(name))
        self.assertEqual(gitx.asked_branch(" release/1.x "), "release/1.x")
        for name in ("-x", "a b", "a..b", 0, False, 7, b"main", ["main"]):
            with self.assertRaises(gitx.CatalogError, msg=repr(name)) as raised:
                gitx.asked_branch(name)
            self.assertEqual(raised.exception.code, "branch_missing")

    def test_an_empty_repository_is_not_a_catalog(self):
        gitfixture.git("init", "--bare", "--quiet", "--template=", "--", str(self.root / "remotes" / "empty.git"))
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(gitx.parse_address(gitfixture.url(self.root / "remotes" / "empty.git")))
        self.assertEqual(raised.exception.code, "not_catalog")

    def test_a_repository_that_is_not_there_is_not_found(self):
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(gitx.parse_address("file://" + str(self.root / "remotes" / "none.git")))
        self.assertEqual(raised.exception.code, "not_found")

    def test_a_protocol_git_may_not_use_is_refused_by_git_itself_as_well(self):
        self.patch(gitx, "PROTOCOLS", "https:ssh")
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(self.address)
        self.assertEqual(raised.exception.code, "bad_address")

    def test_a_server_that_answers_no_branch_for_head_is_asked_which_one_it_is(self):
        """A server without symref: the branch is the one at HEAD's commit, `main` first."""
        head = gitfixture.tip(self.repo)
        gitfixture.git("update-ref", "refs/heads/zeta", head, repo=self.repo)
        gitfixture.git("update-ref", "refs/heads/alpha", head, repo=self.repo)
        real = gitx.run

        def without_symref(args, **how):
            if "--symref" in args:
                args = [a for a in args if a != "--symref"]
            return real(args, **how)
        self.patch(gitx, "run", without_symref)
        self.assertEqual(gitx.ls_remote(self.address), {"branch": "main", "head": head})
        gitfixture.git("update-ref", "-d", "refs/heads/main", repo=self.repo)
        gitfixture.git("symbolic-ref", "HEAD", "refs/heads/zeta", repo=self.repo)
        self.assertEqual(gitx.ls_remote(self.address), {"branch": "alpha", "head": head})

    def test_the_head_follows_a_force_push(self):
        before = gitx.ls_remote(self.address)
        gitfixture.force_push_over(self.repo, {"catalog.yml": "x\n"})
        after = gitx.ls_remote(self.address)
        self.assertNotEqual(before["head"], after["head"])
        self.assertEqual(after["head"], gitfixture.tip(self.repo))

    def test_an_anonymous_question_forgets_the_credential_helper_and_the_headers(self):
        seen = []
        real = gitx.run

        def watch(args, **how):
            seen.append(list(args))
            return real(args, **how)
        self.patch(gitx, "run", watch)
        gitx.ls_remote(self.address, anonymous=True)
        gitx.ls_remote(self.address)
        self.assertEqual(seen[0][:4], ["-c", "credential.helper=", "-c", "http.extraHeader="])
        self.assertNotIn("credential.helper=", seen[1])
        self.assertEqual(seen[0][4:6], ["ls-remote", "--symref"])
        self.assertEqual(seen[0][6:8], ["--", self.address.url])

    def test_an_ssh_address_cannot_be_asked_anonymously_and_runs_nothing(self):
        with mock.patch.object(gitx, "run", side_effect=AssertionError("ran")):
            for text in ("git@github.com:acme/lab-hub.git", "ssh://git@github.com/acme/lab-hub.git"):
                with self.assertRaises(gitx.CatalogError) as raised:
                    gitx.ls_remote(gitx.parse_address(text), anonymous=True)
                self.assertEqual(raised.exception.code, "no_access")

    def test_a_folder_is_not_a_remote(self):
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(gitx.Address("folder", "", "/h/x", "~/x", "", ""))
        self.assertEqual(raised.exception.code, "bad_address")

    def test_a_connection_refused_is_unreachable(self):
        gitfixture.fake_git(self, "echo \"fatal: unable to access 'https://git.example.test/team/hub.git/': Failed to connect "
                                  "to git.example.test port 443 after 2 ms: Couldn't connect to server\" >&2; exit 128")
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(gitx.parse_address("https://git.example.test/team/hub.git"))
        self.assertEqual((raised.exception.code, raised.exception.status), ("unreachable", 502))
        self.assertEqual(raised.exception.message, "Cannot reach git.example.test. Check your connection, then try again.")

    def test_an_address_built_by_hand_is_not_given_to_a_command(self):
        with mock.patch.object(gitx.subprocess, "Popen", side_effect=AssertionError("ran")):
            for url in ("ext::sh -c touch% /tmp/pwned", "-oProxyCommand=evil", "--upload-pack=x", "git://github.com/a/b.git",
                        "http://example.test/a/b", "https://user:pw@github.com/a/b", ""):
                forged = gitx.Address("git", url, "", "github.com/a/b", "github.com", "a/b")
                for call in (lambda: gitx.ls_remote(forged), lambda: gitx.ls_remote(forged, anonymous=True),
                             lambda: gitx.push_probe(forged, "a" * 40), lambda: gitx.checked(forged)):
                    with self.assertRaises(gitx.CatalogError, msg=url) as raised:
                        call()
                    self.assertIn(raised.exception.code, ("bad_address", "bad_address_secret"))
        with self.assertRaises(gitx.CatalogError):
            gitx.checked(("git", "https://github.com/a/b.git", "", "github.com/a/b", "github.com", "a/b"))   # not an Address
        for text in ACCEPTED:
            address = gitx.parse_address(text)
            self.assertIs(gitx.checked(address), address, text)

    def test_a_time_limit_is_a_timeout_error(self):
        gitfixture.fake_git(self, "sleep 30")
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(self.address, timeout=0.4)
        self.assertEqual((raised.exception.code, raised.exception.status), ("timeout", 502))

    def test_git_that_is_missing_is_git_missing_before_any_question(self):
        self.start(mock.patch.dict(os.environ, {"PATH": str(self.root)}))
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(self.address)
        self.assertEqual(raised.exception.code, "git_missing")


class Stranger(Case):
    """The anonymous question of the audience is asked as a stranger would: a catalog that a person can read
    with their own sign-in must not be measured Public because of it. A server on this machine answers 401 to a request
    without a header, and the tests see which requests carried one."""

    def setUp(self):
        gitfixture.require()
        self.home = gitfixture.isolate(self)
        self.headers = []
        seen = self.headers

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(401 if self.headers.get("Authorization") is None else 404)
                self.send_header("WWW-Authenticate", 'Basic realm="lab"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except OSError as error:
            print(f"SKIP: no server could be started on this machine ({error}), the anonymous question was not tested", file=sys.stderr)
            self.skipTest("no loopback server")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.origin = f"http://127.0.0.1:{server.server_address[1]}"
        self.address = gitx.parse_address(f"{self.origin}/team/hub.git")

    def asked(self, anonymous):
        """The Authorization headers of the requests of one question (None: a request without one), and the error."""
        del self.headers[:]
        with self.assertRaises(gitx.CatalogError) as raised:
            gitx.ls_remote(self.address, anonymous=anonymous)
        return list(self.headers), raised.exception.code

    def sign_in_with(self, config=None, netrc=None):
        if netrc:
            (self.home / ".netrc").write_text(netrc)
            (self.home / ".netrc").chmod(0o600)
        if config:
            (self.home / ".gitconfig").write_text(config)

    def check(self, **how):
        """The question as the person is asked it carries their sign-in (so the test proves something); as a stranger, never."""
        self.sign_in_with(**how)
        signed, code = self.asked(anonymous=False)
        if not any(signed):
            print("SKIP: this git did not sign in with the stored login, the anonymous question was not tested", file=sys.stderr)
            self.skipTest("the stored login is not used by this git")
        self.assertEqual(code, "not_found", "the server refuses the login it was given with a 404")
        stranger, code = self.asked(anonymous=True)
        self.assertEqual((stranger, code), ([None], "no_access"), "a question as a stranger carries no login")

    def test_the_netrc_of_the_person_is_not_used(self):
        self.check(netrc="machine 127.0.0.1 login lab-user password lab-secret\n")

    def test_a_credential_helper_of_the_person_for_this_address_is_not_used(self):
        """A helper set for a server by its address: the configuration of the person is not read for the question, and
        `-c credential.helper=` forgets the helpers read before it."""
        helper = "!f() { echo username=lab-user; echo password=lab-secret; }; f"
        self.check(config=f'[credential "{self.origin}"]\n\thelper = "{helper}"\n')

    def test_a_header_stored_in_the_configuration_of_the_person_is_not_sent(self):
        self.check(config='[http]\n\textraHeader = Authorization: Bearer lab-secret\n')

    def test_the_configuration_is_read_again_for_a_question_that_is_not_anonymous(self):
        """The empty home is for the anonymous question alone: the next one is the person's again."""
        self.sign_in_with(netrc="machine 127.0.0.1 login lab-user password lab-secret\n")
        self.asked(anonymous=True)
        signed, _ = self.asked(anonymous=False)
        if not any(signed):
            self.skipTest("the stored login is not used by this git")
        self.assertTrue(any(signed))

    def test_the_empty_home_is_made_for_the_question_and_removed_after_it(self):
        seen = []
        real = gitx.run

        def watch(args, **how):
            seen.append(how["env"])
            return real(args, **how)
        self.patch(gitx, "run", watch)
        self.asked(anonymous=True)
        self.asked(anonymous=False)
        stranger, person = seen
        self.assertEqual(stranger["HOME"], stranger["XDG_CONFIG_HOME"])
        self.assertNotEqual(stranger["HOME"], str(self.home))
        self.assertFalse(os.path.exists(stranger["HOME"]), "removed once the question was asked")
        self.assertNotIn("HOME", person, "the person's own home comes from the environment of the lab")


class PushProbe(Case):

    def setUp(self):
        gitfixture.require()
        gitfixture.isolate(self)
        self.folder()
        self.repo = gitfixture.make_repo(self.root / "remotes" / "hub.git", gitfixture.catalog_files())
        self.address = gitx.parse_address(gitfixture.url(self.repo))
        self.store = gitfixture.make_repo(self.root / "cache" / "hub.git", gitfixture.catalog_files())

    def refs(self, repo):
        return gitfixture.git("for-each-ref", "--format=%(refname)", repo=repo).split()

    def test_a_remote_that_takes_pushes_answers_yes_and_nothing_is_pushed(self):
        before = self.refs(self.repo)
        self.assertEqual(gitx.push_probe(self.address, gitfixture.tip(self.store), store=self.store), "yes")
        self.assertEqual(self.refs(self.repo), before)

    def test_without_a_store_an_empty_commit_is_pushed_instead(self):
        before = self.refs(self.repo)
        self.assertEqual(gitx.push_probe(self.address, "0" * 40), "yes")
        self.assertEqual(gitx.push_probe(self.address, gitfixture.tip(self.store)), "yes", "a store that is not named")
        self.assertEqual(gitx.push_probe(self.address, gitfixture.tip(self.store), store=self.root / "none.git"), "yes")
        self.assertEqual(self.refs(self.repo), before)

    def test_the_branch_pushed_is_one_that_does_not_exist(self):
        seen = []
        real = gitx.run

        def watch(args, **how):
            seen.append(list(args))
            return real(args, **how)
        self.patch(gitx, "run", watch)
        gitx.push_probe(self.address, gitfixture.tip(self.store), store=self.store)
        push = next(args for args in seen if "push" in args)
        self.assertIn("--dry-run", push)
        self.assertEqual(push[push.index("--") + 1], self.address.url)
        self.assertRegex(push[-1], r"^[0-9a-f]{40}:refs/heads/lab/probe-[0-9a-f]{8}$")

    def answer(self, code, err):
        return mock.patch.object(gitx, "run", return_value=gitx.Result(code, b"", err))

    def test_a_remote_that_refuses_the_sign_in_or_the_permission_answers_no(self):
        for err in ("fatal: unable to access 'https://x/': The requested URL returned error: 403",
                    "remote: Permission to acme/hub.git denied to someone.",
                    "fatal: could not read Username for 'https://x': terminal prompts disabled",
                    "git@x: Permission denied (publickey).", "ERROR: Repository not found."):
            with self.subTest(err):
                with self.answer(1, err):
                    self.assertEqual(self.probe(), "no")

    def probe(self):
        with mock.patch.object(gitx, "_probe_commit", return_value=("/x", "a" * 40)):
            return gitx.push_probe(gitx.parse_address("https://git.example.test/team/hub.git"), "a" * 40)

    def test_a_question_that_was_not_answered_is_unknown(self):
        for err in ("fatal: Could not resolve host: x", "git timed out (no answer within 20 s)",
                    "fatal: unable to access: SSL certificate problem: self-signed", "something odd"):
            with self.subTest(err):
                with self.answer(1, err):
                    self.assertEqual(self.probe(), "unknown")

    def test_a_folder_cannot_be_pushed_to(self):
        self.assertEqual(gitx.push_probe(gitx.Address("folder", "", "/h/x", "~/x", "", ""), "a" * 40), "unknown")

    def test_the_probe_works_in_a_folder_it_removes(self):
        private = self.root / "tmp"
        private.mkdir()
        self.patch(tempfile, "tempdir", str(private))
        gitx.push_probe(self.address, "0" * 40)
        self.assertEqual(os.listdir(private), [])


if __name__ == "__main__":
    unittest.main()
