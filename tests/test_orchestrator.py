import unittest

from engine.orchestrator import WorkflowValidationError, validate_and_plan


ROSTER = {"agents": [
    {"id": "manager", "enabled": True, "role": "orchestrator", "capabilities": ["plan", "prioritize", "delegate", "report"]},
    {"id": "researcher", "enabled": True, "role": "research", "capabilities": ["research", "compare", "summarize"]},
    {"id": "reviewer", "enabled": True, "role": "quality_and_security", "capabilities": ["review", "risk_assess", "validate"]},
    {"id": "marketer", "enabled": True, "role": "marketing_and_content", "capabilities": ["strategy", "draft_content", "analyze_campaigns"]},
    {"id": "operator", "enabled": True, "role": "operations_and_monitoring", "capabilities": ["monitor", "triage", "report"]},
    {"id": "disabled", "enabled": False, "role": "test", "capabilities": ["research"]},
]}


class OrchestratorTests(unittest.TestCase):
    def workflow(self):
        return {"id": "launch_plan", "steps": [
            {"id": "research", "task_type": "research_brief", "title": "Research",
             "payload": {"topic": "market"}, "acceptance_checks": ["Sources are cited"]},
            {"id": "draft", "task_type": "content_draft", "title": "Draft",
             "payload": {"channel": "social"}, "acceptance_checks": ["Draft follows brand guide"],
             "depends_on": ["research"]},
            {"id": "review", "task_type": "code_review", "title": "Review",
             "acceptance_checks": ["Review findings are recorded"], "depends_on": ["draft"]},
        ]}

    def test_topological_plan_and_root_ready(self):
        plan = validate_and_plan(self.workflow(), ROSTER).as_dict()
        self.assertEqual(plan["order"], ["research", "draft", "review"])
        self.assertEqual(plan["ready"], ["research"])
        self.assertEqual(plan["waiting"]["review"], ["draft"])
        self.assertEqual(plan["assignments"], {
            "research": "researcher", "draft": "marketer", "review": "reviewer"
        })
        self.assertEqual(plan["steps"]["draft"]["required_capability"], "draft_content")
        self.assertEqual(plan["steps"]["draft"]["acceptance_checks"], ["Draft follows brand guide"])
        self.assertNotIn("payload", plan["steps"]["draft"])
        self.assertEqual(plan["execution_waves"], [["research"], ["draft"], ["review"]])
        self.assertFalse(plan["execution_started"])
        self.assertFalse(plan["dispatch_authorized"])
        self.assertEqual(plan["provider_calls"], 0)

    def test_independent_tasks_are_batched_by_parallel_limit(self):
        workflow = {"id": "parallel", "steps": [
            {"id": "r1", "task_type": "research_brief", "title": "R1",
             "acceptance_checks": ["Evidence exists"]},
            {"id": "r2", "task_type": "research_brief", "title": "R2",
             "acceptance_checks": ["Evidence exists"]},
            {"id": "audit", "task_type": "system_audit", "title": "Audit",
             "acceptance_checks": ["Audit passes"]},
        ]}
        plan = validate_and_plan(workflow, ROSTER, {"max_parallel_agents": 2}).as_dict()
        self.assertEqual(plan["execution_waves"], [["audit", "r1"], ["r2"]])

    def test_priority_orders_ready_steps_deterministically(self):
        workflow = {"id": "priority_plan", "steps": [
            {"id": "low", "task_type": "research_brief", "title": "Low",
             "acceptance_checks": ["Evidence exists"], "priority": 10},
            {"id": "high", "task_type": "research_brief", "title": "High",
             "acceptance_checks": ["Evidence exists"], "priority": 90},
            {"id": "dependent", "task_type": "code_review", "title": "Dependent",
             "acceptance_checks": ["Review exists"], "depends_on": ["low"], "priority": 100},
        ]}
        plan = validate_and_plan(workflow, ROSTER).as_dict()
        self.assertEqual(plan["order"], ["high", "low", "dependent"])
        self.assertEqual(plan["execution_waves"], [["high", "low"], ["dependent"]])
        self.assertEqual(plan["steps"]["low"]["priority"], 10)

    def test_invalid_priority_is_rejected(self):
        workflow = self.workflow()
        workflow["steps"][0]["priority"] = True
        with self.assertRaisesRegex(WorkflowValidationError, "priority must be an integer"):
            validate_and_plan(workflow, ROSTER)
        workflow["steps"][0]["priority"] = 101
        with self.assertRaisesRegex(WorkflowValidationError, "priority must be an integer"):
            validate_and_plan(workflow, ROSTER)

    def test_non_object_policy_is_rejected_as_validation_error(self):
        with self.assertRaisesRegex(WorkflowValidationError, "policy must be an object"):
            validate_and_plan(self.workflow(), ROSTER, policy=[])

    def test_wrong_specialist_is_rejected_even_if_enabled(self):
        workflow = self.workflow()
        workflow["steps"][0]["agent"] = "manager"
        with self.assertRaisesRegex(WorkflowValidationError, "does not match required specialist"):
            validate_and_plan(workflow, ROSTER)

    def test_missing_required_capability_rejected(self):
        roster = {"agents": [{**ROSTER["agents"][1], "capabilities": []}]}
        with self.assertRaisesRegex(WorkflowValidationError, "lacks required capability"):
            validate_and_plan(self.workflow(), roster)

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

    def test_disabled_agent_cannot_be_selected(self):
        roster = {"agents": [{**ROSTER["agents"][0]}, {**ROSTER["agents"][1], "enabled": False}]}
        with self.assertRaisesRegex(WorkflowValidationError, "unavailable: researcher"):
            validate_and_plan(self.workflow(), roster)

    def test_duplicate_ids_rejected(self):
        wf = self.workflow()
        wf["steps"][1]["id"] = "research"
        with self.assertRaises(WorkflowValidationError):
            validate_and_plan(wf, ROSTER)

    def test_acceptance_checks_are_required(self):
        wf = self.workflow()
        wf["steps"][0].pop("acceptance_checks")
        with self.assertRaisesRegex(WorkflowValidationError, "acceptance check"):
            validate_and_plan(wf, ROSTER)

    def test_paid_estimate_blocked_by_current_policy(self):
        wf = self.workflow()
        wf["steps"][0]["estimated_model_cost_usd"] = 0.05
        plan = validate_and_plan(wf, ROSTER).as_dict()
        self.assertIn("paid_model_calls_disabled", plan["dispatch_blockers"])
        self.assertIn("safe_dry_run_mode", plan["dispatch_blockers"])
        self.assertFalse(plan["dispatch_authorized"])

    def test_high_risk_requires_human_review(self):
        wf = self.workflow()
        wf["steps"][0]["risk"] = "critical"
        plan = validate_and_plan(wf, ROSTER).as_dict()
        self.assertTrue(plan["human_approval_required"])

    def test_cost_over_per_task_cap_rejected(self):
        wf = self.workflow()
        wf["steps"][0]["estimated_model_cost_usd"] = 0.21
        with self.assertRaisesRegex(WorkflowValidationError, "per-task model budget"):
            validate_and_plan(wf, ROSTER)


    def test_malformed_workflow_and_roster_fail_closed(self):
        with self.assertRaisesRegex(WorkflowValidationError, "workflow must be an object"):
            validate_and_plan([], ROSTER)
        with self.assertRaisesRegex(WorkflowValidationError, "agent roster"):
            validate_and_plan(self.workflow(), [])

    def test_non_string_task_type_and_risk_fail_closed(self):
        wf = self.workflow()
        wf["steps"][0]["task_type"] = []
        with self.assertRaisesRegex(WorkflowValidationError, "task type is not allowlisted"):
            validate_and_plan(wf, ROSTER)
        wf = self.workflow()
        wf["steps"][0]["risk"] = []
        with self.assertRaisesRegex(WorkflowValidationError, "invalid risk level"):
            validate_and_plan(wf, ROSTER)


    def test_mobile_test_is_rejected_until_a_real_worker_exists(self):
        wf = {"id": "mobile", "steps": [
            {"id": "device", "task_type": "mobile_test", "title": "Test device",
             "acceptance_checks": ["Device test evidence is returned"]}
        ]}
        with self.assertRaisesRegex(WorkflowValidationError, "task type is not allowlisted"):
            validate_and_plan(wf, ROSTER)


if __name__ == "__main__":
    unittest.main()
