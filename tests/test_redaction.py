"""A failing run publishes its cause to a public repository.

The batch reporter writes the stderr tail into an Actions annotation and the step
summary. Both are readable by anyone who can read this repository. Bounding the
tail keeps it small; redaction is what keeps it safe. These tests pin both the
masking rules and the material that must survive them.
"""

from __future__ import annotations

import io
import time
import unittest

from engine import run_report
from engine.redaction import contains_secret_shape, redact

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/home/runner/work/raawaa-autonomous-engine/engine/remote_task_store.py", line 121, in _request\n'
    "    raise RemoteTaskStoreError(\n"
    "engine.remote_task_store.RemoteTaskStoreError: Remote task API temporarily "
    "unavailable (HTTP 503): storage_unavailable | D1_ERROR: no such table: task_dependencies\n"
)


class MaskingTests(unittest.TestCase):
    def test_provider_token_prefixes_are_masked(self):
        for token in (
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "github_pat_11ABCDEFG0abcdefghijkl_0123456789",
            "sk-live-abcdefghijklmnopqrstuvwx",
            "xoxb-123456789012-abcdefghijkl",
            "AKIAIOSFODNN7EXAMPLE",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, redact(f"calling with {token} now"))

    def test_authorization_headers_are_masked_with_their_scheme(self):
        for line in (
            "Authorization: Bearer abcdefghijklmnop123456",
            'headers={"Authorization": "Bearer sk-live-abcdefghijklmnopqrstuvwx"}',
            'curl -H "Authorization: Bearer abcdefghij" https://example',
            "authorization=Basic dXNlcjpwYXNzd29yZA==",
        ):
            with self.subTest(line=line):
                out = redact(line)
                self.assertIn("[redacted]", out)
                for leak in ("abcdefghijklmnop", "sk-live", "dXNlcjpwYXNzd29yZA"):
                    self.assertNotIn(leak, out)

    def test_environment_style_token_names_are_masked(self):
        """A word boundary would miss ENGINE_API_TOKEN; the stem must be embedded."""
        for line in (
            "ENGINE_API_TOKEN=supersecretvalue123",
            "CF_ACCESS_CLIENT_SECRET=1234567890abcdef",
            "CLOUDFLARE_API_TOKEN: abcdef1234567890",
            "export GROQ_API_KEY=xyz123456789",
            'GITHUB_APP_PRIVATE_KEY="-----BEGIN-----"',
        ):
            with self.subTest(line=line):
                out = redact(line)
                self.assertIn("[redacted]", out)
                self.assertNotIn("supersecret", out)
                self.assertNotIn("1234567890abcdef", out)
                self.assertNotIn("xyz123456789", out)

    def test_json_and_quoted_values_are_masked(self):
        out = redact('{"client_secret": "topsecret", "note": "kept"}')
        self.assertIn("[redacted]", out)
        self.assertNotIn("topsecret", out)
        self.assertIn("kept", out)

    def test_long_opaque_runs_are_masked(self):
        blob = "QWxsb2NhdGlvbjptb2RlbGNoZWNrc3VtZm9ydGhlc3RvcmFnZWNvbnRleHQ="
        self.assertNotIn(blob, redact(f"payload {blob} sent"))


class PreservationTests(unittest.TestCase):
    """A redactor that eats stack frames destroys the value it protects."""

    def test_a_real_traceback_survives_intact(self):
        out = redact(TRACEBACK)
        self.assertEqual(out, TRACEBACK)

    def test_commit_ids_are_not_mistaken_for_secrets(self):
        sha = "a7f7a2f1c9b0e4d8f3a6b5c7d9e0f1a2b3c4d5e6"
        self.assertIn(sha, redact(f"base commit {sha} deployed"))

    def test_ordinary_prose_is_untouched(self):
        line = "tasks_processed=0 stop_reason=queue_idle statuses=none"
        self.assertEqual(redact(line), line)

    def test_redaction_is_idempotent(self):
        once = redact("ENGINE_API_TOKEN=abcdef123456 Bearer sk-live-abcdefghijklmnop")
        self.assertEqual(redact(once), once)

    def test_contains_secret_shape_agrees_with_redact(self):
        self.assertTrue(contains_secret_shape("token=abcdef123456"))
        self.assertFalse(contains_secret_shape("stop_reason=queue_idle"))

    def test_large_input_stays_fast(self):
        """An unbounded identifier prefix made this quadratic: 161s for a test run."""
        started = time.monotonic()
        redact("x" * 50000)
        redact("ENGINE_API_TOKEN=abc123 " * 2000)
        self.assertLess(time.monotonic() - started, 2.0)


class ReporterIntegrationTests(unittest.TestCase):
    def test_annotation_never_carries_a_secret(self):
        tail = TRACEBACK + "Authorization: Bearer abcdefghijklmnop123456\n"
        line = run_report.stderr_notice(tail)
        self.assertIn("no such table: task_dependencies", line)
        self.assertNotIn("abcdefghijklmnop123456", line)

    def test_step_summary_never_carries_a_secret(self):
        tail = TRACEBACK + "ENGINE_API_TOKEN=supersecretvalue123\n"
        block = run_report.stderr_markdown(tail)
        self.assertIn("#### stderr tail", block)
        self.assertIn("RemoteTaskStoreError", block)
        self.assertNotIn("supersecretvalue123", block)

    def test_batch_notice_values_are_redacted(self):
        batch = {"results": [{"id": "t-1", "status": "failed",
                              "reason": "auth rejected Bearer abcdefghijklmnop123456"}]}
        line = run_report.notices(batch)[0]
        self.assertNotIn("abcdefghijklmnop123456", line)
        self.assertIn("[redacted]", line)


if __name__ == "__main__":
    unittest.main()
