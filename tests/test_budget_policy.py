import json
import unittest
from pathlib import Path

from engine.budget_policy import validate_budget_policy


ROOT = Path(__file__).resolve().parents[1]


class BudgetPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "config" / "policy.json").read_text(encoding="utf-8"))

    def test_configured_hybrid_budget_is_valid_and_free_first(self):
        self.assertEqual(validate_budget_policy(self.policy), [])
        self.assertEqual(self.policy["budget_envelope"]["monthly_total_hard_cap_usd"], 0)
        self.assertEqual(self.policy["monthly_budget_usd"], 0)
        self.assertEqual(self.policy["budget_envelope"]["unallocated_reserve_usd"], 0)
        self.assertEqual(self.policy["model_policy"]["strategy"], "hybrid")
        self.assertFalse(self.policy["model_policy"]["paid_fallback_enabled"])
        self.assertFalse(self.policy["allow_paid_model_calls"])

    def test_active_policy_is_zero_spend_in_every_cap(self):
        # Consistency guard for the owner's USD 0 mandate: every monetary cap
        # in both active policy files must be exactly zero, and no route in
        # the active provider registry may be enabled for a paid call.
        for name in ("policy.json", "policy.groq-smoke.json"):
            policy = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
            self.assertEqual(validate_budget_policy(policy), [], name)
            caps = [
                policy["daily_budget_usd"], policy["monthly_budget_usd"],
                policy["per_task_budget_usd"],
                policy["model_policy"]["paid_fallback_monthly_cap_usd"],
            ] + [
                policy["budget_envelope"][key] for key in (
                    "monthly_total_hard_cap_usd", "model_monthly_hard_cap_usd",
                    "infrastructure_monthly_hard_cap_usd", "unallocated_reserve_usd",
                    "daily_model_hard_cap_usd", "per_task_model_hard_cap_usd",
                )
            ]
            self.assertTrue(all(cap == 0 for cap in caps), name)
            self.assertFalse(policy["allow_paid_model_calls"], name)
            self.assertFalse(policy["model_policy"]["paid_fallback_enabled"], name)
        registry = json.loads((ROOT / "config" / "provider_profiles.json").read_text(encoding="utf-8"))
        for route in registry["routes"]:
            self.assertFalse(route["enabled"], route["id"])
            # A route may publish exact zero prices or leave pricing unknown
            # (None); unknown pricing is blocked, never treated as free.
            for cost in (route["input_cost_per_million_usd"],
                         route["output_cost_per_million_usd"]):
                self.assertIn(cost, (0, None), route["id"])

    def test_monthly_total_cannot_be_raised(self):
        self.policy["budget_envelope"]["monthly_total_hard_cap_usd"] = 0.01
        self.assertTrue(any("exactly 0.00" in error for error in validate_budget_policy(self.policy)))

    def test_model_allocation_cannot_exceed_total_or_break_sum(self):
        self.policy["budget_envelope"]["model_monthly_hard_cap_usd"] = 15.0
        self.assertTrue(validate_budget_policy(self.policy))

    def test_infrastructure_overages_must_remain_disabled(self):
        self.policy["budget_envelope"]["infrastructure_monthly_hard_cap_usd"] = 0.01
        self.assertTrue(any("infrastructure paid overages" in error for error in validate_budget_policy(self.policy)))

    def test_unallocated_reserve_cannot_be_spent_without_reallocation(self):
        self.policy["budget_envelope"]["unallocated_reserve_usd"] = 2.99
        self.assertTrue(validate_budget_policy(self.policy))

    def test_daily_and_per_task_caps_are_nested(self):
        self.policy["per_task_budget_usd"] = 0.41
        self.assertTrue(any("per-task cap" in error for error in validate_budget_policy(self.policy)))

    def test_paid_fallback_requires_a_provider_hard_stop_and_stays_disabled(self):
        self.policy["model_policy"]["paid_fallback_enabled"] = True
        self.assertTrue(any("paid fallback remains disabled" in error for error in validate_budget_policy(self.policy)))

    def test_unknown_cost_or_unsettled_reservation_cannot_be_ignored(self):
        self.policy["budget_envelope"]["stop_on_unknown_cost"] = False
        self.assertTrue(any("unknown provider costs" in error for error in validate_budget_policy(self.policy)))
        self.policy["budget_envelope"]["stop_on_unknown_cost"] = True
        self.policy["budget_envelope"]["stop_on_unsettled_reservation"] = False
        self.assertTrue(any("unsettled monetary reservations" in error for error in validate_budget_policy(self.policy)))

    def test_boolean_money_values_are_rejected(self):
        self.policy["monthly_budget_usd"] = True
        self.assertTrue(validate_budget_policy(self.policy))

    def test_malformed_policy_objects_fail_closed(self):
        self.assertTrue(validate_budget_policy(None))
        self.policy["model_policy"].pop("strategy")
        self.assertTrue(validate_budget_policy(self.policy))


if __name__ == "__main__":
    unittest.main()
