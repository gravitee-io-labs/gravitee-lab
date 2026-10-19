"""The refills: what the lessons spend, put back on every run. The declaration is read strictly (a typo is an error, never a
refill that refills nothing), a refill deletes the cost counters of one member's subscription and nothing else, whatever
goes wrong with Redis is told and never stops the seed, and the refill of Acme Bakery names the Hackathon sandbox of its files."""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from lab_seeder import common
from lab_seeder import refills
from lab_seeder import seed
from lab_seeder import testkit
from lab_seeder.testkit import PLAYGROUNDS, FakeRedis, Seed, seed_environment, seed_service

ACME_BAKERY = PLAYGROUNDS / "acme-bakery"
ACME_SEED = ACME_BAKERY / "seed"

SANDBOX = "sub-sandbox"
KEY = "ratelimit:ws-1:app-sandbox:cost-rl"


def write(*lines):
    return "\n".join(lines) + "\n"


def counter(subscription, spent="116910"):
    """What the gateway keeps for a budget: the spent amount, the limit, when the period ends, and whose it is."""
    return {"counter": spent, "limit": "120000", "reset": "1791506259034", "subscription": subscription}


def redis_of_the_lab():
    """A Redis as the lab's: the sandbox's counters, those of the other members, a token counter of the sandbox, a
    key that is not a hash, and the semantic cache of the LLM proxy."""
    return FakeRedis({
        KEY: counter(SANDBOX),
        "ratelimit:ws-1:app-care:cost-rl": counter("sub-care", "28100"),
        "ratelimit:ws-1:app-fraud:cost-rl": counter("sub-fraud", "18500"),
        "ratelimit:ws-other:app-sandbox:cost-rl": counter("sub-elsewhere", "4800"),       # same application, another workspace
        "ratelimit:plan-1" + SANDBOX + ":app-sandbox:token-rl": counter(SANDBOX, "400"),  # the same subscription's tokens
        "ratelimit:ws-1:broken:cost-rl": "not a hash",
        "acme-bakery-llm:1234": {"retrieval_context_key": "x", "subscription": SANDBOX},
    })


class Declaration(Seed):

    def declare(self, text):
        self.seed_files({"refills.yaml": text})
        return refills.declared()

    def test_a_seed_without_refills_declares_none_and_reads_no_setting(self):
        self.assertEqual(refills.declared(), [])
        self.assertEqual(refills.begin(), [])
        self.assertEqual(refills.of("Teams", ["team-a"]), [])
        refills.run([], {})
        refills.finish()

    def test_a_refill_names_a_workspace_a_member_and_the_counters_to_refill(self):
        found = self.declare(write("sandbox:", "  workspace: Teams", "  member: team-a", "  counters: cost",
                                   "other: {workspace: Teams, member: team-b, counters: cost}"))
        self.assertEqual([(r.id, r.workspace, r.member, r.counters) for r in found],
                         [("sandbox", "Teams", "team-a", "cost"), ("other", "Teams", "team-b", "cost")])

    def test_a_declaration_that_is_not_understood_is_an_error_that_names_the_refill(self):
        for text, message in (
                ("x: [1]", "refills.yaml: x: expected the workspace, the member and the counters to refill"),
                ("x: {workspace: W, member: M, counters: cost, memberr: 1}", "x: memberr is not a key of a refill"),
                ("x: {member: M, counters: cost}", "x: workspace is the name of the AI workspace"),
                ("x: {workspace: '', member: M, counters: cost}", "x: workspace is the name of the AI workspace"),
                ("x: {workspace: W, counters: cost}", "x: member is the source id of one of its members"),
                ("x: {workspace: W, member: 1, counters: cost}", "x: member is the source id of one of its members"),
                ("x: {workspace: W, member: M}", "x: counters is one of cost"),
                ("x: {workspace: W, member: M, counters: tokens}", "x: counters is one of cost"),
                ("[a, b]", "refills.yaml: expected the refills, by name")):
            with self.subTest(text=text), self.assertRaisesRegex(common.SeedError, message):
                self.declare(text)

    def test_a_refill_is_found_by_the_workspace_a_module_is_about_to_seed(self):
        self.declare(write("a: {workspace: Teams, member: team-a, counters: cost}",
                           "b: {workspace: Other, member: team-a, counters: cost}"))
        self.assertEqual([r.id for r in refills.of("Teams", ["team-a", "team-b"])], ["a"])
        self.assertEqual([r.id for r in refills.of("Other", ["team-a"])], ["b"])
        self.assertEqual(refills.of("Nobody", ["team-a"]), [])

    def test_a_member_the_workspace_file_does_not_list_is_an_error_that_says_so(self):
        self.declare("a: {workspace: Teams, member: team-z, counters: cost}\n")
        with self.assertRaisesRegex(common.SeedError, "^refills.yaml: a refills team-z, who is not a member of AI workspace 'Teams'$"):
            refills.of("Teams", ["team-a", "team-b"])


