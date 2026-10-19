"""resolve: the closure of a unit, who claims a name first and who loses a clash, the audience, what a unit needs from other
catalogs, the add-on that runs the gateway of a platform, and the same item twice."""

import unittest

from lab_ui.catalogs import resolve

from .. import Case


def unit(name, tier="other", services=(), containers=(), volumes=(), **more):
    """The record of collisions(): what a unit holds, and who it is."""
    return dict({"name": name, "tier": tier, "services": list(services), "containers": list(containers),
                 "volumes": list(volumes)}, **more)


class Targets(Case):

    def test_a_unit_file_is_read_by_where_it_sits(self):
        for path, found in ((".lab/catalogs/gravitee/addons/kafka/compose.yml", ("gravitee", "addon", "kafka")),
                            (".lab/catalogs/acme-hub/playgrounds/bank/compose.yml", ("acme-hub", "playground", "bank")),
                            (".lab/catalogs/local/setups/mine.yml", ("local", "setup", "mine")),
                            (".lab/catalogs/ab/addons/x/compose.yml", ("ab", "addon", "x"))):
            with self.subTest(path=path):
                self.assertEqual(resolve.target_of(path), found)

    def test_anything_else_is_no_unit(self):
        for path in (None, "", 7, "addons/kafka/compose.yml", ".lab/catalogs/.stage-acme/addons/x/compose.yml",
                     ".lab/catalogs/acme/addons/x/certs/compose.yml", ".lab/catalogs/acme/addons/x/compose.yml/y",
                     ".lab/catalogs/acme/addons/x.yml", ".lab/catalogs/acme/setups/x/compose.yml", ".lab/catalogs/acme/setups/x.yml/y",
                     ".lab/catalogs/a/addons/x/compose.yml", ".lab/catalogs/Acme/addons/x/compose.yml",
                     ".lab/catalogs/acme/addons/X/compose.yml", ".lab/catalogs/acme/addons/../x/compose.yml",
                     ".lab/catalogs/acme-/addons/x/compose.yml", ".lab/catalogs/acme/other/x/compose.yml",
                     "/.lab/catalogs/acme/addons/x/compose.yml", ".lab/catalogs/acme/addons/x/compose.yml\n"):
            with self.subTest(path=path):
                self.assertIsNone(resolve.target_of(path))


class Closure(Case):

    def test_a_unit_then_its_setups_then_its_add_ons_sorted(self):
        units = {("playground", "bank"): [("addon", "kafka"), ("setup", "pair"), ("addon", "jade")],
                 ("setup", "pair"): [("addon", "mail"), ("addon", "kafka")], ("addon", "kafka"): [("addon", "base")],
                 ("addon", "jade"): [], ("addon", "mail"): [], ("addon", "base"): []}
        self.assertEqual(resolve.closure(units, "playground", "bank"),
                         [("playground", "bank"), ("setup", "pair"), ("addon", "base"), ("addon", "jade"), ("addon", "kafka"),
                          ("addon", "mail")])
        self.assertEqual(resolve.closure(units, "setup", "pair"), [("setup", "pair"), ("addon", "base"), ("addon", "kafka"), ("addon", "mail")])
        self.assertEqual(resolve.closure(units, "addon", "mail"), [("addon", "mail")])

    def test_a_cycle_ends_the_walk(self):
        units = {("addon", "a"): [("addon", "b")], ("addon", "b"): [("addon", "c")], ("addon", "c"): [("addon", "a"), ("addon", "b")]}
        self.assertEqual(resolve.closure(units, "addon", "a"), [("addon", "a"), ("addon", "b"), ("addon", "c")])

    def test_an_include_the_project_does_not_know_is_listed_and_not_followed(self):
        units = {("addon", "a"): [("addon", "gone")]}
        self.assertEqual(resolve.closure(units, "addon", "a"), [("addon", "a"), ("addon", "gone")])
        self.assertEqual(resolve.closure({}, "addon", "x"), [("addon", "x")])

    def test_the_lists_of_a_json_file_are_units_too(self):
        units = {("addon", "a"): [["addon", "b"]], ("addon", "b"): []}
        self.assertEqual(resolve.closure(units, "addon", "a"), [("addon", "a"), ("addon", "b")])

    def test_the_project_grouping_is_read_as_well(self):
        project = {"addon": {"kafka": {"needs": ["base"]}, "base": {"needs": []}, "jade": {"needs": []}},
                   "setup": {"pair": {"includes": ["mail", "kafka"]}, "mail": {}},
                   "playground": {"bank": {"setups": ["pair"], "includes": ["jade"], "addons": ["jade", "mail", "kafka", "base"]}}}
        self.assertEqual(resolve.closure(project, "playground", "bank"),
                         [("playground", "bank"), ("setup", "pair"), ("addon", "jade"), ("addon", "kafka"), ("addon", "mail"),
                          ("addon", "base")][:2] + [("addon", "base"), ("addon", "jade"), ("addon", "kafka"), ("addon", "mail")])
        self.assertEqual(resolve.closure(project, "addon", "nobody"), [("addon", "nobody")])

    def test_ids_of_two_kinds_are_two_units(self):
        units = {("playground", "bank"): [("addon", "bank")], ("addon", "bank"): []}
        self.assertEqual(resolve.closure(units, "playground", "bank"), [("playground", "bank"), ("addon", "bank")])

    def test_a_long_chain_is_not_a_recursion(self):
        units = {("addon", str(number)): [("addon", str(number + 1))] for number in range(5000)}
        self.assertEqual(len(resolve.closure(units, "addon", "0")), 5001)


