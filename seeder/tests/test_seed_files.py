"""The checks of seed files: no address is written out, the seed service is read whichever way it is written, and the folders
of seed/environments agree with themselves: a member of a group is a user, an application's group is a group, a subscription
names an application, an API and a plan of the folder.

The checks are those of seedlint.py, which a catalog's CI runs on the command line (test_seedlint.py tests the command and runs
it on the engine's sample); this file tests them on files made for it."""

import json
import tempfile
import unittest
from pathlib import Path

from lab_seeder import common
from lab_seeder.seedlint import SMALL, environment_problems, literal_addresses, seed_environment, seed_service


class Preflight(unittest.TestCase):
    """The checks themselves, on files made for it: they find what they are meant to find."""

    def test_an_address_that_is_not_a_variable_is_found(self):
        text = ("a: http://localhost:8083/x\nb: http://bakery-mcp:8000\nc: https://host.docker.internal:11434/v1\n"
                "d: ${BAKERY_MCP_URL}/x\ne: http://${HOST}:80\nf: https://huggingface.co/m\ng: https://docs.gravitee.io/apim\n"
                'h: "https://www.w3.org/2001/XMLSchema"')
        self.assertEqual(literal_addresses(text),
                         {"http://localhost", "http://bakery-mcp", "https://host.docker.internal"})

    def test_a_seed_service_is_the_one_with_the_seed_label_and_its_variables_are_read_whichever_way_they_are_written(self):
        with tempfile.TemporaryDirectory() as folder:
            compose = Path(folder) / "compose.yml"
            compose.write_text("services:\n  app:\n    environment: {A: x}\n  init:\n    labels: [io.gravitee.role=seed]\n"
                               "    environment:\n      - URL=http://apim:8083\n      - KEY=${KEY:-demo}\n      - BARE\n", encoding="utf-8")
            self.assertEqual(seed_environment(seed_service(compose)), {"URL": "http://apim:8083", "KEY": "demo", "BARE": ""})
            compose.write_text("services:\n  init:\n    labels: {io.gravitee.role: seed}\n    environment: {N: 5}\n", encoding="utf-8")
            self.assertEqual(seed_environment(seed_service(compose)), {"N": "5"})
            compose.write_text("services:\n  app: {image: x}\n", encoding="utf-8")
            self.assertIsNone(seed_service(compose))


