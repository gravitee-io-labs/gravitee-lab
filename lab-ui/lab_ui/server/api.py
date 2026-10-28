"""The Lab UI over HTTP: the route table, the state the page draws, job output, logs and the load of the containers as
event streams, the static files of web/, and the command line."""

import argparse
import errno
import hashlib
import http.client
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from ..library import check, drift, layers, layout, machine, model, personal, playgrounds, project, starred, updates
from ..docker import compose, files, images, plugins, probe, terminal
from ..gravitee import core, license, places, platforms
from . import dashboard, stack
from .. import paths
from ..catalogs import contribute, exports, firstrun, kept, moving, service, standalone, trust
from ..catalogs.registry import CatalogError
from ..docker.jobs import JOBS, Job


class TooLarge(Exception):
    """A request body bigger than the page ever sends."""


# What a failure answers, first match. A CatalogError comes first and has a status of its own and a code: the answer is
# {"error": the line of the catalogue of errors, "code": the code}. Anything else is a 500, its message headed by its type: a KeyError alone
# would say only the key. socket.timeout is a TimeoutError only since 3.10. A ConnectionError is a registry or the download
# site hanging up: urllib does not wrap it.
ERRORS = [(TooLarge, 413), (ValueError, 400), (paths.NotFound, 404), (RuntimeError, 409),
          ((URLError, TimeoutError, socket.timeout, ConnectionError), 502)]
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png"}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")   # a log line's timestamp, as `logs --timestamps` writes it
FOLLOW_AGAIN = 2   # seconds between two looks at a container whose logs ended, until it runs again
STATS_EVERY = 1    # seconds between two events of the containers' load
# The page is HTML in the origin that holds the keys to Docker, and a guide is third-party HTML in it: it runs only the scripts it
# loads itself (its own files and the pinned directories of the CDN libraries it loads with their hash, never inline), and nothing
# is framed but the consoles of a playground on this machine. This is what the page needs (tests/web/test_static.py reads it from
# the page's files and holds this string to it, word for word): a library the page loads adds its directory here.
POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.11.0/ https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/ "
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/ https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.4.17/ "
    "https://cdnjs.cloudflare.com/ajax/libs/marked/18.1.1/ https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.15.0/",
    "style-src 'self' https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.11.0/ https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/ "
    "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/ 'unsafe-inline'",
    "font-src 'self' https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/",
    "img-src 'self' data:", "connect-src 'self'", "frame-src http://localhost:* http://127.0.0.1:*", "worker-src 'self' data:",
    "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"])
# It shows the consoles of a playground in frames and holds the keys to the lab (Stop, Reset, Delete): no page frames it. Every
# answer says so; an HTML one, which a browser runs as a page, says the whole policy
UNFRAMED = {"Content-Security-Policy": "frame-ancestors 'none'", "X-Frame-Options": "DENY"}
README = "(see Prerequisites in README.md)"
MAX_BODY = 1 << 20   # the largest thing the page sends is the text of a compose file
# ... but for an imported playground, which the page sends as base64: the largest archive an import reads, and a third more
LARGE_BODIES = {"/api/catalogs/import": (exports.MAX_ARCHIVE * 4) // 3 + 1024}
# What the home page says to a request that names the Lab UI by another host than localhost or 127.0.0.1 (0.0.0.0, a
# name of this machine): the Host header is not repeated in it
WRONG_HOST = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="color-scheme" content="light dark">'
              '<title>Gravitee Lab UI</title><body style="font:16px system-ui;margin:3rem">'
              '<p>Open the Lab UI at <a href="{url}">{url}</a>')


# ---------------------------------------------------------------------------------------------------------------------
# Handlers that are more than one call
# ---------------------------------------------------------------------------------------------------------------------

def _docker_down(body):
    """Docker that does not answer, is not installed or has a Compose older than 5.0 keeps the platform from starting: it is said as
    Docker's notice, in the words of the machine's check (machine), with the checks behind it, and not as a file that does not load"""
    if body["docker"].get("error"):
        return
    down = [one for one in body.get("problems", []) if one.get("file") == paths.CORE_FILE
            and (compose.unreachable(one.get("message")) or "No such file or directory: 'docker'" in (one.get("message") or ""))]
    lacking = compose.CACHE.get("requirement", 10, requirement)
    if not down and not lacking:
        return
    row = machine.report()["checks"][0]
    unreachable = row["state"] == "error" and "running" in row["detail"] or bool(down) and compose.unreachable(down[0]["message"])
    body["docker"].update(error=row["detail"] if row["state"] in ("error", "working") else "Docker does not answer",
                          title="Docker is not reachable" if unreachable else "Docker is not ready")
    body["problems"] = [one for one in body.get("problems", []) if one not in down]
    body["wait"] = body["docker"]["title"]


def snapshot():
    """The state the page draws, before it is sent. Docker not answering is part of the state, not a failure of the Lab
    UI, and so is docker-compose.yml not loading: then what runs is not known either. What Docker says of the
    containers moves the last job too: their health, which Compose does not wait for, and after it returned. Each
    container says which license it runs, as a digest: one saved and not applied is said."""
    p, read = project.loaded()
    asked = time.monotonic()   # Docker's answer is newer than every line the job read before
    containers, error = compose.containers(project.LICENSE_KEY) if p["core_loaded"] else ({}, None)
    last = JOBS.current
    if last and not error and p["core_loaded"]:
        last.observe(model.container_steps(p, containers), asked)
    job = last.summary() if last else None
    created = {pid: playgrounds.created(pid) for pid in p["playgrounds"]}
    # What each platform holds is read from this machine's files: the state never waits for a platform
    deployed = {pid: places.deployed(pid, playgrounds.created) for pid in p["playgrounds"]}
    known = p["core_loaded"] and not error
    docker = {"error": error, "memory": compose.docker_memory() if known else None}
    saved = project.saved(p) if known else None
    # The settings and labels that differ in what runs behind by its configuration, for the reason a row gives
    changed = drift.changes(p, read["raw"], containers, model.lateness(p, containers, saved), saved) if saved else None
    on = layers.current(p)
    # What the catalogs say is read from this machine's files and memory: the state never runs git, asks a network or waits for
    # the worker
    body = model.build(p, on, containers, job, JOBS.failed_job, license.features_cached(), docker,
                       created, saved=saved, license_status=license.status(p), platforms=platforms.read(),
                       deployed=deployed, changed=changed, git=model.git_state(service.git_version()),
                       catalogs=service.describe(p, read), content=firstrun.content(p, read), layout=layout.status())
    # What the page gives an AI agent to create an item: where the project is, the folder and the word on its commands
    # for the agent (machine.agent), and the skills to follow. Where API Management runs, from this machine's choice and
    # what its platform last said
    body.update(root=str(paths.ROOT), agent=machine.agent(), skills=personal.skills(), lab=updates.describe(),
                kept_settings=kept.listed(),
                core=core.describe(p, on, containers if known else {}, saved, body.get("wait")))
    _docker_down(body)
    unread = service.problems()   # the files that record the catalogs cannot be read: nothing would be shown, and no word said
    if unread:
        body["problems"] = [*body.get("problems", []), *unread]
    starred.mark(body)
    return body


