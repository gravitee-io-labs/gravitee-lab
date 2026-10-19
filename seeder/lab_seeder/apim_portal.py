"""The Developer Portal of each environment, from the portal folder of its seed:

    seed/apim/portal/                  the platform's own environment (APIM_ENV)
    seed/environments/<id>/portal/     the environment of that id: one customer's portal

    theme.yaml        the theme (PortalThemeSpec); `assets:` names its images, as paths relative to the file
    portal.yaml       the portal (PortalSpec): its name, its theme (activeThemeHrid) and the folders of its top bar
    pages/*.yaml      a page each (DocumentationSpec); `file:` replaces `content` and names a text of content/
    links/*.yaml      a link each (PortalLinkSpec)
    listings/*.yaml   the APIs shown, and where (PortalListingSpec); an apiHrid is the crossId of an export of apis/
    api-overview.md   optional: the text of the Overview page of every API a listing shows (Gravitee Markdown)
    api-docs/<feed>.yaml   optional: the AsyncAPI document of the listed API whose crossId is <environment id>-<feed>
    api-details.md    optional: the text of the Details tab of the page of every listed API (plain Markdown)

Every file is rendered with the variables of the seed and with `${ENV_ID}` and `${ENV_NAME}`, the environment's. A page's
own FreeMarker expression is written `$${...}`. A path is written as people read it ("Event feeds/Manufacturing"): the
platform keeps it as a slug ("/event-feeds/manufacturing"), and the folder shows the name the file gives it (displayName,
else the last words of the path). The Console's `pageBackground` and `cardBackground` colors are accepted in a theme: the
Automation API reads them as `background.page` and `background.card`, and drops the first two without a word.

The theme, the portal, the pages and the links go through the Automation API of the environment: it keeps each one by
hrid, gives it an id that does not change and publishes it. The portal makes its theme the one the portal shows. A portal
applied with other folders deletes the folders it no longer names, with what is in them.

A listing goes through the Console's navigation items instead: the Automation API shows an API in a listing only when the
Automation API itself made it (it finds it by hrid, not by crossId), and the exports a seed imports are not. Each API of a
listing becomes an item of its location, found by the crossId of the API in the environment, with an id the seed computes
(so a run finds what an earlier one made), published once its folders are. An API that is gone from the file loses its item, and
one that moved to another location in the file moves.

An API item has no page of its own: a portal shows "No content to show" for it. The platform makes the default Overview page of an
item on request (`_default-pages`, unpublished, with a template of its own), which the seed asks for, then publishes: the page is
the one of every listed API. With api-overview.md, the seed writes that text in each of them, rendered with the seed's variables
and the engine's `${ENV_ID}` and `${ENV_NAME}`; the platform's own expressions are written `$${api.name}`, and the platform
refuses a text whose expression it cannot evaluate. The Overview pages are recorded with the listing and go with its items.

An API that has a file in api-docs/ gets, next to its Overview page, an AsyncAPI page (published, after the Overview): the portal's
viewer shows the document as it is, with its channels, the direction of each (receive or send) and its messages with their schema
and example. The platform renders no template and checks nothing in an AsyncAPI text, and the viewer shows an error in place of a
document that is wrong or that is of a version it does not know, so the seed reads the document first (ASYNCAPI_VERSIONS).

The menu of a portal is only one of the two ways to an API. The page an API card opens (/catalog/api/<id>) reads the pages of the
API itself, which the Console keeps under its Documentation: the Details tab is the page that is the API's homepage, the
Documentation tab the others. The seed makes there a Markdown homepage from api-details.md (the platform evaluates the template of
a page of an API with the name, version and description of the API only, and refuses the components of Gravitee Markdown, so it is
not the Overview text) and an AsyncAPI page from the document. They are found by their type and name, and recorded with the
listing's pages; the platform deletes them with their API.

A hrid held by an object the seed did not create stops the seed: it never changes it. What it created is recorded, with the
environment and the hrid, and a second run changes nothing.
"""

import base64
import copy
import json
import os
import re
import uuid
from pathlib import Path

import requests
import yaml

from . import common
from .common import (Api, SeedError, created, excerpt, files, items, load, log, owned, parallel, pause, record, recorded, save_created, seed_file,
                    setting, text, wait_until)

SECTION = "apim-portal"
DEPENDS = ("apim",)   # the modules whose work this one reads on the platform (schedule.py)
MEANWHILE = ("apim",)   # a seed starts it with them: the portal of an environment waits for that environment's APIs (apply)
ORDER = ["portal-api-page", "portal-listing", "portal-link", "portal-page", "portal", "portal-theme"]   # what depends on what: removed first
KINDS = {"portal-theme": ("portal theme", "portal themes"), "portal": ("portal", "portals"),
         "portal-page": ("portal page", "portal pages"), "portal-link": ("portal link", "portal links"),
         "portal-listing": ("portal listing", "portal listings"), "portal-api-page": ("API page", "API pages")}
OVERVIEW = "api-overview.md"   # the optional text of the Overview page of every API a listing shows
OVERVIEW_TITLE = "Overview"   # what the platform calls the default page of an API item
DOCS = "api-docs"   # the folder of AsyncAPI documents, one file for each API: <feed>.yaml, the end of the API's crossId
DETAILS = "api-details.md"   # the optional text of the Details tab of the page of every API a listing shows
ASYNCAPI_TITLE = "AsyncAPI"   # the name of the page of an API's document, under its item and in the pages of the API
ASYNCAPI_ORDER = 1   # after the Overview page, which the platform makes first
# The versions the portal's viewer reads (its parser holds a schema of each): any other is an error shown in place of the page
ASYNCAPI_VERSIONS = ("2.0.0", "2.1.0", "2.2.0", "2.3.0", "2.4.0", "2.5.0", "2.6.0", "3.0.0", "3.1.0")
API_PAGE_VISIBILITY = "PUBLIC"   # of a page of the API itself: the visibility of the API says who may open the API and so its pages

