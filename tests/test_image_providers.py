"""Image provider chain: failover, cost policy, and budget enforcement."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.image_providers import (  # noqa: E402
    BudgetExhausted, CloudflareFlux, ImageChain, ImageRequest, ImageProviderError,
    OfflineGradient, ProviderUnavailable, chain_from_config, encode_png,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class StubProvider:
    def __init__(self, name, *, fail=None, available=True, neurons=0.0):
        self.name = name
        self.model = f"{name}-model"
        self.cost_usd_per_image = 0.0
        self._fail = fail
        self._available = available
        self._neurons = neurons
        self.calls = 0

    def available(self):
        return self._available, "stub" if self._available else "offline on purpose"

    def generate(self, request):
        self.calls += 1
        if self._fail:
            raise self._fail
        from engine.image_providers import ImageResult
        return ImageResult(provider=self.name, model=self.model,
                           png_bytes=PNG_MAGIC + b"data", width=request.width,
                           height=request.height, neurons=self._neurons)


class EncodingTests(unittest.TestCase):
    def test_png_is_valid_and_sized(self):
        png = encode_png(8, 8, bytes([120, 100, 80] * 64))
        self.assertTrue(png.startswith(PNG_MAGIC))
        self.assertIn(b"IHDR", png)

    def test_wrong_pixel_count_is_refused(self):
        with self.assertRaises(ValueError):
            encode_png(4, 4, b"too short")


class CostPolicyTests(unittest.TestCase):
    def test_a_priced_provider_cannot_enter_the_chain(self):
        paid = StubProvider("paid")
        paid.cost_usd_per_image = 0.04
        with self.assertRaises(ValueError):
            ImageChain([paid])

    def test_unknown_price_is_not_treated_as_free(self):
        # A provider whose price cannot be established must not be assumed zero.
        paid = StubProvider("mystery")
        paid.cost_usd_per_image = -1  # sentinel for "unknown"
        with self.assertRaises(ValueError):
            ImageChain([paid])


class CloudflareCostModelTests(unittest.TestCase):
    def test_neuron_formula_matches_published_rates(self):
        self.assertAlmostEqual(CloudflareFlux.neurons_for(1024, 1024, 4), 57.6, places=2)

    def test_images_per_day_matches_the_published_ballpark(self):
        provider = CloudflareFlux(env={})
        self.assertEqual(provider.images_per_day(1024, 1024, 4), 173)

    def test_missing_credentials_is_reported_not_guessed(self):
        ok, reason = CloudflareFlux(env={}).available()
        self.assertFalse(ok)
        self.assertIn("CLOUDFLARE_ACCOUNT_ID", reason)

    def test_generate_without_credentials_raises_unavailable(self):
        with self.assertRaises(ProviderUnavailable):
            CloudflareFlux(env={}).generate(ImageRequest(prompt="x"))


class FailoverTests(unittest.TestCase):
    def test_first_failure_falls_through_to_the_next(self):
        first = StubProvider("a", fail=ImageProviderError("boom"))
        second = StubProvider("b")
        chain = ImageChain([first, second])
        result, meta = chain.generate(ImageRequest(prompt="hello"))
        self.assertEqual(result.provider, "b")
        self.assertEqual(meta["attempts"][0]["outcome"], "failed")

    def test_unavailable_provider_is_skipped_with_a_reason(self):
        skipped = StubProvider("a", available=False)
        chain = ImageChain([skipped, StubProvider("b")])
        result, meta = chain.generate(ImageRequest(prompt="hello"))
        self.assertEqual(result.provider, "b")
        self.assertEqual(meta["attempts"][0]["outcome"], "unavailable")
        self.assertEqual(skipped.calls, 0)

    def test_all_failing_reports_every_reason(self):
        chain = ImageChain([StubProvider("a", fail=ImageProviderError("x")),
                            StubProvider("b", fail=BudgetExhausted("y"))])
        with self.assertRaises(ImageProviderError) as ctx:
            chain.generate(ImageRequest(prompt="hello"))
        message = str(ctx.exception)
        self.assertIn("a=failed", message)
        self.assertIn("b=failed", message)

    def test_offline_fallback_always_serves(self):
        chain = ImageChain([OfflineGradient()])
        result, _ = chain.generate(ImageRequest(prompt="layout", width=64, height=64))
        self.assertTrue(result.png_bytes.startswith(PNG_MAGIC))


class BudgetTests(unittest.TestCase):
    def test_daily_cap_blocks_further_cloudflare_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            cf = CloudflareFlux(env={"CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "b"})
            ledger = Path(tmp) / "ledger.jsonl"
            chain = ImageChain([cf, StubProvider("fallback")], ledger_path=ledger,
                               daily_neuron_cap=10.0)
            result, meta = chain.generate(ImageRequest(prompt="hi", width=512, height=512, steps=1))
            self.assertEqual(result.provider, "fallback")
            self.assertEqual(meta["attempts"][0]["outcome"], "budget")

    def test_ledger_persists_and_accumulates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            chain = ImageChain([StubProvider("a", neurons=25.0)], ledger_path=ledger)
            chain.generate(ImageRequest(prompt="one"))
            chain.generate(ImageRequest(prompt="two"))
            self.assertAlmostEqual(chain.neurons_used_today(), 50.0)
            self.assertAlmostEqual(ImageChain([StubProvider("a")], ledger_path=ledger)
                                   .neurons_used_today(), 50.0)


class ConfigTests(unittest.TestCase):
    def test_repository_config_builds_a_chain(self):
        config = json.loads((ROOT / "config" / "image_providers.json").read_text(encoding="utf-8"))
        chain = chain_from_config(config, env={})
        names = [p.name for p in chain.providers]
        self.assertEqual(names, ["cloudflare", "local_qwen", "offline"])

    def test_repository_config_uses_only_zero_cost_routes(self):
        config = json.loads((ROOT / "config" / "image_providers.json").read_text(encoding="utf-8"))
        for provider in config["providers"]:
            self.assertEqual(provider["cost_usd_per_image"], 0, provider["id"])

    def test_unknown_provider_id_is_refused(self):
        with self.assertRaises(ValueError):
            chain_from_config({"providers": [{"id": "nope"}]})


class RequestValidationTests(unittest.TestCase):
    def test_empty_prompt_is_refused(self):
        with self.assertRaises(ValueError):
            ImageRequest(prompt="  ").validate()

    def test_non_multiple_of_eight_is_refused(self):
        with self.assertRaises(ValueError):
            ImageRequest(prompt="x", width=1001).validate()


if __name__ == "__main__":
    unittest.main()


class CloudflarePayloadSchemaTests(unittest.TestCase):
    """The hosted endpoint rejects unsupported fields; guard the exact shape."""

    def test_payload_has_no_width_or_height(self):
        captured = {}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                from engine.image_providers import encode_png
                return encode_png(8, 8, bytes([200, 190, 180] * 64))

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode())
            return FakeResponse()

        provider = CloudflareFlux(env={"CLOUDFLARE_ACCOUNT_ID": "a",
                                       "CLOUDFLARE_API_TOKEN": "b"})
        with mock.patch("engine.image_providers.urllib.request.urlopen", fake_urlopen):
            provider.generate(ImageRequest(prompt="x", width=512, height=512, steps=4))

        self.assertEqual(set(captured["body"]), {"prompt", "steps"})
        self.assertEqual(captured["body"]["steps"], 4)

    def test_result_size_is_read_from_the_returned_image(self):
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                from engine.image_providers import encode_png
                return encode_png(32, 32, bytes([10, 20, 30] * 1024))

        def fake_urlopen(request, timeout=None):
            return FakeResponse()

        provider = CloudflareFlux(env={"CLOUDFLARE_ACCOUNT_ID": "a",
                                       "CLOUDFLARE_API_TOKEN": "b"})
        with mock.patch("engine.image_providers.urllib.request.urlopen", fake_urlopen):
            result = provider.generate(ImageRequest(prompt="x", width=1024, height=1024))
        self.assertEqual((result.width, result.height), (32, 32))
        self.assertTrue(any("does not accept explicit dimensions" in n for n in result.notes))