def state(req):
    """What the page draws, with a tag of it: nothing is sent when the page already has it."""
    data = json.dumps(snapshot()).encode()
    tag = '"' + hashlib.sha256(data).hexdigest()[:16] + '"'
    # The page asks every 3 s with the tag of what it has: nothing is sent, and nothing drawn, when nothing changed
    if req.headers.get("If-None-Match") == tag:
        return req.answer(304, headers={"ETag": tag})
    return req.answer(200, data, "application/json", {"ETag": tag})


def frame(req, pid):
    """What one UI of a playground is for this page: {answers, frameable, reason}. The page is the one that asks, by
    the name it was opened with; the address must be one the playground declares."""
    return probe.probe(project.load(), pid, req.query.get("url"), f"http://{req.headers.get('Host')}")


def _terminal(pid):
    """The live shell of a playground's terminal, opened in the container of the service its `terminal` names."""
    p = project.load()
    playground = p["playgrounds"].get(pid)
    if not playground:
        raise paths.NotFound(f"No playground {pid}")
    found = p["services"].get(playground.get("terminal") or "")
    if not found:
        raise ValueError(f"{playground['name']} has no terminal")
    # N05: a catalog's playground has a terminal only in a service of its own or of what it brings, never in its seed
    if trust.line("N05") in ((playground.get("verdict") or {}).get("notes") or ()):
        raise ValueError(trust.NOT_OFFERED)
    return terminal.attach(pid, found["container"])


def terminal_stream(req, pid):
    """What the shell prints, as it does: from the offset the page already holds (`from`, else all that is kept) as
    "out" events carrying the offset after them and the bytes in base64, a comment now and then while it is quiet, and an
    "exit" event when the shell ends. The page left: the shell stays, for the next time."""
    session = _terminal(pid)
    offset = int(req.query["from"]) if req.query.get("from", "").isdigit() else 0
    req.sse_start()
    try:
        while True:
            end, data, alive = session.read(offset, terminal.HEARTBEAT)
            if data:
                req.sse_send({"at": end, "data": terminal.encode(data)}, event="out")
            elif alive:
                req.wfile.write(b": still here\n\n")
                req.wfile.flush()
            offset = end
            if not alive:
                return req.sse_send({}, event="exit")
    except OSError:
        return None


def terminal_input(req, pid):
    """What is typed (`data`, text) and the size of the page's terminal (`size`, [rows, columns])."""
    session = _terminal(pid)
    typed, size = terminal.clean(req.body.get("data")), terminal.size(req.body.get("size"))
    if size:
        session.resize(*size)
    if typed:
        session.write(typed)
    return {"alive": session.alive}


def terminal_close(req, pid):
    """Ends the playground's shell: the next opening of its terminal is a new one."""
    terminal.drop(pid)
    return {"closed": True}


def accepted(req):
    """What the page sends back of the rows its dialog showed (`accept`): a list of keys, "<kind>:<id>". Nothing sent is
    nothing accepted. Anything else is a bad request: a text would be searched by pieces of it ("addon:kafka" is in
    "addon:kafka-desk"), a mapping by its keys, and the gate that asks for what the page showed would take them for it."""
    found = req.body.get("accept")
    if found is None:
        return []
    if not (isinstance(found, list) and all(isinstance(one, str) for one in found)):
        raise ValueError("What was accepted must be a list of rows")
    return found


def switch(req, kind, uid, do):
    """Starts or stops an add-on or a setup. The plan is made again: it must not be blocked, and what it stops or
    turns off besides the item itself must be what the page showed and sent back in `accept`. The job is named as
    its button: "Turn on Kiosk", "Turn off Kafka". A start that names a `place` runs the item there (it is
    playgrounds.deploy_unit's)."""
    p = project.load()
    place = place_of(req.body.get("place")) if do == "start" else None
    if place:
        return playgrounds.deploy_unit(p, kind, uid, accepted(req), place)
    containers = compose.ps()
    plan = layers.plan(p, layers.current(p), model.up(p, containers), kind, uid, do,
                       behind=model.behind(p, containers, project.saved(p)))
    layers.check(p, plan, accepted(req))
    return layers.apply(p, plan, f"{plan['label']} {p[kind + 's'][uid]['name']}")


def check_unit(req, kind, uid):
    """What starting an add-on or a setup on a place says first: its checks and the dialog of its plan."""
    place = place_of(req.query.get("place"))
    if not place:
        raise ValueError("Choose a place besides This machine")
    return playgrounds.unit_checks(project.load(), kind, uid, place)


def effects(req):
    """What Apply, or a Save & apply, would make again and start again: {restarts, starts, runs, exact}. Of what is
    saved, or with a change not saved yet: a .env form's values ({file, values}), a compose file's text ({path,
    content}), versions and registries of Images ({images}). `exact`: false when what the change makes Compose create
    cannot be known, then what it may make again is counted too: the running services of what it changes; false as
    well when what is saved could not be read, then every service that runs is counted."""
    p, body = project.load(), req.body
    if body.get("images") is not None:
        if not isinstance(body["images"], dict):
            raise ValueError("The images must be a mapping")
        hashes, exact = images.drafted(p, body["images"])
        maybe = images.changing(p, body["images"])
    elif body.get("values") is not None:
        hashes, exact = files.env_hashes(p, body.get("file"), body["values"])
        maybe = files.affected(p, body.get("file"))
    elif body.get("path"):
        hashes, exact = files.draft_hashes(p, body["path"], body.get("content", ""))
        maybe = files.affected(p, body["path"])
    else:
        hashes, exact, maybe = None, True, ()
    found = stack.effects(p, hashes, () if exact else maybe)
    return {**found, "exact": exact and found["exact"]}


def installed(req):
    """What a product loads (plugins.installed), with what the license says of each of its plugins that needs a
    feature it does not give, in the words of a row (`licensed`: {plugin: note}, model.license_note): the plugins
    page says it as the lists do."""
    p = project.load()
    found = plugins.installed(p, req.query.get("product"))
    status, features = license.status(p), license.allowed(license.info(p))
    notes = {name: model.license_note(status, features, [feature]) for name, feature in found["features"].items()}
    return {**found, "licensed": {name: note for name, note in notes.items() if note}}


def place_of(value):
    """The place a request names: None for This machine (none given, or `machine`), else its id, which what deploys
    there verifies. What is not text is refused: it is never taken for This machine."""
    if value is None or value == layers.MACHINE:
        return None
    if not (isinstance(value, str) and value):
        raise ValueError("Choose a place")
    return value