RETRIES = 4   # times a request that got no answer (a connection that fails, a timeout), a 5xx or a 429 is tried again
RETRY_PAUSE = 8   # seconds before each: a management API that reloads is back within about half a minute
MAX_IMAGE = 256 * 1024   # bytes of an image file: a theme is one document of the platform's database, and its images are in it
IMAGE_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon"}
IMAGES = ("logo", "optionalLogo", "favicon", "backgroundImage")
HRID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+[a-zA-Z0-9]$")   # the Automation API's own pattern
VISIBILITY = ("PUBLIC", "PRIVATE")
PAGE_TYPES = ("GRAVITEE_MARKDOWN", "OPENAPI", "ASYNCAPI")
AREAS = ("TOP_NAVBAR", "HOMEPAGE")
IDS = uuid.UUID("5d0c2f6e-2b7a-4a58-9c0e-6a1f0f6f3c11")   # the namespace of the ids the seed computes for the items it creates


# --- Paths and values ---------------------------------------------------------------------

def slug(words):
    """What the platform makes of a path segment: lower case, any run of other characters one dash."""
    return re.sub(r"[^a-z0-9]+", "-", words.lower()).strip("-")


def parts_of(path):
    return [part for part in str(path or "").split("/") if part.strip()]


def normal(path):
    """A path as the platform keeps it: "/event-feeds/manufacturing". None for one that has no letter or digit in a segment."""
    slugs = [slug(part) for part in parts_of(path)]
    return "/" + "/".join(slugs) if slugs and all(slugs) else None


def prefixes(path):
    """The path and each folder above it: the platform makes the folders a path goes through."""
    names = path.strip("/").split("/")
    return ["/" + "/".join(names[:i]) for i in range(1, len(names) + 1)]


def covers(live, wanted):
    """Whether `live` holds everything `wanted` says: the platform adds what it defaults and the ids it computes."""
    if isinstance(wanted, dict):
        return isinstance(live, dict) and all(covers(live.get(key), value) for key, value in wanted.items())
    if isinstance(wanted, list):
        return isinstance(live, list) and len(live) == len(wanted) and all(covers(a, b) for a, b in zip(live, wanted))
    return live == wanted


def shown(path):
    """A seed file as a message names it."""
    try:
        return Path(path).resolve().relative_to(common.SEED_DIR.resolve()).as_posix()
    except ValueError:
        return str(path)


def inside(path):
    """The resolved path, when it is in the seed: a file of a seed names nothing outside it."""
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(common.SEED_DIR.resolve())
    except ValueError:
        raise SeedError(f"{shown(path)}: it is outside the seed") from None
    return resolved


# --- The files ----------------------------------------------------------------------------

class Scope:
    """What every file of one environment is rendered with: the variables of the seed and the engine's ${ENV_ID} and
    ${ENV_NAME}."""

    def __init__(self, env, name):
        self.provided = {"ENV_ID": env, "ENV_NAME": name}

    def spec(self, path, keys):
        try:
            spec = load(path, provided=self.provided)
        except SeedError as e:
            raise SeedError(str(e).replace(str(path), shown(path))) from None
        except yaml.YAMLError as e:
            raise SeedError(f"{shown(path)}: it is not YAML ({excerpt(e, 200)})") from None
        if not isinstance(spec, dict):
            raise SeedError(f"{shown(path)}: it is not a mapping")
        unknown = sorted(set(spec) - set(keys))
        if unknown:
            raise SeedError(f"{shown(path)}: {unknown[0]} is not a key of this file (it has {', '.join(sorted(keys))})")
        return spec

    def text(self, path):
        """The text of a page: `$${api.name}` is left as `${api.name}`, for the page's own templating to evaluate."""
        try:
            return text(path, provided=self.provided)
        except SeedError as e:
            raise SeedError(str(e).replace(str(path), shown(path))) from None


def hrid_of(spec, path, seen):
    hrid = spec.get("hrid")
    if not hrid or not HRID.match(str(hrid)):
        raise SeedError(f"{shown(path)}: hrid {hrid!r} is missing or has a character the platform refuses (letters, digits, - _ .)")
    if hrid in seen:
        raise SeedError(f"{shown(path)}: the hrid {hrid} is also {seen[hrid]}'s")
    seen[hrid] = shown(path)
    return str(hrid)


def required(spec, path, *keys):
    for key in keys:
        if spec.get(key) in (None, ""):
            raise SeedError(f"{shown(path)}: {key} is missing")


def visibility_of(spec, path):
    if spec.get("visibility") not in (None, *VISIBILITY):
        raise SeedError(f"{shown(path)}: visibility is {spec['visibility']}, PUBLIC or PRIVATE")


def place(spec, path, known):
    """The location of a page, a link or a listed API, as the platform keeps it, and one that is a path of the portal."""
    where = normal(spec.get("location"))
    if where is None or where not in known:
        raise SeedError(f"{shown(path)}: location {spec.get('location')!r} is no path of portal.yaml")
    return where


def image(asset, theme):
    """The data URI of an image file, which is what the platform's theme holds."""
    file = inside(theme.parent / asset)
    kind = IMAGE_TYPES.get(file.suffix.lower())
    if not file.is_file():
        raise SeedError(f"{shown(theme)}: {asset} is not a file")
    if not kind:
        raise SeedError(f"{shown(theme)}: {asset} is not an image of {', '.join(sorted(IMAGE_TYPES))}")
    size = file.stat().st_size
    if size > MAX_IMAGE:
        raise SeedError(f"{shown(theme)}: {asset} is {size // 1024} KB, over {MAX_IMAGE // 1024} KB")
    return f"data:{kind};base64,{base64.b64encode(file.read_bytes()).decode()}"


