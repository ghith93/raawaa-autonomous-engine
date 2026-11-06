"""Guard rails on free-resource claims.

These exist because 'free AI images' is a category full of stale advice: the
Gemini free image tier disappeared, Hugging Face removed free monthly credits,
and ModelScope's generosity is non-commercial. A config file that quietly drifts
back to a non-commercial licence would be a legal problem, not a bug.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


def load(name: str) -> dict:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


class FreeImageSourceTests(unittest.TestCase):
    def setUp(self):
        self.stack = load("raawaa_ai_stack.json")
        self.programs = load("free_resource_programs.json")

    def test_nano_banana_is_recorded_as_closed_source(self):
        nb = self.stack["image_sources_2026_11"]["nano_banana"]
        self.assertFalse(nb["open_source"])
        self.assertFalse(nb["free_api"])

    def test_no_non_commercial_model_is_ever_approved(self):
        approved = self.programs["open_weight_models"]
        for model in approved:
            if model["verdict"] == "approved":
                self.assertTrue(model["commercial_use"], model["id"])
                self.assertIn(model["license"], {"Apache-2.0", "MIT"})

    def test_qwen_2_1_is_explicitly_rejected_for_commercial_use(self):
        rejected = {m["id"]: m for m in self.programs["open_weight_models"]
                    if m["verdict"] != "approved"}
        self.assertIn("Qwen/Qwen-Image-2.1", rejected)
        self.assertFalse(rejected["Qwen/Qwen-Image-2.1"]["commercial_use"])

    def test_every_rejected_free_tier_has_a_reason(self):
        for entry in self.programs["verified_image_free_tiers_2026_11"]:
            if entry["verdict"] == "reject":
                self.assertTrue(entry.get("why"), entry["id"])

    def test_cloudflare_is_the_recommended_paid_card_free_route(self):
        entries = {e["id"]: e for e in self.programs["verified_image_free_tiers_2026_11"]}
        cf = entries["cloudflare_workers_ai"]
        self.assertEqual(cf["verdict"], "use")
        self.assertTrue(cf["commercial_use"])
        self.assertFalse(cf["card_required"])

    def test_monthly_need_is_far_below_free_capacity(self):
        need = self.stack["image_sources_2026_11"]["monthly_need_vs_available"]
        self.assertLess(need["need_images"], need["cloudflare_free_capacity"])


if __name__ == "__main__":
    unittest.main()