def at(value):
    """The place of a request as what deploys there takes it, `place=`: nothing for This machine, so that its call is as
    it always was."""
    place = place_of(value)
    return {"place": place} if place else {}


def start_playground(req, pid):
    """Starts a playground, on This machine or in a place. A place sends back what its dialog showed (`accept`)."""
    accept = accepted(req)
    more = {"remove": True} if req.body.get("remove") else {}   # delete what the playground it stops created
    found, where = project.load(), at(req.body.get("place"))
    if req.body.get("switch") and not where:   # the person chose the versions it was tested with, on This machine
        more["switching"] = True
    return playgrounds.start(found, pid, accept, **more, **where)


def reseed_playground(req, pid):
    """Seeds a playground again, or seeds it for the first time in a place (Deploy): the place sends back its dialog
    (`accept`), because what it deploys by may need starting."""
    more, accept = at(req.body.get("place")), accepted(req)
    return playgrounds.reseed(project.load(), pid, **more, **({"accept": accept} if more else {}))


def stop_playground(req, pid):
    """Stops a playground, and deletes what it created where it is asked to: every place that holds a record."""
    return playgrounds.stop(project.load(), pid, bool(req.body.get("remove")), accepted(req),
                            **at(req.body.get("place")))


def delete(req, kind, uid):
    """Deletes an item of this machine; a playground that names a `place` is not deleted: what its seed created in that
    place is. A place is never This machine here, whose record is Stop's: asking for it must not delete the folder."""
    p = project.load()
    if kind == "playground" and "place" in req.body:
        place = place_of(req.body["place"])
        if not place:
            raise ValueError("Choose a place besides This machine")
        return playgrounds.delete_place(p, uid, place)
    return personal.delete(p, kind, uid)


def changed(uid, result):
    """A platform changed: what the platforms say is read again, so that the places the page asks for next are as they
    are now. Answers the result."""
    places.invalidate(uid)
    return result


def check_platform(req, pid):
    found = platforms.check(pid, project.load())
    return changed(pid, found)


def register_platform(req):
    record = platforms.add(**fields(req, "name", "kind", "url", "token", "account"))
    return changed(record["id"], record)


def gateway_plan(p, pid, gid):
    """The plan of the start of a gateway of a platform, and its place (places.gateway): the add-on that runs it is
    turned on for that place, or made again with its token when it serves another one."""
    place = places.gateway(p, pid, gid)
    containers = compose.ps()
    plan = layers.plan(p, layers.current(p), model.up(p, containers), "addon", place["gateway"], "start",
                       behind=model.behind(p, containers, project.saved(p)), place=place)
    return plan, place


def gateway_dialog(req, pid, gid):
    """What starting a gateway says first: the dialog of its plan when it does more than start it (what it serves from
    now on, what it stops serving), else null."""
    plan, _ = gateway_plan(project.load(), pid, gid)
    req.send_json(layers.dialog(plan) if layers.asks(plan) else None)


def start_gateway(req, pid, gid):
    """Starts a gateway: the add-on that runs it, with the token kept for this one. It is a job of the add-on, whose
    own buttons show it. The token is written by the job, before the add-on is made, whenever the gateway is not the
    one that runs here: the add-on may hold another, or one no platform lists any more. The add-on may come from a catalog: the
    start is verified by the catalogs (layers.check), the on file is written through the gate of a unit that is blocked, and
    the folders its binds name are made first, as every other start does."""
    p = project.load()
    plan, place = gateway_plan(p, pid, gid)
    layers.check(p, plan, accepted(req))

    def prepare(job):
        layers.write(plan["playground"], plan["explicit"], blocked=layers.blocked_files(p))
        layers.make_binds(p, plan)
        for part, verb in (("starts", "Starts"), ("restarts", "Restarts")):
            if plan[part]:
                job.lines.append(f"{verb}: {', '.join(row['name'] for row in plan[part])}")
        if not place["here"]:
            platforms.swap_gateway(p, pid, gid)
    name = p["addons"][place["gateway"]]["name"]
    return JOBS.start(f"{plan['label']} {name}", [prepare, *layers.commands(plan), lambda job: places.invalidate(pid)],
                      key=f"addon:{place['gateway']}", verb="start", changes=layers.changes(p, plan),
                      waits=layers.waits(p, plan))


def turning_off(p, pid, gid=None):
    """What a platform's forgotten gateway leaves to do when it ran here: the plan to turn the add-on that runs it off
    (None when its gateway was not the one here, or the add-on is neither on nor running). A job runs it, after the
    platform acted: it is asked for, and refused, before anything changes, like a stop of the add-on that would stop
    more than the add-on."""
    if not platforms.runs_here(p, pid, gid):
        return None
    addon = platforms.gateway_addon(p, platforms.find(pid)["kind"])
    state, containers = layers.current(p), compose.ps()
    running = model.up(p, containers)
    if addon["id"] not in state["effective"] and addon["id"] not in running["addons"]:
        return None
    if JOBS.current and not JOBS.current.done:
        raise RuntimeError(f"'{JOBS.current.title}' is still running")
    plan = layers.plan(p, state, running, "addon", addon["id"], "stop")
    layers.check(p, plan, [])
    return plan, f"{plan['label']} {addon['name']}"


def turned_off(p, found):
    """The job that turns the gateway's add-on off, once the platform forgot its gateway; {} when there is none."""
    return layers.apply(p, *found) if found else {}


def delete_gateway(req, pid, gid):
    """Deletes a gateway in its platform, then the token kept for it. When it was the one that runs here, the add-on
    is turned off: a job, which this answers."""
    p = project.load()
    found = turning_off(p, pid, gid)
    platforms.delete_gateway(p, pid, gid)
    return turned_off(p, changed(pid, found))


def delete_platform(req, pid):
    """Forgets a platform with its tokens and the records of what was deployed there, which stays in the platform. When
    its gateway ran here, the add-on is turned off: a job, which this answers."""
    p = project.load()
    found, left = turning_off(p, pid), None
    try:
        platforms.remove(pid, p)
    except RuntimeError as error:   # the platform is forgotten; what it could not delete is said after the add-on is off
        left = error
    changed(pid, None)
    job = turned_off(p, found)
    if left:
        raise left
    return job


def fields(req, *names):
    """These fields of a form, as the body sent them: None for one it does not have."""
    return {name: req.body.get(name) for name in names}


def save_images(req):
    values = req.body.get("values") or {}
    if not isinstance(values, dict):
        raise ValueError("The versions must be a mapping")
    images.save(project.load(), values)
    return images.state(project.load())   # read again: the .env files changed


def core_plan(req):
    """What Start all says first with API Management in the Cloud: its dialog, the platform read again now. Refused with the reason it
    cannot start."""
    p = project.load()
    found = core.plan(p, layers.current(p), compose.ps(), project.saved(p), live=True)
    if found["blocked"]:
        raise RuntimeError(found["blocked"])
    return {"dialog": found["dialog"], "needed": found["needed"]}


