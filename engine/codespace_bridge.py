"""Opt-in, bounded Codespace executor for inspect-only and fixed test operations.

No arbitrary shell, patching, deployment, inference, external writes, or secrets access.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from engine.codespace_task_store import CodespaceTaskStore
from engine.operating_policy import OperatingPolicyError, load_operating_policy
from engine.remote_task_store import RemoteTaskStoreError


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MAX_OUTPUT_CHARS = 8_000
COMMAND_TIMEOUT_SECONDS = 240
POLL_MIN_SECONDS = 30
POLL_MAX_SECONDS = 300
MAX_POLL_RUNTIME_SECONDS = 3600
TEST_COMMANDS = {
    "python_tests": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    "worker_tests": ["npm", "run", "test:worker"],
    "workflow_tests": ["npm", "run", "test:workflow-spike"],
}


def _bounded(value: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[output truncated]"


def _run_fixed(command: list[str], runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    try:
        # Do not pass Codespaces secrets, provider keys, or user shell configuration
        # into project test processes. Use a disposable HOME and a minimal environment.
        with tempfile.TemporaryDirectory(prefix="raawaa-codespace-") as isolated_home:
            safe_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": isolated_home,
                "USERPROFILE": isolated_home,
                "CI": "true",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            completed = runner(
                command, cwd=REPOSITORY_ROOT, capture_output=True, text=True,
                timeout=COMMAND_TIMEOUT_SECONDS, check=False, shell=False, env=safe_env,
            )
        return {
            "exit_code": int(completed.returncode),
            "stdout": _bounded(completed.stdout or ""),
            "stderr": _bounded(completed.stderr or ""),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {
            "exit_code": None, "stdout": _bounded(stdout), "stderr": _bounded(stderr),
            "timed_out": True,
        }


def execute_task(task: dict[str, Any], runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Execute only fixed operations selected by task type and validated payload."""
    task_type = task.get("task_type")
    try:
        payload = json.loads(task.get("payload_json", "{}"))
    except (TypeError, ValueError):
        return {"status": "blocked", "reason": "invalid_task_payload", "external_writes": 0}
    if not isinstance(payload, dict):
        return {"status": "blocked", "reason": "invalid_task_payload", "external_writes": 0}

    operation = payload.get("operation")
    if task_type == "codespace_inspect" and operation == "inspect":
        git = _run_fixed(["git", "rev-parse", "HEAD"], runner)
        status = _run_fixed(["git", "status", "--short", "--branch"], runner)
        return {
            "status": "succeeded" if git["exit_code"] == 0 and status["exit_code"] == 0 else "failed",
            "operation": "inspect",
            "commit": git["stdout"].strip() if git["exit_code"] == 0 else None,
            "git_status": status["stdout"],
            "working_tree_clean": status["exit_code"] == 0 and not status["stdout"].strip().splitlines()[1:],
            "external_writes": 0,
        }
    if task_type == "codespace_test" and operation in TEST_COMMANDS:
        outcome = _run_fixed(TEST_COMMANDS[operation], runner)
        return {
            "status": "succeeded" if outcome["exit_code"] == 0 and not outcome["timed_out"] else "failed",
            "operation": operation,
            **outcome,
            "external_writes": 0,
        }
    return {"status": "blocked", "reason": "operation_not_allowlisted", "external_writes": 0}


def run_once(store: CodespaceTaskStore | None = None,
             runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Load mandatory policy before any remote claim, then process at most one task."""
    try:
        policy = load_operating_policy()
    except Exception:
        return {"status": "blocked", "reason": "operating_policy_unavailable"}

    client = store or CodespaceTaskStore()
    try:
        task = client.claim_next()
    except (RemoteTaskStoreError, ValueError):
        return {"status": "blocked", "reason": "codespace_claim_failed"}
    if task is None:
        return {"status": "idle"}

    task_id = task.get("id")
    lease_token = task.get("lease_token")
    if not isinstance(task_id, str) or not isinstance(lease_token, str):
        return {"status": "blocked", "reason": "invalid_claim"}

    try:
        client.heartbeat(task_id, lease_token)
        try:
            result = execute_task(task, runner=runner)
        except Exception as exc:
            result = {"status": "blocked", "reason": f"bridge_execution_failed:{type(exc).__name__}",
                      "external_writes": 0}
        terminal = result.pop("status")
        result["policy_documents"] = policy.get("documents_loaded", [])
        result["policy_fingerprints"] = policy.get("fingerprints", {})
        result["executor"] = "codespace"
        client.finish(task_id, terminal, result=result, lease_token=lease_token,
                      error=result.get("reason"))
        return {"id": task_id, "status": terminal, "operation": result.get("operation")}
    except RemoteTaskStoreError:
        # A lost/ambiguous lease must never be followed by a stale completion write.
        return {"id": task_id, "status": "lease_lost_or_remote_error"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the opt-in Raawaa Codespace bridge")
    parser.add_argument("--preflight", action="store_true", help="check the scoped token without claiming work")
    parser.add_argument("--poll", action="store_true", help="poll with bounded backoff while the Codespace is active")
    args = parser.parse_args(argv)

    if args.preflight:
        try:
            load_operating_policy()
            status = CodespaceTaskStore().preflight()
            print(json.dumps({"status": "ready", "scope": status}, sort_keys=True))
            return 0
        except (OperatingPolicyError, RemoteTaskStoreError, ValueError):
            print('{"status":"blocked","reason":"preflight_failed"}')
            return 1

    if os.environ.get("RAAWAA_ENGINE_CODESPACE_BRIDGE", "").strip().lower() != "enabled":
        print('{"status":"disabled","reason":"explicit_opt_in_required"}')
        return 0

    try:
        store = CodespaceTaskStore()
    except ValueError:
        print('{"status":"blocked","reason":"configuration_invalid"}')
        return 1

    if not args.poll:
        print(json.dumps(run_once(store), sort_keys=True))
        return 0

    delay = POLL_MIN_SECONDS
    started = time.monotonic()
    while time.monotonic() - started < MAX_POLL_RUNTIME_SECONDS:
        result = run_once(store)
        if result.get("status") == "idle":
            time.sleep(delay)
            delay = min(POLL_MAX_SECONDS, delay * 2)
            continue
        delay = POLL_MIN_SECONDS
        print(json.dumps(result, sort_keys=True))
        if result.get("status") in {"blocked", "lease_lost_or_remote_error"}:
            return 1
    print('{"status":"stopped","reason":"maximum_poll_runtime_reached"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
