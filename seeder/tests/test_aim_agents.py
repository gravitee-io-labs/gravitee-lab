"""The identity and the access of an agent of the catalog, from the keys `identity` and `access` of its file: the application
that acts for it, the AM application attached to that one as its identity, and a subscription to the plan of a studio. Against
a platform that keeps what it is given: made once, left alone by a second run (no write at all), repaired when a step is
missing, never taking over what the seed did not make, and removed with only what it made."""

import itertools
import os
import re
import unittest
from unittest import mock

from lab_seeder import aim_agents
from lab_seeder import common
from lab_seeder import seed
from lab_seeder.testkit import PERSONAL, Seed

AIM = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/aim"
ENV = "/management/organizations/DEFAULT/environments/DEFAULT"
AM = "http://am" + ENV
AGENTS = AIM + "/catalog/agents"
APPLICATION = AGENTS + "/agent-1/application"
IDENTITY = APPLICATION + "/identity"
SUBSCRIPTIONS = AIM + "/mcp-proxies/studio-1/subscriptions"
CONTRACT = PERSONAL / "agent-toolbox" / "seed" / "aim" / "agents" / "care-copilot.yaml"
LINK_CONTRACT = PERSONAL / "agent-toolbox" / "seed" / "am" / "gamma-link.yaml"

AGENT = """\
entityId: care-copilot
source: Harbor
url: ${CARE_COPILOT_CARD_URL}
metadata:
  riskClassification: limited
identity:
  clientId: care-copilot
access:
  - {studio: Customer care, plan: Agents}
"""
SECOND = """\
entityId: second-agent
identity:
  clientId: second
access:
  - {studio: Customer care, plan: Agents}
"""
LINK = """\
domain: harbor
serviceAccount: gamma-service-account
"""
SETTINGS = {"CARE_COPILOT_CARD_URL": "http://harbor-copilot:8000"}
APPLICATION_ID, AM_APPLICATION_ID = "app-1", "am-app-1"
APPLICATIONS = {"agent-1": APPLICATION_ID, "agent-2": "app-2"}
NOT_FOUND = (404, {"code": "agent.application.not_found", "message": "No application acts for this agent"})


def page(rows):
    """What a list of Agent Management answers when the platform pages it itself: the rows and their count."""
    return 200, {"content": rows, "pageNumber": 0, "pageElements": len(rows), "totalElements": len(rows)}


def agent_of(call):
    return re.search(r"/catalog/agents/([^/]+)/", call.path).group(1)


