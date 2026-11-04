"""Tests for the issue-comment receiving edge.

Two kinds of test live here: the decision logic (what a comment turns into) and
the workflow's own safety posture (what the job is *allowed* to do). The second
kind matters as much as the first — a correct decision function behind a job that
can write to issues would still be able to loop.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import github_comment_ingest as ingest  # noqa: E402

TODAY = date(2026, 11, 4)
WORKFLOW = ROOT / ".github" / "workflows" / "issue-comment-ingest.yml"

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


def comment(body=ENVELOPE, author="ghith93", author_type="User", comment_id=6600000001):
    return {"id": comment_id, "author": author, "author_type": author_type, "body": body}


class DecisionTests(unittest.TestCase):
    def _plan(self, **kwargs):
        issue = kwargs.pop("issue_number", 464)
        return ingest.plan(comment(**kwargs), issue, "ghith93", 464, today=TODAY)

    def test_valid_envelope_enqueues(self):
        result = self._plan()
        self.assertEqual(result["action"], "enqueue", result)
        task = result["task"]
        self.assertEqual(task["task_type"], "issue_comment_task")
        self.assertEqual(task["idempotency_key"], "github-comment-6600000001")
        self.assertEqual(task["payload"]["envelope"]["id"], "ARENA-INGEST-001")
        self.assertEqual(len(task["payload"]["envelope_fingerprint"]), 64)

    def test_payload_is_json_serialisable(self):
        task = self._plan()["task"]
        self.assertIn("ARENA-INGEST-001", json.dumps(task))

    def test_prose_comment_is_ignored(self):
        result = self._plan(body="شكرًا، سأراجع الأمر لاحقًا.")
        self.assertEqual(result["action"], "ignore")
        self.assertEqual(result["reason"], "no_envelope")
        self.assertNotIn("task", result)

    def test_bot_author_is_skipped(self):
        result = self._plan(author_type="Bot")
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "bot_author")

    def test_other_actor_is_skipped(self):
        result = self._plan(author="someone-else")
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "actor_not_allowed")

    def test_other_issue_is_skipped(self):
        result = self._plan(issue_number=999)
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "issue_not_tracked")

    def test_missing_comment_id_is_skipped(self):
        result = self._plan(comment_id=None)
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "comment_id_missing")

    def test_unfalsifiable_acceptance_is_rejected(self):
        bad = ENVELOPE.replace(
            "Write docs/operations/ingest-check.md and confirm the file exists",
            "Review the repository and improve things generally",
        )
        result = self._plan(body=bad)
        self.assertEqual(result["action"], "reject", result)
        self.assertTrue(result["errors"])
        self.assertNotIn("task", result)

    def test_expired_envelope_is_rejected(self):
        result = self._plan(body=ENVELOPE.replace("expires: 2026-12-01", "expires: 2026-10-01"))
        self.assertEqual(result["action"], "reject")
        self.assertTrue(any("past" in error for error in result["errors"]))

    def test_envelope_named_in_a_plain_fence_is_not_a_task(self):
        fenced = "Example:\n```\n" + ENVELOPE + "```\n"
        self.assertEqual(self._plan(body=fenced)["action"], "ignore")


class EventPayloadTests(unittest.TestCase):
    def test_extracts_comment_body_without_interpreting_shell_metacharacters(self):
        body = "TASK v1\nacceptance: $(touch /tmp/not-run); `echo nope`\n"
        payload = {
            "issue": {"number": 464},
            "comment": {
                "id": 6600000001,
                "user": {"login": "ghith93", "type": "User"},
                "body": body,
            },
        }
        extracted, issue_number = ingest._comment_from_event_payload(payload)
        self.assertEqual(issue_number, 464)
        self.assertEqual(extracted["body"], body)
        self.assertEqual(extracted["id"], 6600000001)

    def test_rejects_malformed_event_payloads(self):
        for payload in (None, {}, {"comment": {}, "issue": {}}, {
            "issue": {"number": True},
            "comment": {"id": 1, "user": {"login": "x", "type": "User"}, "body": ""},
        }):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ingest._comment_from_event_payload(payload)

    def test_main_uses_runner_event_payload_instead_of_environment_body(self):
        body = ENVELOPE.replace("ARENA-INGEST-001", "ARENA-EVENT-001")
        payload = {
            "issue": {"number": 464},
            "comment": {
                "id": 6600000002,
                "user": {"login": "ghith93", "type": "User"},
                "body": body,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as event_file:
            json.dump(payload, event_file)
            event_file.flush()
            stdout = io.StringIO()
            with patch.dict(os.environ, {
                "GITHUB_EVENT_PATH": event_file.name,
                "COMMENT_BODY": "not the event content",
                "ALLOWED_ACTOR": "ghith93",
                "ALLOWED_ISSUE": "464",
            }, clear=False), contextlib.redirect_stdout(stdout):
                self.assertEqual(ingest.main(["--today", "2026-11-04"]), 0)
        rendered = json.loads(stdout.getvalue().split("\n::notice", 1)[0])
        self.assertEqual(rendered["task"]["payload"]["envelope"]["id"], "ARENA-EVENT-001")
        self.assertEqual(rendered["comment_id"], 6600000002)


class WorkflowPostureTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_triggers_on_issue_comment_only(self):
        self.assertIn("issue_comment:", self.text)
        self.assertNotIn("workflow_dispatch", self.text)
        self.assertNotIn("pull_request:", self.text)

    def test_job_cannot_write_to_issues(self):
        # The job must not be able to comment: that is what makes looping impossible.
        self.assertIn("contents: read", self.text)
        self.assertNotIn("issues: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertNotIn("actions: write", self.text)

    def test_disabled_by_default_behind_a_repository_variable(self):
        self.assertIn("vars.ISSUE_COMMENT_INGEST_ENABLED == 'true'", self.text)

    def test_actor_and_issue_are_allowlisted_in_the_job_guard(self):
        self.assertIn("github.event.comment.user.login == 'ghith93'", self.text)
        self.assertIn("github.event.issue.number == 464", self.text)
        self.assertIn("github.event.comment.user.type != 'Bot'", self.text)

    def test_actions_are_pinned_to_commit_shas(self):
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("uses:"):
                with self.subTest(action=stripped):
                    self.assertRegex(
                        stripped,
                        r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40}",
                        "third-party actions must be pinned to a full commit SHA",
                    )

    def test_runs_the_ingest_script(self):
        self.assertIn("scripts/github_comment_ingest.py --post", self.text)

    def test_untrusted_comment_body_is_not_interpolated_into_workflow(self):
        self.assertNotIn("${{ github.event.comment.body }}", self.text)
        self.assertNotIn("COMMENT_BODY:", self.text)
        self.assertIn("GITHUB_EVENT_PATH", (ROOT / "scripts" / "github_comment_ingest.py").read_text(encoding="utf-8"))

class ReportingTests(unittest.TestCase):
    """The run must state its own outcome: log download is blocked in some
    environments, so an annotation is the only evidence a reader may get."""

    def test_notice_names_action_and_reason(self):
        result = {"action": "enqueue", "reason": "valid_envelope", "comment_id": 42,
                  "task": {"idempotency_key": "github-comment-42"}}
        notice, summary = ingest.report_lines(result, "")
        self.assertTrue(notice.startswith("::notice title=Issue comment ingest::"))
        self.assertIn("action=enqueue", notice)
        self.assertIn("idempotency_key=github-comment-42", notice)
        self.assertIn("action=enqueue", summary)

    def test_ignored_comment_still_reports(self):
        notice, _ = ingest.report_lines({"action": "ignore", "reason": "no_envelope", "comment_id": 7}, "")
        self.assertIn("action=ignore", notice)
        self.assertIn("reason=no_envelope", notice)

    def test_stage_is_appended_when_present(self):
        notice, _ = ingest.report_lines({"action": "skip", "reason": "bot_author", "comment_id": 9}, "http_200_created_True")
        self.assertIn("stage=http_200_created_True", notice)



if __name__ == "__main__":
    unittest.main()
