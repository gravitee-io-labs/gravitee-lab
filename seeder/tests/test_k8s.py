"""Kubernetes: the manifests of seed/k8s applied to a cluster, waited for, recorded, and deleted in the operator's order."""

import base64
import re
import tempfile
from pathlib import Path

import yaml

from lab_seeder import common
from lab_seeder import k8s
from lab_seeder.testkit import Seed
from tests import doubling

SERVER = "https://k8s:6443"
KUBECONFIG = """
apiVersion: v1
clusters: [{name: lab, cluster: {server: "%s", insecure-skip-tls-verify: true}}]
users: [{name: lab, user: {token: secret-token}}]
"""
CORE = {"resources": [{"name": "namespaces", "kind": "Namespace", "namespaced": False},
                      {"name": "secrets", "kind": "Secret", "namespaced": True}]}
GRAVITEE = {"resources": [{"name": "managementcontexts", "kind": "ManagementContext", "namespaced": True, "verbs": ["list"]},
                          {"name": "apiv4definitions", "kind": "ApiV4Definition", "namespaced": True, "verbs": ["list"]},
                          {"name": "applications", "kind": "Application", "namespaced": True, "verbs": ["list"]},
                          {"name": "subscriptions", "kind": "Subscription", "namespaced": True, "verbs": ["list"]},
                          {"name": "apiv4definitions/status", "kind": "ApiV4Definition", "namespaced": True, "verbs": []}]}
NAMESPACE = SERVER + "/api/v1/namespaces/acme"
SECRET = SERVER + "/api/v1/namespaces/acme/secrets/lab-credentials"
CONTEXT = SERVER + "/apis/gravitee.io/v1alpha1/namespaces/acme/managementcontexts/lab"
FILES = {"k8s/01-namespace.yaml": "apiVersion: v1\nkind: Namespace\nmetadata: {name: acme}\n",
         "k8s/02-context.yaml": """
apiVersion: v1
kind: Secret
metadata: {name: lab-credentials, namespace: acme}
stringData: {username: "${APIM_USERNAME}", password: "${APIM_PASSWORD}"}
---
apiVersion: gravitee.io/v1alpha1
kind: ManagementContext
metadata: {name: lab, namespace: acme}
spec: {baseUrl: "${APIM_MANAGEMENT_URL}"}
"""}
ACCEPTED = {"status": {"conditions": [{"type": "Accepted", "status": "True"}]}}


def route(url):
    return re.sub(r"^https://k8s:6443", "", url)


