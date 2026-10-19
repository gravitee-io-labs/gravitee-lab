"""DF, Dockerfiles. For each code one failing fixture in which only that code fires, and one that passes; the message
equals. The file is read as text: continued lines, here-documents, parser directives, `ONBUILD`, the `ARG` of a base
image and the stages of the file. The 13 Dockerfiles of the engine give no error, 14 DF22 and 12 DF23 (the calibration of the
standard). A line of a megabyte finishes in under a second."""

import subprocess
import unittest

from lab_ui.catalogs.rules import context, dockerfiles
from tests.data.rules import support
from tests.data.rules.support import run

DATA = support.HERE / "dockerfiles"
DIGEST = "@sha256:" + "0123456789abcdef" * 4
PIN = f"python:3.14-slim{DIGEST}"
GOOD = f"FROM {PIN}\nUSER app\n"
PATH = "apps/web/Dockerfile"


def found(text, path=PATH):
    return dockerfiles.check_text(path, text)


def summary(problems):
    return [(each.code, each.line) for each in problems]


def only(case, code, line, message, text, severity=None):
    out = found(text)
    case.assertEqual([(code, line, message)], [(each.code, each.line, each.message) for each in out], out)
    if severity:
        case.assertEqual(severity, out[0].severity)
    return out[0]