class Ending(Seed):

    def setUp(self):
        super().setUp()
        self.seed_files({"refills.yaml": write("a: {workspace: Teams, member: team-a, counters: cost}",
                                               "b: {workspace: Other, member: team-b, counters: cost}")})

    def test_a_refill_no_module_found_a_workspace_for_stops_the_seed_naming_it(self):
        refills.begin()
        refills.of("Teams", ["team-a"])
        with self.assertRaisesRegex(common.SeedError, "^refills.yaml: b names AI workspace 'Other', which no seed file has$"):
            refills.finish()

    def test_a_seed_whose_refills_all_found_their_workspace_ends(self):
        refills.begin()
        refills.of("Teams", ["team-a"])
        refills.of("Other", ["team-b"])
        refills.finish()

    def test_a_workspace_misspelled_in_the_declaration_is_found_by_nobody(self):
        self.seed_files({"refills.yaml": "a: {workspace: Teems, member: team-a, counters: cost}\n"})
        refills.begin()
        self.assertEqual(refills.of("Teams", ["team-a"]), [])
        with self.assertRaisesRegex(common.SeedError, "a names AI workspace 'Teems'"):
            refills.finish()

    def test_a_new_seed_forgets_what_the_last_one_found(self):
        refills.begin()
        refills.of("Teams", ["team-a"])
        refills.begin()
        with self.assertRaisesRegex(common.SeedError, "a names AI workspace 'Teams'.*b names AI workspace 'Other'"):
            refills.finish()


class Deleting(unittest.TestCase):
    """What a refill takes out of Redis, and what it leaves."""

    refill = refills.Refill("sandbox", {"workspace": "Teams", "member": "team-a", "counters": "cost"})

    def test_the_cost_counters_of_the_subscription_go_and_nothing_else(self):
        client = redis_of_the_lab()
        self.assertEqual(refills.drop(client, self.refill, SANDBOX), 1)
        self.assertEqual(sorted(client.data), sorted(redis_of_the_lab().data.keys() - {KEY}))

    def test_a_counter_is_the_subscriptions_by_what_it_says_not_by_its_name(self):
        client = FakeRedis({"ratelimit:one:two:cost-rl": counter(SANDBOX), "ratelimit:" + SANDBOX + ":two:cost-rl": counter("sub-x")})
        refills.drop(client, self.refill, SANDBOX)
        self.assertEqual(list(client.data), ["ratelimit:" + SANDBOX + ":two:cost-rl"])

    def test_every_counter_of_the_subscription_goes(self):
        client = FakeRedis({"ratelimit:a:1:cost-rl": counter(SANDBOX), "ratelimit:b:1:cost-rl": counter(SANDBOX),
                            "ratelimit:c:1:cost-rl": counter("sub-care")})
        self.assertEqual(refills.drop(client, self.refill, SANDBOX), 2)
        self.assertEqual(list(client.data), ["ratelimit:c:1:cost-rl"])

    def test_nothing_spent_is_nothing_to_delete_and_no_delete_is_asked(self):
        client = FakeRedis({"ratelimit:ws-1:app-care:cost-rl": counter("sub-care")})
        self.assertEqual(refills.drop(client, self.refill, SANDBOX), 0)
        self.assertEqual(client.calls, [("scan", "ratelimit:*:cost-rl"), ("pipeline", 1)])
        self.assertEqual(refills.drop(FakeRedis(), self.refill, SANDBOX), 0)

    def test_the_keys_are_asked_by_the_pattern_of_the_counters_and_read_in_chunks(self):
        client = FakeRedis({f"ratelimit:ws:{n}:cost-rl": counter("sub-" + str(n)) for n in range(7)} | {KEY: counter(SANDBOX)})
        with mock.patch.object(refills, "CHUNK", 3):
            self.assertEqual(refills.drop(client, self.refill, SANDBOX), 1)
        self.assertEqual([call for call in client.calls if call[0] != "delete"],
                         [("scan", "ratelimit:*:cost-rl"), ("pipeline", 3), ("pipeline", 3), ("pipeline", 2)])
        self.assertEqual(client.calls[-1], ("delete", [KEY]))

    def test_a_key_that_is_not_a_hash_is_no_ones_counter_and_does_not_stop_the_others(self):
        client = FakeRedis({"ratelimit:a:1:cost-rl": "text", KEY: counter(SANDBOX)})
        self.assertEqual(refills.drop(client, self.refill, SANDBOX), 1)
        self.assertEqual(list(client.data), ["ratelimit:a:1:cost-rl"])

    def test_no_subscription_is_refused_and_nothing_is_read_or_deleted(self):
        """A counter that says no subscription (the field is empty or absent) must not be taken for the one asked for."""
        orphan = {"counter": "500", "limit": "120000", "reset": "1791506259034"}
        for subscription in (None, "", 0):
            with self.subTest(subscription=subscription):
                client = FakeRedis({"ratelimit:ws-1:no-owner:cost-rl": orphan, "ratelimit:ws-1:empty:cost-rl": counter(""),
                                    KEY: counter(SANDBOX)})
                before = dict(client.data)
                with self.assertRaisesRegex(common.SeedError, "^team-a has no subscription in Teams$"):
                    refills.drop(client, self.refill, subscription)
                self.assertEqual((client.data, client.calls), (before, []))

    def test_the_search_itself_refuses_a_missing_subscription(self):
        client = FakeRedis({"ratelimit:ws-1:no-owner:cost-rl": {"counter": "500"}, KEY: counter(SANDBOX)})
        for subscription in (None, ""):
            with self.subTest(subscription=subscription), self.assertRaises(common.SeedError):
                refills.owned(client, "ratelimit:*:cost-rl", subscription)
        self.assertEqual(client.calls, [], "it did not even look")


