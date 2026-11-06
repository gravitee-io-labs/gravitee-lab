"""Gamma: what the modules that talk to the APIM management API share.

The management API serves API Management (v1 and v2), the Gamma modules (Agent Management `aim`, Authorization
Management `authz`, `platform`), the Observability dashboards and the Automation API, and answers to the same
credentials, so a module takes one `Gamma()` and uses the client of the area it needs:

    gamma = Gamma()
    gamma.connect(SECTION, lambda: gamma.aim.get("/providers", params={"perPage": 1}), "AI Management")
    providers = gamma.aim.rows("/providers", CATALOG)

Settings, from the environment of the seed:

    APIM_MANAGEMENT_URL         the management API
    APIM_ORG, APIM_ENV          DEFAULT
    APIM_TOKEN                  a token instead of a login, else APIM_USERNAME and APIM_PASSWORD
    AIM_GAMMA_URL               where /gamma is served, and
    AIM_AUTOMATION_URL          where /automation is served: both default to APIM_MANAGEMENT_URL, and differ where the platform serves them from other hosts
"""

import os
import time

from .common import REFUSED, Api, Gone, HttpError, SeedError, setting, wait_until

SECTION = "gamma"
UNSERVED = 30   # seconds a 404 lasts before it says the platform does not serve a module: its routes answer a moment after it is healthy
NEEDS = "this playground needs the Gamma modules of API Management 4.13 or later and a license that includes them"


# --- Paging -----------------------------------------------------------------------------

class Paging:
    """Where a list starts and how many rows a page may hold: both depend on the endpoint, and a page size over its
    maximum is a 400. A list counts from 0 or from 1, depending on the list and the build, and `first` is where the seed
    starts: `ManagementApi.rows` reads a list from the other start when its answers show that this one is wrong."""

    def __init__(self, first, size):
        self.first, self.size = first, size


WORKSPACES = Paging(first=0, size=100)   # AI workspaces and the MCP proxies
BUDGETS = Paging(first=0, size=100)      # budgets of a workspace
LISTS = Paging(first=1, size=100)        # workspace members, access entities and policies, dashboards
CATALOG = Paging(first=1, size=200)      # the catalog, providers and credentials


# --- The client -------------------------------------------------------------------------

def final(error):
    """Whether a refusal is the platform's last word, whatever the wait: the module says it is not set up (a license
    without it). A 404 is too once it lasts (`UNSERVED`)."""
    return isinstance(error, HttpError) and "not_configured" in (error.body or "")


def page_of(body, path):
    """The rows of one page of a list, and the total it announces (None when it announces none)."""
    key = next((key for key in ("content", "data", "items") if isinstance(body, dict) and key in body), None)
    if key is None:
        raise SeedError(f"GET {path}: this is not a paged list")
    return body[key], body.get("totalElements", body.get("total", (body.get("pagination") or {}).get("totalCount")))


class ManagementApi(Api):
    """An API served by the management API's host. It asks again as every client of the seed does (`common.Api.call`), and
    a module that is not set up says so for good: its 503 `not_configured` is no wait."""

    def busy(self, response):
        return super().busy(response) and "not_configured" not in response.text

    def rows(self, path, paging, **params):
        """Every row of a paged list, whatever its shape: {"content": [...], "totalElements": n}, {"data": [...],
        "pagination": {"totalCount": n}}, {"data": [...], "total": n} or {"items": [...], "total": n}. A list asked from
        page 0 that refuses it (a 400) is read from page 1, and what page 1 answers is the answer: its refusal, if any, is
        the one that is not about where the list starts. A list asked from page 1 that counts from 0 takes it without a word:
        the rows it announces and the pages do not hold say so, and it is read from page 0."""
        def ask(number):
            return self.get(path, params={**params, "page": number, "perPage": paging.size}).json()

        page = paging.first
        try:
            body = ask(page)
        except HttpError as refused:
            if page != 0 or refused.status != 400:
                raise
            page = 1
            body = ask(page)
        out = []
        while True:
            found, total = page_of(body, path)
            out += found
            if not found and paging.first == 1 and len(out) < (total or 0):
                return self.rows(path, Paging(0, paging.size), **params)
            if not found or (len(out) >= total if total is not None else len(found) < paging.size):
                return out
            page += 1
            body = ask(page)


