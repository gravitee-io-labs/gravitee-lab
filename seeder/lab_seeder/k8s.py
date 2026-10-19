"""Kubernetes: the manifests of seed/k8s/*.yaml (a Namespace, a Secret, a ManagementContext...), applied in name order to
the cluster of KUBECONFIG, which the Gravitee Kubernetes Operator watches. One manifest to a file, like the other seed
files (a file of several is applied too):

    apiVersion: v1
    kind: Secret
    metadata: {name: lab-credentials, namespace: acme}
    stringData: {username: "${APIM_USERNAME}", password: "${APIM_PASSWORD}"}

A Secret may ask for random values with the annotation `gravitee-lab/generate: key,other`: the seed gives those keys
a value the first time, keeps it on later runs, and the annotation does not reach the cluster. API Management never
forgets an API key, even one of a deleted subscription, so a key written in a file could be used once.

Re-running is safe: a manifest is applied server side, so the cluster changes only what differs. A resource of the
operator is waited for until it is accepted (the Accepted condition) or refused, with the operator's message.

What it records is the manifests and the namespaces it created. A removal deletes every resource of the operator in a
recorded namespace, the ones a lesson applied too, because the operator deletes the API, application or subscription
from API Management only while the context that names the platform still exists: subscriptions first, then applications
and APIs, the rest, and the context last. Then it deletes the recorded manifests, then the namespaces.
"""

import base64
import os
import secrets
import tempfile
import time
from pathlib import Path

import requests
import yaml

from .common import READY_TIMEOUT, Gone, HttpError, SeedError, excerpt, files, has_seed, log, owned, parse_yaml_all, pause, record, render, slot, \
    setting, wait_until

SECTION = "k8s"
DEPENDS = ("apim",)   # the modules whose work this one reads on the platform (schedule.py)
# What it records
ORDER = ["resource", "namespace"]
FIELD_MANAGER = "gravitee-lab"
OPERATOR_GROUP = "gravitee.io"
# On a Secret: the keys of its stringData that get a random value the first time, and keep it on later runs
GENERATE = "gravitee-lab/generate"
# What the operator deletes from API Management first: a subscription uses an application and an API
DELETE_FIRST = ["Subscription", "Application", "ApiV4Definition", "ApiDefinition"]
DELETE_LAST = ["ManagementContext"]


class Cluster:
    """The Kubernetes API of a kubeconfig, over HTTP: the client certificate or the token of its first user."""

    def __init__(self, kubeconfig):
        data = yaml.safe_load(Path(kubeconfig).read_text(encoding="utf-8")) or {}
        try:
            cluster = data["clusters"][0]["cluster"]
            user = data["users"][0]["user"]
        except (KeyError, IndexError, TypeError) as e:
            raise SeedError(f"{kubeconfig} is not a kubeconfig with a cluster and a user") from e
        self.server = cluster["server"].rstrip("/")
        self.session = requests.Session()
        self.dir = tempfile.mkdtemp(prefix="kube-")
        ca = self.file("ca.crt", cluster.get("certificate-authority-data"))
        self.session.verify = ca if ca else not cluster.get("insecure-skip-tls-verify", False)
        crt, key = self.file("client.crt", user.get("client-certificate-data")), self.file("client.key", user.get("client-key-data"))
        if crt and key:
            self.session.cert = (crt, key)
        if user.get("token"):
            self.session.headers["Authorization"] = f"Bearer {user['token']}"
        self.resources = {}

    def file(self, name, encoded):
        if not encoded:
            return None
        path = os.path.join(self.dir, name)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "wb") as f:
            f.write(base64.b64decode(encoded))
        return path

    def call(self, method, path, expect=range(200, 300), **kwargs):
        with slot():
            r = self.session.request(method, self.server + path, timeout=kwargs.pop("timeout", 30), **kwargs)
        if r.status_code not in expect:
            raise HttpError(f"{method} {path}: HTTP {r.status_code} {excerpt(r.text)}", r.status_code, r.text)
        return r

    def resource(self, api_version, kind):
        """(plural, namespaced) of a kind, found in the discovery document of its group: a kind the cluster does not know
        is an operator that is not installed yet."""
        key = (api_version, kind)
        if key not in self.resources:
            base = "/api/" + api_version if "/" not in api_version else "/apis/" + api_version
            r = self.call("GET", base, expect=(200, 404))
            found = [x for x in (r.json().get("resources") or []) if x.get("kind") == kind and "/" not in x["name"]] \
                if r.status_code == 200 else []
            if not found:
                raise SeedError(f"the cluster has no {kind} of {api_version}: is the operator installed?")
            self.resources[key] = (found[0]["name"], found[0]["namespaced"])
        return self.resources[key]

    def path(self, api_version, kind, namespace, name=None):
        plural, namespaced = self.resource(api_version, kind)
        base = "/api/" + api_version if "/" not in api_version else "/apis/" + api_version
        if namespaced:
            base += f"/namespaces/{namespace}"
        return f"{base}/{plural}" + (f"/{name}" if name else "")


