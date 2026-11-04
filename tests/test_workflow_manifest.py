import unittest

from engine.orchestrator import validate_and_plan
from engine.workflow_manifest import (
    WorkflowManifestError,
    build_workflow_run_manifest,
)


class WorkflowRunManifestTests(unittest.TestCase):
    def setUp(self):
        roster = {"agents": [
            {"id": "researcher", "enabled": True, "role": "research",
             "capabilities": ["research", "compare", "summarize"]},
            {"id": "marketer", "enabled": True, "role": "marketing_and_content",
             "capabilities": ["strategy", "draft_content", "analyze_campaigns"]},
        ]}
        workflow = {
            "id": "launch",
            "steps": [
                {"id": "research", "task_type": "research_brief", "title": "Research",
                 "payload": {"topic": "market"}, "acceptance_checks": ["sources cited"]},
                {"id": "draft", "task_type": "content_draft", "title": "Draft",
                 "payload": {"topic": "market"}, "acceptance_checks": ["reviewed"],
                 "depends_on": ["research"]},
            ],
        }
        self.plan = validate_and_plan(workflow, roster).as_dict()

    def test_manifest_has_stable_run_and_step_idempotency(self):
        first = build_workflow_run_manifest(self.plan, "run-001", 2)
        second = build_workflow_run_manifest(self.plan, "run-001", 2)
        self.assertEqual(first.plan_digest, second.plan_digest)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(first.root_step_ids, ("research",))
        self.assertEqual(first.steps[0]["task_idempotency_key"],
                         second.steps[0]["task_idempotency_key"])
        self.assertNotEqual(
            first.steps[0]["task_idempotency_key"],
            build_workflow_run_manifest(self.plan, "run-002", 2).steps[0]["task_idempotency_key"],
        )

    def test_canonical_digest_is_independent_of_mapping_insertion_order(self):
        import json
        reordered = json.loads(json.dumps(self.plan))
        reordered["steps"] = dict(reversed(list(reordered["steps"].items())))
        self.assertEqual(
            build_workflow_run_manifest(self.plan, "run-001").plan_digest,
            build_workflow_run_manifest(reordered, "run-001").plan_digest,
        )

    def test_rejects_dispatchable_or_side_effecting_plan(self):
        unsafe = dict(self.plan)
        unsafe["dispatch_authorized"] = True
        with self.assertRaisesRegex(WorkflowManifestError, "non-dispatching"):
            build_workflow_run_manifest(unsafe, "run-001")

    def test_rejects_invalid_run_identity_and_version(self):
        with self.assertRaises(WorkflowManifestError):
            build_workflow_run_manifest(self.plan, "../run")
        with self.assertRaises(WorkflowManifestError):
            build_workflow_run_manifest(self.plan, "run-001", True)
        with self.assertRaises(WorkflowManifestError):
            build_workflow_run_manifest(self.plan, "run-001", 0)

    def test_rejects_forward_or_missing_dependencies(self):
        invalid = dict(self.plan)
        invalid["steps"] = dict(self.plan["steps"])
        invalid["steps"]["research"] = dict(self.plan["steps"]["research"])
        invalid["steps"]["research"]["depends_on"] = ["draft"]
        with self.assertRaisesRegex(WorkflowManifestError, "forward dependency"):
            build_workflow_run_manifest(invalid, "run-001")

    def test_rejects_incomplete_or_duplicate_plan_order(self):
        invalid = dict(self.plan)
        invalid["order"] = ["research", "research"]
        with self.assertRaisesRegex(WorkflowManifestError, "every step exactly once"):
            build_workflow_run_manifest(invalid, "run-001")


if __name__ == "__main__":
    unittest.main()
