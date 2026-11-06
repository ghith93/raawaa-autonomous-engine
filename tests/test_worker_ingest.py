"""The ingest handler must record a comment task without thinking, and without
trusting the row it was handed.

Three properties are under test, and each one failed in an earlier iteration:

1. **No inference.** The path completes with `inference: none` and
   `execution_authorized: False`, and never builds a contract or an agent.
2. **No blind trust in the queue row.** Provenance (source, issue, author,
   comment id) and integrity (a recomputed envelope fingerprint) are re-checked
   at claim time, because a durable row can be edited between admission and claim.
3. **No spending authority.** A `cost_cap_usd` above zero is refused here, even
   though the general envelope validator only rejects negatives — a valid schema
   is not proof of authorisation.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from engine.worker import INGEST_TASK_TYPE, SAFE_TASKS, run_once
from scripts.task_envelope import envelope_hash

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

COMMENT_ID = 6600000001


class FakeStore:
    def __init__(self, task):
        self.task = task
        self.finished: dict | None = None

    def recover_stale(self):
        return {}

    def claim_next(self, execution_mode=None, worker_lane="general"):  # noqa: ARG002 - interface parity
        return self.task

    def finish(self, task_id, status, result=None, error=None, lease_token=None, execution_mode=None, worker_lane="general"):
        self.finished = {"task_id": task_id, "status": status, "result": result,
                         "error": error, "lease_token": lease_token}


def ingest_task(envelope=None, payload_overrides=None, **task_overrides):
    env = VALID_ENVELOPE if envelope is None else envelope
    payload = {
        "source": "github_issue_comment",
        "issue_number": 464,
        "comment_id": COMMENT_ID,
        "comment_author": "ghith93",
        "envelope": env,
        "envelope_fingerprint": envelope_hash(env) if env else "",
    }
    if payload_overrides:
        payload.update(payload_overrides)
    task = {
        "id": "task-1",
        "task_type": INGEST_TASK_TYPE,
        "payload": payload,
        "lease_token": "lease-1",
        "idempotency_key": f"github-comment-{COMMENT_ID}",
    }
    task.update(task_overrides)
    return task


class InferenceBoundaryTests(unittest.TestCase):
    def test_valid_envelope_succeeds_without_inference(self):
        store = FakeStore(ingest_task())
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "succeeded", outcome)
        result = store.finished["result"]
        self.assertEqual(result["inference"], "none")
        self.assertIs(result["execution_authorized"], False)
        self.assertEqual(result["meaning"], "validated_and_recorded_only")

    def test_no_contract_or_agent_is_built(self):
        store = FakeStore(ingest_task())
        with mock.patch("engine.worker.build_task_contract") as contract, \
             mock.patch("engine.worker.load_agent_roster") as roster:
            run_once(store, environ={})
        contract.assert_not_called()
        roster.assert_not_called()

    def test_no_provider_gateway_is_required(self):
        store = FakeStore(ingest_task())
        self.assertEqual(run_once(store, live_gateway=None, environ={})["status"], "succeeded")

    def test_ingest_type_is_not_in_the_model_allowlist(self):
        self.assertNotIn(INGEST_TASK_TYPE, SAFE_TASKS)
        self.assertEqual(SAFE_TASKS,
                         {"research_brief", "code_review", "content_draft", "system_audit"})


class IntegrityTests(unittest.TestCase):
    def test_fingerprint_mismatch_is_blocked(self):
        store = FakeStore(ingest_task(payload_overrides={"envelope_fingerprint": "0" * 64}))
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["reason"], "envelope_fingerprint_mismatch")
        self.assertEqual(store.finished["status"], "blocked")

    def test_missing_fingerprint_is_blocked(self):
        store = FakeStore(ingest_task(payload_overrides={"envelope_fingerprint": ""}))
        self.assertEqual(run_once(store, environ={})["reason"], "missing_envelope_fingerprint")

    def test_edited_envelope_is_blocked(self):
        task = ingest_task()
        task["payload"]["envelope"] = dict(VALID_ENVELOPE, acceptance="Run `pytest` and it exits with 0")
        self.assertEqual(run_once(FakeStore(task), environ={})["reason"],
                         "envelope_fingerprint_mismatch")

    def test_payload_json_string_is_accepted(self):
        task = ingest_task()
        payload_json = json.dumps(task.pop("payload"))
        self.assertEqual(run_once(FakeStore(dict(task, payload_json=payload_json)), environ={})["status"],
                         "succeeded")


class ProvenanceTests(unittest.TestCase):
    def test_wrong_source_is_blocked(self):
        store = FakeStore(ingest_task(payload_overrides={"source": "manual"}))
        self.assertEqual(run_once(store, environ={})["reason"], "ingest_provenance_rejected")
        self.assertIn("unexpected source", store.finished["error"])

    def test_wrong_issue_is_blocked(self):
        store = FakeStore(ingest_task(payload_overrides={"issue_number": 999}))
        self.assertEqual(run_once(store, environ={})["reason"], "ingest_provenance_rejected")

    def test_unlisted_author_is_blocked(self):
        store = FakeStore(ingest_task(payload_overrides={"comment_author": "somebody"}))
        self.assertEqual(run_once(store, environ={})["reason"], "ingest_provenance_rejected")

    def test_idempotency_key_mismatch_is_blocked(self):
        store = FakeStore(ingest_task(idempotency_key="github-comment-999"))
        self.assertEqual(run_once(store, environ={})["reason"], "ingest_provenance_rejected")


class PolicyTests(unittest.TestCase):
    def test_non_zero_cost_cap_is_blocked(self):
        funded = dict(VALID_ENVELOPE, cost_cap_usd="5")
        store = FakeStore(ingest_task(envelope=funded))
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["reason"], "ingest_cost_cap_exceeded")
        self.assertIn("cost cap", store.finished["error"])

    def test_non_numeric_cost_cap_is_blocked(self):
        # Caught earlier by the validator: a cap that is not a number is a
        # malformed envelope before it is a policy breach.
        store = FakeStore(ingest_task(envelope=dict(VALID_ENVELOPE, cost_cap_usd="lots")))
        self.assertEqual(run_once(store, environ={})["reason"], "invalid_ingested_envelope")

    def test_expired_envelope_is_blocked(self):
        store = FakeStore(ingest_task(envelope=dict(VALID_ENVELOPE, expires="2020-01-01")))
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["reason"], "invalid_ingested_envelope")
        self.assertIn("past", store.finished["error"])

    def test_unfalsifiable_acceptance_is_blocked(self):
        broken = dict(VALID_ENVELOPE, acceptance="Review things and improve them")
        store = FakeStore(ingest_task(envelope=broken))
        self.assertEqual(run_once(store, environ={})["reason"],
                         "invalid_ingested_envelope")

    def test_missing_envelope_is_blocked(self):
        store = FakeStore(ingest_task(envelope={} | {}, payload_overrides={"envelope": {}}))
        self.assertEqual(run_once(store, environ={})["reason"], "missing_envelope")

    def test_unknown_task_type_is_still_blocked(self):
        store = FakeStore({"id": "t2", "task_type": "weird_type", "payload": {}, "lease_token": "l"})
        outcome = run_once(store, environ={})
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "unsupported_task_type")


if __name__ == "__main__":
    unittest.main()
