import unittest
from datetime import datetime, timezone, timedelta

from engine.executive_intelligence import build_executive_snapshot


class ExecutiveIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def test_ranks_tasks_deterministically_and_explains_scores(self):
        tasks = [
            {"id": "a", "task_type": "content_draft", "title": "Draft", "status": "pending",
             "created_at": (self.now - timedelta(days=2)).isoformat(), "attempts": 0},
            {"id": "b", "task_type": "system_audit", "title": "Audit", "status": "pending",
             "created_at": self.now.isoformat(), "attempts": 0},
            {"id": "c", "task_type": "code_review", "title": "Review", "status": "pending",
             "created_at": self.now.isoformat(), "attempts": 0},
        ]
        result = build_executive_snapshot(tasks, now=self.now)
        ranked = result["next_best_tasks"]
        self.assertEqual([task["task_id"] for task in ranked], ["b", "c", "a"])
        self.assertTrue(all(task["rationale"] for task in ranked))
        self.assertEqual(result["mode"], "read_only_decision_support")

    def test_failed_blocked_and_expired_running_are_escalated(self):
        tasks = [
            {"id": "failed", "title": "Failed", "status": "failed", "task_type": "research_brief",
             "attempts": 2, "error": "test error"},
            {"id": "blocked", "title": "Blocked", "status": "blocked", "task_type": "shell"},
            {"id": "expired", "title": "Expired", "status": "running", "lease_expires_at": "2026-11-03T11:00:00+00:00"},
        ]
        result = build_executive_snapshot(tasks, now=self.now)
        self.assertEqual(result["portfolio_health"]["needs_attention"], 2)
        self.assertEqual(result["portfolio_health"]["expired_or_unleased_running"], 1)
        self.assertEqual(result["recommendations"][0]["action"], "inspect_expired_leases")
        self.assertEqual(result["side_effects"]["tasks_claimed"], 0)

    def test_unknown_task_type_is_visible_not_discarded(self):
        task = {"id": "unknown", "task_type": "new_kind", "title": "New", "status": "pending",
                "created_at": self.now.isoformat(), "attempts": 0}
        result = build_executive_snapshot([task], now=self.now)
        self.assertEqual(result["next_best_tasks"][0]["task_id"], "unknown")
        self.assertIn("Unclassified", result["next_best_tasks"][0]["rationale"])

    def test_snapshot_is_read_only_and_limit_is_validated(self):
        task = {"id": "a", "task_type": "research_brief", "title": "Research", "status": "pending",
                "created_at": self.now.isoformat(), "attempts": 0}
        original = dict(task)
        result = build_executive_snapshot([task], now=self.now, limit=1)
        self.assertEqual(task, original)
        self.assertEqual(result["side_effects"], {
            "tasks_created": 0, "tasks_claimed": 0, "queue_reordered": False,
            "network_calls": 0, "model_calls": 0,
        })
        with self.assertRaises(ValueError):
            build_executive_snapshot([task], now=self.now, limit=0)


if __name__ == "__main__":
    unittest.main()
