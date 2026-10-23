"""images: components derived from each service's `image:` as written, the Images page's state, saving to the .env
that owns each value, where tags are listed, and the Azure login. No disk, no Docker, no registry: what they would
answer is given."""

import unittest

from lab_ui.docker import compose, envfiles, images
from lab_ui.library import layers
from lab_ui import paths

from .. import Case

DATABASES = {"kind": "platform", "id": "databases", "name": "Databases"}
APIM = {"kind": "platform", "id": "api-management", "name": "API Management"}
KAFKA = {"kind": "addon", "id": "kafka", "name": "Kafka"}
KIOSK = {"kind": "addon", "id": "kiosk", "name": "Kiosk"}
TOOLS = {"kind": "addon", "id": "tools", "name": "Tools"}

GATEWAY_IMAGE = ("${APIM_GATEWAY_REGISTRY:-${APIM_REGISTRY:-graviteeio}}/apim-gateway"
                 ":${APIM_GATEWAY_VERSION:-${APIM_VERSION:-4.12.20}-debian}")

# name: (owner, label, image as written, image as Compose runs it, registry a variable chose)
SERVICES = {
    "mongodb": (DATABASES, "MongoDB", "mongo:${MONGODB_VERSION:-8.0.29}", "mongo:8.0.29", None),
    "elasticsearch": (DATABASES, "Elasticsearch", "docker.elastic.co/elasticsearch/elasticsearch:"
                      "${ELASTICSEARCH_VERSION:-9.5.2}", "docker.elastic.co/elasticsearch/elasticsearch:9.5.2", None),
    "gamma-ui": (APIM, "Gamma Console", "${APIM_REGISTRY:-graviteeio}/gamma-ui:${APIM_VERSION:-4.12.20}",
                 "graviteeio.azurecr.io/gamma-ui:4.13.0", "graviteeio.azurecr.io"),
    "apim-management-api": (APIM, "Management API", "${APIM_REGISTRY:-graviteeio}/apim-management-api:"
                            "${APIM_VERSION:-4.12.20}", "graviteeio.azurecr.io/apim-management-api:4.13.0",
                            "graviteeio.azurecr.io"),
    "apim-gateway": (APIM, "Gateway", GATEWAY_IMAGE, "graviteeio.azurecr.io/apim-gateway:4.13.0-debian",
                     "graviteeio.azurecr.io"),
    "kafka": (KAFKA, "Kafka broker", "docker.io/bitnamilegacy/kafka:${KAFKA_VERSION:-3.9}",
              "docker.io/bitnamilegacy/kafka:3.9", None),
    "kafka-ui": (KAFKA, "Kafka UI", "provectuslabs/kafka-ui:latest", "provectuslabs/kafka-ui:latest", None),
    "kiosk": (KIOSK, "Kiosk", "docker.elastic.co/kiosk/kiosk:${KIOSK_VERSION:-${ELASTICSEARCH_VERSION:-9.5.2}}",
               "docker.elastic.co/kiosk/kiosk:9.5.2", None),
    "mcp-lens": (TOOLS, "MCP Lens", "ghcr.io/modelcontextprotocol/inspector:${MCP_LENS_VERSION:-2.9.0}",
                      "ghcr.io/modelcontextprotocol/inspector:2.9.0", None),
    "mirror": (TOOLS, "Mirror", "localhost:5000/team/mirror:${MIRROR_VERSION:-1}", "localhost:5000/team/mirror:1",
               None),
    "built": (TOOLS, "Built here", "gravitee-lab/built", "gravitee-lab/built", None),
}
# The io.gravitee.image.tags label of the services that have one
TAGS = {"elasticsearch": "library/elasticsearch", "kiosk": "library/kiosk"}


def project():
    services = {name: {"label": label, "owner": owner, "raw_image": raw, "image": image,
                       "tag": image.split(":")[-1], "registry": registry, "image_tags": TAGS.get(name)}
                for name, (owner, label, raw, image, registry) in SERVICES.items()}
    return {"groups": [{"id": "databases"}, {"id": "api-management"}], "addons": {}, "playgrounds": {},
            "services": services}