def theme_body(scope, path):
    spec = scope.spec(path, ("hrid", "name", "definition", "assets", *IMAGES))
    hrid_of(spec, path, {})
    required(spec, path, "name")
    definition = copy.deepcopy(spec.get("definition") or {})
    color = definition.get("color")
    if isinstance(color, dict):
        for old, new in (("pageBackground", "page"), ("cardBackground", "card")):
            if old in color:
                color.setdefault("background", {})[new] = color.pop(old)
    body = {"hrid": str(spec["hrid"]), "name": spec["name"], "definition": definition}
    body.update({key: spec[key] for key in IMAGES if spec.get(key)})
    assets = spec.get("assets") or {}
    for key in assets:
        if key not in IMAGES:
            raise SeedError(f"{shown(path)}: assets.{key} is not an image of a theme ({', '.join(IMAGES)})")
        body[key] = image(assets[key], path)
    return body


def portal_body(scope, path, theme):
    spec = scope.spec(path, ("hrid", "name", "activeThemeHrid", "structure"))
    hrid_of(spec, path, {})
    required(spec, path, "name")
    active = spec.get("activeThemeHrid") or (theme and theme["hrid"])
    if active and not (theme and theme["hrid"] == active):
        raise SeedError(f"{shown(path)}: activeThemeHrid {active} is no theme of this folder")
    entries, listed, titles = [], set(), {}
    for entry in (spec.get("structure") or {}).get("topNavbar") or []:
        where = normal(entry.get("path") if isinstance(entry, dict) else None)
        if where is None:
            raise SeedError(f"{shown(path)}: a path of structure.topNavbar is missing or has a segment with no letter or digit")
        if where in listed:
            raise SeedError(f"{shown(path)}: the path {entry['path']} is there twice")
        if set(entry) - {"path", "displayName", "visibility"}:
            raise SeedError(f"{shown(path)}: a path of structure.topNavbar has path, displayName and visibility; it shows in the order of the list")
        visibility_of(entry, path)
        listed.add(where)
        words = parts_of(entry["path"])
        for folder, words_of in zip(prefixes(where)[:-1], words):
            titles.setdefault(folder, words_of.strip())   # a folder the path goes through: the platform names it for its words
        titles[where] = entry.get("displayName") or words[-1].strip()
        entries.append({**{key: value for key, value in entry.items() if key != "path"}, "path": where, "displayName": titles[where]})
    body = {"hrid": str(spec["hrid"]), "name": spec["name"], "structure": {"topNavbar": entries}}
    if active:
        body["activeThemeHrid"] = active
    return body, titles


def page_body(scope, path, known, seen):
    spec = scope.spec(path, ("hrid", "name", "type", "file", "content", "location", "order", "area", "visibility"))
    hrid_of(spec, path, seen)
    required(spec, path, "name")
    if (spec.get("file") is None) == (spec.get("content") is None):
        raise SeedError(f"{shown(path)}: it has file or content, one of the two")
    if spec.get("type", PAGE_TYPES[0]) not in PAGE_TYPES or spec.get("area", AREAS[0]) not in AREAS:
        raise SeedError(f"{shown(path)}: type is {', '.join(PAGE_TYPES)} and area {' or '.join(AREAS)}")
    visibility_of(spec, path)
    if spec.get("file") is not None:
        file = inside(path.parent / spec["file"])
        if not file.is_file():
            raise SeedError(f"{shown(path)}: {spec['file']} is not a file")
        content = scope.text(file)
    else:
        content = str(spec["content"])
    body = {"hrid": str(spec["hrid"]), "name": spec["name"], "type": spec.get("type", PAGE_TYPES[0]), "content": content,
            "area": spec.get("area", AREAS[0])}
    body.update({key: spec[key] for key in ("order", "visibility") if spec.get(key) is not None})
    if spec.get("location") is not None and body["area"] == "TOP_NAVBAR":
        body["location"] = place(spec, path, known)
    return body


def link_body(scope, path, known, seen):
    spec = scope.spec(path, ("hrid", "name", "href", "location", "order", "visibility"))
    hrid_of(spec, path, seen)
    required(spec, path, "name", "href")
    visibility_of(spec, path)
    body = {key: spec[key] for key in ("hrid", "name", "href", "order", "visibility") if spec.get(key) is not None}
    body["hrid"] = str(body["hrid"])
    if spec.get("location") is not None:
        body["location"] = place(spec, path, known)
    return body


def listing_body(scope, path, known, cross_ids, seen):
    spec = scope.spec(path, ("hrid", "apis"))
    hrid_of(spec, path, seen)
    if not isinstance(spec.get("apis"), list) or not spec["apis"]:
        raise SeedError(f"{shown(path)}: apis is missing or empty")
    apis, listed = [], {}
    for entry in spec["apis"]:
        if not isinstance(entry, dict) or not entry.get("apiHrid"):
            raise SeedError(f"{shown(path)}: an entry of apis has no apiHrid")
        if entry["apiHrid"] not in cross_ids:
            raise SeedError(f"{shown(path)}: {entry['apiHrid']} is the crossId of no API in the apis/ of this environment")
        visibility_of(entry, path)
        where = place(entry, path, known)
        listed[where] = listed.get(where, 0) + 1
        # An order is a position among the items of a location: the platform keeps one that is alone in its folder at 0
        wanted = {"apiHrid": entry["apiHrid"], "location": where, "order": entry.get("order", listed[where] - 1)}
        if entry.get("visibility"):
            wanted["visibility"] = entry["visibility"]
        apis.append(wanted)
    return {"hrid": str(spec["hrid"]), "apis": apis}


def cross_ids(scope, parts):
    """The crossId of each API export of the environment: what a listing may name. Read as written: only this value is rendered."""
    found = set()
    for path in files(*parts, "apis", suffixes=(".json",)):
        try:
            cross = json.loads(path.read_text(encoding="utf-8")).get("api", {}).get("crossId")
        except (ValueError, AttributeError):
            raise SeedError(f"{shown(path)}: it is not an API export") from None
        if cross:
            found.add(common.render(cross, shown(path), provided=scope.provided))
    return found


