import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.quota_config import load_provider_profile_registry


class ProviderProfileConfigTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "profiles.json"
        self.source = Path(__file__).resolve().parents[1] / "config" / "provider_profiles.json"

    def test_staged_groq_profile_loads_but_is_not_route_ready(self):
        entries = load_provider_profile_registry(self.source)
        self.assertEqual(len(entries), 3)
        entry = next(item for item in entries if item.profile.model == "qwen/qwen3.8-27b")
        self.assertEqual(entry.profile.provider, "groq")
        self.assertEqual(entry.profile.minute_request_limit, 30)
        self.assertEqual(entry.profile.minute_token_limit, 8000)
        self.assertEqual(entry.profile.daily_request_limit, 1000)
        self.assertEqual(entry.profile.daily_token_limit, 200000)
        self.assertEqual(entry.api_key_env, "GROQ_API_KEY")
        self.assertFalse(entry.enabled)
        self.assertFalse(entry.ready_for_routing)
        self.assertEqual(entry.model_stage, "preview")
        self.assertFalse(entry.production_use_allowed)
        self.assertTrue(entry.profile.commercial_use_allowed)
        self.assertEqual(entry.profile.input_cost_per_million_usd, 0.0)
        self.assertEqual(entry.profile.output_cost_per_million_usd, 0.0)
        self.assertTrue(entry.profile.commercial_use_allowed)
        self.assertFalse(entry.profile.production_use_allowed)


    def test_production_gpt_oss_profiles_are_staged_with_priority_and_limits(self):
        entries = load_provider_profile_registry(self.source)
        by_model = {entry.profile.model: entry for entry in entries}
        for model, priority in (("openai/gpt-oss-20b", 10), ("openai/gpt-oss-120b", 20)):
            with self.subTest(model=model):
                entry = by_model[model]
                self.assertEqual(entry.model_stage, "production")
                self.assertTrue(entry.production_use_allowed)
                self.assertTrue(entry.profile.production_use_allowed)
                self.assertFalse(entry.enabled)
                self.assertFalse(entry.ready_for_routing)
                self.assertEqual(entry.profile.priority, priority)
                self.assertEqual(entry.profile.minute_request_limit, 30)
                self.assertEqual(entry.profile.minute_token_limit, 8000)
                self.assertEqual(entry.profile.daily_request_limit, 1000)
                self.assertEqual(entry.profile.daily_token_limit, 200000)
                self.assertEqual(entry.profile.input_cost_per_million_usd, 0.0)
                self.assertIn("openai/gpt-oss", entry.model_terms_source_url)

    def test_registry_requires_managed_opt_in_mode_and_boolean_enablement(self):
        data = json.loads(self.source.read_text(encoding="utf-8"))
        data["mode"] = "staged_disabled"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "managed_opt_in"):
            load_provider_profile_registry(self.path)

        data["mode"] = "managed_opt_in"
        data["routes"][0]["enabled"] = "yes"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "explicit boolean"):
            load_provider_profile_registry(self.path)

        data["routes"][0]["enabled"] = True
        self.path.write_text(json.dumps(data), encoding="utf-8")
        enabled = load_provider_profile_registry(self.path)[0]
        self.assertTrue(enabled.enabled)
        self.assertTrue(enabled.profile.enabled)
        self.assertTrue(enabled.ready_for_routing)

    def test_stale_future_and_timezone_naive_verification_are_not_route_ready(self):
        data = json.loads(self.source.read_text(encoding="utf-8"))
        route = data["routes"][0]
        route["enabled"] = True
        now = datetime.now(timezone.utc)
        for label, verified_at in (
            ("stale", now - timedelta(days=8)),
            ("future", now + timedelta(days=1)),
            ("naive", now.replace(tzinfo=None)),
        ):
            with self.subTest(label=label):
                route["verified_at"] = verified_at.isoformat()
                self.path.write_text(json.dumps(data), encoding="utf-8")
                entry = load_provider_profile_registry(self.path)[0]
                self.assertFalse(entry.verification_is_current)
                self.assertFalse(entry.ready_for_routing)

    def test_registry_rejects_duplicate_ids(self):
        data = json.loads(self.source.read_text(encoding="utf-8"))
        data["routes"].append(dict(data["routes"][0]))
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unique"):
            load_provider_profile_registry(self.path)

    def test_registry_rejects_secret_values_in_api_key_field(self):
        data = json.loads(self.source.read_text(encoding="utf-8"))
        data["routes"][0]["api_key_env"] = "gsk_live_actual_secret_value"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "environment variable name"):
            load_provider_profile_registry(self.path)


if __name__ == "__main__":
    unittest.main()
