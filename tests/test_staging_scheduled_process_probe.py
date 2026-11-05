from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import Mock, patch

from engine.remote_task_store import RemoteTaskStoreError

from scripts import run_staging_scheduled_process_probe as probe


class FakeStore:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.enqueued = None
        self.claimed_id = None
        self.status = "pending"
        self.payload = {"operation": probe.OPERATION}
        self.requests = []

    def _request(self, path, method="POST", payload=None):
        self.requests.append((path, method, payload))
        return {"ok": True}

    def enqueue(self, task_type, title, payload, **kwargs):
        self.enqueued = (task_type, title, payload, kwargs)
        return {
            "id": "probe-123",
            "task_type": task_type,
            "title": title,
            "idempotency_key": kwargs["idempotency_key"],
            "payload_json": json.dumps(self.payload),
            "status": self.status,
        }

    def claim_supervised_probe(self, task_id):
        self.claimed_id = task_id
        if self.status != "pending":
            return None
        return {
            "id": task_id,
            "task_type": probe.TASK_TYPE,
            "title": probe.TITLE,
            "idempotency_key": self.enqueued[3]["idempotency_key"],
            "payload_json": json.dumps(self.payload),
            "status": "running",
            "execution_mode": "dry_run",
            "lease_token": "lease-token",
        }


def cron_environment(**overrides):
    result = {
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "123456789",
        "RAAWAA_ENGINE_API_URL": probe.STAGING_API_URL,
        "RAAWAA_ENGINE_API_TOKEN": "temporary-staging-token-" + "x" * 40,
        "RAAWAA_ENGINE_PROCESS_SUPERVISOR": "enabled",
        "RAAWAA_ENGINE_LIVE_INFERENCE": "disabled",
    }
    result.update(overrides)
    return result


