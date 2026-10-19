"""Custom dashboards of the Gamma console (Observability), from seed/aim/dashboards/*.yaml.

A file is the dashboard as the console saves it: an id the file chooses, a title, filters, a time range and widgets, which
the platform stores as written and the console draws. A dashboard is found by its id and written only when what is stored
differs from its file, so the seed also puts back a dashboard someone edited. It has no data of its own: its widgets show
what the gateway reports.
"""

from . import gamma
from .aim_workspaces import subset
from .common import HttpError, SeedError, files, has_seed, load, log, owned, record, recorded

SECTION = "aim-dashboards"
DEPENDS = ()   # the modules whose work this one reads on the platform: none, so it runs at the start (schedule.py)
# The folder of the seed it owns
FOLDERS = ("aim/dashboards",)
# What it records, dependents first
ORDER = ["dashboard"]
CONTENT = ("title", "description", "filters", "timeRange", "widgets")
# The module of Gamma that shows a dashboard (its console: /aim/observe/dashboards), which a platform that asks for it is told
MODULE = "aim"


class Dashboards:
    def __init__(self):
        self.g = gamma.Gamma()
        self.api = self.g.observability

    def connect(self):
        self.g.connect(SECTION, lambda: self.api.get("/dashboards", params={"perPage": 1}), "dashboards")

    def apply(self, spec):
        dashboard_id, title = spec["id"], spec["title"]
        content = {key: spec[key] for key in CONTENT if key in spec}
        stored = self.api.get(f"/dashboards/{dashboard_id}", expect=(200, 404))
        if stored.status_code == 404:
            self.create({"id": dashboard_id, **content}, spec)
            record(SECTION, "dashboard", "dashboards", title, dashboard_id)
            log(SECTION, f"✓ Dashboard created: {title}")
        elif not recorded(SECTION, "dashboard", dashboard_id):
            raise SeedError(f"Dashboard {title}: {dashboard_id} is the id of a dashboard the seed did not create")
        elif not subset(content, stored.json()):
            module = stored.json().get("module")   # one that has a module keeps it
            self.api.put(f"/dashboards/{dashboard_id}", json={**content, **({"module": module} if module else {})},
                         headers={"If-Match": "*"})   # whatever revision is stored
            log(SECTION, f"✓ Dashboard updated: {title}")


    def create(self, body, spec):
        """A platform whose dashboards belong to a module says so, and is asked again with the module of the file (the one that
        shows them by default): another does not ask, and is sent nothing more."""
        try:
            self.api.post("/dashboards", json=body)
        except HttpError as e:
            if e.status != 400 or "module_required" not in (e.body or ""):
                raise
            self.api.post("/dashboards", json={**body, "module": spec.get("module", MODULE)})


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    dashboards = Dashboards()
    dashboards.connect()
    for path in files("aim", "dashboards"):
        dashboards.apply(load(path))


def remove(records):
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    dashboards = Dashboards()
    dashboards.connect()
    for x in mine:
        dashboards.api.delete(f"/dashboards/{x['id']}")
        log(SECTION, f"✓ Deleted dashboard '{x['name']}'")
