"""Tests for the launch infrastructure: order intake, offers and visual quality."""
from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from engine import raawaa_infrastructure as infra
from engine import raawaa_quality as quality


def _png(path: Path, width: int, height: int) -> None:
    raw = b"".join(b"\x00" + bytes([200, 190, 180] * width) for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)


class PhoneTests(unittest.TestCase):
    def test_accepts_local_and_international_forms(self):
        for raw in ("0999123456", "+963999123456", "00963999123456", "0999 123 456"):
            self.assertEqual(infra.normalise_phone(raw), "0999123456", raw)

    def test_accepts_arabic_indic_digits(self):
        self.assertEqual(infra.normalise_phone("٠٩٩٩١٢٣٤٥٦"), "0999123456")

    def test_rejects_impossible_numbers(self):
        for raw in ("12345", "0799123456", "", "abc", "09991234567", None):
            self.assertIsNone(infra.normalise_phone(raw), raw)


class OrderValidationTests(unittest.TestCase):
    def setUp(self):
        self.storefront = infra.load_storefront()

    def valid(self, **overrides):
        payload = {"name": "سارة", "phone": "0999123456", "city": "حماة", "sku": "duo-50"}
        payload.update(overrides)
        return payload

    def test_accepts_a_valid_order(self):
        result = infra.validate_order(self.valid(), self.storefront)
        self.assertTrue(result["ok"])
        self.assertEqual(result["normalised"]["phone"], "0999123456")

    def test_requires_every_mandatory_field(self):
        result = infra.validate_order({}, self.storefront)
        self.assertFalse(result["ok"])
        for field in ("name", "phone", "city", "sku"):
            self.assertEqual(result["errors"][field], "required")

    def test_bad_phone_becomes_a_page_error_not_a_crash(self):
        result = infra.validate_order(self.valid(phone="111"), self.storefront)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"]["phone"], "invalid_syrian_number")

    def test_unknown_sku_rejected(self):
        result = infra.validate_order(self.valid(sku="999ml"), self.storefront)
        self.assertEqual(result["errors"]["sku"], "unknown_sku")

    def test_long_note_rejected(self):
        result = infra.validate_order(self.valid(notes="x" * 301), self.storefront)
        self.assertEqual(result["errors"]["notes"], "max_300")

    def test_idempotency_collapses_a_double_tap(self):
        a = infra.idempotency_key(self.valid(), today=__import__("datetime").date(2026, 11, 7))
        b = infra.idempotency_key(self.valid(phone="+963999123456"),
                                  today=__import__("datetime").date(2026, 11, 7))
        self.assertEqual(a, b)

    def test_idempotency_changes_across_skus(self):
        a = infra.idempotency_key(self.valid(sku="duo-50"))
        b = infra.idempotency_key(self.valid(sku="30ml"))
        self.assertNotEqual(a, b)


class CapacityTests(unittest.TestCase):
    def test_accepts_under_the_limit_and_closes_at_it(self):
        self.assertTrue(infra.capacity_gate(0)["accepting"])
        self.assertTrue(infra.capacity_gate(19)["accepting"])
        gate = infra.capacity_gate(20)
        self.assertFalse(gate["accepting"])
        self.assertIsNotNone(gate["message_ar"])

    def test_negative_count_rejected(self):
        with self.assertRaises(ValueError):
            infra.capacity_gate(-1)


class OfferTests(unittest.TestCase):
    def setUp(self):
        self.offers = infra.load_offers()

    def test_lead_offer_is_the_duo(self):
        self.assertEqual(infra.active_offer(self.offers)["id"], "launch-duo")

    def test_every_allowed_scarcity_is_actually_true(self):
        for claim in self.offers["allowed_scarcity"]:
            self.assertTrue(claim["true_because"], claim["id"])
            self.assertTrue(claim["implementation_ar"], claim["id"])

    def test_fake_scarcity_names_the_usual_tricks(self):
        forbidden = " ".join(c["claim_ar"] for c in self.offers["forbidden_fake_scarcity"])
        self.assertIn("آخر قطعة", forbidden)
        self.assertIn("٧٠٪", forbidden)

    def test_guarantee_costs_less_than_a_successful_order(self):
        self.assertIn("لا تدفع", self.offers["guarantee"]["text_ar"])


class FunnelTests(unittest.TestCase):
    def test_reports_the_weakest_step(self):
        report = infra.funnel_report({
            "visit": 1000, "product_view": 400, "form_start": 100,
            "form_submit": 80, "confirmed": 40, "delivered": 36, "refused": 4,
        })
        # 400/1000 = 40% but 100/400 = 25%: the drop into the form is the weak link.
        self.assertEqual(report["weakest_step"], "product_view->form_start")
        self.assertAlmostEqual(report["refusal_rate"], 0.1)

    def test_empty_funnel_does_not_divide_by_zero(self):
        report = infra.funnel_report({})
        self.assertIsNone(report["refusal_rate"])


class VisualQualityTests(unittest.TestCase):
    def test_accepts_a_four_by_five_1080_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "good.png"
            _png(path, 1080, 1350)
            self.assertEqual(quality.assess(quality.inspect(path)), [])

    def test_rejects_a_small_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small.png"
            _png(path, 600, 750)
            problems = quality.assess(quality.inspect(path))
            self.assertTrue(any(p.startswith("too_small") for p in problems))

    def test_rejects_a_missing_file_without_raising(self):
        problems = quality.assess(quality.inspect("/nonexistent.jpg"))
        self.assertEqual(problems, ["file_not_found"])

    def test_checklist_forbids_filters_and_night_light(self):
        data = quality.shot_checklist()
        joined = " ".join(data["phone_settings"]["do_not_ar"])
        self.assertIn("وميض", joined)
        self.assertIn("الليل", joined)
        self.assertIn("فلتر", data["phone_settings"]["no_filter_ar"])

    def test_acceptance_requires_a_legible_label(self):
        data = quality.shot_checklist()
        self.assertTrue(any("الملصق" in item for item in data["acceptance"]))


if __name__ == "__main__":
    unittest.main()