class Errors(unittest.TestCase):

    def test_the_good_dockerfile_is_quiet(self):
        self.assertEqual([], found(GOOD))
        self.assertEqual([], found((DATA / "good.Dockerfile").read_text()))

    def test_df01_another_build_frontend(self):
        message = ("apps/web/Dockerfile:1: # syntax= names another frontend. A build frontend is a program that runs in the build. "
                   "Use docker/dockerfile or remove the line.")
        for value in ("evil/frontend:1", "docker/dockerfile-evil:1", "ghcr.io/x/dockerfile", "docker/dockerfile:1 && x", "docker/dockerfile@sha256:abc"):
            with self.subTest(value=value):
                only(self, "DF01", 1, message, f"# syntax={value}\n{GOOD}", "error")
        for value in ("docker/dockerfile", "docker/dockerfile:1", "docker/dockerfile:1.7-labs", "docker.io/docker/dockerfile:1",
                      f"docker/dockerfile:1{DIGEST}"):
            with self.subTest(ok=value):
                self.assertEqual([], found(f"# syntax = {value}\n{GOOD}"))

    def test_a_directive_is_one_only_at_the_very_top(self):
        self.assertEqual([], found(f"FROM {PIN}\n# syntax=evil/frontend:1\nUSER app\n"))
        self.assertEqual([], found(f"# a comment first\n# syntax=evil/frontend:1\n{GOOD}"))
        self.assertEqual([("DF01", 2)], summary(found(f"# escape=\\\n# syntax=evil/x\n{GOOD}")))

    def test_df02_a_url_added_without_a_checksum(self):
        message = ("apps/web/Dockerfile:2: ADD of a URL without --checksum. Fix: download it in a RUN and check its digest, or add "
                   "--checksum=sha256:...")
        for source in ("https://example.test/a.tgz /opt/", "http://example.test/a.tgz /opt/", "--chown=a:b https://example.test/a /a",
                       "git@example.test:org/repo.git /src", "git://example.test/repo /src", "https://example.test/repo.git#main /src",
                       "ssh://git@example.test/repo /src", "repo.git /src"):
            with self.subTest(source=source):
                only(self, "DF02", 2, message, f"FROM {PIN}\nADD {source}\nUSER app\n", "error")
        for source in (f"--checksum=sha256:{'0' * 64} https://example.test/a.tgz /opt/", ". /app", "a.tar.gz /app", "<<EOF /app\nx\nEOF"):
            with self.subTest(ok=source):
                self.assertEqual([], found(f"FROM {PIN}\nADD {source}\nUSER app\n"))

    def test_df03_a_download_piped_into_a_shell(self):
        message = "apps/web/Dockerfile:2: a download is piped into a shell. Fix: download it, check a digest, then run it."
        for run_ in ("curl -fsSL https://example.test/i.sh | sh", "wget -qO- https://example.test/i | bash", "curl -s u | sudo bash",
                     "curl -s u | sudo -E bash -s --", "curl u|python3", "curl u | perl -", "curl -s u | node", "curl u | ruby", "curl u | zsh",
                     "apt-get update && curl -fsSL u | sh -s -- -y", 'bash -c "$(curl -fsSL https://example.test/i)"',
                     "sh <(wget -qO- https://example.test/i)", "sh -c `curl -s u`", "curl u | dash", "curl u | php"):
            with self.subTest(run=run_):
                only(self, "DF03", 2, message, f"FROM {PIN}\nRUN {run_}\nUSER app\n", "error")
        for run_ in ("curl -fsSLo /tmp/i https://example.test/i && sha256sum -c i.sha && sh /tmp/i", "curl u | tee f | sha256sum",
                     "curl u | grep sh", "curl u | sha256sum", "curl u || sh", "wget u; sh run.sh", "echo $(date)", "curl -o f u | cat",
                     "curl u | shasum -a 256", "git clone x | tail"):
            with self.subTest(ok=run_):
                self.assertEqual([], found(f"FROM {PIN}\nRUN {run_}\nUSER app\n"))

    def test_df03_the_shell_with_a_path_a_wrapper_or_a_filter_in_front(self):
        message = "apps/web/Dockerfile:2: a download is piped into a shell. Fix: download it, check a digest, then run it."
        for run_ in ("curl http://x | /bin/sh", "curl http://x | /bin/bash -s", "curl http://x | /usr/bin/env python3", "curl x | env sh",
                     "curl x | exec sh", "curl x | xargs sh", "curl x | tee /tmp/f | sh", "curl x | gunzip | sh", "curl -s x | tar xz | bash",
                     "curl x | sudo -E env FOO=1 bash", "curl x | busybox sh", "curl x 2>&1 | /bin/sh -s -- -y", "curl -fsSL x | nice -n 5 sh",
                     "wget -qO- x | /usr/local/bin/python3.11 -", "curl x | tee a | tee b | gunzip | zsh", 'curl "http://x/?a=1;b=2" | sh',
                     'eval "$(curl -fsSL http://x)"', "eval $(wget -qO- x)", "source <(curl http://x)", ". <(curl http://x)",
                     "/bin/sh -c \"$(curl -fsSL x)\"", "/usr/bin/env bash <(curl x)", "bash -c `wget -qO- x`"):
            with self.subTest(run=run_):
                only(self, "DF03", 2, message, f"FROM {PIN}\nRUN {run_}\nUSER app\n", "error")
        for run_ in ("curl x | tee f | sha256sum", "curl x; sh run.sh", "curl x && echo y | sh", "curl x || echo y | sh", "curl x | grep -c sh",
                     "curl x > f; cat f | sh", "echo done. $(date)", "curl -o f x && . /etc/profile", "ls | sh", "echo | /bin/sh", "curl x | wc -l | xargs echo",
                     "curl x | tee /tmp/f", 'curl "a|b" -o f', "curl x | cat > f ; sh f", "curl x\nsh f"):
            with self.subTest(ok=run_):
                self.assertEqual([], found(f"FROM {PIN}\nRUN {run_}\nUSER app\n"))

    def test_df02_a_source_held_in_an_arg_or_an_env(self):
        message = "apps/web/Dockerfile:{}: ADD of a URL without --checksum. Fix: download it in a RUN and check its digest, or add --checksum=sha256:..."
        for head, line in (("ARG URL=https://example.test/a.tgz\nADD $URL /opt/", 3), ("ARG BASE=https://example.test\nADD ${BASE}/a.tgz /opt/", 3),
                           ("ENV SRC=git://example.test/repo\nADD ${SRC} /src", 3), ('ARG URL="https://example.test/a.tgz"\nADD --chown=a:b ${URL} /opt/', 3),
                           ("ARG U=https://e.test/\nARG F=a.tgz\nADD ${U}${F} /opt/", 4), ("ARG U=${V:-https://e.test/a}\nADD $U /opt/", 3),
                           ("ENV SRC https://example.test/a.tgz\nADD $SRC /opt/", 3)):
            with self.subTest(head=head):
                only(self, "DF02", line, message.format(line), f"FROM {PIN}\n{head}\nUSER app\n", "error")
        for head in ("ARG URL\nADD $URL /opt/", "ARG FILE=a.tgz\nADD $FILE /opt/", f"ARG URL=https://e.test/a\nADD --checksum=sha256:{'0' * 64} $URL /opt/",
                     "ADD $URL /opt/\nARG URL=https://e.test/a", "ARG BASE=https://e.test\nADD ./${BASE##*/}.tgz /opt/", "ARG URL=https://e.test/a\nADD . /opt/"):
            with self.subTest(ok=head):
                self.assertEqual([], found(f"FROM {PIN}\n{head}\nUSER app\n"))

    def test_df03_across_continued_lines_and_in_a_heredoc(self):
        text = f"FROM {PIN}\nRUN apt-get update \\\n    && curl -fsSL https://example.test/i \\\n    | sh\nUSER app\n"
        self.assertEqual([("DF03", 2)], summary(found(text)))
        text = f"FROM {PIN}\nRUN <<EOF\nset -e\ncurl -fsSL https://example.test/i | sh\nEOF\nUSER app\n"
        self.assertEqual([("DF03", 2)], summary(found(text)))
        self.assertEqual([("DF03", 2)], summary(found(f"FROM {PIN}\nONBUILD RUN curl u | sh\nUSER app\n")))

    def test_df04_a_build_given_a_key_or_the_host(self):
        message = "apps/web/Dockerfile:2: a build is given no key and no host access. Fix: remove the option."
        for option in ("--security=insecure", "--network=host", "--mount=type=ssh", "--mount=type=secret,id=key",
                       "--mount=type=cache,target=/c --mount=type=secret,id=a", "--mount=id=x,type=ssh", "--mount=type=ssh,id=k --network=none"):
            with self.subTest(option=option):
                only(self, "DF04", 2, message, f"FROM {PIN}\nRUN {option} echo\nUSER app\n", "error")
        for option in ("--network=none", "--network=default", "--security=sandbox", "--mount=type=cache,target=/c", "--mount=type=bind,source=a,target=/b",
                       "--mount=type=tmpfs,target=/t"):
            with self.subTest(ok=option):
                self.assertEqual([], found(f"FROM {PIN}\nRUN {option} echo\nUSER app\n"))

    def test_df05_a_secret_written_in_the_image(self):
        message = "apps/web/Dockerfile:2: {} has a literal value. Fix: take it as a setting at start, not in the image."
        for line, name in (("ENV API_TOKEN=abc123", "API_TOKEN"), ("ENV API_TOKEN abc123", "API_TOKEN"), ("ARG DB_PASSWORD=hunter2", "DB_PASSWORD"),
                           ("ENV A=1 CLIENT_SECRET='s3cr3t'", "CLIENT_SECRET"), ('ENV GITHUB_API_KEY="k"', "GITHUB_API_KEY"),
                           ("ENV PASSPHRASE=x", "PASSPHRASE")):
            with self.subTest(line=line):
                only(self, "DF05", 2, message.format(name), f"FROM {PIN}\n{line}\nUSER app\n", "error")
        for line in ("ARG DB_PASSWORD", "ENV API_TOKEN=", "ENV TOKEN_URL=http://x", "ENV PASSWORD_FILE=/run/secrets/p", "ENV X=${Y}",
                     "ENV API_TOKEN=$TOKEN", "ARG VERSION=1.0", "ENV PORT=8000", "ENV PUBLIC_KEY=abc"):
            with self.subTest(ok=line):
                self.assertEqual([], found(f"FROM {PIN}\n{line}\nUSER app\n"))


