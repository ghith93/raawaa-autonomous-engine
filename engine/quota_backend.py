"""Select the quota ledger backend.

When RAAWAA_ENGINE_API_URL is set the durable D1 ledger (via the control
Worker) is used. Otherwise the local SQLite ledger is used, which is only
suitable for a single long-lived machine, never for ephemeral CI runners.
"""
from __future__ import annotations

import os
from pathlib import Path

from engine.d1_quota_ledger import D1QuotaLedger
from engine.quota_manager import QuotaLedger
from engine.remote_task_store import RemoteTaskStore


def remote_ledger_configured(environ=None) -> bool:
    source = os.environ if environ is None else environ
    return bool(str(source.get("RAAWAA_ENGINE_API_URL", "")).strip())


def open_quota_ledger(db_path: str | Path, environ=None):
    source = os.environ if environ is None else environ
    if remote_ledger_configured(source):
        return D1QuotaLedger(RemoteTaskStore(
            base_url=source.get("RAAWAA_ENGINE_API_URL", ""),
            token=source.get("RAAWAA_ENGINE_API_TOKEN", ""),
            access_client_id=source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", ""),
            access_client_secret=source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", ""),
        ))
    return QuotaLedger(db_path)