def stack_action(req, name):
    """An action of the platform; the Dashboard's Start all and Stop all say `core`: they follow where API
    Management runs."""
    return stack.action(project.load(), name, for_core=bool(req.body.get("core")), accept=accepted(req))


def cancel(req, jid):
    job = JOBS.get(jid)
    job.cancel()
    return job


def add_catalog(req):
    """Add: the ticket of the check, or a listed catalog by its id, which is added with no check and no dialog. The
    allowance of the dialog's choice may come with the ticket (`host`); the page sends it as a request of its own."""
    if req.body.get("listed") is not None:
        return service.add_listed(req.body["listed"])
    return service.add(req.body.get("ticket"), host=req.body.get("host") is True)


def update_catalog(req, ns, change):
    """Update or Go back: `check` answers the plan, `ticket` applies it. Neither is a mistake of the page."""
    body = req.body
    if not body.get("check") and body.get("ticket") is None:
        raise ValueError("Send check, or the ticket of the plan")
    if change == "update":
        return service.update(ns, check=bool(body.get("check")), ticket=body.get("ticket"),
                              confirm_history=body.get("confirm_history") is True)
    return service.undo(ns, check=bool(body.get("check")), ticket=body.get("ticket"))


def delete_catalog(req, ns):
    """Remove: `check` says what it would do; a removal says whether to keep the settings, and a request that says
    neither removes nothing. Once a catalog is removed the first-run thread looks at the layout again: a person
    whose old layout waits for the default catalog has just decided to have none, and the move must not wait for the next start."""
    if req.body.get("check"):
        return service.remove(ns, check=True)
    if "keep_settings" not in req.body:
        raise ValueError("Say whether to keep the settings of the catalog")
    removed = service.remove(ns, keep_settings=req.body["keep_settings"])
    firstrun.removed()
    return removed


def close_layout(req):
    """The person closed the "Lab updated" notice: it is not shown again. The marker is written again, and what the
    project reads is read again at once (the marker's time is part of what it is read from, 6.7), so the next state does not
    carry the notice. Answers the status of the layout, which is what the state says."""
    layout.dismiss()
    project.forget()
    return layout.status()


def retry_layout(req):
    """Try again, after a move that failed: what has no copy is attempted now (firstrun.wake) and the move is made
    again, here, so that the answer says whether it worked. It raises nothing for a disk or a registry that failed: the status
    does (`failed`, with the notice that offers Try again once more). A move with nothing to do changes nothing."""
    firstrun.wake()
    layout.migrate()
    project.forget()
    return layout.status()


def use_catalog(req, kind, uid):
    """Use the catalog version: the personal copy of an item the catalog now has gives its place to the catalog's."""
    return contribute.use_catalog_version(project.load(), kind, uid)


def destinations(p, kind, uid):
    """The catalogs a personal item can be shared to, {"destinations": [...]}, for the first check of a share."""
    return {"destinations": contribute.destinations(p, kind, uid)}


def share_item(req, kind, uid):
    """Share. With no `to` it is today's check and share: {file, problems, changes}, and a check also names the
    catalogs it can go to (`destinations`); with `to`, a catalog's namespace, the check and the share of that destination."""
    p, body = project.load(), req.body
    to, check = body.get("to") or "", bool(body.get("check"))
    if not isinstance(to, str):
        raise ValueError("The destination must be text")
    if to:
        return personal.share(p, kind, uid, check_only=check, to=to)
    found = personal.share(p, kind, uid, check_only=check)
    return {**found, **destinations(p, kind, uid)} if check else found


def created(req, make, *names, **given):
    """A new add-on, setup or playground from the form's fields. `catalog` (a catalog's namespace) says where it is kept: not
    given, it is Personal; the namespace of an editable folder catalog writes it there, and any other is `not_editable`
    (personal.py). `shared`, the destination that was deleted, is ignored."""
    if layout.waiting():   # what a create writes is read from files that are not where the on file says
        raise CatalogError("layout_waiting", layers.WAITING, 409)
    asked = req.body.get("catalog")
    if asked is not None and not isinstance(asked, str):
        raise ValueError("The catalog must be text")
    return make(project.load(), **fields(req, *names), **given, **({"catalog": asked} if asked else {}))


def static(req, folder="", name="index.html"):
    """A file of web/. The path is resolved before it is read: nothing outside the folder asked for is served."""
    base = (paths.WEB_DIR / folder).resolve()
    file = (base / name).resolve()
    if base not in file.parents or file.suffix not in TYPES or not file.is_file():
        raise paths.NotFound("Not found")
    kind = TYPES[file.suffix]
    req.answer(200, file.read_bytes(), kind if kind.startswith("image/") else f"{kind}; charset=utf-8", {"Cache-Control": "no-cache"})


def stream_job(req, jid):
    """A job's output as it comes, then its summary. Between them, how far the images it pulls are, when it pulls
    any ("pulls" events, each the whole of it)."""
    job = JOBS.get(jid)
    req.sse_start()
    sent, pulls = 0, None
    while True:
        lines = job.lines[sent:]
        if lines:
            req.sse_send(lines)
            sent += len(lines)
        found = job.pull_view()
        if found and found != pulls:
            req.sse_send(found, event="pulls")
            pulls = found
        if job.done and sent >= len(job.lines):
            return req.sse_send(job.summary(), event="done")
        time.sleep(0.2)


def _stamp(when):
    """A time Docker gives as a log line's timestamp writes it, nine digits of a second, so that the two sort as
    text: "2026-10-03T12:44:35.3299Z" -> "2026-10-03T12:44:35.329900000Z"."""
    head, _, fraction = when.rstrip("Z").partition(".")
    return f"{head}.{fraction.ljust(9, '0')[:9]}Z"


def _started(container):
    """(whether a container runs, when it last started, as a log line's timestamp); (False, None) without one."""
    rc, out, _ = compose.run(["docker", "container", "inspect", "--format", "{{.State.Running}} {{.State.StartedAt}}",
                              container], timeout=20)
    running, _, started = out.strip().partition(" ")
    return rc == 0 and running == "true", _stamp(started) if rc == 0 and started else None


