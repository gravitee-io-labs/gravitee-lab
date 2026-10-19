"""What an agent of the catalog needs to sign in and to call an MCP server, from the keys `identity` and `access` of
seed/aim/agents/*.yaml. The card and the `metadata` of the same file are aim_catalog's, which registered the agent
before; a file with neither key is left to it.

    identity:
      clientId: care-copilot                       # the AM application the agent signs in with
    access:
      - {studio: Customer care, plan: Agents}      # a subscription to a plan of an MCP studio (aim/studios)

Three steps for each agent, each writing only what is missing:

    1. The application of API Management that acts for the agent (type AGENT). The gateway attributes the agent's
       traffic to it, and its subscriptions are the access the agent holds: pausing them is the kill switch of Agent
       Management.
    2. The identity: the AM application with that client id, in the domain of seed/am/gamma-link.yaml, attached to the
       application, whose client id becomes the identity's. A JWT plan matches a token to its subscription by that
       client id. Gamma attaches it through its link to Access Management, which am_link sets up before this module
       runs.
    3. The access: a subscription of the application to the plan of each studio, accepted when the plan asks for it.

The seed records the application and the subscriptions it makes, and changes nothing else. An application that acts for
the agent and was not made by it stops the seed, as does an identity of another client id attached to the application.
The AM application is seed/am's, which makes it.

Re-seed starts the lessons over, and the last of them is the kill switch: an agent whose access was revoked is put back
in service before its subscriptions are looked at, with the platform's own restore, which resumes what revoking
paused. A subscription that exists, whatever its status, is otherwise left as it is.
"""

from . import am
from .common import SeedError, excerpt, files, has_seed, items, load, log, owned, record, recorded, seed_file
from .gamma import CATALOG, WORKSPACES, Gamma

SECTION = "aim-agents"
DEPENDS = ("am", "am_link", "aim_catalog", "aim_proxies", "apim")   # the modules whose work this one reads on the platform (schedule.py)
# The folder of the seed it reads: aim_catalog reads it too, for the card of each agent
FOLDERS = ("aim/agents",)
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("aim",)
# What it records, dependents first
ORDER = ["subscription", "agent-application"]
# What the Lab UI calls each kind when it asks whether to delete them: plural, ending in an s
LABELS = {"subscription": "agent subscriptions", "agent-application": "agent applications"}
# The statuses of a subscription that holds the access: a closed or a rejected one is none
HELD = ("PENDING", "ACCEPTED", "PAUSED")
# What closing a subscription answers when it is done: or closed already (400), or gone with its studio (404)
CLOSED = (200, 202, 204, 400, 404)
# What detaching an identity answers when there is nothing to do or to reach: no identity attached (409), the agent or
# its application gone (404), Access Management out of reach (502)
DETACHED = (200, 404, 409, 502)


# --- What the files ask ---------------------------------------------------------------------

def wanted():
    """The agent files that give an identity or access, checked before any call. The other files of the folder are
    aim_catalog's alone."""
    found = []
    for path in files("aim", "agents"):
        spec = load(path)
        if not {"identity", "access"} & set(spec):
            continue
        if not spec.get("entityId"):
            raise SeedError(f"{path.name}: entityId missing")
        if "identity" in spec and not (isinstance(spec["identity"], dict) and spec["identity"].get("clientId")):
            raise SeedError(f"{path.name}: identity needs a clientId")
        access = spec.get("access") or []
        if not isinstance(access, list) or not all(isinstance(a, dict) and a.get("studio") and a.get("plan")
                                                   for a in access):
            raise SeedError(f"{path.name}: access is a list of {{studio, plan}}")
        found.append({**spec, "access": access})
    if any("identity" in spec for spec in found) and not seed_file("am", "gamma-link.yaml"):
        raise SeedError("An agent's identity is an application of Access Management: seed/am/gamma-link.yaml names the "
                        "domain that holds it")
    return found


# --- Access Management ----------------------------------------------------------------------

class Identities:
    """The applications of the AM domain of the playground (seed/am/gamma-link.yaml), where an agent's identity is."""

    def __init__(self):
        domain = load(seed_file("am", "gamma-link.yaml")).get("domain")
        if not domain:
            raise SeedError("seed/am/gamma-link.yaml: domain missing")
        self.access = am.AccessManagement()
        self.access.connect()
        found = self.access.find_domain(domain)
        self.domain = found.get("hrid") or found["name"]
        self.url = f"{self.access.env_url}/domains/{found['id']}"

    def find(self, client_id):
        """The id of the application with this client id, which is the identity the catalog attaches. None for none."""
        api = self.access.api
        for candidate in items(api.get(f"{self.url}/applications", params={"q": client_id, "size": 200}).json()):
            detail = api.get(f"{self.url}/applications/{candidate['id']}").json()
            if ((detail.get("settings") or {}).get("oauth") or {}).get("clientId") == client_id:
                return detail["id"]
        return None


