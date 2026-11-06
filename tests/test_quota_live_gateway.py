import os
import tempfile
import unittest
from pathlib import Path

from engine.live_adapters import LiveAdapterError
from engine.model_gateway import ProviderUnavailable, UsageLedger
from engine.provider import GenerationRequest, GenerationResponse
from engine.quota_manager import QuotaLedger, QuotaManager, QuotaProfile
from engine.quota_live_gateway import QuotaAwareLiveGateway


class FakeAdapter:
    def __init__(self, provider, error=None, cost_usd=0.0):
        self.provider = provider
        self.error = error
        self.cost_usd = cost_usd
        self.calls = 0

    def generate(self, model, request):
        self.calls += 1
        if self.error:
            raise self.error
        return GenerationResponse(self.provider, model, "ok", 5, 7, self.cost_usd, mocked=False)


class FakeInvocationStore:
    def __init__(self):
        self.calls = []
        self.prepared = {
            "invocation_id": "00000000-0000-4000-8000-000000000099",
            "request_id": "00000000-0000-4000-8000-000000000098",
            "step_key": "research:primary",
            "status": "prepared",
        }

    def prepare(self, task_id, lease_token, step_key, task_kind):
        self.calls.append(("prepare", task_id, lease_token, step_key, task_kind))
        return dict(self.prepared)

    def start(self, task_id, lease_token, invocation_id, route_id):
        self.calls.append(("start", task_id, lease_token, invocation_id, route_id))

    def reject(self, task_id, lease_token, invocation_id, route_id, rejection_code):
        self.calls.append(("reject", task_id, lease_token, invocation_id, route_id, rejection_code))

    def mark_ambiguous(self, task_id, lease_token, invocation_id):
        self.calls.append(("ambiguous", task_id, lease_token, invocation_id))

    def settle(self, task_id, lease_token, invocation_id):
        self.calls.append(("settle", task_id, lease_token, invocation_id))


class QuotaAwareGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.quota = QuotaLedger(root / "quota.db")
        self.usage = UsageLedger(root / "usage.db")
        self.request = GenerationRequest("research", "hello world", 20, task_id="task-1", request_id="00000000-0000-4000-8000-000000000010")
        self._previous_kill_switch = os.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE")
        os.environ["RAAWAA_ENGINE_LIVE_INFERENCE"] = "enabled"

    def tearDown(self):
        if self._previous_kill_switch is None:
            os.environ.pop("RAAWAA_ENGINE_LIVE_INFERENCE", None)
        else:
            os.environ["RAAWAA_ENGINE_LIVE_INFERENCE"] = self._previous_kill_switch
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
                      free_verified=True, commercial_use_allowed=True, production_use_allowed=True, enabled=True,
                      minute_request_limit=30, minute_token_limit=8000,
                      daily_request_limit=10, daily_token_limit=1000,
                      monthly_request_limit=100, monthly_token_limit=10000,
                      per_task_request_limit=5, per_task_token_limit=5000, priority=10,
                      endpoint_url="https://" + endpoint,
                      pricing_source_url="https://" + pricing,
                      quota_source_url="https://" + quota,
                      terms_source_url="https://" + terms,
                      verified_at=__import__("datetime").datetime(2026, 11, 1, 0,
                          tzinfo=__import__("datetime").timezone.utc),
                      max_output_tokens=512,
                      input_cost_per_million_usd=0.0,
                      output_cost_per_million_usd=0.0)
        values.update(changes)
        return QuotaProfile(**values)

    def test_preflight_reports_capacity_without_reserving_or_calling_provider(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        result = gateway.preflight_capacity(max_output_tokens=256)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "verified_free_route_available")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(manager.snapshot()[0]["daily_requests_used"], 0)
        self.assertEqual(self.usage.totals()["pending_reservations"], 0)

    def test_preflight_detects_exhausted_quota_and_cooldown(self):
        profile = self.profile(minute_request_limit=1)
        adapter = FakeAdapter("groq")
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.quota.record(profile.route_id, 1)
        result = gateway.preflight_capacity(max_output_tokens=256)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "provider_capacity_unavailable")
        self.quota.cool_down(profile.route_id, 900, "test")
        self.assertEqual(gateway.preflight_capacity()["reason"], "provider_capacity_unavailable")
        self.assertEqual(adapter.calls, 0)

    def test_preflight_detects_global_monetary_budget_exhaustion(self):
        adapter = FakeAdapter("groq")
        self.usage.record("groq", "model-x", "research", 1, 1, 0.01, task_id="previous-task")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            daily_budget_usd=0.0, monthly_budget_usd=1.0,
        )
        result = gateway.preflight_capacity()
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "monetary_budget_exhausted")
        self.assertEqual(adapter.calls, 0)

    def test_preflight_fails_closed_when_live_switch_is_off(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True, environ={},
        )
        result = gateway.preflight_capacity()
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "live_inference_disabled")
        self.assertEqual(adapter.calls, 0)

    def test_checkpoint_prepare_failure_is_never_reclassified_as_safe_retry(self):
        class BrokenCheckpoint:
            def prepare(self, *args):
                raise TimeoutError("checkpoint response lost")

        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            invocation_store=BrokenCheckpoint(),
        )
        request = GenerationRequest(
            "research", "hello", 20, task_id="task-1",
            step_key="research:primary", lease_token="lease-1",
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(request)
        self.assertEqual(adapter.calls, 0)

    def test_missing_task_id_blocks_before_provider_call(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        request = GenerationRequest("research", "hello world", 20)
        with self.assertRaisesRegex(ProviderUnavailable, "task ID is required"):
            gateway.generate(request)
        self.assertEqual(adapter.calls, 0)

    def test_per_task_quota_blocks_before_provider_call(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(per_task_request_limit=1, per_task_token_limit=1000)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        gateway.generate(self.request)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.quota.usage_for_task("task-1")["requests"], 1)

    def test_operator_kill_switch_required_even_when_programmatic_flag_is_true(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage,
            network_calls_enabled=True, environ={},
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.quota.audit_events()[0]["event"], "live_request_blocked")

    def test_network_disabled_by_default(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)
        events = self.quota.audit_events()
        self.assertEqual(events[0]["event"], "live_request_blocked")
        self.assertEqual(events[0]["details"], {"reason": "kill_switch_off"})

    def test_operator_kill_switch_required_even_when_programmatic_flag_is_true(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage,
                                        network_calls_enabled=True, environ={})
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.quota.audit_events()[0]["event"], "live_request_blocked")

    def test_unknown_or_nonzero_price_never_calls_provider(self):
        for changes in (
            {"input_cost_per_million_usd": None},
            {"output_cost_per_million_usd": None},
            {"input_cost_per_million_usd": 0.01},
            {"output_cost_per_million_usd": 0.01},
        ):
            with self.subTest(changes=changes):
                adapter = FakeAdapter("groq")
                manager = QuotaManager([self.profile(**changes)], self.quota)
                gateway = QuotaAwareLiveGateway(
                    manager, {"groq": adapter}, self.usage, True,
                    environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
                )
                with self.assertRaises(ProviderUnavailable):
                    gateway.generate(self.request)
                self.assertEqual(adapter.calls, 0)


    def test_preview_only_profile_is_blocked_before_provider_call(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(production_use_allowed=False)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)

    def test_unverified_route_never_called(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile(free_verified=False)], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)

    def test_durable_invocation_store_supplies_request_identity_and_settles(self):
        adapter = FakeAdapter("groq")
        invocation_store = FakeInvocationStore()
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            invocation_store=invocation_store,
        )
        request = GenerationRequest(
            "research", "hello world", 20, task_id="task-1",
            step_key="research:primary", lease_token="lease-1",
        )
        result = gateway.generate(request)
        self.assertEqual(result.text, "ok")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(invocation_store.calls[0][0], "prepare")
        self.assertEqual(invocation_store.calls[1][0], "start")
        self.assertEqual(invocation_store.calls[1][3], invocation_store.prepared["invocation_id"])
        self.assertEqual(invocation_store.calls[-1][0], "settle")

    def test_durable_invocation_store_reopens_only_after_explicit_429(self):
        first = self.profile()
        second = self.profile(provider="cerebras", account="backup", priority=20)
        invocation_store = FakeInvocationStore()
        limited = FakeAdapter("groq", LiveAdapterError("rate limited", status_code=429))
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": limited, "cerebras": backup}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            invocation_store=invocation_store,
        )
        request = GenerationRequest(
            "research", "hello world", 20, task_id="task-1",
            step_key="research:primary", lease_token="lease-1",
        )
        gateway.generate(request)
        actions = [call[0] for call in invocation_store.calls]
        self.assertEqual(actions, ["prepare", "start", "reject", "start", "settle"])
        self.assertEqual(limited.calls, 1)
        self.assertEqual(backup.calls, 1)

    def test_unexpected_adapter_error_records_safe_failure_stage(self):
        adapter = FakeAdapter("groq", ValueError("invalid provider response"))
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        event = next(item for item in self.quota.audit_events() if item["event"] == "adapter_failure")
        details = event["details"]
        self.assertEqual(details["failure_stage"], "adapter_generate")
        self.assertEqual(details["error_type"], "ValueError")
        self.assertNotIn("invalid provider response", str(details))
        self.assertEqual(adapter.calls, 1)

    def test_durable_invocation_store_marks_timeout_ambiguous_and_never_retries(self):
        first = self.profile()
        second = self.profile(provider="cerebras", account="backup", priority=20)
        invocation_store = FakeInvocationStore()
        timed_out = FakeAdapter("groq", LiveAdapterError("request timed out"))
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": timed_out, "cerebras": backup}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            invocation_store=invocation_store,
        )
        request = GenerationRequest(
            "research", "hello world", 20, task_id="task-1",
            step_key="research:primary", lease_token="lease-1",
        )
        with self.assertRaisesRegex(ProviderUnavailable, "route rotation stopped"):
            gateway.generate(request)
        self.assertEqual(timed_out.calls, 1)
        self.assertEqual(backup.calls, 0)
        self.assertEqual([call[0] for call in invocation_store.calls],
                         ["prepare", "start", "ambiguous"])

    def test_durable_invocation_store_blocks_without_step_or_lease(self):
        adapter = FakeAdapter("groq")
        invocation_store = FakeInvocationStore()
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            invocation_store=invocation_store,
        )
        with self.assertRaisesRegex(ProviderUnavailable, "step_key"):
            gateway.generate(GenerationRequest("research", "prompt", 20, task_id="task-1"))
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(invocation_store.calls, [])

    def test_success_reserves_and_settles_actual_usage(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        result = gateway.generate(self.request)
        self.assertEqual(result.text, "ok")
        row = manager.snapshot()[0]
        self.assertEqual(row["daily_requests_used"], 1)
        self.assertEqual(row["daily_tokens_used"], 12)
        self.assertEqual(self.usage.totals()["pending_reservations"], 0)
        self.assertEqual(self.usage.totals()["daily_reserved_usd"], 0.0)
        events = self.quota.audit_events()
        self.assertEqual({event["event"] for event in events},
                         {"quota_reserved", "completion_received"})
        self.assertNotIn("hello world", str(events))
        self.assertNotIn('"ok"', str(events))

    def test_missing_provider_billing_uses_verified_profile_estimate(self):
        adapter = FakeAdapter("groq", cost_usd=None)
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        result = gateway.generate(self.request)
        self.assertIsNone(result.cost_usd)
        completion = next(
            event for event in self.quota.audit_events()
            if event["event"] == "completion_received"
        )
        self.assertEqual(completion["details"]["cost_basis"], "verified_profile_estimate")
        self.assertEqual(completion["details"]["cost_usd"], 0.0)
        self.assertEqual(self.usage.totals()["daily_usd"], 0.0)

    def test_provider_reported_nonzero_cost_is_recorded_and_quarantines_free_route(self):
        profile = self.profile()
        adapter = FakeAdapter("groq", cost_usd=0.0001)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaisesRegex(ProviderUnavailable, "non-zero cost"):
            gateway.generate(self.request)
        self.assertAlmostEqual(self.usage.totals()["daily_usd"], 0.0001)
        self.assertTrue(self.quota.cooldown_active(profile.route_id))
        completion = next(
            event for event in self.quota.audit_events()
            if event["event"] == "completion_received"
        )
        self.assertEqual(completion["details"]["cost_basis"], "provider_reported")

    def test_boolean_provider_cost_is_rejected_as_invalid(self):
        adapter = FakeAdapter("groq", cost_usd=True)
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaisesRegex(ProviderUnavailable, "failed unexpectedly"):
            gateway.generate(self.request)
        self.assertEqual(self.usage.totals()["daily_usd"], 0.0)

    def test_429_cools_down_and_rotates(self):
        first = self.profile()
        second = self.profile(provider="cerebras", account="backup", priority=20)
        limited = FakeAdapter("groq", LiveAdapterError("rate limited", status_code=429))
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": limited, "cerebras": backup},
                                        self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        result = gateway.generate(self.request)
        self.assertEqual(result.provider, "cerebras")
        self.assertTrue(self.quota.cooldown_active(first.route_id))
        self.assertEqual(limited.calls, 1)
        self.assertEqual(backup.calls, 1)
        self.assertEqual(self.usage.totals()["pending_reservations"], 0)


    def test_timeout_does_not_rotate_to_another_provider(self):
        first = self.profile()
        second = self.profile(provider="cerebras", account="backup", priority=20)
        timed_out = FakeAdapter("groq", LiveAdapterError("request timed out"))
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": timed_out, "cerebras": backup}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaisesRegex(ProviderUnavailable, "route rotation stopped"):
            gateway.generate(self.request)
        self.assertEqual(timed_out.calls, 1)
        self.assertEqual(backup.calls, 0)
        self.assertEqual(self.quota.usage_for_task("task-1")["requests"], 1)
        self.assertEqual(self.usage.totals()["pending_reservations"], 1)
        events = self.quota.audit_events()
        self.assertIn("provider_failure_stopped", {event["event"] for event in events})

    def test_model_permission_error_code_rotates_and_is_audited(self):
        first = self.profile(model="openai/gpt-oss-120b", priority=10)
        second = self.profile(model="openai/gpt-oss-20b", priority=20)
        denied = FakeAdapter(
            "groq",
            LiveAdapterError(
                "Provider returned HTTP 403",
                status_code=403,
                error_code="model_permission_blocked_org",
            ),
        )
        fallback = FakeAdapter("groq")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {first.route_id: denied, second.route_id: fallback},
            self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        result = gateway.generate(self.request)
        self.assertEqual(result.model, second.model)
        self.assertEqual(denied.calls, 1)
        self.assertEqual(fallback.calls, 1)
        self.assertTrue(self.quota.cooldown_active(first.route_id))
        self.assertEqual(self.usage.totals()["daily_usd"], 0.0)
        self.assertEqual(self.usage.totals()["pending_reservations"], 0)
        events = self.quota.audit_events()
        provider_error = next(event for event in events if event["event"] == "provider_error")
        self.assertEqual(provider_error["details"]["error_code"], "model_permission_blocked_org")
        denied_event = next(event for event in events if event["event"] == "provider_route_denied")
        self.assertEqual(denied_event["details"]["error_code"], "model_permission_blocked_org")
        self.assertNotIn("Provider returned HTTP 403", str(events))

    def test_unclassified_403_remains_ambiguous_and_stops_rotation(self):
        first = self.profile(model="openai/gpt-oss-120b", priority=10)
        second = self.profile(provider="cerebras", account="backup", priority=20)
        denied = FakeAdapter("groq", LiveAdapterError("forbidden", status_code=403))
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {first.route_id: denied, second.route_id: backup},
            self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaisesRegex(ProviderUnavailable, "route rotation stopped"):
            gateway.generate(self.request)
        self.assertEqual(denied.calls, 1)
        self.assertEqual(backup.calls, 0)
        self.assertEqual(self.usage.totals()["pending_reservations"], 1)

    def test_unexpected_adapter_error_stops_rotation_and_retains_reserved_quota(self):
        class BrokenAdapter:
            def generate(self, model, request):
                raise RuntimeError("internal failure")

        first = self.profile()
        second = self.profile(provider="cerebras", account="backup", priority=20)
        backup = FakeAdapter("cerebras")
        manager = QuotaManager([first, second], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": BrokenAdapter(), "cerebras": backup}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}
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
            manager, {"groq": MockedCompletion()}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}
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
            manager, {"groq": EmptyCompletion()}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}
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
        gateway = QuotaAwareLiveGateway(manager, {"groq": WrongProvider()}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
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
            gateway = QuotaAwareLiveGateway(manager, {"groq": MalformedAdapter()}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
            with self.subTest(response=response):
                with self.assertRaises(ProviderUnavailable):
                    gateway.generate(self.request)


    def test_utf8_byte_estimate_prevents_under_reserving_unicode_prompts(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(daily_token_limit=25)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
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
        gateway = QuotaAwareLiveGateway(manager, {"groq": OversizedAdapter()}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        with self.assertRaisesRegex(ProviderUnavailable, "exceeded reserved token ceilings"):
            gateway.generate(self.request)
        self.assertTrue(self.quota.cooldown_active(profile.route_id))
        self.assertEqual(manager.snapshot()[0]["daily_tokens_used"], 28)
        self.assertEqual(self.usage.totals()["daily_usd"], 0.0)

    def test_unexpected_nonzero_cost_is_recorded_and_route_quarantined(self):
        class ChargedAdapter:
            def __init__(self):
                self.calls = 0

            def generate(self, model, request):
                self.calls += 1
                return GenerationResponse("groq", model, "result", 6, 8, 0.02, mocked=False)

        charged = ChargedAdapter()
        backup = FakeAdapter("cerebras")
        profile = self.profile()
        backup_profile = self.profile(provider="cerebras", account="backup", priority=20)
        manager = QuotaManager([profile, backup_profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": charged, "cerebras": backup}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaisesRegex(ProviderUnavailable, "non-zero cost"):
            gateway.generate(self.request)
        self.assertTrue(self.quota.cooldown_active(profile.route_id))
        self.assertEqual(manager.snapshot()[0]["daily_tokens_used"], 14)
        self.assertEqual(self.usage.totals()["daily_usd"], 0.02)
        # A charge on one supposedly-free route blocks every subsequent zero-budget route.
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(charged.calls, 1)
        self.assertEqual(backup.calls, 0)

    def test_per_task_spend_cap_blocks_other_routes_before_provider_call(self):
        adapter = FakeAdapter("cerebras")
        self.usage.record(
            "groq", "model-x", "research", 10, 5, 0.02, task_id="task-1"
        )
        manager = QuotaManager(
            [self.profile(provider="cerebras", account="backup")], self.quota
        )
        gateway = QuotaAwareLiveGateway(
            manager, {"cerebras": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            daily_budget_usd=1.0, monthly_budget_usd=1.0,
            per_task_budget_usd=0.01,
        )
        with self.assertRaisesRegex(ProviderUnavailable, "monetary budget"):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.usage.task_totals("task-1")["actual_usd"], 0.02)

    def test_existing_spend_blocks_live_gateway_before_provider_call(self):
        adapter = FakeAdapter("groq")
        self.usage.record("groq", "model-x", "research", 1, 1, 0.01, task_id="other-task")
        manager = QuotaManager([self.profile()], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            daily_budget_usd=0.0, monthly_budget_usd=0.0,
        )
        with self.assertRaisesRegex(ProviderUnavailable, "monetary budget"):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)

    def test_request_above_profile_output_ceiling_never_calls_provider(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(max_output_tokens=10)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(manager.snapshot()[0]["daily_requests_used"], 0)

    def test_monthly_caps_are_reserved_before_provider_call(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(monthly_request_limit=1, monthly_token_limit=100)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        gateway.generate(self.request)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 1)
        row = manager.snapshot()[0]
        self.assertEqual(row["monthly_requests_used"], 1)

    def test_audit_ledger_filters_prompt_and_output_fields(self):
        self.quota.audit_event(
            "safe_test", route_id="groq:main:model-x", task_kind="research",
            details={"reason": "safe", "prompt": "private prompt", "output": "private output"},
        )
        event = self.quota.audit_events()[0]
        self.assertEqual(event["details"], {"reason": "safe"})
        self.assertNotIn("private prompt", str(event))
        self.assertNotIn("private output", str(event))


    def test_minute_quota_blocks_provider_before_network_call(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(minute_request_limit=1, minute_token_limit=8000)
        self.quota.record(profile.route_id, 1)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)

    def test_minute_token_quota_blocks_oversized_estimate_before_network_call(self):
        adapter = FakeAdapter("groq")
        profile = self.profile(minute_request_limit=30, minute_token_limit=10)
        manager = QuotaManager([profile], self.quota)
        gateway = QuotaAwareLiveGateway(
            manager, {"groq": adapter}, self.usage, True,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)

    def test_no_paid_or_unknown_quota_fallback(self):
        adapter = FakeAdapter("groq")
        manager = QuotaManager([self.profile(daily_token_limit=None)], self.quota)
        gateway = QuotaAwareLiveGateway(manager, {"groq": adapter}, self.usage, True, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"})
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(self.request)
        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
