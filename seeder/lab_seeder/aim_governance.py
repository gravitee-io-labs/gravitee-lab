"""Who decides what an agent may not decide alone, and what a regulation asks of it, from seed/aim/governance/:

    approvals.yaml     settings    how long an approval stays actionable, and what happens when it lapses
                       rules       the tool calls that wait in the inbox (Govern > HITL) for a person to approve, edit
                                   or reject
    compliance.yaml    frameworks  the regulations to switch on (eu-ai-act)
                       agents      for each agent of the catalog: `metadata`, what only a person can say of it (its
                                   `riskClassification` is not here: the agent's file in aim/agents holds it), and
                                   `attest`, the controls the team vouches for because the platform cannot read them yet

A rule names a tool (`tool`: the name a client calls, which is the alias that a studio gives it (aim/studios), and, to show
on the tool page of the catalog, the name there too; `*` for any run of characters, `|` between alternatives) and
conditions on the arguments of the call (`when`). It applies on every MCP server that exposes a tool of that name, and
holds a call only where the studio has human approval switched on (aim/studios).

Everything is found first and written only when it differs, so a second run writes nothing.

    Rules         found by name. One the seed did not make stops it; one it made is replaced when it differs from the
                  file.
    Settings      written when the platform holds other values. A value the file does not name stays what it is.
    Frameworks    switched on when they are off. One that was on already is not the seed's, and stays on.
    Answers       an agent's `metadata` is written when it changed since the last run, and never for a key the file does
                  not name: a value that a person sets in the console stays. `{user: admin}` is the id of that user of
                  API Management, which is what the control for an accountable human holds.
    Attestations  made for each control of `attest` that the platform reports NOT_ASSESSED. An attestation does not
                  lift a control the platform measures itself, so one it measures is left as it is.

The seed records the rules, the settings it changed (with the values they had), the frameworks it switched on and the
answers it wrote (with a fingerprint of them). A removal deletes the rules, puts the settings back unless someone
changed them since, and switches the frameworks off. The answers and the attestations belong to the agent, which
aim_catalog deletes.
"""

from .aim_catalog import fingerprint
from .common import SeedError, created, has_seed, items, load, log, owned, record, seed_file
from .gamma import CATALOG, Gamma

SECTION = "aim-governance"
DEPENDS = ("aim_catalog", "aim_agents")   # the modules whose work this one reads on the platform (schedule.py)
# The folder of the seed it owns
FOLDERS = ("aim/governance",)
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("aim",)
# What it records, dependents first
ORDER = ["approval-rule", "approval-settings", "compliance-answers", "compliance-framework"]
# What the Lab UI calls each kind when it asks whether to delete them: plural, ending in an s
LABELS = {"approval-rule": "approval rules", "approval-settings": "approval settings",
          "compliance-answers": "compliance answers", "compliance-framework": "compliance frameworks"}
EXPIRIES = ("DENY", "ALLOW")
SETTINGS = ("defaultTtlSeconds", "defaultOnExpiry", "approvalCost")
# The statuses of a control that an attestation has nothing to add to
MET = {"ATTESTED", "SATISFIED"}


# --- What the files say, checked before any call --------------------------------------------

def invalid(file, problem):
    return SeedError(f"{file}: {problem}")


def mapping(file, spec):
    if not isinstance(spec, dict):
        raise invalid(file, "a mapping of keys to values is expected")
    return spec


def whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def settings_of(spec):
    """The `settings` of approvals.yaml."""
    if spec is None:
        return {}
    if not isinstance(spec, dict) or set(spec) - set(SETTINGS):
        raise invalid("approvals.yaml", f"settings are {', '.join(SETTINGS)}")
    if "defaultTtlSeconds" in spec and not (whole(spec["defaultTtlSeconds"]) and spec["defaultTtlSeconds"] >= 1):
        raise invalid("approvals.yaml", "defaultTtlSeconds is a number of seconds, 1 or more")
    if "defaultOnExpiry" in spec and spec["defaultOnExpiry"] not in EXPIRIES:
        raise invalid("approvals.yaml", f"defaultOnExpiry is {' or '.join(EXPIRIES)}")
    if "approvalCost" in spec and not (number(spec["approvalCost"]) and spec["approvalCost"] >= 0):
        raise invalid("approvals.yaml", "approvalCost is a number, 0 or more")
    return spec


