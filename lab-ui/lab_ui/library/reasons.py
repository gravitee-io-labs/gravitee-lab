"""Why a service runs behind what is saved, as one plain line a person understands. Pure.

A service has one of three causes, the first that holds: another image tag is saved than the one that runs (`tag`),
the image its tag names here is not the one that runs (`image`, a pull or a build since it started), or its
configuration changed since it started (`config`, with the settings and the labels that differ when drift.py told
them). A cause that is not established is never claimed: the last one says only that the configuration changed. Never a
value: a tag and a setting's name are all a line holds. The same causes are given structured (causes()), for the page to draw
them: the kind, the tags saved and running (and whether the one saved is `older`, which Apply goes back to), the names of
the settings, the services a cause is about."""

from ..server import dashboard

SHOWN = 3   # names a line spells out before it counts the rest


def why(service, container, cause, settings=(), labels=False):
    """The cause of one service that runs behind what is saved, `cause` being model.lateness()'s answer for it:
    {kind: tag, saved, running}, {kind: image, tag, built} or {kind: config, settings, labels}. `container`: what ps()
    says of it. An image that Docker names by its id (a tag that a pull moved) tells no tag: it is a newer image."""
    running = _tag(container.get("image"))
    if running and service["tag"] and running != service["tag"]:
        return {"kind": "tag", "saved": service["tag"], "running": running}
    if cause == "image":
        return {"kind": "image", "tag": service["tag"], "built": bool(service["built"])}
    return {"kind": "config", "settings": list(settings), "labels": bool(labels)}


def _tag(image):
    image = image or ""
    last = image.split("/")[-1]
    return last.split(":")[-1] if ":" in last and "@sha256:" not in image and not image.startswith("sha256:") else ""


def spelled(items, limit=SHOWN):
    """Items as a line says them: the first few, then how many are left."""
    items = list(items)
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _versions(saved, running):
    """The two tags as a line says them: without the system they share ("4.12.20-debian" and "4.13.0-alpha.4-debian"
    are 4.12.20 and 4.13.0-alpha.4), whole when the system is what differs."""
    (one, system), (other, then) = dashboard.bare(saved), dashboard.bare(running)
    return (one, other) if system == then and one != other else (saved, running)


def _subject(names, labels, sole):
    """Who a clause is about, "" for a service whose row says which it is, and for the one clause of an owner whose every
    service is behind (`sole`: it speaks of the owner). Else the services by label while they are few, then how many."""
    if labels is None or sole:
        return ""
    return spelled(labels[name] for name in names) if len(names) <= SHOWN else f"{len(names)} containers"


def _clauses(whys):
    """What the services say, grouped by what they say: [(names, text, plural wording or None, cause)], in the order tag,
    image, configuration. `cause` is the clause as data: {kind: tag, saved, running, older} (None for both tags when there
    are too many to say), {kind: image, tag, built}, {kind: settings, settings} or {kind: config}."""
    clauses, tags = [], {}
    for name, found in whys.items():
        if found["kind"] == "tag":
            tags.setdefault(_versions(found["saved"], found["running"]), []).append(name)
    if len(tags) > SHOWN:   # too many tags for a line: say that, the rows say which
        clauses.append(([name for group in tags.values() for name in group], "the saved image differs from the one that runs",
                        "the saved images differ from the ones that run", {"kind": "tag", "saved": None, "running": None, "older": False}))
    else:
        clauses.extend((names, f"{saved} is saved, {running} still runs", None,
                        {"kind": "tag", "saved": saved, "running": running, "older": dashboard.older(saved, running)})
                       for (saved, running), names in tags.items())
    images = [name for name, found in whys.items() if found["kind"] == "image"]
    if images:
        single = whys[images[0]]
        one = len(images) == 1
        clauses.append((images, "its image was built again since it started" if single["built"] and one
                        else f"image {single['tag']} changed since it started" if one
                        else "their images changed since they started", None,
                        {"kind": "image", "tag": single["tag"] if one else None, "built": bool(single["built"] and one)}))
    configs = {}
    for name, found in whys.items():
        if found["kind"] == "config":
            configs.setdefault((tuple(found["settings"]), found["labels"]), []).append(name)
    for (settings, labels), names in configs.items():
        what = "settings" if settings else "configuration"   # the labels Compose sets are no word of a person's: they are the configuration
        clauses.append((names, f"its {what} changed since it started" + (f": {spelled(settings)}" if settings else ""),
                        f"their {what} changed since they started" + (f": {spelled(settings)}" if settings else ""),
                        {"kind": "settings", "settings": list(settings)} if settings else {"kind": "config"}))
    return clauses


def line(whys, labels=None, owner="", whole=False):
    """One line for these services, {name: why()}: their causes grouped, in the order tag, image, config. `labels`:
    {name: how a person calls it}, None for a single service whose row says which it is. `owner`: what the services
    belong to ("API Management"), and `whole`: every one of its services is behind. The line then begins with the owner
    ("API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs."), and the services a clause is about follow it in
    brackets ("Databases: MongoDB (8.0.29 is saved, 8.3.11 still runs)") when they are not all of them. On the owner's own
    row, with no `owner`, it begins with nothing. Empty when none runs behind."""
    clauses = _clauses(whys)
    sole = whole and len(clauses) == 1
    said = []
    for names, text, plural, _ in clauses:
        subject = _subject(names, labels, sole)
        wording = plural if plural and len(names) > 1 and not (sole and owner) else text
        if owner:
            said.append(f"{subject} ({wording})" if subject else wording)
        else:
            said.append(f"{subject}: {wording}" if subject else wording[:1].upper() + wording[1:])
    return (f"{owner}: " if owner else "") + "; ".join(said) + "." if said else ""


def causes(whys, labels=None, whole=False):
    """The causes of line(), as data for the page to draw ([{kind, ..., services}], in the same order and grouping):
    `services` names the services a cause is about by `labels` (three at most, else how many), empty when the line would
    name none (a single service, whose row says which; the one cause of an owner whose every service is behind). Never a
    value: tags and the names of settings only."""
    found = _clauses(whys)
    sole = whole and len(found) == 1
    return [dict(cause, services=[] if labels is None or sole else [labels[name] for name in names] if len(names) <= SHOWN
                 else [f"{len(names)} containers"]) for names, _, _, cause in found]