class World:
    """Agent Management (the agents of the catalog and the application that acts for each, the studios with their plans and
    subscriptions), the applications of API Management and Access Management's domain. Like the real ones it refuses a second
    application for an agent and an identity over an identity, it deletes the identity it detaches, and a subscription keeps
    the id of the APIM plan behind the plan of the studio."""

    def __init__(self, validation="AUTO"):
        self.next_id = itertools.count(1)
        self.agents = [{"id": "agent-1", "entityId": "agent.care-copilot", "state": "ACTIVE",
                        "definition": {"name": "Care copilot"}},
                       {"id": "agent-2", "entityId": "agent.second-agent", "state": "ACTIVE",
                        "definition": {"name": "Second agent"}}]
        self.applications = {}      # agent id -> the application that acts for it
        self.studios = [{"id": "studio-1", "name": "Customer care", "mode": "STUDIO", "contextPath": "/mcp/customer-care"}]
        self.plans = {"studio-1": [
            {"id": "plan-registration", "apimPlanId": "apim-registration", "name": "Registration", "status": "PUBLISHED"},
            {"id": "plan-agents", "apimPlanId": "apim-agents", "name": "Agents", "status": "PUBLISHED"}]}
        self.subscriptions = []
        self.validation = validation
        self.domains = [{"id": "dom-1", "name": "Harbor", "hrid": "harbor"}]
        self.am_applications = {AM_APPLICATION_ID: self.identity(AM_APPLICATION_ID, "care-copilot"),
                                "am-app-2": self.identity("am-app-2", "second")}
        self.deleted_applications = []
        self.detach_answer = None   # what detaching an identity answers, in place of detaching it
        self.restore_error = None   # why restoring an agent's access reaches no subscription
        self.blind = False          # the first look at an application finds nothing, whatever is there
        self.race = False           # someone makes the application between the seed's look and its creation

    @staticmethod
    def identity(id, client_id):
        return {"id": id, "name": client_id, "type": "AGENT", "settings": {"oauth": {"clientId": client_id}}}

    def routes(self):
        agent = AGENTS + "/[^/]+/application"
        return {
            ("GET", AGENTS): lambda call: (200, {"data": self.agents, "pagination": {"totalCount": len(self.agents)}}),
            ("GET", re.compile(agent)): self.read_application,
            ("POST", re.compile(agent)): self.create_application,
            ("PUT", re.compile(agent + "/identity")): self.attach,
            ("DELETE", re.compile(agent + "/identity")): self.detach,
            ("GET", re.compile(AGENTS + "/[^/]+/access")): self.read_access,
            ("POST", re.compile(AGENTS + "/[^/]+/access/_restore")): self.restore_access,
            ("GET", AIM + "/mcp-proxies"): lambda call: page(self.studios),
            ("GET", re.compile(AIM + "/mcp-proxies/[^/]+/plans")): lambda call: (200, self.plans[call.path.split("/")[-2]]),
            ("GET", SUBSCRIPTIONS): self.list_subscriptions,
            ("POST", SUBSCRIPTIONS): self.subscribe,
            ("POST", re.compile(SUBSCRIPTIONS + "/[^/]+/_accept")): lambda call: self.settle(call, "ACCEPTED"),
            ("POST", re.compile(SUBSCRIPTIONS + "/[^/]+/_close")): lambda call: self.settle(call, "CLOSED"),
            ("DELETE", re.compile(ENV + "/applications/[^/]+")): self.delete_application,
            ("POST", "http://am/management/auth/token"): (200, {"access_token": "am-session"}),
            ("GET", AM + "/domains"): lambda call: (200, {"data": self.domains}),
            ("GET", AM + "/domains/dom-1/applications"): lambda call: (200, {"data": [
                {"id": a["id"], "name": a["name"]} for a in self.am_applications.values() if call.params["q"] in str(a)]}),
            ("GET", re.compile(ENV + "/domains/dom-1/applications/[^/]+")):
                lambda call: (200, self.am_applications[call.path.split("/")[-1]]),
        }

    # --- Agent Management -------------------------------------------------------------------

    def read_application(self, call):
        found = self.applications.get(agent_of(call))
        if self.blind:
            self.blind = False
            return NOT_FOUND
        return (200, found) if found else NOT_FOUND

    def create_application(self, call):
        agent = agent_of(call)
        if self.race:
            self.applications[agent] = {"applicationId": "app-by-hand", "name": "Care copilot"}
        if agent in self.applications:
            return 409, {"code": "agent.application.already_exists", "message": "An application already acts for this agent"}
        name = next(a["definition"]["name"] for a in self.agents if a["id"] == agent)
        self.applications[agent] = {"applicationId": APPLICATIONS[agent], "name": name}
        return 200, self.applications[agent]

    def attach(self, call):
        application = self.applications.get(agent_of(call))
        if not application:
            return 409, {"code": "agent.application.missing", "message": "No application acts for this agent"}
        if application.get("identityId"):
            return 409, {"code": "agent.identity.already_attached", "message": "An identity is already attached"}
        identity = self.am_applications[call.json["identityId"]]
        application.update(identityId=identity["id"], clientId=identity["settings"]["oauth"]["clientId"])
        return 200, application

    def detach(self, call):
        if self.detach_answer:
            return self.detach_answer
        application = self.applications.get(agent_of(call))
        if not application or not application.get("identityId"):
            return 409, {"code": "agent.identity.not_attached", "message": "No identity is attached"}
        identity = application.pop("identityId")
        application.pop("clientId", None)
        self.am_applications.pop(identity, None)
        return 200, {"applicationId": application["applicationId"], "identityId": identity, "result": "DETACHED"}

    def agent(self, id):
        return next(a for a in self.agents if a["id"] == id)

    def read_access(self, call):
        return 200, {"state": self.agent(agent_of(call))["state"], "levers": ["SUBSCRIPTION"],
                     "applicationId": APPLICATIONS[agent_of(call)]}

    def revoke(self, agent="agent-1"):
        """What the kill switch does: the agent is inactive and the subscriptions of its application are paused."""
        self.agent(agent)["state"] = "INACTIVE"
        for subscription in self.subscriptions:
            if subscription["applicationId"] == APPLICATIONS[agent] and subscription["status"] == "ACCEPTED":
                subscription["status"] = "PAUSED"

    def restore_access(self, call):
        agent = self.agent(agent_of(call))
        outcomes = []
        for subscription in self.subscriptions:
            if subscription["applicationId"] == APPLICATIONS[agent["id"]] and subscription["status"] == "PAUSED":
                failed = bool(self.restore_error)
                subscription["status"] = "PAUSED" if failed else "ACCEPTED"
                outcomes.append({"lever": "SUBSCRIPTION", "target": "studio-1", "detail": subscription["id"],
                                 "succeeded": not failed, **({"error": self.restore_error} if failed else {})})
        agent["state"] = "INACTIVE" if self.restore_error else "ACTIVE"
        return 200, {"result": "PARTIAL" if self.restore_error else "RESTORED", "outcomes": outcomes}

    def list_subscriptions(self, call):
        application = call.params.get("applicationId")
        return page([s for s in self.subscriptions if application in (None, s["applicationId"])])

    def subscribe(self, call):
        plan = next(p for p in self.plans["studio-1"] if p["id"] == call.json["planId"])
        made = {"id": f"sub-{next(self.next_id)}", "applicationId": call.json["applicationId"], "planId": plan["apimPlanId"],
                "status": "ACCEPTED" if self.validation == "AUTO" else "PENDING"}
        self.subscriptions.append(made)
        return 201, made

    def settle(self, call, status):
        found = next((s for s in self.subscriptions if s["id"] == call.path.split("/")[-2]), None)
        if found is None:
            return 404, {"code": "subscription.not_found", "message": "No such subscription"}
        found["status"] = status
        return 200, found

    # --- API Management ---------------------------------------------------------------------

    def delete_application(self, call):
        gone = call.path.rsplit("/", 1)[1]
        self.deleted_applications.append(gone)
        self.applications = {agent: app for agent, app in self.applications.items() if app["applicationId"] != gone}
        return 204


