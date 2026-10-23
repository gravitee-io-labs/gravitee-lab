"""The Developer Portal of each environment: the theme, the portal, the pages, the links and the listings of a seed, made
once, found again by their hrid, never taken from someone else, and removed in the order of what depends on what. The
platform here keeps what the Automation API and the Console's navigation routes kept on a live 4.13 (a theme that only a
portal makes the one shown, a portal that deletes the folders it no longer names, an item that is published only under
published folders)."""

import base64
import copy
import json
import os
import re
import shutil
import unittest
import uuid
from unittest import mock

import requests

from lab_seeder import apim_portal
from lab_seeder import common
from lab_seeder.common import SeedError
from lab_seeder.testkit import Seed

ENVS = "/management/organizations/DEFAULT/environments"
AUTO = "/automation/organizations/DEFAULT/environments"
V2 = "/management/v2/organizations/DEFAULT/environments"
MODERNA = "environments/moderna"
DOCS = f"{AUTO}/moderna/portals/moderna-portal/documentations"
LINKS = f"{AUTO}/moderna/portals/moderna-portal/links"
ITEMS = f"{V2}/moderna/portal-navigation-items"
LOGO = "<svg xmlns='http://www.w3.org/2000/svg'/>"

THEME = """hrid: moderna-theme
name: Moderna
definition:
  color: {primary: "#B3003C", secondary: "#2D2D2D", pageBackground: "#FAF7F5", cardBackground: "#FFFFFF"}
  font: {fontFamily: "Georgia, serif"}
assets:
  logo: ../assets/logo.svg
  favicon: ../assets/favicon.svg
"""
PORTAL = """hrid: moderna-portal
name: ${ENV_NAME} Developer Portal
activeThemeHrid: moderna-theme
structure:
  topNavbar:
    - {path: Guides, visibility: PUBLIC}
    - {path: Event feeds, visibility: PUBLIC}
    - {path: Event feeds/Process development, displayName: Process development, visibility: PRIVATE}
"""
HOME = "hrid: moderna-home\nname: Home\ntype: GRAVITEE_MARKDOWN\narea: HOMEPAGE\nfile: ../content/home.md\nvisibility: PUBLIC\n"
GUIDE = "hrid: moderna-guide\nname: Getting started\ntype: GRAVITEE_MARKDOWN\nlocation: Guides\norder: 0\nfile: ../content/guide.md\n"
LINK = "hrid: moderna-broker\nname: Broker manager\nhref: ${MANAGER_URL}\nlocation: Guides\norder: 1\n"
LISTING = """hrid: moderna-feeds
apis:
  - {apiHrid: moderna-batch-status, location: Event feeds/Process development, order: 0, visibility: PRIVATE}
  - {apiHrid: moderna-telemetry, location: Event feeds/Process development, order: 1}
"""
FILES = {
    f"{MODERNA}/environment.yaml": "id: moderna\nname: Moderna\n",
    f"{MODERNA}/apis/batch-status.json": json.dumps({"api": {"name": "Batch status", "crossId": "moderna-batch-status"}}),
    f"{MODERNA}/apis/telemetry.json": json.dumps({"api": {"name": "Telemetry", "crossId": "moderna-telemetry"}}),
    f"{MODERNA}/assets/logo.svg": LOGO,
    f"{MODERNA}/assets/favicon.svg": "<svg xmlns='http://www.w3.org/2000/svg' width='1'/>",
    f"{MODERNA}/portal/theme.yaml": THEME,
    f"{MODERNA}/portal/portal.yaml": PORTAL,
    f"{MODERNA}/portal/pages/home.yaml": HOME,
    f"{MODERNA}/portal/pages/guide.yaml": GUIDE,
    f"{MODERNA}/portal/content/home.md": "# ${ENV_NAME} (${ENV_ID})\n\nGateway ${GATEWAY}. Expression: $${api.name} and $${NAME}.\n",
    f"{MODERNA}/portal/content/guide.md": "# Getting started\n",
    f"{MODERNA}/portal/links/broker.yaml": LINK,
    f"{MODERNA}/portal/listings/feeds.yaml": LISTING,
}
DEFAULT_OVERVIEW = "# ${api.name}\n\nDiscover ${api.name}.\n"   # the template the platform gives an API item's default page
SAMPLE_ASYNCAPI = "asyncapi: 3.0.0\ninfo:\n  title: Default AsyncAPI Page\n  version: 1.0.0\nchannels: {}\n"   # what the platform gives a new AsyncAPI page
APIS = [{"id": "api-1", "name": "Batch status", "crossId": "moderna-batch-status"},
        {"id": "api-2", "name": "Telemetry", "crossId": "moderna-telemetry"}]
SETTINGS = {"MANAGER_URL": "http://localhost:8095", "GATEWAY": "http://localhost:8082", "NAME": "kept"}


def ident(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "/".join(parts)))


def item_id(api, env="moderna", portal="moderna-portal", listing="moderna-feeds"):
    """The id the seed computes for the item of an API of a listing."""
    return str(uuid.uuid5(apim_portal.IDS, f"{env}/{portal}/{listing}/{api}"))


FEEDS = [item_id("moderna-batch-status"), item_id("moderna-telemetry")]
OVERVIEWS = [ident("moderna", "overview", feed) for feed in FEEDS]   # the pages the platform makes under them
FOLDER = ident("moderna", "folder", "/event-feeds/process-development")


class World:
    """Environments with a theme, a portal, pages, links and a navigation tree, as the platform keeps them."""

    def __init__(self, environments=None, apis=None):
        self.environments = environments or {"moderna": "Moderna"}
        self.apis = {env: list((apis or {}).get(env, APIS)) for env in self.environments}
        self.store = {}                                   # (env, kind, portal, hrid) -> what the platform answers
        self.items = {env: {} for env in self.environments}
        self.contents = {env: {} for env in self.environments}   # env -> {id: the text of a page, as the platform keeps it}
        self.own = {env: {} for env in self.environments}   # env -> {api id: {page id: a page of the API itself}}
        self.active = {}                                  # env -> hrid of the theme the portal shows
        self.made = 0                                     # pages made, which gives each its id
        self.publishing = True                            # False: the platform cannot publish the page of an API

    def routes(self):
        nav = V2 + "/[^/]+/portal-navigation-items"
        contents = V2 + "/[^/]+/portal-page-contents/[^/]+"
        pages = V2 + "/[^/]+/apis/[^/]+/pages"
        return {
            ("GET", ENVS): (200, []),
            ("GET", re.compile(ENVS + "/[^/]+")): self.environment,
            ("GET", re.compile(AUTO + "/.*")): self.known(self.automation), ("PUT", re.compile(AUTO + "/.*")): self.known(self.automation),
            ("DELETE", re.compile(AUTO + "/.*")): self.known(self.automation),
            ("GET", re.compile(V2 + "/[^/]+/apis")):
                self.known(lambda call: (200, {"data": self.apis[self.env(call)], "pagination": {"pageCount": 1}})),
            ("GET", re.compile(nav)): self.known(lambda call: (200, {"items": copy.deepcopy(list(self.items[self.env(call)].values()))})),
            ("POST", re.compile(nav)): self.known(self.create_item),
            ("POST", re.compile(nav + "/_default-pages")): self.known(self.default_pages),
            ("PUT", re.compile(nav + "/[^/]+")): self.known(self.put_item),
            ("DELETE", re.compile(nav + "/[^/]+")): self.known(self.delete_item),
            ("GET", re.compile(contents)): self.known(self.get_content), ("PUT", re.compile(contents)): self.known(self.put_content),
            ("GET", re.compile(pages)): self.known(self.list_own), ("POST", re.compile(pages)): self.known(self.create_own),
            ("PUT", re.compile(pages + "/[^/]+")): self.known(self.put_own), ("DELETE", re.compile(pages + "/[^/]+")): self.known(self.delete_own),
            ("POST", re.compile(pages + "/[^/]+/_publish")): self.known(self.publish_own),
        }

    @staticmethod
    def env(call):
        return re.search(r"/environments/([^/]+)", call.path).group(1)

    def known(self, route):
        """A route of an environment, which answers what the platform does for one it does not have: 400, on every route."""
        def answer(call):
            if self.env(call) not in self.environments:
                return 400, {"httpStatus": 400, "message": "Invalid organization or environment"}
            return route(call)
        return answer

    def environment(self, call):
        env = call.path.rsplit("/", 1)[1]
        if env not in self.environments:
            return 400, {"message": "Invalid organization or environment"}
        return 200, {"id": env, "name": self.environments[env]}

    # --- The Automation API ---

    def automation(self, call):
        env, *rest = call.path[len(AUTO) + 1:].split("/")
        kind, portal = {"portal-themes": "theme", "portals": "portal"}[rest[0]], None
        if kind == "portal" and len(rest) > 2:
            kind, portal, rest = {"documentations": "page", "links": "link"}[rest[2]], rest[1], rest[2:]
        if call.method == "PUT":
            return self.put(env, kind, portal, call.json)
        key = (env, kind, portal, rest[1])
        if key not in self.store:
            return 404, {"httpStatus": 404, "message": f"HRID [{rest[1]}] cannot be found.", "technicalCode": "hrid.notFound"}
        if call.method == "GET":
            return 200, self.store[key]
        self.drop(key)
        return 204

    def put(self, env, kind, portal, body):
        if kind == "portal" and (env, kind, None, body["hrid"]) not in self.store:
            for entry in body["structure"]["topNavbar"]:
                top = "/" + entry["path"].strip("/").split("/")[0]
                if any(not i.get("parentId") and i["id"] != ident(env, "folder", top) and apim_portal.normal(i["title"]) == top
                       for i in self.items[env].values()):
                    return 400, {"httpStatus": 400, "message": f"Navigation path [{top}] is already occupied by content not managed by the Automation API"}
        state = {**body, "id": ident(env, kind, body["hrid"]), "environmentId": env, "organizationId": "DEFAULT"}
        if portal:
            state["portalHrid"] = portal
        self.store[(env, kind, portal, body["hrid"])] = state
        if kind == "portal":
            self.active[env] = body.get("activeThemeHrid")
            self.shape(env, body["structure"]["topNavbar"])
        elif kind in ("page", "link"):
            self.place(env, state, kind)
        return 200, state

    def shape(self, env, paths):
        """The folders of a portal's structure, and only those: the others go, with what is in them."""
        keep = set()
        for entry in paths:
            names = entry["path"].strip("/").split("/")
            for i in range(1, len(names) + 1):
                where = "/" + "/".join(names[:i])
                folder = ident(env, "folder", where)
                keep.add(folder)
                if folder not in self.items[env]:
                    last = entry if i == len(names) else {}
                    self.items[env][folder] = {
                        "id": folder, "type": "FOLDER", "title": last.get("displayName", names[i - 1]), "order": len(self.items[env]),
                        "published": True, "visibility": last.get("visibility", "PUBLIC"),
                        **({"parentId": ident(env, "folder", "/" + "/".join(names[:i - 1]))} if i > 1 else {})}
        for gone in [i for i, item in self.items[env].items() if item["type"] == "FOLDER" and i not in keep]:
            self.remove_item(env, gone)

    def place(self, env, state, kind):
        item = {"id": ident(env, kind, state["hrid"]), "type": "PAGE" if kind == "page" else "LINK", "title": state["name"],
                "published": True, "visibility": state.get("visibility", "PUBLIC"), "order": state.get("order", 0)}
        folder = ident(env, "folder", state.get("location", ""))
        if folder in self.items[env]:
            item["parentId"] = folder
        self.items[env][item["id"]] = item

    def drop(self, key):
        env, kind, portal, hrid = key
        del self.store[key]
        if kind in ("page", "link"):
            self.items[env].pop(ident(env, kind, hrid), None)
        elif kind == "portal":
            for other in [k for k in self.store if k[0] == env and k[2] == hrid]:
                self.drop(other)
            self.items[env].clear()
            self.active.pop(env, None)

    # --- The Console's navigation items ---

    def below(self, env, parent):
        return [i for i, item in self.items[env].items() if item.get("parentId") == parent]

    def descendants(self, env, parent):
        return [d for child in self.below(env, parent) for d in [child, *self.descendants(env, child)]]

    def remove_item(self, env, item):
        for child in self.below(env, item):
            self.remove_item(env, child)
        self.items[env].pop(item, None)

    def create_item(self, call):
        env, body = self.env(call), call.json
        if body.get("parentId") not in self.items[env]:
            return 400, {"message": "Invalid parent"}
        if body["type"] == "PAGE":
            return self.create_page(env, body)
        api = next(a for a in self.apis[env] if a["id"] == body["apiId"])
        item = {"id": body["id"], "type": "API", "apiId": body["apiId"], "title": body["title"] or api["name"], "order": body["order"],
                "parentId": body["parentId"], "published": False, "visibility": body["visibility"], "categoryIds": []}
        self.items[env][item["id"]] = item
        return 201, copy.deepcopy(item)

    def create_page(self, env, body):
        """A page under an item, unpublished, with the sample text of its type, after the pages it has (the platform's order)."""
        self.made += 1
        page, content = ident(env, "page", str(self.made)), ident(env, "page-content", str(self.made))
        sample = SAMPLE_ASYNCAPI if body["contentType"] == "ASYNCAPI" else DEFAULT_OVERVIEW
        self.contents[env][content] = {"id": content, "type": body["contentType"], "content": sample}
        order = body.get("order", len(self.below(env, body["parentId"])))
        item = {"id": page, "type": "PAGE", "title": body["title"], "order": order, "published": False, "visibility": body["visibility"],
                "parentId": body["parentId"], "portalPageContentId": content}
        self.items[env][page] = item
        return 201, copy.deepcopy(item)

    def put_item(self, call):
        env, item_id_ = self.env(call), call.path.rsplit("/", 1)[1]
        item, body = self.items[env].get(item_id_), call.json
        if not item:
            return 404, {"message": "Portal navigation item not found"}
        if body["published"] and body.get("parentId") and not self.items[env][body["parentId"]]["published"]:
            return 400, {"message": "A child is published only when its parent is"}
        item.update({key: body[key] for key in ("title", "order", "published", "visibility")})
        item.pop("parentId", None)   # sent without its parent, an item goes to the root
        item.update({key: body[key] for key in ("parentId",) if key in body})
        if not body["published"] or call.params.get("propagatePublishToChildren") == "true":
            for child in self.descendants(env, item_id_):
                self.items[env][child]["published"] = body["published"]
        return 200, copy.deepcopy(item)

    def default_pages(self, call):
        """What the platform does when it is asked: an unpublished Overview under each API item that has no page, with its text."""
        env = self.env(call)
        for api_item in call.json["ids"]:
            parent = self.items[env].get(api_item)
            if not parent or parent["type"] != "API":
                return 400, {"message": "Not an API item"}
            if not any(i.get("parentId") == api_item for i in self.items[env].values()):
                page = ident(env, "overview", api_item)
                content = ident(env, "overview-content", api_item)
                self.contents[env][content] = {"id": content, "type": "GRAVITEE_MARKDOWN", "content": DEFAULT_OVERVIEW}
                self.items[env][page] = {"id": page, "type": "PAGE", "title": "Overview", "order": 0, "published": False,
                                         "visibility": parent["visibility"], "parentId": api_item, "portalPageContentId": content}
        return 204

    def get_content(self, call):
        found = self.contents[self.env(call)].get(call.path.rsplit("/", 1)[1])
        return (200, found) if found else (404, {"message": "Portal page content not found"})

    def put_content(self, call):
        """The platform renders a text before it keeps it: an expression it cannot evaluate is a 400."""
        env, content = self.env(call), call.path.rsplit("/", 1)[1]
        if content not in self.contents[env]:
            return 404, {"message": "Portal page content not found"}
        text = call.json["content"]
        if call.json["type"] == "ASYNCAPI":   # the platform renders no template in an AsyncAPI text, and checks nothing in it
            self.contents[env][content] = {"id": content, "type": "ASYNCAPI", "content": text}
            return 200, self.contents[env][content]
        if text.count("${") != text.count("}"):
            return 400, {"httpStatus": 400, "message": 'Invalid template: Syntax error in template "" in line 1:\nUnexpected end of file'}
        for expression in re.findall(r"\$\{([^}]*)\}", text):
            if expression.split("!")[0] not in ("api.name", "api.version", "api.description"):
                return 400, {"httpStatus": 400, "message": f"Invalid expression or value is missing for {expression}"}
        self.contents[env][content] = {"id": content, "type": call.json["type"], "content": text}
        return 200, self.contents[env][content]

    # --- The pages of an API itself (the Console's Documentation of the API) ---

    def pages_of(self, call):
        env, api = self.env(call), re.search(r"/apis/([^/]+)/pages", call.path).group(1)
        return (self.own[env].setdefault(api, {}) if any(a["id"] == api for a in self.apis[env]) else None)

    def list_own(self, call):
        """Every page of the API, and the system folder the platform makes for each API."""
        pages = self.pages_of(call)
        if pages is None:
            return 404, {"message": "API not found"}
        aside = {"id": "aside", "name": "Aside", "type": "SYSTEM_FOLDER", "order": 0, "published": True, "visibility": "PUBLIC", "homepage": False}
        return 200, {"pages": copy.deepcopy([aside, *pages.values()]), "breadcrumb": []}

    @staticmethod
    def refused(body):
        """What the platform checks in a Markdown page of an API before it keeps it: no tag of Gravitee Markdown, and a template
        that the name, the version and the description of the API alone can fill."""
        if body["type"] != "MARKDOWN":
            return None
        content = body.get("content", "")
        tags = sorted(set(re.findall(r"<(gmd-[a-z-]+)", content)))
        if tags:
            return 400, {"httpStatus": 400, "technicalCode": "pageContent.unsafe",
                         "message": "The page content does not follow security policy: [" + ", ".join(f"Tag not allowed: {t}" for t in tags) + "]"}
        for expression in re.findall(r"\$\{([^}]*)\}", content):
            if expression.split("!")[0] not in ("api.name", "api.version", "api.description"):
                return 400, {"httpStatus": 400, "message": f"Invalid expression {expression}"}
        return None

    def create_own(self, call):
        pages, body = self.pages_of(call), call.json
        if pages is None:
            return 404, {"message": "API not found"}
        if body["type"] not in ("MARKDOWN", "ASYNCAPI"):
            return 400, {"message": "Unsupported type"}
        if self.refused(body):
            return self.refused(body)
        self.made += 1
        page = {"id": ident("page", str(self.made)), "name": body["name"], "type": body["type"], "content": body.get("content", ""),
                "order": 1 + max([p["order"] for p in pages.values()], default=0), "published": False, "visibility": body["visibility"],
                "homepage": body.get("homepage", False)}
        pages[page["id"]] = page
        return 201, copy.deepcopy(page)

    def put_own(self, call):
        """A page put again: what the body leaves out goes back to its default (the order to 0, the homepage to false), and the page stays published."""
        pages, body = self.pages_of(call), call.json
        page = (pages or {}).get(call.path.rsplit("/", 1)[1])
        if not page:
            return 404, {"message": "Page not found"}
        if self.refused(body):
            return self.refused(body)
        page.update({"type": body["type"], "name": body["name"], "visibility": body["visibility"], "content": body.get("content", ""),
                     "order": body.get("order", 0), "homepage": body.get("homepage", False)})
        return 200, copy.deepcopy(page)

    def publish_own(self, call):
        page = (self.pages_of(call) or {}).get(call.path.rsplit("/", 2)[1])
        if not page:
            return 404, {"message": "Page not found"}
        if not self.publishing:
            return 500, {"message": "Cannot publish"}
        page["published"] = True
        return 200, copy.deepcopy(page)

    def delete_own(self, call):
        pages = self.pages_of(call)
        if pages is None or call.path.rsplit("/", 1)[1] not in pages:
            return 404, {"message": "Page not found"}
        del pages[call.path.rsplit("/", 1)[1]]
        return 204

    def delete_item(self, call):
        env, item_id_ = self.env(call), call.path.rsplit("/", 1)[1]
        if item_id_ not in self.items[env]:
            return 404, {"message": "Portal navigation item not found"}
        self.remove_item(env, item_id_)
        return 204


