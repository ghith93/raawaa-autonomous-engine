"""Safety tests for the one-shot Groq Staging smoke task lifecycle."""
import unittest

from scripts import run_groq_free_inference_smoke as smoke


class FakeStore:
    def __init__(self, rows, task_snapshot, global_snapshot=None, mobile=None,
                 recovery=None, keep_running=False):
        self.rows = rows
        self.task_snapshot = task_snapshot
        self.global_snapshot = global_snapshot or task_snapshot
        self.mobile = mobile or {
            "monetary_ledger": {
                "available": True,
                "daily_spent_usd": 0,
                "monthly_spent_usd": 0,
                "pending_reserved_usd": 0,
                "pending_count": 0,
                "ambiguous_count": 0,
            },
            "model_costs_usd": {"total": 0},
        }
        self.recovery = recovery or {"requeued": 0, "failed": 1}
        self.keep_running = keep_running
        self.recover_calls = []
        self.settlement_calls = []

    def recover_stale(self, worker_lane):
        self.recover_calls.append(worker_lane)
        if not self.keep_running:
            for row in self.rows:
                if row.get("id") == smoke.PREVIOUS_AMBIGUOUS_TASK_ID and row.get("status") == "running":
                    row["status"] = "failed"
        return self.recovery

    def list_tasks(self, limit, worker_lane):
        return self.rows[:limit]

    def _request(self, path, method="GET", payload=None):
        if path == "/v1/mobile/status":
            return self.mobile
        if path == "/v1/monetary-ledger" and method == "POST":
            self.settlement_calls.append(payload)
            self.global_snapshot = {"task_pending_count": 0, "unresolved": []}
            self.task_snapshot = {"task_pending_count": 0, "unresolved": []}
            return {"ok": True, "settled": True, "over_budget": False}
        if path == "/v1/monetary-ledger":
            return {"snapshot": self.global_snapshot}
        if path.startswith("/v1/monetary-ledger?task_id="):
            if smoke.PREVIOUS_AMBIGUOUS_TASK_ID in path:
                return {"snapshot": self.task_snapshot}
            return {"snapshot": {"task_pending_count": 0, "task_spent_nanos": 0}}
        raise AssertionError("Unexpected endpoint in safety test: " + path)


def ambiguous_snapshot():
    row = {
        "task_id": smoke.PREVIOUS_AMBIGUOUS_TASK_ID,
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "status": "ambiguous",
        "estimated_cost_nanos": 0,
        "reservation_id": "d4d9f02e-82d4-4d3a-8f50-6fd5a5a03d41",
    }
    return {"task_pending_count": 1, "unresolved": [row]}


class GroqSmokeRecoveryTests(unittest.TestCase):
    def store(self, rows=None, keep_running=False):
        snapshot = ambiguous_snapshot()
        return FakeStore(
            rows or [{"id": smoke.PREVIOUS_AMBIGUOUS_TASK_ID, "status": "running"}],
            snapshot,
            global_snapshot=snapshot,
            keep_running=keep_running,
        )

    def test_recovers_expired_single_attempt_task_without_replaying_it(self):
        store = self.store()
        result = smoke._verify_previous_ambiguous_task(store)
        self.assertEqual(result["task_status"], "failed")
        self.assertEqual(result["reservation_status"], "settled")
        self.assertEqual(result["actual_cost_nanos"], 0)
        self.assertEqual(store.recover_calls, [smoke.EXECUTION_LANE])
        self.assertEqual(store.settlement_calls, [{
            "action": "settle",
            "reservation_id": "d4d9f02e-82d4-4d3a-8f50-6fd5a5a03d41",
            "actual_cost_nanos": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }])

    def test_refuses_if_previous_ambiguous_task_is_still_running(self):
        store = self.store(keep_running=True)
        with self.assertRaisesRegex(SystemExit, "not terminal"):
            smoke._verify_previous_ambiguous_task(store)

    def test_refuses_if_another_researcher_task_is_pending(self):
        store = self.store(rows=[
            {"id": smoke.PREVIOUS_AMBIGUOUS_TASK_ID, "status": "failed"},
            {"id": "unrelated-task", "status": "pending"},
        ])
        with self.assertRaisesRegex(SystemExit, "pending/running work"):
            smoke._verify_previous_ambiguous_task(store)

    def test_refuses_if_prior_reservation_is_not_the_exact_zero_cost_ambiguity(self):
        snapshot = ambiguous_snapshot()
        snapshot["unresolved"][0]["estimated_cost_nanos"] = 1
        store = self.store()
        store.task_snapshot = snapshot
        with self.assertRaisesRegex(SystemExit, "exact zero-cost record"):
            smoke._verify_previous_ambiguous_task(store)

    def test_final_ledger_requires_all_reservations_settled(self):
        store = self.store(rows=[{"id": smoke.PREVIOUS_AMBIGUOUS_TASK_ID, "status": "failed"}])
        smoke._verify_previous_ambiguous_task(store)
        result = smoke._assert_zero_spend(store, "new-task")
        self.assertEqual(result["pending_reservations"], 0)
        self.assertEqual(result["ambiguous_reservations"], 0)
        self.assertEqual(result["daily_spend_usd"], 0)

    def test_final_ledger_rejects_any_unresolved_reservation(self):
        store = self.store(rows=[{"id": smoke.PREVIOUS_AMBIGUOUS_TASK_ID, "status": "failed"}])
        smoke._verify_previous_ambiguous_task(store)
        store.global_snapshot = {
            "unresolved": [{
                "task_id": "unexpected-task", "provider": "groq",
                "model": "openai/gpt-oss-20b", "status": "ambiguous",
                "estimated_cost_nanos": 0,
            }]
        }
        with self.assertRaisesRegex(SystemExit, "unresolved reservations"):
            smoke._assert_zero_spend(store, "new-task")

    def test_smoke_uses_a_new_idempotency_key_not_the_ambiguous_task_key(self):
        self.assertTrue(smoke.IDEMPOTENCY_KEY.endswith("-v9"))
        self.assertNotEqual(smoke.IDEMPOTENCY_KEY,
                            "groq-free-inference-staging-specialist-smoke-2026-11-06-v8")


if __name__ == "__main__":
    unittest.main()
