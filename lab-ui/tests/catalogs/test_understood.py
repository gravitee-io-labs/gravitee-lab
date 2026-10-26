"""catalogs/understood.py: what this Lab understands of a catalog. The items of the contract a unit uses, read from its file as
Compose gives it, and those the lock of this Lab does not list, kind by kind; the lock read once for each of its versions. Pure
but for a seed folder on disk and the paths of the engine, which each test makes in a folder of its own."""

import os

from lab_ui import paths
from lab_ui.catalogs import understood

from .. import Case, fixtures

REPO = fixtures.CORE.parent   # the engine's own tree: the contract.lock of this Lab


def unit(meta=None, services=None, **more):
    """A unit as compose.raw gives it: its x-gravitee block and its services, with the labels every shared service carries."""
    found = {"x-gravitee": {"kind": "addon", "name": "Echo", "description": "Answers", "category": "API", **(meta or {})},
             "services": services if services is not None else {
                 "echo": {"image": "busybox:1.36", "networks": ["internal"],
                          "labels": {"io.gravitee.name": "Echo", "io.gravitee.description": "Answers"}}}}
    found.update(more)
    return found


class Unknown(Case):
    """What a unit uses that the lock of this Lab does not list, kind by kind."""

    def setUp(self):
        self.folder(paths)
        self.lock = understood.parse((REPO / understood.LOCK_FILE).read_text(encoding="utf-8"))

    def unknown(self, config, folder=None, kind="addon"):
        return understood.of_unit(config, folder, kind, self.lock, self.root)

    def test_a_unit_that_uses_only_what_the_lock_lists_is_understood(self):
        waits = unit(services={"echo": {"image": "busybox", "networks": ["internal", "edge"],
                                        "depends_on": {"apim-gateway": {"condition": "service_healthy"}},
                                        "labels": {"io.gravitee.name": "Echo", "io.gravitee.description": "Answers",
                                                   "io.gravitee.console.web.name": "Echo", "io.gravitee.console.web.url": "x"}},
                               "apim-gateway": {"environment": {"A": "1"}}})
        self.assertEqual(self.unknown(waits), [])
        self.assertEqual(self.unknown(unit(meta={"requires": {"images": {"APIM_VERSION": "4.9"}, "lab": 1}})), [])

    def test_a_key_of_x_gravitee_or_of_requires_this_lab_does_not_know(self):
        self.assertEqual(self.unknown(unit(meta={"badge": "gold"})), [("unit.key", "x-gravitee.badge")])
        self.assertEqual(self.unknown(unit(meta={"requires": {"gpu": True}})), [("unit.key", "x-gravitee.requires.gpu")])

    def test_a_key_no_lab_reads_any_more_is_no_sign_of_a_newer_lab(self):
        self.assertEqual(self.unknown(unit(meta={"deploys_to": ["cloud"]})), [], "the check says it is gone")

    def test_a_label_this_lab_does_not_read_and_a_console_attribute_it_does_not_know(self):
        config = unit()
        config["services"]["echo"]["labels"].update({"io.gravitee.badge": "gold", "io.gravitee.console.web.icon": "x",
                                                     "org.opencontainers.image.title": "Echo"})
        self.assertEqual(self.unknown(config), [("label", "io.gravitee.badge"), ("label", "io.gravitee.console.<id>.icon")])

    def test_a_core_service_a_unit_waits_for_shows_or_changes_and_does_not_define(self):
        config = unit()
        config["services"]["echo"]["depends_on"] = ["gamma-gateway"]
        config["x-gravitee"]["consoles"] = ["gamma-portal"]
        config["services"]["gamma-agent"] = {"environment": {"A": "1"}}   # adds to a service it does not define
        self.assertEqual(self.unknown(config), [("core.service", "gamma-agent"), ("core.service", "gamma-gateway"),
                                                ("core.service", "gamma-portal")])

    def test_a_network_it_joins_and_does_not_declare(self):
        config = unit()
        config["services"]["echo"]["networks"] = ["internal", "mesh"]
        self.assertEqual(self.unknown(config), [("core.network", "mesh")])
        self.assertEqual(self.unknown({**config, "networks": {"mesh": {}}}), [], "a network of its own is its own")

    def test_a_category_this_lab_does_not_list(self):
        self.assertEqual(self.unknown(unit(meta={"category": "Payments"})), [("core.category", "Payments")])

    def test_a_place_of_the_engine_it_builds_or_mounts(self):
        for name in ("seeder", "plugins/apim", "tools"):
            (self.root / name).mkdir(parents=True)
        config = unit(services={"seed": {"build": {"context": str(self.root / "seeder")}, "volumes": [
            {"type": "bind", "source": str(self.root / "plugins/apim"), "target": "/p"},
            {"type": "bind", "source": str(self.root / "tools"), "target": "/t"},
            {"type": "volume", "source": "data", "target": "/d"},
            {"type": "bind", "source": str(self.root / ".lab/catalogs/hub/addons/echo/conf"), "target": "/c"}]}})
        self.assertEqual(self.unknown(config), [("anchor", "tools")], "the seeder and a plugin folder are anchors of the lock")

    def test_what_a_seed_holds_where_the_lock_names_the_entries(self):
        folder = self.root / "playgrounds" / "tour"
        for path in ("seed/apim/apis/one.json", "seed/apim/settings.yaml", "seed/apim/notes.yaml", "seed/controls.yaml",
                     "seed/fleet/cars.yaml", "seed/environments/eu/environment.yaml", "seed/k8s/01-ns.yaml",
                     "seed/.hidden/x", "seed/aim/governance/approvals.yaml", "seed/aim/governance/audit.yaml"):
            path = folder / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n", encoding="utf-8")
        found = self.unknown(unit(meta={"kind": "playground"}), folder, "playground")
        self.assertEqual(found, [("seed.file", "aim/governance/audit.yaml"), ("seed.file", "apim/notes.yaml"),
                                 ("seed.folder", "fleet")],
                         "what sits in apim/apis/, an environment or k8s/ is data; what sits where the lock lists is not")

    def test_a_setting_of_the_seeders_run_this_lab_does_not_read(self):
        config = unit(services={"tour-seed": {"build": "x", "labels": {"io.gravitee.role": "seed", "io.gravitee.name": "S",
                                                                         "io.gravitee.description": "S"},
                                              "environment": {"SEED_WORKERS": "4", "SEED_FAST": "1", "TOUR_URL": "x",
                                                              "APIM_GATEWAY_URL": "x"}}})
        self.assertEqual(self.unknown(config), [("seed.setting", "SEED_FAST")],
                         "the other settings of a seed service are what its seed files read")

    def test_without_a_lock_nothing_is_judged(self):
        self.assertEqual(understood.unknown({("unit.key", "x-gravitee.badge")}, None), [])
        self.assertEqual(understood.of_unit(unit(meta={"badge": 1}), root=self.root), [], "this folder holds no contract.lock")

    def test_an_item_is_named_as_a_line_says_it(self):
        self.assertEqual(understood.named([("unit.key", "x-gravitee.badge"), ("label", "io.gravitee.badge")]),
                         "label io.gravitee.badge, x-gravitee.badge")
        many = [("seed.folder", name) for name in "abcde"]
        self.assertEqual(understood.named(many), "seed folder a, seed folder b, seed folder c and 2 more")
        self.assertEqual(understood.name(("catalog.key", "mirrors")), "catalog.yml key mirrors")


