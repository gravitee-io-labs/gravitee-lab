"""places: where a playground can deploy, one place for each environment of each registered platform, from what the
platforms say, kept for a minute and read in the background; the gateway that runs here; the settings of a seed and the
check before a deploy. Against the fake Cloud on a free port, in a temporary project folder. No network. A token is
never in an answer, an error or the record, and a test never prints one."""

import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from lab_ui.gravitee import cloud, platforms, places
from lab_ui.docker import envfiles
from lab_ui import paths
from lab_ui.catalogs import trust

from .. import Case, fake_cloud

from . import test_platforms
from .test_platforms import ACCOUNT, ADDON_ENV, DEV, GATEWAY, PROJECT, TEST

NAME = "My Account"
KEY = "PLATFORM_MY_ACCOUNT_TOKEN"
DEV_KEY = "PLATFORM_MY_ACCOUNT_ENV_FAKE_ENV_DEV_TOKEN"
DEVELOPMENT = f"Development ({NAME})"
BOTH = ["localhost:8090", "apim-hybrid-gateway:8082"]
PLAYGROUND = {"id": "hotels", "name": "Hotels"}   # the playground a seed belongs to: the policy found nothing against its seed
REPOSITORY = Path(__file__).resolve().parents[3]
# The address of the platform's gateway, which the seeder looks at with the API key of an API of the environment (its own
# session, never the Cloud Token's), so the Lab gives it only when it knows it
NOT_TOKEN_ADDRESS = {"GATEWAY_INTERNAL_URL"}


def _names_read():
    """The settings of the contract that the modules of the seeder which read the Cloud Token (APIM_TOKEN) read, by the
    source of the seeder itself."""
    lock = {line.split()[1] for line in (REPOSITORY / "contract.lock").read_text(encoding="utf-8").splitlines()
            if line.startswith("seed.setting ")}
    names = set()
    for module in sorted((REPOSITORY / "seeder" / "lab_seeder").glob("*.py")):
        text = module.read_text(encoding="utf-8")
        if "APIM_TOKEN" in text:
            names |= set(re.findall(r"""(?:setting|getenv)\(\s*["']([A-Z][A-Z0-9_]*)["']""", text))
    return names & lock


def token_destinations():
    """The settings the official seeder takes an address or a path from, that it sends the Cloud Token to or joins to such an
    address: the contract's settings that end in _URL or _PATH, in the modules that read the token, but the gateway's."""
    return {name for name in _names_read() if name.endswith(("_URL", "_PATH"))} - NOT_TOKEN_ADDRESS


class Places(test_platforms.Platforms):
    """A platform registered as My Account, and nothing read of it yet."""

    def setUp(self):
        super().setUp()
        self.patch(places, "_cache", {})
        self.patch(places, "_reading", {})
        self.addCleanup(self.settle)   # a read that goes on would write what it finds in the next test's cache
        self.add(NAME)

    def settle(self):
        """Waits for the reads that are going on in the background."""
        for thread in list(places._reading.values()):
            thread.join(10)
            self.assertFalse(thread.is_alive(), "a read did not end")

    def read(self, project=PROJECT):
        """The places, once the platforms have said what they hold."""
        places.places(project)
        self.settle()
        return places.places(project)

    def place(self, environment=DEV, uid="my-account"):
        self.read()
        return places.place(PROJECT, f"{uid}/{environment}")

    def keep(self, token=fake_cloud.CLOUD_TOKEN, environment=DEV, uid="my-account"):
        platforms.set_cloud_token(uid, environment, token)

    def ready(self, environment=DEV):
        """A gateway and a Cloud Token in an environment: the place is ready."""
        gateway = self.create(environment, "lab", platform="my-account")["gateway"]
        self.keep(fake_cloud.cloud_token(environment), environment)
        return gateway

    def blocked(self):
        """Reads of a platform that wait until the test lets them go: the number of reads that began."""
        release, began = threading.Event(), []
        real = platforms.fetch

        def slow(record):
            began.append(record["id"])
            release.wait(10)
            return real(record)
        self.patch(platforms, "fetch", slow)
        self.addCleanup(release.set)
        return release, began


class Reading(Places):
    def test_nothing_registered_asks_nothing_and_leaves_nothing(self):
        """The Lab with no platform is as it was: no place, no request, no thread, no file."""
        platforms.remove("my-account")
        (self.root / platforms.FILE).unlink()
        asked, folders = len(self.fake.state.asked), sorted(path.name for path in self.root.iterdir())
        self.assertEqual((places.places(PROJECT), places.serving(PROJECT), places.place(PROJECT, "a/b")),
                         ([], None, None))
        self.assertEqual((places._reading, places._cache), ({}, {}))
        self.assertEqual((len(self.fake.state.asked), sorted(path.name for path in self.root.iterdir())),
                         (asked, folders))

    def test_a_token_in_the_add_on_is_no_place_without_a_platform(self):
        platforms.remove("my-account")
        self.write(ADDON_ENV, "HYBRID_CLOUD_TOKEN=" + fake_cloud.GATEWAY_TOKEN + "\n")
        asked = len(self.fake.state.asked)
        self.assertEqual((places.places(PROJECT), places.serving(PROJECT)), ([], None))
        self.assertEqual((len(self.fake.state.asked), places._reading), (asked, {}))

    def test_a_platform_not_read_yet_is_loading_and_nothing_is_waited_for(self):
        release, began = self.blocked()
        started = time.monotonic()
        found = places.places(PROJECT)
        self.assertLess(time.monotonic() - started, 2, "the call waited for the platform")
        self.assertEqual(found, [{"id": "my-account", "name": NAME, "platform": {
            "id": "my-account", "name": NAME, "kind": "cloud"}, "environment": None, "state": "unusable",
            "missing": None, "reason": "Loading", "token": None, "gateway": None, "capabilities": None,
            "cannot": {}}])
        self.assertEqual(places.place(PROJECT, f"my-account/{DEV}")["reason"], "Loading")
        self.assertEqual(places.serving(PROJECT), None)
        release.set()
        self.settle()
        self.assertEqual(places.place(PROJECT, f"my-account/{DEV}")["state"], "missing")

    def test_a_platform_is_read_by_one_thread_at_a_time(self):
        release, began = self.blocked()
        for _ in range(5):
            places.places(PROJECT)
        self.assertEqual(len(places._reading), 1)
        release.set()
        self.settle()
        self.assertEqual(began, ["my-account"])

    def test_what_was_read_is_served_for_a_minute_without_a_request(self):
        self.read()
        asked = len(self.fake.state.asked)
        for _ in range(3):
            places.places(PROJECT)
            places.serving(PROJECT)
            places.place(PROJECT, f"my-account/{DEV}")
        self.assertEqual((len(self.fake.state.asked), places._reading), (asked, {}))
        self.assertEqual(places.TTL, 60)

    def test_a_read_that_is_old_is_made_again_in_the_background_and_the_old_one_is_served(self):
        self.read()
        self.patch(places, "TTL", 0)
        release, began = self.blocked()
        self.assertEqual([p["state"] for p in places.places(PROJECT)], ["missing"] * 3, "the old read, at once")
        self.assertEqual(len(places._reading), 1)
        release.set()
        self.settle()
        self.assertEqual(began, ["my-account"])

    def test_an_action_on_a_platform_is_seen_by_the_next_call_when_it_is_forgotten(self):
        self.read()
        self.create(DEV, "lab", platform="my-account")
        self.assertEqual(places.place(PROJECT, f"my-account/{DEV}")["missing"], "No hybrid gateway", "served as read")
        places.invalidate("my-account")
        self.assertEqual(places.place(PROJECT, f"my-account/{DEV}")["missing"], "No Cloud Token")

    def test_the_local_things_are_not_served_from_a_minute_ago(self):
        """A Cloud Token kept, or a gateway swapped, shows at once: only what the platform said is kept."""
        self.create(DEV, "lab", platform="my-account")
        self.read()
        self.assertEqual(self.place()["token"], {"state": "unset"})
        self.keep()
        self.assertEqual(self.place()["state"], "ready")
        envfiles.write(".env", {DEV_KEY: None})
        self.assertEqual(self.place()["missing"], "No Cloud Token")

    def test_a_platform_that_is_gone_is_forgotten(self):
        self.read()
        platforms.remove("my-account")
        places.invalidate("my-account")
        self.assertEqual((places.places(PROJECT), places._cache), ([], {}))

    def test_a_read_that_fails_is_the_word_of_the_platform_and_ends(self):
        self.patch(platforms, "fetch", side_effect=RuntimeError("something odd"))
        found = self.read()
        self.assertEqual([(p["state"], p["reason"]) for p in found], [("unusable", "Unreachable")])
        self.assertEqual(places._reading, {})
        self.assertEqual(places._cache["my-account"][1]["message"], "RuntimeError", "its type, not its message")

    def test_the_first_read_does_not_ask_the_platform_to_the_caller(self):
        """The state of the Lab calls places() for every request: it must not ask the Cloud itself."""
        release, began = self.blocked()
        asked = len(self.fake.state.asked)
        places.places(PROJECT)
        self.assertEqual(len(self.fake.state.asked), asked)


