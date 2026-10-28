"""The Gravitee releases newer than the defaults of the core: the core follows Gravitee's releases as soon as they are out
(docs/maintaining.md), so that a first start runs the latest one and the Dashboard shows no update. The other images keep
Dependabot's week.

    python3 -I .github/scripts/gravitee_versions.py [--github-output]

A default is a `${NAME_VERSION:-X.Y.Z}` of an image of Gravitee (`${NAME_REGISTRY:-graviteeio}/repository`) in
docker-compose.yml; the newest is the highest X.Y.Z tag of its repository on Docker Hub. Prints a line for each default with a
newer release, nothing when there is none. With --github-output it also writes them as `body` to the file GITHUB_OUTPUT
names, for the next step to put in an issue."""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE = re.compile(r"image:\s*\$\{(\w+)_REGISTRY:-graviteeio\}/([\w.-]+):\$\{(\w+_VERSION):-(\d+\.\d+\.\d+)\}")
RELEASE = re.compile(r"\d+\.\d+\.\d+")
TAGS = "https://hub.docker.com/v2/repositories/graviteeio/{}/tags?page_size=100&ordering=last_updated"
PAGES = 3   # the newest tags come first: a release is among the last 300 pushed


def defaults(text):
    """{version variable: (its default, the repository of the first image that names it)} of a compose file."""
    found = {}
    for _, repository, variable, version in IMAGE.findall(text):
        found.setdefault(variable, (version, repository))
    return found


def number(version):
    return tuple(int(part) for part in version.split("."))


def newest(tags):
    """The highest release (X.Y.Z, nothing after it) among tag names; None for none."""
    return max((tag for tag in tags if RELEASE.fullmatch(tag)), key=number, default=None)


def tags(repository):
    url, names = TAGS.format(repository), []
    for _ in range(PAGES):
        with urllib.request.urlopen(url, timeout=30) as answer:
            page = json.load(answer)
        names += [tag["name"] for tag in page.get("results") or []]
        url = page.get("next")
        if not url:
            break
    return names


def newer(found, read=tags):
    """[(variable, default, newest)] of the defaults that a newer release passes."""
    lines = []
    for variable, (version, repository) in sorted(found.items()):
        latest = newest(read(repository))
        if latest and number(latest) > number(version):
            lines.append((variable, version, latest))
    return lines


def main(argv):
    lines = [f"- `{variable}`: {version} → {latest}"
             for variable, version, latest in newer(defaults((ROOT / "docker-compose.yml").read_text(encoding="utf-8")))]
    print("\n".join(lines))
    if "--github-output" in argv:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("body<<EOF\n" + "\n".join(lines) + "\nEOF\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
