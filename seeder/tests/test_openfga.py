"""OpenFGA stores: a store is found by name before it is made, a model is written only when it changed, tuples are written
in chunks, and a removal deletes the stores the seed made. OpenFGA is a small memory that keeps what it is given."""

import json
import re
import subprocess
import unittest
from collections import namedtuple
from unittest import mock

from lab_seeder import common
from lab_seeder import openfga
from lab_seeder.testkit import Seed

DOC_METADATA = {"relations": {"viewer": {"directly_related_user_types": [{"type": "user"}]}}}
MODEL = {"schema_version": "1.1", "type_definitions": [
    {"type": "user"}, {"type": "doc", "relations": {"viewer": {"this": {}}}, "metadata": DOC_METADATA}]}
STORE = r"/stores/[^/]+"
FILE = "openfga/documents.yaml"


def stored(model):
    """A model as OpenFGA gives it back: its id and the empty fields it adds."""
    return {**model, "conditions": {}, "type_definitions": [{"relations": {}, **t} for t in model["type_definitions"]]}


class OpenFga:
    """OpenFGA as far as the seed uses it: stores, the models of each (the latest first) and the writes of tuples."""

    def __init__(self):
        self.stores, self.models, self.writes = {}, {}, []

    def routes(self):
        return {
            ("GET", "/stores"): lambda call: (200, {"stores": [{"id": i, "name": n} for i, n in self.stores.items()]}),
            ("POST", "/stores"): self.create_store,
            ("GET", re.compile(STORE + "/authorization-models")): lambda call: (200, {"authorization_models": self.models.get(self.store(call), [])[:1]}),
            ("POST", re.compile(STORE + "/authorization-models")): self.write_model,
            ("POST", re.compile(STORE + "/write")): self.write,
            ("DELETE", re.compile(STORE)): self.delete_store,
        }

    @staticmethod
    def store(call):
        return call.path.split("/")[2]

    def create_store(self, call):
        store_id = f"store-{len(self.stores) + 1}"
        self.stores[store_id] = call.json["name"]
        return 201, {"id": store_id, "name": call.json["name"]}

    def write_model(self, call):
        model_id = f"model-{sum(len(models) for models in self.models.values()) + 1}"
        held = self.models.setdefault(self.store(call), [])
        held.insert(0, {**stored(call.json), "id": model_id})
        return 201, {"authorization_model_id": model_id}

    def write(self, call):
        self.writes.append(call.json)
        return 200, {}

    def delete_store(self, call):
        self.stores.pop(self.store(call), None)
        return 204


class Case(Seed):

    def setUp(self):
        super().setUp()
        self.fga = OpenFga()
        self.platform.add(self.fga.routes())
        self.model = dict(MODEL)
        patch = mock.patch.object(openfga, "to_json", side_effect=lambda dsl: self.model)
        self.to_json = patch.start()
        self.addCleanup(patch.stop)

    def store_file(self, tuples=(), **spec):
        self.seed_files({FILE: json.dumps({"name": "Documents", "model": "model\n  schema 1.1\n", "tuples": list(tuples), **spec})})

    def stores_made(self):
        return [(x["product"], x["kind"], x["label"], x["name"], x["id"]) for x in common.created()]


class Canonical(unittest.TestCase):

    def test_empty_fields_do_not_make_two_models_differ(self):
        padded = {"id": "model-1", "schema_version": "1.1", "conditions": {},
                  "type_definitions": [{"type": "doc", "relations": {"viewer": {"this": {}}}, "metadata": DOC_METADATA, "extra": None},
                                       {"type": "user", "relations": {}, "metadata": {"relations": {}}}]}
        self.assertEqual(openfga.canonical(padded), openfga.canonical(MODEL))

    def test_the_order_of_the_types_does_not_matter(self):
        reversed_types = {"type_definitions": list(reversed(MODEL["type_definitions"]))}
        self.assertEqual(openfga.canonical(reversed_types), openfga.canonical(MODEL))

    def test_a_direct_relation_is_kept_though_it_is_empty(self):
        self.assertIn('"this"', openfga.canonical(MODEL))

    def test_a_relation_that_changed_makes_two_models_differ(self):
        changed = {"type_definitions": [MODEL["type_definitions"][0], {"type": "doc", "relations": {"editor": {"this": {}}}}]}
        self.assertNotEqual(openfga.canonical(changed), openfga.canonical(MODEL))

    def test_the_conditions_are_part_of_a_model(self):
        self.assertNotEqual(openfga.canonical({**MODEL, "conditions": {"in_office": {"name": "in_office"}}}), openfga.canonical(MODEL))


class Model(unittest.TestCase):

    RESULT = namedtuple("Result", "returncode stdout stderr")

    def test_the_model_is_what_the_cli_transforms_it_to(self):
        with mock.patch.object(subprocess, "run", return_value=self.RESULT(0, json.dumps(MODEL), "")) as run:
            self.assertEqual(openfga.to_json("model\n  schema 1.1\n"), MODEL)
        self.assertEqual(run.call_args.args[0][:3], ["fga", "model", "transform"])

    def test_a_model_the_cli_refuses_stops_the_seed_with_its_message(self):
        with mock.patch.object(subprocess, "run", return_value=self.RESULT(1, "", "unexpected token\n")):
            with self.assertRaisesRegex(common.SeedError, "OpenFGA model: unexpected token"):
                openfga.to_json("not a model")


