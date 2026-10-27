"""project.build() on inputs shaped like what docker compose prints: no disk, no Docker. Then what tells that a file
the project is read from changed, in a temporary folder. The first classes read the layout before catalogs (Legacy); the
last read the fixture catalogs (tests/data), as the Lab UI reads a machine with catalogs: from the fixtures with the real
core (Compose renders the files, no daemon; a skip that shows without Compose 5) and from inputs made by hand."""

import copy
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose, envfiles
from lab_ui.library import layers, project
from lab_ui.gravitee import license, places, platforms
from lab_ui import paths
from lab_ui.catalogs import trust

from .. import Case, fixtures

CORE, KAFKA, CONSOLE = "docker-compose.yml", "addons/kafka/compose.yml", "addons/kafka-desk/compose.yml"
JADE, OPENFGA = "addons/jade/compose.yml", "addons/openfga/compose.yml"
INSPECTOR = "addons/agent-lens/compose.yml"
EVENTS, MINE, WELCOME = "setups/event-streams.yml", "setups/local/mine.yml", "playgrounds/welcome/compose.yml"
WHOAMI, SANDBOX = "addons/local/whoami/compose.yml", "playgrounds/local/sandbox/compose.yml"
HYBRID, CLOUD = "addons/hybrid/compose.yml", "playgrounds/cloud/compose.yml"