class Portals(Seed):
    """Every test runs against one World; the seed is the portal files of Moderna's environment unless a test says otherwise."""

    def setUp(self):
        super().setUp()
        self.env(**SETTINGS)
        self.world = World()
        self.platform.add(self.world.routes())
        self.seed_files(FILES)

    def file(self, name, text):
        self.seed_files({f"{MODERNA}/{name}": text})

    def only(self, files):
        """A seed folder with these files and no other."""
        shutil.rmtree(common.SEED_DIR)
        self.seed_files(files)

    def worlds(self, environments):
        self.world.__init__(environments)
        self.platform.add(self.world.routes())

    def sent(self, method, ending):
        """The bodies sent with a method to a path that ends so."""
        return [c.json for c in self.platform.calls if c.method == method and c.path.endswith(ending)]

    def run_seed(self):
        """A run of the seed, and what it changed on the platform."""
        self.platform.calls.clear()
        apim_portal.seed()
        return self.platform.changes()

    def seeded(self):
        self.run_seed()
        self.platform.calls.clear()

    def items(self, kind=None):
        return [i for i in self.world.items["moderna"].values() if kind in (None, i["type"])]


class Creation(Portals):

    def test_a_first_run_makes_the_theme_then_the_portal_the_pages_the_link_and_the_listing(self):
        self.assertEqual(self.run_seed(), [
            ("PUT", f"{AUTO}/moderna/portal-themes"), ("PUT", f"{AUTO}/moderna/portals"), ("PUT", DOCS), ("PUT", DOCS),
            ("PUT", LINKS),
            ("POST", ITEMS), ("PUT", f"{ITEMS}/{FEEDS[0]}"), ("POST", ITEMS), ("PUT", f"{ITEMS}/{FEEDS[1]}"),
            ("POST", f"{ITEMS}/_default-pages"), ("PUT", f"{ITEMS}/{OVERVIEWS[0]}"), ("PUT", f"{ITEMS}/{OVERVIEWS[1]}")])
        self.assertEqual([x["kind"] for x in common.created()],
                         ["portal-theme", "portal", "portal-page", "portal-page", "portal-link", "portal-listing", "portal-api-page"])

    def test_what_it_made_is_recorded_with_its_environment_and_its_hrid(self):
        self.run_seed()
        theme = next(x for x in common.created() if x["kind"] == "portal-theme")
        self.assertEqual({key: theme[key] for key in ("product", "label", "name", "env", "hrid")},
                         {"product": "apim-portal", "label": "portal themes", "name": "moderna: moderna-theme",
                          "env": "moderna", "hrid": "moderna-theme"})
        self.assertEqual(theme["id"], ident("moderna", "theme", "moderna-theme"))
        page = next(x for x in common.created() if x["name"] == "moderna: moderna-guide")
        self.assertEqual((page["portal"], page["label"]), ("moderna-portal", "portal pages"))
        self.assertEqual([x["label"] for x in common.created()][3:],
                         ["portal pages", "portal links", "portal listings", "API pages"])

    def test_the_theme_holds_its_images_as_data_uris_and_the_colors_the_automation_api_reads(self):
        self.run_seed()
        [theme] = self.sent("PUT", "/portal-themes")
        self.assertEqual(theme["logo"], "data:image/svg+xml;base64," + base64.b64encode(LOGO.encode()).decode())
        self.assertTrue(theme["favicon"].startswith("data:image/svg+xml;base64,"))
        self.assertEqual(theme["definition"]["color"], {"primary": "#B3003C", "secondary": "#2D2D2D",
                                                        "background": {"page": "#FAF7F5", "card": "#FFFFFF"}})
        self.assertEqual(theme["definition"]["font"], {"fontFamily": "Georgia, serif"})
        self.assertNotIn("assets", theme)

    def test_an_image_has_the_type_of_its_file(self):
        for suffix, kind in {".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon", ".webp": "image/webp"}.items():
            with self.subTest(suffix=suffix):
                self.seed_files({f"{MODERNA}/assets/logo{suffix}": "x"})
                self.file("portal/theme.yaml", THEME.replace("logo.svg", f"logo{suffix}"))
                self.run_seed()
                self.assertTrue(self.sent("PUT", "/portal-themes")[-1]["logo"].startswith(f"data:{kind};base64,"))

    def test_the_colors_a_theme_gives_the_automation_way_are_kept(self):
        self.file("portal/theme.yaml", "hrid: moderna-theme\nname: M\ndefinition:\n"
                                       "  color: {primary: '#111111', background: {page: '#222222', card: '#333333'}}\n")
        self.run_seed()
        self.assertEqual(self.sent("PUT", "/portal-themes")[0]["definition"]["color"],
                         {"primary": "#111111", "background": {"page": "#222222", "card": "#333333"}})

    def test_the_portal_shows_the_theme_and_its_paths_are_kept_as_the_platform_keeps_them(self):
        self.run_seed()
        [portal] = self.sent("PUT", "/portals")
        self.assertEqual(portal["activeThemeHrid"], "moderna-theme")
        self.assertEqual(portal["name"], "Moderna Developer Portal")
        self.assertEqual(portal["structure"]["topNavbar"], [
            {"path": "/guides", "displayName": "Guides", "visibility": "PUBLIC"},
            {"path": "/event-feeds", "displayName": "Event feeds", "visibility": "PUBLIC"},
            {"path": "/event-feeds/process-development", "displayName": "Process development", "visibility": "PRIVATE"}])
        self.assertEqual(self.world.active, {"moderna": "moderna-theme"})

    def test_a_folder_with_no_display_name_shows_the_last_words_of_its_path(self):
        self.file("portal/portal.yaml", PORTAL + "    - {path: Support/Help desk}\n")
        self.run_seed()
        self.assertEqual(self.sent("PUT", "/portals")[0]["structure"]["topNavbar"][-1],
                         {"path": "/support/help-desk", "displayName": "Help desk"})

    def test_a_portal_without_the_theme_key_shows_the_theme_of_its_folder(self):
        self.file("portal/portal.yaml", PORTAL.replace("activeThemeHrid: moderna-theme\n", ""))
        self.run_seed()
        self.assertEqual(self.sent("PUT", "/portals")[0]["activeThemeHrid"], "moderna-theme")

    def test_a_portal_with_no_theme_shows_the_platforms_own(self):
        os.remove(common.SEED_DIR / MODERNA / "portal" / "theme.yaml")
        self.file("portal/portal.yaml", PORTAL.replace("activeThemeHrid: moderna-theme\n", ""))
        self.run_seed()
        self.assertNotIn("activeThemeHrid", self.sent("PUT", "/portals")[0])
        self.assertEqual(self.sent("PUT", "/portal-themes"), [])

    def test_a_page_is_rendered_with_the_variables_of_the_seed_and_of_its_environment(self):
        self.run_seed()
        home = next(page for page in self.sent("PUT", "/documentations") if page["hrid"] == "moderna-home")
        self.assertEqual(home["content"],
                         "# Moderna (moderna)\n\nGateway http://localhost:8082. Expression: ${api.name} and ${NAME}.\n")
        self.assertEqual((home["area"], home["type"], home["visibility"]), ("HOMEPAGE", "GRAVITEE_MARKDOWN", "PUBLIC"))
        self.assertNotIn("location", home)

    def test_the_variables_of_the_engine_stay_out_of_the_environment_of_the_process(self):
        self.run_seed()
        self.assertNotIn("ENV_ID", os.environ)
        self.assertNotIn("ENV_NAME", os.environ)

    def test_a_page_goes_to_a_path_of_the_portal_and_a_link_too(self):
        self.run_seed()
        guide = next(page for page in self.sent("PUT", "/documentations") if page["hrid"] == "moderna-guide")
        self.assertEqual((guide["location"], guide["order"], guide["area"]), ("/guides", 0, "TOP_NAVBAR"))
        self.assertEqual(self.sent("PUT", "/links"), [{"hrid": "moderna-broker", "name": "Broker manager",
                                                       "href": "http://localhost:8095", "location": "/guides", "order": 1}])

    def test_a_page_may_hold_its_text_itself(self):
        self.file("portal/pages/guide.yaml", "hrid: moderna-guide\nname: Guide\ntype: ASYNCAPI\ncontent: 'asyncapi: 3.0.0'\n")
        self.run_seed()
        guide = next(page for page in self.sent("PUT", "/documentations") if page["hrid"] == "moderna-guide")
        self.assertEqual((guide["type"], guide["content"]), ("ASYNCAPI", "asyncapi: 3.0.0"))

    def test_the_log_names_what_it_did_and_nothing_secret(self):
        self.env(APIM_PASSWORD="s3cret-pass")
        self.run_seed()
        self.assertIn("✓ Portal theme created: moderna: moderna-theme", self.output())
        self.assertIn("✓ Portal listing created: moderna: moderna-feeds", self.output())
        self.assertNotIn("s3cret-pass", self.output())


