import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from scripts.run_staging_d1_claim_contention import (
    STAGING_API_URL,
    StagingContentionProofError,
    run_proof,
    retire_stale_pending_proof_tasks,
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

    def list_tasks(self, _limit):
        return [dict(self.task)] if self.task else []

    def claim_supervised_probe(self, task_id):
        with self.lock:
            if task_id != self.task["id"] or self.claimed or self.task["status"] != "pending":
                return None
            self.claimed = True
            self.task.update(status="running", attempts=1, execution_mode="dry_run")
            self.audit.append({"task_id": task_id, "event": "task_claimed"})
            return {**self.task, "lease_token": "lease-test"}

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

    def test_only_exact_stale_pending_contention_proofs_are_retired(self):
        class CleanupStore:
            def __init__(self):
                self.rows = [
                    {
                        "id": "stale-proof",
                        "task_type": "supervised_probe",
                        "title": "Remote Staging D1 concurrent claim contention proof",
                        "idempotency_key": "staging-d1-contention:old-run:1",
                        "payload_json": '{"operation": "python_platform_probe"}',
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 1,
                        "execution_mode": "not_started",
                        "priority": 100,
                        "dependencies_json": "[]",
                    },
                    {
                        "id": "lookalike",
                        "task_type": "supervised_probe",
                        "title": "Remote Staging D1 concurrent claim contention proof",
                        "idempotency_key": "staging-d1-contention:lookalike:1",
                        "payload_json": '{"operation": "different_operation"}',
                        "status": "pending",
                        "attempts": 0,
                        "max_attempts": 1,
                        "execution_mode": "not_started",
                        "priority": 100,
                        "dependencies_json": "[]",
                    },
                    {
                        "id": "lookalike-attempted",
                        "task_type": "supervised_probe",
                        "title": "Remote Staging D1 concurrent claim contention proof",
                        "idempotency_key": "staging-d1-contention:attempted:1",
                        "payload_json": '{"operation": "python_platform_probe"}',
                        "status": "pending",
                        "attempts": 1,
                        "max_attempts": 1,
                        "execution_mode": "not_started",
                        "priority": 100,
                        "dependencies_json": "[]",
                    },
                    {
                        "id": "unrelated",
                        "task_type": "research_brief",
                        "title": "Unrelated Staging task",
                        "idempotency_key": "user-task-key",
                        "status": "pending",
                    },
                ]
                self.finished = []

            def list_tasks(self, _limit):
                return [dict(row) for row in self.rows]

            def claim_supervised_probe(self, task_id):
                row = next(row for row in self.rows if row["id"] == task_id)
                row["status"] = "running"
                return {"id": task_id, "lease_token": "lease-stale"}

            def finish(self, task_id, status, **kwargs):
                self.finished.append((task_id, status, kwargs))

        store = CleanupStore()
        self.assertEqual(retire_stale_pending_proof_tasks(store), 1)
        self.assertEqual(len(store.finished), 1)
        self.assertEqual(store.finished[0][0:2], ("stale-proof", "blocked"))
        self.assertEqual(
            [row["id"] for row in store.rows if row["status"] == "pending"],
            ["lookalike", "lookalike-attempted", "unrelated"],
        )

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