class States(Places):
    def test_the_places_of_a_platform_are_its_environments_in_the_order_of_the_account(self):
        found = self.read()
        self.assertEqual([(p["id"], p["name"]) for p in found], [
            ("my-account/fake-env-dev", DEVELOPMENT), ("my-account/fake-env-test", f"Test ({NAME})"),
            ("my-account/fake-env-prod", f"Production ({NAME})")])
        self.assertEqual(found[0]["platform"], {"id": "my-account", "name": NAME, "kind": "cloud"})
        self.assertEqual(found[0]["environment"],
                         {"id": DEV, "name": "Development", "organization": "fake-organization"})
        self.assertHidden(found)

    def test_nothing_in_an_environment_lacks_a_hybrid_gateway_first(self):
        # With no gateway at all it is not set up: what it runs is not known
        found = self.place()
        self.assertEqual((found["state"], found["missing"], found["reason"], found["token"], found["gateway"],
                          found["capabilities"]), ("missing", "No hybrid gateway",
                                                   f"Set up a gateway in {DEVELOPMENT} first", {"state": "unset"}, None,
                                                   None))

    def test_one_that_has_only_gateways_the_lab_cannot_run_lacks_one_of_its_own(self):
        made = self.create(DEV, "lab", platform="my-account")["gateway"]
        envfiles.write(".env", {test_platforms.gateway_key(made["id"], "MY_ACCOUNT"): None})   # as if made in the console
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": None})
        found = self.place()
        self.assertEqual((found["missing"], found["reason"], found["capabilities"]["products"]),
                         ("No hybrid gateway", f"Create a hybrid gateway for {DEVELOPMENT}", {"APIM": "4.12.20"}))

    def test_then_a_cloud_token(self):
        self.create(DEV, "lab", platform="my-account")
        found = self.place()
        self.assertEqual((found["state"], found["missing"], found["reason"]),
                         ("missing", "No Cloud Token", f"Cloud Token for {DEVELOPMENT} is not set"))
        self.assertEqual(self.place(TEST)["missing"], "No hybrid gateway", "the other environments are as they were")

    def test_a_gateway_and_a_cloud_token_make_a_place_ready(self):
        gateway = self.ready()
        found = self.place()
        self.assertEqual((found["state"], found["missing"], found["reason"]), ("ready", None, ""))
        self.assertEqual(found["token"], {"state": "valid", "expires": cloud.claims(
            envfiles.read(".env")[DEV_KEY])["expiry"]})
        self.assertEqual(found["gateway"], {"id": gateway["id"], "name": "lab", "state": "DEPLOYED", "here": True,
                                            "kept": True, "connection": "CONNECTED"})
        self.assertHidden(found)

    def test_a_gateway_that_is_not_connected_does_not_block_a_place(self):
        self.fake.state.connect_after = 3600
        self.ready()
        found = self.place()
        self.assertEqual((found["state"], found["gateway"]["connection"]), ("ready", "NOT_CONNECTED"))

    def test_a_cloud_token_that_expired_or_was_refused(self):
        self.create(DEV, "lab", platform="my-account")
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        found = self.place()
        self.assertEqual((found["state"], found["missing"], found["reason"], found["token"]["state"]), (
            "missing", "Cloud Token expired", f"Cloud Token for {DEVELOPMENT} has expired", "expired"))
        self.keep()
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)
        with self.assertRaises(cloud.CloudError):
            platforms.prove("my-account", DEV)
        found = self.place()
        self.assertEqual((found["state"], found["missing"], found["reason"], found["token"]["state"]), (
            "missing", "Cloud Token refused", f"Gravitee Cloud refuses the Cloud Token of {DEVELOPMENT}", "refused"))

    def test_a_gateway_of_another_environment_is_not_the_one_of_this(self):
        self.create(TEST, "lab", platform="my-account")
        self.assertEqual((self.place(DEV)["missing"], self.place(TEST)["missing"]),
                         ("No hybrid gateway", "No Cloud Token"))

    def test_the_gateway_that_runs_here_is_the_one_of_its_environment(self):
        first = self.create(DEV, "first", platform="my-account")["gateway"]
        second = self.create(DEV, "second", platform="my-account")["gateway"]
        self.assertEqual(self.place()["gateway"]["id"], first["id"])
        platforms.swap_gateway(PROJECT, "my-account", second["id"])
        found = self.place()["gateway"]
        self.assertEqual((found["id"], found["here"]), (second["id"], True))

    def test_without_one_that_runs_here_it_is_the_first_whose_token_is_kept(self):
        first = self.create(TEST, "elsewhere", platform="my-account")["gateway"]   # runs here, in Test
        lost = self.create(DEV, "lost", platform="my-account")["gateway"]
        kept = self.create(DEV, "kept", platform="my-account")["gateway"]
        envfiles.write(".env", {test_platforms.gateway_key(lost["id"], "MY_ACCOUNT"): None})
        self.assertEqual(self.place(DEV)["gateway"]["id"], kept["id"], "not the one whose token is not kept")
        self.assertEqual((self.place(DEV)["gateway"]["here"], self.place(TEST)["gateway"]["id"]), (False, first["id"]))
        envfiles.write(".env", {test_platforms.gateway_key(kept["id"], "MY_ACCOUNT"): None})
        self.assertEqual(self.place(DEV)["missing"], "No hybrid gateway", "none is kept")

    def test_only_a_hybrid_gateway_is_one(self):
        gateways = [{"id": "h", "environmentId": "e", "type": "HOSTED", "here": False, "kept": True},
                    {"id": "k", "environmentId": "e", "type": "HYBRID", "here": False, "kept": True},
                    {"id": "n", "environmentId": "e", "type": "HYBRID", "here": False, "kept": False},
                    {"id": "o", "environmentId": "other", "type": "HYBRID", "here": True, "kept": True}]
        self.assertEqual(places._gateway_of("e", gateways)["id"], "k")
        self.assertIsNone(places._gateway_of("none", gateways))

    def test_the_platforms_come_by_name_and_their_environments_as_the_account_gives_them(self):
        self.second("Client Lab")
        self.assertEqual([p["id"] for p in self.read()], [
            "client-lab/fake-client-dev", "client-lab/fake-client-test",
            "my-account/fake-env-dev", "my-account/fake-env-test", "my-account/fake-env-prod"])
        self.assertEqual(places.place(PROJECT, "client-lab/fake-client-test")["name"], "Test (Client Lab)")

    def test_the_tokens_of_the_other_account_are_its_own(self):
        self.second("Client Lab")
        self.keep(fake_cloud.SECOND_CLOUD_TOKEN, "fake-client-test", "client-lab")
        self.assertEqual(self.place("fake-client-test", "client-lab")["missing"], "No hybrid gateway")
        self.assertEqual(self.place(TEST)["token"], {"state": "unset"})

    def test_the_things_a_picker_needs_are_all_there(self):
        self.ready()
        for found in self.read():
            self.assertEqual(sorted(found), ["cannot", "capabilities", "environment", "gateway", "id", "missing", "name",
                                             "platform", "reason", "state", "token"])
            self.assertIn(found["state"], ("ready", "missing"))