class Agents(Seed):
    def setUp(self):
        super().setUp()
        self.env(**SETTINGS)
        self.world = World()
        self.platform.add(self.world.routes())
        self.seed_files({"aim/agents/care-copilot.yaml": AGENT, "am/gamma-link.yaml": LINK})

    def records(self, kind=None):
        return [x for x in common.created() if x["product"] == aim_agents.SECTION and kind in (None, x["kind"])]

    def made(self):
        """What a first run leaves, and a platform that has not been asked anything since."""
        aim_agents.seed()
        self.platform.calls.clear()

    def changed(self):
        """What was written, which is not signing in to Access Management."""
        return [change for change in self.platform.changes() if change != ("POST", "/management/auth/token")]


class FirstRun(Agents):
    def test_it_makes_the_application_attaches_the_identity_and_subscribes_in_that_order(self):
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", APPLICATION), ("PUT", IDENTITY), ("POST", SUBSCRIPTIONS)])

    def test_it_sends_what_the_platform_reads(self):
        aim_agents.seed()
        sent = {(call.method, call.path): call.json for call in self.platform.calls if call.method != "GET"}
        self.assertEqual(sent[("POST", APPLICATION)], {})
        self.assertEqual(sent[("PUT", IDENTITY)], {"identityId": AM_APPLICATION_ID})
        self.assertEqual(sent[("POST", SUBSCRIPTIONS)], {"applicationId": APPLICATION_ID, "planId": "plan-agents"})

    def test_the_agent_is_the_one_of_the_entity_id_in_the_catalog(self):
        self.world.agents.insert(0, {"id": "agent-0", "entityId": "agent.other", "definition": {"name": "Other"}})
        aim_agents.seed()
        self.assertEqual(self.world.applications["agent-1"]["identityId"], AM_APPLICATION_ID)
        self.assertNotIn("agent-0", self.world.applications)

    def test_the_identity_is_the_application_of_that_exact_client_id_in_the_domain_of_the_link(self):
        self.world.am_applications["am-app-0"] = self.world.identity("am-app-0", "care-copilot-old")
        aim_agents.seed()
        self.assertEqual(self.world.applications["agent-1"]["identityId"], AM_APPLICATION_ID)
        searched = self.platform.sent("GET", AM + "/domains/dom-1/applications")
        self.assertEqual(searched[0].params["q"], "care-copilot")

    def test_the_domain_may_be_named_by_its_name_or_by_its_hrid(self):
        for name in ("Harbor", "harbor"):
            with self.subTest(domain=name):
                self.seed_files({"am/gamma-link.yaml": f"domain: {name}\n"})
                self.world.applications.clear()
                aim_agents.seed()
                self.assertEqual(self.world.applications["agent-1"]["identityId"], AM_APPLICATION_ID)

    def test_it_records_what_it_made_and_nothing_else(self):
        aim_agents.seed()
        application, subscription = self.records("agent-application"), self.records("subscription")
        self.assertEqual([(x["name"], x["id"], x["label"]) for x in application],
                         [("Care copilot", APPLICATION_ID, "agent applications")])
        self.assertEqual((application[0]["agent"], application[0]["identity"]), ("agent-1", AM_APPLICATION_ID))
        self.assertEqual([(x["name"], x["label"], x["api"], x["application"]) for x in subscription],
                         [("Care copilot → Customer care (Agents)", "agent subscriptions", "studio-1", APPLICATION_ID)])
        self.assertEqual(subscription[0]["id"], self.world.subscriptions[0]["id"])
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(len(common.created()), 2)

    def test_it_says_what_it_did(self):
        aim_agents.seed()
        self.assertIn("[aim-agents] ✓ Agent application created: Care copilot", self.output())
        self.assertIn("[aim-agents]   ✓ Identity attached: care-copilot", self.output())
        self.assertIn("[aim-agents]   ✓ Subscribed: Care copilot → Customer care (Agents)", self.output())

    def test_a_plan_that_asks_for_a_person_is_accepted_by_the_seed(self):
        self.world.validation = "MANUAL"
        aim_agents.seed()
        self.assertEqual(self.changed()[-2:], [("POST", SUBSCRIPTIONS), ("POST", SUBSCRIPTIONS + "/sub-1/_accept")])
        self.assertEqual(self.world.subscriptions[0]["status"], "ACCEPTED")

    def test_a_plan_that_accepts_by_itself_is_not_accepted_again(self):
        aim_agents.seed()
        self.assertEqual(self.platform.sent("POST", re.compile(SUBSCRIPTIONS + "/[^/]+/_accept")), [])

    def test_an_agent_without_identity_gets_its_application_and_access_and_asks_nothing_of_access_management(self):
        self.seed_files({"aim/agents/care-copilot.yaml": AGENT.replace("identity:\n  clientId: care-copilot\n", "")})
        (common.SEED_DIR / "am" / "gamma-link.yaml").unlink()
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", APPLICATION), ("POST", SUBSCRIPTIONS)])
        self.assertEqual([call for call in self.platform.calls if call.url.startswith("http://am")], [])

    def test_an_agent_without_access_gets_its_application_and_identity(self):
        without_access = AGENT.replace("access:\n  - {studio: Customer care, plan: Agents}\n", "")
        self.seed_files({"aim/agents/care-copilot.yaml": without_access})
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", APPLICATION), ("PUT", IDENTITY)])

    def test_every_agent_of_the_folder_that_asks_is_served(self):
        self.seed_files({"aim/agents/second-agent.yaml": SECOND})
        aim_agents.seed()
        second = AGENTS + "/agent-2/application"
        self.assertEqual(self.changed(), [("POST", APPLICATION), ("PUT", IDENTITY), ("POST", SUBSCRIPTIONS),
                                          ("POST", second), ("PUT", second + "/identity"), ("POST", SUBSCRIPTIONS)])
        self.assertEqual(self.world.applications["agent-2"]["identityId"], "am-app-2")
        self.assertEqual([s["applicationId"] for s in self.world.subscriptions], [APPLICATION_ID, "app-2"])
        self.assertEqual(len(self.records()), 4)
        self.platform.calls.clear()
        aim_agents.seed()
        self.assertEqual(self.changed(), [])

    def test_the_access_management_login_is_made_once_for_all_the_agents(self):
        self.seed_files({"aim/agents/second-agent.yaml": SECOND})
        aim_agents.seed()
        self.assertEqual(len(self.platform.sent("POST", "http://am/management/auth/token")), 1)