class Images(Case):

    def setUp(self):
        self.project = project()

    def component(self, cid):
        return next(c for c in images.components(self.project) if c["id"] == cid)

    def test_registry_and_version_variables(self):
        c = self.component("APIM_VERSION")
        self.assertEqual((c["kind"], c["version_key"], c["version_default"]), ("gravitee", "APIM_VERSION", "4.12.20"))
        self.assertEqual((c["registry_key"], c["registry_default"]), ("APIM_REGISTRY", "graviteeio"))
        self.assertEqual((c["host"], c["repository"], c["source"]), (None, "apim-management-api", None))
        self.assertEqual(c["follows"], [])

    def test_services_sharing_a_version_are_one_component(self):
        c = self.component("APIM_VERSION")
        self.assertEqual(c["services"], ["apim-management-api", "gamma-ui"])
        self.assertEqual((c["label"], c["detail"]), ("Management API, Gamma Console", "API Management"))
        self.assertEqual(c["owner"], APIM)

    def test_nested_default_follows_another_component(self):
        c = self.component("APIM_GATEWAY_VERSION")
        self.assertEqual((c["version_default"], c["registry_default"]), ("${APIM_VERSION}-debian", "${APIM_REGISTRY}"))
        self.assertEqual(c["follows"], [{"id": "APIM_VERSION", "version": "${APIM_VERSION}-debian",
                                         "registry": "${APIM_REGISTRY}"}])

    def test_third_party_images(self):
        found = [c for c in images.components(self.project) if c["owner"] in (KAFKA, TOOLS) or c["label"] == "MongoDB"]
        where = {c["id"]: (c["kind"], c["host"], c["repository"], c["source"], c["registry_key"]) for c in found}
        self.assertEqual(where, {
            "MONGODB_VERSION": ("third-party", None, "library/mongo", "Docker Hub", None),
            "KAFKA_VERSION": ("third-party", None, "bitnamilegacy/kafka", "Docker Hub", None),
            "MCP_LENS_VERSION": ("third-party", "ghcr.io", "modelcontextprotocol/inspector", "ghcr.io", None),
            "MIRROR_VERSION": ("third-party", "localhost:5000", "team/mirror", "localhost:5000", None)})

    def test_tags_label_says_where_tags_are_listed(self):
        elastic, kiosk = self.component("ELASTICSEARCH_VERSION"), self.component("KIOSK_VERSION")
        self.assertEqual((elastic["host"], elastic["repository"], elastic["source"]),
                         (None, "library/elasticsearch", "docker.elastic.co"))
        self.assertEqual((kiosk["host"], kiosk["repository"]), (None, "library/kiosk"))
        self.assertEqual(kiosk["version_default"], "${ELASTICSEARCH_VERSION}")
        self.assertEqual([(f["id"], f["version"], "registry" in f) for f in kiosk["follows"]],
                         [("ELASTICSEARCH_VERSION", "${ELASTICSEARCH_VERSION}", False)])

    def test_without_the_label_tags_come_from_the_image_registry(self):
        self.project["services"]["elasticsearch"]["image_tags"] = None
        c = self.component("ELASTICSEARCH_VERSION")
        self.assertEqual((c["host"], c["repository"]), ("docker.elastic.co", "elasticsearch/elasticsearch"))

    def test_an_image_without_a_version_variable_is_no_component(self):
        listed = {name for c in images.components(self.project) for name in c["services"]}
        self.assertEqual(set(SERVICES) - listed, {"kafka-ui", "built"})

    def test_platform_first_in_group_order_then_by_owner(self):
        # Under one owner, what most services share first: API Management before its gateway
        self.assertEqual([c["id"] for c in images.components(self.project)],
                         ["ELASTICSEARCH_VERSION", "MONGODB_VERSION", "APIM_VERSION", "APIM_GATEWAY_VERSION",
                          "KAFKA_VERSION", "KIOSK_VERSION", "MCP_LENS_VERSION", "MIRROR_VERSION"])

    def test_the_gateway_of_an_add_on_follows_the_group_that_runs_its_image(self):
        # The hybrid gateway runs API Management's gateway image (project._gateway_groups): its component comes after
        # the group's own, says the group under its name, and stays the add-on's (its owner, its .env, its state)
        hybrid = {"kind": "addon", "id": "hybrid", "name": "Hybrid gateway"}
        self.project["services"]["apim-hybrid-gateway"] = {
            "label": "Hybrid gateway", "owner": hybrid, "image": "graviteeio/apim-gateway:4.12.21-debian",
            "raw_image": "${HYBRID_REGISTRY:-graviteeio}/apim-gateway:${HYBRID_GATEWAY_VERSION:-4.12.21-debian}",
            "tag": "4.12.21-debian", "registry": "graviteeio", "image_tags": None}
        self.project["addons"]["hybrid"] = {"gateway": {"service": "apim-hybrid-gateway", "group": "api-management"}}
        self.project["groups"] = [{"id": "databases", "name": "Databases"},
                                  {"id": "api-management", "name": "API Management"}]
        found = images.components(self.project)
        self.assertEqual([c["id"] for c in found],
                         ["ELASTICSEARCH_VERSION", "MONGODB_VERSION", "APIM_VERSION", "APIM_GATEWAY_VERSION",
                          "HYBRID_GATEWAY_VERSION", "KAFKA_VERSION", "KIOSK_VERSION", "MCP_LENS_VERSION",
                          "MIRROR_VERSION"])
        gateway = self.component("HYBRID_GATEWAY_VERSION")
        self.assertEqual((gateway["label"], gateway["detail"], gateway["owner"], gateway["group"]),
                         ("Hybrid gateway", "API Management", hybrid, "api-management"))
        self.assertEqual([c["group"] for c in found if c["owner"]["kind"] == "addon"],
                         ["api-management", None, None, None, None])
        # A gateway whose image no group runs stays with the add-ons, by name
        self.project["addons"]["hybrid"]["gateway"]["group"] = None
        self.assertEqual([c["id"] for c in images.components(self.project)][4:],
                         ["HYBRID_GATEWAY_VERSION", "KAFKA_VERSION", "KIOSK_VERSION", "MCP_LENS_VERSION",
                          "MIRROR_VERSION"])
        self.assertEqual(self.component("HYBRID_GATEWAY_VERSION")["detail"], "Hybrid gateway")

    def test_groups_in_declared_order_and_add_ons_by_name(self):
        self.project["groups"].reverse()
        self.project["services"]["kafka"]["owner"] = {"kind": "addon", "id": "kafka", "name": "zookeeper and Kafka"}
        self.assertEqual([c["id"] for c in images.components(self.project)],
                         ["APIM_VERSION", "APIM_GATEWAY_VERSION", "ELASTICSEARCH_VERSION", "MONGODB_VERSION",
                          "KIOSK_VERSION", "MCP_LENS_VERSION", "MIRROR_VERSION", "KAFKA_VERSION"])

    def test_host_rule(self):
        for name, where in {"redis": (None, "library/redis"), "openfga/openfga": (None, "openfga/openfga"),
                            "docker.io/redis": (None, "library/redis"), "graviteeio/apim-gateway":
                            (None, "graviteeio/apim-gateway"), "localhost/a/b": ("localhost", "a/b"),
                            "graviteeio.azurecr.io/apim-gateway": ("graviteeio.azurecr.io", "apim-gateway"),
                            "registry.example.com:5000/a/b": ("registry.example.com:5000", "a/b")}.items():
            self.assertEqual(images._locate(name), where, name)

    def test_state(self):
        self.patch(layers, "current", return_value={"effective": ["kiosk"], "playground": None})
        self.patch(images, "local_images", return_value={"mongo:8.0.29", "bitnamilegacy/kafka:3.9"})
        self.patch(compose, "project_name", return_value="lab")
        self.patch(compose, "ps", return_value={"mongodb": {"state": "running", "image": "mongo:7"},
                                                "kafka": {"state": "exited", "image": "x"}})
        env = {".env": {"APIM_REGISTRY": "graviteeio.azurecr.io", "APIM_VERSION": "4.13.0"}}
        self.patch(envfiles, "read", side_effect=lambda path: env.get(path, {}))
        state = images.state(self.project)
        self.assertEqual(state["registries"], [{"value": "graviteeio", "label": "Docker Hub"},
                                               {"value": "graviteeio.azurecr.io", "label": "Azure"}])
        by_id = {c["id"]: c for c in state["components"]}
        apim, gateway = by_id["APIM_VERSION"], by_id["APIM_GATEWAY_VERSION"]
        self.assertEqual((apim["version"], apim["registry"]), ("4.13.0", "graviteeio.azurecr.io"))
        self.assertEqual((gateway["version"], gateway["registry"]), ("${APIM_VERSION}-debian", "${APIM_REGISTRY}"))
        self.assertEqual((gateway["version_resolved"], gateway["registry_resolved"]),
                         ("4.13.0-debian", "graviteeio.azurecr.io"))
        mongo, kafka = by_id["MONGODB_VERSION"], by_id["KAFKA_VERSION"]
        self.assertEqual((mongo["configured"], mongo["running"], mongo["pulled"], mongo["enabled"]),
                         (["mongo:8.0.29"], ["mongo:7"], True, True))
        self.assertEqual((kafka["running"], kafka["pulled"], kafka["enabled"]), ([], True, False))
        self.assertEqual((by_id["KIOSK_VERSION"]["enabled"], by_id["KIOSK_VERSION"]["pulled"]), (True, False))
        # What runs with its owner on, and what is off and does not run, say nothing of it
        self.assertEqual((mongo["outside"], kafka["outside"]), (None, None))
        # Kafka runs while its add-on is off: started by a command, in the words of its row
        self.patch(compose, "ps", return_value={"kafka": {"state": "running", "image": "bitnamilegacy/kafka:3.9"}})
        kafka = {c["id"]: c for c in images.state(self.project)["components"]}["KAFKA_VERSION"]
        self.assertEqual(kafka["outside"], {"note": "Started by hand",
                                            "note_title": "Not from the Lab UI, so the platform does not start it."})

    def test_whether_a_component_that_is_on_takes_its_images_from_azure(self):
        self.patch(images, "local_images", return_value=set())
        self.patch(compose, "containers", return_value=({}, None))
        self.patch(envfiles, "read", return_value={})
        current = self.patch(layers, "current", return_value={"effective": [], "playground": None})
        # The platform is always on: its images come from the Azure registry its .env names (resolved here)
        self.assertTrue(images.state(self.project)["azure_in_use"])
        for name in ("gamma-ui", "apim-management-api", "apim-gateway"):
            self.project["services"][name]["registry"] = "graviteeio"
        self.assertFalse(images.state(self.project)["azure_in_use"])
        # An add-on's, only while it is on; a namespace under an Azure host counts too
        self.project["services"]["mirror"].update(raw_image="${MIRROR_REGISTRY:-team}/mirror:${MIRROR_VERSION:-1}",
                                                  registry="acme.azurecr.io/team")
        self.assertFalse(images.state(self.project)["azure_in_use"])
        current.return_value = {"effective": ["tools"], "playground": None}
        self.assertTrue(images.state(self.project)["azure_in_use"])

    def test_with_docker_not_answering_what_is_pulled_is_not_known(self):
        compose.CACHE.drop("local:images")
        self.addCleanup(compose.CACHE.drop, "local:images")
        self.patch(compose, "run", return_value=(1, "", "Cannot connect to the Docker daemon"))
        self.assertIsNone(images.local_images())
        self.patch(layers, "current", return_value={"effective": [], "playground": None})
        self.patch(compose, "containers", return_value=({}, "Cannot connect to the Docker daemon"))
        self.patch(envfiles, "read", return_value={})
        self.assertEqual({c["pulled"] for c in images.state(self.project)["components"]}, {None})

    def test_save_writes_each_value_to_the_env_that_owns_it(self):
        write = self.patch(envfiles, "write")
        self.patch(envfiles, "read", return_value={})
        images.save(self.project, {"APIM_VERSION": "4.13.0", "APIM_REGISTRY": "graviteeio",
                                   "APIM_GATEWAY_VERSION": "${APIM_VERSION}-debian",
                                   "KIOSK_VERSION": "9.5.0", "KAFKA_VERSION": "3.9"})
        self.assertEqual({call.args[0]: call.args[1] for call in write.call_args_list}, {
            ".env": {"APIM_VERSION": "4.13.0", "APIM_REGISTRY": None, "APIM_GATEWAY_VERSION": None},
            paths.unit_env("addon", "kiosk"): {"KIOSK_VERSION": "9.5.0"},
            paths.unit_env("addon", "kafka"): {"KAFKA_VERSION": None}})

    def test_save_drops_a_line_that_follows_as_the_default_does(self):
        # The gateway's registry written as its default follows the API Management's: once that one goes back to its
        # default, the line would be read blank, with a warning. Lines of a value of its own stay.
        write = self.patch(envfiles, "write")
        self.patch(envfiles, "read", return_value={"APIM_REGISTRY": "graviteeio.azurecr.io",
                                                   "APIM_GATEWAY_REGISTRY": "${APIM_REGISTRY}", "AM_VERSION": "4.13.0"})
        images.save(self.project, {"APIM_REGISTRY": "graviteeio"})
        self.assertEqual(write.call_args.args, (".env", {"APIM_REGISTRY": None, "APIM_GATEWAY_REGISTRY": None}))

    def test_what_saving_them_would_have_compose_make_again(self):
        # The core's values in its .env as a save writes them, an add-on's given to Compose as they are
        drafted = self.patch(images.files, "drafted", return_value=({"kafka": "h"}, True))
        self.patch(envfiles, "read", return_value={})
        values = {"APIM_VERSION": "4.13.0", "APIM_REGISTRY": "graviteeio", "KIOSK_VERSION": "9.5.0",
                  "KAFKA_VERSION": "3.9"}
        self.assertEqual(images.drafted(self.project, values), ({"kafka": "h"}, True))
        # Each add-on's by its .env: Compose reads them for every file, which those of another add-on must not reach
        drafted.assert_called_once_with(self.project, {"APIM_VERSION": "4.13.0", "APIM_REGISTRY": None},
                                        {paths.unit_env("addon", "kiosk"): {"KIOSK_VERSION": "9.5.0"},
                                         paths.unit_env("addon", "kafka"): {"KAFKA_VERSION": "3.9"}})
        # What they may make again, when that cannot be known: the services of the components they set
        self.assertEqual(images.changing(self.project, {"APIM_VERSION": "4.13.0", "KAFKA_VERSION": "3.9"}),
                         ["apim-gateway", "apim-management-api", "gamma-ui", "kafka"])
        with self.assertRaisesRegex(ValueError, "Invalid tag"):
            images.drafted(self.project, {"APIM_VERSION": "not a tag!"})

    def test_save_refuses_what_is_not_a_tag_a_registry_or_a_followed_reference(self):
        write = self.patch(envfiles, "write")
        for values in ({"REDIS_PASSWORD": "x"}, {"APIM_VERSION": "4.13 0"}, {"APIM_VERSION": "${AM_VERSION}"},
                       {"APIM_REGISTRY": "https://example.com"}, {"APIM_VERSION": None},
                       {"MONGODB_VERSION": "8.0.30", "APIM_VERSION": ""}):
            with self.assertRaises(ValueError, msg=values):
                images.save(self.project, values)
        write.assert_not_called()

    def test_tags_are_listed_where_the_image_is(self):
        compose.CACHE.drop("tags:")
        self.addCleanup(compose.CACHE.drop, "tags:")
        for name in ("_hub_tags", "_oci_tags", "_azure_tags"):
            self.patch(images, name, side_effect=lambda *where, name=name: [name, *where])
        asked = {(cid, registry): images.tags(self.project, cid, registry) for cid, registry in (
            ("APIM_VERSION", None), ("APIM_VERSION", "graviteeio"), ("APIM_VERSION", "registry.example.com/team"),
            ("ELASTICSEARCH_VERSION", None), ("MCP_LENS_VERSION", "ignored"))}
        self.assertEqual(asked, {
            ("APIM_VERSION", None): ["_azure_tags", "graviteeio.azurecr.io", "apim-management-api"],
            ("APIM_VERSION", "graviteeio"): ["_hub_tags", "graviteeio/apim-management-api"],
            ("APIM_VERSION", "registry.example.com/team"): ["_oci_tags", "registry.example.com",
                                                            "team/apim-management-api"],
            ("ELASTICSEARCH_VERSION", None): ["_hub_tags", "library/elasticsearch"],
            ("MCP_LENS_VERSION", "ignored"): ["_oci_tags", "ghcr.io", "modelcontextprotocol/inspector"]})
        with self.assertRaises(ValueError):
            images.tags(self.project, "APIM_VERSION", "bad registry")
        with self.assertRaises(paths.NotFound):
            images.tags(self.project, "NOPE_VERSION")

    def test_the_azure_cli_is_found_once_and_every_command_runs_it(self):
        self.patch(images, "AZ", "/opt/az/bin/az")
        jobs, run = self.patch(images, "JOBS"), self.patch(compose, "run", return_value=(0, "[]", ""))
        images.login("graviteeio.azurecr.io")
        self.assertEqual(self.started(jobs)[1][0][0], "/opt/az/bin/az")
        images._azure_tags("graviteeio.azurecr.io", "apim-management-api")
        compose.CACHE.drop("local:registry")
        self.addCleanup(compose.CACHE.drop, "local:registry")
        self.patch(images, "_can_pull", return_value=True)
        images.registry_status(self.project)
        self.assertEqual({call.args[0][0] for call in run.call_args_list}, {"/opt/az/bin/az"})
        self.assertEqual(len(run.call_args_list), 2)

    def test_login_only_to_an_azure_registry(self):
        jobs = self.patch(images, "JOBS")
        images.login("graviteeio.azurecr.io")
        self.assertEqual(self.started(jobs)[1:], ([[images.AZ, "acr", "login", "--name", "graviteeio"]],
                                                  {"key": "registry:graviteeio.azurecr.io", "verb": "login"}))
        for host in ("graviteeio", "evil.example.com", "x.azurecr.io/../y", None):
            with self.assertRaises(ValueError):
                images.login(host)
        # From a row's menu, it is the job of that item: its button shows it
        images.login("graviteeio.azurecr.io", "playground:local/sandbox")
        self.assertEqual(self.started(jobs)[2], {"key": "playground:local/sandbox", "verb": "login"})
        for key in ("platform", "registry:x.azurecr.io", "addon:Kafka", "group:databases", ""):
            with self.assertRaises(ValueError, msg=key):
                images.login("graviteeio.azurecr.io", key)


