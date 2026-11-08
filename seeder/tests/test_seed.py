"""The seed against management APIs that answer from a table: a removal that fails keeps its record, an authorization engine
follows the seed's OpenFGA model, the modules run in their order and a module with nothing to seed leaves the platform alone."""

import json
import os
import subprocess
import sys
import threading
from unittest import mock

from lab_seeder import am
from lab_seeder import apim
from lab_seeder import common
from lab_seeder import openfga
from lab_seeder import seed
from lab_seeder.testkit import Seed

V2 = "/management/v2/organizations/DEFAULT/environments/DEFAULT"
ENGINES = "/domain/authorization-engines"


class Engines(Seed):

    SPEC = {"type": "openfga", "name": "OpenFGA", "store": "Documents", "connectionUri": "http://openfga:8180"}
    STORES = {"Documents": {"id": "store", "model_id": "model-2"}}
    WANTED = {"connectionUri": "http://openfga:8180", "storeId": "store", "authorizationModelId": "model-2"}

    def engine(self, configuration, recorded=True):
        """AM with one OpenFGA engine set so; then the seed for it."""
        record = {"product": "am", "kind": "authorization-engine", "name": "OpenFGA", "id": "e1"}
        self.answer({("GET", ENGINES): (200, [{"id": "e1", "type": "openfga", "name": "OpenFGA",
                                               "configuration": json.dumps(configuration)}]),
                     ("PUT", f"{ENGINES}/e1"): (200, {})},
                    [record] if recorded else [])
        access = am.AccessManagement()
        access.domain_url, access.domain_id = "/domain", "domain"
        access.authorization_engine(self.SPEC, self.STORES)

    def test_the_seeds_engine_follows_a_new_model(self):
        self.engine({**self.WANTED, "authorizationModelId": "model-1"})
        self.assertEqual(self.platform.calls[-1].json, {"name": "OpenFGA", "configuration": json.dumps(self.WANTED)})
        self.assertEqual(self.platform.changes(), [("PUT", f"{ENGINES}/e1")])

    def test_an_engine_set_as_the_seed_wants_is_kept(self):
        self.engine(self.WANTED)
        self.assertEqual(self.platform.changes(), [])

    def test_an_engine_the_seed_did_not_create_is_never_changed(self):
        with self.assertRaisesRegex(common.SeedError, "the seed did not create it"):
            self.engine({**self.WANTED, "authorizationModelId": "model-1"}, recorded=False)
        self.assertEqual(self.platform.changes(), [])


class Removal(Seed):

    STORE = {"product": "openfga", "kind": "store", "name": "Documents", "id": "s1"}

    def remove(self, status):
        self.answer({("DELETE", "/stores/s1"): status}, [self.STORE])
        return self.main("--remove")

    def test_what_is_gone_already_counts_as_deleted(self):
        self.assertEqual(self.remove(404), 0)
        self.assertEqual(common.created(), [])

    def test_a_refused_delete_keeps_the_record(self):
        self.assertEqual(self.remove(403), 1)
        self.assertEqual([x["id"] for x in common.created()], ["s1"])

    def test_it_says_how_many_items_it_removed(self):
        self.answer({("DELETE", "/stores/s1"): 204, ("DELETE", "/stores/s2"): 204},
                    [self.STORE, {**self.STORE, "name": "Projects", "id": "s2"}])
        self.main("--remove")
        self.assertIn("✓ 2 items removed", self.output())

    def test_it_says_item_for_one(self):
        self.remove(204)
        self.assertIn("✓ 1 item removed", self.output())

    def test_what_the_platform_refuses_to_delete_keeps_its_record_and_the_rest_is_removed(self):
        mine = {"product": "apim", "kind": "user", "label": "users", "name": "astrid", "id": "u1"}
        self.answer({("DELETE", "/stores/s1"): 204, ("GET", V2 + "/apis"): (200, {"data": []}),
                     ("DELETE", "/management/organizations/DEFAULT/users/u1"): (400, {"message": "owns an application"})},
                    [self.STORE, mine])
        self.assertEqual(self.main("--remove"), 0)
        self.assertEqual([x["id"] for x in common.created()], ["u1"])
        self.assertIn("✓ 1 item removed, 1 kept", self.output())

    def test_a_removal_that_fails_midway_keeps_the_records_of_what_is_still_there_and_those_only(self):
        second = {**self.STORE, "name": "Projects", "id": "s2"}
        self.answer({("DELETE", "/stores/s1"): 204, ("DELETE", "/stores/s2"): (403, {"message": "no"})}, [self.STORE, second])
        self.assertEqual(self.main("--remove"), 1)
        self.assertEqual([x["id"] for x in common.created()], ["s2"])

    def test_what_a_seed_records_while_a_removal_runs_is_kept(self):
        """A removal beside a seed that still runs (in another container) writes back only what it deleted."""
        late = {"product": "openfga", "kind": "store", "label": "OpenFGA stores", "name": "Late", "id": "s9"}

        def deleted(call):
            common.record(late["product"], late["kind"], late["label"], late["name"], late["id"])   # the other seed, meanwhile
            return 204
        self.answer({("DELETE", "/stores/s1"): deleted}, [self.STORE])
        self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(common.created(), [late])

    def test_a_record_of_no_module_is_forgotten_as_before(self):
        self.answer({}, [{"product": "gone-module", "kind": "thing", "name": "t", "id": "t1"}])
        self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(common.created(), [])


