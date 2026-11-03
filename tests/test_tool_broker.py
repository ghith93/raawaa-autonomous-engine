import unittest

from engine.tool_broker import (
    ApprovalEvidence,
    ToolBroker,
    ToolBrokerAuditError,
    ToolBrokerDenied,
    ToolRequest,
    validate_permission_matrix,
)


ROSTER = {"agents": [
    {"id": "reviewer", "role": "quality_and_security", "enabled": True, "capabilities": ["review"]},
    {"id": "marketer", "role": "marketing_and_content", "enabled": True, "capabilities": ["draft_content"]},
]}


MATRIX = {
    "schema_version": 1,
    "default_effect": "deny",
    "tools": {
        "repository.read_file": {
            "access": "read",
            "action": "inspect_code",
            "grants": [{"agent_role": "quality_and_security", "task_types": ["code_review"]}],
            "environments": ["local", "staging"],
        },
        "storefront.publish": {
            "access": "write",
            "action": "publish_content",
            "grants": [{"agent_role": "marketing_and_content", "task_types": ["content_draft"]}],
            "environments": ["staging"],
        },
    },
}


def request(**overrides):
    values = {
        "task_id": "task-1",
        "agent_id": "reviewer",
        "agent_role": "quality_and_security",
        "task_type": "code_review",
        "tool_name": "repository.read_file",
        "action": "inspect_code",
        "environment": "local",
        "payload": {"path": "README.md"},
    }
    values.update(overrides)
    return ToolRequest(**values)


class ToolBrokerTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.calls = []
        self.policy = {
            "daily_budget_usd": 0.4,
            "allow_paid_model_calls": False,
            "allow_external_writes": False,
            "require_human_approval_for": ["publish_content"],
        }
        self.adapters = {
            "repository.read_file": lambda req: self.calls.append(req) or "file contents",
            "storefront.publish": lambda req: self.calls.append(req) or "published",
        }
        self.broker = ToolBroker(
            self.policy, MATRIX, self.adapters, agent_roster=ROSTER, audit_sink=self.events.append
        )

    def test_permission_matrix_is_valid_and_deny_by_default(self):
        self.assertEqual(validate_permission_matrix(MATRIX), [])
        invalid = {**MATRIX, "default_effect": "allow"}
        self.assertTrue(validate_permission_matrix(invalid))
        mismatched = {"agents": ROSTER["agents"][:1]}
        self.assertTrue(validate_permission_matrix(MATRIX, mismatched))

    def test_read_permission_cannot_be_used_for_execution_or_network_actions(self):
        for action in ("run_tests", "research"):
            with self.subTest(action=action):
                unsafe = {
                    **MATRIX,
                    "tools": {
                        **MATRIX["tools"],
                        "repository.read_file": {
                            **MATRIX["tools"]["repository.read_file"],
                            "action": action,
                        },
                    },
                }
                errors = validate_permission_matrix(unsafe, ROSTER)
                self.assertTrue(any("read access is restricted to inspect_code" in error for error in errors))

    def test_authorized_read_executes_through_registered_adapter(self):
        result = self.broker.execute(request())
        self.assertEqual(result, "file contents")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            [event["event"] for event in self.events],
            ["tool_call_authorized", "tool_call_completed"],
        )

    def test_unknown_tool_is_denied_without_adapter_execution(self):
        decision = self.broker.authorize(request(tool_name="shell.exec", action="run_shell"))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "tool_not_allowlisted")
        self.assertEqual(self.calls, [])

    def test_role_task_action_and_environment_must_all_match(self):
        cases = [
            ({"agent_role": "marketing_and_content"}, "agent_identity_not_authorized"),
            ({"task_type": "system_audit"}, "task_agent_assignment_mismatch"),
            ({"action": "publish_content"}, "tool_action_mismatch"),
            ({"environment": "production"}, "environment_not_authorized"),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                decision = self.broker.authorize(request(**overrides))
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, expected)

    def test_unregistered_adapter_is_denied(self):
        broker = ToolBroker(
            self.policy, MATRIX, {}, agent_roster=ROSTER, audit_sink=self.events.append
        )
        decision = broker.authorize(request())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "trusted_adapter_not_registered")

    def test_paid_call_is_denied_before_adapter(self):
        decision = self.broker.authorize(request(estimated_cost_usd=0.01))
        self.assertFalse(decision.allowed)
        self.assertEqual(self.calls, [])

    def test_external_write_is_denied_even_with_approval_id_when_policy_is_off(self):
        req = ToolRequest(
            task_id="task-2", agent_id="marketer",
            agent_role="marketing_and_content", task_type="content_draft",
            tool_name="storefront.publish", action="publish_content",
            environment="staging", payload={"content": "draft"},
            approval_id="approval-1", idempotency_key="idem-1",
        )
        decision = self.broker.authorize(req)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "external_writes_disabled")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.events[-1]["event"], "tool_call_denied")

    def test_write_requires_trusted_approval_and_idempotency(self):
        policy = {**self.policy, "allow_external_writes": True}
        broker = ToolBroker(policy, MATRIX, self.adapters, agent_roster=ROSTER, audit_sink=self.events.append)
        req = ToolRequest(
            task_id="task-2", agent_id="marketer",
            agent_role="marketing_and_content", task_type="content_draft",
            tool_name="storefront.publish", action="publish_content",
            environment="staging", payload={"content": "draft"},
        )
        decision = broker.authorize(req)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_exact_trusted_approval_evidence_is_required_for_write(self):
        policy = {**self.policy, "allow_external_writes": True}
        req = ToolRequest(
            task_id="task-2", agent_id="marketer",
            agent_role="marketing_and_content", task_type="content_draft",
            tool_name="storefront.publish", action="publish_content",
            environment="staging", payload={"content": "draft"},
            approval_id="approval-1", idempotency_key="idem-1",
        )
        evidence = ApprovalEvidence(
            approval_id="approval-1", task_id="task-2", agent_id="marketer",
            agent_role="marketing_and_content", tool_name="storefront.publish",
            action="publish_content", environment="staging",
        )
        broker = ToolBroker(
            policy, MATRIX, self.adapters, agent_roster=ROSTER, audit_sink=self.events.append,
            approval_verifier=lambda _request: evidence,
        )
        self.assertEqual(broker.execute(req), "published")

        mismatch = ApprovalEvidence(
            approval_id="approval-1", task_id="other-task", agent_id="marketer",
            agent_role="marketing_and_content", tool_name="storefront.publish",
            action="publish_content", environment="staging",
        )
        broker = ToolBroker(
            policy, MATRIX, self.adapters, agent_roster=ROSTER, audit_sink=self.events.append,
            approval_verifier=lambda _request: mismatch,
        )
        with self.assertRaises(ToolBrokerDenied):
            broker.execute(req)

    def test_audit_failure_blocks_execution(self):
        def broken_audit(_record):
            raise OSError("audit unavailable")
        broker = ToolBroker(
            self.policy, MATRIX, self.adapters, agent_roster=ROSTER, audit_sink=broken_audit
        )
        decision = broker.authorize(request())
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "audit_sink_unavailable")
        self.assertEqual(self.calls, [])

    def test_completion_audit_failure_is_reported_as_ambiguous(self):
        count = {"value": 0}
        def fail_after_authorization(_record):
            count["value"] += 1
            if count["value"] == 2:
                raise OSError("audit unavailable")
        broker = ToolBroker(
            self.policy, MATRIX, self.adapters, agent_roster=ROSTER, audit_sink=fail_after_authorization
        )
        with self.assertRaises(ToolBrokerAuditError):
            broker.execute(request())
        self.assertEqual(len(self.calls), 1)

    def test_mutating_trusted_config_after_construction_does_not_widen_broker(self):
        policy = dict(self.policy)
        matrix = {
            **MATRIX,
            "tools": {name: dict(spec) for name, spec in MATRIX["tools"].items()},
        }
        broker = ToolBroker(policy, matrix, self.adapters, agent_roster=ROSTER, audit_sink=self.events.append)
        policy["allow_external_writes"] = True
        matrix["tools"]["repository.read_file"]["action"] = "run_tests"
        decision = broker.authorize(request())
        self.assertTrue(decision.allowed)

    def test_invalid_request_fails_closed(self):
        decision = self.broker.authorize(request(environment="unknown"))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "invalid_tool_request")


if __name__ == "__main__":
    unittest.main()
