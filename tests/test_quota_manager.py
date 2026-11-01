import tempfile
import unittest
from datetime import datetime, timezone

from engine.quota_manager import QuotaLedger, QuotaManager, QuotaProfile


class QuotaManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = QuotaLedger(self.tmp.name + "/quota.db")
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, **changes):
        values = dict(provider="groq", account="primary", model="model-a",
                      free_verified=True, commercial_use_allowed=True,
                      daily_request_limit=10, daily_token_limit=1000, priority=10)
        values.update(changes)
        return QuotaProfile(**values)

    def test_selects_verified_free_route(self):
        route = self.profile()
        self.assertEqual(QuotaManager([route], self.ledger).select(100, self.now), route)

    def test_unknown_or_unverified_quota_is_not_selected(self):
        unknown = self.profile(daily_request_limit=None)
        unverified = self.profile(account="other", free_verified=False)
        self.assertIsNone(QuotaManager([unknown, unverified], self.ledger).select(10, self.now))

    def test_commercial_restriction_blocks_route(self):
        route = self.profile(commercial_use_allowed=False)
        self.assertIsNone(QuotaManager([route], self.ledger).select(10, self.now))

    def test_daily_request_limit_rotates_to_next_account(self):
        first = self.profile()
        second = self.profile(account="secondary", priority=20)
        for _ in range(10):
            self.ledger.record(first.route_id, 1, self.now)
        self.assertEqual(QuotaManager([first, second], self.ledger).select(1, self.now), second)

    def test_token_limit_blocks_oversized_request(self):
        route = self.profile(daily_token_limit=50)
        self.assertIsNone(QuotaManager([route], self.ledger).select(51, self.now))

    def test_rate_limit_cooldown_skips_provider(self):
        route = self.profile()
        self.ledger.cool_down(route.route_id, 600, now=self.now)
        self.assertIsNone(QuotaManager([route], self.ledger).select(10, self.now))

    def test_records_usage_and_snapshot(self):
        route = self.profile()
        self.ledger.record(route.route_id, 25, self.now)
        row = QuotaManager([route], self.ledger).snapshot(self.now)[0]
        self.assertEqual(row["daily_requests_used"], 1)
        self.assertEqual(row["daily_tokens_used"], 25)


if __name__ == "__main__":
    unittest.main()
