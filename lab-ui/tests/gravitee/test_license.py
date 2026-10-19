"""license: a license3j license decoded, the license model parsed and found in a product's image. No Docker: what
it would run is replaced."""

import base64
import datetime
import unittest
from unittest import mock

from lab_ui.docker import compose, envfiles
from lab_ui.gravitee import license

from .. import Case

PROJECT = {
    "license_var": "GRAVITEE_LICENSE", "license_services": ["gateway", "management"],
    "groups": [{"id": "databases", "product": None}, {"id": "api-management", "product": "apim"}],
    "products": {"apim": {"name": "API Management", "folder": "plugins/apim",
                          "services": {"gateway": "/opt/gateway/plugins"}, "lib": {"gateway": "/opt/gateway/lib"}}},
    "services": {"gateway": {"container": "gio-gateway", "image": "graviteeio/apim-gateway:4", "label": "Gateway"}},
}

# In the shape of the license-model.yml the products ship: comments, banners, anchors on features and on packs
MODEL = """\
packs:
    enterprise-features:
        features:
            - apim-debug-mode # not a plugin. Feature is in the UI
            - apim-bridge-gateway # repository: gravitee-apim-repository-bridge-http-server

            - am-policy-authzen # policy: gravitee-am-policy-authzen
    observability:
        features:
            - apim-reporter-tcp # reporter: gravitee-reporter-tcp
    ############################### Gamma packs ################################
    standard:
        features: &standard-features
            - apim-policy-assign-metrics
            - apim-policy-data-cache
    enterprise:
        features:
            - *standard-features
            - apim-en-entrypoint-webhook
    #############################################################################

tiers:
    planet:
        packs: &planet-packs
            - enterprise-features
    galaxy:
        packs: &galaxy-packs
            - *planet-packs
            - observability
    universe:
        packs:
            - *galaxy-packs
            - enterprise
    gamma-planet:
        packs:
            - standard
"""


def feature(kind, name, value):
    """One license3j feature, as a license holds it."""
    name = name.encode()
    head = kind.to_bytes(4, "big") + len(name).to_bytes(4, "big")
    if kind not in license.FIXED_SIZES:
        head += len(value).to_bytes(4, "big")
    blob = head + name + value
    return len(blob).to_bytes(4, "big") + blob


class Model(Case):

    def test_packs_and_tiers(self):
        model = license.parse_model(MODEL)
        self.assertEqual(model["packs"], {
            "enterprise-features": ["apim-debug-mode", "apim-bridge-gateway", "am-policy-authzen"],
            "observability": ["apim-reporter-tcp"],
            "standard": ["apim-policy-assign-metrics", "apim-policy-data-cache"],
            "enterprise": ["apim-policy-assign-metrics", "apim-policy-data-cache", "apim-en-entrypoint-webhook"]})
        self.assertEqual(model["tiers"], {
            "planet": ["enterprise-features"], "galaxy": ["enterprise-features", "observability"],
            "universe": ["enterprise-features", "observability", "enterprise"], "gamma-planet": ["standard"]})
        self.assertTrue(all(set(packs) <= set(model["packs"]) for packs in model["tiers"].values()))

    def test_anchors_and_aliases(self):
        text = ("packs:\n    a:\n        features: &a\n            - f1 # why\n            - f2\n    b:\n"
                "        features:\n            - *a\n            - f3\ntiers:\n    t:\n        packs:\n"
                "            - a\n")
        self.assertEqual(license.parse_model(text),
                         {"packs": {"a": ["f1", "f2"], "b": ["f1", "f2", "f3"]}, "tiers": {"t": ["a"]}})

    def test_the_model_comes_from_the_running_container(self):
        asked = []

        def run(cmd, timeout=120, cwd=None):
            asked.append(cmd[:3])
            return 0, "/opt/gateway/lib/gravitee-node-license-7.1.0.jar\n" + MODEL, ""
        compose.CACHE.drop("license-model:")
        self.addCleanup(compose.CACHE.drop, "license-model:")
        image = self.patch(compose, "image_key", return_value="sha256:running")
        with mock.patch.object(compose, "run", side_effect=run):
            found = license._model(PROJECT)
            license._model(PROJECT)   # kept, by the image it was read from
        self.assertEqual((found["source"], found["service"]), ("gravitee-node-license-7.1.0", "Gateway"))
        self.assertEqual(found["tiers"]["planet"], ["enterprise-features"])
        self.assertEqual(asked, [["docker", "exec", "gio-gateway"]])
        # Its container now runs another image: read again, not kept under the name of the one saved
        image.return_value = "sha256:new"
        with mock.patch.object(compose, "run", side_effect=run):
            license._model(PROJECT)
        self.assertEqual(len(asked), 2)

    def test_why_no_image_could_tell_the_model_in_plain_words(self):
        # The image is not here: it says which one to pull; Docker down says so
        compose.CACHE.drop("license-model:")
        self.addCleanup(compose.CACHE.drop, "license-model:")
        self.patch(compose, "image_key", return_value="graviteeio/apim-gateway:4")
        missing = (125, "", "docker: Error response from daemon: No such image: graviteeio/apim-gateway:4\n")
        for answers, said, pull in (
                ([(1, "", "Error response from daemon: No such container: gio-gateway\n"), missing],
                 "Not known until graviteeio/apim-gateway:4 is pulled", "graviteeio/apim-gateway:4"),
                ([(1, "", "Cannot connect to the Docker daemon at unix:///x.sock. Is the docker daemon running?")],
                 "Docker is not reachable", None),
                ([(1, "", "unzip: nothing\n")], "No product image carries a license model", None)):
            with mock.patch.object(compose, "run", side_effect=answers), \
                    self.assertRaises(license.Unknown) as raised:
                license._model(PROJECT)
            self.assertEqual((str(raised.exception), raised.exception.pull), (said, pull))


