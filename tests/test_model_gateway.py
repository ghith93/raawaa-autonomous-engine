import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from engine.model_router import ModelOption
from engine.provider import GenerationRequest, GenerationResponse, MockAdapter
from engine.model_gateway import BudgetExceeded, ModelGateway, ProviderUnavailable, UsageLedger


class CostAdapter:
    def __init__(self, cost_usd=0.0, error=None):
        self.cost_usd = cost_usd
        self.error = error
        self.calls = 0

    def generate(self, model, request):
        self.calls += 1
        if self.error:
            raise self.error
        return GenerationResponse("mock", model, "generated", 10, 5, self.cost_usd, mocked=False)


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "usage.db"
        self.ledger = UsageLedger(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mock_offline_path(self):
        gateway = ModelGateway(
            [ModelOption("mock", "offline", "free", True, 0.0)],
            {"mock": MockAdapter()}, self.ledger, network_calls_enabled=False,
        )
        result = gateway.generate(GenerationRequest("offline_test", "test prompt"))
        self.assertTrue(result.mocked)
        self.assertEqual(result.cost_usd, 0.0)
        self.assertEqual(self.ledger.totals()["daily_usd"], 0.0)
        self.assertEqual(self.ledger.totals()["daily_reserved_usd"], 0.0)

    def test_live_task_blocked_by_default(self):
        gateway = ModelGateway(
            [ModelOption("mock", "offline", "free", True, 0.0)],
            {"mock": MockAdapter()}, self.ledger,
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(GenerationRequest("research", "test"))

    def test_live_request_requires_task_id_before_provider_call(self):
        adapter = CostAdapter(cost_usd=0.0)
        gateway = ModelGateway(
            [ModelOption("mock", "free-model", "free", True, 0.0)],
            {"mock": adapter}, self.ledger, network_calls_enabled=True,
        )
        with self.assertRaisesRegex(ProviderUnavailable, "task ID is required"):
            gateway.generate(GenerationRequest("research", "live request"))
        self.assertEqual(adapter.calls, 0)

    def test_no_model_fails_closed(self):
        gateway = ModelGateway([], {"mock": MockAdapter()}, self.ledger, network_calls_enabled=True)
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(GenerationRequest("research", "test"))

    def test_zero_cost_model_is_allowed_with_zero_budget(self):
        gateway = ModelGateway(
            [ModelOption("mock", "offline", "free", True, 0.0)],
            {"mock": MockAdapter()}, self.ledger,
            daily_budget_usd=0.0, monthly_budget_usd=0.0,
        )
        result = gateway.generate(GenerationRequest("offline_test", "test"))
        self.assertEqual(result.cost_usd, 0.0)

    def test_paid_model_not_selected_when_paid_calls_disabled(self):
        gateway = ModelGateway(
            [ModelOption("mock", "paid-model", "paid", True, 0.1)],
            {"mock": MockAdapter()}, self.ledger,
            daily_budget_usd=1.0, monthly_budget_usd=1.0,
            allow_paid_calls=False, network_calls_enabled=True,
        )
        with self.assertRaises(ProviderUnavailable):
            gateway.generate(GenerationRequest("research", "test"))

    def test_usage_ledger_rejects_invalid_tokens_and_non_finite_costs(self):
        for kwargs in [
            {"input_tokens": -1, "output_tokens": 1, "cost_usd": 0.0},
            {"input_tokens": 1, "output_tokens": -1, "cost_usd": 0.0},
            {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("nan")},
            {"input_tokens": 1, "output_tokens": 1, "cost_usd": float("inf")},
        ]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.ledger.record("provider", "model", "task", **kwargs)

    def test_status_exposes_no_live_adapter(self):
        gateway = ModelGateway([], {}, self.ledger)
        self.assertFalse(gateway.status()["live_provider_adapters_enabled"])

    def test_reservations_atomically_enforce_daily_and_monthly_budgets(self):
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        reservation = self.ledger.reserve("mock", "model", "research", 0.60, 1.0, 2.0, now, task_id="task-a", per_task_budget_usd=3.0)
        self.assertGreater(reservation, 0)
        self.assertEqual(self.ledger.totals(now)["daily_reserved_usd"], 0.60)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("mock", "model", "research", 0.41, 1.0, 2.0, now, task_id="task-a", per_task_budget_usd=3.0)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("mock", "model", "research", 1.50, 2.0, 1.0, now, task_id="task-a", per_task_budget_usd=3.0)

    def test_settlement_records_actual_cost_and_is_idempotent(self):
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        reservation = self.ledger.reserve("mock", "model", "research", 0.50, 1.0, 1.0, now, task_id="task-a", per_task_budget_usd=1.0)
        result = self.ledger.settle(reservation, 10, 5, 0.25, now)
        self.assertEqual(result["daily_usd"], 0.25)
        self.assertEqual(result["daily_reserved_usd"], 0.0)
        self.ledger.settle(reservation, 10, 5, 0.25, now)
        self.assertEqual(self.ledger.totals(now)["daily_usd"], 0.25)
        with self.assertRaisesRegex(RuntimeError, "different usage"):
            self.ledger.settle(reservation, 10, 5, 0.26, now)

    def test_ambiguous_reservation_is_counted_across_date_boundaries(self):
        before_midnight = datetime(2026, 11, 2, 23, 59, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 11, 3, 0, 1, tzinfo=timezone.utc)
        self.ledger.reserve("mock", "model", "research", 0.60, 1.0, 2.0, before_midnight, task_id="task-a", per_task_budget_usd=2.0)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("mock", "model", "research", 0.50, 1.0, 2.0, after_midnight, task_id="task-a", per_task_budget_usd=2.0)
        self.assertEqual(self.ledger.totals(after_midnight)["daily_reserved_usd"], 0.60)

    def test_estimated_cost_budget_blocks_before_provider_call(self):
        adapter = CostAdapter(cost_usd=0.0)
        gateway = ModelGateway(
            [ModelOption("mock", "paid-model", "paid", True, 1.0)],
            {"mock": adapter}, self.ledger,
            daily_budget_usd=0.01, monthly_budget_usd=0.10,
            allow_paid_calls=True, network_calls_enabled=True, per_task_budget_usd=1.0,
        )
        with self.assertRaises(BudgetExceeded):
            gateway.generate(GenerationRequest("research", "short prompt", 10, task_id="task-a"))
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.ledger.totals()["daily_reserved_usd"], 0.0)

    def test_ambiguous_adapter_failure_keeps_reservation_consuming_budget(self):
        adapter = CostAdapter(error=TimeoutError("ambiguous"))
        gateway = ModelGateway(
            [ModelOption("mock", "paid-model", "paid", True, 0.01)],
            {"mock": adapter}, self.ledger,
            daily_budget_usd=1.0, monthly_budget_usd=1.0,
            allow_paid_calls=True, network_calls_enabled=True, per_task_budget_usd=1.0,
        )
        with self.assertRaises(TimeoutError):
            gateway.generate(GenerationRequest("research", "short prompt", 10, task_id="task-a"))
        self.assertEqual(adapter.calls, 1)
        self.assertGreater(self.ledger.totals()["daily_reserved_usd"], 0.0)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve("mock", "model", "research", 1.0, 1.0, 1.0)

    def test_unexpected_nonzero_cost_is_recorded_even_when_paid_calls_are_disabled(self):
        adapter = CostAdapter(cost_usd=0.02)
        gateway = ModelGateway(
            [ModelOption("mock", "free-model", "free", True, 0.0)],
            {"mock": adapter}, self.ledger,
            daily_budget_usd=0.0, monthly_budget_usd=0.0,
            allow_paid_calls=False, network_calls_enabled=True,
        )
        with self.assertRaisesRegex(BudgetExceeded, "actual usage was recorded"):
            gateway.generate(GenerationRequest("research", "short prompt", 10, task_id="task-a"))
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.ledger.totals()["daily_usd"], 0.02)

    def test_actual_cost_overrun_is_recorded_before_gateway_blocks(self):
        adapter = CostAdapter(cost_usd=0.02)
        gateway = ModelGateway(
            [ModelOption("mock", "paid-model", "paid", True, 0.001)],
            {"mock": adapter}, self.ledger,
            daily_budget_usd=0.01, monthly_budget_usd=0.05,
            allow_paid_calls=True, network_calls_enabled=True, per_task_budget_usd=1.0,
        )
        with self.assertRaisesRegex(BudgetExceeded, "Actual provider cost exceeded"):
            gateway.generate(GenerationRequest("research", "short prompt", 10, task_id="task-a"))
        self.assertEqual(self.ledger.totals()["daily_usd"], 0.02)

    def test_per_task_budget_is_aggregated_across_provider_routes(self):
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        first = self.ledger.reserve(
            "groq", "model-a", "research", 0.30, 1.0, 1.0, now,
            task_id="task-a", per_task_budget_usd=0.50,
        )
        with self.assertRaisesRegex(BudgetExceeded, "per-task"):
            self.ledger.reserve(
                "cerebras", "model-b", "research", 0.21, 1.0, 1.0, now,
                task_id="task-a", per_task_budget_usd=0.50,
            )
        second = self.ledger.reserve(
            "cerebras", "model-b", "research", 0.30, 1.0, 1.0, now,
            task_id="task-b", per_task_budget_usd=0.50,
        )
        self.assertGreater(first, 0)
        self.assertGreater(second, 0)
        self.assertEqual(self.ledger.task_totals("task-a")["reserved_usd"], 0.30)
        self.assertEqual(self.ledger.task_totals("task-b")["reserved_usd"], 0.30)

    def test_task_settlement_preserves_task_id_and_actual_cost(self):
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        reservation = self.ledger.reserve(
            "groq", "model-a", "research", 0.30, 1.0, 1.0, now,
            task_id="task-a", per_task_budget_usd=0.50,
        )
        self.ledger.settle(reservation, 10, 5, 0.20, now)
        totals = self.ledger.task_totals("task-a")
        self.assertEqual(totals["actual_usd"], 0.20)
        self.assertEqual(totals["reserved_usd"], 0.0)
        self.assertEqual(totals["pending_reservations"], 0)

    def test_per_task_budget_blocks_model_before_provider_call(self):
        adapter = CostAdapter(cost_usd=0.0)
        gateway = ModelGateway(
            [ModelOption("mock", "paid-model", "paid", True, 1.0)],
            {"mock": adapter}, self.ledger,
            daily_budget_usd=1.0, monthly_budget_usd=1.0,
            allow_paid_calls=True, network_calls_enabled=True,
            per_task_budget_usd=0.01,
        )
        with self.assertRaisesRegex(BudgetExceeded, "per-task"):
            gateway.generate(GenerationRequest(
                "research", "short prompt", 10, task_id="task-a"
            ))
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.ledger.task_totals("task-a")["pending_reservations"], 0)

    def test_concurrent_reservations_cannot_exceed_remaining_budget(self):
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)

        def reserve():
            try:
                self.ledger.reserve("mock", "model", "research", 0.20, 1.0, 1.0, now, task_id="task-a", per_task_budget_usd=1.0)
                return "reserved"
            except BudgetExceeded:
                return "blocked"

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: reserve(), range(10)))
        self.assertEqual(results.count("reserved"), 5)
        self.assertEqual(results.count("blocked"), 5)
        self.assertAlmostEqual(self.ledger.totals(now)["daily_reserved_usd"], 1.0)

    def test_legacy_usage_database_migrates_and_preserves_cost(self):
        path = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE model_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, model TEXT NOT NULL,
                task_kind TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL CHECK(cost_usd >= 0), created_at TEXT NOT NULL)""")
            db.execute(
                "INSERT INTO model_usage(provider,model,task_kind,input_tokens,output_tokens,cost_usd,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("mock", "model", "research", 10, 5, 0.25, "2026-11-02T12:00:00+00:00"),
            )
            db.execute("""CREATE TABLE model_usage_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, model TEXT NOT NULL,
                task_kind TEXT NOT NULL, created_at TEXT NOT NULL,
                estimated_cost_nanos INTEGER NOT NULL CHECK(estimated_cost_nanos >= 0),
                settled_at TEXT, actual_cost_nanos INTEGER, input_tokens INTEGER, output_tokens INTEGER
            )""")
            db.execute(
                "INSERT INTO model_usage_reservations(provider,model,task_kind,created_at,estimated_cost_nanos) "
                "VALUES(?,?,?,?,?)",
                ("mock", "model", "research", "2026-11-02T12:00:00+00:00", 100),
            )
        migrated = UsageLedger(path)
        self.assertAlmostEqual(
            migrated.totals(datetime(2026, 11, 2, 13, tzinfo=timezone.utc))["daily_usd"], 0.25
        )
        with sqlite3.connect(path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(model_usage)")}
        self.assertIn("cost_nanos", columns)
        self.assertIn("task_id", columns)
        with sqlite3.connect(path) as db:
            reservation_columns = {row[1] for row in db.execute("PRAGMA table_info(model_usage_reservations)")}
        self.assertIn("task_id", reservation_columns)


if __name__ == "__main__":
    unittest.main()
