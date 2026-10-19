"""platforms: the registry in .lab/platforms.json, the tokens kept as settings (the Account Token, a Cloud Token for
each environment, the token of each gateway the Lab created), and the actions on a platform, against the fake Cloud on
a free port, in a temporary project folder. No network. A token is never in an answer, an error or the record, and a
test never prints one: it asks whether it is there, not what it is."""

import errno
import json
import os
import stat
import time
import unittest

from lab_ui.gravitee import cloud, platforms
from lab_ui.docker import envfiles, files
from lab_ui import paths

from .. import Case, fixtures, fake_cloud
from .test_cloud import Scripted, serving
from ..library.test_project import read

ADDON_ENV = "addons/hybrid/.env"
ACCOUNT = fake_cloud.ACCOUNT["id"]   # the Account ID a person copies from Cloud
SECOND = fake_cloud.SECOND_ACCOUNT["id"]
KEY = "PLATFORM_GRAVITEE_CLOUD_TOKEN"
DEV, TEST = "fake-env-dev", "fake-env-test"
DEV_KEY = "PLATFORM_GRAVITEE_CLOUD_ENV_FAKE_ENV_DEV_TOKEN"
FORM = {"key": "HYBRID_CLOUD_TOKEN", "default": None, "required": True, "secret": True, "comment": "the token"}
GATEWAY = {"platform": "cloud", "service": "apim-hybrid-gateway", "token": "HYBRID_CLOUD_TOKEN",
           "domains": ["localhost:8090", "apim-hybrid-gateway:8082"]}
PROJECT = {"addons": {"hybrid": {"id": "hybrid", "env_file": ADDON_ENV, "settings": [FORM], "gateway": GATEWAY},
                      "kafka": {"id": "kafka", "env_file": "addons/kafka/.env", "settings": [], "gateway": None}},
           "playgrounds": {}, "core_variables": []}


def gateway_key(gateway, platform="GRAVITEE_CLOUD"):
    """The setting that keeps the token of a gateway, by hand and not with the code under test."""
    return f"PLATFORM_{platform}_GATEWAY_{gateway.upper().replace('-', '_')}_TOKEN"


class Platforms(Case):
    """A project folder with a .env, and the fake Cloud."""

    def setUp(self):
        self.folder(paths, envfiles, files, files={".env": "GRAVITEE_LICENSE=keep-me\n",
                                                   "addons/hybrid/compose.yml": ""})
        self.patch(platforms, "_refused", set())
        self.patch(platforms, "_kept", {})
        self.fake = fake_cloud.serve(connect_after=0)
        serving(self.fake)
        self.addCleanup(self.fake.server_close)
        self.addCleanup(self.fake.shutdown)
        self.url = f"http://127.0.0.1:{self.fake.server_port}"

    def add(self, name="Gravitee Cloud", token=fake_cloud.TOKEN, account=ACCOUNT, **more):
        return platforms.add(name, "cloud", more.pop("url", self.url), token, account, **more)

    def second(self, name="Client Lab"):
        return self.add(name, token=fake_cloud.SECOND_TOKEN, account=SECOND)

    def served(self, answer):
        """The address of a server that answers as `answer(method, path, headers, body)` says."""
        address = Scripted(answer)
        serving(address)
        self.addCleanup(address.server_close)
        self.addCleanup(address.shutdown)
        return f"http://127.0.0.1:{address.server_port}"

    def text(self, path):
        return (self.root / path).read_text(encoding="utf-8")

    def tokens(self):
        """Every token that is secret in the test: those of the module, and those the Cloud issued."""
        return [*fake_cloud.SECRETS, *self.fake.state.tokens.values()]

    def assertHidden(self, *things):
        """None of the tokens is in these (answers, errors, records). It does not say which one is, or what it is."""
        text = json.dumps(things, default=str)
        self.assertFalse(any(token in text for token in self.tokens()), "a token is in it")

    def create(self, environment=DEV, name="lab", project=PROJECT, platform="gravitee-cloud"):
        return platforms.create_gateway(project, platform, environment, name)

    def held(self):
        """The token the add-on holds."""
        return envfiles.read(ADDON_ENV).get("HYBRID_CLOUD_TOKEN")


