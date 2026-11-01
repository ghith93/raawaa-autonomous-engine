"""Policy-enforced model gateway with persistent usage accounting and no live adapters."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from engine.provider import GenerationRequest, ProviderAdapter
from engine.model_router import ModelOption, ModelRouter


class BudgetExceeded(RuntimeError):
    pass


class ProviderUnavailable(RuntimeError):
    pass


class UsageLedger:
    def __init__(self, db_path: str | Path = "data/engine.db"):
        self.db_path=Path(db_path); self.db_path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS model_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, model TEXT NOT NULL,
                task_kind TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL CHECK(cost_usd >= 0), created_at TEXT NOT NULL)""")
    def _connect(self):
        db=sqlite3.connect(self.db_path,timeout=10); db.row_factory=sqlite3.Row; return db
    def totals(self) -> dict[str,float]:
        now=datetime.now(timezone.utc)
        day=now.strftime("%Y-%m-%d")
        month=now.strftime("%Y-%m")
        with self._connect() as db:
            daily=db.execute("SELECT COALESCE(SUM(cost_usd),0) n FROM model_usage WHERE substr(created_at,1,10)=?",(day,)).fetchone()["n"]
            monthly=db.execute("SELECT COALESCE(SUM(cost_usd),0) n FROM model_usage WHERE substr(created_at,1,7)=?",(month,)).fetchone()["n"]
        return {"daily_usd":float(daily),"monthly_usd":float(monthly)}
    def record(self, provider: str, model: str, task_kind: str, input_tokens: int,
               output_tokens: int, cost_usd: float):
        if cost_usd < 0: raise ValueError("cost cannot be negative")
        with self._connect() as db:
            db.execute("INSERT INTO model_usage(provider,model,task_kind,input_tokens,output_tokens,cost_usd,created_at) VALUES(?,?,?,?,?,?,?)",
                (provider,model,task_kind,input_tokens,output_tokens,cost_usd,datetime.now(timezone.utc).isoformat()))


class ModelGateway:
    def __init__(self, options: list[ModelOption], adapters: dict[str,ProviderAdapter],
                 ledger: UsageLedger, daily_budget_usd: float=0.0,
                 monthly_budget_usd: float=0.0, allow_paid_calls: bool=False,
                 network_calls_enabled: bool=False):
        self.options=options; self.adapters=adapters; self.ledger=ledger
        self.daily_budget_usd=daily_budget_usd; self.monthly_budget_usd=monthly_budget_usd
        self.allow_paid_calls=allow_paid_calls; self.network_calls_enabled=network_calls_enabled
        self.router=ModelRouter(options,paid_calls_enabled=allow_paid_calls)
    def status(self) -> dict[str,Any]:
        return {"network_calls_enabled":self.network_calls_enabled,
                "live_provider_adapters_enabled":False,
                "registered_adapters":sorted(self.adapters),
                "paid_calls_enabled":self.allow_paid_calls,
                "budgets":{"daily_usd":self.daily_budget_usd,"monthly_usd":self.monthly_budget_usd},
                "usage":self.ledger.totals(),
                "note":"Only explicitly injected adapters can run; repository ships no live network adapter."}
    def generate(self, request: GenerationRequest):
        if not self.network_calls_enabled and request.task_kind != "offline_test":
            raise ProviderUnavailable("Model execution is disabled by policy.")
        model=self.router.select(request.task_kind)
        if model is None: raise ProviderUnavailable("No eligible model is configured.")
        adapter=self.adapters.get(model.provider)
        if adapter is None: raise ProviderUnavailable(f"No adapter registered for provider: {model.provider}")
        usage=self.ledger.totals()
        if usage["daily_usd"] >= self.daily_budget_usd or usage["monthly_usd"] >= self.monthly_budget_usd:
            raise BudgetExceeded("Configured model budget is exhausted.")
        response=adapter.generate(model.model,request)
        if response.cost_usd > self.daily_budget_usd-usage["daily_usd"] or response.cost_usd > self.monthly_budget_usd-usage["monthly_usd"]:
            raise BudgetExceeded("Response cost exceeds remaining budget; usage was not recorded.")
        if response.cost_usd > 0 and not self.allow_paid_calls:
            raise BudgetExceeded("Paid model calls are disabled.")
        self.ledger.record(response.provider,response.model,request.task_kind,
                           response.input_tokens,response.output_tokens,response.cost_usd)
        return response