class Stores(Case):

    def test_a_store_is_created_once_and_recorded(self):
        self.store_file()
        stores = openfga.seed()
        self.assertEqual(self.fga.stores, {"store-1": "Documents"})
        self.assertEqual(self.stores_made(), [("openfga", "store", "OpenFGA stores", "Documents", "store-1")])
        self.assertEqual(stores, {"Documents": {"id": "store-1", "model_id": "model-1"}})
        self.assertIn("✓ Store 'Documents' created", self.output())
        openfga.seed()
        self.assertEqual(self.platform.sent("POST", "/stores")[1:], [])
        self.assertEqual(len(self.stores_made()), 1)
        self.assertIn("Store 'Documents' exists", self.output())

    def test_a_store_made_elsewhere_is_used_and_not_recorded(self):
        self.fga.stores["store-9"] = "Documents"
        self.store_file()
        self.assertEqual(openfga.seed()["Documents"]["id"], "store-9")
        self.assertEqual(self.platform.sent("POST", "/stores"), [])
        self.assertEqual(common.created(), [])

    def test_a_store_is_named_by_its_file_when_the_file_gives_no_name(self):
        self.seed_files({FILE: json.dumps({"model": "model\n  schema 1.1\n"})})
        self.assertEqual(list(openfga.seed()), ["documents"])

    def test_a_model_can_sit_in_a_file_next_to_the_store(self):
        self.seed_files({FILE: json.dumps({"name": "Documents", "model_file": "documents.fga"}), "openfga/documents.fga": "model\n  schema 1.1\n"})
        openfga.seed()
        self.to_json.assert_called_once_with("model\n  schema 1.1\n")

    def test_a_seed_without_stores_asks_nothing(self):
        self.assertEqual(openfga.seed(), {})
        self.assertEqual(self.platform.calls, [])


class Models(Case):

    def test_the_model_is_written_when_the_store_has_none(self):
        self.store_file()
        stores = openfga.seed()
        self.assertEqual(self.platform.changes(), [("POST", "/stores"), ("POST", "/stores/store-1/authorization-models")])
        self.assertEqual(stores["Documents"]["model_id"], "model-1")
        self.assertIn("✓ Model written", self.output())

    def test_an_unchanged_model_writes_nothing_and_keeps_its_id(self):
        self.store_file()
        openfga.seed()
        self.platform.calls.clear()
        stores = openfga.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(stores["Documents"]["model_id"], "model-1")
        self.assertIn("Model unchanged", self.output())

    def test_a_changed_model_is_written_as_a_new_one(self):
        self.store_file()
        openfga.seed()
        self.model = {**MODEL, "type_definitions": MODEL["type_definitions"] + [{"type": "folder"}]}
        self.platform.calls.clear()
        stores = openfga.seed()
        self.assertEqual(self.platform.changes(), [("POST", "/stores/store-1/authorization-models")])
        self.assertEqual(stores["Documents"]["model_id"], "model-2")


class Tuples(Case):

    @staticmethod
    def tuples(count):
        return [{"user": f"user:{i}", "relation": "viewer", "object": "doc:1", "extra": "left out"} for i in range(count)]

    def test_tuples_are_written_a_hundred_at_a_time_and_a_duplicate_is_ignored(self):
        self.store_file(self.tuples(250))
        openfga.seed()
        self.assertEqual([len(w["writes"]["tuple_keys"]) for w in self.fga.writes], [100, 100, 50])
        self.assertEqual({w["writes"]["on_duplicate"] for w in self.fga.writes}, {"ignore"})
        self.assertEqual({w["authorization_model_id"] for w in self.fga.writes}, {"model-1"})
        self.assertIn("✓ 250 tuples", self.output())

    def test_a_tuple_is_written_with_its_user_relation_and_object_only(self):
        self.store_file(self.tuples(1))
        openfga.seed()
        self.assertEqual(self.fga.writes[0]["writes"]["tuple_keys"], [{"user": "user:0", "relation": "viewer", "object": "doc:1"}])

    def test_a_hundred_tuples_are_one_write(self):
        self.store_file(self.tuples(100))
        openfga.seed()
        self.assertEqual(len(self.fga.writes), 1)

    def test_a_store_without_tuples_writes_none(self):
        self.store_file()
        openfga.seed()
        self.assertEqual(self.fga.writes, [])
        self.assertNotIn("tuples", self.output())


class Removal(Case):

    def test_the_stores_the_seed_made_are_deleted(self):
        self.store_file()
        openfga.seed()
        self.platform.calls.clear()
        openfga.remove(common.created())
        self.assertEqual(self.platform.changes(), [("DELETE", "/stores/store-1")])
        self.assertEqual(self.fga.stores, {})
        self.assertIn("✓ Store 'Documents' deleted", self.output())

    def test_a_store_that_is_gone_is_not_an_error(self):
        openfga.remove([{"product": "openfga", "kind": "store", "name": "Documents", "id": "store-7", "label": ""}])
        self.assertEqual(self.platform.changes(), [("DELETE", "/stores/store-7")])

    def test_what_another_module_made_is_left_alone(self):
        openfga.remove([{"product": "am", "kind": "domain", "name": "d", "id": "d", "label": ""}])
        openfga.remove([])
        self.assertEqual(self.platform.calls, [])


if __name__ == "__main__":
    unittest.main()
