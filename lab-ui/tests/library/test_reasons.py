"""Why a service runs behind what is saved, in one line: each cause, the lines of several services, and that no line
holds a value."""

import unittest

from lab_ui.library import reasons

SERVICE = {"tag": "4.12.20", "built": False}


def tag(saved="4.12.20", running="4.13.0-alpha.4"):
    return {"kind": "tag", "saved": saved, "running": running}


def config(*settings, labels=False):
    return {"kind": "config", "settings": list(settings), "labels": labels}


IMAGE = {"kind": "image", "tag": "3.0.2", "built": False}


class Why(unittest.TestCase):
    def test_another_tag_saved_is_the_first_cause(self):
        found = reasons.why(SERVICE, {"image": "graviteeio/apim-gateway:4.13.0-alpha.4"}, "config", ["A_SETTING"])
        self.assertEqual(found, tag())
        # Whatever else changed with it: the tag is what a person chose
        self.assertEqual(reasons.why(SERVICE, {"image": "x/y:4.13.0-alpha.4"}, "image"), tag())

    def test_the_same_tag_on_another_image_is_a_newer_image(self):
        self.assertEqual(reasons.why({"tag": "3.0.2", "built": False}, {"image": "graviteeio/ae-engine:3.0.2"}, "image"), IMAGE)
        self.assertEqual(reasons.why({"tag": "latest", "built": True}, {"image": "lab/app:latest"}, "image"),
                         {"kind": "image", "tag": "latest", "built": True})

    def test_a_tag_that_a_pull_moved_is_a_newer_image_not_a_tag_that_is_a_hash(self):
        # Docker names the image of such a container by its id, or by its digest: neither is a version
        latest = {"tag": "latest", "built": False}
        for image in ("sha256:3f2a9c1b77d0", "redis@sha256:abc123def456", "ghcr.io/org/app@sha256:abc123def456"):
            self.assertEqual(reasons.why(latest, {"image": image}, "image"),
                             {"kind": "image", "tag": "latest", "built": False}, image)
        # Without another cause, the configuration is what is left to say
        self.assertEqual(reasons.why(latest, {"image": "sha256:3f2a9c1b77d0"}, "config"), config())

    def test_a_changed_configuration_names_its_settings_and_labels_when_they_are_known(self):
        self.assertEqual(reasons.why({"tag": "3.0.2", "built": False}, {"image": "a/b:3.0.2"}, "config", ["X", "Y"]),
                         config("X", "Y"))
        self.assertEqual(reasons.why({"tag": "3.0.2", "built": False}, {"image": "a/b:3.0.2"}, "config", labels=True),
                         config(labels=True))
        # A container whose image is not known says nothing of tags
        self.assertEqual(reasons.why(SERVICE, {"image": ""}, "config"), config())
        self.assertEqual(reasons.why(SERVICE, {}, "config"), config())