class Follower:
    """The logs of one service, followed into `lines` until `closed`: [service, timestamp, line] each. `docker logs
    -f` ends when its container stops or is made again: once it runs again after a new start, a mark ({service,
    timestamp}, the stream's `restarted` event) goes first, then what it wrote since the last line read, followed
    again. Only the lines of the container are sent: what Docker says itself has no timestamp."""

    def __init__(self, name, container, tail, lines, closed, spawn):
        self.name, self.container, self.tail, self.lines, self.closed, self.spawn = \
            name, container, tail, lines, closed, spawn

    def run(self):
        since, start = None, _started(self.container)[1]
        while True:
            when = ["--since", since] if since else ["--tail", str(self.tail)]
            logs = self.spawn(["docker", "logs", "-f", "--timestamps", *when, self.container])
            if logs is None:   # the stream was closed
                return
            floor = since   # --since counts the line read last: it is not sent twice
            for line in logs.stdout:
                timestamp, _, text = line.rstrip("\n").partition(" ")
                if STAMP.match(timestamp) and not (floor and timestamp <= floor):
                    since = timestamp
                    self.lines.put([self.name, timestamp, text])
            logs.wait()
            while True:   # until it runs again
                if self.closed.wait(FOLLOW_AGAIN):
                    return
                running, started = _started(self.container)
                if running:
                    break
            if started != start:
                start = started
                self.lines.put({"service": self.name, "timestamp": started})


def stream_logs(req):
    """The logs of some services, followed: batches of [service, timestamp, line], and a `restarted` event
    ({service, timestamp}) when a container runs again after a new start, followed again from there."""
    services = project.load()["services"]
    names = [name for name in req.query.get("services", "").split(",") if name in services]
    tail = max(0, min(int(req.query.get("tail", "300")), 5000))
    lines, closed, lock, followed = queue.Queue(), threading.Event(), threading.Lock(), []

    def spawn(command):
        """A `docker logs` started, unless the stream was closed: none outlives it."""
        with lock:
            if closed.is_set():
                return None
            logs = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", bufsize=1)
            followed.append(logs)
            return logs

    for name in names:
        follower = Follower(name, services[name]["container"], tail, lines, closed, spawn)
        threading.Thread(target=follower.run, daemon=True).start()
    try:
        req.sse_start()
        send_batches(req, lines)
    finally:
        with lock:
            closed.set()
            for logs in followed:
                logs.kill()


def stream_stats(req):
    """The load of the lab's containers, an event a second: {cpu_percent, memory_used, containers, top, at}. The one
    `docker stats` that reads it runs while a page follows the stream (dashboard.STATS); a closed page is noticed by the
    write."""
    cores = dashboard.cores()
    dashboard.STATS.join()
    try:
        req.sse_start()
        while True:
            dashboard.STATS.names = dashboard.lab_names()
            load = dashboard.STATS.read(cores)
            if load:   # none while the first sample is on its way
                req.sse_send(load)
            time.sleep(STATS_EVERY if load else 0.1)
    finally:
        dashboard.STATS.leave()


def send_batches(req, lines):
    """Sends what was read every quarter of a second, until the page closes the stream. A container that runs again
    is said as it comes, after the lines before it."""
    beat = time.time()
    while True:
        batch, deadline = [], time.time() + 0.25
        while time.time() < deadline and len(batch) < 500:
            try:
                item = lines.get(timeout=max(0.01, deadline - time.time()))
            except queue.Empty:
                break
            if isinstance(item, dict):
                if batch:
                    req.sse_send(batch)
                    batch = []
                req.sse_send(item, event="restarted")
                beat = time.time()
            else:
                batch.append(item)
        if batch:
            req.sse_send(batch)
        elif time.time() - beat > 15:   # a silent stream is written to as well: that is how a closed page is noticed
            req.wfile.write(b": keep-alive\n\n")
            req.wfile.flush()
            beat = time.time()


# ---------------------------------------------------------------------------------------------------------------------
# Routes: a handler gets the request (query, body) and the groups of its pattern. It returns what to answer as JSON
# (a Job answers its summary), or None when it answered itself.
# ---------------------------------------------------------------------------------------------------------------------

ID, DO = "([a-z0-9-]+)", "(start|stop|restart)"
NS = f"({paths.NS})"   # a catalog's namespace, as a segment of a route
CLOUD = f"({platforms.ID.pattern})"   # what a platform calls an environment or a gateway: one segment, as platforms accepts
KIND, UNIT = f"({'|'.join(paths.KINDS)})", paths.ID   # an add-on, a setup or a playground; its id, local/<name> too
SERVICE = "([a-zA-Z0-9._-]+)"   # what Compose accepts as a service name

def kept_remove(folder):
    """Kept settings removed at the person's word, as a change of catalogs."""
    with service._changing():
        return kept.remove(folder)


