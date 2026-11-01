import sqlite3
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
                      minute_request_limit=30, minute_token_limit=8000,
                      daily_request_limit=10, daily_token_limit=1000,
                      monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000, minute_request_limit=30, minute_token_limit=8000,
                      per_task_request_limit=5, per_task_token_limit=5000, priority=10,
                      endpoint_url="https://api.groq.com/openai/v1",
                      pricing_source_url="https://groq.com/pricing",
                      quota_source_url="https://console.groq.com/docs/rate-limits",
                      terms_source_url="https://groq.com/terms",
                      verified_at=datetime(2026, 11, 1, 0, tzinfo=timezone.utc),
                      max_output_tokens=512,
                      input_cost_per_million_usd=0.0,
                      output_cost_per_million_usd=0.0)
        values.update(changes)
        return QuotaProfile(**values)

    def test_selects_verified_free_route(self):
        route = self.profile()
        self.assertEqual(QuotaManager([route], self.ledger).select(100, self.now), route)

    def test_unknown_or_unverified_quota_is_not_selected(self):
        unknown = self.profile(monthly_token_limit=None)
        unverified = self.profile(account="other", free_verified=False)
        self.assertIsNone(QuotaManager([unknown, unverified], self.ledger).select(10, self.now))

    def test_unknown_or_nonzero_prices_are_rejected_before_selection(self):
        profiles = [
            self.profile(input_cost_per_million_usd=None),
            self.profile(output_cost_per_million_usd=None),
            self.profile(input_cost_per_million_usd=0.01),
            self.profile(output_cost_per_million_usd=0.01),
            self.profile(input_cost_per_million_usd=float("nan")),
        ]
        manager = QuotaManager(profiles, self.ledger)
        for route in manager.profiles:
            with self.subTest(route=route.route_id, prices=(
                route.input_cost_per_million_usd, route.output_cost_per_million_usd
            )):
                self.assertFalse(manager.eligible(route, 1, self.now))
        self.assertIsNone(manager.select(1, self.now))

    def test_commercial_restriction_blocks_route(self):
        route = self.profile(commercial_use_allowed=False)
        self.assertIsNone(QuotaManager([route], self.ledger).select(10, self.now))

    def test_route_requires_complete_recent_evidence(self):
        missing = self.profile(endpoint_url=None)
        stale = self.profile(verified_at=self.now.replace(year=2026) -
                             __import__("datetime").timedelta(days=31))
        future = self.profile(verified_at=self.now.replace(year=2026) +
                              __import__("datetime").timedelta(days=1))
        no_output_cap = self.profile(max_output_tokens=None)
        manager = QuotaManager([missing, stale, future, no_output_cap], self.ledger)
        for route in manager.profiles:
            self.assertFalse(manager.eligible(route, 1, self.now))

    def test_invalid_https_evidence_urls_are_rejected(self):
        invalid_endpoint = self.profile(endpoint_url="https://")
        credentialed_source = self.profile(
            pricing_source_url="https://user:password@groq.com/pricing"
        )
        placeholder_hosts = [
            self.profile(endpoint_url="https://provider.example/v1"),
            self.profile(pricing_source_url="https://example.com/pricing"),
            self.profile(quota_source_url="https://localhost/limits"),
            self.profile(terms_source_url="https://docs.example.org/terms"),
        ]
        manager = QuotaManager([invalid_endpoint, credentialed_source, *placeholder_hosts], self.ledger)
        for route in manager.profiles:
            self.assertFalse(manager.eligible(route, 1, self.now))

    def test_invalid_quota_limit_types_are_rejected(self):
        bool_minute_limit = self.profile(minute_request_limit=True)
        bool_minute_tokens = self.profile(minute_token_limit=True)
        bool_request_limit = self.profile(daily_request_limit=True)
        bool_token_limit = self.profile(daily_token_limit=True)
        bool_monthly_limit = self.profile(monthly_request_limit=True)
        bool_task_limit = self.profile(per_task_token_limit=True)
        manager = QuotaManager(
            [bool_minute_limit, bool_minute_tokens, bool_request_limit, bool_token_limit, bool_monthly_limit, bool_task_limit],
            self.ledger,
        )
        for route in manager.profiles:
            self.assertFalse(manager.eligible(route, 1, self.now))

    def test_invalid_estimated_token_count_is_rejected(self):
        route = self.profile()
        manager = QuotaManager([route], self.ledger)
        self.assertFalse(manager.eligible(route, True, self.now))
        self.assertFalse(manager.eligible(route, 0, self.now))

    def test_daily_request_limit_rotates_to_next_account(self):
        first = self.profile()
        second = self.profile(account="secondary", priority=20)
        for _ in range(10):
            self.ledger.record(first.route_id, 1, self.now)
        self.assertEqual(QuotaManager([first, second], self.ledger).select(1, self.now), second)

    def test_token_limit_blocks_oversized_request(self):
        route = self.profile(daily_token_limit=50)
        self.assertIsNone(QuotaManager([route], self.ledger).select(51, self.now))

    def test_monthly_token_limit_blocks_route(self):
        route = self.profile(monthly_token_limit=50)
        self.ledger.record(route.route_id, 40, self.now)
        self.assertFalse(QuotaManager([route], self.ledger).eligible(route, 11, self.now))

    def test_monthly_request_limit_blocks_route(self):
        route = self.profile(monthly_request_limit=1)
        self.ledger.record(route.route_id, 1, self.now)
        self.assertFalse(QuotaManager([route], self.ledger).eligible(route, 1, self.now))

    def test_per_task_request_and_token_limits_are_atomic(self):
        route = self.profile(per_task_request_limit=1, per_task_token_limit=50)
        self.ledger.reserve(
            route.route_id, 40, 10, 1000, self.now,
            monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000,
            task_id="task-one", per_task_request_limit=1, per_task_token_limit=50,
        )
        with self.assertRaises(RuntimeError):
            self.ledger.reserve(
                route.route_id, 1, 10, 1000, self.now,
                monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000,
                task_id="task-one", per_task_request_limit=1, per_task_token_limit=50,
            )
        self.assertEqual(self.ledger.usage_for_task("task-one"),
                         {"requests": 1, "tokens": 40})

    def test_per_task_limits_aggregate_across_provider_routes(self):
        first = self.profile(provider="groq", per_task_request_limit=5,
                             per_task_token_limit=50)
        backup = self.profile(provider="cerebras", account="backup",
                              per_task_request_limit=5, per_task_token_limit=50)
        self.ledger.reserve(
            first.route_id, 40, 10, 1000, self.now,
            monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000,
            task_id="shared-task", per_task_request_limit=5, per_task_token_limit=50,
        )
        with self.assertRaises(RuntimeError):
            self.ledger.reserve(
                backup.route_id, 11, 10, 1000, self.now,
                monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000,
                task_id="shared-task", per_task_request_limit=5, per_task_token_limit=50,
            )
        self.assertEqual(self.ledger.usage_for_task("shared-task"),
                         {"requests": 1, "tokens": 40})

    def test_rate_limit_cooldown_skips_provider(self):
        route = self.profile()
        self.ledger.cool_down(route.route_id, 600, now=self.now)
        self.assertIsNone(QuotaManager([route], self.ledger).select(10, self.now))


    def test_rolling_minute_request_and_token_limits(self):
        route = self.profile(minute_request_limit=2, minute_token_limit=50)
        manager = QuotaManager([route], self.ledger)
        self.ledger.record(route.route_id, 20, self.now)
        self.assertFalse(manager.eligible(route, 31, self.now))
        self.assertTrue(manager.eligible(route, 30, self.now))
        self.ledger.record(route.route_id, 1, self.now)
        self.assertFalse(manager.eligible(route, 1, self.now))
        later = self.now.replace(minute=1, second=1)
        self.assertTrue(manager.eligible(route, 30, later))

    def test_minute_caps_are_atomic_under_concurrency(self):
        route = self.profile(minute_request_limit=1, minute_token_limit=100)
        attempts = 6
        def reserve():
            try:
                self.ledger.reserve(
                    route.route_id, 10, 10, 1000, self.now,
                    monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000,
                    task_id="minute-race", per_task_request_limit=10,
                    per_task_token_limit=1000, minute_request_limit=1,
                    minute_token_limit=100,
                )
                return "reserved"
            except RuntimeError:
                return "denied"
        with ThreadPoolExecutor(max_workers=attempts) as pool:
            outcomes = list(pool.map(lambda _: reserve(), range(attempts)))
        self.assertEqual(outcomes.count("reserved"), 1)
        self.assertEqual(outcomes.count("denied"), attempts - 1)
        self.assertEqual(self.ledger.usage_minute(route.route_id, self.now),
                         {"requests": 1, "tokens": 10})

    def test_minute_limit_values_must_be_positive_integers(self):
        for value in (None, 0, True, 1.5):
            route = self.profile(minute_request_limit=value)
            self.assertFalse(QuotaManager([route], self.ledger).eligible(route, 1, self.now))

    def test_records_usage_and_snapshot(self):
        route = self.profile()
        self.ledger.record(route.route_id, 25, self.now)
        row = QuotaManager([route], self.ledger).snapshot(self.now)[0]
        self.assertEqual(row["daily_requests_used"], 1)
        self.assertEqual(row["daily_tokens_used"], 25)
        self.assertEqual(row["monthly_requests_used"], 1)
        self.assertEqual(row["monthly_tokens_used"], 25)

    def test_existing_quota_database_migrates_without_losing_reservation(self):
        path = self.tmp.name + "/legacy-quota.db"
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE quota_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id TEXT NOT NULL,
                requests INTEGER NOT NULL,
                tokens INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )""")
            db.execute(
                "INSERT INTO quota_usage(route_id,requests,tokens,occurred_at) VALUES(?,?,?,?)",
                ("groq:primary:model-a", 1, 100, self.now.isoformat()),
            )

        migrated = QuotaLedger(path)
        migrated.settle(1, 25)
        self.assertEqual(
            migrated.usage_today("groq:primary:model-a", self.now),
            {"requests": 1, "tokens": 25},
        )
        with sqlite3.connect(path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(quota_usage)")}
        self.assertIn("settled_at", columns)
        self.assertIn("task_id", columns)

    def test_settlement_is_idempotent_for_same_usage(self):
        route = self.profile()
        reservation = self.ledger.reserve(route.route_id, 100, 10, 1000, self.now,
                                    monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000,
                                    task_id="task-settle", per_task_request_limit=5,
                                    per_task_token_limit=5000)
        self.ledger.settle(reservation, 25)
        self.ledger.settle(reservation, 25)
        self.assertEqual(self.ledger.usage_today(route.route_id, self.now),
                         {"requests": 1, "tokens": 25})

    def test_conflicting_second_settlement_fails_closed(self):
        route = self.profile()
        reservation = self.ledger.reserve(route.route_id, 100, 10, 1000, self.now,
                                    monthly_request_limit=100, monthly_token_limit=10000,
            minute_request_limit=30, minute_token_limit=8000,
                                    task_id="task-settle", per_task_request_limit=5,
                                    per_task_token_limit=5000)
        self.ledger.settle(reservation, 25)
        with self.assertRaisesRegex(RuntimeError, "already settled"):
            self.ledger.settle(reservation, 26)
        self.assertEqual(self.ledger.usage_today(route.route_id, self.now),
                         {"requests": 1, "tokens": 25})

    def test_settlement_rejects_unknown_reservation(self):
        with self.assertRaises(KeyError):
            self.ledger.settle(99999, 25)

    def test_concurrent_reservations_cannot_overspend_last_request(self):
        route = self.profile(daily_request_limit=10, daily_token_limit=1000,
                             monthly_request_limit=1, monthly_token_limit=100)
        attempts = 8

        def reserve():
            try:
                self.ledger.reserve(route.route_id, 10, 10, 1000, self.now,
                                    monthly_request_limit=1, monthly_token_limit=100,
                                    task_id="task-concurrent", per_task_request_limit=1,
                                    per_task_token_limit=100)
                return "reserved"
            except RuntimeError:
                return "denied"

        with ThreadPoolExecutor(max_workers=attempts) as pool:
            outcomes = list(pool.map(lambda _: reserve(), range(attempts)))

        self.assertEqual(outcomes.count("reserved"), 1)
        self.assertEqual(outcomes.count("denied"), attempts - 1)
        usage = self.ledger.usage_today(route.route_id, self.now)
        self.assertEqual(usage, {"requests": 1, "tokens": 10})
        self.assertEqual(self.ledger.usage_month(route.route_id, self.now),
                         {"requests": 1, "tokens": 10})


if __name__ == "__main__":
    unittest.main()
