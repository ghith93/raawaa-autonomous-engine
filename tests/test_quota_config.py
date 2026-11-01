import json
import tempfile
import unittest
from pathlib import Path

from engine.quota_config import load_provider_profile_registry


class ProviderProfileConfigTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "profiles.json"
        self.source = Path(__file__).resolve().parents[1] / "config" / "provider_profiles.json"

    def test_staged_groq_profile_loads_but_is_not_route_ready(self):
        entries = load_provider_profile_registry(self.source)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.profile.provider, "groq")
        self.assertEqual(entry.profile.model, "qwen/qwen3.8-27b")
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
        self.assertEqual(entry.profile.input_cost_per_million_usd, 0.0)
        self.assertEqual(entry.profile.output_cost_per_million_usd, 0.0)
        self.assertTrue(entry.profile.commercial_use_allowed)
        self.assertFalse(entry.profile.production_use_allowed)

    def test_registry_rejects_enabled_mode_or_route(self):
        data = json.loads(self.source.read_text(encoding="utf-8"))
        data["network_calls_enabled"] = True
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "staged_disabled"):
            load_provider_profile_registry(self.path)

        data["network_calls_enabled"] = False
        data["routes"][0]["enabled"] = True
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "enabled=false"):
            load_provider_profile_registry(self.path)

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
