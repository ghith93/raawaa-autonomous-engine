import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from engine.remote_quota_ledger import RemoteQuotaLedger, RemoteQuotaLedgerError
from engine.quota_ledger_factory import build_quota_ledger
from engine.quota_manager import QuotaLedger


class FakeResponse:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return json.dumps(self.value).encode()


class RemoteQuotaLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = RemoteQuotaLedger("https://engine.example", "app-token")

    @patch("engine.remote_quota_ledger.urlopen")
    def test_usage_snapshot_reads_server_owned_windows(self, open_url):
        open_url.return_value = FakeResponse({
            "minute": {"requests": 1, "tokens": 200},
            "daily": {"requests": 2, "tokens": 500},
            "monthly": {"requests": 5, "tokens": 1200},
            "cooldown_active": False,
        })
        result = self.ledger.usage_snapshot("groq:account:model")
        self.assertEqual(result["minute"], {"requests": 1, "tokens": 200})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/quota/usage-snapshot")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer app-token")
        self.assertEqual(json.loads(request.data), {"route_id": "groq:account:model"})

    @patch("engine.remote_quota_ledger.urlopen")
    def test_reserve_sends_all_limits_and_returns_server_reservation_id(self, open_url):
        open_url.return_value = FakeResponse({"reservation_id": "uuid-reservation"})
        reservation = self.ledger.reserve(
            "groq:account:model", 500, 1000, 200000,
            monthly_request_limit=10000, monthly_token_limit=2000000,
            task_id="task-1", per_task_request_limit=3, per_task_token_limit=16000,
            minute_request_limit=30, minute_token_limit=8000,
        )
        self.assertEqual(reservation, "uuid-reservation")
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(payload["minute_request_limit"], 30)
        self.assertEqual(payload["daily_token_limit"], 200000)
        self.assertEqual(payload["task_id"], "task-1")

    @patch("engine.remote_quota_ledger.urlopen")
    def test_settle_cooldown_and_audit_use_separate_metadata_endpoints(self, open_url):
        open_url.side_effect = [
            FakeResponse({"ok": True, "settled": True}),
            FakeResponse({"ok": True, "until_at": "2026-11-01T00:15:00Z"}),
            FakeResponse({"id": 7}),
        ]
        self.ledger.settle("reservation-1", 420)
        self.ledger.cool_down("groq:account:model", 900, "http_429")
        event_id = self.ledger.audit_event(
            "completion_received", "groq:account:model", "research",
            {"actual_tokens": 420, "prompt": "never send this"},
        )
        self.assertEqual(event_id, 7)
        urls = [call.args[0].full_url for call in open_url.call_args_list]
        self.assertEqual(urls, [
            "https://engine.example/v1/quota/settle",
            "https://engine.example/v1/quota/cooldown",
            "https://engine.example/v1/quota/audit",
        ])
        audit_payload = json.loads(open_url.call_args_list[2].args[0].data)
        self.assertEqual(audit_payload["details"], {"actual_tokens": 420})

    @patch("engine.remote_quota_ledger.urlopen")
    def test_quota_conflict_is_safe_runtime_error(self, open_url):
        open_url.side_effect = HTTPError(
            "https://engine.example/v1/quota/reserve", 409, "conflict", {}, None
        )
        with self.assertRaisesRegex(RuntimeError, "capacity or settlement conflict") as caught:
            self.ledger.reserve(
                "groq:account:model", 100, 10, 1000,
                monthly_request_limit=100, monthly_token_limit=10000,
                task_id="task-1", per_task_request_limit=2, per_task_token_limit=2000,
                minute_request_limit=5, minute_token_limit=500,
            )
        self.assertNotIn("app-token", str(caught.exception))

    @patch("engine.remote_quota_ledger.urlopen")
    def test_remote_api_failure_never_exposes_token_or_response_body(self, open_url):
        open_url.side_effect = HTTPError(
            "https://engine.example/v1/quota/usage-snapshot", 503,
            "secret provider body", {}, None
        )
        with self.assertRaises(RemoteQuotaLedgerError) as caught:
            self.ledger.usage_snapshot("groq:account:model")
        self.assertNotIn("app-token", str(caught.exception))
        self.assertNotIn("secret provider body", str(caught.exception))

    def test_ledger_factory_uses_remote_store_when_worker_url_is_configured(self):
        ledger = build_quota_ledger(
            "unused.db",
            {"RAAWAA_ENGINE_API_URL": "https://engine.example",
             "RAAWAA_ENGINE_API_TOKEN": "test-token"},
        )
        self.assertIsInstance(ledger, RemoteQuotaLedger)

    def test_ledger_factory_keeps_local_sqlite_for_local_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ledger = build_quota_ledger(
                tmp + "/quota.db", {},
            )
            self.assertIsInstance(ledger, QuotaLedger)


if __name__ == "__main__":
    unittest.main()