class SecondRun(Agents):
    def test_it_changes_nothing(self):
        self.made()
        aim_agents.seed()
        self.assertEqual(self.changed(), [])
        self.assertEqual(len(self.records()), 2)

    def test_it_says_that_everything_is_in_place(self):
        aim_agents.seed()
        aim_agents.seed()
        self.assertIn("[aim-agents] Agent application exists: Care copilot", self.output())
        self.assertIn("[aim-agents]   Identity attached: care-copilot", self.output())
        self.assertIn("[aim-agents]   Subscription exists: Care copilot → Customer care (Agents)", self.output())

    def test_a_subscription_that_was_paused_by_hand_on_an_agent_in_service_stays_paused(self):
        self.made()
        self.world.subscriptions[0]["status"] = "PAUSED"
        aim_agents.seed()
        self.assertEqual(self.changed(), [])
        self.assertEqual(self.world.subscriptions[0]["status"], "PAUSED")

    def test_a_subscription_that_was_closed_or_rejected_is_made_again(self):
        for status in ("CLOSED", "REJECTED"):
            with self.subTest(status=status):
                self.world.subscriptions.clear()
                self.made()
                self.world.subscriptions[0]["status"] = status
                aim_agents.seed()
                self.assertEqual(self.changed(), [("POST", SUBSCRIPTIONS)])
                self.assertEqual([s["status"] for s in self.world.subscriptions], [status, "ACCEPTED"])

    def test_a_subscription_the_seed_made_and_left_pending_is_accepted(self):
        self.world.validation = "MANUAL"
        self.made()
        self.world.subscriptions[0]["status"] = "PENDING"   # the run stopped before it accepted it
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", SUBSCRIPTIONS + "/sub-1/_accept")])
        self.assertEqual(self.world.subscriptions[0]["status"], "ACCEPTED")

    def test_a_pending_subscription_that_a_person_made_is_left_to_the_person(self):
        self.made()
        common.save_created([x for x in common.created() if x["kind"] != "subscription"])
        self.world.subscriptions[0]["status"] = "PENDING"
        aim_agents.seed()
        self.assertEqual(self.changed(), [])
        self.assertEqual(self.world.subscriptions[0]["status"], "PENDING")

    def test_the_subscription_of_another_application_or_to_another_plan_is_none_of_the_agents(self):
        self.made()
        self.world.subscriptions[0]["status"] = "CLOSED"
        self.world.subscriptions += [
            {"id": "sub-x", "applicationId": "somebody", "planId": "apim-agents", "status": "ACCEPTED"},
            {"id": "sub-y", "applicationId": APPLICATION_ID, "planId": "apim-registration", "status": "ACCEPTED"}]
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", SUBSCRIPTIONS)])

    def test_a_subscription_may_carry_the_id_of_the_plan_of_the_studio_as_well_as_the_apim_id_behind_it(self):
        self.made()
        self.world.subscriptions[0]["planId"] = "plan-agents"
        aim_agents.seed()
        self.assertEqual(self.changed(), [])

    def test_a_missing_subscription_is_made_without_redoing_the_other_steps(self):
        self.made()
        self.world.subscriptions.clear()
        common.save_created([x for x in common.created() if x["kind"] != "subscription"])
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", SUBSCRIPTIONS)])

    def test_an_identity_that_was_detached_is_attached_again(self):
        self.made()
        self.world.applications["agent-1"].pop("identityId")
        aim_agents.seed()
        self.assertEqual(self.changed(), [("PUT", IDENTITY)])

    def test_an_application_that_is_gone_is_made_again_and_the_record_follows_it(self):
        self.made()
        self.world.applications.clear()
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", APPLICATION), ("PUT", IDENTITY)])
        self.assertEqual(len(self.records("agent-application")), 1)