class Gamma:
    """One client for each base path of the management API, all signed in with the same credentials:

        v1, v2          API Management of the environment (/management/..., /management/v2/...)
        org_v1          API Management of the organization (platform users)
        automation      the Automation API of the environment (/aim/mcp-proxies...)
        aim, authz,     the Gamma modules of the environment (/gamma/.../modules/<name>)
        platform
        observability   the dashboards and logs (/gamma/.../observability, not under /modules)
    """

    def __init__(self):
        self.org, self.env = setting("APIM_ORG", "DEFAULT"), setting("APIM_ENV", "DEFAULT")
        self.scope = f"/organizations/{self.org}/environments/{self.env}"
        management = setting("APIM_MANAGEMENT_URL")
        self.gamma_url = setting("AIM_GAMMA_URL", management)
        self.token = os.getenv("APIM_TOKEN")
        self.login = None if self.token else (setting("APIM_USERNAME"), setting("APIM_PASSWORD"))
        self.v1 = self._client(management, f"/management{self.scope}")
        self.v2 = self._client(management, f"/management/v2{self.scope}")
        self.org_v1 = self._client(management, f"/management/organizations/{self.org}")
        self.automation = self._client(setting("AIM_AUTOMATION_URL", management), f"/automation{self.scope}")
        self.observability = self._client(self.gamma_url, f"/gamma{self.scope}/observability")
        self.aim, self.authz, self.platform = self.module("aim"), self.module("authz"), self.module("platform")

    def _client(self, root, path):
        api = ManagementApi(root.rstrip("/") + path, SECTION)
        if self.token:
            api.session.headers["Authorization"] = f"Bearer {self.token}"
        else:
            api.session.auth = self.login
        return api

    def module(self, name):
        """The client of another Gamma module."""
        return self._client(self.gamma_url, f"/gamma{self.scope}/modules/{name}")

    def connect(self, section, probe, what="the management API"):
        """Waits until `probe`, a function that makes one cheap request, succeeds: the management API answers before it
        is fully started. A module it does not serve (a 404 that lasts UNSERVED seconds), or has no license for, ends the
        wait, and so does a refusal of the seed's credentials or ids (REFUSED)."""
        patience = time.time() + UNSERVED

        def check():
            try:
                probe()
            except HttpError as e:
                if final(e) or (e.status == 404 and time.time() >= patience):
                    raise Gone(f"{what} is not available on this platform ({e}): {NEEDS}") from e
                raise
        wait_until(section, what, check, final=REFUSED)

    def modules(self):
        """The ids of the Gamma modules this platform serves, which is what its console loads them from; None when the
        platform answers that it serves no such list (404)."""
        client = self._client(self.gamma_url, f"/gamma/organizations/{self.org}")
        answer = client.get("/modules", expect=(200, 404))
        if answer.status_code == 404:
            return None
        try:
            found = answer.json()
        except ValueError:
            found = None
        if not isinstance(found, list):
            raise SeedError("GET /modules: the answer is not a list of modules")
        return {module.get("id") for module in found if isinstance(module, dict)}


def require_modules(names):
    """Stops the seed before it changes anything when the platform does not serve a Gamma module it will fill: otherwise
    the first call that needs one fails with a bare HTTP error. The management API is healthy a moment before every route
    of it answers: a 404 is the platform's last word once it lasts UNSERVED seconds, and an answer that is no list is not
    ready yet."""
    if not names:
        return
    served, patience = [], time.time() + UNSERVED

    def look():
        found = Gamma().modules()
        if found is None and time.time() < patience:
            raise SeedError("GET /modules: HTTP 404")
        served.append(found or set())
    wait_until(SECTION, "the list of Gamma modules", look, final=REFUSED)
    missing = sorted(set(names) - served[-1])
    if missing:
        raise SeedError(f"This platform does not serve the Gamma module{'s' if len(missing) > 1 else ''} "
                        f"{', '.join(missing)}: {NEEDS}")
