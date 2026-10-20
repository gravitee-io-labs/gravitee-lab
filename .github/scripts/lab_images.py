"""Builds the images the lab builds, for Apple silicon and Intel alike, and pushes them to a registry: the seeder, and
those of the add-ons and playgrounds found under the roots it is given (the shared ones of this repository by default).

What an add-on or a playground builds is what Compose says it builds: each service with a `build` section, read
through the core and the item together, as the lab runs them. The seeder is built from the `seeder/` folder of this
repository under the name `x-gravitee.seeder.image` of the core file gives, whatever the items say, so it is built even
where there is no item. An image several items build is built once. Each is pushed as <registry>/<name>:latest and
:sha-<commit>, where <name> is the last part of its image name: the item's `ghcr.io/<owner>/gravitee-lab/<name>:latest`
with `pull_policy: always` then pulls it.

    REGISTRY=ghcr.io/<owner>/gravitee-lab python3 .github/scripts/lab_images.py [--dry-run]

With --dry-run it prints the builds instead of running them, and REGISTRY is optional.

Discovery (`items`, `builds`, `seeder`) is apart from building (`command`, `main`): the label and tag logic is what
another builder of images imports.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = "linux/amd64,linux/arm64"
PLACEHOLDER_REGISTRY = "ghcr.io/<owner>/gravitee-lab"
# The license of this repository (SPDX identifier), stated by the images built from its own files
LICENSE = "AGPL-3.0-only"
# The file that includes one item, for Compose to read it with the core (git-ignored)
INCLUDE_FILE = ROOT / ".lab-images.tmp"
# The folders whose subfolders are items to build, when none are given
ROOTS = ("addons", "playgrounds")
# The seeder: what units build it from, and its file in that folder
SEEDER = ROOT / "seeder"
SEEDER_FILE = "Dockerfile"


def include_file(name):
    """What the file that includes the compose.yml of the item folder `name` holds, as UTF-8 bytes. The path is a
    double-quoted scalar (JSON is one) so that Compose reads the name as it is: a folder called `a: b` or `a #1` would
    otherwise read as a mapping or a comment and the item would be left out without a word. Its `$` is doubled, which
    is how Compose is told not to interpolate it."""
    path = json.dumps(name.replace("$", "$$") + "/compose.yml", ensure_ascii=False)
    return f"include:\n  - {path}\n".encode("utf-8")


def render(item=None):
    """What Compose makes of the core, or of the core with this item included, as a dict. Exits with Compose's words
    when it cannot read them."""
    command = ["docker", "compose", "-f", "docker-compose.yml"]
    name = "docker-compose.yml"
    if item is not None:
        name = Path(os.path.relpath(item, ROOT)).as_posix()
        try:
            text = include_file(name)
        except UnicodeEncodeError:   # a name that is not text (a lone surrogate): git allows any bytes in a name
            sys.exit(f"{name!r}: the name of this folder is not valid text, so Compose cannot be asked to read it")
        command += ["-f", INCLUDE_FILE.name]
    try:
        if item is not None:
            INCLUDE_FILE.write_bytes(text)
        done = subprocess.run(
            [*command, "config", "--format", "json"], cwd=ROOT, capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if k != "COMPOSE_FILE"})
    finally:
        if item is not None:
            INCLUDE_FILE.unlink(missing_ok=True)
    if done.returncode != 0:
        sys.exit(f"{name}: Compose cannot read it: {done.stderr.strip()}")
    return json.loads(done.stdout)


def image_name(image):
    """The last part of an image name, without its tag: ghcr.io/owner/gravitee-lab/seeder:latest is seeder."""
    return image.rsplit("/", 1)[-1].split(":")[0]


def items(roots=None):
    """The folders of the add-ons and playgrounds to build: those with a compose.yml in each of these roots (the
    add-ons and playgrounds of this repository when none are given), by name in each, the personal ones aside. A root
    that is not there holds none, which is what an engine with no content is."""
    if roots is None:
        roots = [ROOT / name for name in ROOTS]
    found = []
    for root in roots:
        root = Path(root)
        if root.is_dir():
            found += sorted(path for path in root.iterdir()
                            if (path / "compose.yml").is_file() and path.name != "local")
    return found


def builds(item):
    """The services of an add-on or a playground that Compose builds: {image name: build section}."""
    services = render(item)["services"]
    return {image_name(service["image"]): service["build"]
            for service in services.values() if service.get("build") and service.get("image")}


def seeder():
    """The seeder image, built from this repository's own `seeder/` folder: {image name: build section}. Its name is
    the `x-gravitee.seeder.image` of the core file, which is also the one the seed services of the items use."""
    image = ((render().get("x-gravitee") or {}).get("seeder") or {}).get("image")
    if not isinstance(image, str) or not image:
        sys.exit("docker-compose.yml: x-gravitee.seeder.image is not set, so the seeder has no name to be built under")
    if not (SEEDER / SEEDER_FILE).is_file():
        sys.exit(f"{SEEDER.relative_to(ROOT).as_posix()}/{SEEDER_FILE} is missing: there is nothing to build the "
                 "seeder from")
    return {image_name(image): {"context": str(SEEDER), "dockerfile": SEEDER_FILE}}


def is_remote(context):
    """Whether a build context is fetched from a URL or a git repository, not read from this repository."""
    return context.startswith(("http://", "https://", "git@"))


def settings(dry_run):
    """(registry, commit, source) of this run, from the environment: where to push, the short commit that tags the
    images, and the address of the repository they come from (None outside CI)."""
    registry = os.environ.get("REGISTRY", "").lower()
    if not registry:
        if not dry_run:
            sys.exit("REGISTRY is not set. Usage: REGISTRY=ghcr.io/<owner>/gravitee-lab "
                     "python3 .github/scripts/lab_images.py [--dry-run]")
        registry = PLACEHOLDER_REGISTRY
    commit = os.environ.get("GITHUB_SHA", "local")[:7]
    repository = os.environ.get("GITHUB_REPOSITORY")
    source = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}" if repository else None
    return registry, commit, source


def command(registry, name, build, commit, source):
    """The buildx command that builds and pushes one image: its tags, its labels (the license when the source is this
    repository's own, where it comes from when known), and what Compose says about how to build it. No cache, as in the
    catalogs (WF17): a build is rare, and what it reads is only what main holds."""
    image = f"{registry}/{name}"
    result = ["docker", "buildx", "build", "--platform", PLATFORMS, "--push",
              "-t", f"{image}:latest", "-t", f"{image}:sha-{commit}"]
    # An image built from another project's source keeps that project's license
    if not is_remote(build["context"]):
        result += ["--label", f"org.opencontainers.image.licenses={LICENSE}"]
    if source:
        result += ["--label", f"org.opencontainers.image.source={source}"]
    if build.get("dockerfile") and not is_remote(build["context"]):
        result += ["-f", str(Path(build["context"]) / build["dockerfile"])]
    elif build.get("dockerfile"):
        result += ["-f", build["dockerfile"]]
    for key, value in (build.get("args") or {}).items():
        result += ["--build-arg", f"{key}={value}"]
    result.append(build["context"])
    return result


def main(dry_run, roots=None):
    registry, commit, source = settings(dry_run)
    found = {}
    for item in items(roots):
        found.update(builds(item))
    found.update(seeder())
    for name, build in sorted(found.items()):
        print(f"== {registry}/{name}", flush=True)
        build_command = command(registry, name, build, commit, source)
        if dry_run:
            print(" ".join(build_command))
        else:
            subprocess.run(build_command, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Builds the lab's images for amd64 and arm64 and pushes them to REGISTRY.",
        epilog="REGISTRY, in the environment, is where to push: ghcr.io/<owner>/gravitee-lab for example.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the builds instead of running them; REGISTRY is optional")
    main(parser.parse_args().dry_run)