class KillSwitch(Agents):
    """The last lesson revokes the access of the agent. Re-seed starts the lessons over."""

    RESTORE = AGENTS + "/agent-1/access/_restore"

    def test_an_agent_in_service_is_looked_at_and_not_restored(self):
        self.made()
        aim_agents.seed()
        self.assertEqual(len(self.platform.sent("GET", AGENTS + "/agent-1/access")), 1)
        self.assertEqual(self.platform.sent("POST", self.RESTORE), [])

    def test_an_agent_whose_access_was_revoked_is_restored_with_what_revoking_paused(self):
        self.made()
        self.world.revoke()
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", self.RESTORE)])
        self.assertEqual((self.world.agents[0]["state"], self.world.subscriptions[0]["status"]), ("ACTIVE", "ACCEPTED"))
        self.assertIn("[aim-agents]   ✓ Access restored: Care copilot", self.output())

    def test_the_restore_comes_before_the_subscriptions_are_looked_at_so_none_is_made_again(self):
        self.made()
        self.world.revoke()
        aim_agents.seed()
        self.assertEqual(self.platform.sent("POST", SUBSCRIPTIONS), [])
        self.platform.calls.clear()
        aim_agents.seed()
        self.assertEqual(self.changed(), [])

    def test_an_agent_whose_access_cannot_be_restored_stops_the_seed_with_the_reason_the_platform_gives(self):
        self.made()
        self.world.revoke()
        self.world.restore_error = "The subscription could not be resumed"
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "The access of the agent 'Care copilot' could not be restored: The "
                                                 "subscription could not be resumed")
        self.assertEqual(self.world.agents[0]["state"], "INACTIVE")

    def test_the_restore_is_for_the_agent_that_is_revoked_and_no_other(self):
        self.seed_files({"aim/agents/second-agent.yaml": SECOND})
        self.made()
        self.world.revoke("agent-2")
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", AGENTS + "/agent-2/access/_restore")])
        self.assertEqual([s["status"] for s in self.world.subscriptions], ["ACCEPTED", "ACCEPTED"])

    def test_a_revoked_agent_whose_application_is_not_the_seeds_is_not_restored(self):
        self.world.applications["agent-1"] = {"applicationId": "app-by-hand", "name": "Care copilot"}
        self.world.agents[0]["state"] = "INACTIVE"
        with self.assertRaises(common.SeedError):
            aim_agents.seed()
        self.assertEqual(self.platform.sent("GET", AGENTS + "/agent-1/access"), [])
        self.assertEqual(self.changed(), [])


