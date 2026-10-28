"""A fake Gravitee Cloud, to try the Lab's platforms without a Cloud account, and to test them without a network: the
management API of the accounts and Cloud Gate, on one port. Standard library only, and nothing is kept once it stops.

    python3 lab-ui/tests/fake_cloud.py --port 9990

Then register http://localhost:9990 in the Lab UI with the token fake-account-token and the Account ID
4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46. The account is my-account (My Account) with the environments Development, Test
and Production. A gateway created there is DEPLOYED and becomes CONNECTED a few seconds later, as if it had been
started. It is listed as the real Cloud lists a gateway: with its type, its scope (APIM) and the version of API
Management that the Cloud runs for its environment (--version). The token fake-member-token reads but is not allowed to create or delete (403). A second account, client-lab,
has the token fake-second-token and the environments Development and Test. The Cloud Tokens that open the environments
of Cloud Gate are printed at start.

The management API answers as the real Cloud does to an Account Token, which opens the paths under
/management/accounts/{uuid} of its own account and nothing else: the list of accounts, /me and the account's hrid answer
401. The environments it lists have no hrid and no status.

Cloud Gate (/apim/rest) takes a Cloud Token, a JWT with the claims of a real one (org, envs, scopes automation and rest,
aud CloudGate, exp). It answers 401 to a token that is revoked, expired, for another environment than the path's, or no
Cloud Token. A gateway's token carries claims too (scopes reports and sync, meta GATEWAYID). It serves what a seed does
to put an API in an environment and to take it out: the list, the import, the publication, the deployment, the start,
the stop and the deletion. Deploying an API whose type needs a feature that the organization's license lacks answers
403, as the real Cloud does."""

import argparse
import base64
import json
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = "fake-account-token"
MEMBER_TOKEN = "fake-member-token"
SECOND_TOKEN = "fake-second-token"
PATH = "/management"
GATE = "/apim/rest"
NAME = re.compile(r"^[a-zA-Z0-9\s\-_.]+$")
YEAR = 365 * 24 * 3600
SIGNATURE = "fake-signature"   # every token of the fake is signed with it, which is how it knows its own
VERSION = "4.12.20"   # the version of API Management its gateways answer, by default


def environments(organization, label, *named):
    """The environments of an organization, as the Cloud lists them: (id, name) pairs."""
    return [{"id": f"fake-{key}", "name": name, "organizationId": organization, "organizationName": label,
             "linked": False, "trial": False} for key, name in named]


ACCOUNT = {"id": "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46", "hrid": "my-account", "name": "My Account",
           "description": "The fake Cloud's account", "profile": "CLOUD", "installationId": "fake-installation"}
ENVIRONMENTS = environments("fake-organization", "My Account",
                            ("env-dev", "Development"), ("env-test", "Test"), ("env-prod", "Production"))
SECOND_ACCOUNT = {"id": "d7a34c19-52f0-4b86-8e1d-3c9a60b7f215", "hrid": "client-lab", "name": "Client Lab",
                  "description": "The fake Cloud's second account", "profile": "CLOUD",
                  "installationId": "fake-second-installation"}
SECOND_ENVIRONMENTS = environments("fake-second-organization", "Client Lab",
                                   ("client-dev", "Development"), ("client-test", "Test"))
# The features that the license of each organization gives, which decide what a deploy is allowed to put there
FEATURES = {"fake-organization": ["apim-bridge-gateway"], "fake-second-organization": ["apim-bridge-gateway", "mcp"]}
NEEDS = {"MCP_PROXY": "mcp"}   # the feature that deploying an API of a type needs
ID = "([^/]+)"
OWN = re.compile(r"/accounts/([^/]+)(?:/.*)?")   # the paths an Account Token may call: those of an account


# ---------------------------------------------------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------------------------------------------------

