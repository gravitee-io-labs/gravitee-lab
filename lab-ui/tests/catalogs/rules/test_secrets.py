"""SE, secrets and private identifiers, in the files of a tree and in the lines a change added. For each code one
failing fixture in which only that code fires, and one that passes; the message equals and never holds the secret. A
secret that a test plants is made of pieces when the test runs (tests/data/rules/support.py), so no file holds one. A line of a
megabyte finishes every pattern in under a second. The calibration at the end reads this checkout's tracked files."""

import re
import subprocess
import unittest

from lab_ui.catalogs.rules import Context, context, secrets, text_of
from lab_ui.catalogs.tree import Added, ListTree
from tests.data.rules import support
from tests.data.rules.support import codes, run

PRIVATE = ("{p}:{n} holds a private key. Fix: revoke it first, it is public in this pull request. Then remove it from the "
           "commits of this branch: git reset --soft $(git merge-base origin/main HEAD), commit again, and force-push your "
           "branch.")
TOKEN = ("{p}:{n} looks like {kind}. Fix: make it a setting (${{PREFIX_NAME}}) and keep the value in .env. If it is a real token, "
         "revoke it first, then remove it from the commits of this branch (git reset --soft $(git merge-base origin/main HEAD), "
         "commit again, force-push).")


def only(case, code, path, line, message, *changes, **how):
    found = run(secrets, *changes, **how)
    case.assertEqual([(code, path, line, message)], [(each.code, each.path, each.line, each.message) for each in found], found)
    return found[0]


