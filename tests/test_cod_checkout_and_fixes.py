"""Tests for COD accounting fixes, multi-store checkout failover, compose backdrop, and WebP quality."""
from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from engine import raawaa_quality as quality
from engine.commerce.cod_checkout import (
    CatalogItem,
    CheckoutError,
    CheckoutLineInput,
    CheckoutRequest,
    DeliveryZone,
    FailoverOrderSink,
    HttpEndpointSink,
    LocalJsonlSink,
    SinkUnavailable,
    StoreCatalog,
    prepare_checkout,
)
from engine.commerce.delivery import (
    DeliveryBook,
    DeliveryRate,
    Order as DeliveryOrder,
    break_even_refusal_rate,
)
from engine.commerce.money import Money
from engine.commerce.unit_economics import Sku, break_even_refusal, contribution
from engine.image_providers import CloudflareFlux
from scripts import raawaa_compose as compose


def _solid_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _vp8x_webp(path: Path, width: int, height: int) -> None:
    w_minus_1 = (width - 1).to_bytes(3, "little")
    h_minus_1 = (height - 1).to_bytes(3, "little")
    vp8x_payload = b"\x00\x00\x00\x00" + w_minus_1 + h_minus_1
    riff_body = b"WEBP" + b"VP8X" + struct.pack("<I", len(vp8x_payload)) + vp8x_payload
    path.write_bytes(b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body)


class CodDeliveryAccountingFixTests(unittest.TestCase):
    def setUp(self):
        self.rate = DeliveryRate(fee=Money.of(2), refusal_fee=Money.of(1))

    def test_refused_cod_order_never_counts_uncollected_revenue(self):
        refused = DeliveryOrder(
            order_id="ref-1",
            revenue=Money.of(18),
            goods_cost=Money.of(4.5),
            delivery=self.rate,
            delivered=False,
        )
        # Total write-off default: -(4.5 + 1.0) = -5.50, never +12.50!
        self.assertAlmostEqual(refused.contribution().major, -5.50, places=2)

    def test_intact_returned_goods_only_lose_refusal_fee_and_repack(self):
        refused_intact = DeliveryOrder(
            order_id="ref-2",
            revenue=Money.of(40),
            goods_cost=Money.of(22),
            delivery=self.rate,
            delivered=False,
            spoilage_rate=0.0,
            repack_cost=Money.of("0.25"),
        )
        self.assertAlmostEqual(refused_intact.contribution().major, -1.25, places=2)

    def test_delivery_book_net_contribution_reflects_refusal_loss(self):
        book = DeliveryBook()
        book.add(DeliveryOrder("1", Money.of(18), Money.of(4.5), self.rate,
                               delivered=True, cod_collected=Money.of(18)))
        book.add(DeliveryOrder("2", Money.of(18), Money.of(4.5), self.rate,
                               delivered=False))
        # +11.50 + (-5.50) = +6.00
        self.assertAlmostEqual(book.report()["contribution"], 6.00, places=2)

    def test_reusable_stock_raises_break_even_refusal_ceiling(self):
        writeoff_be = break_even_refusal_rate(Money.of(40), Money.of(22), self.rate, spoilage_rate=1.0)
        intact_be = break_even_refusal_rate(
            Money.of(40), Money.of(22), self.rate, spoilage_rate=0.1, repack_cost=Money.of("0.25")
        )
        self.assertGreater(intact_be, writeoff_be)

    def test_unit_economics_supports_reusable_return_stock(self):
        writeoff_sku = Sku("appliance", Money.of(40), Money.of(22), Money.of(2), Money.of(1),
                           return_spoilage_rate=1.0)
        reusable_sku = Sku("appliance", Money.of(40), Money.of(22), Money.of(2), Money.of(1),
                           return_spoilage_rate=0.10, repack_cost=Money.of("0.25"))
        self.assertGreater(
            contribution(reusable_sku, refusal_rate=0.25).minor,
            contribution(writeoff_sku, refusal_rate=0.25).minor,
        )
        self.assertGreater(break_even_refusal(reusable_sku), break_even_refusal(writeoff_sku))


