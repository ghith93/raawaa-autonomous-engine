"""Policy-enforced model gateway with atomic monetary reservations and usage accounting."""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any

from engine.provider import GenerationRequest, ProviderAdapter
from engine.model_router import ModelOption, ModelRouter


NANOS_PER_USD = Decimal("1000000000")


class BudgetExceeded(RuntimeError):
    pass


class ProviderUnavailable(RuntimeError):
    pass


def _money_nanos(value: float | int, *, round_up: bool) -> int:
    """Convert USD to integer nano-dollars; round estimates/actuals up and caps down."""
    if isinstance(value, bool):
        raise ValueError("monetary values must be finite non-negative numbers")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("monetary values must be finite non-negative numbers") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("monetary values must be finite non-negative numbers")
    rounding = ROUND_CEILING if round_up else ROUND_FLOOR
    return int((amount * NANOS_PER_USD).to_integral_value(rounding=rounding))


def _utc(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class UsageLedger:
    """Persistent spend ledger with atomic pre-call reservations and idempotent settlement.

    An unsettled reservation is deliberately retained after an ambiguous provider error.
    It is counted against every subsequent daily/monthly budget until reconciled; it must
    never be silently released and reused.
    """

    def __init__(self, db_path: str | Path = "data/engine.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS model_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, model TEXT NOT NULL,
                task_kind TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL CHECK(cost_usd >= 0), created_at TEXT NOT NULL)""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(model_usage)")}
            if "cost_nanos" not in columns:
                db.execute("ALTER TABLE model_usage ADD COLUMN cost_nanos INTEGER NOT NULL DEFAULT 0")
                db.execute(
                    "UPDATE model_usage SET cost_nanos=CAST(ROUND(cost_usd * 1000000000) AS INTEGER) "
                    "WHERE cost_usd > 0"
                )
            if "task_id" not in columns:
                db.execute("ALTER TABLE model_usage ADD COLUMN task_id TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS model_usage_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                task_kind TEXT NOT NULL,
                task_id TEXT,
                created_at TEXT NOT NULL,
                estimated_cost_nanos INTEGER NOT NULL CHECK(estimated_cost_nanos >= 0),
                settled_at TEXT,
                actual_cost_nanos INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER
            )""")
            reservation_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(model_usage_reservations)")
            }
            if "task_id" not in reservation_columns:
                db.execute("ALTER TABLE model_usage_reservations ADD COLUMN task_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_reservations_created "
                "ON model_usage_reservations(created_at, settled_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_usage_task "
                "ON model_usage(task_id, created_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_reservations_task "
                "ON model_usage_reservations(task_id, settled_at)"
            )

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _spend_nanos(db, now: datetime) -> tuple[int, int, int, int]:
        day = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        daily_spent = db.execute(
            "SELECT COALESCE(SUM(cost_nanos),0) n FROM model_usage WHERE substr(created_at,1,10)=?",
            (day,),
        ).fetchone()["n"]
        monthly_spent = db.execute(
            "SELECT COALESCE(SUM(cost_nanos),0) n FROM model_usage WHERE substr(created_at,1,7)=?",
            (month,),
        ).fetchone()["n"]
        # Ambiguous in-flight calls remain reserved across date boundaries. Counting
        # them in every future window is intentionally conservative until reconciled.
        pending = db.execute(
            "SELECT COALESCE(SUM(estimated_cost_nanos),0) n FROM model_usage_reservations "
            "WHERE settled_at IS NULL"
        ).fetchone()["n"]
        return int(daily_spent), int(monthly_spent), int(pending), int(pending)

    def totals(self, now: datetime | None = None) -> dict[str, float | int]:
        stamp = _utc(now)
        with self._connect() as db:
            daily, monthly, daily_reserved, monthly_reserved = self._spend_nanos(db, stamp)
            pending_count = int(db.execute(
                "SELECT COUNT(*) n FROM model_usage_reservations WHERE settled_at IS NULL"
            ).fetchone()["n"])
        return {
            "daily_usd": daily / 1_000_000_000,
            "monthly_usd": monthly / 1_000_000_000,
            "daily_reserved_usd": daily_reserved / 1_000_000_000,
            "monthly_reserved_usd": monthly_reserved / 1_000_000_000,
            "daily_total_nanos": daily + daily_reserved,
            "monthly_total_nanos": monthly + monthly_reserved,
            "pending_reservations": pending_count,
        }

    def pending_reservations(self) -> list[dict[str, Any]]:
        """Return safe metadata for unresolved monetary reservations; never releases them."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,provider,model,task_kind,task_id,created_at,estimated_cost_nanos "
                "FROM model_usage_reservations WHERE settled_at IS NULL ORDER BY id"
            ).fetchall()
        return [{
            "id": int(row["id"]),
            "provider": row["provider"],
            "model": row["model"],
            "task_kind": row["task_kind"],
            "task_id": row["task_id"],
            "created_at": row["created_at"],
            "estimated_cost_usd": int(row["estimated_cost_nanos"]) / 1_000_000_000,
        } for row in rows]

    def task_totals(self, task_id: str) -> dict[str, float | int]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required for task spend totals")
        with self._connect() as db:
            actual = int(db.execute(
                "SELECT COALESCE(SUM(cost_nanos),0) n FROM model_usage WHERE task_id=?",
                (task_id,),
            ).fetchone()["n"])
            reserved = int(db.execute(
                "SELECT COALESCE(SUM(estimated_cost_nanos),0) n "
                "FROM model_usage_reservations WHERE task_id=? AND settled_at IS NULL",
                (task_id,),
            ).fetchone()["n"])
            pending = int(db.execute(
                "SELECT COUNT(*) n FROM model_usage_reservations "
                "WHERE task_id=? AND settled_at IS NULL",
                (task_id,),
            ).fetchone()["n"])
        return {
            "actual_usd": actual / 1_000_000_000,
            "reserved_usd": reserved / 1_000_000_000,
            "total_nanos": actual + reserved,
            "pending_reservations": pending,
        }

    def task_summaries(self, per_task_budget_usd: float = 0.0) -> list[dict[str, Any]]:
        """Return per-task spend totals and exact hard-cap status."""
        cap = _money_nanos(per_task_budget_usd, round_up=False)
        with self._connect() as db:
            rows = db.execute(
                "SELECT task_id FROM model_usage WHERE task_id IS NOT NULL "
                "UNION SELECT task_id FROM model_usage_reservations WHERE task_id IS NOT NULL "
                "ORDER BY task_id"
            ).fetchall()
        summaries = []
        for row in rows:
            task_id = row["task_id"]
            totals = self.task_totals(task_id)
            summaries.append({
                "task_id": task_id,
                **totals,
                "over_budget": totals["total_nanos"] > cap,
            })
        return summaries

    def budget_status(self, daily_budget_usd: float, monthly_budget_usd: float,
                      now: datetime | None = None, task_id: str | None = None,
                      per_task_budget_usd: float = 0.0) -> dict[str, bool]:
        """Compare actual plus outstanding spend to all hard caps using integer nano-USD."""
        daily_cap = _money_nanos(daily_budget_usd, round_up=False)
        monthly_cap = _money_nanos(monthly_budget_usd, round_up=False)
        task_cap = _money_nanos(per_task_budget_usd, round_up=False)
        if task_cap > 0 and task_id is None:
            raise ValueError("task_id is required when a per-task monetary budget is configured")
        totals = self.totals(now)
        task_total = self.task_totals(task_id)["total_nanos"] if task_id is not None else 0
        return {
            "daily_over_budget": totals["daily_total_nanos"] > daily_cap,
            "monthly_over_budget": totals["monthly_total_nanos"] > monthly_cap,
            "per_task_over_budget": task_total > task_cap,
        }

    def release(self, reservation_id: int) -> None:
        """Release only a pre-call reservation when no provider request was sent."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT settled_at FROM model_usage_reservations WHERE id=?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError("monetary reservation not found")
            if row["settled_at"] is not None:
                raise RuntimeError("settled monetary reservations cannot be released")
            changed = db.execute(
                "DELETE FROM model_usage_reservations WHERE id=? AND settled_at IS NULL",
                (reservation_id,),
            ).rowcount
            if changed != 1:
                raise RuntimeError("monetary reservation release state changed")

    def reserve(self, provider: str, model: str, task_kind: str,
                estimated_cost_usd: float, daily_budget_usd: float,
                monthly_budget_usd: float, now: datetime | None = None,
                task_id: str | None = None,
                per_task_budget_usd: float = 0.0) -> int:
        """Atomically reserve estimated spend before a provider call."""
        if not all(isinstance(value, str) and value.strip()
                   for value in (provider, model, task_kind)):
            raise ValueError("provider, model and task_kind are required")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be a non-empty string when supplied")
        task_cap = _money_nanos(per_task_budget_usd, round_up=False)
        if task_cap > 0 and task_id is None:
            raise ValueError("task_id is required when a per-task monetary budget is configured")
        estimate = _money_nanos(estimated_cost_usd, round_up=True)
        if estimate > 0 and task_id is None:
            raise ValueError("task_id is required for every non-zero-cost reservation")
        daily_cap = _money_nanos(daily_budget_usd, round_up=False)
        monthly_cap = _money_nanos(monthly_budget_usd, round_up=False)
        current = _utc(now)
        stamp = current.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            daily, monthly, daily_reserved, monthly_reserved = self._spend_nanos(db, current)
            if daily + daily_reserved + estimate > daily_cap:
                raise BudgetExceeded("Estimated request cost exceeds the remaining daily budget.")
            if monthly + monthly_reserved + estimate > monthly_cap:
                raise BudgetExceeded("Estimated request cost exceeds the remaining monthly budget.")
            if task_id is not None:
                task_actual = int(db.execute(
                    "SELECT COALESCE(SUM(cost_nanos),0) n FROM model_usage WHERE task_id=?",
                    (task_id,),
                ).fetchone()["n"])
                task_reserved = int(db.execute(
                    "SELECT COALESCE(SUM(estimated_cost_nanos),0) n "
                    "FROM model_usage_reservations WHERE task_id=? AND settled_at IS NULL",
                    (task_id,),
                ).fetchone()["n"])
                if task_actual + task_reserved + estimate > task_cap:
                    raise BudgetExceeded("Estimated request cost exceeds the remaining per-task budget.")
            cursor = db.execute(
                "INSERT INTO model_usage_reservations("
                "provider,model,task_kind,task_id,created_at,estimated_cost_nanos) "
                "VALUES(?,?,?,?,?,?)",
                (provider, model, task_kind, task_id, stamp, estimate),
            )
            return int(cursor.lastrowid)

    def settle(self, reservation_id: int, input_tokens: int, output_tokens: int,
               actual_cost_usd: float, now: datetime | None = None) -> dict[str, Any]:
        """Record actual cost exactly once; return integer spend totals for hard-cap checks."""
        if type(input_tokens) is not int or type(output_tokens) is not int:
            raise ValueError("token counts must be integers")
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        actual = _money_nanos(actual_cost_usd, round_up=True)
        current = _utc(now)
        stamp = current.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reservation = db.execute(
                "SELECT * FROM model_usage_reservations WHERE id=?", (reservation_id,)
            ).fetchone()
            if reservation is None:
                raise KeyError("monetary reservation not found")
            if reservation["settled_at"] is not None:
                same = (
                    int(reservation["actual_cost_nanos"]) == actual
                    and int(reservation["input_tokens"]) == input_tokens
                    and int(reservation["output_tokens"]) == output_tokens
                )
                if not same:
                    raise RuntimeError("monetary reservation already settled with different usage")
            else:
                db.execute(
                    "INSERT INTO model_usage(provider,model,task_kind,input_tokens,output_tokens,"
                    "cost_usd,cost_nanos,created_at,task_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        reservation["provider"], reservation["model"], reservation["task_kind"],
                        input_tokens, output_tokens, actual / 1_000_000_000, actual, stamp,
                        reservation["task_id"],
                    ),
                )
                changed = db.execute(
                    "UPDATE model_usage_reservations SET settled_at=?,actual_cost_nanos=?,"
                    "input_tokens=?,output_tokens=? WHERE id=? AND settled_at IS NULL",
                    (stamp, actual, input_tokens, output_tokens, reservation_id),
                ).rowcount
                if changed != 1:
                    raise RuntimeError("monetary reservation settlement state changed")
            daily, monthly, daily_reserved, monthly_reserved = self._spend_nanos(db, current)
        return {
            "daily_usd": daily / 1_000_000_000,
            "monthly_usd": monthly / 1_000_000_000,
            "daily_reserved_usd": daily_reserved / 1_000_000_000,
            "monthly_reserved_usd": monthly_reserved / 1_000_000_000,
            "daily_total_nanos": daily + daily_reserved,
            "monthly_total_nanos": monthly + monthly_reserved,
        }

    def record(self, provider: str, model: str, task_kind: str, input_tokens: int,
               output_tokens: int, cost_usd: float, task_id: str | None = None):
        """Record externally measured usage (e.g. a verified-free route anomaly)."""
        if type(input_tokens) is not int or type(output_tokens) is not int:
            raise ValueError("token counts must be integers")
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        cost = _money_nanos(cost_usd, round_up=True)
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be a non-empty string when supplied")
        if cost > 0 and task_id is None:
            raise ValueError("task_id is required to record non-zero provider cost")
        with self._connect() as db:
            db.execute(
                "INSERT INTO model_usage(provider,model,task_kind,input_tokens,output_tokens,"
                "cost_usd,cost_nanos,created_at,task_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (provider, model, task_kind, input_tokens, output_tokens,
                 cost / 1_000_000_000, cost, datetime.now(timezone.utc).isoformat(), task_id),
            )


class ModelGateway:
    def __init__(self, options: list[ModelOption], adapters: dict[str, ProviderAdapter],
                 ledger: UsageLedger, daily_budget_usd: float = 0.0,
                 monthly_budget_usd: float = 0.0, allow_paid_calls: bool = False,
                 network_calls_enabled: bool = False, per_task_budget_usd: float = 0.0):
        self.options = options
        self.adapters = adapters
        self.ledger = ledger
        self.daily_budget_usd = daily_budget_usd
        self.monthly_budget_usd = monthly_budget_usd
        _money_nanos(daily_budget_usd, round_up=False)
        _money_nanos(monthly_budget_usd, round_up=False)
        _money_nanos(per_task_budget_usd, round_up=False)
        self.per_task_budget_usd = per_task_budget_usd
        self.allow_paid_calls = allow_paid_calls
        self.network_calls_enabled = network_calls_enabled
        self.router = ModelRouter(options, paid_calls_enabled=allow_paid_calls)

    def status(self) -> dict[str, Any]:
        return {
            "network_calls_enabled": self.network_calls_enabled,
            "live_provider_adapters_enabled": False,
            "registered_adapters": sorted(self.adapters),
            "paid_calls_enabled": self.allow_paid_calls,
            "budgets": {
                "daily_usd": self.daily_budget_usd,
                "monthly_usd": self.monthly_budget_usd,
                "per_task_usd": self.per_task_budget_usd,
            },
            "usage": self.ledger.totals(),
            "note": "Only explicitly injected adapters can run; repository ships no live network adapter.",
        }

    def generate(self, request: GenerationRequest):
        if not self.network_calls_enabled and request.task_kind != "offline_test":
            raise ProviderUnavailable("Model execution is disabled by policy.")
        model = self.router.select(request.task_kind)
        if model is None:
            raise ProviderUnavailable("No eligible model is configured.")
        if request.task_kind != "offline_test" and not request.task_id:
            raise ProviderUnavailable("A task ID is required before live provider execution.")
        adapter = self.adapters.get(model.provider)
        if adapter is None:
            raise ProviderUnavailable(f"No adapter registered for provider: {model.provider}")

        estimated_tokens = max(1, len(request.prompt.split())) + request.max_output_tokens
        estimated_cost = (estimated_tokens / 1000.0) * model.estimated_cost_per_1k_tokens_usd
        if estimated_cost > 0 and not self.allow_paid_calls:
            raise BudgetExceeded("Paid model calls are disabled.")

        reservation_id = self.ledger.reserve(
            model.provider, model.model, request.task_kind, estimated_cost,
            self.daily_budget_usd, self.monthly_budget_usd,
            task_id=request.task_id, per_task_budget_usd=self.per_task_budget_usd,
        )
        # If the adapter raises or times out, the reservation intentionally remains
        # outstanding because provider-side processing/billing may be ambiguous.
        response = adapter.generate(model.model, request)
        if isinstance(getattr(response, "cost_usd", None), bool) or not isinstance(
            getattr(response, "cost_usd", None), (int, float)
        ):
            raise ValueError("provider response cost must be numeric")
        if not isinstance(getattr(response, "input_tokens", None), int) or not isinstance(
            getattr(response, "output_tokens", None), int
        ):
            raise ValueError("provider response token counts must be integers")
        if not math.isfinite(response.cost_usd) or response.cost_usd < 0:
            raise ValueError("provider response cost must be finite and non-negative")

        settlement = self.ledger.settle(
            reservation_id, response.input_tokens, response.output_tokens, response.cost_usd,
        )
        if response.cost_usd > 0 and not self.allow_paid_calls:
            raise BudgetExceeded(
                "Provider reported unexpected non-zero cost; actual usage was recorded."
            )
        daily_cap = _money_nanos(self.daily_budget_usd, round_up=False)
        monthly_cap = _money_nanos(self.monthly_budget_usd, round_up=False)
        budget_status = self.ledger.budget_status(
            self.daily_budget_usd, self.monthly_budget_usd,
            task_id=request.task_id, per_task_budget_usd=self.per_task_budget_usd,
        )
        if (settlement["daily_total_nanos"] > daily_cap
                or settlement["monthly_total_nanos"] > monthly_cap
                or budget_status["per_task_over_budget"]):
            raise BudgetExceeded(
                "Actual provider cost exceeded a configured spend budget; actual usage was recorded."
            )
        return response
