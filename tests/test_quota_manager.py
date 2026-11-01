import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_settlement_is_idempotent_for_same_usage(self):
        route = self.profile()
        reservation = self.ledger.reserve(route.route_id, 100, 10, 1000, self.now)
        self.ledger.settle(reservation, 25)
        self.ledger.settle(reservation, 25)
        self.assertEqual(self.ledger.usage_today(route.route_id, self.now),
                         {"requests": 1, "tokens": 25})

    def test_conflicting_second_settlement_fails_closed(self):
        route = self.profile()
        reservation = self.ledger.reserve(route.route_id, 100, 10, 1000, self.now)
        self.ledger.settle(reservation, 25)
        with self.assertRaisesRegex(RuntimeError, "already settled"):
            self.ledger.settle(reservation, 26)
        self.assertEqual(self.ledger.usage_today(route.route_id, self.now),
                         {"requests": 1, "tokens": 25})

    def test_settlement_rejects_unknown_reservation(self):
        with self.assertRaises(KeyError):
            self.ledger.settle(99999, 25)

    def test_concurrent_reservations_cannot_overspend_last_request(self):
        route = self.profile(daily_request_limit=1, daily_token_limit=100)
        attempts = 8

        def reserve():
            try:
                self.ledger.reserve(route.route_id, 10, 1, 100, self.now)
                return "reserved"
            except RuntimeError:
                return "denied"

        with ThreadPoolExecutor(max_workers=attempts) as pool:
            outcomes = list(pool.map(lambda _: reserve(), range(attempts)))

        self.assertEqual(outcomes.count("reserved"), 1)
        self.assertEqual(outcomes.count("denied"), attempts - 1)
        usage = self.ledger.usage_today(route.route_id, self.now)
        self.assertEqual(usage, {"requests": 1, "tokens": 10})


if __name__ == "__main__":
    unittest.main()