class Precedence(Case):

    def test_the_default_catalog_then_personal_then_the_others_as_added(self):
        units = {"gravitee": [("addon", "kafka"), ("addon", "jade")], "local": [("addon", "mine")],
                 "acme-hub": [("addon", "z"), ("addon", "a")], "partner": [("addon", "p")]}
        self.assertEqual(resolve.precedence_order(units, ["gravitee", "local", "partner", "acme-hub"]),
                         [("addon", "jade"), ("addon", "kafka"), ("addon", "mine"), ("addon", "p"), ("addon", "a"), ("addon", "z")])

    def test_a_catalog_the_list_does_not_name_claims_last(self):
        units = {"gravitee": [("addon", "a")], "zeta": [("addon", "z")], "alpha": [("addon", "y")]}
        self.assertEqual(resolve.precedence_order(units, ["gravitee"]), [("addon", "a"), ("addon", "y"), ("addon", "z")])
        self.assertEqual(resolve.precedence_order({}, ["gravitee"]), [])
        self.assertEqual(resolve.precedence_order({"x": []}, []), [])

    def test_within_a_catalog_an_add_on_comes_before_a_playground_of_the_same_name(self):
        units = {"acme": [("playground", "bank"), ("addon", "bank")]}
        self.assertEqual(resolve.precedence_order(units, ["acme"]), [("addon", "bank"), ("playground", "bank")])


