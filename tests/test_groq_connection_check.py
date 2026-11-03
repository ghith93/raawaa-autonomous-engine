"""Tests for the read-only Groq catalog diagnostic; no provider requests are sent."""
from __future__ import annotations

import unittest

from scripts.groq_connection_check import (
    FREE_MODEL_CANDIDATES,
    extract_model_ids,
    safe_provider_error_code,
)


class GroqConnectionCheckTests(unittest.TestCase):
    def test_extracts_exact_model_ids_and_ignores_malformed_rows(self):
        ids = extract_model_ids({
            "data": [
                {"id": "openai/gpt-oss-120b"},
                {"id": "openai/gpt-oss-20b"},
                {"id": 42},
                None,
            ]
        })
        self.assertEqual(ids, set(FREE_MODEL_CANDIDATES))

    def test_rejects_unexpected_catalog_shape(self):
        with self.assertRaises(ValueError):
            extract_model_ids({"models": []})

    def test_extracts_only_safe_machine_error_code(self):
        raw = (
            b'{"error":{"message":"Do not log this private provider message",'
            b'"type":"permissions_error","code":"model_permission_blocked_org"}}'
        )
        self.assertEqual(safe_provider_error_code(raw), "model_permission_blocked_org")
        self.assertNotIn("private provider message", safe_provider_error_code(raw) or "")

    def test_never_returns_provider_message_or_unbounded_code(self):
        raw = b'{"error":{"message":"secret detail","code":"bad code\nwith newline"}}'
        self.assertIsNone(safe_provider_error_code(raw))
        self.assertIsNone(safe_provider_error_code(b"not-json"))


if __name__ == "__main__":
    unittest.main()