class Manifests(Seed):

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        kubeconfig = Path(folder.name) / "kubeconfig.yaml"
        kubeconfig.write_text(KUBECONFIG % SERVER, encoding="utf-8")
        self.env(KUBECONFIG=str(kubeconfig))
        self.cluster = {("GET", SERVER + "/readyz"): (200, "ok"),
                        ("GET", SERVER + "/api/v1"): (200, CORE),
                        ("GET", SERVER + "/apis/gravitee.io/v1alpha1"): (200, GRAVITEE),
                        ("PATCH", NAMESPACE): (200, {}), ("PATCH", SECRET): (200, {}), ("PATCH", CONTEXT): (200, {}),
                        ("GET", CONTEXT): (200, ACCEPTED)}

    def test_a_manifest_that_asks_the_loader_for_a_bomb_of_pairs_is_refused_before_anything_is_built(self):
        self.seed_files({"k8s/01-bomb.yaml": doubling(40)})
        with self.assertRaises(common.MergeError):
            k8s.manifests()

    def test_it_does_nothing_without_manifests(self):
        k8s.seed()
        self.assertEqual(self.platform.calls, [])

    def test_manifests_are_applied_in_order_with_their_variables_and_recorded(self):
        self.seed_files(FILES)
        self.answer(self.cluster)
        k8s.seed()
        self.assertEqual([call.path for call in self.platform.calls if call.method == "PATCH"],
                         ["/api/v1/namespaces/acme", "/api/v1/namespaces/acme/secrets/lab-credentials",
                          "/apis/gravitee.io/v1alpha1/namespaces/acme/managementcontexts/lab"])
        secret = self.platform.sent("PATCH", SECRET)[0]
        self.assertEqual(secret.params, {"fieldManager": "gravitee-lab", "force": "true"})
        self.assertEqual(secret.headers["Content-Type"], "application/apply-patch+yaml")
        self.assertEqual(secret.headers["Authorization"], "Bearer secret-token")
        self.assertIn("password: admin", secret.data)
        self.assertEqual([(x["kind"], x["name"]) for x in common.created()],
                         [("namespace", "acme"), ("resource", "Secret acme/lab-credentials"),
                          ("resource", "ManagementContext acme/lab")])

    def test_a_resource_of_the_operator_is_waited_for_until_it_is_accepted(self):
        self.seed_files(FILES)
        self.answer({**self.cluster, ("GET", CONTEXT): [(200, {}), (200, ACCEPTED)]})
        k8s.seed()
        self.assertEqual(len(self.platform.sent("GET", CONTEXT)), 2)

    def test_a_resource_the_operator_refuses_stops_the_seed_with_its_message(self):
        self.seed_files(FILES)
        refused = {"status": {"conditions": [{"type": "Accepted", "status": "False", "reason": "ControlPlaneError",
                                              "message": "401 unauthorized"}]}}
        self.answer({**self.cluster, ("GET", CONTEXT): (200, refused)})
        with self.assertRaises(common.SeedError) as e:
            k8s.seed()
        self.assertIn("ControlPlaneError: 401 unauthorized", str(e.exception))

    def test_a_webhook_refusal_stops_the_seed_at_once(self):
        self.seed_files(FILES)
        self.answer({**self.cluster, ("PATCH", CONTEXT): (403, {"message": "denied the request: Path [/x] already exists"})})
        with self.assertRaises(common.SeedError) as e:
            k8s.seed()
        self.assertIn("denied the request", str(e.exception))
        self.assertEqual(len(self.platform.sent("PATCH", CONTEXT)), 1)

    def test_a_webhook_that_is_not_up_yet_is_asked_again(self):
        self.seed_files(FILES)
        self.answer({**self.cluster, ("PATCH", CONTEXT): [(500, {"message": "failed calling webhook"}), (200, {})]})
        k8s.seed()
        self.assertEqual(len(self.platform.sent("PATCH", CONTEXT)), 2)

    def test_a_cluster_without_the_operator_is_an_error_that_says_so(self):
        self.seed_files(FILES)
        self.answer({**self.cluster, ("GET", SERVER + "/apis/gravitee.io/v1alpha1"): (404, {})})
        with self.assertRaises(common.SeedError) as e:
            k8s.seed()
        self.assertIn("is the operator installed", str(e.exception))

    def test_a_namespaced_manifest_needs_its_namespace(self):
        self.seed_files({"k8s/01.yaml": "apiVersion: v1\nkind: Secret\nmetadata: {name: lab-credentials}\n"})
        self.answer(self.cluster)
        with self.assertRaises(common.SeedError) as e:
            k8s.seed()
        self.assertIn("needs metadata.namespace", str(e.exception))