ROUTES = [(method, re.compile(pattern), handler) for method, pattern, handler in [
    ("GET", "/", static),
    ("GET", "/(css|js|img)/(.+)", static),
    ("GET", "/favicon.ico", lambda r: r.answer(204)),   # the page carries its own icon
    ("GET", "/api/state", state),
    ("GET", "/api/jobs/([a-f0-9]{8})", stream_job),
    ("GET", "/api/logs", stream_logs),
    ("GET", "/api/dashboard", lambda r: dashboard.read(project.load(), refresh=r.query.get("refresh") == "1")),
    ("GET", "/api/dashboard/stats", stream_stats),
    ("GET", "/api/images", lambda r: images.state(project.load())),
    ("GET", "/api/tags", lambda r: {"tags": images.tags(project.load(), r.query.get("component"),
                                                        r.query.get("registry")),
                                    "local": sorted(images.local_images() or [])}),
    ("GET", "/api/registry", lambda r: images.registry_status(project.load())),
    ("GET", "/api/files", lambda r: {"sections": files.sections(project.load(), r.query.get("path"))}),
    ("GET", "/api/files/content", lambda r: files.read(r.query.get("path"))),
    ("GET", "/api/env", lambda r: files.settings(project.load(), r.query.get("file"))),
    ("GET", "/api/plugins/catalog", lambda r: {"plugins": plugins.catalog(project.load(), r.query.get("product"),
                                                                          refresh=r.query.get("refresh") == "1")}),
    ("GET", "/api/plugins/installed", installed),
    ("GET", "/api/plugins/versions", lambda r: {"versions": plugins.versions(r.query.get("path"))}),
    ("GET", "/api/license", lambda r: license.info(project.load())),
    ("GET", "/api/machine", lambda r: machine.report(fresh=r.query.get("fresh") == "1")),
    ("POST", "/api/machine/act", lambda r: machine.act(r.body.get("id"))),
    ("GET", f"/api/playgrounds/{UNIT}/check",
     lambda r, pid: playgrounds.check(project.load(), pid, **at(r.query.get("place")))),
    ("GET", f"/api/(addon|setup)s/{UNIT}/check", lambda r, kind, uid: check_unit(r, kind, uid)),
    ("GET", f"/api/playgrounds/{UNIT}/guide", lambda r, pid: {"markdown": playgrounds.guide(pid)}),
    ("GET", f"/api/playgrounds/{UNIT}/frame", frame),
    ("GET", f"/api/playgrounds/{UNIT}/terminal", terminal_stream),
    ("GET", "/api/platforms", lambda r: platforms.listing()),
    # Where a playground can be deployed besides this machine, as the platforms last said it: never waits for one
    ("GET", "/api/places", lambda r: places.places(project.load())),
    ("GET", f"/api/platforms/{ID}/gateways/{CLOUD}/plan", gateway_dialog),
    # Where API Management runs is in the state; what Start all does with it in the Cloud is asked of the platform at the click
    ("GET", "/api/core/plan", core_plan),

    ("POST", "/api/addons", lambda r: created(r, personal.create_addon, "name", "description", "category", "image", "port")),
    ("POST", "/api/setups", lambda r: created(r, personal.save_setup, "name", "description", "category", "addons",
                                              sid=r.body.get("id"), check_only=bool(r.body.get("check")))),
    ("POST", "/api/playgrounds", lambda r: created(r, personal.create_playground, "name", "description", "category",
                                                   "setups", "addons")),
    ("POST", f"/api/{KIND}s/{UNIT}/share", share_item),
    # A playground in a file: what its export carries (`check`), then the file, base64; and the import of one, as an Add
    ("POST", f"/api/playgrounds/{UNIT}/export", lambda r, pid: exports.plan(pid) if r.body.get("check")
     else exports.archive(pid, private=[one for one in r.body.get("private") or () if isinstance(one, str)])),
    # The same playground for Docker Compose alone, with no Lab: what the folder holds and asks (`check`), then the file, base64
    ("POST", f"/api/playgrounds/{UNIT}/standalone",
     lambda r, pid: standalone.plan(pid) if r.body.get("check")
     else standalone.archive(pid, private=[one for one in r.body.get("private") or () if isinstance(one, str)])),
    ("POST", "/api/catalogs/import", lambda r: exports.receive(r.body.get("data"))),
    ("POST", "/api/kept-settings/delete", lambda r: kept_remove(r.body.get("folder"))),
    ("POST", "/api/catalogs/import/personal", lambda r: exports.to_personal(r.body.get("ticket"), allow=r.body.get("allow") is True)),
    ("POST", f"/api/{KIND}s/{UNIT}/use-catalog", use_catalog),
    ("POST", f"/api/{KIND}s/{UNIT}/move", lambda r, kind, uid: moving.plan(kind, uid) if r.body.get("check")
     else moving.move(kind, uid, bool(r.body.get("together")), allow=r.body.get("allow") is True)),
    ("POST", f"/api/{KIND}s/{UNIT}/copy", lambda r, kind, uid: moving.copy_plan(kind, uid) if r.body.get("check")
     else moving.copy(kind, uid, bool(r.body.get("users")), allow=r.body.get("allow") is True)),
    # The catalogs. What changes one, runs git or asks a network is a POST and the page's alone; the state never does
    # The Lab's own versions: what each changed, a fetch of the newest now, and the update to it (a job, then a new start)
    ("GET", "/api/lab", lambda r: updates.releases()),
    ("POST", "/api/lab/check", lambda r: updates.check()),
    ("POST", "/api/lab/update", lambda r: updates.update(r.body.get("version"))),
    ("POST", "/api/catalogs/check", lambda r: service.check_address(r.body.get("address"), r.body.get("branch"))),
    ("POST", "/api/catalogs", add_catalog),
    ("POST", "/api/catalogs/new", lambda r: service.create_folder(r.body.get("name"))),
    ("POST", f"/api/catalogs/{NS}/check", lambda r, ns: service.check_now(ns)),
    ("POST", f"/api/catalogs/{NS}/auto", lambda r, ns: service.set_auto(ns, r.body.get("enabled"))),
    ("POST", f"/api/catalogs/{NS}/update", lambda r, ns: update_catalog(r, ns, "update")),
    ("POST", f"/api/catalogs/{NS}/undo", lambda r, ns: update_catalog(r, ns, "undo")),
    ("POST", f"/api/catalogs/{NS}/changes", lambda r, ns: service.changes(ns, r.body.get("ticket"))),
    ("POST", f"/api/catalogs/{NS}/repair", lambda r, ns: service.repair(ns)),
    ("POST", f"/api/catalogs/{NS}/delete", delete_catalog),
    ("POST", f"/api/catalogs/{NS}/allow", lambda r, ns: service.allow_host(ns, r.body.get("enabled"))),
    # The move of an old layout: the notice is closed, a move that failed is made again. The state says where it is
    ("POST", "/api/layout/close", close_layout),
    ("POST", "/api/layout/retry", retry_layout),
    ("POST", f"/api/{KIND}s/{UNIT}/star", lambda r, kind, uid: starred.star(
        project.load(), kind, uid, bool(r.body.get("starred")))),
    ("POST", f"/api/{KIND}s/{UNIT}/delete", delete),
    # Reading a platform sends it the token kept for it: a POST, so that only the page asks (the Lab UI's header). What
    # changes a platform reads it again, for the places
    ("POST", "/api/platforms", register_platform),
    ("POST", f"/api/platforms/{ID}/check", check_platform),
    ("POST", f"/api/platforms/{ID}/delete", delete_platform),
    ("POST", f"/api/platforms/{ID}/name", lambda r, pid: changed(pid, platforms.rename(pid, r.body.get("name")))),
    ("POST", f"/api/platforms/{ID}/token", lambda r, pid: changed(pid, platforms.replace_account_token(
        pid, r.body.get("token")))),
    ("POST", f"/api/platforms/{ID}/environments/{CLOUD}/token", lambda r, pid, env: changed(
        pid, platforms.set_cloud_token(pid, env, r.body.get("token")))),
    ("POST", f"/api/platforms/{ID}/environments/{CLOUD}/token/delete",
     lambda r, pid, env: changed(pid, platforms.delete_cloud_token(pid, env))),
    ("POST", f"/api/platforms/{ID}/gateways", lambda r, pid: changed(pid, platforms.create_gateway(
        project.load(), pid, r.body.get("environmentId"), r.body.get("name")))),
    ("POST", f"/api/platforms/{ID}/gateways/{CLOUD}/start", start_gateway),
    ("POST", f"/api/platforms/{ID}/gateways/{CLOUD}/delete", delete_gateway),
    ("POST", f"/api/(addon|setup)s/{UNIT}/(start|stop)", switch),
    # The actions on the containers of an add-on or a playground, the Stack's and the catalog's Restart: it stays on
    # or off
    ("POST", f"/api/(addon|playground)s/{UNIT}/containers/{DO}",
     lambda r, kind, uid, do: stack.unit_action(project.load(), kind, uid, do)),
    ("POST", f"/api/playgrounds/{UNIT}/start", start_playground),
    ("POST", f"/api/playgrounds/{UNIT}/stop", stop_playground),
    ("POST", f"/api/playgrounds/{UNIT}/terminal", terminal_input),
    ("POST", f"/api/playgrounds/{UNIT}/terminal/close", terminal_close),
    ("POST", f"/api/playgrounds/{UNIT}/reseed", reseed_playground),
    ("POST", "/api/stack/([a-z]+)", stack_action),
    # Where API Management runs: This machine or a linked environment of a Cloud account. Starts and stops nothing
    ("POST", "/api/core", lambda r: core.choose(project.load(), r.body.get("place"))),
    # What an apply would recreate, of what is saved or with a change not saved yet: read only
    ("POST", "/api/effects", effects),
    ("POST", f"/api/groups/{ID}/{DO}", lambda r, gid, do: stack.group_action(project.load(), gid, do)),
    ("POST", f"/api/services/{SERVICE}/{DO}", lambda r, name, do: stack.service_action(project.load(), name, do)),
    # Its job is the item's whose row asked (`key`), else the registry's
    ("POST", "/api/registry/login", lambda r: images.login(r.body.get("host"), r.body.get("key"))),
    ("POST", "/api/jobs/([a-f0-9]{8})/cancel", cancel),
    ("POST", "/api/images", save_images),
    ("POST", "/api/files/validate", lambda r: files.validate(project.load(), r.body.get("path"),
                                                             r.body.get("content", ""))),
    ("POST", "/api/files/save", lambda r: files.save(project.load(), r.body.get("path"), r.body.get("content", ""),
                                                     r.body.get("version"))),
    ("POST", "/api/env", lambda r: files.save_settings(project.load(), r.body.get("file"), r.body.get("values") or {},
                                                       r.body.get("version"))),
    ("POST", "/api/plugins/apply", lambda r: plugins.apply(project.load(), r.body.get("product"),
                                                           r.body.get("install") or [], r.body.get("remove") or [])),
]]