class SecondRun(Portals):

    def test_a_second_run_changes_nothing(self):
        self.seeded()
        records = json.dumps(common.created())
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(json.dumps(common.created()), records)
        self.assertIn("Portal exists: moderna: moderna-portal", self.output())
        self.assertIn("Portal listing exists: moderna: moderna-feeds", self.output())

    def test_a_page_that_changed_is_the_only_one_put_again(self):
        self.seeded()
        self.file("portal/content/guide.md", "# Getting started\n\nMore.\n")
        self.assertEqual(self.run_seed(), [("PUT", DOCS)])
        self.assertEqual(self.sent("PUT", "/documentations")[0]["hrid"], "moderna-guide")
        self.assertIn("✓ Portal page updated: moderna: moderna-guide", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_a_theme_whose_colors_changed_is_put_again_and_the_rest_is_left(self):
        self.seeded()
        self.file("portal/theme.yaml", THEME.replace("#B3003C", "#C00040"))
        self.assertEqual(self.run_seed(), [("PUT", f"{AUTO}/moderna/portal-themes")])

    def test_an_object_that_is_gone_is_made_again(self):
        self.seeded()
        del self.world.store[("moderna", "link", "moderna-portal", "moderna-broker")]
        self.assertEqual(self.run_seed(), [("PUT", LINKS)])

    def test_what_a_removed_file_made_is_deleted_and_forgotten(self):
        self.seeded()
        (common.SEED_DIR / MODERNA / "portal/links/broker.yaml").unlink()
        self.assertEqual(self.run_seed(), [("DELETE", f"{LINKS}/moderna-broker")])
        self.assertIn("✓ Deleted portal-link 'moderna: moderna-broker': its file is gone", self.output())
        self.assertEqual([x for x in common.created() if x["kind"] == "portal-link"], [])
        self.assertNotIn(("moderna", "link", "moderna-portal", "moderna-broker"), self.world.store)
        self.assertEqual(self.run_seed(), [])

    def test_a_page_whose_file_is_gone_is_deleted_and_the_others_are_kept(self):
        self.seeded()
        (common.SEED_DIR / MODERNA / "portal/pages/guide.yaml").unlink()
        self.assertEqual(self.run_seed(), [("DELETE", f"{DOCS}/moderna-guide")])
        self.assertEqual([x["hrid"] for x in common.created() if x["kind"] == "portal-page"], ["moderna-home"])
        self.assertTrue([x for x in common.created() if x["kind"] == "portal-theme"])
        self.assertEqual(self.run_seed(), [])

    def test_a_portal_that_lost_its_folders_gets_them_back_and_the_listing_its_items(self):
        self.seeded()
        self.world.put("moderna", "portal", None, {"hrid": "moderna-portal", "name": "x", "structure": {"topNavbar": [
            {"path": "/guides", "displayName": "Guides"}]}})   # someone applied another portal: the folders and their items go
        self.assertEqual(self.items("API"), [])
        self.run_seed()
        self.assertEqual(sorted(i["title"] for i in self.items("API")), ["Batch status", "Telemetry"])
        self.assertTrue(all(i["published"] for i in self.items()))
        self.platform.calls.clear()
        self.assertEqual(self.run_seed(), [])

    def test_a_record_that_was_lost_is_made_again_and_the_objects_are_not_taken(self):
        self.seeded()
        records = common.created()
        common.save_created([x for x in records if x["kind"] == "portal-listing"])
        with self.assertRaisesRegex(SeedError, "the hrid is taken by one the seed did not create"):
            self.run_seed()
        common.save_created(records[:-2])
        self.assertEqual(self.run_seed(), [])
        self.assertEqual([x["kind"] for x in common.created()][-2:], ["portal-listing", "portal-api-page"])


class Listings(Portals):

    def test_each_api_is_found_by_its_cross_id_and_becomes_an_item_of_its_location_published(self):
        self.run_seed()
        self.assertEqual(sorted((i["apiId"], i["parentId"], i["published"], i["visibility"], i["order"]) for i in self.items("API")),
                         [("api-1", FOLDER, True, "PRIVATE", 0), ("api-2", FOLDER, True, "PRIVATE", 1)])

    def test_an_item_has_an_id_the_seed_computes_so_that_a_run_finds_it_again(self):
        self.run_seed()
        listing = next(x for x in common.created() if x["kind"] == "portal-listing")
        self.assertEqual(listing["items"], FEEDS)
        self.assertEqual((listing["portal"], listing["env"], listing["hrid"]), ("moderna-portal", "moderna", "moderna-feeds"))
        self.assertEqual(set(listing["items"]) - set(self.world.items["moderna"]), set())

    def test_an_item_with_no_visibility_of_its_own_is_that_of_its_folder(self):
        self.run_seed()
        self.assertEqual(self.world.items["moderna"][FEEDS[1]]["visibility"], "PRIVATE")

    def test_an_item_is_created_then_published(self):
        self.run_seed()
        calls = [(c.method, c.path.rsplit("/", 1)[1]) for c in self.platform.calls if "portal-navigation-items" in c.path and c.method != "GET"]
        self.assertEqual(calls[:2], [("POST", "portal-navigation-items"), ("PUT", FEEDS[0])])
        [created] = [c.json for c in self.platform.calls if c.method == "POST"][:1]
        self.assertEqual((created["id"], created["type"], created["apiId"], created["parentId"], created["area"]),
                         (FEEDS[0], "API", "api-1", FOLDER, "TOP_NAVBAR"))
        published = self.sent("PUT", FEEDS[0])[0]
        self.assertEqual((published["published"], published["parentId"], published["apiId"], published["title"]),
                         (True, FOLDER, "api-1", "Batch status"))

    def test_a_folder_that_was_unpublished_is_published_with_what_is_in_it_before_the_item(self):
        self.seeded()
        events = ident("moderna", "folder", "/event-feeds")
        for item in self.items():
            if item["type"] in ("FOLDER", "API") and item["id"] != ident("moderna", "folder", "/guides"):
                item["published"] = False
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{events}")])
        self.assertEqual([c.params for c in self.platform.calls if c.method == "PUT"], [{"propagatePublishToChildren": "true"}])
        self.assertTrue(all(i["published"] for i in self.items()))

    def test_an_unpublished_item_under_published_folders_is_published_alone(self):
        self.seeded()
        self.world.items["moderna"][FEEDS[1]]["published"] = False
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{FEEDS[1]}")])
        self.assertTrue(self.world.items["moderna"][FEEDS[1]]["published"])

    def test_an_item_that_is_not_where_the_file_says_is_put_right(self):
        self.seeded()
        self.world.items["moderna"][FEEDS[0]]["order"] = 5
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{FEEDS[0]}")])
        self.assertEqual(self.world.items["moderna"][FEEDS[0]]["order"], 0)

    def test_the_item_of_a_renamed_api_takes_its_new_name(self):
        self.seeded()
        self.world.items["moderna"][FEEDS[0]]["title"] = "Old name"   # the title the platform gave it when the API had its old name
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{FEEDS[0]}")])
        api = next(a for a in self.world.apis["moderna"] if a["id"] == self.world.items["moderna"][FEEDS[0]]["apiId"])
        self.assertEqual(self.world.items["moderna"][FEEDS[0]]["title"], api["name"])

    def test_the_item_of_an_api_made_again_is_made_again_for_it(self):
        self.seeded()
        self.world.items["moderna"][FEEDS[0]]["apiId"] = "api-gone"   # an LLM proxy is made again, with a new id, when its file changes
        changes = self.run_seed()
        self.assertIn(("DELETE", f"{ITEMS}/{FEEDS[0]}"), changes)
        self.assertEqual(self.world.items["moderna"][FEEDS[0]]["apiId"], "api-1")

    def test_an_api_that_leaves_the_listing_loses_its_item_and_its_page_leaves_the_record(self):
        self.seeded()
        self.file("portal/listings/feeds.yaml", LISTING.split("  - {apiHrid: moderna-telemetry")[0])
        self.assertEqual(self.run_seed(), [("DELETE", f"{ITEMS}/{FEEDS[1]}")])
        self.assertNotIn(FEEDS[1], self.world.items["moderna"])
        self.assertNotIn(OVERVIEWS[1], self.world.items["moderna"])
        listing = next(x for x in common.created() if x["kind"] == "portal-listing")
        self.assertEqual(listing["items"], FEEDS[:1])
        pages = next(x for x in common.created() if x["kind"] == "portal-api-page")
        self.assertEqual((pages["items"], pages["count"]), (OVERVIEWS[:1], 1))
        self.assertEqual(self.run_seed(), [])

    def test_an_api_moved_to_another_location_in_its_file_is_moved_with_its_page_and_published_again(self):
        self.seeded()
        self.file("portal/listings/feeds.yaml", LISTING.replace("{apiHrid: moderna-telemetry, location: Event feeds/Process development, order: 1}",
                                                                "{apiHrid: moderna-telemetry, location: Guides, order: 0, visibility: PRIVATE}"))
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{FEEDS[1]}"), ("PUT", f"{ITEMS}/{FEEDS[1]}"), ("PUT", f"{ITEMS}/{OVERVIEWS[1]}")])
        moved, guides = self.world.items["moderna"][FEEDS[1]], ident("moderna", "folder", "/guides")
        self.assertEqual((moved["parentId"], moved["published"], moved["visibility"]), (guides, True, "PRIVATE"))
        self.assertEqual(self.world.items["moderna"][OVERVIEWS[1]]["parentId"], FEEDS[1])
        self.assertTrue(self.world.items["moderna"][OVERVIEWS[1]]["published"])
        self.assertIn("✓ Portal listing updated: moderna: moderna-feeds", self.output())
        self.platform.calls.clear()
        self.assertEqual(self.run_seed(), [])
        self.assertIn("Portal listing exists: moderna: moderna-feeds", self.output().split("✓ Portal listing updated")[1])

    def test_an_item_is_moved_unpublished_because_a_child_is_published_only_under_a_published_folder(self):
        self.seeded()
        self.file("portal/listings/feeds.yaml", LISTING.replace("Event feeds/Process development, order: 1", "Guides, order: 0"))
        self.run_seed()
        move, publish = self.sent("PUT", FEEDS[1])
        self.assertEqual((move["parentId"], move["published"]), (ident("moderna", "folder", "/guides"), False))
        self.assertTrue(publish["published"])

    def test_the_default_order_of_an_api_is_its_position_among_the_items_of_its_location(self):
        both = "hrid: moderna-feeds\napis:\n  - {apiHrid: moderna-batch-status, location: Guides}\n  - {apiHrid: moderna-telemetry, location: %s}\n"
        self.file("portal/listings/feeds.yaml", both % "Guides")
        self.run_seed()
        self.assertEqual(sorted((i["apiId"], i["order"]) for i in self.items("API")), [("api-1", 0), ("api-2", 1)])
        self.file("portal/listings/feeds.yaml", both % "Event feeds/Process development")
        self.run_seed()
        self.assertEqual(sorted((i["apiId"], i["order"]) for i in self.items("API")), [("api-1", 0), ("api-2", 0)],
                         "the platform keeps an item that is alone in its folder at 0, so another order would never settle")
        self.assertEqual(self.run_seed(), [])

    def test_an_api_the_environment_does_not_have_stops_the_seed_before_it_puts_an_item(self):
        self.world.apis["moderna"] = APIS[:1]
        with self.assertRaisesRegex(SeedError, "Portal listing moderna-feeds in moderna: moderna-telemetry is the crossId of no API "
                                               "in moderna: the seed imports it first"):
            self.run_seed()
        self.assertEqual([c for c in self.platform.changes() if "portal-navigation-items" in c[1]], [])

    def test_an_api_already_in_the_folder_by_someone_else_is_not_taken(self):
        self.run_seed()
        item = self.world.items["moderna"].pop(FEEDS[0])
        self.world.items["moderna"]["by-hand"] = {**item, "id": "by-hand"}
        with self.assertRaisesRegex(SeedError, "Batch status is already in Process development, put there by someone else"):
            self.run_seed()

    def test_a_location_that_is_not_in_the_navigation_yet_is_said(self):
        self.seeded()
        self.world.items["moderna"] = {i: item for i, item in self.world.items["moderna"].items()
                                       if item["id"] != FOLDER and item["type"] != "API"}
        with self.assertRaisesRegex(SeedError, "/event-feeds/process-development is not in the navigation of moderna"):
            self.run_seed()

    def test_a_folder_is_found_by_the_title_the_portal_file_gives_it_and_not_by_its_path(self):
        self.file("portal/portal.yaml", PORTAL.replace("{path: Guides, visibility: PUBLIC}", "{path: Docs/Guides, displayName: Developer guides}")
                  .replace("{path: Event feeds, visibility: PUBLIC}", "{path: Event feeds, displayName: Streams, visibility: PUBLIC}")
                  .replace("location: Guides", "location: Docs/Guides"))
        self.file("portal/pages/guide.yaml", GUIDE.replace("location: Guides", "location: Docs/Guides"))
        self.file("portal/links/broker.yaml", LINK.replace("Guides", "Docs/Guides"))
        self.file("portal/listings/feeds.yaml", LISTING.replace("Event feeds/Process development", "Docs/Guides"))
        self.run_seed()
        guides = next(i for i in self.items("FOLDER") if i["title"] == "Developer guides")
        self.assertEqual(sorted(i["parentId"] for i in self.items("API")), [guides["id"], guides["id"]])
        self.assertEqual(next(i for i in self.items("FOLDER") if i["id"] == guides["parentId"])["title"], "docs")   # a folder a path goes through has the platform's name for it

    def test_two_listings_may_name_one_api_in_two_places(self):
        self.file("portal/listings/more.yaml", "hrid: moderna-more\napis:\n  - {apiHrid: moderna-telemetry, location: Guides}\n")
        self.run_seed()
        self.assertEqual(len(self.items("API")), 3)
        self.assertEqual(sorted(x["hrid"] for x in common.created() if x["kind"] == "portal-listing"), ["moderna-feeds", "moderna-more"])


class ApiPages(Portals):
    """An API item has no page of its own, so a portal shows nothing for it: the platform makes the default Overview page when it is
    asked, unpublished, and the seed publishes it, with the text of api-overview.md in it when the folder has one."""

    TEXT = 'API: $${api.name}\n\n${ENV_NAME} (${ENV_ID}) at ${GATEWAY}, version $${api.version!"?"}\n'
    DEFAULT_PAGES = re.compile(V2 + "/[^/]+/portal-navigation-items/_default-pages")

    def overview(self, text=TEXT):
        self.file("portal/api-overview.md", text)

    def page(self, i):
        return self.world.items["moderna"][OVERVIEWS[i]]

    def text(self, i):
        return self.world.contents["moderna"][self.page(i)["portalPageContentId"]]["content"]

    def test_the_platform_is_asked_once_for_the_default_pages_of_the_api_items_of_a_listing(self):
        self.run_seed()
        self.assertEqual(self.sent("POST", "/_default-pages"), [{"ids": FEEDS}])
        self.assertEqual([self.page(i)["parentId"] for i in (0, 1)], FEEDS)

    def test_the_pages_are_asked_for_after_every_item_of_the_listing_exists_and_published_after_that(self):
        self.run_seed()
        changes = [c for c in self.platform.changes() if "portal-navigation-items" in c[1]]
        self.assertEqual(changes[-3:], [("POST", f"{ITEMS}/_default-pages"), ("PUT", f"{ITEMS}/{OVERVIEWS[0]}"), ("PUT", f"{ITEMS}/{OVERVIEWS[1]}")])
        self.assertTrue(all(self.page(i)["published"] for i in (0, 1)))

    def test_a_page_is_published_with_the_visibility_of_its_item_and_stays_where_the_platform_put_it(self):
        self.run_seed()
        publish = self.sent("PUT", OVERVIEWS[0])[0]
        self.assertEqual({k: publish[k] for k in ("type", "title", "published", "visibility", "parentId")},
                         {"type": "PAGE", "title": "Overview", "published": True, "visibility": "PRIVATE", "parentId": FEEDS[0]})
        self.assertEqual([self.page(i)["visibility"] for i in (0, 1)], ["PRIVATE", "PRIVATE"])

    def test_a_folder_with_no_api_overview_leaves_the_text_the_platform_gave_the_page(self):
        self.run_seed()
        self.assertEqual([c for c in self.platform.changes() if "portal-page-contents" in c[1]], [])
        self.assertEqual(self.text(0), DEFAULT_OVERVIEW)

    def test_the_text_of_api_overview_is_written_in_the_page_of_every_listed_api_with_the_seeds_variables(self):
        self.overview()
        self.run_seed()
        expected = 'API: ${api.name}\n\nModerna (moderna) at http://localhost:8082, version ${api.version!"?"}\n'
        self.assertEqual([self.text(0), self.text(1)], [expected, expected])
        puts = [c for c in self.platform.calls if c.method == "PUT" and "portal-page-contents" in c.path]
        self.assertEqual([c.json for c in puts], [{"type": "GRAVITEE_MARKDOWN", "content": expected}] * 2)

    def test_the_text_is_written_before_the_page_is_published_so_the_default_text_is_never_shown(self):
        self.overview()
        self.run_seed()
        order = [("content" if "portal-page-contents" in c.path else "publish") for c in self.platform.calls
                 if c.method == "PUT" and ("portal-page-contents" in c.path or c.path.endswith(tuple(OVERVIEWS)))]
        self.assertEqual(order, ["content", "publish", "content", "publish"])

    def test_a_second_run_changes_nothing_and_a_page_is_not_written_when_it_holds_the_text(self):
        self.overview()
        self.seeded()
        records = json.dumps(common.created())
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(json.dumps(common.created()), records)
        self.assertIn("API pages exist: moderna: moderna-feeds", self.output())

    def test_a_text_that_changed_is_written_in_every_page_and_nothing_else_is_put(self):
        self.overview()
        self.seeded()
        self.overview(self.TEXT + "More.\n")
        self.assertEqual(self.run_seed(), [("PUT", f"{V2}/moderna/portal-page-contents/{self.page(i)['portalPageContentId']}") for i in (0, 1)])
        self.assertTrue(self.text(0).endswith("More.\n"))
        self.assertIn("✓ API pages updated: moderna: moderna-feeds (2)", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_a_text_someone_edited_in_the_console_is_written_back_to_the_files(self):
        self.overview()
        self.seeded()
        self.world.contents["moderna"][self.page(1)["portalPageContentId"]]["content"] = "By hand"
        self.assertEqual(self.run_seed(), [("PUT", f"{V2}/moderna/portal-page-contents/{self.page(1)['portalPageContentId']}")])
        self.assertEqual(self.text(1), self.text(0))

    def test_a_page_that_was_unpublished_is_published_alone_and_a_visibility_that_changed_follows_its_item(self):
        self.seeded()
        self.page(0)["published"] = False
        self.page(1)["visibility"] = "PUBLIC"
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{OVERVIEWS[0]}"), ("PUT", f"{ITEMS}/{OVERVIEWS[1]}")])
        self.assertEqual([(self.page(i)["published"], self.page(i)["visibility"]) for i in (0, 1)], [(True, "PRIVATE")] * 2)

    def test_a_page_that_was_deleted_is_asked_for_again_for_its_item_alone_written_published_and_recorded_anew(self):
        self.overview()
        self.seeded()
        content = self.page(0)["portalPageContentId"]
        del self.world.items["moderna"][OVERVIEWS[0]]
        del self.world.contents["moderna"][content]
        self.assertEqual(self.run_seed(), [("POST", f"{ITEMS}/_default-pages"), ("PUT", f"{V2}/moderna/portal-page-contents/{content}"),
                                           ("PUT", f"{ITEMS}/{OVERVIEWS[0]}")])
        self.assertEqual(self.sent("POST", "/_default-pages"), [{"ids": FEEDS[:1]}])
        self.assertEqual((self.text(0), self.page(0)["published"]), (self.text(1), True))
        self.assertIn("✓ API pages updated: moderna: moderna-feeds (2)", self.output())

    def test_a_page_of_someone_else_under_an_api_item_is_left_alone_and_the_overview_is_the_one_called_so(self):
        self.overview()
        self.seeded()
        mine = {"id": "changelog", "type": "PAGE", "title": "Changelog", "order": -1, "published": False, "visibility": "PRIVATE",
                "parentId": FEEDS[0], "portalPageContentId": "changelog-content"}
        self.world.items["moderna"]["changelog"] = dict(mine)
        self.world.contents["moderna"]["changelog-content"] = {"id": "changelog-content", "type": "GRAVITEE_MARKDOWN", "content": "# Changelog"}
        self.assertEqual(self.run_seed(), [])
        self.assertEqual((self.world.items["moderna"]["changelog"], self.world.contents["moderna"]["changelog-content"]["content"]),
                         (mine, "# Changelog"))

    def test_an_api_item_with_a_page_that_is_not_called_overview_gets_none_from_the_platform_and_the_seed_says_so(self):
        self.seeded()
        del self.world.items["moderna"][OVERVIEWS[0]]
        self.world.items["moderna"]["changelog"] = {"id": "changelog", "type": "PAGE", "title": "Changelog", "order": 0, "published": True,
                                                    "visibility": "PRIVATE", "parentId": FEEDS[0], "portalPageContentId": "x"}
        with self.assertRaisesRegex(SeedError, "^Portal listing moderna-feeds in moderna: Batch status has a page that is not called Overview, "
                                               "so the platform made no Overview page for it$"):
            self.run_seed()

    def test_a_platform_that_makes_no_page_stops_the_seed_with_the_api(self):
        self.platform.add({("POST", self.DEFAULT_PAGES): 204})
        with self.assertRaisesRegex(SeedError, "^Portal listing moderna-feeds in moderna: the platform made no Overview page for Batch status$"):
            self.run_seed()

    def test_the_listing_is_recorded_before_the_pages_so_a_page_that_fails_loses_no_item(self):
        self.overview("$${api.name")
        with self.assertRaises(SeedError):
            self.run_seed()
        self.assertEqual([x["kind"] for x in common.created()][-1], "portal-listing")
        self.assertEqual([x["items"] for x in common.created() if x["kind"] == "portal-listing"], [FEEDS])

    def test_a_text_the_platform_cannot_render_stops_the_seed_with_one_line_naming_the_file_and_the_api(self):
        self.overview("# $${api.name")
        with self.assertRaises(SeedError) as stopped:
            self.run_seed()
        message = str(stopped.exception)
        self.assertRegex(message, r"^Overview page of Batch status in moderna \(portal/api-overview\.md\): the platform cannot render it: Invalid template")
        self.assertNotIn("\n", message)
        self.overview("# $${api.name}\n")   # fixed: the next run goes on from where it stopped
        self.run_seed()
        self.assertEqual([self.text(0), self.text(1)], ["# ${api.name}\n"] * 2)
        self.assertTrue(all(self.page(i)["published"] for i in (0, 1)))
        self.assertEqual(self.run_seed(), [])

    def test_an_expression_the_platform_does_not_know_stops_the_seed_too(self):
        self.overview("# $${nobody.knows}\n")
        with self.assertRaisesRegex(SeedError, "the platform cannot render it: Invalid expression or value is missing for nobody.knows"):
            self.run_seed()

    def test_the_pages_are_recorded_for_their_listing_as_one_record_that_counts_them(self):
        self.run_seed()
        pages = next(x for x in common.created() if x["kind"] == "portal-api-page")
        self.assertEqual({k: pages[k] for k in ("product", "label", "name", "env", "hrid", "portal", "items", "count")},
                         {"product": "apim-portal", "label": "API pages", "name": "moderna: moderna-feeds", "env": "moderna",
                          "hrid": "moderna-feeds", "portal": "moderna-portal", "items": OVERVIEWS, "count": 2})
        self.assertIn("✓ API pages created: moderna: moderna-feeds (2)", self.output())

    def test_each_listing_has_its_pages_and_its_record(self):
        self.file("portal/listings/more.yaml", "hrid: moderna-more\napis:\n  - {apiHrid: moderna-telemetry, location: Guides}\n")
        self.overview()
        self.run_seed()
        self.assertEqual(sorted(x["name"] for x in common.created() if x["kind"] == "portal-api-page"),
                         ["moderna: moderna-feeds", "moderna: moderna-more"])
        overviews = [i for i in self.items("PAGE") if i["title"] == "Overview"]
        self.assertEqual(len(overviews), 3)
        self.assertEqual({self.world.contents["moderna"][i["portalPageContentId"]]["content"] for i in overviews}, {self.text(0)})
        self.assertEqual(self.run_seed(), [])


DOC = """asyncapi: 3.0.0
info:
  title: ${ENV_NAME} batch status
  version: '1.0'
channels:
  batchStatus:
    address: biofoundry/${ENV_ID}/mes/batch/{batchId}/{event}
    parameters:
      batchId: {description: The batch}
    messages:
      created: {$ref: '#/components/messages/created'}
operations:
  receiveBatchStatus:
    action: receive
    channel: {$ref: '#/channels/batchStatus'}
components:
  messages:
    created:
      payload: {type: object, properties: {note: {type: string, description: 'Write $${api.name} as it is'}}}
"""
DOC_RENDERED = DOC.replace("${ENV_NAME}", "Moderna").replace("${ENV_ID}", "moderna").replace("$${", "${")
DETAILS = "# $${api.name}\n\n$${api.description}\n\nVersion $${api.version} of an event feed of ${ENV_NAME}.\n"
DETAILS_RENDERED = "# ${api.name}\n\n${api.description}\n\nVersion ${api.version} of an event feed of Moderna.\n"
MORE = "hrid: moderna-more\napis:\n  - {apiHrid: moderna-batch-status, location: Guides}\n"


class Documents(Portals):
    """An API that has a file in api-docs/ gets an AsyncAPI page next to its Overview, which the portal's viewer shows: under its item
    in the menu, and among the pages of the API itself, which the page an API card opens reads (its Documentation tab; its Details
    tab shows the page that is the API's homepage, which api-details.md gives)."""

    def document(self, feed="batch-status", text=DOC):
        self.file(f"portal/api-docs/{feed}.yaml", text)

    def drop(self, name):
        os.remove(common.SEED_DIR / MODERNA / "portal" / name)

    def child(self, i, title):
        return next((p for p in self.items("PAGE") if p.get("parentId") == FEEDS[i] and p["title"] == title), None)

    def titles(self, i):
        """The titles of the pages under the item of an API, in the order the portal shows them."""
        return [p["title"] for p in sorted(self.items("PAGE"), key=lambda p: p["order"]) if p.get("parentId") == FEEDS[i]]

    def stored(self, page):
        return self.world.contents["moderna"][page["portalPageContentId"]]

    def own(self, i):
        """The pages of the API itself (not the system folder the platform makes), by name."""
        return {p["name"]: p for p in self.world.own["moderna"].get(f"api-{i + 1}", {}).values()}

    def address(self, i, name, *more):
        return "/".join([f"{V2}/moderna/apis/api-{i + 1}/pages", self.own(i)[name]["id"], *more])

    def record(self):
        return next(x for x in common.created() if x["kind"] == "portal-api-page")

    def destroyed(self):
        return [c for c in self.platform.changes() if c[0] == "DELETE"]

    # --- The page of the menu ---

    def test_an_api_with_a_document_gets_an_asyncapi_page_after_its_overview_published_with_the_text(self):
        self.document()
        self.run_seed()
        page = self.child(0, "AsyncAPI")
        self.assertEqual((page["published"], page["visibility"], page["order"]), (True, "PRIVATE", 1))
        self.assertEqual(self.stored(page), {"id": page["portalPageContentId"], "type": "ASYNCAPI", "content": DOC_RENDERED})
        self.assertEqual(self.titles(0), ["Overview", "AsyncAPI"])
        created = next(c.json for c in self.platform.calls if c.method == "POST" and c.json.get("type") == "PAGE")
        self.assertEqual(created, {"type": "PAGE", "title": "AsyncAPI", "area": "TOP_NAVBAR", "parentId": FEEDS[0], "visibility": "PRIVATE",
                                   "contentType": "ASYNCAPI", "order": 1})

    def test_an_api_with_no_document_gets_none_and_keeps_its_overview_alone(self):
        self.document()
        self.run_seed()
        self.assertEqual(self.titles(1), ["Overview"])

    def test_the_document_is_rendered_with_the_engine_variables_the_seeds_and_the_escape(self):
        self.document(text=DOC + "# ${GATEWAY} $${x}\n")
        self.run_seed()
        self.assertEqual(self.stored(self.child(0, "AsyncAPI"))["content"], DOC_RENDERED + "# http://localhost:8082 ${x}\n")

    def test_the_calls_go_overview_then_the_asyncapi_page_made_written_and_published_in_that_order(self):
        self.document()
        self.run_seed()
        page = self.child(0, "AsyncAPI")
        after = [(c.method, c.path.rsplit("/", 1)[1]) for c in self.platform.calls if c.method != "GET"]
        after = after[after.index(("POST", "_default-pages")):]
        self.assertEqual(after[:6], [("POST", "_default-pages"), ("PUT", OVERVIEWS[0]), ("POST", "portal-navigation-items"),
                                     ("PUT", page["portalPageContentId"]), ("PUT", page["id"]), ("PUT", OVERVIEWS[1])])

    def test_the_text_is_written_before_the_page_is_published_so_the_platforms_sample_is_never_shown(self):
        self.document()
        self.run_seed()
        page = self.child(0, "AsyncAPI")
        puts = [c for c in self.platform.calls if c.method == "PUT" and c.path.endswith((page["portalPageContentId"], page["id"]))]
        self.assertEqual([c.path.rsplit("/", 1)[1] for c in puts], [page["portalPageContentId"], page["id"]])
        self.assertEqual(puts[0].json, {"type": "ASYNCAPI", "content": DOC_RENDERED})
        self.assertTrue(puts[1].json["published"])

    def test_a_second_run_changes_nothing_and_a_page_is_not_written_when_it_holds_the_text(self):
        self.document()
        self.seeded()
        records = json.dumps(common.created())
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(json.dumps(common.created()), records)
        self.assertIn("API pages exist: moderna: moderna-feeds", self.output())

    def test_every_version_the_viewer_shows_is_kept(self):
        for version in apim_portal.ASYNCAPI_VERSIONS:
            with self.subTest(version=version):
                self.document(text=DOC.replace("asyncapi: 3.0.0", f"asyncapi: {version}"))
                self.run_seed()
                self.assertTrue(self.stored(self.child(0, "AsyncAPI"))["content"].startswith(f"asyncapi: {version}\n"))

    def test_a_document_that_changed_is_the_only_thing_written(self):
        self.document()
        self.seeded()
        self.document(text=DOC + "# more\n")
        page = self.child(0, "AsyncAPI")
        self.assertEqual(self.run_seed(), [("PUT", f"{V2}/moderna/portal-page-contents/{page['portalPageContentId']}"),
                                           ("PUT", self.address(0, "AsyncAPI"))])
        self.assertTrue(self.stored(page)["content"].endswith("# more\n"))
        self.assertEqual(self.run_seed(), [])

    def test_a_text_someone_edited_in_the_console_is_written_back_to_the_file(self):
        self.document()
        self.seeded()
        self.stored(self.child(0, "AsyncAPI"))["content"] = "By hand"
        self.run_seed()
        self.assertEqual(self.stored(self.child(0, "AsyncAPI"))["content"], DOC_RENDERED)

    def test_an_asyncapi_page_that_was_unpublished_or_moved_in_the_order_is_put_right_alone(self):
        self.document()
        self.seeded()
        page = self.child(0, "AsyncAPI")
        page["order"] = 7
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{page['id']}")])
        self.assertEqual((page["published"], page["order"]), (True, 1))
        page["published"] = False
        self.assertEqual(self.run_seed(), [("PUT", f"{ITEMS}/{page['id']}")])
        self.assertTrue(page["published"])
        self.assertIn("✓ API pages updated: moderna: moderna-feeds (4)", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_a_page_whose_stored_text_is_the_right_one_of_another_type_is_written_again(self):
        self.document()
        self.file("portal/api-overview.md", "# $${api.name}\n")
        self.seeded()
        for page in (self.child(0, "AsyncAPI"), self.child(0, "Overview")):
            self.stored(page)["type"] = "OPENAPI"
        self.assertEqual(len(self.run_seed()), 2)
        self.assertEqual([self.stored(self.child(0, title))["type"] for title in ("Overview", "AsyncAPI")], ["GRAVITEE_MARKDOWN", "ASYNCAPI"])

    def test_an_asyncapi_page_that_was_deleted_is_made_again_for_its_api_alone(self):
        self.document()
        self.document("telemetry")
        self.seeded()
        self.world.remove_item("moderna", self.child(0, "AsyncAPI")["id"])
        self.assertEqual([c for c in self.run_seed() if c[0] == "POST"], [("POST", ITEMS)])
        self.assertEqual(self.stored(self.child(0, "AsyncAPI"))["content"], DOC_RENDERED)
        self.assertEqual(self.record()["items"][1], self.child(0, "AsyncAPI")["id"])
        self.assertEqual(self.run_seed(), [])

    def test_an_overview_that_was_deleted_beside_an_asyncapi_page_is_asked_for_again_and_the_page_is_made_after_it(self):
        self.document()
        self.seeded()
        old = self.child(0, "AsyncAPI")["id"]
        self.world.remove_item("moderna", OVERVIEWS[0])   # the platform makes the default page of an item only when it has no page
        self.run_seed()
        self.assertEqual(self.destroyed(), [("DELETE", f"{ITEMS}/{old}")])
        self.assertEqual(self.sent("POST", "/_default-pages"), [{"ids": FEEDS[:1]}])
        self.assertEqual(self.titles(0), ["Overview", "AsyncAPI"])
        self.assertEqual(self.stored(self.child(0, "AsyncAPI"))["content"], DOC_RENDERED)
        self.assertEqual(self.run_seed(), [])

    def test_a_page_of_someone_else_called_asyncapi_is_not_deleted_to_make_the_platform_give_an_overview(self):
        self.seeded()
        self.world.remove_item("moderna", OVERVIEWS[1])
        by_hand = {"id": "by-hand", "type": "PAGE", "title": "AsyncAPI", "order": 0, "published": True, "visibility": "PRIVATE",
                   "parentId": FEEDS[1], "portalPageContentId": "x"}
        self.world.items["moderna"]["by-hand"] = dict(by_hand)
        with self.assertRaisesRegex(SeedError, "Telemetry has a page that is not called Overview"):
            self.run_seed()
        self.assertEqual((self.destroyed(), self.world.items["moderna"]["by-hand"]), ([], by_hand))

    def test_a_document_that_is_gone_from_the_folder_takes_its_pages_and_only_those_the_seed_made(self):
        self.document()
        self.seeded()
        page, legacy = self.child(0, "AsyncAPI"), self.address(0, "AsyncAPI")
        by_hand = {"id": "by-hand", "type": "PAGE", "title": "AsyncAPI", "order": 3, "published": True, "visibility": "PRIVATE",
                   "parentId": FEEDS[1], "portalPageContentId": "x"}
        self.world.items["moderna"]["by-hand"] = dict(by_hand)
        self.drop("api-docs/batch-status.yaml")
        self.assertEqual(self.run_seed(), [("DELETE", f"{ITEMS}/{page['id']}"), ("DELETE", legacy)])
        self.assertEqual(self.titles(0), ["Overview"])
        self.assertEqual((self.own(0), self.world.items["moderna"]["by-hand"]), ({}, by_hand))
        self.assertEqual((self.record()["items"], self.record()["legacy"], self.record()["count"]), (OVERVIEWS, [], 2))
        self.assertEqual(self.run_seed(), [])

    def test_the_pages_of_the_menu_are_recorded_in_the_order_of_the_pages_of_each_api_and_counted(self):
        self.document()
        self.document("telemetry")
        self.run_seed()
        record = self.record()
        self.assertEqual(record["items"], [OVERVIEWS[0], self.child(0, "AsyncAPI")["id"], OVERVIEWS[1], self.child(1, "AsyncAPI")["id"]])
        self.assertEqual((record["legacy"], record["count"]), ([{"api": "api-1", "page": self.own(0)["AsyncAPI"]["id"]},
                                                               {"api": "api-2", "page": self.own(1)["AsyncAPI"]["id"]}], 6))
        self.assertIn("✓ API pages created: moderna: moderna-feeds (6)", self.output())

    def test_a_folder_with_no_document_records_no_page_of_an_api(self):
        self.run_seed()
        self.assertEqual((self.record()["legacy"], self.record()["count"]), ([], 2))

    # --- The pages of the API itself ---

    def test_an_api_with_a_document_gets_an_asyncapi_page_among_its_own_pages_published(self):
        self.document()
        self.run_seed()
        page = self.own(0)["AsyncAPI"]
        self.assertEqual({k: page[k] for k in ("type", "content", "published", "visibility", "homepage")},
                         {"type": "ASYNCAPI", "content": DOC_RENDERED, "published": True, "visibility": "PUBLIC", "homepage": False})
        self.assertEqual(self.own(1), {})
        self.assertEqual([c.path for c in self.platform.calls if "/api-2/pages" in c.path], [])   # nothing is asked of it
        [made] = self.sent("POST", "/api-1/pages")
        self.assertEqual(made, {"type": "ASYNCAPI", "name": "AsyncAPI", "visibility": "PUBLIC", "homepage": False, "content": DOC_RENDERED})

    def test_without_a_document_or_a_details_text_the_pages_of_the_apis_are_not_asked_for(self):
        self.run_seed()
        self.assertEqual([c.path for c in self.platform.calls if "/apis/" in c.path], [])

    def test_api_details_is_the_homepage_of_every_listed_api_rendered_with_its_own_escape(self):
        self.file("portal/api-details.md", DETAILS)
        self.run_seed()
        for i in (0, 1):
            page = self.own(i)["Overview"]
            self.assertEqual({k: page[k] for k in ("type", "content", "published", "visibility", "homepage")},
                             {"type": "MARKDOWN", "content": DETAILS_RENDERED, "published": True, "visibility": "PUBLIC", "homepage": True})
        self.assertEqual((self.record()["count"], len(self.record()["legacy"])), (4, 2))

    def test_each_page_of_an_api_is_made_then_published_and_every_one_is_recorded(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        self.run_seed()
        kinds = [(c.method, c.path.rsplit("/", 1)[1]) for c in self.platform.calls if c.method != "GET" and "/api-1/pages" in c.path]
        self.assertEqual(kinds, [("POST", "pages"), ("POST", "_publish"), ("POST", "pages"), ("POST", "_publish")])
        self.assertEqual([e["page"] for e in self.record()["legacy"] if e["api"] == "api-1"], [p["id"] for p in self.own(0).values()])

    def test_a_page_that_is_made_is_recorded_before_it_is_published_so_a_failure_leaves_none_the_seed_does_not_know(self):
        self.document()
        self.world.publishing = False
        with self.assertRaisesRegex(SeedError, r"^Documentation tab of Batch status in moderna \(.*\): HTTP 500 .* \(5 tries\)$"):
            self.run_seed()
        [made] = self.own(0).values()
        self.assertEqual((made["published"], self.record()["legacy"]), (False, [{"api": "api-1", "page": made["id"]}]))
        self.world.publishing = True   # the platform is back: the page is the seed's, so it is published, and not made twice
        self.run_seed()
        self.assertEqual(([p["published"] for p in self.own(0).values()], len(self.own(0))), ([True], 1))

    def test_a_second_run_changes_nothing_in_the_pages_of_the_apis_either(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        records = json.dumps(common.created())
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(json.dumps(common.created()), records)

    def test_a_text_that_changed_is_put_in_the_page_of_the_api_with_its_homepage_and_order_kept(self):
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        page = self.own(0)["Overview"]
        before = (page["order"], page["homepage"], page["published"])
        self.file("portal/api-details.md", DETAILS + "More.\n")
        self.assertEqual(self.run_seed(), [("PUT", self.address(0, "Overview")), ("PUT", self.address(1, "Overview"))])
        self.assertTrue(self.own(0)["Overview"]["content"].endswith("More.\n"))
        self.assertEqual((page["order"], page["homepage"], page["published"]), before)
        sent = self.sent("PUT", f"/api-1/pages/{page['id']}")[0]
        self.assertEqual({k: sent[k] for k in ("type", "name", "visibility", "homepage", "order")},
                         {"type": "MARKDOWN", "name": "Overview", "visibility": "PUBLIC", "homepage": True, "order": before[0]})
        self.assertEqual(self.run_seed(), [])

    def test_a_page_of_an_api_that_someone_changed_in_the_console_is_put_back_and_one_unpublished_is_published_alone(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        self.own(0)["AsyncAPI"]["content"] = "By hand"
        self.own(0)["Overview"]["homepage"] = False
        self.own(1)["Overview"]["published"] = False
        self.assertEqual(self.run_seed(), [("PUT", self.address(0, "Overview")), ("PUT", self.address(0, "AsyncAPI")),
                                           ("POST", self.address(1, "Overview", "_publish"))])
        self.assertEqual((self.own(0)["AsyncAPI"]["content"], self.own(0)["Overview"]["homepage"], self.own(1)["Overview"]["published"]),
                         (DOC_RENDERED, True, True))

    def test_a_page_of_an_api_that_someone_made_private_in_the_console_is_made_public_again(self):
        self.document()
        self.seeded()
        self.own(0)["AsyncAPI"]["visibility"] = "PRIVATE"
        self.assertEqual(self.run_seed(), [("PUT", self.address(0, "AsyncAPI"))])
        self.assertEqual(self.own(0)["AsyncAPI"]["visibility"], "PUBLIC")

    def test_pages_that_went_with_their_api_are_made_again_and_the_record_holds_the_new_ones(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        old = {p["id"] for p in self.own(0).values()}
        self.world.own["moderna"]["api-1"].clear()   # the platform deletes the pages of an API with the API
        self.run_seed()
        self.assertEqual(set(self.own(0)), {"Overview", "AsyncAPI"})
        self.assertTrue(old.isdisjoint({p["id"] for p in self.own(0).values()}))
        self.assertEqual(sorted(e["page"] for e in self.record()["legacy"] if e["api"] == "api-1"), sorted(p["id"] for p in self.own(0).values()))
        self.assertEqual(self.run_seed(), [])

    def test_a_page_of_an_api_that_the_seed_did_not_make_is_never_taken(self):
        self.document()
        self.world.own["moderna"]["api-1"] = {"mine": {"id": "mine", "name": "AsyncAPI", "type": "ASYNCAPI", "content": "By hand", "order": 1,
                                                       "published": True, "visibility": "PUBLIC", "homepage": False}}
        with self.assertRaisesRegex(SeedError, r"^Documentation tab of Batch status in moderna \(environments/moderna/portal/api-docs/"
                                               r"batch-status\.yaml\): the page is taken by one the seed did not create$"):
            self.run_seed()
        self.assertEqual(self.world.own["moderna"]["api-1"]["mine"]["content"], "By hand")
        self.assertEqual([c for c in self.platform.changes() if "/apis/" in c[1]], [])

    def test_a_record_that_was_lost_leaves_the_pages_of_the_apis_taken_and_the_seed_goes_on_when_it_is_back(self):
        self.document()
        self.seeded()
        records = common.created()
        common.save_created([x for x in records if x["kind"] != "portal-api-page"])
        with self.assertRaisesRegex(SeedError, "the page is taken by one the seed did not create"):
            self.run_seed()
        common.save_created(records)
        self.assertEqual(self.run_seed(), [])

    def test_a_page_of_another_type_or_name_is_not_the_one_the_seed_looks_for(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        page = {"order": 1, "published": True, "visibility": "PUBLIC", "homepage": False, "content": "c"}
        self.world.own["moderna"]["api-1"] = {"a": {**page, "id": "a", "name": "Changelog", "type": "MARKDOWN"},
                                              "b": {**page, "id": "b", "name": "AsyncAPI", "type": "MARKDOWN"},
                                              "c": {**page, "id": "c", "name": "Overview", "type": "ASYNCAPI"}}
        self.run_seed()
        self.assertEqual(sorted(p["name"] + p["type"] for p in self.world.own["moderna"]["api-1"].values()),
                         ["AsyncAPIASYNCAPI", "AsyncAPIMARKDOWN", "ChangelogMARKDOWN", "OverviewASYNCAPI", "OverviewMARKDOWN"])
        self.assertEqual({p["id"]: p["content"] for p in self.world.own["moderna"]["api-1"].values() if p["id"] in "abc"}, {"a": "c", "b": "c", "c": "c"})

    def test_the_platform_refusing_the_template_of_the_details_stops_the_seed_on_one_line_naming_the_file_and_the_api(self):
        self.file("portal/api-details.md", "# $${api.name}\n\n$${api.entrypoints}\n")
        with self.assertRaises(SeedError) as stopped:
            self.run_seed()
        self.assertEqual(str(stopped.exception), "Details tab of Batch status in moderna (portal/api-details.md): the platform refuses it: "
                                                 "Invalid expression api.entrypoints")

    def test_the_platform_refusing_the_components_of_gravitee_markdown_in_the_details_is_said_with_its_message(self):
        self.file("portal/api-details.md", "<gmd-card>x</gmd-card>\n")
        with self.assertRaisesRegex(SeedError, r"the platform refuses it: The page content does not follow security policy: "
                                               r"\[Tag not allowed: gmd-card\]$"):
            self.run_seed()

    def test_a_text_the_platform_refuses_in_a_page_that_exists_stops_the_seed_too(self):
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        self.file("portal/api-details.md", "$${nobody}\n")
        with self.assertRaisesRegex(SeedError, r"Details tab of Batch status in moderna.*: the platform refuses it: Invalid expression nobody$"):
            self.run_seed()

    def test_an_api_that_leaves_the_listing_loses_its_pages_and_leaves_the_record(self):
        self.document()
        self.document("telemetry")
        self.seeded()
        gone = self.address(1, "AsyncAPI")
        self.file("portal/listings/feeds.yaml", LISTING.split("  - {apiHrid: moderna-telemetry")[0])
        with self.assertRaisesRegex(SeedError, "moderna-telemetry is no API that a listing of this portal shows"):
            self.run_seed()   # its file would document an API that is not shown
        self.drop("api-docs/telemetry.yaml")
        self.assertEqual(self.run_seed(), [("DELETE", f"{ITEMS}/{FEEDS[1]}"), ("DELETE", gone)])
        self.assertEqual(self.own(1), {})
        self.assertEqual(([e["api"] for e in self.record()["legacy"]], self.record()["count"]), (["api-1"], 3))
        self.assertEqual(self.run_seed(), [])

    def test_a_document_that_is_gone_leaves_the_details_of_its_api_alone(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        self.drop("api-docs/batch-status.yaml")
        self.run_seed()
        self.assertEqual((sorted(self.own(0)), sorted(self.own(1))), (["Overview"], ["Overview"]))
        self.assertEqual(self.run_seed(), [])

    def test_a_run_that_stops_before_it_deletes_a_stale_page_leaves_it_in_the_record_with_the_pages_it_made(self):
        self.document()
        self.seeded()
        stale = {"api": "api-1", "page": self.own(0)["AsyncAPI"]["id"]}
        self.drop("api-docs/batch-status.yaml")
        self.document("telemetry")
        self.world.publishing = False
        with self.assertRaisesRegex(SeedError, r"HTTP 500"):
            self.run_seed()
        made = next(p for p in self.own(1).values())
        self.assertEqual(self.record()["legacy"], [{"api": "api-2", "page": made["id"]}, stale])
        self.assertIn(stale["page"], self.world.own["moderna"]["api-1"])
        self.world.publishing = True
        self.run_seed()
        self.assertEqual(self.record()["legacy"], [{"api": "api-2", "page": made["id"]}])
        self.assertEqual(self.own(0), {})

    def test_a_details_file_that_is_gone_takes_the_homepages_of_the_apis_and_leaves_the_documents_alone(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        self.drop("api-details.md")
        self.run_seed()
        self.assertEqual((sorted(self.own(0)), sorted(self.own(1))), (["AsyncAPI"], []))
        self.assertEqual(self.run_seed(), [])

    def test_two_listings_of_one_api_make_its_pages_once_and_one_that_lets_it_go_does_not_take_them_from_the_other(self):
        self.document()
        self.file("portal/listings/more.yaml", MORE)
        self.seeded()
        page = self.own(0)["AsyncAPI"]["id"]
        self.assertEqual(len(self.own(0)), 1)
        self.assertEqual([x["legacy"] for x in common.created() if x["kind"] == "portal-api-page"], [[{"api": "api-1", "page": page}]] * 2)
        self.file("portal/listings/more.yaml", MORE.replace("moderna-batch-status", "moderna-telemetry"))
        self.run_seed()
        self.assertEqual([c for c in self.destroyed() if "/apis/" in c[1]], [])
        self.assertEqual(self.own(0)["AsyncAPI"]["id"], page)
        self.assertEqual({x["name"]: x["legacy"] for x in common.created() if x["kind"] == "portal-api-page"},
                         {"moderna: moderna-feeds": [{"api": "api-1", "page": page}], "moderna: moderna-more": []})

    def test_a_removal_deletes_the_pages_of_the_apis_by_id_and_the_pages_of_the_menu_cost_no_call(self):
        self.document()
        self.file("portal/api-details.md", DETAILS)
        self.seeded()
        records = common.created()
        legacy = next(x for x in records if x["kind"] == "portal-api-page")["legacy"]
        apim_portal.remove(records)
        pages = [c for c in self.platform.changes() if "/apis/" in c[1]]
        self.assertEqual(sorted(pages), sorted(("DELETE", f"{V2}/moderna/apis/{e['api']}/pages/{e['page']}") for e in legacy))
        self.assertEqual(len(pages), 3)
        self.assertEqual([c for c in self.platform.changes() if c[1].endswith(tuple(OVERVIEWS))], [])
        self.assertEqual(self.world.own["moderna"]["api-1"], {})
        self.assertLess(self.output().index("Deleted portal-api-page"), self.output().index("Deleted portal-listing"))

    def test_a_removal_goes_on_when_the_api_and_its_pages_are_gone_already(self):
        self.document()
        self.seeded()
        self.world.own["moderna"]["api-1"].clear()
        self.world.apis["moderna"] = APIS[1:]
        apim_portal.remove(common.created())
        self.assertIn("✓ Deleted portal-api-page 'moderna: moderna-feeds'", self.output())

    def test_a_removal_with_an_environment_that_is_gone_deletes_no_page(self):
        self.document()
        self.seeded()
        records = common.created()
        self.world.environments.pop("moderna")
        apim_portal.remove(records)
        self.assertEqual([c for c in self.platform.calls if "/apis/" in c.path], [])
        self.assertIn("✓ Deleted portal-api-page 'moderna: moderna-feeds': its environment is gone", self.output())

    def test_a_record_from_before_the_pages_of_the_apis_has_none_and_a_run_that_makes_none_leaves_it_as_it_is(self):
        self.seeded()
        records = common.created()
        for x in records:
            x.pop("legacy", None)
        common.save_created(records)
        before = json.dumps(common.created())
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(json.dumps(common.created()), before)
        apim_portal.remove(common.created())
        self.assertIn("✓ Deleted portal-api-page 'moderna: moderna-feeds'", self.output())



class Occupied(Portals):
    """The folder the platform makes in the top bar of an environment, which the Automation API will not take."""

    def made(self, title, *children):
        self.world.items["moderna"]["default"] = {"id": "default", "type": "FOLDER", "title": title, "published": True, "visibility": "PUBLIC", "order": 0}
        for n, child in enumerate(children):
            self.world.items["moderna"][f"child-{n}"] = {"id": f"child-{n}", "type": child, "title": "Getting started", "parentId": "default"}

    def test_an_empty_folder_at_a_path_of_the_portal_is_removed_before_the_portal_is_made(self):
        self.made("Event feeds")
        changes = self.run_seed()
        self.assertEqual(changes[1:3], [("DELETE", f"{ITEMS}/default"), ("PUT", f"{AUTO}/moderna/portals")])
        self.assertNotIn("default", self.world.items["moderna"])
        self.assertNotIn("default", json.dumps(common.created()))

    def test_a_folder_at_no_path_of_the_portal_is_left_alone(self):
        self.made("APIs")
        self.assertNotIn(("DELETE", f"{ITEMS}/default"), self.run_seed())

    def test_a_folder_that_holds_something_stops_the_seed(self):
        self.made("Event feeds", "PAGE")
        with self.assertRaisesRegex(SeedError, "^Portal moderna-portal in moderna: /event-feeds holds Event feeds, which the seed did not make$"):
            self.run_seed()
        self.assertIn("default", self.world.items["moderna"])

    def test_a_portal_that_is_there_is_never_cleared(self):
        self.seeded()
        self.made("Guides")
        self.run_seed()
        self.assertNotIn("DELETE", [c.method for c in self.platform.calls])


class Taken(Portals):
    """A hrid held by what the seed did not make is never changed."""

    def test_each_kind_of_object_stops_the_seed_when_the_seed_did_not_make_it(self):
        cases = {"portal-theme": ("moderna-theme", "Portal theme"), "portal": ("moderna-portal", "Portal"),
                 "portal-page": ("moderna-guide", "Portal page"), "portal-link": ("moderna-broker", "Portal link")}
        for kind, (hrid, what) in cases.items():
            with self.subTest(kind=kind):
                self.seeded()
                common.save_created([x for x in common.created() if x["kind"] != kind])   # the platform has it, the record does not
                with self.assertRaisesRegex(SeedError, f"^{what} {hrid} in moderna: the hrid is taken by one the seed did not create$"):
                    self.run_seed()
                self.assertEqual([c for c in self.platform.changes() if c[0] == "DELETE"], [])
                self.worlds({"moderna": "Moderna"})
                common.save_created([])
                self.platform.calls.clear()

    def test_a_page_the_seed_did_not_make_is_never_put(self):
        self.world.put("moderna", "portal", None, {"hrid": "moderna-portal", "name": "By hand", "structure": {"topNavbar": []}})
        by_hand = {"hrid": "moderna-guide", "name": "By hand", "type": "GRAVITEE_MARKDOWN", "content": "x", "area": "TOP_NAVBAR"}
        self.world.put("moderna", "page", "moderna-portal", by_hand)
        common.save_created([{"product": "apim-portal", "kind": "portal", "label": "portals", "name": "moderna: moderna-portal",
                              "id": ident("moderna", "portal", "moderna-portal")},
                             {"product": "apim-portal", "kind": "portal-theme", "label": "portal themes", "name": "moderna: moderna-theme",
                              "id": ident("moderna", "theme", "moderna-theme")}])
        self.world.put("moderna", "theme", None, {"hrid": "moderna-theme", "name": "Moderna"})
        self.platform.calls.clear()
        with self.assertRaisesRegex(SeedError, "Portal page moderna-guide in moderna: the hrid is taken"):
            self.run_seed()
        self.assertEqual(self.world.store["moderna", "page", "moderna-portal", "moderna-guide"]["name"], "By hand")
        self.assertNotIn("moderna-guide", [c.json["hrid"] for c in self.platform.calls if c.method == "PUT" and c.path == DOCS])


class Refused(Portals):
    """What the files say that cannot be right is said before the first change."""

    def refused(self, message, files):
        """The seed with these files stops with this message, and changes nothing; the files are as they were after it."""
        before = {name: (common.SEED_DIR / MODERNA / name).read_text(encoding="utf-8")
                  if (common.SEED_DIR / MODERNA / name).exists() else None for name in files}
        self.seed_files({f"{MODERNA}/{name}": text for name, text in files.items()})
        try:
            with self.assertRaisesRegex(SeedError, message):
                self.run_seed()
            self.assertEqual(self.platform.changes(), [])
        finally:
            for name, text in before.items():
                if text is None:
                    (common.SEED_DIR / MODERNA / name).unlink()
                else:
                    self.file(name, text)

    def test_a_theme_that_names_no_file(self):
        self.refused(r"portal/theme\.yaml: \.\./assets/missing\.svg is not a file",
                     {"portal/theme.yaml": THEME.replace("logo.svg", "missing.svg")})

    def test_an_image_over_256_kb(self):
        self.seed_files({f"{MODERNA}/assets/logo.svg": "x" * (300 * 1024)})
        with self.assertRaisesRegex(SeedError, r"theme\.yaml: \.\./assets/logo\.svg is 300 KB, over 256 KB"):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])

    def test_an_image_of_256_kb_is_kept(self):
        self.seed_files({f"{MODERNA}/assets/logo.svg": "x" * (256 * 1024)})
        self.run_seed()
        self.assertIn(("PUT", f"{AUTO}/moderna/portal-themes"), self.platform.changes())

    def test_a_file_that_is_no_image(self):
        self.refused(r"logo\.txt is not an image of", {"assets/logo.txt": "x", "portal/theme.yaml": THEME.replace("logo.svg", "logo.txt")})

    def test_an_asset_that_is_no_image_of_a_theme(self):
        self.refused(r"assets\.banner is not an image of a theme", {"portal/theme.yaml": THEME + "  banner: ../assets/logo.svg\n"})

    def test_a_file_outside_the_seed(self):
        self.refused(r"it is outside the seed", {"portal/theme.yaml": THEME.replace("../assets/logo.svg", "../../../../../../etc/hosts")})

    def test_a_portal_that_shows_a_theme_the_folder_does_not_have(self):
        self.refused(r"portal\.yaml: activeThemeHrid other-theme is no theme of this folder",
                     {"portal/portal.yaml": PORTAL.replace("moderna-theme", "other-theme")})

    def test_a_second_portal_file(self):
        self.refused(r"portal/portal-2\.yaml: a second portal: there is one portal for an environment",
                     {"portal/portal-2.yaml": PORTAL.replace("moderna-portal", "second-portal")})

    def test_a_file_a_portal_folder_does_not_have(self):
        self.refused(r"portal/notes\.yaml: a portal folder has theme\.yaml", {"portal/notes.yaml": "a: 1\n"})

    def test_a_text_that_is_not_api_overview_is_no_file_of_a_portal_folder_and_a_hidden_file_is_none(self):
        self.refused(r"portal/notes\.md: a portal folder has theme\.yaml, portal\.yaml, api-overview\.md, api-details\.md, pages/, content/, "
                     r"links/, listings/ and api-docs/", {"portal/notes.md": "x"})
        self.refused(r"portal/api-overviews\.md: a portal folder has", {"portal/api-overviews.md": "# $${api.name}\n"})
        self.file("portal/.keep", "")
        self.run_seed()

    def test_api_overview_for_a_portal_that_lists_no_api(self):
        os.remove(common.SEED_DIR / MODERNA / "portal" / "listings" / "feeds.yaml")
        self.file("portal/api-overview.md", "# $${api.name}\n")
        with self.assertRaisesRegex(SeedError, r"^environments/moderna/portal/api-overview\.md: it is the Overview page of the APIs a listing shows, "
                                               r"and this portal has no listing$"):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])

    def test_an_api_overview_with_nothing_in_it(self):
        self.refused(r"portal/api-overview\.md: it is empty", {"portal/api-overview.md": "  \n\n"})

    def test_a_variable_of_api_overview_that_no_seed_service_sets(self):
        self.refused(r"portal/api-overview\.md: UNSET_URL not set", {"portal/api-overview.md": "# $${api.name}\n${UNSET_URL}\n"})

    def test_api_overview_with_no_portal_file_is_said_and_not_ignored(self):
        shutil.rmtree(common.SEED_DIR / MODERNA / "portal")
        self.file("portal/api-overview.md", "# $${api.name}\n")
        with self.assertRaisesRegex(SeedError, r"portal\.yaml is missing"):
            self.run_seed()

    def test_a_portal_folder_with_no_portal(self):
        os.remove(common.SEED_DIR / MODERNA / "portal" / "portal.yaml")
        with self.assertRaisesRegex(SeedError, r"portal\.yaml is missing: a theme, a page, a link and a listing belong to a portal"):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_location_that_is_no_path_of_the_portal(self):
        self.refused(r"pages/guide\.yaml: location 'Support' is no path of portal\.yaml",
                     {"portal/pages/guide.yaml": GUIDE.replace("location: Guides", "location: Support")})
        self.refused(r"links/broker\.yaml: location 'Nowhere' is no path of portal\.yaml",
                     {"portal/links/broker.yaml": LINK.replace("Guides", "Nowhere")})
        self.refused(r"listings/feeds\.yaml: location 'Event feeds/Other' is no path of portal\.yaml",
                     {"portal/listings/feeds.yaml": LISTING.replace("Process development", "Other")})

    def test_a_folder_a_path_goes_through_is_a_location_too(self):
        self.file("portal/portal.yaml", PORTAL + "    - {path: Support/Tickets/Open}\n")
        self.file("portal/pages/guide.yaml", GUIDE.replace("location: Guides", "location: Support/Tickets"))
        self.run_seed()
        self.assertEqual(self.sent("PUT", "/documentations")[0]["location"], "/support/tickets")

    def test_a_listing_that_names_no_cross_id_of_the_environment(self):
        self.refused(r"listings/feeds\.yaml: moderna-ghost is the crossId of no API in the apis/ of this environment",
                     {"portal/listings/feeds.yaml": LISTING.replace("moderna-telemetry", "moderna-ghost")})

    def test_a_listing_with_no_apis(self):
        self.refused(r"listings/feeds\.yaml: apis is missing or empty", {"portal/listings/feeds.yaml": "hrid: moderna-feeds\napis: []\n"})

    def test_two_homepages(self):
        self.refused(r"pages: a portal has one homepage", {"portal/pages/second.yaml": HOME.replace("moderna-home", "moderna-home-2")})

    def test_a_hrid_the_platform_refuses_or_that_is_there_twice(self):
        self.refused(r"pages/guide\.yaml: hrid 'a' is missing or has a character the platform refuses",
                     {"portal/pages/guide.yaml": GUIDE.replace("moderna-guide", "a")})
        self.refused(r"pages/home\.yaml: the hrid moderna-home is also environments/moderna/portal/pages/guide\.yaml's",
                     {"portal/pages/guide.yaml": GUIDE.replace("moderna-guide", "moderna-home")})

    def test_a_page_with_its_text_in_a_file_and_in_the_page_or_in_neither(self):
        self.refused(r"it has file or content, one of the two", {"portal/pages/guide.yaml": GUIDE + "content: x\n"})
        self.refused(r"it has file or content, one of the two", {"portal/pages/guide.yaml": GUIDE.replace("file: ../content/guide.md\n", "")})

    def test_a_page_that_names_no_file(self):
        self.refused(r"guide\.yaml: \.\./content/none\.md is not a file", {"portal/pages/guide.yaml": GUIDE.replace("guide.md", "none.md")})

    def test_a_key_the_platform_would_drop_silently(self):
        self.refused(r"portal/theme\.yaml: favicons is not a key of this file", {"portal/theme.yaml": THEME + "favicons: x\n"})
        self.refused(r"pages/guide\.yaml: loaction is not a key of this file", {"portal/pages/guide.yaml": GUIDE + "loaction: Guides\n"})
        self.refused(r"a path of structure\.topNavbar has path, displayName and visibility",
                     {"portal/portal.yaml": PORTAL + "    - {path: Support, order: 3}\n"})

    def test_a_type_a_visibility_or_an_area_the_platform_does_not_have(self):
        self.refused(r"type is GRAVITEE_MARKDOWN, OPENAPI, ASYNCAPI", {"portal/pages/guide.yaml": GUIDE.replace("GRAVITEE_MARKDOWN", "WIKI")})
        self.refused(r"type is GRAVITEE_MARKDOWN, OPENAPI, ASYNCAPI and area TOP_NAVBAR or HOMEPAGE",
                     {"portal/pages/guide.yaml": GUIDE + "area: SIDEBAR\n"})
        self.refused(r"visibility is SECRET, PUBLIC or PRIVATE", {"portal/links/broker.yaml": LINK + "visibility: SECRET\n"})

    def test_a_variable_no_seed_service_sets(self):
        self.refused(r"links/broker\.yaml: UNSET_URL not set", {"portal/links/broker.yaml": LINK.replace("MANAGER_URL", "UNSET_URL")})

    def test_a_page_with_an_expression_that_is_not_escaped_is_a_variable_nobody_sets(self):
        self.refused(r"content/guide\.md: api not set", {"portal/content/guide.md": "Name: ${api}\n"})

    def test_two_paths_that_are_one(self):
        self.refused(r"the path Event Feeds is there twice", {"portal/portal.yaml": PORTAL + "    - {path: Event Feeds}\n"})

    def test_a_path_with_nothing_to_read_in_a_segment(self):
        self.refused(r"a path of structure\.topNavbar is missing or has a segment with no letter or digit",
                     {"portal/portal.yaml": PORTAL + "    - {path: Event feeds/--}\n"})

    def test_a_file_that_is_not_yaml_is_said_on_one_line(self):
        self.refused(r"^environments/moderna/portal/links/broker\.yaml: it is not YAML \([^\n]*\)$",
                     {"portal/links/broker.yaml": "name: [unclosed\nhref: {\n"})

    def test_a_file_that_is_not_a_mapping(self):
        self.refused(r"links/broker\.yaml: it is not a mapping", {"portal/links/broker.yaml": "- a\n- b\n"})

    def test_an_api_export_that_is_no_json_object(self):
        self.refused(r"apis/batch-status\.json: it is not an API export", {"apis/batch-status.json": "[1]"})

    def test_every_file_is_checked_before_any_environment_changes(self):
        self.seed_files({"environments/beam/environment.yaml": "id: beam\nname: Beam\n",
                         "environments/beam/portal/portal.yaml": "hrid: beam-portal\nname: Beam\nbad: key\n"})
        self.worlds({"moderna": "Moderna", "beam": "Beam"})
        with self.assertRaisesRegex(SeedError, r"environments/beam/portal/portal\.yaml: bad is not a key"):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])


class Documented(Portals):
    """What api-docs/ and api-details.md hold that cannot be right is said before the first change."""

    refused = Refused.refused

    def test_a_file_for_no_api_that_a_listing_shows(self):
        self.refused(r"^environments/moderna/portal/api-docs/ghost\.yaml: moderna-ghost is no API that a listing of this portal shows$",
                     {"portal/api-docs/ghost.yaml": DOC})

    def test_a_file_named_for_an_api_of_the_folder_that_no_listing_shows(self):
        self.seed_files({f"{MODERNA}/apis/ghost.json": json.dumps({"api": {"name": "Ghost", "crossId": "moderna-ghost"}})})
        self.refused(r"api-docs/ghost\.yaml: moderna-ghost is no API that a listing of this portal shows", {"portal/api-docs/ghost.yaml": DOC})

    def test_the_file_is_named_for_the_feed_and_not_for_the_cross_id(self):
        self.refused(r"api-docs/moderna-batch-status\.yaml: moderna-moderna-batch-status is no API", {"portal/api-docs/moderna-batch-status.yaml": DOC})

    def test_two_files_for_one_api(self):
        self.refused(r"api-docs/batch-status\.yml: moderna-batch-status has its document in environments/moderna/portal/api-docs/"
                     r"batch-status\.yaml already", {"portal/api-docs/batch-status.yaml": DOC, "portal/api-docs/batch-status.yml": DOC})

    def test_a_file_that_is_no_document_of_an_api(self):
        self.refused(r"api-docs/notes\.txt: api-docs/ holds the AsyncAPI document of an API, <feed>\.yaml", {"portal/api-docs/notes.txt": "x"})

    def test_a_hidden_file_and_a_folder_in_api_docs_are_none(self):
        self.seed_files({f"{MODERNA}/portal/api-docs/.keep": "", f"{MODERNA}/portal/api-docs/old/x.yaml": "a: 1\n"})
        self.assertEqual(self.run_seed()[0], ("PUT", f"{AUTO}/moderna/portal-themes"))

    def test_documents_for_a_portal_that_lists_no_api(self):
        os.remove(common.SEED_DIR / MODERNA / "portal" / "listings" / "feeds.yaml")
        self.refused(r"^environments/moderna/portal/api-docs: they are the documents of the APIs a listing shows, and this portal has no listing$",
                     {"portal/api-docs/batch-status.yaml": DOC})

    def test_documents_with_no_portal_file(self):
        shutil.rmtree(common.SEED_DIR / MODERNA / "portal")
        self.file("portal/api-docs/batch-status.yaml", DOC)
        with self.assertRaisesRegex(SeedError, r"portal\.yaml is missing"):
            self.run_seed()

    def test_api_details_with_nothing_in_it_or_for_a_portal_that_lists_no_api(self):
        self.refused(r"portal/api-details\.md: it is empty", {"portal/api-details.md": "  \n"})
        os.remove(common.SEED_DIR / MODERNA / "portal" / "listings" / "feeds.yaml")
        self.refused(r"^environments/moderna/portal/api-details\.md: it is the Details tab of the APIs a listing shows, and this portal has no listing$",
                     {"portal/api-details.md": DETAILS})

    def test_a_variable_of_a_document_or_of_the_details_that_no_seed_service_sets(self):
        self.refused(r"api-docs/batch-status\.yaml: UNSET_URL not set", {"portal/api-docs/batch-status.yaml": DOC + "# ${UNSET_URL}\n"})
        self.refused(r"portal/api-details\.md: UNSET_URL not set", {"portal/api-details.md": "${UNSET_URL}\n"})

    def test_a_document_that_is_not_yaml_is_said_on_one_line(self):
        self.refused(r"^environments/moderna/portal/api-docs/batch-status\.yaml: it is not YAML \([^\n]*\)$",
                     {"portal/api-docs/batch-status.yaml": "asyncapi: [unclosed\nx: {\n"})

    def test_a_file_that_is_no_asyncapi_document(self):
        for text in ("- a\n- b\n", "hello: world\n", "asyncapi:\ninfo: {title: t, version: '1'}\n", ""):
            with self.subTest(text=text):
                self.refused(r"batch-status\.yaml: it is no AsyncAPI document: it has no asyncapi key", {"portal/api-docs/batch-status.yaml": text})

    def test_a_version_the_viewer_does_not_show(self):
        for version in ("1.2.0", "2.7.0", "3.2.0", "4.0.0", "3"):
            with self.subTest(version=version):
                self.refused(r"batch-status\.yaml: asyncapi %s is a version the portal does not show \(2\.0\.0, 2\.1\.0, 2\.2\.0, 2\.3\.0, "
                             r"2\.4\.0, 2\.5\.0, 2\.6\.0, 3\.0\.0, 3\.1\.0\)$" % re.escape(version),
                             {"portal/api-docs/batch-status.yaml": DOC.replace("asyncapi: 3.0.0", f"asyncapi: '{version}'")})

    def test_a_document_with_no_title_or_no_version_in_its_info(self):
        for info in ("info: {version: '1'}", "info: {title: T}", "info: {title: '', version: '1'}", "info: [a]", "info:"):
            with self.subTest(info=info):
                text = f"asyncapi: 3.0.0\n{info}\nchannels: {{a: {{address: a}}}}\n"
                self.refused(r"batch-status\.yaml: info needs a title and a version", {"portal/api-docs/batch-status.yaml": text})

    def test_a_document_with_no_channel(self):
        for channels in ("", "channels: {}\n", "channels: [a]\n", "channels:\n"):
            with self.subTest(channels=channels):
                self.refused(r"batch-status\.yaml: channels is missing or empty",
                             {"portal/api-docs/batch-status.yaml": "asyncapi: 3.0.0\ninfo: {title: T, version: '1'}\n" + channels})


class Environments(Portals):

    def two(self):
        """Moderna's environment, and Beam's with the same files."""
        self.worlds({"moderna": "Moderna", "beam": "Beam Therapeutics"})
        self.world.apis["beam"] = [{**api, "id": api["id"] + "-beam", "crossId": api["crossId"].replace("moderna", "beam")} for api in APIS]
        self.seed_files({name.replace("moderna", "beam"): text.replace("moderna", "beam") for name, text in FILES.items()})
        self.seed_files({"environments/beam/environment.yaml": "id: beam\nname: Beam Therapeutics\n"})

    def test_each_environment_has_its_own_portal_and_its_own_records(self):
        self.two()
        self.run_seed()
        self.assertEqual(sorted(self.world.active), ["beam", "moderna"])
        self.assertEqual([x["name"] for x in common.created() if x["kind"] == "portal"], ["beam: beam-portal", "moderna: moderna-portal"])
        self.assertEqual({x["env"] for x in common.created()}, {"beam", "moderna"})
        beam = [c.json for c in self.platform.calls if c.method == "PUT" and c.path == f"{AUTO}/beam/portals"]
        self.assertEqual(beam[0]["name"], "Beam Therapeutics Developer Portal")
        self.assertEqual((len(self.world.items["beam"]), len(self.world.items["moderna"])), (10, 10))

    def test_two_environments_are_seeded_by_a_second_run_with_no_change(self):
        self.two()
        self.seeded()
        self.assertEqual(self.run_seed(), [])

    def test_the_same_hrid_in_two_environments_is_two_objects(self):
        self.two()
        for env in ("moderna", "beam"):
            folder = common.SEED_DIR / "environments" / env / "portal"
            (folder / "portal.yaml").write_text(PORTAL.replace("moderna-theme", "shared-theme").replace("moderna-portal", "shared-portal"))
            (folder / "theme.yaml").write_text(THEME.replace("moderna-theme", "shared-theme"))
        self.run_seed()
        self.assertEqual(sorted(x["name"] for x in common.created() if x["kind"] == "portal-theme"),
                         ["beam: shared-theme", "moderna: shared-theme"])

    def test_the_platforms_own_environment_is_read_from_apim_portal(self):
        self.worlds({"DEFAULT": "Default environment"})
        self.only({"apim/portal/portal.yaml": "hrid: lab-portal\nname: ${ENV_NAME} (${ENV_ID})\n",
                   "apim/portal/pages/home.yaml": "hrid: lab-home\nname: Home\ntype: GRAVITEE_MARKDOWN\nfile: ../content/home.md\n",
                   "apim/portal/content/home.md": "# ${ENV_ID}\n"})
        self.run_seed()
        self.assertEqual([c.json for c in self.platform.calls if c.method == "PUT" and c.path == f"{AUTO}/DEFAULT/portals"],
                         [{"hrid": "lab-portal", "name": "Default environment (DEFAULT)", "structure": {"topNavbar": []}}])
        self.assertEqual({x["env"] for x in common.created()}, {"DEFAULT"})
        self.assertEqual(self.world.store["DEFAULT", "page", "lab-portal", "lab-home"]["content"], "# DEFAULT\n")

    def test_the_platforms_own_environment_follows_apim_env(self):
        self.env(APIM_ENV="lab")
        self.worlds({"lab": "Lab"})
        self.only({"apim/portal/portal.yaml": "hrid: lab-portal\nname: ${ENV_ID}\n"})
        self.run_seed()
        self.assertEqual(self.world.store["lab", "portal", None, "lab-portal"]["name"], "lab")

    def test_one_environment_with_its_portal_in_two_places(self):
        self.env(APIM_ENV="moderna")
        self.seed_files({"apim/portal/portal.yaml": "hrid: lab-portal\nname: x\n"})
        with self.assertRaisesRegex(SeedError, "Environment moderna: its portal is in seed/apim/portal and in seed/environments/moderna/portal"):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])

    def test_an_environment_the_platform_does_not_have(self):
        self.world.environments.pop("moderna")
        with self.assertRaisesRegex(SeedError, "^Environment moderna does not exist on this platform$"):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])

    def test_an_environment_with_a_portal_folder_that_holds_nothing_is_left_alone(self):
        self.seed_files({"environments/beam/portal/content/empty.md": "x"})
        self.worlds({"moderna": "Moderna", "beam": "Beam"})
        self.run_seed()
        self.assertEqual({x["env"] for x in common.created()}, {"moderna"})

    def test_an_environment_with_no_portal_folder_is_not_asked_about(self):
        self.seed_files({"environments/beam/environment.yaml": "id: beam\nname: Beam\n"})
        self.run_seed()
        self.assertNotIn("beam", " ".join(c.path for c in self.platform.calls))