class Patterns(unittest.TestCase):

    def test_the_good_catalog_is_quiet(self):
        self.assertEqual([], run(secrets))

    def test_se01_a_private_key(self):
        for header in ("-----BEGIN RSA PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----", "-----BEGIN EC PRIVATE KEY-----",
                       "-----BEGIN OPENSSH PRIVATE KEY-----", "-----BEGIN ENCRYPTED PRIVATE KEY-----",
                       "-----BEGIN PGP PRIVATE KEY BLOCK-----"):
            with self.subTest(header=header):
                only(self, "SE01", "docs/a.md", 3, PRIVATE.format(p="docs/a.md", n=3), {"docs/a.md": f"one\ntwo\n{header}\nbody\n"})
        self.assertEqual([], run(secrets, {"docs/a.md": "-----BEGIN PUBLIC KEY-----\n-----BEGIN CERTIFICATE-----\nPRIVATE KEY\n"}))

    def test_se02_each_token_shape_says_what_it_looks_like(self):
        kinds = {
            "a GitHub token": [support.github_token(), "gh" + "o_" + "a" * 36, "gh" + "u_" + "B" * 40, "gh" + "s_" + "c" * 36,
                               "gh" + "r_" + "D" * 36, "github" + "_pat_" + "a1" * 30],
            "an AWS access key": [support.aws_key(), "AS" + "IA" + "0123456789ABCDEF"],
            "a Slack token": [support.slack_token(), "xo" + "xa-" + "1" * 12, "xo" + "xp-" + "a-b" * 5, "xo" + "xr-" + "z" * 10,
                              "xo" + "xs-" + "9" * 11],
            "a Google API key": [support.google_key()],
            "a JWT": [support.jwt()],
            "an API key that starts with sk-": [support.sk_key(), "s" + "k-ant-" + "Zz9" * 12],
        }
        for kind, values in kinds.items():
            for value in values:
                with self.subTest(kind=kind, value=value[:6]):
                    found = only(self, "SE02", "docs/a.md", 2, TOKEN.format(p="docs/a.md", n=2, kind=kind),
                                 {"docs/a.md": f"first\nkey = {value}\n"})
                    self.assertNotIn(value, found.message)

    def test_se02_a_token_is_found_after_an_underscore_or_a_dash_and_not_inside_a_word(self):
        for value in (support.aws_key(), support.github_token(), support.slack_token(), support.google_key(), support.sk_key()):
            for before in ("_", "KEY_", "x_", "-", "=", "\"", "/", ".", ":", "(", "token_", "A_B_"):
                with self.subTest(value=value[:6], before=before):
                    self.assertEqual(["SE02"], [each.code for each in run(secrets, {"docs/a.md": f"a {before}{value} b\n"})])
            for before in ("x", "9", "KEY", "abc"):
                with self.subTest(value=value[:6], word=before):
                    self.assertEqual([], run(secrets, {"docs/a.md": f"a {before}{value} b\n"}), "part of a longer word")

    def test_se02_what_is_not_a_secret_passes(self):
        text = "\n".join([
            "gh" + "p_short", "AK" + "IA is a prefix", "AK" + "IA" + "lower" + "case" * 4, "xo" + "xb", "xo" + "xb-short", "AI" + "za" + "short",
            "s" + "k-learn-pipeline-for-the-workshop-notes-and-more", "s" + "k-" + "a" * 40, "task-" + "b" * 40,
            "ey" + "J" + "abc.ey" + "J" + "def", ".".join(["notey" + "J" + "hbGciOiJIUzI1NiJ9", "ey" + "J" + "zdWIiOiIxMjM0NTY3ODkwIn0", "sigsigsigsigsig"]),
            "a" * 80, "-----BEGIN CERT-----", "token: ${GITHUB_TOKEN}", "ghp_" + "${TOKEN}"])
        self.assertEqual([], run(secrets, {"docs/a.md": text}))

    def test_a_secret_is_found_in_any_file(self):
        value = support.github_token()
        for name in ("docs/a.md", "addons/hello/compose.yml", "docs/x.txt", "tools/run.sh", "docs/deep/x.json", ".env.example"):
            with self.subTest(name=name):
                self.assertIn("SE02", codes(run(secrets, {name: f"x: {value}\n"})))
        self.assertEqual([], run(secrets, {"docs/big.md": b"x\n" * (512 * 1024) + b"key = " + value.encode()}), "past the megabyte it is ST09's")

    def test_a_file_is_read_up_to_what_st09_lets_it_be(self):
        """A file may be 1 MB if it is a .yml .yaml .md or .json, and 2 MB if it is anything else: a file that is no error is read."""
        value = support.aws_key().encode()
        pad = b"x\n" * (700 * 1024)   # 1.4 MB
        self.assertEqual(["SE02"], [each.code for each in run(secrets, {"docs/big.txt": pad + b"key = " + value})])
        self.assertEqual(["SE02"], [each.code for each in run(secrets, {"tools/run.sh": pad + b"key = " + value + b"\n"})])
        self.assertEqual([], run(secrets, {"docs/big.json": pad + b"key = " + value}), "1 MB for a .json, ST09's past it")
        self.assertEqual([], run(secrets, {"docs/huge.txt": b"x\n" * (1100 * 1024) + b"key = " + value}), "2 MB, ST09's past it")
        self.assertEqual([("docs/big.txt", 1)], [(each.path, each.line) for each in run(
            secrets, {"docs/big.txt": b"key = " + value + b"\n" + pad})])

    def test_a_nul_byte_hides_nothing(self):
        """GitHub shows no diff for a file with a NUL in it, and a build, a script or a guide reads it all the same: a secret kept
        behind one, anywhere in the file, is found, in the tree as in the history."""
        value = support.aws_key().encode()
        padding = b"x" * 5000
        forms = {"a NUL first": b"\0\nkey = " + value, "ten NULs": b"\0" * 10 + value, "a NUL at 100": b"x" * 100 + b"\0\nkey = " + value,
                 "a NUL past 8 KB": b"x" * 9000 + b"\0\nkey = " + value, "NULs inside the key": b"key = " + b"\0".join(
                     value[at:at + 1] for at in range(len(value))), "a secret then a NUL": b"key = " + value + b"\n\0" + padding,
                 "UTF-16 with a mark": b"\xff\xfe" + ("key = " + value.decode() + "\n").encode("utf-16-le"),
                 "UTF-16 without a mark": ("key = " + value.decode() + "\n").encode("utf-16-be"),
                 "after the signature of a PNG": b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + padding + b"\n" + value}
        for label, data in forms.items():
            for name in ("docs/a.bin", "docs/picture.png", "docs/notes.txt", "addons/hello/data.dat"):
                with self.subTest(form=label, name=name):
                    self.assertEqual(["SE02"], [each.code for each in run(secrets, {name: data})])

    def test_the_bytes_of_a_real_binary_file_match_nothing(self):
        import random
        noise = random.Random(7).getrandbits(8 * 600000).to_bytes(600000, "big")
        for name, data in (("docs/noise.bin", noise), ("docs/picture.png", b"\x89PNG\r\n\x1a\n" + noise), ("docs/all.dat", bytes(range(256)) * 2000),
                           ("docs/zeros.bin", b"\0" * 900000)):
            with self.subTest(name=name):
                self.assertEqual([], run(secrets, {name: data}))

    def test_the_line_is_the_one_of_the_secret_in_a_file_of_any_line_ending(self):
        value = support.aws_key()
        for ending in ("\n", "\r\n"):
            found = run(secrets, {"docs/a.md": f"a{ending}b{ending}c {value}{ending}"})
            self.assertEqual([3], [each.line for each in found])

    def test_se04_a_literal_value_under_the_name_of_a_secret(self):
        message = "{p}:{n}: {name} has a literal value. Fix: write ${{NAME:-}} and set the value in .env."
        compose = ("x-gravitee:\n  name: Demo\nservices:\n  db:\n    image: postgres:16\n    environment:\n      DB_PASSWORD: hunter2\n"
                   "      OTHER: ${A}\n      KEY: ${B:-x}\n")
        only(self, "SE04", "addons/hello/compose.yml", 7, message.format(p="addons/hello/compose.yml", n=7, name="DB_PASSWORD"),
             {"addons/hello/compose.yml": compose})
        for text, name in (("      - DB_PASSWORD=hunter2\n", "DB_PASSWORD"), ("      - 'API_KEY=abc123'\n", "API_KEY"),
                           ("      SERVICE_TOKEN: \"abc\"\n", "SERVICE_TOKEN"), ("      client_secret: s3cr3t\n", "client_secret"),
                           ("      PASSPHRASE: 'a b'  # comment\n", "PASSPHRASE")):
            with self.subTest(text=text):
                body = "services:\n  db:\n    environment:\n" + text
                found = run(secrets, {"addons/hello/compose.yml": body})
                self.assertEqual([("SE04", 4, name)], [(each.code, each.line, re.search(r": (\S+) has", each.message).group(1))
                                                      for each in found])

    def test_se04_a_literal_in_a_flow_collection_or_on_the_next_line(self):
        head = "services:\n  db:\n    image: postgres:16\n    environment:"
        for tail, name in ((" [DB_PASSWORD=hunter2, OTHER=1]\n", "DB_PASSWORD"), (" ['API_KEY=abc123']\n", "API_KEY"),
                           (" {DB_PASSWORD: hunter2}\n", "DB_PASSWORD"), (" {OTHER: 1, \"CLIENT_SECRET\": 's3cr3t'}\n", "CLIENT_SECRET"),
                           (" {A: 1, B: {SERVICE_TOKEN: abc}}\n", "SERVICE_TOKEN"), (" [A=1, [B_PASSWORD=x]]\n", "B_PASSWORD"),
                           ("\n      DB_PASSWORD:\n        hunter2\n", "DB_PASSWORD"), ("\n      DB_PASSWORD:\n        \"hunter2\"\n", "DB_PASSWORD"),
                           (" {DB_PASSWORD:\n   hunter2}\n", "DB_PASSWORD"), (" [\n   DB_PASSWORD=hunter2,\n   B=1\n ]\n", "DB_PASSWORD")):
            with self.subTest(tail=tail):
                found = run(secrets, {"addons/hello/compose.yml": head + tail})
                self.assertEqual([("SE04", name)], [(each.code, re.search(r": (\S+) has", each.message).group(1)) for each in found])
                self.assertEqual(5 if tail.startswith("\n") else 4, found[0].line, "the line of the key that holds the value")
        for tail in (" [DB_PASSWORD=${X}, OTHER=1]\n", " {DB_PASSWORD: ${X:-demo}}\n", " {A: 1, B: [2, 3]}\n", " [PASSWORD_FILE=/run/p, TOKEN_URL=u]\n",
                     " {DB_PASSWORD: ''}\n", "\n      DB_PASSWORD:\n        ${X}\n", "\n      DB_PASSWORD:\n\n", " [A_B=c=d]\n"):
            with self.subTest(ok=tail):
                self.assertEqual([], run(secrets, {"addons/hello/compose.yml": head + tail}))

    def test_se04_a_flow_account_and_an_alias_in_a_flow_collection(self):
        body = "x-defs:\n  pass: &pass hunter2\nservices:\n  a:\n    environment: {A_PASSWORD: *pass}\n"
        self.assertEqual([("SE04", 5)], [(each.code, each.line) for each in run(secrets, {"addons/hello/compose.yml": body})])
        body = "x-gravitee:\n  accounts: [{for: me, password: hunter2}, {for: key, key: abc}]\n  tested: [{APIM_PASSWORD: x}]\n"
        self.assertEqual([("SE04", 2), ("SE04", 2)], [(each.code, each.line) for each in run(secrets, {"playgrounds/shop/compose.yml": body,
                                                                                            "playgrounds/shop/README.md": "x"})])

    def test_se04_is_asked_of_every_compose_file_of_a_unit_and_not_of_what_is_no_compose_file(self):
        text = "services:\n  a:\n    environment:\n      DB_PASSWORD: hunter2\n"
        for name in ("addons/hello/compose.override.yml", "addons/hello/compose.prod.yaml", "addons/hello/services.yml",
                     "addons/hello/docker-compose.dev.yaml", "playgrounds/shop/apps/web/dev.yml", "addons/hello/sub/extra.yml"):
            with self.subTest(name=name):
                self.assertEqual(["SE04"], [each.code for each in run(secrets, {name: text})])
        other = "database:\n  password: hunter2\nservices_list: [a]\n"
        for name in ("addons/hello/config.yml", "addons/hello/application.yaml", "docs/services.yml", "playgrounds/shop/seed/x/services.yml"):
            with self.subTest(quiet=name):
                self.assertEqual([], run(secrets, {name: other}), "a configuration is no Compose file")
        for name in ("docs/services.yml", "playgrounds/shop/seed/x/services.yml"):
            with self.subTest(outside=name):
                self.assertEqual([], run(secrets, {name: text}), "not in a unit, or a seed")

    def test_se04_in_an_env_example(self):
        message = "{p}:{n}: {name} has a literal value. Fix: write ${{NAME:-}} and set the value in .env."
        only(self, "SE04", "addons/hello/.env.example", 2, message.format(p="addons/hello/.env.example", n=2, name="DB_PASSWORD"),
             {"addons/hello/.env.example": "# settings\nDB_PASSWORD=hunter2\nDB_USER=app\nTOKEN_URL=https://x\nEMPTY_SECRET=\n"
                                           "REF_PASSWORD=${OTHER}\nPASSWORD_FILE=/run/x\n"})
        self.assertEqual([], run(secrets, {".env.example": "EMPTY_PASSWORD=\nA=1\n# PASSWORD=commented\n"}))

    def test_se04_what_the_lab_reads_and_a_setting_are_not_literals(self):
        body = ("x-gravitee:\n  name: Demo\n  tested:\n    - APIM_VERSION: \"4.1\"\n  accounts:\n    - for: me\n      password: *demo\n"
                "services:\n  a:\n    environment:\n      A_PASSWORD: ${A_PASSWORD:-demo}\n      B_SECRET: $B_SECRET\n"
                "      C_TOKEN_URL: https://token.example/\n      D_PASSWORD_FILE: /run/secrets/d\n      E_API_KEY: ''\n"
                "      F_PASSWORD: *setting\n    secrets: [db_password, api_key]\n    command: |\n      SOME_PASSWORD: not a setting, a script\n")
        body = "x-defs:\n  setting: &setting ${F:-x}\n  demo: &demo ${DEMO_PASSWORD:-HelloWorld}\n" + body
        self.assertEqual([], run(secrets, {"addons/hello/compose.yml": body}))

    def test_se04_an_alias_of_a_literal_is_a_literal(self):
        body = "x-defs:\n  pass: &pass hunter2\nservices:\n  a:\n    environment:\n      A_PASSWORD: *pass\n"
        self.assertEqual(["SE04"], [each.code for each in run(secrets, {"addons/hello/compose.yml": body}) if each.line == 6])

    def test_se04_only_the_accounts_of_x_gravitee_hold_secrets(self):
        body = "x-gravitee:\n  accounts:\n    - for: me\n      username: a\n      password: hunter2\n    - for: key\n      key: abc\n"
        found = run(secrets, {"playgrounds/shop/compose.yml": body, "playgrounds/shop/README.md": "x"})
        self.assertEqual([("SE04", 5), ("SE04", 7)], [(each.code, each.line) for each in found])

    def test_se04_is_asked_of_compose_files_and_not_of_a_guide_or_a_seed(self):
        text = "services:\n  a:\n    environment:\n      DB_PASSWORD: hunter2\n"
        for name in ("docs/a.md", "playgrounds/shop/seed/apim/apis/a.yaml", "docs/config.yml"):
            self.assertEqual([], run(secrets, {name: text}), name)
        for name in ("addons/hello/compose.yml", "setups/all.yml", "docker-compose.yml", "addons/hello/x.compose.yml"):
            self.assertEqual({"SE04"}, codes(run(secrets, {name: text})), name)

    def test_se05_a_url_with_a_password(self):
        message = "{p}:{n} holds a URL with a password. Fix: take the password from a setting."
        for scheme in ("postgres", "https", "mongodb+srv", "redis"):
            with self.subTest(scheme=scheme):
                found = only(self, "SE05", "addons/hello/compose.yml", 4, message.format(p="addons/hello/compose.yml", n=4),
                             {"addons/hello/compose.yml": f"services:\n  a:\n    environment:\n      URL: {scheme}://user:hunter2@host:1/db\n"})
                self.assertEqual("error", found.severity)
        for name in ("playgrounds/shop/seed/apim/apis/a.yaml",):
            self.assertEqual("error", run(secrets, {name: "u: https://user:hunter2@host/\n"})[0].severity, name)
        for name in ("docs/a.md", "README.md", "tools/run.sh"):
            found = run(secrets, {name: "see https://user:hunter2@host/\n"})
            self.assertEqual([("SE05", "warning")], [(each.code, each.severity) for each in found], name)

    def test_se05_an_empty_user_and_the_characters_of_a_password(self):
        for url in ("redis://:hunter2@cache:6379", "postgres://user:pa$$word@db/x", "postgres://u:pa{ss}@h", "amqp://guest:g{u}e$st@mq/",
                    "mongodb://:p}w{d@host", "postgres://${USER}:hunter2@db/x", "https://$USER:hunter2@host/", "redis://default:hunter2@cache"):
            with self.subTest(url=url):
                self.assertEqual(["SE05"], [each.code for each in run(secrets, {"docs/a.md": f"see {url}\n"})])
        for url in ("redis://:${REDIS_PASSWORD}@cache:6379", "redis://:$REDIS_PASSWORD@cache", "postgres://u:${P:-x}@h", "postgres://u:ab${P}@h",
                    "postgres://${U}:${P}@h", "redis://:@cache", "redis://:ab@cache"):
            with self.subTest(ok=url):
                self.assertEqual([], run(secrets, {"docs/a.md": f"see {url}\n"}))

    def test_se05_what_is_no_password_in_a_url(self):
        text = "\n".join(["https://user@host/", "https://user:${PASSWORD}@host/", "https://user:$PASS@host/", "postgres://user:@host/",
                          "http://[::1]:8080/x@y", "https://host:8080/path", "mailto:a@b.c", "x://user:ab@host", "git@github.com:a/b.git",
                          "://user:hunter2@host", "-://user:hunter2@host"])
        self.assertEqual([], run(secrets, {"docs/a.md": text}))

    def test_se06_a_private_identifier_when_share_targets_a_public_catalog(self):
        message = "{p}:{n} names {name}, which belongs to a private catalog. Fix: remove it, or share to a Private catalog."
        only(self, "SE06", "addons/hello/compose.yml", 5, message.format(p="addons/hello/compose.yml", n=5, name="registry.corp.example"),
             {"addons/hello/compose.yml": "services:\n  a:\n    image: busybox\n    x: y\n    z: Registry.Corp.Example/a:1\n"},
             private=("registry.corp.example",))
        self.assertEqual([], run(secrets, {"docs/a.md": "ns here"}, private=("ns", "")), "an identifier shorter than four letters is none")
        self.assertEqual([], run(secrets, {"docs/a.md": "registry.corp.example"}), "nothing is private until Share says so")

    def test_se07_an_allow_marker(self):
        message = "{p}:{n} carries a scanner allow marker. Fix: remove it; a maintainer decides what the scan may skip."
        for marker in ("# gitleaks:allow", "x = 1  // GITLEAKS:ALLOW", "a  # pragma: allowlist secret", "# pragma:allowlist nextline secret",
                       "# trufflehog:ignore", "/* secretlint-disable-line */"):
            with self.subTest(marker=marker):
                only(self, "SE07", "docs/a.md", 2, message.format(p="docs/a.md", n=2), {"docs/a.md": f"fine\nvalue {marker}\n"})
        self.assertEqual([], run(secrets, {"docs/a.md": "gitleaks is a scanner; allow lists are discussed elsewhere\n"}))

    def test_se03_is_a_row_of_the_table_and_a_helper_for_the_gitleaks_stage(self):
        found = secrets.se03("docs/a.md", 4, "generic-api-key")
        self.assertEqual(("SE03", "error", "docs/a.md", 4), found[:4])
        self.assertEqual("docs/a.md:4 matches the rule generic-api-key (the secret is redacted). Fix: revoke it first, then remove "
                         "it from the commits of this branch: git reset --soft $(git merge-base origin/main HEAD), commit again, "
                         "and force-push your branch.", found.message)

    def test_the_messages_of_the_table_are_section_6(self):
        self.assertEqual({"SE01", "SE02", "SE03", "SE04", "SE05", "SE06", "SE07"}, set(secrets.RULES))
        self.assertEqual("{path}:{line}: {name} has a literal value. Fix: write ${NAME:-} and set the value in .env.",
                         secrets.RULES["SE04"].message)
        self.assertEqual("{path}:{line} looks like {kind}. Fix: make it a setting (${PREFIX_NAME}) and keep the value in .env. If "
                         "it is a real token, revoke it first, then remove it from the commits of this branch (git reset --soft "
                         "$(git merge-base origin/main HEAD), commit again, force-push).", secrets.RULES["SE02"].message)

    def test_the_names_of_secrets_are_the_ones_of_the_engines_own_check(self):
        try:
            from lab_ui.library import check
        except ImportError as error:   # check.py is rewritten by another package: say so and do not pass for it
            print(f"SKIP: lab_ui.library.check does not import ({error}), its SECRET_NAME was not compared")
            self.skipTest("check.py does not import")
        if not (hasattr(check, "SECRET_NAME") and hasattr(check, "NOT_SECRET")):
            print("SKIP: lab_ui.library.check has no SECRET_NAME, it was not compared")
            self.skipTest("check.py has no SECRET_NAME")
        self.assertEqual(check.SECRET_NAME.pattern, secrets.SECRET_NAME.pattern)
        self.assertEqual(check.SECRET_NAME.flags, secrets.SECRET_NAME.flags)
        self.assertEqual(check.SIGN_IN.pattern, secrets.SIGN_IN.pattern)
        self.assertEqual(check.NOT_SECRET.pattern, secrets.NOT_SECRET.pattern)
        if hasattr(check, "_secrets"):
            sample = {"services": {"a": {"environment": {"DB_PASSWORD": "hunter2", "OK": "${X}", "A_TOKEN_URL": "u", "KEY": "v"}}}}
            flagged = {key for _, key, _, why in check._secrets(sample) if why == "literal"}
            text = "services:\n  a:\n    environment:\n      DB_PASSWORD: hunter2\n      OK: ${X}\n      A_TOKEN_URL: u\n      KEY: v\n"
            self.assertEqual(flagged, {name for _, name in secrets.compose_literals(text)})