class Register(Platforms):
    def test_a_platform_is_registered_once_it_accepts_the_token(self):
        record = self.add()
        self.assertEqual(record, {"id": "gravitee-cloud", "name": "Gravitee Cloud", "kind": "cloud", "url": self.url,
                                  "account": {"id": ACCOUNT, "hrid": "my-account", "name": "My Account"},
                                  "tokenSet": True})
        self.assertEqual(platforms.listing(), {"platforms": [record]})
        self.assertHidden(record, platforms.listing())

    def test_no_address_of_cloud_gate_is_kept(self):
        """The Gate is found when a Cloud Token is used, from the token: the record holds no `gate`."""
        self.add()
        self.assertEqual(sorted(platforms.read()[0]), ["account", "id", "kind", "name", "url"])
        self.assertNotIn("gate", self.text(platforms.FILE))

    def test_a_token_that_works_only_under_its_own_account_registers(self):
        """The Cloud answers 401 to an Account Token on the list of accounts, /me and an hrid: only the account's own
        path works, and it is the only one the Lab calls, whatever it does after."""
        self.add()
        platforms.check("gravitee-cloud")
        made = self.create()["gateway"]
        platforms.delete_gateway(PROJECT, "gravitee-cloud", made["id"])
        asked = self.fake.state.asked
        self.assertEqual(asked[0], ("GET", f"/accounts/{ACCOUNT}"))   # what registering asks, and only that
        self.assertEqual({path for _, path in asked if not path.startswith(f"/accounts/{ACCOUNT}")}, set())
        self.assertFalse({"/accounts", "/me"} & {path for _, path in asked}, "a path of a user was called")

    def test_the_account_comes_from_the_id_given_and_what_the_cloud_says_of_it(self):
        self.assertEqual(self.add(account=f"  {ACCOUNT}\n")["account"],
                         {"id": ACCOUNT, "hrid": "my-account", "name": "My Account"})
        self.assertEqual(self.fake.state.asked, [("GET", f"/accounts/{ACCOUNT}")])

    def test_what_the_cloud_does_not_say_of_the_account_is_not_needed(self):
        for text, account in [('{"id": "acc"}', {"id": "acc", "hrid": "", "name": "acc"}),
                              ('{"id": "acc", "hrid": "h"}', {"id": "acc", "hrid": "h", "name": "h"}),
                              ('{"id": "acc", "hrid": null, "name": null}', {"id": "acc", "hrid": "", "name": "acc"}),
                              ('{"id": "acc", "name": "N", "profile": "SELF_HOSTED"}',
                               {"id": "acc", "hrid": "", "name": "N"})]:
            address = self.served(lambda *asked, text=text: (200, text, None))
            with self.subTest(text=text):
                found = platforms.add("Odd", "cloud", address, "t", "acc")
                self.assertEqual(found["account"], account)
                platforms.remove(found["id"])

    def test_the_token_is_a_setting_of_the_core_and_not_in_the_record(self):
        self.add()
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "keep-me",
                                                 "PLATFORM_GRAVITEE_CLOUD_TOKEN": fake_cloud.TOKEN})
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)
        self.assertFalse(fake_cloud.TOKEN in self.text(platforms.FILE), "the token is in the record")

    def test_the_address_is_kept_normalised(self):
        self.assertEqual(self.add(url=self.url + "/management/")["url"], self.url)

    def test_the_address_defaults_to_the_eu_cloud(self):
        with self.assertRaises(cloud.CloudError) as caught:   # nothing listens there in a test: only what is asked
            account = self.patch(cloud.Cloud, "account", side_effect=cloud.CloudError("stop"))
            platforms.add("Gravitee Cloud", None, "", fake_cloud.TOKEN, ACCOUNT)
        self.assertEqual(str(caught.exception), "stop")
        account.assert_called_once_with(ACCOUNT)

    def test_the_id_follows_the_name_and_stays_unique(self):
        names = ("Gravitee Cloud", "Gravitee  Cloud!", "Other", "gravitee-cloud")
        # Four accounts, since an account is connected once
        def account(name, n):
            address = self.served(lambda *asked: (200, json.dumps({"id": f"acc-{n}", "name": "A"}), None))
            return platforms.add(name, "cloud", address, "t", f"acc-{n}")
        self.assertEqual([account(name, n)["id"] for n, name in enumerate(names)],
                         ["gravitee-cloud", "gravitee-cloud-2", "other", "gravitee-cloud-3"])
        self.assertEqual(sorted(envfiles.read(".env")),
                         ["GRAVITEE_LICENSE", "PLATFORM_GRAVITEE_CLOUD_2_TOKEN", "PLATFORM_GRAVITEE_CLOUD_3_TOKEN",
                          "PLATFORM_GRAVITEE_CLOUD_TOKEN", "PLATFORM_OTHER_TOKEN"])

    def test_the_name_is_the_accounts_when_none_is_given(self):
        for name in (None, "", "   "):
            with self.subTest(name=name):
                record = self.add(name)
                self.assertEqual((record["id"], record["name"]), ("my-account", "My Account"))
                platforms.remove(record["id"])

    def test_two_accounts_are_two_platforms_by_their_names(self):
        first, second = self.add(None), self.add(None, token=fake_cloud.SECOND_TOKEN, account=SECOND)
        self.assertEqual([(p["id"], p["name"]) for p in (first, second)],
                         [("my-account", "My Account"), ("client-lab", "Client Lab")])
        self.assertEqual(sorted(envfiles.read(".env")),
                         ["GRAVITEE_LICENSE", "PLATFORM_CLIENT_LAB_TOKEN", "PLATFORM_MY_ACCOUNT_TOKEN"])

    def test_the_name_of_an_account_that_is_too_long_is_cut(self):
        address = self.served(lambda *asked: (200, json.dumps({"id": "acc", "name": "x" * 80}), None))
        self.assertEqual(platforms.add(None, "cloud", address, "t", "acc")["name"], "x" * platforms.NAME_LIMIT)

    def test_a_name_is_used_once(self):
        """Whatever its case: the second is refused, nothing is kept. Another account, so that it is the name that is refused."""
        self.add("Cloud one")
        kept = (platforms.read(), self.text(".env"))
        for name, words in [("Cloud one", "Cloud one"), ("cloud one", "cloud one"), (" CLOUD ONE ", "CLOUD ONE")]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"^An account named {words} is already connected$"):
                self.second(name)
        self.assertEqual((platforms.read(), self.text(".env")), kept)

    def test_an_account_is_connected_once_whatever_its_name_or_token(self):
        """It is known by its Account ID, before anything is asked of the Cloud: the refusal names the account, nothing is kept."""
        self.add("Cloud one")
        kept = (platforms.read(), self.text(".env"))
        for name in ("Cloud two", None):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "^This account is already connected as Cloud one$"):
                self.add(name, token="another-token", account=ACCOUNT.upper())
        self.assertEqual((platforms.read(), self.text(".env")), kept)

    def test_a_token_the_platform_refuses_registers_nothing(self):
        with self.assertRaisesRegex(ValueError, "^The token or the Account ID is not right$") as caught:
            self.add(token="wrong-token-for-the-fake")
        self.assertFalse("wrong-token-for-the-fake" in str(caught.exception), "the token is in the message")
        self.assertEqual((platforms.read(), envfiles.read(".env")), ([], {"GRAVITEE_LICENSE": "keep-me"}))
        self.assertFalse((self.root / platforms.FILE).exists())

    def test_the_hrid_is_no_account_id(self):
        """The Cloud answers 401 to it, and the words do not say whether the token or the id is wrong."""
        for wrong in ("my-account", "00000000-0000-0000-0000-000000000000", ACCOUNT.upper()):
            with self.subTest(account=wrong), self.assertRaises(cloud.CloudError) as caught:
                self.add(account=wrong)
            self.assertEqual((str(caught.exception), caught.exception.kind, caught.exception.status),
                             ("The token or the Account ID is not right", "invalid", 401))
            self.assertHidden(str(caught.exception))
        self.assertEqual((platforms.read(), envfiles.read(".env")), ([], {"GRAVITEE_LICENSE": "keep-me"}))
        self.assertFalse((self.root / platforms.FILE).exists())

    def test_no_token_in_what_registering_answers_says_or_keeps(self):
        seen = [self.add()]
        for call in (lambda: self.add("Other", token="wrong-token-for-the-fake"),
                     lambda: self.add("Other", account="my-account"), lambda: self.add("Other", account=""),
                     lambda: self.add("Other", account="a/b"), lambda: self.add("Other", url="ftp://x"),
                     lambda: self.add("gravitee cloud")):
            with self.assertRaises(ValueError) as caught:
                call()
            seen.append(str(caught.exception))
        seen += [platforms.read(), platforms.listing(), self.text(platforms.FILE)]
        self.assertHidden(seen)
        self.assertFalse("wrong-token-for-the-fake" in json.dumps(seen), "the token that was refused is in it")

    def test_a_refusal_says_the_same_whichever_it_is(self):
        """401, 403 and 404 are the same words: the Cloud does not say which of the token and the id is wrong."""
        for code in (401, 403, 404):
            address = self.served(lambda *asked, code=code: (code, '{"message": "which one it is"}', None))
            with self.subTest(code=code), self.assertRaises(cloud.CloudError) as caught:
                platforms.add("Odd", "cloud", address, "t", "acc")
            self.assertEqual(str(caught.exception), "The token or the Account ID is not right")
        self.assertEqual(platforms.read(), [])

    def test_what_is_not_a_refusal_is_said_as_it_is(self):
        for code, words in [(400, "Gravitee Cloud refuses it: no way"), (500, "answered an error .HTTP 500.")]:
            address = self.served(lambda *asked, code=code: (code, '{"message": "no way"}', None))
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, words):
                platforms.add("Odd", "cloud", address, "t", "acc")
        self.assertEqual(platforms.read(), [])

    def test_a_platform_that_is_not_there_registers_nothing(self):
        self.fake.shutdown()
        self.fake.server_close()
        with self.assertRaisesRegex(ValueError, "refuses the connection"):
            self.add()
        self.assertEqual((platforms.read(), envfiles.read(".env")), ([], {"GRAVITEE_LICENSE": "keep-me"}))

    def test_what_cannot_be_registered(self):
        for fields, words in [(dict(name="x" * 61), "The name is"), (dict(name=4), "The name is"),
                              (dict(name=["a"]), "The name is"), (dict(name="!!"), "needs a letter"),
                              (dict(kind="aws"), "Only Gravitee Cloud"),
                              (dict(kind=["cloud"]), "Only Gravitee Cloud"), (dict(url="ftp://x"), "must be http"),
                              (dict(token=""), "token is empty"), (dict(token=None), "token is empty"),
                              (dict(account=None), "^Enter the Account ID$"),
                              (dict(account=""), "^Enter the Account ID$"),
                              (dict(account="   "), "^Enter the Account ID$"),
                              (dict(account=4), "^Enter the Account ID$"),
                              (dict(account=[ACCOUNT]), "^Enter the Account ID$"),
                              (dict(account="a/b"), "letters, digits and dashes"),
                              (dict(account="../x"), "letters, digits and dashes"),
                              (dict(account="two words"), "letters, digits and dashes"),
                              (dict(account="a?b"), "letters, digits and dashes"),
                              (dict(account="café"), "letters, digits and dashes"),
                              (dict(account="a" * 65), "letters, digits and dashes")]:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, words):
                platforms.add(**{"name": "Cloud", "kind": "cloud", "url": self.url, "token": fake_cloud.TOKEN,
                                 "account": ACCOUNT, **fields})
        self.assertEqual(platforms.read(), [])

    def test_an_account_id_that_is_none_is_not_asked_of_the_platform(self):
        for wrong in (None, "", "   ", 4, "a/b", "../x", "a b", "a" * 65):
            with self.subTest(account=wrong), self.assertRaises(ValueError):
                self.add(account=wrong)
        self.assertEqual((self.fake.state.asked, platforms.read()), ([], []))

    def test_an_account_id_is_a_segment_of_letters_digits_and_dashes(self):
        for good in (ACCOUNT, ACCOUNT.upper(), "a", "my-account", "0", "a" * 64):
            self.assertTrue(platforms.ACCOUNT_ID.fullmatch(good), good)
        for bad in ("", " ", "a b", "a/b", "a.b", "a_b", "%2e", "a\n", "a" * 65):
            self.assertFalse(platforms.ACCOUNT_ID.fullmatch(bad), repr(bad))

    def test_a_token_is_kept_as_it_was_given(self):
        token = "a$b#c'd\"e\\f"
        address = self.served(lambda *asked: (200, '{"id": "a", "hrid": "h", "name": "N"}', None))
        platforms.add("Odd", "cloud", address, f"  {token}\n", "a")
        self.assertEqual(envfiles.read(".env")["PLATFORM_ODD_TOKEN"], token)

    def test_a_record_that_cannot_be_written_takes_its_token_with_it(self):
        self.write(".env", "GRAVITEE_LICENSE=keep-me\n# a comment\nOTHER=1\n")
        before = self.text(".env")
        write = self.patch(platforms, "_write")
        for error in (OSError(errno.ENOSPC, "No space left on device"), TypeError("not serializable: secret-looking"),
                      KeyError("whatever")):
            write.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(RuntimeError) as caught:
                self.add()
            message = str(caught.exception)
            self.assertTrue(message.startswith("The platform could not be saved (")
                            and message.endswith("nothing was kept"))
            self.assertNotIn("secret-looking", message, "what an error says is not repeated")
            self.assertHidden(message)
            self.assertEqual(self.text(".env"), before, "the token line is gone, the rest as it was")
        self.assertEqual(platforms.listing(), {"platforms": []})

    def test_the_reason_of_a_record_that_cannot_be_written_is_the_systems(self):
        self.patch(platforms, "_write", side_effect=OSError(errno.ENOSPC, "No space left on device"))
        with self.assertRaisesRegex(RuntimeError, r"could not be saved \(No space left on device\): nothing was kept"):
            self.add()

    def test_a_token_that_cannot_be_removed_either_is_named(self):
        """The line stays: the person is told which one to delete, and the token is not in the words."""
        real = envfiles.write
        calls = []

        def write(path, changes):
            calls.append(changes)
            if len(calls) > 1:   # the first writes the token, the second removes it
                raise OSError(errno.EROFS, "Read-only file system")
            real(path, changes)
        self.patch(envfiles, "write", write)
        self.patch(platforms, "_write", side_effect=OSError(errno.ENOSPC, "No space left on device"))
        with self.assertRaises(RuntimeError) as caught:
            self.add()
        self.assertEqual(str(caught.exception), f"The platform could not be saved (No space left on device): "
                                                f"remove {KEY} from the .env")
        self.assertHidden(str(caught.exception))

    def test_a_record_file_that_does_not_read(self):
        for text in ("{", "{}", '"x"', "[1, null, {}]", '[{"id": "a", "name": "n", "kind": "cloud", "url": "u"}]',
                     '[{"id": "a", "name": "n", "kind": "cloud", "url": "u", "account": []}]'):
            self.write(platforms.FILE, text)
            self.assertEqual(platforms.read(), [], text)

    def test_a_record_keeps_only_what_it_is(self):
        kept = {"id": "a", "name": "N", "kind": "cloud", "url": "http://x", "token": "t", "gate": "http://g",
                "account": {"id": "i", "hrid": None, "name": "M", "more": 1}}
        self.write(platforms.FILE, json.dumps([kept]))
        self.assertEqual(platforms.read(), [{"id": "a", "name": "N", "kind": "cloud", "url": "http://x",
                                             "account": {"id": "i", "hrid": "", "name": "M"}}])


class Rename(Platforms):
    def test_the_name_changes_and_the_id_the_account_and_the_token_stay(self):
        record = self.add()
        renamed = platforms.rename(record["id"], "  Work  ")
        self.assertEqual(renamed, {**record, "name": "Work"})
        self.assertEqual(platforms.read(), [{key: renamed[key] for key in ("id", "name", "kind", "url", "account")}])
        self.assertEqual(envfiles.read(".env")["PLATFORM_GRAVITEE_CLOUD_TOKEN"], fake_cloud.TOKEN)
        self.assertEqual(self.fake.state.asked, [("GET", f"/accounts/{ACCOUNT}")], "Gravitee Cloud is not asked")

    def test_a_name_is_refused_when_empty_too_long_or_another_accounts(self):
        record, other = self.add(), self.second()
        kept = platforms.read()
        for name, words in [("", "^The name is 1 to 60 characters$"), (None, "^The name is 1 to 60 characters$"),
                            ("x" * 61, "^The name is 1 to 60 characters$"), (" client lab ", "^Another account is named client lab$")]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, words):
                platforms.rename(record["id"], name)
        self.assertEqual(platforms.read(), kept)
        self.assertEqual(platforms.rename(other["id"], "CLIENT LAB")["name"], "CLIENT LAB", "its own name in another case")

    def test_an_unknown_platform_is_not_found(self):
        with self.assertRaises(paths.NotFound):
            platforms.rename("nothing", "Work")


class ReplaceAccountToken(Platforms):
    def setUp(self):
        super().setUp()
        self.add()

    def test_another_token_of_the_account_takes_the_place_of_the_first(self):
        record = platforms.read()[0]
        answer = platforms.replace_account_token("gravitee-cloud", fake_cloud.MEMBER_TOKEN)
        self.assertEqual(answer, {**record, "tokenSet": True})
        self.assertEqual(envfiles.read(".env")[KEY], fake_cloud.MEMBER_TOKEN)
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)
        self.assertEqual(platforms.read(), [record])
        self.assertEqual(self.fake.state.asked[-1], ("GET", f"/accounts/{ACCOUNT}"))   # checked for the stored account
        self.assertHidden(answer)

    def test_the_cloud_tokens_and_the_gateways_stay(self):
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        made = self.create()["gateway"]
        before = envfiles.read(".env")
        platforms.replace_account_token("gravitee-cloud", fake_cloud.MEMBER_TOKEN)
        self.assertEqual(envfiles.read(".env"), {**before, KEY: fake_cloud.MEMBER_TOKEN})
        self.assertEqual([g["id"] for g in platforms.check("gravitee-cloud", PROJECT)["gateways"]], [made["id"]])

    def test_a_token_of_another_account_is_refused_and_the_first_stays(self):
        for wrong in (fake_cloud.SECOND_TOKEN, "wrong-token-for-the-fake"):
            with self.subTest(token=wrong), \
                    self.assertRaisesRegex(ValueError, "^The token or the Account ID is not right$"):
                platforms.replace_account_token("gravitee-cloud", wrong)
        self.assertEqual(envfiles.read(".env")[KEY], fake_cloud.TOKEN)

    def test_a_token_that_cannot_be_sent_is_refused_before_it_is(self):
        for wrong in (None, "", "two words"):
            with self.subTest(token=wrong), self.assertRaisesRegex(ValueError, "token is empty"):
                platforms.replace_account_token("gravitee-cloud", wrong)
        self.assertEqual(envfiles.read(".env")[KEY], fake_cloud.TOKEN)

    def test_a_platform_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.replace_account_token("nope", fake_cloud.TOKEN)

    def test_a_platform_that_refused_its_token_reads_again_with_the_new_one(self):
        envfiles.write(".env", {KEY: "rotated-since"})
        self.assertEqual(platforms.check("gravitee-cloud")["state"], "invalid")
        platforms.replace_account_token("gravitee-cloud", fake_cloud.TOKEN)
        self.assertEqual(platforms.check("gravitee-cloud")["state"], "ok")


