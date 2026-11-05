import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from engine.d1_monetary_ledger import D1MonetaryLedgerError

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

    def test_verified_zero_cost_route_does_not_require_paid_provider_hard_stop(self):
        self.policy["model_policy"]["provider_hard_stop_verified"] = False
        self.policy["model_policy"]["provider_hard_stop_evidence"] = None
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNotNone(gateway)
        self.assertEqual(len(gateway.adapters), 1)

    def test_paid_fallback_cannot_be_enabled_without_paid_hard_stop(self):
        self.policy["model_policy"]["paid_fallback_enabled"] = True
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

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

    def test_native_gemini_profile_selects_gemini_adapter_without_network_io(self):
        route = dict(self.registry["routes"][0])
        route.update({
            "id": "gemini-free-flash-primary",
            "provider": "gemini",
            "account": "primary",
            "model": "gemini-2.5-flash",
            "api_key_env": "GEMINI_API_KEY",
            "endpoint_url": "https://generativelanguage.googleapis.com/v1beta",
            "pricing_source_url": "https://ai.google.dev/gemini-api/docs/pricing",
            "quota_source_url": "https://ai.google.dev/gemini-api/docs/rate-limits",
            "terms_source_url": "https://ai.google.dev/gemini-api/terms",
            "model_terms_source_url": "https://ai.google.dev/gemini-api/terms",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "enabled": True,
        })
        self.registry["routes"] = [route]
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GEMINI_API_KEY": "test-key"},
        )
        self.assertIsNotNone(gateway)
        adapter = gateway.adapters["gemini:primary:gemini-2.5-flash"]
        from engine.live_adapters import GeminiAdapter
        self.assertIsInstance(adapter, GeminiAdapter)
        # Construction must not send an inference request.

    def test_unknown_provider_and_arbitrary_endpoint_fail_closed(self):
        route = dict(self.registry["routes"][0])
        route.update({
            "id": "unknown-provider-route",
            "provider": "unknown-provider",
            "api_key_env": "UNKNOWN_PROVIDER_KEY",
            "endpoint_url": "https://attacker.example/v1",
            "enabled": True,
        })
        self.registry["routes"] = [route]
        self.write_files()
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "UNKNOWN_PROVIDER_KEY": "test-key"},
        )
        self.assertIsNone(gateway)

    def test_cloud_mode_requires_d1_token_and_never_uses_local_sqlite(self):
        with self.assertRaises(D1MonetaryLedgerError):
            build_live_gateway(
                self.db_path, self.policy_path, self.registry_path,
                environ={
                    "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
                    "RAAWAA_ENGINE_API_URL": "https://engine.example",
                    "GROQ_API_KEY": "test-key",
                },
            )

    def test_cloud_mode_injects_shared_d1_monetary_and_token_ledgers(self):
        class FakeMonetaryLedger:
            def budget_status(self, *args, **kwargs):
                return {"daily_over_budget": False, "monthly_over_budget": False, "per_task_over_budget": False}
            def totals(self):
                return {"daily_total_nanos": 0, "monthly_total_nanos": 0, "pending_reservations": 0}

        class FakeQuotaLedger:
            def usage_today(self, *args, **kwargs): return {"requests": 0, "tokens": 0}
            def usage_minute(self, *args, **kwargs): return {"requests": 0, "tokens": 0}
            def usage_month(self, *args, **kwargs): return {"requests": 0, "tokens": 0}
            def usage_for_task(self, *args, **kwargs): return {"requests": 0, "tokens": 0}
            def cooldown_active(self, *args, **kwargs): return False

        fake_money = FakeMonetaryLedger()
        fake_quota = FakeQuotaLedger()
        with (
            patch("engine.live_runtime.D1MonetaryLedger", return_value=fake_money) as money_factory,
            patch("engine.live_runtime.D1QuotaLedger", return_value=fake_quota) as quota_factory,
        ):
            gateway = build_live_gateway(
                self.db_path, self.policy_path, self.registry_path,
                environ={
                    "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
                    "RAAWAA_ENGINE_API_URL": "https://engine.example",
                    "RAAWAA_ENGINE_API_TOKEN": "test-token",
                    "RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN": "quota-admin-test-token",
                    "GROQ_API_KEY": "test-key",
                },
            )
        self.assertIsNotNone(gateway)
        self.assertIs(gateway.usage_ledger, fake_money)
        self.assertIs(gateway.quota_manager.ledger, fake_quota)
        money_factory.assert_called_once_with(
            base_url="https://engine.example", token="test-token"
        )
        quota_factory.assert_called_once_with(
            base_url="https://engine.example", token="test-token",
            admin_token="quota-admin-test-token"
        )

    def test_github_actions_without_control_worker_url_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "both durable D1"):
            build_live_gateway(
                self.db_path, self.policy_path, self.registry_path,
                environ={
                    "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
                    "GITHUB_ACTIONS": "true",
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

    def test_zero_budget_caps_are_accepted_for_verified_free_routes(self):
        # Zero-spend caps must not block genuinely free (zero-cost) routes:
        # the gateway builds and every cap stays exactly USD 0.
        gateway = build_live_gateway(
            self.db_path, self.policy_path, self.registry_path,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"},
        )
        self.assertIsNotNone(gateway)
        self.assertEqual(gateway.daily_budget_usd, 0)
        self.assertEqual(gateway.monthly_budget_usd, 0)
        self.assertEqual(gateway.per_task_budget_usd, 0)

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
