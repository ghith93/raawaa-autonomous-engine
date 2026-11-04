"""Tests for the task-envelope validator (scripts/task_envelope.py).

The envelope in ``REAL_ENVELOPE`` is the verbatim task posted by ChatGPT in
issue #464 (comment 6621054977). It is kept as a fixture on purpose: a validator
that rejects the real traffic is worthless, and a validator that accepts
everything is worse.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import task_envelope as te  # noqa: E402

TODAY = date(2026, 11, 4)

REAL_ENVELOPE = """TASK v1
id: ARENA-COLLAB-LOW-LATENCY-001
from: chatgpt
to: arena
scope: research-only
base: e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e
forbidden: repo-write, pr-create, webhook-registration, permission-change, secret-change, workflow-dispatch, deployment, d1-write, paid-inference, external-message, account-creation
cost_cap_usd: 0
expires: 2026-11-18
acceptance: Post one ARENA REPORT in #464 with (1) a capability matrix for ChatGPT, Arena, GitHub, Activepieces and Cloudflare; (2) exact official documentation links and repo paths/line references; (3) a recommended event-driven plus recovery design and exact existing components to reuse; (4) required permissions, failure recovery, loop prevention, monitoring and rollback; (5) explicit verified/unknown classification for whether external events can start or continue ChatGPT and Arena sessions; (6) estimated cost within zero spend.
evidence: issue-comment-6621054977; issue-comment-6621065291
"""

VALID_ENVELOPE = """TASK v1
id: ARENA-DOCS-002
from: owner
to: arena
scope: report-only
base: 4f7104b
forbidden: repo-write, deployment
cost_cap_usd: 0
expires: 2026-12-01
acceptance: Write docs/operations/example.md and confirm the file exists with at least 200 lines
evidence: docs/operations/example.md
"""


def _with(**overrides) -> str:
    """Return VALID_ENVELOPE with individual fields replaced."""
    lines = VALID_ENVELOPE.splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        if not line.strip():
            continue
        key = line.split(":", 1)[0].strip()
        if key in overrides:
            out.append(f"{key}: {overrides[key]}")
        else:
            out.append(line)
    return "\n".join(out)


class ParseTests(unittest.TestCase):
    def test_parses_all_fields(self):
        envelope = te.parse_envelope(REAL_ENVELOPE)
        self.assertEqual(envelope["id"], "ARENA-COLLAB-LOW-LATENCY-001")
        self.assertEqual(envelope["cost_cap_usd"], "0")
        self.assertEqual(len(envelope), len(te.REQUIRED_FIELDS))

    def test_ignores_prose_before_and_after(self):
        text = "Here is the task.\n\n" + VALID_ENVELOPE + "\nThen some closing prose."
        envelope = te.parse_envelope(text)
        self.assertEqual(envelope["id"], "ARENA-DOCS-002")

    def test_rejects_text_without_marker(self):
        with self.assertRaises(te.EnvelopeError):
            te.parse_envelope("id: ARENA-DOCS-002\nfrom: owner")

    def test_rejects_marker_not_on_own_line(self):
        with self.assertRaises(te.EnvelopeError):
            te.parse_envelope("TASK v1 id: ARENA-DOCS-002")

    def test_empty_block_raises(self):
        with self.assertRaises(te.EnvelopeError):
            te.parse_envelope("TASK v1\n\nnothing here")


class ExampleFenceTests(unittest.TestCase):
    """Regression tests for the defect found on the first live run of #464:
    a protocol document that *shows* an envelope was being read as a task."""

    def test_plain_fence_is_treated_as_example(self):
        text = "Here is how a task looks:\n```\n" + VALID_ENVELOPE + "\n```\nThat is all."
        with self.assertRaises(te.EnvelopeError):
            te.parse_envelope(text)

    def test_task_fence_is_live(self):
        text = "```task\n" + VALID_ENVELOPE + "\n```"
        self.assertEqual(te.parse_envelope(text)["id"], "ARENA-DOCS-002")

    def test_unfenced_envelope_is_live(self):
        self.assertEqual(te.parse_envelope(VALID_ENVELOPE)["id"], "ARENA-DOCS-002")

    def test_state_markers_in_plain_fence_ignored(self):
        body = "How it looks:\n```\n[TASK ARENA-DOCS-002] CLAIMED hash=abc123\n```"
        self.assertEqual(te.parse_state_markers(body), [])

    def test_state_marker_unfenced_detected(self):
        body = "[TASK ARENA-DOCS-002] CLAIMED hash=abc123"
        self.assertEqual(len(te.parse_state_markers(body)), 1)

    def test_live_block_after_closed_fence_still_parsed(self):
        text = "```bash\necho example\n```\n" + VALID_ENVELOPE
        self.assertEqual(te.parse_envelope(text)["id"], "ARENA-DOCS-002")


class ValidateTests(unittest.TestCase):
    def test_real_chatgpt_envelope_is_valid(self):
        errors, warnings = te.validate(te.parse_envelope(REAL_ENVELOPE), today=TODAY)
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [])

    def test_valid_envelope_passes(self):
        errors, _ = te.validate(te.parse_envelope(VALID_ENVELOPE), today=TODAY)
        self.assertEqual(errors, [])

    def test_missing_field_is_reported(self):
        text = VALID_ENVELOPE.replace("cost_cap_usd: 0\n", "")
        errors, _ = te.validate(te.parse_envelope(text), today=TODAY)
        self.assertTrue(any("cost_cap_usd" in e for e in errors), errors)

    def test_bad_id_shape_rejected(self):
        errors, _ = te.validate(te.parse_envelope(_with(id="docs-task")), today=TODAY)
        self.assertTrue(any("does not match" in e for e in errors), errors)

    def test_sender_equals_receiver_rejected(self):
        errors, _ = te.validate(te.parse_envelope(_with(to="owner")), today=TODAY)
        self.assertTrue(any("must differ" in e for e in errors), errors)

    def test_unknown_scope_rejected(self):
        errors, _ = te.validate(te.parse_envelope(_with(scope="whatever")), today=TODAY)
        self.assertTrue(any("scope" in e for e in errors), errors)

    def test_forbidden_cannot_be_none(self):
        errors, _ = te.validate(te.parse_envelope(_with(forbidden="none")), today=TODAY)
        self.assertTrue(any("may not be 'none'" in e for e in errors), errors)

    def test_forbidden_outside_vocabulary_warns_only(self):
        errors, warnings = te.validate(te.parse_envelope(_with(forbidden="dns-change")), today=TODAY)
        self.assertEqual(errors, [])
        self.assertTrue(any("dns-change" in w for w in warnings), warnings)

    def test_negative_cost_cap_rejected(self):
        errors, _ = te.validate(te.parse_envelope(_with(cost_cap_usd="-1")), today=TODAY)
        self.assertTrue(any("negative" in e for e in errors), errors)

    def test_past_expiry_rejected(self):
        errors, _ = te.validate(te.parse_envelope(_with(expires="2026-10-01")), today=TODAY)
        self.assertTrue(any("past" in e for e in errors), errors)

    def test_vague_acceptance_rejected(self):
        acceptance = "Review the repository and report anything that looks risky"
        errors, _ = te.validate(te.parse_envelope(_with(acceptance=acceptance)), today=TODAY)
        self.assertTrue(any("not falsifiable" in e for e in errors), errors)

    def test_acceptance_needs_measurable_marker(self):
        acceptance = "Improve the overall quality of the documentation and make it clearer for readers everywhere"
        errors, _ = te.validate(te.parse_envelope(_with(acceptance=acceptance)), today=TODAY)
        self.assertTrue(any("not falsifiable" in e for e in errors), errors)

    def test_acceptance_with_command_passes(self):
        acceptance = "Run `python3 -m unittest` and it exits with 0"
        errors, _ = te.validate(te.parse_envelope(_with(acceptance=acceptance)), today=TODAY)
        self.assertEqual(errors, [])

    def test_evidence_must_be_openable(self):
        errors, _ = te.validate(te.parse_envelope(_with(evidence="somewhere in chat")), today=TODAY)
        self.assertTrue(any("openable artefact" in e for e in errors), errors)

    def test_evidence_list_allowed(self):
        errors, _ = te.validate(
            te.parse_envelope(_with(evidence="issue-comment-1; pr-466")), today=TODAY
        )
        self.assertEqual(errors, [])


class FingerprintTests(unittest.TestCase):
    def test_hash_is_stable_across_formatting(self):
        compact = VALID_ENVELOPE
        padded = VALID_ENVELOPE.replace("scope: report-only", "scope:   report-only  ")
        self.assertEqual(
            te.envelope_hash(te.parse_envelope(compact)),
            te.envelope_hash(te.parse_envelope(padded)),
        )

    def test_hash_is_independent_of_field_order(self):
        lines = [line for line in VALID_ENVELOPE.splitlines() if line.strip()]
        header, fields = lines[0], lines[1:]
        reordered = "\n".join([header] + list(reversed(fields)))
        self.assertEqual(
            te.envelope_hash(te.parse_envelope(VALID_ENVELOPE)),
            te.envelope_hash(te.parse_envelope(reordered)),
        )

    def test_hash_changes_when_content_changes(self):
        changed = _with(acceptance="Run `pytest` and it exits with 0")
        self.assertNotEqual(
            te.envelope_hash(te.parse_envelope(VALID_ENVELOPE)),
            te.envelope_hash(te.parse_envelope(changed)),
        )

    def test_short_hash_is_prefix_of_full(self):
        envelope = te.parse_envelope(VALID_ENVELOPE)
        self.assertTrue(te.envelope_hash(envelope).startswith(te.short_hash(envelope)))


class LedgerTests(unittest.TestCase):
    def _comment(self, comment_id, created_at, body):
        return {"id": comment_id, "created_at": created_at, "body": body}

    def test_happy_path_reaches_verified(self):
        fingerprint = te.short_hash(te.parse_envelope(VALID_ENVELOPE))
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", f"[TASK ARENA-DOCS-002] CLAIMED hash={fingerprint}"),
            self._comment(3, "2026-11-04T10:06:00Z", "[TASK ARENA-DOCS-002] IN_PROGRESS"),
            self._comment(
                4,
                "2026-11-04T11:00:00Z",
                "[TASK ARENA-DOCS-002] DELIVERED `python3 -m unittest` exit 0 (562 tests)",
            ),
            self._comment(5, "2026-11-04T11:30:00Z", "[TASK ARENA-DOCS-002] VERIFIED"),
        ]
        ledger = te.build_ledger(comments)
        self.assertEqual(ledger["ARENA-DOCS-002"]["current"], "VERIFIED")
        errors, _ = te.validate_ledger(ledger, today=TODAY)
        self.assertEqual(errors, [], errors)

    def test_claim_hash_mismatch_detected(self):
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", "[TASK ARENA-DOCS-002] CLAIMED hash=deadbeef"),
        ]
        errors, _ = te.validate_ledger(te.build_ledger(comments), today=TODAY)
        self.assertTrue(any("does not match envelope fingerprint" in e for e in errors), errors)

    def test_claim_without_hash_detected(self):
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", "[TASK ARENA-DOCS-002] CLAIMED"),
        ]
        errors, _ = te.validate_ledger(te.build_ledger(comments), today=TODAY)
        self.assertTrue(any("CLAIMED without hash" in e for e in errors), errors)

    def test_illegal_transition_detected(self):
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", "[TASK ARENA-DOCS-002] VERIFIED"),
        ]
        errors, _ = te.validate_ledger(te.build_ledger(comments), today=TODAY)
        self.assertTrue(any("illegal transition POSTED -> VERIFIED" in e for e in errors), errors)

    def test_delivery_without_proof_detected(self):
        fingerprint = te.short_hash(te.parse_envelope(VALID_ENVELOPE))
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", f"[TASK ARENA-DOCS-002] CLAIMED hash={fingerprint}"),
            self._comment(3, "2026-11-04T11:00:00Z", "[TASK ARENA-DOCS-002] DELIVERED it is done"),
        ]
        errors, _ = te.validate_ledger(te.build_ledger(comments), today=TODAY)
        self.assertTrue(any("without a backticked command" in e for e in errors), errors)

    def test_duplicate_id_with_different_content_detected(self):
        amended = _with(acceptance="Run `pytest` and it exits with 0")
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", amended),
        ]
        warnings = te.validate_ledger(te.build_ledger(comments), today=TODAY)[1]
        errors = te.validate_ledger(te.build_ledger(comments), today=TODAY)[0]
        self.assertTrue(any("different content" in e for e in errors), (errors, warnings))

    def test_state_without_envelope_detected(self):
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", "[TASK ARENA-GHOST-001] CLAIMED hash=abc123"),
        ]
        errors, _ = te.validate_ledger(te.build_ledger(comments), today=TODAY)
        self.assertTrue(any("no task envelope" in e for e in errors), errors)


class PreflightTests(unittest.TestCase):
    """The channel protocol asks each agent to read everything newer than the
    last comment it acknowledged. These tests keep that watermark a number
    rather than a memory."""

    def _comment(self, comment_id, created_at, body):
        return {"id": comment_id, "created_at": created_at, "body": body}

    def test_watermark_is_highest_comment_id(self):
        comments = [
            self._comment(10, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(20, "2026-11-04T10:05:00Z", "unrelated chatter"),
            self._comment(15, "2026-11-04T10:02:00Z", "later by date, earlier id"),
        ]
        report = te.preflight(comments, today=TODAY)
        self.assertEqual(report["watermark"], 20)
        self.assertEqual(report["comments"], 3)

    def test_posted_task_awaits_the_receiver(self):
        comments = [self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE)]
        report = te.preflight(comments, today=TODAY)
        self.assertEqual([t["id"] for t in report["awaiting_arena"]], ["ARENA-DOCS-002"])
        self.assertEqual(report["tasks"][0]["actor"], "arena")

    def test_delivered_task_awaits_the_sender(self):
        fingerprint = te.short_hash(te.parse_envelope(VALID_ENVELOPE))
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", f"[TASK ARENA-DOCS-002] CLAIMED hash={fingerprint}"),
            self._comment(3, "2026-11-04T11:00:00Z", "[TASK ARENA-DOCS-002] DELIVERED `pytest` ok"),
        ]
        report = te.preflight(comments, today=TODAY)
        self.assertEqual(report["awaiting_arena"], [])
        self.assertEqual(report["tasks"][0]["state"], "DELIVERED")
        self.assertEqual(report["tasks"][0]["actor"], "owner")

    def test_verified_task_has_no_next_actor(self):
        fingerprint = te.short_hash(te.parse_envelope(VALID_ENVELOPE))
        comments = [
            self._comment(1, "2026-11-04T10:00:00Z", VALID_ENVELOPE),
            self._comment(2, "2026-11-04T10:05:00Z", f"[TASK ARENA-DOCS-002] CLAIMED hash={fingerprint}"),
            self._comment(3, "2026-11-04T11:00:00Z", "[TASK ARENA-DOCS-002] DELIVERED `pytest` ok"),
            self._comment(4, "2026-11-04T12:00:00Z", "[TASK ARENA-DOCS-002] VERIFIED"),
        ]
        report = te.preflight(comments, today=TODAY)
        self.assertIsNone(report["tasks"][0]["actor"])


class CliTests(unittest.TestCase):
    def test_lint_exit_zero_for_valid_envelope(self):
        result = subprocess.run(
            [sys.executable, "scripts/task_envelope.py", "lint", "-", "--today", "2026-11-04"],
            input=VALID_ENVELOPE,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK ARENA-DOCS-002", result.stdout)

    def test_lint_exit_one_for_vague_acceptance(self):
        text = _with(acceptance="Review the repository and improve things")
        result = subprocess.run(
            [sys.executable, "scripts/task_envelope.py", "lint", "-", "--today", "2026-11-04"],
            input=text,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not falsifiable", result.stderr)

    def test_hash_subcommand_prints_sha256(self):
        result = subprocess.run(
            [sys.executable, "scripts/task_envelope.py", "hash", "-"],
            input=VALID_ENVELOPE,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{64}$")

    def test_ledger_subcommand_reads_snapshot(self):
        snapshot = [
            {"id": 1, "created_at": "2026-11-04T10:00:00Z", "body": VALID_ENVELOPE},
        ]
        result = subprocess.run(
            [sys.executable, "scripts/task_envelope.py", "ledger", "-", "--json", "--today", "2026-11-04"],
            input=json.dumps(snapshot),
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("ARENA-DOCS-002", payload["tasks"])


if __name__ == "__main__":
    unittest.main()