def asking(**more):
    """What a playground asks of a platform (project.asks): API Management, and what `more` says."""
    return {"deploys": True, "products": ["API Management"], "versions": [], "fills": ["API Management"],
            "plugins": False, "changes": False, "uses": False, "more": False, **more}


# Hotels goes where API Management runs, Later needs its 4.13, Bank needs Access Management and changes the gateway here
JUDGED = {**PROJECT, "groups": [{"name": "API Management", "product": "apim"}], "products": {}, "playgrounds": {
    "hotels": {"id": "hotels", "asks": asking()},
    "later": {"id": "later", "asks": asking(versions=[["API Management", "4.13.0"]])},
    "bank": {"id": "bank", "asks": asking(products=["Access Management", "API Management"], changes=True)}}}
CHANGES = "Changes this machine's Management API"


class Compatibility(Places):
    """What each place says of each playground: judged against what its environment runs, as its gateways said it in
    the read the places already hold; nothing is asked for it."""

    def at(self, environment=DEV, project=JUDGED):
        places.places(project)
        self.settle()
        return places.place(project, f"my-account/{environment}")

    def test_an_environment_whose_gateway_runs_api_management(self):
        self.create(DEV, "lab", platform="my-account")
        found = self.at()
        self.assertEqual(found["capabilities"], {"products": {"API Management": "4.12.20"}, "plugins": False,
                                                 "detail": "", "installed": None, "license": None})
        self.assertEqual(found["cannot"], {"bank": "Needs Access Management", "later": "Needs API Management 4.13"})

    def test_a_seed_of_its_own_is_off_for_every_place_and_says_so_first(self):
        # Hotels brings its own seed code, Later reads a token in its seed data, Bank would also need Access Management
        notes = {"hotels": [trust.SEED_OWN], "later": [trust.SEED_TOKEN], "bank": ["Another note", trust.SEED_OWN]}
        project = {**JUDGED, "playgrounds": {pid: {**one, "verdict": {"notes": notes[pid]}}
                                             for pid, one in JUDGED["playgrounds"].items()}}
        self.create(DEV, "lab", platform="my-account")
        self.assertEqual(self.at(project=project)["cannot"], {"hotels": "Brings its own seed code",
                                                              "later": "Seed data reads a token",
                                                              "bank": "Brings its own seed code"})
        # A place that is not usable says the same: the reason does not depend on what the environment runs
        envfiles.write(".env", {KEY: "rotated-since"})
        self.assertEqual(self.at(project=project)["cannot"]["hotels"], "Brings its own seed code")

    def test_a_later_version_there_lets_the_playground_that_needs_it_go(self):
        self.fake.state.version = "4.13.2"
        self.create(DEV, "lab", platform="my-account")
        self.assertEqual(self.at()["cannot"], {"bank": "Needs Access Management"})

    def test_an_environment_without_a_gateway_has_nothing_to_compare(self):
        # What does not depend on the environment is all that is said; the place says to set up a gateway
        self.create(TEST, "lab", platform="my-account")
        found = self.at(DEV)
        self.assertEqual((found["capabilities"], found["cannot"], found["reason"]),
                         (None, {"bank": CHANGES}, f"Set up a gateway in {DEVELOPMENT} first"))

    def test_a_platform_that_cannot_be_read_keeps_its_own_state(self):
        self.create(DEV, "lab", platform="my-account")
        envfiles.write(".env", {KEY: "rotated-since"})
        found = self.at()
        self.assertEqual((found["state"], found["reason"], found["capabilities"], found["cannot"]),
                         ("unusable", "Invalid token", None, {"bank": CHANGES}))

    def test_the_places_are_judged_from_what_was_read(self):
        self.create(DEV, "lab", platform="my-account")
        self.at()
        asked = len(self.fake.state.asked)
        for _ in range(3):
            places.places(JUDGED)
        self.assertEqual(len(self.fake.state.asked), asked, "a place judged its playgrounds by asking the platform")


class Unusable(Places):
    def only(self):
        (found,) = self.read()
        return found

    def test_an_account_token_that_the_cloud_refuses(self):
        envfiles.write(".env", {KEY: "rotated-since"})
        self.assertEqual(self.only(), {
            "id": "my-account", "name": NAME, "platform": {"id": "my-account", "name": NAME, "kind": "cloud"},
            "environment": None, "state": "unusable", "missing": None, "reason": "Invalid token", "token": None,
            "gateway": None, "capabilities": None, "cannot": {}})

    def test_a_platform_that_does_not_answer(self):
        self.fake.shutdown()
        self.fake.server_close()
        self.assertEqual((self.only()["state"], self.only()["reason"]), ("unusable", "Unreachable"))

    def test_an_account_token_that_is_not_kept(self):
        envfiles.write(".env", {KEY: None})
        self.assertEqual(self.only()["reason"], "Invalid token")

    def test_a_place_of_a_platform_that_cannot_be_read_answers_as_the_platform(self):
        envfiles.write(".env", {KEY: "rotated-since"})
        self.read()
        for place_id in (f"my-account/{DEV}", "my-account/anything", "my-account"):
            self.assertEqual(places.place(PROJECT, place_id)["state"], "unusable", place_id)

    def test_what_is_not_a_place(self):
        self.read()
        for place_id in ("nope/x", "nope", "my-account/nope", "", None, 4):
            self.assertIsNone(places.place(PROJECT, place_id), repr(place_id))

    def test_the_places_come_back_with_a_token_the_cloud_accepts(self):
        envfiles.write(".env", {KEY: "rotated-since"})
        self.assertEqual(self.only()["state"], "unusable")
        platforms.replace_account_token("my-account", fake_cloud.TOKEN)
        places.invalidate("my-account")
        self.assertEqual([p["state"] for p in places.places(PROJECT)], ["missing"] * 3)

    def test_one_platform_that_cannot_be_read_leaves_the_other_places_alone(self):
        self.second("Client Lab")
        envfiles.write(".env", {"PLATFORM_CLIENT_LAB_TOKEN": "rotated-since"})
        found = self.read()
        self.assertEqual([(p["id"], p["state"]) for p in found], [
            ("client-lab", "unusable"), ("my-account/fake-env-dev", "missing"), ("my-account/fake-env-test", "missing"),
            ("my-account/fake-env-prod", "missing")])