class Collisions(Case):

    ORDER = [("addon", "echo"), ("addon", "mine"), ("addon", "a-one"), ("addon", "b-two"), ("playground", "c-three")]

    def owned(self, changes=None):
        found = {("addon", "echo"): unit("Echo", "default", ["echo"], ["gio-echo"], ["data-echo"]),
                 ("addon", "mine"): unit("Mine", "local", ["mine"]),
                 ("addon", "a-one"): unit("One", "other", ["one"], ["gio-one"], ["data-one"]),
                 ("addon", "b-two"): unit("Two", "other", ["two"]),
                 ("playground", "c-three"): unit("Three", "other", ["three"])}
        found.update(changes or {})
        return found

    def test_without_a_clash_nobody_is_blocked(self):
        self.assertEqual(resolve.collisions(self.ORDER, self.owned(), {}), {})

    def test_a_unit_of_another_catalog_that_defines_a_service_an_earlier_unit_holds_is_blocked(self):
        owned = self.owned({("addon", "a-one"): unit("One", "other", ["echo"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("addon", "a-one"): "Same name as Echo"})

    def test_a_container_and_a_volume_clash_too(self):
        owned = self.owned({("addon", "a-one"): unit("One", "other", ["one"], ["gio-echo"]),
                              ("addon", "b-two"): unit("Two", "other", ["two"], [], ["data-echo"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}),
                         {("addon", "a-one"): "Same name as Echo", ("addon", "b-two"): "Same name as Echo"})

    def test_only_the_first_clash_is_said_and_it_names_the_owner(self):
        owned = self.owned({("addon", "b-two"): unit("Two", "other", ["echo", "one"], ["gio-one"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("addon", "b-two"): "Same name as Echo"})
        owned = self.owned({("addon", "b-two"): unit("Two", "other", ["one", "echo"], ["gio-echo"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("addon", "b-two"): "Same name as One"})

    def test_the_default_catalog_and_personal_units_are_never_blocked(self):
        owned = self.owned({("addon", "mine"): unit("Mine", "local", ["echo"]),
                              ("addon", "echo"): unit("Echo", "default", ["echo"], [], [])})
        order = [("addon", "mine"), ("addon", "echo")]
        self.assertEqual(resolve.collisions(order, owned, {}), {})

    def test_adding_a_catalog_never_blocks_what_was_there(self):
        before = resolve.collisions(self.ORDER[:3], self.owned(), {})
        order = self.ORDER[:3] + [("addon", "z-new")]
        owned = self.owned({("addon", "z-new"): unit("New", "other", ["echo", "one", "mine"])})
        after = resolve.collisions(order, owned, {})
        self.assertEqual(before, {})
        self.assertEqual(after, {("addon", "z-new"): "Same name as Echo"})
        self.assertEqual({key for key in after if key in self.ORDER[:3]}, set())

    def test_a_unit_that_brings_the_one_that_holds_the_name_is_not_in_a_clash(self):
        owned = self.owned({("playground", "c-three"): unit("Three", "other", ["three", "one"])})
        closures = {("playground", "c-three"): [("playground", "c-three"), ("addon", "a-one")]}
        self.assertEqual(resolve.collisions(self.ORDER, owned, closures), {})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("playground", "c-three"): "Same name as One"})

    def test_a_unit_blocked_already_claims_nothing(self):
        owned = self.owned({("addon", "a-one"): unit("One", "other", ["shared"]), ("addon", "b-two"): unit("Two", "other", ["shared"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("addon", "b-two"): "Same name as One"})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}, blocked=[("addon", "a-one")]), {})

    def test_a_unit_that_is_blocked_is_not_judged_again(self):
        owned = self.owned({("addon", "a-one"): unit("One", "other", ["echo"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}, blocked=[("addon", "a-one")]), {})

    def test_the_personal_twin_of_a_unit_is_not_a_clash(self):
        mine = unit("Bank", "local", ["bakery-api"], id="local/bank", kind="playground")
        theirs = unit("Bank", "other", ["bakery-api"], id="@acme/bank", kind="playground")
        order = [("playground", "local/bank"), ("playground", "@acme/bank")]
        owned = {order[0]: mine, order[1]: theirs}
        self.assertEqual(resolve.collisions(order, owned, {}), {})
        other = unit("Bank", "other", ["bakery-api", "bakery-web"], id="@acme/bank", kind="playground")
        self.assertEqual(resolve.collisions(order, {order[0]: mine, order[1]: other}, {}), {order[1]: "Same name as Bank"})

    def test_keys_may_be_ids(self):
        order = ["echo", "@acme/x"]
        owned = {"echo": unit("Echo", "default", ["echo"]), "@acme/x": unit("X", "other", ["echo"])}
        self.assertEqual(resolve.collisions(order, owned, {}), {"@acme/x": "Same name as Echo"})
        self.assertEqual(resolve.collisions(order, {"echo": {"tier": "default", "services": ["echo"]},
                                                    "@acme/x": {"services": ["echo"]}}, {}), {"@acme/x": "Same name as echo"})

    def test_a_unit_with_no_record_holds_nothing(self):
        self.assertEqual(resolve.collisions([("addon", "unknown")], {}, {}), {})
        self.assertEqual(resolve.collisions([], {}, {}), {})

    def test_the_names_of_a_unit_come_from_its_resolved_configuration(self):
        config = {"services": {"a": {"container_name": "gio-a"}, "b": {}, "core-one": {"container_name": "gio-core"}, "c": 3},
                  "volumes": {"data-a": {}, "data-core": {}, "data-theirs": {}}}
        core = {"volumes": {"data-core": {}}}
        found = resolve.owned_names(config, core, ["a", "b", "missing", "c"], [{"volumes": {"data-theirs": {}}}, None])
        self.assertEqual(found, {"services": ["a", "b", "c"], "containers": ["gio-a"], "volumes": ["data-a"]})
        self.assertEqual(resolve.owned_names(None, None, ["a"]), {"services": [], "containers": [], "volumes": []})

    def test_the_aliases_a_service_takes_are_names_it_answers_to(self):
        config = {"services": {"a": {"container_name": "gio-a", "networks": {"internal": {"aliases": ["db", "gio-a", 7, None]}, "default": None}},
                               "b": {"networks": ["internal"]}, "c": {"networks": {"x": {"aliases": "db"}, "y": None}},
                               "core-one": {"networks": {"internal": {"aliases": ["theirs"]}}}}}
        found = resolve.owned_names(config, {}, ["a", "b", "c"])
        self.assertEqual(found["containers"], ["gio-a", "db"])
        self.assertEqual(found["services"], ["a", "b", "c"])

    def test_a_service_and_a_container_are_one_namespace_with_the_aliases(self):
        """All three are names that the lab network answers to: a unit that takes the name of another's service as a container
        name or as an alias would answer for it."""
        owned = self.owned({("addon", "a-one"): unit("One", "other", ["one"], ["echo"]),
                            ("addon", "b-two"): unit("Two", "other", ["two"], ["one"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("addon", "a-one"): "Same name as Echo"})
        owned = self.owned({("addon", "a-one"): unit("One", "other", ["gio-echo"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("addon", "a-one"): "Same name as Echo"})
        owned = self.owned({("addon", "b-two"): unit("Two", "other", ["two"], ["one"])})
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {("addon", "b-two"): "Same name as One"})
        owned = self.owned({("addon", "b-two"): unit("Two", "other", ["two"], [], ["one", "gio-one"])})   # a volume is another kind of name
        self.assertEqual(resolve.collisions(self.ORDER, owned, {}), {})

    def test_the_platform_is_an_owner_like_any_other_when_the_caller_names_it(self):
        platform = ("core", "platform")
        owned = self.owned({platform: unit("the platform", "default", ["apim-management-api", "mongodb"], ["gio_apim_management_api"]),
                            ("addon", "a-one"): unit("One", "other", ["one"], ["apim-management-api"]),
                            ("addon", "b-two"): unit("Two", "other", ["two"], ["gio_apim_management_api"])})
        found = resolve.collisions([platform, *self.ORDER], owned, {})
        self.assertEqual(found, {("addon", "a-one"): "Same name as the platform", ("addon", "b-two"): "Same name as the platform"})

    def test_the_name_of_the_owner_is_cut(self):
        long = "x" * 100000
        owned = self.owned({("addon", "echo"): unit(long + "\n\x00", "default", ["echo"]), ("addon", "a-one"): unit("One", "other", ["echo"])})
        found = resolve.collisions(self.ORDER, owned, {})[("addon", "a-one")]
        self.assertLess(len(found), 100)
        self.assertTrue(found.startswith("Same name as xxx") and found.endswith("\u2026"))
        self.assertEqual(resolve.shown("  a\tb\n\x00c "), "a b c")
        self.assertEqual(resolve.shown("abcdef", 4), "abc\u2026")
        self.assertEqual(resolve.shown(None), "None")


class Audience(Case):

    def test_a_rank_for_each_audience_and_any_doubt_is_private(self):
        self.assertEqual([resolve.rank(audience) for audience in ("public", "private", "folder", None, "", 3)], [0, 1, 1, 1, 1, 1])

    def test_a_unit_depends_on_its_own_rank_or_a_lower_one(self):
        for unit_rank, dependency, blocked in ((0, 0, False), (0, 1, True), (1, 0, False), (1, 1, False)):
            with self.subTest(unit=unit_rank, dependency=dependency):
                self.assertEqual(resolve.audience_blocked(unit_rank, dependency), blocked)


class Needs(Case):

    CATALOGS = {"hub": {"name": "Hub", "version": "1.0.0", "state": "uptodate", "pin": "abc",
                        "requires_catalogs": {"partner": {"name": "Partner Bank", "url": "https://example.invalid/p.git", "min": "2.0.0"},
                                              "gravitee": {"name": "Gravitee public", "url": "https://example.invalid/g.git", "min": "1.0.0"}}}}
    INCLUDE = ".lab/catalogs/partner/addons/ledger/compose.yml"
    ENTRY = {"id": "partner", "name": "Partner Bank", "url": "https://example.invalid/p.git", "min": "2.0.0"}

    def needs(self, catalogs=None, includes=None, missing=(), ns="hub"):
        return resolve.catalog_needs({"ns": ns, "includes": includes if includes is not None else [self.INCLUDE]},
                                     dict(self.CATALOGS, **(catalogs or {})), list(missing))

    def test_a_catalog_that_is_not_added_is_missing(self):
        self.assertEqual(self.needs(missing=[self.INCLUDE]), [dict(self.ENTRY, have=None, reason="missing")])

    def test_a_catalog_being_downloaded_or_that_failed_has_no_pin_yet(self):
        for state, error, reason in (("downloading", None, "downloading"), ("downloading", "unreachable", "downloading"),
                                     ("unreachable", "unreachable", "failed"), ("noaccess", None, "failed"), ("checking", None, "downloading"),
                                     ("needs_git", None, "failed")):
            catalogs = {"partner": {"name": "Partner", "version": None, "state": state, "error": error, "pin": None}}
            with self.subTest(state=state):
                found = self.needs(catalogs, missing=[self.INCLUDE])
                self.assertEqual([item["reason"] for item in found], [reason])
                self.assertEqual(found[0]["name"], "Partner Bank")

    def test_a_row_of_the_registry_has_no_pin_and_a_missing_version_says_there_is_no_copy(self):
        """Registry.inputs() gives name, version, state, error and the rest, no pin: a catalog that was added and
        never downloaded has no version, and it is `failed` (Try again), never `old` (Update)."""
        for state, error, reason in (("downloading", None, "downloading"), ("unreachable", "unreachable", "failed"), ("noaccess", None, "failed"),
                                     ("needs_git", None, "failed"), ("wrong_catalog", "no catalog.yml", "failed"), ("checking", None, "downloading"),
                                     ({"key": "unreachable"}, None, "failed"), ("added", None, "downloading")):
            for version in (None, "", 0):
                catalogs = {"partner": {"name": "Partner", "version": version, "state": state, "error": error}}
                with self.subTest(state=state, version=version):
                    found = self.needs(catalogs, missing=[self.INCLUDE])
                    self.assertEqual([(item["reason"], item["have"]) for item in found], [(reason, version)])
                    self.assertEqual(found[0]["name"], "Partner Bank")
                    self.assertEqual([item["reason"] for item in self.needs(catalogs)], [reason])

    def test_a_catalog_that_has_a_copy_is_old_or_fine_whatever_its_state_says(self):
        for state in ("uptodate", "unreachable", "noaccess", "checking"):
            catalogs = {"partner": {"name": "Partner", "version": "1.0.0", "state": state, "error": "x" if state == "unreachable" else None}}
            with self.subTest(state=state):
                self.assertEqual([item["reason"] for item in self.needs(catalogs)], ["old"])
                catalogs["partner"]["version"] = "2.0.0"
                self.assertEqual(self.needs(catalogs), [])
        catalogs = {"partner": {"name": "Partner", "version": "2.0.0", "state": "uptodate", "pin": ""}}
        self.assertEqual([item["reason"] for item in self.needs(catalogs)], ["downloading"])   # a pin that says nothing is no copy

    def test_a_catalog_added_that_lost_the_file_is_gone(self):
        catalogs = {"partner": {"name": "Partner", "version": "2.1.0", "state": "uptodate", "pin": "abc"}}
        found = self.needs(catalogs, missing=[self.INCLUDE])
        self.assertEqual(found, [dict(self.ENTRY, have="2.1.0", reason="gone", unit="ledger")])

    def test_a_catalog_older_than_the_floor_is_old(self):
        catalogs = {"partner": {"name": "Partner", "version": "1.9.9", "state": "uptodate", "pin": "abc"}}
        self.assertEqual(self.needs(catalogs), [dict(self.ENTRY, have="1.9.9", reason="old")])
        catalogs["partner"]["version"] = "2.0.0"
        self.assertEqual(self.needs(catalogs), [])
        catalogs["partner"]["version"] = "10.0.0"
        self.assertEqual(self.needs(catalogs), [])
        catalogs["partner"]["version"] = "not a version"
        self.assertEqual([item["reason"] for item in self.needs(catalogs)], ["old"])

    def test_a_catalog_it_can_use_needs_nothing(self):
        catalogs = {"partner": {"name": "Partner", "version": "2.0.0", "state": "uptodate", "pin": "abc"}}
        self.assertEqual(self.needs(catalogs), [])
        self.assertEqual(self.needs(catalogs, includes=[]), [])

    def test_the_catalog_of_the_unit_itself_is_never_needed(self):
        self.assertEqual(self.needs(includes=[".lab/catalogs/hub/addons/x/compose.yml"], missing=[".lab/catalogs/hub/addons/x/compose.yml"]), [])

    def test_a_catalog_it_does_not_declare_gives_nothing_to_add(self):
        self.assertEqual(self.needs(includes=[".lab/catalogs/stranger/addons/x/compose.yml"],
                                    missing=[".lab/catalogs/stranger/addons/x/compose.yml"]), [])

    def test_a_personal_unit_declares_nothing_and_is_described_by_the_catalog_alone(self):
        found = resolve.catalog_needs({"ns": "local", "includes": [".lab/catalogs/gravitee/addons/kafka/compose.yml"]},
                                      {"gravitee": {"name": "Gravitee public", "version": "1.2.0", "state": "uptodate", "pin": "abc"}},
                                      [".lab/catalogs/gravitee/addons/kafka/compose.yml"])
        self.assertEqual(found, [{"id": "gravitee", "name": "Gravitee public", "url": None, "min": None, "have": "1.2.0",
                                  "reason": "gone", "unit": "kafka"}])
        missing = resolve.catalog_needs({"ns": "local", "includes": [".lab/catalogs/gravitee/addons/kafka/compose.yml"]}, {},
                                        [".lab/catalogs/gravitee/addons/kafka/compose.yml"])
        self.assertEqual([(item["id"], item["reason"]) for item in missing], [("gravitee", "missing")])

    def test_each_catalog_once_in_the_order_of_its_namespace(self):
        includes = [".lab/catalogs/partner/addons/a/compose.yml", ".lab/catalogs/gravitee/addons/b/compose.yml",
                    ".lab/catalogs/partner/addons/c/compose.yml"]
        found = self.needs(includes=includes, missing=includes)
        self.assertEqual([item["id"] for item in found], ["gravitee", "partner"])

    def test_a_target_that_is_not_a_unit_file_still_names_its_catalog(self):
        found = self.needs(includes=[".lab/catalogs/partner/addons/ledger/odd/compose.yml"],
                           missing=[".lab/catalogs/partner/addons/ledger/odd/compose.yml"])
        self.assertEqual([item["reason"] for item in found], ["missing"])

    def test_what_is_not_a_path_is_skipped(self):
        self.assertEqual(self.needs(includes=[None, 7, "", "../x"], missing=[None, 3]), [])
        self.assertEqual(resolve.catalog_needs({}, {}, []), [])


class Gateway(Case):

    def addon(self, platform="cloud", **more):
        return dict({"gateway": {"platform": platform, "service": "gw", "token": "T"}}, **more)

    def test_the_first_in_the_precedence_that_can_run_wins(self):
        units = {"alpha": self.addon(), "hybrid": self.addon(), "zeta": self.addon()}
        self.assertEqual(resolve.gateway_addon(units, "cloud"), "alpha")
        self.assertEqual(resolve.gateway_addon(units, "cloud", order=["zeta", "alpha", "hybrid"]), "zeta")

    def test_not_alphabetical(self):
        units = {"@acme/aaa": self.addon(), "hybrid": self.addon()}
        self.assertEqual(resolve.gateway_addon(units, "cloud", order=["hybrid", "@acme/aaa"]), "hybrid")

    def test_a_unit_that_cannot_run_is_not_a_candidate(self):
        for more in ({"blocked": {"reasons": ["x"]}}, {"verdict": {"state": "blocked"}}, {"compat": "new"}, {"compat": "old"},
                     {"needs_catalogs": [{"id": "x"}]}):
            units = {"first": self.addon(**more), "second": self.addon()}
            with self.subTest(more=more):
                self.assertEqual(resolve.gateway_addon(units, "cloud"), "second")
        self.assertEqual(resolve.gateway_addon({"first": self.addon(blocked=None, compat="ok", needs_catalogs=[],
                                                                    verdict={"state": "ok"})}, "cloud"), "first")

    def test_none_when_nothing_runs_the_gateway_of_that_kind(self):
        self.assertIsNone(resolve.gateway_addon({}, "cloud"))
        self.assertIsNone(resolve.gateway_addon({"a": self.addon("other")}, "cloud"))
        self.assertIsNone(resolve.gateway_addon({"a": {"gateway": None}, "b": {}, "c": None, "d": {"gateway": "cloud"}}, "cloud"))
        self.assertIsNone(resolve.gateway_addon({"a": self.addon(blocked={"reasons": ["x"]})}, "cloud"))

    def test_an_id_the_order_names_and_the_units_lack_is_skipped(self):
        self.assertEqual(resolve.gateway_addon({"a": self.addon()}, "cloud", order=["ghost", "a"]), "a")


class SameItem(Case):

    def pair(self, **changes):
        mine = {"id": "local/bank", "kind": "playground", "name": "Bank", "services": ["bakery-api", "bakery-web"]}
        theirs = dict(mine, id="@acme/bank", **changes)
        return mine, theirs

    def test_the_same_name_and_the_same_services_is_the_same_item(self):
        self.assertTrue(resolve.same_item(*self.pair()))
        self.assertTrue(resolve.same_item(*self.pair(services=["bakery-web", "bakery-api"])))

    def test_a_different_name_or_other_services_is_another_item(self):
        self.assertFalse(resolve.same_item(*self.pair(name="Bank 2")))
        self.assertFalse(resolve.same_item(*self.pair(services=["bakery-api"])))
        self.assertFalse(resolve.same_item(*self.pair(services=["bakery-api", "bakery-web", "bank-db"])))

    def test_a_unit_is_not_the_twin_of_itself_and_a_kind_is_not_another(self):
        mine, _ = self.pair()
        self.assertFalse(resolve.same_item(mine, mine))
        self.assertFalse(resolve.same_item(*self.pair(kind="addon")))

    def test_a_name_is_compared_as_written_and_without_its_edges(self):
        self.assertTrue(resolve.same_item(*self.pair(name="  Bank ")))
        self.assertFalse(resolve.same_item(*self.pair(name="bank")))

    def test_a_unit_with_no_name_is_nobodys_twin(self):
        mine = {"id": "local/x", "name": "", "services": []}
        self.assertFalse(resolve.same_item(mine, dict(mine, id="@a/x")))
        self.assertFalse(resolve.same_item(dict(mine, name=None), dict(mine, id="@a/x", name=None)))

    def test_what_is_not_a_unit_is_not_an_item(self):
        self.assertFalse(resolve.same_item(None, {}))
        self.assertFalse(resolve.same_item({}, "x"))

    def test_the_twin_of_a_unit_with_no_services_is_a_unit_with_none(self):
        mine = {"id": "local/x", "kind": "setup", "name": "Pair", "services": []}
        self.assertTrue(resolve.same_item(mine, dict(mine, id="@a/x", services=None)))


if __name__ == "__main__":
    unittest.main()
