import unittest
from datetime import datetime, timezone

from engine.brain_cycle import (
    DEFAULT_SNAPSHOT_LIMIT,
    build_brain_cycle,
    build_outcome_scorecards,
    wilson_interval,
)


class BrainCycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 11, 0, tzinfo=timezone.utc)

    # --- scorecards -------------------------------------------------------

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

    def test_task_type_whitespace_variants_share_one_bucket(self):
        tasks = (
            [{"task_type": "research_brief", "status": "succeeded"} for _ in range(3)]
            + [{"task_type": " research_brief ", "status": "succeeded"} for _ in range(3)]
        )
        cards = build_outcome_scorecards(tasks)
        self.assertEqual(len(cards), 1, "whitespace variants must not split the sample")
        self.assertEqual(cards[0]["task_type"], "research_brief")
        self.assertEqual(cards[0]["sample_size"], 6)

    # --- statistics -------------------------------------------------------

    def test_wilson_interval_matches_reference_values(self):
        self.assertEqual(wilson_interval(1, 5), {"low": 0.0362, "high": 0.6245})
        self.assertEqual(wilson_interval(40, 100), {"low": 0.3094, "high": 0.498})
        self.assertIsNone(wilson_interval(0, 0))

    def test_one_failure_in_five_is_a_watch_not_an_escalation(self):
        """n=5 at a 20% point estimate has a 95% interval spanning 3.6%-62.4%."""
        tasks = (
            [{"id": f"s{i}", "task_type": "research_brief", "status": "succeeded"} for i in range(4)]
            + [{"id": "f0", "task_type": "research_brief", "status": "failed"}]
        )
        card = build_outcome_scorecards(tasks)[0]
        self.assertEqual(card["sample_size"], 5)
        self.assertEqual(card["failure_share"], 0.2)
        self.assertEqual(card["failure_share_ci95"], {"low": 0.0362, "high": 0.6245})
        self.assertEqual(card["evidence_level"], "reliability_watch")
        self.assertNotEqual(card["evidence_level"], "reliability_needs_review")

        result = build_brain_cycle(tasks, now=self.now)
        self.assertEqual(result["learning"]["reliability_reviews_needed"], 0)
        self.assertEqual(result["learning"]["reliability_watch_count"], 1)
        decisions = {(d["decision"], d["priority"]) for d in result["decision_candidates"]}
        self.assertIn(("widen_sample_for_task_type", "medium"), decisions)
        self.assertNotIn(("review_task_type_reliability", "high"), decisions)

    def test_escalation_requires_the_interval_lower_bound_to_clear_threshold(self):
        tasks = (
            [{"id": f"s{i}", "task_type": "research_brief", "status": "succeeded"} for i in range(40)]
            + [{"id": f"f{i}", "task_type": "research_brief", "status": "failed"} for i in range(20)]
        )
        card = build_outcome_scorecards(tasks)[0]
        self.assertEqual(card["sample_size"], 60)
        self.assertEqual(card["failure_share_ci95"], {"low": 0.2273, "high": 0.4594})
        self.assertEqual(card["evidence_level"], "reliability_needs_review")

    def test_healthy_task_type_never_escalates(self):
        tasks = [{"task_type": "content_draft", "status": "succeeded"} for _ in range(100)]
        card = build_outcome_scorecards(tasks)[0]
        self.assertEqual(card["failure_share_ci95"], {"low": 0.0, "high": 0.037})
        self.assertEqual(card["evidence_level"], "provisional_reliability_signal")

    def test_repeated_failures_create_review_candidate_not_policy_change(self):
        tasks = (
            [{"id": f"s{i}", "task_type": "research_brief", "status": "succeeded"} for i in range(40)]
            + [{"id": f"f{i}", "task_type": "research_brief", "status": "failed"} for i in range(20)]
        )
        result = build_brain_cycle(tasks, now=self.now)
        card = result["learning"]["scorecards"][0]
        self.assertEqual(card["sample_size"], 60)
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

    # --- snapshot bookkeeping --------------------------------------------

    def test_snapshot_reports_its_own_window_and_exclusions(self):
        tasks = [
            {"id": "p1", "task_type": "system_audit", "title": "Audit", "status": "pending",
             "created_at": "2026-11-04T10:00:00Z", "attempts": 0},
            *[{"id": f"r{i}", "task_type": "code_review", "status": "succeeded",
               "created_at": f"2026-11-0{1 + (i % 3)}T0{i % 10}:00:00Z"} for i in range(5)],
        ]
        result = build_brain_cycle(tasks, now=self.now)
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["requested_limit"], DEFAULT_SNAPSHOT_LIMIT)
        self.assertFalse(snapshot["truncated"])
        self.assertEqual(snapshot["records_returned"], 6)
        self.assertEqual(snapshot["records_usable"], 6)
        self.assertEqual(result["excluded"], {"non_terminal": 1, "missing_task_type": 0, "malformed": 0})
        self.assertEqual(snapshot["oldest_returned"], "2026-11-01T00:00:00+00:00")
        self.assertEqual(snapshot["newest_returned"], "2026-11-04T10:00:00+00:00")

    def test_full_window_is_flagged_as_truncated(self):
        tasks = [{"task_type": "code_review", "status": "succeeded"} for _ in range(5)]
        result = build_brain_cycle(tasks, now=self.now, snapshot_limit=5)
        self.assertTrue(result["snapshot"]["truncated"])

        partial = build_brain_cycle(tasks[:4], now=self.now, snapshot_limit=5)
        self.assertFalse(partial["snapshot"]["truncated"])

    def test_malformed_rows_are_counted_not_fatal(self):
        tasks = [
            None,
            "not a task",
            *[{"task_type": "code_review", "status": "succeeded"} for _ in range(5)],
        ]
        result = build_brain_cycle(tasks, now=self.now)
        self.assertEqual(result["excluded"]["malformed"], 2)
        self.assertEqual(result["snapshot"]["records_returned"], 7)
        self.assertEqual(result["snapshot"]["records_usable"], 5)
        self.assertEqual(result["learning"]["scorecards"][0]["sample_size"], 5)

    def test_limit_is_a_portfolio_view_control_only(self):
        tasks = [
            *[{"id": f"p{i}", "task_type": "system_audit", "title": f"p{i}", "status": "pending",
               "created_at": "2026-11-04T10:00:00Z", "attempts": 0} for i in range(6)],
            *[{"task_type": "code_review", "status": "succeeded"} for _ in range(5)],
        ]
        narrow = build_brain_cycle(tasks, now=self.now, limit=1)
        wide = build_brain_cycle(tasks, now=self.now, limit=50)
        self.assertEqual(len(narrow["portfolio"]["next_best_tasks"]), 1)
        self.assertEqual(len(wide["portfolio"]["next_best_tasks"]), 6)
        self.assertEqual(
            narrow["learning"]["scorecards"], wide["learning"]["scorecards"],
            "the reliability denominator must not follow the view limit",
        )

    # --- contract ---------------------------------------------------------

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
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(first["guardrails"]["model_calls"], 0)
        self.assertEqual(first["guardrails"]["task_writes"], 0)
        self.assertFalse(first["guardrails"]["dispatch_authorized"])
        self.assertEqual(first["learning"]["automatic_prompt_changes"], 0)
        self.assertEqual(first["learning"]["memory_writes"], 0)

    def test_invalid_input_and_limit_fail_closed(self):
        with self.assertRaises(ValueError):
            build_brain_cycle("not a list", now=self.now)
        with self.assertRaises(ValueError):
            build_brain_cycle([], now=self.now, limit=0)
        with self.assertRaises(ValueError):
            build_brain_cycle([], now=self.now, limit=51)
        with self.assertRaises(ValueError):
            build_brain_cycle([], now=self.now, snapshot_limit=0)


if __name__ == "__main__":
    unittest.main()