class Forget(Platforms):
    def test_a_platform_and_its_token_go(self):
        self.add()
        self.second("Other")
        self.assertEqual(platforms.remove("gravitee-cloud"), {})
        self.assertEqual([p["id"] for p in platforms.read()], ["other"])
        self.assertEqual(envfiles.read(".env"),
                         {"GRAVITEE_LICENSE": "keep-me", "PLATFORM_OTHER_TOKEN": fake_cloud.SECOND_TOKEN})

    def test_its_gateways_stay_in_the_platform(self):
        self.add()
        self.create()
        platforms.remove("gravitee-cloud")
        self.assertEqual(len(self.fake.state.gateways), 1)

    def test_one_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.remove("nope")

    def test_a_platform_whose_token_is_gone_is_forgotten_all_the_same(self):
        self.add()
        envfiles.write(".env", {"PLATFORM_GRAVITEE_CLOUD_TOKEN": None})
        platforms.remove("gravitee-cloud")
        self.assertEqual((platforms.read(), envfiles.read(".env")), ([], {"GRAVITEE_LICENSE": "keep-me"}))

    def test_no_env_file_is_made_to_forget(self):
        self.add()
        platforms.remove("gravitee-cloud")
        (self.root / ".env").unlink()
        self.add("Again")
        (self.root / ".env").unlink()
        platforms.remove("again")
        self.assertFalse((self.root / ".env").exists())

    def test_every_token_kept_for_it_goes_and_no_other(self):
        """The Account Token, the Cloud Tokens, the gateways' tokens: those of the platform, by the shape of their
        names. gravitee-cloud-2 begins with gravitee-cloud, and keeps its own."""
        self.add()
        self.add("Gravitee Cloud 2", token=fake_cloud.SECOND_TOKEN, account=SECOND)
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        platforms.set_cloud_token("gravitee-cloud-2", "fake-client-test", fake_cloud.SECOND_CLOUD_TOKEN)
        mine = self.create()["gateway"]
        theirs = self.create("fake-client-test", platform="gravitee-cloud-2")["gateway"]
        notes = {"PLATFORM_GRAVITEE_CLOUD_NOTES": "x", "PLATFORM_GRAVITEE_CLOUD_ENV_X": "x", "OTHER": "kept"}
        envfiles.write(".env", notes)
        second = ["PLATFORM_GRAVITEE_CLOUD_2_TOKEN", "PLATFORM_GRAVITEE_CLOUD_2_ENV_FAKE_CLIENT_TEST_TOKEN",
                  gateway_key(theirs["id"], "GRAVITEE_CLOUD_2")]
        self.assertEqual(sorted(envfiles.read(".env")), sorted([
            "GRAVITEE_LICENSE", KEY, DEV_KEY, gateway_key(mine["id"]), *second, *notes]))
        platforms.remove("gravitee-cloud")
        self.assertEqual(sorted(envfiles.read(".env")), sorted(["GRAVITEE_LICENSE", *second, *notes]))
        self.assertEqual([p["id"] for p in platforms.read()], ["gravitee-cloud-2"])
        platforms.remove("gravitee-cloud-2")
        self.assertEqual(sorted(envfiles.read(".env")), sorted(["GRAVITEE_LICENSE", *notes]))

    def test_a_platform_named_like_the_setting_of_another_keeps_it(self):
        """x has the Cloud Token PLATFORM_X_ENV_1_TOKEN, and x-env-1 has that name for its Account Token: the longer id
        owns what begins with it."""
        env = dict.fromkeys(["PLATFORM_X_TOKEN", "PLATFORM_X_ENV_9_TOKEN", "PLATFORM_X_GATEWAY_G_TOKEN",
                             "PLATFORM_X_ENV_1_TOKEN", "PLATFORM_X_ENV_1_GATEWAY_G_TOKEN", "PLATFORM_X_OTHER",
                             "PLATFORM_XY_TOKEN", "X_TOKEN"], "v")
        self.assertEqual(platforms._owned("x", env, ["x", "x-env-1", "xy"]),
                         ["PLATFORM_X_TOKEN", "PLATFORM_X_ENV_9_TOKEN", "PLATFORM_X_GATEWAY_G_TOKEN"])
        self.assertEqual(platforms._owned("x-env-1", env, ["x", "x-env-1", "xy"]),
                         ["PLATFORM_X_ENV_1_TOKEN", "PLATFORM_X_ENV_1_GATEWAY_G_TOKEN"])

    def test_the_add_on_keeps_the_token_it_holds(self):
        """The platform form deletes; turning off and emptying the add-on's setting are for the gateway's own delete."""
        self.add()
        self.create()
        held = self.held()
        platforms.remove("gravitee-cloud")
        self.assertEqual(self.held(), held)

    def test_what_a_refusal_remembered_is_forgotten(self):
        self.add()
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)
        with self.assertRaises(cloud.CloudError):
            platforms.prove("gravitee-cloud", DEV)
        self.assertEqual(platforms._refused, {("gravitee-cloud", DEV)})
        platforms.remove("gravitee-cloud")
        self.assertEqual(platforms._refused, set())


class Listing(Platforms):
    def test_a_token_that_is_not_kept_is_said(self):
        self.add()
        envfiles.write(".env", {"PLATFORM_GRAVITEE_CLOUD_TOKEN": None})
        self.assertEqual([p["tokenSet"] for p in platforms.listing()["platforms"]], [False])

    def test_nothing_registered(self):
        self.assertEqual(platforms.listing(), {"platforms": []})
        self.assertEqual((platforms.read(), self.fake.state.asked), ([], []))


class Check(Platforms):
    def test_what_the_platform_holds(self):
        self.add()
        found = platforms.check("gravitee-cloud")
        unset = {"state": "unset"}
        self.assertEqual(found, {"state": "ok", "message": "", "gateways": [], "orphan": None, "environments": [
            {"id": "fake-env-dev", "hrid": "", "name": "Development", "organizationId": "fake-organization",
             "token": unset, "installed": None},
            {"id": "fake-env-test", "hrid": "", "name": "Test", "organizationId": "fake-organization", "token": unset,
             "installed": None},
            {"id": "fake-env-prod", "hrid": "", "name": "Production", "organizationId": "fake-organization",
             "token": unset, "installed": None}],
            # What the account's plan and license say; the fake's license text cannot be decoded: not said
            "entitled": {"plan": {"gamma": False},
                         "organizations": {"fake-organization": {"scopes": ["APIM"], "license": None}}}})
        self.assertHidden(found)

    def test_the_gateways_with_their_connection_and_domains(self):
        self.add()
        self.fake.state.connect_after = 3600
        made = self.create(TEST, "lab")["gateway"]
        self.fake.state.gateways[made["id"]]["created"] -= 7200   # created long ago: it connected
        slow = self.create(DEV, "slow")["gateway"]
        found = {g["name"]: g for g in platforms.check("gravitee-cloud", PROJECT)["gateways"]}
        both = ["localhost:8090", "apim-hybrid-gateway:8082"]
        self.assertEqual(found["lab"], {"id": made["id"], "name": "lab", "environmentId": "fake-env-test",
                                        "type": "HYBRID", "scope": "APIM", "status": "DEPLOYED",
                                        "connection": "CONNECTED",
                                        "version": "4.12.20", "domains": both, "here": True, "kept": True})
        self.assertEqual((found["slow"]["id"], found["slow"]["connection"], found["slow"]["here"]),
                         (slow["id"], "NOT_CONNECTED", False))

    def test_without_the_project_nothing_is_known_to_run_here(self):
        self.add()
        made = self.create()["gateway"]
        (found,) = platforms.check("gravitee-cloud")["gateways"]
        self.assertEqual((found["id"], found["here"], found["kept"]), (made["id"], False, True))

    def test_a_token_the_platform_refuses_now(self):
        self.add()
        envfiles.write(".env", {"PLATFORM_GRAVITEE_CLOUD_TOKEN": "rotated-since"})
        found = platforms.check("gravitee-cloud")
        self.assertEqual(found, {"state": "invalid", "message": "Gravitee Cloud refuses this token",
                                 "environments": [], "gateways": []})

    def test_a_token_that_is_not_kept(self):
        self.add()
        envfiles.write(".env", {"PLATFORM_GRAVITEE_CLOUD_TOKEN": None})
        found = platforms.check("gravitee-cloud")
        self.assertEqual((found["state"], found["message"]),
                         ("invalid", "No token is kept for Gravitee Cloud: register it again"))

    def test_a_token_or_address_edited_by_hand_into_nonsense(self):
        self.add()
        envfiles.write(".env", {"PLATFORM_GRAVITEE_CLOUD_TOKEN": "'two words'"})
        self.assertEqual(platforms.check("gravitee-cloud")["state"], "invalid")

    def test_a_platform_that_does_not_answer(self):
        self.add()
        self.fake.shutdown()
        self.fake.server_close()
        found = platforms.check("gravitee-cloud")
        self.assertEqual(found["state"], "unreachable")
        self.assertIn("refuses the connection", found["message"])
        self.assertEqual((found["environments"], found["gateways"]), ([], []))

    def test_a_platform_that_is_no_cloud(self):
        address = self.served(lambda *asked: (200, "<html></html>", None))
        self.add()
        moved = {**platforms.read()[0], "url": address}
        self.write(platforms.FILE, json.dumps([moved]))
        found = platforms.check("gravitee-cloud")
        self.assertEqual((found["state"], found["gateways"]), ("unreachable", []))
        self.assertIn("does not answer like Gravitee Cloud", found["message"])

    def test_a_token_that_only_reads(self):
        self.add(token=fake_cloud.MEMBER_TOKEN)
        found = platforms.check("gravitee-cloud")
        self.assertEqual(found["state"], "ok")
        with self.assertRaisesRegex(cloud.CloudError, "not allowed"):
            self.create()
        self.assertEqual((self.fake.state.gateways, envfiles.read(ADDON_ENV)), ({}, {}))

    def test_one_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.check("nope")

    def test_an_id_that_is_no_segment_is_left_out(self):
        """What the Cloud calls an environment or a gateway reaches paths, settings and folders: letters, digits and
        dashes, or it is not listed."""
        def answer(method, path, headers, body):
            if path.endswith("/environments"):
                return 200, json.dumps([{"id": i, "name": i, "organizationId": "o"} for i in
                                        ("good-1", "../x", "a/b", "a.b", "a b", "", ".", "-a", "a" * 65, "é")]
                                       + [{"name": "no id"}]), None
            if "/gateways" in path:
                return 200, json.dumps([{"id": i, "name": "g", "environmentId": "good-1", "type": "HYBRID"}
                                        for i in ("gw-1", "../y", "a/b", "a_b", "", None)]), None
            return 200, json.dumps({"id": "acc", "name": "Odd"}), None
        platforms.add("Odd", "cloud", self.served(answer), "t", "acc")
        found = platforms.check("odd", PROJECT)
        self.assertEqual(([e["id"] for e in found["environments"]], [g["id"] for g in found["gateways"]]),
                         (["good-1"], ["gw-1"]))