class Running(Seed):
    """run(): the log lines, and a seed that goes on whatever Redis does."""

    def setUp(self):
        super().setUp()
        self.refill = refills.Refill("hackathon", {"workspace": "Teams", "member": "team-a", "counters": "cost"})
        self.client = redis_of_the_lab()
        patch = mock.patch.object(refills, "connect", return_value=self.client)
        self.connect = patch.start()
        self.addCleanup(patch.stop)

    def test_the_member_has_their_whole_budget_again_and_the_seed_says_so(self):
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertNotIn(KEY, self.client.data)
        self.assertEqual(self.output(), "[refills] ✓ hackathon: the cost budget of team-a is whole again\n")
        self.assertTrue(self.client.closed)

    def test_a_second_run_finds_nothing_spent_and_says_so(self):
        refills.run([self.refill], {"team-a": SANDBOX})
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertEqual(self.output().splitlines()[-1], "[refills] hackathon: team-a has spent nothing in this period")

    def test_a_member_who_is_not_there_is_told_and_redis_is_not_even_reached(self):
        refills.run([self.refill], {"team-b": "sub-b"})
        self.assertEqual(self.output(), "[refills] ! hackathon: team-a is not a member of Teams yet: nothing to refill\n")
        self.connect.assert_not_called()

    def test_a_member_with_no_subscription_is_told_and_nothing_is_deleted(self):
        orphan = FakeRedis({"ratelimit:ws-1:no-owner:cost-rl": {"counter": "500"}, KEY: counter(SANDBOX)})
        self.connect.return_value = orphan
        refills.run([self.refill], {"team-a": None})
        self.assertIn("! Could not refill hackathon: team-a has no subscription in Teams. The budget is whole again when its period ends",
                      self.output())
        self.assertEqual(sorted(orphan.data), sorted(["ratelimit:ws-1:no-owner:cost-rl", KEY]))
        self.assertTrue(orphan.closed)

    def test_without_a_refill_redis_is_not_reached(self):
        refills.run([], {"team-a": SANDBOX})
        self.connect.assert_not_called()

    def test_a_redis_nobody_can_reach_is_told_and_the_seed_goes_on(self):
        self.connect.side_effect = ConnectionError("Error 111 connecting to redis:6379. Connection refused.")
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertIn("! Could not refill hackathon: ConnectionError: Error 111 connecting to redis:6379.", self.output())
        self.assertIn("The budget is whole again when its period ends", self.output())
        self.assertIn(KEY, self.client.data)

    def test_an_error_redis_answers_with_is_told_with_its_name_and_the_seed_goes_on(self):
        self.client.scan_iter = mock.Mock(side_effect=PermissionError("NOPERM this user has no permissions to run the 'scan' command"))
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertIn("! Could not refill hackathon: PermissionError: NOPERM this user has no permissions to run the 'scan' command.",
                      self.output())
        self.assertTrue(self.client.closed)

    def test_an_error_on_one_refill_does_not_keep_the_next_from_being_refilled(self):
        second = refills.Refill("care", {"workspace": "Teams", "member": "team-b", "counters": "cost"})
        real = refills.drop
        asked = []

        def drop(client, refill, subscription):
            asked.append(refill.id)
            if refill.id == "hackathon":
                raise OSError("connection reset")
            return real(client, refill, subscription)

        with mock.patch.object(refills, "drop", drop):
            refills.run([self.refill, second], {"team-a": SANDBOX, "team-b": "sub-care"})
        self.assertEqual(asked, ["hackathon", "care"])
        self.assertIn("! Could not refill hackathon: OSError: connection reset.", self.output())
        self.assertIn("✓ care: the cost budget of team-b is whole again", self.output())
        self.assertNotIn("ratelimit:ws-1:app-care:cost-rl", self.client.data)

    def test_a_missing_address_is_told_as_the_setting_that_is_missing(self):
        self.connect.side_effect = common.SeedError("REDIS_URL is not set")
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertIn("! Could not refill hackathon: REDIS_URL is not set. The budget", self.output())

    def test_the_password_is_never_in_what_is_told(self):
        self.env(REDIS_PASSWORD="s3cret-value")
        self.connect.side_effect = ConnectionError("Error connecting to redis://:s3cret-value@redis:6379")
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertNotIn("s3cret-value", self.output())
        self.assertIn("redis://:***@redis:6379", self.output())

    def test_what_is_told_ends_in_one_full_stop_not_two(self):
        self.connect.side_effect = ConnectionError("Error 111 connecting to redis:6379. Connection refused.")
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertIn("Connection refused. The budget is whole again when its period ends", self.output())

    def test_a_connection_that_fails_to_close_is_not_a_failure(self):
        self.client.close = mock.Mock(side_effect=OSError("already closed"))
        refills.run([self.refill], {"team-a": SANDBOX})
        self.assertIn("✓ hackathon", self.output())


