import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from engine.live_runtime import build_live_gateway


class LiveRuntimeFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.policy_path = self.root / "policy.json"
        self.registry_path = self.root / "profiles.json"
        self.db_path = self.root / "engine.db"
        config_policy = Path(__file__).resolve().parents[1] / "config" / "policy.json"
        self.policy = json.loads(config_policy.read_text(encoding="utf-8"))
        self.policy["mode"] = "live_inference_opt_in"
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

    def test_cloud_mode_stays_blocked_until_external_spend_hard_stop_is_verified(self):
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={
                "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
                "RAAWAA_ENGINE_API_URL": "https://engine.example",
                "RAAWAA_ENGINE_API_TOKEN": "test-token",
                "RAAWAA_ENGINE_EXTERNAL_SPEND_HARD_STOP_VERIFIED": "enabled",
                "GROQ_API_KEY": "test-key",
            },
        )
        self.assertIsNone(gateway)

    def test_cloud_mode_uses_d1_adapters_only_when_both_hard_stop_gates_are_true(self):
        self.policy["budget_envelope"]["external_spend_hard_stop_verified"] = True
        self.write_files()
        environment = {
            "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
            "RAAWAA_ENGINE_API_URL": "https://engine.example",
            "RAAWAA_ENGINE_API_TOKEN": "test-token",
            "RAAWAA_ENGINE_EXTERNAL_SPEND_HARD_STOP_VERIFIED": "enabled",
            "GROQ_API_KEY": "test-key",
        }
        with patch("engine.live_runtime.D1QuotaLedger") as d1_quota, \
             patch("engine.live_runtime.D1MonetaryLedger") as d1_money, \
             patch("engine.live_runtime.load_provider_profile_registry", return_value=[]):
            gateway = build_live_gateway(
                self.db_path, self.policy_path, self.registry_path, environ=environment,
            )
        self.assertIsNone(gateway)
        d1_quota.assert_called_once_with(
            base_url="https://engine.example", token="test-token", environ=environment,
        )
        d1_money.assert_called_once_with(environ=environment)

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

    def test_nonzero_budget_caps_are_accepted_for_verified_free_routes(self):
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNotNone(gateway)
        self.assertEqual(gateway.daily_budget_usd, 0.4)
        self.assertEqual(gateway.monthly_budget_usd, 12.0)
        self.assertEqual(gateway.per_task_budget_usd, 0.2)

    def test_budget_policy_mismatch_blocks_gateway(self):
        self.policy["per_task_budget_usd"] = 0.21
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

    def test_daily_cap_mismatch_blocks_gateway(self):
        self.policy["daily_budget_usd"] = 0.5
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
