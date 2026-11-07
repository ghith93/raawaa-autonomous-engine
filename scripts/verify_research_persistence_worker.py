"""Read-only readiness probe for the Staging Worker research idempotency endpoint."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

STAGING_HOST = "raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
REQUIRED_TABLES = {"research_ingest_batches", "research_ingest_events"}
MAX_RESPONSE_BYTES = 64 * 1024


class ResearchWorkerError(RuntimeError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Staging schema redirects are disabled", headers, fp)


def verify_worker(base_url: str, token: str, opener=None, timeout: int = 10) -> dict:
    if not isinstance(base_url, str) or not isinstance(token, str) or not token.strip():
        raise ResearchWorkerError("Staging Worker URL or token is missing")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https" or parsed.hostname != STAGING_HOST
        or parsed.username or parsed.password or parsed.port
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise ResearchWorkerError("Refusing research persistence outside the exact Staging Worker")
    request = urllib.request.Request(
        f"https://{STAGING_HOST}/v1/diagnostics/schema",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + token.strip(),
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        },
        method="GET",
    )
    open_request = opener or urllib.request.build_opener(_RejectRedirects()).open
    with open_request(request, timeout=timeout) as response:
        if response.status != 200:
            raise ResearchWorkerError("Staging Worker schema endpoint is not ready")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ResearchWorkerError("Staging Worker schema response exceeded the bound")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ResearchWorkerError("Staging Worker returned invalid schema JSON") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise ResearchWorkerError("Staging Worker schema probe is not ready")
    missing = body.get("missing")
    checked = body.get("checked")
    if (
        missing != [] or not isinstance(checked, list)
        or not REQUIRED_TABLES.issubset(set(checked))
    ):
        raise ResearchWorkerError("Staging Worker lacks the research idempotency schema")
    return {"ready": True, "target": "staging", "checked_research_tables": sorted(REQUIRED_TABLES)}


def main() -> int:
    try:
        result = verify_worker(
            os.environ.get("RAAWAA_ENGINE_API_URL", ""),
            os.environ.get("RAAWAA_ENGINE_STAGING_API_TOKEN", ""),
        )
    except Exception as exc:
        print(json.dumps({
            "research_worker_ready": False,
            "reason": "staging_worker_schema_unavailable",
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return 1
    print(json.dumps({
        "research_worker_ready": result["ready"],
        "target": result["target"],
        "checked_research_tables": result["checked_research_tables"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
