import json
import unittest
from unittest.mock import patch

from engine.d1_quota_ledger import D1QuotaLedger, D1QuotaLedgerError


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


class D1QuotaLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = D1QuotaLedger(
            "https://engine.example", "test-token",
        )

    @patch("urllib.request.urlopen")
    def test_reserve_uses_authenticated_worker_and_passes_every_limit(self, open_url):
        open_url.return_value = Response({"reservation_id": 17, "settlement_key": "opaque-proof"})
        result = self.ledger.reserve(
            "groq:main:model", 100, 10, 1000,
            monthly_request_limit=50, monthly_token_limit=5000,
            task_id="task-1", per_task_request_limit=3, per_task_token_limit=900,
            minute_request_limit=2, minute_token_limit=500,
        )
        self.assertEqual(result, 17)
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/quota")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("User-agent"), "RaawaaAutonomousEngine/1.0")
        payload = json.loads(request.data)
        self.assertEqual(payload["action"], "reserve")
        self.assertEqual(payload["task_id"], "task-1")
        self.assertNotIn("daily_request_limit", payload)
        self.assertNotIn("minute_token_limit", payload)
        self.assertEqual(payload, {"action": "reserve", "route_id": "groq:main:model", "estimated_tokens": 100, "task_id": "task-1"})
        self.assertEqual(self.ledger._reservations[17], ("opaque-proof", "groq:main:model", "task-1"))

    @patch("urllib.request.urlopen")
    def test_identical_settlement_replay_is_accepted_by_worker(self, open_url):
        open_url.return_value = Response({"ok": True, "settled": True, "replay": True})
        self.ledger._reservations[17] = ("opaque-proof", "groq:main:model", "task-1")
        self.ledger.settle(17, 88)
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(payload, {
            "action": "settle", "reservation_id": 17, "actual_tokens": 88,
            "settlement_key": "opaque-proof", "route_id": "groq:main:model", "task_id": "task-1",
        })

    @patch("urllib.request.urlopen")
    def test_worker_conflict_fails_closed(self, open_url):
        from urllib.error import HTTPError
        self.ledger._reservations[17] = ("opaque-proof", "groq:main:model", "task-1")
        open_url.side_effect = HTTPError(
            "https://engine.example/v1/quota", 409, "conflict", {}, None
        )
        with self.assertRaises(D1QuotaLedgerError):
            self.ledger.settle(17, 90)

    @patch("urllib.request.urlopen")
    def test_ambiguous_transport_failure_raises_without_local_fallback(self, open_url):
        from urllib.error import URLError
        self.ledger._reservations[17] = ("opaque-proof", "groq:main:model", "task-1")
        open_url.side_effect = URLError("connection reset")
        with self.assertRaisesRegex(D1QuotaLedgerError, "transport failed"):
            self.ledger.settle(17, 88)

    def test_cloud_mode_requires_https_and_token(self):
        for url, token in (("http://engine.example", "token"),
                           ("https://engine.example", "")):
            with self.subTest(url=url, token=bool(token)):
                with self.assertRaises(D1QuotaLedgerError):
                    D1QuotaLedger(url, token)


if __name__ == "__main__":
    unittest.main()
