"""Read-only D1 mobile-status probe used by the scheduled Task Runner.

Only sanitized health flags and aggregate counts are emitted to GitHub Actions.
No credentials, model costs, candidate IDs, or provider usage details are logged.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None

_ALLOWED_HOST = "raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev"
_OUTPUT_KEYS = (
    "http",
    "paper_ledger",
    "provider_usage",
    "telegram_receipts",
    "last_scan",
    "source_freshness",
    "ci_health",
    "kill_switch",
    "tasks_pending",
    "tasks_running",
    "paper_events",
)


def _validate_api_url(api_url: str) -> str:
    if not isinstance(api_url, str) or not api_url:
        raise ValueError("ENGINE_API_URL is required")
    parsed = urlsplit(api_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _ALLOWED_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ENGINE_API_URL is not the approved HTTPS Worker origin")
    return api_url.rstrip("/")


def build_headers(
    engine_api_token: str,
    cf_access_client_id: str = "",
    cf_access_client_secret: str = "",
) -> dict[str, str]:
    if not isinstance(engine_api_token, str) or not engine_api_token.strip():
        raise ValueError("ENGINE_API_TOKEN is required")
    client_id = cf_access_client_id or ""
    client_secret = cf_access_client_secret or ""
    if bool(client_id) != bool(client_secret):
        raise ValueError("Cloudflare Access service-token secrets must be configured as a pair")
    headers = {
        "Authorization": "Bearer " + engine_api_token,
        "Accept": "application/json",
        "User-Agent": "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)",
    }
    if client_id and client_secret:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = client_secret
    return headers


def _count(value: object) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("status count must be a non-negative integer")


def summarize_mobile_status(payload: object, http_status: int = 200) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("mobile status payload must be an object")
    unavailable = payload.get("unavailable_metrics") or {}
    if not isinstance(unavailable, dict):
        raise ValueError("unavailable_metrics must be an object")

    task_counts: dict[str, int] = {}
    tasks = payload.get("tasks") or []
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    for item in tasks:
        if not isinstance(item, dict) or not isinstance(item.get("status"), str):
            continue
        task_counts[item["status"]] = _count(item.get("count", 0))

    events = payload.get("paper_events") or []
    if not isinstance(events, list):
        raise ValueError("paper_events must be a list")
    paper_event_count = sum(
        _count(item.get("count", 0))
        for item in events
        if isinstance(item, dict)
    )

    def text_value(key: str) -> str:
        value = unavailable.get(key, "")
        return value.lower() if isinstance(value, str) else ""

    def migration_flag(key: str) -> str:
        value = unavailable.get(key)
        if value == "Available":
            return "available"
        return "not_available" if isinstance(value, str) and value else "unknown"

    telegram_schema = payload.get("telegram_webhook_schema")
    telegram_available = telegram_schema.get("available") if isinstance(telegram_schema, dict) else None
    telegram_receipts = (
        "available" if telegram_available is True
        else "not_available" if telegram_available is False
        else "unknown"
    )

    def gap_flag(key: str, marker: str, missing_label: str) -> str:
        value = text_value(key)
        if not value:
            return "unknown"
        return missing_label if marker in value else "available"

    return {
        "http": str(http_status),
        "paper_ledger": migration_flag("paper_ledger"),
        "provider_usage": migration_flag("provider_usage"),
        "telegram_receipts": telegram_receipts,
        "last_scan": gap_flag("last_scan", "not recorded", "not_recorded"),
        "source_freshness": gap_flag("source_freshness", "not yet aggregated", "not_aggregated"),
        "ci_health": gap_flag("ci_health", "not yet ingested", "not_ingested"),
        "kill_switch": gap_flag("kill_switch", "not yet mirrored", "not_mirrored"),
        "tasks_pending": str(task_counts.get("pending", 0)),
        "tasks_running": str(task_counts.get("running", 0)),
        "paper_events": str(paper_event_count),
    }


def probe_mobile_status(
    api_url: str,
    engine_api_token: str,
    cf_access_client_id: str = "",
    cf_access_client_secret: str = "",
    *,
    opener=None,
) -> dict[str, str]:
    origin = _validate_api_url(api_url)
    headers = build_headers(engine_api_token, cf_access_client_id, cf_access_client_secret)
    request = Request(origin + "/v1/mobile/status", headers=headers, method="GET")
    open_url = opener or build_opener(_RejectRedirects()).open
    try:
        with open_url(request, timeout=15) as response:
            status = int(response.status)
            if status != 200:
                raise RuntimeError(f"mobile status returned HTTP {status}")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code != 404:
            raise
        # The deployed Worker can lag behind main because production deployment
        # is separately gated. Report this accurately without blocking D1 tasks.
        return {
            "http": "404",
            "paper_ledger": "route_not_deployed",
            "provider_usage": "route_not_deployed",
            "last_scan": "route_not_deployed",
            "source_freshness": "route_not_deployed",
            "ci_health": "route_not_deployed",
            "kill_switch": "route_not_deployed",
            "tasks_pending": "unknown",
            "tasks_running": "unknown",
            "paper_events": "unknown",
        }
    return summarize_mobile_status(payload, status)


def _write_outputs(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        for key in _OUTPUT_KEYS:
            output.write(f"{key}={values.get(key, 'unknown')}\n")


def main() -> int:
    _write_outputs({key: "unknown" for key in _OUTPUT_KEYS})
    try:
        values = probe_mobile_status(
            os.environ.get("ENGINE_API_URL", ""),
            os.environ.get("ENGINE_API_TOKEN", ""),
            os.environ.get("CF_ACCESS_CLIENT_ID", ""),
            os.environ.get("CF_ACCESS_CLIENT_SECRET", ""),
        )
        _write_outputs(values)
        if values["http"] == "404":
            print("D1 mobile status route is not deployed; the Task Runner will continue without this optional read.")
        else:
            print("D1 mobile status read succeeded: " + ", ".join(f"{key}={value}" for key, value in values.items()))
        return 0
    except HTTPError as error:
        print(f"Mobile status returned HTTP {error.code}.")
    except (URLError, TimeoutError, OSError, ValueError, TypeError, RuntimeError) as error:
        print(f"Mobile status read failed: {type(error).__name__}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
