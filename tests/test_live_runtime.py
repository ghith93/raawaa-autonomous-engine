import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from engine.live_runtime import build_live_gateway


class LiveRuntimeFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.policy_path = self.root / "policy.json"
        self.registry_path = self.root / "profiles.json"
        self.db_path = self.root / "engine.db"
        self.policy = {
            "mode": "live_inference_opt_in",
            "daily_budget_usd": 0,
            "monthly_budget_usd": 0,
            "per_task_budget_usd": 0,
            "allow_paid_model_calls": False,
            "allow_external_writes": False,
        }
        source = Path(__file__).resolve().parents[1] / "config" / "provider_profiles.json"
        self.registry = json.loads(source.read_text(encoding="utf-8"))
        self.registry["routes"][0]["enabled"] = True
        self.registry["routes"][0]["verified_at"] = datetime.now(timezone.utc).isoformat()
        self.write_files()

    def tearDown(self):
        self.temp.cleanup()

    def write_files(self):
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        self.registry_path.write_text(json.dumps(self.registry), encoding="utf-8")

    def test_requires_operator_kill_switch(self):
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

    def test_safe_dry_run_policy_blocks_gateway_even_with_switch_and_key(self):
        self.policy["mode"] = "safe_dry_run"
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

    def test_missing_provider_key_blocks_gateway(self):
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.assertIsNone(gateway)

    def test_enabled_verified_gpt_oss_builds_gateway_without_sending_request(self):
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNotNone(gateway)
        self.assertEqual(len(gateway.adapters), 1)
        self.assertIn("groq:primary:openai/gpt-oss-20b", gateway.adapters)
        # Factory construction is local only; no adapter.generate/network call is made.

    def test_cloud_mode_requires_durable_worker_credentials_and_never_falls_back(self):
        with self.assertRaisesRegex(RuntimeError, "RAAWAA_ENGINE_API_TOKEN"):
            build_live_gateway(
                self.db_path, self.policy_path, self.registry_path,
                environ={
                    "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
                    "RAAWAA_ENGINE_API_URL": "https://engine.example",
                    "GROQ_API_KEY": "test-key",
                },
            )

    def test_cloud_mode_uses_durable_d1_monetary_ledger_without_local_fallback(self):
        fake_usage = type("FakeUsageLedger", (), {
            "budget_status": lambda self, *args, **kwargs: {
                "daily_over_budget": False,
                "monthly_over_budget": False,
                "per_task_over_budget": False,
            },
            "totals": lambda self, *args, **kwargs: {
                "daily_usd": 0.0, "monthly_usd": 0.0,
                "daily_reserved_usd": 0.0, "monthly_reserved_usd": 0.0,
                "daily_total_nanos": 0, "monthly_total_nanos": 0,
                "pending_reservations": 0,
            },
        })()
        class FakeManager:
            def __init__(self, profiles, ledger):
                self.profiles = profiles
                self.ledger = ledger
            def eligible(self, profile, estimated_tokens):
                return True
            def snapshot(self):
                return []

        environ = {
            "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
            "RAAWAA_ENGINE_API_URL": "https://engine.example",
            "RAAWAA_ENGINE_API_TOKEN": "test-worker-token",
            "GROQ_API_KEY": "test-key",
        }
        with patch("engine.live_runtime.D1QuotaLedger", return_value=object()):
            with patch("engine.live_runtime.D1UsageLedger", return_value=fake_usage) as d1_usage:
                with patch("engine.live_runtime.QuotaManager", side_effect=FakeManager):
                    with patch("engine.live_runtime.OpenAICompatibleAdapter", return_value=object()):
                        with patch("engine.live_runtime.UsageLedger") as local_usage:
                            gateway = build_live_gateway(
                                self.db_path, self.policy_path, self.registry_path,
                                environ=environ,
                            )
        self.assertIsNotNone(gateway)
        self.assertIs(gateway.usage_ledger, fake_usage)
        d1_usage.assert_called_once_with()
        local_usage.assert_not_called()

    def test_preview_profile_is_not_eligible_even_if_marked_enabled(self):
        qwen = next(route for route in self.registry["routes"]
                    if route["model"] == "qwen/qwen3.8-27b")
        qwen["enabled"] = True
        qwen["verified_at"] = datetime.now(timezone.utc).isoformat()
        self.registry["routes"] = [qwen]
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

    def test_nonzero_cost_profile_is_not_eligible(self):
        self.registry["routes"][0]["input_cost_per_million_usd"] = 0.01
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

    def test_nonzero_per_task_budget_blocks_gateway_even_with_zero_daily_and_monthly(self):
        self.policy["per_task_budget_usd"] = 0.05
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

    def test_boolean_or_missing_budget_values_fail_closed(self):
        for value in (True, None, "0"):
            with self.subTest(value=value):
                self.policy["per_task_budget_usd"] = value
                self.write_files()
                gateway = build_live_gateway(
                    self.db_path, self.policy_path, self.registry_path,
                    environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
                )
                self.assertIsNone(gateway)

    def test_nonzero_budget_is_rejected_by_current_free_only_runtime(self):
        self.policy["daily_budget_usd"] = 0.01
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

    def test_paid_policy_is_rejected(self):
        self.policy["allow_paid_model_calls"] = True
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)


if __name__ == "__main__":
    unittest.main()