def _part(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def jwt(claims):
    """A JWT with these claims, signed with nothing that matters."""
    return f"{_part({'alg': 'RS256', 'typ': 'JWT'})}.{_part(claims)}.{SIGNATURE}"


def claims_of(token):
    """The claims of a token of the fake, None for any other."""
    parts = token.split(".")
    if len(parts) != 3 or parts[2] != SIGNATURE:
        return None
    try:
        found = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def organization_of(environment):
    return next((found["organizationId"] for found in ENVIRONMENTS + SECOND_ENVIRONMENTS
                 if found["id"] == environment), "fake-organization")


def cloud_token(environment, expires=YEAR):
    """A Cloud Token (type Automation) for an environment, valid for `expires` seconds: negative is one that expired."""
    return jwt({"org": organization_of(environment), "envs": [environment], "cpg": "eu",
                "scopes": ["automation", "rest"], "aud": "CloudGate", "jti": uuid.uuid4().hex,
                "exp": int(time.time()) + expires})


def gateway_token(gateway, environment):
    """The token of a hybrid gateway: reports and sync, with the gateway it belongs to."""
    return jwt({"org": organization_of(environment), "envs": [environment], "cpg": "eu",
                "scopes": ["reports", "sync"], "meta": {"GATEWAYID": gateway}, "aud": "CloudGate",
                "jti": uuid.uuid4().hex, "exp": int(time.time()) + YEAR})


CLOUD_TOKEN = cloud_token("fake-env-dev")                    # Development of My Account
CLOUD_TOKEN_TEST = cloud_token("fake-env-test")              # another environment of the same account
CLOUD_TOKEN_EXPIRED = cloud_token("fake-env-dev", expires=-3600)
SECOND_CLOUD_TOKEN = cloud_token("fake-client-test")         # Test of Client Lab
GATEWAY_TOKEN = gateway_token("fake-gateway", "fake-env-dev")   # a gateway token, pasted where a Cloud Token is asked
# An Account Token that carries claims, as one pasted where a Cloud Token is asked: another audience than Cloud Gate
ACCOUNT_JWT = jwt({"sub": "fake-user", "aud": "Cockpit", "jti": uuid.uuid4().hex, "exp": int(time.time()) + YEAR})
SECRETS = (TOKEN, MEMBER_TOKEN, SECOND_TOKEN, CLOUD_TOKEN, CLOUD_TOKEN_TEST, CLOUD_TOKEN_EXPIRED, SECOND_CLOUD_TOKEN,
           GATEWAY_TOKEN, ACCOUNT_JWT)   # every token of the module, apart from those of the gateways created

# What an Account Token opens: the account, its environments, and whether it may write
LOGINS = {TOKEN: (ACCOUNT, ENVIRONMENTS, True), MEMBER_TOKEN: (ACCOUNT, ENVIRONMENTS, False),
          ACCOUNT_JWT: (ACCOUNT, ENVIRONMENTS, True), SECOND_TOKEN: (SECOND_ACCOUNT, SECOND_ENVIRONMENTS, True)}


# ---------------------------------------------------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------------------------------------------------

class State:
    """What the fake Cloud holds: its gateways and the token of each, the APIs seeded through Cloud Gate, the Cloud
    Tokens it refuses as revoked, when each gateway was created, and every request it was asked, (method, path),
    whatever it answered."""

    def __init__(self, connect_after=5.0, version=VERSION):
        self.connect_after, self.gateways, self.tokens, self.lock = connect_after, {}, {}, threading.Lock()
        self.version = version
        self.apis, self.revoked, self.asked = {}, set(), []
        # What the Cloud says of what it offers: the plan of the account (gammaEnabled), the scopes of the access points
        # of its organizations, the license text, and what the Management API has installed, by kind, {id: deployed}
        self.subscription = {"maxNumberOrgs": 10, "maxNumberEnvs": 10, "gammaEnabled": False, "maxNumberSaasGateways": 10}
        self.scopes, self.license_text = ["APIM"], "fake-license"
        self.plugins = {"policy": {"rate-limit": True, "policy-interrupt": True, "ai-prompt-guard-rails": False},
                        "endpoint": {"http-proxy": True, "llm-proxy": False},
                        "entrypoint": {"http-proxy": True, "llm-proxy": False, "mcp": False},
                        "resource": {"cache": True, "ai-model-text-embedding": False}}
        self.license = {"tier": "universe", "packs": ["enterprise-policy"], "features": ["apim-policy-interrupt"]}

    def view(self, gateway, status=False):
        """A gateway as the Cloud lists it, with its connection when asked: it connects some seconds after it was
        created."""
        found = {key: value for key, value in gateway.items() if key != "created"}
        if status:
            connected = time.monotonic() - gateway["created"] >= self.connect_after
            found["connectionStatus"] = "CONNECTED" if connected else "NOT_CONNECTED"
        return found


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeGraviteeCloud"

    def log_message(self, fmt, *args):   # quiet
        pass

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def send(self, code, body=None):
        """A JSON answer, a text one when the body is text, an empty one without a body."""
        text = isinstance(body, str)
        data = body.encode() if text else json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "text/plain" if text else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def fail(self, code, message):
        self.send(code, {"httpCode": code, "message": message, "technicalCode": message.split(":")[0].lower()})

    def route(self, method):
        # The body is read before anything is refused: a client that sent it must still be able to read the refusal
        length = self.headers.get("Content-Length", "")
        self.body = self.rfile.read(min(int(length), 1 << 20)) if length.isdigit() else b""
        url = urlsplit(self.path)
        if url.path.startswith(GATE + "/"):
            return self.gate(method, url)
        if not url.path.startswith(PATH + "/"):
            return self.fail(404, "Not found")
        path = url.path[len(PATH):]
        with self.server.state.lock:
            self.server.state.asked.append((method, path))
        asked = self.headers.get("Authorization") or ""
        login = LOGINS.get(asked[len("Bearer "):] if asked.startswith("Bearer ") else "")
        if not login:
            return self.fail(401, "Unauthorized")
        self.account, self.environments, writes = login
        # An Account Token is no user: it opens its own account by its id, not the list of accounts, /me or the hrid
        own = OWN.fullmatch(path)
        if not own or own.group(1) != self.account["id"]:
            return self.fail(401, "Unauthorized")
        if method != "GET" and not writes:
            return self.fail(403, "Forbidden")
        for verb, pattern, handler in [
            ("GET", f"/accounts/{ID}", lambda account: self.send(200, self.account)),
            ("GET", f"/accounts/{ID}/environments", self.list_environments),
            ("GET", f"/accounts/{ID}/subscription", lambda account: self.send(200, self.server.state.subscription)),
            ("GET", f"/accounts/{ID}/organizations", self.list_organizations),
            ("GET", f"/accounts/{ID}/organizations/{ID}/license", self.organization_license),
            ("GET", f"/accounts/{ID}/gateways", lambda account: self.gateways(url.query)),
            ("POST", f"/accounts/{ID}/gateways", self.create),
            ("DELETE", f"/accounts/{ID}/gateways/{ID}", self.delete),
        ]:
            found = re.fullmatch(pattern, path) if verb == method else None
            if found:
                return handler(*found.groups())
        return self.fail(404, "Not found")

    def list_organizations(self, account):
        """The organizations of the account, each with the access points of the products it has (the scopes)."""
        seen = {}
        for environment in self.environments:
            seen.setdefault(environment["organizationId"], environment["organizationName"])
        self.send(200, [{"id": organization, "name": name, "accountId": account, "status": "ACTIVE",
                         "accessPoints": [{"target": target, "scope": scope, "host": f"{target.lower()}.fake"}
                                          for scope in self.server.state.scopes for target in ("CONSOLE", "CONSOLE_API")]}
                        for organization, name in seen.items()])

    def organization_license(self, account, organization):
        """The license of an organization, as the signed text the real Cloud answers (the fake's cannot be decoded)."""
        body = self.server.state.license_text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def list_environments(self, account):
        self.send(200, self.environments)

    def gateways(self, query):
        state = self.server.state
        with state.lock:
            self.send(200, [state.view(found, parse_qs(query).get("withStatus") == ["true"])
                            for found in state.gateways.values() if found["accountId"] == self.account["id"]])

    def create(self, account):
        try:
            body = json.loads(self.body or b"{}")
        except ValueError:
            return self.fail(400, "Bad request: the body is not JSON")
        if not isinstance(body, dict):
            return self.fail(400, "Bad request: the body is not an object")
        name = body.get("name")
        environment = next((env for env in self.environments if env["id"] == body.get("environmentId")), None)
        if not isinstance(name, str) or not 2 <= len(name) <= 128 or not NAME.match(name):
            return self.fail(400, "Bad request: name must have 2 to 128 characters matching ^[a-zA-Z0-9\\s\\-_.]+$")
        if not environment:
            return self.fail(400, "Bad request: environmentId is not an environment of this account")
        if body.get("scope") != "APIM" or body.get("type") != "HYBRID":
            return self.fail(400, "Bad request: the fake Cloud creates APIM hybrid gateways only")
        domains = body.get("domains") or []
        if not isinstance(domains, list) or not all(isinstance(domain, str) and domain for domain in domains):
            return self.fail(400, "Bad request: domains must be a list of names")
        gateway = {"id": str(uuid.uuid4()), "accountId": self.account["id"],
                   "organizationId": environment["organizationId"], "environmentId": environment["id"],
                   "name": name, "type": "HYBRID", "scope": "APIM",
                   "domains": [{"id": str(uuid.uuid4()), "domain": domain, "type": "HYBRID", "protocol": "HTTP",
                                "secured": False} for domain in domains],
                   "status": "DEPLOYED", "version": self.server.state.version, "created": time.monotonic()}
        state, token = self.server.state, gateway_token(gateway["id"], environment["id"])
        with state.lock:
            state.gateways[gateway["id"]], state.tokens[gateway["id"]] = gateway, token
        self.send(200, {"gateway": state.view(gateway), "token": token})

    def delete(self, account, gateway):
        state = self.server.state
        with state.lock:
            found = state.gateways.get(gateway)
            if found and found["accountId"] == self.account["id"]:
                del state.gateways[gateway]
            else:
                found = None
        if found is None:
            return self.fail(404, "Gateway not found")
        self.send(200)

    # --- Cloud Gate ----------------------------------------------------------------------------------------------

    def cloud_claims(self):
        """The claims of the Cloud Token of the request, None when the Gate refuses it: not one of the fake, revoked,
        not a Cloud Token, or expired."""
        asked = self.headers.get("Authorization") or ""
        token = asked[len("Bearer "):] if asked.startswith("Bearer ") else ""
        found = claims_of(token)
        if not found or token in self.server.state.revoked or found.get("aud") != "CloudGate" \
                or "automation" not in (found.get("scopes") or []):
            return None
        expiry = found.get("exp")
        return found if isinstance(expiry, int) and expiry > time.time() else None

    def gate(self, method, url):
        with self.server.state.lock:
            self.server.state.asked.append((method, url.path))
        found = self.cloud_claims()
        if found is None:
            return self.fail(401, "Unauthorized")
        # What the Management API has installed and the license of the organization: asked of the organization
        elsewhere = re.fullmatch(f"{GATE}/v2/organizations/{ID}/(?:plugins/(policies|endpoints|entrypoints|resources)|(license))", url.path)
        if elsewhere and method == "GET":
            organization, kind, license = elsewhere.groups()
            if organization != found.get("org"):
                return self.fail(401, "Unauthorized")   # a Cloud Token opens its own organization
            state = self.server.state
            if license:
                return self.send(200, state.license)
            name = {"policies": "policy", "endpoints": "endpoint", "entrypoints": "entrypoint", "resources": "resource"}[kind]
            return self.send(200, [{"id": plugin, "name": plugin, "version": "4.12.20", "deployed": deployed}
                                   for plugin, deployed in state.plugins[name].items()])
        scope = re.fullmatch(f"{GATE}/v2/organizations/{ID}/environments/{ID}(/.*)?", url.path)
        if not scope:
            return self.fail(404, "Not found")
        organization, environment, rest = scope.groups()
        if organization != found.get("org") or [environment] != found.get("envs"):
            return self.fail(401, "Unauthorized")   # a Cloud Token opens its own environment
        self.scope = (organization, environment)
        for verb, pattern, handler in [
            ("GET", "/apis", lambda: self.apis(url.query)),
            ("POST", "/apis/_import/definition", self.import_api),
            ("GET", f"/apis/{ID}", self.api),
            ("PUT", f"/apis/{ID}", self.update_api),
            ("POST", f"/apis/{ID}/deployments", self.deploy),
            ("POST", f"/apis/{ID}/_start", self.start),
            ("POST", f"/apis/{ID}/_stop", self.stop),
            ("DELETE", f"/apis/{ID}", self.remove),
        ]:
            matched = re.fullmatch(pattern, rest or "") if verb == method else None
            if matched:
                return handler(*matched.groups())
        return self.fail(404, "Not found")

    def held(self, api):
        """The API of this environment with this id, None for none: it answers 404 for the caller."""
        found = self.server.state.apis.get(api)
        if found and (found["organizationId"], found["environmentId"]) == self.scope:
            return found
        self.fail(404, f"Api [{api}] cannot be found")

    def body_object(self):
        """The JSON object the request sent, None after it answered 400."""
        try:
            body = json.loads(self.body or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self.fail(400, "Bad request: the body is not a JSON object")
        return body if isinstance(body, dict) else None

    def apis(self, query):
        per = parse_qs(query).get("perPage", ["10"])[0]
        size = int(per) if per.isdigit() and int(per) > 0 else 10
        state = self.server.state
        with state.lock:
            mine = [found for found in state.apis.values()
                    if (found["organizationId"], found["environmentId"]) == self.scope]
        self.send(200, {"data": mine[:size], "pagination": {
            "page": 1, "perPage": size, "pageCount": -(-len(mine) // size) or 1, "pageItemsCount": len(mine[:size]),
            "totalCount": len(mine)}})

    def import_api(self):
        body = self.body_object()
        if body is None:
            return
        definition = body.get("api")
        if not isinstance(definition, dict) or not isinstance(definition.get("name"), str) or not definition["name"]:
            return self.fail(400, "Bad request: the definition holds no api with a name")
        found = {"id": definition["id"] if isinstance(definition.get("id"), str) and definition["id"]
                 else str(uuid.uuid4()),
                 "name": definition["name"], "type": definition.get("type") or "PROXY", "state": "STOPPED",
                 "lifecycleState": "CREATED", "deploymentState": "NEED_REDEPLOY",
                 "organizationId": self.scope[0], "environmentId": self.scope[1]}
        with self.server.state.lock:
            self.server.state.apis[found["id"]] = found
        self.send(201, found)

    def api(self, api):
        found = self.held(api)
        if found:
            self.send(200, found)

    def update_api(self, api):
        found = self.held(api)
        body = self.body_object() if found else None
        if body is not None:
            if isinstance(body.get("lifecycleState"), str):
                found["lifecycleState"] = body["lifecycleState"]
            self.send(200, found)

    def deploy(self, api):
        found = self.held(api)
        if not found:
            return
        needed = NEEDS.get(found["type"])
        if needed and needed not in FEATURES.get(self.scope[0], []):
            return self.fail(403, f"Feature '{needed}' is not available with your license tier")
        found["deploymentState"] = "DEPLOYED"
        self.send(202, found)

    def start(self, api):
        found = self.held(api)
        if found:
            found["state"] = "STARTED"
            self.send(204)

    def stop(self, api):
        found = self.held(api)
        if found:
            found["state"] = "STOPPED"
            self.send(204)

    def remove(self, api):
        if self.held(api):
            with self.server.state.lock:
                del self.server.state.apis[api]
            self.send(204)


def serve(port=0, connect_after=5.0, version=VERSION):
    """The fake Cloud listening on 127.0.0.1, not started: serve_forever() runs it. `.state` holds its gateways."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.state = State(connect_after, version)
    return server


def main():
    parser = argparse.ArgumentParser(description="A fake Gravitee Cloud")
    parser.add_argument("--port", type=int, default=9990)
    parser.add_argument("--connect-after", type=float, default=5.0,
                        help="seconds after which a new gateway shows as connected (5)")
    parser.add_argument("--version", default=VERSION,
                        help=f"the version of API Management that a new gateway answers ({VERSION})")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # the tokens it prints are for whoever reads its output as it starts
    server = serve(args.port, args.connect_after, args.version)
    print(f"Fake Gravitee Cloud on http://localhost:{args.port}  token: {TOKEN}  Account ID: {ACCOUNT['id']}")
    print(f"Second account  token: {SECOND_TOKEN}  Account ID: {SECOND_ACCOUNT['id']}")
    for account, found in ((ACCOUNT, ENVIRONMENTS), (SECOND_ACCOUNT, SECOND_ENVIRONMENTS)):
        for env in found:
            print(f"Cloud Token of {env['name']} ({account['name']}): {cloud_token(env['id'])}")
    print("(Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