def asyncapi(scope, path):
    """The text of an AsyncAPI file, which the platform keeps as it is (it renders no template in one and checks nothing): the
    viewer is the one that says, on the page, that a document is wrong. So the file is read here, for what it needs to show."""
    document = scope.text(path)
    try:
        spec = yaml.safe_load(document)
    except yaml.YAMLError as e:
        raise SeedError(f"{shown(path)}: it is not YAML ({excerpt(e, 200)})") from None
    if not isinstance(spec, dict) or spec.get("asyncapi") is None:
        raise SeedError(f"{shown(path)}: it is no AsyncAPI document: it has no asyncapi key")
    if str(spec["asyncapi"]) not in ASYNCAPI_VERSIONS:
        raise SeedError(f"{shown(path)}: asyncapi {spec['asyncapi']} is a version the portal does not show ({', '.join(ASYNCAPI_VERSIONS)})")
    info = spec.get("info")
    if not isinstance(info, dict) or any(info.get(key) in (None, "") for key in ("title", "version")):
        raise SeedError(f"{shown(path)}: info needs a title and a version")
    if not isinstance(spec.get("channels"), dict) or not spec["channels"]:
        raise SeedError(f"{shown(path)}: channels is missing or empty")
    return document


class Folder:
    """The portal files of one environment, read and checked: nothing was asked of the platform."""

    def __init__(self, env, name, parts):
        self.env, self.name, self.parts = env, name, parts
        self.theme = self.portal = None
        self.titles = {}   # the title of each folder of the top bar, by its path
        self.pages, self.links, self.listings = [], [], []
        self.overview = None   # the text of the Overview page of every listed API, when the folder has one
        self.details = None   # the text of the Details tab of the page of every listed API, when the folder has one
        self.docs = {}   # the AsyncAPI text of the listed APIs that have one, by crossId
        self.sources = {}   # the file each of those comes from, as a message names it

    @property
    def empty(self):
        return not (self.theme or self.portal or self.pages or self.links or self.listings)

    def read(self):
        scope, folder = Scope(self.env, self.name), common.SEED_DIR.joinpath(*self.parts, "portal")
        for path in sorted(folder.iterdir()) if folder.is_dir() else []:
            if path.is_file() and not path.name.startswith(".") and path.name not in ("theme.yaml", "portal.yaml", OVERVIEW, DETAILS):
                raise SeedError(f"{shown(path)}: " + ("a second portal: there is one portal for an environment"
                                                      if path.stem.startswith("portal") else
                                                      f"a portal folder has theme.yaml, portal.yaml, {OVERVIEW}, {DETAILS}, pages/, content/, "
                                                      f"links/, listings/ and {DOCS}/"))
        theme, portal = seed_file(*self.parts, "portal", "theme.yaml"), seed_file(*self.parts, "portal", "portal.yaml")
        overview, details = seed_file(*self.parts, "portal", OVERVIEW), seed_file(*self.parts, "portal", DETAILS)
        pages, links, listings = (files(*self.parts, "portal", name) for name in ("pages", "links", "listings"))
        docs = files(*self.parts, "portal", DOCS, suffixes=(".yaml", ".yml"))
        for path in sorted((folder / DOCS).iterdir()) if (folder / DOCS).is_dir() else []:
            if path.is_file() and not path.name.startswith(".") and path not in docs:
                raise SeedError(f"{shown(path)}: {DOCS}/ holds the AsyncAPI document of an API, <feed>.yaml")
        if not (theme or portal or overview or details or pages or links or listings or docs):
            return self
        if not portal:
            raise SeedError(f"{shown(folder)}: portal.yaml is missing: a theme, a page, a link and a listing belong to a portal")
        if theme:
            self.theme = theme_body(scope, theme)
        self.portal, known = portal_body(scope, portal, self.theme)
        self.titles = known
        seen = {"page": {}, "link": {}, "listing": {}}
        for path in pages:
            self.pages.append(page_body(scope, path, known, seen["page"]))
        if sum(page["area"] == "HOMEPAGE" for page in self.pages) > 1:
            raise SeedError(f"{shown(folder / 'pages')}: a portal has one homepage")
        for path in links:
            self.links.append(link_body(scope, path, known, seen["link"]))
        crosses = cross_ids(scope, self.parts) if listings else set()
        for path in listings:
            self.listings.append(listing_body(scope, path, known, crosses, seen["listing"]))
        if overview:
            if not listings:
                raise SeedError(f"{shown(overview)}: it is the Overview page of the APIs a listing shows, and this portal has no listing")
            self.overview = scope.text(overview)
            if not self.overview.strip():
                raise SeedError(f"{shown(overview)}: it is empty")
        if details:
            if not listings:
                raise SeedError(f"{shown(details)}: it is the Details tab of the APIs a listing shows, and this portal has no listing")
            self.details = scope.text(details)
            if not self.details.strip():
                raise SeedError(f"{shown(details)}: it is empty")
        if docs and not listings:
            raise SeedError(f"{shown(folder / DOCS)}: they are the documents of the APIs a listing shows, and this portal has no listing")
        listed = {entry["apiHrid"] for listing in self.listings for entry in listing["apis"]}
        for path in docs:
            cross = f"{self.env}-{path.stem}"
            if cross not in listed:
                raise SeedError(f"{shown(path)}: {cross} is no API that a listing of this portal shows")
            if cross in self.docs:
                raise SeedError(f"{shown(path)}: {cross} has its document in {self.sources[cross]} already")
            self.docs[cross], self.sources[cross] = asyncapi(scope, path), shown(path)
        return self


def folders():
    """(environment id, seed folder) of each portal folder of the seed: the platform's own environment has None."""
    found = []
    if common.SEED_DIR.joinpath("apim", "portal").is_dir():
        found.append((None, ("apim",)))
    environments = common.SEED_DIR / "environments"
    if environments.is_dir():
        found += [(p.name, ("environments", p.name)) for p in sorted(environments.iterdir()) if (p / "portal").is_dir()]
    return found


# --- The platform -------------------------------------------------------------------------