class Entitlements(Platforms):
    """What the account's plan and licenses say, and what the Management API of an environment has: the data from which
    the Lab says what an environment can run."""

    NAMED = {**PROJECT, "groups": [{"name": "API Management", "product": "apim"},
                                                     {"name": "Access Management", "product": "am"}],
                                  "products": {}}

    def setUp(self):
        super().setUp()
        self.add()

    def read(self):
        return platforms.fetch(platforms.find("gravitee-cloud"))

    def test_the_plan_and_the_scopes_of_the_organizations_are_read(self):
        found = self.read()["entitled"]
        self.assertEqual(found["plan"], {"gamma": False})
        self.assertEqual(found["organizations"]["fake-organization"]["scopes"], ["APIM"])
        self.fake.state.subscription = {**self.fake.state.subscription, "gammaEnabled": True}
        self.fake.state.scopes = ["APIM", "AM"]
        platforms._kept.clear()
        found = self.read()["entitled"]
        self.assertEqual((found["plan"], found["organizations"]["fake-organization"]["scopes"]),
                         ({"gamma": True}, ["AM", "APIM"]))

    def test_gamma_and_access_management_are_products_an_environment_has_when_the_account_is_entitled(self):
        self.create()
        self.fake.state.subscription = {**self.fake.state.subscription, "gammaEnabled": True}
        self.fake.state.scopes = ["APIM", "AM"]
        found = self.read()
        offered = platforms.capabilities(self.NAMED, found, DEV)
        self.assertEqual(offered["products"], {"API Management": "4.12.20", "Access Management": "", "Gamma": ""})
        # Without the plan, without the scope: neither
        self.fake.state.subscription = {**self.fake.state.subscription, "gammaEnabled": False}
        self.fake.state.scopes = ["APIM"]
        platforms._kept.clear()
        self.assertEqual(platforms.capabilities(self.NAMED, self.read(), DEV)["products"], {"API Management": "4.12.20"})

    def test_the_management_api_is_read_with_the_cloud_token_of_the_environment_and_only_with_it(self):
        self.create()
        self.assertIsNone(next(e for e in self.read()["environments"] if e["id"] == DEV)["installed"])
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        found = next(e for e in self.read()["environments"] if e["id"] == DEV)
        self.assertEqual(found["installed"]["plugins"], {
            "policy": {"rate-limit": True, "policy-interrupt": True, "ai-prompt-guard-rails": False},
            "endpoint": {"http-proxy": True, "llm-proxy": False},
            "entrypoint": {"http-proxy": True, "llm-proxy": False, "mcp": False},
            "resource": {"cache": True, "ai-model-text-embedding": False}})
        self.assertEqual(found["installed"]["license"]["tier"], "universe")
        offered = platforms.capabilities(self.NAMED, self.read(), DEV)
        self.assertEqual((offered["installed"] is not None, offered["license"]["tier"]), (True, "universe"))
        self.assertNotIn("fake-cloud-token", json.dumps(self.read(), default=str), "no answer holds a token")

    def test_what_is_read_is_kept_for_a_while_and_what_the_cloud_did_not_say_is_asked_again(self):
        self.create()
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        self.read()
        before = len(self.fake.state.asked)
        self.read()
        plugin_reads = [path for _, path in self.fake.state.asked[before:] if "/plugins/" in path]
        self.assertEqual(plugin_reads, [], "the plugins are kept")
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)   # the Cloud refuses the token now
        platforms._kept.clear()
        self.assertIsNone(next(e for e in self.read()["environments"] if e["id"] == DEV)["installed"])


class Capabilities(Platforms):
    """What an environment runs, as its gateways say it in a read of the platform: nothing else is claimed."""

    NAMED = {**PROJECT, "groups": [{"name": "API Management", "product": "apim"}, {"name": "Databases", "product": None}],
             "products": {"am": {"name": "Access Management", "services": {}}}}

    def setUp(self):
        super().setUp()
        self.add()

    def runs(self, environment=DEV, project=NAMED):
        return platforms.capabilities(project, platforms.check("gravitee-cloud", project), environment)

    def hosted(self, scope, version, environment=DEV):
        """A gateway that Cloud hosts in an environment, as it lists one."""
        uid = f"hosted-{scope.lower()}-{len(self.fake.state.gateways)}"
        self.fake.state.gateways[uid] = {"id": uid, "accountId": ACCOUNT, "organizationId": "fake-organization",
                                         "environmentId": environment, "name": uid, "type": "SAAS", "scope": scope,
                                         "domains": [], "status": "DEPLOYED", "version": version,
                                         "created": time.monotonic()}

    def test_one_gateway_says_the_product_and_its_version(self):
        self.create()
        self.assertEqual(self.runs(), {"products": {"API Management": "4.12.20"}, "plugins": False, "detail": "",
                                       "installed": None, "license": None})

    def test_the_oldest_of_several_gateways_counts_and_the_detail_says_so(self):
        self.create()
        self.fake.state.version = "4.13.1"
        self.create(name="later")
        self.hosted("APIM", "4.12.9")
        found = self.runs()
        self.assertEqual(found["products"], {"API Management": "4.12.9"})
        self.assertEqual(found["detail"], "API Management: the gateways run 4.12.9, 4.12.20, 4.13.1, the oldest counts")

    def test_a_gateway_of_access_management_is_that_product(self):
        self.hosted("AM", "4.10.3")
        self.assertEqual(self.runs()["products"], {"Access Management": "4.10.3"}, "named as the project names it")
        self.assertEqual(self.runs(project=PROJECT)["products"], {"AM": "4.10.3"}, "else in the scope's own words")
        self.create()
        self.assertEqual(self.runs()["products"], {"Access Management": "4.10.3", "API Management": "4.12.20"})

    def test_an_environment_without_a_gateway_is_not_set_up(self):
        self.create(TEST)
        self.assertIsNone(self.runs(DEV), "the gateway of another environment says nothing of this one")
        self.assertIsNotNone(self.runs(TEST))

    def test_a_read_that_fails_says_nothing_and_keeps_its_state(self):
        self.create()
        envfiles.write(".env", {KEY: "rotated-since"})
        found = platforms.check("gravitee-cloud", self.NAMED)
        self.assertEqual(found["state"], "invalid")
        self.assertIsNone(platforms.capabilities(self.NAMED, found, DEV))
        unreachable = {"state": "unreachable", "message": "Unreachable", "environments": [], "gateways": []}
        self.assertIsNone(platforms.capabilities(self.NAMED, unreachable, DEV))

    def test_plugins_are_what_the_gateway_of_the_add_on_mounts(self):
        self.create()
        mounted = {**self.NAMED, "products": {"apim": {"name": "API Management", "folder": "plugins/apim",
                                                       "services": {"apim-hybrid-gateway": "/opt/plugins"}}}}
        self.assertTrue(self.runs(project=mounted)["plugins"])
        self.assertFalse(platforms.plugins({**mounted, "addons": {}}, "cloud"), "no add-on runs a gateway here")


class Keys(Case):
    def test_the_names_of_the_settings(self):
        self.assertEqual(platforms._key("gravitee-cloud"), "PLATFORM_GRAVITEE_CLOUD_TOKEN")
        self.assertEqual(platforms._key("gravitee-cloud", "ENV", "1b4548fc-00ab"),
                         "PLATFORM_GRAVITEE_CLOUD_ENV_1B4548FC_00AB_TOKEN")
        self.assertEqual(platforms._key("a", "GATEWAY", "Gw-9"), "PLATFORM_A_GATEWAY_GW_9_TOKEN")

    def test_an_id_is_a_segment_of_letters_digits_and_dashes(self):
        for good in ("fake-env-dev", "1b4548fc-00ab-4c1d-9e0f-123456789abc", "A1", "a", "0", "a" * 64):
            self.assertTrue(platforms.ID.fullmatch(good), good)
            platforms._key("p", "ENV", good)
        for bad in ("", "-a", "a b", "a/b", "a.b", "a_b", "..", ".", "%2e", "a\n", "a" * 65, "é", None, 4, ["a"]):
            self.assertFalse(isinstance(bad, str) and platforms.ID.fullmatch(bad), repr(bad))
            with self.assertRaisesRegex(ValueError, "^Not an id of Gravitee Cloud$"):
                platforms._key("p", "ENV", bad)

    def test_removed_constants(self):
        self.assertFalse(hasattr(platforms, "TOKEN_SETTING") or hasattr(platforms, "DEFAULT_DOMAIN"))


class GateAddress(Case):
    """Where Cloud Gate is: the region of the token when the platform is Gravitee Cloud, else the platform's own."""

    def found(self, cpg="eu"):
        return {"cpg": cpg}

    def test_gravitee_cloud_has_a_gate_by_region_that_the_token_names(self):
        for region in ("eu", "us", "ap-1"):
            with self.subTest(region=region):
                self.assertEqual(platforms.gate({"url": "https://eu.cloud.gravitee.io"}, self.found(region)),
                                 f"https://{region}.cloudgate.gravitee.io")

    def test_a_token_that_names_no_region_is_no_cloud_token(self):
        with self.assertRaisesRegex(cloud.CloudError, "^This is not a Cloud Token$"):
            platforms.gate({"url": "https://eu.cloud.gravitee.io"}, self.found(None))

    def test_any_other_address_is_its_own_gate(self):
        """A token of the fake says eu too: it must not be sent to the real Gate."""
        for url in ("http://127.0.0.1:9990", "https://cloud.gravitee.io", "https://gravitee.example.com/cloud",
                    "https://evil.test/.cloud.gravitee.io", "https://eu.cloud.gravitee.io.evil.test",
                    "https://evilcloud.gravitee.io"):
            with self.subTest(url=url):
                self.assertEqual(platforms.gate({"url": url}, self.found("eu")), url)
                self.assertEqual(platforms.gate({"url": url}, self.found(None)), url)


