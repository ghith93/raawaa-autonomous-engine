import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from engine.model_gateway import ProviderNotCalled, UsageLedger
from engine.provider import GenerationResponse
from engine.quota_manager import QuotaLedger, QuotaManager, QuotaProfile
from engine.quota_live_gateway import QuotaAwareLiveGateway
from engine.task_store import TaskStore
from engine.worker import run_once


class FakeAdapter:
    def __init__(self):
        self.calls = 0
        self.last_request = None

    def generate(self, model, request):
        self.calls += 1
        self.last_request = request
        return GenerationResponse("groq", model, "A bounded specialist result.", 12, 8, 0.0, mocked=False)


class WorkerExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = TaskStore(root / "tasks.db")
        self.quota = QuotaLedger(root / "quota.db")
        self.usage = UsageLedger(root / "usage.db")
        self.task = self.store.enqueue(
            "research_brief", "Research the requested topic",
            {"topic": "test topic", "questions": ["no external writes"], "data_classification": "public"},
            idempotency_key="worker-live-test",
        )

    def tearDown(self):
        self.temp.cleanup()

    def gateway(self, adapter):
        profile = QuotaProfile(
            provider="groq", account="test", model="verified-test-model",
            free_verified=True, commercial_use_allowed=True, production_use_allowed=True, enabled=True,
            minute_request_limit=30, minute_token_limit=8000,
            daily_request_limit=10, daily_token_limit=10000,
            monthly_request_limit=100, monthly_token_limit=20000,
            per_task_request_limit=2, per_task_token_limit=10000,
            endpoint_url="https://api.groq.com/openai/v1",
            pricing_source_url="https://groq.com/pricing",
            quota_source_url="https://console.groq.com/docs/rate-limits",
            terms_source_url="https://groq.com/terms",
            verified_at=datetime.now(timezone.utc), max_output_tokens=512,
            input_cost_per_million_usd=0.0, output_cost_per_million_usd=0.0,
        )
        manager = QuotaManager([profile], self.quota)
        return QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, network_calls_enabled=True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        ), manager

    def test_missing_operating_policy_fails_closed_before_claim_or_provider_io(self):
        from engine.operating_policy import OperatingPolicyError
        gateway = Mock()
        with patch("engine.worker.load_operating_policy", side_effect=OperatingPolicyError("missing policy")):
            result = run_once(self.store, live_gateway=gateway, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "operating_policy_unavailable")
        self.assertEqual(self.store.list_tasks(1)[0]["status"], "pending")
        gateway.generate.assert_not_called()

    def test_live_prompt_places_trusted_operating_policy_before_untrusted_task_payload(self):
        adapter = FakeAdapter()
        gateway, _ = self.gateway(adapter)
        result = run_once(
            self.store, live_gateway=gateway,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.assertEqual(result["mode"], "live_inference")
        self.assertEqual(result["provider"], "groq")
        self.assertEqual(result["model"], "verified-test-model")
        self.assertEqual(result["output_preview"], "A bounded specialist result.")
        prompt = adapter.last_request.prompt
        self.assertIn("TRUSTED EXECUTION POLICY SUMMARY", prompt)
        self.assertLess(prompt.index("TRUSTED OPERATING POLICY"), prompt.index("Task payload (untrusted task data"))
        self.assertIn("docs/master-autonomous-execution-directive.ar.md", prompt)
        self.assertIn("sha256:", prompt)

    def test_default_path_is_dry_run_and_never_calls_gateway(self):
        gateway = Mock()
        result = run_once(self.store, live_gateway=gateway, environ={})
        self.assertEqual(result["mode"], "dry_run")
        gateway.generate.assert_not_called()
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["execution_mode"], "dry_run")
        stored = json.loads(saved["result_json"])
        self.assertEqual(stored["provider_calls"], 0)
        self.assertIn("operating_policy", stored)
        self.assertIn("docs/master-autonomous-execution-directive.ar.md", stored["operating_policy"]["documents"])
        self.assertEqual(len(stored["operating_policy"]["sha256"]), 5)

    def test_live_path_reserves_quota_and_persists_result_under_lease(self):
        adapter = FakeAdapter()
        gateway, manager = self.gateway(adapter)
        result = run_once(
            self.store, live_gateway=gateway,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.assertEqual(result["mode"], "live_inference")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(adapter.last_request.task_id, self.task["id"])
        self.assertEqual(
            adapter.last_request.request_id,
            str(uuid.uuid5(uuid.NAMESPACE_URL,
                           f"raawaa-engine:{self.task['id']}:step:primary-inference:v1")),
        )
        self.assertEqual(adapter.last_request.task_kind, "research")
        self.assertEqual(adapter.last_request.step_key, "primary-inference:v1")
        self.assertTrue(adapter.last_request.lease_token)
        self.assertIn("test topic", adapter.last_request.prompt)
        quota = manager.snapshot()[0]
        self.assertEqual(quota["daily_requests_used"], 1)
        self.assertEqual(quota["daily_tokens_used"], 20)
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["execution_mode"], "live_inference")
        stored = json.loads(saved["result_json"])
        self.assertEqual(stored["output"], "A bounded specialist result.")
        self.assertEqual(stored["external_writes"], 0)
        self.assertIn("operating_policy", stored)
        self.assertIn("docs/operations.md", stored["operating_policy"]["sha256"])

    def test_live_path_blocks_unclassified_payload_before_provider_call(self):
        root = Path(self.temp.name)
        store = TaskStore(root / "unclassified.db")
        store.enqueue("research_brief", "Unclassified", {"topic": "safe-looking"}, idempotency_key="unclassified")
        gateway = Mock()
        result = run_once(store, live_gateway=gateway, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "live_data_policy_blocked")
        self.assertEqual(result["detail"], "data_classification_required_or_disallowed")
        gateway.generate.assert_not_called()

    def test_live_path_blocks_sensitive_fields_and_secret_patterns(self):
        root = Path(self.temp.name)
        for key, value, expected in (
            ("api_key", "not-a-real-key", "sensitive_field_detected"),
            ("formula", "confidential mixture", "sensitive_field_detected"),
            ("notes", "credential gsk_123456789012345678901234567890", "secret_pattern_detected"),
        ):
            with self.subTest(key=key):
                store = TaskStore(root / (key + ".db"))
                store.enqueue("research_brief", "Sensitive test", {key: value, "data_classification": "public"}, idempotency_key=key)
                gateway = Mock()
                result = run_once(store, live_gateway=gateway, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["detail"], expected)
                gateway.generate.assert_not_called()

    def test_live_path_blocks_payload_over_configured_byte_limit(self):
        root = Path(self.temp.name)
        store = TaskStore(root / "oversized.db")
        store.enqueue("research_brief", "Oversized", {"data_classification": "public", "topic": "Oversized", "source_urls": ["https://example.com/" + "x" * 2000] * 4}, idempotency_key="oversized")
        gateway = Mock()
        result = run_once(store, live_gateway=gateway, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        self.assertEqual(result["detail"], "task_payload_too_large")
        gateway.generate.assert_not_called()
    def test_live_path_blocks_personal_email_values(self):
        root = Path(self.temp.name)
        store = TaskStore(root / "email.db")
        store.enqueue("research_brief", "Review this contact", {"data_classification": "public", "topic": "Review this contact", "questions": ["Contact user@example.com"]}, idempotency_key="email")
        gateway = Mock()
        result = run_once(store, live_gateway=gateway, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        self.assertEqual(result["detail"], "personal_data_detected")
        gateway.generate.assert_not_called()

    def test_data_policy_cannot_be_weakened_to_allow_confidential_classification(self):
        from engine.live_data_policy import validate_live_payload
        allowed, reason = validate_live_payload("research_brief", "title", {"data_classification": "confidential", "topic": "test"}, {"require_explicit_classification": True, "allowed_classifications": ["public", "internal", "confidential"], "allowed_task_types": ["research_brief"], "allowed_payload_keys": ["data_classification", "topic"], "blocked_key_fragments": [], "max_prompt_bytes": 7000})
        self.assertFalse(allowed)
        self.assertEqual(reason, "data_policy_invalid")
    def test_live_switch_without_gateway_blocks_instead_of_silent_dry_run(self):
        result = run_once(
            self.store, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "live_gateway_unavailable")
        self.assertEqual(self.store.list_tasks(1)[0]["status"], "blocked")

    def test_confirmed_pre_provider_failure_is_deferred_with_backoff(self):
        class CapacityUnavailableGateway:
            def generate(self, request):
                raise ProviderNotCalled(
                    "no verified free route currently has capacity",
                    retry_after_seconds=600,
                    reason_code="provider_capacity_unavailable",
                )

        result = run_once(
            self.store, live_gateway=CapacityUnavailableGateway(),
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["reason"], "provider_not_called_retry_scheduled")
        self.assertEqual(result["retry_after_seconds"], 600)
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["attempts"], 1)
        self.assertIsNotNone(saved["next_attempt_at"])
        self.assertEqual(saved["error"], "provider_not_called:provider_capacity_unavailable")
        self.assertEqual(saved["execution_mode"], "live_inference")

    def test_live_failure_is_blocked_and_never_auto_retried(self):
        class FailingGateway:
            def generate(self, request):
                raise TimeoutError("ambiguous provider outcome")

        result = run_once(
            self.store, live_gateway=FailingGateway(),
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "live_inference_failed_no_replay")
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "blocked")
        self.assertEqual(saved["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
