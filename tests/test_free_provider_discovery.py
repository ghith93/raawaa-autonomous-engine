import unittest
from unittest.mock import patch

from engine.free_provider_discovery import DiscoveryError, discover, discover_all


class FakeResponse:
    status = 200
    def __init__(self, payload):
        import json
        self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.payload


class FreeProviderDiscoveryTests(unittest.TestCase):
    def test_missing_key_fails_closed(self):
        with self.assertRaises(DiscoveryError):
            discover("groq", {})

    def test_unknown_provider_rejected(self):
        with self.assertRaises(DiscoveryError):
            discover("unknown", {})

    @patch("engine.free_provider_discovery.urlopen")
    def test_openai_compatible_model_list(self, mocked):
        mocked.return_value = FakeResponse({"data": [{"id": "model-z"}, {"id": "model-a"}]})
        result = discover("groq", {"GROQ_API_KEY": "test-key"})
        self.assertEqual([item["model"] for item in result], ["model-a", "model-z"])
        self.assertFalse(result[0]["free_tier_verified"])
        request = mocked.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    @patch("engine.free_provider_discovery.urlopen")
    def test_gemini_model_list(self, mocked):
        mocked.return_value = FakeResponse({"models": [{"name": "models/gemini-test"}]})
        result = discover("gemini", {"GEMINI_API_KEY": "test-key"})
        self.assertEqual(result[0]["model"], "models/gemini-test")
        self.assertFalse(result[0]["free_tier_verified"])

    @patch("engine.free_provider_discovery.urlopen")
    def test_discover_all_reports_missing_keys_without_generation(self, mocked):
        mocked.return_value = FakeResponse({"data": []})
        result = discover_all({"GROQ_API_KEY": "test-key"})
        self.assertEqual(result["generation_requests"], 0)
        self.assertIn("gemini", result["unavailable"])
        self.assertEqual(result["models"]["groq"], [])


if __name__ == "__main__":
    unittest.main()
