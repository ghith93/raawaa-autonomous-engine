"""Tests for the durable D1 quota ledger: SQL semantics, client, backend selection.

The Worker's atomic reservation SQL is extracted from worker/quota.js and executed
against real SQLite using the production migrations, so limit, window,
per-task and idempotency behaviour is verified against actual SQL.
"""
import json
import re
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from engine.d1_quota_ledger import D1QuotaLedger
from engine.live_runtime import build_live_gateway
from engine.quota_backend import open_quota_ledger, remote_ledger_configured
from engine.quota_manager import QuotaLedger
from engine.remote_task_store import RemoteTaskStoreError

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-11-02T12:00:30.000Z"
DAY = "2026-11-02T00:00:00.000Z"
MONTH = "2026-11-01T00:00:00.000Z"
MINUTE = "2026-11-02T11:59:30.000Z"
BIG = 1_000_000


def reserve_sql() -> str:
    source = (ROOT / "worker" / "quota.js").read_text(encoding="utf-8")
    match = re.search(r"RESERVE_SQL = `(.*?)`;", source, re.S)
    assert match, "RESERVE_SQL not found in worker/quota.js"
    return match.group(1)


class MigrationTests(unittest.TestCase):
    def test_quota_migration_is_additive_and_idempotent(self):
        db = sqlite3.connect(":memory:")
        for name in ("0001_initial.sql", "0002_research_candidates.sql", "0003_quota_ledger.sql"):
            sql = (ROOT / "migrations" / name).read_text(encoding="utf-8")
            db.executescript(sql)
            db.executescript(sql)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"quota_usage", "quota_cooldowns", "quota_audit_events", "tasks"} <= tables)
        versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, ["0001_initial", "0002_research_candidates", "0003_quota_ledger"])

    def test_null_idempotency_keys_may_repeat_but_real_keys_are_unique(self):
        db = sqlite3.connect(":memory:")
        db.executescript((ROOT / "migrations" / "0003_quota_ledger.sql").read_text(encoding="utf-8").replace(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0003_quota_ledger');", ""))
        for _ in range(2):
            db.execute("INSERT INTO quota_usage(route_id,requests,tokens,occurred_at) VALUES('r',1,1,'t')")
        db.execute("INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,idempotency_key) VALUES('r',1,1,'t','k')")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,idempotency_key) VALUES('r',1,1,'t','k')")


@unittest.skipIf(sqlite3.sqlite_version_info < (3, 35, 0), "INSERT ... RETURNING needs SQLite 3.35+")
class ReserveSqlSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        for name in ("0001_initial.sql", "0003_quota_ledger.sql"):
            self.db.executescript((ROOT / "migrations" / name).read_text(encoding="utf-8"))
        self.sql = reserve_sql()

    def reserve(self, route="r", tokens=100, task="t1", key=None, **overrides):
        limits = {"day_req": BIG, "day_tok": BIG, "month_req": BIG, "month_tok": BIG,
                  "min_req": BIG, "min_tok": BIG, "task_req": BIG, "task_tok": BIG}
        limits.update(overrides)
        params = (route, tokens, NOW, task, key or str(uuid.uuid4()),
                  DAY, limits["day_req"], limits["day_tok"],
                  MONTH, limits["month_req"], limits["month_tok"],
                  MINUTE, limits["min_req"], limits["min_tok"],
                  limits["task_req"], limits["task_tok"])
        row = self.db.execute(self.sql, params).fetchone()
        return None if row is None else row[0]

    def insert_old(self, occurred_at, route="r", tokens=100, task="old"):
        self.db.execute(
            "INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,settled_at,task_id) VALUES(?,?,?,?,?,?)",
            (route, 1, tokens, occurred_at, occurred_at, task))

    def test_daily_request_limit_is_enforced(self):
        self.assertIsNotNone(self.reserve(day_req=2))
        self.assertIsNotNone(self.reserve(day_req=2))
        self.assertIsNone(self.reserve(day_req=2))

    def test_daily_token_limit_counts_unsettled_estimates(self):
        self.assertIsNotNone(self.reserve(tokens=600, day_tok=1000))
        self.assertIsNone(self.reserve(tokens=600, day_tok=1000))
        self.assertIsNotNone(self.reserve(tokens=400, day_tok=1000))

    def test_settling_lower_frees_capacity(self):
        rid = self.reserve(tokens=900, day_tok=1000)
        self.assertIsNone(self.reserve(tokens=200, day_tok=1000))
        self.db.execute("UPDATE quota_usage SET tokens=10, settled_at=? WHERE id=?", (NOW, rid))
        self.assertIsNotNone(self.reserve(tokens=200, day_tok=1000))

    def test_per_task_limits_are_independent_between_tasks(self):
        self.assertIsNotNone(self.reserve(task="a", task_req=1))
        self.assertIsNone(self.reserve(task="a", task_req=1))
        self.assertIsNotNone(self.reserve(task="b", task_req=1))
        self.assertIsNone(self.reserve(task="b", tokens=600, task_tok=500))

    def test_minute_window_ignores_older_usage(self):
        self.insert_old("2026-11-02T11:58:00.000Z")
        self.assertIsNotNone(self.reserve(min_req=1))
        self.assertIsNone(self.reserve(min_req=1))

    def test_usage_inside_minute_window_blocks(self):
        self.insert_old("2026-11-02T12:00:10.000Z")
        self.assertIsNone(self.reserve(min_req=1))

    def test_monthly_window_includes_earlier_days_but_not_earlier_months(self):
        self.insert_old("2026-10-31T23:59:00.000Z")
        self.assertIsNotNone(self.reserve(month_req=1))
        self.db.execute("DELETE FROM quota_usage")
        self.insert_old("2026-11-01T01:00:00.000Z")
        self.assertIsNone(self.reserve(month_req=1))
        self.assertIsNotNone(self.reserve(day_req=1))

    def test_routes_do_not_share_route_limits(self):
        self.assertIsNotNone(self.reserve(route="a", day_req=1))
        self.assertIsNotNone(self.reserve(route="b", day_req=1))
        self.assertIsNone(self.reserve(route="a", day_req=1))

    def test_replayed_idempotency_key_cannot_insert_twice(self):
        self.assertIsNotNone(self.reserve(key="same"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.reserve(key="same")
        count = self.db.execute("SELECT COUNT(*) FROM quota_usage").fetchone()[0]
        self.assertEqual(count, 1)


class FakeStore:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _request(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


RESERVE_ARGS = dict(
    estimated_tokens=100, request_limit=10, token_limit=1000,
    monthly_request_limit=100, monthly_token_limit=10000, task_id="task-1",
    per_task_request_limit=2, per_task_token_limit=500,
    minute_request_limit=5, minute_token_limit=400,
)


class D1QuotaLedgerClientTests(unittest.TestCase):
    def test_reserve_returns_id_and_sends_all_limits_with_idempotency_key(self):
        store = FakeStore([{"reserved": True, "reservation_id": 12}])
        self.assertEqual(D1QuotaLedger(store).reserve("route", **RESERVE_ARGS), 12)
        path, method, payload = store.calls[0]
        self.assertEqual((path, method), ("/v1/quota/reserve", "POST"))
        self.assertEqual(payload["route_id"], "route")
        self.assertEqual(payload["minute_token_limit"], 400)
        self.assertEqual(payload["per_task_request_limit"], 2)
        self.assertTrue(payload["idempotency_key"])

    def test_unavailable_capacity_raises_runtime_error_like_local_ledger(self):
        with self.assertRaisesRegex(RuntimeError, "quota reservation unavailable"):
            D1QuotaLedger(FakeStore([{"reserved": False}])).reserve("route", **RESERVE_ARGS)

    def test_transient_failure_is_retried_with_the_same_idempotency_key(self):
        store = FakeStore([RemoteTaskStoreError("temporarily unavailable"),
                           {"reserved": True, "reservation_id": 5, "replayed": True}])
        self.assertEqual(D1QuotaLedger(store).reserve("route", **RESERVE_ARGS), 5)
        self.assertEqual(len(store.calls), 2)
        self.assertEqual(store.calls[0][2]["idempotency_key"], store.calls[1][2]["idempotency_key"])

    def test_persistent_failure_fails_closed(self):
        store = FakeStore([RemoteTaskStoreError("down"), RemoteTaskStoreError("down")])
        with self.assertRaises(RemoteTaskStoreError):
            D1QuotaLedger(store).reserve("route", **RESERVE_ARGS)

    def test_invalid_reservation_input_never_reaches_the_network(self):
        store = FakeStore([])
        ledger = D1QuotaLedger(store)
        for bad in ({"estimated_tokens": 0}, {"task_id": ""}, {"request_limit": None},
                    {"minute_token_limit": True and 0}, {"token_limit": 1.5}):
            with self.assertRaises(ValueError):
                ledger.reserve("route", **{**RESERVE_ARGS, **bad})
        self.assertEqual(store.calls, [])

    def test_invalid_server_reply_is_not_treated_as_a_grant(self):
        with self.assertRaises(RemoteTaskStoreError):
            D1QuotaLedger(FakeStore([{"reserved": "yes"}])).reserve("route", **RESERVE_ARGS)

    def test_settle_outcomes(self):
        D1QuotaLedger(FakeStore([{"ok": True}])).settle(3, 50)
        with self.assertRaises(KeyError):
            D1QuotaLedger(FakeStore([{"ok": False, "error": "not_found"}])).settle(3, 50)
        with self.assertRaisesRegex(RuntimeError, "different usage"):
            D1QuotaLedger(FakeStore([{"ok": False, "error": "conflict"}])).settle(3, 50)
        with self.assertRaises(ValueError):
            D1QuotaLedger(FakeStore([])).settle(3, -1)

    def test_usage_windows_and_cooldown_parse_the_status_response(self):
        status = {"minute": {"requests": 1, "tokens": 10}, "day": {"requests": 2, "tokens": 20},
                  "month": {"requests": 3, "tokens": 30}, "cooldown_active": True}
        ledger = D1QuotaLedger(FakeStore([status] * 4))
        self.assertEqual(ledger.usage_minute("r"), {"requests": 1, "tokens": 10})
        self.assertEqual(ledger.usage_today("r"), {"requests": 2, "tokens": 20})
        self.assertEqual(ledger.usage_month("r"), {"requests": 3, "tokens": 30})
        self.assertIs(ledger.cooldown_active("r"), True)

    def test_malformed_status_fails_closed(self):
        with self.assertRaises(RemoteTaskStoreError):
            D1QuotaLedger(FakeStore([{"day": {"requests": "1"}}])).usage_today("r")
        with self.assertRaises(RemoteTaskStoreError):
            D1QuotaLedger(FakeStore([{}])).cooldown_active("r")

    def test_cooldown_and_task_usage(self):
        store = FakeStore([{"ok": True}, {"requests": 2, "tokens": 40}])
        ledger = D1QuotaLedger(store)
        ledger.cool_down("r", 900, "http_429")
        self.assertEqual(store.calls[0][2], {"route_id": "r", "seconds": 900, "reason": "http_429"})
        self.assertEqual(ledger.usage_for_task("task-1"), {"requests": 2, "tokens": 40})
        with self.assertRaises(ValueError):
            ledger.cool_down("r", 0)

    def test_audit_drops_unlisted_details_and_is_best_effort(self):
        store = FakeStore([{"ok": True, "id": 9}])
        ledger = D1QuotaLedger(store)
        self.assertEqual(ledger.audit_event("quota_reserved", "r", "k",
                                            {"estimated_tokens": 5, "prompt": "secret"}), 9)
        self.assertEqual(store.calls[0][2]["details"], {"estimated_tokens": 5})
        down = D1QuotaLedger(FakeStore([RemoteTaskStoreError("x"), RemoteTaskStoreError("x")]))
        self.assertEqual(down.audit_event("quota_reserved"), 0)


class BackendSelectionTests(unittest.TestCase):
    def test_remote_ledger_is_selected_only_when_api_url_is_configured(self):
        self.assertFalse(remote_ledger_configured({}))
        self.assertFalse(remote_ledger_configured({"RAAWAA_ENGINE_API_URL": "  "}))
        self.assertTrue(remote_ledger_configured({"RAAWAA_ENGINE_API_URL": "https://engine.example"}))

    def test_factory_returns_matching_backend(self):
        env = {"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "t"}
        self.assertIsInstance(open_quota_ledger("unused.db", env), D1QuotaLedger)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(open_quota_ledger(Path(tmp) / "q.db", {}), QuotaLedger)


class FakeLedger:
    def __init__(self, error=None):
        self.error = error

    def cooldown_active(self, route_id, now=None):
        if self.error:
            raise self.error
        return False

    def usage_minute(self, route_id, now=None):
        return {"requests": 0, "tokens": 0}

    usage_today = usage_month = usage_minute


class LiveRuntimeDurableLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.policy_path, self.registry_path, self.db_path = root / "p.json", root / "r.json", root / "e.db"
        self.policy_path.write_text(json.dumps({
            "mode": "live_inference_opt_in", "daily_budget_usd": 0, "monthly_budget_usd": 0,
            "per_task_budget_usd": 0, "allow_paid_model_calls": False, "allow_external_writes": False,
        }), encoding="utf-8")
        registry = json.loads((ROOT / "config" / "provider_profiles.json").read_text(encoding="utf-8"))
        registry["routes"][0]["enabled"] = True
        registry["routes"][0]["verified_at"] = datetime.now(timezone.utc).isoformat()
        self.registry_path.write_text(json.dumps(registry), encoding="utf-8")
        self.env = {"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled", "GROQ_API_KEY": "test-key"}

    def tearDown(self):
        self.temp.cleanup()

    def build(self, env):
        return build_live_gateway(self.db_path, self.policy_path, self.registry_path, environ=env)

    def test_ci_runner_without_durable_ledger_never_builds_a_live_gateway(self):
        self.assertIsNone(self.build({**self.env, "GITHUB_ACTIONS": "true"}))

    def test_local_machine_may_still_use_the_local_ledger(self):
        self.assertIsNotNone(self.build(self.env))

    def test_durable_ledger_allows_ci_gateway(self):
        env = {**self.env, "GITHUB_ACTIONS": "true", "RAAWAA_ENGINE_API_URL": "https://engine.example",
               "RAAWAA_ENGINE_API_TOKEN": "t"}
        with mock.patch("engine.live_runtime.open_quota_ledger", return_value=FakeLedger()):
            self.assertIsNotNone(self.build(env))

    def test_unreachable_durable_ledger_fails_closed(self):
        env = {**self.env, "RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "t"}
        with mock.patch("engine.live_runtime.open_quota_ledger",
                        return_value=FakeLedger(RemoteTaskStoreError("down"))):
            self.assertIsNone(self.build(env))


if __name__ == "__main__":
    unittest.main()