class Warnings(unittest.TestCase):

    def test_df21_a_base_image_with_no_version(self):
        message = "apps/web/Dockerfile:1: the base image {} has no version. Fix: pin a tag."
        for image in ("python", "node:latest", "ghcr.io/acme/app", "localhost:5000/app", "acme/app:latest"):
            with self.subTest(image=image):
                only(self, "DF21", 1, message.format(image), f"FROM {image}\nUSER app\n", "warning")
        for image in (PIN, "python@sha256:" + "0" * 64, f"python:latest{DIGEST}", "scratch", "localhost:5000/app:1.2"):
            with self.subTest(ok=image):
                codes = {each.code for each in found(f"FROM {image}\nUSER app\n")}
                self.assertNotIn("DF21", codes)

    def test_df22_a_base_image_not_pinned_by_digest(self):
        message = ("apps/web/Dockerfile:1: the base image {} is not pinned by digest. Dependabot keeps name:tag@sha256:... "
                   "current.")
        for image in ("python:3.14-slim", "ghcr.io/acme/app:1.2", "localhost:5000/app:1.2", "node:24-alpine"):
            with self.subTest(image=image):
                only(self, "DF22", 1, message.format(image), f"FROM {image}\nUSER app\n", "warning")

    def test_df21_and_df22_are_not_both_said_of_one_image(self):
        self.assertEqual([("DF21", 1)], summary(found("FROM python\nUSER app\n")))
        self.assertEqual([("DF22", 1)], summary(found("FROM python:3\nUSER app\n")))

    def test_df23_the_final_stage_runs_as_root(self):
        message = "apps/web/Dockerfile: the final stage runs as root. Fix: add USER."
        for text in (f"FROM {PIN}\n", f"FROM {PIN}\nUSER root\n", f"FROM {PIN}\nUSER 0\n", f"FROM {PIN}\nUSER 0:0\n", f"FROM {PIN}\nUSER root:root\n",
                     f"FROM {PIN}\nUSER app\nUSER root\n", f"FROM {PIN}\nUSER app\nFROM {PIN}\nCMD x\n"):
            with self.subTest(text=text):
                out = found(text)
                self.assertEqual([("DF23", message)], [(each.code, each.message) for each in out], out)
                self.assertEqual("warning", out[0].severity)
        for text in (GOOD, f"FROM {PIN}\nUSER 1000\n", f"FROM {PIN}\nUSER app:app\n", f"FROM {PIN}\nUSER ${{APP_USER}}\n",
                     f"FROM {PIN}\nUSER root\nUSER app\n", f"FROM {PIN} AS a\nUSER root\nFROM {PIN}\nUSER app\n"):
            with self.subTest(ok=text):
                self.assertEqual([], found(text))

    def test_df23_the_line_is_the_one_of_the_final_from(self):
        self.assertEqual([("DF23", 3)], summary(found(f"FROM {PIN} AS build\nUSER app\nFROM {PIN}\nCMD x\n")))

    def test_df23_a_stage_that_is_built_on_a_stage_keeps_its_user(self):
        self.assertEqual([], found(f"FROM {PIN} AS base\nUSER app\nFROM base AS final\nCMD x\n"))
        self.assertEqual([("DF23", 3)], summary(found(f"FROM {PIN} AS base\nCMD x\nFROM base AS final\nCMD y\n")))
        self.assertEqual([], found(f"FROM {PIN} AS base\nUSER app\nFROM base\nCOPY --from=base /a /b\n"))

    def test_df24_a_copy_from_an_image(self):
        message = "apps/web/Dockerfile:3: copies from the image {}. It is listed in the summary with the base images."
        for source in ("nginx:1.27", "ghcr.io/acme/tool:2", "busybox"):
            with self.subTest(source=source):
                only(self, "DF24", 3, message.format(source), f"FROM {PIN} AS build\nUSER app\nCOPY --from={source} /a /b\n", "warning")
        for source in ("0", "build", "BUILD"):
            with self.subTest(ok=source):
                self.assertEqual([], found(f"FROM {PIN} AS build\nUSER app\nCOPY --from={source} /a /b\n"))
        self.assertEqual([], found(f"FROM {PIN}\nUSER app\nCOPY . /app\nCOPY --chown=app:app a b\n"))