class Line(unittest.TestCase):
    def test_a_service_on_its_own_row(self):
        self.assertEqual(reasons.line({"a": tag()}), "4.12.20 is saved, 4.13.0-alpha.4 still runs.")
        self.assertEqual(reasons.line({"a": IMAGE}), "Image 3.0.2 changed since it started.")
        self.assertEqual(reasons.line({"a": {**IMAGE, "built": True}}), "Its image was built again since it started.")
        self.assertEqual(reasons.line({"a": config("GRAVITEE_LICENSE", "REDIS_PASSWORD")}),
                         "Its settings changed since it started: GRAVITEE_LICENSE, REDIS_PASSWORD.")

    def test_a_configuration_that_cannot_be_told_says_only_that_it_changed(self):
        self.assertEqual(reasons.line({"a": config()}), "Its configuration changed since it started.")

    def test_nothing_behind_says_nothing(self):
        self.assertEqual(reasons.line({}), "")
        self.assertEqual(reasons.line({}, {}), "")

    def test_services_are_named_while_few_then_counted(self):
        labels = {name: name.title() for name in "abcdef"}
        whys = {name: tag() for name in "abc"}
        self.assertEqual(reasons.line(whys, labels), "A, B, C: 4.12.20 is saved, 4.13.0-alpha.4 still runs.")
        whys = {name: tag() for name in "abcdef"}
        self.assertEqual(reasons.line(whys, labels), "6 containers: 4.12.20 is saved, 4.13.0-alpha.4 still runs.")

    def test_an_owner_whose_every_service_is_behind_for_one_cause_is_the_subject(self):
        labels = {name: name.title() for name in "abc"}
        whys = {name: tag() for name in "abc"}
        self.assertEqual(reasons.line(whys, labels, "API Management", whole=True),
                         "API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs.")
        # On its own row the owner is not named again
        self.assertEqual(reasons.line(whys, labels, whole=True), "4.12.20 is saved, 4.13.0-alpha.4 still runs.")
        self.assertEqual(reasons.line({"a": config("S")}, {"a": "A"}, "Alert Engine", whole=True),
                         "Alert Engine: its settings changed since it started: S.")
        # Several of them are the owner, and an owner is one: its words are the singular
        self.assertEqual(reasons.line({"a": config("S"), "b": config("S")}, labels, "Kafka", whole=True),
                         "Kafka: its settings changed since it started: S.")
        self.assertEqual(reasons.line({"a": config("S"), "b": config("S")}, labels, whole=True),
                         "Their settings changed since they started: S.")

    def test_some_of_the_services_or_several_causes_name_the_services_after_the_owner_once(self):
        labels = {"a": "Gateway", "b": "Console", "c": "Engine"}
        self.assertEqual(reasons.line({"a": tag()}, labels, "API Management"),
                         "API Management: Gateway (4.12.20 is saved, 4.13.0-alpha.4 still runs).")
        whys = {"a": tag(), "b": tag(), "c": config("S")}
        self.assertEqual(reasons.line(whys, labels, "API Management", whole=True),
                         "API Management: Gateway, Console (4.12.20 is saved, 4.13.0-alpha.4 still runs); "
                         "Engine (its settings changed since it started: S).")
        many = {name: tag() for name in "abcd"}
        self.assertEqual(reasons.line(many, {name: name for name in "abcd"}, "Owner"),
                         "Owner: 4 containers (4.12.20 is saved, 4.13.0-alpha.4 still runs).")
        # Each tag of a group of databases, in its own words
        databases = {"a": tag("8.0.29", "8.3.11"), "b": tag("8.10.1", "8.10.2")}
        self.assertEqual(reasons.line(databases, {"a": "MongoDB", "b": "Redis"}, "Databases", whole=True),
                         "Databases: MongoDB (8.0.29 is saved, 8.3.11 still runs); Redis (8.10.1 is saved, 8.10.2 still runs).")

    def test_the_system_two_tags_share_is_not_part_of_what_a_line_says(self):
        labels = {"a": "Gateway", "b": "Console"}
        whys = {"a": tag("4.12.20-debian", "4.13.0-alpha.4-debian"), "b": tag()}
        self.assertEqual(reasons.line(whys, labels, "API Management", whole=True),
                         "API Management: 4.12.20 is saved, 4.13.0-alpha.4 still runs.")
        # When the system is what differs, both tags are said in full
        self.assertEqual(reasons.line({"a": tag("4.12.20-debian", "4.12.20-alpine")}, {"a": "Gateway"}),
                         "Gateway: 4.12.20-debian is saved, 4.12.20-alpine still runs.")

    def test_causes_are_grouped_by_what_they_say(self):
        labels = {"a": "Gateway", "b": "Console", "c": "Engine", "d": "Cache"}
        whys = {"a": tag("4.12.20", "4.13.0-alpha.4"), "b": tag("4.11.0", "4.12.0"), "c": config("S"), "d": IMAGE}
        self.assertEqual(reasons.line(whys, labels),
                         "Gateway: 4.12.20 is saved, 4.13.0-alpha.4 still runs; "
                         "Console: 4.11.0 is saved, 4.12.0 still runs; "
                         "Cache: image 3.0.2 changed since it started; "
                         "Engine: its settings changed since it started: S.")

    def test_each_set_of_settings_is_a_clause_of_its_own(self):
        labels = {"a": "Gateway", "b": "Engine", "c": "Cache"}
        whys = {"a": config("PLUGINS"), "b": config("INTERNAL_API_PASSWORD"), "c": config(labels=True)}
        self.assertEqual(reasons.line(whys, labels),
                         "Gateway: its settings changed since it started: PLUGINS; "
                         "Engine: its settings changed since it started: INTERNAL_API_PASSWORD; "
                         "Cache: its configuration changed since it started.")
        # The same ones are one clause, however many services
        same = {"a": config("PLUGINS"), "b": config("PLUGINS")}
        self.assertEqual(reasons.line(same, labels), "Gateway, Engine: their settings changed since they started: PLUGINS.")
        both = {"a": config("PLUGINS", labels=True)}
        self.assertEqual(reasons.line(both, labels), "Gateway: its settings changed since it started: PLUGINS.")

    def test_too_many_tags_for_a_line_say_that_and_the_rows_say_which(self):
        labels = {name: name for name in "abcde"}
        whys = {"a": tag("1", "2"), "b": tag("3", "4"), "c": tag("5", "6"), "d": tag("7", "8"), "e": config()}
        self.assertEqual(reasons.line(whys, labels),
                         "4 containers: the saved images differ from the ones that run; "
                         "e: its configuration changed since it started.")
        # Three tags are said, each with its services
        del whys["d"]
        self.assertEqual(reasons.line(whys, labels),
                         "a: 1 is saved, 2 still runs; b: 3 is saved, 4 still runs; c: 5 is saved, 6 still runs; "
                         "e: its configuration changed since it started.")

    def test_the_settings_of_several_services_are_listed_once_and_cut_after_three(self):
        labels = {"a": "One", "b": "Two"}
        self.assertEqual(reasons.line({"a": config("X", "Y"), "b": config("X", "Y")}, labels),
                         "One, Two: their settings changed since they started: X, Y.")
        many = {"a": config("S1", "S2", "S3", "S4", "S5")}
        self.assertEqual(reasons.line(many), "Its settings changed since it started: S1, S2, S3 and 2 more.")

    def test_a_line_holds_tags_and_names_never_a_value(self):
        secret = "s3cr3t-value"
        # The facts of a why are a tag, a flag and names: nothing a value could ride in
        found = reasons.why(SERVICE, {"image": "x/y:4.13", "env": {"A": secret}}, "config", ["A"])
        self.assertNotIn(secret, str(found))
        self.assertNotIn(secret, reasons.line({"a": found}, {"a": "A"}))