class ImageProviderCredentialAliasTests(unittest.TestCase):
    def test_cloudflare_flux_accepts_role_aliases_from_credentials_config(self):
        provider = CloudflareFlux(env={
            "CF_ACCOUNT_ID": "acc_alias_123",
            "CLOUDFLARE_ACCESS_AUDIT_TOKEN": "tok_alias_456",
        })
        ok, _ = provider.available()
        self.assertTrue(ok)
        self.assertIn("acc_alias_123", provider._url())


class ComposeAndQualityFixesTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("convert"), "ImageMagick not installed")
    def test_backdrop_composites_product_instead_of_discarding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fg = root / "bottle.png"
            bg = root / "linen.png"
            # Pure dark foreground over bright backdrop -> center pixel must be dark!
            _solid_png(fg, 600, 800, (20, 25, 30))
            _solid_png(bg, 1200, 1500, (240, 235, 225))
            report = compose.compose(fg, root / "out", backdrop=str(bg))
            self.assertTrue(report["all_ok"], report)
            self.assertIn("backdrop_composited", report["notes"])
            feed = root / "out" / "bottle__feed_4x5.jpg"
            import subprocess
            center_hex = subprocess.check_output(
                ["convert", str(feed), "-crop", "1x1+540+675", "+repage", "txt:-"],
                text=True,
            )
            # If fg had been discarded as before, center would be near #F0EBE1 (240,235,225).
            self.assertIn("srgb(", center_hex)
            self.assertFalse("240,235,225" in center_hex or "#F0EBE1" in center_hex.upper())

    def test_webp_and_3x4_grid_ratio_pass_quality_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            webp_path = Path(tmp) / "card.webp"
            _vp8x_webp(webp_path, 1080, 1440)
            info = quality.inspect(webp_path)
            self.assertEqual(info.fmt, "webp")
            self.assertEqual((info.width, info.height), (1080, 1440))
            self.assertEqual(quality.assess(info), [])

    def test_mean_luma_bounds_are_actually_enforced_when_measured(self):
        dark = quality.ImageInfo("x.jpg", 1080, 1350, "jpeg", 100_000, mean_luma=18.0)
        bright = quality.ImageInfo("y.jpg", 1080, 1350, "jpeg", 100_000, mean_luma=248.0)
        self.assertTrue(any(p.startswith("too_dark:") for p in quality.assess(dark)))
        self.assertTrue(any(p.startswith("too_bright:") for p in quality.assess(bright)))