class Serving(Places):
    def test_nothing_runs_here_without_a_token(self):
        self.read()
        self.assertIsNone(places.serving(PROJECT))
        self.create(DEV, "lab", platform="my-account")
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": None})
        self.assertIsNone(places.serving(PROJECT))
        self.assertFalse((self.root / ADDON_ENV).exists())

    def test_the_gateway_that_runs_here_serves_its_environment(self):
        gateway = self.create(TEST, "lab", platform="my-account")["gateway"]
        self.read()
        self.assertEqual(places.serving(PROJECT), {
            "platform": {"id": "my-account", "name": NAME, "kind": "cloud"},
            "environment": {"id": TEST, "name": "Test", "organization": "fake-organization"},
            "gateway": {"id": gateway["id"], "name": "lab"}})

    def test_it_follows_the_swap(self):
        self.create(DEV, "first", platform="my-account")
        second = self.create(TEST, "second", platform="my-account")["gateway"]
        self.read()
        self.assertEqual(places.serving(PROJECT)["environment"]["id"], DEV)
        platforms.swap_gateway(PROJECT, "my-account", second["id"])
        self.assertEqual(places.serving(PROJECT)["environment"]["id"], TEST)
        self.assertEqual(places.serving(PROJECT)["gateway"]["id"], second["id"])

    def test_it_is_none_when_the_platform_has_not_said_yet(self):
        self.create(DEV, "lab", platform="my-account")
        release, began = self.blocked()
        self.assertIsNone(places.serving(PROJECT))

    def test_it_is_none_for_a_gateway_that_no_platform_lists(self):
        """A token pasted by hand, or the gateway deleted in the console."""
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        self.fake.state.gateways.pop(gateway["id"])
        places.invalidate("my-account")
        self.assertIsNone(places.serving(PROJECT))
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": "not-a-token"})
        self.assertIsNone(places.serving(PROJECT))

    def test_it_is_none_after_the_gateway_is_deleted(self):
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        self.read()
        self.assertTrue(places.serving(PROJECT))
        platforms.delete_gateway(PROJECT, "my-account", gateway["id"])
        places.invalidate("my-account")
        self.assertIsNone(places.serving(PROJECT))