class Legacy(Case):
    """A project as it was before catalogs: with no catalogs.default.json the units sit in addons/, setups/ and playgrounds/,
    each kind with its personal ones in a folder `local`, and the units are read and shown together as they always were.
    The repository has a default file now, so these cases ask for that layout: what they say must hold while it is served."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patcher = mock.patch.object(paths, "default_ns", return_value=None)
        patcher.start()
        cls.addClassCleanup(patcher.stop)


def port(published, target=None, host_ip="127.0.0.1"):
    """A port as Compose prints it: published on one address of this machine, unless `host_ip` is None."""
    return {"mode": "ingress", "protocol": "tcp", "published": str(published), "target": target or published,
            **({"host_ip": host_ip} if host_ip else {})}


def bind(source, target):
    return {"type": "bind", "source": str(paths.ROOT / source), "target": target, "bind": {"create_host_path": True}}


def service(image=None, name=None, restart="unless-stopped", ports=(), **more):
    labels = {"io.gravitee.description": "What it does", **more.pop("labels", {}), **({"io.gravitee.name": name} if name else {})}
    definition = {"image": image, "restart": restart, "ports": list(ports), "labels": labels, **more}
    return {key: value for key, value in definition.items() if value not in (None, [], {})}


def variable(key, default=None, comment=""):
    return {"key": key, "default": default, "required": default is None, "comment": comment}


GATEWAY_IMAGE = ("${APIM_GATEWAY_REGISTRY:-${APIM_REGISTRY:-graviteeio}}/apim-gateway"
                 ":${APIM_GATEWAY_VERSION:-${APIM_VERSION:-4.12.20}-debian}")

# What an add-on's file defines shows again, merged in, in every file that includes it
KAFKA_SERVICES = {
    "kafka": service("docker.io/bitnamilegacy/kafka:${KAFKA_VERSION:-3.9}", "Kafka broker", ports=[port(9091)]),
    "kafka-client": service("docker.io/bitnamilegacy/kafka:${KAFKA_VERSION:-3.9}", "Kafka client"),
    "kafka-ui": service("provectuslabs/kafka-ui:latest", "Kafka UI", restart=None, ports=[port(8088, 8080)],
                        labels={"io.gravitee.console.kafka-ui.name": "Kafka UI",
                                "io.gravitee.console.kafka-ui.url": "http://localhost:8088"}),
    "apim-gateway": {"ports": [port(9092)], "environment": {"gravitee_kafka_enabled": "true"}},
}
INSPECTOR_SERVICES = {
    "agent-lens": service("gravitee-lab/agent-lens", "AI Agent Lens", ports=[port(9002)],
                               build={"context": "https://example.com/repo.git"}),
    "apim-gateway": {"environment": {"gravitee_reporters_tcp_enabled": "true"}},
}
OPENFGA_SERVICES = {
    "openfga": service("openfga/openfga:${OPENFGA_VERSION:-v1.21.0}", "OpenFGA", ports=[port(3000)],
                       depends_on={"openfga-migrate": {"condition": "service_completed_successfully",
                                                       "required": True}}),
    "openfga-migrate": service("openfga/openfga:${OPENFGA_VERSION:-v1.21.0}", restart=None),
}

RAW = {
    CORE: {
        "name": "gravitee",
        "x-gravitee": {"name": "Gravitee platform", "groups": ["Databases", "API Management"],
                       "categories": ["Gravitee Platform", "AI", "Event", "Observability", "Other"]},
        "services": {
            "redis": service("redis:${REDIS_VERSION:-8.10.1-alpine}", "Redis", ports=[port(6379)],
                             container_name="gio-redis", labels={"io.gravitee.group": "Databases"},
                             command="redis-server --requirepass ${REDIS_PASSWORD:-redispassword}",
                             volumes=[{"type": "volume", "source": "data-redis", "target": "/data", "volume": {}}]),
            "mongodb": service("mongo:${MONGODB_VERSION:-8.0.29}", "MongoDB", ports=[port(27017)],
                               labels={"io.gravitee.group": "Databases", "io.gravitee.image.tags": "library/mongo"}),
            "apim-management-api": service(
                "${APIM_REGISTRY:-graviteeio}/apim-management-api:${APIM_VERSION:-4.12.20}", "Management API",
                ports=[port(8083), port(18083)], labels={"io.gravitee.group": "API Management"},
                volumes=[bind("plugins/apim", "/opt/graviteeio-management-api/plugins-ext")],
                environment={"gravitee_license_key": "${GRAVITEE_LICENSE:-}",
                             "gravitee_plugins_path_1": "$${gravitee.home}/plugins-ext"}),
            "apim-gateway": service(
                GATEWAY_IMAGE, "Gateway", ports=[port(8082), port(18082)],
                labels={"io.gravitee.group": "API Management"},
                volumes=[bind("plugins/apim", "/opt/graviteeio-gateway/plugins-ext"),
                         bind("certs/mtls", "/opt/graviteeio-gateway/mtls")],
                environment={"gravitee_license_key": "${GRAVITEE_LICENSE:-}",
                             "gravitee_ratelimit_redis_password": "${REDIS_PASSWORD:-redispassword}"}),
            "apim-portal-ui": service(
                "${APIM_REGISTRY:-graviteeio}/apim-portal-ui:${APIM_VERSION:-4.12.20}", "Developer Portal",
                ports=[port(8085, 8080)],
                labels={"io.gravitee.group": "API Management",
                        "io.gravitee.console.portal-legacy.name": "Legacy Developer Portal",
                        "io.gravitee.console.portal-legacy.url": "http://localhost:8085",
                        "io.gravitee.console.portal-legacy.legacy": "true",
                        "io.gravitee.console.portal.name": "Developer Portal",
                        "io.gravitee.console.portal.url": "http://localhost:8085/next/"}),
        },
    },
    KAFKA: {
        "name": "kafka",
        "x-gravitee": {"kind": "addon", "name": "Kafka", "category": "Event", "description": "Kafka broker"},
        "services": KAFKA_SERVICES,
    },
    CONSOLE: {
        "name": "kafka-desk",
        "x-gravitee": {"kind": "addon", "name": "Kafka Desk", "category": "Gravitee Platform",
                       "description": "Gravitee's console for Kafka"},
        "services": {
            **KAFKA_SERVICES,
            "kafka-desk": service(
                "${KAFKA_DESK_REGISTRY:-graviteeio}/apim-kafka-desk:${KAFKA_DESK_VERSION:-4.9.0}",
                "Kafka Desk", ports=[port(8080)], container_name="gio-kafka-desk",
                labels={"io.gravitee.console.kafka-desk.name": "Kafka Desk",
                        "io.gravitee.console.kafka-desk.url": "http://localhost:8080"}),
            "apim-management-api": {"environment": {"gravitee_kafka_desk_enabled": "true"}},
        },
    },
    JADE: {
        "x-gravitee": {"kind": "addon", "name": "Jade", "category": "Observability", "description": "Tracing"},
        "services": {"jade": service("jadetracing/jade:${JADE_VERSION:-2.21.0}", "Jade", ports=[port(16686)]),
                     "apim-gateway": {"environment": {"gravitee_services_opentelemetry_enabled": "true"}}},
    },
    INSPECTOR: {
        "x-gravitee": {"kind": "addon", "name": "AI Agent Lens", "category": "AI", "description": "Live diagram",
                       "conflicts": {"jade": "both take the gateway's OpenTelemetry exporter"},
                       "requires": {"license": ["apim-reporter-tcp"]}},
        "services": INSPECTOR_SERVICES,
    },
    OPENFGA: {
        "x-gravitee": {"kind": "addon", "name": "OpenFGA", "category": "AI", "description": "Authorization"},
        "services": OPENFGA_SERVICES,
    },
    EVENTS: {"x-gravitee": {"kind": "setup", "name": "Event streams", "category": "Event", "description": "Kafka"},
             "services": {}},
    MINE: {"x-gravitee": {"kind": "setup", "name": "My demo"}, "services": {}},
    WELCOME: {
        "x-gravitee": {"kind": "playground", "name": "Welcome", "category": "AI",
                       "description": "ACME Hello",
                       "tags": ["MCP", "A2A"], "duration": "30 min", "first_start": "5 min",
                       "consoles": ["acme-website"],
                       "requires": {"plugins": {"apim": ["gravitee-policy-interrupt"]}},
                       "accounts": [{"for": "Website", "username": "john", "password": "${WELCOME_PASSWORD:-doe}"}]},
        "services": {
            **INSPECTOR_SERVICES, **OPENFGA_SERVICES,
            "acme-website": service("gravitee-lab/acme-website", "ACME Hello website", ports=[port(8002, 80)],
                                    build={"context": "https://example.com/repo.git"}, pull_policy="build",
                                    labels={"io.gravitee.console.acme.name": "ACME Hello",
                                            "io.gravitee.console.acme.url": "http://localhost:8002"}),
            "hello-agent": service("gravitee-lab/hello-agent", "Hello AI agent"),
            "hello-seed": service("gravitee-lab/hello-seed", "Playground seed", restart="no",
                                       labels={"io.gravitee.role": "seed"}, build={"context": "/seeder"},
                                       pull_policy="build"),
            # The inspector's change to the gateway, with the playground's own on top
            "apim-gateway": {"environment": {"gravitee_reporters_tcp_enabled": "true",
                                             "gravitee_groovy_whitelist_mode": "append"}},
        },
    },
}

IMAGES = {
    "redis": "redis:8.10.1-alpine", "mongodb": "mongo:8.0.29",
    "apim-management-api": "graviteeio.azurecr.io/apim-management-api:4.13.0",
    "apim-portal-ui": "graviteeio.azurecr.io/apim-portal-ui:4.13.0",
    "apim-gateway": "graviteeio.azurecr.io/apim-gateway:4.13.0-debian",
    "kafka": "docker.io/bitnamilegacy/kafka:3.9", "kafka-client": "docker.io/bitnamilegacy/kafka:3.9",
    "kafka-desk": "localhost:5000/team/apim-kafka-desk:4.9.0", "jade": "jadetracing/jade:2.21.0",
    "openfga": "openfga/openfga:v1.21.0", "openfga-migrate": "openfga/openfga:v1.21.0",
}


def resolved():
    """One interpolated configuration of everything: each service once, images with their values."""
    services = {}
    for path in (WELCOME, OPENFGA, INSPECTOR, JADE, CONSOLE, KAFKA, CORE):
        for name, definition in RAW[path]["services"].items():
            if "image" in definition:
                services[name] = dict(definition, image=IMAGES.get(name, definition["image"]))
    services["apim-gateway"]["ports"] = services["apim-gateway"]["ports"] + [port(9092)]
    return services


def inputs():
    return copy.deepcopy({
        "raw": RAW,
        "includes": {CORE: [], KAFKA: [], CONSOLE: [KAFKA], JADE: [], INSPECTOR: [], OPENFGA: [],
                     EVENTS: [CONSOLE], MINE: [JADE, CONSOLE], WELCOME: [OPENFGA, INSPECTOR]},
        "variables": {
            CORE: [variable("GRAVITEE_LICENSE", "", "Enterprise license"), variable("REDIS_VERSION", "8.10.1-alpine"),
                   variable("REDIS_PASSWORD", "redispassword", "Redis password"), variable("MONGODB_VERSION", "8.0.29"),
                   variable("APIM_REGISTRY", "graviteeio"), variable("APIM_VERSION", "4.12.20"),
                   variable("APIM_GATEWAY_REGISTRY", "${APIM_REGISTRY:-graviteeio}"),
                   variable("APIM_GATEWAY_VERSION", "${APIM_VERSION:-4.12.20}-debian")],
            CONSOLE: [variable("KAFKA_DESK_REGISTRY", "graviteeio"), variable("KAFKA_DESK_VERSION", "4.9.0"),
                      variable("KAFKA_DESK_SECRET", "demo", "Signs the console's tokens"),
                      variable("KAFKA_DESK_THEME", None, "Light or dark"), variable("GRAVITEE_LICENSE", "")],
            KAFKA: [variable("KAFKA_VERSION", "3.9")],
        },
        "resolved": resolved(),
        "environ": {}, "env": {},   # the environment of the Lab UI, and the variables of each .env file
        "license_usable": True,
        "guides": {"welcome": "# Welcome\n\n## 1. Semantic caching\ntext\n\n## 2.  Rate limiting \n\n"
                               "## Not numbered\n"},
        "seeds": {},   # what the seed folder of each playground holds, by name
        "problems": [{"file": "addons/broken/compose.yml", "message": "yaml: line 3: did not find expected key"}],
    })


# The add-on that runs the gateway of a platform, with its own Redis, and a playground that can be deployed to that
# platform. They are not in RAW: the tests of the other modules count its add-ons and playgrounds.
HYBRID_RAW = {
    "x-gravitee": {"kind": "addon", "name": "Hybrid gateway", "category": "Gravitee Platform",
                   "description": "Gateway managed by a platform",
                   "gateway": {"platform": "cloud", "service": "apim-hybrid-gateway", "token": "HYBRID_CLOUD_TOKEN"}},
    "services": {
        "apim-hybrid-gateway": service(
            "${HYBRID_GATEWAY_REGISTRY:-graviteeio}/apim-gateway:${HYBRID_GATEWAY_VERSION:-4.12.21-debian}",
            "Hybrid gateway", ports=[port(8090, 8082)], environment={"gravitee_cloud_token": "${HYBRID_CLOUD_TOKEN}"},
            depends_on={"hybrid-redis": {"condition": "service_healthy", "required": True}}),
        "hybrid-redis": service("redis:${HYBRID_REDIS_VERSION:-${REDIS_VERSION:-8.10.1-alpine}}",
                                "Hybrid gateway Redis"),
    },
}
CLOUD_RAW = {
    "x-gravitee": {"kind": "playground", "name": "Cloud", "category": "AI", "description": "Deploys to a platform"},
    "services": {
        "cloud-api": service("gravitee-lab/cloud-api", "Cloud API", ports=[port(8120, 8000)],
                             environment={"PORT": "8000"}, build={"context": "https://example.com/repo.git"},
                             pull_policy="build"),
        "cloud-seed": service("gravitee-lab/cloud-seed", "Seed", restart="no", labels={"io.gravitee.role": "seed"},
                              build={"context": "/seeder"}, pull_policy="build",
                              environment={"APIM_MANAGEMENT_URL": "http://apim-management-api:8083"},
                              depends_on={"apim-management-api": {"condition": "service_healthy", "required": True}}),
    },
}


def with_cloud():
    """inputs() with the hybrid gateway add-on and the playground that can be deployed to a platform: an API seed, apps
    that stand alone."""
    given = inputs()
    given["raw"].update({HYBRID: copy.deepcopy(HYBRID_RAW), CLOUD: copy.deepcopy(CLOUD_RAW)})
    given["includes"].update({HYBRID: [], CLOUD: []})
    given["variables"].update({
        HYBRID: [variable("HYBRID_GATEWAY_REGISTRY", "graviteeio"),
                 variable("HYBRID_GATEWAY_VERSION", "4.12.21-debian"),
                 variable("HYBRID_REDIS_VERSION", "${REDIS_VERSION:-8.10.1-alpine}"),
                 variable("REDIS_VERSION", "8.10.1-alpine"),
                 variable("HYBRID_CLOUD_TOKEN", None, "Token of the hybrid gateway"),
                 variable("HYBRID_LICENSE", "", "License key")], CLOUD: []})
    given["guides"]["cloud"] = "# Cloud\n\n## 1. First\n"
    given["seeds"]["cloud"] = ["apim"]
    return given


class Build(Legacy):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.given = inputs()
        cls.project = project.build(cls.given)

    def test_inputs_are_left_as_they_were(self):
        self.assertEqual(self.given, inputs())

    def test_platform(self):
        p = self.project
        self.assertEqual((p["name"], p["core_loaded"]), ("Gravitee platform", True))
        self.assertEqual(p["categories"], ["Gravitee Platform", "AI", "Event", "Observability", "Other"])
        self.assertEqual(p["license_var"], "GRAVITEE_LICENSE")
        # The services that read the license: one saved and not applied is told by what they run
        self.assertEqual(p["license_services"], ["apim-gateway", "apim-management-api"])

    def test_groups_in_declared_order(self):
        groups = self.project["groups"]
        self.assertEqual([g["name"] for g in groups], ["Databases", "API Management"])
        self.assertEqual([g["id"] for g in groups], ["databases", "api-management"])
        self.assertEqual(groups[0]["services"], ["mongodb", "redis"])
        self.assertEqual(groups[1]["services"], ["apim-gateway", "apim-management-api", "apim-portal-ui"])
        self.assertEqual({(g["file"], g["env_file"]) for g in groups}, {("docker-compose.yml", ".env")})

    def test_group_version_is_the_one_most_images_share(self):
        databases, apim = self.project["groups"]
        self.assertEqual((apim["registry"], apim["version"]), ("graviteeio.azurecr.io", "4.13.0"))
        self.assertEqual((databases["registry"], databases["version"]), (None, None))   # third-party images

    def test_group_settings_are_the_core_variables_its_services_use(self):
        databases, apim = self.project["groups"]
        self.assertEqual(databases["settings"], ["REDIS_PASSWORD"])
        self.assertEqual(apim["settings"], ["GRAVITEE_LICENSE", "REDIS_PASSWORD"])

    def test_core_variables_leave_image_variables_out(self):
        self.assertEqual(self.project["core_variables"], [
            {"key": "GRAVITEE_LICENSE", "default": "", "required": False, "comment": "Enterprise license",
             "secret": True},
            {"key": "REDIS_PASSWORD", "default": "redispassword", "required": False, "comment": "Redis password",
             "secret": True}])

    def test_products_from_plugin_mounts(self):
        self.assertEqual(self.project["products"], {"apim": {
            "name": "API Management", "folder": "plugins/apim",
            "services": {"apim-gateway": "/opt/graviteeio-gateway/plugins",
                         "apim-management-api": "/opt/graviteeio-management-api/plugins"},
            "lib": {"apim-gateway": "/opt/graviteeio-gateway/lib",
                    "apim-management-api": "/opt/graviteeio-management-api/lib"}}})
        self.assertEqual([g["product"] for g in self.project["groups"]], [None, "apim"])

    def test_own_services_leave_out_the_core_and_what_is_included(self):
        addons = self.project["addons"]
        self.assertEqual(addons["kafka"]["services"], ["kafka", "kafka-client", "kafka-ui"])
        self.assertEqual(addons["kafka-desk"]["services"], ["kafka-desk"])
        self.assertEqual(addons["kafka-desk"]["needs"], ["kafka"])
        self.assertEqual(addons["kafka"]["needs"], [])

    def test_restarts_are_the_core_services_a_file_changes_itself(self):
        addons = self.project["addons"]
        self.assertEqual(addons["kafka"]["restarts"], ["apim-gateway"])
        self.assertEqual(addons["kafka-desk"]["restarts"], ["apim-management-api"])   # the gateway is Kafka's
        self.assertEqual(addons["openfga"]["restarts"], [])
        self.assertEqual(self.project["playgrounds"]["welcome"]["restarts"], ["apim-gateway"])

    def test_a_core_service_that_two_includes_change_is_theirs(self):
        # Merged, the gateway is neither Kafka's nor Jade's: what each of them sets is still not the file's
        given, demo = inputs(), "playgrounds/demo/compose.yml"
        gateway = {"ports": [port(9092)], "environment": {"gravitee_kafka_enabled": "true",
                                                          "gravitee_services_opentelemetry_enabled": "true"}}
        given["raw"][demo] = {"x-gravitee": {"kind": "playground", "name": "Demo"},
                              "services": {**KAFKA_SERVICES, **RAW[JADE]["services"], "apim-gateway": gateway}}
        given["includes"][demo] = [KAFKA, JADE]
        self.assertEqual(project.build(given)["playgrounds"]["demo"]["restarts"], [])
        gateway["environment"]["gravitee_groovy_whitelist_mode"] = "append"   # a setting of its own
        self.assertEqual(project.build(given)["playgrounds"]["demo"]["restarts"], ["apim-gateway"])

    def test_addon(self):
        addon = self.project["addons"]["agent-lens"]
        self.assertEqual({k: addon[k] for k in ("id", "kind", "name", "category", "description", "file", "env_file")},
                         {"id": "agent-lens", "kind": "addon", "name": "AI Agent Lens", "category": "AI",
                          "description": "Live diagram", "file": INSPECTOR, "env_file": "addons/agent-lens/.env"})
        self.assertEqual(addon["conflicts"], {"jade": "both take the gateway's OpenTelemetry exporter"})
        self.assertEqual(addon["requires"], {"license": ["apim-reporter-tcp"]})
        self.assertEqual(self.project["addons"]["jade"]["conflicts"], {})

    def test_settings_are_an_addons_own_variables(self):
        settings = self.project["addons"]["kafka-desk"]["settings"]
        self.assertEqual(settings, [
            {"key": "KAFKA_DESK_SECRET", "default": "demo", "required": False,
             "comment": "Signs the console's tokens", "secret": True},
            {"key": "KAFKA_DESK_THEME", "default": None, "required": True, "comment": "Light or dark",
             "secret": False}])
        self.assertEqual(self.project["addons"]["kafka"]["settings"], [])   # KAFKA_VERSION belongs to Images

    def test_setups(self):
        setups = self.project["setups"]
        self.assertEqual(list(setups), ["event-streams", "local/mine"])
        self.assertEqual(setups["event-streams"]["includes"], ["kafka-desk"])
        self.assertEqual(setups["event-streams"]["members"], ["kafka", "kafka-desk"])
        self.assertFalse(setups["event-streams"]["personal"])
        mine = setups["local/mine"]
        self.assertEqual((mine["kind"], mine["name"], mine["category"], mine["description"], mine["file"]),
                         ("setup", "My demo", "", "", MINE))
        self.assertTrue(mine["personal"])
        self.assertEqual(mine["includes"], ["jade", "kafka-desk"])
        self.assertEqual(mine["members"], ["jade", "kafka", "kafka-desk"])

    def test_what_belongs_to_this_machine_is_built_like_the_rest(self):
        given = inputs()
        whoami = {"whoami": service("traefik/whoami:${WHOAMI_VERSION:-v1.11}", "Whoami", ports=[port(8098, 80)],
                                    environment={"WHOAMI_NAME": "${WHOAMI_NAME}"})}
        given["raw"][WHOAMI] = {"x-gravitee": {"kind": "addon", "name": "Whoami", "category": "Other"},
                                "services": whoami}
        given["raw"][SANDBOX] = {"x-gravitee": {"kind": "playground", "name": "Sandbox"},
                                 "services": {**whoami, **KAFKA_SERVICES, **RAW[JADE]["services"],
                                              **RAW[CONSOLE]["services"]}}
        given["includes"].update({WHOAMI: [], SANDBOX: [MINE, WHOAMI]})
        given["variables"][WHOAMI] = [variable("WHOAMI_VERSION", "v1.11"), variable("WHOAMI_NAME", None, "Its name")]
        given["guides"]["local/sandbox"] = "# Sandbox\n\n## 1. First step\n"
        built = project.build(given)
        addon, playground = built["addons"]["local/whoami"], built["playgrounds"]["local/sandbox"]
        self.assertEqual((addon["file"], addon["env_file"], addon["services"]),
                         (WHOAMI, "addons/local/whoami/.env", ["whoami"]))
        self.assertEqual([setting["key"] for setting in addon["settings"]], ["WHOAMI_NAME"])
        self.assertEqual((playground["file"], playground["setups"], playground["addons"], playground["services"]),
                         (SANDBOX, ["local/mine"], ["jade", "kafka", "kafka-desk", "local/whoami"], []))
        self.assertEqual(playground["chapters"], ["First step"])
        self.assertEqual(built["services"]["whoami"]["owner"],
                         {"kind": "addon", "id": "local/whoami", "name": "Whoami"})
        self.assertEqual([unit["id"] for kind in ("addons", "setups", "playgrounds") for unit in built[kind].values()
                          if unit["personal"]], ["local/whoami", "local/mine", "local/sandbox"])

    def test_a_playground_brings_the_add_ons_of_the_setups_it_includes(self):
        given = inputs()
        given["includes"][WELCOME] = [EVENTS, OPENFGA, INSPECTOR]
        # Compose merges in what the setup's add-ons define; the gateway keeps the playground's own change
        merged = given["raw"][WELCOME]["services"]
        merged.update({name: service for name, service in RAW[CONSOLE]["services"].items() if name not in merged})
        playground = project.build(given)["playgrounds"]["welcome"]
        self.assertEqual(playground["setups"], ["event-streams"])
        self.assertEqual(playground["includes"], ["openfga", "agent-lens"])   # in the order of the file
        self.assertEqual(playground["addons"], ["agent-lens", "kafka", "kafka-desk", "openfga"])
        # What the setup's add-ons run and change is theirs
        self.assertEqual(playground["services"], ["acme-website", "hello-agent", "hello-seed"])
        self.assertEqual(playground["restarts"], ["apim-gateway"])

    def test_playground(self):
        playground = self.project["playgrounds"]["welcome"]
        self.assertEqual(playground["setups"], [])
        self.assertEqual(playground["addons"], ["agent-lens", "openfga"])
        self.assertEqual(playground["services"], ["acme-website", "hello-agent", "hello-seed"])
        self.assertEqual(playground["apps"], ["acme-website", "hello-agent"])
        self.assertEqual(playground["seed"], "hello-seed")
        self.assertEqual(playground["chapters"], ["Semantic caching", "Rate limiting"])
        # Its guide by a version of its text: the page reads it again once it changed
        given = inputs()
        given["guides"]["welcome"] += "## 3. Tracing\n"
        changed = project.build(given)["playgrounds"]["welcome"]["guide"]
        self.assertEqual([len(version) for version in (playground["guide"], changed)], [12, 12])
        self.assertNotEqual(changed, playground["guide"])
        self.assertEqual((playground["tags"], playground["duration"], playground["first_start"], playground["source"]),
                         (["MCP", "A2A"], "30 min", "5 min", None))
        self.assertEqual(playground["consoles"], ["acme-website"])
        self.assertNotIn("start", playground)
        self.assertEqual(playground["requires"], {"plugins": {"apim": ["gravitee-policy-interrupt"]}})
        self.assertEqual(playground["accounts"], [{"for": "Website", "username": "john", "password": "doe"}])

    def test_service_of_the_platform(self):
        gateway = self.project["services"]["apim-gateway"]
        self.assertEqual(gateway["label"], "Gateway")
        self.assertEqual(gateway["container"], "gravitee-apim-gateway-1")
        self.assertEqual(gateway["owner"], {"kind": "platform", "id": "api-management", "name": "API Management"})
        self.assertEqual(gateway["raw_image"], GATEWAY_IMAGE)
        self.assertEqual(gateway["image"], "graviteeio.azurecr.io/apim-gateway:4.13.0-debian")
        self.assertEqual((gateway["registry"], gateway["tag"]), ("graviteeio.azurecr.io", "4.13.0-debian"))
        self.assertEqual(gateway["ports"], ["8082", "18082"])   # its own: 9092 is published by Kafka
        self.assertIsNone(gateway["image_tags"])
        self.assertEqual(self.project["services"]["mongodb"]["image_tags"], "library/mongo")
        self.assertEqual(self.project["services"]["redis"]["container"], "gio-redis")
        self.assertIsNone(self.project["services"]["redis"]["registry"])

    def test_a_service_that_runs_the_image_another_builds_runs_a_build_of_here(self):
        # The agent and the agent without the gateway: one builds the image, both run a build of here
        given = inputs()
        for where in (given["resolved"], given["raw"][WELCOME]["services"]):
            where["hello-agent"]["image"] = where["acme-website"]["image"]
        services = project.build(given)["services"]
        self.assertEqual((services["acme-website"]["built"], services["hello-agent"]["built"]), (True, True))
        self.assertFalse(project.build(inputs())["services"]["hello-agent"]["built"])

    def test_service_of_an_addon_and_of_a_playground(self):
        services = self.project["services"]
        console = services["kafka-desk"]
        self.assertEqual(console["owner"], {"kind": "addon", "id": "kafka-desk", "name": "Kafka Desk"})
        self.assertEqual((console["registry"], console["tag"]), ("localhost:5000/team", "4.9.0"))
        self.assertEqual(services["kafka"]["owner"]["id"], "kafka")
        website = services["acme-website"]
        self.assertEqual(website["owner"], {"kind": "playground", "id": "welcome", "name": "Welcome"})
        self.assertEqual((website["built"], website["tag"], website["role"]), (True, "latest", None))
        self.assertEqual(services["hello-seed"]["role"], "seed")
        self.assertEqual(services["agent-lens"]["owner"]["kind"], "addon")
        self.assertEqual(len(services), 16)

    def test_one_shot_services(self):
        services = self.project["services"]
        oneshot = {name for name, s in services.items() if s["oneshot"]}
        # Kafka UI has no restart policy, but it publishes a port; the Kafka client restarts unless stopped
        self.assertEqual(oneshot, {"openfga-migrate", "hello-seed"})

    def test_a_service_says_whether_it_needs_a_license(self):
        # The label io.gravitee.license: required, as Compose runs the service; any other value, or none, is no need
        given = inputs()
        given["resolved"]["redis"]["labels"]["io.gravitee.license"] = "required"
        given["resolved"]["mongodb"]["labels"]["io.gravitee.license"] = "optional"
        services = project.build(given)["services"]
        self.assertEqual({name for name, s in services.items() if s["needs_license"]}, {"redis"})

    def test_whether_the_license_is_usable_is_given_not_read(self):
        # build() is pure: the fact comes with the other inputs
        for usable in (True, False):
            self.assertIs(project.build({**inputs(), "license_usable": usable})["license_usable"], usable)

    def test_what_a_service_depends_on(self):
        services = self.project["services"]
        self.assertEqual((services["openfga"]["depends_on"], services["openfga-migrate"]["depends_on"]),
                         (["openfga-migrate"], []))

    def test_a_service_has_the_command_it_runs_as_one_text(self):
        given = inputs()
        given["resolved"]["redis"]["command"] = ["/bin/sh", "-c", "sed -i x; sh /run.sh"]
        given["resolved"]["mongodb"]["command"] = "mongod --quiet"
        built = project.build(given)
        self.assertEqual(built["services"]["redis"]["command"], "/bin/sh -c sed -i x; sh /run.sh")
        self.assertEqual(built["services"]["mongodb"]["command"], "mongod --quiet")
        self.assertEqual(built["services"]["apim-gateway"]["command"], "")

    def test_a_service_says_whether_a_setting_of_its_own_lets_the_lab_ui_frame_it(self):
        given = inputs()
        given["resolved"]["redis"]["environment"] = {"ALLOWED_FRAME_ANCESTORS": "http://localhost:8084 http://localhost:9000 http://127.0.0.1:9000"}
        given["resolved"]["mongodb"]["environment"] = {"ALLOWED_FRAME_ANCESTORS": "http://localhost:8084 http://localhost:9000"}
        built = project.build(given)
        self.assertEqual([built["services"][name]["lets_frame"] for name in ("redis", "mongodb", "apim-gateway")], [True, False, False])

    def test_consoles_current_first(self):
        portal = self.project["services"]["apim-portal-ui"]["consoles"]
        self.assertEqual(portal, [{"label": "Developer Portal", "url": "http://localhost:8085/next/", "legacy": False},
                                  {"label": "Legacy Developer Portal", "url": "http://localhost:8085", "legacy": True}])
        self.assertEqual(self.project["services"]["redis"]["consoles"], [])

    def test_links_current_first_then_by_label(self):
        links = self.project["links"]
        self.assertEqual([(link["label"], link["section"], link["port"], link["service"]) for link in links], [
            ("ACME Hello", "Playground", "8002", "acme-website"),
            ("Developer Portal", "API Management", "8085", "apim-portal-ui"),
            ("Kafka Desk", "Gravitee Platform", "8080", "kafka-desk"),
            ("Kafka UI", "Event", "8088", "kafka-ui"),
            ("Legacy Developer Portal", "API Management", "8085", "apim-portal-ui")])
        self.assertEqual([link["legacy"] for link in links], [False, False, False, False, True])
        self.assertEqual(links[0]["url"], "http://localhost:8002")

    def test_problems_are_kept(self):
        self.assertEqual(self.project["problems"],
                         [{"file": "addons/broken/compose.yml", "message": "yaml: line 3: did not find expected key"}])
        self.assertNotIn("broken", self.project["addons"])


class Degraded(Legacy):
    """What does not load, or is not declared as it should be, is reported and the rest still builds."""

    def test_a_file_that_does_not_load_says_why_without_its_full_path(self):
        refused = compose.ComposeError(f"validating {paths.ROOT / INSPECTOR}: services.x additional properties 'imgae'")
        with mock.patch.object(compose, "raw", side_effect=refused):
            self.assertEqual(project._raw(INSPECTOR),
                             (None, {"file": INSPECTOR, "message": "services.x additional properties 'imgae'"}))

    def test_a_service_outside_the_declared_groups(self):
        given = inputs()
        del given["raw"][CORE]["services"]["mongodb"]["labels"]["io.gravitee.group"]
        built = project.build(given)
        problem = {"file": CORE, "message": "mongodb: io.gravitee.group must be one of x-gravitee.groups"}
        self.assertIn(problem, built["problems"])
        self.assertEqual(built["groups"][0]["services"], ["redis"])

    def test_conflicts_as_a_list(self):
        given = inputs()
        given["raw"][INSPECTOR]["x-gravitee"]["conflicts"] = ["jade"]
        built = project.build(given)
        self.assertEqual(built["addons"]["agent-lens"]["conflicts"], {})
        problem = {"file": INSPECTOR, "message": "conflicts must map each add-on id to a reason"}
        self.assertIn(problem, built["problems"])

    def test_x_gravitee_that_is_not_a_mapping(self):
        # Compose does not look inside the block: the editor saves it, and the file must still open
        given = inputs()
        given["raw"][KAFKA]["x-gravitee"] = "kafka"
        given["raw"][JADE]["x-gravitee"] = ["addon"]
        given["raw"][CORE]["x-gravitee"] = "platform"
        built = project.build(given)
        for file in (KAFKA, JADE, CORE):
            self.assertIn({"file": file, "message": "x-gravitee must be a mapping"}, built["problems"])
        kafka = built["addons"]["kafka"]
        self.assertEqual((kafka["name"], kafka["category"], kafka["requires"], kafka["services"]),
                         ("kafka", "", {}, ["kafka", "kafka-client", "kafka-ui"]))
        self.assertEqual((built["name"], built["groups"]), ("gravitee", []))
        self.assertEqual(built["setups"]["event-streams"]["members"], ["kafka", "kafka-desk"])

    def test_requires_that_is_not_a_mapping(self):
        given = inputs()
        given["raw"][INSPECTOR]["x-gravitee"]["requires"] = ["license"]
        given["raw"][WELCOME]["x-gravitee"]["requires"] = "enterprise"
        built = project.build(given)
        for file in (INSPECTOR, WELCOME):
            self.assertIn({"file": file, "message": "x-gravitee.requires must be a mapping"}, built["problems"])
        self.assertEqual(built["addons"]["agent-lens"]["requires"], {})
        self.assertEqual(built["playgrounds"]["welcome"]["requires"], {})
        self.assertEqual(built["addons"]["agent-lens"]["name"], "AI Agent Lens")

    def test_the_tested_versions_are_a_list_of_sets_or_are_reported_and_left_out(self):
        given = inputs()
        given["raw"][WELCOME]["x-gravitee"]["tested"] = [{"APIM_VERSION": "4.13.0-alpha.3"}]
        self.assertEqual(project.build(given)["playgrounds"]["welcome"]["tested"], [{"APIM_VERSION": "4.13.0-alpha.3"}])
        for written in ({"APIM_VERSION": "4.13"}, [["APIM_VERSION", "4.13"]], [{"APIM_VERSION": 4.13}], [{}]):
            given["raw"][WELCOME]["x-gravitee"]["tested"] = written
            built = project.build(given)
            self.assertIn({"file": WELCOME, "message": "x-gravitee.tested must be a list of sets, each a mapping of a "
                                                       "version variable to the version it was tested with, in quotes"},
                          built["problems"])
            self.assertEqual(built["playgrounds"]["welcome"]["tested"], [])

    def test_requirements_written_otherwise(self):
        # Each kind as the Lab UI reads it: one written otherwise is reported and left out, the others are kept
        given = inputs()
        given["raw"][INSPECTOR]["x-gravitee"]["requires"] = {"license": "apim-reporter-tcp"}
        given["raw"][WELCOME]["x-gravitee"]["requires"] = {
            "plugins": ["gravitee-policy-interrupt"], "host": [{"name": "Ollama"}], "license": ["feature-1"]}
        built = project.build(given)
        for file, key, shape in ((INSPECTOR, "license", "a list of license features, or a mapping of each feature to "
                                                        "what needs it"),
                                 (WELCOME, "plugins", "a mapping of each product to a list of plugins"),
                                 (WELCOME, "host", "a list of checks, each with a name and a check URL")):
            self.assertIn({"file": file, "message": f"x-gravitee.requires.{key} must be {shape}"}, built["problems"])
        self.assertEqual(built["addons"]["agent-lens"]["requires"], {})
        self.assertEqual(built["playgrounds"]["welcome"]["requires"], {"license": ["feature-1"]})
        # The license also as a mapping of each feature to what needs it, but not to a list
        given["raw"][INSPECTOR]["x-gravitee"]["requires"] = {"license": {"feature-1": "Step 4", "feature-2": None}}
        given["raw"][WELCOME]["x-gravitee"]["requires"] = {"license": {"feature-1": ["Step 4"]}}
        built = project.build(given)
        self.assertEqual(built["addons"]["agent-lens"]["requires"],
                         {"license": {"feature-1": "Step 4", "feature-2": None}})
        self.assertEqual(built["playgrounds"]["welcome"]["requires"], {})

    def test_the_license_features_a_unit_requires_and_what_needs_them(self):
        self.assertEqual(project.license_features({"license": ["a", "b"]}), {"a": None, "b": None})
        self.assertEqual(project.license_features({"license": {"a": "Step 4", "b": ""}}), {"a": "Step 4", "b": None})
        self.assertEqual(project.license_features({}), {})

    def test_a_name_that_is_a_number(self):
        given = inputs()
        given["raw"][KAFKA]["x-gravitee"]["name"] = 2024
        self.assertEqual(project.build(given)["addons"]["kafka"]["name"], "2024")

    def test_without_the_interpolated_configuration(self):
        given = inputs()
        given["resolved"] = {}
        built = project.build(given)
        self.assertEqual(built["services"]["redis"]["image"], "redis:${REDIS_VERSION:-8.10.1-alpine}")
        self.assertEqual(built["addons"]["kafka-desk"]["services"], ["kafka-desk"])

    def test_without_the_core_file(self):
        given = inputs()
        del given["raw"][CORE]
        given["resolved"] = {}   # nothing to interpolate without the core
        built = project.build(given)
        self.assertEqual((built["core_loaded"], built["groups"], built["license_var"], built["products"]),
                         (False, [], None, {}))
        self.assertEqual(built["addons"]["kafka"]["services"], ["kafka", "kafka-client", "kafka-ui", "apim-gateway"])

    def test_the_services_of_a_playground_are_in_the_order_of_its_file_and_its_seed_is_last(self):
        # Not alphabetical: its author orders them, and the seed, which runs once at the end, comes after the apps
        given = inputs()
        services = given["raw"][WELCOME]["services"]
        written = ["hello-seed", "hello-agent", "acme-website"]
        given["raw"][WELCOME]["services"] = {name: services[name] for name in written} | {
            name: s for name, s in services.items() if name not in written}
        playground = project.build(given)["playgrounds"]["welcome"]
        self.assertEqual(playground["services"], ["hello-agent", "acme-website", "hello-seed"])
        self.assertEqual(playground["apps"], ["hello-agent", "acme-website"])

    def test_a_playground_without_guide_or_seed(self):
        given = inputs()
        given["guides"] = {}
        del given["raw"][WELCOME]["services"]["hello-seed"]
        del given["resolved"]["hello-seed"]
        playground = project.build(given)["playgrounds"]["welcome"]
        self.assertEqual((playground["guide"], playground["chapters"], playground["seed"]), (None, [], None))
        self.assertEqual(playground["apps"], playground["services"])


class Platforms(Legacy):
    """What an add-on says of the gateway it runs."""

    def built(self, change=None):
        """The project of with_cloud() once `change(raw of the hybrid add-on, raw of the playground)` is made."""
        given = with_cloud()
        if change:
            change(given["raw"][HYBRID], given["raw"][CLOUD])
        return project.build(given)

    def problems(self, built, path):
        return [problem["message"] for problem in built["problems"] if problem["file"] == path]

    def test_the_gateway_an_add_on_runs(self):
        built = self.built()
        self.assertEqual(built["addons"]["hybrid"]["gateway"], {
            "platform": "cloud", "service": "apim-hybrid-gateway", "token": "HYBRID_CLOUD_TOKEN",
            "domains": ["localhost:8090", "apim-hybrid-gateway:8082"], "group": "api-management"})
        self.assertEqual(self.problems(built, HYBRID), [])
        self.assertEqual(built["addons"]["hybrid"]["services"], ["apim-hybrid-gateway", "hybrid-redis"])
        self.assertEqual(built["addons"]["hybrid"]["restarts"], [])   # its own Redis: it changes nothing of the core

    def test_a_gateway_belongs_to_the_group_that_runs_its_image_whatever_its_registry_and_tag(self):
        # The core's gateway is graviteeio.azurecr.io/apim-gateway:4.13.0-debian, the hybrid one
        # graviteeio/apim-gateway:4.12.21-debian: another registry and another tag, the same image
        built = self.built()
        name = {service: built["services"][service]["image"].split(":")[0] for service in built["services"]}
        self.assertNotEqual(name["apim-gateway"], name["apim-hybrid-gateway"])
        self.assertEqual(built["addons"]["hybrid"]["gateway"]["group"], "api-management")
        # Its Redis runs the image of the core's Redis, in Databases: the gateway decides, not the add-on's other services
        self.assertEqual(name["hybrid-redis"], name["redis"])

    def test_a_gateway_whose_image_no_group_runs_belongs_to_none(self):
        def other(hybrid, cloud):
            hybrid["services"]["apim-hybrid-gateway"]["image"] = "${HYBRID_REGISTRY:-graviteeio}/am-gateway:4.12.7"
        self.assertIsNone(self.built(other)["addons"]["hybrid"]["gateway"]["group"])

    def test_the_image_of_a_service_whatever_its_registry_tag_or_digest(self):
        for service, image in (
                ({"image": "graviteeio/apim-gateway:4.12.21-debian", "registry": "graviteeio"}, "apim-gateway"),
                ({"image": "graviteeio.azurecr.io/apim-gateway:4.13.0", "registry": "graviteeio.azurecr.io"},
                 "apim-gateway"),
                ({"image": "redis:8.10.1-alpine", "registry": None}, "redis"),
                ({"image": "localhost:5000/team/app:1", "registry": None}, "localhost:5000/team/app"),
                ({"image": "redis@sha256:3f2a", "registry": None}, "redis"),
                ({"image": "", "registry": None}, "")):
            with self.subTest(image=service["image"]):
                self.assertEqual(project._repository(service), image)

    def test_the_domains_come_from_the_first_published_port_and_are_listed_nowhere(self):
        def ports(*found):
            return lambda hybrid, cloud: hybrid["services"]["apim-hybrid-gateway"].__setitem__("ports", list(found))
        for found, domains in (
                ([port(8090, 8082), port(8443, 8443)], ["localhost:8090", "apim-hybrid-gateway:8082"]),
                ([{"target": 9000}, port(8095, 8080)], ["localhost:8095", "apim-hybrid-gateway:8080"]),
                ([{"target": 9000}, port("${HYBRID_PORT:-8090}", 8082)], []),   # no number to publish on
                ([{"target": 9000}], []), ([], [])):
            with self.subTest(ports=found):
                built = self.built(ports(*found))
                self.assertEqual(built["addons"]["hybrid"]["gateway"]["domains"], domains)
        # A service that does not exist publishes none: `make check` says so
        self.assertEqual(self.built(lambda hybrid, cloud: hybrid["x-gravitee"]["gateway"].update(service="nothing"))
                         ["addons"]["hybrid"]["gateway"]["domains"], [])

    def test_an_add_on_that_says_none_runs_none(self):
        built = self.built()
        self.assertEqual([uid for uid, addon in built["addons"].items() if addon["gateway"]], ["hybrid"])
        self.assertIsNone(self.built(lambda hybrid, cloud: hybrid["x-gravitee"].pop("gateway"))
                          ["addons"]["hybrid"]["gateway"])
        self.assertIsNone(self.built(lambda hybrid, cloud: hybrid["x-gravitee"].update(gateway=None))
                          ["addons"]["hybrid"]["gateway"])

    def test_a_gateway_written_otherwise_is_reported_and_left_out(self):
        message = "x-gravitee.gateway must be a mapping of platform, service and token, each a text"
        complete = {"platform": "cloud", "service": "apim-hybrid-gateway", "token": "HYBRID_CLOUD_TOKEN"}
        for written in ("cloud", ["cloud"], {}, {**complete, "token": ""}, {**complete, "service": 8082},
                        {key: value for key, value in complete.items() if key != "platform"}):
            with self.subTest(written=written):
                built = self.built(lambda hybrid, cloud: hybrid["x-gravitee"].update(gateway=written))
                self.assertIsNone(built["addons"]["hybrid"]["gateway"])
                self.assertEqual(self.problems(built, HYBRID), [message])

    def test_without_the_key_the_project_is_what_it_was(self):
        # The key changes its own entry of the project and nothing else; the entry is empty without it
        with_key, without = self.built(), self.built(lambda hybrid, cloud: hybrid["x-gravitee"].pop("gateway"))
        self.assertIsNone(without["addons"]["hybrid"]["gateway"])
        with_key["addons"]["hybrid"]["gateway"] = None
        # Where it can go is its own too: the add-on that runs a gateway is where it runs
        for part in ("deploys_to", "cannot"):
            with_key["addons"]["hybrid"][part] = without["addons"]["hybrid"][part]
        # And the project says which add-on runs the gateway of a platform, for the pages to read instead of looking
        self.assertEqual(with_key["platform"], {"gateways": {"cloud": "hybrid"}})
        with_key["platform"] = without["platform"]
        self.assertEqual(without["platform"], {"gateways": {"cloud": None}})
        self.assertEqual(with_key, without)

    def test_a_unit_says_nothing_more_than_it_did(self):
        built = project.build(inputs())
        before = {"addons": {"category", "conflicts", "description", "env_file", "file", "id", "kind", "name", "needs",
                             "personal", "requires", "restarts", "services", "settings"},
                  "setups": {"category", "description", "file", "id", "includes", "kind", "members", "name", "personal"},
                  "playgrounds": {"accounts", "addons", "apps", "category", "chapters", "consoles", "description",
                                  "duration", "env_file", "file", "first_start", "guide", "id", "includes", "kind",
                                  "name", "personal", "requires", "restarts", "seed", "services", "settings", "setups",
                                  "source", "tags", "terminal", "tested"}}
        platform = {"asks", "deploys_to", "cannot"}   # what a platform makes of each, from its files
        # Where it comes from and whether it can start: none of a unit read before catalogs says anything of them
        placed = {"catalog", "origin", "readonly", "display_file", "blocked", "needs_catalogs", "compat", "unknown", "twin",
                  "uses_machine", "verdict"}
        new = {"addons": {"gateway"} | platform | placed, "setups": platform | placed, "playgrounds": platform | placed}
        for kind, units in before.items():
            for uid, unit in built[kind].items():
                with self.subTest(unit=uid):
                    self.assertEqual(set(unit), units | new[kind])


NOTHING, MORE = "Nothing to deploy", "Seeds more than API Management and Access Management"
ACCESS, GAMMA, LATER = "Needs Access Management", "Needs Gamma", "Needs API Management 4.13"
PLUGINS = "Needs plugins on the gateway"
CHANGES, USES = "Changes this machine's Management API", "Its apps use this machine's Management API"


def check(*gateways, environment="env"):
    """A read of a platform, as platforms.fetch answers it, whose environment has these gateways: (scope, version) each,
    as the Cloud lists them."""
    return {"state": "ok", "message": "",
            "environments": [{"id": environment, "hrid": "", "name": "Development", "organizationId": "org"}],
            "gateways": [{"id": f"g{number}", "name": f"gateway {number}", "environmentId": environment,
                          "type": "HYBRID", "scope": scope, "status": "DEPLOYED", "connection": "CONNECTED",
                          "version": version, "domains": []} for number, (scope, version) in enumerate(gateways)]}


class Deployable(Legacy):
    """Where a playground can be deployed: judged from what it is and from what an environment runs, as its gateways
    say, never declared. The playground of with_cloud() is one that can go to an environment whose gateway runs API
    Management 4.12, once something of it is changed: one reason at a time, and each reason with a playground that does
    not have it."""

    def setUp(self):
        self.given = with_cloud()

    def built(self):
        return project.build(self.given)

    def why(self, offers=None, uid="cloud"):
        """The reasons the playground cannot go to an environment that offers this, by default one whose gateway runs
        API Management 4.12.21, the first one first."""
        built = self.built()
        offers = offers or platforms.capabilities(built, check(("APIM", "4.12.21")), "env")
        return project.reasons(built["playgrounds"][uid], built, self.given["raw"], self.given["seeds"].get(uid, []),
                               offers)

    def meta(self, **said):
        self.given["raw"][CLOUD]["x-gravitee"].update(said)

    def app(self, **definition):
        self.given["raw"][CLOUD]["services"]["cloud-api"].update(definition)

    def seed(self, *names):
        self.given["seeds"]["cloud"] = list(names)

    def brings(self, *files):
        """The playground includes these add-ons, which merge their services into its own."""
        self.given["includes"][CLOUD] = list(files)
        for file in files:
            self.given["raw"][CLOUD]["services"].update(self.given["raw"][file]["services"])

    def needs(self, file, name, *others):
        """A service of an add-on starts after these, as its file says and as Compose resolves it."""
        depends_on = {other: {"condition": "service_healthy", "required": True} for other in others}
        for definition in (self.given["raw"][file]["services"][name], self.given["resolved"].get(name, {})):
            definition["depends_on"] = depends_on

    def access_management(self):
        """The core has the product, with an image whose version is a setting, and its plugins folder."""
        self.given["raw"][CORE]["x-gravitee"]["groups"].append("Access Management")
        self.given["raw"][CORE]["services"]["am-gateway"] = service(
            "${AM_REGISTRY:-graviteeio}/am-gateway:${AM_VERSION:-4.12.0}", "AM gateway",
            labels={"io.gravitee.group": "Access Management"},
            volumes=[bind("plugins/am", "/opt/graviteeio-am-gateway/plugins-ext")])

    def test_a_playground_with_an_api_seed_and_apps_that_stand_alone_can_go(self):
        playground = self.built()["playgrounds"]["cloud"]
        self.assertEqual((playground["deploys_to"], playground["cannot"]), (["cloud"], {}))
        self.assertEqual(self.why(), [])
        self.assertEqual(playground["asks"], {"deploys": True, "products": ["API Management"], "versions": [],
                                              "fills": ["API Management"], "plugins": False, "changes": False,
                                              "uses": False, "more": False, "features": [],
                                              "needs": {"entrypoint": [], "endpoint": [], "policy": [], "resource": []}})

    # --- What its APIs need of the Management API ------------------------------------------------------------------

    DEFINITION = {"api": {
        "listeners": [{"type": "HTTP", "entrypoints": [{"type": "llm-proxy"}, {"type": "mcp"}]}],
        "endpointGroups": [{"type": "llm-proxy", "endpoints": [{"type": "llm-proxy"}]}, {"type": "http-proxy"}],
        "resources": [{"type": "cache", "configuration": {"type": "array"}}],
        "flows": [{"request": [{"policy": "rate-limit", "enabled": True, "configuration": {"type": "object"}}],
                   "response": [{"policy": "transform-headers", "enabled": True}]}]},
        "plans": [{"flows": [{"request": [{"policy": "policy-interrupt", "enabled": True}]}]}]}

    def test_the_plugins_of_an_api_definition_are_its_entrypoints_endpoints_policies_and_resources(self):
        # What a configuration holds under "type" is no plugin: only what the definition names as one
        found = project._uses(self.DEFINITION)
        self.assertEqual({kind: sorted(ids) for kind, ids in found.items()},
                         {"entrypoint": ["llm-proxy", "mcp"], "endpoint": ["http-proxy", "llm-proxy"],
                          "policy": ["policy-interrupt", "rate-limit", "transform-headers"], "resource": ["cache"]})
        self.assertEqual(project._uses({}), {kind: set() for kind in project.PLUGIN_KINDS})

    def asked(self, **needs):
        return {"deploys": True, "products": [], "versions": [], "fills": [], "plugins": False, "changes": False,
                "uses": False, "more": False, "needs": {kind: needs.get(kind, []) for kind in project.PLUGIN_KINDS}}

    def test_a_plugin_that_the_management_api_lacks_or_does_not_deploy_is_a_reason(self):
        installed = {"policy": {"rate-limit": True, "ai-prompt-guard-rails": False}, "endpoint": {"llm-proxy": False},
                     "entrypoint": {"http-proxy": True}, "resource": {}}
        offers = {"products": None, "plugins": True, "installed": installed}
        asked = self.asked(policy=["rate-limit", "ai-prompt-guard-rails", "policy-interrupt"],
                           endpoint=["llm-proxy"], entrypoint=["http-proxy", "mcp"])
        # What is not there first, then what the license does not deploy, each kind in its order
        self.assertEqual(project.judge(asked, offers),
                         ["Needs the mcp entrypoint", "Needs the policy-interrupt policy",
                          "The license lacks the llm-proxy endpoint", "The license lacks the ai-prompt-guard-rails policy"])
        # What every plugin has is no reason; an environment whose Management API is not known judges none
        self.assertEqual(project.judge(self.asked(policy=["rate-limit"]), offers), [])
        self.assertEqual(project.judge(asked, {**offers, "installed": None}), [])

    def test_the_features_of_api_management_it_requires_must_be_in_the_license_of_the_environment(self):
        # Those of Access Management and Gamma are for the machines they run on
        self.meta(requires={"license": ["apim-bridge-gateway", "apim-policy-interrupt", "am-authorizationengine-openfga"]})
        offers = {"products": None, "plugins": True, "license": {"features": ["apim-bridge-gateway"]}}
        self.assertEqual(project.judge(self.built()["playgrounds"]["cloud"]["asks"], offers),
                         ["The license lacks apim-policy-interrupt"])
        offers["license"]["features"].append("apim-policy-interrupt")
        self.assertEqual(project.judge(self.built()["playgrounds"]["cloud"]["asks"], offers), [])
        # Not said (no Cloud Token): nothing is judged
        self.assertEqual(project.judge(self.built()["playgrounds"]["cloud"]["asks"], {"products": None, "plugins": True}), [])

    def test_the_products_the_account_is_entitled_to_are_what_a_playground_can_ask(self):
        asked = {**self.asked(), "products": ["Gamma"], "fills": ["Gamma"]}
        base = {"products": {"API Management": "4.12.21"}, "plugins": True}
        self.assertEqual(project.judge(asked, base), ["Needs Gamma"])
        entitled = {**base, "products": {"API Management": "4.12.21", "Gamma": ""}}
        # A product the account has is no longer missing
        self.assertEqual(project.judge(asked, entitled), [])

    def test_it_deploys_nothing_without_a_seed(self):
        self.seed()   # a seed folder with nothing in it
        self.assertEqual(self.why(), [NOTHING])
        self.seed("apim")
        self.assertEqual(self.why(), [])
        del self.given["raw"][CLOUD]["services"]["cloud-seed"]   # the folder has no service to run it
        self.assertEqual(self.why(), [NOTHING])

    def test_a_playground_read_without_its_seed_folder_deploys_nothing(self):
        del self.given["seeds"]
        built = self.built()
        self.assertEqual(built["playgrounds"]["cloud"]["cannot"], {"cloud": NOTHING})

    def test_it_needs_the_product_that_a_folder_of_its_seed_fills(self):
        # Access Management runs on this machine, whichever platform runs API Management: its folder is no need.
        # Gamma's are: it needs the platform to have it, and its seed is for this machine's API Management
        for names, wanted in ((("apim",), []), (("am", "apim"), []), (("am",), []),
                              (("aim", "apim"), [GAMMA, MORE]), (("apim", "authz"), [GAMMA, MORE]),
                              (("aim", "apim", "authz"), [GAMMA, MORE]),   # one reason for a product
                              (("authz", "am", "apim", "aim"), [GAMMA, MORE])):
            with self.subTest(seed=names):
                self.seed(*names)
                self.assertEqual(self.why(), wanted)

    def test_it_needs_the_product_that_an_image_it_requires_belongs_to(self):
        self.access_management()
        self.meta(requires={"images": {"APIM_VERSION": "4.12.0"}})   # a product the platform runs
        self.assertEqual(self.why(), [])
        self.meta(requires={"images": {"APIM_VERSION": "4.12.0", "AM_VERSION": "4.12"}})   # Access Management runs here
        self.assertEqual(self.why(), [])
        self.seed("aim", "am", "apim")
        self.assertEqual(self.why(), [GAMMA, MORE])
        self.seed("aim", "apim")
        self.assertEqual(self.why(), [GAMMA, MORE])

    def test_it_needs_a_later_version_of_a_product_than_the_platform_runs(self):
        for wanted, reasons in (("4.13.0-alpha.3", [LATER]), ("4.13", [LATER]), ("4.14.2", ["Needs API Management 4.14"]),
                                ("5.0.0", ["Needs API Management 5.0"]), ("4.12", []), ("4.12.5", []), ("4.12.99", []),
                                ("4.11.0", []), ("latest", [])):
            with self.subTest(wanted=wanted):
                self.meta(requires={"images": {"APIM_VERSION": wanted}})
                self.assertEqual(self.why(), reasons)

    def test_every_version_it_requires_of_a_product_is_judged_once_by_what_it_asks(self):
        self.meta(requires={"images": {"APIM_VERSION": "4.13.0", "APIM_GATEWAY_VERSION": "4.13.1"}})
        self.assertEqual(self.why(), [LATER])
        self.meta(requires={"images": {"APIM_VERSION": "4.13.0", "APIM_GATEWAY_VERSION": "4.14.0"}})
        self.assertEqual(self.why(), [LATER, "Needs API Management 4.14"])
        # The image of an add-on, or of nothing, is no product of the platform
        self.meta(requires={"images": {"KAFKA_DESK_VERSION": "9.0.0", "NOTHING_VERSION": "9.0.0",
                                       "MONGODB_VERSION": "9.0.0"}})
        self.assertEqual(self.why(), [])
        self.given["raw"][KAFKA]["x-gravitee"]["name"] = "Gamma"   # named as a product, it is not a group of the core
        self.meta(requires={"images": {"KAFKA_VERSION": "9.0.0"}})
        self.assertEqual(self.why(), [])

    def test_it_asks_for_no_plugin_of_a_platform_whose_gateway_mounts_none(self):
        self.meta(requires={"plugins": {"apim": ["gravitee-policy-x"]}})
        self.assertEqual(self.why(), [PLUGINS])
        for requires in ({"plugins": {"apim": []}}, {"plugins": {}}, {"license": ["apim-reporter-tcp"]}, {}):
            with self.subTest(requires=requires):
                self.meta(requires=requires)
                self.assertEqual(self.why(), [])

    def test_it_changes_nothing_of_the_control_plane_of_this_machine(self):
        # What it sets on the gateway is written for the gateway of the platform
        self.given["raw"][CLOUD]["services"]["apim-gateway"] = {"environment": {"gravitee_groovy_whitelist": "append"}}
        self.assertEqual(self.why(), [])
        del self.given["raw"][CLOUD]["services"]["apim-gateway"]
        # The Management API is the platform's: Gravitee Cloud runs it
        self.given["raw"][CLOUD]["services"]["apim-management-api"] = {"environment": {"gravitee_x": "y"}}
        self.assertEqual(self.why(), [CHANGES])
        del self.given["raw"][CLOUD]["services"]["apim-management-api"]
        self.assertEqual(self.why(), [])

    def test_nor_do_the_add_ons_it_brings(self):
        self.brings(OPENFGA)   # nothing of the core
        self.assertEqual(self.why(), [])
        self.brings(OPENFGA, KAFKA)   # a port and a setting on the gateway: written for the gateway of the platform
        self.assertEqual(self.why(), [])
        self.brings(OPENFGA, CONSOLE)   # a setting on the Management API, which is not here
        self.assertEqual(self.why(), [CHANGES])
        self.brings(EVENTS, OPENFGA)   # through a setup, and the add-on it includes
        self.given["includes"][EVENTS] = [CONSOLE]
        self.given["raw"][CLOUD]["services"].update(self.given["raw"][CONSOLE]["services"])
        self.assertEqual(self.why(), [CHANGES])
        self.given["raw"][CLOUD]["services"].pop("apim-management-api", None)
        self.brings(OPENFGA)
        self.given["includes"][OPENFGA] = [JADE]   # what an add-on includes is brought too: it sets the gateway only
        self.assertEqual(self.why(), [])
        self.given["includes"][OPENFGA] = [CONSOLE]
        self.assertEqual(self.why(), [CHANGES])

    def test_an_app_waits_for_no_service_of_the_core(self):
        # An add-on's service starts with the deploy; the seed's own dependencies are the core's place, not this one
        self.brings(OPENFGA)
        self.app(depends_on={"openfga": {"condition": "service_healthy", "required": True}})
        self.assertEqual(self.why(), [])
        # What of the platform runs here with the deploy (the databases, Access Management) is no reason; the control plane
        # of API Management is Gravitee Cloud's
        self.app(depends_on={name: {"condition": "service_healthy", "required": True} for name in ("openfga", "redis")})
        self.assertEqual(self.why(), [])
        self.app(depends_on={name: {"condition": "service_healthy", "required": True}
                             for name in ("openfga", "apim-management-api")})
        self.assertEqual(self.why(), [USES])
        # The gateway is not one of them: what the app says of it is written for the gateway of the platform
        self.app(depends_on={"apim-gateway": {"condition": "service_healthy", "required": True}})
        self.assertEqual(self.why(), [])

    def test_nor_does_a_service_of_an_add_on_it_brings(self):
        self.brings(OPENFGA)
        self.needs(OPENFGA, "openfga-migrate", "openfga")   # for another of the add-on: it starts with the deploy
        self.assertEqual(self.why(), [])
        self.needs(OPENFGA, "openfga-migrate", "redis", "openfga")   # the platform's database runs here too
        self.assertEqual(self.why(), [])
        self.needs(OPENFGA, "openfga-migrate", "apim-management-api", "openfga")
        self.assertEqual(self.why(), [USES])
        self.needs(OPENFGA, "openfga-migrate", "openfga")
        self.needs(OPENFGA, "openfga", "apim-management-api")   # each service of the add-on is read, not the first
        self.assertEqual(self.why(), [USES])

    def test_nor_one_of_a_setup_s_add_ons(self):
        self.given["includes"][EVENTS] = [OPENFGA]
        self.brings(EVENTS)
        self.assertEqual(self.why(), [])
        self.needs(OPENFGA, "openfga", "redis")
        self.assertEqual(self.why(), [])
        self.needs(OPENFGA, "openfga", "apim-management-api")
        self.assertEqual(self.why(), [USES])

    def test_an_app_calls_no_gateway_nor_service_of_the_core(self):
        self.brings(OPENFGA)
        # A backend of an add-on, this machine, the internet, and a name in a sentence are fine
        self.app(environment={"PORT": "8000", "FGA_URL": "http://openfga:8080", "NOTE": "the apim-gateway is not called",
                              "OLLAMA_URL": "${OLLAMA_URL:-http://host.docker.internal:11434}"},
                 command=["serve", "--upstream", "https://api.example.com"])
        self.assertEqual(self.why(), [])
        # A gateway is a gateway, whichever platform it is of: the name is written for the platform's
        for environment, command in (
                ({"LLM_URL": "http://apim-gateway:8082/acme/llm"}, ""),
                ({"MCP_URL": "${MCP_URL:-http://apim-gateway:8082/mcp}"}, ""),
                ({"GATEWAY_URL": "http://apim-hybrid-gateway:8082"}, ""),
                ({}, ["serve", "--llm=http://apim-gateway:8082/llm"])):
            with self.subTest(environment=environment, command=command):
                self.app(environment=environment, command=command)
                self.assertEqual(self.why(), [])
        # The rest of the platform runs here with the deploy
        for environment, command in (
                ({"DB_URL": "mongodb://mongodb:27017/db"}, ""),
                ({"PORT": 8000}, "serve --db http://mongodb:27017 --llm http://apim-gateway:8082/llm")):
            with self.subTest(environment=environment, command=command):
                self.app(environment=environment, command=command)
                self.assertEqual(self.why(), [])
        # The Management API of API Management is what Gravitee Cloud runs
        for environment, command in (
                ({"API_URL": "http://apim-management-api:8083/management"}, ""),
                ({"PORT": 8000}, "serve --api http://apim-management-api:8083")):
            with self.subTest(environment=environment, command=command):
                self.app(environment=environment, command=command)
                self.assertEqual(self.why(), [USES])

    def test_a_user_and_a_password_in_a_url_do_not_hide_the_host_it_calls(self):
        # A password is a setting, so a URL that has one is written ${NAME:-default}@host
        for url in ("http://${API_USER:-app}:${API_PASSWORD:-demo}@apim-management-api:8083/management",
                    "https://app:demo@apim-management-api/management"):
            with self.subTest(url=url):
                self.app(environment={"URL": url})
                self.assertEqual(self.why(), [USES])
        self.app(environment={"URL": "http://app:demo@apim-gateway:8082/llm"})   # the gateway is the platform's own
        self.assertEqual(self.why(), [])
        # The host is the one after the user, not a name after the path
        self.app(environment={"API_URL": "https://${API_USER:-app}:${API_PASSWORD:-demo}@api.example.com/v1",
                              "LINK": "https://example.com/mail@redis", "HOOK": "https://hooks.example.com#redis@x"})
        self.assertEqual(self.why(), [])

    def test_the_seed_is_not_an_app(self):
        # It runs in the core's place when it deploys to this machine: it calls the Management API and the gateway
        self.given["raw"][CLOUD]["services"]["cloud-seed"]["environment"] = {
            "APIM_MANAGEMENT_URL": "http://apim-management-api:8083", "GATEWAY_INTERNAL_URL": "http://apim-gateway:8082"}
        self.assertEqual(self.why(), [])

    def test_it_seeds_nothing_but_what_the_platform_and_this_machine_run(self):
        # API Management goes to the platform, Access Management and OpenFGA are seeded here
        for names in (("apim", "openfga"), ("apim", "am", "openfga")):
            with self.subTest(seed=names):
                self.seed(*names)
                self.assertEqual(self.why(), [])
        for names in (("apim", "controls.yaml"), ("apim", "refills.yaml"), ("apim", "notes.txt")):
            with self.subTest(seed=names):
                self.seed(*names)
                self.assertEqual(self.why(), [MORE])
        self.seed("am", "apim", "refills.yaml")
        self.assertEqual(self.why(), [MORE])

    def test_a_playground_that_seeds_environments_of_its_own_cannot_be_deployed(self):
        # No management API creates an environment, and a platform's own are not the lab's to multiply: the playground
        # makes them in the core's database, which only this machine has
        for names in (("apim", "environments"), ("environments",), ("am", "apim", "environments", "openfga")):
            with self.subTest(seed=names):
                self.seed(*names)
                self.assertEqual(self.why(), [MORE])
        playground = self.built()["playgrounds"]["cloud"]
        self.assertEqual((playground["deploys_to"], playground["cannot"], playground["asks"]["more"]),
                         ([], {"cloud": MORE}, True))

    def test_the_most_basic_reason_comes_first_and_the_others_follow_in_order(self):
        # What a person fixes first: nothing to deploy, then the products and their versions that the platform lacks,
        # what it mounts, what it changes of this machine's core, what its apps use of it, and last more seeding
        self.seed("authz", "controls.yaml", "am", "apim")
        self.meta(requires={"images": {"APIM_VERSION": "4.13.0"}, "plugins": {"apim": ["gravitee-policy-x"]}})
        self.given["raw"][CLOUD]["services"]["apim-management-api"] = {"environment": {"gravitee_groovy_whitelist": "append"}}
        self.app(depends_on={"apim-management-api": {"condition": "service_healthy", "required": True}})
        every = [GAMMA, LATER, PLUGINS, CHANGES, USES, MORE]
        self.assertEqual(self.why(), every)
        # The kind of platform is judged on what does not depend on an environment: the products come with each one
        playground = self.built()["playgrounds"]["cloud"]
        self.assertEqual((playground["deploys_to"], playground["cannot"]), ([], {"cloud": PLUGINS}))
        self.seed("apim")   # one is fixed, the next comes first
        self.assertEqual(self.why()[0], LATER)
        self.meta(requires={})
        self.assertEqual((self.why()[0], self.built()["playgrounds"]["cloud"]["cannot"]), (CHANGES, {"cloud": CHANGES}))
        self.seed()
        self.assertEqual((self.why()[0], self.built()["playgrounds"]["cloud"]["cannot"]), (NOTHING, {"cloud": NOTHING}))

    def test_each_environment_is_judged_by_what_its_gateways_run(self):
        self.access_management()
        self.seed("am", "apim")
        self.meta(requires={"images": {"APIM_VERSION": "4.13.0", "AM_VERSION": "4.13"}})
        built = self.built()
        self.assertEqual((built["playgrounds"]["cloud"]["deploys_to"], built["playgrounds"]["cloud"]["cannot"]),
                         (["cloud"], {}), "nothing that an environment does not decide keeps it from the kind")
        # Access Management runs here: its image and its folder ask nothing of the platform
        self.assertEqual(self.why(), [LATER])
        later = platforms.capabilities(built, check(("APIM", "4.13.2"), ("AM", "4.13.0")), "env")
        self.assertEqual(later["products"], {"API Management": "4.13.2", "Access Management": "4.13.0"})
        self.assertEqual(self.why(later), [])
        self.seed("apim")
        self.assertEqual(self.why(later), [])
        self.meta(requires={"images": {"APIM_VERSION": "4.13.0"}})
        several = platforms.capabilities(built, check(("APIM", "4.13.2"), ("APIM", "4.12.21")), "env")
        self.assertEqual((several["products"], several["detail"]), (
            {"API Management": "4.12.21"}, "API Management: the gateways run 4.12.21, 4.13.2, the oldest counts"))
        self.assertEqual(self.why(several), [LATER], "the oldest gateway of the environment is the version it runs")
        self.assertEqual(self.why(platforms.capabilities(built, check(("AM", "4.13.0")), "env")),
                         ["Needs API Management"], "an environment without API Management")
        self.assertEqual(self.why(platforms.capabilities(built, check(("APIM", "")), "env")), [],
                         "a version the gateway does not say is not compared")

    def test_an_environment_without_a_gateway_has_nothing_to_compare(self):
        built = self.built()
        self.assertIsNone(platforms.capabilities(built, check(), "env"))
        self.access_management()
        self.seed("am", "apim")
        self.meta(requires={"images": {"APIM_VERSION": "4.13.0"}, "plugins": {"apim": ["gravitee-policy-x"]}})
        self.assertEqual(self.why({"products": None, "plugins": False}), [PLUGINS], "what does not depend on it only")
        self.assertEqual(self.why({"products": None, "plugins": True}), [])

    def test_a_plugin_needs_a_gateway_that_mounts_a_plugins_folder(self):
        # The hybrid gateway mounts what its add-on's file mounts: here nothing, then the plugins of API Management
        self.meta(requires={"plugins": {"apim": ["gravitee-policy-x"]}})
        self.assertEqual(self.why(), [PLUGINS])
        self.given["raw"][HYBRID]["services"]["apim-hybrid-gateway"]["volumes"] = [
            bind("plugins/apim", "/opt/graviteeio-gateway/plugins-ext")]
        built = self.built()
        self.assertTrue(platforms.plugins(built, "cloud"))
        self.assertEqual((built["playgrounds"]["cloud"]["deploys_to"], self.why()), (["cloud"], []))

    def test_a_playground_that_does_not_stand_alone_cannot_go(self):
        welcome = self.built()["playgrounds"]["welcome"]
        self.assertEqual((welcome["deploys_to"], welcome["cannot"]), ([], {"cloud": NOTHING}))

    def test_an_add_on_that_does_not_load_is_left_out(self):
        self.given["includes"][CLOUD] = ["addons/broken/compose.yml"]   # its file is not among those that load
        self.assertEqual(self.why(), [])


def read(case, catalogs=None, local=(), fixture=None, more=None):
    """The fixture catalogs in a project folder of `case`, read as the Lab UI reads a project: (what gather() read, the
    project). `fixture`: how the default catalog is installed (an install() patch), `catalogs`: {namespace: its install()
    patch} for the ones beside it, `local`: units of the personal area, `more(case)`: what else the project folder holds.
    Compose renders the files, no daemon; a skip that shows without Compose 5."""
    fixtures.need_compose()
    fixtures.project(case, paths, project, compose, envfiles, **(fixture or {}))
    for ns, how in (catalogs or {}).items():
        fixtures.install(case, ns, **how)
    for name in local:
        fixtures.local(case, name)
    case.patch(license, "status_of", lambda variable: None)   # the files of the project folder, not the machine's license
    if more:
        more(case)
    inputs = project.gather()
    return inputs, project.build(inputs)


class Fixture(Case):
    """The fixture catalogs read once for the class, in a project folder of its own (read): `inputs`, `built`. What a class
    changes is its CATALOGS, LOCAL and FIXTURE."""

    CATALOGS = {"partner": {}, "lab-new": {}, "lab-old": {}}
    LOCAL = ("scratch", "sandbox")
    FIXTURE = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures.need_compose()
        holder = cls.holder = Case()   # a case of the class's own: the folder it makes and what it patches last as long
        cls.addClassCleanup(holder.doCleanups)
        cls.inputs, cls.built = read(holder, cls.CATALOGS, cls.LOCAL, cls.FIXTURE)
        cls.root = holder.root


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Real(Fixture):
    """The playgrounds of the fixture catalog, read as the Lab UI reads them: what each asks of a platform, and what can go."""

    def reasons(self, uid, version="4.12.21"):
        """Against an environment whose gateway runs API Management at this version."""
        offers = platforms.capabilities(self.built, check(("APIM", version)), "env")
        self.assertEqual(offers["products"], {"API Management": version}, "the group of the core names the product")
        return project.reasons(self.built["playgrounds"][uid], self.built, self.inputs["raw"],
                               self.inputs["seeds"][uid], offers)

    def test_shop_needs_more_than_a_platform_offers_and_the_plugins_of_its_api_are_the_first_reason(self):
        # Its seed fills API Management and Access Management, which runs here with the deploy, so the platform is asked
        # for API Management 4.12 and for the plugin its API uses, on a gateway that mounts plugins
        shop = self.built["playgrounds"]["shop"]
        self.assertEqual(shop["deploys_to"], [])
        self.assertEqual(self.reasons("shop"), [PLUGINS])
        self.assertEqual(self.reasons("shop", "4.11.2"), ["Needs API Management 4.12", PLUGINS])
        self.assertEqual(shop["cannot"]["cloud"], PLUGINS)

    def test_tour_api_can_go(self):
        tour = self.built["playgrounds"]["tour"]
        self.assertEqual((tour["deploys_to"], tour["cannot"], self.reasons("tour")), (["cloud"], {}, []))

    def test_the_seed_folders_are_read_by_name(self):
        self.assertEqual(self.inputs["seeds"]["tour"], ["apim"])
        self.assertEqual(self.inputs["seeds"]["shop"], ["am", "apim"])
        self.assertEqual(self.inputs["seeds"]["local/sandbox"], ["apim"])

    def test_what_the_apis_of_a_seed_use_is_read_in_its_folder(self):
        self.assertEqual(self.inputs["uses"]["shop"]["policy"], ["fixture-policy"])
        self.assertEqual(self.inputs["uses"]["tour"]["entrypoint"], ["http-proxy"])


def unit_of(built, key):
    """A unit of the project by the key the oracle uses: "addon:@partner/ledger"."""
    kind, _, uid = key.partition(":")
    return built[kind + "s"][uid]


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Catalogs(Fixture):
    """The units of the catalogs: how they are keyed, where they come from, what the policy says of each, and what a blocked
    unit brings."""

    maxDiff = None

    def test_the_units_are_keyed_bare_for_the_first_catalog_local_for_this_machine_and_by_namespace_for_the_others(self):
        built = self.built
        self.assertEqual(sorted(built["addons"]), [
            "@lab-new/newer", "@lab-old/older", "@partner/ledger", "cluster", "echo", "echo-core", "echo-twin", "future",
            "gateway", "local/scratch", "port-core"])
        self.assertEqual(sorted(built["setups"]), ["pair"])
        self.assertEqual(sorted(built["playgrounds"]), ["local/sandbox", "shop", "tour"])
        self.assertEqual([uid for kind in ("addons", "setups", "playgrounds") for uid, unit in built[kind].items()
                          if unit["personal"]], ["local/scratch", "local/sandbox"])

    def test_a_unit_says_where_it_comes_from(self):
        built = self.built
        echo, ledger, scratch = built["addons"]["echo"], built["addons"]["@partner/ledger"], built["addons"]["local/scratch"]
        # This machine's units say no origin (they are Personal). Those of the first catalog say theirs once another catalog is
        # added: until then there is nothing to tell apart
        self.assertEqual((echo["catalog"], echo["readonly"]), ("fixture", False))
        self.assertEqual(echo["origin"]["id"], "fixture")
        self.assertEqual((scratch["catalog"], scratch["origin"], scratch["readonly"]), ("local", None, False))
        self.assertEqual((ledger["catalog"], ledger["origin"], ledger["readonly"]),
                         ("partner", {"id": "partner", "name": "Partner fixture", "version": "2.0.0"}, False))
        # The path a person reads is relative to the catalog it is in, this machine's own included: never the place of the local
        # catalog on the disk
        self.assertEqual((echo["file"], echo["display_file"]),
                         (".lab/catalogs/fixture/addons/echo/compose.yml", "addons/echo/compose.yml"))
        self.assertEqual(built["setups"]["pair"]["display_file"], "setups/pair.yml")
        self.assertEqual(built["playgrounds"]["shop"]["display_file"], "playgrounds/shop/compose.yml")
        self.assertEqual((scratch["file"], scratch["display_file"]),
                         (".lab/catalogs/local/addons/scratch/compose.yml", "addons/scratch/compose.yml"))
        self.assertEqual((built["playgrounds"]["local/sandbox"]["file"], built["playgrounds"]["local/sandbox"]["display_file"]),
                         (".lab/catalogs/local/playgrounds/sandbox/compose.yml", "playgrounds/sandbox/compose.yml"))
        self.assertEqual(ledger["env_file"], ".lab/catalogs/partner/addons/ledger/.env")
        for kind in ("addons", "setups", "playgrounds"):
            for uid, unit in built[kind].items():
                with self.subTest(unit=uid):
                    self.assertFalse(unit["display_file"].startswith((".lab", "/")), "no place of this machine in what a person reads")
                    self.assertTrue(unit["file"].endswith(unit["display_file"]))

    def test_what_the_judgement_sees_of_the_includes_is_what_compose_names_of_a_block_list(self):
        """The units of the fixture catalogs write their includes as a block list, which both readers name alike: the screen's
        reading (trust.include_targets) and Compose's walk (compose.includes). Only a list the walk cannot name differs."""
        files = [file for file in self.inputs["raw"] if file.startswith(".lab/catalogs/")]
        self.assertGreater(len(files), 10)
        for file in files:
            with self.subTest(file=file):
                self.assertEqual(self.inputs["includes"][file], compose.includes(file))
        self.assertEqual(self.inputs["includes"][".lab/catalogs/fixture/setups/pair.yml"],
                         [".lab/catalogs/fixture/addons/echo/compose.yml", ".lab/catalogs/partner/addons/ledger/compose.yml"])

    def test_every_unit_has_the_fields_of_a_row(self):
        for kind in ("addons", "setups", "playgrounds"):
            for uid, unit in self.built[kind].items():
                with self.subTest(unit=uid):
                    self.assertLessEqual({"origin", "readonly", "display_file", "blocked", "needs_catalogs", "compat", "twin",
                                          "uses_machine", "catalog", "verdict"}, set(unit))
                    self.assertIn(unit["compat"], ("ok", "new", "old"))
                    self.assertIsInstance(unit["needs_catalogs"], list)

    def test_the_verdict_of_each_unit_is_the_one_of_the_oracle(self):
        """expected.json is what the policy must say of every unit of fixture and partner: the verdict, its reasons and notes
        and what the unit will do, which for a unit that brings others is the sum of theirs."""
        for key, wanted in fixtures.expected().items():
            with self.subTest(unit=key):
                unit = unit_of(self.built, key)
                found = unit["verdict"]
                self.assertEqual((found["state"], found["reasons"], found["notes"]),
                                 (wanted["verdict"], wanted["reasons"], wanted["notes"]))
                self.assertEqual(found["surface"], trust.sum_surfaces([fixtures.expected()[one]["surface"]
                                                                       for one in wanted["closure"]]))
                self.assertEqual(unit["blocked"], {"reasons": wanted["reasons"]} if wanted["reasons"] else None)

    def test_a_unit_that_uses_this_machine_says_what_and_why(self):
        cluster = self.built["addons"]["cluster"]
        self.assertEqual(cluster["uses_machine"], [{
            "service": "fixture-cluster", "what": ["runs privileged"],
            "why": "Runs a service manager inside its container, which only a privileged container can do"}])
        self.assertIsNone(cluster["blocked"], "the catalog is listed and the unit says why")
        self.assertEqual(self.built["addons"]["echo"]["uses_machine"], [])

    def test_a_unit_brings_the_machine_use_of_what_it_includes(self):
        # Scratch of this machine includes Echo; a unit that includes the cluster would say it too: here, the sum is the unit's
        self.assertEqual(self.built["addons"]["echo-core"]["uses_machine"], [])
        self.assertEqual(self.built["setups"]["pair"]["uses_machine"], [])

    def test_the_level_a_unit_is_written_for_is_read_against_the_core(self):
        built = self.built
        self.assertEqual({uid: built["addons"][uid]["compat"] for uid in ("echo", "future", "@lab-new/newer", "@lab-old/older")},
                         {"echo": "ok", "future": "new", "@lab-new/newer": "new", "@lab-old/older": "old"})
        self.assertEqual(built["addons"]["future"]["blocked"], {"reasons": ["Needs a newer Gravitee Lab"]})
        self.assertEqual(built["addons"]["@lab-old/older"]["blocked"], {"reasons": ["Written for an older Gravitee Lab"]})

    def test_a_blocked_unit_is_listed_and_brings_no_service(self):
        built = self.built
        for kind, uid in (("addons", "future"), ("addons", "@lab-new/newer"), ("addons", "@lab-old/older"), ("setups", "pair")):
            self.assertTrue(built[kind][uid]["blocked"], uid)
        self.assertEqual([built["addons"][uid]["services"] for uid in ("future", "@lab-new/newer", "@lab-old/older")], [[], [], []])
        owned = {service["owner"]["id"] for service in built["services"].values()}
        self.assertFalse(owned & {"future", "@lab-new/newer", "@lab-old/older"})
        self.assertNotIn("fixture-future", built["services"])
        # What can start brings its services
        self.assertEqual(built["services"]["fixture-echo"]["owner"], {"kind": "addon", "id": "echo", "name": "Echo"})
        self.assertEqual(built["addons"]["echo"]["services"], ["fixture-echo"])
        self.assertEqual(built["services"]["partner-ledger"]["owner"]["id"], "@partner/ledger")

    def test_what_a_unit_defines_itself_is_what_its_own_configuration_gave(self):
        service = self.built["services"]["fixture-echo"]
        self.assertEqual((service["image"], service["ports"], service["container"], service["label"]),
                         ("busybox:1.36", ["8191"], "gio-fixture-echo", "Echo"))
        self.assertEqual([link["url"] for link in self.built["links"] if link["service"] == "fixture-echo"],
                         ["http://localhost:8191"])

    def test_the_catalogs_know_what_their_units_found_out(self):
        summary = self.built["catalogs"]
        self.assertEqual(list(summary), ["fixture", "lab-new", "lab-old", "local", "partner"])
        fixture = summary["fixture"]
        self.assertEqual((fixture["name"], fixture["kind"], fixture["counts"]),
                         ("Fixture catalog", "folder", {"playground": 2, "setup": 1, "addon": 7}))
        self.assertEqual([(one["id"], one["reasons"]) for one in fixture["blocked"]], [
            ("future", ["Needs a newer Gravitee Lab"]), ("pair", ["Depends on private content"])])
        self.assertEqual([(use["id"], use["service"], use["what"]) for use in fixture["uses_machine"]],
                         [("cluster", "fixture-cluster", ["runs privileged"])])
        self.assertEqual(summary["local"]["name"], "Personal")
        self.assertEqual(summary["partner"]["blocked"], [])
        self.assertEqual(summary["lab-new"]["blocked"][0]["id"], "@lab-new/newer")

    def test_the_gateway_an_add_on_declares_is_the_one_the_project_chooses(self):
        built = self.built
        self.assertEqual(built["addons"]["gateway"]["gateway"]["service"], "fixture-gateway")
        self.assertEqual(built["platform"], {"gateways": {"cloud": "gateway"}})
        self.assertEqual(built["addons"]["gateway"]["gateway"]["domains"], ["localhost:8195", "fixture-gateway:80"])

    def test_nothing_a_catalog_says_reaches_the_banner(self):
        self.assertEqual(self.built["problems"], [])
        self.assertEqual(self.built["catalog_problems"], {})

    def test_what_gather_read_for_the_policy_is_given_with_the_inputs(self):
        inputs = self.inputs
        self.assertEqual(inputs["default"], "fixture")
        self.assertEqual(inputs["contract"], {"contract": 3, "contract_min": 1, "seeder_image": fixtures.seeder_image(self.holder)})
        self.assertEqual(inputs["layout"], {"phase": "ready", "notice": None})
        self.assertEqual(list(inputs["catalogs"]), ["fixture", "local", "partner", "lab-new", "lab-old"])
        self.assertEqual(inputs["screened"], {})
        self.assertIsNotNone(inputs["core_config"])
        self.assertIn("apim-gateway", inputs["core_config"]["services"])
        self.assertTrue(set(inputs["configs"]) >= {".lab/catalogs/fixture/addons/echo/compose.yml"})