class License(Case):

    def test_decode(self):
        raw = license.MAGIC + feature(2, "tier", b"universe") \
            + feature(11, "expiryDate", (4102444800000).to_bytes(8, "big")) \
            + feature(2, "features", b"x,y") + feature(2, "legacy-feature", b"included")
        features, valid = license.decode(base64.b64encode(raw).decode())
        self.assertEqual((features["tier"], features["expiryDate"].year), ("universe", 2100))
        self.assertFalse(valid)   # not signed by GraviteeSource
        with self.assertRaises(ValueError):
            license.decode(base64.b64encode(b"nope").decode())
        grants = license._grants(features, {"packs": {"p": ["f1"]}, "tiers": {"universe": ["p"]}})
        self.assertEqual(grants["features"], ["f1", "legacy-feature", "x", "y"])
        self.assertEqual(grants["extra_features"], ["legacy-feature", "x", "y"])
        self.assertTrue(grants["tier_known"])

    def test_public_key(self):
        n, e = license._public_key()
        self.assertEqual((n.bit_length(), e), (2048, 65537))

    def test_no_license_and_an_unreadable_one_need_no_docker(self):
        # Without a license, the model is still asked for the names of its packs; an unreadable one asks nothing
        values = {}
        with mock.patch.object(envfiles, "read", side_effect=lambda path: values), \
                mock.patch.object(compose, "run") as run, mock.patch.dict(license._known, features=None), \
                mock.patch.object(license, "_model", side_effect=license.Unknown("Docker is not reachable")) as model:
            self.assertIsNone(license.features_cached())
            self.assertEqual(license.info(PROJECT), {"present": False, "employee": False})
            self.assertEqual(license.features_cached(), set())
            model.assert_called_once_with(PROJECT)
            values["GRAVITEE_LICENSE"] = "not base64 !!"
            self.assertEqual(license.info(PROJECT), {"present": True, "readable": False, "employee": False})
            self.assertEqual(license.features_cached(), set())
            model.assert_called_once()
        run.assert_not_called()

    def test_a_gravitee_employee_is_said_by_one_setting_nothing_else_shows(self):
        for value, said in (("true", True), ("TRUE", True), ('"1"', True), ("yes", True), ("false", False), ("", False), ("maybe", False)):
            with self.subTest(value=value), mock.patch.object(envfiles, "read", side_effect=lambda path: {"GRAVITEE_EMPLOYEE": value}), \
                    mock.patch.object(license, "_model", side_effect=license.Unknown("no docker")), mock.patch.dict(license._known, features=None, packs=None):
                self.assertEqual(license.info(PROJECT)["employee"], said)

    def test_a_feature_is_said_by_the_name_of_its_pack(self):
        self.start(mock.patch.dict(license._known, features=None, packs=None))
        # While the model is not known, by its id
        self.assertEqual(license.named(["apim-reporter-tcp", "am-policy-authzen"]),
                         ["am-policy-authzen", "apim-reporter-tcp"])
        # Read without a license too: the fewest packs that hold them, the smaller of two that hold as many
        with mock.patch.object(envfiles, "read", return_value={}), \
                mock.patch.object(license, "_model", return_value=license.parse_model(MODEL)):
            license.info(PROJECT)
        self.assertEqual(license.named(["apim-policy-data-cache"]), ["Standard"])
        self.assertEqual(license.named(["apim-policy-data-cache", "apim-en-entrypoint-webhook"]), ["Enterprise"])
        self.assertEqual(license.named(["apim-reporter-tcp", "unknown-feature", "am-policy-authzen"]),
                         ["Enterprise features", "Observability", "unknown-feature"])
        # As the License dialog writes them: acronyms and products' ids in capitals
        self.assertEqual([license._title(pack, {"mfa", "oem", "apim"}) for pack in
                          ("enterprise-mfa-factor", "oem", "apim-gateway-packs")],
                         ["Enterprise MFA factor", "OEM", "APIM gateway packs"])
        # Docker gone: the ids again
        with mock.patch.object(envfiles, "read", return_value={}), \
                mock.patch.object(license, "_model", side_effect=license.Unknown("Docker is not reachable")):
            license.info(PROJECT)
        self.assertEqual(license.named(["apim-reporter-tcp"]), ["apim-reporter-tcp"])

    def test_without_a_product_image_the_license_still_says_what_it_says_itself(self):
        # Expired in 2020: no image to read the model from changes nothing to that
        raw = license.MAGIC + feature(2, "tier", b"universe") + feature(2, "company", b"ACME") \
            + feature(11, "expiryDate", (1577836800000).to_bytes(8, "big"))
        values = {"GRAVITEE_LICENSE": base64.b64encode(raw).decode()}
        compose.CACHE.drop("license-model:")
        self.addCleanup(compose.CACHE.drop, "license-model:")
        refused = (1, "", "Error response from daemon: No such container: gio-gateway\n")
        with mock.patch.object(envfiles, "read", side_effect=lambda path: values), \
                mock.patch.object(compose, "run", return_value=refused), \
                mock.patch.dict(license._known, features=None):
            found = license.info(PROJECT)
            self.assertEqual(license.features_cached(), set())   # expired: nothing allowed, model or not
        self.assertEqual({key: found[key] for key in ("present", "readable", "expired", "company", "tier")},
                         {"present": True, "readable": True, "expired": True, "company": "ACME", "tier": "universe"})
        self.assertEqual({key: found[key] for key in license.MODEL_FACTS}, dict.fromkeys(license.MODEL_FACTS))
        self.assertEqual((found["model_error"], found["pull"]), ("No product image carries a license model", None))

    def status(self, value, valid=True, expires=None):
        """What status() says of a license in .env: decoded as given, its signature as `valid` says."""
        features = {"tier": "universe", **({"expiryDate": expires} if expires else {})}
        with mock.patch.object(envfiles, "read", return_value={"GRAVITEE_LICENSE": value}), \
                mock.patch.object(license, "decode", return_value=(features, valid)) as decode, \
                mock.patch.object(compose, "run") as run:
            if value == "unreadable":
                decode.side_effect = ValueError("Not a license")
            found = license.status(PROJECT)
        run.assert_not_called()   # every few seconds: never Docker
        return found

    def test_its_status_as_the_banner_and_the_rows_say_it(self):
        now = datetime.datetime.now(datetime.timezone.utc)

        def in_days(days):
            return now + datetime.timedelta(days=days, hours=1)
        cases = [("", True, None, "none", None),
                 ("unreadable", True, None, "unreadable", "The license cannot be read: Enterprise features are off"),
                 ("x", False, None, "invalid",
                  "The license is not signed by GraviteeSource: Enterprise features are off"),
                 ("x", True, None, "valid", None),
                 ("x", True, in_days(90), "valid", None),
                 ("x", True, in_days(12), "expiring", f"The license expires in 12 days ({in_days(12).day} "
                                                     f"{in_days(12):%b %Y})"),
                 ("x", True, in_days(1), "expiring", f"The license expires in 1 day ({in_days(1).day} "
                                                    f"{in_days(1):%b %Y})"),
                 ("x", True, now + datetime.timedelta(hours=3), "expiring", None),
                 ("x", True, in_days(-3), "expired", None)]
        for value, valid, expires, status, problem in cases:
            with self.subTest(status=status, expires=expires):
                found = self.status(value, valid, expires)
                self.assertEqual(found["status"], status)
                if problem:
                    self.assertEqual(found["problem"], problem)
                self.assertEqual(found["digest"], compose.digest(value))
        self.assertTrue(self.status("x", True, now + datetime.timedelta(hours=3))["problem"].startswith(
            "The license expires today ("))
        expired = self.status("x", True, datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
        self.assertEqual((expired["problem"], expired["date"], expired["expires"][:10]),
                         ("The license expired on 1 Jan 2020: Enterprise features are off", "1 Jan 2020", "2020-01-01"))
        self.assertIsNone(license.status({**PROJECT, "license_var": None}))   # nothing reads a license

    def test_a_license_is_usable_when_genuine_and_not_expired(self):
        # What the services that need one start with: valid, or expiring within 30 days
        now = datetime.datetime.now(datetime.timezone.utc)
        cases = [("", True, None, False), ("unreadable", True, None, False), ("x", False, None, False),
                 ("x", True, None, True), ("x", True, now + datetime.timedelta(days=90), True),
                 ("x", True, now + datetime.timedelta(days=5), True),
                 ("x", True, now - datetime.timedelta(days=3), False)]
        for value, valid, expires, usable in cases:
            with self.subTest(value=value, valid=valid, expires=expires):
                self.assertIs(license.usable(self.status(value, valid, expires)), usable)
        self.assertIs(license.usable(None), False)   # nothing reads a license
        self.assertIs(license.usable(license.status_of(None)), False)

    def test_applied_when_what_runs_reads_the_license_saved(self):
        # Each container that reads the license says which one it runs, as a digest (compose.ps(LICENSE_KEY))
        saved, other = compose.digest("new"), compose.digest("old")
        running = {"state": "running", "variable": saved}
        self.assertTrue(license.applied(PROJECT, {"gateway": running, "management": running}, saved))
        self.assertFalse(license.applied(PROJECT, {"gateway": running, "management": {**running, "variable": other}},
                                         saved))
        # What does not run takes it at its start; a container read without it tells nothing
        self.assertTrue(license.applied(PROJECT, {"gateway": running, "management": {"state": "exited",
                                                                                     "variable": other}}, saved))
        self.assertTrue(license.applied(PROJECT, {"gateway": {"state": "running"}}, saved))

    def test_the_services_that_run_another_license_are_named(self):
        saved, other = compose.digest("new"), compose.digest("old")
        running = {"state": "running", "variable": saved}
        self.assertEqual(license.unapplied(PROJECT, {"gateway": running, "management": {**running, "variable": other}},
                                           saved), ["management"])
        self.assertEqual(license.unapplied(PROJECT, {"gateway": running, "management": running}, saved), [])

    def test_what_a_license_allows(self):
        current = {"present": True, "readable": True, "valid": True, "expired": False, "features": ["f1"]}
        self.assertEqual(license.allowed(current), {"f1"})
        self.assertIsNone(license.allowed({**current, "features": None}))   # the model could not be read
        for refused in ({**current, "valid": False}, {**current, "expired": True}, {"present": False},
                        {"present": True, "readable": False}):
            self.assertEqual(license.allowed(refused), set(), refused)


if __name__ == "__main__":
    unittest.main()
