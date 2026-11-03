import unittest

from engine.agent_runtime import AgentContractError, build_task_contract


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

    def test_non_object_payload_fails_closed(self):
        with self.assertRaises(AgentContractError):
            build_task_contract(self.task(payload="[]"), ROSTER)


if __name__ == "__main__":
    unittest.main()