def manifests():
    """The manifests of seed/k8s, in order, with their variables replaced."""
    out = []
    for path in files("k8s", suffixes=(".yaml", ".yml")):
        for doc in parse_yaml_all(render(path.read_text(encoding="utf-8"), path), str(path)):
            if doc:
                if not all(doc.get(k) for k in ("apiVersion", "kind")) or not doc.get("metadata", {}).get("name"):
                    raise SeedError(f"{path.name}: a manifest needs apiVersion, kind and metadata.name")
                out.append(doc)
    return out


def identity(doc):
    meta = doc["metadata"]
    return doc["apiVersion"], doc["kind"], meta.get("namespace"), meta["name"]


def label(doc):
    api_version, kind, namespace, name = identity(doc)
    return f"{kind} {namespace + '/' if namespace else ''}{name}"


def refused(status, text):
    """What a retry cannot fix: the cluster or the operator's webhook said no (a 5xx is a webhook that is not up yet)."""
    return status < 500 and status != 429


def generated(cluster, doc):
    """The manifest of a Secret with its generated keys filled: the value the cluster already holds, else a new one."""
    meta = doc["metadata"]
    wanted = [k.strip() for k in (meta.get("annotations") or {}).get(GENERATE, "").split(",") if k.strip()]
    if doc["kind"] != "Secret" or not wanted:
        return doc
    held = cluster.call("GET", cluster.path("v1", "Secret", meta["namespace"], meta["name"]), expect=(200, 404))
    data = (held.json().get("data") or {}) if held.status_code == 200 else {}
    string_data = dict(doc.get("stringData") or {})
    for key in wanted:
        string_data[key] = base64.b64decode(data[key]).decode() if data.get(key) else secrets.token_hex(16)
    annotations = {k: v for k, v in meta["annotations"].items() if k != GENERATE}
    return {**doc, "metadata": {**{k: v for k, v in meta.items() if k != "annotations"},
                                **({"annotations": annotations} if annotations else {})}, "stringData": string_data}


def apply(cluster, doc):
    api_version, kind, namespace, name = identity(doc)
    plural, namespaced = cluster.resource(api_version, kind)
    if namespaced and not namespace:
        raise SeedError(f"{label(doc)}: a {kind} needs metadata.namespace")
    path = cluster.path(api_version, kind, namespace, name)

    def attempt():
        try:
            cluster.call("PATCH", path, params={"fieldManager": FIELD_MANAGER, "force": "true"},
                         data=yaml.safe_dump(doc), headers={"Content-Type": "application/apply-patch+yaml"})
        except HttpError as e:
            if refused(e.status, e.body):
                raise Gone(f"{label(doc)}: {e}") from e
            raise

    wait_until(SECTION, f"{label(doc)} to be accepted by the cluster", attempt)
    return path


def status_of(cluster, api_version, kind, namespace, name):
    r = cluster.call("GET", cluster.path(api_version, kind, namespace, name))
    return r.json().get("status") or {}


def wait_accepted(cluster, doc):
    """A resource of the operator is ready once its Accepted condition is true; the operator's message says why not."""
    api_version, kind, namespace, name = identity(doc)
    if not api_version.startswith(OPERATOR_GROUP + "/"):
        return

    def check():
        conditions = {c.get("type"): c for c in status_of(cluster, api_version, kind, namespace, name).get("conditions") or []}
        accepted = conditions.get("Accepted")
        if accepted is None:
            raise SeedError("no status yet")
        if accepted.get("status") != "True":
            raise SeedError(f"{accepted.get('reason')}: {excerpt(accepted.get('message'), 300)}")

    wait_until(SECTION, f"{label(doc)} to be accepted by the operator", check)