class Hostile(unittest.TestCase):
    """The file of a stranger: no pattern may take a second on a line of a megabyte, whatever that line repeats."""

    PIECES = ["ghp_", "github_pat_", "AKIA", "xoxb-", "AIza", "sk-sk-", "ey" + "J.", ".eyJ", "-----BEGIN A A A ", "PRIVATE KEY", "x://a:",
              "a:b@", "gitleaks:allow", "pragma: ", " ", "a", "-", ".", "${", "}", "*", "&", "'", '"', "\\", "- KEY=", "key: ",
              "ghp_" + "a" * 40 + "_", "sk-" + "a" * 33 + "!", "a" * 38 + ".", "eyJ" + "a" * 20 + ".", "xoxb-" + "a" * 20 + "_",
              "://:", "://a:$", "://:${", "://:$$", "://" + "a" * 100, "://a:b", "_AKIA", "KEY_ghp_", ":\n  x", "A_PASSWORD="]

    def fast(self, call, *args):
        """The seconds a call takes; asked again twice when it is slow, because the machine of a test run is shared."""
        best = support.timed(call, *args)[1]
        for _ in range(2 if best >= 1.0 else 0):
            best = min(best, support.timed(call, *args)[1])
        return best

    def test_every_pattern_finishes_on_a_line_of_a_megabyte_in_under_a_second(self):
        for piece in self.PIECES:
            line = piece * (1024 * 1024 // len(piece))
            listing = ListTree.from_files({"addons/hello/compose.yml": line, "docs/a.md": line, ".env.example": line})
            ctx = context(listing, private=("registry.corp.example",))
            for path in ("addons/hello/compose.yml", "docs/a.md"):
                seconds = self.fast(secrets.scan_text, path, line, ctx)
                self.assertLess(seconds, 2.0, f"{piece[:20]!r} in {path}: {seconds:.2f} s")   # one second on a quiet machine, two on a shared runner
            added = [Added("a" * 40, "addons/hello/compose.yml", 1, line)]

            class Here:
                def read(self, path, limit=0):
                    return line.encode()[:limit]

            seconds = self.fast(lambda: secrets.scan_added(added, ctx, Here()))
            self.assertLess(seconds, 1.0, f"{piece[:20]!r} as an added line: {seconds:.2f} s")

    def test_a_file_of_many_short_lines_is_read_in_proportion_to_its_size(self):
        text = "services:\n  a:\n    environment:\n" + "      K: v\n" * 60000
        _, seconds = support.timed(secrets.scan_text, "addons/hello/compose.yml", text, Context())
        self.assertLess(seconds, 4.0)
        text = "x-a:\n" + "".join(f"  k{number}: &a{number} v\n" for number in range(30000))
        _, seconds = support.timed(secrets.scan_text, "addons/hello/compose.yml", text, Context())
        self.assertLess(seconds, 4.0)

    def test_flow_collections_are_read_within_a_budget(self):
        head = "services:\n  a:\n    environment: "
        shapes = {"nested maps": "{a:" * 20000, "nested lists": "[" * 60000, "many pairs": "{" + "K_PASSWORD: v, " * 4000 + "}",
                  "many items": "[" + "K_PASSWORD=v, " * 4000 + "]", "deep": "[{" * 15000 + "A_PASSWORD=x" + "}]" * 15000,
                  "quotes": "[" + "'" * 60000, "colons": "{" + ":" * 60000 + "}", "commas": "[" + "," * 60000 + "]"}
        for label, shape in shapes.items():
            text = head + shape + "\n"
            _, seconds = support.timed(secrets.scan_text, "addons/hello/compose.yml", text, Context())
            self.assertLess(seconds, 1.0, f"{label}: {seconds:.2f} s")
        joined = head + "[\n" + "\n".join(["A_B=c, " * 1400 for _ in range(100)]) + "]\n"   # a megabyte over a hundred lines
        self.assertLess(len(joined), 1024 * 1024 + 100)
        _, seconds = support.timed(secrets.scan_text, "addons/hello/compose.yml", joined, Context())
        self.assertLess(seconds, 0.8, "a flow collection over a hundred lines is scanned once, not again for each line")

    def test_the_flow_reader_gives_what_a_flow_collection_sets(self):
        self.assertEqual([("A", "1"), ("B", "x y")], secrets.flow_pairs("[A=1, \"B=x y\", C]"))
        self.assertEqual([("a", "1"), ("b", "{c: 2}"), ("c", "2")], secrets.flow_pairs("{a: 1, b: {c: 2}}"))
        self.assertEqual([("q", "v")], secrets.flow_pairs("{'q': v}"))
        self.assertEqual([], secrets.flow_pairs("not a collection"))
        self.assertEqual(["a", " [b, c]", " 'd, e'"], secrets.flow_items("a, [b, c], 'd, e'"))

    def test_jwts_are_read_as_tokens_and_not_searched_for(self):
        self.assertEqual([], secrets.jwts("eyJ" * 400000))
        self.assertEqual([0], secrets.jwts(support.jwt()))
        self.assertEqual([6], secrets.jwts("Token " + support.jwt() + " tail"))


class History(unittest.TestCase):
    """The lines a change added: a secret removed by a later commit is still public in the pull request."""

    SHA = "1a2b3c4d" + "0" * 32

    @staticmethod
    def lines(*rows):
        return [Added(sha, path, number, text) for sha, path, number, text in rows]

    def history(self, rows):
        added = self.lines(*rows)

        class Fake:
            def commits(self):
                return []

            def added(self):
                return iter(added)

        return Fake()

    def found_in(self, rows, files=None, **how):
        listing = support.tree(files or {})
        return secrets.check(listing, context(listing, history=self.history(rows), **how))

    def test_a_secret_removed_later_is_reported_with_its_commit(self):
        value = support.github_token()
        found = self.found_in([(self.SHA, "docs/old.md", 4, f"token {value}")], {"docs/old.md": "clean now\n"})
        self.assertEqual([("SE02", "docs/old.md", 4)], [(each.code, each.path, each.line) for each in found])
        self.assertEqual("docs/old.md:4 looks like a GitHub token in commit 1a2b3c4, removed later in this pull request. Fix: make "
                         "it a setting (${PREFIX_NAME}) and keep the value in .env. If it is a real token, revoke it first, then "
                         "remove it from the commits of this branch (git reset --soft $(git merge-base origin/main HEAD), commit "
                         "again, force-push).", found[0].message)
        self.assertNotIn(value, found[0].message)

    def test_a_secret_that_is_still_there_is_reported_once_by_the_scan_of_the_tree(self):
        value = support.aws_key()
        found = self.found_in([(self.SHA, "docs/old.md", 1, f"key {value}")], {"docs/old.md": f"key {value}\n"})
        self.assertEqual(1, len(found))
        self.assertNotIn("removed later", found[0].message)

    def test_a_deleted_file_is_removed_later_too(self):
        found = self.found_in([(self.SHA, "docs/gone.md", 1, support.private_key())])
        self.assertEqual(["SE01"], [each.code for each in found])
        self.assertIn("in commit 1a2b3c4, removed later in this pull request. Fix:", found[0].message)

    def test_every_rule_that_reads_lines_reads_them(self):
        rows = [(self.SHA, "addons/hello/compose.yml", 1, "      DB_PASSWORD: hunter2"), (self.SHA, "addons/hello/compose.yml", 2, "      - API_KEY=abc"),
                (self.SHA, "docs/a.md", 3, "see https://u:hunter2@host/ # gitleaks:allow"), (self.SHA, "docs/a.md", 4, "plain"),
                (self.SHA, "addons/hello/.env.example", 5, "TOKEN=abc")]
        found = self.found_in(rows)
        self.assertEqual({"SE04", "SE05", "SE07"}, codes(found))
        self.assertEqual(5, len(found))
        self.assertTrue(all("in commit 1a2b3c4, removed later" in each.message for each in found))

    def test_a_line_of_x_gravitee_that_names_a_secret_is_not_a_literal_in_the_history_either(self):
        self.assertEqual([], self.found_in([(self.SHA, "addons/hello/compose.yml", 1, "  client_secret: demo")]), "lower case names are the tree's")

    def test_the_same_line_of_a_commit_is_said_once(self):
        rows = [(self.SHA, "docs/a.md", 1, f"a {support.aws_key()} {support.aws_key()}")] * 3
        self.assertEqual(1, len(self.found_in(rows)))

    def test_a_flood_of_lines_is_cut_per_commit_and_code(self):
        rows = [(self.SHA, "docs/a.md", number, f"a {support.aws_key()}") for number in range(1, 400)]
        self.assertEqual(secrets.PER_FILE, len(self.found_in(rows)))

    def test_a_key_that_a_later_commit_only_changed_is_still_public(self):
        """The line is judged as a whole line of the file as it is now. A character added to the key, or to the line around it,
        makes the old line another line: the key was public in the pull request all the same."""
        value = support.aws_key()
        rows = [(self.SHA, "docs/old.md", 1, f"key {value}")]
        for final in (f"key {value}9\n", f"key {value[:-1]}\n", f"# key {value[:12]}\nkey = {value}9\n", f"x\nkey  {value}9\n"):
            with self.subTest(final=final):
                found = self.found_in(rows, {"docs/old.md": final})
                self.assertEqual(["SE02"], [each.code for each in found])
                self.assertIn("in commit 1a2b3c4, removed later in this pull request", found[0].message)
        found = self.found_in(rows, {"docs/old.md": f"the key {value}\n"})
        self.assertEqual(["SE02", "SE02"], [each.code for each in found], "the line is in the tree now, and the old one is gone")
        self.assertEqual([False, True], sorted("removed later" in each.message for each in found))

    def test_a_line_that_is_still_there_is_the_same_line_whatever_its_ending_or_its_nuls(self):
        value = support.aws_key()
        for final in (f"key {value}\r\n", f"a\r\nkey {value}\r\nb\r\n", f"\0\nkey {value}"):
            with self.subTest(final=final):
                found = self.found_in([(self.SHA, "docs/old.md", 2, f"key {value}\r")], {"docs/old.md": final})
                self.assertEqual(1, len(found))
                self.assertNotIn("removed later", found[0].message)
        found = self.found_in([(self.SHA, "docs/old.md", 1, f"ke\0y {value}")], {"docs/old.md": f"key {value}\n"})
        self.assertEqual(1, len(found), "the NULs of an added line are taken out as those of the file are")

    def test_a_secret_in_a_compose_file_that_is_named_otherwise_is_the_tree_s_while_it_is_there(self):
        line = "      DB_PASSWORD: hunter2"
        name = "addons/hello/services.yml"
        there = "services:\n  a:\n    environment:\n" + line + "\n"
        found = self.found_in([(self.SHA, name, 4, line)], {name: there})
        self.assertEqual([("SE04", False)], [(each.code, "removed later" in each.message) for each in found])
        found = self.found_in([(self.SHA, name, 4, line)], {name: "services:\n  a:\n    image: x\n"})
        self.assertEqual([("SE04", True)], [(each.code, "removed later" in each.message) for each in found])
        found = self.found_in([(self.SHA, "addons/hello/data.yml", 4, line)], {"addons/hello/data.yml": "other: 1\n" + line + "\n"})
        self.assertEqual([], found, "a file that is no Compose file is not asked for SE04, in the tree or in the history")

    def test_nothing_without_a_history(self):
        listing = support.tree()
        self.assertEqual([], secrets.check(listing, context(listing, history=None)))


# ---------------------------------------------------------------------------------------------------------------------
# Calibration on this checkout
# ---------------------------------------------------------------------------------------------------------------------

class Calibration(unittest.TestCase):
    """Over the tracked files of the engine the seven precise patterns find nothing outside the fixtures of the tests, and the
    shape "URL with a password" finds only examples: the documentation, one docstring and the tests."""

    # Files that are not tests and that write such a URL as an example
    EXAMPLES = ("lab-ui/lab_ui/catalogs/gitx.py", "lab-ui/lab_ui/catalogs/rules/secrets.py")

    def tracked(self):
        try:
            done = subprocess.run(["git", "ls-files", "-z"], cwd=support.ROOT, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is None or done.returncode != 0:
            self.skipTest("this checkout is not a git work tree")
        return [name.decode() for name in done.stdout.split(b"\0") if name]

    def test_the_precise_patterns_find_nothing_outside_the_fixtures_of_tests(self):
        found, urls, scanned = [], [], 0
        for name in self.tracked():
            path = support.ROOT / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            data = path.read_bytes()
            scanned += 1
            text = text_of(data)   # every file: an image or a font matches no pattern by chance
            found += [(name, hit.code, hit.kind) for hit in secrets.token_hits(text)]
            urls += [name for _ in secrets.password_urls(text)]
        self.assertGreater(scanned, 500)
        outside = [each for each in found if "/tests/" not in each[0]]
        self.assertEqual([], outside)
        self.assertEqual([], [name for name in urls if "/tests/" not in name and not name.endswith(".md") and name not in self.EXAMPLES],
                         "a URL with a password outside the documentation and the tests")

    def test_the_units_of_the_engine_hold_no_literal_secret_and_no_url_with_a_password(self):
        found = []
        for name in self.tracked():
            if name.split("/")[0] in ("addons", "setups", "playgrounds") and not name.endswith((".md", ".png", ".svg")):
                path = support.ROOT / name
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024:
                    found += [(name, each.code, each.line) for each in secrets.scan_text(name, text_of(path.read_bytes()), Context())]
        self.assertEqual([], found)
        core = (support.ROOT / "docker-compose.yml").read_text()
        self.assertEqual([], secrets.scan_text("docker-compose.yml", core, Context()))


if __name__ == "__main__":
    unittest.main()