class TheLibrary(unittest.TestCase):
    """What FakeRedis stands for. Where redis-py is installed (the seed image has it, and so does make setup) the real
    library has the calls a refill makes, with the arguments it passes; elsewhere this is skipped."""

    def setUp(self):
        try:
            import redis
        except ImportError:
            self.skipTest("redis-py is not installed: run make setup")
        self.redis = redis

    def test_the_calls_a_refill_makes_are_those_of_the_library(self):
        import inspect

        calls = {"scan_iter": self.redis.Redis.scan_iter, "pipeline": self.redis.Redis.pipeline, "delete": self.redis.Redis.delete,
                 "close": self.redis.Redis.close, "hget": self.redis.client.Pipeline.hget, "execute": self.redis.client.Pipeline.execute}
        self.assertLessEqual({"match", "count"}, set(inspect.signature(calls["scan_iter"]).parameters))
        self.assertIn("transaction", inspect.signature(calls["pipeline"]).parameters)
        self.assertIn("raise_on_error", inspect.signature(calls["execute"]).parameters)
        self.assertLessEqual({"name", "key"}, set(inspect.signature(calls["hget"]).parameters))

    def test_the_fake_takes_the_same_arguments_as_the_library(self):
        import inspect

        for fake, real in ((FakeRedis.scan_iter, self.redis.Redis.scan_iter), (FakeRedis.pipeline, self.redis.Redis.pipeline),
                           (testkit.FakeBatch.execute, self.redis.client.Pipeline.execute)):
            used = set(inspect.signature(fake).parameters) - {"self"}
            self.assertLessEqual(used, set(inspect.signature(real).parameters), fake.__qualname__)

    def test_a_connection_is_made_from_an_address_with_timeouts_and_a_password_as_the_refill_gives_them(self):
        client = self.redis.Redis.from_url("redis://redis:6379", decode_responses=True, socket_timeout=10, socket_connect_timeout=10,
                                           password="not-a-real-password")
        self.assertEqual(client.connection_pool.connection_kwargs["socket_timeout"], 10)
        client.close()