class MultiStoreCodCheckoutTests(unittest.TestCase):
    def _catalog(self, store_id: str = "raawaa") -> StoreCatalog:
        return StoreCatalog(
            store_id=store_id,
            brand_ar="رواء" if store_id == "raawaa" else "سلاف",
            whatsapp_e164="963997310856",
            items={
                "50ml": CatalogItem("50ml", "سيروم ٥٠ مل", Money.of(18), free_delivery=True),
                "gadget-1": CatalogItem("gadget-1", "جهاز عناية", Money.of(24), free_delivery=False),
            },
            zones={
                "hama": DeliveryZone("hama", "حماة", Money.of(2)),
                "damascus": DeliveryZone("damascus", "دمشق", Money.of(4)),
            },
            free_shipping_from=Money.of(32),
        )

    def test_product_agnostic_checkout_computes_server_totals_and_wa_link(self):
        order = prepare_checkout(
            self._catalog("sulaf"),
            CheckoutRequest(
                customer_name="ندى الأحمد",
                customer_phone="٠٩٩٩١٢٣٤٥٦",
                zone_id="damascus",
                lines=[CheckoutLineInput("gadget-1", 1)],
                address_detail="المزة فيلات غربية قرب صيدلية الشام",
                payment_method="syriatel_cash",
                wallet_transfer_ref="SYR-88412",
            ),
            primary_zone_id="hama",
            day="2026-11-06",
        )
        self.assertEqual(order.store_id, "sulaf")
        self.assertTrue(order.order_ref.startswith("SUL-"))
        self.assertEqual(order.customer_phone, "0999123456")
        self.assertEqual(order.subtotal.major, 24.0)
        self.assertEqual(order.delivery_fee.major, 4.0)
        self.assertEqual(order.total.major, 28.0)
        self.assertEqual(order.rto_risk.level, "low")
        self.assertTrue(order.whatsapp_url.startswith("https://wa.me/963997310856?text="))
        self.assertIn("SYR-88412", order.whatsapp_message_ar)
        self.assertIn("ختم الأمان", order.inspection_policy_ar)

    def test_high_risk_cod_flags_address_and_whatsapp_confirmation_gate(self):
        order = prepare_checkout(
            self._catalog("raawaa"),
            CheckoutRequest(
                customer_name="ريم",
                customer_phone="+963 991 222 333",
                zone_id="damascus",
                lines=[CheckoutLineInput("gadget-1", 2)],
                address_detail="دمشق",  # too vague
                payment_method="cod",
            ),
            primary_zone_id="hama",
            day="2026-11-06",
        )
        # 2 * $24 = $48 >= $32 threshold -> free shipping, but high COD RTO risk
        self.assertEqual(order.delivery_fee.major, 0.0)
        self.assertEqual(order.rto_risk.level, "high")
        self.assertEqual(order.rto_risk.required_gate, "address_landmark_and_confirm")

    def test_failover_sink_survives_primary_outage_and_deduplicates(self):
        class BrokenSink:
            name = "cloudflare_edge"
            def available(self): return True, "configured"
            def write(self, order): raise SinkUnavailable("ISP reset on mobile carrier")

        with tempfile.TemporaryDirectory() as tmp:
            outbox = LocalJsonlSink(Path(tmp) / "orders.jsonl")
            unconfigured_supabase = HttpEndpointSink("supabase_edge", "")
            chain = FailoverOrderSink([BrokenSink(), unconfigured_supabase, outbox])
            order = prepare_checkout(
                self._catalog(),
                CheckoutRequest("سارة", "0999123456", "hama", [CheckoutLineInput("50ml", 1)],
                                address_detail="حي الحاضر جانب الساعة"),
                day="2026-11-06",
            )
            first = chain.submit(order)
            self.assertTrue(first["saved"])
            self.assertEqual(first["served_by"], "local_jsonl")
            self.assertEqual(first["attempts"][0]["outcome"], "failed")
            self.assertEqual(first["attempts"][1]["outcome"], "unavailable")

            second = chain.submit(order)
            self.assertTrue(second["receipt"].startswith("duplicate:"))

    def test_all_sinks_failing_still_returns_whatsapp_client_rescue(self):
        class DeadSink:
            name = "dead"
            def available(self): return True, "up"
            def write(self, order): raise RuntimeError("503")

        order = prepare_checkout(
            self._catalog(),
            CheckoutRequest("سارة", "0999123456", "hama", [CheckoutLineInput("50ml", 1)]),
            day="2026-11-06",
        )
        res = FailoverOrderSink([DeadSink()]).submit(order)
        self.assertFalse(res["saved"])
        self.assertEqual(res["served_by"], "whatsapp_client_rescue")
        self.assertTrue(res["whatsapp_rescue_url"].startswith("https://wa.me/"))

    def test_invalid_phone_rejected_cleanly(self):
        with self.assertRaises(CheckoutError):
            prepare_checkout(
                self._catalog(),
                CheckoutRequest("سارة", "12345", "hama", [CheckoutLineInput("50ml", 1)]),
            )


if __name__ == "__main__":
    unittest.main()