class WhatItDidNotMake(Agents):
    def test_an_application_that_acts_for_the_agent_and_was_not_made_by_the_seed_stops_it(self):
        self.world.applications["agent-1"] = {"applicationId": "app-by-hand", "name": "Care copilot"}
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "An application already acts for the agent 'Care copilot' and was not created "
                                                 "by this playground: delete it, the seed leaves it alone")
        self.assertEqual(self.changed(), [])
        self.assertEqual(self.records(), [])

    def test_an_application_made_by_someone_since_the_look_is_met_by_the_conflict_and_is_still_not_the_seeds(self):
        self.world.race = True
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertIn("was not created by this playground", str(stopped.exception))
        self.assertEqual(self.changed(), [("POST", APPLICATION)])
        self.assertEqual(self.records(), [])

    def test_an_application_the_seed_made_is_read_when_the_platform_says_it_exists(self):
        self.made()
        self.world.blind = True   # the first look finds nothing, the creation is refused: it is there
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", APPLICATION)])   # refused, so written nowhere
        self.assertEqual(len(self.world.applications), 1)
        self.assertIn("Agent application exists: Care copilot", self.output())
        self.assertEqual([x["id"] for x in self.records("agent-application")], [APPLICATION_ID])

    def test_a_conflict_with_nothing_to_read_is_reported_whole(self):
        self.platform.add({("GET", APPLICATION): NOT_FOUND,
                           ("POST", APPLICATION): (409, {"code": "agent.application.already_exists", "message": "exists"})})
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertIn("agent.application.already_exists", str(stopped.exception))
        self.assertEqual(self.records(), [])

    def test_an_identity_of_another_client_id_attached_to_the_application_stops_it(self):
        self.made()
        self.world.applications["agent-1"]["identityId"] = "am-app-9"
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "The application of the agent 'Care copilot' presents another identity "
                                                 "than the client id 'care-copilot': detach it in Agent Identity, the "
                                                 "seed leaves it alone")
        self.assertEqual(self.changed(), [])

    def test_an_identity_that_a_person_attached_is_accepted_and_not_recorded(self):
        self.world.applications["agent-1"] = {"applicationId": "app-by-hand", "name": "Care copilot",
                                              "identityId": AM_APPLICATION_ID}
        common.save_created([{"product": "aim-agents", "kind": "agent-application", "label": "agent applications",
                              "name": "Care copilot", "id": "app-by-hand", "agent": "agent-1"}])
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", SUBSCRIPTIONS)])
        self.assertNotIn("identity", self.records("agent-application")[0])


class WhatItNeeds(Agents):
    def test_an_agent_that_is_not_in_the_catalog_stops_the_seed_before_it_writes(self):
        self.world.agents.clear()
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "The agent 'agent.care-copilot' is not in the catalog: aim_catalog registers "
                                                 "it from the same file")
        self.assertEqual(self.changed(), [])

    def test_an_identity_that_access_management_does_not_have_stops_it_naming_the_domain(self):
        self.world.am_applications.clear()
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "No application of the AM domain 'harbor' has the client id 'care-copilot': "
                                                 "seed/am/applications makes it before the agent takes it as its identity")
        self.assertEqual(self.changed(), [("POST", APPLICATION)])   # the application is there for the next run

    def test_a_domain_that_is_not_there_stops_it(self):
        self.world.domains.clear()
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "AM domain 'harbor' not found")

    def test_an_identity_without_the_link_that_names_the_domain_stops_it_before_any_call(self):
        (common.SEED_DIR / "am" / "gamma-link.yaml").unlink()
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertIn("seed/am/gamma-link.yaml names the domain that holds it", str(stopped.exception))
        self.assertEqual(self.platform.calls, [])

    def test_a_link_without_a_domain_stops_it(self):
        self.seed_files({"am/gamma-link.yaml": "serviceAccount: x\n"})
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "seed/am/gamma-link.yaml: domain missing")

    def test_a_studio_that_is_not_there_stops_it(self):
        self.world.studios.clear()
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "The MCP studio 'Customer care' is not there: aim/studios makes it before the "
                                                 "agent subscribes")

    def test_a_proxy_of_that_name_is_not_a_studio(self):
        self.world.studios[0]["mode"] = "PROXY"
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertIn("The MCP studio 'Customer care' is not there", str(stopped.exception))

    def test_two_studios_of_that_name_are_not_chosen_between(self):
        self.world.studios.append({**self.world.studios[0], "id": "studio-2"})
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "2 MCP studios are named 'Customer care': the agent cannot tell which one to "
                                                 "subscribe to")

    def test_a_plan_that_is_not_published_is_not_there(self):
        self.world.plans["studio-1"][1]["status"] = "STAGING"
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertEqual(str(stopped.exception), "The MCP studio 'Customer care' has no published plan 'Agents'")

    def test_a_plan_of_another_name_is_not_there(self):
        self.seed_files({"aim/agents/care-copilot.yaml": AGENT.replace("plan: Agents", "plan: Humans")})
        with self.assertRaises(common.SeedError) as stopped:
            aim_agents.seed()
        self.assertIn("has no published plan 'Humans'", str(stopped.exception))

    def test_an_agent_file_is_checked_before_any_call(self):
        access = "care-copilot.yaml: access is a list of {studio, plan}"
        for text, problem in ((AGENT.replace("entityId: care-copilot\n", ""), "care-copilot.yaml: entityId missing"),
                              (AGENT.replace("clientId: care-copilot", "clientIds: x"),
                               "care-copilot.yaml: identity needs a clientId"),
                              (AGENT.replace("- {studio: Customer care, plan: Agents}", "- Customer care"), access),
                              (AGENT.replace(", plan: Agents", ""), access)):
            with self.subTest(problem=problem):
                self.seed_files({"aim/agents/care-copilot.yaml": text})
                with self.assertRaises(common.SeedError) as stopped:
                    aim_agents.seed()
                self.assertEqual(str(stopped.exception), problem)
                self.assertEqual(self.platform.calls, [])


