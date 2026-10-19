"""The link from Gamma to Access Management, so that Agent Identity lists the agents of the playground's domain.

    seed/am/gamma-link.yaml     domain (name or hrid of the AM domain Agent Identity lists), serviceAccount

Gamma has one link to AM for the whole environment, and no API takes it away. What the seed does follows what Gamma has:

    no link         Gamma gets a token to read AM with: a personal access token of an AM service account made for it,
                    with the organization owner role (the whole organization, not one domain) and no expiry (AM takes
                    a name and nothing else for a token), or AM_TOKEN where nothing may be created in AM
    a link to no domain, a domain that is gone, or this playground's own
                    the domain is pointed at the playground's, the token Gamma has stays
    a link to another domain that works
                    left as it is, with a warning

The token goes from AM to Gamma and nowhere else: it is not logged and not recorded. The link stays when the playground
is removed, with the service account and its token, so the records of them are notes: whoever is done with the lab deletes
the account in AM. The seed adopts a service account of that name only when it made it (it is recorded): another one
stops the seed, which does not renew the tokens of an account it does not own.

    GAMMA_AM_URL    AM's management API as the APIM management API reaches it (AM_MANAGEMENT_URL when it is the same)
    AM_PUBLIC_URL   AM's gateway as a browser reaches it: what Gamma shows and puts in the issuer of a token
"""

import os

from . import am
from .common import SeedError, has_seed, items, load, log, owned, record, recorded, seed_file, setting, wait_until
from .gamma import Gamma

SECTION = "am-link"
DEPENDS = ("am",)   # the modules whose work this one reads on the platform (schedule.py)
# The one file of the seed it owns: seed/am/ itself belongs to every playground that seeds Access Management
FOLDERS = ("am/gamma-link.yaml",)
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("platform",)
# What it records, dependents first: the link and the service account it reads AM with, which nothing undoes
ORDER = ["am-link", "am-service-account"]

ORG_OWNER = "ORGANIZATION_OWNER"   # the least role that lets an account manage a domain: a lower one is refused on domains


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    spec = load(seed_file("am", "gamma-link.yaml"))
    access = am.AccessManagement()
    access.connect()
    domain = access.find_domain(spec["domain"])
    gamma = Gamma()
    config = gamma.platform
    gamma.connect(SECTION, lambda: config.get("/am/am-config"), "Gamma")
    saved = config.get("/am/am-config").json()
    wanted = {"baseUrl": setting("GAMMA_AM_URL", setting("AM_MANAGEMENT_URL")), "amOrganizationId": access.org,
              "environmentId": access.env, "defaultDomainId": domain["id"],
              "defaultDomainHrid": domain.get("hrid") or domain["name"], "gatewayUrl": setting("AM_PUBLIC_URL")}
    hrid = wanted["defaultDomainHrid"]
    if not saved.get("hasAccessToken"):
        token = os.getenv("AM_TOKEN") or service_account_token(access, spec.get("serviceAccount", "gamma-service-account"))
        first_link(config, wanted, token)
    elif not repairable(saved, wanted, access.domains()):
        where = f"the AM domain '{saved['defaultDomainHrid']}'" if saved.get("defaultDomainHrid") else saved.get("baseUrl")
        log(SECTION, f"! Gamma is linked to {where}, which the seed leaves alone: Agent Identity does not list this playground's agents there. "
                     f"Choose '{hrid}' in Platform > Access Management")
        return
    elif all(saved.get(key) == value for key, value in wanted.items()):
        log(SECTION, f"Gamma is linked to the domain '{hrid}'")
        return
    else:
        config.put("/am/am-config", json=wanted)   # no token in it: the one Gamma has stays
    record(SECTION, "am-link", "Gamma links", "Gamma to Access Management", domain["id"], domain=hrid)
    # A role just given to the service account takes a few seconds to count in AM
    path = f"/am/environments/{access.env}/domains/{domain['id']}"
    wait_until(SECTION, "Gamma's access to the domain", lambda: config.get(path), timeout=60)
    log(SECTION, f"✓ Gamma is linked to the domain '{hrid}'")


def first_link(config, wanted, token):
    """Gives Gamma the link and the token to read AM with: tested first, as the console does."""
    body = {**wanted, "serviceAccountAccessToken": token}
    result = config.post("/am/am-config/_test", json=body).json()
    if not result.get("ok"):
        raise SeedError(f"Gamma cannot reach Access Management at {wanted['baseUrl']}: {result.get('message')}")
    config.put("/am/am-config", json=body)


def repairable(saved, wanted, domains):
    """Whether the link Gamma has is the seed's to point at its own domain: it is on the same AM, and aims at no domain,
    at this playground's own, or at one that is gone."""
    if (saved.get("baseUrl") or "").rstrip("/") != wanted["baseUrl"].rstrip("/"):
        return False
    aimed = saved.get("defaultDomainId")
    gone = (saved.get("environmentId") in (None, wanted["environmentId"])
            and aimed not in {domain["id"] for domain in domains})
    return (not aimed or aimed == wanted["defaultDomainId"] or saved.get("defaultDomainHrid") == wanted["defaultDomainHrid"]
            or gone)


def service_account_token(access, name):
    """A new personal access token of the AM service account `name`, which is made, with its role, when it is not there.
    A token is shown once: the one Gamma had is not known, so this is only asked when Gamma has none."""
    api, org = access.api, access.org_url
    users = items(api.get(f"{org}/users", params={"q": name, "size": 50}).json())
    user = next((u for u in users if u.get("username") == name and u.get("serviceAccount")), None)
    if user and not recorded(SECTION, "am-service-account", user["id"]):
        # Its tokens would be deleted below, and the role it has is not ours to change
        raise SeedError(f"AM already has a service account '{name}' that the seed did not create: delete it in Access "
                        "Management, or name another in seed/am/gamma-link.yaml, or give Gamma a token of its own "
                        "with AM_TOKEN")
    if not user:
        user = api.post(f"{org}/users", json={"username": name, "serviceAccount": True, "firstName": "Gamma",
                                              "lastName": "Service account"}).json()
        record(SECTION, "am-service-account", "AM service accounts", name, user["id"], role=ORG_OWNER)
        log(SECTION, f"✓ AM service account '{name}' created")
    role = next((r for r in items(api.get(f"{org}/roles").json())
                 if r["name"] == ORG_OWNER and r.get("assignableType") == "organization"), None)
    if not role:
        raise SeedError(f"AM has no organization role {ORG_OWNER}")
    # A new user has a membership already, with a role that cannot read a domain: the role is what is compared, and a
    # membership posted again takes the new role
    members = api.get(f"{org}/members").json().get("memberships", [])
    if not any(m.get("memberId") == user["id"] and m.get("roleId") == role["id"] for m in members):
        api.post(f"{org}/members", json={"memberId": user["id"], "memberType": "USER", "role": role["id"]})
        log(SECTION, f"  ✓ Role {ORG_OWNER}")
    tokens = f"{org}/users/{user['id']}/tokens"
    for old in api.get(tokens).json() or []:   # a token nobody knows is of no use
        api.delete(f"{tokens}/{old['tokenId']}")
    return api.post(tokens, json={"name": "gamma"}).json()["token"]


def remove(records):
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    message = ("! The link from Gamma to Access Management cannot be undone through the API: it stays. The next seed "
               "points it at the new domain")
    accounts = [r["name"] for r in mine if r["kind"] == "am-service-account"]
    if accounts:
        message += (f". So does the service account '{accounts[0]}' with the {ORG_OWNER} role and its token, which does "
                    "not expire: delete the account in Access Management when you are done with the lab")
    log(SECTION, message)