class SeedEnv(Places):
    def setUp(self):
        super().setUp()
        self.keep()

    def test_what_the_seeder_is_told(self):
        asked = len(self.fake.state.asked)
        self.assertEqual(places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND), {
            "APIM_MANAGEMENT_URL": self.url, "AIM_AUTOMATION_URL": self.url, "AIM_GAMMA_URL": self.url,
            "APIM_MANAGEMENT_PATH": "/apim/rest", "APIM_PORTAL_PATH": "/portal", "APIM_TOKEN": fake_cloud.CLOUD_TOKEN,
            "APIM_ORG": "fake-organization", "APIM_ENV": DEV, "APIM_GATEWAY_URL": "http://localhost:8090",
            "GATEWAY_INTERNAL_URL": "http://apim-hybrid-gateway:8082", "STATE_DIR": f"/state/my-account/{DEV}"})
        self.assertEqual(len(self.fake.state.asked), asked, "a seed's settings are made of what is kept")

    def test_the_only_secret_is_the_cloud_token_and_it_is_only_there(self):
        env = places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)
        self.assertEqual([key for key, value in env.items() if value == fake_cloud.CLOUD_TOKEN], ["APIM_TOKEN"])
        self.assertEqual(env["STATE_DIR"].count(fake_cloud.CLOUD_TOKEN), 0)
        self.read()
        self.assertHidden(places.places(PROJECT), places.serving(PROJECT), self.text(platforms.FILE))

    def test_the_organization_and_the_environment_are_the_tokens(self):
        self.keep(fake_cloud.CLOUD_TOKEN_TEST, TEST)
        env = places.seed_env(PROJECT, f"my-account/{TEST}", PLAYGROUND)
        self.assertEqual((env["APIM_ORG"], env["APIM_ENV"], env["STATE_DIR"]), (
            "fake-organization", TEST, f"/state/my-account/{TEST}"))

    def test_no_address_the_seeder_sends_the_token_to_is_the_authors(self):
        # The official seeder sends the token to the management API and to the two addresses it defaults to, and joins the
        # path of the management API and of the Portal API to them: the author's own value of any of these, in the
        # environment of the seed service, is replaced by the name passed with the command. They are not listed here but
        # read from the seeder: whatever address or path a module that holds the token reads is one, so that a setting
        # the seeder starts to read cannot go unpinned without this test saying so.
        env = places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)
        sent = token_destinations()
        self.assertLessEqual({"APIM_MANAGEMENT_URL", "AIM_AUTOMATION_URL", "AIM_GAMMA_URL", "APIM_MANAGEMENT_PATH",
                              "APIM_PORTAL_PATH"}, sent, "the scan of the seeder finds what the token is sent to")
        self.assertEqual(sorted(sent - set(env)), [], "a name the Lab does not give is the author's")
        self.assertEqual({env[name] for name in sent if name.endswith("_URL")}, {self.url})
        for name in sorted(sent):
            if name.endswith("_PATH"):   # joined after an address: one that begins with '@' or '//' names another host
                self.assertRegex(env[name], r"^/[a-z][a-z/]*$", name)
        self.assertEqual(env["APIM_PORTAL_PATH"], places.PINNED["APIM_PORTAL_PATH"])
        record = {**platforms.read()[0], "url": "https://eu.cloud.gravitee.io"}
        self.write(platforms.FILE, json.dumps([record]))
        env = places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)
        self.assertEqual({env[name] for name in sent if name.endswith("_URL")}, {"https://eu.cloudgate.gravitee.io"})

    def test_a_proxy_or_a_trust_store_of_the_author_is_emptied_and_never_given_a_value(self):
        env = places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)
        self.assertEqual(set(places.CLEARED) & set(env), set())
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE",
                     "SSL_CERT_DIR"):
            self.assertIn(name, places.CLEARED)
            if name.endswith("_PROXY"):
                self.assertIn(name.lower(), places.CLEARED)

    def test_emptied_they_leave_nothing_for_the_http_library_to_honor(self):
        # The way the seeder's library (requests) takes a proxy from the environment is urllib's: a name whose value is empty
        # is no proxy, in either case, for every scheme the seeder talks
        proxies = {name: "http://author.invalid:3128" for name in places.CLEARED if name.lower().endswith("_proxy")}
        # Whatever proxy this machine's own environment names is not the test's business
        mine = {name: value for name, value in os.environ.items() if not name.lower().endswith("_proxy")
                and name != "REQUEST_METHOD"}
        with mock.patch.dict(os.environ, {**mine, **proxies}, clear=True):
            self.assertEqual(set(urllib.request.getproxies_environment()), {"http", "https", "all"})
        with mock.patch.dict(os.environ, {**mine, **proxies, **{name: "" for name in places.CLEARED}}, clear=True):
            self.assertEqual(urllib.request.getproxies_environment(), {})

    def test_a_seed_of_its_own_or_one_that_reads_a_token_gets_none(self):
        # Before the token is read: it never is held for a seed that must not have it
        read = self.patch(platforms, "cloud_token", side_effect=AssertionError("the token was read"))
        for notes, why in (([trust.SEED_OWN], "Brings its own seed code"), ([trust.SEED_TOKEN], "Seed data reads a token"),
                           ([trust.SEED_TOKEN, trust.SEED_OWN], "Brings its own seed code"),
                           (["Some of its addresses are not on this machine, so they are not opened", trust.SEED_TOKEN],
                            "Seed data reads a token")):
            with self.subTest(notes=notes), self.assertRaisesRegex(ValueError, f"^Bank cannot go to a place: {why}$"):
                places.seed_env(PROJECT, f"my-account/{DEV}", {"name": "Bank", "verdict": {"notes": notes}})
        read.assert_not_called()

    def test_a_playground_the_policy_found_nothing_against_in_its_seed_gets_the_token(self):
        for playground in ({"name": "Bank"}, {"name": "Bank", "verdict": None}, {"name": "Bank", "verdict": {"notes": []}},
                           {"name": "Bank", "verdict": {"notes": ["Its terminal is not offered by this catalog"]}}):
            with self.subTest(playground=playground):
                self.assertEqual(places.seed_env(PROJECT, f"my-account/{DEV}", playground)["APIM_TOKEN"],
                                 fake_cloud.CLOUD_TOKEN)

    def test_the_gateway_addresses_are_those_of_the_add_on(self):
        for domains, expected in [(BOTH[:1], {"APIM_GATEWAY_URL": "http://localhost:8090"}), ([], {}),
                                  (["localhost:8091", "gw:80", "more:1"], {"APIM_GATEWAY_URL": "http://localhost:8091",
                                                                          "GATEWAY_INTERNAL_URL": "http://gw:80"})]:
            project = {**PROJECT, "addons": {"hybrid": {**PROJECT["addons"]["hybrid"],
                                                        "gateway": {**GATEWAY, "domains": domains}}}}
            with self.subTest(domains=domains):
                env = places.seed_env(project, f"my-account/{DEV}", PLAYGROUND)
                self.assertEqual({k: v for k, v in env.items() if k.endswith("_URL") and "GATE" in k}, expected)

    def test_without_the_add_on_there_are_no_gateway_addresses(self):
        env = places.seed_env({"addons": {}}, f"my-account/{DEV}", PLAYGROUND)
        self.assertFalse({"APIM_GATEWAY_URL", "GATEWAY_INTERNAL_URL"} & set(env))

    def test_the_gate_of_gravitee_cloud_is_by_region(self):
        """The platform's address ends so: the token's cpg names the Gate. (The record is edited, as the fake's address
        cannot be a Cloud's.)"""
        record = {**platforms.read()[0], "url": "https://eu.cloud.gravitee.io"}
        self.write(platforms.FILE, json.dumps([record]))
        self.assertEqual(places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)["APIM_MANAGEMENT_URL"],
                         "https://eu.cloudgate.gravitee.io")

    def test_what_cannot_be_seeded(self):
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        with self.assertRaisesRegex(cloud.CloudError, "^This Cloud Token has expired$"):
            places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)
        for place_id, error, words in [
                (f"my-account/{TEST}", ValueError, "^No Cloud Token is kept for this environment$"),
                ("my-account/", ValueError, "^Not an id of Gravitee Cloud$"),
                ("my-account/../x", ValueError, "^Not an id of Gravitee Cloud$"),
                ("my-account/a/b", ValueError, "^Not an id of Gravitee Cloud$"),
                ("my-account", ValueError, "^Not an id of Gravitee Cloud$"),
                ("nope/x", paths.NotFound, "^No platform nope$")]:
            with self.subTest(place=place_id), self.assertRaisesRegex(error, words):
                places.seed_env(PROJECT, place_id, PLAYGROUND)

    def test_a_token_kept_for_another_environment_is_not_used(self):
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_TEST})
        with self.assertRaisesRegex(ValueError, "is not one for it"):
            places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)

    def test_an_organization_that_is_no_segment_is_not_passed_to_a_seed(self):
        for org in ("../x", "a b", "", "a/b"):
            token = fake_cloud.jwt({"org": org, "envs": [DEV], "cpg": "eu", "scopes": ["automation", "rest"],
                                    "aud": "CloudGate", "exp": int(time.time()) + 3600})
            envfiles.write(".env", {DEV_KEY: token})
            with self.subTest(org=org), self.assertRaisesRegex(ValueError, "is not one for it"):
                places.seed_env(PROJECT, f"my-account/{DEV}", PLAYGROUND)