class Handler(BaseHTTPRequestHandler):
    server_version = "GraviteeLabUI"

    def log_message(self, fmt, *args):   # quiet: the page asks every 3 s
        pass

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def allowed(self, method):
        """Only this Lab UI's own page uses the API: asked for by a local name, not from a page of another site (a
        browser says where a request comes from), and a POST carries a header that such a page cannot send without a
        CORS preflight, which fails. The page itself may be opened from a link anywhere."""
        host, site = (self.headers.get("Host") or "").split(":")[0], self.headers.get("Sec-Fetch-Site")
        own_page = site in (None, "same-origin", "none") or not urlparse(self.path).path.startswith("/api/")
        return host in ("localhost", "127.0.0.1") and own_page \
            and (method == "GET" or self.headers.get("X-Gravitee-Lab-UI") == "1")

    def route(self, method):
        url = urlparse(self.path)
        if not self.allowed(method):
            if method == "GET" and url.path == "/":   # a person opening the page: say where it is
                address = f"http://localhost:{self.server.server_port}"
                return self.answer(403, WRONG_HOST.format(url=address).encode(), "text/html; charset=utf-8")
            return self.send_json({"error": "Forbidden"}, 403)
        self.query = {key: values[0] for key, values in parse_qs(url.query).items()}
        try:
            self.body = self.read_json(LARGE_BODIES.get(url.path, MAX_BODY)) if method == "POST" else {}
            result = self.call(method, url.path)
            if result is not None:
                self.send_json(result.summary() if isinstance(result, Job) else result)
        except Exception as error:  # noqa: BLE001 - reported to the page
            if isinstance(error, CatalogError):
                code, answer = error.status, {"error": error.message, "code": error.code}
            elif isinstance(error, RuntimeError) and str(error) == layers.WAITING:   # Start and Apply say it as Create does
                code, answer = 409, {"error": str(error), "code": "layout_waiting"}
            else:
                code = next((code for kinds, code in ERRORS if isinstance(error, kinds)), 500)
                answer = {"error": str(error) if code != 500 else f"{type(error).__name__}: {error}"}
            try:
                self.send_json(answer, code)
            except OSError:
                pass   # the page went away

    def call(self, method, path):
        for verb, pattern, handler in ROUTES:
            found = pattern.fullmatch(path) if verb == method else None
            if found:
                return handler(self, *found.groups())
        raise paths.NotFound("Not found")

    def read_json(self, limit=MAX_BODY):
        size = self.headers.get("Content-Length") or "0"
        if not (size.isascii() and size.isdigit()):   # a negative number reads until the connection closes
            raise ValueError("The Content-Length must be a number of bytes")
        if int(size) > limit:
            raise TooLarge(f"The request is larger than {limit >> 20} MB")
        body = json.loads(self.rfile.read(int(size)) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("The body must be a JSON object")
        return body

    def answer(self, code, body=b"", content_type=None, headers=None):
        self.send_response(code)
        page = {"Content-Security-Policy": POLICY} if (content_type or "").startswith("text/html") else {}
        headers = {"Content-Type": content_type, "Cache-Control": "no-store", **UNFRAMED, **page, **(headers or {}),
                   "Content-Length": len(body)}
        for key, value in headers.items():
            if value:
                self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code=200):
        self.answer(code, json.dumps(data).encode(), "application/json")

    def sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        for key, value in UNFRAMED.items():
            self.send_header(key, value)
        self.end_headers()

    def sse_send(self, data, event=None):
        self.wfile.write(((f"event: {event}\n" if event else "") + f"data: {json.dumps(data)}\n\n").encode())
        self.wfile.flush()


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------

# Where Docker Desktop puts the `docker` command on a Mac: a Docker installed while the Lab UI runs is found there, the shell's
# PATH being the one of before it
DOCKER_BINS = ("/usr/local/bin", "~/.docker/bin", "/Applications/Docker.app/Contents/Resources/bin")


def find_docker():
    """The places of DOCKER_BINS added at the end of PATH on a Mac, those it lacks"""
    if sys.platform != "darwin":
        return
    path = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(path + [os.path.expanduser(place) for place in DOCKER_BINS
                                                 if os.path.expanduser(place) not in path])


def requirement():
    """What this machine lacks to run the lab, in one line, in the words of the Makefile's; None when it lacks
    nothing."""
    rc, out, _ = compose.run(["docker", "compose", "version", "--short"], timeout=20)
    if rc == 127:   # docker itself is not there
        return f"docker not found: install Docker with Compose 5.0 or later {README}"
    version = tuple(int(number) for number in re.findall(r"\d+", out)[:2])
    # An add-on or a playground changes core services from an included file: Compose merges them from 5.0 on, earlier
    # versions refuse them ("conflicts with imported resource")
    if rc != 0 or len(version) < 2 or version < (5, 0):
        found = out.strip().lstrip("v") if rc == 0 else ""
        return f"Docker Compose 5.0 or later is needed, found: {found or 'none'} {README}"
    return None


