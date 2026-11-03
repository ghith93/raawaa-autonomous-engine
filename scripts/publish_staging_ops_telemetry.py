"""Publish sanitized Engine CI and default inference-gate snapshots to Staging D1.

This writes only to the dedicated non-production database. It does not call a
model provider or alter the policy, Worker, production D1, or any external app.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from engine.d1_client import D1Client, D1APIError, D1ConfigurationError

STAGING_DATABASE_NAME = "raawaa-engine-staging"
STAGING_DATABASE_ID = "231dd948-04a0-49e3-ad73-444f77d69477"
PRODUCTION_DATABASE_ID = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
JOB_RESULTS = {"success", "failure", "cancelled", "skipped"}


class TelemetryPublishError(RuntimeError):
    """A sanitized Staging telemetry publication failed."""


def _ci_conclusion(results: dict[str, str]) -> str:
    if any(value in {"failure", "cancelled"} for value in results.values()):
        return "failure"
    if all(value == "success" for value in results.values()):
        return "success"
    return "incomplete"


def _policy_snapshot(root: Path, live_inference_value: str) -> dict:
    policy = json.loads((root / "config" / "policy.json").read_text(encoding="utf-8"))
    workflow = (root / ".github" / "workflows" / "task-runner.yml").read_text(encoding="utf-8")
    if "RAAWAA_ENGINE_LIVE_INFERENCE:" not in workflow:
        raise TelemetryPublishError("Task Runner live-inference gate is not wired")
    if live_inference_value == "enabled":
        live_default = "enabled"
    elif live_inference_value in ("", "disabled"):
        live_default = "disabled"
    else:
        live_default = "unknown"
    safe = (
        policy.get("mode") == "safe_dry_run"
        and policy.get("allow_paid_model_calls") is False
        and policy.get("allow_external_writes") is False
        and policy.get("model_policy", {}).get("paid_fallback_enabled") is False
        and live_default == "disabled"
    )
    return {
        "scope": "scheduled_task_runner_default",
        "status": "engaged" if safe else "not_engaged",
        "policy_mode": policy.get("mode") if isinstance(policy.get("mode"), str) else "unknown",
        "live_inference_default": live_default,
        "allow_paid_model_calls": policy.get("allow_paid_model_calls") is True,
        "paid_fallback_enabled": policy.get("model_policy", {}).get("paid_fallback_enabled") is True,
        "allow_external_writes": policy.get("allow_external_writes") is True,
    }


def _validate_database_targets(root: Path) -> None:
    with (root / "wrangler.staging.toml").open("rb") as stream:
        staging = tomllib.load(stream)
    with (root / "wrangler.toml").open("rb") as stream:
        production = tomllib.load(stream)
    staging_bindings = staging.get("d1_databases", [])
    production_bindings = production.get("d1_databases", [])
    if len(staging_bindings) != 1 or len(production_bindings) != 1:
        raise TelemetryPublishError("Expected exactly one D1 binding in each Wrangler config")
    stage = staging_bindings[0]
    prod = production_bindings[0]
    if (
        stage.get("database_name") != STAGING_DATABASE_NAME
        or stage.get("database_id") != STAGING_DATABASE_ID
        or prod.get("database_id") != PRODUCTION_DATABASE_ID
        or stage.get("database_id") == prod.get("database_id")
    ):
        raise TelemetryPublishError("Refusing telemetry write: Staging/production D1 identity mismatch")


def build_telemetry_statement(
    environ: dict[str, str], root: Path, now: datetime | None = None
) -> tuple[str, list[str], dict]:
    _validate_database_targets(root)
    required = (
        "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "GITHUB_RUN_ID",
        "GITHUB_RUN_NUMBER", "GITHUB_SHA", "GITHUB_REF_NAME", "GITHUB_EVENT_NAME",
        "GITHUB_WORKFLOW", "ENGINE_CI_TEST_RESULT",
        "ENGINE_CI_STAGING_MIGRATE_RESULT", "ENGINE_CI_STAGING_WORKER_RESULT",
    )
    missing = [name for name in required if not environ.get(name, "").strip()]
    if missing:
        raise TelemetryPublishError("Missing required telemetry environment settings: " + ",".join(missing))
    if environ["GITHUB_REF_NAME"] != "main" or environ["GITHUB_EVENT_NAME"] != "push":
        raise TelemetryPublishError("Only main push CI telemetry may be written to Staging")
    if environ["GITHUB_WORKFLOW"] != "Engine CI":
        raise TelemetryPublishError("Unexpected workflow name")
    if not environ["GITHUB_RUN_ID"].isdigit() or not environ["GITHUB_RUN_NUMBER"].isdigit():
        raise TelemetryPublishError("Invalid GitHub run identifiers")
    if not SHA_PATTERN.fullmatch(environ["GITHUB_SHA"]):
        raise TelemetryPublishError("Invalid source commit SHA")

    job_results = {
        "test": environ["ENGINE_CI_TEST_RESULT"],
        "staging_migrate": environ["ENGINE_CI_STAGING_MIGRATE_RESULT"],
        "staging_worker_verify": environ["ENGINE_CI_STAGING_WORKER_RESULT"],
    }
    if any(value not in JOB_RESULTS for value in job_results.values()):
        raise TelemetryPublishError("Invalid GitHub job result")
    conclusion = _ci_conclusion(job_results)
    recorded_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    ci = {
        "workflow": "Engine CI",
        "run_id": int(environ["GITHUB_RUN_ID"]),
        "run_number": int(environ["GITHUB_RUN_NUMBER"]),
        "event": "push",
        "branch": "main",
        "head_sha": environ["GITHUB_SHA"],
        "conclusion": conclusion,
        "jobs": job_results,
        "recorded_at": recorded_at,
    }
    kill_switch = _policy_snapshot(root, environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "disabled"))
    kill_switch["source_sha"] = environ["GITHUB_SHA"]
    kill_switch["recorded_at"] = recorded_at

    sql = (
        "INSERT INTO audit_log(task_id,event,details_json,created_at) "
        "VALUES(NULL,?,?,?),(NULL,?,?,?)"
    )
    params = [
        "ci_health_snapshot", json.dumps(ci, sort_keys=True, separators=(",", ":")), recorded_at,
        "kill_switch_snapshot", json.dumps(kill_switch, sort_keys=True, separators=(",", ":")), recorded_at,
    ]
    return sql, params, {
        "conclusion": conclusion,
        "kill_switch": kill_switch["status"],
        "recorded_at": recorded_at,
        "run_id": ci["run_id"],
    }


def publish_staging_telemetry(
    environ: dict[str, str] | None = None,
    *,
    root: Path | None = None,
    client_factory=D1Client,
    now: datetime | None = None,
) -> dict:
    env = dict(os.environ if environ is None else environ)
    repo_root = root or Path(__file__).resolve().parents[1]
    sql, params, summary = build_telemetry_statement(env, repo_root, now)
    try:
        client = client_factory(
            account_id=env["CLOUDFLARE_ACCOUNT_ID"],
            database_id=STAGING_DATABASE_ID,
            api_token=env["CLOUDFLARE_API_TOKEN"],
        )
        written = client.query(sql, params)
        changes = written.get("meta", {}).get("changes")
        if changes != 2:
            raise TelemetryPublishError("Staging D1 did not confirm both telemetry inserts")
        readback = client.query(
            "SELECT event,created_at,details_json FROM audit_log "
            "WHERE task_id IS NULL AND created_at=? "
            "AND event IN ('ci_health_snapshot','kill_switch_snapshot')",
            [summary["recorded_at"]],
        )
        rows = readback.get("results")
        if not isinstance(rows, list) or len(rows) != 2:
            raise TelemetryPublishError("Staging D1 readback did not confirm both telemetry events")
        events = {row.get("event") for row in rows if isinstance(row, dict)}
        if events != {"ci_health_snapshot", "kill_switch_snapshot"}:
            raise TelemetryPublishError("Staging D1 readback returned unexpected telemetry events")
    except (D1APIError, D1ConfigurationError, OSError, ValueError) as exc:
        raise TelemetryPublishError("Staging D1 telemetry write/readback failed: " + type(exc).__name__) from None
    return {"published_events": 2, "verified_events": 2, "database": STAGING_DATABASE_NAME, **summary}


def main() -> int:
    try:
        result = publish_staging_telemetry()
    except TelemetryPublishError as exc:
        print("Staging ops telemetry failed: " + str(exc))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
