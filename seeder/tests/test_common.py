"""What every module shares: the settings of the seed service, the HTTP client that reports the body of a refusal, the
seed files with their variables replaced (and YAML that asks the loader for millions of pairs, which is refused), and waiting
for a platform that is still starting."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import requests
import yaml
from urllib3.exceptions import MaxRetryError, NewConnectionError
from yaml.constructor import SafeConstructor

from lab_seeder import common
from lab_seeder.testkit import Seed, seeded_playgrounds

from . import chained, doubling, fanned, flowing


class Settings(Seed):

    def test_an_empty_setting_takes_its_default(self):
        self.env(AIM_GAMMA_URL="")
        self.assertEqual(common.setting("AIM_GAMMA_URL", "http://apim"), "http://apim")

    def test_a_setting_is_the_environment_before_its_default(self):
        self.env(AIM_GAMMA_URL="http://gamma")
        self.assertEqual(common.setting("AIM_GAMMA_URL", "http://apim"), "http://gamma")

    def test_a_setting_without_a_value_or_a_default_is_an_error(self):
        self.env(AIM_GAMMA_URL="")
        with self.assertRaisesRegex(common.SeedError, "AIM_GAMMA_URL is not set"):
            common.setting("AIM_GAMMA_URL")


class Client(Seed):

    def test_a_call_takes_30_seconds_unless_it_says_otherwise(self):
        self.answer({("GET", "/slow"): 200})
        api = common.Api("http://apim/")
        api.get("/slow")
        api.get("/slow", timeout=120)
        self.assertEqual([call.timeout for call in self.platform.calls], [30, 120])
        self.assertEqual(self.platform.calls[0].url, "http://apim/slow")

    def test_a_status_it_did_not_expect_is_an_error_with_the_body(self):
        self.answer({("POST", "/x"): (409, {"message": "taken"})})
        with self.assertRaisesRegex(common.SeedError, r'POST /x: HTTP 409 \{"message": "taken"\}'):
            common.Api("http://apim").post("/x")

    def test_the_body_of_an_error_is_one_line(self):
        # The Lab UI shows the last line of the seed's output as the reason: Gamma pretty-prints its errors
        pretty = '{\n  "message" : "no such tool",\n  "http_status" : 400\n}'
        self.answer({("GET", "/x"): (400, pretty)})
        with self.assertRaises(common.SeedError) as raised:
            common.Api("http://apim").get("/x")
        self.assertEqual(str(raised.exception), 'GET /x: HTTP 400 { "message" : "no such tool", "http_status" : 400 }')

    def test_a_delete_of_what_is_gone_is_done(self):
        self.answer({("DELETE", "/x"): 404})
        self.assertEqual(common.Api("http://apim").delete("/x").status_code, 404)


def refused_connection():
    """What requests raises for a connection that could not be made: the request never reached the platform."""
    return requests.ConnectionError(MaxRetryError(None, "/x", NewConnectionError(None, "Connection refused")))


class Requests(Seed):
    """The one policy of every request of the seed: what is sent again, how often, and what is said when it is not."""

    def ask(self, method, *answers, **kwargs):
        self.answer({(method, "/x"): list(answers)})
        return common.Api("http://apim").call(method, "/x", **kwargs)

    def sent(self, method):
        return len(self.platform.sent(method, "/x"))

    def test_a_get_put_or_delete_the_platform_could_not_answer_now_is_sent_again(self):
        for method in ("GET", "PUT", "DELETE"):
            for trouble in (500, 502, 503, 504, 429, requests.ConnectionError("reset"), requests.ReadTimeout("slow"), refused_connection()):
                with self.subTest(method=method, trouble=str(trouble)):
                    self.platform.calls.clear()
                    self.assertEqual(self.ask(method, trouble, (200, {"ok": True})).status_code, 200)
                    self.assertEqual(self.sent(method), 2)

    def test_a_post_or_a_patch_whose_answer_is_lost_is_sent_once(self):
        """The platform may have made what it asked for: sent again, it would make a second one."""
        for method in ("POST", "PATCH"):
            for lost in (500, 502, 504, requests.ConnectionError("reset"), requests.ReadTimeout("slow"),
                         requests.exceptions.ChunkedEncodingError("cut")):
                with self.subTest(method=method, lost=str(lost)):
                    self.platform.calls.clear()
                    with self.assertRaises(common.SeedError):
                        self.ask(method, lost, 201)
                    self.assertEqual(self.sent(method), 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_post_the_platform_did_not_take_is_sent_again(self):
        for trouble in (refused_connection(), requests.ConnectTimeout("no route"), 429, 503):
            with self.subTest(trouble=str(trouble)):
                self.platform.calls.clear()
                self.assertEqual(self.ask("POST", trouble, 201).status_code, 201)
                self.assertEqual(self.sent("POST"), 2)

    def test_a_post_its_caller_says_is_idempotent_is_sent_again_as_a_put_is(self):
        self.assertEqual(self.ask("POST", requests.ReadTimeout("slow"), 502, 202, idempotent=True).status_code, 202)
        self.assertEqual(self.sent("POST"), 3)

    def test_it_waits_between_two_tries_and_says_so_in_words(self):
        self.ask("GET", requests.ConnectionError("http://secret-host:8083 Max retries"), 503, 200)
        self.assertEqual(self.clock.sleeps, [common.RETRY_PAUSE, common.RETRY_PAUSE])
        self.assertIn(f"[seed] ! GET /x: the connection was cut, trying again in {common.RETRY_PAUSE} s (1/{common.RETRIES})", self.output())
        self.assertIn(f"[seed] ! GET /x: HTTP 503, trying again in {common.RETRY_PAUSE} s (2/{common.RETRIES})", self.output())
        self.assertNotIn("secret-host", self.output())

    def test_a_platform_that_stays_busy_stops_the_seed_with_its_last_answer(self):
        with self.assertRaisesRegex(common.HttpError, rf"^GET /x: HTTP 502 bad gateway \({common.RETRIES + 1} tries\)$"):
            self.ask("GET", (502, "bad gateway"))
        self.assertEqual((self.sent("GET"), len(self.clock.sleeps)), (common.RETRIES + 1, common.RETRIES))

    def test_no_answer_stops_the_seed_with_one_line_in_words_and_never_the_library_text(self):
        """A traceback line of requests (HTTPConnectionPool(host=...): Read timed out) is what the Lab would show as the reason."""
        for error, words in ((requests.ReadTimeout("HTTPConnectionPool(host='apim'): Read timed out"), "no answer in 30 s"),
                             (refused_connection(), f"cannot connect ({common.RETRIES + 1} tries)"),
                             (requests.ConnectionError("('Connection aborted.', RemoteDisconnected())"), "the connection was cut")):
            with self.subTest(words=words):
                with self.assertRaises(common.SeedError) as raised:
                    self.ask("POST", error, what="API Orders")
                self.assertEqual(str(raised.exception), f"API Orders: {words}")
                self.assertNotIsInstance(raised.exception, common.HttpError)

    def test_a_refusal_is_not_sent_again(self):
        for status in (400, 401, 403, 404, 409, 422):
            with self.subTest(status=status):
                self.platform.calls.clear()
                with self.assertRaises(common.HttpError) as raised:
                    self.ask("GET", (status, {"message": "no"}))
                self.assertEqual((raised.exception.status, self.sent("GET")), (status, 1))
        self.assertEqual(self.clock.sleeps, [])

    def test_a_status_the_caller_expects_is_handed_back_and_not_asked_again(self):
        self.assertEqual(self.ask("GET", 503, 200, expect=(200, 503)).status_code, 503)
        self.assertEqual(self.sent("GET"), 1)

    def test_no_request_waits_for_ever(self):
        self.answer({("GET", "/x"): 200})
        common.Api("http://apim").send("GET", "/x")
        common.Api("http://apim").send("GET", "/x", timeout=5)
        self.assertEqual([call.timeout for call in self.platform.calls], [common.TIMEOUT, 5])

    def test_inside_a_wait_a_request_is_sent_once_and_the_wait_asks_again(self):
        api = common.Api("http://apim")
        self.answer({("GET", "/x"): [503, requests.ConnectionError("reset"), 200]})
        common.wait_until("test", "API", lambda: api.get("/x"))
        self.assertEqual(self.sent("GET"), 3)
        self.assertNotIn("trying again", self.output())
        self.assertEqual(self.clock.sleeps, [0.5, 1])

    def test_a_busy_platform_is_waited_for_about_a_minute(self):
        self.assertLessEqual(common.RETRIES * common.RETRY_PAUSE, 60)
        self.assertGreaterEqual(common.RETRIES * common.RETRY_PAUSE, 45)


class Parallel(unittest.TestCase):
    """What a module does at the same time (`parallel`, `together`, `later`): bounded in one place, the first failure stopping
    the others."""

    def setUp(self):
        common.configure(4)
        self.addCleanup(common.configure, 1)

    def test_items_that_do_not_depend_on_one_another_run_at_the_same_time(self):
        meeting = threading.Barrier(2, timeout=5)   # each waits for the other: one after the other, neither would ever come
        self.assertEqual(common.together([lambda: (meeting.wait(), "a")[1], lambda: (meeting.wait(), "b")[1]]), ["a", "b"])

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

        class Session:
            def request(self, *args, **kwargs):
                with lock:
                    inside[0] += 1
                    most[0] = max(most[0], inside[0])
                hold.wait(0.1)
                with lock:
                    inside[0] -= 1
        api = common.Api("http://apim")
        api.session = Session()
        common.parallel(lambda _: api.send("GET", "/x"), range(6))
        self.assertEqual(most[0], 2)

    def test_a_request_does_not_start_once_the_seed_stops(self):
        common.STOP.set()
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


class Provided(Seed):
    """The names the engine gives the files of an environment: the id of its folder and the name its file says."""

    GIVEN = {"ENV_ID": "novo-nordisk", "ENV_NAME": "Novo Nordisk"}

    def test_the_names_the_engine_gives_are_these_two(self):
        self.assertEqual(common.PROVIDED, ("ENV_ID", "ENV_NAME"))

    def test_a_provided_name_is_replaced_by_its_value(self):
        self.assertEqual(common.render("/events/${ENV_ID}: ${ENV_NAME}", "page.md", provided=self.GIVEN),
                         "/events/novo-nordisk: Novo Nordisk")

    def test_a_provided_name_wins_over_the_environment_of_the_seed(self):
        self.env(ENV_ID="from-the-service")
        self.assertEqual(common.render("${ENV_ID}", "page.md", provided=self.GIVEN), "novo-nordisk")
        self.assertEqual(common.render("${ENV_ID}", "page.md"), "from-the-service")

    def test_a_name_that_is_not_provided_comes_from_the_environment_of_the_seed(self):
        self.env(APIM_GATEWAY_URL="http://gateway")
        self.assertEqual(common.render("${APIM_GATEWAY_URL}/${ENV_ID}", "page.md", provided=self.GIVEN), "http://gateway/novo-nordisk")

    def test_a_provided_name_with_no_value_has_the_default_of_the_file_or_is_not_set(self):
        self.assertEqual(common.render("${ENV_NAME:-nobody}", "page.md", provided={"ENV_NAME": ""}), "nobody")
        with self.assertRaisesRegex(common.SeedError, "page.md: ENV_ID not set"):
            common.render("${ENV_ID}", "page.md", provided={"ENV_NAME": "x"})

    def test_without_the_names_a_file_that_uses_them_is_not_set(self):
        with self.assertRaisesRegex(common.SeedError, "page.md: ENV_ID, ENV_NAME not set"):
            common.render("${ENV_NAME} ${ENV_ID}", "page.md")

    def test_a_dollar_that_is_escaped_stays_a_variable_of_the_page_whether_or_not_the_name_is_provided(self):
        self.assertEqual(common.render("$${ENV_ID} and ${ENV_ID}", "page.md", provided=self.GIVEN), "${ENV_ID} and novo-nordisk")
        self.assertEqual(common.render("$${api.name}", "page.md", provided=self.GIVEN), "${api.name}")

    def test_an_expression_of_a_page_keeps_what_follows_its_escaped_dollar_and_a_value_is_not_unescaped(self):
        self.env(NOTE="costs $${50}")
        self.assertEqual(common.render("$${#context.attributes['x']} $${a.b:-c} ${NOTE}", "page.md"),
                         "${#context.attributes['x']} ${a.b:-c} costs $${50}")

    def test_a_json_file_has_its_provided_value_escaped(self):
        self.seed_files({"api.json": '{"name": "${ENV_NAME}", "path": "/${ENV_ID}/"}'})
        loaded = common.load(common.SEED_DIR / "api.json", provided={"ENV_ID": "bch", "ENV_NAME": "Boston \"Children's\" Hospital"})
        self.assertEqual(loaded, {"name": "Boston \"Children's\" Hospital", "path": "/bch/"})

    def test_a_yaml_file_and_a_text_file_have_theirs_replaced_too(self):
        self.seed_files({"site.yaml": "title: ${ENV_NAME}\nid: ${ENV_ID}\n", "page.md": "# ${ENV_NAME}\n$${expression}\n"})
        self.assertEqual(common.load(common.SEED_DIR / "site.yaml", provided=self.GIVEN), {"title": "Novo Nordisk", "id": "novo-nordisk"})
        self.assertEqual(common.text(common.SEED_DIR / "page.md", provided=self.GIVEN), "# Novo Nordisk\n${expression}\n")

    def test_a_file_without_provided_names_loads_as_before(self):
        self.seed_files({"site.yaml": "title: ${TITLE:-demo}\n"})
        self.assertEqual(common.load(common.SEED_DIR / "site.yaml"), {"title": "demo"})


class JsonInJson(Seed):
    """A string of a JSON file that is itself JSON text (an endpoint's `sharedConfiguration`) gets a variable's value escaped for
    that text: a password with a quote or a backslash arrives as it was set."""

    SHARED = '{"url": "${SMF_URL}", "vpn": "apelor", "password": "${PASSWORD}"}'

    def api(self, **values):
        """The file of an API with a plain string and a JSON text that name the password, loaded with these settings."""
        self.env(SMF_URL="tcp://solace:55555", **values)
        template = {"api": {"name": "${NAME}", "endpointGroups": [{"sharedConfiguration": self.SHARED}]}}
        self.seed_files({"api.json": json.dumps(template)})
        return common.load(common.SEED_DIR / "api.json", provided={"NAME": 'Say "hi" \\ now'})

    def inner(self, loaded):
        return json.loads(loaded["api"]["endpointGroups"][0]["sharedConfiguration"])

    def test_a_password_with_a_quote_or_a_backslash_arrives_as_it_was_set(self):
        for password in ('apelor-demo', 'p"w', "back\\slash", "a\\bc", 'a\\"b', "line\nbreak", "tab\t", "ünï \u00e9 \u2713", "$${x}", "'single'"):
            with self.subTest(password=password):
                self.assertEqual(self.inner(self.api(PASSWORD=password))["password"], password)

    def test_a_plain_string_gets_the_value_as_it_is_and_the_text_around_it_stays(self):
        loaded = self.api(PASSWORD='p"w')
        self.assertEqual(loaded["api"]["name"], 'Say "hi" \\ now')
        self.assertEqual(self.inner(loaded), {"url": "tcp://solace:55555", "vpn": "apelor", "password": 'p"w'})

    def test_a_plain_string_is_the_same_whether_the_file_is_read_as_a_document_or_as_text(self):
        self.env(NOTE='a "quoted" \\ value')
        text = '{"note": "x ${NOTE} y", "list": ["${NOTE}", 1, true, null], "n": {"k": "${NOTE}"}}'
        self.assertEqual(common.render_json(text, "f.json"), json.loads(common.render(text, "f.json", json_escape=True)))

    def test_a_json_text_inside_a_list_is_escaped_too_and_a_string_that_only_looks_like_json_is_not(self):
        self.env(V='q"v')
        loaded = common.render_json(json.dumps({"a": ['["${V}"]'], "b": "{not json: ${V}}", "c": "[1, 2"}), "f.json")
        self.assertEqual(json.loads(loaded["a"][0]), ['q"v'])
        self.assertEqual((loaded["b"], loaded["c"]), ("{not json: q\"v}", "[1, 2"))

    def test_a_key_is_replaced_too(self):
        self.env(KEY="id-1")
        self.assertEqual(common.render_json('{"${KEY}": 1}', "f.json"), {"id-1": 1})

    def test_every_name_that_is_not_set_is_said_at_once_whichever_string_uses_it(self):
        self.env(SMF_URL="x")
        with self.assertRaisesRegex(common.SeedError, "api.json: NAME, PASSWORD not set"):
            common.render_json(json.dumps({"n": "${NAME}", "o": {"s": '{"p": "${PASSWORD}"}'}}), "api.json")

    def test_a_default_in_the_file_and_an_escaped_dollar_work_in_a_json_text(self):
        loaded = common.render_json(json.dumps({"s": '{"a": "${NONE:-dflt}", "b": "$${api.name}"}'}), "f.json")
        self.assertEqual(json.loads(loaded["s"]), {"a": "dflt", "b": "${api.name}"})

    def test_a_file_that_is_no_document_until_its_variables_are_replaced_is_replaced_as_text(self):
        self.env(PORT="8080", NAME='a"b')
        self.assertEqual(common.render_json('{"port": ${PORT}, "name": "${NAME}"}', "f.json"), {"port": 8080, "name": 'a"b'})

    def test_a_json_file_that_is_not_json_is_an_error_of_the_parser(self):
        with self.assertRaises(ValueError):
            common.render_json("{", "f.json")

    def test_the_playgrounds_json_files_read_as_they_did_as_text(self):
        for folder, environment in seeded_playgrounds():
            with mock.patch.dict(os.environ, environment, clear=True):
                for path in sorted((folder / "seed").rglob("*.json")):
                    provided = dict.fromkeys(common.PROVIDED, "x") if path.relative_to(folder / "seed").parts[:1] == ("environments",) else None
                    with self.subTest(file=str(path.relative_to(folder))):
                        text = path.read_text(encoding="utf-8")
                        self.assertEqual(common.render_json(text, path, provided),
                                         json.loads(common.render(text, path, json_escape=True, provided=provided)))


def outcome(load, text):
    """What a loader gives for a text: ("loaded", the document), or ("refused", the class of the error and what it says)."""
    try:
        return "loaded", load(text)
    except Exception as e:  # noqa: BLE001 - the error is the answer being compared
        return "refused", type(e).__name__, str(e)


class YamlValues(Seed):
    """A YAML file is read as a document before its variables are replaced, as a JSON file is: a value is put in a string of
    it, and never changes the file around it."""

    def load(self, text, **values):
        self.env(**values)
        self.seed_files({"a.yaml": text})
        return common.load(common.SEED_DIR / "a.yaml")

    def test_a_password_with_a_quote_a_backslash_or_an_escape_arrives_as_it_was_set(self):
        for password in ('S3cr3t"Pw', "back\\slash", "pa\\nss", "tab\\tbed", "it's", "a: b # c", "- [x]", "{x}", "%x", "&a *b"):
            with self.subTest(password=password):
                self.assertEqual(self.load('password: "${PW}"\nother: \'${PW}\'\nplain: ${PW}\n', PW=password),
                                 {"password": password, "other": password, "plain": password})

    def test_a_plain_variable_is_read_as_yaml_reads_its_value_as_in_a_file_written_out(self):
        self.assertEqual(self.load("port: ${PORT}\nenabled: ${FLAG}\nempty: ${NONE:-}\nkey: '${PORT}'\nurl: http://h:${PORT}/x\n",
                                   PORT="8080", FLAG="true"),
                         {"port": 8080, "enabled": True, "empty": None, "key": "8080", "url": "http://h:8080/x"})

    def test_an_explicit_str_tag_keeps_a_plain_variable_a_string(self):
        text = "version: !!str ${VERSION}\nport: !!str ${PORT}\nflag: !!str ${FLAG}\nanchored: &a !!str ${PORT}\ncopy: *a\nbare: ! ${PORT}\n"
        self.assertEqual(self.load(text, VERSION="1.10", PORT="8080", FLAG="true"),
                         {"version": "1.10", "port": "8080", "flag": "true", "anchored": "8080", "copy": "8080", "bare": 8080})

    def test_keys_aliases_and_lists_get_their_values_too(self):
        text = "${NAME}: &u {user: '${USER}'}\ncopy: *u\nlist: ['${USER}', x]\nmerged: {<<: *u, own: 1}\n"
        self.assertEqual(self.load(text, NAME="alice", USER='o"brien'),
                         {"alice": {"user": 'o"brien'}, "copy": {"user": 'o"brien'}, "list": ['o"brien', "x"],
                          "merged": {"user": 'o"brien', "own": 1}})

    def test_a_string_that_is_json_text_gets_the_value_escaped_for_that_text(self):
        loaded = self.load("configuration: '{\"password\": \"${PW}\"}'\n", PW='q"uo\\te')
        self.assertEqual(json.loads(loaded["configuration"]), {"password": 'q"uo\\te'})

    def test_every_name_that_is_not_set_is_said_at_once(self):
        with self.assertRaisesRegex(common.SeedError, "a.yaml: ONE, TWO not set"):
            self.load("a: ${ONE}\nb: '${TWO}'\n")

    def test_a_name_in_a_comment_is_no_value_to_give(self):
        self.assertEqual(self.load("a: 1   # set ${NOBODY} to change it\n"), {"a": 1})

    def test_a_file_that_is_not_yaml_says_its_line_and_never_what_it_holds(self):
        """What the parser quotes of a file is where a password would be: the reason the Lab shows says the line alone."""
        with self.assertRaises(common.SeedError) as raised:
            self.load('a: 1\nb: [1, 2\npassword: "${PW}"\n', PW="S3cr3t-Pw")
        self.assertRegex(str(raised.exception), r"a\.yaml: it is not valid YAML once its variables are replaced \(line \d+\)$")
        self.assertNotIn("S3cr3t", str(raised.exception))
        self.assertNotIn("\n", str(raised.exception))

    def test_the_playgrounds_yaml_files_read_as_they_did_as_text(self):
        """With values YAML reads alike whichever way they are put in, a document reads as the text it used to be replaced in."""
        found = list(seeded_playgrounds())
        self.assertTrue(found)
        for folder, _ in found:
            for path in sorted((folder / "seed").rglob("*.y*ml")):
                text = path.read_text(encoding="utf-8")
                values = {m.group(2): "value-" + m.group(2).lower() for m in common.VARIABLE.finditer(text) if not m.group(1)}
                with self.subTest(file=str(path.relative_to(folder))), mock.patch.dict(os.environ, values):
                    self.assertEqual(common.render_yaml(text, path), common.parse_yaml(common.render(text, path), path))


class MergeKeys(unittest.TestCase):
    """A `<<` key makes the loader of YAML copy the pairs of the mappings it merges into its own, and each of those may have
    merged others: a file of a kilobyte can ask for a million pairs, or for more than the machine holds. What the loader would
    copy is counted on the composed document, which builds no value. These tests never hand the loader a document that costs
    more than a few thousand pairs."""

    def test_nothing_is_copied_where_nothing_is_merged(self):
        for text in ("", "a: 1\n", "a: hi!\n", "a: '<<'\n", "# <<\nb: [1, 2]\n", "a: [\n", "- a\n- b\n", "a: 1\n---\nb: 2\n", "<<: 3\n",
                     "<<: [3, 4]\n", "a: !!str b\n"):
            with self.subTest(text=text):
                self.assertEqual(common.merged_pairs(text), 0)

    def test_the_count_is_what_the_loader_does_with_the_pairs(self):
        """The loader merges, scans and builds the pairs of each mapping: count them there, and the count is within a tenth."""
        done = [0]
        flatten, build = SafeConstructor.flatten_mapping, SafeConstructor.construct_mapping

        def flattening(constructor, node):
            done[0] += len(node.value)
            flatten(constructor, node)
            done[0] += len(node.value)

        def building(constructor, node, deep=False):
            done[0] += len(node.value)
            return build(constructor, node, deep)

        shapes = {"a chain": chained(120), "a doubling": doubling(12), "a doubling by its tag": doubling(10, merge="!!merge m"),
                  "a fan": fanned(200, 40), "a fan and a chain": fanned(100, 100) + chained(60)}
        for what, text in shapes.items():
            with self.subTest(what=what), mock.patch.object(SafeConstructor, "flatten_mapping", flattening), \
                    mock.patch.object(SafeConstructor, "construct_mapping", building):
                done[0] = 0
                yaml.safe_load(text)
                self.assertGreater(done[0], 1000, "a shape that is not worth counting")
                self.assertAlmostEqual(common.merged_pairs(text) / done[0], 1.0, delta=0.1)

    def test_the_count_adds_what_each_merge_makes_the_loader_do(self):
        self.assertEqual(common.merged_pairs(doubling(10)), 6138)
        self.assertEqual(common.merged_pairs(doubling(10, merge="!!merge m")), 6138, "a tag is a merge key as much as <<")
        self.assertEqual(common.merged_pairs(chained(100)), 14949)

    def test_a_count_over_the_limit_stops_at_the_limit(self):
        for text in (doubling(60), doubling(2000), chained(5000), fanned(2000, 1000)):
            self.assertEqual(common.merged_pairs(text), common.COPIED + 1)

    def test_mappings_that_merge_each_other_are_counted_and_end(self):
        for text in ("a: &a {<<: *a, x: 1}\n", "a: &a {x: 1, b: &b {<<: *a, y: 2}, <<: *b}\n", "- &a {<<: [*a, *a], x: 1}\n"):
            with self.subTest(text=text):
                self.assertIsInstance(common.merged_pairs(text), int)
                self.assertLess(common.merged_pairs(text), 100)

    def test_the_merge_tag_is_a_merge_key_whichever_way_it_is_spelled(self):
        """The loader merges on the tag, and the tag has four spellings that a search for `<<` or `merge` would not all find."""
        spellings = {"by its handle": ("", "!!merge m"),
                     "by a handle of the file": ("%TAG !m! tag:yaml.org,2002:m\n---\n", "!m!erge m"),
                     "by its URI": ("", "!<tag:yaml.org,2002:merge> m"),
                     "by its URI, with an escape": ("", "!<tag:yaml.org,2002:%6Derge> m")}
        for what, (head, key) in spellings.items():
            with self.subTest(what=what):
                self.assertEqual(common.merged_pairs(head + doubling(22, merge=key)), common.COPIED + 1)
        self.assertEqual(common.merged_pairs(doubling(22, merge='"<<"')), 0, "a quoted << is a string, not a merge key")

    def test_a_file_in_reasonable_use_is_not_held_back(self):
        """What the compose files and the seed of the playgrounds need is a few hundred pairs; the limit is thousands of times
        that."""
        self.assertLess(common.merged_pairs(fanned(100, 100)), common.COPIED // 10)
        found = list(seeded_playgrounds())
        self.assertTrue(found)
        for folder, _ in found:
            for path in [folder / "compose.yml", *sorted((folder / "seed").rglob("*.y*ml"))]:
                with self.subTest(file=path.name):
                    self.assertLess(common.merged_pairs(path.read_text(encoding="utf-8")), 10_000, str(path))


class Bombs(Seed):
    """A seed file is a stranger's, and the seed container would hang on a bomb of merge keys: the loader refuses a file that
    asks for more than COPIED pairs before it builds anything. Everything else loads as it did."""

    BOMBS = {"chained": chained(3000), "doubling": doubling(22), "doubling by its tag": doubling(22, merge="!!merge m"),
             "a fan": fanned(2000, 1000)}

    def refuses_to_build(self):
        """A patch under which the loader fails the test when it starts to build a document."""
        return mock.patch.object(yaml.SafeLoader, "construct_document", side_effect=AssertionError("the loader ran on a bomb"))

    def test_a_bomb_is_refused_and_the_loader_never_builds_it(self):
        for what, text in self.BOMBS.items():
            with self.subTest(what=what), self.refuses_to_build():
                with self.assertRaises(common.MergeError) as raised:
                    common.parse_yaml(text, "bomb.yaml")
                self.assertEqual(str(raised.exception), f"bomb.yaml: {common.MERGES}")

    def test_load_refuses_the_file_with_the_same_words(self):
        for what, text in self.BOMBS.items():
            self.seed_files({"bomb.yaml": text})
            with self.subTest(what=what), self.refuses_to_build():
                with self.assertRaises(common.MergeError) as raised:
                    common.load(common.SEED_DIR / "bomb.yaml")
                self.assertEqual(str(raised.exception), f"{common.SEED_DIR / 'bomb.yaml'}: {common.MERGES}")

    def test_the_refusal_is_a_seed_error_that_says_what_to_do(self):
        self.assertTrue(issubclass(common.MergeError, common.SeedError))
        self.assertIn("Fix: write the values out, or merge less", common.MERGES)
        self.assertEqual(common.COPIED, 1_000_000)

    def test_a_bomb_is_counted_in_a_fraction_of_a_second(self):
        started = time.process_time()
        for text in self.BOMBS.values():
            common.merged_pairs(text)
        self.assertLess(time.process_time() - started, 5.0)

    def test_a_variable_brings_a_value_never_keys_so_never_merge_keys(self):
        """The file's structure is its own: a value that is YAML text, a bomb even, stays the text it is."""
        self.seed_files({"a.yaml": "a: ${KEY}\n"})
        path = common.SEED_DIR / "a.yaml"
        self.env(KEY=flowing(22))
        started = time.process_time()
        self.assertEqual(common.load(path), {"a": flowing(22)})
        self.assertEqual(common.load(path, provided={"KEY": flowing(22)}), {"a": flowing(22)})
        self.assertLess(time.process_time() - started, 1.0, "the loader copied no pair")

    def test_a_file_that_is_no_yaml_until_its_variables_are_replaced_is_guarded_once_they_are(self):
        """Replaced as text, the variables may bring the keys: the loader gets the replaced text and counts its pairs."""
        self.seed_files({"a.yaml": "a: [${KEY}]\n"})
        path = common.SEED_DIR / "a.yaml"
        self.env(KEY=flowing(5))
        self.assertEqual(common.load(path)["a"][0]["a5"], {"k": 1})
        self.env(KEY=flowing(22))
        with self.refuses_to_build(), self.assertRaises(common.MergeError):
            common.load(path)
        with self.refuses_to_build(), self.assertRaises(common.MergeError):
            common.load(path, provided={"KEY": flowing(22)})

    def test_a_json_file_has_no_merge_keys(self):
        self.seed_files({"a.json": json.dumps({"<<": [1, 2], "!!merge": 3})})
        self.assertEqual(common.load(common.SEED_DIR / "a.json"), {"<<": [1, 2], "!!merge": 3})

    def test_a_text_is_composed_once(self):
        """The count is made on the document the loader builds: a file with merge keys is not read twice."""
        composed, compose = [], yaml.SafeLoader.get_single_node

        def counting(loader):
            composed.append(1)
            return compose(loader)

        with mock.patch.object(yaml.SafeLoader, "get_single_node", counting), mock.patch.object(yaml, "compose", side_effect=AssertionError):
            self.assertEqual(common.parse_yaml(chained(20) + "x: [1, 2]\n")["a19"]["k19"], 1)
        self.assertEqual(len(composed), 1)

    def test_what_a_normal_file_loads_is_what_safe_load_gives(self):
        """Merge keys in use, anchors, tags, comments, several documents, an empty text, a text that is no YAML: the same
        document, or the same refusal in the same words."""
        corpus = ["", "# only a comment\n", "a: 1\n", "- 1\n- two\n", "just text", "a: [\n", "a: 1\n---\nb: 2\n", "a: &x {b: 1}\nc: *x\n",
                  "base: &b {k: 1}\nuse: {<<: *b, own: 2}\nmany: {<<: [*b, {z: 3}], own: 4}\n", "a: !!str 12\nb: !!set {x, y}\n",
                  "a: !!binary aGk=\n", "a: !!python/object:os.system x\n", "a: !unknown 1\n", "\ufeffa: 1\n", "a: \"\\u00e9\\x41\"\n",
                  "a: ${NOT_REPLACED}\n", "<<: {a: 1}\n", "<<: [3]\n", chained(30), doubling(8), fanned(50, 20), flowing(6),
                  "a: &a {x: 1}\nb: &b {<<: *a}\nc: {<<: *b, <<: *a}\n"]
        for text in corpus:
            with self.subTest(text=text[:60]):
                self.assertEqual(outcome(common.parse_yaml, text), outcome(yaml.safe_load, text))

    def test_every_yaml_file_of_the_playgrounds_loads_as_safe_load_gives_it(self):
        found = list(seeded_playgrounds())
        self.assertTrue(found)
        for folder, _ in found:
            for path in [folder / "compose.yml", *sorted((folder / "seed").rglob("*.y*ml"))]:
                with self.subTest(file=str(path.relative_to(folder))):
                    text = path.read_text(encoding="utf-8")
                    self.assertEqual(outcome(common.parse_yaml, text), outcome(yaml.safe_load, text))


class Folders(Seed):

    def test_the_folders_of_a_folder_are_listed_in_name_order_without_files_or_hidden_ones(self):
        self.seed_files({"environments/moderna/environment.yaml": "", "environments/beam/environment.yaml": "",
                         "environments/.hidden/x.yaml": "", "environments/readme.md": ""})
        self.assertEqual(common.folders("environments"), ["beam", "moderna"])

    def test_a_folder_that_is_not_there_has_none(self):
        self.assertEqual(common.folders("environments"), [])


class Waiting(Seed):

    def test_a_check_that_returns_is_ready(self):
        checks = iter([common.SeedError("starting"), None])

        def check():
            outcome = next(checks)
            if outcome:
                raise outcome
        with mock.patch.object(common.time, "sleep") as sleep:
            common.wait_until("test", "API", check)
        self.assertEqual(sleep.call_count, 1)

    def test_a_platform_that_never_answers_ends_the_wait_without_waiting(self):
        with self.assertRaisesRegex(common.SeedError, "API not ready after 300 s: down"):
            common.wait_until("test", "API", lambda: (_ for _ in ()).throw(common.SeedError("down")))
        self.assertGreaterEqual(self.clock.elapsed, 300)

    def test_a_refusal_the_wait_is_told_is_final_ends_it_at_once(self):
        """A wrong password, a revoked token or a wrong environment id is not fixed by waiting five minutes."""
        api = common.Api("http://apim")
        for status in common.REFUSED:
            with self.subTest(status=status):
                self.platform.calls.clear()
                self.answer({("GET", "/x"): (status, {"message": "no"})})
                with self.assertRaisesRegex(common.SeedError, rf'^API: GET /x: HTTP {status} {{"message": "no"}}$'):
                    common.wait_until("test", "API", lambda: api.get("/x"), final=common.REFUSED)
                self.assertEqual(len(self.platform.calls), 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_refusal_is_waited_out_when_the_wait_does_not_say_it_is_final(self):
        """A role just given takes a few seconds to count: some waits are for a refusal to end."""
        api = common.Api("http://apim")
        self.answer({("GET", "/x"): [(403, {"message": "no"}), 200]})
        common.wait_until("test", "access", lambda: api.get("/x"))
        self.assertEqual(len(self.platform.calls), 2)

    def test_a_probe_that_gets_no_answer_is_said_in_words(self):
        with self.assertRaises(common.SeedError) as raised:
            common.wait_until("test", "API", lambda: (_ for _ in ()).throw(requests.ConnectionError("HTTPConnectionPool(host='x')")),
                              timeout=5)
        self.assertEqual(str(raised.exception), "API not ready after 5 s: the connection was cut")


if __name__ == "__main__":
    unittest.main()