class Reading(unittest.TestCase):

    def test_a_base_image_written_with_an_arg_is_the_one_it_defaults_to(self):
        self.assertEqual([("DF22", 3)], summary(found("ARG BASE=python:3.14-slim\n\nFROM ${BASE}\nUSER app\n")))
        self.assertEqual([("DF21", 2)], summary(found("ARG BASE=python\nFROM $BASE\nUSER app\n")))
        self.assertEqual([], found(f"ARG BASE={PIN}\nFROM ${{BASE}}\nUSER app\n"))
        self.assertEqual([], found("ARG BASE\nFROM ${BASE}\nUSER app\n"), "no default: it cannot be told")
        self.assertEqual([("DF22", 2)], summary(found("ARG V=3\nFROM python:${V}\nUSER app\n")))
        self.assertEqual([("DF22", 2)], summary(found("ARG V=3\nFROM python:${W:-4}\nUSER app\n")))

    def test_the_message_names_the_image_with_its_arg_put_in(self):
        self.assertIn("python:3.14-slim", found("ARG BASE=python:3.14-slim\nFROM ${BASE}\nUSER app\n")[0].message)

    def test_a_platform_flag_and_a_stage_name_are_read(self):
        text = "FROM --platform=$BUILDPLATFORM python:3.14 AS build\nUSER app\nFROM build\nCOPY --from=build /a /b\n"
        self.assertEqual([("DF22", 1)], summary(found(text)))

    def test_continued_lines_are_one_instruction_and_a_comment_between_is_skipped(self):
        text = f"FROM {PIN}\nRUN echo a \\\n    # a comment inside\n    && echo b \\\n\n    && curl u | sh\nUSER app\n"
        self.assertEqual([("DF03", 2)], summary(found(text)))

    def test_the_escape_character_can_be_a_backtick(self):
        text = f"# escape=`\nFROM {PIN}\nRUN curl u `\n   | sh\nUSER app\n"
        self.assertEqual([("DF03", 3)], summary(found(text)))
        self.assertEqual([], found(f"# escape=`\nFROM {PIN}\nENV A=C:\\Users\\app\nUSER app\n"))

    def test_the_body_of_a_heredoc_is_not_instructions(self):
        text = f"FROM {PIN}\nRUN <<EOF\nFROM python\nUSER root\nADD https://example.test/x /y\nEOF\nUSER app\n"
        self.assertEqual([], found(text))
        text = f"FROM {PIN}\nCOPY <<-EOT /etc/app.conf\n\tFROM x\n\tEOT\nUSER app\n"
        self.assertEqual([], found(text))
        text = f"FROM {PIN}\nRUN <<A <<B\none\nA\ntwo\nB\nUSER app\n"
        self.assertEqual([], found(text))

    def test_onbuild_is_looked_through(self):
        self.assertEqual([("DF05", 2)], summary(found(f"FROM {PIN}\nONBUILD ENV API_TOKEN=abc\nUSER app\n")))
        self.assertEqual([("DF02", 2)], summary(found(f"FROM {PIN}\nONBUILD ADD https://example.test/a /a\nUSER app\n")))

    def test_the_case_of_a_keyword_does_not_matter(self):
        self.assertEqual([("DF03", 2)], summary(found(f"from {PIN}\nrun curl u | sh\nuser app\n")))

    def test_windows_line_endings_are_read(self):
        self.assertEqual([("DF03", 2)], summary(found(f"FROM {PIN}\r\nRUN curl u | sh\r\nUSER app\r\n")))

    def test_an_empty_dockerfile_and_one_with_only_comments_have_nothing_to_say(self):
        self.assertEqual([], found(""))
        self.assertEqual([], found("# nothing\n\n# here\n"))
        self.assertEqual([], found("FROM\nUSER app\n"), "a FROM with no image is the builder's to refuse")

    def test_what_is_a_dockerfile(self):
        for name in ("Dockerfile", "apps/web/Dockerfile", "Dockerfile.dev", "app.Dockerfile", "dockerfile", "Dockerfile-test"):
            self.assertTrue(dockerfiles.is_dockerfile(name), name)
        for name in ("Dockerfiles.md", "docker-compose.yml", "README.md", "dockerfile.md.txt", "Dockerfile.json", "dockerfile-notes.yml"):
            self.assertFalse(dockerfiles.is_dockerfile(name), name)

    BAD = f"FROM {PIN}\nUSER app\nRUN curl -s https://example.test/i | sh\n"

    @staticmethod
    def unit(dockerfile=None, context=".", **more):
        build = f"build:\n      context: {context}\n" + (f"      dockerfile: {dockerfile}\n" if dockerfile else "")
        return {"playgrounds/shop/compose.yml": f"services:\n  web:\n    image: ghcr.io/acme/shop-web:1\n    {build}    pull_policy: never\n",
                "playgrounds/shop/README.md": "# Shop\n", **more}

    def test_a_file_the_build_of_a_unit_names_is_a_dockerfile_whatever_its_name(self):
        """Compose accepts any name for `build.dockerfile`: renaming the file must not turn the rules off."""
        names = ("build.docker", "Containerfile", "Dockerfile.yml", "Dockerfile.txt", "app.df", "docker/build", "Dockerfile.md", "prod.json")
        for name in names:
            with self.subTest(name=name):
                out = run(dockerfiles, self.unit(name, ".", **{f"playgrounds/shop/{name}": self.BAD}))
                self.assertEqual([("DF03", f"playgrounds/shop/{name}", 3)], [(each.code, each.path, each.line) for each in out])
        for context_, dockerfile, where in (("./app", "build.docker", "app/build.docker"), ("app", "../shared/x.df", "shared/x.df"),
                                            (".", "./docker/../b.df", "b.df"), ("${CTX:-app}", "${DF:-d.docker}", "app/d.docker"),
                                            ("app/", "sub/f", "app/sub/f")):
            with self.subTest(context=context_, dockerfile=dockerfile):
                out = run(dockerfiles, self.unit(dockerfile, context_, **{f"playgrounds/shop/{where}": self.BAD}))
                self.assertEqual([("DF03", f"playgrounds/shop/{where}")], [(each.code, each.path) for each in out])

    def test_a_build_with_a_string_and_the_default_name_judges_the_dockerfile_of_the_folder(self):
        files = {"playgrounds/shop/compose.yml": "services:\n  web:\n    image: ghcr.io/acme/shop-web:1\n    build: ./app\n    pull_policy: never\n",
                 "playgrounds/shop/app/Dockerfile": self.BAD}
        self.assertEqual(["DF03"], [each.code for each in run(dockerfiles, files)])

    def test_a_build_that_names_nothing_in_the_catalog_names_nothing(self):
        for context_, dockerfile in (("https://example.test/repo.git", "Dockerfile"), ("../../../outside", "build.docker"), ("${CTX}", "x.df"),
                                     (".", "/abs/x.df"), (".", "${NAME}"), (".", "../../../../x.df")):
            with self.subTest(context=context_):
                self.assertEqual([], run(dockerfiles, self.unit(dockerfile, context_, **{"playgrounds/shop/x.df": "not read\n"})))

    def test_a_file_of_a_unit_that_starts_like_a_dockerfile_is_judged_unnamed(self):
        for head in ("FROM", "from", "# syntax=docker/dockerfile:1\nFROM", "# comment\n\nARG V=1\nFROM", "# escape=`\nARG V=1\nFROM"):
            with self.subTest(head=head):
                text = f"{head} {PIN}\nUSER app\nRUN curl -s u | sh\n"
                out = run(dockerfiles, {"playgrounds/shop/apps/web/runner": text, "playgrounds/shop/apps/web/build.docker": text,
                                        "addons/hello/Containerfile": text})
                self.assertEqual({"playgrounds/shop/apps/web/runner", "playgrounds/shop/apps/web/build.docker", "addons/hello/Containerfile"},
                                 {each.path for each in out})
        self.assertEqual([], run(dockerfiles, {"docs/build.docker": self.BAD}), "outside a unit it is a document")
        for text in ("services:\n  a:\n    image: x\n", "# Title\n\nSome words\n", "#!/bin/sh\nFROM=1\n", "import os\nFROM = 1\n", "{}\n",
                     "FROM: a@b.c\n", "ARG only\n", "", "FROM\n", "\n\n\n", "RUN x\nFROM python\n"):
            with self.subTest(not_one=text):
                self.assertEqual([], run(dockerfiles, {"playgrounds/shop/data.txt": text, "addons/hello/script.sh": text,
                                                       "addons/hello/data.yml": text}))
        for name in ("logo.png", "guide.md", "doc.pdf", "font.woff2"):
            self.assertEqual([], run(dockerfiles, {f"addons/hello/{name}": self.BAD}), "never read for this")

    def test_the_units_the_caller_loaded_name_their_builds_too(self):
        listing = support.tree({"addons/hello/build.docker": self.BAD})
        units = [{"kind": "addon", "id": "hello", "file": "addons/hello/compose.yml",
                  "config": {"services": {"a": {"image": "x", "build": {"context": ".", "dockerfile": "build.docker"}}}}}]
        self.assertEqual(["DF03"], [each.code for each in dockerfiles.check(listing, context(listing, units=units))])

    def test_a_dockerfile_is_read_up_to_what_st09_lets_it_be(self):
        """It has no suffix, so it may weigh 2 MB: padding it to a megabyte must not turn the rules off."""
        pad = "# padding\n" * (140 * 1024)   # 1.4 MB
        bad = f"FROM {PIN}\nUSER app\n{pad}RUN curl -s u | sh\n"
        out = run(dockerfiles, {"apps/web/Dockerfile": bad, "apps/web/other.df": bad.replace("curl", "wget")})
        self.assertEqual([("DF03", "apps/web/Dockerfile")], [(each.code, each.path) for each in out], "only a Dockerfile is judged by its name")
        self.assertEqual([], run(dockerfiles, {"apps/web/Dockerfile": pad * 2 + "RUN curl -s u | sh\n"}), "past 2 MB it is ST09's")

    def test_the_rules_run_over_a_tree_of_files(self):
        out = run(dockerfiles, {"apps/a/Dockerfile": "FROM python\n", "apps/b/web.Dockerfile": f"FROM {PIN}\nUSER app\n",
                                "apps/c/Dockerfile.big": b"x" * (1024 * 1024 + 1), "apps/d/Dockerfile": b"\0\0FROM python\n"})
        self.assertEqual([("DF21", "apps/a/Dockerfile"), ("DF23", "apps/a/Dockerfile"), ("DF21", "apps/d/Dockerfile"),
                          ("DF23", "apps/d/Dockerfile")], [(each.code, each.path) for each in out], "a NUL byte hides nothing")

    def test_the_fixture_with_every_rule_in_it(self):
        out = found((DATA / "bad.Dockerfile").read_text())
        counts = {}
        for each in out:
            counts[each.code] = counts.get(each.code, 0) + 1
        self.assertEqual({"DF01": 1, "DF02": 2, "DF03": 3, "DF04": 4, "DF05": 2, "DF21": 2, "DF22": 1, "DF23": 1, "DF24": 1}, counts)

    def test_the_messages_of_the_table_are_section_6(self):
        self.assertEqual({"DF01", "DF02", "DF03", "DF04", "DF05", "DF21", "DF22", "DF23", "DF24"}, set(dockerfiles.RULES))
        self.assertEqual(["error"] * 5 + ["warning"] * 4, [dockerfiles.RULES[code].severity for code in sorted(dockerfiles.RULES)])
        self.assertEqual("{path}: the final stage runs as root. Fix: add USER.", dockerfiles.RULES["DF23"].message)


