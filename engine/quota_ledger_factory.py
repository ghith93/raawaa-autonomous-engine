"""Select persistent quota storage for the current execution environment."""
from __future__ import annotations

import os
from pathlib import Path

from engine.quota_manager import QuotaLedger
from engine.remote_quota_ledger import RemoteQuotaLedger


def build_quota_ledger(db_path: str | Path, environ=None):
    source = os.environ if environ is None else environ
    api_url = source.get("RAAWAA_ENGINE_API_URL", "").strip()
    if not api_url:
        return QuotaLedger(db_path)
    return RemoteQuotaLedger(
        base_url=api_url,
        token=source.get("RAAWAA_ENGINE_API_TOKEN", ""),
        access_client_id=source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", ""),
        access_client_secret=source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", ""),
    )