if __name__ == "__main__":
    unittest.main()


class Anonymous(Case):
    """images.private_images: whether a person with no login can pull an image, asked of its registry as `docker pull` asks it
    (stood in for here): Docker Hub on its own API, another registry for the manifest of that very image, without a login then with
    the token it gives anyone. What a registry does not answer about is not said."""

    def setUp(self):
        self.answers = {}
        self.patch(images, "_status", side_effect=lambda url, token=None: self.answers.get((url, bool(token)), (None, "")))
        self.patch(images, "_status_get", side_effect=lambda url: self.answers.get((url, False), (None, "")))
        self.patch(images, "_json", side_effect=lambda url, token=None: self.answers[("token", url)])
        self.patch(compose, "CACHE", compose.Cache())   # each test asks afresh

    def test_docker_hub_says_it_of_the_repository(self):
        self.answers[("https://hub.docker.com/v2/repositories/library/busybox/", False)] = (200, "")
        self.answers[("https://hub.docker.com/v2/repositories/acme/secret/", False)] = (404, "")
        self.assertEqual(images.private_images(["busybox:1.36", "acme/secret:2", "acme/secret:3"]),
                         {"Docker Hub": ["acme/secret:2", "acme/secret:3"]})

    def test_another_registry_is_asked_for_the_manifest_with_the_token_anyone_gets(self):
        realm = 'Bearer realm="https://reg.example/token",service="reg.example",scope="repository:{}:pull"'
        for repo, reference, code in (("open/app", "1", 200), ("closed/app", "sha256:ab", 403)):
            url = f"https://reg.example/v2/{repo}/manifests/{reference}"
            self.answers[(url, False)] = (401, realm.format(repo))
            self.answers[("token", f"https://reg.example/token?service=reg.example&scope=repository%3A{repo.replace('/', '%2F')}%3Apull")] = {"token": "t"}
            self.answers[(url, True)] = (code, "")
        self.assertEqual(images.private_images(["reg.example/open/app:1", "reg.example/closed/app@sha256:ab"]),
                         {"reg.example": ["reg.example/closed/app@sha256:ab"]})

    def test_a_registry_that_gives_no_token_to_anyone_is_private_and_one_that_asks_nothing_is_not(self):
        realm = 'Bearer realm="https://reg.example/token",service="reg.example",scope="repository:locked/app:pull"'
        self.answers[("https://reg.example/v2/locked/app/manifests/latest", False)] = (401, realm)
        self.answers[("https://reg.example/v2/free/app/manifests/2", False)] = (200, "")
        def refuse(url, token=None):
            raise images.urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        self.patch(images, "_json", side_effect=refuse)
        self.assertEqual(images.private_images(["reg.example/locked/app", "reg.example/free/app:2"]),
                         {"reg.example": ["reg.example/locked/app"]})

    def test_a_registry_that_does_not_answer_is_not_said_private(self):
        self.assertEqual(images.private_images(["reg.example/quiet/app:1"]), {})