def namespaces_of(records):
    return [x["name"] for x in records if x["kind"] == "namespace"]


def seed():
    if not has_seed("k8s"):
        return
    docs = manifests()
    cluster = Cluster(setting("KUBECONFIG"))
    wait_until(SECTION, "the Kubernetes API", lambda: cluster.call("GET", "/readyz", timeout=10))
    for doc in docs:
        api_version, kind, namespace, name = identity(doc)
        wait_until(SECTION, f"the cluster to know {kind}", lambda: cluster.resource(api_version, kind))
        apply(cluster, generated(cluster, doc))
        wait_accepted(cluster, doc)
        if kind == "Namespace" and api_version == "v1":
            record(SECTION, "namespace", "namespaces", name, name)
        else:
            record(SECTION, "resource", "Kubernetes resources", label(doc), name, apiVersion=api_version, kind_name=kind,
                   namespace=namespace)
        log(SECTION, f"✓ {label(doc)}")


# --- Removal ----------------------------------------------------------------------------

def operator_kinds(cluster):
    """(apiVersion, kind) of every namespaced kind of the operator's group."""
    groups = cluster.call("GET", "/apis").json().get("groups") or []
    out = []
    for group in groups:
        if group["name"] == OPERATOR_GROUP:
            version = group["preferredVersion"]["groupVersion"]
            for r in cluster.call("GET", f"/apis/{version}").json().get("resources") or []:
                if r.get("namespaced") and "/" not in r["name"] and "list" in r.get("verbs", []):
                    out.append((version, r["kind"], r["name"]))
    return out


def rank(kind):
    if kind in DELETE_FIRST:
        return DELETE_FIRST.index(kind)
    return len(DELETE_FIRST) + 1 if kind in DELETE_LAST else len(DELETE_FIRST)


def delete_and_wait(cluster, path, what):
    """Deletes one object and waits until it is gone: the operator removes the API, application or subscription from API
    Management first, and its webhook refuses to delete what another resource still uses, so a refusal is retried."""
    cluster.call("DELETE", path, expect=(200, 202, 404), timeout=60)

    def gone():
        if cluster.call("GET", path, expect=(200, 404)).status_code != 404:
            raise SeedError(f"{what} is still there")

    wait_until(SECTION, f"{what} to be deleted", gone)


def delete_retrying(cluster, path, what):
    """A refusal is retried too: the webhook says no while another resource still uses this one, and that one is going."""
    deadline = time.time() + READY_TIMEOUT
    while True:
        try:
            return delete_and_wait(cluster, path, what)
        except HttpError as e:
            if time.time() > deadline:
                raise SeedError(f"{what} cannot be deleted: {e}") from e
            pause(3)


def remove(records):
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    cluster = Cluster(setting("KUBECONFIG"))
    try:
        cluster.call("GET", "/readyz", timeout=10)
    except (requests.RequestException, SeedError) as e:
        log(SECTION, f"! The cluster does not answer ({excerpt(e, 200)}): what it holds is left as it is")
        return
    deleted = set()
    for namespace in namespaces_of(mine):
        found = []
        for api_version, kind, plural in operator_kinds(cluster):
            base = ("/apis/" + api_version) + f"/namespaces/{namespace}/{plural}"
            for item in cluster.call("GET", base, expect=(200, 404)).json().get("items") or []:
                found.append((rank(kind), api_version, kind, f"{base}/{item['metadata']['name']}", item["metadata"]["name"]))
        for _, api_version, kind, path, name in sorted(found, key=lambda x: x[0]):
            delete_retrying(cluster, path, f"{kind} {namespace}/{name}")
            deleted.add(path)
    resources = [x for x in mine if x["kind"] == "resource"]
    for item in reversed(resources):
        path = cluster.path(item["apiVersion"], item["kind_name"], item["namespace"], item["id"])
        if path in deleted:
            continue
        try:
            delete_retrying(cluster, path, item["name"])
        except SeedError as e:
            log(SECTION, f"! {item['name']}: {e}")
    for namespace in namespaces_of(mine):
        cluster.call("DELETE", f"/api/v1/namespaces/{namespace}", expect=(200, 202, 404))
        log(SECTION, f"✓ namespace {namespace} deleted")