class Hostile(unittest.TestCase):

    PIECES = ["curl ", "| ", "curl | sh ", "$(curl", "<(wget", "`curl", "FROM ", "FROM a AS ", "RUN ", "ADD ", "ENV A=", "ENV ", "ARG ",
              "| /bin/", "| env ", "| xargs ", "| sudo -E env A=b ", "eval ", "source ", ". <(curl ", "; ", "| gunzip ", "| tee f ", "ADD $", "ARG U=a ",
              "${U}/", "/usr/bin/env python3 ", "curl | env env env sh ", "curl x | " + "a" * 100 + "/",
              "COPY --from=", "USER ", "ONBUILD ", "# syntax=", "<<", "${", "${a-", "$a", "a=b ", "a=\"", "'", '"', "\t", " ", "x",
              "--mount=type=", "--platform=", "sudo ", "sh", "|| ", "&& ", "# ", "FROM ${a-${b-", "ENV PASSWORD=x PASSWORD=y ",
              "COPY --from=x --from=y ", "\\ ", "\\"]

    def test_every_form_the_reader_looks_for_finishes_in_under_a_second(self):
        for piece in self.PIECES:
            for head in ("", "FROM python:3\nRUN ", "FROM python:3\nENV "):
                text = head + piece * (1024 * 1024 // len(piece))
                best = min(support.timed(dockerfiles.check_text, PATH, text)[1] for _ in range(2))
                self.assertLess(best, 1.0, f"{head!r} + {piece[:20]!r}: {best:.2f} s")

    def test_a_megabyte_of_short_lines_is_read_in_proportion_to_its_size(self):
        for piece in ("wget | bash\n", "RUN <<EOF\n", "<<EOF\n", "# escape=`\n", "\n", "\\\n", "\\\n\n", "ARG A=${A}\nFROM ${A}\n"):
            text = "FROM python:3\n" + piece * (1024 * 1024 // len(piece))
            best = min(support.timed(dockerfiles.check_text, PATH, text)[1] for _ in range(2))
            self.assertLess(best, 5.0, f"{piece!r}: {best:.2f} s")

    def test_a_dockerfile_of_many_instructions_is_read_in_proportion(self):
        text = "FROM python:3\n" + "RUN echo a && curl u -o f\nENV A=b\nCOPY . .\n" * 20000
        _, seconds = support.timed(dockerfiles.check_text, PATH, text)
        self.assertLess(seconds, 5.0)

    def test_a_long_continued_instruction_does_not_copy_itself_for_each_line(self):
        text = "FROM python:3\nRUN echo \\\n" + "    a \\\n" * 100000 + "    b\n"
        _, seconds = support.timed(dockerfiles.check_text, PATH, text)
        self.assertLess(seconds, 2.0)


class Calibration(unittest.TestCase):
    """The Dockerfiles of this checkout, the Lab's seeder: judged as a catalog's would be, they hold no error."""

    def names(self):
        try:
            done = subprocess.run(["git", "ls-files", "-z"], cwd=support.ROOT, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is None or done.returncode != 0:
            self.skipTest("this checkout is not a git work tree")
        return [name for name in (each.decode() for each in done.stdout.split(b"\0") if each)
                if name.rsplit("/", 1)[-1] == "Dockerfile" and "/tests/" not in name and not name.startswith(("lab-ui/", ".github/"))]

    def test_the_dockerfiles_of_the_engine_give_no_error(self):
        names = self.names()
        self.assertTrue(names)
        for name in names:
            with self.subTest(name=name):
                self.assertEqual([], [each for each in found((support.ROOT / name).read_text(), name) if each.severity == "error"])

    def test_the_tree_of_the_engine_gives_the_same(self):
        names = self.names()
        listing = support.ListTree.from_files({name: (support.ROOT / name).read_bytes() for name in names})
        out = dockerfiles.check(listing, context(listing))
        self.assertEqual([], [each for each in out if each.severity == "error"])


if __name__ == "__main__":
    unittest.main()