class Verify(Places):
    def test_a_place_that_is_ready_is_asked_live(self):
        self.ready()
        self.read()
        before = list(self.fake.state.asked)
        found = places.verify(PROJECT, f"my-account/{DEV}")
        self.assertEqual((found["id"], found["state"]), (f"my-account/{DEV}", "ready"))
        asked = self.fake.state.asked[len(before):]
        # The plan and the organizations were read with the platform and are kept; the fake's license text cannot be
        # decoded, so it is asked again, as anything the Cloud did not say is
        self.assertEqual(asked, [("GET", f"/accounts/{ACCOUNT}/environments"), ("GET", f"/accounts/{ACCOUNT}/gateways"),
                                 ("GET", f"/accounts/{ACCOUNT}/organizations/fake-organization/license"),
                                 ("GET", f"{cloud.GATE}/v2/organizations/fake-organization/environments/{DEV}/apis")])

    def test_it_reads_the_platform_again_whatever_was_served(self):
        self.ready()
        self.read()
        self.fake.state.gateways.clear()   # deleted in the console after the read
        with self.assertRaisesRegex(ValueError, f"^Set up a gateway in {re.escape(DEVELOPMENT)} first$"):
            places.verify(PROJECT, f"my-account/{DEV}")

    def test_what_a_place_lacks_is_said_as_the_button_says_it(self):
        with self.assertRaisesRegex(ValueError, f"^Set up a gateway in {re.escape(DEVELOPMENT)} first$"):
            places.verify(PROJECT, f"my-account/{DEV}")
        self.create(DEV, "lab", platform="my-account")
        with self.assertRaisesRegex(ValueError, f"^Cloud Token for {re.escape(DEVELOPMENT)} is not set$"):
            places.verify(PROJECT, f"my-account/{DEV}")
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        with self.assertRaisesRegex(ValueError, f"^Cloud Token for {re.escape(DEVELOPMENT)} has expired$"):
            places.verify(PROJECT, f"my-account/{DEV}")
        self.assertEqual([a for a in self.fake.state.asked if a[1].startswith(cloud.GATE)], [], "Cloud Gate not asked")

    def test_a_cloud_token_that_cloud_gate_refuses_is_said_and_remembered(self):
        self.ready()
        self.fake.state.revoked.add(envfiles.read(".env")[DEV_KEY])
        with self.assertRaisesRegex(ValueError, f"^Gravitee Cloud refuses the Cloud Token of {re.escape(DEVELOPMENT)}$") as caught:
            places.verify(PROJECT, f"my-account/{DEV}")
        self.assertNotIsInstance(caught.exception, cloud.CloudError)
        self.assertEqual(places.place(PROJECT, f"my-account/{DEV}")["missing"], "Cloud Token refused")
        self.assertHidden(str(caught.exception))

    def test_a_cloud_token_that_cannot_read_api_management_is_refused_too(self):
        self.ready()
        self.patch(cloud.Gate, "read", side_effect=cloud.CloudError("This Cloud Token cannot read API Management",
                                                                    "invalid", 403))
        with self.assertRaisesRegex(ValueError, "^Gravitee Cloud refuses the Cloud Token of"):
            places.verify(PROJECT, f"my-account/{DEV}")

    def test_cloud_gate_that_does_not_answer_is_not_a_refusal(self):
        self.ready()
        self.patch(cloud.Gate, "read", side_effect=cloud.CloudError("localhost does not answer in time"))
        with self.assertRaisesRegex(cloud.CloudError, "does not answer in time"):
            places.verify(PROJECT, f"my-account/{DEV}")
        self.assertEqual(platforms._refused, set())

    def test_a_platform_that_cannot_be_read_says_why(self):
        self.ready()
        envfiles.write(".env", {KEY: "rotated-since"})
        with self.assertRaisesRegex(ValueError, "^Gravitee Cloud refuses this token$"):
            places.verify(PROJECT, f"my-account/{DEV}")
        self.fake.shutdown()
        self.fake.server_close()
        envfiles.write(".env", {KEY: fake_cloud.TOKEN})
        with self.assertRaisesRegex(ValueError, "refuses the connection"):
            places.verify(PROJECT, f"my-account/{DEV}")

    def test_what_is_not_a_place(self):
        for place_id in ("nope/x", f"my-account/{TEST}x", "my-account/a/b"):
            with self.subTest(place=place_id), self.assertRaisesRegex(paths.NotFound, "^No place "):
                places.verify(PROJECT, place_id)

    def test_what_a_request_sends_that_is_no_text_is_not_a_place_either(self):
        """The body of a request is JSON: a number or a list is a refusal in words, not an error of the code."""
        for place_id in (5, ["my-account/" + DEV], {"id": "x"}, None, True):
            with self.subTest(place=place_id):
                with self.assertRaisesRegex(paths.NotFound, "^No place"):
                    places.verify(PROJECT, place_id)
                with self.assertRaisesRegex(paths.NotFound, "^No place"):
                    places.seed_env(PROJECT, place_id, PLAYGROUND)

    def test_nothing_in_what_it_says_holds_a_token(self):
        self.ready()
        seen = [places.verify(PROJECT, f"my-account/{DEV}")]
        self.fake.state.revoked.add(envfiles.read(".env")[DEV_KEY])
        with self.assertRaises(ValueError) as caught:
            places.verify(PROJECT, f"my-account/{DEV}")
        seen += [str(caught.exception), places.places(PROJECT), places.serving(PROJECT)]
        self.assertHidden(seen)


class Naming(Places):
    def test_a_place_is_named_by_its_environment_and_its_platform_never_with_a_dot_between(self):
        self.assertEqual(places.label("My Account", "Development"), "Development (My Account)")
        found = self.read()
        self.assertEqual([place["name"] for place in found],
                         ["Development (My Account)", "Test (My Account)", "Production (My Account)"])
        self.assertFalse(any(" · " in json.dumps(place) for place in found), "a middle dot separates a place")
        self.assertEqual(places.place(PROJECT, f"my-account/{DEV}")["name"], DEVELOPMENT)


class Gateways(Places):
    """The place a gateway serves, for the plan of its start."""

    def test_the_gateway_that_runs_here(self):
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        found = places.gateway(PROJECT, "my-account", gateway["id"])
        self.assertEqual(found, {"id": f"my-account/{DEV}", "name": DEVELOPMENT, "gateway": "hybrid", "here": True,
                                 "serving": {"id": f"my-account/{DEV}", "name": DEVELOPMENT}})
        self.assertHidden(found)

    def test_a_gateway_that_does_not_run_here_says_what_does_and_what_it_serves(self):
        self.create(DEV, "first", platform="my-account")
        second = self.create(TEST, "second", platform="my-account")["gateway"]
        found = places.gateway(PROJECT, "my-account", second["id"])
        self.assertEqual((found["id"], found["name"], found["gateway"], found["here"]),
                         (f"my-account/{TEST}", f"Test ({NAME})", "hybrid", False))
        self.assertEqual(found["serving"], {"id": f"my-account/{DEV}", "name": DEVELOPMENT})

    def test_what_it_serves_is_the_name_of_a_place_of_another_platform_too(self):
        self.second("Client Lab")
        mine = self.create(DEV, "mine", platform="my-account")["gateway"]
        theirs = self.create("fake-client-test", "theirs", platform="client-lab")["gateway"]
        self.read()   # the platform whose gateway runs here has said so
        found = places.gateway(PROJECT, "client-lab", theirs["id"])
        self.assertEqual((found["name"], found["here"], found["serving"]),
                         ("Test (Client Lab)", False, {"id": f"my-account/{DEV}", "name": DEVELOPMENT}))
        self.assertTrue(places.gateway(PROJECT, "my-account", mine["id"])["here"])

    def test_what_it_serves_is_not_known_while_its_platform_has_not_said_so_which_is_not_nothing(self):
        self.second("Client Lab")
        self.create(DEV, "mine", platform="my-account")
        theirs = self.create("fake-client-test", "theirs", platform="client-lab")["gateway"]
        release, real = threading.Event(), platforms.fetch
        self.patch(platforms, "fetch", lambda record: (release.wait(10) if record["id"] == "my-account" else None)
                   or real(record))   # My Account is being read: it says nothing yet
        self.addCleanup(release.set)
        places.places(PROJECT)
        found = places.gateway(PROJECT, "client-lab", theirs["id"])
        release.set()
        self.assertEqual((found["here"], found["serving"]), (False, None))

    def test_nothing_serves_when_none_runs_here(self):
        # Kept, none runs here: a token of this one only. None is not "nothing is served" for the plan: it just has no
        # place to say it stops serving
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": None})
        found = places.gateway(PROJECT, "my-account", gateway["id"])
        self.assertEqual((found["here"], found["serving"]), (False, None))

    def test_a_token_that_no_gateway_matches_is_no_gateway_that_runs_here(self):
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": "not-a-token"})
        found = places.gateway(PROJECT, "my-account", gateway["id"])
        self.assertEqual((found["here"], found["serving"]), (False, None))

    def test_the_platform_is_read_again_because_a_click_asks(self):
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        self.read()
        asked = len(self.fake.state.asked)
        places.gateway(PROJECT, "my-account", gateway["id"])
        self.assertGreater(len(self.fake.state.asked), asked)

    def test_what_cannot_start(self):
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        # The Lab created it and has no token for it: it was made in the console
        kept = platforms._key("my-account", "GATEWAY", gateway["id"])
        envfiles.write(paths.ENV_FILE, {kept: None})
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": None})
        with self.assertRaisesRegex(ValueError, "^Its token is not kept here$"):
            places.gateway(PROJECT, "my-account", gateway["id"])
        with self.assertRaises(paths.NotFound):
            places.gateway(PROJECT, "my-account", "nope")
        with self.assertRaises(paths.NotFound):
            places.gateway(PROJECT, "nope", gateway["id"])
        with self.assertRaisesRegex(paths.NotFound, "^No add-on runs a gateway of My Account$"):
            places.gateway({"addons": {}}, "my-account", gateway["id"])

    def test_a_gateway_that_cloud_hosts_is_none_that_runs_here(self):
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        self.fake.state.gateways[gateway["id"]]["type"] = "HOSTED"
        with self.assertRaisesRegex(paths.NotFound, "^No gateway .* in My Account$"):
            places.gateway(PROJECT, "my-account", gateway["id"])

    def test_a_platform_that_cannot_be_read_says_why(self):
        gateway = self.create(DEV, "lab", platform="my-account")["gateway"]
        envfiles.write(paths.ENV_FILE, {KEY: "revoked"})
        with self.assertRaisesRegex(ValueError, "^Gravitee Cloud refuses this token$"):
            places.gateway(PROJECT, "my-account", gateway["id"])
        self.fake.shutdown()
        self.fake.server_close()
        envfiles.write(paths.ENV_FILE, {KEY: fake_cloud.TOKEN})
        with self.assertRaises(ValueError):
            places.gateway(PROJECT, "my-account", gateway["id"])