class Connecting(Seed):
    """connect() hands the library the lab's address and password and no more, and says what is missing."""

    def library(self):
        module = types.ModuleType("redis")
        module.Redis = mock.Mock()
        patch = mock.patch.dict(sys.modules, {"redis": module})
        patch.start()
        self.addCleanup(patch.stop)
        return module.Redis.from_url

    def test_the_address_and_the_password_of_the_core_redis_are_given_with_timeouts(self):
        from_url = self.library()
        self.env(REDIS_URL="redis://redis:6379", REDIS_PASSWORD="pw")
        refills.connect()
        from_url.assert_called_once_with("redis://redis:6379", decode_responses=True, socket_timeout=10,
                                         socket_connect_timeout=10, password="pw")

    def test_no_password_is_no_password(self):
        from_url = self.library()
        self.env(REDIS_URL="rediss://redis.example.org:6380", REDIS_PASSWORD="")
        refills.connect()
        self.assertNotIn("password", from_url.call_args.kwargs)

    def test_without_an_address_the_seed_is_told_what_is_missing_and_the_library_is_not_needed(self):
        with mock.patch.dict(sys.modules, {"redis": None}):
            with self.assertRaisesRegex(common.SeedError, "^REDIS_URL is not set$"):
                refills.connect()


class Main(Seed):
    """seed.main() with refills: a declaration that is wrong stops the seed before it changes anything, a refill nobody found
    a workspace for stops it after the modules ran."""

    def modules(self):
        return mock.patch.multiple(seed, AFTER_AM=(), openfga=mock.DEFAULT, am=mock.DEFAULT)

    def test_a_declaration_that_is_not_understood_stops_the_seed_before_any_call(self):
        self.seed_files({"refills.yaml": "a: {workspace: Teams, member: team-a, counters: tokens}\n"})
        with self.modules() as modules:
            self.assertEqual(self.main(), 1)
        self.assertIn("✗ refills.yaml: a: counters is one of cost", self.output())
        modules["openfga"].seed.assert_not_called()
        self.assertEqual(self.platform.calls, [])

    def test_a_refill_no_module_found_a_workspace_for_stops_the_seed(self):
        self.seed_files({"refills.yaml": "a: {workspace: Teams, member: team-a, counters: cost}\n"})
        with self.modules():
            self.assertEqual(self.main(), 1)
        self.assertIn("✗ refills.yaml: a names AI workspace 'Teams', which no seed file has", self.output())
        self.assertNotIn("Playground ready", self.output())

    def test_a_seed_without_refills_says_nothing_about_them(self):
        with self.modules():
            self.assertEqual(self.main(), 0)
        self.assertNotIn("refills", self.output())


class AcmeBakery(Seed):
    """The refill of Acme Bakery against its real files: it names the Hackathon sandbox the lesson empties."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(common, "SEED_DIR", ACME_SEED)
        patch.start()
        self.addCleanup(patch.stop)
        self.workspace = common.load(ACME_SEED / "aim" / "workspaces" / "acme-bakery-teams.yaml")

    def test_the_sandbox_of_the_hackathon_is_the_one_refill(self):
        [refill] = refills.declared()
        self.assertEqual((refill.id, refill.workspace, refill.member, refill.counters),
                         ("hackathon", "Acme Bakery Teams", "team-hackathon", "cost"))

    def test_the_workspace_file_has_that_workspace_and_that_member_in_the_budget_the_lesson_empties(self):
        [refill] = refills.declared()
        self.assertEqual(self.workspace["name"], refill.workspace)
        [member] = [m for m in self.workspace["members"] if m["sourceId"] == refill.member]
        [budget] = [b for b in self.workspace["budgets"] if b["name"] == member["budget"]]
        self.assertEqual((budget["name"], budget["dollars"], budget["period"]), ("Hackathon sandbox", 0.12, "HOUR"))
        self.assertEqual([r.id for r in refills.of(refill.workspace, [m["sourceId"] for m in self.workspace["members"]])], ["hackathon"])
        refills.finish()

    def test_only_the_sandbox_is_refilled_not_the_teams_whose_budget_a_day_covers(self):
        refilled = {refill.member for refill in refills.declared()}
        self.assertEqual(refilled, {"team-hackathon"})

    def test_the_seed_service_gives_the_seed_the_core_redis_it_refills_in(self):
        environment = seed_environment(seed_service(ACME_BAKERY / "compose.yml"))
        self.assertEqual(environment["REDIS_URL"], "redis://redis:6379")
        self.assertIn("REDIS_PASSWORD", environment)

    def test_the_seeder_installs_the_library_the_refill_imports(self):
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8").split()
        self.assertTrue(any(line.startswith("redis==") for line in requirements))


if __name__ == "__main__":
    unittest.main()
