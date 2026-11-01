"""Quota-aware free-provider rotation with a persistent local usage ledger.

Only explicitly configured, verified-free routes can be selected. Unknown quotas,
trial credits, and promotional credits are never treated as permanent free capacity.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class QuotaProfile:
    provider: str
    account: str
    model: str
    free_verified: bool
    commercial_use_allowed: bool
    daily_request_limit: int | None = None
    daily_token_limit: int | None = None
    monthly_credit_usd: float | None = None
    priority: int = 100

    @property
    def route_id(self) -> str:
        return f"{self.provider}:{self.account}:{self.model}"


class QuotaLedger:
    def __init__(self, db_path: str | Path = "data/engine.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS quota_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id TEXT NOT NULL,
                requests INTEGER NOT NULL,
                tokens INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS quota_cooldowns (
                route_id TEXT PRIMARY KEY,
                until_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def usage_today(self, route_id: str, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE route_id=? AND occurred_at>=?",
                (route_id, start),
            ).fetchone()
        return {"requests": int(row["requests"]), "tokens": int(row["tokens"])}

    def record(self, route_id: str, tokens: int, now: datetime | None = None) -> None:
        if tokens < 0:
            raise ValueError("tokens cannot be negative")
        now = now or datetime.now(timezone.utc)
        with self._connect() as db:
            db.execute(
                "INSERT INTO quota_usage(route_id,requests,tokens,occurred_at) VALUES(?,?,?,?)",
                (route_id, 1, tokens, now.astimezone(timezone.utc).isoformat()),
            )

    def cool_down(self, route_id: str, seconds: int, reason: str = "rate_limited",
                  now: datetime | None = None) -> None:
        if seconds < 1:
            raise ValueError("cooldown must be positive")
        now = now or datetime.now(timezone.utc)
        until = (now + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO quota_cooldowns(route_id,until_at,reason) VALUES(?,?,?) "
                "ON CONFLICT(route_id) DO UPDATE SET until_at=excluded.until_at, reason=excluded.reason",
                (route_id, until, reason[:120]),
            )

    def cooldown_active(self, route_id: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        with self._connect() as db:
            row = db.execute(
                "SELECT until_at FROM quota_cooldowns WHERE route_id=?", (route_id,)
            ).fetchone()
        return bool(row and datetime.fromisoformat(row["until_at"]) > now.astimezone(timezone.utc))


class QuotaManager:
    def __init__(self, profiles: list[QuotaProfile], ledger: QuotaLedger):
        self.profiles = profiles
        self.ledger = ledger

    def eligible(self, profile: QuotaProfile, estimated_tokens: int = 1,
                 now: datetime | None = None) -> bool:
        if not profile.free_verified or not profile.commercial_use_allowed:
            return False
        if profile.daily_request_limit is None or profile.daily_token_limit is None:
            return False
        if profile.daily_request_limit < 1 or profile.daily_token_limit < 1:
            return False
        if self.ledger.cooldown_active(profile.route_id, now):
            return False
        usage = self.ledger.usage_today(profile.route_id, now)
        return (
            usage["requests"] < profile.daily_request_limit
            and usage["tokens"] + max(1, estimated_tokens) <= profile.daily_token_limit
        )

    def select(self, estimated_tokens: int = 1, now: datetime | None = None) -> QuotaProfile | None:
        candidates = [p for p in self.profiles if self.eligible(p, estimated_tokens, now)]
        if not candidates:
            return None
        # Prefer lower configured priority, then the route with the most remaining token capacity.
        return sorted(
            candidates,
            key=lambda p: (
                p.priority,
                -(p.daily_token_limit - self.ledger.usage_today(p.route_id, now)["tokens"]),
                p.provider, p.account, p.model,
            ),
        )[0]

    def snapshot(self, now: datetime | None = None) -> list[dict]:
        rows = []
        for profile in self.profiles:
            usage = self.ledger.usage_today(profile.route_id, now)
            rows.append({
                "route_id": profile.route_id,
                "free_verified": profile.free_verified,
                "commercial_use_allowed": profile.commercial_use_allowed,
                "daily_requests_used": usage["requests"],
                "daily_tokens_used": usage["tokens"],
                "eligible": self.eligible(profile, 1, now),
                "cooldown": self.ledger.cooldown_active(profile.route_id, now),
            })
        return rows
