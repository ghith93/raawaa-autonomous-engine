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
                        "idempotency_key": "staging-d1-contention:12345:1",
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
                        "idempotency_key": "staging-d1-contention:12346:1",
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
                        "idempotency_key": "staging-d1-contention:12347:1",
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

    def test_over_hundred_historical_succeeded_tasks_do_not_trip_pending_cleanup_ceiling(self):
        class LargeHistoryStore(FakeStore):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.history = [
                    {"id": f"hist-{i}", "status": "succeeded", "task_type": "supervised_probe"}
                    for i in range(150)
                ]
                self.queries = []

            def list_tasks(self, limit=50, *, status=None, task_id=None):
                self.queries.append({"limit": limit, "status": status, "task_id": task_id})
                rows = list(self.history) + ([dict(self.task)] if self.task else [])
                if status is not None:
                    rows = [r for r in rows if r.get("status") == status]
                if task_id is not None:
                    rows = [r for r in rows if r.get("id") == task_id]
                return rows[:limit]

        created_stores = []

        def factory(*args, **kwargs):
            s = LargeHistoryStore(*args, **kwargs)
            created_stores.append(s)
            return s

        result = run_proof(self.env, store_factory=factory, executor_factory=ThreadPoolExecutor)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(created_stores[0].queries[0]["status"], "pending")
        self.assertEqual(created_stores[0].queries[1]["task_id"], "staging-task-1")

    def test_winning_lease_is_retired_when_sibling_concurrent_claim_raises(self):
        class FlakyFirstClaimStore(FakeStore):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.calls = 0
                self.finish_calls = []

            def claim_supervised_probe(self, task_id):
                with self.lock:
                    self.calls += 1
                    if self.calls == 1:
                        raise RuntimeError("transient_http_503")
                return super().claim_supervised_probe(task_id)

            def finish(self, task_id, status, result=None, error=None, lease_token=None, execution_mode=None):
                self.finish_calls.append((task_id, status, lease_token))
                return super().finish(
                    task_id, status, result=result, error=error,
                    lease_token=lease_token, execution_mode=execution_mode,
                )

        created = []

        def factory(*args, **kwargs):
            s = FlakyFirstClaimStore(*args, **kwargs)
            created.append(s)
            return s

        class SequentialExecutor:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def submit(self, fn, *args):
                class ImmediateFuture:
                    def __init__(self):
                        self._exc = None
                        self._res = None
                        try:
                            self._res = fn(*args)
                        except BaseException as exc:  # noqa: BLE001
                            self._exc = exc

                    def result(self):
                        if self._exc is not None:
                            raise self._exc
                        return self._res

                return ImmediateFuture()

        with self.assertRaises(StagingContentionProofError):
            run_proof(self.env, store_factory=factory, executor_factory=SequentialExecutor)
        self.assertEqual(created[0].finish_calls, [("staging-task-1", "blocked", "lease-test")])
        self.assertEqual(created[0].task["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