class Quiet(Seed):

    def test_a_playground_with_no_portal_folder_needs_no_setting_and_makes_no_call(self):
        self.seed_files({"apim/applications/a.yaml": "name: A\n", "environments/moderna/environment.yaml": "id: moderna\n"})
        with mock.patch.dict(os.environ, {}, clear=True):
            apim_portal.seed()
            apim_portal.remove([])
        self.assertEqual(self.platform.calls, [])

    def test_it_asks_nothing_of_the_platform_when_no_record_is_its_own(self):
        apim_portal.remove([{"product": "apim", "kind": "api", "name": "a", "id": "a"}])
        self.assertEqual(self.platform.calls, [])


class Trouble(Portals):

    def test_a_platform_that_cannot_answer_now_is_asked_again(self):
        self.platform.add({("PUT", f"{AUTO}/moderna/portal-themes"): [503, 429, (200, {"id": "t1"})]})
        self.run_seed()
        self.assertEqual(len(self.sent("PUT", "/moderna/portal-themes")), 3)
        self.assertEqual(self.clock.sleeps, [apim_portal.RETRY_PAUSE] * 2)
        self.assertIn("! Portal theme moderna-theme in moderna: HTTP 503, trying again", self.output())

    def test_a_platform_that_never_answers_ends_the_seed_with_what_it_said(self):
        self.platform.add({("PUT", f"{AUTO}/moderna/portal-themes"): 503})
        with self.assertRaisesRegex(SeedError, r"^Portal theme moderna-theme in moderna: HTTP 503 \(5 tries\)$"):
            self.run_seed()
        self.assertEqual(len(self.clock.sleeps), apim_portal.RETRIES)

    def test_a_refusal_stops_the_seed_at_once_with_the_platforms_message_on_one_line(self):
        self.platform.add({("PUT", f"{AUTO}/moderna/portals"): (400, {"message": "structure.topNavbar[0].path\nmust start with '/'"})})
        with self.assertRaises(SeedError) as stopped:
            self.run_seed()
        self.assertEqual(str(stopped.exception), "Portal moderna-portal in moderna: HTTP 400 "
                                                 "{\"message\": \"structure.topNavbar[0].path\\nmust start with '/'\"}")
        self.assertEqual(self.clock.sleeps, [])

    def test_a_connection_that_fails_is_asked_again_and_its_address_is_not_said(self):
        self.platform.add({("PUT", f"{AUTO}/moderna/portal-themes"):
                           [requests.ConnectionError("http://secret-host"), (200, {"id": "t1"})]})
        self.run_seed()
        self.assertIn("no answer from the management API: ConnectionError", self.output())
        self.assertNotIn("secret-host", self.output())

    def test_a_platform_that_says_what_it_could_not_put_in_a_200_stops_the_seed(self):
        self.platform.add({("PUT", LINKS): (200, {"id": "l1", "errors": {"severe": ["location: no such path"]}})})
        with self.assertRaisesRegex(SeedError, "^Portal link moderna-broker in moderna: location: no such path$"):
            self.run_seed()

    def test_a_token_is_used_in_place_of_the_login(self):
        self.env(APIM_TOKEN="t0ken", APIM_USERNAME=None, APIM_PASSWORD=None)
        self.run_seed()
        self.assertEqual({c.headers.get("Authorization") for c in self.platform.calls}, {"Bearer t0ken"})

    def test_the_login_is_the_one_of_the_seed(self):
        self.run_seed()
        self.assertEqual({c.auth for c in self.platform.calls}, {("admin", "admin")})

    def test_the_automation_api_may_be_served_from_another_host(self):
        self.env(AIM_AUTOMATION_URL="http://automation")
        self.run_seed()
        self.assertEqual({c.url.split("/")[2] for c in self.platform.calls if c.path.startswith("/automation")}, {"automation"})
        self.assertEqual({c.url.split("/")[2] for c in self.platform.calls if not c.path.startswith("/automation")}, {"apim"})

    def test_the_paths_follow_the_organization_and_the_management_path(self):
        self.env(APIM_ORG="acme", APIM_MANAGEMENT_PATH="/apim/rest")
        with self.assertRaises(AssertionError):   # no route of the fake platform serves another organization
            self.run_seed()
        self.assertEqual(self.platform.calls[0].path, "/apim/rest/organizations/acme/environments")