class WhatItLeavesAlone(Agents):
    CARD_ONLY = "entityId: acme-bakery-ace\nsource: Acme Bakery\nurl: http://bakery-agent:8000\n"

    def test_a_file_with_neither_key_is_aim_catalogs_alone(self):
        self.seed_files({"aim/agents/care-copilot.yaml": self.CARD_ONLY})
        with mock.patch.dict(os.environ, {}, clear=True):
            aim_agents.seed()
        self.assertEqual(self.platform.calls, [])

    def test_the_agents_that_ask_are_served_beside_those_that_do_not(self):
        self.seed_files({"aim/agents/acme.yaml": self.CARD_ONLY})
        aim_agents.seed()
        self.assertEqual(self.changed(), [("POST", APPLICATION), ("PUT", IDENTITY), ("POST", SUBSCRIPTIONS)])

    def test_a_playground_that_seeds_no_agent_needs_no_setting_and_makes_no_call(self):
        with mock.patch.object(common, "SEED_DIR", common.SEED_DIR / "nothing"), mock.patch.dict(os.environ, {}, clear=True):
            aim_agents.seed()
            aim_agents.remove([])
        self.assertEqual(self.platform.calls, [])

    def test_the_records_of_other_modules_are_left_alone(self):
        theirs = [{"product": "apim", "kind": "subscription", "name": "s", "id": "s", "label": ""},
                  {"product": "aim-catalog", "kind": "agent", "name": "a", "id": "a", "label": ""},
                  {"product": "am", "kind": "agent", "name": "a", "id": "a", "label": ""}]
        aim_agents.remove(theirs)
        self.assertEqual(self.platform.calls, [])