class StagingScheduledProbeTests(unittest.TestCase):
    def test_only_real_main_schedule_event_is_accepted(self):
        for override in (
            {"GITHUB_EVENT_NAME": "workflow_dispatch"},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REF": "refs/heads/agent"},
            {"GITHUB_RUN_ID": "../123"},
        ):
            with self.subTest(override=override), self.assertRaises(probe.ScheduledProbeError):
                probe.validate_environment(cron_environment(**override))

    def test_only_the_exact_staging_origin_is_accepted(self):
        for url in (
            "https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev",
            "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev.attacker.invalid",
            "http://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev",
        ):
            with self.subTest(url=url), self.assertRaises(probe.ScheduledProbeError):
                probe.validate_environment(cron_environment(RAAWAA_ENGINE_API_URL=url))

    def test_live_inference_provider_credentials_and_supervisor_are_fail_closed(self):
        for override in (
            {"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
            {"RAAWAA_ENGINE_LIVE_INFERENCE": ""},
            {"RAAWAA_ENGINE_PROCESS_SUPERVISOR": "disabled"},
            {"GROQ_API_KEY": "must-not-be-present"},
            {"GEMINI_API_KEY": "must-not-be-present"},
            {"RAAWAA_ENGINE_API_TOKEN": "short"},
        ):
            with self.subTest(override=override), self.assertRaises(probe.ScheduledProbeError):
                probe.validate_environment(cron_environment(**override))

    def test_runs_one_exact_probe_task_and_reports_safe_evidence(self):
        store = FakeStore()
        processor = Mock(return_value={
            "id": "probe-123",
            "status": "succeeded",
            "classification": "succeeded",
            "operation": probe.OPERATION,
            "dispatch_id": "d" * 64,
            "replayed": False,
        })
        created = []

        def store_factory(*args, **kwargs):
            instance = FakeStore(*args, **kwargs)
            created.append(instance)
            return instance

        result = probe.run_scheduled_probe(
            cron_environment(), store_factory=store_factory, processor=processor,
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].args, (probe.STAGING_API_URL,
                                           cron_environment()["RAAWAA_ENGINE_API_TOKEN"]))
        self.assertTrue(created[0].kwargs["reject_redirects"])
        self.assertEqual(created[0].enqueued[0:3], (
            probe.TASK_TYPE, probe.TITLE, {"operation": probe.OPERATION},
        ))
        options = created[0].enqueued[3]
        self.assertEqual(options["idempotency_key"], probe.IDEMPOTENCY_PREFIX + "123456789")
        self.assertEqual(options["max_attempts"], 1)
        self.assertEqual(options["priority"], 100)
        self.assertEqual(created[0].claimed_id, "probe-123")
        processor.assert_called_once()
        self.assertEqual(processor.call_args.kwargs["lease_timeout_seconds"],
                         probe.LEASE_TIMEOUT_SECONDS)
        self.assertLessEqual(probe.LEASE_TIMEOUT_SECONDS, 120)
        self.assertEqual(result, {
            "task_id": "probe-123",
            "status": "succeeded",
            "classification": "succeeded",
            "operation": probe.OPERATION,
            "dispatch_id": "d" * 64,
            "replayed": False,
            "provider_calls": 0,
            "external_writes": 0,
            "backend": "cloudflare_staging",
            "fallback_standby": "not_checked",
        })

    def test_uses_pinned_supabase_fallback_for_retryable_cloudflare_failure(self):
        primary = Mock()
        primary.enqueue.side_effect = RemoteTaskStoreError(
            "Remote task API temporarily unavailable (HTTP 503)",
            status_code=503, retryable=True,
        )
        primary_factory = Mock(return_value=primary)
        fallback = FakeStore()
        fallback_factory = Mock(return_value=fallback)
        oidc_factory = Mock(return_value="a" * 100 + ".b.c")
        processor = Mock(return_value={
            "id": "probe-123", "status": "succeeded", "classification": "succeeded",
            "operation": probe.OPERATION, "dispatch_id": "d" * 32, "replayed": False,
        })

        result = probe.run_scheduled_probe(
            cron_environment(), store_factory=primary_factory,
            fallback_store_factory=fallback_factory,
            oidc_token_factory=oidc_factory, processor=processor,
        )

        primary_factory.assert_called_once()
        oidc_factory.assert_called_once_with(
            cron_environment(), audience=probe.SUPABASE_OIDC_AUDIENCE,
        )
        fallback_factory.assert_called_once_with(
            probe.SUPABASE_FALLBACK_URL,
            probe.SUPABASE_FALLBACK_PUBLISHABLE_KEY,
            "a" * 100 + ".b.c",
            timeout=15,
        )
        self.assertEqual(fallback.enqueued[0], probe.TASK_TYPE)
        self.assertEqual(fallback.enqueued[2], {"operation": probe.OPERATION})
        self.assertEqual(result["backend"], "supabase_free_fallback")
        self.assertEqual(result["fallback_standby"], "ready")
        self.assertIn("HTTP 503", result["cloudflare_primary_failure"])
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["provider_calls"], 0)

    def test_missing_cloudflare_token_skips_primary_and_can_use_fallback(self):
        fallback = FakeStore()
        processor = Mock(return_value={
            "id": "probe-123", "status": "succeeded", "classification": "succeeded",
            "operation": probe.OPERATION, "dispatch_id": "d" * 32, "replayed": False,
        })
        primary_factory = Mock()
        result = probe.run_scheduled_probe(
            cron_environment(RAAWAA_ENGINE_STAGING_TOKEN_INSTALLED="false"),
            store_factory=primary_factory,
            fallback_store_factory=lambda *args, **kwargs: fallback,
            oidc_token_factory=lambda *args, **kwargs: "a" * 100 + ".b.c",
            processor=processor,
        )
        primary_factory.assert_not_called()
        self.assertEqual(result["backend"], "supabase_free_fallback")
        self.assertIn("token installation unavailable", result["cloudflare_primary_failure"])

    def test_non_retryable_cloudflare_auth_error_does_not_fail_over(self):
        primary = Mock()
        primary.enqueue.side_effect = RemoteTaskStoreError(
            "Remote task API authentication failed", status_code=401,
        )
        fallback_factory = Mock()
        with self.assertRaisesRegex(RemoteTaskStoreError, "authentication failed"):
            probe.run_scheduled_probe(
                cron_environment(), store_factory=lambda *args, **kwargs: primary,
                fallback_store_factory=fallback_factory,
                oidc_token_factory=Mock(),
            )
        fallback_factory.assert_not_called()

    def test_daily_standby_heartbeat_is_nonfatal_to_healthy_primary(self):
        primary = FakeStore()
        fallback = FakeStore()
        result = probe.run_scheduled_probe(
            cron_environment(RAAWAA_ENGINE_FALLBACK_STANDBY_CHECK="enabled"),
            store_factory=lambda *args, **kwargs: primary,
            fallback_store_factory=lambda *args, **kwargs: fallback,
            oidc_token_factory=lambda *args, **kwargs: "a" * 100 + ".b.c",
            processor=lambda *args, **kwargs: {
                "id": "probe-123", "status": "succeeded", "classification": "succeeded",
                "operation": probe.OPERATION, "dispatch_id": "d" * 32, "replayed": False,
            },
        )
        self.assertEqual(fallback.requests, [("/health", "POST", {})])
        self.assertEqual(result["backend"], "cloudflare_staging")
        self.assertEqual(result["fallback_standby"], "ready")

    def test_rejects_wrong_task_payload_without_claim_or_execution(self):
        store = FakeStore()
        store.payload = {"operation": "unapproved"}
        processor = Mock()
        with self.assertRaisesRegex(probe.ScheduledProbeError, "unexpected probe operation"):
            probe.run_scheduled_probe(cron_environment(),
                                      store_factory=lambda *a, **k: store,
                                      processor=processor)
        self.assertIsNone(store.claimed_id)
        processor.assert_not_called()

    def test_does_not_relaunch_a_task_that_already_succeeded_on_workflow_rerun(self):
        store = FakeStore()
        store.status = "succeeded"
        processor = Mock()
        result = probe.run_scheduled_probe(cron_environment(),
                                           store_factory=lambda *a, **k: store,
                                           processor=processor)
        self.assertTrue(result["replayed"])
        self.assertEqual(result["classification"], "already_settled")
        self.assertIsNone(store.claimed_id)
        processor.assert_not_called()

    def test_claim_failure_fails_closed_without_running_handler(self):
        store = FakeStore()
        store.claim_supervised_probe = Mock(return_value=None)
        processor = Mock()
        with self.assertRaisesRegex(probe.ScheduledProbeError, "not claimable"):
            probe.run_scheduled_probe(cron_environment(),
                                      store_factory=lambda *a, **k: store,
                                      processor=processor)
        processor.assert_not_called()

    def test_non_successful_worker_outcome_fails_closed(self):
        processor = Mock(return_value={"id": "probe-123", "status": "blocked"})
        with self.assertRaisesRegex(probe.ScheduledProbeError, "fresh successful"):
            probe.run_scheduled_probe(cron_environment(),
                                      store_factory=FakeStore,
                                      processor=processor)

    def test_main_logs_bounded_redacted_remote_error_reason(self):
        output = StringIO()
        secret = "supersecretvalue123456789"
        error = RemoteTaskStoreError(
            "Remote task API temporarily unavailable (HTTP 503): "
            f"storage_unavailable | D1_ERROR: no such table: task_dependencies ENGINE_API_TOKEN={secret}"
        )
        with patch.object(probe, "run_scheduled_probe", side_effect=error):
            with redirect_stderr(output):
                result = probe.main()
        self.assertEqual(result, 1)
        self.assertIn("RemoteTaskStoreError", output.getvalue())
        self.assertIn("HTTP 503", output.getvalue())
        self.assertIn("no such table: task_dependencies", output.getvalue())
        self.assertIn("[redacted]", output.getvalue())
        self.assertNotIn(secret, output.getvalue())
        self.assertLessEqual(len(output.getvalue()), 400)

    def test_main_hides_untrusted_unexpected_exception_text(self):
        output = StringIO()
        with patch.object(
            probe, "run_scheduled_probe", side_effect=RuntimeError("untrusted response body")
        ):
            with redirect_stderr(output):
                result = probe.main()
        self.assertEqual(result, 1)
        self.assertIn("RuntimeError", output.getvalue())
        self.assertNotIn("untrusted response body", output.getvalue())


if __name__ == "__main__":
    unittest.main()
