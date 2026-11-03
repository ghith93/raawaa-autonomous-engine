import io
import os
import unittest
from urllib.error import HTTPError
from types import SimpleNamespace
from unittest.mock import patch

from scripts import verify_staging_monetary_ledger as harness


class ApiResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class StagingMonetaryHarnessTests(unittest.TestCase):
    def test_authenticated_api_retries_401_until_secret_propagates(self):
        unauthorized = HTTPError(
            "https://example.invalid/v1/monetary-ledger",
            401, "Unauthorized", {}, io.BytesIO(b'{"error":"unauthorized"}')
        )
        success = ApiResponse(b'{"snapshot":{},"budget_policy":{"enabled":true}}')
        with (
            patch.object(harness, "BASE_URL", "https://example.invalid"),
            patch.object(harness, "TOKEN", "test-token"),
            patch("scripts.verify_staging_monetary_ledger.urllib.request.urlopen",
                  side_effect=[unauthorized, success]) as open_url,
            patch.object(harness.time, "sleep") as sleep,
        ):
            status, body = harness.api("GET", "/v1/monetary-ledger")
        self.assertEqual(status, 200)
        self.assertTrue(body["budget_policy"]["enabled"])
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_authenticated_api_does_not_retry_non_auth_4xx(self):
        forbidden = HTTPError(
            "https://example.invalid/v1/monetary-ledger",
            403, "Forbidden", {}, io.BytesIO(b'{"error":"forbidden"}')
        )
        with (
            patch.object(harness, "BASE_URL", "https://example.invalid"),
            patch.object(harness, "TOKEN", "test-token"),
            patch("scripts.verify_staging_monetary_ledger.urllib.request.urlopen",
                  side_effect=forbidden) as open_url,
            patch.object(harness.time, "sleep") as sleep,
        ):
            status, body = harness.api("GET", "/v1/monetary-ledger")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "forbidden")
        open_url.assert_called_once()
        sleep.assert_not_called()

    def test_unauthenticated_api_does_not_retry_401(self):
        unauthorized = HTTPError(
            "https://example.invalid/health",
            401, "Unauthorized", {}, io.BytesIO(b'{"error":"unauthorized"}')
        )
        with (
            patch.object(harness, "BASE_URL", "https://example.invalid"),
            patch("scripts.verify_staging_monetary_ledger.urllib.request.urlopen",
                  side_effect=unauthorized) as open_url,
            patch.object(harness.time, "sleep") as sleep,
        ):
            status, body = harness.api("GET", "/health", authenticated=False)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")
        open_url.assert_called_once()
        sleep.assert_not_called()

    def test_default_policy_uses_integer_nano_usd(self):
        self.assertEqual(
            harness.default_policy_nanos(),
            (400_000_000, 12_000_000_000, 200_000_000),
        )

    def test_staging_target_matches_isolated_database_config(self):
        self.assertIsNone(harness.verify_staging_target())

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_safe_staging_cleanup_delete_retries_transient_d1_7429(self, run):
        run.side_effect = [
            SimpleNamespace(returncode=1, stdout="", stderr="internal error [code: 7429]"),
            SimpleNamespace(returncode=0, stdout="ok", stderr=""),
        ]
        with patch.object(harness, "verify_staging_target") as verify:
            with patch.object(harness.time, "sleep") as sleep:
                harness.execute_staging_sql(
                    "DELETE FROM monetary_reservations WHERE provider LIKE 'staging-verification-%'"
                )
        verify.assert_called_once_with()
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_non_idempotent_staging_write_is_not_retried_on_d1_7429(self, run):
        run.return_value = SimpleNamespace(
            returncode=1, stdout="", stderr="internal error [code: 7429]"
        )
        with patch.object(harness, "verify_staging_target"):
            with patch.object(harness.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "Staging-only test cleanup failed"):
                    harness.execute_staging_sql(
                        "INSERT INTO tasks(id) VALUES ('staging-verification-fixture')"
                    )
        run.assert_called_once()
        sleep.assert_not_called()

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_policy_update_uses_remote_staging_database_only(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(harness, "verify_staging_target") as verify:
            harness.set_staging_policy(100, 200, 50)
        verify.assert_called_once_with()
        command = run.call_args.args[0]
        self.assertEqual(command[0:5], [
            "npx", "--yes", "wrangler@4.156.1", "d1", "execute"
        ])
        self.assertIn("raawaa-engine-staging", command)
        self.assertIn("--remote", command)
        config_index = command.index("--config")
        self.assertEqual(command[config_index + 1], "wrangler.staging.toml")
        sql = command[command.index("--command") + 1]
        self.assertIn("daily_budget_nanos=100", sql)
        self.assertIn("monthly_budget_nanos=200", sql)
        self.assertIn("per_task_budget_nanos=50", sql)

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_invalid_policy_values_are_rejected_before_remote_write(self, run):
        with self.assertRaisesRegex(RuntimeError, "invalid Staging monetary policy"):
            harness.set_staging_policy(True, 200, 50)
        run.assert_not_called()

    def test_worker_readiness_waits_for_authenticated_token_propagation(self):
        responses = [
            (200, {"ok": True}),
            (401, {"error": "unauthorized"}),
            (200, {"ok": True}),
            (200, {"snapshot": {}, "budget_policy": {"enabled": True}}),
        ]
        with patch.object(harness, "api", side_effect=responses) as request:
            with patch.object(harness.time, "sleep") as sleep:
                harness.wait_for_worker(timeout_seconds=10)
        self.assertEqual(request.call_count, 4)
        sleep.assert_called_once_with(2)
        self.assertEqual(request.call_args_list[1].args, ("GET", "/v1/monetary-ledger"))

    def test_worker_readiness_fails_fast_on_authenticated_configuration_error(self):
        with patch.object(harness, "api", side_effect=[
            (200, {"ok": True}),
            (403, {"error": "forbidden"}),
        ]) as request:
            with patch.object(harness.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                    harness.wait_for_worker(timeout_seconds=10)
        self.assertEqual(request.call_count, 2)
        sleep.assert_not_called()

    def test_monthly_cap_test_restores_original_staging_policy(self):
        from itertools import count
        from unittest.mock import call

        original = {
            "daily_budget_nanos": 4_000,
            "monthly_budget_nanos": 12_000,
            "per_task_budget_nanos": 2_000,
            "enabled": True,
        }
        current = {"daily_total_nanos": 100, "monthly_total_nanos": 1_000}
        attempts = count()

        def reserve(*args, **kwargs):
            return (201, {}, "reservation") if next(attempts) < 2 else (409, {"error": "budget_exceeded"}, "rejected")

        with (
            patch.object(harness, "snapshot", return_value=current),
            patch.object(harness, "budget_policy", return_value=original),
            patch.object(harness, "set_staging_policy") as update_policy,
            patch.object(harness, "reserve", side_effect=reserve),
            patch.object(harness, "action", return_value=(200, {})),
        ):
            harness.cap_test("monthly", "test-run", "monthly")

        estimate = (12_000 - 1_000) // 2
        temporary = (100 + 100 * estimate, 1_000 + 2 * estimate, estimate)
        update_policy.assert_has_calls([call(*temporary), call(4_000, 12_000, 2_000)])
        self.assertEqual(update_policy.call_count, 2)

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_wrong_target_stops_before_remote_write(self, run):
        with patch.object(harness, "verify_staging_target", side_effect=RuntimeError("wrong D1 target")):
            with self.assertRaisesRegex(RuntimeError, "wrong D1 target"):
                harness.set_staging_policy(100, 200, 50)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