class Records(Places):
    """What a playground deployed to the places, read from its own folder."""

    RECORD = "playgrounds/hotels/.state/{}/{}/created.json"
    META = "playgrounds/hotels/.state/{}/{}/meta.json"

    def record(self, platform, environment, name="Development", labels=("API", "API", "Application")):
        self.write(self.RECORD.format(platform, environment), json.dumps([{"label": label} for label in labels]))
        if name:
            self.write(self.META.format(platform, environment), json.dumps({"environment": name}))

    @staticmethod
    def created(pid, place):
        """What playgrounds.created reads from a place's record: [{what, count}]."""
        file = paths.ROOT / "playgrounds" / pid / ".state" / place / "created.json"
        try:
            items = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        counts = {}
        for item in items:
            counts[item["label"]] = counts.get(item["label"], 0) + 1
        return [{"what": what, "count": count} for what, count in counts.items()]

    def test_the_places_that_hold_a_record_are_the_folders_of_the_state_that_hold_one(self):
        self.record("my-account", DEV)
        self.record("my-account", TEST, "Test")
        self.record("client-lab", "fake-client-test", "Test")
        self.write("playgrounds/hotels/.state/created.json", "[]")   # This machine's: not a place
        self.write("playgrounds/hotels/.state/my-account/fake-env-prod/other.json", "[]")   # no record
        self.write("playgrounds/hotels/.state/.hidden/x/created.json", "[]")
        self.write("playgrounds/hotels/.state/my-account/..x/created.json", "[]")
        self.write("playgrounds/hotels/.state/under_score/x/created.json", "[]")
        self.assertEqual(places.recorded("hotels"), ["client-lab/fake-client-test", f"my-account/{DEV}",
                                                     f"my-account/{TEST}"])
        self.assertEqual(places.recorded("nothing"), [])
        self.assertEqual(places.recorded("local/sandbox"), [])

    def test_what_it_holds_is_counted_by_the_record_and_named_by_its_environment(self):
        self.record("my-account", DEV)
        self.assertEqual(places.deployed("hotels", self.created), [{
            "place": f"my-account/{DEV}", "environment": "Development",
            "created": [{"what": "API", "count": 2}, {"what": "Application", "count": 1}]}])

    def test_a_place_that_holds_nothing_is_not_listed(self):
        self.record("my-account", DEV, labels=())   # a delete emptied it
        self.assertEqual(places.deployed("hotels", self.created), [])

    def test_the_environment_is_named_by_its_record_whatever_the_platform_says_now(self):
        self.read()
        self.record("my-account", DEV, "Renamed")
        self.assertEqual(places.deployed("hotels", self.created)[0]["environment"], "Renamed")

    def test_without_a_name_in_its_record_the_platform_names_it_as_it_last_said(self):
        self.record("my-account", DEV, name=None)
        self.assertEqual(places.deployed("hotels", self.created)[0]["environment"], DEV)   # not said yet: its id
        self.read()
        self.assertEqual(places.deployed("hotels", self.created)[0]["environment"], "Development")

    def test_they_come_in_the_order_of_the_accounts_once_the_platforms_said_it(self):
        self.second("Client Lab")
        for platform, environment in (("my-account", "fake-env-prod"), ("my-account", DEV), ("client-lab", "fake-client-test"),
                                      ("my-account", TEST)):
            self.record(platform, environment, "Name")
        listed = lambda: [found["place"] for found in places.deployed("hotels", self.created)]   # noqa: E731
        # Not read yet: by their ids
        self.assertEqual(listed(), ["client-lab/fake-client-test", "my-account/fake-env-dev", "my-account/fake-env-prod",
                                    "my-account/fake-env-test"])
        self.read()
        # Platforms by name, environments as each account lists them
        self.assertEqual(listed(), ["client-lab/fake-client-test", "my-account/fake-env-dev", "my-account/fake-env-test",
                                    "my-account/fake-env-prod"])

    def test_the_state_asks_nothing_of_a_platform_and_starts_no_read(self):
        self.record("my-account", DEV)
        asked = len(self.fake.state.asked)
        for _ in range(3):
            places.deployed("hotels", self.created)
        self.assertEqual((len(self.fake.state.asked), places._reading, places._cache), (asked, {}, {}))

    def test_a_playground_that_holds_nothing_does_not_even_read_the_platforms(self):
        read = self.patch(places.platforms, "read", wraps=platforms.read)
        self.assertEqual(places.deployed("hotels", self.created), [])
        read.assert_not_called()

    def test_nothing_in_it_holds_a_token(self):
        self.record("my-account", DEV)
        self.read()
        self.assertHidden(places.deployed("hotels", self.created), places.recorded("hotels"))