class Accounts(Legacy):
    """The accounts of a playground show the settings in them as they are now: the Lab UI's environment first, then the
    root .env, then the playground's own, else the default the value gives. Compose reads them so: the root .env wins
    over the .env of an included file, which only applies where the root sets nothing."""

    OWN, CORE_ENV, OTHER = "playgrounds/welcome/.env", ".env", "addons/kafka/.env"

    def password(self, text="${WELCOME_PASSWORD:-doe}", environ=None, **files):
        """The password of the playground's account, written `text`, with these variables in the environment and in
        these .env files."""
        given = inputs()
        given["raw"][WELCOME]["x-gravitee"]["accounts"][0]["password"] = text
        given["environ"], given["env"] = environ or {}, files
        built = project.build(given)
        self.assertEqual(given["raw"][WELCOME]["x-gravitee"]["accounts"][0]["password"], text)   # as it was
        return built["playgrounds"]["welcome"]["accounts"][0]["password"]

    def test_the_default_when_nothing_sets_it(self):
        self.assertEqual(self.password(), "doe")
        self.assertEqual(self.password(**{self.OWN: {"OTHER": "x"}, self.CORE_ENV: {}}), "doe")

    def test_set_in_the_playground_s_env(self):
        self.assertEqual(self.password(**{self.OWN: {"WELCOME_PASSWORD": "mine"}}), "mine")

    def test_set_in_the_core_s_env(self):
        self.assertEqual(self.password(**{self.CORE_ENV: {"WELCOME_PASSWORD": "core"}}), "core")

    def test_set_in_the_environment(self):
        self.assertEqual(self.password(environ={"WELCOME_PASSWORD": "shell"}), "shell")

    def test_the_environment_comes_first_then_the_root_env_then_the_playground(self):
        files = {self.OWN: {"WELCOME_PASSWORD": "own"}, self.CORE_ENV: {"WELCOME_PASSWORD": "core"}}
        self.assertEqual(self.password(environ={"WELCOME_PASSWORD": "shell"}, **files), "shell")
        self.assertEqual(self.password(**files), "core")
        del files[self.CORE_ENV]
        self.assertEqual(self.password(**files), "own")

    def test_the_env_of_another_item_is_not_read(self):
        self.assertEqual(self.password(**{self.OTHER: {"WELCOME_PASSWORD": "kafka"},
                                          "playgrounds/other/.env": {"WELCOME_PASSWORD": "other"}}), "doe")

    def test_an_empty_value_is_no_value_for_a_default_with_a_colon(self):
        self.assertEqual(self.password(**{self.OWN: {"WELCOME_PASSWORD": ""}}), "doe")
        self.assertEqual(self.password(environ={"WELCOME_PASSWORD": ""}), "doe")
        # What Compose reads: the first place that sets it decides, also when it sets it to nothing
        self.assertEqual(self.password(**{self.CORE_ENV: {"WELCOME_PASSWORD": ""}, self.OWN: {"WELCOME_PASSWORD": "own"}}),
                         "doe")
        self.assertEqual(self.password(**{self.OWN: {"WELCOME_PASSWORD": ""}, self.CORE_ENV: {"WELCOME_PASSWORD": "core"}}),
                         "core")
        self.assertEqual(self.password("${WELCOME_PASSWORD-doe}", **{self.OWN: {"WELCOME_PASSWORD": ""}}), "")
        self.assertEqual(self.password("${WELCOME_PASSWORD-doe}"), "doe")

    def test_a_setting_without_default_shows_its_name_until_it_has_a_value(self):
        self.assertEqual(self.password("${WELCOME_PASSWORD}"), "${WELCOME_PASSWORD}")
        self.assertEqual(self.password("${WELCOME_PASSWORD}", **{self.OWN: {"WELCOME_PASSWORD": "mine"}}), "mine")

    def test_a_default_that_refers_to_another_setting(self):
        text = "${WELCOME_PASSWORD:-${WELCOME_ADMIN_PASSWORD:-last}}"
        self.assertEqual(self.password(text), "last")
        self.assertEqual(self.password(text, **{self.CORE_ENV: {"WELCOME_ADMIN_PASSWORD": "admin"}}), "admin")

    def test_every_text_of_every_account_is_read_and_nothing_else(self):
        given = inputs()
        given["raw"][WELCOME]["x-gravitee"]["accounts"] = [
            {"for": "Website of ${WELCOME_NAME:-ACME}", "username": "${WELCOME_USER:-john}", "port": 8002,
             "admin": True, "tags": ["${WELCOME_USER:-john}"]},
            {"for": "LLM", "key": "${WELCOME_KEY:-key-1}"}, "text", None]
        given["env"] = {self.OWN: {"WELCOME_USER": "jane", "WELCOME_KEY": "key-2"}}
        self.assertEqual(project.build(given)["playgrounds"]["welcome"]["accounts"], [
            {"for": "Website of ACME", "username": "jane", "port": 8002, "admin": True, "tags": ["${WELCOME_USER:-john}"]},
            {"for": "LLM", "key": "key-2"}, "text", None])

    def test_accounts_that_are_not_a_list_are_left_as_they_are(self):
        given = inputs()
        given["raw"][WELCOME]["x-gravitee"]["accounts"] = "${WELCOME_PASSWORD:-doe}"
        self.assertEqual(project.build(given)["playgrounds"]["welcome"]["accounts"], "${WELCOME_PASSWORD:-doe}")
        del given["raw"][WELCOME]["x-gravitee"]["accounts"]
        self.assertEqual(project.build(given)["playgrounds"]["welcome"]["accounts"], [])