class CloudTokens(Platforms):
    def setUp(self):
        super().setUp()
        self.add()

    def keep(self, token=fake_cloud.CLOUD_TOKEN, environment=DEV):
        return platforms.set_cloud_token("gravitee-cloud", environment, token)

    def gate_asked(self):
        return [asked for asked in self.fake.state.asked if asked[1].startswith(cloud.GATE)]

    def token_of(self, environment=DEV):
        return {e["id"]: e["token"] for e in platforms.check("gravitee-cloud")["environments"]}[environment]

    def test_a_cloud_token_is_kept_for_its_environment_after_one_question_to_cloud_gate(self):
        self.assertEqual(self.keep(), {})
        self.assertEqual(envfiles.read(".env")[DEV_KEY], fake_cloud.CLOUD_TOKEN)
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)
        self.assertEqual(self.gate_asked(), [("GET", f"{cloud.GATE}/v2/organizations/fake-organization/environments/"
                                                     f"{DEV}/apis")])
        self.assertFalse(fake_cloud.CLOUD_TOKEN in self.text(platforms.FILE), "the token is in the record")
        self.assertEqual(envfiles.read(".env")[KEY], fake_cloud.TOKEN, "the Account Token stays")

    def test_the_gate_asked_is_the_platforms_own_for_an_address_that_is_not_the_cloud(self):
        """The token of the fake says eu: it is not sent to the real Cloud Gate, or this would not reach the fake."""
        self.keep()
        self.assertEqual(len(self.gate_asked()), 1)

    def test_a_token_is_kept_without_the_spaces_around_it(self):
        self.keep(f"  {fake_cloud.CLOUD_TOKEN}\n")
        self.assertEqual(envfiles.read(".env")[DEV_KEY], fake_cloud.CLOUD_TOKEN)

    def test_the_environment_says_it_is_valid_until_the_day_the_token_says(self):
        self.assertEqual(self.token_of(), {"state": "unset"})
        self.keep()
        found = self.token_of()
        self.assertEqual(found, {"state": "valid", "expires": cloud.claims(fake_cloud.CLOUD_TOKEN)["expiry"]})
        self.assertEqual(self.token_of(TEST), {"state": "unset"})

    def test_a_token_replaces_the_one_before(self):
        self.keep()
        newer = fake_cloud.cloud_token(DEV)
        self.keep(newer)
        self.assertEqual(envfiles.read(".env")[DEV_KEY], newer)
        self.assertEqual(self.token_of()["state"], "valid")

    def test_each_environment_has_its_own_token(self):
        self.keep()
        self.keep(fake_cloud.CLOUD_TOKEN_TEST, TEST)
        self.assertEqual(sorted(key for key in envfiles.read(".env") if "_ENV_" in key),
                         [DEV_KEY, "PLATFORM_GRAVITEE_CLOUD_ENV_FAKE_ENV_TEST_TOKEN"])

    def test_a_token_that_is_not_right_is_refused_in_plain_words(self):
        for token, environment, words in [
                (fake_cloud.CLOUD_TOKEN_TEST, DEV, "This Cloud Token is for Test, not Development"),
                (fake_cloud.CLOUD_TOKEN, TEST, "This Cloud Token is for Development, not Test"),
                (fake_cloud.SECOND_CLOUD_TOKEN, DEV, "This Cloud Token is not for an environment of Gravitee Cloud"),
                (fake_cloud.GATEWAY_TOKEN, DEV, "This is a gateway token, not a Cloud Token"),
                (fake_cloud.ACCOUNT_JWT, DEV, "This is not a Cloud Token"),
                (fake_cloud.TOKEN, DEV, "This is not a Cloud Token"),
                ("4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46", DEV, "This is not a Cloud Token"),
                ("", DEV, "This is not a Cloud Token"), (None, DEV, "This is not a Cloud Token"),
                (fake_cloud.CLOUD_TOKEN_EXPIRED, DEV, "This Cloud Token has expired")]:
            with self.subTest(words=words, token=token is not None and token[:6]), \
                    self.assertRaises(cloud.CloudError) as caught:
                self.keep(token, environment)
            self.assertEqual((str(caught.exception), caught.exception.kind), (words, "invalid"))
            self.assertHidden(str(caught.exception))
        self.assertNotIn(DEV_KEY, envfiles.read(".env"))
        self.assertEqual(self.gate_asked(), [], "a token that is plainly wrong is not sent to Cloud Gate")

    def test_an_environment_the_platform_does_not_list_is_not_one(self):
        for environment in ("fake-env-nope", "", None, "a/b", "../x", 4):
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, "^Choose an environment$"):
                self.keep(environment=environment)
        self.assertEqual(self.gate_asked(), [])

    def test_a_token_that_cloud_gate_refuses_is_not_kept(self):
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)
        with self.assertRaisesRegex(cloud.CloudError, "^Gravitee Cloud refuses this token$"):
            self.keep()
        self.assertNotIn(DEV_KEY, envfiles.read(".env"))

    def test_a_cloud_token_that_cannot_read_api_management(self):
        self.patch(cloud.Gate, "read", side_effect=cloud.CloudError("This Cloud Token cannot read API Management",
                                                                    "invalid", 403))
        with self.assertRaisesRegex(cloud.CloudError, "cannot read API Management"):
            self.keep()
        self.assertNotIn(DEV_KEY, envfiles.read(".env"))

    def test_cloud_gate_that_does_not_answer_is_said(self):
        self.patch(cloud.Gate, "read", side_effect=cloud.CloudError("localhost does not answer in time"))
        with self.assertRaisesRegex(cloud.CloudError, "does not answer in time") as caught:
            self.keep()
        self.assertEqual(caught.exception.kind, "unreachable")

    def test_an_account_token_that_the_cloud_refuses_is_named_so(self):
        """The environments are asked of the Account API: its refusal is not the Cloud Token's."""
        envfiles.write(".env", {KEY: "rotated-since"})
        with self.assertRaisesRegex(cloud.CloudError, "^Gravitee Cloud refuses the Account Token of Gravitee Cloud$"):
            self.keep()
        self.assertEqual(self.gate_asked(), [])

    def test_a_platform_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.set_cloud_token("nope", DEV, fake_cloud.CLOUD_TOKEN)
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.delete_cloud_token("nope", DEV)

    def test_a_token_that_expired_since_is_said_and_is_not_valid(self):
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        found = self.token_of()
        self.assertEqual((found["state"], found["expires"] < time.time()), ("expired", True))

    def test_a_token_that_cloud_gate_refused_stays_refused_until_it_is_replaced(self):
        self.keep()
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)
        with self.assertRaisesRegex(cloud.CloudError, "refuses this token"):
            platforms.prove("gravitee-cloud", DEV)
        self.assertEqual(self.token_of()["state"], "refused")
        self.assertEqual(self.token_of()["state"], "refused", "it is remembered, not asked again")
        self.keep(fake_cloud.cloud_token(DEV))
        self.assertEqual(self.token_of()["state"], "valid")

    def test_a_forgotten_token_is_not_refused_any_more(self):
        self.keep()
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)
        with self.assertRaises(cloud.CloudError):
            platforms.prove("gravitee-cloud", DEV)
        platforms.delete_cloud_token("gravitee-cloud", DEV)
        self.assertEqual(self.token_of(), {"state": "unset"})
        self.keep(fake_cloud.cloud_token(DEV))
        self.assertEqual(self.token_of()["state"], "valid")

    def test_a_token_is_forgotten_on_this_machine_only(self):
        self.keep()
        self.keep(fake_cloud.CLOUD_TOKEN_TEST, TEST)
        before = len(self.fake.state.asked)
        self.assertEqual(platforms.delete_cloud_token("gravitee-cloud", DEV), {})
        self.assertNotIn(DEV_KEY, envfiles.read(".env"))
        self.assertEqual(sum("_ENV_" in key for key in envfiles.read(".env")), 1)
        self.assertEqual(envfiles.read(".env")[KEY], fake_cloud.TOKEN)
        self.assertEqual(len(self.fake.state.asked), before, "nothing is asked of the platform")
        self.assertEqual(platforms.delete_cloud_token("gravitee-cloud", DEV), {}, "forgetting twice is not an error")

    def test_an_environment_that_is_no_segment_is_no_token_to_forget(self):
        for environment in ("", "a/b", "..", None):
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, "Not an id"):
                platforms.delete_cloud_token("gravitee-cloud", environment)

    def test_no_token_in_anything_it_answers_says_or_keeps(self):
        seen = [self.keep(), platforms.check("gravitee-cloud"), platforms.listing()]
        for token, environment in [(fake_cloud.CLOUD_TOKEN_TEST, DEV), (fake_cloud.GATEWAY_TOKEN, DEV),
                                   (fake_cloud.CLOUD_TOKEN_EXPIRED, DEV), (fake_cloud.TOKEN, DEV),
                                   (fake_cloud.CLOUD_TOKEN, "nope")]:
            with self.assertRaises(ValueError) as caught:
                self.keep(token, environment)
            seen.append(str(caught.exception))
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)
        with self.assertRaises(ValueError) as caught:
            platforms.prove("gravitee-cloud", DEV)
        seen.append(str(caught.exception))
        self.assertHidden(seen, self.text(platforms.FILE))


class Prove(Platforms):
    def setUp(self):
        super().setUp()
        self.add()

    def test_one_question_to_cloud_gate_and_the_claims_of_the_token(self):
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        before = len(self.fake.state.asked)
        found = platforms.prove("gravitee-cloud", DEV)
        self.assertEqual((found["kind"], found["organization"], found["environment"]),
                         ("cloud", "fake-organization", DEV))
        self.assertEqual(len(self.fake.state.asked), before + 1)

    def test_no_token_kept(self):
        with self.assertRaisesRegex(ValueError, "^No Cloud Token is kept for this environment$"):
            platforms.prove("gravitee-cloud", DEV)

    def test_a_token_that_expired_is_not_sent(self):
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        with self.assertRaisesRegex(cloud.CloudError, "^This Cloud Token has expired$"):
            platforms.prove("gravitee-cloud", DEV)
        self.assertEqual(self.fake.state.asked, [("GET", f"/accounts/{ACCOUNT}")], "only registering asked")

    def test_a_token_that_is_not_one_for_the_environment_it_is_kept_under(self):
        """Edited by hand: another environment's token, a gateway's, a word."""
        for kept in (fake_cloud.CLOUD_TOKEN_TEST, fake_cloud.GATEWAY_TOKEN, "words"):
            envfiles.write(".env", {DEV_KEY: kept})
            with self.subTest(kept=kept[:6]), self.assertRaisesRegex(ValueError, "is not one for it"):
                platforms.prove("gravitee-cloud", DEV)

    def test_cloud_gate_that_is_not_reached_does_not_refuse_the_token(self):
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        self.patch(cloud.Gate, "read", side_effect=cloud.CloudError("localhost does not answer in time"))
        with self.assertRaises(cloud.CloudError):
            platforms.prove("gravitee-cloud", DEV)
        self.assertEqual(platforms._refused, set())

    def test_a_platform_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.prove("nope", DEV)


