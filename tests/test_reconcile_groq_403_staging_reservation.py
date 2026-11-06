"""Tests for bounded Staging quota-quarantine retries."""
import unittest
from unittest.mock import patch

from scripts import reconcile_groq_403_staging_reservation as reconcile


class StagingQuotaQuarantineTests(unittest.TestCase):
    def test_retries_transient_admin_secret_propagation_and_succeeds(self):
        payload = {"action": "cool_down", "route_id": reconcile.ROUTE_ID,
                   "seconds": 86400, "reason": "manual_reconcile_http_403"}
        with (
            patch.object(reconcile, "api", side_effect=[
                (403, {"error": "forbidden_operation_scope"}),
                (200, {"ok": True, "until_at": "2026-11-07T16:00:00.000Z"}),
            ]) as request,
            patch.object(reconcile.time, "monotonic", side_effect=[0.0, 1.0]),
            patch.object(reconcile.time, "sleep") as sleep,
        ):
            result = reconcile.quarantine_route(payload, timeout_seconds=10)
        self.assertTrue(result["ok"])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_does_not_retry_invalid_cooldown_request(self):
        with (
            patch.object(reconcile, "api", return_value=(400, {"error": "invalid_cooldown"})) as request,
            patch.object(reconcile.time, "monotonic", return_value=0.0),
            patch.object(reconcile.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 400; error=invalid_cooldown"):
                reconcile.quarantine_route({"action": "cool_down"}, timeout_seconds=10)
        request.assert_called_once()
        sleep.assert_not_called()

    def test_stops_after_bounded_admin_capability_propagation_window(self):
        with (
            patch.object(reconcile, "api", return_value=(403, {"error": "forbidden_operation_scope"})) as request,
            patch.object(reconcile.time, "monotonic", side_effect=[0.0, 2.0]),
            patch.object(reconcile.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "did not become ready.*HTTP 403"):
                reconcile.quarantine_route({"action": "cool_down"}, timeout_seconds=1)
        request.assert_called_once()
        sleep.assert_not_called()

    def test_error_code_is_bounded_and_never_echoes_arbitrary_response_text(self):
        self.assertEqual(reconcile.safe_error_code({"error": "forbidden_operation_scope"}),
                         "forbidden_operation_scope")
        self.assertEqual(reconcile.safe_error_code({"error": "token=do-not-print"}), "unknown")
        self.assertEqual(reconcile.safe_error_code({"error": "x" * 200}), "unknown")


if __name__ == "__main__":
    unittest.main()
