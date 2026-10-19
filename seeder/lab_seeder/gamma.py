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
import re

import requests

from .common import Api, Gone, HttpError, SeedError, excerpt, log, pause, setting, wait_until

SECTION = "gamma"
RETRIES = 12   # with PAUSE seconds between: a restart of the management API lasts about a minute
PAUSE = 5
NEEDS = "this playground needs the Gamma modules of API Management 4.13 or later and a license that includes them"


# --- Paging -----------------------------------------------------------------------------

class Paging:
    """Where a list starts and how many rows a page may hold: both depend on the endpoint, and a page size over its
    maximum is a 400."""

    def __init__(self, first, size):
        self.first, self.size = first, size


# What a platform whose lists start elsewhere than the seed expected says: they have not all started at the same page in every build
PAGE_FLOOR = re.compile(r"page param must be >= (\d+)")
WORKSPACES = Paging(first=0, size=100)   # AI workspaces and the MCP proxies
LISTS = Paging(first=1, size=100)        # budgets (see requires.images in compose.yml), workspace members, access entities and policies, dashboards
CATALOG = Paging(first=1, size=200)      # the catalog, providers and credentials


# --- The client -------------------------------------------------------------------------

def reloading(response):
    """Whether this is what the management API answers for a few seconds while it reloads, and not about the request."""
    text = response.text
    return ((response.status_code in (502, 503, 504) and "not_configured" not in text)   # a module not set up says so for good
            or (response.status_code == 400 and "Invalid organization or environment" in text)
            or (response.status_code == 500 and "Timeout while receiving message" in text))


def final(error):
    """Whether a refusal is the platform's last word, whatever the wait: the path is not served (a platform without the
    module), or the module says it is not set up (a license without it)."""
    return isinstance(error, HttpError) and (error.status == 404 or "not_configured" in error.body)


def page_of(body, path):
    """The rows of one page of a list, and the total it announces (None when it announces none)."""
    key = next((key for key in ("content", "data", "items") if isinstance(body, dict) and key in body), None)
    if key is None:
        raise SeedError(f"GET {path}: this is not a paged list")
    return body[key], body.get("totalElements", body.get("total", (body.get("pagination") or {}).get("totalCount")))


class ManagementApi(Api):
    """An API served by the management API's host. A request that meets a reload is sent again, up to RETRIES times,
    whatever its method: a module finds what it created before it creates it again."""

    def send(self, method, path, **kwargs):
        for attempt in range(1, RETRIES + 2):
            try:
                answer = super().send(method, path, **kwargs)
                trouble = f"HTTP {answer.status_code}" if reloading(answer) else None
            except requests.ConnectionError as e:
                answer, trouble = None, str(e)[:200]
            if trouble is None:
                return answer
            if attempt > RETRIES:
                break
            log(SECTION, f"! {method} {path}: {trouble}, trying again in {PAUSE} s ({attempt}/{RETRIES})")
            pause(PAUSE)
        if answer is None:
            raise SeedError(f"{method} {path}: {trouble}")
        return answer   # the last answer: the caller reports it

    def rows(self, path, paging, **params):
        """Every row of a paged list, whatever its shape: {"content": [...], "totalElements": n}, {"data": [...],
        "pagination": {"totalCount": n}}, {"data": [...], "total": n} or {"items": [...], "total": n}."""
        out, page = [], paging.first
        while True:
            try:
                body = self.get(path, params={**params, "page": page, "perPage": paging.size}).json()
            except HttpError as e:
                floor = PAGE_FLOOR.search(e.body or "") if e.status == 400 else None
                if not floor or int(floor.group(1)) == page:   # not about where the list starts
                    raise
                out, page = [], int(floor.group(1))   # this platform's list starts there: asked again from its first page
                continue
            found, total = page_of(body, path)
            out += found
            if not found or (len(out) >= total if total is not None else len(found) < paging.size):
                return out
            page += 1


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
        api = ManagementApi(root.rstrip("/") + path)
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
        is fully started. A module it does not serve, or has no license for, ends the wait at once."""
        def check():
            try:
                probe()
            except HttpError as e:
                if final(e):
                    raise Gone(f"{what} is not available on this platform ({e}): {NEEDS}") from e
                raise
        wait_until(section, what, check)

    def modules(self):
        """The ids of the Gamma modules this platform serves, which is what its console loads them from."""
        client = self._client(self.gamma_url, f"/gamma/organizations/{self.org}")
        answer = client.send("GET", "/modules")
        if answer.status_code == 404:
            return set()
        if answer.status_code != 200:
            raise HttpError(f"GET /modules: HTTP {answer.status_code} {excerpt(answer.text)}", answer.status_code,
                            answer.text)
        try:
            found = answer.json()
        except ValueError:
            found = []
        return {module.get("id") for module in found if isinstance(module, dict)}


def require_modules(names):
    """Stops the seed before it changes anything when the platform does not serve a Gamma module it will fill: otherwise
    the first call that needs one fails with a bare HTTP error."""
    if not names:
        return
    served = []
    # The management API is healthy before every route of it answers: only a 404 is the platform's last word
    wait_until(SECTION, "the list of Gamma modules", lambda: served.append(Gamma().modules()))
    missing = sorted(set(names) - served[-1])
    if missing:
        raise SeedError(f"This platform does not serve the Gamma module{'s' if len(missing) > 1 else ''} "
                        f"{', '.join(missing)}: {NEEDS}")
