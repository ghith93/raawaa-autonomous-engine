"""The ingest handler must record a comment task without thinking.

The property under test is narrow and deliberate: a task admitted from a GitHub
comment is completed with a stored result and **no provider call, no contract and
no agent**. If any of those ever happens, an untrusted comment has reached a
model, which is the one outcome the whole receiving edge was designed to prevent.
"""

from __future__ import annotations

import unittest

from engine.worker import INGEST_TASK_TYPE, SAFE_TASKS, run_once

VALID_ENVELOPE = {
    "id": "ARENA-INGEST-TEST-002",
    "from": "owner",
    "to": "arena",
    "scope": "research-only",
    "base": "3fcfe21",
    "forbidden": "repo-write, deployment",
    "cost_cap_usd": "0",
    "expires": "2099-01-01",
    "acceptance": "Confirm in #464 that the ingest notice reports the enqueue result",
    "evidence": "comment-in-464",
}


class FakeStore:
    """Minimal TaskStore surface used by run_once for the ingest path."""

    def __init__(self, task):
        self.task = task
        self.finished: dict | None = None

    def recover_stale(self):
        return {}

    def claim_next(self, execution_mode=None):  # noqa: ARG002 - interface parity
        return self.task

    def finish(self, task_id, status, result=None, error=None, lease_token=None, execution_mode=None):
        self.finished = {
            "task_id": task_id,
            "status": status,
            "result": result,
            "error": error,
            "lease_token": lease_token,
        }


def ingest_task(envelope=None, **overrides):
    payload = {
        "source": "github_issue_comment",
        "issue_number": 464,
        "comment_id": 6600000001,
        "envelope": envelope if envelope is not None else VALID_ENVELOPE,
        "envelope_fingerprint": "f" * 64,
    }
    task = {
        "id": "task-1",
        "task_type": INGEST_TASK_TYPE,
        "payload": payload,
        "lease_token": "lease-1",
    }
    task.update(overrides)
    return task


class IngestHandlerTests(unittest.TestCase):
    def test_valid_envelope_succeeds_without_inference(self):
        store = FakeStore(ingest_task())
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "succeeded", outcome)
        self.assertEqual(outcome["reason"], "issue_comment_recorded")
        self.assertEqual(store.finished["status"], "succeeded")
        self.assertEqual(store.finished["result"]["inference"], "none")
        self.assertEqual(store.finished["result"]["envelope_id"], "ARENA-INGEST-TEST-002")
        self.assertEqual(store.finished["result"]["comment_id"], 6600000001)

    def test_valid_envelope_does_not_need_a_live_gateway(self):
        # A provider call would require the gateway; passing none proves the path
        # never tries to reach one.
        store = FakeStore(ingest_task())
        outcome = run_once(store, live_gateway=None, environ={})
        self.assertEqual(outcome["status"], "succeeded")

    def test_missing_envelope_is_blocked(self):
        store = FakeStore(ingest_task(envelope={}))
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "missing_envelope")
        self.assertEqual(store.finished["status"], "blocked")

    def test_invalid_envelope_is_blocked(self):
        broken = dict(VALID_ENVELOPE, acceptance="Review things and improve them")
        store = FakeStore(ingest_task(envelope=broken))
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "invalid_ingested_envelope")
        self.assertIn("Invalid ingested envelope", store.finished["error"])

    def test_expired_envelope_is_blocked_at_claim_time(self):
        expired = dict(VALID_ENVELOPE, expires="2020-01-01")
        store = FakeStore(ingest_task(envelope=expired))
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "blocked")
        self.assertIn("past", store.finished["error"])

    def test_payload_json_string_is_accepted(self):
        import json

        task = ingest_task()
        payload_json = json.dumps(task.pop("payload"))
        store = FakeStore(dict(task, payload_json=payload_json))
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "succeeded", outcome)

    def test_unknown_task_type_is_still_blocked(self):
        store = FakeStore({"id": "t2", "task_type": "weird_type", "payload": {}, "lease_token": "l"})
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "unsupported_task_type")

    def test_ingest_type_is_not_in_the_model_allowlist(self):
        # The allowlist for inference-capable work must keep its original four
        # entries; admitting comments must not widen it.
        self.assertNotIn(INGEST_TASK_TYPE, SAFE_TASKS)
        self.assertEqual(
            SAFE_TASKS, {"research_brief", "code_review", "content_draft", "system_audit"}
        )


if __name__ == "__main__":
    unittest.main()
