"""Commerce primitives: money, delivery, orders, offers, publishing, sourcing."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.commerce.delivery import (  # noqa: E402
    DeliveryBook, DeliveryRate, Order as DeliveryOrder,
    break_even_refusal_rate, free_shipping_threshold,
)
from engine.commerce.money import CurrencyMismatch, Money, safe_div  # noqa: E402
from engine.commerce.offers import (  # noqa: E402
    Offer, ScarcityClaim, copy_is_honest, is_forbidden,
)
from engine.commerce.orders import (  # noqa: E402
    Order, OrderBook, OrderError, OrderLine, TransitionError, idempotency_key,
    normalise_phone,
)
from engine.commerce.publishing import LocalFileChannel, Post, PublishError, Queue  # noqa: E402
from engine.commerce.sourcing import Supplier, landed_cost, rank  # noqa: E402
from engine.commerce.unit_economics import Sku, break_even_refusal, contribution  # noqa: E402
from engine.commerce.validation import Product, is_publishable, validate  # noqa: E402


class MoneyTests(unittest.TestCase):
    def test_integer_minor_units_never_drift(self):
        running = Money.zero()
        for _ in range(10):
            running = running + Money.of("0.1")
        self.assertEqual(running.minor, 100)

    def test_currency_mismatch_is_refused(self):
        with self.assertRaises(CurrencyMismatch):
            Money.of(5, "USD") + Money.of(5, "SYP")

    def test_safe_div_reports_unknown_instead_of_infinity(self):
        self.assertIsNone(safe_div(1, 0))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.rate = DeliveryRate(fee=Money.of(2), refusal_fee=Money.of(1))

    def test_break_even_refusal_matches_the_published_formula(self):
        rate = break_even_refusal_rate(Money.of(18), Money.of(4.5), self.rate)
        self.assertAlmostEqual(rate * 100, 67.6, places=1)

    def test_a_price_below_cost_has_no_break_even(self):
        self.assertEqual(
            break_even_refusal_rate(Money.of(3), Money.of(4.5), self.rate), 0.0)

    def test_free_shipping_threshold_covers_cost_and_margin(self):
        price = free_shipping_threshold(Money.of(0), Money.of(4.5), self.rate, target_margin=0.5)
        self.assertIsNotNone(price)
        self.assertGreater(price.minor, 0)

    def test_cash_reconciliation_flags_a_shortfall(self):
        order = DeliveryOrder(
            order_id="1", revenue=Money.of(18), goods_cost=Money.of(4.5),
            delivery=self.rate, delivered=True, cod_collected=Money.of(16),
        )
        self.assertFalse(order.cash_reconciles())

    def test_book_reports_in_flight_separately_from_resolved(self):
        book = DeliveryBook()
        book.add(DeliveryOrder("1", Money.of(18), Money.of(4.5), self.rate, delivered=True,
                               cod_collected=Money.of(18)))
        book.add(DeliveryOrder("2", Money.of(18), Money.of(4.5), self.rate, delivered=False))
        book.add(DeliveryOrder("3", Money.of(18), Money.of(4.5), self.rate))
        report = book.report()
        self.assertEqual(report["shipped"], 2)
        self.assertEqual(report["in_flight"], 1)
        self.assertAlmostEqual(report["refusal_rate"], 0.5)


class PhoneTests(unittest.TestCase):
    def test_arabic_indic_digits_are_accepted(self):
        self.assertEqual(normalise_phone("٠٩٩٩١٢٣٤٥٦"), "0999123456")

    def test_country_code_and_spacing_are_accepted(self):
        self.assertEqual(normalise_phone("+963 999 123 456"), "0999123456")

    def test_short_number_is_rejected(self):
        with self.assertRaises(OrderError):
            normalise_phone("12345")

    def test_bad_prefix_is_rejected(self):
        with self.assertRaises(OrderError):
            normalise_phone("0111234567")


class OrderTests(unittest.TestCase):
    def make(self):
        return Order("A1", "0999123456", "Hama",
                     [OrderLine("serum-50", 1, Money.of(18))], Money.of(0))

    def test_requested_is_not_revenue(self):
        self.assertFalse(self.make().is_revenue())

    def test_full_flow_reaches_revenue(self):
        order = self.make()
        for state in ("confirmed", "prepared", "shipped", "delivered"):
            order.transition(state)
        self.assertFalse(order.is_revenue())
        order.cod_collected = order.total()
        self.assertTrue(order.is_revenue())

    def test_cash_settlement_gate_and_unreconciled_agree(self):
        book = OrderBook()
        order = self.make()
        book.submit(order, "k1")
        order.cod_collected = order.total()
        self.assertFalse(order.is_revenue())  # cash alone is insufficient
        for state in ("confirmed", "prepared", "shipped", "delivered"):
            order.transition(state)
        for cash in (None, Money.of(17), Money.of(19), Money.of(18, "EUR")):
            with self.subTest(cash=cash):
                order.cod_collected = cash
                self.assertFalse(order.is_revenue())
                self.assertEqual(book.funnel()["revenue_recognised"], 0)
                self.assertEqual(book.unreconciled(), ["A1"])
        order.cod_collected = Money.of(18)
        self.assertTrue(order.is_revenue())
        self.assertEqual(book.funnel()["revenue_recognised"], 18)
        self.assertEqual(book.unreconciled(), [])

    def test_skipping_a_step_is_refused(self):
        with self.assertRaises(TransitionError):
            self.make().transition("delivered")

    def test_refusal_needs_a_reason(self):
        order = self.make()
        order.transition("confirmed")
        order.transition("prepared")
        order.transition("shipped")
        with self.assertRaises(TransitionError):
            order.transition("refused")
        order.transition("refused", "buyer not reachable")
        self.assertFalse(order.is_revenue())

    def test_duplicate_submission_is_not_a_second_order(self):
        book = OrderBook()
        key = idempotency_key("0999123456", "serum-50")
        self.assertTrue(book.submit(self.make(), key))
        self.assertFalse(book.submit(self.make(), key))
        self.assertEqual(book.funnel()["submitted"], 1)

    def test_unreconciled_cash_is_reported(self):
        book = OrderBook()
        order = self.make()
        for state in ("confirmed", "prepared", "shipped", "delivered"):
            order.transition(state)
        book.submit(order, "k1")
        self.assertEqual(book.unreconciled(), ["A1"])


class OfferHonestyTests(unittest.TestCase):
    def test_fake_scarcity_is_detected(self):
        self.assertTrue(is_forbidden("آخر قطعة! ينتهي بعد ٣ ساعات"))
        self.assertTrue(is_forbidden("خصم ٧٠٪"))

    def test_truthful_copy_passes(self):
        self.assertEqual(is_forbidden("سعر الإطلاق لأول ٥٠ طلبًا"), [])

    def test_counter_claim_needs_a_real_used_count(self):
        claim = ScarcityClaim("c", "٢٠ طلبًا", "counter", limit=20, enforced_value=20)
        self.assertTrue(claim.validate())

    def test_expired_date_range_is_rejected(self):
        claim = ScarcityClaim("d", "عرض", "date_range",
                              starts=date.today() - timedelta(days=30),
                              ends=date.today() - timedelta(days=1))
        self.assertTrue(any("passed" in p for p in claim.validate()))

    def test_offer_above_honest_discount_ceiling_is_flagged(self):
        offer = Offer("o", "خصم", Money.of(5), 1, Money.of(20))
        self.assertTrue(any("ceiling" in p for p in offer.validate()))


class PublishingTests(unittest.TestCase):
    def test_draft_cannot_be_published_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Queue(Path(tmp) / "q.jsonl", channels={"manual": LocalFileChannel(Path(tmp))})
            queue.add(Post("p1", "نص", ["a.jpg"], ["manual"]))
            with self.assertRaises(PublishError):
                queue.publish("p1")

    def test_approved_post_publishes_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.jsonl"
            queue = Queue(path, channels={"manual": LocalFileChannel(Path(tmp) / "posts")})
            queue.add(Post("p1", "نص", ["a.jpg"], ["manual"]))
            queue.approve("p1")
            results = queue.publish("p1")
            self.assertIn("manual", results)
            reloaded = Queue(path, channels={"manual": LocalFileChannel(Path(tmp) / "posts")})
            self.assertEqual(reloaded.posts["p1"].state, "published")

    def test_missing_channel_adapter_is_recorded_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Queue(Path(tmp) / "q.jsonl")
            queue.add(Post("p1", "نص", ["a.jpg"], ["instagram"]))
            queue.approve("p1")
            self.assertEqual(queue.publish("p1"), {})
            self.assertEqual(queue.posts["p1"].state, "failed")
            self.assertIn("no adapter", queue.posts["p1"].failures[0]["reason"])


class InstagramChannelTests(unittest.TestCase):
    def test_without_credentials_it_reports_unavailable_instead_of_failing_late(self):
        from engine.commerce.publishing import InstagramGraphChannel
        ok, reason = InstagramGraphChannel(env={}).available()
        self.assertFalse(ok)
        self.assertIn("IG_USER_ID", reason)

    def test_without_a_public_media_host_it_stays_unavailable(self):
        from engine.commerce.publishing import InstagramGraphChannel
        channel = InstagramGraphChannel(env={"IG_USER_ID": "1", "IG_ACCESS_TOKEN": "t"})
        ok, reason = channel.available()
        self.assertFalse(ok)
        self.assertIn("publicly reachable", reason)


class SourcingTests(unittest.TestCase):
    def test_cheap_but_unreliable_supplier_loses(self):
        local = Supplier("a", "local", Money.of(4), 3, 100, 0.02, 0.9)
        cheap = Supplier("b", "import", Money.of(2.5), 21, 500, 0.08, 0.7)
        ranked = rank([local, cheap], sell_price=Money.of(15), cash_available=Money.of(2000))
        self.assertEqual(ranked[0].supplier_id, "a")

    def test_supplier_beyond_cash_is_rejected_not_zero_scored(self):
        expensive = Supplier("c", "big", Money.of(10), 5, 1000, 0.01, 0.95)
        ranked = rank([expensive], sell_price=Money.of(15), cash_available=Money.of(500))
        self.assertIsNotNone(ranked[0].rejected_reason)

    def test_landed_cost_includes_defects(self):
        supplier = Supplier("a", "local", Money.of(4), 3, 100, 0.10, 0.9)
        self.assertGreater(landed_cost(supplier, 100, Money.of(0)).minor, 400)


class UnitEconomicsTests(unittest.TestCase):
    def test_break_even_refusal_for_the_serum(self):
        sku = Sku("50ml", Money.of(18), Money.of(4.5), Money.of(2), Money.of(1))
        self.assertAlmostEqual(break_even_refusal(sku) * 100, 67.6, places=1)

    def test_refusals_reduce_but_do_not_immediately_kill_contribution(self):
        sku = Sku("50ml", Money.of(18), Money.of(4.5), Money.of(2), Money.of(1))
        self.assertGreater(contribution(sku, refusal_rate=0.2).major, 0)

    def test_cost_above_price_is_an_explicit_error(self):
        sku = Sku("x", Money.of(5), Money.of(6), Money.of(1), Money.of(1))
        with self.assertRaises(Exception):
            contribution(sku)


class ValidationTests(unittest.TestCase):
    def base(self, **kw):
        fields = dict(sku="serum-50", name_ar="سيروم", category="cosmetic",
                      price=18, weight_g=120, cost=4.5, images=["a.jpg"])
        fields.update(kw)
        return Product(**fields)

    def test_a_clean_product_is_publishable(self):
        self.assertTrue(is_publishable(self.base()))

    def test_medical_claim_word_is_flagged(self):
        problems = validate(self.base(claims_ar="يعالج التساقط"))
        self.assertTrue(problems["regulatory"])

    def test_missing_image_blocks_publication(self):
        self.assertFalse(is_publishable(self.base(images=[])))

    def test_thin_margin_is_flagged(self):
        problems = validate(self.base(price=10, cost=7))
        self.assertTrue(any("margin" in p for p in problems["pricing"]))


if __name__ == "__main__":
    unittest.main()
