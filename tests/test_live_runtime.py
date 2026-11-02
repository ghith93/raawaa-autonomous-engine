import json
import tempfile
import unittest
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

    def test_per_task_budget_is_passed_as_a_cap_without_enabling_paid_calls(self):
        self.policy["per_task_budget_usd"] = 0.05
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNotNone(gateway)
        self.assertEqual(gateway.per_task_budget_usd, 0.05)
        self.assertEqual(gateway.daily_budget_usd, 0)
        self.assertEqual(gateway.monthly_budget_usd, 0)

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
