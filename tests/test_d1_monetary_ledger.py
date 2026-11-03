import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from engine.model_gateway import BudgetExceeded
from engine.d1_monetary_ledger import D1MonetaryLedger, D1MonetaryLedgerError


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.payload


class D1MonetaryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = D1MonetaryLedger("https://engine.example", "test-token")

    @patch("urllib.request.urlopen")
    def test_reserve_uses_integer_nano_usd_and_authenticated_worker(self, open_url):
        open_url.return_value = Response({"reservation_id": "00000000-0000-4000-8000-000000000001"})
        with patch("engine.d1_monetary_ledger.uuid.uuid4", side_effect=[
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
        ]):
            result = self.ledger.reserve(
                "groq", "model-a", "research", 0.0123456781,
                0.4, 12.0, task_id="task-1", per_task_budget_usd=0.2,
            )
        self.assertEqual(result, "00000000-0000-4000-8000-000000000001")
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/monetary-ledger")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        payload = json.loads(request.data)
        self.assertEqual(payload["action"], "reserve")
        self.assertEqual(payload["estimated_cost_nanos"], 12_345_679)
        self.assertEqual(payload["daily_budget_nanos"], 400_000_000)
        self.assertEqual(payload["monthly_budget_nanos"], 12_000_000_000)
        self.assertEqual(payload["per_task_budget_nanos"], 200_000_000)
        self.assertEqual(payload["task_id"], "task-1")

    @patch("urllib.request.urlopen")
    def test_start_ambiguous_release_and_settle_use_lifecycle_actions(self, open_url):
        open_url.return_value = Response({"ok": True})
        self.ledger.mark_started("00000000-0000-4000-8000-000000000001")
        self.assertEqual(json.loads(open_url.call_args.args[0].data)["action"], "start")
        self.ledger.mark_ambiguous("00000000-0000-4000-8000-000000000001")
        self.assertEqual(json.loads(open_url.call_args.args[0].data)["action"], "ambiguous")
        self.ledger.release("00000000-0000-4000-8000-000000000001")
        self.assertEqual(json.loads(open_url.call_args.args[0].data)["action"], "release")
        self.ledger.settle("00000000-0000-4000-8000-000000000001", 12, 8, 0.031)
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(payload["action"], "settle")
        self.assertEqual(payload["actual_cost_nanos"], 31_000_000)
        self.assertEqual(payload["input_tokens"], 12)
        self.assertEqual(payload["output_tokens"], 8)

    @patch("urllib.request.urlopen")
    def test_budget_conflict_maps_to_budget_exceeded(self, open_url):
        open_url.side_effect = HTTPError(
            "https://engine.example/v1/monetary-ledger", 409, "conflict", {},
            io.BytesIO(b'{"error":"budget_exceeded"}'),
        )
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("groq", "model", "research", 0.1, 0.4, 12,
                                task_id="task-1", per_task_budget_usd=0.2)

    @patch("urllib.request.urlopen")
    def test_ambiguous_transport_failure_never_falls_back(self, open_url):
        open_url.side_effect = URLError("connection reset")
        with self.assertRaisesRegex(D1MonetaryLedgerError, "transport failed"):
            self.ledger.settle("00000000-0000-4000-8000-000000000001", 1, 1, 0.0)

    @patch("urllib.request.urlopen")
    def test_status_reads_durable_unresolved_reservations(self, open_url):
        open_url.return_value = Response({"snapshot": {
            "daily_spent_nanos": 10, "monthly_spent_nanos": 20,
            "pending_reserved_nanos": 30, "daily_total_nanos": 40,
            "monthly_total_nanos": 50, "pending_count": 1, "unresolved": [{"status":"ambiguous"}],
        }, "budget_policy": {
            "daily_budget_nanos": 400_000_000,
            "monthly_budget_nanos": 12_000_000_000,
            "per_task_budget_nanos": 200_000_000,
            "enabled": True,
        }})
        totals = self.ledger.totals()
        self.assertEqual(totals["daily_total_nanos"], 40)
        self.assertEqual(totals["monthly_total_nanos"], 50)
        self.assertEqual(totals["pending_reservations"], 1)
        self.assertEqual(self.ledger.pending_reservations()[0]["status"], "ambiguous")

    @patch("urllib.request.urlopen")
    def test_task_totals_url_encodes_task_id(self, open_url):
        open_url.return_value = Response({"snapshot": {
            "task_spent_nanos": 4,
            "task_reserved_nanos": 5,
            "task_total_nanos": 9,
            "task_pending_count": 1,
        }})
        totals = self.ledger.task_totals("task /one")
        self.assertEqual(totals["total_nanos"], 9)
        self.assertEqual(open_url.call_args.args[0].full_url,
                         "https://engine.example/v1/monetary-ledger?task_id=task%20%2Fone")


    @patch("urllib.request.urlopen")
    def test_budget_status_requires_operator_owned_caps(self, open_url):
        open_url.return_value = Response({
            "snapshot": {
                "daily_total_nanos": 10, "monthly_total_nanos": 20,
                "task_total_nanos": 0,
            },
            "budget_policy": {
                "daily_budget_nanos": 400_000_000,
                "monthly_budget_nanos": 12_000_000_000,
                "per_task_budget_nanos": 200_000_000,
                "enabled": True,
            },
        })
        self.assertEqual(
            self.ledger.budget_status(0.4, 12.0),
            {"daily_over_budget": False, "monthly_over_budget": False, "per_task_over_budget": False},
        )
        with self.assertRaisesRegex(D1MonetaryLedgerError, "operator-owned"):
            self.ledger.budget_status(0.5, 12.0)

    def test_requires_https_and_token(self):
        for url, token in (("http://engine.example", "token"), ("https://engine.example", "")):
            with self.subTest(url=url, has_token=bool(token)):
                with self.assertRaises(D1MonetaryLedgerError):
                    D1MonetaryLedger(url, token)


if __name__ == "__main__":
    unittest.main()