class Gateways(Platforms):
    def setUp(self):
        super().setUp()
        self.add()

    def test_a_hybrid_gateway_is_created_with_the_domains_of_the_add_on_and_its_token_kept(self):
        answer = self.create()
        self.assertEqual(set(answer), {"gateway", "tokenSaved"})
        self.assertTrue(answer["tokenSaved"])
        both = ["localhost:8090", "apim-hybrid-gateway:8082"]
        self.assertEqual(answer["gateway"], {"id": answer["gateway"]["id"], "name": "lab", "type": "HYBRID",
                                             "scope": "APIM", "environmentId": "fake-env-dev", "status": "DEPLOYED",
                                             "connection": "UNKNOWN_HYBRID", "version": "4.12.20", "domains": both,
                                             "here": True, "kept": True})
        (made,) = self.fake.state.gateways.values()
        self.assertEqual([item["domain"] for item in made["domains"]], both, "the Cloud keeps both")
        (issued,) = self.fake.state.tokens.values()
        # In the .env of the add-on, which its form shows as the value of the setting
        self.assertEqual(envfiles.read(ADDON_ENV), {"HYBRID_CLOUD_TOKEN": issued})
        shown = {v["key"]: v["value"] for s in files.settings(PROJECT, ADDON_ENV)["sections"]
                 for g in s["groups"] for v in g["vars"]}
        self.assertEqual(shown, {"HYBRID_CLOUD_TOKEN": issued})
        self.assertEqual(stat.S_IMODE((self.root / ADDON_ENV).stat().st_mode), 0o600)
        self.assertHidden(answer)

    def test_the_token_is_kept_under_the_gateway_in_the_core_env_too(self):
        gateway = self.create()["gateway"]
        (issued,) = self.fake.state.tokens.values()
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "keep-me", KEY: fake_cloud.TOKEN,
                                                 gateway_key(gateway["id"]): issued})
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)
        self.assertEqual(cloud.claims(issued)["gateway"], gateway["id"])

    def test_the_other_settings_of_the_add_on_stay(self):
        self.write(ADDON_ENV, "# why\nHYBRID_GATEWAY_VERSION=4.12.1\n")
        self.create()
        (issued,) = self.fake.state.tokens.values()
        self.assertEqual(self.text(ADDON_ENV), f"# why\nHYBRID_GATEWAY_VERSION=4.12.1\nHYBRID_CLOUD_TOKEN={issued}\n")

    def test_the_other_settings_of_the_core_stay(self):
        self.write(".env", "# a note\nGRAVITEE_LICENSE=keep-me\nPLATFORM_GRAVITEE_CLOUD_TOKEN=fake-account-token\n")
        before = self.text(".env")
        gateway = self.create()["gateway"]
        (issued,) = self.fake.state.tokens.values()
        self.assertEqual(self.text(".env"), before + f"{gateway_key(gateway['id'])}={issued}\n")

    def test_a_gateway_made_while_one_runs_here_does_not_take_its_place(self):
        first = self.create(DEV, "first")["gateway"]
        held = self.held()
        second = self.create(TEST, "second")["gateway"]
        self.assertEqual((first["here"], second["here"], second["kept"]), (True, False, True))
        self.assertEqual(self.held(), held, "the add-on still holds the first")
        tokens = {k: v for k, v in envfiles.read(".env").items() if "_GATEWAY_" in k}
        self.assertEqual(sorted(tokens), sorted([gateway_key(first["id"]), gateway_key(second["id"])]))
        self.assertEqual(cloud.claims(tokens[gateway_key(second["id"])])["gateway"], second["id"])
        self.assertEqual(cloud.claims(held)["gateway"], first["id"])

    def test_a_token_the_add_on_holds_already_is_not_replaced_whatever_it_is(self):
        self.write(ADDON_ENV, "HYBRID_CLOUD_TOKEN=old\n")
        gateway = self.create()["gateway"]
        self.assertEqual((self.held(), gateway["here"], gateway["kept"]), ("old", False, True))

    def test_what_cannot_be_asked_is_not_asked(self):
        for fields, words in [(dict(name="x"), "2 to 128"), (dict(name=""), "2 to 128"), (dict(name=None), "2 to 128"),
                              (dict(name="lab!"), "2 to 128"), (dict(name="x" * 129), "2 to 128"),
                              (dict(environment=""), "Choose an environment"),
                              (dict(environment=None), "Choose an environment"),
                              (dict(environment="a/b"), "Choose an environment"),
                              (dict(environment="../x"), "Choose an environment")]:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, words):
                self.create(**fields)
        self.assertEqual((self.fake.state.gateways, envfiles.read(ADDON_ENV)), ({}, {}))

    def test_a_name_the_platform_checks_the_same_way(self):
        self.create(name="  My lab-2_x.y  ")
        self.assertEqual([g["name"] for g in self.fake.state.gateways.values()], ["My lab-2_x.y"])

    def test_an_environment_the_platform_does_not_know(self):
        with self.assertRaisesRegex(cloud.CloudError, "refuses it: Bad request: environmentId"):
            self.create(environment="fake-env-nope")
        self.assertEqual((self.fake.state.gateways, envfiles.read(ADDON_ENV)), ({}, {}))

    def test_without_the_add_on_nothing_is_created(self):
        for addons in ({"kafka": PROJECT["addons"]["kafka"]},
                       {"hybrid": {**PROJECT["addons"]["hybrid"], "gateway": {**GATEWAY, "platform": "other"}}}):
            with self.subTest(addons=sorted(addons)), \
                    self.assertRaisesRegex(paths.NotFound, "^No add-on runs a gateway of Gravitee Cloud$"):
                self.create(project={**PROJECT, "addons": addons})
        self.assertEqual(self.fake.state.gateways, {})

    def test_the_add_on_is_found_by_the_platform_it_says_and_not_by_its_name(self):
        project = {**PROJECT, "addons": {"zz-other": {**PROJECT["addons"]["hybrid"], "env_file": "addons/zz/.env",
                                                      "gateway": {**GATEWAY, "token": "ZZ_TOKEN"}},
                                         "hybrid": {"env_file": ADDON_ENV, "settings": [], "gateway": None}}}
        self.write("addons/zz/compose.yml")
        self.create(project=project)
        (issued,) = self.fake.state.tokens.values()
        self.assertEqual(envfiles.read("addons/zz/.env"), {"ZZ_TOKEN": issued})
        self.assertFalse((self.root / ADDON_ENV).exists())

    def test_a_token_that_cannot_be_kept_removes_the_gateway_again(self):
        self.patch(envfiles, "write", side_effect=OSError(errno.ENOSPC, "No space left on device"))
        saved = "could not be saved .No space left on device.: the gateway was removed"
        with self.assertRaisesRegex(RuntimeError, saved) as caught:
            self.create()
        self.assertEqual(self.fake.state.gateways, {})
        self.assertHidden(str(caught.exception))
        self.assertFalse((self.root / ADDON_ENV).exists())

    def test_a_token_that_cannot_be_kept_for_the_add_on_is_not_kept_for_the_gateway_either(self):
        real = envfiles.write

        def write(path, changes):
            if path == ADDON_ENV:
                raise OSError(errno.ENOSPC, "No space left on device")
            real(path, changes)
        self.patch(envfiles, "write", write)
        saved = "could not be saved .No space left on device.: the gateway was removed"
        with self.assertRaisesRegex(RuntimeError, saved):
            self.create()
        self.assertEqual(self.fake.state.gateways, {})
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "keep-me", KEY: fake_cloud.TOKEN})

    def test_whatever_makes_the_write_fail_removes_the_gateway_again(self):
        write = self.patch(envfiles, "write")
        for error in (ValueError("words with the token in them"), TypeError("x"), KeyError("k"),
                      UnicodeEncodeError("utf-8", "é", 0, 1, "surrogates not allowed"), OSError("no reason given")):
            write.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(RuntimeError) as caught:
                self.create()
            message = str(caught.exception)
            kind = "OSError" if type(error) is OSError else type(error).__name__
            self.assertEqual(message, f"The gateway's token could not be saved ({kind}): the gateway was removed "
                                      "from the platform")
            self.assertEqual(self.fake.state.gateways, {})
            self.assertHidden(message)

    def test_a_gateway_that_cannot_be_removed_either_is_named(self):
        self.patch(envfiles, "write", side_effect=OSError(errno.ENOSPC, "No space left on device"))
        for error in (cloud.CloudError("Gravitee Cloud refuses this token", "invalid"), ValueError("Not an id")):
            self.patch(cloud.Cloud, "delete_gateway", side_effect=error)
            with self.subTest(error=type(error).__name__), self.assertRaises(RuntimeError) as caught:
                self.create()
            self.assertEqual(str(caught.exception), "The gateway's token could not be saved (No space left on "
                                                    "device): delete the gateway in the platform")
        self.assertEqual(len(self.fake.state.gateways), 2, "they are still there, and the person is told")
        self.assertHidden(str(caught.exception))

    def test_a_gateway_the_cloud_gives_an_id_that_is_no_segment_is_removed_again(self):
        asked = []

        def answer(method, path, headers, body):
            asked.append((method, path))
            if method == "POST":
                return 200, json.dumps({"gateway": {"id": "../x", "name": "lab"}, "token": "gw-secret-token-1"}), None
            return 200, json.dumps({"id": "acc", "name": "Odd"}) if method == "GET" else "", None
        platforms.add("Odd", "cloud", self.served(answer), "t", "acc")
        with self.assertRaisesRegex(RuntimeError, r"^The gateway's token could not be saved \(ValueError\): "
                                                  "the gateway was removed from the platform$"):
            self.create(platform="odd")
        self.assertEqual(asked[-1][0], "DELETE")
        self.assertEqual(sorted(envfiles.read(".env")), ["GRAVITEE_LICENSE", KEY, "PLATFORM_ODD_TOKEN"])
        self.assertEqual(envfiles.read(ADDON_ENV), {})

    def test_a_platform_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            self.create(platform="nope")

    def test_no_token_in_anything_it_answers_says_or_keeps(self):
        seen = [self.create(), platforms.check("gravitee-cloud", PROJECT), platforms.listing()]
        for call in (lambda: self.create(name="x"), lambda: self.create(environment="nope"),
                     lambda: platforms.delete_gateway(PROJECT, "gravitee-cloud", "nope")):
            with self.assertRaises(ValueError) as caught:
                call()
            seen.append(str(caught.exception))
        self.assertHidden(seen, self.text(platforms.FILE))


class Swap(Platforms):
    """Two gateways, the first of which runs here."""

    def setUp(self):
        super().setUp()
        self.add()
        self.first, self.second = self.create(DEV, "first")["gateway"], self.create(TEST, "second")["gateway"]

    def here(self):
        return {g["name"]: g["here"] for g in platforms.check("gravitee-cloud", PROJECT)["gateways"]}

    def test_the_kept_token_becomes_the_one_the_add_on_holds(self):
        self.assertEqual(self.here(), {"first": True, "second": False})
        self.assertEqual(platforms.swap_gateway(PROJECT, "gravitee-cloud", self.second["id"]), {})
        self.assertEqual(cloud.claims(self.held())["gateway"], self.second["id"])
        self.assertEqual(self.here(), {"first": False, "second": True})
        self.assertEqual(envfiles.read(ADDON_ENV).keys(), {"HYBRID_CLOUD_TOKEN"})

    def test_the_gateway_that_was_here_stays_kept_and_can_come_back(self):
        platforms.swap_gateway(PROJECT, "gravitee-cloud", self.second["id"])
        found = {g["name"]: g for g in platforms.check("gravitee-cloud", PROJECT)["gateways"]}
        self.assertEqual((found["first"]["here"], found["first"]["kept"]), (False, True))
        platforms.swap_gateway(PROJECT, "gravitee-cloud", self.first["id"])
        self.assertEqual(self.here(), {"first": True, "second": False})

    def test_the_swap_asks_nothing_of_the_platform(self):
        before = len(self.fake.state.asked)
        platforms.swap_gateway(PROJECT, "gravitee-cloud", self.second["id"])
        self.assertEqual(len(self.fake.state.asked), before)

    def test_a_gateway_whose_token_is_not_kept_cannot_be_swapped_to(self):
        held = self.held()
        envfiles.write(".env", {gateway_key(self.second["id"]): None})
        self.assertEqual({g["name"]: g["kept"] for g in platforms.check("gravitee-cloud", PROJECT)["gateways"]},
                         {"first": True, "second": False})
        with self.assertRaisesRegex(ValueError, "^Its token is not kept here$"):
            platforms.swap_gateway(PROJECT, "gravitee-cloud", self.second["id"])
        self.assertEqual(self.held(), held)

    def test_what_cannot_be_swapped(self):
        for call, error, words in [
                (lambda: platforms.swap_gateway(PROJECT, "nope", self.second["id"]), paths.NotFound,
                 "No platform nope"),
                (lambda: platforms.swap_gateway(PROJECT, "gravitee-cloud", "../x"), ValueError, "Not an id"),
                (lambda: platforms.swap_gateway(PROJECT, "gravitee-cloud", "unknown-one"), ValueError, "not kept"),
                (lambda: platforms.swap_gateway({"addons": {}}, "gravitee-cloud", self.second["id"]), paths.NotFound,
                 "No add-on runs a gateway")]:
            with self.assertRaisesRegex(error, words):
                call()

    def test_no_token_in_anything_it_answers_says_or_keeps(self):
        seen = [platforms.swap_gateway(PROJECT, "gravitee-cloud", self.second["id"]),
                platforms.check("gravitee-cloud", PROJECT)]
        self.assertHidden(seen, self.text(platforms.FILE))


