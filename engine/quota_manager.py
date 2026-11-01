"""Quota-aware free-provider rotation with a persistent local usage ledger.

Only explicitly configured, verified-free routes can be selected. Unknown quotas,
trial credits, and promotional credits are never treated as permanent free capacity.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


def _valid_https_url(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        reserved_hosts = (
            "localhost", "example.com", "example.net", "example.org",
        )
        is_reserved = (
            host in reserved_hosts
            or any(host.endswith("." + suffix) for suffix in reserved_hosts)
            or any(host.endswith(suffix) for suffix in (
                ".example", ".invalid", ".test", ".localhost"
            ))
        )
        return (
            parsed.scheme == "https"
            and bool(host)
            and not is_reserved
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class QuotaProfile:
    provider: str
    account: str
    model: str
    free_verified: bool
    commercial_use_allowed: bool
    production_use_allowed: bool = False
    minute_request_limit: int | None = None
    minute_token_limit: int | None = None
    daily_request_limit: int | None = None
    daily_token_limit: int | None = None
    monthly_request_limit: int | None = None
    monthly_token_limit: int | None = None
    per_task_request_limit: int | None = None
    per_task_token_limit: int | None = None
    monthly_credit_usd: float | None = None
    input_cost_per_million_usd: float | None = None
    output_cost_per_million_usd: float | None = None
    priority: int = 100
    endpoint_url: str | None = None
    pricing_source_url: str | None = None
    quota_source_url: str | None = None
    terms_source_url: str | None = None
    verified_at: datetime | None = None
    max_output_tokens: int | None = None

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
                occurred_at TEXT NOT NULL,
                settled_at TEXT,
                task_id TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS quota_cooldowns (
                route_id TEXT PRIMARY KEY,
                until_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS quota_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                route_id TEXT,
                task_kind TEXT,
                details_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""")
            # Existing databases predate settlement idempotency. Keep existing
            # rows unsettled rather than guessing whether an interrupted request
            # had reached its provider.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(quota_usage)")}
            if "settled_at" not in columns:
                db.execute("ALTER TABLE quota_usage ADD COLUMN settled_at TEXT")
            if "task_id" not in columns:
                db.execute("ALTER TABLE quota_usage ADD COLUMN task_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_quota_usage_task ON quota_usage(task_id, occurred_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_quota_usage_route_time ON quota_usage(route_id, occurred_at)")

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

    def usage_minute(self, route_id: str, now: datetime | None = None) -> dict[str, int]:
        """Return rolling 60-second usage, including unsettled reservations."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = (now - timedelta(seconds=60)).isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE route_id=? AND occurred_at>?",
                (route_id, cutoff),
            ).fetchone()
        return {"requests": int(row["requests"]), "tokens": int(row["tokens"])}

    def usage_month(self, route_id: str, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        start = now.astimezone(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE route_id=? AND occurred_at>=?",
                (route_id, start),
            ).fetchone()
        return {"requests": int(row["requests"]), "tokens": int(row["tokens"])}

    def usage_for_task(self, task_id: str) -> dict[str, int]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE task_id=?", (task_id,)
            ).fetchone()
        return {"requests": int(row["requests"]), "tokens": int(row["tokens"])}

    def reserve(self, route_id: str, estimated_tokens: int, request_limit: int,
                token_limit: int, now: datetime | None = None,
                monthly_request_limit: int | None = None,
                monthly_token_limit: int | None = None,
                task_id: str | None = None,
                per_task_request_limit: int | None = None,
                per_task_token_limit: int | None = None,
                minute_request_limit: int | None = None,
                minute_token_limit: int | None = None) -> int:
        """Atomically reserve one request and its estimated tokens before network I/O."""
        if (type(estimated_tokens) is not int or type(request_limit) is not int
                or type(token_limit) is not int or type(monthly_request_limit) is not int
                or type(monthly_token_limit) is not int
                or type(per_task_request_limit) is not int
                or type(per_task_token_limit) is not int
                or type(minute_request_limit) is not int
                or type(minute_token_limit) is not int
                or not isinstance(task_id, str) or not task_id.strip()
                or min(estimated_tokens, request_limit, token_limit,
                       monthly_request_limit, monthly_token_limit,
                       per_task_request_limit, per_task_token_limit,
                       minute_request_limit, minute_token_limit) < 1):
            raise ValueError("reservation limits and estimated tokens must be positive integers")
        now = now or datetime.now(timezone.utc)
        current = now.astimezone(timezone.utc)
        start = current.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        minute_start = (current - timedelta(seconds=60)).isoformat()
        stamp = current.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            daily = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE route_id=? AND occurred_at>=?", (route_id, start)
            ).fetchone()
            monthly = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE route_id=? AND occurred_at>=?", (route_id, month_start)
            ).fetchone()
            minute = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE route_id=? AND occurred_at>?", (route_id, minute_start)
            ).fetchone()
            task = db.execute(
                "SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens "
                "FROM quota_usage WHERE task_id=?", (task_id,)
            ).fetchone()
            if (int(daily["requests"]) >= request_limit
                    or int(daily["tokens"]) + estimated_tokens > token_limit
                    or int(monthly["requests"]) >= monthly_request_limit
                    or int(monthly["tokens"]) + estimated_tokens > monthly_token_limit
                    or int(minute["requests"]) >= minute_request_limit
                    or int(minute["tokens"]) + estimated_tokens > minute_token_limit
                    or int(task["requests"]) >= per_task_request_limit
                    or int(task["tokens"]) + estimated_tokens > per_task_token_limit):
                raise RuntimeError("quota reservation unavailable")
            cur = db.execute(
                "INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,task_id) VALUES(?,?,?,?,?)",
                (route_id, 1, estimated_tokens, stamp, task_id),
            )
            return int(cur.lastrowid)

    def settle(self, reservation_id: int, actual_tokens: int) -> None:
        """Settle once; an identical retry is safe, conflicting retries fail closed."""
        if actual_tokens < 0:
            raise ValueError("actual_tokens cannot be negative")
        stamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT tokens, settled_at FROM quota_usage WHERE id=?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError("quota reservation not found")
            if row["settled_at"] is not None:
                if int(row["tokens"]) == actual_tokens:
                    return
                raise RuntimeError("quota reservation already settled with different usage")
            changed = db.execute(
                "UPDATE quota_usage SET tokens=?, settled_at=? WHERE id=? AND settled_at IS NULL",
                (actual_tokens, stamp, reservation_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("quota reservation settlement state changed")

    def record(self, route_id: str, tokens: int, now: datetime | None = None) -> None:
        if tokens < 0:
            raise ValueError("tokens cannot be negative")
        now = now or datetime.now(timezone.utc)
        stamp = now.astimezone(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,settled_at) VALUES(?,?,?,?,?)",
                (route_id, 1, tokens, stamp, stamp),
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

    def audit_event(self, event: str, route_id: str | None = None,
                    task_kind: str | None = None,
                    details: dict | None = None,
                    now: datetime | None = None) -> int:
        """Persist metadata-only inference audit events; never store prompts or outputs."""
        if not isinstance(event, str) or not event or len(event) > 80:
            raise ValueError("audit event name is invalid")
        allowed = {
            "reason", "error_type", "http_status", "estimated_tokens",
            "actual_tokens", "cooldown_seconds", "cost_usd",
        }
        safe = {}
        for key, value in (details or {}).items():
            if key not in allowed:
                continue
            if isinstance(value, str):
                safe[key] = value[:100]
            elif type(value) in {int, float, bool}:
                safe[key] = value
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO quota_audit_events(event,route_id,task_kind,details_json,occurred_at) "
                "VALUES(?,?,?,?,?)",
                (event, route_id[:200] if isinstance(route_id, str) else None,
                 task_kind[:100] if isinstance(task_kind, str) else None,
                 json.dumps(safe, sort_keys=True), stamp),
            )
            return int(cur.lastrowid)

    def audit_events(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM quota_audit_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {**dict(row), "details": json.loads(row["details_json"])}
            for row in rows
        ]



class QuotaManager:
    def __init__(self, profiles: list[QuotaProfile], ledger: QuotaLedger):
        self.profiles = profiles
        self.ledger = ledger

    def eligible(self, profile: QuotaProfile, estimated_tokens: int = 1,
                 now: datetime | None = None) -> bool:
        if (not profile.free_verified or not profile.commercial_use_allowed
                or type(profile.production_use_allowed) is not bool
                or not profile.production_use_allowed):
            return False
        # This runtime is zero-spend by policy. Unknown or non-zero prices must
        # be rejected before network I/O; a post-response cost check is too late
        # to prevent an accidental charge.
        prices = (profile.input_cost_per_million_usd, profile.output_cost_per_million_usd)
        if any(
            type(price) not in {int, float} or not math.isfinite(price) or price != 0.0
            for price in prices
        ):
            return False
        # A boolean alone is not evidence. Require an HTTPS endpoint, explicit
        # pricing/quota/terms sources, a recent timezone-aware verification date,
        # and a bounded model output ceiling before the route can be selected.
        evidence_urls = (
            profile.endpoint_url, profile.pricing_source_url,
            profile.quota_source_url, profile.terms_source_url,
        )
        if any(not _valid_https_url(url) for url in evidence_urls):
            return False
        if (not isinstance(profile.verified_at, datetime)
                or profile.verified_at.tzinfo is None
                or profile.verified_at.utcoffset() is None):
            return False
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        verified = profile.verified_at.astimezone(timezone.utc)
        if verified > current or current - verified > timedelta(days=30):
            return False
        if (type(profile.max_output_tokens) is not int
                or not 1 <= profile.max_output_tokens <= 4096):
            return False
        if (type(profile.minute_request_limit) is not int
                or type(profile.minute_token_limit) is not int
                or min(profile.minute_request_limit, profile.minute_token_limit) < 1):
            return False
        if (type(profile.daily_request_limit) is not int
                or type(profile.daily_token_limit) is not int
                or type(profile.monthly_request_limit) is not int
                or type(profile.monthly_token_limit) is not int
                or type(profile.per_task_request_limit) is not int
                or type(profile.per_task_token_limit) is not int
                or min(profile.daily_request_limit, profile.daily_token_limit,
                       profile.monthly_request_limit, profile.monthly_token_limit,
                       profile.per_task_request_limit, profile.per_task_token_limit) < 1):
            return False
        if type(estimated_tokens) is not int or estimated_tokens < 1:
            return False
        if self.ledger.cooldown_active(profile.route_id, now):
            return False
        minute = self.ledger.usage_minute(profile.route_id, now)
        if (minute["requests"] >= profile.minute_request_limit
                or minute["tokens"] + estimated_tokens > profile.minute_token_limit):
            return False
        daily = self.ledger.usage_today(profile.route_id, now)
        monthly = self.ledger.usage_month(profile.route_id, now)
        return (
            daily["requests"] < profile.daily_request_limit
            and daily["tokens"] + estimated_tokens <= profile.daily_token_limit
            and monthly["requests"] < profile.monthly_request_limit
            and monthly["tokens"] + estimated_tokens <= profile.monthly_token_limit
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
            monthly = self.ledger.usage_month(profile.route_id, now)
            minute = self.ledger.usage_minute(profile.route_id, now)
            rows.append({
                "route_id": profile.route_id,
                "free_verified": profile.free_verified,
                "commercial_use_allowed": profile.commercial_use_allowed,
                "minute_requests_used": minute["requests"],
                "minute_tokens_used": minute["tokens"],
                "minute_request_limit": profile.minute_request_limit,
                "minute_token_limit": profile.minute_token_limit,
                "daily_requests_used": usage["requests"],
                "daily_tokens_used": usage["tokens"],
                "monthly_requests_used": monthly["requests"],
                "monthly_tokens_used": monthly["tokens"],
                "eligible": self.eligible(profile, 1, now),
                "cooldown": self.ledger.cooldown_active(profile.route_id, now),
            })
        return rows
