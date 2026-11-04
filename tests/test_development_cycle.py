import json
import unittest
from datetime import datetime, timedelta, timezone

from engine.development_cycle import build_development_cycle, render_markdown


def _iso(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


class PipelineEscalationTests(unittest.TestCase):
    def test_red_main_ci_outranks_everything_else(self):
        state = {
            "signals": {"main_ci_status": "failure"},
            "open_pull_requests": [
                {"number": 10, "title": "docs", "checks": "success",
                 "mergeable": "MERGEABLE", "created_at": _iso(60)},
            ],
        }
        report = build_development_cycle(state)
        self.assertEqual(report["next_actions"][0]["action"], "fix_red_main_ci")
        kinds = [item["action"] for item in report["next_actions"]]
        self.assertLess(kinds.index("fix_red_main_ci"), kinds.index("merge_after_verification"))

    def test_green_pipeline_raises_no_pipeline_action(self):
        state = {"signals": {"main_ci_status": "success", "deploy_status": "skipped",
                             "watchdog_last_success_at": _iso(5)}}
        report = build_development_cycle(state)
        self.assertEqual(report["next_actions"], [])

    def test_stale_watchdog_is_reported_with_its_age(self):
        state = {"signals": {"main_ci_status": "success",
                             "watchdog_last_success_at": _iso(45)}}
        report = build_development_cycle(state)
        action = [item for item in report["next_actions"] if item["action"] == "restore_watchdog_cadence"]
        self.assertEqual(len(action), 1)
        self.assertGreaterEqual(action[0]["evidence"]["minutes_since_last_success"], 44)

    def test_fresh_watchdog_inside_threshold_is_silent(self):
        state = {"signals": {"main_ci_status": "success",
                             "watchdog_last_success_at": _iso(10)}}
        report = build_development_cycle(state)
        self.assertNotIn("restore_watchdog_cadence", [i["action"] for i in report["next_actions"]])

    def test_failed_deploy_carries_an_owner_gate(self):
        state = {"signals": {"main_ci_status": "success", "deploy_status": "failure"}}
        report = build_development_cycle(state)
        deploy = [i for i in report["next_actions"] if i["action"] == "diagnose_failed_deploy"][0]
        self.assertIn("owner approval", deploy["gate"])

    def test_stalled_queue_is_escalated(self):
        state = {"signals": {"main_ci_status": "success", "oldest_pending_age_minutes": 300}}
        report = build_development_cycle(state)
        self.assertIn("drain_stalled_queue", [i["action"] for i in report["next_actions"]])


class PullRequestClassificationTests(unittest.TestCase):
    def _report(self, **pr):
        state = {"signals": {"main_ci_status": "success"},
                 "open_pull_requests": [{"number": 7, "title": "t", "created_at": _iso(1440), **pr}]}
        return build_development_cycle(state)

    def test_conflicting_branch_asks_for_a_rebase(self):
        report = self._report(mergeable="CONFLICTING", checks="success")
        self.assertEqual(report["next_actions"][0]["action"], "rebase_or_supersede")

    def test_stale_base_is_not_treated_as_verified(self):
        report = self._report(mergeable="MERGEABLE", merge_state_status="BEHIND", checks="success")
        action = report["next_actions"][0]
        self.assertEqual(action["action"], "refresh_branch_then_verify")
        self.assertTrue(action["evidence"]["stale_evidence"])

    def test_current_green_branch_is_mergeable(self):
        report = self._report(mergeable="MERGEABLE", merge_state_status="CLEAN", checks="success")
        self.assertEqual(report["next_actions"][0]["action"], "merge_after_verification")

    def test_failing_checks_are_diagnosed_not_merged(self):
        report = self._report(mergeable="MERGEABLE", checks="failure")
        self.assertEqual(report["next_actions"][0]["action"], "diagnose_failing_check")

    def test_security_change_requires_independent_review(self):
        report = self._report(mergeable="MERGEABLE", merge_state_status="CLEAN",
                              checks="success", title="security: restrict bridge")
        action = report["next_actions"][0]
        self.assertIn("independent correctness review", action["gate"])
        self.assertTrue(action["evidence"]["security_sensitive"])

    def test_draft_is_flagged_but_not_hidden(self):
        report = self._report(mergeable="MERGEABLE", merge_state_status="CLEAN",
                              checks="success", is_draft=True)
        self.assertTrue(report["next_actions"][0]["evidence"]["is_draft"])
        self.assertIn("draft", report["next_actions"][0]["reason"])


class IssueRankingTests(unittest.TestCase):
    def test_security_labelled_issue_outranks_an_enhancement(self):
        state = {
            "signals": {"main_ci_status": "success"},
            "open_issues": [
                {"number": 1, "title": "enhancement", "labels": ["enhancement"], "created_at": _iso(60)},
                {"number": 2, "title": "security", "labels": ["security"], "created_at": _iso(60)},
            ],
        }
        report = build_development_cycle(state)
        self.assertEqual(report["next_actions"][0]["target"], "#2")

    def test_age_only_breaks_ties_within_a_weight(self):
        state = {
            "signals": {"main_ci_status": "success"},
            "open_issues": [
                {"number": 3, "title": "new", "labels": ["bug"], "created_at": _iso(60)},
                {"number": 4, "title": "old", "labels": ["bug"], "created_at": _iso(1440 * 25)},
            ],
        }
        report = build_development_cycle(state)
        self.assertEqual([i["target"] for i in report["next_actions"]], ["#4", "#3"])

    def test_blocked_issue_is_held_not_ranked(self):
        state = {
            "signals": {"main_ci_status": "success"},
            "open_issues": [
                {"number": 5, "title": "waiting", "labels": ["blocked"], "created_at": _iso(60)},
            ],
        }
        report = build_development_cycle(state)
        self.assertEqual(report["next_actions"], [])
        self.assertEqual(report["blocked"][0]["target"], "#5")
        self.assertIn("external decision", report["blocked"][0]["gate"])


class HygieneTests(unittest.TestCase):
    def test_branch_sprawl_becomes_housekeeping(self):
        report = build_development_cycle({"remote_branch_count": 464})
        self.assertEqual(report["next_actions"][0]["action"], "audit_stale_branches")

    def test_documentation_drift_is_reported_with_names(self):
        report = build_development_cycle({"documentation_drift": ["docs/a.md", "docs/b.md"]})
        action = report["next_actions"][0]
        self.assertEqual(action["action"], "reconcile_documentation")
        self.assertEqual(action["evidence"]["documents"], ["docs/a.md", "docs/b.md"])


class ContractTests(unittest.TestCase):
    def test_no_side_effects_are_ever_reported(self):
        report = build_development_cycle({"signals": {"main_ci_status": "failure"},
                                          "remote_branch_count": 500})
        self.assertEqual(report["side_effects"]["merges"], 0)
        self.assertEqual(report["side_effects"]["deploys"], 0)
        self.assertEqual(report["side_effects"]["spend_usd"], 0.0)
        self.assertEqual(report["side_effects"]["network_calls"], 0)
        self.assertFalse(report["side_effects"]["queue_reordered"])

    def test_ranking_is_deterministic_for_the_same_state(self):
        state = {
            "signals": {"main_ci_status": "success"},
            "open_pull_requests": [
                {"number": 11, "title": "a", "mergeable": "CONFLICTING", "created_at": _iso(60)},
                {"number": 12, "title": "b", "mergeable": "MERGEABLE",
                 "merge_state_status": "CLEAN", "checks": "success", "created_at": _iso(60)},
            ],
            "open_issues": [
                {"number": 13, "title": "c", "labels": ["bug"], "created_at": _iso(600)},
            ],
        }
        first = [(i["target"], i["score"]) for i in build_development_cycle(state)["next_actions"]]
        second = [(i["target"], i["score"]) for i in build_development_cycle(state)["next_actions"]]
        self.assertEqual(first, second)

    def test_empty_state_is_valid_and_quiet(self):
        report = build_development_cycle({})
        self.assertEqual(report["next_actions"], [])
        self.assertEqual(report["schema_version"], 1)

    def test_limit_truncates_into_deferred_without_losing_items(self):
        state = {"open_issues": [{"number": n, "title": str(n), "labels": ["enhancement"],
                                  "created_at": _iso(60)} for n in range(1, 10)]}
        report = build_development_cycle(state, limit=3)
        self.assertEqual(len(report["next_actions"]), 3)
        self.assertEqual(len(report["deferred"]), 6)
        self.assertEqual(
            {i["target"] for i in report["next_actions"] + report["deferred"]},
            {f"#{n}" for n in range(1, 10)},
        )

    def test_markdown_render_is_readable_and_non_authorising(self):
        report = build_development_cycle({
            "signals": {"main_ci_status": "failure"},
            "open_issues": [{"number": 9, "title": "x", "labels": ["bug"], "created_at": _iso(60)}],
        })
        markdown = render_markdown(report)
        self.assertIn("## Development cycle", markdown)
        self.assertIn("`main`", markdown)
        self.assertIn("do not authorise", markdown)
        self.assertIn("#9", markdown)


if __name__ == "__main__":
    unittest.main()