class Causes(unittest.TestCase):
    """The causes of a line as data, for the popover the page draws: every kind, grouped as the line groups them, with the
    services a cause is about, and never a value."""

    def test_every_kind_of_a_service_on_its_own_row(self):
        self.assertEqual(reasons.causes({"a": tag()}), [{"kind": "tag", "saved": "4.12.20", "running": "4.13.0-alpha.4", "older": True, "services": []}])
        self.assertEqual(reasons.causes({"a": IMAGE}), [{"kind": "image", "tag": "3.0.2", "built": False, "services": []}])
        self.assertEqual(reasons.causes({"a": {**IMAGE, "built": True}}), [{"kind": "image", "tag": "3.0.2", "built": True, "services": []}])
        self.assertEqual(reasons.causes({"a": config("GRAVITEE_LICENSE", "REDIS_PASSWORD")}),
                         [{"kind": "settings", "settings": ["GRAVITEE_LICENSE", "REDIS_PASSWORD"], "services": []}])
        # The labels Compose sets are no word of a person's: the configuration changed, no name
        self.assertEqual(reasons.causes({"a": config(labels=True)}), [{"kind": "config", "services": []}])
        self.assertEqual(reasons.causes({"a": config()}), [{"kind": "config", "services": []}])

    def test_the_tags_are_said_without_the_system_they_share(self):
        found = reasons.causes({"a": tag("4.12.20-debian", "4.13.0-alpha.4-debian")})
        self.assertEqual((found[0]["saved"], found[0]["running"]), ("4.12.20", "4.13.0-alpha.4"))
        found = reasons.causes({"a": tag("4.12.20-debian", "4.12.20")})
        self.assertEqual((found[0]["saved"], found[0]["running"]), ("4.12.20-debian", "4.12.20"), "whole when the system is what differs")

    def test_services_are_grouped_as_the_line_groups_them_and_named_when_not_all_of_the_owner(self):
        labels = {"es": "Elasticsearch", "mongo": "MongoDB", "redis": "Redis", "gw": "Gateway"}
        found = reasons.causes({"es": tag("9.5.2", "9.5.3"), "mongo": tag("8.0.29", "8.3.11"), "gw": config("A", "B"), "redis": config("A", "B")},
                               labels)
        self.assertEqual(found, [
            {"kind": "tag", "saved": "9.5.2", "running": "9.5.3", "older": True, "services": ["Elasticsearch"]},
            {"kind": "tag", "saved": "8.0.29", "running": "8.3.11", "older": True, "services": ["MongoDB"]},
            {"kind": "settings", "settings": ["A", "B"], "services": ["Gateway", "Redis"]}])
        # One cause for every service of the owner: the owner's row says who, the cause names nobody
        self.assertEqual(reasons.causes({"gw": tag(), "es": tag()}, labels, whole=True),
                         [{"kind": "tag", "saved": "4.12.20", "running": "4.13.0-alpha.4", "older": True, "services": []}])
        # ... but some of them only: named
        self.assertEqual(reasons.causes({"gw": tag()}, labels, whole=False)[0]["services"], ["Gateway"])

    def test_more_services_than_a_line_names_are_counted_and_too_many_tags_are_one_cause(self):
        many = {f"s{i}": IMAGE for i in range(5)}
        self.assertEqual(reasons.causes(many, {name: name.upper() for name in many})[0]["services"], ["5 containers"])
        tags = {f"s{i}": tag(f"1.{i}", f"2.{i}") for i in range(4)}
        self.assertEqual(reasons.causes(tags), [{"kind": "tag", "saved": None, "running": None, "older": False, "services": []}])

    def test_a_tag_saved_older_than_the_one_that_runs_says_so(self):
        # Apply goes back to it: the page marks it, the dialog of the Apply says it
        self.assertTrue(reasons.causes({"a": tag("4.12.20-debian", "4.13.0-alpha.4-debian")})[0]["older"])
        self.assertFalse(reasons.causes({"a": tag("4.13.0", "4.13.0-alpha.4")})[0]["older"], "a release is above its pre-releases")
        self.assertFalse(reasons.causes({"a": tag("latest", "4.13.0")})[0]["older"], "a tag that moves names no version")

    def test_never_a_value(self):
        # What drift.py finds is a name; the causes hold names and tags, and the line the same
        found = reasons.causes({"a": config("INTERNAL_API_PASSWORD")})
        self.assertEqual(found, [{"kind": "settings", "settings": ["INTERNAL_API_PASSWORD"], "services": []}])
        self.assertEqual(set().union(*(cause.keys() for cause in found)), {"kind", "settings", "services"})