class Lock(Case):
    """contract.lock, as this Lab reads it."""

    def setUp(self):
        self.folder(paths)

    def test_the_lock_is_the_two_levels_and_one_row_for_each_item(self):
        self.write("contract.lock", 'contract 2\ncontract_min 1\ncore.category "Gravitee Platform" since=1\n'
                                    "label io.gravitee.name since=2\nbroken line\n")
        found = understood.lock()
        self.assertEqual(found, (2, 1, {("core.category", "Gravitee Platform"): 1, ("label", "io.gravitee.name"): 2}))

    def test_a_lock_is_read_again_when_it_changes_and_is_none_when_it_is_not_the_engines(self):
        self.write("contract.lock", "contract 1\ncontract_min 1\nlabel io.gravitee.name since=1\n")
        first = understood.fingerprint()
        self.write("contract.lock", "contract 1\ncontract_min 1\nlabel io.gravitee.name since=1\nlabel io.gravitee.role since=1\n")
        self.assertNotEqual(understood.fingerprint(), first, "a Lab that is updated understands more")
        self.write("contract.lock", "contract 1\n")
        self.assertIsNone(understood.lock())
        os.remove(self.root / "contract.lock")
        self.assertIsNone(understood.lock())
        self.assertEqual(understood.fingerprint(), "")

    def test_the_lock_of_this_lab_lists_what_its_published_catalogs_use(self):
        """Every item the default catalogs of the Lab use today is in the lock: none of them needs a newer Lab."""
        found = understood.parse((REPO / understood.LOCK_FILE).read_text(encoding="utf-8"))
        for item in (("unit.key", "x-gravitee.host_access"), ("label", "io.gravitee.console.<id>.url"),
                     ("anchor", "seeder"), ("anchor", "plugins/apim"), ("seed.file", "controls.yaml"),
                     ("seed.folder", "aim/studios"), ("core.service", "apim-gateway"), ("core.network", "edge")):
            self.assertIn(item, found.rows, item)
