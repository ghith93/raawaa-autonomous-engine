"""Local SQLite integration checks for the exact Worker D1 quota reservation SQL.

These tests validate migration compatibility, the actual SQL embedded in
worker/quota.js, and serialized concurrent reservation behavior. They are not
a substitute for a non-production Cloudflare D1 concurrency/restart test.
"""
from __future__ import annotations

import concurrent.futures
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def worker_reservation_sql() -> str:
    source = (ROOT / "worker" / "quota.js").read_text(encoding="utf-8")
    match = re.search(
        r"const result = await env\.DB\.prepare\([\x60]\s*(INSERT INTO quota_usage.*?RETURNING id)\s*[\x60]\)\.bind\(",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("Could not locate the quota reservation SQL in worker/quota.js")
    return match.group(1)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reservation_params(
    now: datetime,
    *,
    route_id: str = "groq:primary:model",
    task_id: str = "task-concurrent",
    estimated_tokens: int = 10,
    daily_requests: int = 3,
    daily_tokens: int = 100,
    monthly_requests: int = 100,
    monthly_tokens: int = 1000,
    minute_requests: int = 100,
    minute_tokens: int = 1000,
    task_requests: int = 3,
    task_tokens: int = 100,
) -> list[object]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    minute = now - timedelta(minutes=1)
    stamp = iso(now)
    return [
        route_id, estimated_tokens, stamp, task_id,
        route_id, stamp,
        route_id, iso(day), daily_requests,
        route_id, iso(day), estimated_tokens, daily_tokens,
        route_id, iso(month), monthly_requests,
        route_id, iso(month), estimated_tokens, monthly_tokens,
        route_id, iso(minute), minute_requests,
        route_id, iso(minute), estimated_tokens, minute_tokens,
        task_id, task_requests,
        task_id, estimated_tokens, task_tokens,
    ]


def worker_cooldown_sql() -> str:
    source = (ROOT / "worker" / "quota.js").read_text(encoding="utf-8")
    match = re.search(
        r'const cooldown = await env\.DB\.prepare\("(INSERT INTO quota_cooldowns.*?)"\)\.bind\(',
        source,
    )
    if match is None:
        raise AssertionError("Could not locate the quota cooldown SQL in worker/quota.js")
    return match.group(1)


class D1QuotaSqlIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "quota.sqlite3"
        connection = sqlite3.connect(self.db_path)
        for migration in sorted((ROOT / "migrations").glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))
        connection.close()
        self.sql = worker_reservation_sql()
        self.cooldown_sql = worker_cooldown_sql()

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=15, isolation_level=None)

    def test_all_d1_migrations_apply_and_quota_tables_survive_reopen(self) -> None:
        connection = self.connect()
        versions = {
            row[0] for row in connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        connection.close()
        reopened = self.connect()
        self.assertIn("0001_initial", versions)
        self.assertIn("0002_research_candidates", versions)
        self.assertIn("0003_durable_quota_ledger", versions)
        self.assertTrue({"quota_usage", "quota_cooldowns", "quota_audit_events"} <= tables)
        self.assertEqual(
            reopened.execute("SELECT COUNT(*) FROM quota_usage").fetchone()[0], 0
        )
        reopened.close()

    def test_actual_worker_reservation_sql_is_atomic_under_concurrency(self) -> None:
        now = datetime.now(timezone.utc)
        params = reservation_params(now)

        def reserve() -> int | None:
            connection = self.connect()
            try:
                row = connection.execute(self.sql, params).fetchone()
                return int(row[0]) if row else None
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(lambda _: reserve(), range(12)))

        accepted = [value for value in outcomes if value is not None]
        self.assertEqual(len(accepted), 3)
        connection = self.connect()
        usage = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens),0) FROM quota_usage "
            "WHERE route_id=? AND task_id=?",
            ("groq:primary:model", "task-concurrent"),
        ).fetchone()
        connection.close()
        self.assertEqual(usage, (3, 30))

    def test_persisted_reservation_blocks_second_request_after_reopen(self) -> None:
        now = datetime.now(timezone.utc)
        params = reservation_params(
            now, daily_requests=1, monthly_requests=1,
            minute_requests=1, task_requests=1,
        )
        first = self.connect()
        accepted = first.execute(self.sql, params).fetchone()
        first.close()
        self.assertIsNotNone(accepted)

        reopened = self.connect()
        rejected = reopened.execute(self.sql, params).fetchone()
        reopened.close()
        self.assertIsNone(rejected)

    def test_worker_cooldown_sql_never_shortens_existing_deadline(self) -> None:
        connection = self.connect()
        existing_until = "2099-01-01T00:00:00.000Z"
        existing_updated = "2026-11-01T00:00:00.000Z"
        connection.execute(
            "INSERT INTO quota_cooldowns(route_id,until_at,reason,updated_at) VALUES(?,?,?,?)",
            ("groq:primary:model", existing_until, "long_backoff", existing_updated),
        )

        shorter = connection.execute(
            self.cooldown_sql,
            ("groq:primary:model", "2098-01-01T00:00:00.000Z", "short_retry", "2026-11-03T00:00:00.000Z"),
        ).fetchone()
        self.assertEqual(shorter, (existing_until,))
        row = connection.execute(
            "SELECT until_at,reason,updated_at FROM quota_cooldowns WHERE route_id=?",
            ("groq:primary:model",),
        ).fetchone()
        self.assertEqual(row, (existing_until, "long_backoff", existing_updated))

        longer_until = "2100-01-01T00:00:00.000Z"
        longer = connection.execute(
            self.cooldown_sql,
            ("groq:primary:model", longer_until, "new_backoff", "2026-11-04T00:00:00.000Z"),
        ).fetchone()
        self.assertEqual(longer, (longer_until,))
        row = connection.execute(
            "SELECT until_at,reason,updated_at FROM quota_cooldowns WHERE route_id=?",
            ("groq:primary:model",),
        ).fetchone()
        self.assertEqual(row, (longer_until, "new_backoff", "2026-11-04T00:00:00.000Z"))
        connection.close()

    def test_active_cooldown_blocks_reservation(self) -> None:
        now = datetime.now(timezone.utc)
        connection = self.connect()
        connection.execute(
            "INSERT INTO quota_cooldowns(route_id,until_at,reason,updated_at) "
            "VALUES(?,?,?,?)",
            (
                "groq:primary:model",
                iso(now + timedelta(minutes=5)),
                "rate_limited",
                iso(now),
            ),
        )
        rejected = connection.execute(
            self.sql, reservation_params(now)
        ).fetchone()
        connection.close()
        self.assertIsNone(rejected)


if __name__ == "__main__":
    unittest.main()
