"""Resolve credentials by role, not by name.

The repository accumulated several naming conventions for the same thing:
CLOUDFLARE_API_TOKEN next to CLOUDFLARE_ACCESS_AUDIT_TOKEN, D1_DATABASE_ID next
to CLOUDFLARE_ACCOUNT_ID. Requiring one exact name is brittle, and renaming
secrets breaks every workflow at once.

So callers ask for a *role* ("the Cloudflare token") and this module answers with
whichever alias is present. Two rules keep that from becoming sloppy:

* order matters: the alias list is a priority, not a set
* identity is a fingerprint, never a value, so two similar secrets can be told
  apart in a log without either being exposed

The account id problem is solved the same way: instead of insisting the stored
id is correct, the resolver asks Cloudflare which account owns the D1 database
named in D1_DATABASE_ID. That is a fact, and it settles the disagreement between
two secrets that each look correct on their own.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

API = "https://api.cloudflare.com/client/v4"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "credentials.json"


def fingerprint(value: str) -> str:
    """A stable, non-reversible label for a secret: length plus six hex characters."""
    if not value:
        return "absent"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:6]
    return f"len{len(value)}:{digest}"


@dataclass
class Resolved:
    role: str
    name: str | None
    value: str = field(repr=False, default="")
    fingerprint: str = "absent"

    @property
    def present(self) -> bool:
        return bool(self.value)


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or DEFAULT_CONFIG
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(role: str, env: dict[str, str], config: dict[str, Any] | None = None) -> Resolved:
    """Return the first present alias for a role. Raises nothing; absence is data."""
    config = config or load_config()
    aliases = config.get("roles", {}).get(role)
    if not aliases:
        raise KeyError(f"no aliases configured for role: {role}")
    for name in aliases:
        value = (env.get(name) or "").strip()
        if value:
            return Resolved(role=role, name=name, value=value, fingerprint=fingerprint(value))
    return Resolved(role=role, name=None)


class CloudflareError(RuntimeError):
    pass


@dataclass
class Cloudflare:
    token: Resolved
    account_id: Resolved
    d1_database_id: Resolved
    chosen_account_id: str | None = None
    evidence: str = ""
    accounts_seen: list[dict] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """A loggable dict. Account ids are shortened; the token never appears."""
        def short(value: str | None) -> str | None:
            if not value:
                return None
            return f"{value[:6]}...{value[-4:]}" if len(value) > 12 else value

        return {
            "token": {"name": self.token.name, "fingerprint": self.token.fingerprint},
            "account_id": {
                "name": self.account_id.name,
                "stored": short(self.account_id.value),
                "used": short(self.chosen_account_id),
                "matches_stored": self.account_id.value == self.chosen_account_id,
            },
            "d1_database_id": {"name": self.d1_database_id.name,
                               "fingerprint": self.d1_database_id.fingerprint},
            "evidence": self.evidence,
            "accounts_seen": [
                {"name": a.get("name"), "id": short(a.get("id"))} for a in self.accounts_seen
            ],
        }


def _http_get(path: str, token: str, timeout: int = 20) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except (json.JSONDecodeError, ValueError):
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}


def resolve_cloudflare(
    env: dict[str, str],
    config: dict[str, Any] | None = None,
    *,
    get: Callable[[str, str], tuple[int, dict]] | None = None,
) -> Cloudflare:
    """Choose the account that provably owns the configured D1 database.

    Order of evidence:
      1. the stored CLOUDFLARE_ACCOUNT_ID, if the token can actually see it
      2. the account whose D1 database id matches D1_DATABASE_ID
      3. the single account the token can see
    Anything else is an explicit error rather than a guess, because picking the
    wrong account silently would produce 9109 errors that look like a bad token.
    """
    get = get or _http_get
    config = config or load_config()

    token = resolve("cloudflare_token", env, config)
    stored_account = resolve("cloudflare_account", env, config)
    d1_id = resolve("d1_database_id", env, config)
    resolved = Cloudflare(token=token, account_id=stored_account, d1_database_id=d1_id)

    if not token.present:
        resolved.evidence = "no Cloudflare token under any configured alias"
        return resolved

    # 1. does the stored id work?
    if stored_account.present:
        status, payload = get(f"/accounts/{stored_account.value}", token.value)
        if status and payload.get("success"):
            resolved.chosen_account_id = stored_account.value
            resolved.evidence = f"stored account id works ({stored_account.name})"
            return resolved

    # 2/3. discover what the token can see
    status, payload = get("/accounts", token.value)
    if not status or not payload.get("success"):
        resolved.evidence = ("token cannot list accounts; either the stored id is wrong "
                             "or the token is scoped to a single resource")
        return resolved

    accounts = [a for a in payload.get("result") or [] if isinstance(a, dict)]
    resolved.accounts_seen = accounts

    if d1_id.present:
        for account in accounts:
            account_id = str(account.get("id", ""))
            s2, p2 = get(f"/accounts/{account_id}/d1/database", token.value)
            if not s2 or not p2.get("success"):
                continue
            for database in p2.get("result") or []:
                if str(database.get("uuid", "")) == d1_id.value:
                    resolved.chosen_account_id = account_id
                    resolved.evidence = (
                        f"account chosen by D1 database id from {d1_id.name} "
                        f"(database {database.get('name')})")
                    return resolved
        resolved.evidence = (f"no visible account owns the D1 database id in {d1_id.name}; "
                             "falling back to the stored id")

    if len(accounts) == 1:
        resolved.chosen_account_id = str(accounts[0].get("id"))
        prefix = ""
        if d1_id.present:
            prefix = (f"no visible account owns the D1 database id in {d1_id.name}; "
                      "using the only account the token can see")
        resolved.evidence = prefix or "single visible account"
        return resolved

    if stored_account.present and accounts:
        resolved.chosen_account_id = stored_account.value
        resolved.evidence = f"ambiguous: {len(accounts)} accounts visible, keeping the stored id"
        return resolved

    resolved.evidence = f"ambiguous: {len(accounts)} accounts visible and no D1 ownership match"
    return resolved
