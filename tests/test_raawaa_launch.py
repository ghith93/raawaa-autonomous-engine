"""Regression tests for the Raawaa economics and content modules.

These exist because the numbers decide the business, and a silent change to a
price, a cost or a claim rule should fail CI rather than reach a customer.
"""
from __future__ import annotations

import unittest
from datetime import date

from engine import raawaa_content as content
from engine import raawaa_economics as econ


class EconomicsTests(unittest.TestCase):
    def setUp(self):
        self.brand = econ.load_brand()

    def test_config_matches_the_operator_numbers(self):
        """Pinned on purpose: these are the operator's real figures."""
        self.assertEqual([(p["sku"], p["price_usd"]) for p in self.brand["products"]],
                         [("30ml", 13), ("50ml", 16), ("100ml", 25)])
        self.assertEqual(self.brand["delivery"]["home_usd"], 2)
        self.assertEqual(self.brand["delivery"]["governorate_usd"], 1)
        self.assertEqual(self.brand["delivery"]["refused_shipment_charge_usd"], 1)

    def test_delivered_contribution_50ml(self):
        row = econ.contribution(self.brand, "50ml")
        # 16 - 3.5 - 2
        self.assertEqual(row["contribution_usd"], 10.5)

    def test_refused_order_is_a_real_loss(self):
        row = econ.contribution(self.brand, "50ml", delivered=False)
        self.assertLess(row["contribution_usd"], 0)
        self.assertEqual(row["revenue_usd"], 0)

    def test_break_even_refusal_rate_is_high_but_not_infinite(self):
        be = econ.break_even_refusal_rate(self.brand, "50ml")
        # 10.5 / (10.5 + 4.5) = 0.70
        self.assertAlmostEqual(be["break_even_refusal_rate"], 0.70, places=4)

    def test_expected_value_falls_as_refusals_rise(self):
        values = [econ.expected_per_order(self.brand, "50ml", r)["expected_per_attempt_usd"]
                  for r in econ.REFUSAL_SCENARIOS]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_at_break_even_the_expectation_is_zero(self):
        be = econ.break_even_refusal_rate(self.brand, "50ml")["break_even_refusal_rate"]
        expected = econ.expected_per_order(self.brand, "50ml", be)["expected_per_attempt_usd"]
        self.assertAlmostEqual(expected, 0.0, places=2)

    def test_bundle_beats_single_unit(self):
        single = econ.contribution(self.brand, "50ml")["contribution_usd"]
        bundle = econ.bundle_contribution(self.brand, "duo-50")["contribution_usd"]
        self.assertGreater(bundle, single * 2 - 1)

    def test_inventory_mix_must_match_units(self):
        with self.assertRaises(ValueError):
            econ.inventory_runway(self.brand, units=100, mix={"30ml": 1})

    def test_report_is_deterministic(self):
        self.assertEqual(econ.report(self.brand), econ.report(self.brand))


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.plan = content.week_plan(week=1, start=date(2026, 11, 7))

    def test_week_has_six_posts_and_three_reels(self):
        self.assertEqual(self.plan["counts"]["total"], 6)
        self.assertEqual(self.plan["counts"]["reels"], 3)

    def test_any_post_claiming_a_visible_result_requires_a_real_asset(self):
        """The rule is about the claim, not the format.

        A reel that explains or answers may be auto-drafted. A post whose value
        *is* the visible result must use real footage, because a fabricated
        before/after is the one thing that costs an account rather than a post.
        """
        for item in self.plan["items"]:
            if item["pillar"] == "result":
                self.assertTrue(item["real_asset_required"], item["id"])
                self.assertEqual(item["publish_mode"], "manual_review", item["id"])
            if item["real_asset_required"]:
                self.assertEqual(item["publish_mode"], "manual_review", item["id"])
                self.assertFalse(item["ai_visual_allowed"], item["id"])

    def test_ai_only_items_are_auto_candidates(self):
        auto = [i for i in self.plan["items"] if not i["real_asset_required"]]
        self.assertTrue(auto)
        for item in auto:
            self.assertEqual(item["publish_mode"], "auto_publish_candidate")
            self.assertTrue(item["ai_visual_allowed"])

    def test_no_forbidden_claim_leaks_into_a_caption(self):
        forbidden = self.brand_forbidden()
        for item in self.plan["items"]:
            body = item["caption_ar"]
            for term in forbidden:
                self.assertNotIn(term, body, f"{item['id']} contains forbidden claim {term!r}")

    def brand_forbidden(self) -> list[str]:
        return econ.load_brand()["claims"]["forbidden_ar"]

    def test_llm_prompt_carries_the_claim_rules(self):
        prompt = self.plan["items"][0]["llm_prompt"]
        self.assertIn("ممنوعة", prompt)
        self.assertIn("لا تذكر أنك ذكاء اصطناعي", prompt)

    def test_dates_are_strictly_increasing(self):
        dates = [item["date"] for item in self.plan["items"]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(set(dates)), len(dates))

    def test_weeks_do_not_repeat_the_same_hooks(self):
        week_two = content.week_plan(week=2, start=date(2026, 11, 14))
        hooks_one = {i["hook_ar"] for i in self.plan["items"]}
        hooks_two = {i["hook_ar"] for i in week_two["items"]}
        self.assertNotEqual(hooks_one, hooks_two)

    def test_markdown_render_mentions_manual_review_policy(self):
        text = content.render_markdown(self.plan)
        self.assertIn("خطة محتوى", text)
        self.assertIn("manual_review", text)

    def test_invalid_week_rejected(self):
        with self.assertRaises(ValueError):
            content.week_plan(week=0)


if __name__ == "__main__":
    unittest.main()