class Removal(Portals):

    def test_it_deletes_the_listings_the_links_the_pages_the_portal_then_the_theme_by_hrid(self):
        self.seeded()
        self.world.put("moderna", "page", "moderna-portal", {"hrid": "not-mine", "name": "N", "type": "GRAVITEE_MARKDOWN",
                                                              "content": "x", "area": "TOP_NAVBAR"})   # the seed did not make it
        self.platform.calls.clear()
        apim_portal.remove(common.created())
        self.assertEqual(self.platform.changes(), [
            ("DELETE", f"{ITEMS}/{FEEDS[0]}"), ("DELETE", f"{ITEMS}/{FEEDS[1]}"),
            ("DELETE", f"{LINKS}/moderna-broker"),
            ("DELETE", f"{DOCS}/moderna-guide"), ("DELETE", f"{DOCS}/moderna-home"),
            ("DELETE", f"{AUTO}/moderna/portals/moderna-portal"), ("DELETE", f"{AUTO}/moderna/portal-themes/moderna-theme")])
        self.assertEqual([k for k in self.world.store if k[1] in ("theme", "portal")], [])

    def test_it_says_what_it_deleted(self):
        self.seeded()
        apim_portal.remove(common.created())
        self.assertIn("✓ Deleted portal-listing 'moderna: moderna-feeds'", self.output())
        self.assertIn("✓ Deleted portal-theme 'moderna: moderna-theme'", self.output())

    def test_what_is_gone_already_counts_as_deleted(self):
        self.seeded()
        self.worlds({"moderna": "Moderna"})
        apim_portal.remove(common.created())
        self.assertIn("✓ Deleted portal 'moderna: moderna-portal'", self.output())

    def test_a_refused_delete_stops_the_removal(self):
        self.seeded()
        self.platform.add({("DELETE", f"{AUTO}/moderna/portals/moderna-portal"): (403, {"message": "no"})})
        with self.assertRaisesRegex(SeedError, "Portal moderna-portal in moderna: HTTP 403"):
            apim_portal.remove(common.created())

    def test_it_leaves_the_records_of_the_other_modules_alone(self):
        self.seeded()
        apim_portal.remove(common.created() + [{"product": "apim", "kind": "api", "label": "APIs", "name": "a", "id": "a"}])
        self.assertTrue(all("/apis" not in c.path for c in self.platform.calls if c.method != "GET"))

    def test_the_api_pages_are_deleted_with_the_api_items_of_their_listing_and_cost_no_call_of_their_own(self):
        self.seeded()
        records = common.created()
        apim_portal.remove(records)
        self.assertEqual([c for c in self.platform.changes() if c[1].endswith(tuple(OVERVIEWS))], [])
        self.assertEqual(self.items("PAGE"), [])
        self.assertIn("✓ Deleted portal-api-page 'moderna: moderna-feeds'", self.output())
        self.assertLess(self.output().index("Deleted portal-api-page"), self.output().index("Deleted portal-listing"))

    def test_what_an_environment_that_is_gone_held_is_left_one_line_each_and_the_other_environments_are_removed(self):
        self.worlds({"moderna": "Moderna", "beam": "Beam Therapeutics"})
        self.world.apis["beam"] = [{**api, "id": api["id"] + "-beam", "crossId": api["crossId"].replace("moderna", "beam")} for api in APIS]
        self.seed_files({name.replace("moderna", "beam"): text.replace("moderna", "beam") for name, text in FILES.items()})
        self.seeded()
        records = common.created()
        self.world.environments.pop("moderna")   # the platform answers 400 to every route of an environment it does not have
        apim_portal.remove(records)
        self.assertEqual([(c.method, c.path) for c in self.platform.calls if "/environments/moderna" in c.path], [("GET", f"{ENVS}/moderna")])
        for kind in ("portal-api-page", "portal-listing", "portal-link", "portal-page", "portal", "portal-theme"):
            self.assertIn(f"✓ Deleted {kind} 'moderna: ", self.output().split(f"Deleted {kind} 'moderna: ")[0] + f"Deleted {kind} 'moderna: ")
        self.assertIn("✓ Deleted portal 'moderna: moderna-portal': its environment is gone", self.output())
        self.assertNotIn("Deleted portal 'beam: beam-portal': its environment is gone", self.output())
        self.assertEqual([k for k in self.world.store if k[0] == "beam"], [])

    def test_each_record_is_removed_in_its_own_environment(self):
        self.worlds({"moderna": "Moderna", "beam": "Beam Therapeutics"})
        self.world.apis["beam"] = [{**api, "id": api["id"] + "-beam", "crossId": api["crossId"].replace("moderna", "beam")} for api in APIS]
        self.seed_files({name.replace("moderna", "beam"): text.replace("moderna", "beam") for name, text in FILES.items()})
        self.seeded()
        apim_portal.remove(common.created())
        self.assertEqual({c.path.split("/environments/")[1].split("/")[0] for c in self.platform.calls if c.method == "DELETE"},
                         {"beam", "moderna"})
        self.assertEqual(self.world.store, {})


