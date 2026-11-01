import unittest

from engine.orchestrator import WorkflowValidationError, validate_and_plan


ROSTER = {"agents": [
    {"id": "manager", "enabled": True},
    {"id": "researcher", "enabled": True},
    {"id": "reviewer", "enabled": True},
    {"id": "disabled", "enabled": False},
]}


class OrchestratorTests(unittest.TestCase):
    def workflow(self):
        return {"id": "launch_plan", "steps": [
            {"id": "research", "task_type": "research_brief", "title": "Research", "agent": "researcher"},
            {"id": "draft", "task_type": "content_draft", "title": "Draft", "agent": "manager", "depends_on": ["research"]},
            {"id": "review", "task_type": "code_review", "title": "Review", "agent": "reviewer", "depends_on": ["draft"]},
        ]}

    def test_topological_plan_and_root_ready(self):
        plan = validate_and_plan(self.workflow(), ROSTER).as_dict()
        self.assertEqual(plan["order"], ["research", "draft", "review"])
        self.assertEqual(plan["ready"], ["research"])
        self.assertEqual(plan["waiting"]["review"], ["draft"])
        self.assertFalse(plan["execution_started"])
        self.assertEqual(plan["provider_calls"], 0)

    def test_cycle_rejected(self):
        wf = self.workflow()
        wf["steps"][0]["depends_on"] = ["review"]
        with self.assertRaises(WorkflowValidationError):
            validate_and_plan(wf, ROSTER)

    def test_unknown_dependency_rejected(self):
        wf = self.workflow()
        wf["steps"][1]["depends_on"] = ["missing"]
        with self.assertRaises(WorkflowValidationError):
            validate_and_plan(wf, ROSTER)

    def test_unsafe_task_type_rejected(self):
        wf = self.workflow()
        wf["steps"][0]["task_type"] = "shell"
        with self.assertRaises(WorkflowValidationError):
            validate_and_plan(wf, ROSTER)

    def test_disabled_agent_rejected(self):
        wf = self.workflow()
        wf["steps"][0]["agent"] = "disabled"
        with self.assertRaises(WorkflowValidationError):
            validate_and_plan(wf, ROSTER)

    def test_duplicate_ids_rejected(self):
        wf = self.workflow()
        wf["steps"][1]["id"] = "research"
        with self.assertRaises(WorkflowValidationError):
            validate_and_plan(wf, ROSTER)


if __name__ == "__main__":
    unittest.main()