def listing():
    """Prints what is on, then its consoles by section, the platform's groups first. While the old layout is waiting to be
    moved it says so first, as the Makefile does for a `docker compose` typed by hand: this command moves nothing, and
    what it lists is the core and the person's own items."""
    if layout.waiting():
        print(layout.GUARD)
    p = project.load()
    on = layers.current(p)
    names = [p["playgrounds"][on["playground"]]["name"]] if on["playground"] else []
    names += [p["addons"][uid]["name"] for uid in on["effective"]]
    print(f"On top of the platform: {', '.join(names) or 'nothing'}")
    running = layers.up_services(p, on["playground"], on["effective"])
    shown = [link for link in project.shown(p["links"], on["playground"], on["effective"]) if link["service"] in running]
    sections = [group["name"] for group in p["groups"]]
    sections += sorted({link["section"] for link in shown} - set(sections))
    for section in sections:
        links = [link for link in shown if link["section"] == section]
        if links:
            print(f"\n{section}")
        for link in links:
            print(f"  {link['label']:<26} {link['url']}")


def answers_on_ipv6(port):
    """Whether a program answers on this port of the IPv6 loopback, where `localhost` can lead the browser even while
    127.0.0.1 is free"""
    try:
        with socket.create_connection(("::1", port), timeout=1):
            return True
    except OSError:
        return False


def is_lab_ui(port):
    """Whether what answers on this port of the machine is a Lab UI, by the Server header of its home page. Any
    error means it is not."""
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/")
            return (connection.getresponse().getheader("Server") or "").startswith(Handler.server_version)
        finally:
            connection.close()
    except (OSError, http.client.HTTPException):
        return False


def start_up():
    """What the Lab does once its port is bound in this order: the layout of an existing machine is looked
    at and its own items moved (it never touches the network), what a stop left of a change of Personal is put right, the sweeper finishes what a stop left in the installs of the Lab
    and then in the folders of the catalogs that are linked (the half copies a Share left), the intent of the first run is
    written and what has no copy is marked as downloading, and the one background thread of the Lab starts. Each step is local
    and fast but the last, which returns at once; a failure of one is said in a line and never keeps the Lab from serving,
    because its row says what is wrong."""
    for what, step in (("looking at the layout", layout.ensure), ("finishing what a stop left", service.recover),
                       ("finishing a change of Personal that a stop left", moving.recover_at_start),
                       ("sweeping what a share left", contribute.sweep), ("preparing the first run", firstrun.reconcile),
                       ("starting the download of catalogs", firstrun.start_worker),
                       ("starting the check for new versions", updates.start_checking)):
        try:
            step()
        except Exception as error:  # noqa: BLE001 - the Lab serves whatever these find
            print(f"Gravitee Lab UI: {what} failed ({type(error).__name__}: {error})", file=sys.stderr)


def open_browser(url):
    """The page in the person's browser. In WSL it is the browser of Windows, which explorer.exe opens: a browser of Linux
    is rarely there, and the one WSLg offers answers with an error"""
    if machine.system() == "wsl" and shutil.which("explorer.exe"):
        subprocess.Popen(["explorer.exe", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        webbrowser.open(url)


def serve(port, browser):
    url = f"http://localhost:{port}"
    try:
        if answers_on_ipv6(port):   # the browser may go there for localhost, and find another program
            raise OSError(errno.EADDRINUSE, f"Port {port} is used on ::1")
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)   # this machine only
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        if not is_lab_ui(port):
            sys.exit(f"Port {port} is used by another program. Free it, or use another port: UI_PORT={port + 1} "
                     "gravitee-lab (the UIs of a playground do not show next to the guide on another port)")
        address(url, "Already running. To load a new version, stop it with Ctrl+C in its terminal, then run gravitee-lab "
                     "again.")
        if browser:
            open_browser(url)
        return   # a Lab serves already: nothing on disk is touched, so nothing of its work is finished or swept from here
    server.daemon_threads = True   # an open log stream does not keep the Lab UI from stopping
    address(url, "Keep this terminal open: Ctrl+C stops the Lab UI, and the containers keep running.")
    compose.stop_on_signals()   # once, from the main thread, before it serves: a closed terminal ends what runs
    start_up()
    if browser:
        threading.Timer(0.5, open_browser, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def address(url, then):
    """The address of the Lab UI, the line to click, then what to know about it: in the look of install.sh, which printed
    the lines above it. Colors in a terminal (none with NO_COLOR), an arrow when it is UTF-8. What the machine lacks is not
    said here: the page says it, with its fix (machine)."""
    styled = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM", "dumb") != "dumb"
    bold, cyan, dim, off = ("\033[1m", "\033[36m", "\033[2m", "\033[0m") if styled else ("", "", "", "")
    encoding = os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") or os.environ.get("LANG") or ""
    arrow = "➜" if re.search(r"utf-?8", encoding, re.IGNORECASE) else ">"
    print(f"\n  {cyan}{arrow}{off}  {bold}Lab UI{off}   {cyan}{url}{off}\n     {dim}{then}{off}\n", flush=True)


def port_number(text):
    """A port from 1 to 65535, as an argument; the error argparse prints otherwise."""
    if not (text.isascii() and text.isdigit() and 0 < int(text) < 65536):
        raise argparse.ArgumentTypeError("must be a port number, for example 9001")
    return int(text)


def configured_port():
    """The port UI_PORT names, 9000 without it."""
    try:
        return port_number(os.environ["UI_PORT"]) if os.environ.get("UI_PORT") else 9000
    except argparse.ArgumentTypeError:
        sys.exit("UI_PORT must be a port number, for example 9001")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):   # the marks of `make check` and the names of items are not ASCII
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Gravitee Lab UI")
    parser.add_argument("--port", type=port_number, help="9000, or UI_PORT")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    parser.add_argument("--check", action="store_true", help="check every add-on, setup and playground, then exit")
    parser.add_argument("--list", action="store_true", help="print what is on and the consoles, then exit")
    parser.add_argument("--e2e", metavar="KIND:ID", help="start an add-on, a setup or a playground on a machine with no lab, "
                        "check that it works, remove what it made, then exit (for CI: see docs/maintaining.md)")
    parser.add_argument("--tested", type=int, metavar="N", help="with --e2e: the versions of the N-th set of x-gravitee.tested, "
                        "from 0; without it, the defaults of the core")
    parser.add_argument("--timeout", type=int, default=1800, metavar="SECONDS", help="with --e2e: how long the start may take")
    parser.add_argument("--keep", action="store_true", help="with --e2e: leave what was started running, to look at it")
    args = parser.parse_args(argv)
    find_docker()
    missing = requirement()
    if missing and (args.check or args.list or args.e2e):
        sys.exit(missing)
    layers.prepare()
    if args.check:
        sys.exit(check.run())
    if args.list:
        return listing()
    if args.e2e:
        from .. import e2e   # only a run needs it
        sys.exit(e2e.run(args.e2e, args.tested, args.timeout, args.keep))
    # The Lab UI that an update started again opens no browser: the page that asked for the update is there, and reloads
    return serve(args.port or configured_port(), not args.no_open and not os.environ.get(updates.RESTARTED))