def rule_body(spec):
    """What the platform is sent for a rule of the file: `tool` is its toolPattern and `when` its argumentConditions."""
    if not isinstance(spec, dict) or not spec.get("name") or not spec.get("tool"):
        raise invalid("approvals.yaml", "a rule needs a name and a tool")
    name, conditions = spec["name"], spec.get("when") or []
    if not isinstance(conditions, list) or not all(
            isinstance(c, dict) and {"path", "operator", "value"} <= set(c) for c in conditions):
        raise invalid("approvals.yaml", f"the rule '{name}': when is a list of {{path, operator, value}}")
    if "onExpiry" in spec and spec["onExpiry"] not in EXPIRIES:
        raise invalid("approvals.yaml", f"the rule '{name}': onExpiry is {' or '.join(EXPIRIES)}")
    body = {"name": name, "toolPattern": spec["tool"], "enabled": spec.get("enabled", True),
            "argumentConditions": [{"path": c["path"], "operator": c["operator"], "value": str(c["value"])}
                                   for c in conditions]}
    body.update({key: spec[key] for key in ("description", "ttlSeconds", "onExpiry", "priority", "cost")
                 if key in spec})
    return body


def rules_of(spec):
    bodies = [rule_body(rule) for rule in spec.get("rules") or []]
    names = [body["name"] for body in bodies]
    if len(set(names)) != len(names):
        raise invalid("approvals.yaml", "two rules have the same name")
    return bodies


def compliance_of(spec):
    """(frameworks, agents) of compliance.yaml."""
    frameworks, agents = spec.get("frameworks") or [], spec.get("agents") or []
    if not isinstance(frameworks, list) or not all(isinstance(f, str) and f for f in frameworks):
        raise invalid("compliance.yaml", "frameworks is a list of framework ids")
    for entry in agents:
        if not isinstance(entry, dict) or not entry.get("agent"):
            raise invalid("compliance.yaml", "an agent needs the entity id of the agent in the catalog")
        who = entry["agent"]
        metadata = entry.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise invalid("compliance.yaml", f"the metadata of '{who}' is a mapping of keys to values")
        if "riskClassification" in metadata:
            raise invalid("compliance.yaml", f"riskClassification of '{who}' is set on the agent: write it in the "
                                             "metadata of its file in aim/agents")
        for key, value in metadata.items():
            if isinstance(value, dict) and ("user" not in value or set(value) - {"user", "source"}):
                raise invalid("compliance.yaml", f"the value of {key} for '{who}' is a text or {{user: ...}}")
        attest = entry.get("attest") or []
        if not isinstance(attest, list) or not all(isinstance(control, str) and control for control in attest):
            raise invalid("compliance.yaml", f"attest of '{who}' is a list of control ids")
    return frameworks, agents


# --- What the platform holds ----------------------------------------------------------------

def conditions_of(rule):
    return [{key: c.get(key) for key in ("path", "operator", "value")} for c in rule.get("argumentConditions") or []]


def differs(found, body):
    """Whether the rule the platform holds is not the one the file describes, on the keys the file gives."""
    return any(conditions_of(found) != value if key == "argumentConditions" else found.get(key) != value
               for key, value in body.items())


def statuses(assessment):
    """{control id: its statuses} over the frameworks of an assessment: a control may be in more than one."""
    found = {}
    for framework in assessment.get("frameworks") or []:
        for control in framework.get("controls") or []:
            found.setdefault(control["controlId"], set()).add(control["status"])
    return found


