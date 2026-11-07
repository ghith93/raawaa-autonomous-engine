"""No-network reconciliation inventory tests; no claimed enqueue evidence."""
import contextlib
import io
import json
import tempfile
import subprocess
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import plan_deferred_issue_comments as planner
ENVELOPE = """TASK v1
id: ARENA-INGEST-001
from: owner
to: arena
scope: research-only
base: aa3ac25
forbidden: repo-write, deployment
cost_cap_usd: 0
expires: 2026-12-01
acceptance: Write docs/operations/ingest-check.md and confirm the file exists
evidence: docs/operations/ingest-check.md
"""

TODAY = date(2026, 11, 4)


def record(comment_id, body=ENVELOPE, *, author="ghith93", author_type="User", edited=False):
    return {"id": comment_id, "body": body, "user": {"login": author, "type": author_type},
            "created_at": "2026-11-04T00:00:00Z",
            "updated_at": "2026-11-04T00:01:00Z" if edited else "2026-11-04T00:00:00Z"}


class DeferredInventoryTests(unittest.TestCase):
    def test_candidates_are_unknown_not_claimed_pending_or_enqueued(self):
        rows = planner.inventory([record(9), record(2, "plain text"), record(3, author_type="Bot")], today=TODAY)
        self.assertEqual([x["comment_id"] for x in rows], [2, 3, 9])
        self.assertEqual(rows[2]["state"], "candidate_enqueue_status_unknown")
        self.assertEqual(rows[2]["idempotency_key"], "github-comment-9")
        self.assertEqual(rows[0]["state"], "ignore")
        self.assertEqual(rows[1]["state"], "skip")
        self.assertNotIn(ENVELOPE, json.dumps(rows))

    def test_edited_valid_envelope_and_expired_envelope_never_become_candidates(self):
        rows = planner.inventory([record(1, edited=True), record(2, ENVELOPE.replace("2026-12-01", "2026-01-01"))], today=TODAY)
        self.assertEqual(rows[0]["state"], "edited_requires_review")
        self.assertNotIn("idempotency_key", rows[0])
        self.assertEqual(rows[1]["state"], "invalid_or_expired")

    def test_duplicate_or_malformed_input_fails_closed(self):
        for rows in ([record(1), record(1)], [{"id": 1}], [record(True)], [record(-1)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                planner.inventory(rows, today=TODAY)
        with self.assertRaises(ValueError):
            planner.inventory([record(1)], issue_number=577, today=TODAY)

    def test_cli_only_reads_local_file_and_does_not_post(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "comments.json"
            path.write_text(json.dumps([record(7)]), encoding="utf-8")
            out = io.StringIO()
            with patch("scripts.github_comment_ingest._post") as post, contextlib.redirect_stdout(out):
                self.assertEqual(planner.main([str(path), "--today", "2026-11-04"]), 0)
                post.assert_not_called()
            result = json.loads(out.getvalue())
            self.assertEqual(result["mode"], "offline_inventory_only")
            self.assertEqual(result["enqueue_status"], "unknown")
            self.assertNotIn(ENVELOPE, out.getvalue())

    def test_direct_cli_invocation_does_not_require_pythonpath(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "comments.json"
            path.write_text(json.dumps([record(11)]), encoding="utf-8")
            script = Path(planner.__file__).resolve()
            result = subprocess.run(["python3", str(script), str(path), "--today", "2026-11-04"],
                                    text=True, capture_output=True, cwd=folder, check=True)
            self.assertEqual(json.loads(result.stdout)["results"][0]["state"], "candidate_enqueue_status_unknown")
            self.assertEqual(result.stderr, "")

    def test_invalid_json_has_generic_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            path.write_text("secret-body-not-json", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(planner.main([str(path)]), 2)
            self.assertNotIn("secret-body-not-json", err.getvalue())


if __name__ == "__main__":
    unittest.main()