class Removal(Agents):
    def setUp(self):
        super().setUp()
        aim_agents.seed()
        self.platform.calls.clear()

    def remove(self, with_am_agent=True):
        """A removal as seed.py runs it: with the whole record, in which seed/am has recorded the identity it made."""
        am_agent = {"label": "AM agents", "product": "am", "kind": "agent", "name": "Care copilot", "id": AM_APPLICATION_ID}
        aim_agents.remove(common.created() + ([am_agent] if with_am_agent else []))

    def test_it_closes_the_subscription_detaches_the_identity_and_deletes_the_application_in_that_order(self):
        self.remove()
        self.assertEqual(self.changed(), [("POST", SUBSCRIPTIONS + "/sub-1/_close"), ("DELETE", IDENTITY),
                                          ("DELETE", ENV + f"/applications/{APPLICATION_ID}")])
        self.assertEqual(self.world.subscriptions[0]["status"], "CLOSED")
        self.assertEqual(self.world.deleted_applications, [APPLICATION_ID])
        self.assertIn("[aim-agents] ✓ Closed subscription 'Care copilot → Customer care (Agents)'", self.output())
        self.assertIn("[aim-agents] ✓ Deleted agent application 'Care copilot'", self.output())

    def test_an_identity_that_seed_am_did_not_make_is_not_detached_because_detaching_deletes_it(self):
        self.remove(with_am_agent=False)
        self.assertEqual(self.changed(), [("POST", SUBSCRIPTIONS + "/sub-1/_close"),
                                          ("DELETE", ENV + f"/applications/{APPLICATION_ID}")])
        self.assertIn(AM_APPLICATION_ID, self.world.am_applications)

    def test_an_identity_that_a_person_attached_is_not_detached(self):
        common.save_created([{k: v for k, v in x.items() if k != "identity"} for x in common.created()])
        self.remove()
        self.assertEqual(self.platform.sent("DELETE", IDENTITY), [])
        self.assertEqual(self.world.deleted_applications, [APPLICATION_ID])

    def test_what_is_gone_is_no_error(self):
        self.world.subscriptions.clear()   # the studio is gone with its subscriptions
        self.platform.add({("POST", re.compile(SUBSCRIPTIONS + "/[^/]+/_close")): 404,
                           ("DELETE", IDENTITY): (409, {"code": "agent.identity.not_attached", "message": "none"}),
                           ("DELETE", ENV + f"/applications/{APPLICATION_ID}"): 404})
        self.remove()
        self.assertIn("✓ Deleted agent application 'Care copilot'", self.output())

    def test_a_subscription_that_is_closed_already_is_no_error(self):
        self.platform.add({("POST", re.compile(SUBSCRIPTIONS + "/[^/]+/_close")):
                           (400, {"code": "subscription.closed", "message": "closed"})})
        self.remove()
        self.assertEqual(self.world.deleted_applications, [APPLICATION_ID])

    def test_an_agent_that_is_gone_answers_404_to_the_detaching_and_that_is_no_error(self):
        self.platform.add({("DELETE", IDENTITY): (404, {"code": "agent.not_found", "message": "none"})})
        self.remove()
        self.assertEqual(self.world.deleted_applications, [APPLICATION_ID])

    def test_access_management_out_of_reach_does_not_stop_the_removal(self):
        self.world.detach_answer = (502, {"code": "identity.unreachable", "message": "Access Management did not answer"})
        self.remove()
        self.assertEqual(self.world.deleted_applications, [APPLICATION_ID])
        self.assertIn("! Access Management did not answer: the identity of 'Care copilot' stays attached", self.output())

    def test_an_identity_that_stays_in_access_management_is_said(self):
        self.world.detach_answer = (200, {"applicationId": APPLICATION_ID, "identityId": AM_APPLICATION_ID, "result": "PARTIAL",
                                          "error": "The identity service refused the deletion"})
        self.remove()
        self.assertIn("! The identity of 'Care copilot' is detached and stays in Access Management: The identity service "
                      "refused the deletion", self.output())

    def test_a_refusal_that_is_not_about_what_is_gone_is_an_error(self):
        self.platform.add({("DELETE", ENV + f"/applications/{APPLICATION_ID}"): (403, {"message": "forbidden"})})
        with self.assertRaises(common.SeedError):
            self.remove()

    def test_a_removal_through_the_seed_empties_the_record(self):
        with mock.patch.object(seed, "MODULES", (aim_agents,)):
            self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual(self.world.deleted_applications, [APPLICATION_ID])
        self.assertEqual(common.created(), [])


class Declared(Seed):
    def test_the_kinds_it_records_are_the_kinds_it_labels(self):
        self.assertEqual(sorted(aim_agents.LABELS), sorted(aim_agents.ORDER))
        self.assertTrue(all(label.endswith("s") for label in aim_agents.LABELS.values()))

    def test_a_subscription_goes_before_its_application(self):
        self.assertLess(aim_agents.ORDER.index("subscription"), aim_agents.ORDER.index("agent-application"))

    def test_it_reads_the_folder_of_the_agents_and_needs_agent_management(self):
        self.assertEqual((aim_agents.FOLDERS, aim_agents.GAMMA, aim_agents.SECTION), (("aim/agents",), ("aim",), "aim-agents"))


@unittest.skipUnless(CONTRACT.is_file() and LINK_CONTRACT.is_file(),
                     "the personal playground agent-toolbox is not on this machine")
class Contract(Agents):
    """The file of the playground, as its author wrote it."""

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/agents/care-copilot.yaml": CONTRACT.read_text(encoding="utf-8"),
                         "am/gamma-link.yaml": LINK_CONTRACT.read_text(encoding="utf-8")})

    def test_the_file_gives_the_agent_its_identity_and_its_access_and_a_second_run_changes_nothing(self):
        aim_agents.seed()
        self.assertEqual(self.world.applications["agent-1"]["identityId"], AM_APPLICATION_ID)
        self.assertEqual([(s["applicationId"], s["planId"], s["status"]) for s in self.world.subscriptions],
                         [(APPLICATION_ID, "apim-agents", "ACCEPTED")])
        self.platform.calls.clear()
        aim_agents.seed()
        self.assertEqual(self.changed(), [])


if __name__ == "__main__":
    unittest.main()