def stored(kind, key=None):
    """The record of the seed of this kind, with this id when one is given."""
    return next((x for x in created() if (x["product"], x["kind"]) == (SECTION, kind) and key in (None, x["id"])), None)


class Governance:
    def __init__(self):
        self.g = Gamma()
        self.aim = self.g.aim
        self.agents = None   # the agents of the catalog, listed once

    def connect(self):
        self.g.connect(SECTION, lambda: self.aim.get("/approval-settings"), "Approvals and compliance")

    # --- Approvals ------------------------------------------------------------------------

    def settings(self, wanted):
        """A replace without the price of a decision would clear it, so a value the file does not name is sent as it
        is. The values before the first write are recorded, to put them back."""
        if not wanted:
            return
        current = self.aim.get("/approval-settings").json()
        before = {key: current.get(key) for key in SETTINGS}
        after = {key: wanted.get(key, before[key]) for key in SETTINGS}
        if before == after:
            log(SECTION, "Approval settings as the file says")
            return
        self.aim.put("/approval-settings", json={key: value for key, value in after.items() if value is not None})
        earlier = stored("approval-settings")
        record(SECTION, "approval-settings", LABELS["approval-settings"], "Approval settings", "approval-settings",
               before=earlier["before"] if earlier else before, wrote=after)
        log(SECTION, f"✓ Approval settings: {after['defaultTtlSeconds']} s, {after['defaultOnExpiry']} on expiry")

    def rules(self, bodies):
        live = self.aim.rows("/approval-rules", CATALOG) if bodies else []
        ours = {x["id"] for x in created() if (x["product"], x["kind"]) == (SECTION, "approval-rule")}
        for body in bodies:
            name = body["name"]
            named = [rule for rule in live if rule["name"] == name]
            found = next((rule for rule in named if rule["id"] in ours), None)
            if named and found is None:
                raise SeedError(f"The approval rule '{name}' exists and was not created by this playground: rename or "
                                "delete it, the seed leaves it alone")
            if found is None:
                made = self.aim.post("/approval-rules", json=body).json()
                record(SECTION, "approval-rule", LABELS["approval-rule"], name, made["id"])
                log(SECTION, f"✓ Approval rule created: {name}")
            elif differs(found, body):
                self.aim.put(f"/approval-rules/{found['id']}", json=body)
                log(SECTION, f"✓ Approval rule updated: {name}")
            else:
                log(SECTION, f"Approval rule up to date: {name}")

    # --- Compliance -----------------------------------------------------------------------

    def activate(self, frameworks):
        if not frameworks:
            return
        active = {a["frameworkId"] for a in items(self.aim.get("/compliance/activations").json())}
        for framework in frameworks:
            if framework in active:
                log(SECTION, f"Framework on: {framework}")
                continue
            made = self.aim.post("/compliance/activations", json={"frameworkId": framework}).json()
            record(SECTION, "compliance-framework", LABELS["compliance-framework"], framework, made["id"])
            log(SECTION, f"✓ Framework on: {framework}")

    def agent(self, slug):
        if self.agents is None:
            self.agents = self.aim.rows("/catalog/agents", CATALOG)
        found = next((a for a in self.agents if a["entityId"] == f"agent.{slug}"), None)
        if found is None:
            raise SeedError(f"The agent 'agent.{slug}' is not in the catalog: seed/aim/agents registers it before")
        return found

    def user(self, name, source):
        users = self.g.org_v1.get("/users", params={"q": name, "size": 100}).json().get("data", [])
        found = [u for u in users if u.get("sourceId") == name and source in (None, u.get("source"))]
        if not found:
            raise SeedError(f"The user '{name}' is not known to API Management: it signs in once first")
        if len(found) > 1:
            sources = ", ".join(sorted(u["source"] for u in found))
            raise SeedError(f"The user '{name}' is in more than one source ({sources}): "
                            f"write it {{user: {name}, source: ...}}")
        return found[0]["id"]

    def values(self, metadata):
        """What is sent for an agent's `metadata`: strings, and for {user: admin} the id of that user."""
        values = {}
        for key, value in metadata.items():
            if isinstance(value, dict):
                values[key] = self.user(value["user"], value.get("source"))
            else:
                values[key] = "" if value is None else str(value)
        return values

    def agent_compliance(self, entry):
        slug = entry["agent"]
        agent = self.agent(slug)
        name, base = agent["definition"]["name"], f"/compliance/agents/{agent['id']}"
        values = self.values(entry.get("metadata") or {})
        digest, earlier, assessment = fingerprint(values), stored("compliance-answers", agent["id"]), None
        if values and (earlier is None or earlier.get("digest") != digest):
            assessment = self.aim.put(f"{base}/metadata", json={"values": values}).json()
            record(SECTION, "compliance-answers", LABELS["compliance-answers"], name, agent["id"], digest=digest)
            log(SECTION, f"✓ Compliance answers written: {name}")
        elif values:
            log(SECTION, f"Compliance answers up to date: {name}")
        for control in entry.get("attest") or []:
            assessment = self.attest(base, name, control, assessment or self.aim.get(f"{base}/assessment").json())

    def attest(self, base, name, control, assessment):
        """Attests the control when the platform cannot read it. Returns the assessment as it stands."""
        found = statuses(assessment).get(control)
        if found is None:
            raise SeedError(f"The control '{control}' is in no framework that is on: it cannot be attested for "
                            f"'{name}'")
        if "NOT_ASSESSED" in found:
            log(SECTION, f"  ✓ Attested: {control}")
            return self.aim.post(f"{base}/controls/{control}/attestation").json()
        if found <= MET:
            log(SECTION, f"  Attested already: {control}")
        else:
            log(SECTION, f"  ! {control} is {' and '.join(sorted(found)).lower()}: the platform measures it itself, "
                         "an attestation does not lift it")
        return assessment

    # --- Removal --------------------------------------------------------------------------

    def restore(self, x):
        """Puts the settings back to what they were, unless someone set other values since the seed did."""
        current = self.aim.get("/approval-settings").json()
        if {key: current.get(key) for key in SETTINGS} != x["wrote"]:
            log(SECTION, "! The approval settings were changed since the seed set them: left as they are")
            return
        self.aim.put("/approval-settings", json={key: value for key, value in x["before"].items() if value is not None})
        log(SECTION, "✓ Approval settings put back")


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    approvals = seed_file("aim", "governance", "approvals.yaml")
    compliance = seed_file("aim", "governance", "compliance.yaml")
    if not (approvals or compliance):
        return
    # Every file is read before a call
    spec = mapping("approvals.yaml", load(approvals)) if approvals else {}
    settings, rules = settings_of(spec.get("settings")), rules_of(spec)
    frameworks, agents = compliance_of(mapping("compliance.yaml", load(compliance))) if compliance else ([], [])
    governance = Governance()
    governance.connect()
    governance.settings(settings)
    governance.rules(rules)
    governance.activate(frameworks)
    for entry in agents:
        governance.agent_compliance(entry)


def remove(records):
    """Deletes the rules, puts the settings back and switches off the frameworks the seed switched on. The answers are
    the agent's: it is deleted with the catalog."""
    mine = [x for x in owned(records, SECTION, ORDER) if x["kind"] != "compliance-answers"]
    if not mine:
        return
    governance = Governance()
    governance.connect()
    for x in mine:
        if x["kind"] == "approval-rule":
            governance.aim.delete(f"/approval-rules/{x['id']}")
            log(SECTION, f"✓ Deleted approval rule '{x['name']}'")
        elif x["kind"] == "approval-settings":
            governance.restore(x)
        else:
            governance.aim.delete(f"/compliance/activations/{x['name']}")
            log(SECTION, f"✓ Framework off: {x['name']}")