class Orphan(Platforms):
    """The gateway that runs here is deleted in the platform: the add-on keeps its token."""

    def setUp(self):
        super().setUp()
        self.add()
        self.made = self.create(DEV, "first")["gateway"]

    def deleted(self):
        """Deletes the gateway where it lives, as its console does, so that the Lab is not told."""
        record = platforms.find("gravitee-cloud")
        platforms._client(record).delete_gateway(ACCOUNT, self.made["id"])

    def test_a_gateway_that_the_platform_lists_is_no_orphan(self):
        self.assertIsNone(platforms.check("gravitee-cloud", PROJECT)["orphan"])

    def test_the_gateway_the_platform_forgot_is_said_with_its_environment(self):
        self.deleted()
        found = platforms.check("gravitee-cloud", PROJECT)
        self.assertEqual(found["orphan"], {"gateway": self.made["id"], "environmentId": DEV})
        self.assertEqual(found["gateways"], [])
        self.assertHidden(found)

    def test_nothing_is_said_without_the_project_or_a_token_held(self):
        self.deleted()
        self.assertIsNone(platforms.check("gravitee-cloud")["orphan"])
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": None})
        self.assertIsNone(platforms.check("gravitee-cloud", PROJECT)["orphan"])

    def test_a_gateway_of_an_environment_the_platform_does_not_list_is_none_of_its_own(self):
        """The token the add-on holds may be of another account's gateway: this platform says nothing of it."""
        other = self.second()
        self.assertIsNone(platforms.check(other["id"], PROJECT)["orphan"])

    def test_a_platform_that_does_not_answer_says_none(self):
        self.assertNotIn("orphan", platforms.annotate(platforms.find("gravitee-cloud"),
                                                      {"state": "unreachable", "message": "x", "environments": [],
                                                       "gateways": []}, PROJECT))


class Cascades(Platforms):
    def setUp(self):
        super().setUp()
        self.add()

    def test_a_gateway_goes_from_the_platform_first_and_then_its_token(self):
        gateway = self.create()["gateway"]
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": "elsewhere"})   # the add-on holds another
        self.assertEqual(platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"]), {})
        self.assertEqual(self.fake.state.gateways, {})
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "keep-me", KEY: fake_cloud.TOKEN})
        self.assertEqual(self.held(), "elsewhere", "the add-on holds another gateway's token: it stays")

    def test_the_gateway_that_runs_here_leaves_the_add_on_without_a_token(self):
        gateway = self.create()["gateway"]
        self.write(ADDON_ENV, f"HYBRID_GATEWAY_VERSION=4.12.1\nHYBRID_CLOUD_TOKEN={self.held()}\n")
        platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"])
        self.assertEqual(self.text(ADDON_ENV), "HYBRID_GATEWAY_VERSION=4.12.1\n")

    def test_the_setting_is_emptied_by_the_claims_of_the_token_even_when_the_lab_did_not_keep_it(self):
        gateway = self.create()["gateway"]
        envfiles.write(".env", {gateway_key(gateway["id"]): None})
        platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"])
        self.assertEqual(envfiles.read(ADDON_ENV), {})

    def test_another_gateway_leaves_the_one_here_alone(self):
        first, second = self.create(DEV, "first")["gateway"], self.create(TEST, "second")["gateway"]
        held = self.held()
        platforms.delete_gateway(PROJECT, "gravitee-cloud", second["id"])
        self.assertEqual(self.held(), held)
        self.assertEqual(sorted(k for k in envfiles.read(".env") if "_GATEWAY_" in k), [gateway_key(first["id"])])

    def test_a_gateway_the_cloud_does_not_delete_keeps_everything(self):
        gateway = self.create()["gateway"]
        self.fake.state.gateways.clear()   # deleted in the console
        before = (envfiles.read(".env"), envfiles.read(ADDON_ENV))
        with self.assertRaisesRegex(cloud.CloudError, "^Not found in Gravitee Cloud$"):
            platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"])
        self.assertEqual((envfiles.read(".env"), envfiles.read(ADDON_ENV)), before)

    def test_an_account_token_that_may_not_delete_keeps_everything(self):
        gateway = self.create()["gateway"]
        platforms.replace_account_token("gravitee-cloud", fake_cloud.MEMBER_TOKEN)
        with self.assertRaisesRegex(cloud.CloudError, "not allowed"):
            platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"])
        self.assertIn(gateway_key(gateway["id"]), envfiles.read(".env"))
        self.assertTrue(self.held())

    def test_an_id_that_is_no_segment_is_not_deleted(self):
        before = len(self.fake.state.asked)
        for gateway in ("", "..", "a/b", None):
            with self.subTest(gateway=gateway), self.assertRaisesRegex(ValueError, "Not an id"):
                platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway)
        self.assertEqual(len(self.fake.state.asked), before)

    def test_a_gateway_of_a_platform_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.delete_gateway(PROJECT, "nope", "g")

    def test_a_gateway_is_deleted(self):
        gateway = self.create()["gateway"]
        self.assertEqual(platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"]), {})
        self.assertEqual(platforms.check("gravitee-cloud", PROJECT)["gateways"], [])
        with self.assertRaisesRegex(cloud.CloudError, "Not found"):
            platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"])

    def test_no_token_in_anything_it_answers_says_or_keeps(self):
        gateway = self.create()["gateway"]
        seen = [platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"])]
        with self.assertRaises(ValueError) as caught:
            platforms.delete_gateway(PROJECT, "gravitee-cloud", gateway["id"])
        seen.append(str(caught.exception))
        self.assertHidden(seen, self.text(platforms.FILE))


class RunsHere(Platforms):
    """Whether the gateway that runs here is one of a platform's, and what forgetting the platform leaves of it."""

    def setUp(self):
        super().setUp()
        self.add()

    def test_it_is_a_gateway_the_lab_created_for_the_platform_whose_token_the_add_on_holds(self):
        self.assertFalse(platforms.runs_here(PROJECT, "gravitee-cloud"))   # the add-on holds none
        first = self.create(DEV, "first")["gateway"]
        second = self.create(TEST, "second")["gateway"]
        self.assertTrue(platforms.runs_here(PROJECT, "gravitee-cloud"))
        self.assertEqual([platforms.runs_here(PROJECT, "gravitee-cloud", gateway["id"]) for gateway in (first, second)],
                         [True, False])
        platforms.swap_gateway(PROJECT, "gravitee-cloud", second["id"])
        self.assertEqual([platforms.runs_here(PROJECT, "gravitee-cloud", gateway["id"]) for gateway in (first, second)],
                         [False, True])

    def test_a_token_pasted_by_hand_is_not_a_gateway_of_the_platform(self):
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": fake_cloud.GATEWAY_TOKEN})
        self.assertFalse(platforms.runs_here(PROJECT, "gravitee-cloud"))
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": "not-a-token"})
        self.assertFalse(platforms.runs_here(PROJECT, "gravitee-cloud"))

    def test_a_platform_that_is_not_known(self):
        with self.assertRaisesRegex(paths.NotFound, "No platform nope"):
            platforms.runs_here(PROJECT, "nope")

    def test_forgetting_the_platform_empties_the_add_on_only_when_its_gateway_ran_here(self):
        self.second()
        theirs = self.create("fake-client-test", "theirs", platform="client-lab")["gateway"]   # runs here
        self.create(DEV, "mine")   # a gateway of this platform, kept, that does not
        held = self.held()
        platforms.remove("gravitee-cloud", PROJECT)
        self.assertEqual(self.held(), held, "the gateway that runs here is another platform's")
        self.assertTrue(platforms.runs_here(PROJECT, "client-lab", theirs["id"]))
        platforms.remove("client-lab", PROJECT)
        self.assertIsNone(self.held())
        self.assertFalse((self.root / ADDON_ENV).exists())

    def test_forgetting_it_with_the_gateway_here_empties_the_add_on_and_every_key(self):
        gateway = self.create()["gateway"]
        self.write(ADDON_ENV, f"HYBRID_GATEWAY_VERSION=4.12.1\nHYBRID_CLOUD_TOKEN={self.held()}\n")
        platforms.remove("gravitee-cloud", PROJECT)
        self.assertEqual(self.text(ADDON_ENV), "HYBRID_GATEWAY_VERSION=4.12.1\n")
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "keep-me"})
        self.assertEqual(platforms.read(), [])
        self.assertEqual(len(self.fake.state.gateways), 1, f"{gateway['id']} stays in the platform that holds it")

    def test_without_the_project_the_add_on_is_not_known_and_stays_as_it_is(self):
        self.create()
        held = self.held()
        platforms.remove("gravitee-cloud")
        self.assertEqual(self.held(), held)
        self.assertEqual(envfiles.read(".env"), {"GRAVITEE_LICENSE": "keep-me"})

    def test_the_token_of_a_gateway_pasted_by_hand_is_not_touched(self):
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": fake_cloud.GATEWAY_TOKEN})
        platforms.remove("gravitee-cloud", PROJECT)
        self.assertEqual(self.held(), fake_cloud.GATEWAY_TOKEN)


class Records(Platforms):
    """What the playgrounds recorded of what they deployed to a platform goes with it, and only that."""

    def setUp(self):
        super().setUp()
        self.add()
        self.add("Gravitee Cloud 2", token=fake_cloud.SECOND_TOKEN, account=SECOND)
        for pid in ("hotels", "local/mine"):
            self.write(f"playgrounds/{pid}/compose.yml")
            for platform in ("gravitee-cloud", "gravitee-cloud-2"):
                self.write(f"playgrounds/{pid}/.state/{platform}/{DEV}/created.json", "[]")
                self.write(f"playgrounds/{pid}/.state/{platform}/{DEV}/meta.json", "{}")
            self.write(f"playgrounds/{pid}/.state/created.json", "[]")   # This machine's

    def left(self, pid):
        return sorted(path.relative_to(self.root / "playgrounds" / pid / ".state").as_posix()
                      for path in (self.root / "playgrounds" / pid / ".state").rglob("*") if path.is_file())

    def test_the_folder_of_the_platform_goes_from_every_playground_and_nothing_else(self):
        platforms.remove("gravitee-cloud")
        for pid in ("hotels", "local/mine"):
            with self.subTest(pid=pid):
                self.assertEqual(self.left(pid), ["created.json", f"gravitee-cloud-2/{DEV}/created.json",
                                                  f"gravitee-cloud-2/{DEV}/meta.json"])

    def test_a_link_is_not_followed(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "keep").write_text("x")
        folder = self.root / "playgrounds/hotels/.state/gravitee-cloud"
        for path in folder.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(folder.rglob("*"), reverse=True):
            path.rmdir()
        folder.rmdir()
        folder.symlink_to(outside, target_is_directory=True)
        platforms.remove("gravitee-cloud")
        self.assertTrue((outside / "keep").exists())

    def test_a_folder_that_cannot_be_deleted_is_said_after_the_platform_is_forgotten(self):
        failing = self.patch(platforms.shutil, "rmtree", side_effect=OSError(13, "Permission denied"))
        with self.assertRaises(RuntimeError) as caught:
            platforms.remove("gravitee-cloud")
        self.assertEqual(failing.call_count, 2)
        self.assertRegex(str(caught.exception), r"^The platform was forgotten, but what the playgrounds recorded of it "
                                                r"could not be deleted, delete these yourself: playgrounds/.*Permission denied")
        self.assertEqual([record["id"] for record in platforms.read()], ["gravitee-cloud-2"])
        self.assertEqual(envfiles.read(".env").get(KEY), None)

    def test_a_playground_that_never_deployed_there_has_nothing_to_delete(self):
        self.write("playgrounds/other/compose.yml")
        platforms.remove("gravitee-cloud")
        self.assertFalse((self.root / "playgrounds/other/.state").exists())