class Gathered(Legacy):
    """The variables the accounts are read with come from the disk and the process, and a change shows."""

    def test_the_env_files_of_the_core_and_of_each_add_on_and_playground_are_read(self):
        self.folder(paths, project, envfiles, files={
            CORE: "", KAFKA: "", WELCOME: "", EVENTS: "", ".env": "A=1\n", "addons/kafka/.env": "B='two words'\n",
            "playgrounds/welcome/.env": "WELCOME_PASSWORD=\"mine\"\n# comment\nEMPTY=\n"})
        self.assertEqual(project._env_values(), {
            ".env": {"A": "1"}, "addons/kafka/.env": {"B": "two words"},
            "playgrounds/welcome/.env": {"WELCOME_PASSWORD": "mine", "EMPTY": ""}})

    def test_an_account_shows_a_change_of_an_env_file_the_next_time_the_project_is_loaded(self):
        self.folder(paths, project, envfiles, files=dict.fromkeys((CORE, WELCOME), ""))
        self.patch(project, "_cache", {"key": None, "project": None, "inputs": None})
        self.patch(project, "gather", lambda: {**inputs(), "environ": {}, "env": project._env_values()})

        def shown():
            return project.load()["playgrounds"]["welcome"]["accounts"][0]["password"]
        self.assertEqual(shown(), "doe")
        self.write("playgrounds/welcome/.env", "WELCOME_PASSWORD=first\n")
        self.assertEqual(shown(), "first")
        self.write("playgrounds/welcome/.env", "WELCOME_PASSWORD=second\n")
        self.assertEqual(shown(), "second")
        self.write(".env", "WELCOME_PASSWORD=core\n")
        self.assertEqual(shown(), "core")   # the root one comes first
        (self.root / ".env").unlink()
        self.assertEqual(shown(), "second")

    def test_the_seed_folder_of_each_playground_is_read_by_name(self):
        self.folder(paths, project, files={
            "playgrounds/welcome/seed/apim/apis/one.json": "{}", "playgrounds/welcome/seed/am/domain.yaml": "",
            "playgrounds/welcome/seed/controls.yaml": "", "playgrounds/welcome/seed/.DS_Store": "",
            "playgrounds/local/sandbox/seed/aim/agents/one.yaml": "", "playgrounds/empty/seed/.gitkeep": "",
            "playgrounds/none/compose.yml": "", "playgrounds/file/seed": "not a folder"})
        self.assertEqual([project._seed_names(uid) for uid in ("welcome", "local/sandbox", "empty", "none", "file")],
                         [["am", "apim", "controls.yaml"], ["aim"], [], [], []])

    def test_what_the_apis_of_a_seed_use_is_read_in_the_platforms_folder_and_in_each_environment(self):
        def api(entrypoint, endpoint=None):
            groups = [{"type": endpoint, "endpoints": [{"type": endpoint}]}] if endpoint else []
            return json.dumps({"api": {"listeners": [{"entrypoints": [{"type": entrypoint}]}], "endpointGroups": groups}})
        self.folder(paths, project, files={
            "playgrounds/welcome/seed/apim/apis/one.json": api("http-proxy", "http-proxy"),
            "playgrounds/welcome/seed/environments/novo-nordisk/apis/feed.json": api("sse", "solace"),
            "playgrounds/welcome/seed/environments/beam/apis/feed.json": api("webhook", "solace"),
            "playgrounds/welcome/seed/environments/beam/apis/broken.json": "not json",
            "playgrounds/welcome/seed/environments/beam/apis/list.json": "[]",
            "playgrounds/local/sandbox/seed/environments/moderna/apis/feed.json": api("websocket"),
            "playgrounds/none/compose.yml": ""})
        self.assertEqual(project._seed_uses("welcome"), {
            "entrypoint": ["http-proxy", "sse", "webhook"], "endpoint": ["http-proxy", "solace"], "policy": [],
            "resource": []})
        self.assertEqual(project._seed_uses("local/sandbox")["entrypoint"], ["websocket"])
        self.assertEqual(project._seed_uses("none"), {kind: [] for kind in project.PLUGIN_KINDS})

    def test_the_seed_folders_are_gathered_with_the_playgrounds(self):
        self.folder(paths, project, envfiles, files={
            CORE: "", WELCOME: "", "playgrounds/welcome/seed/apim/apis/one.json": "{}", "playgrounds/welcome/seed/am/x": "",
            SANDBOX: "", "addons/kafka/seed/apim/x": ""})
        self.patch(project, "_resolved", return_value={})
        self.patch(project, "_raw", lambda path: ({"services": {}}, None))
        self.patch(compose, "includes", return_value=[])
        self.patch(compose, "variables", return_value=[])
        self.assertEqual(project.gather()["seeds"], {"welcome": ["am", "apim"], "local/sandbox": []})

    def test_the_environment_of_the_process_is_gathered(self):
        self.folder(paths, project, envfiles, files={CORE: ""})
        self.patch(project, "_resolved", return_value={})
        self.patch(project, "_raw", lambda path: ({"services": {}}, None))
        self.patch(compose, "includes", return_value=[])
        self.patch(compose, "variables", return_value=[])
        with mock.patch.dict("os.environ", {"WELCOME_PASSWORD": "shell"}):
            self.assertEqual(project.gather()["environ"]["WELCOME_PASSWORD"], "shell")


class License(Legacy):
    """Whether the license of the core's .env lets the services that need one start, gathered with the other inputs and
    seen again by the next project once it is saved."""

    CORE_LICENSE = {"services": {"gateway": {"environment": {"gravitee_license_key": "${GRAVITEE_LICENSE:-}"}}}}

    def setUp(self):
        self.folder(paths, project, envfiles, files={CORE: ""})
        self.patch(project, "_cache", {"key": None, "project": None, "inputs": None})
        self.patch(project, "_resolved", return_value={})
        self.patch(project, "_raw", lambda path: (self.CORE_LICENSE, None))
        self.patch(compose, "includes", return_value=[])
        self.patch(compose, "variables", return_value=[])
        # A license is genuine when its text says so
        self.patch(license, "decode", lambda text: ({"tier": "universe"}, text == "genuine"))

    def usable(self):
        return project.load()["license_usable"]

    def test_gathered_from_the_env_of_the_core(self):
        self.assertIs(project.gather()["license_usable"], False)   # no .env
        self.write(".env", "GRAVITEE_LICENSE=genuine\n")
        self.assertIs(project.gather()["license_usable"], True)
        self.write(".env", "GRAVITEE_LICENSE=forged\n")   # not signed by GraviteeSource
        self.assertIs(project.gather()["license_usable"], False)
        self.write(".env", "GRAVITEE_LICENSE=\n")
        self.assertIs(project.gather()["license_usable"], False)

    def test_an_expired_license_is_not_usable_and_one_that_expires_soon_is(self):
        now = license.datetime.datetime.now(license.datetime.timezone.utc)
        self.write(".env", "GRAVITEE_LICENSE=genuine\n")
        for days, usable in ((-1, False), (5, True), (400, True)):
            with self.subTest(days=days):
                expiry = now + license.datetime.timedelta(days=days, hours=1)
                self.patch(license, "decode", lambda text, expiry=expiry: ({"expiryDate": expiry}, True))
                self.assertIs(project.gather()["license_usable"], usable)

    def test_nothing_reads_a_license_when_no_core_service_does(self):
        self.patch(project, "_raw", lambda path: ({"services": {"gateway": {}}}, None))
        self.write(".env", "GRAVITEE_LICENSE=genuine\n")
        self.assertIs(project.gather()["license_usable"], False)

    def test_a_license_saved_shows_in_the_next_project(self):
        # The .env is part of what the project is read from: saving the license reads it again
        self.assertIs(self.usable(), False)
        self.write(".env", "GRAVITEE_LICENSE=genuine\n")
        self.assertIs(self.usable(), True)
        self.write(".env", "GRAVITEE_LICENSE=forged\n")
        self.assertIs(self.usable(), False)
        (self.root / ".env").unlink()
        self.assertIs(self.usable(), False)

    def test_a_license_that_runs_out_stops_counting_the_next_day(self):
        # No file changes when it expires: the project is read again each day
        self.write(".env", "GRAVITEE_LICENSE=genuine\n")
        gathered = self.patch(project, "gather", wraps=project.gather)
        today = mock.Mock(date=mock.Mock(today=mock.Mock(return_value="2026-10-11")))
        with mock.patch.object(project, "datetime", today):
            self.assertIs(self.usable(), True)
            self.assertIs(self.usable(), True)
            self.assertEqual(gathered.call_count, 1)
            today.date.today.return_value = "2026-10-12"
            self.assertIs(self.usable(), True)
            self.assertEqual(gathered.call_count, 2)


class Order(Legacy):

    def test_a_file_gives_its_services_as_it_writes_them_not_as_compose_sorts_them(self):
        self.folder(project, files={"playgrounds/bank/compose.yml": (
            "name: bank\n\nx-gravitee:\n  kind: playground\n\nservices:\n  bakery-web:   # the screen\n    image: web\n"
            "    labels:\n      a: b\n\n  # comment\n  bakery-api:\n    image: api\n  bakery-agent:\n    image: agent\n"
            "\nnetworks:\n  internal:\n    name: n\n")})
        self.assertEqual(project._written("playgrounds/bank/compose.yml"), ["bakery-web", "bakery-api", "bakery-agent"])
        self.assertEqual(project._written("playgrounds/missing/compose.yml"), [])
        config = {"services": {"bakery-agent": {}, "bakery-api": {}, "bakery-web": {}, "extra": {}, "core": {}}}
        # In the file's order, what it does not name after them, the core's services out
        self.assertEqual(project._own("playgrounds/bank/compose.yml", config, [], {"core"}),
                         ["bakery-web", "bakery-api", "bakery-agent", "extra"])


class Tag(Legacy):

    def test_the_tag_an_image_names(self):
        self.assertEqual([project.tag(image) for image in ("graviteeio/apim-gateway:4.12.20", "redis:8.0", "localhost:5000/team/app:1.2",
                                                           "redis", "localhost:5000/app", "")],
                         ["4.12.20", "8.0", "1.2", "latest", "latest", ""])

    def test_an_image_that_docker_names_by_its_id_or_digest_names_no_tag(self):
        # What a container reports once a pull moved its tag: a hash is not a version
        for image in ("sha256:3f2a9c1b77d0aa", "redis@sha256:abc123def456", "ghcr.io/org/app@sha256:abc123def456"):
            self.assertEqual(project.tag(image), "", image)


class Fingerprint(Legacy):

    def test_changes_when_a_file_it_reads_changes_appears_or_goes(self):
        root = self.folder(paths, project, files=dict.fromkeys((CORE, KAFKA, WELCOME), ""))
        env, guide = root / "addons/kafka/.env", root / "playgrounds/welcome/guide.md"
        seen = [project.fingerprint()]
        for change in (lambda: env.write_text("A=1\n", encoding="utf-8"), lambda: guide.write_text("# Welcome\n", encoding="utf-8"),
                       lambda: env.write_text("A=22\n", encoding="utf-8"), guide.unlink):
            change()
            seen.append(project.fingerprint())
        self.write("addons/kafka/notes.md", "Not read")
        seen.append(project.fingerprint())
        self.assertEqual(len(set(seen)), len(seen) - 1)
        self.assertEqual(seen[-1], seen[-2])


    def test_a_seed_folder_decides_where_a_playground_goes_so_its_entries_are_part_of_it(self):
        root = self.folder(paths, project, files=dict.fromkeys((CORE, WELCOME), ""))
        seed, seen = root / "playgrounds/welcome/seed", [project.fingerprint()]
        for at, change in enumerate((seed.mkdir, (seed / "apim").mkdir, (seed / "am").mkdir, (seed / "am").rmdir), 1):
            change()
            os.utime(seed, ns=(at * 10**9, at * 10**9))   # a filesystem may not tell two changes a moment apart
            seen.append(project.fingerprint())
        self.assertEqual(len(set(seen)), len(seen))


class Saved(Legacy):
    """What each service of what is on is, as saved: the hashes Compose makes them from, the images their tags name."""

    def setUp(self):
        self.folder(paths, project, files={CORE: "", KAFKA: ""})
        compose.CACHE.drop("config-hash:")
        self.addCleanup(compose.CACHE.drop, "config-hash:")
        self.hashes = self.patch(compose, "config_hashes", return_value={"mongodb": "h1", "redis": "h2"})
        self.held = self.patch(compose, "image_ids", return_value={"mongo:8.0.29": "sha256:m",
                                                                   "bitnamilegacy/kafka:3.9": "sha256:k"})
        self.project = {"core_loaded": True, "services": {
            "mongodb": {"image": "mongo:8.0.29"}, "redis": {"image": "redis:8"},
            "kafka": {"image": "docker.io/bitnamilegacy/kafka:3.9"}, "app": {"image": ""}}}

    def test_hashes_and_images(self):
        self.assertEqual(project.saved(self.project), {"hashes": {"mongodb": "h1", "redis": "h2"},
                                                       "images": {"mongodb": "sha256:m", "kafka": "sha256:k"}})

    def test_the_hashes_are_asked_again_when_a_file_or_the_on_file_changes(self):
        project.saved(self.project)
        project.saved(self.project)
        self.assertEqual(self.hashes.call_count, 1)
        self.write(".lab/on.yml", "include:\n  - addons/kafka/compose.yml\n")   # what is on changed
        project.saved(self.project)
        self.write(KAFKA, "services: {}\n")
        project.saved(self.project)
        self.assertEqual(self.hashes.call_count, 3)
        # What could not be had is asked again at the next ask, not minutes later: meanwhile what runs behind is not
        # known
        compose.CACHE.drop("config-hash:")
        self.hashes.return_value = None
        self.assertEqual(project.saved(self.project)["hashes"], {})
        project.saved(self.project)
        self.assertEqual(self.hashes.call_count, 5)
        self.hashes.return_value = {"mongodb": "h1"}
        self.assertEqual(project.saved(self.project)["hashes"], {"mongodb": "h1"})

    def test_nothing_without_the_core_or_docker(self):
        self.assertEqual(project.saved({**self.project, "core_loaded": False}), {"hashes": {}, "images": {}})
        self.held.return_value = None
        self.assertEqual(project.saved(self.project)["images"], {})


