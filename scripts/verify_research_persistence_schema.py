"""Read-only check that the Staging D1 migration required for idempotent research persistence exists."""
from __future__ import annotations

import json
import os
from typing import Callable

from engine.d1_client import D1Client

STAGING_DATABASE_ID = "231dd948-04a0-49e3-ad73-444f77d69477"
REQUIRED_MIGRATION = "0015_research_ingest_idempotency"


class ResearchSchemaError(RuntimeError):
    pass


def verify_research_schema(environ: dict[str, str] | None = None,
                           client_factory: Callable = D1Client) -> dict:
    values = os.environ if environ is None else environ
    account_id = values.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = values.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise ResearchSchemaError("Staging schema verification credentials are missing")
    client = client_factory(
        account_id=account_id,
        database_id=STAGING_DATABASE_ID,
        api_token=api_token,
    )
    result = client.query(
        "SELECT version FROM schema_migrations WHERE version = ?",
        [REQUIRED_MIGRATION],
    )
    rows = result.get("results") if isinstance(result, dict) else None
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or rows[0].get("version") != REQUIRED_MIGRATION
    ):
        raise ResearchSchemaError("Required Staging research persistence migration is not applied")
    return {
        "ready": True,
        "database": "staging",
        "migration": REQUIRED_MIGRATION,
    }


def main() -> int:
    try:
        result = verify_research_schema()
    except Exception as exc:
        print(json.dumps({
            "research_persistence_schema_ready": False,
            "reason": "staging_schema_unavailable",
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return 1
    print(json.dumps({
        "research_persistence_schema_ready": result["ready"],
        "database": result["database"],
        "migration": result["migration"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
