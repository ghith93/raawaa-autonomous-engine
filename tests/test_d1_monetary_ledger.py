import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from io import BytesIO

from engine.d1_monetary_ledger import D1MonetaryLedger, D1MonetaryLedgerError
from engine.model_gateway import BudgetExceeded


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.payload


class D1MonetaryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = D1MonetaryLedger("https://engine.example", "test-token")

    @patch("urllib.request.urlopen")
    def test_reserve_sends_integer_nanos_and_authenticated_request(self, open_url):
        reservation_id = "6e0b0e91-ffb2-4a8b-a3d5-0b6a76f9e5c1"
        open_url.return_value = Response({"reservation_id": reservation_id, "status": "reserved"})
        with patch("engine.d1_monetary_ledger.uuid.uuid4", return_value=reservation_id):
            result = self.ledger.reserve(
                "groq", "openai/gpt-oss-20b", "research_brief", 0.0000000012, 0.4, 12.0,
                task_id="task-1", per_task_budget_usd=0.2,
            )
        self.assertEqual(result, reservation_id)
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/monetary-ledger")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("User-agent"), "RaawaaAutonomousEngine/1.0")
        payload = json.loads(request.data)
        self.assertEqual(payload["action"], "reserve")
        self.assertEqual(payload["estimated_cost_nanos"], 2)
        self.assertEqual(payload["daily_cap_nanos"], 400000000)
        self.assertEqual(payload["monthly_cap_nanos"], 12000000000)
        self.assertEqual(payload["per_task_cap_nanos"], 200000000)
        self.assertEqual(payload["task_id"], "task-1")

    @patch("urllib.request.urlopen")
    def test_budget_exceeded_is_typed_and_fails_closed(self, open_url):
        error = HTTPError("https://engine.example/v1/monetary-ledger", 409, "conflict", {}, BytesIO(b'{"error":"budget_exceeded"}'))
        open_url.side_effect = error
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("groq", "model", "research_brief", 0.1, 0.4, 12, task_id="task-1", per_task_budget_usd=0.2)

    @patch("urllib.request.urlopen")
    def test_provider_lifecycle_actions_are_explicit(self, open_url):
        open_url.side_effect = [
            Response({"ok": True, "status": "in_flight"}),
            Response({"ok": True, "status": "ambiguous"}),
            Response({"ok": True, "status": "released"}),
        ]
        reservation_id = "6e0b0e91-ffb2-4a8b-a3d5-0b6a76f9e5c1"
        self.ledger.mark_started(reservation_id)
        self.ledger.mark_ambiguous(reservation_id, "provider_timeout")
        self.ledger.release(reservation_id, "provider_not_called")
        payloads = [json.loads(call.args[0].data) for call in open_url.call_args_list]
        self.assertEqual([item["action"] for item in payloads], ["mark_started", "mark_ambiguous", "release"])
        self.assertEqual(payloads[1]["reason"], "provider_timeout")

    @patch("urllib.request.urlopen")
    def test_settlement_records_actual_cost_and_cost_basis(self, open_url):
        open_url.return_value = Response({"ok": True, "status": "settled", "over_budget": False})
        self.ledger.settle(
            "6e0b0e91-ffb2-4a8b-a3d5-0b6a76f9e5c1", 120, 80, 0.0000000012,
            cost_basis="verified_profile_estimate",
        )
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(payload["actual_cost_nanos"], 2)
        self.assertEqual(payload["input_tokens"], 120)
        self.assertEqual(payload["output_tokens"], 80)
        self.assertEqual(payload["cost_basis"], "verified_profile_estimate")

    @patch("urllib.request.urlopen")
    def test_status_read_is_authenticated_and_task_id_is_url_encoded(self, open_url):
        status = {
            "caps": {"daily_nanos": 400000000, "monthly_nanos": 12000000000, "per_task_nanos": 200000000},
            "daily": {"settled_usd": 0.1, "pending_usd": 0.0, "total_nanos": 100000000, "over_budget": False},
            "monthly": {"settled_usd": 0.1, "pending_usd": 0.0, "total_nanos": 100000000, "over_budget": False},
            "per_task": {"total_nanos": 50000000, "over_budget": False},
            "pending": {"count": 0, "ambiguous_count": 0},
        }
        open_url.return_value = Response(status)
        result = self.ledger.budget_status(0.4, 12.0, task_id="task / one", per_task_budget_usd=0.2)
        self.assertFalse(result["daily_over_budget"])
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/monetary-ledger?task_id=task%20%2F%20one")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    @patch("urllib.request.urlopen")
    def test_global_budget_status_can_be_checked_without_task_context(self, open_url):
        open_url.return_value = Response({
            "caps": {"daily_nanos": 400000000, "monthly_nanos": 12000000000, "per_task_nanos": 200000000},
            "daily": {"settled_usd": 0.1, "pending_usd": 0.0, "total_nanos": 100000000, "over_budget": False},
            "monthly": {"settled_usd": 0.1, "pending_usd": 0.0, "total_nanos": 100000000, "over_budget": False},
            "per_task": None, "pending": {"count": 0, "ambiguous_count": 0},
        })
        result = self.ledger.budget_status(0.4, 12.0)
        self.assertFalse(result["daily_over_budget"])
        self.assertFalse(result["monthly_over_budget"])
        self.assertFalse(result["per_task_over_budget"])

    @patch("urllib.request.urlopen")
    def test_policy_mismatch_fails_closed(self, open_url):
        open_url.return_value = Response({
            "caps": {"daily_nanos": 400000000, "monthly_nanos": 12000000000, "per_task_nanos": 300000000},
            "daily": {"settled_usd": 0, "pending_usd": 0, "total_nanos": 0, "over_budget": False},
            "monthly": {"settled_usd": 0, "pending_usd": 0, "total_nanos": 0, "over_budget": False},
            "per_task": None, "pending": {"count": 0, "ambiguous_count": 0},
        })
        with self.assertRaisesRegex(D1MonetaryLedgerError, "does not match"):
            self.ledger.budget_status(0.4, 12, per_task_budget_usd=0.2)

    @patch("urllib.request.urlopen")
    def test_transport_failure_never_falls_back_to_local_ledger(self, open_url):
        open_url.side_effect = URLError("connection reset")
        with self.assertRaisesRegex(D1MonetaryLedgerError, "transport failed"):
            self.ledger.settle("6e0b0e91-ffb2-4a8b-a3d5-0b6a76f9e5c1", 1, 1, 0.0)

    def test_cloud_mode_requires_https_and_token(self):
        for url, token in (("http://engine.example", "token"), ("https://engine.example", "")):
            with self.subTest(url=url, token=bool(token)), self.assertRaises(D1MonetaryLedgerError):
                D1MonetaryLedger(url, token)

    def test_nano_usd_rounds_estimates_up(self):
        self.assertEqual(D1MonetaryLedger.money_nanos(0.0000000001), 1)
        self.assertEqual(D1MonetaryLedger.money_nanos(0.000000001), 1)


if __name__ == "__main__":
    unittest.main()
