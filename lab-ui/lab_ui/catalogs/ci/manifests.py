"""`catalog.py ci manifests PATH --units JSON`: every image the touched units pull exists in its registry. It answers the
most frequent mistake of a contributor, a version that does not exist or a tag a registry removed, which nothing else in a pull
request sees: the smoke starts only what needs no platform, and the platform run is for maintainers. Without it a pull request
that bumps a platform image to a tag nobody published is green and merges.

For each unit it reads the images as Compose resolves them, the core's and the unit's, with the scrubbed environment and
the defaults of the core, and again for each set of versions of the unit's `x-gravitee.tested` (the set's variables are put on
top, and only those that are the version or the registry of an image, as `e2e.versions` decides). It leaves out the images the
unit builds, which are in no registry yet, and asks each of the others once for its manifest with a HEAD request, which a
registry does not count as a pull: `.github/scripts/image_manifests.py` does the asking (the engine's script, imported by its path, its
command line unchanged), including the refusal of a registry that is on the machine's own network and the anonymous token.

    MF01  the registry answered that the tag does not exist             an error
    MF02  a registry that did not answer (unreachable, slow, private, refused): the image was not checked   a warning

Only an answered "no such tag" fails. A stage that cannot reach the network is not a verdict on the catalog. It holds no secret:
the requests carry no token of the runner. Compose is run only for a unit the Lab's own screen and policy let through (a unit
that was refused is a finding of `check`, said here too and never read), and only through `ci.load`.

Python 3.9 and later, standard library only."""

import importlib.util
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ... import paths
from .. import report
from ..report import Problem
from . import IMAGE_NAME, Loaded, Unavailable, conclude, entry, load, parse_units, parsed, parser, problem, refusals

SCRIPT = (".github", "scripts", "image_manifests.py")


def script():
    """The engine's `image_manifests.py` as a module, loaded from the Lab this runs in. Its command line is not run: the module
    has none to run when it is imported."""
    where = Path(paths.ROOT).joinpath(*SCRIPT)
    if not where.is_file():
        raise Unavailable("this Lab has no .github/scripts/image_manifests.py, which asks the registries")
    spec = importlib.util.spec_from_file_location("image_manifests", str(where))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def images_of(unit: Loaded, version_sets: Callable, notes: List[str]) -> List[str]:
    """The images `unit` pulls: those of its resolved JSON with the core's defaults, and those of each set of versions of its
    `x-gravitee.tested`. A set that cannot be read is a sentence in `notes`, never a guess; so is an image name that is not a
    name, which no registry is asked about."""
    from ...docker import compose
    from ... import e2e
    found = set(version_sets(unit.config.get("services") or {}))
    sets = [each for each in unit.tested if each]
    if sets and unit.resolve is None:
        notes.append(f"{unit.key}: the sets of versions it was tried with were not read")
    for number in range(len(sets) if unit.resolve is not None else 0):
        try:
            chosen = e2e.versions({"name": unit.name, "tested": sets}, number, None if unit.variables is None else set(unit.variables))
        except ValueError:
            notes.append(f"{unit.key}: set {number} of x-gravitee.tested sets something that is not the version or the "
                         "registry of an image, so it was not read")
            continue
        try:
            config = unit.resolve(chosen)
        except (compose.ComposeError, ValueError) as error:
            notes.append(f"{unit.key}: set {number} of x-gravitee.tested could not be read: {report.printable(str(error))[:120]}")
            continue
        found |= set(version_sets(config.get("services") or {}))
    names = sorted(each for each in found if IMAGE_NAME.fullmatch(each))
    for each in sorted(found - set(names)):   # (Compose does not check what an image is called: a registry is not asked about this)
        notes.append(f"{unit.key}: an image name that is not a name was not asked: {report.printable(each)[:40]}")
    return names


def verdicts(images: Sequence[str], ask: Callable, check: Callable) -> Dict[str, Optional[bool]]:
    """{image: True (there), False (the registry has no such tag), None (it did not say)}, asked in parallel by the script's
    `check`. What the script's `exists` does not expect, an error of the request that it does not catch, is None too: a registry
    that answers strangely did not say."""
    def safe(image: str):
        try:
            return ask(image)
        except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
            return None
    return check(list(images), safe)


def findings(units: Sequence[Loaded], results: Dict[str, Optional[bool]], owners: Dict[str, str],
             reference: Callable) -> List[Problem]:
    """MF01 for an image whose tag a registry said it does not have, MF02 for a registry that did not answer, each once, at the
    compose file of the first unit that pulls the image."""
    where = {unit.key: unit.file for unit in units}
    found: List[Problem] = []
    for image, there in sorted(results.items()):
        host, _, tag = reference(image)
        path = where.get(owners.get(image, ""), "")
        if there is False:
            found.append(problem("MF01", path, 0, image=image, tag=tag))
        elif there is None:
            found.append(problem("MF02", path, 0, registry=host, image=image))
    return found


def run(units: Sequence[Loaded], ask: Optional[Callable] = None,
        module=None) -> Tuple[List[Problem], Dict[str, Optional[bool]], List[str]]:
    """(the findings, {image: answer}, the sentences of what could not be read) for these units. `ask(image)` is the registry's
    answer (the script's `exists`); `module` the script as a module. The tests give their own."""
    module = module or script()
    asking = ask or module.exists
    notes: List[str] = []
    owners: Dict[str, str] = {}
    for unit in units:
        for image in images_of(unit, module.pulled, notes):
            owners.setdefault(image, unit.key)
    results = verdicts(sorted(owners), asking, module.check)
    return findings(units, results, owners, module.reference), results, notes


def lines(results: Dict[str, Optional[bool]]) -> List[str]:
    """What a person reads: a mark and the image, for each image, in the order of the names."""
    marks = {True: "✓", False: "✗", None: "?"}
    return [f"{marks[there]} {report.printable(image)}" + ("" if there is not None else ": the registry did not say")
            for image, there in sorted(results.items())]


@entry
def main(argv, out, err, **seams) -> int:
    """`ci manifests`. Exit 1 for an image whose tag is missing (MF02 alone is a warning: 0), 2 when the units cannot be read here."""
    found = parser("manifests", "Every image the touched units pull exists in its registry.", units=True)
    args = parsed(found, argv)
    names = parse_units(args.units)
    if not os.path.isdir(args.path):
        raise Unavailable(f"{report.printable(args.path)} is not a folder")
    loading = load(os.path.abspath(args.path), names)
    problems = refusals(loading.refused, "read")
    skipped = list(loading.notes)
    if loading.units:
        more, results, notes = run(loading.units, seams.get("ask"), seams.get("module"))
        problems += more
        skipped += notes
        if args.format != "json":
            out.write("\n".join(lines(results)) + "\n" if results else "")
    return conclude("manifests", problems, args, out, "make ci", skipped, title="Every image exists in its registry")
