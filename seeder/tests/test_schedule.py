"""Steps that do not depend on one another run at the same time, bounded in one place, and the first failure stops the others."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from lab_seeder import common
from lab_seeder import schedule
from lab_seeder import seed


class Parallel(unittest.TestCase):
    def setUp(self):
        common.configure(4)
        self.addCleanup(common.configure, 1)

    def test_steps_that_do_not_depend_on_one_another_run_at_the_same_time(self):
        meeting = threading.Barrier(2, timeout=5)   # each waits for the other: one after the other, neither would ever come
        started = []

        def work(name):
            meeting.wait()
            started.append(name)
        schedule.run(["a", "b"], {}, work)
        self.assertEqual(sorted(started), ["a", "b"])

    def test_a_step_waits_for_those_it_needs_and_a_removal_goes_the_other_way_round(self):
        names, needs = ["a", "b", "c", "d"], {"b": ("a",), "c": ("a",), "d": ("b", "c")}
        for back in (False, True):
            order, lock = [], threading.Lock()

            def work(name):
                with lock:
                    order.append(name)
            schedule.run(names, needs, work, back=back)
            before = (lambda x, y: order.index(x) < order.index(y)) if not back else (lambda x, y: order.index(y) < order.index(x))
            with self.subTest(back=back):
                self.assertEqual(sorted(order), names)
                self.assertTrue(all(before(need, name) for name in names for need in needs.get(name, ())), order)

    def test_with_one_worker_the_steps_run_in_the_order_they_are_given_and_a_removal_in_the_reverse(self):
        common.configure(1)
        order = []
        schedule.run(["a", "b", "c"], {"c": ("a",)}, order.append)
        schedule.run(["a", "b", "c"], {"c": ("a",)}, order.append, back=True)
        self.assertEqual(order, ["a", "b", "c", "c", "b", "a"])

    def test_the_first_failure_is_raised_the_others_stop_and_what_needs_the_failed_step_never_starts(self):
        started = []

        def work(name):
            started.append(name)
            if name == "a":
                raise common.SeedError("a failed")
            while True:
                common.pause(0.01)   # stops with the seed
        with self.assertRaisesRegex(common.SeedError, "^a failed$"):
            schedule.run(["a", "b", "c"], {"c": ("a",)}, work)
        self.assertNotIn("c", started)
        self.assertIsInstance(common.failure(), common.SeedError)
        self.assertEqual(str(common.failure()), "a failed")

    def test_steps_that_are_not_in_an_order_the_run_can_follow_are_refused(self):
        with self.assertRaisesRegex(common.SeedError, "b needs a, which does not come before it"):
            schedule.run(["b", "a"], {"b": ("a",)}, lambda name: None)
        with self.assertRaisesRegex(common.SeedError, "a needs ghost"):
            schedule.run(["a"], {"a": ("ghost",)}, lambda name: None)

    def test_items_are_done_together_with_their_results_in_the_order_of_the_items(self):
        self.assertEqual(common.parallel(lambda number: number * 2, [3, 1, 2]), [6, 2, 4])
        common.configure(1)
        self.assertEqual(common.parallel(lambda number: number * 2, [3, 1, 2]), [6, 2, 4])

    def test_an_item_that_fails_ends_the_others_and_is_the_failure_raised(self):
        def work(number):
            if number == 2:
                raise common.SeedError("two failed")
            while True:
                common.pause(0.01)
        with self.assertRaisesRegex(common.SeedError, "^two failed$"):
            common.parallel(work, [1, 2, 3])

    def test_no_more_requests_are_in_flight_than_workers(self):
        common.configure(2)
        inside, most, lock = [0], [0], threading.Lock()
        hold = threading.Event()

        def work(_):
            with common.slot():
                with lock:
                    inside[0] += 1
                    most[0] = max(most[0], inside[0])
                hold.wait(0.1)
                with lock:
                    inside[0] -= 1
        common.parallel(work, range(6))
        self.assertEqual(most[0], 2)

    def test_a_request_does_not_start_once_the_seed_stops(self):
        common.stop(common.SeedError("x"))
        with self.assertRaises(common.Stopped):
            common.check()
        with self.assertRaises(common.Stopped):
            common.pause(1)

    def test_what_was_created_is_recorded_whole_whoever_records_it(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(common, "STATE_FILE", Path(folder) / "created.json"):
                common.parallel(lambda number: common.record("p", "k", "ks", f"n{number}", number), range(40))
                self.assertEqual(sorted(x["id"] for x in common.created()), list(range(40)))
                self.assertEqual(len(json.loads(common.STATE_FILE.read_text())), 40)

    def test_a_wait_put_off_runs_at_the_end_and_at_once_when_nothing_is_put_off(self):
        ran = []
        common.later("a wait", lambda: ran.append("now"))
        self.assertEqual(ran, ["now"])
        common.defer_waits()
        common.later("a wait", lambda: ran.append("at the end"))
        common.later("another", lambda: ran.append("also at the end"))
        self.assertEqual(ran, ["now"])
        common.run_later()
        self.assertEqual(sorted(ran[1:]), ["also at the end", "at the end"])


class Milestones(unittest.TestCase):
    def setUp(self):
        common.configure(4)
        self.addCleanup(common.configure, 1)

    def test_a_step_that_waits_for_a_milestone_goes_on_when_another_says_it_is_reached(self):
        seen = []

        def waiter():
            common.reached("apim:beam")
            seen.append("went on")
        thread = threading.Thread(target=waiter)
        thread.start()
        self.assertEqual(seen, [])
        common.milestone("apim:beam")
        thread.join(5)
        self.assertEqual(seen, ["went on"])

    def test_the_end_of_a_whole_step_releases_those_that_wait_for_a_part_of_it(self):
        seen = []

        def work(name):
            if name == "portal":
                common.reached("apim:nowhere", also="apim")   # apim has nothing for that environment: its end says so
                seen.append("portal went on")
            else:
                seen.append("apim ends")
        schedule.run(["apim", "portal"], {}, work)   # started together, as a MEANWHILE does
        self.assertIn("portal went on", seen)

    def test_in_order_a_wait_for_a_milestone_returns_at_once_and_a_stopped_seed_ends_it(self):
        common.configure(1)
        common.reached("never")
        common.configure(4)
        common.stop(common.SeedError("x"))
        with self.assertRaises(common.Stopped):
            common.reached("never")


class Modules(unittest.TestCase):
    def test_every_module_says_what_it_depends_on_and_only_on_those_before_it(self):
        named, needs = seed.graph()
        self.assertEqual(list(named), [module.__name__.rsplit('.', 1)[-1] for module in seed.MODULES])
        for module in seed.MODULES:
            self.assertIsInstance(module.DEPENDS, tuple, module.__name__.rsplit('.', 1)[-1])
        self.assertIsNone(schedule.order_of(list(named), needs))

    def test_a_seed_starts_a_module_with_those_it_can_start_meanwhile_and_a_removal_keeps_its_dependencies(self):
        class Early:
            DEPENDS, MEANWHILE = (), ()

            @staticmethod
            def seed():
                pass

        class Late:
            DEPENDS, MEANWHILE = ("Early",), ("Early",)

            @staticmethod
            def seed():
                pass
        with mock.patch.object(seed, "AFTER_AM", (Early, Late)):
            self.assertEqual(seed.graph(seeding=True)[1]["Late"], ())
            self.assertEqual(seed.graph()[1]["Late"], ("Early",))

    def test_a_module_that_says_nothing_depends_on_all_those_before_it(self):
        class Module:
            @staticmethod
            def seed():
                pass
        with mock.patch.object(seed, "AFTER_AM", (Module,)):
            named, needs = seed.graph()
        self.assertEqual(needs["Module"], ("openfga", "am"))


if __name__ == "__main__":
    unittest.main()
