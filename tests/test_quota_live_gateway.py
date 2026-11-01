import tempfile
import unittest
from pathlib import Path

from engine.live_adapters import LiveAdapterError
from engine.model_gateway import ProviderUnavailable, UsageLedger
from engine.provider import GenerationRequest, GenerationResponse
from engine.quota_manager import QuotaLedger, QuotaManager, QuotaProfile
from engine.quota_live_gateway import QuotaAwareLiveGateway


class FakeAdapter:
    def __init__(self, provider, error=None):
        self.provider = provider
        self.error = error
        self.calls = 0

    def generate(self, model, request):
        self.calls += 1
        if self.error:
            raise self.error
        return GenerationResponse(self.provider, model, "ok", 5, 7, 0.0, mocked=False)


class QuotaAwareGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.quota = QuotaLedger(root / "quota.db")
        self.usage = UsageLedger(root / "usage.db")
        self.request = GenerationRequest("research", "hello world", 20)

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, provider="groq", account="main", **changes):
        hosts = {
            "groq": ("api.groq.com/openai/v1", "groq.com/pricing",
                     "console.groq.com/docs/rate-limits", "groq.com/terms"),
            "cerebras": ("api.cerebras.ai/v1", "cerebras.ai/pricing",
                         "docs.cerebras.ai/rate-limits", "cerebras.ai/terms"),
            "gemini": ("generativelanguage.googleapis.com/v1beta",
                       "ai.google.dev/gemini-api/docs/pricing",
                       "ai.google.dev/gemini-api/docs/rate-limits",
                       "policies.google.com/terms"),
        }
        endpoint, pricing, quota, terms = hosts.get(provider, hosts["groq"])
        values = dict(provider=provider, account=account, model="model-x",
                      free_verified=True, commercial_use_allowed=True,
                      daily_request_limit=10, daily_token_limit=1000,
                      monthly_request_limit=100, monthly_token_limit=10000, priority=10,
                      endpoint_url="https://" + endpoint,
                      pricing_source_url="https://" + pricing,
                      quota_source_url="https://" + quota,
                      terms_source_url="https://" + terms,
                      verified_at=__import__("datetime").datetime(2026, 11, 1, 0,
                          tzinfo=__import__("datetime").timezone.utc),
                      max_output_tokens=512)
        values.update(changes)
        return QuotaProfile(**values)

    def test_network_disabled_by_default(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)

    def test_unverified_route_never_called(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile(free_verified=False)], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)

    def test_success_reserves_and_settles_actual_usage(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True)
        result = gateway.generate(self.request)
        self.assertEqual(result.text, "ok")
        row = manager.snapshot()[0]
        self.assertEqual(row["daily_requests_used"], 1)
        self.assertEqual(row["daily_tokens_used"], 12)

    def test_429_cools_down_and_rotates(self):
        first = self.profile()
        second = self.profile(provider="cerebras", account="backup", priority=20)
        limited = FakeAdapter("groq", LiveAdapterError("rate limited", status_code=429))
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": limited, "cerebras": backup},
                                        self.usage, True)
        result = gateway.generate(self.request)
        self.assertEqual(result.provider, "cerebras")
        self.assertTrue(self.quota.cooldown_active(first.route_id))
        self.assertEqual(limited.calls, 1)
        self.assertEqual(backup.calls, 1)


    def test_unexpected_adapter_error_stops_rotation_and_releases_tokens(self):
        class BrokenAdapter:
            def generate(self, model, request):
                raise RuntimeError("internal failure")

        first = self.profile()
        second = self.profile(provider="cerebras", account="backup", priority=20)
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": BrokenAdapter(), "cerebras": backup}, self.usage, True
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(backup.calls, 0)
        snapshot = {row["route_id"]: row for row in manager.snapshot()}
        self.assertEqual(snapshot[first.route_id]["daily_requests_used"], 1)
        self.assertEqual(
            snapshot[first.route_id]["daily_tokens_used"],
            len(self.request.prompt.encode("utf-8")) + self.request.max_output_tokens,
        )

    def test_mocked_response_is_rejected_by_live_gateway(self):
        class MockedCompletion:
            def generate(self, model, request):
                return GenerationResponse("groq", model, "mock output", 5, 7, 0.0, mocked=True)

        profile = self.profile()
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": MockedCompletion()}, self.usage, True
        )
        with self.assertRaisesRegex(ProviderUnavailable, "failed unexpectedly"):
            gateway.generate(self.request)
        row = manager.snapshot()[0]
        self.assertEqual(row["daily_requests_used"], 1)
        self.assertEqual(
            row["daily_tokens_used"],
            len(self.request.prompt.encode("utf-8")) + self.request.max_output_tokens,
        )
        self.assertEqual(self.usage.totals()["daily_usd"], 0.0)

    def test_empty_completion_is_rejected_by_live_gateway(self):
        class EmptyCompletion:
            def generate(self, model, request):
                return GenerationResponse("groq", model, "   ", 5, 0, 0.0, mocked=False)

        profile = self.profile()
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": EmptyCompletion()}, self.usage, True
        )
        with self.assertRaisesRegex(ProviderUnavailable, "failed unexpectedly"):
            gateway.generate(self.request)
        self.assertEqual(manager.snapshot()[0]["daily_requests_used"], 1)

    def test_mismatched_provider_response_is_rejected_before_accounting(self):
        class WrongProvider:
            def generate(self, model, request):
                return GenerationResponse("other-provider", model, "ok", 1, 1, 0.0, mocked=False)

        profile = self.profile()
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": WrongProvider()}, self.usage, True)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        row = manager.snapshot()[0]
        self.assertEqual(row["daily_requests_used"], 1)
        self.assertEqual(
            row["daily_tokens_used"],
            len(self.request.prompt.encode("utf-8")) + self.request.max_output_tokens,
        )

    def test_negative_tokens_and_non_finite_cost_are_rejected(self):
        for response in [
            GenerationResponse("groq", "model-x", "ok", -1, 1, 0.0, mocked=False),
            GenerationResponse("groq", "model-x", "ok", 1, 1, float("nan"), mocked=False),
        ]:
            class MalformedAdapter:
                def generate(self, model, request):
                    return response

            manager = QuotaManager([self.profile()], self.quota)
            gateway = QuotaAwareLiveGateway(manager, {"groq": MalformedAdapter()}, self.usage, True)
            with self.subTest(response=response):
                with self.assertRaises(ProviderUnavailable):
                    gateway.generate(self.request)


    def test_utf8_byte_estimate_prevents_under_reserving_unicode_prompts(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(daily_token_limit=25)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True)
        request = GenerationRequest("research", "مرحبا", 20)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(request)
        self.assertEqual(adapter.calls, 0)

    def test_response_exceeding_reserved_token_ceiling_is_recorded_and_quarantined(self):
        class OversizedAdapter:
            def generate(self, model, request):
                return GenerationResponse("groq", model, "result", 7, 21, 0.0, mocked=False)

        profile = self.profile()
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": OversizedAdapter()}, self.usage, True)
        with self.assertRaisesRegex(ProviderUnavailable, "exceeded reserved token ceilings"):
            gateway.generate(self.request)
        self.assertTrue(self.quota.cooldown_active(profile.route_id))
        self.assertEqual(manager.snapshot()[0]["daily_tokens_used"], 28)
        self.assertEqual(self.usage.totals()["daily_usd"], 0.0)

    def test_unexpected_nonzero_cost_is_recorded_and_route_quarantined(self):
        class ChargedAdapter:
            def generate(self, model, request):
                return GenerationResponse("groq", model, "result", 6, 8, 0.02, mocked=False)

        profile = self.profile()
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": ChargedAdapter()}, self.usage, True)
        with self.assertRaisesRegex(ProviderUnavailable, "non-zero cost"):
            gateway.generate(self.request)
        self.assertTrue(self.quota.cooldown_active(profile.route_id))
        self.assertEqual(manager.snapshot()[0]["daily_tokens_used"], 14)
        self.assertEqual(self.usage.totals()["daily_usd"], 0.02)

    def test_request_above_profile_output_ceiling_never_calls_provider(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(max_output_tokens=10)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(manager.snapshot()[0]["daily_requests_used"], 0)

    def test_monthly_caps_are_reserved_before_provider_call(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(monthly_request_limit=1, monthly_token_limit=100)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True)
        gateway.generate(self.request)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 1)
        row = manager.snapshot()[0]
        self.assertEqual(row["monthly_requests_used"], 1)

    def test_no_paid_or_unknown_quota_fallback(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile(daily_token_limit=None)], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
