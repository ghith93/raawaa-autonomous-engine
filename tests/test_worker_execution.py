import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from engine.model_gateway import UsageLedger
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
            {"topic": "test topic", "constraints": ["no external writes"]},
            idempotency_key="worker-live-test",
        )

    def tearDown(self):
        self.temp.cleanup()

    def gateway(self, adapter):
        profile = QuotaProfile(
            provider="groq", account="test", model="verified-test-model",
            free_verified=True, commercial_use_allowed=True, production_use_allowed=True, enabled=True,
            minute_request_limit=30, minute_token_limit=8000,
            daily_request_limit=10, daily_token_limit=2000,
            monthly_request_limit=100, monthly_token_limit=20000,
            per_task_request_limit=2, per_task_token_limit=4000,
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

    def test_default_path_is_dry_run_and_never_calls_gateway(self):
        gateway = Mock()
        result = run_once(self.store, live_gateway=gateway, environ={})
        self.assertEqual(result["mode"], "dry_run")
        gateway.generate.assert_not_called()
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(json.loads(saved["result_json"])["provider_calls"], 0)

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
        self.assertEqual(adapter.last_request.task_kind, "research")
        self.assertIn("test topic", adapter.last_request.prompt)
        quota = manager.snapshot()[0]
        self.assertEqual(quota["daily_requests_used"], 1)
        self.assertEqual(quota["daily_tokens_used"], 20)
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "succeeded")
        stored = json.loads(saved["result_json"])
        self.assertEqual(stored["output"], "A bounded specialist result.")
        self.assertEqual(stored["external_writes"], 0)

    def test_live_switch_without_gateway_blocks_instead_of_silent_dry_run(self):
        result = run_once(
            self.store, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "live_gateway_unavailable")
        self.assertEqual(self.store.list_tasks(1)[0]["status"], "blocked")

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
