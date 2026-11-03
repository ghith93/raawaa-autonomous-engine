import unittest
from unittest.mock import patch

from scripts import reconcile_groq_403_staging_reservation as reconcile


class Groq403StagingReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.base_url = reconcile.BASE_URL
        self.token = reconcile.TOKEN
        reconcile.BASE_URL = "https://raawaa-autonomous-engine-staging.example.workers.dev"
        reconcile.TOKEN = "test-token"

    def tearDown(self):
        reconcile.BASE_URL = self.base_url
        reconcile.TOKEN = self.token

    def test_reconciles_only_exact_ambiguous_403_and_quarantines_route(self):
        reservation = {
            "reservation_id": "00000000-0000-4000-8000-000000000099",
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "task_id": reconcile.TASK_ID,
            "status": "ambiguous",
        }
        before = {"snapshot": {"unresolved": [reservation], "task_pending_count": 1}}
        quota = {"audit": [
            {"event": "provider_error", "details": {"http_status": 403}},
            {"event": "provider_failure_stopped", "details": {"http_status": 403}},
        ]}
        after = {"snapshot": {"task_pending_count": 0, "unresolved": []}}
        with patch.object(reconcile, "api", side_effect=[
            (200, before), (200, quota), (200, {"settled": True}),
            (200, {"ok": True}), (200, after),
        ]) as api:
            with patch("builtins.print"):
                reconcile.run()
        self.assertEqual(api.call_count, 5)
        self.assertEqual(api.call_args_list[2].args[2]["actual_cost_nanos"], 0)
        self.assertEqual(api.call_args_list[2].args[2]["reservation_id"], reservation["reservation_id"])
        self.assertEqual(api.call_args_list[3].args[2]["seconds"], 86400)

    def test_repeat_reconciliation_quarantines_route_without_second_settlement(self):
        before = {"snapshot": {"unresolved": [], "task_pending_count": 0}}
        quota = {"audit": [
            {"event": "provider_error", "details": {"http_status": 403}},
            {"event": "provider_failure_stopped", "details": {"http_status": 403}},
        ]}
        after = {"snapshot": {"task_pending_count": 0, "unresolved": []}}
        with patch.object(reconcile, "api", side_effect=[
            (200, before), (200, quota), (200, {"ok": True}), (200, after),
        ]) as api:
            with patch("builtins.print"):
                reconcile.run()
        self.assertEqual(api.call_count, 4)
        self.assertEqual(api.call_args_list[2].args[2]["action"], "cool_down")

    def test_does_not_mutate_when_http_403_audit_is_missing(self):
        before = {"snapshot": {"unresolved": [{
            "reservation_id": "00000000-0000-4000-8000-000000000099",
            "provider": "groq", "model": "openai/gpt-oss-120b",
            "task_id": reconcile.TASK_ID, "status": "ambiguous",
        }], "task_pending_count": 1}}
        with patch.object(reconcile, "api", side_effect=[
            (200, before), (200, {"audit": []}),
        ]) as api:
            with self.assertRaisesRegex(RuntimeError, "HTTP 403 evidence"):
                reconcile.run()
        self.assertEqual(api.call_count, 2)


if __name__ == "__main__":
    unittest.main()