class TwoAccounts(Platforms):
    def test_each_platform_keeps_its_own_tokens_and_asks_its_own_account(self):
        self.add()
        self.second()
        platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.CLOUD_TOKEN)
        platforms.set_cloud_token("client-lab", "fake-client-test", fake_cloud.SECOND_CLOUD_TOKEN)
        mine, theirs = self.create(), self.create("fake-client-dev", platform="client-lab")
        self.assertEqual([e["name"] for e in platforms.check("client-lab")["environments"]], ["Development", "Test"])
        self.assertEqual([g["id"] for g in platforms.check("gravitee-cloud")["gateways"]], [mine["gateway"]["id"]])
        self.assertEqual([g["id"] for g in platforms.check("client-lab")["gateways"]], [theirs["gateway"]["id"]])
        self.assertEqual(sorted(envfiles.read(".env")), sorted([
            "GRAVITEE_LICENSE", KEY, "PLATFORM_CLIENT_LAB_TOKEN", DEV_KEY, gateway_key(mine["gateway"]["id"]),
            "PLATFORM_CLIENT_LAB_ENV_FAKE_CLIENT_TEST_TOKEN", gateway_key(theirs["gateway"]["id"], "CLIENT_LAB")]))

    def test_a_token_of_the_other_account_is_not_one_of_this_environment(self):
        self.add()
        self.second()
        with self.assertRaisesRegex(cloud.CloudError, "is not for an environment of Gravitee Cloud"):
            platforms.set_cloud_token("gravitee-cloud", DEV, fake_cloud.SECOND_CLOUD_TOKEN)
        with self.assertRaisesRegex(cloud.CloudError, "is for Test, not Development"):
            platforms.set_cloud_token("client-lab", "fake-client-dev", fake_cloud.SECOND_CLOUD_TOKEN)


class CatalogRecords(Platforms):
    """The same, for the playgrounds of every catalog: the first one's (bare ids), the others' (@namespace/name), this
    machine's (local/name)."""

    DEFAULT = json.dumps({"schema": 1, "catalogs": [{"id": "fixture", "url": "https://example.invalid/fixture.git"}]})
    IDS = ("hotels", "@acme/bank", "local/mine")

    def setUp(self):
        super().setUp()
        self.write(paths.DEFAULT_FILE, self.DEFAULT)
        self.add()
        self.add("Gravitee Cloud 2", token=fake_cloud.SECOND_TOKEN, account=SECOND)
        for pid in self.IDS:
            folder = paths.unit_folder("playground", pid)
            self.write(f"{folder}/compose.yml")
            for platform in ("gravitee-cloud", "gravitee-cloud-2"):
                self.write(f"{folder}/.state/{platform}/{DEV}/created.json", "[]")
            self.write(f"{folder}/.state/created.json", "[]")   # This machine's

    def left(self, pid):
        state = self.root / paths.unit_folder("playground", pid) / ".state"
        return sorted(path.relative_to(state).as_posix() for path in state.rglob("*") if path.is_file())

    def test_the_folder_of_the_platform_goes_from_the_playgrounds_of_every_catalog_and_nothing_else(self):
        platforms.remove("gravitee-cloud")
        for pid in self.IDS:
            with self.subTest(pid=pid):
                self.assertEqual(self.left(pid), ["created.json", f"gravitee-cloud-2/{DEV}/created.json"])

    def test_a_state_folder_that_is_a_link_out_of_the_playground_is_left_alone_and_said(self):
        outside = self.root / "elsewhere"
        (outside / "gravitee-cloud").mkdir(parents=True)
        (outside / "gravitee-cloud" / "keep").write_text("x", encoding="utf-8")
        state = self.root / paths.unit_folder("playground", "@acme/bank") / ".state"
        for path in sorted(state.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        state.rmdir()
        os.symlink(outside, state)
        with self.assertRaisesRegex(RuntimeError, r"^The platform was forgotten, but what the playgrounds recorded of it could "
                                                  r"not be deleted, delete these yourself: .lab/catalogs/acme/playgrounds/bank/"
                                                  r".state/gravitee-cloud: it leads out of the playground's folder$"):
            platforms.remove("gravitee-cloud")
        self.assertTrue((outside / "gravitee-cloud" / "keep").exists())
        self.assertEqual(self.left("hotels"), ["created.json", f"gravitee-cloud-2/{DEV}/created.json"])
        self.assertEqual([record["id"] for record in platforms.read()], ["gravitee-cloud-2"])

    def test_a_folder_of_a_place_that_is_a_link_to_another_folder_of_the_playground_is_not_followed(self):
        state = self.root / paths.unit_folder("playground", "hotels") / ".state"
        keep = state / "kept"
        keep.mkdir()
        (keep / "keep").write_text("x", encoding="utf-8")
        for path in sorted((state / "gravitee-cloud").rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (state / "gravitee-cloud").rmdir()
        os.symlink(keep, state / "gravitee-cloud")
        platforms.remove("gravitee-cloud")
        self.assertTrue((keep / "keep").exists())


def gateway(platform="cloud"):
    return {"platform": platform, "service": "gw", "token": "GW_TOKEN", "domains": []}


class Chosen(Case):
    """The add-on that runs the gateway of a kind of platform here is the one the project chose, nothing scanned."""

    def project(self, chosen, **more):
        addons = {"a-first": {"id": "a-first", "gateway": gateway()}, "hybrid": {"id": "hybrid", "gateway": gateway()},
                  "kafka": {"id": "kafka", "gateway": None}, "other": {"id": "other", "gateway": gateway("another")}}
        return {"addons": {**addons, **more.pop("addons", {})}, "platform": {"gateways": {"cloud": chosen}}, **more}

    def test_it_is_the_add_on_the_project_chose_not_the_first_that_says_it_runs_one(self):
        project = self.project("hybrid")
        self.assertIs(platforms.gateway_addon(project, "cloud"), project["addons"]["hybrid"])

    def test_with_none_chosen_there_is_none(self):
        self.assertIsNone(platforms.gateway_addon(self.project(None), "cloud"), "a-first and hybrid declare one: not usable")

    def test_a_kind_the_project_has_no_choice_for_has_none(self):
        project = self.project("hybrid")
        self.assertIsNone(platforms.gateway_addon(project, "another"))
        self.assertIsNone(platforms.gateway_addon({**project, "platform": {"gateways": {}}}, "cloud"))

    def test_a_choice_that_is_no_add_on_of_the_project_or_does_not_declare_the_kind_is_none(self):
        for chosen in ("gone", "kafka", "other", 7, ["hybrid"], ""):
            with self.subTest(chosen=chosen):
                self.assertIsNone(platforms.gateway_addon(self.project(chosen), "cloud"))

    def test_a_project_that_does_not_say_is_asked_the_same_way_over_its_add_ons_by_id(self):
        project = self.project("hybrid")
        del project["platform"]
        self.assertIs(platforms.gateway_addon(project, "cloud"), project["addons"]["a-first"])
        project["addons"]["a-first"]["blocked"] = {"reasons": ["Runs with full privileges"]}
        self.assertIs(platforms.gateway_addon(project, "cloud"), project["addons"]["hybrid"])
        project["addons"]["hybrid"]["needs_catalogs"] = [{"reason": "missing"}]
        self.assertIsNone(platforms.gateway_addon(project, "cloud"))
        self.assertIsNone(platforms.gateway_addon({"addons": {}}, "cloud"))
        self.assertIs(platforms.gateway_addon({**project, "platform": {"gateways": None}}, "another"), project["addons"]["other"])

    def test_the_token_goes_to_the_add_on_that_was_chosen_and_to_no_other(self):
        project = self.project("hybrid")
        self.folder(paths, envfiles)
        self.write(".env", "")
        record = {"id": "cloud", "name": "Cloud", "kind": "cloud", "url": "http://127.0.0.1:1", "account": {"id": "x"}}
        self.write(platforms.FILE, json.dumps([record]))
        envfiles.write(".env", {platforms._key("cloud", "GATEWAY", "gw-1"): "token-of-gw-1"})
        platforms.swap_gateway({**project, "addons": {**project["addons"], "hybrid": {**project["addons"]["hybrid"],
                                                                                    "env_file": "hybrid.env"},
                                                      "a-first": {**project["addons"]["a-first"], "env_file": "first.env"}}},
                               "cloud", "gw-1")
        self.assertEqual(envfiles.read("hybrid.env"), {"GW_TOKEN": "token-of-gw-1"})
        self.assertFalse((self.root / "first.env").exists())


GATEWAY_FILE = (fixtures.CATALOGS / "fixture/addons/gateway/compose.yml").read_text(encoding="utf-8")


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Built(Case):
    """What project.build chose, read back (the choice itself is tested in test_resolve and test_project)."""

    def test_it_is_the_add_on_the_fixture_catalog_declares_and_none_of_the_other_catalogs_is_handed_a_token(self):
        partner = GATEWAY_FILE.replace("fixture-gateway", "partner-gateway").replace("8195", "8295")
        _, built = read(self, catalogs={"partner": {"files": {"addons/gateway/compose.yml": partner}}})
        self.assertIs(platforms.gateway_addon(built, "cloud"), built["addons"]["gateway"])
        self.assertIsNone(built["addons"]["@partner/gateway"]["gateway"], "a catalog that is not trusted with a Cloud token")
        self.assertEqual(platforms.local_gateway(built), "apim-gateway")

    def test_it_is_the_next_in_precedence_when_the_first_cannot_start(self):
        def mine(case):
            case.write(".lab/catalogs/local/addons/mygate/compose.yml",
                       GATEWAY_FILE.replace("fixture-gateway", "mine-gateway").replace("8195", "8295"))
        blocked = GATEWAY_FILE.replace("  kind: addon\n", "  kind: addon\n  requires: {lab: 99}\n", 1)
        _, built = read(self, fixture={"files": {"addons/gateway/compose.yml": blocked}}, more=mine)
        self.assertTrue(built["addons"]["gateway"]["blocked"])
        self.assertIs(platforms.gateway_addon(built, "cloud"), built["addons"]["local/mygate"])


if __name__ == "__main__":
    unittest.main()