def reason(answer):
    """What a refusal says, the platform's message when it gives one."""
    try:
        return answer.json().get("message") or answer.text
    except (ValueError, AttributeError):
        return answer.text


class Tree:
    """The navigation items of the top bar, as the Console lists them: a folder is found by the path of its titles."""

    def __init__(self, listed):
        self.items = {item["id"]: item for item in listed}

    def child(self, parent, title):
        """The page of an item that has this title (the first, by order), or None."""
        return next((i for i in sorted(self.items.values(), key=lambda i: i.get("order", 0))
                     if i["type"] == "PAGE" and i.get("parentId") == parent and i.get("title") == title), None)

    def above(self, item):
        """The folders the item is in, from the first to the nearest."""
        chain = []
        while item.get("parentId") in self.items:
            item = self.items[item["parentId"]]
            chain.insert(0, item)
        return chain

    def at(self, path, titles):
        """The folder of this path: from the first folder down, the one of the parent that has the title the portal file gives."""
        folder = None
        for where in prefixes(path):
            folder = next((item for item in self.items.values() if item["type"] == "FOLDER"
                           and item.get("parentId") == (folder["id"] if folder else None)
                           and slug(item["title"]) == slug(titles[where])), None)
            if not folder:
                return None
        return folder


class Portals:
    def __init__(self):
        management = setting("APIM_MANAGEMENT_URL")
        self.org = setting("APIM_ORG", "DEFAULT")
        self.path = setting("APIM_MANAGEMENT_PATH", "/management")   # /apim/rest on the Cloud Gate of Gravitee Cloud
        self.console, self.automation = Api(management), Api(setting("AIM_AUTOMATION_URL", management))
        self.found = {}   # environment -> whether the platform has it, asked once

    def v1(self, env):
        return f"{self.path}/organizations/{self.org}/environments/{env}"

    def v2(self, env):
        return f"{self.path}/v2/organizations/{self.org}/environments/{env}"

    def auto(self, env):
        return f"/automation/organizations/{self.org}/environments/{env}"

    def connect(self):
        token = os.getenv("APIM_TOKEN")
        for api in (self.console, self.automation):
            if token:
                api.session.headers["Authorization"] = f"Bearer {token}"
            else:
                api.session.auth = (setting("APIM_USERNAME"), setting("APIM_PASSWORD"))
        wait_until(SECTION, "API Management", lambda: self.console.get(f"{self.path}/organizations/{self.org}/environments"))

    def ask(self, what, api, method, path, passed=(), **kwargs):
        """The response of a call of the management API, asked again after a pause when the platform cannot answer now: a connection
        that fails or times out as much as a 5xx or a 429, RETRIES times, and then the seed stops with what it said. Any other refusal
        stops it at once with the platform's message, and a status in `passed` is handed back to the caller, which says what it means."""
        for attempt in range(RETRIES + 1):
            try:
                response = api.send(method, path, timeout=30, **kwargs)
            except requests.RequestException as e:
                short = said = f"no answer from the management API: {type(e).__name__}"   # its address is no help to a reader
            else:
                if response.ok or response.status_code in passed:
                    return response
                short, said = f"HTTP {response.status_code}", f"HTTP {response.status_code} {excerpt(response.text, 300)}".rstrip()
                if response.status_code < 500 and response.status_code != 429:
                    raise SeedError(f"{what}: {said}")
            if attempt == RETRIES:
                raise SeedError(f"{what}: {said} ({RETRIES + 1} tries)")
            log(SECTION, f"! {what}: {short}, trying again")
            pause(RETRY_PAUSE)

    def environment(self, env, name):
        """The name of an environment, which the files of its portal are rendered with; the environment must be there."""
        found = self.ask(f"Environment {env}", self.console, "GET", self.v1(env), passed=(400, 404))
        if found.status_code != 200:
            raise SeedError(f"Environment {env} does not exist on this platform")
        return name or found.json().get("name") or env

    def present(self, env):
        """Whether the platform still has the environment. Asked once: a removal of what an environment held, after the
        environment was removed, would otherwise stop on the first object, for the platform answers 400 to every route of it."""
        if env not in self.found:
            self.found[env] = self.ask(f"Environment {env}", self.console, "GET", self.v1(env), passed=(400, 404)).status_code == 200
        return self.found[env]

    # --- Seeding --------------------------------------------------------------------------

    def read(self, targets):
        """The portal files of every environment, read and checked before the first change."""
        out, seen = [], set()
        for env, parts in targets:
            env = env or setting("APIM_ENV", "DEFAULT")
            if env in seen:
                raise SeedError(f"Environment {env}: its portal is in seed/apim/portal and in seed/environments/{env}/portal")
            seen.add(env)
            named = seed_file(*parts, "environment.yaml")
            name = (load(named, provided={"ENV_ID": env}) if named else {}).get("name")
            folder = Folder(env, self.environment(env, name), parts).read()
            if not folder.empty:
                out.append(folder)
        return out

    def apply(self, folder):
        common.reached(f"apim:{folder.env}", also="apim")   # its listings name APIs that API Management is making
        log(SECTION, f"Developer Portal of {folder.env}")
        if folder.theme:
            self.upsert(folder, "portal-theme", folder.theme)
        self.upsert(folder, "portal", folder.portal)
        for page in folder.pages:
            self.upsert(folder, "portal-page", page)
        for link in folder.links:
            self.upsert(folder, "portal-link", link)
        self.forget(folder)
        if folder.listings:
            tree, apis = None, self.apis(folder.env)
            for listing in folder.listings:
                tree = self.listing(folder, listing, apis, tree)

    def forget(self, folder):
        """What the seed made in this environment from a file that is no more: deleted by its hrid and dropped from the record, so that
        a removed file leaves nothing behind and the Delete dialog counts only what exists."""
        wanted = {"portal-theme": {folder.theme["hrid"]} if folder.theme else set(),
                  "portal-page": {page["hrid"] for page in folder.pages}, "portal-link": {link["hrid"] for link in folder.links}}
        stale = [x for x in created() if x["product"] == SECTION and x.get("env") == folder.env and x["kind"] in wanted
                 and x.get("hrid") not in wanted[x["kind"]]]
        for x in stale:
            self.delete(x)
            log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}': its file is gone")
        if stale:
            with common._state:
                save_created([x for x in created() if x not in stale])

    def upsert(self, folder, kind, body):
        """The object, created when the platform has none, updated when it holds the seed's and is not as the file says."""
        one, many = KINDS[kind]
        env, hrid, portal = folder.env, body["hrid"], folder.portal["hrid"]
        collection = {"portal-theme": "/portal-themes", "portal": "/portals", "portal-page": f"/portals/{portal}/documentations",
                      "portal-link": f"/portals/{portal}/links"}[kind]
        what = f"{one.capitalize()} {hrid} in {env}"
        found = self.ask(what, self.automation, "GET", f"{self.auto(env)}{collection}/{hrid}", passed=(404,))
        verb = "created"
        if found.status_code == 200:
            if not recorded(SECTION, kind, found.json().get("id")):
                raise SeedError(f"{what}: the hrid is taken by one the seed did not create")
            if covers(found.json(), body):
                log(SECTION, f"{one.capitalize()} exists: {env}: {hrid}")
                return
            verb = "updated"
        state = self.ask(what, self.automation, "PUT", f"{self.auto(env)}{collection}", json=body).json()
        problems = state.get("errors") or {}
        if problems.get("severe"):
            raise SeedError(f"{what}: {excerpt('; '.join(problems['severe']), 300)}")
        extra = {"portal": portal} if kind in ("portal-page", "portal-link") else {}
        record(SECTION, kind, many, f"{env}: {hrid}", state["id"], env=env, hrid=hrid, **extra)
        log(SECTION, f"✓ {one.capitalize()} {verb}: {env}: {hrid}")

    # --- Listings -------------------------------------------------------------------------

    def apis(self, env):
        """The APIs of the environment by crossId."""
        out, page = {}, 1
        while True:
            body = self.ask(f"APIs of {env}", self.console, "GET", f"{self.v2(env)}/apis", params={"page": page, "perPage": 100}).json()
            out.update({api["crossId"]: api for api in items(body) if api.get("crossId")})
            if page >= body.get("pagination", {}).get("pageCount", 1):
                return out
            page += 1

    def navigation(self, env):
        listed = self.ask(f"Navigation of {env}", self.console, "GET", f"{self.v2(env)}/portal-navigation-items",
                          params={"area": "TOP_NAVBAR"}).json()
        return Tree(listed.get("items", []))

    def listing(self, folder, listing, apis, tree):
        """The API items of a listing: each at its location, published once the folders above it are. Returns the tree."""
        env, hrid, portal = folder.env, listing["hrid"], folder.portal["hrid"]
        what, name = f"Portal listing {hrid} in {env}", f"{env}: {hrid}"
        tree = tree or self.navigation(env)
        wanted, changed = {}, False
        for entry in listing["apis"]:
            api = apis.get(entry["apiHrid"])
            if not api:
                raise SeedError(f"{what}: {entry['apiHrid']} is the crossId of no API in {env}: the seed imports it first")
            parent = tree.at(entry["location"], folder.titles)
            if not parent:
                raise SeedError(f"{what}: {entry['location']} is not in the navigation of {env}")
            item_id = str(uuid.uuid5(IDS, f"{env}/{portal}/{hrid}/{entry['apiHrid']}"))
            visibility = entry.get("visibility") or parent.get("visibility", "PUBLIC")
            wanted[item_id] = {"api": api, "parent": parent, "visibility": visibility, "order": entry["order"]}
        for item_id, want in wanted.items():
            changed |= self.item(env, what, tree, item_id, want)
        old = next((x for x in created() if (x["product"], x["kind"], x["name"]) == (SECTION, "portal-listing", name)), None)
        for stale in [i for i in (old or {}).get("items", []) if i not in wanted]:
            self.ask(what, self.console, "DELETE", f"{self.v2(env)}/portal-navigation-items/{stale}", passed=(404,))
            tree.items.pop(stale, None)
            changed = True
        if changed or not old or old.get("items") != list(wanted):
            record(SECTION, "portal-listing", KINDS["portal-listing"][1], name, str(uuid.uuid5(IDS, f"{env}/{portal}/{hrid}")),
                   env=env, hrid=hrid, portal=portal, items=list(wanted))
            log(SECTION, f"✓ Portal listing {'updated' if old else 'created'}: {name}")
        else:
            log(SECTION, f"Portal listing exists: {name}")
        self.api_pages(folder, listing, tree, wanted)
        return tree

    def item(self, env, what, tree, item_id, want):
        """One API item, created or put right. True when the platform was changed."""
        api, parent, base = want["api"], want["parent"], f"{self.v2(env)}/portal-navigation-items"
        item, changed = tree.items.get(item_id), False
        if not item:
            twin = next((i for i in tree.items.values() if i["type"] == "API" and i.get("apiId") == api["id"]
                         and i.get("parentId") == parent["id"]), None)
            if twin:
                raise SeedError(f"{what}: {api['name']} is already in {parent['title']}, put there by someone else")
            item = self.ask(what, self.console, "POST", base, json={
                "id": item_id, "type": "API", "title": "", "area": "TOP_NAVBAR", "parentId": parent["id"],
                "visibility": want["visibility"], "apiId": api["id"], "order": want["order"]}).json()
            tree.items[item_id], changed = item, True
        elif item.get("parentId") != parent["id"]:
            # Moved to another location in its file. A child is published only under published folders, so it moves unpublished
            # and is published below, with the folders it now is in
            self.put(env, what, item, {"parentId": parent["id"], "published": False})
            changed = True
        for folder in tree.above(item):
            if not folder.get("published"):   # a child is published only when the folders above it are: this one takes its children along
                self.put(env, what, folder, {"published": True}, params={"propagatePublishToChildren": "true"})
                tree.items = self.navigation(env).items
                item, changed = tree.items[item_id], True
                break
        if not (item.get("published") and item.get("visibility") == want["visibility"] and item.get("order") == want["order"]):
            self.put(env, what, item, {"published": True, "visibility": want["visibility"], "order": want["order"]})
            changed = True
        return changed

    def api_pages(self, folder, listing, tree, wanted):
        """The pages of each API item of a listing, published: its Overview (with the text of api-overview.md when the folder has
        one) and, for an API that has a document, the AsyncAPI page after it; then the pages of the API itself. An API item has no
        child page: the platform makes the default Overview when it is asked (unpublished, hence this), and a page is found by its
        parent and its title. Recorded for the listing: a page that is gone with its item is not in the record any more."""
        env, hrid, portal = folder.env, listing["hrid"], folder.portal["hrid"]
        what, name, base = f"Portal listing {hrid} in {env}", f"{env}: {hrid}", f"{self.v2(env)}/portal-navigation-items"
        old = next((x for x in created() if (x["product"], x["kind"], x["name"]) == (SECTION, "portal-api-page", name)), None)
        before, before_own = (old or {}).get("items", []), (old or {}).get("legacy", [])
        pages = []   # the ids of the pages of the navigation, an API's Overview then its AsyncAPI page

        def write(own):
            record(SECTION, "portal-api-page", KINDS["portal-api-page"][1], name, str(uuid.uuid5(IDS, f"{env}/{portal}/{hrid}/pages")),
                   env=env, hrid=hrid, portal=portal, items=list(pages), legacy=own, count=len(pages) + len(own))

        tree.items = self.navigation(env).items   # read again: unpublishing an item, as a move does, unpublishes its children
        lacking = [item_id for item_id in wanted if not tree.child(item_id, OVERVIEW_TITLE)]
        changed = bool(lacking)
        for item_id in lacking:
            ours = tree.child(item_id, ASYNCAPI_TITLE)
            if ours and ours["id"] in before:   # the platform makes the Overview of an item that has no page: this one is made again after it
                self.ask(what, self.console, "DELETE", f"{base}/{ours['id']}", passed=(404,))
                tree.items.pop(ours["id"], None)
        if lacking:
            self.ask(what, self.console, "POST", f"{base}/_default-pages", json={"ids": lacking})
            tree.items = self.navigation(env).items
        for item_id, want in wanted.items():
            page = tree.child(item_id, OVERVIEW_TITLE)
            if not page:
                other = any(i.get("parentId") == item_id for i in tree.items.values())   # the platform makes the page of an item that has none
                raise SeedError(f"{what}: " + (f"{want['api']['name']} has a page that is not called {OVERVIEW_TITLE}, so the platform made no "
                                               f"{OVERVIEW_TITLE} page for it" if other else
                                               f"the platform made no {OVERVIEW_TITLE} page for {want['api']['name']}"))
            if folder.overview is not None:   # before it is published: the default text is never shown
                changed |= self.page_text(env, f"Overview page of {want['api']['name']} in {env} (portal/{OVERVIEW})", page, PAGE_TYPES[0],
                                          folder.overview, "the platform cannot render it")
            if not (page.get("published") and page.get("visibility") == want["visibility"]):
                self.put(env, what, page, {"published": True, "visibility": want["visibility"]})
                changed = True
            pages.append(page["id"])
            document, touched = self.document(folder, what, tree, item_id, want, before)
            pages += [document] if document else []
            changed |= touched

        def carried(own):
            return own + [e for e in before_own if e["page"] not in {o["page"] for o in own}]   # still the seed's until it is deleted

        own, touched = self.own_pages(folder, what, wanted, name, lambda own: write(carried(own)))
        changed |= touched
        for stale in [e for e in before_own if e["page"] not in {o["page"] for o in own}]:
            held = any(e == stale for x in created() if (x["product"], x["kind"]) == (SECTION, "portal-api-page") and x["name"] != name
                       for e in x.get("legacy", []))   # another listing of the same API keeps it
            if not held:
                self.ask(what, self.console, "DELETE", f"{self.v2(env)}/apis/{stale['api']}/pages/{stale['page']}", passed=(404,))
        if changed or not old or before != pages or before_own != own:
            write(own)
            log(SECTION, f"✓ API pages {'updated' if old else 'created'}: {name} ({len(pages) + len(own)})")
        else:
            log(SECTION, f"API pages exist: {name}")

    def document(self, folder, what, tree, item_id, want, before):
        """The AsyncAPI page of an API item: made when the API has a document, written, then published. A page the seed made for an
        API that has no document any more goes. Returns the id of the page, None when there is none, and whether the platform changed."""
        env, api, base = folder.env, want["api"], f"{self.v2(folder.env)}/portal-navigation-items"
        page, doc = tree.child(item_id, ASYNCAPI_TITLE), folder.docs.get(want["api"]["crossId"])
        if doc is None:
            if page and page["id"] in before:
                self.ask(what, self.console, "DELETE", f"{base}/{page['id']}", passed=(404,))
                tree.items.pop(page["id"], None)
                return None, True
            return None, False
        changed = not page
        if not page:
            page = self.ask(what, self.console, "POST", base, json={
                "type": "PAGE", "title": ASYNCAPI_TITLE, "area": "TOP_NAVBAR", "parentId": item_id, "visibility": want["visibility"],
                "contentType": PAGE_TYPES[2], "order": ASYNCAPI_ORDER}).json()
            tree.items[page["id"]] = page
        changed |= self.page_text(env, f"AsyncAPI page of {api['name']} in {env} ({folder.sources[api['crossId']]})", page, PAGE_TYPES[2],
                                  doc, "the platform refuses it")   # before it is published: the platform's sample is never shown
        if not (page.get("published") and page.get("visibility") == want["visibility"] and page.get("order") == ASYNCAPI_ORDER):
            self.put(env, what, page, {"published": True, "visibility": want["visibility"], "order": ASYNCAPI_ORDER})
            changed = True
        return page["id"], changed

    def page_text(self, env, what, page, kind, text, refusal):
        """The text of a page of the navigation, written when it is not the stored one. True when it was. The platform renders a text
        of Gravitee Markdown before it keeps it and refuses one whose expression it cannot evaluate: that stops the seed, with its message."""
        address = f"{self.v2(env)}/portal-page-contents/{page['portalPageContentId']}"
        stored = self.ask(what, self.console, "GET", address).json()
        if stored.get("type") == kind and stored.get("content") == text:
            return False
        answer = self.ask(what, self.console, "PUT", address, json={"type": kind, "content": text}, passed=(400,))
        if answer.status_code == 400:
            raise SeedError(f"{what}: {refusal}: {excerpt(reason(answer), 250)}")
        return True

    def own_pages(self, folder, what, wanted, name, note):
        """The pages of each listed API itself, which the page an API card opens reads (the Console keeps them under the API's
        Documentation): its Details tab shows the page that is the API's homepage, its Documentation tab the others. Each is found by
        its type and name among the pages of the API, made when it is not there, put right when it differs and published. A page is
        recorded as soon as it exists (`note` writes the record), for the seed takes none it did not make. Returns the entries to
        record, `{api, page}` each, and whether the platform was changed."""
        env, own, changed = folder.env, [], False
        mine = {e["page"] for x in created() if (x["product"], x["kind"]) == (SECTION, "portal-api-page") for e in x.get("legacy", [])}
        for want in wanted.values():
            api = want["api"]
            asked = ([("MARKDOWN", OVERVIEW_TITLE, folder.details, True, f"Details tab of {api['name']} in {env} (portal/{DETAILS})")]
                     if folder.details is not None else [])
            if api["crossId"] in folder.docs:
                asked.append(("ASYNCAPI", ASYNCAPI_TITLE, folder.docs[api["crossId"]], False,
                              f"Documentation tab of {api['name']} in {env} ({folder.sources[api['crossId']]})"))
            if not asked:
                continue
            address = f"{self.v2(env)}/apis/{api['id']}/pages"
            live = self.ask(what, self.console, "GET", address).json().get("pages", [])
            for kind, title, content, homepage, label in asked:
                page = next((p for p in live if p.get("type") == kind and p.get("name") == title), None)
                body = {"type": kind, "name": title, "visibility": API_PAGE_VISIBILITY, "homepage": homepage, "content": content}
                if page and page["id"] not in mine:
                    raise SeedError(f"{label}: the page is taken by one the seed did not create")
                if not page:
                    page = self.refusable(label, "POST", address, body)
                    mine.add(page["id"])
                    own.append({"api": api["id"], "page": page["id"]})
                    note(own)
                    changed = True
                else:
                    own.append({"api": api["id"], "page": page["id"]})
                    if (page.get("content"), page.get("visibility"), bool(page.get("homepage"))) != (content, API_PAGE_VISIBILITY, homepage):
                        page = self.refusable(label, "PUT", f"{address}/{page['id']}", {**body, "order": page.get("order", 0)})
                        changed = True
                if not page.get("published"):
                    self.ask(label, self.console, "POST", f"{address}/{page['id']}/_publish")
                    changed = True
        return own, changed

    def refusable(self, what, method, address, body):
        """What the platform answers to a page of an API it is given: it renders the template of a Markdown page and checks its
        HTML first, and refuses one it cannot (a 400 that names the expression or the tag), which stops the seed with that."""
        answer = self.ask(what, self.console, method, address, json=body, passed=(400,))
        if answer.status_code == 400:
            raise SeedError(f"{what}: the platform refuses it: {excerpt(reason(answer), 250)}")
        return answer.json()

    def put(self, env, what, item, changes, params=None):
        """An item put as it is with these changes: the platform moves one sent without its parent to the root."""
        body = {"type": item["type"], "title": item["title"], "order": item.get("order", 0), "published": item.get("published", False),
                "visibility": item.get("visibility", "PUBLIC")}
        for key in ("parentId", "apiId", "categoryIds"):
            if item.get(key) is not None:
                body[key] = item[key]
        body.update(changes)
        response = self.ask(what, self.console, "PUT", f"{self.v2(env)}/portal-navigation-items/{item['id']}", json=body, params=params)
        item.update(response.json() if response.content else body)

    # --- Removal --------------------------------------------------------------------------

    def delete(self, x):
        """What the record says, where it says. False when its environment is gone: nothing of it is left to delete. The pages of a
        listing in the navigation go with its API items, which a listing removes, and so need no call of their own; the pages of the
        APIs themselves go with their API, and are deleted here all the same, for an API may stay."""
        env, kind = x["env"], x["kind"]
        if not self.present(env):
            return False
        what = f"{KINDS[kind][0].capitalize()} {x['hrid']} in {env}"
        if kind == "portal-listing":
            for item_id in x.get("items", []):
                self.ask(what, self.console, "DELETE", f"{self.v2(env)}/portal-navigation-items/{item_id}", passed=(404,))
        elif kind == "portal-api-page":
            for entry in x.get("legacy", []):
                self.ask(what, self.console, "DELETE", f"{self.v2(env)}/apis/{entry['api']}/pages/{entry['page']}", passed=(404,))
        else:
            path = {"portal-link": f"/portals/{x.get('portal')}/links", "portal-page": f"/portals/{x.get('portal')}/documentations",
                    "portal": "/portals", "portal-theme": "/portal-themes"}[kind]
            self.ask(what, self.automation, "DELETE", f"{self.auto(env)}{path}/{x['hrid']}", passed=(404,))
        return True


def seed():
    targets = folders()
    if not targets:
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    portals = Portals()
    portals.connect()
    parallel(portals.apply, portals.read(targets))   # the portal of an environment depends on no other


def remove(records):
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    portals = Portals()
    portals.connect()
    for x in mine:
        gone = "" if portals.delete(x) else ": its environment is gone"   # an environment that is gone has nothing left in it
        log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}'{gone}")
