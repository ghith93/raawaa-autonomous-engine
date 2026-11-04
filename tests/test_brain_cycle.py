import unittest
from datetime import datetime, timezone

from engine.brain_cycle import build_brain_cycle, build_outcome_scorecards


class BrainCycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 11, 0, tzinfo=timezone.utc)

    def test_scorecards_exclude_nonterminal_and_unknown_outcomes(self):
        tasks = [
            {"task_type": "research_brief", "status": "succeeded"},
            {"task_type": "research_brief", "status": "failed"},
            {"task_type": "research_brief", "status": "blocked"},
            {"task_type": "research_brief", "status": "pending"},
            {"task_type": "research_brief", "status": "cancelled"},
        ]
        cards = build_outcome_scorecards(tasks)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["sample_size"], 2)
        self.assertEqual(cards[0]["execution_success_rate"], 0.5)
        self.assertEqual(cards[0]["evidence_level"], "insufficient_sample")
        self.assertIsNone(cards[0]["quality_score"])

    def test_small_samples_are_never_promoted_to_reliability_signal(self):
        tasks = [{"task_type": "code_review", "status": "succeeded"} for _ in range(4)]
        card = build_outcome_scorecards(tasks)[0]
        self.assertEqual(card["evidence_level"], "insufficient_sample")
        self.assertEqual(card["recommended_action"], "collect_more_verified_outcomes")

    def test_repeated_failures_create_review_candidate_not_policy_change(self):
        tasks = (
            [{"id": f"s{i}", "task_type": "research_brief", "status": "succeeded"} for i in range(6)]
            + [{"id": f"f{i}", "task_type": "research_brief", "status": "failed"} for i in range(2)]
        )
        result = build_brain_cycle(tasks, now=self.now)
        card = result["learning"]["scorecards"][0]
        self.assertEqual(card["sample_size"], 8)
        self.assertEqual(card["evidence_level"], "reliability_needs_review")
        self.assertEqual(result["learning"]["reliability_reviews_needed"], 1)
        self.assertEqual(result["learning"]["automatic_policy_changes"], 0)
        self.assertIn("review_task_type_reliability", [x["decision"] for x in result["decision_candidates"]])

    def test_success_rate_is_execution_reliability_not_quality(self):
        tasks = [{"task_type": "content_draft", "status": "succeeded"} for _ in range(5)]
        card = build_outcome_scorecards(tasks)[0]
        self.assertEqual(card["execution_success_rate"], 1.0)
        self.assertIsNone(card["quality_score"])
        self.assertIn("not a quality", card["quality_note"])

    def test_brain_cycle_is_read_only_and_deterministic(self):
        tasks = [
            {"id": "p1", "task_type": "system_audit", "title": "Audit", "status": "pending",
             "created_at": "2026-11-04T10:00:00Z", "attempts": 0},
            *[{"id": f"r{i}", "task_type": "code_review", "status": "succeeded"} for i in range(5)],
        ]
        original = [dict(task) for task in tasks]
        first = build_brain_cycle(tasks, now=self.now)
        second = build_brain_cycle(tasks, now=self.now)
        self.assertEqual(first, second)
        self.assertEqual(tasks, original)
        self.assertEqual(first["mode"], "read_only_deterministic_brain")
        self.assertEqual(first["guardrails"]["model_calls"], 0)
        self.assertEqual(first["guardrails"]["task_writes"], 0)
        self.assertFalse(first["guardrails"]["dispatch_authorized"])

    def test_invalid_input_and_limit_fail_closed(self):
        with self.assertRaises(ValueError):
            build_brain_cycle([None], now=self.now)
        with self.assertRaises(ValueError):
            build_brain_cycle([], now=self.now, limit=0)


if __name__ == "__main__":
    unittest.main()
