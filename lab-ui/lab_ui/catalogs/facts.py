"""What an update changes in an item that a person decides on, in words: the versions of Gravitee it was tested on, what it
needs (the versions of its images, the features of the license, what this machine runs) and its settings. Read from the
item as the Lab builds it, before and after (`project` units: `requires`, `tested`, `settings`), so that every catalog is
said alike, whatever its files hold. Pure: no file, no Git."""

from typing import Iterable, List

LIMIT = 8   # the lines said of one item: past them, how many more
# The subject of the commit the catalogs' Gravitee versions workflow makes when it records what works (versions.yml): the sets it
# names are said precisely by `facts` ("Tested on APIM_VERSION 4.13.3"), so an update does not say them a second time as its notes
RECORDED = "Tested on the newest Gravitee releases"


def _versions(one) -> str:
    """A tested set as a person reads it: its versions, "APIM_VERSION 4.13.3, AM_VERSION 4.13.3" (the registries are where the
    images come from, not what they are)."""
    return ", ".join(f"{key} {value}" for key, value in sorted((one or {}).items()) if key.endswith("_VERSION"))


def _names(value) -> List[str]:
    """The features of a license or the needs of the machine, as names: a list of names, a {name: why} or a list of {name}."""
    if isinstance(value, dict):
        return sorted(str(key) for key in value)
    names = [one.get("name") if isinstance(one, dict) else one for one in value or []]
    return sorted(str(name) for name in names if name)


def _keys(settings) -> dict:
    return {one["key"]: one for one in settings or [] if isinstance(one, dict) and one.get("key")}


def _requires(unit, part):
    return ((unit or {}).get("requires") or {}).get(part)


def facts(before: dict, after: dict) -> List[str]:
    """What changes from `before` to `after` (one item of a catalog, as two versions build it), one sentence each, in the order a
    person weighs them: what it needs, then what it was tested on, then its settings. At most LIMIT, the rest counted."""
    lines = []
    old, new = _requires(before, "images") or {}, _requires(after, "images") or {}
    for key in sorted(set(old) | set(new)):
        if key not in old:
            lines.append(f"Needs {key} {new[key]}")
        elif key not in new:
            lines.append(f"No longer needs a given {key}")
        elif old[key] != new[key]:
            lines.append(f"Needs {key} {new[key]}, was {old[key]}")
    lines += _gained("Needs the license feature", "No longer needs the license feature",
                     _names(_requires(before, "license")), _names(_requires(after, "license")))
    lines += _gained("Needs", "No longer needs", _names(_requires(before, "host")), _names(_requires(after, "host")))
    tested = [_versions(one) for one in (before or {}).get("tested") or []]
    lines += [f"Tested on {said}" for said in dict.fromkeys(_versions(one) for one in (after or {}).get("tested") or [])
              if said and said not in tested]
    old_settings, new_settings = _keys((before or {}).get("settings")), _keys((after or {}).get("settings"))
    for key in sorted(set(new_settings) - set(old_settings)):
        lines.append(f"New setting {key}" + (", required" if new_settings[key].get("required") else ""))
    lines += [f"Setting {key} removed" for key in sorted(set(old_settings) - set(new_settings))]
    if len(lines) > LIMIT:
        return lines[:LIMIT - 1] + [f"And {len(lines) - LIMIT + 1} more"]
    return lines


def _gained(needs: str, no_longer: str, old: Iterable[str], new: Iterable[str]) -> List[str]:
    old, new = set(old), set(new)
    return [f"{needs} {name}" for name in sorted(new - old)] + [f"{no_longer} {name}" for name in sorted(old - new)]
