import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from scripts.run_staging_d1_claim_contention import (
    STAGING_API_URL,
    StagingContentionProofError,
    run_proof,
    validate_environment,
)


class FakeStore:
    def __init__(self, *_args, **_kwargs):
        self.lock = Lock()
        self.task = None
        self.claimed = False
        self.audit = []

    def enqueue(self, task_type, title, payload, idempotency_key, max_attempts, priority):
        import json
        self.task = {
            "id": "staging-task-1",
            "task_type": task_type,
            "title": title,
            "payload_json": json.dumps(payload),
            "idempotency_key": idempotency_key,
            "status": "pending",
            "attempts": 0,
        }
        self.audit.append({"task_id": "staging-task-1", "event": "task_enqueued"})
        return dict(self.task)

    def claim_supervised_probe(self, task_id):
        with self.lock:
            if task_id != self.task["id"] or self.claimed or self.task["status"] != "pending":
                return None
            self.claimed = True
            self.task.update(status="running", attempts=1, execution_mode="dry_run")
            self.audit.append({"task_id": task_id, "event": "task_claimed"})
            return {**self.task, "lease_token": "lease-test"}

    def list_tasks(self, _limit):
        return [dict(self.task)]

    def finish(self, task_id, status, result=None, error=None, lease_token=None, execution_mode=None):
        assert task_id == self.task["id"]
        assert lease_token == "lease-test"
        self.task.update(status=status, result_json=result, error=error,
                         execution_mode=execution_mode or "dry_run")
        self.audit.append({"task_id": task_id, "event": "task_" + status})

    def audit_events(self, limit=100, task_id=None):
        return [row for row in self.audit if row["task_id"] == task_id][:limit]


class StagingContentionProofTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "RAAWAA_ENGINE_API_URL": STAGING_API_URL,
            "RAAWAA_ENGINE_API_TOKEN": "x" * 48,
            "RAAWAA_ENGINE_LIVE_INFERENCE": "disabled",
        }

    def test_environment_rejects_non_staging_target(self):
        bad = dict(self.env, RAAWAA_ENGINE_API_URL="https://production.example")
        with self.assertRaises(StagingContentionProofError):
            validate_environment(bad)

    def test_environment_rejects_provider_credentials(self):
        bad = dict(self.env, GROQ_API_KEY="must-not-be-present")
        with self.assertRaises(StagingContentionProofError):
            validate_environment(bad)

    def test_environment_rejects_live_inference(self):
        bad = dict(self.env, RAAWAA_ENGINE_LIVE_INFERENCE="enabled")
        with self.assertRaises(StagingContentionProofError):
            validate_environment(bad)

    def test_two_concurrent_claims_have_one_winner_and_durable_completion(self):
        result = run_proof(self.env, store_factory=FakeStore, executor_factory=ThreadPoolExecutor)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["claim_winners"], 1)
        self.assertEqual(result["claim_losers"], 1)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["audit_claim_events"], 1)
        self.assertTrue(result["duplicate_claim_blocked"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["spend_usd"], 0)


if __name__ == "__main__":
    unittest.main()