def addon(name, services=None, include=(), says=""):
    """The compose file of an add-on that a test adds to a catalog: one service of its name, or these, in the house style (a
    label that describes it, the internal network), the includes it has, and what else its x-gravitee block says."""
    text = f"x-gravitee: {{kind: addon, name: {name.title()}, category: Other, description: Added by a test{says}}}\n"
    if include:
        text += "include:\n" + "".join(f"  - {path}\n" for path in include)
    names = [name] if services is None else services
    text += "services:\n" if names else ""
    for service in names:
        text += (f"  {service}:\n    image: busybox\n    container_name: gio-{service}\n"
                 f"    labels: {{io.gravitee.name: {service}, io.gravitee.description: Added by a test}}\n"
                 "    networks: [internal]\n")
    return text


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Needs(Case):
    """A unit that includes another catalog's units: it loads without them, says which catalog it lacks and why, and is judged
    when the catalog arrives."""

    PARTNER = {"id": "partner", "name": "Partner fixture", "url": "https://example.invalid/partner.git", "min": "1.0.0"}

    def pair(self, **how):
        inputs, built = read(self, **how)
        return inputs, built, built["setups"]["pair"]

    def test_a_catalog_that_is_not_added_is_missing_and_the_unit_loads_with_the_include_left_out(self):
        with mock.patch.object(compose, "raw", wraps=compose.raw) as raw:
            inputs, built, pair = self.pair(catalogs={"lab-new": {}})
        self.assertEqual(pair["needs_catalogs"], [dict(self.PARTNER, have=None, reason="missing")])
        self.assertEqual((pair["name"], pair["includes"]), ("Pair", ["echo", "@partner/ledger"]))
        self.assertNotIn("@partner/ledger", built["addons"], "the add-on it names is the catalog's, which is not here")
        ledger = ".lab/catalogs/partner/addons/ledger/compose.yml"
        self.assertEqual(inputs["reach"][pair["file"]]["missing"], [ledger])
        self.assertIn(mock.call(pair["file"], skip=[ledger], timeout=project.UNIT_TIMEOUT), raw.call_args_list)
        self.assertIn(pair["file"], inputs["raw"])
        self.assertNotIn(pair["file"], inputs["configs"], "it is judged when the catalog arrives, not resolved without it")
        self.assertIsNone(pair["blocked"])
        self.assertEqual(built["problems"], [])

    def test_the_other_units_of_the_catalog_are_judged_as_before(self):
        _, built, _ = self.pair(catalogs={"lab-new": {}})
        self.assertEqual(built["addons"]["echo"]["needs_catalogs"], [])
        self.assertTrue(built["addons"]["future"]["blocked"])
        self.assertEqual(built["catalogs"]["fixture"]["needs"],
                         [{key: self.PARTNER[key] for key in ("id", "name", "url", "min")}])

    def test_a_catalog_that_is_added_and_not_downloaded_is_downloading_or_failed(self):
        for error, reason in ((None, "downloading"), ("unreachable", "failed")):
            with self.subTest(error=error):
                _, _, pair = self.pair(more=lambda case: fixtures.declare(case, "partner", **({"error": error} if error else {})))
                self.assertEqual([need["reason"] for need in pair["needs_catalogs"]], [reason])

    def test_a_catalog_older_than_the_floor_is_old(self):
        _, _, pair = self.pair(catalogs={"partner": {"version": "0.5.0"}})
        self.assertEqual(pair["needs_catalogs"], [dict(self.PARTNER, have="0.5.0", reason="old")])

    def test_a_catalog_that_lost_the_unit_is_gone(self):
        _, _, pair = self.pair(catalogs={"partner": {"drop": ["addons/ledger"]}})
        self.assertEqual([(need["reason"], need.get("unit")) for need in pair["needs_catalogs"]], [("gone", "ledger")])

    def test_a_catalog_that_is_there_is_no_need(self):
        _, _, pair = self.pair(catalogs={"partner": {}})
        self.assertEqual(pair["needs_catalogs"], [])

    def test_a_unit_of_this_machine_that_includes_a_catalog_that_is_not_there_says_so_too(self):
        def uses(case):
            case.write(".lab/catalogs/local/addons/uses/compose.yml",
                       addon("uses", include=["../../../partner/addons/ledger/compose.yml"]))
        inputs, built = read(self, more=uses)
        mine = built["addons"]["local/uses"]
        self.assertEqual([need["reason"] for need in mine["needs_catalogs"]], ["missing"])
        self.assertEqual(built["problems"], [], "it loaded: a missing include is not a problem of its file")
        self.assertNotIn(mine["file"], inputs["configs"])
        self.assertIn(mine["file"], inputs["raw"])

    def test_the_aggregate_of_this_machine_leaves_out_a_unit_that_cannot_start(self):
        # Compose would refuse the whole aggregate for one include that is not there
        def uses(case):
            case.write(".lab/catalogs/local/addons/uses/compose.yml",
                       addon("uses", include=["../../../partner/addons/ledger/compose.yml"]))
        inputs, _ = read(self, local=("scratch",), more=uses)
        self.assertIn("fixture-echo", inputs["resolved"], "Scratch of this machine is in it, and Echo, which it brings")
        self.assertNotIn("uses", inputs["resolved"])
        self.assertEqual(inputs["problems"], [])


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Hostile(Case):
    """A catalog is hostile input: what its files say is read before Compose is asked, a unit that fails the screen or
    reaches one that does is never given to it, and one that Compose cannot read is blocked while the rest load."""

    EVIL = ("x-gravitee: {kind: addon, name: Evil, category: Other, description: Reads a lot}\n"
            "services:\n  fixture-evil:\n    image: busybox\n    env_file: /dev/zero\n")

    def asked(self, run):
        """(what `run` made of the project, the files Compose was asked to read or resolve): compose.raw and the resolving of
        one unit are watched, and answer as they would."""
        asked = []
        raw, alone = compose.raw, project._alone

        def watched_raw(path, *more, **how):
            asked.append(path)
            return raw(path, *more, **how)

        def watched_alone(path, *more):
            asked.append(path)
            return alone(path, *more)
        with mock.patch.object(compose, "raw", watched_raw), mock.patch.object(project, "_alone", watched_alone):
            return run(), asked

    def evil(self, case):
        for name, text in (("evil", self.EVIL), ("friend", addon("friend", [], ["../../addons/evil/compose.yml"]))):
            case.write(f".lab/catalogs/fixture/addons/{name}/compose.yml", text)
        case.write(".lab/catalogs/local/addons/mine/compose.yml", addon("mine", [], ["../../../fixture/addons/evil/compose.yml"]))

    def test_a_text_the_screen_refuses_is_never_given_to_compose_nor_is_what_includes_it(self):
        (inputs, built), asked = self.asked(lambda: read(self, more=self.evil))
        folder = ".lab/catalogs/fixture/addons"
        self.assertEqual({file for file in asked if "evil" in file or "friend" in file or "mine" in file}, set())
        self.assertEqual(inputs["screened"][f"{folder}/evil/compose.yml"], ["Reads files outside its folder"])
        for uid in ("friend", "local/mine"):
            self.assertEqual(built["addons"][uid]["blocked"], {"reasons": ["Needs evil, which is blocked"]}, uid)
        self.assertEqual(built["addons"]["evil"]["blocked"], {"reasons": ["Reads files outside its folder"]})
        self.assertEqual([built["addons"][uid]["services"] for uid in ("evil", "friend", "local/mine")], [[], [], []])
        self.assertNotIn("fixture-evil", built["services"])
        self.assertTrue(built["addons"]["echo"]["services"], "the rest of the catalog loads")
        self.assertEqual(built["problems"], [])

    def test_a_file_that_is_a_link_or_a_pipe_or_not_text_or_too_large_is_refused_before_it_is_read(self):
        def files(case):
            base = case.root / ".lab/catalogs/fixture/addons"
            for name in ("link", "pipe", "binary", "large", "shared"):
                (base / name).mkdir()
            os.symlink("/etc/hosts", base / "link/compose.yml")
            os.mkfifo(base / "pipe/compose.yml")   # not a file: it is no unit, and is never opened
            (base / "binary/compose.yml").write_bytes(b"\xff\xfe\x00 not text")
            (base / "large/compose.yml").write_text(addon("large") + "# " + "x" * (1 << 20) + "\n", encoding="utf-8")
            os.symlink("/dev/zero", base / "shared/more.yml")
            (base / "shared/compose.yml").write_text(addon("shared", include=["more.yml"]), encoding="utf-8")
        (inputs, built), asked = self.asked(lambda: read(self, more=files))
        base = ".lab/catalogs/fixture/addons"
        self.assertEqual([file for file in asked if any(name in file for name in ("link", "pipe", "binary", "large", "shared"))], [])
        line = "Reads files outside its folder"
        self.assertEqual({uid: built["addons"][uid]["blocked"]["reasons"] for uid in ("link", "large", "shared")},
                         {"link": [line], "large": [line], "shared": [line]})
        self.assertNotIn("pipe", built["addons"])
        self.assertEqual(built["addons"]["binary"]["blocked"], {"reasons": ["Cannot be read: not UTF-8 text"]})
        self.assertEqual(inputs["unreadable"], {f"{base}/binary/compose.yml": "not UTF-8 text"})

    def test_twenty_files_that_read_without_end_cost_no_call_to_compose(self):
        def many(case):
            for number in range(20):
                case.write(f".lab/catalogs/fixture/addons/zero{number}/compose.yml", self.EVIL)
        started = time.monotonic()
        (inputs, built), asked = self.asked(lambda: read(self, more=many))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual([file for file in asked if "zero" in file], [])
        self.assertEqual(sum(1 for uid, one in built["addons"].items() if uid.startswith("zero") and one["blocked"]), 20)

    def test_a_unit_whose_closure_reaches_a_link_is_held_as_well(self):
        def reaches(case):
            base = case.root / ".lab/catalogs/fixture"
            os.symlink("/dev/zero", base / "addons/echo/more.yml")
            case.write(".lab/catalogs/fixture/addons/reach/compose.yml", addon("reach", [], ["../echo/more.yml"]))
        (_, built), asked = self.asked(lambda: read(self, more=reaches))
        self.assertEqual([file for file in asked if "reach" in file], [])
        self.assertEqual(built["addons"]["reach"]["blocked"], {"reasons": ["Reads files outside its folder"]})

    def test_a_unit_whose_file_is_broken_is_a_problem_of_its_catalog_and_the_rest_load(self):
        def broken(case):
            case.write(".lab/catalogs/fixture/addons/broken/compose.yml", "services: [\n")
        inputs, built = read(self, more=broken)
        found = built["catalog_problems"]["fixture"]
        self.assertEqual([(one["file"], one["display"]) for one in found],
                         [(".lab/catalogs/fixture/addons/broken/compose.yml", "addons/broken/compose.yml")])
        self.assertTrue(found[0]["message"].startswith("Cannot be read: "), found[0]["message"])
        self.assertNotIn(str(self.root), found[0]["message"])
        self.assertEqual(built["problems"], [], "never the banner")
        self.assertNotIn("broken", built["addons"], "it did not load, as a file that does not load never did")
        self.assertIn("echo", built["addons"])
        self.assertEqual(built["catalogs"]["fixture"]["problems"], found)

    def test_a_unit_that_compose_cannot_resolve_is_blocked_and_says_why(self):
        def undefined(case):
            case.write(".lab/catalogs/fixture/addons/needs/compose.yml",
                       addon("needs").replace("image: busybox", "image: ${FIXTURE_NEEDS_IMAGE:?set it}"))
        inputs, built = read(self, more=undefined)
        needs = built["addons"]["needs"]
        self.assertEqual(needs["blocked"], {"reasons": [
            "Cannot be read: error while interpolating services.needs.image: required variable FIXTURE_NEEDS_IMAGE is "
            "missing a value: set it"]})
        self.assertEqual(needs["services"], [])
        self.assertIn(needs["file"], inputs["raw"])
        self.assertNotIn(needs["file"], inputs["configs"])
        self.assertEqual(inputs["errors"][needs["file"]].count("\n"), 0)
        self.assertTrue(built["addons"]["echo"]["services"])

    def test_a_unit_that_compose_takes_too_long_to_read_or_resolve_is_the_unit_of_a_catalog_not_the_project(self):
        late = compose.ComposeError("docker did not answer within 15 s")
        raw, alone = compose.raw, project._alone

        def slow_raw(path, *more, **how):
            if path.endswith("addons/cluster/compose.yml"):
                raise late
            return raw(path, *more, **how)

        def slow_alone(path, *more):
            return (None, str(late)) if path.endswith("addons/gateway/compose.yml") else alone(path, *more)
        with mock.patch.object(compose, "raw", slow_raw), mock.patch.object(project, "_alone", slow_alone):
            inputs, built = read(self)
        self.assertNotIn("cluster", built["addons"])
        self.assertEqual(built["catalog_problems"]["fixture"][0]["message"], "Cannot be read: docker did not answer within 15 s")
        self.assertEqual(built["addons"]["gateway"]["blocked"], {"reasons": ["Cannot be read: docker did not answer within 15 s"]})
        self.assertEqual(built["addons"]["gateway"]["services"], [])
        self.assertEqual(built["platform"], {"gateways": {"cloud": None}}, "a gateway that is blocked is none")
        self.assertEqual(built["problems"], [])
        self.assertTrue(built["addons"]["echo"]["services"])

    def test_a_unit_that_raises_is_blocked_alone(self):
        raw = compose.raw

        def raising(path, *more, **how):
            if path.endswith("addons/echo/compose.yml"):
                raise RuntimeError("not expected")
            return raw(path, *more, **how)
        with mock.patch.object(compose, "raw", raising):
            inputs, built = read(self)
        self.assertEqual(built["catalog_problems"]["fixture"][0]["message"], "Cannot be read: unexpected RuntimeError")
        self.assertIn("tour", built["playgrounds"])

    def test_a_seed_that_is_over_a_megabyte_or_a_link_is_not_followed(self):
        def seed(case):
            base = case.root / ".lab/catalogs/fixture/playgrounds"
            (base / "tour/seed/big.yaml").write_text("x" * ((1 << 20) + 1), encoding="utf-8")
            os.symlink("/dev/zero", base / "shop/seed/zero.yaml")
        inputs, built = read(self, more=seed)
        self.assertEqual(built["playgrounds"]["tour"]["blocked"], {"reasons": ["Reads files outside its folder"]})
        self.assertIsNone(built["playgrounds"]["shop"]["blocked"], "a link in a seed is not followed, and costs nothing")

    def held(self, name, text, more=None, others=None):
        """The project with a unit `name` in the default catalog that has this text and what `more(case)` adds, read with a time
        limit of three seconds so that a Compose that is asked to read what never ends is a failure of the test, not a
        wait: (the inputs, the project, the files Compose was asked about)."""
        def files(case):
            case.write(f".lab/catalogs/fixture/addons/{name}/compose.yml", text)
            if more:
                more(case, case.root / f".lab/catalogs/fixture/addons/{name}")
            for other, content in (others or {}).items():
                case.write(other, content)
        started = time.monotonic()
        with mock.patch.object(project, "UNIT_TIMEOUT", 3):
            (inputs, built), asked = self.asked(lambda: read(self, more=files))
        self.assertLess(time.monotonic() - started, 12, "Compose was not asked to read what never ends")
        return inputs, built, [file for file in asked if name in file or "mine" in file]

    def test_a_file_a_unit_includes_that_is_no_unit_is_never_given_to_compose_for_it_reads_what_its_include_says(self):
        def sneak(case, folder):
            case.write(f"{folder.relative_to(case.root)}/more.yml",
                       "include:\n  - path: sub.yml\n    env_file: pipe\nservices: {}\n")
            os.mkfifo(folder / "pipe")
        _, built, asked = self.held("sneak", addon("sneak", [], ["more.yml"]), sneak)
        self.assertEqual(asked, [])
        self.assertEqual(built["addons"]["sneak"]["blocked"], {"reasons": ["Includes content from outside the catalog"]})
        self.assertEqual(built["addons"]["sneak"]["services"], [])
        self.assertTrue(built["addons"]["echo"]["services"], "the rest of the catalog loads")
        self.assertEqual(built["problems"], [])

    def test_an_include_the_screen_can_name_is_followed_and_what_it_reaches_holds_the_unit(self):
        """A flow list, braces: the screen reads them as it reads a block list, so the walk follows them. The file they name
        here is a pipe, which is never given to Compose, and is the reason the unit is held."""
        def pipe(case, folder):
            os.mkfifo(folder / "more.yml")
        for how, text in (("flow", addon("hidden").replace("services:", "include: [more.yml]\nservices:")),
                          ("braces", addon("hidden").replace("services:", "include:\n  - {path: more.yml}\nservices:")),
                          ("block", addon("hidden", [], ["more.yml"]))):
            with self.subTest(how=how):
                inputs, built, asked = self.held("hidden", text, pipe)
                self.assertEqual(asked, [])
                self.assertEqual(built["addons"]["hidden"]["blocked"], {"reasons": ["Reads files outside its folder"]})
                self.assertEqual(built["addons"]["hidden"]["services"], [])

    def test_an_include_the_screen_cannot_name_holds_the_unit_as_well(self):
        """A long form with a key besides `path` (the file it names may be read from another folder, and nothing here says which),
        a name with a comma: the pipe next to the unit is not reached, Compose is not asked, and the unit is held for what it
        hides. An `env_file` is the screen's own refusal, before the walk."""
        def pipe(case, folder):
            os.mkfifo(folder / "more.yml")
        outside, files = "Includes content from outside the catalog", "Reads files outside its folder"
        for how, text, line in (
                ("project directory", addon("hidden").replace("services:", "include:\n  - path: more.yml\n    project_directory: .\n"
                                                                "services:"), outside),
                ("a comma", addon("hidden").replace("services:", "include:\n  - more.yml,x\nservices:"), outside),
                ("a key spelled with an escape", addon("hidden").replace("services:", '"inclu\\u0064e": [more.yml]\nservices:'), outside),
                ("env file", addon("hidden").replace("services:", "include:\n  - path: more.yml\n    env_file: more.env\nservices:"),
                 files)):
            with self.subTest(how=how):
                _, built, asked = self.held("hidden", text, pipe)
                self.assertEqual(asked, [])
                self.assertEqual(built["addons"]["hidden"]["blocked"], {"reasons": [line]})

    HOSTILE = ("services:\n  sneaky:\n    image: busybox\n    container_name: gio-sneaky\n    networks: [internal]\n"
               "    environment:\n      TOKEN: ${AWS_SECRET_ACCESS_KEY}\n")

    def test_an_include_that_compose_reads_as_another_name_than_its_letters_hold_the_unit_and_that_file_is_never_given_to_compose(self):
        """YAML reads 'compos\\x65.yml' (single quotes), a name with a space inside the quotes, a name on two lines and `:,` as
        the name they spell, not as their letters. The file with that name, next to the unit, is one nobody screened: it is
        a hostile one here, and the unit that includes it is held with Compose never asked (it would load the file)."""
        outside = "Includes content from outside the catalog"
        for how, item, literal in (("single-quoted escape", "'compos\\x65.yml'", "compos\\x65.yml"),
                                   ("plain escape", "compos\\x65.yml", "compos\\x65.yml"),
                                   ("escaped backslash", '"compos\\\\x65.yml"', "compos\\x65.yml"),
                                   ("space at the end", '"compose.yml "', "compose.yml "), ("space at the start", '" compose.yml"', " compose.yml"),
                                   ("space in single quotes", "'compose.yml '", "compose.yml "),
                                   ("two lines", "compose\n    .yml", "compose .yml"), ("colon and comma", ":,", ":,"),
                                   ("path and colon and comma", "path: :,", ":,")):
            with self.subTest(how=how):
                def sneak(case, folder):
                    (folder / literal).write_text(self.HOSTILE, encoding="utf-8")
                text = addon("sneak").replace("services:", f"include:\n  - {item}\nservices:")
                inputs, built, asked = self.held("sneak", text, sneak)
                self.assertEqual(asked, [], "Compose is not asked about the unit, which would load the file")
                self.assertEqual(built["addons"]["sneak"]["blocked"], {"reasons": [outside]})
                self.assertEqual(built["addons"]["sneak"]["services"], [])
                self.assertNotIn("sneaky", built["services"])
                self.assertTrue(built["addons"]["echo"]["services"], "the rest of the catalog loads")

    def test_a_backslash_that_ends_a_line_does_not_hide_an_include_from_the_walk(self):
        """The line after a backslash at the end of a line is a line of its own to YAML, so an `include` on it is read by
        Compose: the unit is held and Compose is not asked."""
        def more(case, folder):
            (folder / "more.yml").write_text(self.HOSTILE, encoding="utf-8")
        for how, text in (("a key", "x-note: abc\\\ninclude:\n  - more.yml\n"), ("a flow list", "x-note: abc\\\ninclude: [more.yml]\n"),
                          ("an escaped key", 'x-note: abc\\\n"inclu\\u0064e": [more.yml]\n'), ("a bad place", "x-note: abc\\\ninclude: [/etc/hostname]\n")):
            with self.subTest(how=how):
                _, built, asked = self.held("sneak", addon("sneak").replace("services:", text + "services:"), more)
                self.assertEqual(asked, [])
                self.assertEqual(built["addons"]["sneak"]["blocked"], {"reasons": ["Includes content from outside the catalog"]})
                self.assertEqual(built["addons"]["sneak"]["services"], [])

    def test_the_includes_the_judgement_sees_are_the_ones_the_walk_followed(self):
        """T23 judges `includes`: an include the walk follows and the judgement does not see would be a unit the policy never
        asked about. An add-on of a catalog that this one does not declare is T23 as a flow list as it is as a block list."""
        newer = ".lab/catalogs/lab-new/addons/newer/compose.yml"
        for how, text in (("flow", addon("looks").replace("services:", "include: [../../../lab-new/addons/newer/compose.yml]\n"
                                                                        "services:")),
                          ("block", addon("looks", [], ["../../../lab-new/addons/newer/compose.yml"]))):
            with self.subTest(how=how):
                def files(case):
                    case.write(".lab/catalogs/fixture/addons/looks/compose.yml", text)
                inputs, built = read(self, {"lab-new": {}}, more=files)
                self.assertEqual(inputs["includes"][".lab/catalogs/fixture/addons/looks/compose.yml"], [newer])
                self.assertEqual(inputs["reach"][".lab/catalogs/fixture/addons/looks/compose.yml"]["targets"], [newer])
                self.assertIn("Includes content from outside the catalog", built["addons"]["looks"]["blocked"]["reasons"])

    def test_an_empty_value_a_unit_writes_reads_nothing_and_a_name_it_passes_through_is_a_read(self):
        """F1, with the real Compose: it prints `- NAME`, `NAME=` and `NAME: ""` as the same empty value, and the texts tell them
        apart. A unit that includes another has that unit's services in its configuration: what that unit writes as an empty
        value is no read of the includer, which gets the text of the file the walk reached."""
        def service(name, environment):
            return (f"  {name}:\n    image: busybox\n    container_name: gio-{name}\n    networks: [internal]\n"
                    f"    labels: {{io.gravitee.name: {name}, io.gravitee.description: Added by a test}}\n"
                    f"    environment:\n{environment}")
        head = "x-gravitee: {kind: addon, name: Added, category: Other, description: Added by a test}\n"
        inner = head + "services:\n" + service("fixture-inner", "      INNER_BLANK: \"\"\n      INNER_SINGLE: ''\n      INNER_NULL:\n")
        outer = (head + "include:\n  - ../inner/compose.yml\nservices:\n"
                 + service("fixture-outer", '      OUTER_BLANK: ""\n      OUTER_NULL:\n'))
        listed = head + "services:\n" + service("fixture-listed", "      - LISTED_BLANK=\n      - LISTED_PASS\n      - LISTED_ALSO=\n")

        def files(case):
            for name, text in (("inner", inner), ("outer", outer), ("listed", listed)):
                case.write(f".lab/catalogs/fixture/addons/{name}/compose.yml", text)
        inputs, _ = read(self, more=files)
        found = {name: inputs["references"].get(f".lab/catalogs/fixture/addons/{name}/compose.yml", [])
                 for name in ("inner", "outer", "listed")}
        self.assertEqual(found, {"inner": ["INNER_NULL"], "outer": ["INNER_NULL", "OUTER_NULL"], "listed": ["LISTED_PASS"]})

    def test_a_dot_env_that_is_a_pipe_holds_the_unit_for_compose_reads_the_one_next_to_a_file_it_includes(self):
        def pipe(case, folder):
            os.mkfifo(folder / ".env")
        _, built, asked = self.held("piped", addon("piped"), pipe)
        self.assertEqual(asked, [])
        self.assertEqual(built["addons"]["piped"]["blocked"], {"reasons": ["Reads files outside its folder"]})
        self.assertEqual(built["problems"], [])

    def test_a_unit_of_this_machine_that_includes_one_of_those_is_held_with_it_and_not_resolved_with_the_aggregate(self):
        def sneak(case, folder):
            case.write(f"{folder.relative_to(case.root)}/more.yml", "include:\n  - path: sub.yml\n    env_file: pipe\nservices: {}\n")
            os.mkfifo(folder / "pipe")
        mine = {".lab/catalogs/local/addons/mine/compose.yml": addon("mine", [], ["../../../fixture/addons/sneak/compose.yml"])}
        with mock.patch.object(compose, "resolved", wraps=compose.resolved) as resolved:
            inputs, built, asked = self.held("sneak", addon("sneak", [], ["more.yml"]), sneak, mine)
        self.assertEqual(asked, [])
        reasons = ["Includes content from outside the catalog"]
        self.assertEqual([built["addons"][uid]["blocked"] for uid in ("sneak", "local/mine")], [{"reasons": reasons}] * 2)
        self.assertNotIn(".lab/catalogs/local/addons/mine/compose.yml", inputs["raw"])
        resolved.assert_called_once()
        self.assertEqual(resolved.call_args[0][0], compose.all_files([]), "nothing of this machine's is in the aggregate")

    def test_a_catalog_file_that_includes_a_pipe_is_never_given_to_compose_for_its_shared_settings(self):
        def catalog(case):
            file = case.root / ".lab/catalogs/fixture/catalog.yml"
            file.write_text(file.read_text(encoding="utf-8") + "  shared_settings: [FIXTURE_ONE]\ninclude:\n  - pipe.yml\n", encoding="utf-8")
            os.mkfifo(case.root / ".lab/catalogs/fixture/pipe.yml")
        raw = compose.raw
        asked = []
        with mock.patch.object(project, "UNIT_TIMEOUT", 3), \
                mock.patch.object(compose, "raw", lambda path, *more, **how: asked.append(path) or raw(path, *more, **how)):
            started = time.monotonic()
            inputs, _ = read(self, more=catalog)
        self.assertLess(time.monotonic() - started, 12)
        self.assertEqual([file for file in asked if file.endswith("catalog.yml")], [])
        self.assertEqual(inputs["shared_settings"]["fixture"], [])

    def test_a_unit_folder_that_is_a_link_out_of_the_catalog_is_refused_before_it_is_read(self):
        def trojan(case):
            outside = case.root / "outside/trojan"
            outside.mkdir(parents=True)
            (outside / "compose.yml").write_text(addon("trojan").replace("networks: [internal]", "networks: [internal]\n    volumes: ['./:/data:ro']"),
                                                 encoding="utf-8")
            os.symlink(outside, case.root / ".lab/catalogs/fixture/addons/trojan")
        (inputs, built), asked = self.asked(lambda: read(self, more=trojan))
        self.assertEqual([file for file in asked if "trojan" in file], [])
        self.assertEqual(built["addons"]["trojan"]["blocked"], {"reasons": ["Reads files outside its folder"]})
        self.assertEqual(built["addons"]["trojan"]["services"], [])
        self.assertNotIn("trojan", built["services"])

    def test_a_catalog_folder_that_is_a_link_holds_its_units_all_the_same(self):
        _, built = read(self, fixture={"link": True})
        self.assertTrue(os.path.islink(self.root / ".lab/catalogs/fixture"))
        self.assertEqual(built["addons"]["echo"]["services"], ["fixture-echo"])
        self.assertIsNone(built["addons"]["echo"]["blocked"])
        self.assertEqual(built["catalog_problems"], {})

    def test_a_seed_folder_that_is_a_link_blocks_the_playground_and_is_not_read(self):
        def seed(case):
            outside = case.root / "outside/seed"
            (outside / "apim").mkdir(parents=True)
            (outside / "apim/x.yaml").write_text("token: ${APIM_TOKEN}\n", encoding="utf-8")
            folder = case.root / ".lab/catalogs/fixture/playgrounds/tour/seed"
            shutil.rmtree(folder)
            os.symlink(outside, folder)
        inputs, built = read(self, more=seed)
        tour = built["playgrounds"]["tour"]
        self.assertEqual(tour["blocked"], {"reasons": ["Reads files outside its folder"]})
        self.assertNotIn(tour["file"], inputs["seed_references"])
        self.assertEqual(inputs["seeds"]["tour"], [])

    def test_a_dot_env_that_is_not_text_takes_nothing_down_but_itself(self):
        def binary(case):
            (case.root / ".lab/catalogs/fixture/addons/echo/.env").write_bytes(b"\xff\xfe\x00bad=1\n")
        inputs, built = read(self, more=binary)
        self.assertEqual(inputs["env"][".lab/catalogs/fixture/addons/echo/.env"], {})
        self.assertTrue(built["addons"]["cluster"]["services"], "the rest of the catalog loads")
        self.assertNotIn("echo", built["addons"], "Compose does not read it either: the problem is the catalog's, not the banner's")
        self.assertEqual(built["problems"], [])

    def test_the_settings_a_seed_reads_are_read_for_the_policy(self):
        def seed(case):
            case.write(".lab/catalogs/fixture/playgrounds/tour/seed/apim/token.yaml", "token: ${APIM_TOKEN}\nurl: ${FIXTURE_TOUR_BACKEND_URL}\n")
        inputs, built = read(self, more=seed)
        tour = built["playgrounds"]["tour"]
        self.assertEqual(inputs["seed_references"][tour["file"]], ["APIM_TOKEN", "FIXTURE_TOUR_BACKEND_URL"])
        self.assertIn("Seed data reads a token", tour["verdict"]["notes"])


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Names(Case):
    """Who keeps a name: the platform, then the default catalog, this machine and the other catalogs in the order they were
    added; only a unit of another catalog loses one, and adding a catalog never blocks what was there."""

    def read(self, files, **how):
        return read(self, catalogs={"partner": {"files": files}}, **how)[1]

    def test_a_unit_that_reuses_the_name_of_a_service_of_an_earlier_one_is_blocked_by_it(self):
        built = self.read({"addons/clash/compose.yml": addon("clash", ["fixture-echo"])})
        self.assertEqual(built["addons"]["@partner/clash"]["blocked"], {"reasons": ["Same name as Echo"]})
        self.assertEqual(built["addons"]["@partner/clash"]["services"], [])
        # The earlier one, the default catalog and the catalog the clash is in are untouched
        self.assertIsNone(built["addons"]["echo"]["blocked"])
        self.assertIsNone(built["addons"]["@partner/ledger"]["blocked"])
        self.assertEqual(built["services"]["fixture-echo"]["owner"]["id"], "echo")

    def test_nor_a_container_name_nor_a_volume(self):
        text = addon("same", ["fixture-same"]).replace("gio-fixture-same", "gio-fixture-echo")
        built = self.read({"addons/same/compose.yml": text})
        self.assertEqual(built["addons"]["@partner/same"]["blocked"], {"reasons": ["Same name as Echo"]})

    def test_the_platform_keeps_its_names_too(self):
        # Compose itself refuses a container name the core has, when it reads the unit with the core: the unit cannot be read
        text = addon("redis-clone", ["redis-clone"]).replace("gio-redis-clone", "gio-redis")
        found = self.read({"addons/redis-clone/compose.yml": text})["addons"]["@partner/redis-clone"]
        self.assertEqual(found["services"], [])
        self.assertIn("gio-redis", found["blocked"]["reasons"][0])

    def test_the_default_catalog_and_this_machine_are_never_blocked_by_a_later_catalog(self):
        # Partner defines Echo's service first in its own file, but the default catalog claims first
        built = self.read({"addons/clash/compose.yml": addon("clash", ["fixture-echo", "scratch-notes"])}, local=("scratch",))
        self.assertIsNone(built["addons"]["echo"]["blocked"])
        self.assertIsNone(built["addons"]["local/scratch"]["blocked"])
        self.assertTrue(built["addons"]["@partner/clash"]["blocked"])

    def test_a_unit_that_is_blocked_already_claims_nothing(self):
        # The first unit of the later catalog is blocked by its level, so the second may keep the name it also defines
        first = addon("first", ["shared-name"], says=", requires: {lab: 99}")
        built = self.read({"addons/first/compose.yml": first, "addons/second/compose.yml": addon("second", ["shared-name"])})
        self.assertEqual(built["addons"]["@partner/first"]["blocked"]["reasons"], ["Needs a newer Gravitee Lab"])
        self.assertIsNone(built["addons"]["@partner/second"]["blocked"])


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Brings(Case):
    """What a unit brings is part of how it is judged, whatever the order the units are read in."""

    def test_a_unit_may_share_the_network_of_a_service_it_brings_even_when_it_is_read_first(self):
        text = addon("aa-joiner", [], ["../echo/compose.yml"]) + (
            "services:\n  fixture-aa-joiner:\n    image: busybox\n    network_mode: service:fixture-echo\n"
            "    labels: {io.gravitee.name: Joiner, io.gravitee.description: Added by a test}\n")
        _, built = read(self, fixture={"files": {"addons/aa-joiner/compose.yml": text}})
        self.assertIsNone(built["addons"]["aa-joiner"]["blocked"])
        self.assertEqual(built["addons"]["aa-joiner"]["services"], ["fixture-aa-joiner"])
        # And one that joins a service it does not bring is refused
        other = text.replace("service:fixture-echo", "service:apim-gateway")
        _, built = read(self, fixture={"files": {"addons/aa-joiner/compose.yml": other}})
        self.assertEqual(built["addons"]["aa-joiner"]["blocked"], {"reasons": ["Joins a network that is not the Lab's"]})


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Twins(Case):
    """The same item in this machine and in a catalog: derived at load, nothing stored; neither is blocked by the other."""

    def twin(self, case):
        text = (fixtures.CATALOGS / "fixture/addons/echo/compose.yml").read_text(encoding="utf-8")
        case.write(".lab/catalogs/local/addons/echo/compose.yml", text.replace("../../", "../../"))

    def test_a_personal_item_and_a_catalog_item_with_the_same_name_and_services_are_each_others_twin(self):
        inputs, built = read(self, more=self.twin)
        personal, shared = built["addons"]["local/echo"], built["addons"]["echo"]
        self.assertEqual(personal["twin"], {"id": "echo", "name": "Fixture catalog"})
        self.assertEqual(shared["twin"], {"id": "local/echo", "name": "Personal"})
        self.assertIsNone(personal["blocked"])
        self.assertIsNone(shared["blocked"])
        self.assertEqual(built["addons"]["echo-twin"]["twin"], None)

    def test_the_services_of_a_twin_are_owned_by_both_so_that_either_one_starts_them(self):
        _, built = read(self, more=self.twin)
        service = built["services"]["fixture-echo"]
        self.assertEqual({one["id"] for one in service["owners"]}, {"echo", "local/echo"})
        self.assertIn(service["owner"]["id"], {"echo", "local/echo"})
        for on in ("echo", "local/echo"):
            self.assertIn("fixture-echo", layers.up_services(built, None, {on}), f"{on} is on: its service starts")
        self.assertNotIn("fixture-echo", layers.up_services(built, None, set()))
        for chosen in ("echo", "local/echo"):
            self.assertEqual(layers.changing(built, {"start": [["addon", chosen]]}).get("fixture-echo"), "start", chosen)

    def test_two_units_that_only_share_a_name_are_not_twins(self):
        def other(case):
            text = (fixtures.CATALOGS / "fixture/addons/echo/compose.yml").read_text(encoding="utf-8")
            case.write(".lab/catalogs/local/addons/echo/compose.yml", text.replace("fixture-echo", "fixture-echo-mine")
                       .replace("8191", "8291").replace("gio-fixture-echo", "gio-fixture-echo-mine"))
        _, built = read(self, more=other)
        self.assertIsNone(built["addons"]["local/echo"]["twin"])
        self.assertIsNone(built["addons"]["echo"]["twin"])

    def test_a_unit_that_is_not_a_unit_of_this_machine_has_no_twin(self):
        _, built = read(self)
        self.assertEqual([unit["twin"] for kind in ("addons", "setups", "playgrounds") for unit in built[kind].values()],
                         [None] * 10)


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class AcrossCatalogs(Case):
    """An item of another catalog that brings an item of the default one, the source everyone has: what the brought item adds to the
    gateway is its own to answer for (T25 carries its verdict), not a change the bringing item makes."""

    BRIDGE = ("x-gravitee: {kind: addon, name: Bridge, category: Other, description: Brings what overlays the gateway}\n"
              "services:\n  bridge-gateway:\n    image: busybox\n    container_name: gio-bridge-gateway\n"
              "    volumes:\n      - ./data:/data:ro\n    ports:\n      - \"127.0.0.1:9192:9192\"\n"
              "  apim-gateway:\n    volumes:\n      - ./data:/data:ro\n    ports:\n      - \"127.0.0.1:9193:9193\"\n")

    CATALOG = ("x-gravitee-catalog:\n  schema: 1\n  id: partner\n  name: Partner fixture\n  description: Brings an item of the default catalog\n"
               "  version: 2.0.0\n  lab: 1\n  license: AGPL-3.0-only\n  requires:\n    catalogs:\n"
               "      fixture: {name: Fixture catalog, url: \"https://example.invalid/fixture.git\", min: 1.0.0}\n")

    def reading(self, files):
        return read(self, fixture={"files": {"addons/bridge/compose.yml": self.BRIDGE}},
                    catalogs={"partner": {"files": {"catalog.yml": self.CATALOG, **files}}})[1]["addons"]

    def test_what_a_default_item_it_brings_adds_to_the_gateway_is_not_its_own_change(self):
        found = self.reading({"addons/via/compose.yml": addon("via", [], ["../../../fixture/addons/bridge/compose.yml"])})
        self.assertIsNone(found["@partner/via"]["blocked"], found["@partner/via"]["blocked"])

    def test_the_same_overlay_written_in_the_item_itself_is_still_its_change(self):
        own = addon("own", ["own-svc"]) + "  apim-gateway:\n    volumes:\n      - /etc:/data:ro\n"
        found = self.reading({"addons/own/compose.yml": own})
        self.assertIsNotNone(found["@partner/own"]["blocked"])


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Allowed(Case):
    """What a catalog may use of this machine: a listed catalog, or one the person allowed, when the unit says why; and
    the gateway of a platform, which a catalog that is neither listed, allowed nor this machine's may not declare (N04)."""

    CLUSTER = (fixtures.CATALOGS / "fixture/addons/cluster/compose.yml").read_text(encoding="utf-8").replace(
        "fixture-cluster", "partner-cluster")
    GATEWAY = (fixtures.CATALOGS / "fixture/addons/gateway/compose.yml").read_text(encoding="utf-8").replace(
        "fixture-gateway", "partner-gateway").replace("8195", "8295")

    def partner(self, **how):
        return read(self, catalogs={"partner": {"files": {"addons/cluster/compose.yml": self.CLUSTER,
                                                           "addons/gateway/compose.yml": self.GATEWAY}, **how}})[1]

    def test_an_item_that_uses_this_machine_is_blocked_in_a_catalog_that_is_not_listed(self):
        found = self.partner()["addons"]["@partner/cluster"]
        self.assertEqual(found["blocked"], {"reasons": ["Runs with full privileges"]})
        self.assertEqual(found["uses_machine"][0]["what"], ["runs privileged"])

    def test_the_allowance_of_a_catalog_is_honored(self):
        found = self.partner(host=True)["addons"]["@partner/cluster"]
        self.assertIsNone(found["blocked"])
        self.assertEqual(found["uses_machine"][0]["service"], "partner-cluster")
        with mock.patch.object(trust, "ALLOWANCE", False):   # without the milestone, whatever the entry says
            self.assertTrue(self.partner(host=True)["addons"]["@partner/cluster"]["blocked"])

    def test_a_gateway_declared_by_a_catalog_that_is_not_trusted_with_one_is_cleared_before_any_reader_sees_it(self):
        built = self.partner()
        gateway = built["addons"]["@partner/gateway"]
        self.assertIsNone(gateway["gateway"])
        self.assertIn("Its gateway is not used: the Cloud pages do not offer it", gateway["verdict"]["notes"])
        self.assertEqual(built["platform"], {"gateways": {"cloud": "gateway"}}, "the default catalog's is still the one")
        self.assertNotIn("group", {key for addon in built["addons"].values() if addon["id"] == "@partner/gateway"
                                   for key in (addon["gateway"] or {})})

    def test_it_is_honored_when_the_catalog_is_listed_or_the_unit_is_this_machines(self):
        built = self.partner()
        self.assertTrue(built["addons"]["gateway"]["gateway"], "a listed catalog")

        def mine(case):
            case.write(".lab/catalogs/local/addons/mygate/compose.yml", self.GATEWAY.replace("partner-gateway", "mine-gateway"))
        built = read(self, more=mine)[1]
        self.assertEqual(built["addons"]["local/mygate"]["gateway"]["service"], "mine-gateway")
        self.assertEqual(built["platform"]["gateways"], {"cloud": "gateway"}, "the default catalog first, then this machine")

    def test_the_gateway_is_the_first_in_precedence_that_can_start(self):
        # The default catalog's gateway is blocked (a level this Lab does not read): this machine's takes its place
        def mine(case):
            case.write(".lab/catalogs/local/addons/mygate/compose.yml", self.GATEWAY.replace("partner-gateway", "mine-gateway"))
        gateway = ", requires: {lab: 99}, gateway: {platform: cloud, service: fixture-gateway, token: FIXTURE_GATEWAY_TOKEN}"
        built = read(self, fixture={"files": {"addons/gateway/compose.yml": addon("gateway", ["fixture-gateway"], says=gateway)}},
                     more=mine)[1]
        self.assertTrue(built["addons"]["gateway"]["blocked"])
        self.assertEqual(built["platform"]["gateways"], {"cloud": "local/mygate"})


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Shared(Case):
    """The settings a unit may read (T20): its catalog's prefix, the core's, and what its catalog says its units share."""

    TEXT = (fixtures.CATALOGS / "partner/catalog.yml").read_text(encoding="utf-8")
    READS = addon("reads", ["partner-reads"]).replace("networks: [internal]", "networks: [internal]\n    command: [\"${OLLAMA_URL}\"]")

    def partner(self, shared):
        text = self.TEXT.replace("  license: AGPL-3.0-only\n", "  license: AGPL-3.0-only\n" + shared)
        return read(self, catalogs={"partner": {"files": {"catalog.yml": text, "addons/reads/compose.yml": self.READS}}})[1]

    def test_a_setting_that_is_not_the_units_own_blocks_it(self):
        found = self.partner("")["addons"]["@partner/reads"]
        self.assertEqual(found["blocked"], {"reasons": ["Reads settings that are not its own: OLLAMA_URL"]})

    def test_a_setting_its_catalog_says_its_units_share_is_allowed(self):
        found = self.partner("  shared_settings: [OLLAMA_URL]\n")["addons"]["@partner/reads"]
        self.assertIsNone(found["blocked"])


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Anchored(Case):
    """The plugin folders of the engine that a unit may mount are the ones the project has and the ones the contract names
    as anchors (contract.lock): a fresh clone has made none yet, and a catalog's CI run has no plugins/ folder at all."""

    LOCK = "contract 1\ncontract_min 1\nanchor lab-ui port=9000 hosts=127.0.0.1,localhost since=1\nanchor plugins/am since=1\n" \
           "anchor plugins/apim since=1\nanchor seeder since=1\n"
    PLUGGED = ("x-gravitee: {kind: addon, name: Plugged, category: Other, description: Gives a service the plugins of API Management}\n"
               "services:\n  partner-plugged:\n    image: busybox\n    container_name: gio-partner-plugged\n"
               "    labels: {io.gravitee.name: partner-plugged, io.gravitee.description: Added by a test}\n"
               "    networks: [internal]\n    volumes:\n      - ../../../../../plugins/apim:/opt/plugins:ro\n")
    OUTSIDE = "Mounts files outside its folder"

    def read(self, lock=None, folders=()):
        def more(case):
            shutil.rmtree(case.root / "plugins", ignore_errors=True)   # what a catalog's CI run, or a fresh clone, has
            for name in folders:
                (case.root / "plugins" / name).mkdir(parents=True)
            if lock is not None:
                case.write("contract.lock", lock)
        inputs, built = read(self, catalogs={"partner": {"files": {"addons/plugged/compose.yml": self.PLUGGED}}}, more=more)
        return inputs, built["addons"]["@partner/plugged"]

    def test_a_unit_that_binds_a_plugin_folder_the_contract_names_is_not_blocked_whether_the_folder_is_there_or_not(self):
        for folders in ((), ("apim",), ("apim", "custom")):
            with self.subTest(folders=folders):
                inputs, unit = self.read(self.LOCK, folders)
                self.assertEqual(inputs["products"], sorted({"am", "apim", *folders}))
                self.assertEqual((unit["blocked"], unit["verdict"]["surface"]["plugins"]), (None, ["apim"]))

    def test_without_the_anchor_the_same_unit_mounts_files_outside_its_folder(self):
        for name, lock in (("no contract.lock", None), ("no anchor in it", "contract 1\ncontract_min 1\nanchor seeder since=1\n"),
                           ("the other product's", "anchor plugins/am since=1\n")):
            with self.subTest(name):
                inputs, unit = self.read(lock)
                self.assertEqual(inputs["products"], ["am"] if lock and "plugins/am" in lock else [])
                self.assertEqual((unit["blocked"] or {}).get("reasons"), [self.OUTSIDE])
        inputs, unit = self.read(None, ("apim",))
        self.assertIsNone(unit["blocked"], "the folder is there: it never needed the anchor")

    def test_what_the_contract_names_is_read_as_a_folder_name_and_only_so(self):
        lock = "\n".join(["anchor plugins/ok-1 since=1", "anchor plugins/ok-2", "anchor plugins/../etc since=1", "anchor plugins/UPPER since=1",
                          "anchor plugins/deep/er since=1", "anchor plugins/apim/ since=1", "anchor plugins/ since=1",
                          "  anchor plugins/indented since=1", "anchors plugins/near since=1", "anchor plugins/a.b since=1",
                          "anchor plugins/space name since=1", "anchor plugins/tab\tsince=1", "# anchor plugins/commented",
                          "anchor seeder since=1", "anchor lab-ui port=9000", "anchor plugins/\u0430pim since=1"])
        self.folder(project)
        self.write("contract.lock", lock)
        self.assertEqual(project._anchored_plugins(), ["ok-1", "ok-2", "space", "tab"])
        self.assertEqual(project._plugin_folders(), ["ok-1", "ok-2", "space", "tab"])

    def test_a_contract_that_is_a_link_or_too_big_or_not_text_names_nothing(self):
        self.folder(project)
        (self.root.parent / "elsewhere.lock").write_text("anchor plugins/apim since=1\n", encoding="utf-8")
        self.addCleanup((self.root.parent / "elsewhere.lock").unlink)
        (self.root / "contract.lock").symlink_to(self.root.parent / "elsewhere.lock")
        self.assertEqual(project._anchored_plugins(), [], "a link is not followed")
        (self.root / "contract.lock").unlink()
        self.write("contract.lock", "anchor plugins/apim since=1\n" + "# x\n" * (project.TEXT_LIMIT // 4))
        self.assertEqual(project._anchored_plugins(), [], "over the limit")
        (self.root / "contract.lock").write_bytes(b"anchor plugins/apim since=1\n\xff\xfe")
        self.assertEqual(project._anchored_plugins(), [], "not UTF-8 text")
        (self.root / "contract.lock").unlink()
        (self.root / "contract.lock").mkdir()
        self.assertEqual(project._anchored_plugins(), [], "a folder")
        (self.root / "contract.lock").rmdir()
        self.assertEqual(project._anchored_plugins(), [], "none")

    def test_a_change_of_the_contract_reads_the_project_again(self):
        self.folder(project)
        before = project.fingerprint()
        self.write("contract.lock", self.LOCK)
        self.assertNotEqual(project.fingerprint(), before)


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Own(Case):
    """The units of this machine are judged for what they restrict (N01 to N03, N05) and never blocked: a personal
    playground with a seed of its own, or whose seed data reads a token, is refused the Cloud Token of a place as a catalog's is."""

    def sandbox(self, change=None, seed=None):
        def more(case):
            folder = fixtures.local(case, "sandbox")
            if change:
                (folder / "compose.yml").write_text(change((folder / "compose.yml").read_text(encoding="utf-8")), encoding="utf-8")
            for name, text in (seed or {}).items():
                (folder / "seed" / name).write_text(text, encoding="utf-8")
        inputs, built = read(self, local=("scratch",), more=more)
        return inputs, built["playgrounds"]["local/sandbox"]

    def test_a_personal_playground_with_the_seeder_of_the_engine_has_no_note_and_can_have_a_token(self):
        inputs, found = self.sandbox()
        self.assertEqual((found["verdict"]["notes"], found["blocked"], places.seed_refused(found)), ([], None, None))
        self.assertEqual(found["verdict"]["state"], "ok")

    def test_a_personal_playground_with_a_seed_of_its_own_is_refused_the_token_and_not_blocked(self):
        inputs, found = self.sandbox(lambda text: text.replace("ghcr.io/gravitee-io-labs/gravitee-lab/seeder", "ghcr.io/someone/seeder")
                                     .replace("build: ../../../../../seeder", "build: ./seed"))
        self.assertEqual(found["verdict"]["notes"], [trust.line("N01")])
        self.assertEqual((found["blocked"], found["verdict"]["state"], found["verdict"]["reasons"]), (None, "ok", []))
        self.assertEqual(places.seed_refused(found), trust.SEED_OWN)
        self.assertEqual(found["verdict"]["surface"]["seed"], "custom")

    def test_a_personal_playground_whose_seed_data_reads_a_token_is_refused_it_and_not_blocked(self):
        inputs, found = self.sandbox(seed={"apim/token.txt": "token=${APIM_TOKEN}\n"})
        self.assertEqual(inputs["seed_references"][found["file"]], ["APIM_TOKEN", "SANDBOX_BACKEND_URL"])
        self.assertEqual((found["verdict"]["notes"], found["blocked"]), ([trust.line("N02")], None))
        self.assertEqual(places.seed_refused(found), trust.SEED_TOKEN)

    def test_a_seed_the_screen_cannot_read_whole_is_a_seed_that_may_read_the_token(self):
        for name, make in (("a link", lambda folder, outside: (folder / "seed" / "apim" / "link").symlink_to(outside)),
                           ("a folder that is a link", lambda folder, outside: (shutil.rmtree(folder / "seed"), (folder / "seed").symlink_to(outside)))):
            with self.subTest(name):
                outside = Path(tempfile.mkdtemp(prefix="gravitee-lab-outside-"))
                self.addCleanup(shutil.rmtree, outside, True)

                def more(case, make=make, outside=outside):
                    folder = fixtures.local(case, "sandbox")
                    make(folder, outside)
                inputs, built = read(self, local=("scratch",), more=more)
                found = built["playgrounds"]["local/sandbox"]
                if name == "a link":
                    self.assertEqual(places.seed_refused(found), None, "a link in it is skipped, as for a catalog: what it leads to is not read")
                else:
                    self.assertEqual((places.seed_refused(found), found["blocked"]), (trust.SEED_TOKEN, None))

    def test_the_services_of_a_personal_unit_are_the_aggregate_s_and_its_alone_configuration_is_only_for_its_notes(self):
        inputs, found = self.sandbox()
        file = found["file"]
        self.assertIn(file, inputs["own_configs"])
        self.assertNotIn(file, inputs["configs"], "what a catalog's unit is run as is not what the person's is")
        self.assertIn("sandbox-seed", inputs["own_configs"][file]["services"])
        self.assertEqual(sorted(inputs["own_configs"]), [".lab/catalogs/local/addons/scratch/compose.yml", file],
                         "an add-on and a playground, not a setup")

    def test_a_personal_setup_is_not_asked_of_compose_and_is_as_before(self):
        def more(case):
            case.write(".lab/catalogs/local/setups/mine.yml", "x-gravitee: {kind: setup, name: Mine, category: Other, description: x}\n"
                       "include:\n  - ../addons/scratch/compose.yml\n")
        inputs, built = read(self, local=("scratch",), more=more)
        mine = built["setups"]["local/mine"]
        self.assertEqual((mine["blocked"], mine["verdict"]["notes"]), (None, []))
        self.assertNotIn(mine["file"], inputs["own_configs"])

    def test_a_personal_unit_that_cannot_be_read_alone_is_judged_as_before_and_never_blocks(self):
        alone = project._alone

        def not_theirs(path, *more):
            return (None, "no such service") if path.startswith(".lab/catalogs/local/") else alone(path, *more)
        with mock.patch.object(project, "_alone", not_theirs):
            inputs, built = read(self, local=("scratch", "sandbox"))
        self.assertEqual((built["addons"]["local/scratch"]["blocked"], built["addons"]["local/scratch"]["verdict"]["notes"]),
                         (None, []))
        self.assertEqual(inputs["own_configs"], {})
        self.assertIsNone(places.seed_refused(built["playgrounds"]["local/sandbox"]))

    def test_the_consoles_of_a_personal_unit_are_all_kept_whatever_its_note_says(self):
        def more(case):
            folder = fixtures.local(case, "scratch")
            text = (folder / "compose.yml").read_text(encoding="utf-8")
            (folder / "compose.yml").write_text(text.replace("    labels:\n", "    labels:\n      io.gravitee.console.far.name: Far\n"
                                                             "      io.gravitee.console.far.url: https://example.com/far\n", 1),
                                                encoding="utf-8")
        inputs, built = read(self, more=more)
        scratch = built["addons"]["local/scratch"]
        self.assertIn(trust.line("N03"), scratch["verdict"]["notes"])
        self.assertEqual([found["label"] for name in scratch["services"] for found in built["services"][name]["consoles"]], ["Far"])


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Staged(Case):
    """A tree that is staged (`.lab/catalogs/.stage-<ns>`) is judged as the project would judge it installed under its name,
    against everything installed, before a single file of it is installed."""

    META = {"shared_settings": [], "requires": {"catalogs": {}}}

    def row(self, units, **more):
        return {"name": "Partner fixture", "description": "", "version": "2.0.0", "lab": 1, "audience": "private", "kind": "git",
                "requires_catalogs": {}, "state": "uptodate", "units": units, "ignored": 0, "error": None, "pending": None,
                "listed": False, "host": "no", **more}

    def stage(self, ns="partner", source="partner", change=None):
        """A copy of a fixture catalog in the staging folder of `ns`, as `install.stage` leaves one."""
        target = self.root / ".lab" / "catalogs" / f".stage-{ns}"
        shutil.copytree(fixtures.CATALOGS / source, target)
        for name, text in (change or {}).items():
            if text is None:
                shutil.rmtree(target / name) if (target / name).is_dir() else (target / name).unlink()
            else:
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                (target / name).write_text(text, encoding="utf-8")
        return target

    def test_the_units_of_a_stage_are_listed_by_their_folders_with_the_file_each_has_in_the_stage(self):
        read(self)
        self.stage(change={"setups/combo.yml": "x-gravitee: {kind: setup, name: Combo, category: Other, description: x}\ninclude: []\n",
                           "addons/odd name/compose.yml": "services: {}\n", "addons/.hidden/compose.yml": "services: {}\n",
                           "playgrounds/empty/README.md": "no compose file\n"})
        folder = ".lab/catalogs/.stage-partner"
        self.assertEqual(project.staged_units("partner"), [
            ("addon", "ledger", f"{folder}/addons/ledger/compose.yml"), ("setup", "combo", f"{folder}/setups/combo.yml")])
        self.assertEqual(project.staged_units("nothing"), [])

    def test_the_stage_is_judged_under_the_name_it_will_have_and_the_project_is_not_changed(self):
        inputs, built = read(self)
        self.stage()
        before = copy.deepcopy({key: value for key, value in inputs.items() if key not in ("realpath", "environ")})
        judged, verdicts = project.judge_staged("partner", self.META, self.row({"addon": ["ledger"], "setup": [], "playground": []}))
        ledger = judged["addon"]["@partner/ledger"]
        self.assertEqual((ledger["file"], ledger["catalog"], ledger["origin"]["id"], ledger["blocked"]),
                         (".lab/catalogs/partner/addons/ledger/compose.yml", "partner", "partner", None))
        self.assertEqual(verdicts[("addon", "@partner/ledger")]["state"], "ok")
        self.assertEqual(verdicts[("addon", "@partner/ledger")]["surface"]["ports"], [8192])
        self.assertIn("echo", judged["addon"], "everything installed is the world it is judged in")
        self.assertEqual({key: value for key, value in project.loaded()[1].items() if key not in ("realpath", "environ")}, before)
        self.assertNotIn("@partner/ledger", project.load()["addons"])
        self.assertFalse(any("stage" in path for path in project.loaded()[1]["raw"]), "nothing of the stage is in the inputs")

    def test_a_staged_unit_that_asks_too_much_is_blocked_before_it_is_installed(self):
        read(self)
        self.stage(change={"addons/ledger/compose.yml": (fixtures.CATALOGS / "partner/addons/ledger/compose.yml").read_text(
            encoding="utf-8").replace("restart: unless-stopped", "privileged: true")})
        judged, verdicts = project.judge_staged("partner", self.META, self.row({"addon": ["ledger"], "setup": [], "playground": []}))
        self.assertEqual(judged["addon"]["@partner/ledger"]["blocked"], {"reasons": ["Runs with full privileges"]})
        self.assertEqual(verdicts[("addon", "@partner/ledger")]["state"], "blocked")

    def test_a_staged_text_the_screen_refuses_is_held_as_it_is_for_an_installed_one(self):
        read(self)
        self.stage(change={"addons/ledger/compose.yml": "include:\n  - /etc/hosts\nservices: {}\n"})
        judged, _ = project.judge_staged("partner", self.META, self.row({"addon": ["ledger"], "setup": [], "playground": []}))
        self.assertEqual(judged["addon"]["@partner/ledger"]["blocked"], {"reasons": ["Includes content from outside the catalog"]})

    def test_an_update_judges_the_new_version_in_place_of_the_installed_one(self):
        read(self, catalogs={"partner": {}})
        self.assertIn("@partner/ledger", project.load()["addons"])
        self.stage(change={"addons/ledger": None, "addons/vault/compose.yml": (fixtures.CATALOGS / "partner/addons/ledger/compose.yml")
                           .read_text(encoding="utf-8").replace("ledger", "vault").replace("8192", "8193").replace("Ledger", "Vault")})
        row = self.row({"addon": ["vault"], "setup": [], "playground": []})
        added, _ = project.judge_staged("partner", self.META, row)
        self.assertEqual(sorted(uid for uid in added["addon"] if "partner" in uid), ["@partner/ledger", "@partner/vault"],
                         "an Add judges it beside what is there")
        replaced, verdicts = project.judge_staged("partner", self.META, row, replacing=True)
        self.assertEqual(sorted(uid for uid in replaced["addon"] if "partner" in uid), ["@partner/vault"])
        self.assertEqual(sorted(key for key in verdicts if "partner" in key[1]), [("addon", "@partner/vault")])

    def test_judge_inputs_is_what_the_state_is_built_from_without_the_rest_of_it(self):
        inputs, built = read(self, catalogs={"partner": {}}, local=("scratch",))
        judged, verdicts = project.judge_inputs(inputs)
        self.assertEqual(sorted(verdicts), sorted((kind, uid) for kind in judged for uid in judged[kind]), "a verdict for each unit")
        for kind in judged:
            self.assertEqual({uid: (unit["blocked"], unit["needs_catalogs"], unit["twin"]) for uid, unit in judged[kind].items()},
                             {uid: (unit["blocked"], unit["needs_catalogs"], unit["twin"]) for uid, unit in built[kind + "s"].items()},
                             "judged as build judges them")
        self.assertTrue(judged["addon"]["future"]["blocked"], "a level this Lab does not read")
        self.assertEqual(verdicts[("addon", "future")]["state"], "blocked")
        self.assertEqual(verdicts[("addon", "local/scratch")]["state"], "ok", "and the person's own never blocks")


def golden(built):
    """What the equivalence gate compares of a project: every service (image, ports, labels, owner, consoles, what
    it waits for), the services each unit owns and the core services it changes, the links, the groups and the products. It
    is what a person sees, whichever way the services were resolved."""
    return {"services": {name: built["services"][name] for name in sorted(built["services"])},
            "units": {f"{kind}:{uid}": {key: unit[key] for key in ("services", "restarts", "apps", "seed") if key in unit}
                      for kind in ("addons", "setups", "playgrounds") for uid, unit in built[kind].items()},
            "links": built["links"], "groups": built["groups"], "products": built["products"]}


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Together(Case):
    """Resolving the units one by one changes nothing a person sees: the services, ports, images, labels and
    owners from the configuration of each unit are those of one configuration of all of them together."""

    maxDiff = None

    def test_the_services_of_the_project_are_those_of_the_aggregate(self):
        inputs, built = read(self, catalogs={"partner": {}}, local=("scratch", "sandbox"))
        files = [unit["file"] for kind in ("addons", "playgrounds") for unit in built[kind].values() if not unit["blocked"]]
        everything = compose.resolved(compose.all_files(files))
        # The same units, judged the same, but their services taken from the one configuration of everything
        with mock.patch.object(project, "_run_as", lambda given, units, core: dict(given["resolved"])):
            together = project.build(dict(copy.deepcopy(inputs), resolved=everything))
        self.assertGreater(len(built["services"]), 20)
        self.assertEqual(golden(built), golden(together))

    def test_each_unit_is_resolved_alone_with_the_core_and_without_the_others(self):
        inputs, built = read(self, catalogs={"partner": {}})
        echo = inputs["configs"][".lab/catalogs/fixture/addons/echo/compose.yml"]
        self.assertIn("fixture-echo", echo["services"])
        self.assertNotIn("partner-ledger", echo["services"])
        self.assertIn("apim-gateway", echo["services"], "with the core")
        self.assertNotIn("fixture-echo", inputs["core_config"]["services"])

    def test_the_aggregate_is_the_core_alone_when_nothing_of_this_machine_is(self):
        with mock.patch.object(compose, "resolved", wraps=compose.resolved) as resolved:
            read(self, catalogs={"partner": {}})
        resolved.assert_called_once()
        self.assertEqual(resolved.call_args[0][0], compose.all_files([]))


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Remembered(Case):
    """What was read of a unit is kept while its files stay as they are: a one-file edit does not read the whole
    catalog again."""

    def reads(self):
        """The units resolved alone by one gather."""
        done = []
        alone = project._alone
        with mock.patch.object(project, "_alone", lambda path, *more: done.append(path) or alone(path, *more)):
            project.gather()
        return sorted(paths.unit_of(path)[1] for path in done)

    def setUp(self):
        read(self, catalogs={"partner": {}}, local=("scratch",))

    def test_a_second_read_asks_compose_nothing_about_a_unit(self):
        self.assertEqual(self.reads(), [])

    def test_an_edit_reads_again_the_unit_and_the_units_that_include_it(self):
        self.write(".lab/catalogs/fixture/addons/echo/compose.yml",
                   (self.root / ".lab/catalogs/fixture/addons/echo/compose.yml").read_text(encoding="utf-8") + "# edited\n")
        self.assertEqual(self.reads(), ["echo", "echo-core", "local/scratch", "pair"],
                         "the unit of this machine that includes it is read again too, for its notes")

    def test_a_setting_of_a_unit_reads_it_again(self):
        self.write(".lab/catalogs/fixture/addons/echo-twin/.env", "FIXTURE_ECHO_TWIN_VERSION=1.35\n")
        self.assertEqual(self.reads(), ["echo-twin"])
        self.write(".env", "FIXTURE_X=1\n")
        self.assertGreater(len(self.reads()), 5, "the root .env is every unit's")

    def test_forgetting_reads_everything_again(self):
        project.forget()
        self.assertGreater(len(self.reads()), 8)


class Fresh(Case):
    """A project with no catalog installed is the core alone, with no problem (Z2)."""

    @unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
    def test_the_core_alone_has_no_unit_and_no_problem(self):
        self.folder(paths, project, compose, envfiles)
        fixtures.core(self)
        fixtures.default(self)
        self.patch(license, "status_of", lambda variable: None)
        built = project.build(project.gather())
        self.assertEqual((built["addons"], built["setups"], built["playgrounds"], built["problems"]), ({}, {}, {}, []))
        self.assertEqual((built["catalog_problems"], built["catalogs"]), ({}, {}))
        self.assertTrue(built["core_loaded"])
        self.assertEqual(sorted(built["services"]), sorted(compose.resolved(compose.all_files([]))))
        self.assertEqual([group["id"] for group in built["groups"]],
                         ["api-management", "access-management", "alert-engine", "databases"])
        self.assertEqual(built["platform"], {"gateways": {"cloud": None}})

    def test_with_no_default_file_the_project_is_what_it_was(self):
        # Before catalogs the units sit in the folders of their kind and are read together, none of them judged
        self.folder(paths, project, compose, envfiles, files={
            CORE: "name: lab\nx-gravitee: {name: Lab, groups: [Core]}\nservices:\n  core:\n    image: busybox\n    labels: "
                  "{io.gravitee.group: Core}\n",
            KAFKA: "x-gravitee: {kind: addon, name: Kafka}\nservices:\n  kafka:\n    image: busybox\n"})
        self.assertIsNone(paths.default_ns())
        with mock.patch.object(compose, "resolved", wraps=compose.resolved) as resolved, \
                mock.patch.object(project, "_alone") as alone:
            inputs = project.gather()
        built = project.build(inputs)
        resolved.assert_called_once_with(compose.all_files([KAFKA]))
        alone.assert_not_called()
        self.assertEqual((inputs["default"], inputs["screened"], inputs["configs"], inputs["catalog_problems"]), (None, {}, {}, {}))
        kafka = built["addons"]["kafka"]
        self.assertEqual((kafka["origin"], kafka["blocked"], kafka["verdict"], kafka["catalog"], kafka["needs_catalogs"]),
                         (None, None, None, None, []))
        self.assertEqual((built["catalogs"], built["catalog_problems"], built["problems"]), ({}, {}, []))
        self.assertEqual(built["platform"], {"gateways": {"cloud": None}})


class Fingerprints(Case):
    """What tells that the project must be read again."""

    def project(self, **how):
        fixtures.project(self, paths, project, **how)
        fixtures.install(self, "partner")
        return self.root

    def changes(self, change):
        before = project.fingerprint()
        change()
        return project.fingerprint() != before

    def test_a_file_of_a_catalog_changes_it(self):
        root = self.project()
        echo = root / ".lab/catalogs/fixture/addons/echo/compose.yml"
        self.assertTrue(self.changes(lambda: echo.write_text(echo.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")))
        guide = root / ".lab/catalogs/fixture/playgrounds/shop/guide.md"
        self.assertTrue(self.changes(lambda: guide.write_text("# Shop\n", encoding="utf-8")))
        self.assertTrue(self.changes(lambda: self.write(".lab/catalogs/fixture/addons/echo/.env", "A=1\n")))
        self.assertTrue(self.changes(lambda: self.write(".lab/catalogs/partner/addons/ledger/.env", "A=1\n")))
        self.assertTrue(self.changes(lambda: self.write(".lab/catalogs/fixture/addons/new/compose.yml", "services: {}\n")))
        self.assertFalse(self.changes(lambda: self.write(".lab/catalogs/fixture/addons/echo/notes.md", "Not read")))

    def test_what_says_which_catalogs_there_are_changes_it(self):
        root = self.project()
        for name in (".lab/catalogs.json", ".lab/catalogs.lock", "catalogs.default.json", ".lab/layout.json"):
            with self.subTest(name=name):
                file = root / name
                self.assertTrue(self.changes(lambda: self.write(name, (file.read_text(encoding="utf-8") if file.is_file() else "{}") + "\n ")))

    def test_the_manifest_and_the_catalog_file_of_an_install_change_it(self):
        self.project()
        self.assertTrue(self.changes(lambda: self.write(".lab/catalogs/partner/.installed.json", "{}")))
        self.assertTrue(self.changes(lambda: self.write(".lab/catalogs/partner/catalog.yml", "x-gravitee-catalog: {}\n")))

    def test_a_catalog_that_comes_or_goes_changes_it(self):
        self.project()
        self.assertTrue(self.changes(lambda: fixtures.install(self, "lab-new")))
        self.assertTrue(self.changes(lambda: shutil.rmtree(self.root / ".lab/catalogs/lab-new")))

    def test_a_staging_folder_is_not_part_of_it(self):
        self.project()
        self.assertFalse(self.changes(lambda: self.write(".lab/catalogs/.stage-new/addons/x/compose.yml", "services: {}\n")))

    def test_it_costs_little_for_a_hundred_units(self):
        files = {f"addons/unit-{number:03}/compose.yml": "services: {}\n" for number in range(100)}
        fixtures.project(self, paths, project, files=files)
        self.write(".lab/catalogs/fixture/addons/unit-000/.env", "A=1\n")
        self.assertGreater(len(paths.files()), 100)
        project.fingerprint()
        best = min(timed(project.fingerprint) for _ in range(30))
        self.assertLess(best, 0.015, f"{best * 1000:.1f} ms")


def timed(function):
    started = time.perf_counter()
    function()
    return time.perf_counter() - started


class Exclusive(Case):
    """The project's one lock, held by whoever changes the catalogs."""

    def test_whoever_holds_it_can_load_and_forget_and_no_one_else_loads_meanwhile(self):
        self.patch(project, "_cache", {"key": None, "project": None, "inputs": None})
        loads = []
        self.patch(project, "gather", lambda: loads.append("gather") or {})
        self.patch(project, "build", lambda inputs: {"built": len(loads)})
        self.patch(project, "fingerprint", lambda: "same")
        done = threading.Event()
        with project.exclusive():
            project.forget()
            self.assertEqual(project.load(), {"built": 1})
            project.forget()
            other = threading.Thread(target=lambda: (project.load(), done.set()), daemon=True)
            other.start()
            self.assertFalse(done.wait(0.3), "another thread waits for the lock")
        self.assertTrue(done.wait(5))
        self.assertEqual(loads, ["gather", "gather"])



# -- The project of a machine with catalogs, built from what gather() would read (no disk, no Compose) -----------------

PLATFORM = {
    "name": "gravitee",
    "x-gravitee": {"name": "Gravitee Platform", "groups": ["API Management"], "categories": ["Other"]},
    "services": {"apim-gateway": service("graviteeio/apim-gateway:4.12", "Gateway", container_name="gio-apim-gateway",
                                         ports=[port(8082)], labels={"io.gravitee.group": "API Management"})},
}


def member(ns, kind, name, services=(), include=(), says=None):
    """A unit of a catalog as Compose reads it written: (its file, its raw configuration). `include`: the units it includes,
    (ns, kind, name) each. The services are of the house style: loopback ports, the internal network, a container name."""
    meta = {"kind": kind, "name": name.title(), "category": "Other", "description": "Added", **(says or {})}
    defined = {one: service("busybox", one, container_name=f"gio-{one}", networks={"internal": None}) for one in services}
    return fixtures.unit_file(ns, kind, name), {"x-gravitee": meta, "services": defined, "_include": list(include)}


def catalog_inputs(units, audience=None, catalogs=None, **more):
    """What gather() reads of a machine with catalogs, made by hand: the core, these units (member()), the catalogs as the
    registry says them (in the order of precedence) and, for each unit, its configuration with the core and what it includes,
    which is what Compose would answer. `audience`: {namespace: public or private}; `catalogs`: what a namespace says
    beyond that (listed, host, lab, version, kind, name)."""
    files = {file: raw for file, raw in units}
    raw = {project.CORE_FILE: copy.deepcopy(PLATFORM)}
    includes = {project.CORE_FILE: []}
    for file, config in files.items():
        raw[file] = {key: value for key, value in copy.deepcopy(config).items() if key != "_include"}
        includes[file] = [fixtures.unit_file(*one) for one in config["_include"]]

    def with_included(file, seen=()):
        found = dict(files[file]["services"]) if file in files else {}
        for target in includes.get(file, []):
            found.update(with_included(target, seen + (file,)) if target not in seen else {})
        return found
    configs = {file: {"name": "gravitee", "networks": {"internal": {}, "edge": {}},
                      "services": {**copy.deepcopy(PLATFORM["services"]), **copy.deepcopy(with_included(file))}}
               for file in files}
    spaces = list(dict.fromkeys(["fixture", "local", *(file.split("/")[2] for file in files), *(catalogs or {})]))
    rows = {ns: {"name": ns.title(), "kind": "machine" if ns == "local" else "folder", "version": "1.0.0", "lab": 1,
                 "audience": "private" if ns == "local" else (audience or {}).get(ns, "public"),
                 "requires_catalogs": {}, "state": "linked", "listed": ns == "fixture", "host": "listed" if ns == "fixture" else "no",
                 **(catalogs or {}).get(ns, {})} for ns in spaces}
    return {"raw": raw, "includes": includes, "variables": {}, "resolved": {}, "environ": {}, "env": {}, "license_usable": True,
            "guides": {}, "seeds": {}, "uses": {}, "problems": [], "default": "fixture", "catalogs": rows,
            "configs": configs, "core_config": {"name": "gravitee", "networks": {"internal": {}, "edge": {}},
                                                "services": copy.deepcopy(PLATFORM["services"])},
            "contract": {"contract": 1, "contract_min": 1, "seeder_image": ""}, "realpath": lambda path: path,
            "reach": {file: {"files": [file], "targets": includes[file], "missing": [], "unsafe": []} for file in files},
            **more}


class Catalog(Case):
    """project.build() on a machine with catalogs: who keeps a name, what a unit that brings a blocked unit is, how the audience
    rules, the order of precedence, and what is the catalog's problem. No disk, no Docker."""

    def setUp(self):
        self.patch(paths, "default_ns", return_value="fixture")

    def build(self, units, **more):
        return project.build(catalog_inputs(units, **more))

    def reasons(self, built, kind, uid):
        return (built[kind + "s"][uid]["blocked"] or {}).get("reasons")

    def test_every_catalogs_units_say_their_origin_once_there_are_two_and_this_machines_say_none(self):
        built = self.build([member("fixture", "addon", "one", ["one"]), member("first", "addon", "two", ["two"]),
                            member("local", "addon", "mine", ["mine"])],
                           catalogs={"fixture": {"version": "3.1.0"}, "first": {"version": "2.0.0", "name": "First hub"}})
        self.assertEqual({uid: unit["origin"] for uid, unit in built["addons"].items()},
                         {"one": {"id": "fixture", "name": "Fixture", "version": "3.1.0"},
                          "@first/two": {"id": "first", "name": "First hub", "version": "2.0.0"}, "local/mine": None})
        alone = self.build([member("fixture", "addon", "one", ["one"])], catalogs={"fixture": {"version": "3.1.0"}})
        self.assertIsNone(alone["addons"]["one"]["origin"], "one catalog: nothing to tell apart")
        self.assertEqual(built["catalogs"]["fixture"]["id"], "fixture", "and the catalog itself is a row of its own")

    def test_a_bare_key_of_conflicts_names_the_add_on_of_the_same_catalog_else_the_default_one_and_the_layers_find_it(self):
        twin = member("acme-hub", "addon", "twin", ["twin"], says={"conflicts": {
            "echo": "both take 8191", "kafka": "same ports", "@fixture/jade": "x", "nothing": "", "local/mine": "y"}})
        mine = member("local", "addon", "mine", ["mine"], says={"conflicts": {"jade": "z", "twin": "w"}})
        units = [twin, mine, member("acme-hub", "addon", "echo", ["echo"]), member("fixture", "addon", "kafka", ["kafka"]),
                 member("fixture", "addon", "jade", ["jade"])]
        built = self.build(units, catalogs={"acme-hub": {"name": "Acme Hub"}})
        self.assertEqual(built["addons"]["@acme-hub/twin"]["conflicts"], {
            "@acme-hub/echo": "both take 8191", "kafka": "same ports", "jade": "x", "nothing": "", "local/mine": "y"})
        self.assertEqual(built["addons"]["local/mine"]["conflicts"], {"jade": "z", "twin": "w"},
                         "a bare key of this machine's is the default catalog's: a unit of another catalog is named in full")
        self.assertEqual(built["addons"]["kafka"]["conflicts"], {})
        ids = sorted(built["addons"])
        self.assertEqual([(clash["a"], clash["b"], clash["reason"]) for clash in layers.conflicts(built, ids)], [
            ("@acme-hub/echo", "@acme-hub/twin", "both take 8191"), ("@acme-hub/twin", "jade", "x"),
            ("@acme-hub/twin", "kafka", "same ports"), ("@acme-hub/twin", "local/mine", "y"), ("jade", "local/mine", "z")])

    def test_two_keys_of_conflicts_that_name_one_add_on_keep_the_first_reason_that_says_something(self):
        twin = member("acme-hub", "addon", "twin", ["twin"], says={"conflicts": {"echo": "", "@acme-hub/echo": "both take 8191",
                                                                                "@fixture/kafka": "a", "kafka": "b"}})
        built = self.build([twin, member("acme-hub", "addon", "echo", ["echo"]), member("fixture", "addon", "kafka", ["kafka"])],
                           catalogs={"acme-hub": {"name": "Acme Hub"}})
        self.assertEqual(built["addons"]["@acme-hub/twin"]["conflicts"], {"@acme-hub/echo": "both take 8191", "kafka": "a"})

    def test_conflict_id_is_the_one_definition_of_what_a_key_names(self):
        known = {"jade", "kafka", "@acme-hub/ledger", "@acme-hub/kafka", "local/kafka", "local/mine"}
        for key, catalog, wanted in (("kafka", "acme-hub", "@acme-hub/kafka"), ("jade", "acme-hub", "jade"),
                                     ("ledger", "acme-hub", "@acme-hub/ledger"), ("ledger", "fixture", None), ("kafka", "fixture", "kafka"),
                                     ("@fixture/kafka", "acme-hub", "kafka"), ("@acme-hub/kafka", "fixture", "@acme-hub/kafka"),
                                     ("kafka", "local", "local/kafka"), ("mine", "local", "local/mine"), ("mine", "fixture", None),
                                     ("local/mine", "acme-hub", "local/mine"), ("kafka", None, "kafka"), (None, "fixture", None),
                                     (7, "fixture", None), ("", "fixture", None), ("@/kafka", "fixture", None)):
            with self.subTest(key=key, catalog=catalog):
                self.assertEqual(project.conflict_id(key, catalog, known), wanted)

    def test_the_path_a_person_reads_is_the_one_in_the_catalog_that_holds_the_unit_this_machines_included(self):
        built = self.build([member("local", "addon", "mine", ["mine-svc"]), member("local", "playground", "play", ["play-app"]),
                            member("first", "addon", "two", ["two-svc"]), member("fixture", "addon", "one", ["one-svc"])])
        shown = {(kind, uid): unit["display_file"] for kind in ("addons", "playgrounds") for uid, unit in built[kind].items()}
        self.assertEqual(shown, {("addons", "local/mine"): "addons/mine/compose.yml",
                                 ("playgrounds", "local/play"): "playgrounds/play/compose.yml",
                                 ("addons", "@first/two"): "addons/two/compose.yml", ("addons", "one"): "addons/one/compose.yml"})
        self.assertEqual(built["addons"]["local/mine"]["file"], ".lab/catalogs/local/addons/mine/compose.yml", "the file is its own")

    def test_a_unit_of_the_layout_before_catalogs_says_its_file(self):
        for file in ("addons/kafka/compose.yml", "addons/local/whoami/compose.yml", "setups/local/mine.yml"):
            with self.subTest(file=file):
                self.assertEqual(project._placed("x", file, {"default": None})["display_file"], file)
        self.assertEqual(project._placed("local/x", ".lab/catalogs/local/addons/x/compose.yml", {"default": "fixture"})["display_file"],
                         "addons/x/compose.yml")

    def test_a_unit_of_a_catalog_added_later_loses_a_name_the_earlier_one_holds_and_the_earlier_is_untouched(self):
        built = self.build([member("fixture", "addon", "one", ["shared"]), member("first", "addon", "two", ["shared"]),
                            member("second", "addon", "three", ["shared"])])
        self.assertEqual(self.reasons(built, "addon", "@first/two"), ["Same name as One"])
        self.assertEqual(self.reasons(built, "addon", "@second/three"), ["Same name as One"])
        self.assertIsNone(self.reasons(built, "addon", "one"))
        self.assertEqual(built["services"]["shared"]["owner"]["id"], "one")

    def test_the_catalog_added_first_keeps_the_name_when_two_later_ones_want_it(self):
        units = [member("first", "addon", "two", ["shared"]), member("second", "addon", "three", ["shared"])]
        built = self.build(units)
        self.assertEqual((self.reasons(built, "addon", "@first/two"), self.reasons(built, "addon", "@second/three")),
                         (None, ["Same name as Two"]))
        # Added in the other order, the other keeps it: the order is the registry's, not the alphabet's
        given = catalog_inputs(units)
        given["catalogs"] = {ns: given["catalogs"][ns] for ns in ("fixture", "local", "second", "first")}
        built = project.build(given)
        self.assertEqual((self.reasons(built, "addon", "@first/two"), self.reasons(built, "addon", "@second/three")),
                         (["Same name as Three"], None))

    def test_a_unit_that_is_blocked_for_another_reason_keeps_no_name(self):
        built = self.build([member("first", "addon", "two", ["shared"], says={"requires": {"lab": 99}}),
                            member("second", "addon", "three", ["shared"])])
        self.assertEqual(self.reasons(built, "addon", "@first/two"), ["Needs a newer Gravitee Lab"])
        self.assertIsNone(self.reasons(built, "addon", "@second/three"))
        self.assertEqual(built["services"]["shared"]["owner"]["id"], "@second/three")

    def test_the_units_of_this_machine_keep_the_names_they_took_before_the_other_catalogs(self):
        built = self.build([member("local", "addon", "mine", ["shared"]), member("first", "addon", "two", ["shared"])])
        self.assertEqual((self.reasons(built, "addon", "local/mine"), self.reasons(built, "addon", "@first/two")),
                         (None, ["Same name as Mine"]))

    def test_a_unit_that_brings_a_blocked_unit_is_blocked_and_brings_nothing_either(self):
        built = self.build([
            member("first", "addon", "old", ["old-svc"], says={"requires": {"lab": 99}}),
            member("first", "addon", "user", ["user-svc"], include=[("first", "addon", "old")]),
            member("local", "addon", "mine", ["mine-svc"], include=[("first", "addon", "old")]),
            member("local", "playground", "play", ["play-app"], include=[("local", "addon", "mine")])])
        why = ["Needs Old, which is blocked"]
        self.assertEqual([self.reasons(built, "addon", "@first/user"), self.reasons(built, "addon", "local/mine"),
                          self.reasons(built, "playground", "local/play")], [why, why, why])
        self.assertEqual({name for name in built["services"] if name != "apim-gateway"}, set())
        self.assertEqual(built["addons"]["@first/user"]["services"], [])
        self.assertEqual(built["playgrounds"]["local/play"]["apps"], [])

    def test_a_public_catalog_does_not_depend_on_a_private_one_but_a_private_one_may_depend_on_a_public_one(self):
        units = [member("open", "addon", "tool", ["tool"]), member("closed", "addon", "inner", ["inner"]),
                 member("open", "addon", "wants", ["wants"], include=[("closed", "addon", "inner")]),
                 member("closed", "addon", "uses", ["uses"], include=[("open", "addon", "tool")])]
        declared = {"open": {"requires_catalogs": {"closed": {}}}, "closed": {"requires_catalogs": {"open": {}}}}
        built = self.build(units, audience={"open": "public", "closed": "private"}, catalogs=declared)
        self.assertEqual(self.reasons(built, "addon", "@open/wants"), ["Depends on private content"])
        self.assertIsNone(self.reasons(built, "addon", "@closed/uses"))
        self.assertIsNone(self.reasons(built, "addon", "@open/tool"))
        # A unit of this machine is private: it may depend on anything
        built = self.build(units + [member("local", "addon", "mine", ["mine"], include=[("closed", "addon", "inner")])],
                           audience={"open": "public", "closed": "private"}, catalogs=declared)
        self.assertIsNone(self.reasons(built, "addon", "local/mine"))

    def test_an_audience_that_is_not_known_is_private(self):
        built = self.build([member("odd", "addon", "x", ["x"]), member("fixture", "addon", "y", ["y"], include=[("odd", "addon", "x")])],
                           audience={"odd": "weird"}, catalogs={"fixture": {"requires_catalogs": {"odd": {}}}})
        self.assertEqual(self.reasons(built, "addon", "y"), ["Depends on private content"])

    def test_a_unit_of_a_catalog_is_blocked_while_the_core_cannot_be_read_and_one_of_this_machine_is_not(self):
        # Nothing to compare it with: it is not let through, and it is judged again when the core can be read
        units = [member("first", "addon", "two", ["two"]), member("local", "addon", "mine", ["mine"])]
        built = project.build(catalog_inputs(units, core_config=None))
        self.assertEqual(self.reasons(built, "addon", "@first/two"), ["Cannot be read: the platform cannot be read"])
        self.assertEqual(built["addons"]["@first/two"]["services"], [])
        self.assertIsNone(self.reasons(built, "addon", "local/mine"), "the person's own is not judged")
        built = project.build(catalog_inputs(units, core_config=None, core_error="docker did not answer within 15 s"))
        self.assertEqual(self.reasons(built, "addon", "@first/two"), ["Cannot be read: docker did not answer within 15 s"])
        # A privileged unit is not let through by a configuration of its own that was kept from before
        given = catalog_inputs([member("first", "addon", "two", ["two"])], core_config=None)
        given["configs"][fixtures.unit_file("first", "addon", "two")]["services"]["two"]["privileged"] = True
        self.assertEqual(self.reasons(project.build(given), "addon", "@first/two"), ["Cannot be read: the platform cannot be read"])

    def test_a_unit_that_misses_a_catalog_is_not_judged_and_keeps_its_services(self):
        units = [member("first", "addon", "two", ["two"], says={"requires": {"lab": 99}})]
        given = catalog_inputs(units)
        file = units[0][0]
        del given["configs"][file]   # it was not resolved: Compose would not read it without the catalog
        given["reach"][file]["missing"] = [".lab/catalogs/gone/addons/x/compose.yml"]
        given["reach"][file]["targets"] = [".lab/catalogs/gone/addons/x/compose.yml"]
        given["catalogs"]["first"]["requires_catalogs"] = {"gone": {"name": "Gone", "url": None, "min": None}}
        built = project.build(given)
        two = built["addons"]["@first/two"]
        self.assertEqual([need["reason"] for need in two["needs_catalogs"]], ["missing"])
        self.assertIsNone(two["blocked"])
        self.assertEqual((two["compat"], two["services"]), ("new", ["two"]))
        self.assertEqual(built["platform"], {"gateways": {"cloud": None}})

    def test_a_unit_that_needs_a_newer_catalog_and_loads_is_judged_all_the_same(self):
        units = [member("first", "addon", "two", ["two"], says={"requires": {"lab": 99}})]
        given = catalog_inputs(units, catalogs={"first": {"requires_catalogs": {"newer": {"name": "Newer", "url": None, "min": "2.0.0"}}},
                                                "newer": {"version": "1.0.0"}})
        given["reach"][units[0][0]]["targets"] = [".lab/catalogs/newer/addons/x/compose.yml"]
        two = project.build(given)["addons"]["@first/two"]
        self.assertEqual([need["reason"] for need in two["needs_catalogs"]], ["old"])
        self.assertEqual(two["blocked"], {"reasons": ["Needs a newer Gravitee Lab"]})

    def test_the_problems_of_a_catalog_file_are_its_catalog_s_and_those_of_this_machine_s_are_the_banner_s(self):
        mine = member("local", "addon", "mine", ["mine"])
        theirs = member("first", "addon", "two", ["two"])
        mine[1]["x-gravitee"]["requires"] = "no"
        theirs[1]["x-gravitee"]["requires"] = "no"
        built = self.build([mine, theirs], catalog_problems={"first": [{"file": theirs[0], "display": "addons/two/compose.yml",
                                                                         "message": "Cannot be read: x"}]})
        self.assertEqual([problem["file"] for problem in built["problems"]], [mine[0]])
        self.assertEqual([(one["display"], one["message"]) for one in built["catalog_problems"]["first"]],
                         [("addons/two/compose.yml", "Cannot be read: x"), ("addons/two/compose.yml",
                                                                             "x-gravitee.requires must be a mapping")])
        self.assertEqual(built["catalogs"]["first"]["problems"], built["catalog_problems"]["first"])

    def test_a_console_that_is_not_on_this_machine_is_not_offered_and_the_others_are(self):
        units = [member("first", "addon", "two", ["two"]), member("local", "addon", "mine", ["mine"])]
        given = catalog_inputs(units)
        for file in (units[0][0], units[1][0]):
            for where in (given["raw"][file], given["configs"][file]):
                service_ = where["services"][file.split("/")[-2]]
                service_["labels"].update({"io.gravitee.console.far.name": "Far", "io.gravitee.console.far.url": "https://example.com/far",
                                           "io.gravitee.console.near.name": "Near", "io.gravitee.console.near.url": "http://localhost:9999"})
        built = project.build(given)
        self.assertEqual([found["label"] for found in built["services"]["two"]["consoles"]], ["Near"])
        self.assertIn("Some of its addresses are not on this machine, so they are not opened",
                      built["addons"]["@first/two"]["verdict"]["notes"])
        self.assertIsNone(built["addons"]["@first/two"]["blocked"], "it is a restriction, not a refusal")
        self.assertEqual([link["url"] for link in built["links"] if link["service"] == "two"], ["http://localhost:9999"])
        # What belongs to this machine is the person's own: nothing of it is held back
        self.assertEqual(sorted(found["label"] for found in built["services"]["mine"]["consoles"]), ["Far", "Near"])

    def test_a_service_comes_from_the_configuration_of_the_unit_that_defines_it(self):
        units = [member("first", "addon", "two", ["two"])]
        given = catalog_inputs(units)
        given["configs"][units[0][0]]["services"]["two"]["image"] = "busybox:resolved"
        self.assertEqual(project.build(given)["services"]["two"]["image"], "busybox:resolved")

    def test_what_a_unit_adds_to_a_core_service_is_put_together_for_the_pages(self):
        # The gateway waits for what two add-ons add to it, as the one configuration of everything had it
        units = [member("first", "addon", "one", ["one-svc"]), member("first", "addon", "two", ["two-svc"])]
        given = catalog_inputs(units)
        for name, wait in (("one", "one-svc"), ("two", "two-svc")):
            given["configs"][fixtures.unit_file("first", "addon", name)]["services"]["apim-gateway"]["depends_on"] = {wait: {}}
        self.assertEqual(project.build(given)["services"]["apim-gateway"]["depends_on"], [])
        given["resolved"] = {"apim-gateway": copy.deepcopy(PLATFORM["services"]["apim-gateway"])}
        self.assertEqual(project.build(given)["services"]["apim-gateway"]["depends_on"], ["one-svc", "two-svc"])

    def overlay(self, units, **labels):
        """catalog_inputs whose aggregate holds the gateway, and whose first unit sets what it waits for and these labels on it."""
        given = catalog_inputs(units)
        given["resolved"] = {"apim-gateway": copy.deepcopy(PLATFORM["services"]["apim-gateway"])}
        gateway = given["configs"][units[0][0]]["services"]["apim-gateway"]
        gateway["depends_on"] = {"one-svc": {}, "ghost": {}}
        gateway["labels"] = {**gateway["labels"], **labels}
        return given

    PHISH = {"io.gravitee.console.evil.name": "Sign in again", "io.gravitee.console.evil.url": "https://phish.example/login"}

    def test_a_unit_that_cannot_start_adds_nothing_to_a_core_service_and_a_link_it_chose_is_not_opened(self):
        before = project.build(catalog_inputs([member("first", "addon", "one", ["one-svc"])]))
        units = [member("first", "addon", "one", ["one-svc"], says={"requires": {"lab": 99}})]
        built = project.build(self.overlay(units, **self.PHISH))
        self.assertTrue(built["addons"]["@first/one"]["blocked"])
        gateway = built["services"]["apim-gateway"]
        self.assertEqual((gateway["depends_on"], gateway["consoles"], built["links"]), ([], [], []))
        self.assertEqual(built["services"]["apim-gateway"], before["services"]["apim-gateway"], "as if the unit was not there")
        self.assertNotIn("one-svc", built["services"])

    def test_what_a_unit_of_a_catalog_adds_to_a_core_service_is_a_console_on_this_machine_and_a_service_of_the_project(self):
        near = {"io.gravitee.console.near.name": "Near", "io.gravitee.console.near.url": "http://localhost:9999"}
        built = project.build(self.overlay([member("first", "addon", "one", ["one-svc"])], **self.PHISH, **near,
                                           **{"io.gravitee.role": "edge", "io.gravitee.license": "required",
                                              "io.gravitee.name": "Renamed"}))
        gateway = built["services"]["apim-gateway"]
        self.assertEqual(gateway["depends_on"], ["one-svc"], "ghost is no service of the project")
        self.assertEqual([found["label"] for found in gateway["consoles"]], ["Near"])
        self.assertEqual([link["url"] for link in built["links"] if link["service"] == "apim-gateway"], ["http://localhost:9999"])
        self.assertEqual((gateway["role"], gateway["needs_license"], gateway["label"]), (None, False, "Gateway"),
                         "its labels are the platform's, but for the consoles")
        one = built["addons"]["@first/one"]
        self.assertIn("Some of its addresses are not on this machine, so they are not opened", one["verdict"]["notes"])
        self.assertIsNone(one["blocked"], "a restriction, not a refusal")

    def test_a_console_the_core_service_already_has_is_not_the_units_to_leave_out(self):
        built = project.build(self.overlay([member("first", "addon", "one", ["one-svc"])]))
        self.assertEqual(built["addons"]["@first/one"]["verdict"]["notes"], [])

    def test_the_labels_a_unit_of_this_machine_adds_are_all_kept(self):
        given = self.overlay([member("local", "addon", "mine", ["one-svc"])], **self.PHISH)
        built = project.build(given)
        self.assertEqual([found["label"] for found in built["services"]["apim-gateway"]["consoles"]], ["Sign in again"])
        self.assertEqual(built["services"]["apim-gateway"]["depends_on"], ["ghost", "one-svc"])

    def test_a_machine_with_catalogs_and_no_other_input_still_builds(self):
        # What a test that does not know the catalogs gives: the units are listed, and none can be judged without a core
        file = fixtures.unit_file("first", "addon", "two")
        raw = {project.CORE_FILE: copy.deepcopy(PLATFORM), file: {"x-gravitee": {"kind": "addon"}, "services": {"two": {}}}}
        built = project.build({"raw": raw, "includes": {}, "variables": {}, "resolved": {}, "environ": {}, "env": {},
                               "guides": {}, "problems": [], "license_usable": True})
        two = built["addons"]["@first/two"]
        self.assertEqual((two["origin"], two["catalog"], two["needs_catalogs"]),
                         ({"id": "first", "name": "first", "version": None}, "first", []))
        self.assertEqual(two["blocked"], {"reasons": ["Cannot be read: the platform cannot be read"]}, "no core was read")

    def test_a_stub_is_listed_with_the_folder_name_and_its_reasons(self):
        file = fixtures.unit_file("first", "playground", "bad")
        given = catalog_inputs([member("first", "addon", "two", ["two"])], screened={file: ["Reads files outside its folder"]},
                               unreadable={fixtures.unit_file("first", "setup", "worse"): "not UTF-8 text"})
        built = project.build(given)
        self.assertEqual((built["playgrounds"]["@first/bad"]["name"], self.reasons(built, "playground", "@first/bad")),
                         ("bad", ["Reads files outside its folder"]))
        self.assertEqual(self.reasons(built, "setup", "@first/worse"), ["Cannot be read: not UTF-8 text"])
        self.assertEqual(built["playgrounds"]["@first/bad"]["services"], [])
        self.assertEqual(built["catalogs"]["first"]["counts"], {"playground": 1, "setup": 1, "addon": 1})

    def test_the_inputs_are_left_as_they_were(self):
        given = catalog_inputs([member("first", "addon", "two", ["two"]), member("local", "addon", "mine", ["mine"])])
        before = copy.deepcopy({key: value for key, value in given.items() if key != "realpath"})
        project.build(given)
        self.assertEqual({key: value for key, value in given.items() if key != "realpath"}, before)



class Reading(Case):
    """What is read of a catalog's files is read as hostile input: a plain file, at most a megabyte, as UTF-8 text, never through
    a link, and never a file that could take for ever to read."""

    def setUp(self):
        self.folder(paths, project, compose, envfiles, files={"plain.yml": "services: {}\n", "big.yml": "x" * ((1 << 20) + 1),
                                           "exact.yml": "x" * (1 << 20), "text.yml": "caf\u00e9\n"})
        (self.root / "bytes.yml").write_bytes(b"\xff\xfe not text")
        os.symlink(self.root / "plain.yml", self.root / "link.yml")
        os.symlink("nothing.yml", self.root / "dangling.yml")
        os.mkfifo(self.root / "pipe.yml")
        (self.root / "folder.yml").mkdir()

    def test_a_plain_file_is_read_whole_and_the_rest_says_why_not(self):
        cases = {"plain.yml": ("services: {}\n", None), "text.yml": ("caf\u00e9\n", None), "exact.yml": ("x" * (1 << 20), None),
                 "big.yml": (None, "large"), "bytes.yml": (None, "unreadable"), "link.yml": (None, "link"),
                 "dangling.yml": (None, "link"), "pipe.yml": (None, "link"), "folder.yml": (None, "link"),
                 "absent.yml": (None, "missing")}
        for name, wanted in cases.items():
            with self.subTest(name=name):
                self.assertEqual(project._read(name), wanted)

    def test_what_is_not_text_may_be_read_all_the_same_when_it_is_data(self):
        self.assertEqual(project._read("bytes.yml", strict=False), ("\ufffd\ufffd not text", None))
        self.assertEqual(project._read("big.yml", limit=2 << 20, strict=False)[1], None)

    def test_a_file_that_is_read_is_opened_without_following_a_link_put_there_after_the_look(self):
        real = os.lstat
        swapped = []

        def lstat_then_swap(path, *more, **how):
            found = real(path, *more, **how)
            if str(path).endswith("plain.yml") and not swapped:   # a regular file when it is looked at, a link when opened
                swapped.append(path)
                os.unlink(path)
                os.symlink(self.root / "text.yml", path)
            return found
        with mock.patch.object(os, "lstat", lstat_then_swap):
            self.assertEqual(project._read("plain.yml"), (None, "link"))

    def test_the_screen_refuses_a_link_and_a_large_file_with_the_line_of_a_file_outside_its_folder(self):
        outside = "Reads files outside its folder"
        self.assertEqual(project._screen("link.yml"), (None, [outside], None))
        self.assertEqual(project._screen("big.yml"), (None, [outside], None))
        self.assertEqual(project._screen("bytes.yml"), (None, [], "not UTF-8 text"))
        self.assertEqual(project._screen("absent.yml"), (None, [], "the file is gone"))
        self.assertEqual(project._screen("plain.yml"), ("services: {}\n", [], None))
        self.write("env.yml", "services:\n  a:\n    env_file: /dev/zero\n")
        self.assertEqual(project._screen("env.yml")[1], [outside])

    def test_a_seed_is_read_for_the_settings_in_it_and_held_to_the_size(self):
        self.write("seed/a.yaml", "x: ${ONE}\n")
        self.write("seed/deep/b.json", '{"x": "${TWO}"}')
        self.write("seed/.hidden", "${NOT}")
        os.symlink(self.root / "plain.yml", self.root / "seed/link.yaml")
        os.symlink(self.root, self.root / "seed/loop")
        texts, reasons = project._seed_scan("seed")
        self.assertEqual((sorted(texts), reasons), (['x: ${ONE}\n', '{"x": "${TWO}"}'], []))
        (self.root / "seed/big.yaml").write_text("x" * ((1 << 20) + 1), encoding="utf-8")
        self.assertEqual(project._seed_scan("seed")[1], ["Reads files outside its folder"])

    def test_a_seed_with_more_files_or_more_depth_than_the_screen_reads_is_refused(self):
        with mock.patch.object(project, "SEED_FILES", 2):
            for number in range(3):
                self.write(f"many/{number}.yaml", "x")
            self.assertEqual(project._seed_scan("many")[1], ["Reads files outside its folder"])
        with mock.patch.object(project, "SEED_BYTES", 2):
            self.assertEqual(project._seed_scan("many")[1], ["Reads files outside its folder"])
        with mock.patch.object(project, "SEED_DEPTH", 1):
            self.write("deep/a/b/c.yaml", "x")
            self.assertEqual(project._seed_scan("deep")[1], ["Reads files outside its folder"])

    UNIT = ".lab/catalogs/xx/addons/{}/compose.yml"

    def test_the_closure_of_a_unit_is_followed_and_what_it_reaches_that_is_not_safe_is_named(self):
        a, b, c, link = (self.UNIT.format(name) for name in ("a", "b", "c", "link"))
        for name, text in ((a, "include:\n  - ../b/compose.yml\n  - missing/compose.yml\n  - ../link/compose.yml\n"),
                           (b, "include:\n  - ../c/compose.yml\n"), (c, "services: {}\n")):
            self.write(name, text)
        os.makedirs(self.root / link.rsplit("/", 1)[0])
        os.symlink(self.root / "plain.yml", self.root / link)
        found = project._reach(a, set(), {b, c})
        self.assertEqual(found["files"], [a, b, c])
        self.assertEqual(found["unsafe"], [link])
        self.assertIn(c, found["targets"])
        self.assertEqual(project._reach(a, {c}, {b, c})["unsafe"], [link, c], "a unit that the screen refused")
        self.assertEqual(project.held_reasons(found, set()), ["Reads files outside its folder"])
        self.patch(paths, "default_ns", return_value="fixture")
        self.assertEqual(project.held_reasons(project._reach(a, {c}, {b, c}), {c}),
                         ["Reads files outside its folder", "Needs c, which is blocked"])

    def test_a_file_that_is_no_unit_is_never_followed_whatever_it_says_for_compose_reads_what_its_include_says(self):
        a, more = self.UNIT.format("a"), ".lab/catalogs/xx/addons/a/more.yml"
        self.write(a, "include:\n  - more.yml\n")
        self.write(more, "include:\n  - path: sub.yml\n    env_file: /dev/zero\nservices: {}\n")
        found = project._reach(a, set(), set())
        self.assertEqual((found["files"], found["unsafe"]), ([a], [more]))
        self.assertEqual(project.held_reasons(found, set()), ["Includes content from outside the catalog"])
        # a file in the folder of a reserved name was never a unit of the Lab: it is not one because it looks like one
        reserved = ".lab/catalogs/core/addons/z/compose.yml"
        self.write(reserved, "services: {}\n")
        self.write(a, "include:\n  - ../../../core/addons/z/compose.yml\n")
        self.assertEqual(project._reach(a, set(), set())["unsafe"], [reserved])

    def test_why_a_unit_is_held_says_which_file_and_which_kind_so_that_an_author_is_told_what_to_change(self):
        # The line a person reads is the policy's (T19, T25); the kind is for whoever tells an author which spelling to change
        a, b, c = (self.UNIT.format(name) for name in ("a", "b", "c"))
        self.patch(paths, "default_ns", return_value="fixture")
        spellings = {"a long form with required": "include:\n  - path: ../b/compose.yml\n    required: false\n",
                     "a flow list over several lines": "include: [\n  ../b/compose.yml,\n  ]\n",
                     "a name with a space": "include:\n  - ../b/compose .yml\n", "an escaped name": "include:\n  - \"../b/c\\x6fmpose.yml\"\n",
                     "an escaped key": '"inclu\\u0064e": [../b/compose.yml]\n'}
        for name, text in spellings.items():
            with self.subTest(name):
                self.write(a, text)
                self.write(b, "services: {}\n")
                found = project._reach(a, set(), {a, b})
                self.assertEqual((found["files"], found["unsafe"], found.get("unnamed")), ([], [a], [a]),
                                 "the unit itself, which the screen found clean, is held: nothing it names is followed")
        self.assertNotIn("unnamed", project._reach(self.UNIT.format("none"), set(), set()), "a unit that is not there names none")
        self.write(a, "include:\n  - ../b/compose.yml\n")
        self.write(b, "include:\n  - path: ../c/compose.yml\n    required: false\n")
        self.write(c, "services: {}\n")
        found = project._reach(a, set(), {a, b, c})
        self.assertEqual((found["files"], found["unsafe"], found["unnamed"]), ([a], [b], [b]))
        self.assertEqual(project.held_why(found, set()), [(b, "unnamed")])
        self.assertEqual(project.held_reasons(found, set()), ["Includes content from outside the catalog"])
        # The others, each with its own kind
        self.write(a, "include:\n  - ../b/compose.yml\n  - ../c/compose.yml\n  - ../d/compose.yml\n  - ../e/compose.yml\n")
        d, e = self.UNIT.format("d"), self.UNIT.format("e")
        self.write(b, "services: {}\n")
        self.write(c, "services: {}\n")
        os.makedirs(self.root / d.rsplit("/", 1)[0])
        os.symlink(self.root / "plain.yml", self.root / d)
        self.write(e, "services: {}\n")
        found = project._reach(a, {c}, {a, b})
        self.assertEqual(project.held_why(found, {c}), [(c, "refused"), (d, "special"), (e, "outside")])
        with mock.patch.object(project, "REACH_LIMIT", 1):
            found = project._reach(a, set(), {a, b, c, e})
        self.assertEqual([kind for _, kind in project.held_why(found, set())], ["bomb"])
        self.assertEqual(project.held_reasons(found, set()), ["Includes content from outside the catalog"])

    def test_what_a_unit_of_this_machine_includes_is_followed_but_for_the_files_of_a_catalog_that_are_no_unit(self):
        mine, theirs, more = ".lab/catalogs/local/addons/m/compose.yml", self.UNIT.format("t"), ".lab/catalogs/xx/addons/t/more.yml"
        own = ".lab/catalogs/local/addons/m/own.yml"
        self.write(theirs, "include:\n  - more.yml\n")
        self.write(more, "services: {}\n")
        self.write(own, "include:\n  - ../../../../../elsewhere.yml\n")
        self.write("elsewhere.yml", "services: {}\n")
        self.write(mine, "include:\n  - own.yml\n  - ../../../xx/addons/t/compose.yml\n")
        found = project._reach(mine, set(), {theirs})
        self.assertEqual(found["files"], [mine, own, theirs, "elsewhere.yml"], "what the person includes is the person's own")
        self.assertEqual(found["unsafe"], [more], "reached through a unit of a catalog that is clean")
        self.write(mine, "include:\n  - ../../../xx/addons/t/more.yml\n")
        self.assertEqual(project._reach(mine, set(), {theirs})["unsafe"], [more], "straight from the unit of this machine")
        # What a file of a catalog includes outside the catalogs is not the person's: it is what the catalog chose
        t2 = self.UNIT.format("t2")
        self.write(t2, "include:\n  - ../../../../../elsewhere.yml\n")
        self.assertEqual(project._reach(t2, set(), {t2})["unsafe"], ["elsewhere.yml"])
        self.write(mine, "include:\n  - ../../../xx/addons/t2/compose.yml\n  - own.yml\n")
        found = project._reach(mine, set(), {t2})
        self.assertEqual((found["unsafe"], "elsewhere.yml" in found["files"]), (["elsewhere.yml"], True),
                         "one file reached both ways is judged both ways")

    def test_a_dot_env_that_is_a_link_or_a_pipe_next_to_a_file_holds_it_for_compose_reads_it_with_the_file(self):
        a = self.UNIT.format("a")
        self.write(a, "services: {}\n")
        env = a.rsplit("/", 1)[0] + "/.env"
        self.write(env, "A=1\n")
        self.assertEqual(project._reach(a, set(), set())["unsafe"], [], "a plain file")
        os.unlink(self.root / env)
        os.mkfifo(self.root / env)
        found = project._reach(a, set(), set())
        self.assertEqual((found["files"], found["unsafe"]), ([], [env]))
        self.assertEqual(project.held_reasons(found, set()), ["Reads files outside its folder"])
        os.unlink(self.root / env)
        os.symlink(self.root / "plain.yml", self.root / env)
        self.assertEqual(project._reach(a, set(), set())["unsafe"], [env], "also one that leads to a plain file")
        mine = ".lab/catalogs/local/addons/m/compose.yml"
        self.write(mine, "services: {}\n")
        os.symlink(self.root / "plain.yml", self.root / ".lab/catalogs/local/addons/m/.env")
        self.assertEqual(project._reach(mine, set(), set())["unsafe"], [], "this machine's own")

    def test_the_closure_follows_every_include_the_screen_names_and_holds_a_file_whose_includes_it_cannot(self):
        a, b, c = (self.UNIT.format(name) for name in "abc")
        for name in (b, c):
            self.write(name, "services: {}\n")
        for text in ("include:\n  - ../b/compose.yml\n", "include: [../b/compose.yml]\n", "include:\n  - {path: ../b/compose.yml}\n",
                     "include:\n  - [../b/compose.yml]\n", "include:\n  - path: [../b/compose.yml]\n",
                     "include:\n  - path:\n      - ../b/compose.yml\n", '"include": [../b/compose.yml]\n',
                     "'include':\n  - ../b/compose.yml\n",
                     "include:\r\n  - ../b/compose.yml\r\n", "include:  # note\n  - path: ../b/compose.yml   # why\n",
                     "x-note: \"does not include: ../c/compose.yml\"\ninclude: [../b/compose.yml]\n"):
            with self.subTest(text=text):
                self.write(a, text)
                found = project._reach(a, set(), {a, b, c})
                self.assertEqual((found["files"], found["unsafe"], found["targets"]), ([a, b], [], [b]))
        for text in ("include:\n  - *shared\n", "include:\n  - path: ../b/compose.yml\n    project_directory: .\n",
                     "include:\n  - ../b/compose.yml\n    env_file: x\n", "include:\n  - a: b\n", "include: [../b/compose.yml, x,y\n",
                     "include:\n  - |\n    ../b/compose.yml\n", "include:\n  - ../b/compose.yml,../c/compose.yml\n",
                     "include:\n  - ../b/compose.yml\n  - /etc/hosts\n", '"inclu\\u0064e": [../b/compose.yml]\n',
                     "include:\n  - '../b/compos\\x65.yml'\n", 'include:\n  - "../b/compose.yml "\n', "include:\n  - :,\n",
                     "include:\n  - ../b/compose\n    .yml\n", "x-note: abc\\\ninclude: [../b/compose.yml]\n"):
            with self.subTest(text=text):
                self.write(a, text)
                found = project._reach(a, set(), {a, b, c})
                self.assertEqual((found["files"], found["unsafe"], found["targets"]), ([], [a], []), "not followed, and held")
                self.assertEqual(project.held_reasons(found, set()), ["Includes content from outside the catalog"])

    def test_a_flow_list_is_followed_to_the_files_it_names_and_they_are_held_to_what_a_unit_is(self):
        a, b, c, link = (self.UNIT.format(name) for name in ("a", "b", "c", "link"))
        self.write(a, "include: [../b/compose.yml, ../c/compose.yml, missing/compose.yml, ../link/compose.yml]\n")
        self.write(b, "include: [../c/compose.yml]\n")
        self.write(c, "services: {}\n")
        os.makedirs(self.root / link.rsplit("/", 1)[0])
        os.symlink(self.root / "plain.yml", self.root / link)
        found = project._reach(a, set(), {b, c})
        self.assertEqual(found["files"], [a, b, c])
        self.assertEqual(found["unsafe"], [link], "a link a flow list names is as much a link")
        self.assertEqual(found["targets"], [b, c, ".lab/catalogs/xx/addons/a/missing/compose.yml", link])
        self.assertEqual(found["missing"], [".lab/catalogs/xx/addons/a/missing/compose.yml"])
        self.assertEqual(project._reach(a, {c}, {b})["unsafe"], [c, link], "a unit the screen refused, reached through a list")
        self.write(c, "services: {}\n# not clean\n")
        self.assertEqual(project._reach(a, set(), {b})["unsafe"], [c, link], "a file that is no clean unit is held, however it is named")

    def test_the_texts_merged_into_a_unit_are_those_of_the_files_its_closure_reached_and_not_its_own(self):
        a, b, c = (self.UNIT.format(name) for name in "abc")
        self.write(a, "include: [../b/compose.yml]\n")
        self.write(b, "include: [../c/compose.yml]\nservices: {}\n")
        self.write(c, "x: caf\u00e9\n")
        found = project._reach(a, set(), {a, b, c})
        self.assertEqual(found["files"], [a, b, c])
        self.assertEqual(project._merged(a, found), ["include: [../c/compose.yml]\nservices: {}\n", "x: caf\u00e9\n"])
        self.assertEqual(project._merged(c, found), ["include: [../b/compose.yml]\n", "include: [../c/compose.yml]\nservices: {}\n"])
        (self.root / c).write_bytes(b"\xff\xfe not text")
        self.assertEqual(project._merged(a, found), ["include: [../c/compose.yml]\nservices: {}\n"], "what cannot be read is left out")
        self.assertEqual(project._merged(a, {"files": [a]}), [])

    def test_the_includes_of_a_file_are_the_screens_for_a_catalog_and_composes_for_the_rest(self):
        a, b = self.UNIT.format("a"), self.UNIT.format("b")
        mine = ".lab/catalogs/local/addons/m/compose.yml"
        self.write(a, "include: [../b/compose.yml]\n")
        self.write(mine, "include:\n  - ../../../xx/addons/a/compose.yml\n")
        screen = {a: ("include: [../b/compose.yml]\n", [], None)}
        self.assertEqual(project._direct(a, screen), [b], "read as the screen reads it")
        self.assertEqual(project._includes(a), [b], "a list on one line too")
        self.assertEqual(project._direct(mine, screen), [a], "this machine's own: what compose.includes names")
        self.assertEqual(project._direct(a, {}), [b], "no text read for it: what compose.includes names")
        self.assertEqual(project._direct("absent.yml", screen), [])
        self.assertEqual(project._direct(a, {a: (None, [], "not UTF-8 text")}), [], "nothing to read")

    def test_a_closure_that_is_too_big_is_not_followed_to_the_end(self):
        base = ".lab/catalogs/local/addons"
        for number in range(5):
            self.write(f"{base}/n{number}/compose.yml", f"include:\n  - ../n{number + 1}/compose.yml\n")
        with mock.patch.object(project, "REACH_LIMIT", 3):
            found = project._reach(f"{base}/n0/compose.yml", set())
        self.assertEqual(found["unsafe"], [f"{base}/n3/compose.yml"])
        self.assertEqual(found["bomb"], f"{base}/n3/compose.yml")
        self.assertEqual(project.held_reasons(found, set()), ["Includes content from outside the catalog"])

    def test_a_file_in_a_folder_that_is_a_link_out_of_the_catalog_is_a_link_too(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        (self.root / ".lab/catalogs/xx/addons").mkdir(parents=True)
        os.symlink(outside, self.root / ".lab/catalogs/xx/addons/trojan")
        self.write(".lab/catalogs/xx/addons/kept/compose.yml", "services: {}\n")
        self.assertEqual(project._read(".lab/catalogs/xx/addons/trojan/compose.yml"), (None, "link"))
        self.assertEqual(project._screen(".lab/catalogs/xx/addons/trojan/compose.yml"), (None, ["Reads files outside its folder"], None))
        self.assertEqual(project._read(".lab/catalogs/xx/addons/kept/compose.yml"), ("services: {}\n", None))
        self.assertEqual(project._read(".lab/catalogs/xx/addons/absent/compose.yml"), (None, "missing"))
        self.assertEqual(project._read("outside/compose.yml"), ("services: {}\n", None), "not a catalog's: the person's own")
        self.assertFalse(project._outside(".lab/catalogs/local/addons/x/compose.yml"))
        # A catalog folder that is itself a link, as a folder catalog's is, holds its files all the same
        os.symlink(self.root / "outside", self.root / ".lab/catalogs/yy")
        (outside / "addons/ok").mkdir(parents=True)
        (outside / "addons/ok/compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.assertEqual(project._read(".lab/catalogs/yy/addons/ok/compose.yml"), ("services: {}\n", None))
        os.symlink(self.root / "plain.yml", outside / "addons/ok/bad.yml")
        self.assertEqual(project._read(".lab/catalogs/yy/addons/ok/bad.yml"), (None, "link"))

    def test_a_seed_folder_that_is_a_link_is_a_reason_and_is_not_followed(self):
        outside = self.root / "outside"
        (outside / "apim").mkdir(parents=True)
        (outside / "apim/x.yaml").write_text("token: ${APIM_TOKEN}\n", encoding="utf-8")
        (self.root / "unit").mkdir()
        os.symlink(outside, self.root / "unit/seed")
        with mock.patch.object(project, "_read", wraps=project._read) as read:
            self.assertEqual(project._seed_scan("unit/seed"), ([], ["Reads files outside its folder"]))
        read.assert_not_called()
        self.assertEqual(project._seed_names("unit", safe=True), [])

    def test_a_catalog_file_that_includes_something_is_not_read_for_the_names_its_units_share(self):
        shared = "x-gravitee-catalog:\n  id: z\n  shared_settings: [A_ONE]\n"
        with mock.patch.object(project, "_raw", side_effect=AssertionError("Compose was asked")):
            for number, text in enumerate(("include:\n  - pipe.yml\n", "include: [pipe.yml]\n", "x: {include: [pipe.yml]}\n",
                                           '"include":\n  - pipe.yml\n', '"inclu\\u0064e": [pipe.yml]\n')):
                with self.subTest(text=text):
                    self.write(f".lab/catalogs/inc{number}/catalog.yml", text + shared)
                    self.assertEqual(project._shared_settings(f"inc{number}"), [])
        self.write(".lab/catalogs/fine/catalog.yml", shared + "  description: Does not include: much\n")
        with mock.patch.object(project, "_raw", lambda path, **how: ({"x-gravitee-catalog": {"shared_settings": ["A_ONE"]}}, None)):
            self.assertEqual(project._shared_settings("fine"), ["A_ONE"])

    def test_a_file_that_includes_nothing_it_can_read_includes_nothing(self):
        self.assertEqual(project._includes("bytes.yml"), [])
        self.assertEqual(project._includes("absent.yml"), [])
        self.assertEqual(project._includes("pipe.yml"), [])

    def test_the_names_a_catalog_says_its_units_share_are_read_after_the_same_screen_as_a_unit(self):
        self.write(".lab/catalogs/x/catalog.yml", "x-gravitee-catalog:\n  id: x\n  shared_settings: [A_ONE, b_two, 'not a name', 3]\n")
        with mock.patch.object(project, "_raw", lambda path, **how: ({"x-gravitee-catalog": {"shared_settings": [
                "A_ONE", "b_two", "not a name", 3]}}, None)):
            self.assertEqual(project._shared_settings("x"), ["A_ONE", "b_two"])
        self.write(".lab/catalogs/y/catalog.yml", "include: [/etc/passwd]\nx-gravitee-catalog:\n  shared_settings: [A]\n")
        with mock.patch.object(project, "_raw", side_effect=AssertionError("Compose was asked")):
            self.assertEqual(project._shared_settings("y"), [])
            self.assertEqual(project._shared_settings("absent"), [])
            self.write(".lab/catalogs/z/catalog.yml", "x-gravitee-catalog:\n  id: z\n")
            self.assertEqual(project._shared_settings("z"), [], "no word of it, no call")

    def test_the_level_of_the_core_and_its_seeder_are_read_with_a_floor_of_one(self):
        self.assertEqual(project._contract({}), {"contract": 1, "contract_min": 1, "seeder_image": ""})
        given = {"x-gravitee": {"contract": 3, "contract_min": 2, "seeder": {"image": "ghcr.io/x/seeder"}}}
        self.assertEqual(project._contract(given), {"contract": 3, "contract_min": 2, "seeder_image": "ghcr.io/x/seeder"})
        for odd in (True, 0, -1, "2", 1.5, None):
            self.assertEqual(project._contract({"x-gravitee": {"contract": odd, "contract_min": odd, "seeder": 4}}),
                             {"contract": 1, "contract_min": 1, "seeder_image": ""}, odd)

    def test_a_message_of_compose_shows_no_path_of_this_machine(self):
        text = f"validating {self.root}/.lab/catalogs/x/addons/y/compose.yml: yaml: line 3 in /var/tmp/z/other.yml"
        self.assertEqual(project._plain(text), "validating addons/y/compose.yml: yaml: line 3 in other.yml")

    def test_an_env_file_of_a_catalog_that_is_a_link_is_not_read(self):
        self.write(".lab/catalogs/x/addons/y/real.env", "SECRET=1\n")
        os.symlink(self.root / ".lab/catalogs/x/addons/y/real.env", self.root / ".lab/catalogs/x/addons/y/.env")
        self.write(".lab/catalogs/x/addons/z/.env", "A=1\n")
        self.write(".lab/catalogs/local/addons/y/real.env", "B=2\n")
        os.symlink(self.root / ".lab/catalogs/local/addons/y/real.env", self.root / ".lab/catalogs/local/addons/y/.env")
        self.assertEqual(project._dotenv(".lab/catalogs/x/addons/y/.env"), {})
        self.assertEqual(project._dotenv(".lab/catalogs/x/addons/z/.env"), {"A": "1"})
        self.assertEqual(project._dotenv(".lab/catalogs/local/addons/y/.env"), {"B": "2"}, "this machine's own")
        self.assertEqual(project._dotenv(".lab/catalogs/x/addons/absent/.env"), {})

    def test_an_env_file_that_is_not_text_or_too_large_or_a_pipe_is_read_as_none_and_never_stops_the_project(self):
        (self.root / ".lab/catalogs/x/addons/y").mkdir(parents=True)
        (self.root / ".lab/catalogs/x/addons/y/.env").write_bytes(b"\xff\xfe\x00bad=1\n")
        (self.root / ".lab/catalogs/x/addons/big").mkdir()
        (self.root / ".lab/catalogs/x/addons/big/.env").write_text("A=1\n" + "# " + "x" * (1 << 20), encoding="utf-8")
        (self.root / ".lab/catalogs/x/addons/pipe").mkdir()
        os.mkfifo(self.root / ".lab/catalogs/x/addons/pipe/.env")
        (self.root / "bad.env").write_bytes(b"\xff\xfe\x00bad=1\n")
        for name in ("y", "big", "pipe"):
            with self.subTest(name=name):
                self.assertEqual(project._dotenv(f".lab/catalogs/x/addons/{name}/.env"), {})
        self.assertEqual(project._dotenv("bad.env"), {}, "also the person's own: not UTF-8 is none")


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Options(Case):
    """How a unit is asked of Compose ()."""

    def setUp(self):
        self.patch(project, "_no_env", True)

    def test_a_unit_is_resolved_without_reading_the_files_it_names(self):
        with mock.patch.object(compose, "_config", wraps=compose._config) as asked:
            read(self)
        # What is asked of a unit with the core, whole (the reading of a file as written is another call)
        options = [call.args[1:] for call in asked.call_args_list
                   if call.kwargs.get("timeout", 120) <= project.UNIT_TIMEOUT and "--no-interpolate" not in call.args]
        self.assertGreater(len(options), 8)
        self.assertEqual(set(options), {("--no-env-resolution",)})

    UNKNOWN = "unknown flag: --no-env-resolution"

    def compose_that_says(self, help_text):
        """A Compose that refuses the option with the words of the real one, and prints this for `config --help`: the calls
        of the help are counted."""
        real, run, helps = compose._config, compose.run, []

        def config(files, *options, timeout=120):
            if "--no-env-resolution" in options:
                raise compose.ComposeError(self.UNKNOWN)
            return real(files, *options, timeout=timeout)

        def helping(cmd, *more, **how):
            if cmd[-2:] == ["config", "--help"]:
                helps.append(cmd)
                return 0, help_text, ""
            return run(cmd, *more, **how)
        self.patch(project, "_option", {"known": None})
        self.start(mock.patch.object(compose, "_config", config))
        self.start(mock.patch.object(compose, "run", helping))
        return helps

    def test_a_compose_that_does_not_know_the_option_is_asked_without_it_from_then_on(self):
        helps = self.compose_that_says("Usage:  docker compose config [OPTIONS]\n      --format string   Format the output\n")
        _, built = read(self)
        self.assertFalse(project._no_env)
        self.assertEqual(built["addons"]["echo"]["blocked"], None)
        self.assertEqual(built["addons"]["echo"]["services"], ["fixture-echo"])
        self.assertEqual(len(helps), 1, "asked once, whatever the units")

    def test_what_a_unit_makes_compose_print_does_not_take_the_option_away_from_the_others(self):
        # An error that a unit makes Compose print, down to a last line that is the very one of the real refusal
        self.patch(project, "_option", {"known": None})
        hostile = [("same", 'image: "${EVIL:?' + self.UNKNOWN + '}"'), ("lines", 'image: "${EVIL:?x\\n' + self.UNKNOWN + '}"')]
        helps, run = [], compose.run

        def units(case):
            for name, image in hostile:
                case.write(f".lab/catalogs/fixture/addons/{name}/compose.yml", addon(name).replace("image: busybox", image))

        def watched(cmd, *more, **how):
            if cmd[-2:] == ["config", "--help"]:
                helps.append(cmd)
            return run(cmd, *more, **how)
        with mock.patch.object(compose, "run", watched):
            _, built = read(self, more=units)
        self.assertTrue(project._no_env)
        for name, _ in hostile:
            self.assertEqual(built["addons"][name]["services"], [], name)
            self.assertIn("unknown flag", " ".join(built["addons"][name]["blocked"]["reasons"]), name)
        self.assertEqual(built["addons"]["echo"]["services"], ["fixture-echo"])
        self.assertEqual(len(helps), 1, "Compose was asked once, whatever the units said")
        self.assertTrue(project._option["known"])

    def test_a_compose_that_cannot_print_its_help_is_taken_to_know_the_option(self):
        self.patch(project, "_option", {"known": None})
        with mock.patch.object(compose, "run", return_value=(2, "", "no")):
            self.assertTrue(project._knows())
        self.assertIsNone(project._option["known"], "not remembered: the help may be had later")
        with mock.patch.object(compose, "run", return_value=(0, "      --no-env-resolution\n", "")) as run:
            self.assertTrue(project._knows())
            self.assertTrue(project._knows())
        run.assert_called_once()
        project.forget()
        self.assertIsNone(project._option["known"], "forgotten with the rest")


class Kept(Case):
    """What the pool remembers of a unit and of the core is what Compose said of their files, not that it did not answer."""

    def setUp(self):
        self.folder(paths, project, compose)
        self.patch(project, "_memo", {})

    def test_an_answer_that_is_not_worth_keeping_is_made_again_and_the_one_that_is_is_made_once(self):
        made = []

        def make():
            made.append(1)
            return {"fine": len(made) > 1}
        def keep(found):
            return found["fine"]
        first = project._remembered("k", "stamp", make, keep)
        second = project._remembered("k", "stamp", make, keep)
        third = project._remembered("k", "stamp", make, keep)
        self.assertEqual((first, second, third, len(made)), ({"fine": False}, {"fine": True}, {"fine": True}, 2))
        self.assertEqual(project._remembered("k", "another", make, keep), {"fine": True}, "a stamp that changed reads again")
        self.assertEqual(len(made), 3)
        # What was kept is dropped when the read that replaces it failed: no stale answer stays behind
        project._remembered("k", "third", lambda: {"fine": False}, keep)
        self.assertNotIn((os.fspath(self.root), "k"), project._memo)

    def test_what_compose_did_not_answer_is_not_what_it_said_of_the_files(self):
        timed = {"raw": None, "problem": {"file": "a", "message": "docker did not answer within 15 s"}}
        for found in (timed, {"raw": None, "problem": None, "error": "docker did not answer within 7 s"},
                      {"raw": None, "problem": {"message": "docker was not run: the Lab UI is ending"}},
                      {"raw": None, "problem": {"message": "[Errno 2] No such file or directory: 'docker'"}},
                      {"raw": None, "problem": {"message": "unexpected KeyError"}, "transient": True}):
            with self.subTest(found=found):
                self.assertFalse(project._answered(found))
        for found in ({"raw": {}, "problem": None, "error": None},
                      {"raw": None, "problem": {"message": "yaml: line 3: did not find expected key"}},
                      {"raw": {}, "problem": None, "error": "error while interpolating services.x.image: required variable A is missing a value: set it"},
                      {"raw": None, "problem": {"message": "image: ${EVIL:?docker did not answer within 15 s}, said by a unit"}}):
            with self.subTest(found=found):
                self.assertTrue(project._answered(found))


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class Healing(Case):
    """A read that failed because Compose did not answer is made again at the next gather, and while it failed nothing is let
    through."""

    PRIVATE = {"partner": {"files": {"addons/priv/compose.yml": addon("priv", ["priv"]).replace(
        "    networks: [internal]", "    networks: [internal]\n    privileged: true")}}}

    def core_fails(self):
        """Compose does not answer, once, for the core alone: answers (the switch that says it does not any more)."""
        real, state = compose._config, {"fails": True}

        def flaky(files, *options, timeout=120):
            if state["fails"] and "--no-env-resolution" in options and list(files) == ["-f", project.CORE_FILE]:
                raise compose.ComposeError("docker did not answer within 15 s")
            return real(files, *options, timeout=timeout)
        self.patch(compose, "_config", flaky)
        return state

    def test_a_core_that_could_not_be_read_blocks_every_unit_of_a_catalog_and_heals_at_the_next_gather(self):
        state = self.core_fails()
        def mine(case):
            case.write(".lab/catalogs/local/addons/mine/compose.yml", addon("mine"))
        inputs, built = read(self, catalogs=self.PRIVATE, more=mine)
        self.assertEqual((inputs["core_config"], inputs["core_error"]), (None, "docker did not answer within 15 s"))
        reason = ["Cannot be read: docker did not answer within 15 s"]
        self.assertEqual({uid: built["addons"][uid]["blocked"]["reasons"] for uid in ("echo", "@partner/priv", "@partner/ledger")},
                         {"echo": reason, "@partner/priv": reason, "@partner/ledger": reason})
        self.assertEqual(built["addons"]["echo"]["services"], [])
        self.assertIsNone(built["addons"]["local/mine"]["blocked"], "this machine's own is not judged")
        # Nothing changed on disk: the core answers, and what was let through before would be let through now
        state["fails"] = False
        inputs, built = fixtures_gather()
        self.assertIsNotNone(inputs["core_config"])
        self.assertEqual(built["addons"]["@partner/priv"]["blocked"], {"reasons": ["Runs with full privileges"]})
        self.assertIsNone(built["addons"]["echo"]["blocked"])
        self.assertEqual(built["addons"]["echo"]["services"], ["fixture-echo"])

    def test_a_unit_that_compose_took_too_long_to_read_is_read_again_and_one_it_refused_is_not(self):
        late, state = compose.ComposeError("docker did not answer within 15 s"), {"late": True}
        raw, alone = compose.raw, project._alone

        def slow_alone(path, *more):
            return (None, str(late)) if state["late"] and path.endswith("addons/gateway/compose.yml") else alone(path, *more)

        def slow_raw(path, *more, **how):
            if state["late"] and path.endswith("addons/cluster/compose.yml"):
                raise late
            return raw(path, *more, **how)

        def undefined(case):
            case.write(".lab/catalogs/fixture/addons/needs/compose.yml",
                       addon("needs").replace("image: busybox", "image: ${FIXTURE_NEEDS_IMAGE:?set it}"))
        with mock.patch.object(project, "_alone", slow_alone), mock.patch.object(compose, "raw", slow_raw):
            inputs, built = read(self, more=undefined)
            self.assertEqual(built["addons"]["gateway"]["blocked"]["reasons"], ["Cannot be read: docker did not answer within 15 s"])
            self.assertNotIn("cluster", built["addons"])
            state["late"] = False
            asked = []
            with mock.patch.object(project, "_alone", lambda path, *more: asked.append(path) or slow_alone(path, *more)):
                inputs, built = fixtures_gather()
        self.assertIsNone(built["addons"]["gateway"]["blocked"])
        self.assertEqual(built["addons"]["gateway"]["services"], ["fixture-gateway"])
        self.assertTrue(built["addons"]["cluster"]["services"])
        self.assertEqual(sorted(paths.unit_of(path)[1] for path in asked), ["cluster", "gateway"],
                         "the two that did not answer, and not the others")
        self.assertTrue(built["addons"]["needs"]["blocked"], "an answer on the files stays")

    def test_the_names_a_catalog_shares_are_read_again_when_compose_did_not_answer(self):
        calls = []
        file = ".lab/catalogs/fixture/catalog.yml"

        def shares(case):
            text = (case.root / file).read_text(encoding="utf-8")
            (case.root / file).write_text(text + "  shared_settings: [FIXTURE_ONE]\n", encoding="utf-8")
        raw = compose.raw

        def watched(path, *more, **how):
            if path == file:
                calls.append(path)
                if len(calls) == 1:
                    raise compose.ComposeError("docker did not answer within 15 s")
            return raw(path, *more, **how)
        with mock.patch.object(compose, "raw", watched):
            inputs, _ = read(self, more=shares)
            self.assertEqual(inputs["shared_settings"]["fixture"], [])
            self.assertEqual(project.gather()["shared_settings"]["fixture"], ["FIXTURE_ONE"])
            project.gather()
        self.assertEqual(len(calls), 2, "read again once, and then kept")


def fixtures_gather():
    """The project read again, nothing changed on disk: (the inputs, the project)."""
    inputs = project.gather()
    return inputs, project.build(inputs)


if __name__ == "__main__":
    unittest.main()