class EnvironmentChecks(unittest.TestCase):
    """The checks of an environment folder, on folders made for it: they accept a good one and find what is wrong in it."""

    API = {"api": {"name": "Batch status", "crossId": "novo-batch-status", "groups": ["novo"]},
           "plans": [{"name": "Standard", "mode": "STANDARD", "validation": "AUTO", "security": {"type": "API_KEY"}},
                     {"name": "Restricted", "mode": "STANDARD", "validation": "MANUAL", "commentRequired": True,
                      "security": {"type": "API_KEY"}},
                     {"name": "Webhook push", "mode": "PUSH", "validation": "AUTO"}]}
    GOOD = {
        "environment.yaml": "id: novo\nname: Novo Nordisk\n",
        "users.yaml": "users:\n  - {source: memory, sourceId: astrid.holm}\n",
        "groups/novo.yaml": ("hrid: novo\nname: ${ENV_NAME}\nmembers:\n  - {source: memory, sourceId: astrid.holm}\n"
                             "  - {source: memory, sourceId: admin, external: true}\n"),
        "applications/sap.yaml": "name: SAP\ngroups: [novo]\n",
        "subscriptions/novo.yaml": (
            "subscriptions:\n  - {application: SAP, api: Batch status, plan: Standard, apiKey: '${KEY}'}\n"
            "  - {application: SAP, api: Batch status, plan: Restricted, pending: true, comment: Why}\n"
            "  - application: SAP\n    api: Batch status\n    plan: Webhook push\n    consumerConfiguration:\n"
            "      entrypointId: webhook\n      entrypointConfiguration:\n"
            "        callbackUrl: ${HOOKS}/${ENV_ID}\n        auth: {type: token, token: {value: '${TOKEN}'}}\n"),
        "portal/pages/welcome.yaml": "hrid: welcome\nfile: ../content/welcome.md\n",
        "portal/content/welcome.md": "# ${ENV_NAME}\n",
        "portal/theme.yaml": "hrid: theme\nassets: {logo: ../assets/logo.svg}\n",
        "assets/logo.svg": "<svg/>",
        "portal/listings/events.yaml": "hrid: events\napis:\n  - {apiHrid: novo-batch-status, location: Events}\n",
    }
    VARIABLES = {"KEY": "k", "HOOKS": "http://hooks", "TOKEN": "t"}

    def check(self, **changes):
        """The problems of the good folder with some files replaced (None removes one, a dict is a JSON file)."""
        files = {**self.GOOD, "apis/batch.json": self.API, **changes}
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "novo"
            for name, text in files.items():
                if text is not None:
                    path = folder / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(text) if isinstance(text, dict) else text, encoding="utf-8")
            return environment_problems(folder, self.VARIABLES)

    def api(self, **api):
        return {**self.API, "api": {**self.API["api"], **api}}

    def test_a_folder_is_named_by_an_id_and_is_not_local_or_the_platforms_own(self):
        for name in ("Novo_Nordisk", "local", "default"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                folder = Path(root) / name
                folder.mkdir()
                (folder / "environment.yaml").write_text(f"id: {name}\nname: x\n", encoding="utf-8")
                self.assertEqual(environment_problems(folder, self.VARIABLES), [
                    "environment.yaml: an environment is named with lowercase letters, digits and dashes, and is not `local` or the "
                    "platform's own"])

    def test_a_good_folder_has_no_problem(self):
        self.assertEqual(self.check(), [])

    def test_an_environment_file_names_its_folder_and_itself(self):
        self.assertEqual(self.check(**{"environment.yaml": None}), ["environment.yaml: missing"])
        self.assertEqual(self.check(**{"environment.yaml": "id: other\nname: x\n"}), ["environment.yaml: id is 'other', the folder is 'novo'"])
        plain = "hrid: novo\nname: Novo\nmembers:\n  - {source: memory, sourceId: astrid.holm}\n"   # ${ENV_NAME} is not set without a name
        self.assertEqual(self.check(**{"environment.yaml": "id: novo\n", "groups/novo.yaml": plain}), ["environment.yaml: name is missing"])

    def test_the_platforms_own_environment_and_a_badly_written_id_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ("default", "Novo_Nordisk"):
                folder = Path(root) / name
                folder.mkdir()
                (folder / "environment.yaml").write_text(f"id: {name}\nname: x\n", encoding="utf-8")
                self.assertEqual(len(environment_problems(folder, {})), 1, name)

    def test_two_applications_of_one_name_are_found(self):
        self.assertEqual(self.check(**{"applications/again.yaml": "name: SAP\n"}),
                         ["applications/sap.yaml: two applications are named SAP: the seed would write each over the other on every run"])   # the second one in name order is the one named

    def test_a_member_of_a_group_is_a_user_or_is_flagged_external(self):
        group = "hrid: novo\nname: N\nmembers:\n  - {source: memory, sourceId: nobody}\n"
        self.assertEqual(self.check(**{"groups/novo.yaml": group}),
                         ["groups/novo.yaml: nobody is no user of users.yaml: add it there, or flag the member `external: true`"])
        self.assertEqual(self.check(**{"groups/novo.yaml": group.replace("}\n", ", external: true}\n")}), [])

    def test_a_user_and_a_member_have_a_source_and_an_id(self):
        self.assertIn("users.yaml: a user needs a source and a sourceId: {'sourceId': 'x'}", self.check(**{"users.yaml": "users: [{sourceId: x}]\n"}))
        found = self.check(**{"groups/novo.yaml": "hrid: novo\nname: N\nmembers: [{sourceId: astrid.holm}]\n"})
        self.assertEqual(found, ["groups/novo.yaml: a member needs a source and a sourceId: {'sourceId': 'astrid.holm'}"])

    def test_the_groups_an_application_and_an_api_name_are_groups_of_the_folder(self):
        self.assertEqual(self.check(**{"applications/sap.yaml": "name: SAP\ngroups: [novo, ghost]\n"}),
                         ["applications/sap.yaml: groups names ghost, which is no group of the folder"])
        self.assertEqual(self.check(**{"apis/batch.json": self.api(groups=["ghost"])}),
                         ["apis/batch.json: api.groups names ghost, which is no group of the folder"])

    def test_an_api_of_an_environment_has_no_id_and_neither_have_its_plans(self):
        self.assertEqual(len(self.check(**{"apis/batch.json": self.api(id="x")})), 1)
        plans = [{**self.API["plans"][0], "id": "p"}] + self.API["plans"][1:]
        self.assertEqual(len(self.check(**{"apis/batch.json": {**self.API, "plans": plans}})), 1)

    def test_a_subscription_names_an_application_an_api_and_a_plan_of_the_folder(self):
        def sub(**names):
            return "subscriptions:\n  - " + json.dumps({"application": "SAP", "api": "Batch status", "plan": "Standard", **names}) + "\n"
        self.assertEqual(self.check(**{"subscriptions/novo.yaml": sub(application="Ghost")}),
                         ["subscriptions/novo.yaml: Ghost → Batch status (Standard): no such application in the folder"])
        self.assertEqual(self.check(**{"subscriptions/novo.yaml": sub(api="Ghost")}),
                         ["subscriptions/novo.yaml: SAP → Ghost (Standard): no such API in the folder"])
        self.assertEqual(self.check(**{"subscriptions/novo.yaml": sub(plan="Ghost")}),
                         ["subscriptions/novo.yaml: SAP → Batch status (Ghost): no such plan on that API"])

    def test_what_the_platform_refuses_of_a_subscription_is_found(self):
        def sub(**fields):
            return "subscriptions:\n  - " + json.dumps({"application": "SAP", "api": "Batch status", **fields}) + "\n"
        def only(**fields):
            return self.check(**{"subscriptions/novo.yaml": sub(**fields)})
        self.assertEqual(only(plan="Webhook push"), ["subscriptions/novo.yaml: SAP → Batch status (Webhook push): a push plan needs a consumerConfiguration"])
        self.assertEqual(only(plan="Standard", consumerConfiguration={"entrypointId": "webhook"}),
                         ["subscriptions/novo.yaml: SAP → Batch status (Standard): only a push plan takes a consumerConfiguration"])
        token = {"entrypointId": "webhook", "entrypointConfiguration": {"auth": {"type": "token", "token": "plain"}}}
        self.assertEqual(only(plan="Webhook push", consumerConfiguration=token),
                         ["subscriptions/novo.yaml: SAP → Batch status (Webhook push): the token of a webhook is an object, token: {value: ...}"])
        self.assertEqual(only(plan="Standard", pending=True),
                         ["subscriptions/novo.yaml: SAP → Batch status (Standard): a subscription stays pending only on a plan that validates by hand"])
        self.assertEqual(only(plan="Restricted", comment="Why", pending=True, apiKey="k"),
                         ["subscriptions/novo.yaml: SAP → Batch status (Restricted): a pending subscription takes no apiKey"])
        self.assertEqual(only(plan="Restricted"), ["subscriptions/novo.yaml: SAP → Batch status (Restricted): the plan asks for a comment"])
        self.assertEqual(only(plan="Webhook push", apiKey="k", consumerConfiguration={"entrypointId": "webhook"}),
                         ["subscriptions/novo.yaml: SAP → Batch status (Webhook push): an apiKey needs a plan with API keys"])

    def test_a_page_a_theme_and_a_listing_name_what_exists(self):
        self.assertEqual(self.check(**{"portal/content/welcome.md": None}), ["portal/pages/welcome.yaml: file ../content/welcome.md does not exist"])
        self.assertEqual(self.check(**{"assets/logo.svg": None}), ["portal/theme.yaml: assets.logo ../assets/logo.svg does not exist"])
        self.assertEqual(self.check(**{"assets/logo.svg": "x" * (SMALL + 1)}), ["portal/theme.yaml: assets.logo ../assets/logo.svg weighs more than 256 KB"])
        listing = "hrid: events\napis:\n  - {apiHrid: ghost}\n"
        self.assertEqual(self.check(**{"portal/listings/events.yaml": listing}), ["portal/listings/events.yaml: apiHrid ghost is the crossId of no API of the folder"])

    def test_a_variable_the_engine_gives_and_one_the_service_sets_replace_in_every_file(self):
        with self.assertRaises(common.SeedError):
            self.check(**{"portal/content/welcome.md": None, "groups/novo.yaml": "hrid: novo\nname: ${UNSET}\n"})


if __name__ == "__main__":
    unittest.main()
