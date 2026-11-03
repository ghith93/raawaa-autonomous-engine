"""Regression tests for the one-shot Groq smoke health preflight.

These tests never contact a Worker or a model provider.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.run_groq_free_inference_smoke import _wait_for_authenticated_worker


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Store:
    def __init__(self):
        self.paths = []

    def _request(self, path):
        self.paths.append(path)
        return {"status": "ready"}


class GroqSmokePreflightTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "RAAWAA_ENGINE_API_URL": "https://raawaa-autonomous-engine-staging.example.workers.dev",
        "CF_ACCESS_CLIENT_ID": "",
        "CF_ACCESS_CLIENT_SECRET": "",
    })
    @patch("urllib.request.urlopen", return_value=_Response())
    def test_health_probe_uses_verified_worker_user_agent_without_access_secrets(
        self, open_url
    ):
        store = _Store()
        result = _wait_for_authenticated_worker(store, timeout_seconds=1)

        request = open_url.call_args.args[0]
        self.assertEqual(
            request.get_header("User-agent"),
            "RaawaaAutonomousEngine/1.0 "
            "(+https://github.com/ghith93/raawaa-autonomous-engine)",
        )
        self.assertIsNone(request.get_header("Cf-access-client-id"))
        self.assertIsNone(request.get_header("Cf-access-client-secret"))
        self.assertEqual(result, {"status": "ready"})
        self.assertEqual(store.paths, ["/v1/mobile/status"])


if __name__ == "__main__":
    unittest.main()
