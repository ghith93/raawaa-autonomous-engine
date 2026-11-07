import unittest

from engine.agent_runtime import AgentContractError, build_task_contract
from engine.worker import SAFE_TASKS


ROSTER = {"agents": [
    {"id": "researcher", "role": "research", "enabled": True, "capabilities": ["research"]},
    {"id": "reviewer", "role": "quality", "enabled": True, "capabilities": ["review"]},
    {"id": "marketer", "role": "content", "enabled": True, "capabilities": ["draft_content"]},
    {"id": "operator", "role": "ops", "enabled": True, "capabilities": ["monitor"]},
    {"id": "mobile_tester", "role": "mobile_quality_assurance", "enabled": False, "capabilities": ["android_ui_testing"]},
]}


class AgentContractTests(unittest.TestCase):
    def task(self, task_type="research_brief", payload='{"topic":"test"}'):
        return {"task_type": task_type, "title": "Test task", "payload_json": payload}

    def test_each_safe_task_has_a_specialist_and_capability(self):
        expected = {
            "research_brief": ("researcher", "research"),
            "code_review": ("reviewer", "review"),
            "content_draft": ("marketer", "draft_content"),
            "system_audit": ("operator", "monitor"),
        }
        for task_type, assignment in expected.items():
            with self.subTest(task_type=task_type):
                contract = build_task_contract(self.task(task_type), ROSTER)
                self.assertEqual((contract["agent_id"], contract["required_capability"]), assignment)
                self.assertEqual(contract["mode"], "dry_run")
                self.assertFalse(contract["execution_started"])
                self.assertEqual(contract["provider_calls"], 0)
                self.assertEqual(contract["external_writes"], 0)

    def test_disabled_or_missing_specialist_fails_closed(self):
        roster = {"agents": [{**ROSTER["agents"][0], "enabled": False}]}
        with self.assertRaises(AgentContractError):
            build_task_contract(self.task(), roster)

    def test_missing_capability_fails_closed(self):
        roster = {"agents": [{**ROSTER["agents"][0], "capabilities": []}]}
        with self.assertRaises(AgentContractError):
            build_task_contract(self.task(), roster)

    def test_unknown_task_type_fails_closed(self):
        with self.assertRaises(AgentContractError):
            build_task_contract(self.task("shell"), ROSTER)

    def test_mobile_test_is_not_worker_allowlisted_without_a_real_executor(self):
        self.assertNotIn("mobile_test", SAFE_TASKS)

    def test_mobile_tester_is_registered_but_disabled_until_artemis_is_verified(self):
        with self.assertRaisesRegex(AgentContractError, "unavailable: mobile_tester"):
            build_task_contract(self.task("mobile_test"), ROSTER)

        enabled_roster = {"agents": [
            {**agent, "enabled": True} if agent["id"] == "mobile_tester" else agent
            for agent in ROSTER["agents"]
        ]}
        contract = build_task_contract(self.task("mobile_test"), enabled_roster)
        self.assertEqual(contract["agent_id"], "mobile_tester")
        self.assertEqual(contract["required_capability"], "android_ui_testing")
        self.assertEqual(contract["mode"], "dry_run")
        self.assertEqual(contract["provider_calls"], 0)
        self.assertEqual(contract["external_writes"], 0)

    def test_task_contract_includes_read_only_topology_context_without_changing_assignment(self):
        contract = build_task_contract(self.task(), ROSTER)
        context = contract["topology_context"]
        self.assertEqual(contract["agent_id"], "researcher")
        self.assertEqual(context["status"], "available")
        self.assertEqual(context["mode"], "read_only_advisory")
        self.assertEqual(context["agent_node"]["id"], "agent:researcher")
        self.assertEqual(context["agent_node"]["status"], "declared_only")
        self.assertRegex(context["map_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("does not prove a live agent process", context["authority_note"])

    def test_invalid_or_incomplete_topology_is_advisory_and_does_not_block_safe_task(self):
        contract = build_task_contract(self.task(), ROSTER, project_map={"schema_version": True})
        self.assertEqual(contract["mode"], "dry_run")
        self.assertEqual(contract["agent_id"], "researcher")
        self.assertEqual(contract["topology_context"]["status"], "unavailable")
        self.assertEqual(contract["topology_context"]["reason"], "project_topology_invalid")
        self.assertEqual(contract["provider_calls"], 0)
        self.assertEqual(contract["external_writes"], 0)

    def test_project_map_status_never_overrides_allowlisted_roster(self):
        project_map = {
            "schema_version": 1,
            "map_id": "synthetic-test-map",
            "scope_note": "Static architecture inventory only.",
            "nodes": [
                {"id": "agent:researcher", "kind": "agent", "status": "disabled", "role": "researcher"},
                {"id": "component:research_discovery", "kind": "component", "status": "implemented",
                 "evidence": ["scripts/research_discovery.py"]},
            ],
            "edges": [
                {"from": "agent:researcher", "to": "component:research_discovery",
                 "relation": "uses", "status": "role_intent_only"}
            ],
        }
        contract = build_task_contract(self.task(), ROSTER, project_map=project_map)
        self.assertEqual(contract["agent_id"], "researcher")
        self.assertEqual(contract["topology_context"]["agent_node"]["status"], "disabled")
        self.assertEqual(contract["topology_context"]["relationships"][0]["edge_status"], "role_intent_only")
        self.assertEqual(contract["topology_context"]["relationships"][0]["node"]["evidence"],
                         ["scripts/research_discovery.py"])

    def test_invalid_edge_endpoint_makes_topology_unavailable_not_task_executable(self):
        project_map = {
            "schema_version": 1,
            "map_id": "synthetic-test-map",
            "scope_note": "Static architecture inventory only.",
            "nodes": [
                {"id": "agent:researcher", "kind": "agent", "status": "declared_only"},
            ],
            "edges": [
                {"from": "agent:researcher", "to": "component:missing",
                 "relation": "uses", "status": "role_intent_only"}
            ],
        }
        contract = build_task_contract(self.task(), ROSTER, project_map=project_map)
        self.assertEqual(contract["topology_context"]["status"], "unavailable")
        self.assertEqual(contract["mode"], "dry_run")
        self.assertFalse(contract["execution_started"])

    def test_non_object_payload_fails_closed(self):
        with self.assertRaises(AgentContractError):
            build_task_contract(self.task(payload="[]"), ROSTER)


if __name__ == "__main__":
    unittest.main()