class Record(Seed):
    """`.state/created.json` is the one place that says what the seed made: what cannot be read of it is never written over."""

    ITEM = {"product": "openfga", "kind": "store", "label": "OpenFGA stores", "name": "Documents", "id": "s1"}

    def test_a_damaged_record_stops_the_seed_and_is_never_written_over(self):
        for damaged in ('[{"product": "openfga", "kind": "store", "name": "Documents", "id": "s1"}] trailing', '[{"product": "x"}]',
                        '{"product": "x"}', "[1, 2]", ""):
            with self.subTest(damaged=damaged):
                self.state.write_text(damaged, encoding="utf-8")
                with self.assertRaisesRegex(common.SeedError, r"^The seed's record of what it created \(created\.json in the "
                                                              r"playground's \.state folder\) is damaged: fix it, or Reset$"):
                    common.record("openfga", "store", "OpenFGA stores", "Projects", "s2")
                self.assertEqual(self.state.read_text(encoding="utf-8"), damaged)

    def test_a_removal_of_a_damaged_record_deletes_nothing_and_says_why(self):
        damaged = '[{"product": "openfga", "kind": "store", "name": "Documents", "id": "s1"}] trailing'
        self.state.write_text(damaged, encoding="utf-8")
        self.assertEqual(self.main("--remove"), 1)
        self.assertNotIn("removed", self.output())
        self.assertIn("is damaged: fix it, or Reset", self.output().splitlines()[-1])
        self.assertEqual(self.state.read_text(encoding="utf-8"), damaged)

    def test_the_damaged_record_of_a_place_is_named_and_never_said_to_go_with_a_reset(self):
        # A deploy to a place records in STATE_DIR=/state/<platform id>/<environment id> (lab_ui.gravitee.places.seed_env),
        # and a Reset of This machine empties .state/created.json alone (lab_ui.library.playgrounds.forget)
        state = self.state.parent / "state"
        place = state / "my-account" / "dev" / "created.json"
        place.parent.mkdir(parents=True)
        place.write_text('[{"product": "apim", "kind": "api"}]', encoding="utf-8")
        with mock.patch.object(common, "STATE", state), mock.patch.object(common, "STATE_FILE", place):
            with self.assertRaisesRegex(common.SeedError, r"^The seed's record of what it created \(created\.json in the "
                                                          r"playground's \.state/my-account/dev folder\) is damaged: fix it, or "
                                                          r"delete it and what the seed made in that environment$"):
                common.record("apim", "api", "APIs", "Hotels", "a1")

    def test_a_read_that_fails_stops_the_seed_and_says_why(self):
        common.save_created([self.ITEM])
        with mock.patch.object(type(self.state), "read_text", side_effect=OSError(5, "Input/output error")), \
                self.assertRaisesRegex(common.SeedError, r"cannot be read \(Input/output error\): fix it, or Reset$"):
            common.created()

    def test_a_record_that_cannot_be_written_fails_the_seed(self):
        """Left unrecorded, what the seed made would be refused by the next seed as not its own, and never deleted."""
        with mock.patch.object(common.os, "replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaisesRegex(common.SeedError, r"cannot be written: No space left on device$"):
                common.record("openfga", "store", "OpenFGA stores", "Projects", "s2")
        self.assertEqual(list(self.state.parent.glob("*.part")), [], "no half-written file is left")

    def test_each_write_has_a_file_of_its_own_and_is_synced_before_it_takes_the_place_of_the_record(self):
        moved, synced = [], []
        replace, fsync = common.os.replace, common.os.fsync
        with mock.patch.object(common.os, "replace", lambda a, b: moved.append(str(a)) or replace(a, b)), \
                mock.patch.object(common.os, "fsync", lambda fd: synced.append(fd) or fsync(fd)):
            common.save_created([self.ITEM])
            common.save_created([self.ITEM])
        self.assertEqual((len(set(moved)), len(synced)), (2, 2))
        self.assertTrue(all(name.endswith(".part") for name in moved))

    def test_another_process_that_holds_the_record_is_waited_for(self):
        """A removal and a seed of one playground may run at once, in two containers that share the folder."""
        common.save_created([self.ITEM])
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(self.state) + ".lock"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        done = threading.Event()
        writer = threading.Thread(target=lambda: (common.record("openfga", "store", "OpenFGA stores", "Projects", "s2"), done.set()))
        writer.start()
        self.assertFalse(done.wait(0.3), "it writes while the other holds the record")
        holder.kill()
        writer.join(10)
        self.assertTrue(done.is_set())
        self.assertEqual([x["id"] for x in common.created()], ["s1", "s2"])


# Holds the lock of the record until it is killed
HOLDER = "import fcntl, sys, time\nf = open(sys.argv[1], 'a')\nfcntl.flock(f, fcntl.LOCK_EX)\nprint('held', flush=True)\ntime.sleep(60)\n"


class Modules(Seed):
    """What the order and the contract of the modules give: a playground that seeds nothing of a module is left alone."""

    def run_modules(self):
        """Replaces seed() and remove() of every module with a function that notes its call, as (action, module)."""
        self.order = []
        for module in seed.MODULES:
            for action in ("seed", "remove"):
                def note(*arguments, name=module.__name__.rsplit('.', 1)[-1], action=action):
                    self.order.append((action, name))

                patch = mock.patch.object(module, action, side_effect=note)
                patch.start()
                self.addCleanup(patch.stop)

    def test_one_module_runs_at_a_time_and_a_failure_stops_the_seed_before_the_next(self):
        """What the tests run is what a seed does: the modules in their order, whatever SEED_WORKERS says, which only bounds
        what a module does at the same time."""
        self.workers(4)
        self.run_modules()
        with mock.patch.object(seed.apim, "seed", side_effect=common.SeedError("apim failed")):
            self.assertEqual(self.main(), 1)
        self.assertEqual([name for _, name in self.order], ["openfga", "am"])
        self.assertTrue(self.output().rstrip().endswith("[seed] ✗ apim failed"), self.output())

    def test_the_modules_seed_in_their_order(self):
        self.run_modules()
        self.assertEqual(self.main(), 0)
        self.assertEqual([name for _, name in self.order],
                         ["openfga", "am", "apim", "apim_portal", "am_link", "aim_catalog", "aim_workspaces", "aim_proxies",
                          "aim_agents", "aim_governance", "authz", "aim_dashboards", "k8s"])

    def test_the_gamma_modules_to_fill_follow_the_folders_of_the_seed(self):
        self.assertEqual(seed.gamma_modules(), set())
        self.seed_files({"aim/providers/p.yaml": "name: p", "aim/dashboards/d.yaml": "id: d"})
        self.assertEqual(seed.gamma_modules(), {"aim"})
        self.seed_files({"authz/entities.yaml": "entities: []", "am/gamma-link.yaml": "domain: d"})
        self.assertEqual(seed.gamma_modules(), {"aim", "authz", "platform"})

    def test_a_platform_that_lacks_a_module_the_seed_fills_stops_it_before_any_module_runs(self):
        self.run_modules()
        self.seed_files({"aim/providers/p.yaml": "name: p"})
        self.answer({("GET", "/gamma/organizations/DEFAULT/modules"): (200, [{"id": "edge"}])})
        self.assertEqual(self.main(), 1)
        self.assertIn("[seed] ✗ This platform does not serve the Gamma module aim: this playground needs the Gamma modules",
                      self.output())
        self.assertEqual(self.order, [])
        self.assertEqual(self.platform.changes(), [])

    def test_a_platform_that_serves_them_is_seeded(self):
        self.run_modules()
        self.seed_files({"aim/providers/p.yaml": "name: p", "authz/entities.yaml": "entities: []"})
        self.answer({("GET", "/gamma/organizations/DEFAULT/modules"): (200, [{"id": "aim"}, {"id": "authz"}])})
        self.assertEqual(self.main(), 0, self.output())
        self.assertEqual(len(self.order), len(seed.MODULES))

    def test_a_removal_asks_nothing_of_the_platform_first(self):
        self.run_modules()
        self.seed_files({"aim/providers/p.yaml": "name: p"})
        self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(self.platform.calls, [])

    def test_a_removal_runs_them_the_other_way_round(self):
        self.run_modules()
        self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(self.order, [("remove", module.__name__.rsplit('.', 1)[-1]) for module in reversed(seed.MODULES)])

    def test_the_stores_of_openfga_go_to_am(self):
        with mock.patch.object(openfga, "seed", return_value={"Documents": {"id": "s"}}), \
                mock.patch.object(am, "seed") as access, mock.patch.object(apim, "seed"):
            self.main()
        access.assert_called_once_with({"Documents": {"id": "s"}})

    def test_a_module_that_breaks_fails_the_seed(self):
        self.run_modules()
        with mock.patch.object(seed.authz, "seed", side_effect=KeyError("id")), \
                mock.patch("traceback.print_exc") as shown:
            self.assertEqual(self.main(), 1)
        shown.assert_called_once()
        self.assertNotIn("Playground ready", self.output())
        self.assertEqual(self.output().splitlines()[-1],
                         "[seed] ✗ The seed stopped on an error it did not expect: the lines above say where")

    def test_a_playground_that_seeds_nothing_needs_no_setting_and_makes_no_call(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.main(), 0)
            self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(self.platform.calls, [])
        self.assertEqual(sorted({line.split("]")[0] for line in self.output().splitlines()}), ["[seed"])

    def test_every_module_leaves_the_records_of_the_others_alone(self):
        everyone = [{"product": module.SECTION, "kind": kind, "name": kind, "id": kind, "label": ""}
                    for module in seed.MODULES for kind in getattr(module, "ORDER", ["x"])]
        everyone += [{"product": "am", "kind": "domain", "name": "d", "id": "d", "label": ""},
                     {"product": "apim", "kind": "api", "name": "a", "id": "a", "label": ""},
                     {"product": "unknown", "kind": "thing", "name": "t", "id": "t", "label": ""}]
        for module in seed.MODULES:
            theirs = [x for x in everyone if x["product"] != module.SECTION
                      or (hasattr(module, "ORDER") and x["kind"] not in module.ORDER)]
            with self.subTest(module=module.__name__.rsplit('.', 1)[-1]):
                module.remove(theirs)
                module.remove([])
        self.assertEqual(self.platform.calls, [])

    def test_no_two_modules_record_the_same_kind(self):
        kinds = [(module.SECTION, kind) for module in seed.MODULES for kind in getattr(module, "ORDER", [])]
        self.assertEqual(len(kinds), len(set(kinds)))

    def test_what_a_module_removes_is_its_own_in_the_order_of_its_kinds(self):
        records = [{"product": "p", "kind": kind, "id": i} for i, kind in enumerate(["a", "b", "c", "b", "other"])]
        records.append({"product": "elsewhere", "kind": "b", "id": 9})
        self.assertEqual([x["id"] for x in common.owned(records, "p", ["b", "a"])], [1, 3, 0])