class Helpers(unittest.TestCase):

    def test_a_path_is_what_the_platform_keeps(self):
        paths = {"Guides": "/guides", "/Event feeds/": "/event-feeds", "Event feeds/Process development": "/event-feeds/process-development",
                 "Getting Started & FAQ": "/getting-started-faq", "Boston Children's": "/boston-children-s",
                 "Événements": "/v-nements", "x/ Y z /w": "/x/y-z/w"}
        for path, kept in paths.items():
            with self.subTest(path=path):
                self.assertEqual(apim_portal.normal(path), kept)
        for path in ("", "/", "a/--", None):
            with self.subTest(path=path):
                self.assertIsNone(apim_portal.normal(path))

    def test_the_folders_a_path_goes_through(self):
        self.assertEqual(apim_portal.prefixes("/a/b/c"), ["/a", "/a/b", "/a/b/c"])

    def test_what_a_state_covers(self):
        covers = apim_portal.covers
        self.assertTrue(covers({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": 2}}))
        self.assertFalse(covers({"a": 1}, {"a": 2}))
        self.assertFalse(covers({}, {"a": 1}))
        self.assertTrue(covers({"l": [{"x": 1, "id": 9}, {"x": 2}]}, {"l": [{"x": 1}, {"x": 2}]}))
        self.assertFalse(covers({"l": [{"x": 1}]}, {"l": [{"x": 1}, {"x": 2}]}))
        self.assertFalse(covers({"l": [{"x": 2}, {"x": 1}]}, {"l": [{"x": 1}, {"x": 2}]}))
        self.assertFalse(covers(None, {"a": 1}))


if __name__ == "__main__":
    unittest.main()