class Removal(Manifests):

    def records(self):
        return [{"product": "k8s", "kind": "namespace", "label": "namespaces", "name": "acme", "id": "acme"},
                {"product": "k8s", "kind": "resource", "label": "Kubernetes resources", "name": "Secret acme/lab-credentials",
                 "id": "lab-credentials", "apiVersion": "v1", "kind_name": "Secret", "namespace": "acme"},
                {"product": "k8s", "kind": "resource", "label": "Kubernetes resources", "name": "ManagementContext acme/lab",
                 "id": "lab", "apiVersion": "gravitee.io/v1alpha1", "kind_name": "ManagementContext", "namespace": "acme"}]

    def lists(self):
        base = SERVER + "/apis/gravitee.io/v1alpha1/namespaces/acme/"
        items = {"managementcontexts": ["lab"], "apiv4definitions": ["orders"], "applications": ["shop"],
                 "subscriptions": ["shop-orders"]}
        routes = {("GET", SERVER + "/apis"): (200, {"groups": [{"name": "gravitee.io", "preferredVersion":
                                                              {"groupVersion": "gravitee.io/v1alpha1"}}]})}
        for plural, names in items.items():
            routes[("GET", base + plural)] = (200, {"items": [{"metadata": {"name": n}} for n in names]})
            for n in names:
                routes[("DELETE", base + f"{plural}/{n}")] = (200, {})
                routes[("GET", base + f"{plural}/{n}")] = [(200, {}), (404, {})]
        routes[("DELETE", SECRET)] = (200, {})
        routes[("GET", SECRET)] = (404, {})
        routes[("DELETE", NAMESPACE)] = (200, {})
        return routes

    def test_what_the_operator_manages_in_a_namespace_goes_in_dependency_order_then_the_rest(self):
        self.answer({**self.cluster, **self.lists()})
        k8s.remove(self.records())
        deleted = [route(call.url).rsplit("/", 2)[-2:] for call in self.platform.calls if call.method == "DELETE"]
        self.assertEqual(deleted, [["subscriptions", "shop-orders"], ["applications", "shop"],
                                   ["apiv4definitions", "orders"], ["managementcontexts", "lab"],
                                   ["secrets", "lab-credentials"], ["namespaces", "acme"]])

    def test_a_deletion_the_webhook_refuses_is_asked_again(self):
        base = SERVER + "/apis/gravitee.io/v1alpha1/namespaces/acme/"
        self.answer({**self.cluster, **self.lists(),
                     ("DELETE", base + "applications/shop"): [(403, {"message": "referenced in 1 subscriptions"}), (200, {})]})
        k8s.remove(self.records())
        self.assertEqual(len(self.platform.sent("DELETE", base + "applications/shop")), 2)

    def test_a_cluster_that_does_not_answer_leaves_what_it_holds(self):
        self.answer({("GET", SERVER + "/readyz"): (503, "down")})
        k8s.remove(self.records())
        self.assertEqual(self.platform.changes(), [])
        self.assertIn("The cluster does not answer", self.output())

    def test_nothing_recorded_touches_nothing(self):
        k8s.remove([{"product": "apim", "kind": "api", "label": "APIs", "name": "x", "id": "x"}])
        self.assertEqual(self.platform.calls, [])




class Generated(Manifests):
    SECRET = "k8s/01-key.yaml"
    FILE = """
apiVersion: v1
kind: Secret
metadata:
  name: desk-key
  namespace: acme
  annotations: {gravitee-lab/generate: "key", other: kept}
stringData: {label: desk}
"""
    PATH = SERVER + "/api/v1/namespaces/acme/secrets/desk-key"

    def applied(self):
        return [yaml.safe_load(call.data) for call in self.platform.sent("PATCH", self.PATH)]

    def test_a_generated_key_gets_a_random_value_the_first_time(self):
        self.seed_files({self.SECRET: self.FILE})
        self.answer({**self.cluster, ("GET", self.PATH): (404, {}), ("PATCH", self.PATH): (200, {})})
        k8s.seed()
        secret = self.applied()[0]
        self.assertRegex(secret["stringData"]["key"], r"^[0-9a-f]{32}$")
        self.assertEqual(secret["stringData"]["label"], "desk")
        self.assertEqual(secret["metadata"]["annotations"], {"other": "kept"})

    def test_a_key_the_cluster_holds_is_kept(self):
        self.seed_files({self.SECRET: self.FILE})
        held = {"data": {"key": base64.b64encode(b"held-value").decode()}}
        self.answer({**self.cluster, ("GET", self.PATH): (200, held), ("PATCH", self.PATH): (200, {})})
        k8s.seed()
        self.assertEqual(self.applied()[0]["stringData"]["key"], "held-value")

    def test_a_secret_without_the_annotation_is_applied_as_written(self):
        self.seed_files({self.SECRET: self.FILE.replace("gravitee-lab/generate", "other-note")})
        self.answer({**self.cluster, ("PATCH", self.PATH): (200, {})})
        k8s.seed()
        self.assertEqual(self.applied()[0]["stringData"], {"label": "desk"})
        self.assertEqual(len(self.platform.sent("GET", self.PATH)), 0)