class State(Case):
    """The state folder of a playground holds what a container wrote, and may hold a link it planted: the Lab reads and writes
    nothing through one."""

    DEFAULT = json.dumps({"schema": 1, "catalogs": [{"id": "fixture", "url": "https://example.invalid/fixture.git"}]})

    def setUp(self):
        self.folder(paths, files={paths.DEFAULT_FILE: self.DEFAULT})
        self.unit = self.root / paths.unit_folder("playground", "@acme/bank")
        self.state = self.unit / ".state"
        self.state.mkdir(parents=True)
        self.outside = self.root / "outside"
        (self.outside / "my-account" / "dev").mkdir(parents=True)
        (self.outside / "my-account" / "dev" / "created.json").write_text('[{"label": "API"}]', encoding="utf-8")

    def test_the_state_folder_of_a_playground_of_a_catalog_is_in_its_folder(self):
        self.assertEqual(places.recorded("@acme/bank"), [])
        self.write(f"{paths.unit_folder('playground', '@acme/bank')}/.state/my-account/dev/created.json", "[]")
        self.write(f"{paths.unit_folder('playground', 'bank')}/.state/my-account/test/created.json", "[]")
        self.write(f"{paths.unit_folder('playground', 'local/bank')}/.state/my-account/prod/created.json", "[]")
        self.assertEqual(places.recorded("@acme/bank"), ["my-account/dev"])
        self.assertEqual(places.recorded("bank"), ["my-account/test"])
        self.assertEqual(places.recorded("local/bank"), ["my-account/prod"])
        self.assertTrue(places.inside("@acme/bank"))

    def test_a_state_folder_that_is_a_link_out_of_the_folder_holds_no_record(self):
        self.state.rmdir()
        os.symlink(self.outside, self.state)
        self.assertFalse(places.inside("@acme/bank"))
        self.assertEqual(places.recorded("@acme/bank"), [])
        self.assertIsNone(places.read_state("@acme/bank", "my-account/dev/created.json"))
        self.assertIsNone(places.read_state("@acme/bank", "my-account/dev/meta.json"))

    def test_a_folder_of_a_place_that_is_a_link_out_of_the_folder_holds_no_record(self):
        os.symlink(self.outside / "my-account", self.state / "my-account")
        self.assertEqual(places.recorded("@acme/bank"), [])
        self.assertIsNone(places.read_state("@acme/bank", "my-account/dev/created.json"))
        (self.state / "linked").mkdir()
        os.symlink(self.outside / "my-account" / "dev", self.state / "linked" / "dev")
        self.assertEqual(places.recorded("@acme/bank"), [])

    def test_a_link_to_another_folder_of_the_playground_is_no_state_either(self):
        # Inside the playground's folder, but not its state: a write there would reach what the policy judged
        (self.unit / "seed").mkdir()
        (self.unit / "seed" / "created.json").write_text('[{"label": "API"}]', encoding="utf-8")
        os.symlink(self.unit / "seed", self.state / "my-account")
        os.symlink(self.unit, self.state / "up")
        for link in ("my-account", "up"):
            with self.subTest(link=link):
                self.assertFalse(places.inside("@acme/bank", self.state / link / "dev"))
                self.assertFalse(places.inside("@acme/bank", self.state / link))
        self.assertEqual(places.recorded("@acme/bank"), [])
        self.assertIsNone(places.read_state("@acme/bank", "my-account/created.json"))
        self.assertIsNone(places.read_state("@acme/bank", "up/seed/created.json"))
        self.assertTrue(places.inside("@acme/bank", self.state / "dev" / "created.json"), "a name of its own is state")

    def test_a_state_folder_that_is_a_link_to_a_folder_of_the_playground_holds_no_record(self):
        (self.unit / "seed").mkdir()
        (self.unit / "seed" / "my-account" / "dev").mkdir(parents=True)
        (self.unit / "seed" / "my-account" / "dev" / "created.json").write_text('[{"label": "API"}]', encoding="utf-8")
        self.state.rmdir()
        os.symlink(self.unit / "seed", self.state)
        self.assertFalse(places.inside("@acme/bank"))
        self.assertEqual(places.recorded("@acme/bank"), [])
        self.assertIsNone(places.read_state("@acme/bank", "my-account/dev/created.json"))

    def test_a_link_to_a_file_inside_the_folder_is_not_a_record_either(self):
        (self.state / "real.json").write_text('[{"label": "API"}]', encoding="utf-8")
        os.symlink(self.state / "real.json", self.state / "created.json")
        self.assertIsNone(places.read_state("@acme/bank", "created.json"))
        self.assertEqual(places.read_state("@acme/bank", "real.json"), '[{"label": "API"}]')

    def test_a_pipe_is_never_waited_for(self):
        os.mkfifo(self.state / "created.json")
        self.assertIsNone(places.read_state("@acme/bank", "created.json"))

    def test_a_file_that_is_too_big_or_is_not_text_is_not_read(self):
        (self.state / "big.json").write_bytes(b" " * (places.STATE_LIMIT + 1))
        (self.state / "ok.json").write_bytes(b" " * places.STATE_LIMIT)
        (self.state / "bytes.json").write_bytes(b"\xff\xfe[]")
        self.assertEqual([places.read_state("@acme/bank", name) is None for name in ("big.json", "ok.json", "bytes.json")],
                         [True, False, True])

    def test_a_name_that_leaves_the_folder_is_not_read(self):
        (self.root / "secret.json").write_text("[]", encoding="utf-8")
        for name in ("../../../../../../../secret.json", "/etc/hosts", "", "a\0b"):
            with self.subTest(name=name):
                self.assertIsNone(places.read_state("@acme/bank", name))

    def test_the_name_of_an_environment_is_read_from_its_record(self):
        self.write(f"{paths.unit_folder('playground', '@acme/bank')}/.state/my-account/dev/meta.json",
                   json.dumps({"environment": "Development"}))
        self.assertEqual(places._named("@acme/bank", "my-account/dev"), "Development")
        self.assertIsNone(places._named("@acme/bank", "my-account/test"))
        self.write(f"{paths.unit_folder('playground', '@acme/bank')}/.state/my-account/test/meta.json", "not json")
        self.assertIsNone(places._named("@acme/bank", "my-account/test"))

    def test_the_name_of_an_environment_is_none_for_whatever_else_a_container_wrote(self):
        meta = f"{paths.unit_folder('playground', '@acme/bank')}/.state/my-account/test/meta.json"
        for text in ("5", "[1]", "null", '"x"', '{"environment": 5}', '{"environment": ""}', '{"environment": ["a"]}',
                     "[" * 100000, '{"a":' * 100000, "\xff"):
            with self.subTest(text=text[:20]):
                self.write(meta, text)
                self.assertIsNone(places._named("@acme/bank", "my-account/test"))


class Imports(unittest.TestCase):
    def test_neither_module_needs_the_checks(self):
        """check.py imports platforms for its kinds: a module of those that imports check would be a circle."""
        found = subprocess.run([sys.executable, "-c", "import sys, lab_ui.gravitee.platforms, lab_ui.gravitee.places; "
                                "sys.exit('lab_ui.library.check' in sys.modules)"], cwd=paths.LAB_UI_DIR, check=False)
        self.assertEqual(found.returncode, 0)


if __name__ == "__main__":
    unittest.main()
