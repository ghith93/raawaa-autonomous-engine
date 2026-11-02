import json
import unittest
from unittest.mock import patch

from urllib.error import HTTPError, URLError

from engine.d1_usage_ledger import D1UsageLedger, D1UsageLedgerError
from engine.model_gateway import BudgetExceeded


class Response:
    def __init__(self, payload, status=200):
        self.payload = json.dumps(payload).encode()
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.payload


def snapshot(**overrides):
    result = {
        "daily_actual_nanos": 0,
        "monthly_actual_nanos": 0,
        "pending_nanos": 0,
        "pending_count": 0,
    }
    result.update(overrides)
    return result


class D1UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = D1UsageLedger("https://engine.example", "test-token")

    @patch("urllib.request.urlopen")
    def test_reserve_uses_authenticated_worker_and_exact_integer_caps(self, open_url):
        open_url.return_value = Response({"reservation_id": 17})
        result = self.ledger.reserve(
            "groq:primary:model", "openai/gpt-oss-20b", "research",
            0, 0, 0, task_id="task-1", per_task_budget_usd=0,
        )
        self.assertEqual(result, 17)
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/usage")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        body = json.loads(request.data)
        self.assertEqual(body["action"], "reserve")
        self.assertEqual(body["estimated_cost_nanos"], 0)
        self.assertEqual(body["daily_budget_nanos"], 0)
        self.assertEqual(body["monthly_budget_nanos"], 0)
        self.assertEqual(body["per_task_budget_nanos"], 0)
        self.assertEqual(body["task_id"], "task-1")

    @patch("urllib.request.urlopen")
    def test_budget_conflict_maps_to_budget_exceeded(self, open_url):
        open_url.side_effect = HTTPError(
            "https://engine.example/v1/usage", 409, "conflict", {}, None
        )
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve(
                "groq", "model", "research", 0.01, 0.0, 0.0,
                task_id="task-1",
            )

    @patch("urllib.request.urlopen")
    def test_settle_persists_actual_usage_and_returns_integer_totals(self, open_url):
        open_url.return_value = Response(snapshot(daily_actual_nanos=5, monthly_actual_nanos=5))
        result = self.ledger.settle(17, 12, 8, 0.000000005)
        request = open_url.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body["action"], "settle")
        self.assertEqual(body["reservation_id"], 17)
        self.assertEqual(body["input_tokens"], 12)
        self.assertEqual(body["output_tokens"], 8)
        self.assertEqual(body["actual_cost_nanos"], 5)
        self.assertEqual(result["daily_total_nanos"], 5)

    @patch("urllib.request.urlopen")
    def test_release_only_requests_pre_call_reservation_release(self, open_url):
        open_url.return_value = Response({"ok": True})
        self.ledger.release(17)
        body = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(body, {"action": "release", "reservation_id": 17})

    @patch("urllib.request.urlopen")
    def test_budget_status_uses_daily_monthly_and_task_nano_totals(self, open_url):
        open_url.side_effect = [
            Response(snapshot(daily_actual_nanos=1, monthly_actual_nanos=2, pending_nanos=3)),
            Response({
                "task_actual_nanos": 4,
                "task_reserved_nanos": 5,
                "task_pending_count": 1,
            }),
        ]
        result = self.ledger.budget_status(
            0.000000004, 0.000000010, task_id="task-1", per_task_budget_usd=0.000000008
        )
        self.assertEqual(result, {
            "daily_over_budget": False,
            "monthly_over_budget": False,
            "per_task_over_budget": True,
        })

    @patch("urllib.request.urlopen")
    def test_pending_reservations_and_task_summaries_are_read_only(self, open_url):
        open_url.side_effect = [
            Response({"pending_reservations": [{"id": 2, "estimated_cost_usd": 0.0}]}),
            Response({"task_summaries": [{"task_id": "task-1", "total_nanos": 0}]}),
        ]
        self.assertEqual(self.ledger.pending_reservations()[0]["id"], 2)
        self.assertEqual(self.ledger.task_summaries()[0]["task_id"], "task-1")
        self.assertEqual([call.args[0].method for call in open_url.call_args_list], ["GET", "GET"])

    @patch("urllib.request.urlopen")
    def test_record_requires_task_id_for_nonzero_cost(self, open_url):
        with self.assertRaises(ValueError):
            self.ledger.record("groq", "model", "research", 1, 1, 0.01)
        self.ledger.record("groq", "model", "research", 1, 1, 0.0, task_id="task-1")
        body = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(body["action"], "record")
        self.assertEqual(body["cost_nanos"], 0)

    @patch("urllib.request.urlopen")
    def test_ambiguous_transport_failure_never_falls_back_to_local_sqlite(self, open_url):
        open_url.side_effect = URLError("connection reset")
        with self.assertRaisesRegex(D1UsageLedgerError, "transport failed"):
            self.ledger.settle(17, 8, 4, 0.0)

    def test_cloud_mode_requires_https_and_token(self):
        for url, token in (("http://engine.example", "token"), ("https://engine.example", "")):
            with self.subTest(url=url, token=bool(token)):
                with self.assertRaises(D1UsageLedgerError):
                    D1UsageLedger(url, token)

    def test_cloudflare_access_service_token_must_be_a_pair(self):
        with patch.dict("os.environ", {"RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID": "id", "RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET": ""}):
            with patch("urllib.request.urlopen") as open_url:
                with self.assertRaisesRegex(D1UsageLedgerError, "configured as a pair"):
                    self.ledger.totals()
                open_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
