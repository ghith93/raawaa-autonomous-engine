from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from engine.remote_task_store import RemoteTaskStoreError
from engine.supabase_fallback_task_store import (
    SUPABASE_FALLBACK_PUBLISHABLE_KEY,
    SUPABASE_FALLBACK_URL,
    SupabaseFallbackTaskStore,
)


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return json.dumps(self.value).encode("utf-8")


class SupabaseFallbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.oidc = "header.payload.signature-" + "x" * 100
        self.store = SupabaseFallbackTaskStore(
            SUPABASE_FALLBACK_URL, SUPABASE_FALLBACK_PUBLISHABLE_KEY, self.oidc,
        )

    def test_pins_project_and_rejects_wrong_key_or_target(self):
        with self.assertRaises(ValueError):
            SupabaseFallbackTaskStore(
                "https://other-project.supabase.co/functions/v1/engine-fallback",
                SUPABASE_FALLBACK_PUBLISHABLE_KEY, self.oidc,
            )
        with self.assertRaises(ValueError):
            SupabaseFallbackTaskStore(SUPABASE_FALLBACK_URL, "not-a-key", self.oidc)

    def test_posts_to_pinned_function_with_public_key_and_oidc_identity(self):
        opener = Mock()
        opener.open.return_value = FakeResponse({"ok": True})
        with patch("engine.supabase_fallback_task_store.build_opener", return_value=opener) as build:
            result = self.store._request("/health", "POST", {})
        self.assertEqual(result, {"ok": True})
        build.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, SUPABASE_FALLBACK_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.oidc)
        self.assertEqual(request.get_header("Apikey"), SUPABASE_FALLBACK_PUBLISHABLE_KEY)
        self.assertEqual(json.loads(request.data), {
            "path": "/health", "method": "POST", "payload": {},
        })

    def test_rejects_non_post_requests_before_network_access(self):
        with patch("engine.supabase_fallback_task_store.build_opener") as build:
            with self.assertRaisesRegex(RemoteTaskStoreError, "unsupported request"):
                self.store._request("/v1/tasks", "GET", None)
        build.assert_not_called()

    def test_http_errors_are_safe_and_retryability_is_classified(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(SUPABASE_FALLBACK_URL, 503, "unavailable", {}, None)
        with patch("engine.supabase_fallback_task_store.build_opener", return_value=opener):
            with self.assertRaises(RemoteTaskStoreError) as caught:
                self.store._request("/health", "POST", {})
        self.assertEqual(caught.exception.status_code, 503)
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn(self.oidc, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