# --- The agents -----------------------------------------------------------------------------

def note(agent, name, application_id, **extra):
    """Records the application of an agent: with the identity too once it is attached, which a removal detaches."""
    record(SECTION, "agent-application", LABELS["agent-application"], name, application_id, agent=agent["id"], **extra)


class Agents:
    def __init__(self):
        self.g = Gamma()
        self.aim = self.g.aim
        self.identities = None   # connected when the first agent asks for an identity
        self.studios = None      # the MCP studios, listed once

    def connect(self):
        self.g.connect(SECTION, lambda: self.aim.get("/catalog/agents", params={"perPage": 1}), "Agents")

    def apply(self, spec):
        agent = self.agent(spec)
        name = agent["definition"]["name"]
        application = self.application(agent, name)
        if "identity" in spec:
            application = self.identity(agent, name, application, spec["identity"]["clientId"])
        self.restore(agent, name)
        for access in spec["access"]:
            self.subscribe(name, application, access)

    def agent(self, spec):
        wanted_id = f"agent.{spec['entityId']}"
        found = next((a for a in self.aim.rows("/catalog/agents", CATALOG) if a["entityId"] == wanted_id), None)
        if found is None:
            raise SeedError(f"The agent '{wanted_id}' is not in the catalog: aim_catalog registers it from the same "
                            "file")
        return found

    # --- Step 1: the application that acts for the agent ----------------------------------

    def read_application(self, agent_id):
        answer = self.aim.get(f"/catalog/agents/{agent_id}/application", expect=(200, 404))
        return answer.json() if answer.status_code == 200 else None

    def application(self, agent, name):
        """The application of the agent, made when there is none. One that exists was made by the seed or it stops it:
        the subscriptions it will hold and the identity it presents are not the seed's to take over."""
        found = self.read_application(agent["id"])
        if found is None:
            answer = self.aim.post(f"/catalog/agents/{agent['id']}/application", json={}, expect=(200, 201, 409))
            if answer.status_code != 409:
                made = answer.json()
                note(agent, name, made["applicationId"])
                log(SECTION, f"✓ Agent application created: {name}")
                return made
            found = self.read_application(agent["id"])   # one appeared since the first look
            if found is None:
                raise SeedError(f"The application of the agent '{name}': HTTP 409 {excerpt(answer.text)}")
        if not recorded(SECTION, "agent-application", found["applicationId"]):
            raise SeedError(f"An application already acts for the agent '{name}' and was not created by this "
                            "playground: delete it, the seed leaves it alone")
        log(SECTION, f"Agent application exists: {name}")
        return found

    # --- Step 2: the identity -------------------------------------------------------------

    def identity(self, agent, name, application, client_id):
        """Attaches the AM application of that client id. An application that presents it already is left as it is."""
        if self.identities is None:
            self.identities = Identities()
        identity_id = self.identities.find(client_id)
        if identity_id is None:
            raise SeedError(f"No application of the AM domain '{self.identities.domain}' has the client id "
                            f"'{client_id}': seed/am/applications makes it before the agent takes it as its identity")
        presented = application.get("identityId")
        if presented == identity_id:
            log(SECTION, f"  Identity attached: {client_id}")
            return application
        if presented:
            raise SeedError(f"The application of the agent '{name}' presents another identity than the client id "
                            f"'{client_id}': detach it in Agent Identity, the seed leaves it alone")
        attached = self.aim.put(f"/catalog/agents/{agent['id']}/application/identity",
                                json={"identityId": identity_id}).json()
        note(agent, name, application["applicationId"], identity=identity_id)
        log(SECTION, f"  ✓ Identity attached: {client_id}")
        return {**application, **attached}

    # --- Step 3: the access ---------------------------------------------------------------

    def restore(self, agent, name):
        """Puts an agent whose access was revoked back in service. Revoking marks the agent inactive and pauses the
        subscriptions of its application, and restoring undoes both. It is asked only for an agent the catalog says is
        inactive, and only after the application was found to be the seed's."""
        if self.aim.get(f"/catalog/agents/{agent['id']}/access").json().get("state") != "INACTIVE":
            return
        outcomes = self.aim.post(f"/catalog/agents/{agent['id']}/access/_restore").json().get("outcomes") or []
        failed = [o.get("error") or o.get("target") for o in outcomes if not o.get("succeeded")]
        if failed:
            raise SeedError(f"The access of the agent '{name}' could not be restored: {excerpt(failed[0], 200)}")
        log(SECTION, f"  ✓ Access restored: {name}")

    def studio(self, name):
        if self.studios is None:
            self.studios = [p for p in self.aim.rows("/mcp-proxies", WORKSPACES) if p.get("mode") == "STUDIO"]
        found = [s for s in self.studios if s["name"] == name]
        if not found:
            raise SeedError(f"The MCP studio '{name}' is not there: aim/studios makes it before the agent subscribes")
        if len(found) > 1:
            raise SeedError(f"{len(found)} MCP studios are named '{name}': the agent cannot tell which one to "
                            "subscribe to")
        return found[0]

    def plan(self, studio, name):
        plans = items(self.aim.get(f"/mcp-proxies/{studio['id']}/plans").json())
        found = next((p for p in plans if p["name"] == name and p.get("status") == "PUBLISHED"), None)
        if found is None:
            raise SeedError(f"The MCP studio '{studio['name']}' has no published plan '{name}'")
        return found

    def subscribe(self, agent_name, application, access):
        """Subscribes the application to the plan unless it holds a subscription to it. One the seed made and left
        pending, because the plan asks for a person to accept it, is accepted."""
        studio = self.studio(access["studio"])
        plan = self.plan(studio, access["plan"])
        label = f"{agent_name} → {studio['name']} ({plan['name']})"
        path, app = f"/mcp-proxies/{studio['id']}/subscriptions", application["applicationId"]
        # A subscription names the plan by the id of the APIM plan behind the studio's
        plan_ids = {i for i in (plan["id"], plan.get("apimPlanId")) if i}
        held = [s for s in self.aim.rows(path, WORKSPACES, applicationId=app)
                if s["applicationId"] == app and s["planId"] in plan_ids and s["status"] in HELD]
        if held:
            if held[0]["status"] == "PENDING" and recorded(SECTION, "subscription", held[0]["id"]):
                self.aim.post(f"{path}/{held[0]['id']}/_accept", json={})
                log(SECTION, f"  ✓ Subscription accepted: {label}")
            else:
                log(SECTION, f"  Subscription exists: {label}")
            return
        made = self.aim.post(path, json={"applicationId": app, "planId": plan["id"]}).json()
        record(SECTION, "subscription", LABELS["subscription"], label, made["id"], api=studio["id"], application=app)
        if made.get("status") == "PENDING":
            self.aim.post(f"{path}/{made['id']}/_accept", json={})
        log(SECTION, f"  ✓ Subscribed: {label}")

    # --- Removal --------------------------------------------------------------------------

    def detach(self, x, records):
        """Detaches the identity that the record `x` of an application says the seed attached, when the AM application
        is one that seed/am made: the platform deletes the identity it detaches, which no module but seed/am may
        delete. An identity that existed before stays attached to an application that is deleted next, which ends the
        link too."""
        identity = x.get("identity")
        made = any((r["product"], r["kind"], r["id"]) == (am.SECTION, "agent", identity) for r in records)
        if not (identity and made):
            return
        answer = self.aim.call("DELETE", f"/catalog/agents/{x['agent']}/application/identity", expect=DETACHED)
        if answer.status_code == 502:
            log(SECTION, f"! Access Management did not answer: the identity of '{x['name']}' stays attached until its "
                         "application is deleted")
        elif answer.status_code == 200 and answer.json().get("result") == "PARTIAL":
            log(SECTION, f"! The identity of '{x['name']}' is detached and stays in Access Management: "
                         f"{excerpt(answer.json().get('error', ''), 200)}")


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    asked = wanted()
    if not asked:
        return   # the agents of the folder are aim_catalog's alone
    agents = Agents()
    agents.connect()
    for spec in asked:
        agents.apply(spec)


def remove(records):
    """Closes the subscriptions the seed made, then detaches the identity it attached when it can, then deletes the
    application: deleting it is what ends the link to an identity that stays. What is gone is no error."""
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    agents = Agents()
    agents.connect()
    for x in mine:
        if x["kind"] == "subscription":
            agents.aim.post(f"/mcp-proxies/{x['api']}/subscriptions/{x['id']}/_close", expect=CLOSED)
            log(SECTION, f"✓ Closed subscription '{x['name']}'")
        else:
            agents.detach(x, records)
            agents.g.v1.delete(f"/applications/{x['id']}")
            log(SECTION, f"✓ Deleted agent application '{x['name']}'")
