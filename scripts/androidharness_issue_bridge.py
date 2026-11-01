#!/usr/bin/env python3
"""Narrow GitHub-Issue task bridge for an authenticated AndroidHarness workspace.

No arbitrary shell is accepted. The bridge supports only read-only environment/status
inspection and Python unit tests against the exact fetched origin/main commit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY = "ghith93/raawaa-autonomous-engine"
OWNER = "ghith93"
ROOT = Path(__file__).resolve().parents[1]
TASK_PREFIX = "[AndroidHarness Task]"
RUNNING_PREFIX = "[AndroidHarness Running]"
COMPLETE_PREFIX = "[AndroidHarness Complete]"
FAILED_PREFIX = "[AndroidHarness Failed]"
REJECTED_PREFIX = "[AndroidHarness Rejected]"
TASK_MARKER = "<!-- RAawaa-AndroidHarness-Task:v1 -->"
POLICY_FILES = (
    "AGENTS.md",
    "docs/master-autonomous-execution-directive.ar.md",
    "docs/continuous-improvement-charter.md",
    "docs/architecture.md",
    "docs/operations.md",
)
ALLOWED_OPERATIONS = {"inspect_environment", "git_status", "run_python_tests"}
TOKEN_PATTERN = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"Bearer\s+\S+|sk-[A-Za-z0-9_-]{20,})"
)


class BridgeError(RuntimeError):
    """A bounded task bridge failure."""


def redact(text: str) -> str:
    return TOKEN_PATTERN.sub("[REDACTED]", text)


def bounded_text(text: str, limit: int) -> str:
    safe = redact(text)
    if len(safe.encode("utf-8")) <= limit:
        return safe
    suffix = "\n...[output truncated]"
    budget = max(0, limit - len(suffix.encode("utf-8")))
    clipped = safe.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return clipped + suffix


def summarize_unittest_output(text: str) -> dict[str, Any]:
    """Return only aggregate unittest status; never publish raw test output."""
    ran = re.search(r"(?m)^Ran (\d+) tests? in [0-9.]+s$", text)
    failed = re.search(r"(?m)^FAILED \(([^)]+)\)$", text)
    passed = bool(re.search(r"(?m)^OK(?: \([^)]*\))?$", text))
    return {
        "tests_run": int(ran.group(1)) if ran else None,
        "status": "failed" if failed else ("passed" if passed else "incomplete"),
        "failure_summary": failed.group(1) if failed else None,
    }


def parse_task(body: str) -> dict[str, Any]:
    if TASK_MARKER not in body:
        raise BridgeError("task marker is missing")
    fence = chr(96) * 3
    block_start = body.find(fence + "json")
    if block_start < 0:
        raise BridgeError("task must contain one fenced JSON object")
    payload_start = body.find("\n", block_start) + 1
    block_end = body.find(fence, payload_start)
    if payload_start <= 0 or block_end < 0:
        raise BridgeError("task JSON block is incomplete")
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise BridgeError("task JSON contains duplicate keys")
            result[key] = value
        return result
    try:
        task = json.loads(
            body[payload_start:block_end].strip(),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(BridgeError("task JSON contains a non-finite number")),
        )
    except json.JSONDecodeError as exc:
        raise BridgeError("task JSON is invalid") from exc
    if not isinstance(task, dict):
        raise BridgeError("task must be a JSON object")
    allowed = {"schema_version", "operation", "commit_sha", "timeout_seconds", "max_output_bytes"}
    if set(task) - allowed:
        raise BridgeError("task contains unsupported fields")
    if type(task.get("schema_version")) is not int or task.get("schema_version") != 1:
        raise BridgeError("unsupported task schema")
    operation = task.get("operation")
    if not isinstance(operation, str) or operation not in ALLOWED_OPERATIONS:
        raise BridgeError("operation is not allowlisted")
    timeout_seconds = task.get("timeout_seconds", 600)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 15 <= timeout_seconds <= 600:
        raise BridgeError("timeout_seconds must be between 15 and 600")
    max_output_bytes = task.get("max_output_bytes", 12000)
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1024 <= max_output_bytes <= 16000:
        raise BridgeError("max_output_bytes must be between 1024 and 16000")
    if operation == "run_python_tests":
        commit_sha = task.get("commit_sha")
        if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise BridgeError("run_python_tests requires a full 40-character commit_sha")
    elif "commit_sha" in task:
        raise BridgeError("commit_sha is only valid for run_python_tests")
    return {
        "schema_version": 1,
        "operation": operation,
        "commit_sha": task.get("commit_sha"),
        "timeout_seconds": timeout_seconds,
        "max_output_bytes": max_output_bytes,
    }


def is_candidate_issue(issue: dict[str, Any]) -> bool:
    return (
        "pull_request" not in issue
        and issue.get("state") == "open"
        and (issue.get("user") or {}).get("login") == OWNER
        and isinstance(issue.get("title"), str)
        and issue["title"].startswith(TASK_PREFIX)
        and isinstance(issue.get("number"), int)
    )


def verify_policy_files(root: Path = ROOT) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for relative in POLICY_FILES:
        path = root / relative
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise BridgeError(f"required policy file unavailable: {relative}") from exc
        if not content.strip():
            raise BridgeError(f"required policy file is empty: {relative}")
        fingerprints[relative] = hashlib.sha256(content).hexdigest()
    return fingerprints



def verify_repository_root(root: Path = ROOT) -> None:
    root = root.resolve()
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True, timeout=15, check=False
    )
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
        raise BridgeError("script is not running from the expected repository root")
    origin = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        text=True, capture_output=True, timeout=15, check=False
    )
    if origin.returncode != 0:
        raise BridgeError("repository origin could not be verified")
    remote = origin.stdout.strip().removesuffix(".git").rstrip("/")
    approved_remotes = {
        "https://github.com/ghith93/raawaa-autonomous-engine",
        "git@github.com:ghith93/raawaa-autonomous-engine",
        "ssh://git@github.com/ghith93/raawaa-autonomous-engine",
    }
    if remote not in approved_remotes:
        raise BridgeError("repository origin does not match the approved Raawaa Autonomous Engine")


def safe_extract_git_archive(archive_bytes: bytes, destination: Path) -> None:
    if len(archive_bytes) > 64 * 1024 * 1024:
        raise BridgeError("repository archive exceeds the 64 MiB limit")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:")
    except tarfile.TarError as exc:
        raise BridgeError("git archive is invalid") from exc
    with archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                raise BridgeError("archive contains an unsafe path")
            target = destination.joinpath(*relative.parts)
            resolved = target.resolve()
            if root != resolved and root not in resolved.parents:
                raise BridgeError("archive path escapes its temporary workspace")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise BridgeError("archive file content is unavailable")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                try:
                    target.chmod(0o600)
                except OSError:
                    pass
            else:
                raise BridgeError("archive contains a symlink or unsupported file type")


def minimal_python_environment(workspace: Path) -> dict[str, str]:
    sandbox_home = workspace / ".androidharness-home"
    temporary = workspace / ".androidharness-tmp"
    for directory in (
        sandbox_home,
        temporary,
        sandbox_home / ".config",
        sandbox_home / ".cache",
        sandbox_home / ".local" / "share",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    allowed = ("PATH", "LANG", "LC_ALL", "TERM")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update({
        "HOME": str(sandbox_home),
        "TMPDIR": str(temporary),
        "XDG_CONFIG_HOME": str(sandbox_home / ".config"),
        "XDG_CACHE_HOME": str(sandbox_home / ".cache"),
        "XDG_DATA_HOME": str(sandbox_home / ".local" / "share"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(workspace),
    })
    return environment


class AndroidHarnessIssueBridge:
    def __init__(self, root: Path = ROOT, audit_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.audit_path = audit_path or (
            Path.home() / ".cache" / "raawaa-autonomous-engine" / "androidharness-bridge.jsonl"
        )

    @staticmethod
    def _api(method: str, endpoint: str, body: dict[str, Any] | None = None) -> Any:
        command = ["gh", "api", endpoint]
        input_text = None
        if method != "GET":
            command.extend(["--method", method, "--input", "-"])
            input_text = json.dumps(body or {}, separators=(",", ":"))
        try:
            result = subprocess.run(
                command, input=input_text, text=True, capture_output=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError("GitHub CLI request could not be completed") from exc
        if result.returncode != 0:
            raise BridgeError("GitHub API request failed; check gh authentication and repository access")
        if not result.stdout.strip():
            return {}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BridgeError("GitHub API returned invalid JSON") from exc

    def acquire_single_instance_lock(self) -> int:
        directory = self.audit_path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix" and stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise BridgeError("local bridge directory permissions must be private (0700)")
        lock_path = directory / "bridge.lock"
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            if os.name == "posix" and stat.S_IMODE(lock_path.stat().st_mode) & 0o077:
                raise BridgeError("local bridge lock permissions must be private (0600)")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BridgeError) as exc:
            os.close(descriptor)
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError("another AndroidHarness bridge instance is already running") from exc
        return descriptor

    def _audit(self, record: dict[str, Any]) -> None:
        directory = self.audit_path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix" and stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise BridgeError("local audit directory permissions must be private (0700)")
        if self.audit_path.exists() and os.name == "posix":
            if stat.S_IMODE(self.audit_path.stat().st_mode) & 0o077:
                raise BridgeError("local audit file permissions must be private (0600)")
        descriptor = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise BridgeError("local audit record could not be written")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        operation = task["operation"]
        if operation == "inspect_environment":
            versions: dict[str, str] = {}
            for executable in ("python3", "git", "gh", "node", "npm"):
                try:
                    result = subprocess.run(
                        [executable, "--version"], text=True, capture_output=True, timeout=10, check=False
                    )
                    versions[executable] = (result.stdout or result.stderr).strip()[:200] if result.returncode == 0 else "unavailable"
                except (OSError, subprocess.TimeoutExpired):
                    versions[executable] = "unavailable"
            disk = shutil.disk_usage(self.root)
            memory_available = None
            meminfo = Path("/proc/meminfo")
            if meminfo.exists():
                for line in meminfo.read_text(errors="ignore").splitlines():
                    if line.startswith("MemAvailable:"):
                        memory_available = line.split(":", 1)[1].strip()
                        break
            return {
                "exit_code": 0,
                "platform": platform.platform(),
                "machine": platform.machine(),
                "cpu_count": os.cpu_count(),
                "versions": versions,
                "disk_free_bytes": disk.free,
                "memory_available": memory_available,
            }

        if operation == "git_status":
            def git(*args: str) -> str:
                result = subprocess.run(
                    ["git", "-C", str(self.root), *args],
                    text=True, capture_output=True, timeout=15, check=False
                )
                if result.returncode != 0:
                    raise BridgeError("git status inspection failed")
                return result.stdout.strip()
            status = git("status", "--porcelain")
            return {
                "exit_code": 0,
                "branch": git("branch", "--show-current"),
                "head": git("rev-parse", "HEAD"),
                "working_tree_clean": not bool(status),
                "changed_path_count": len(status.splitlines()) if status else 0,
            }

        if operation == "run_python_tests":
            commit_sha = task["commit_sha"]
            fetch = subprocess.run(
                ["git", "-C", str(self.root), "fetch", "--quiet", "--no-tags", "origin", "main"],
                text=True, capture_output=True, timeout=120, check=False
            )
            if fetch.returncode != 0:
                raise BridgeError("could not fetch origin/main")
            current_main = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "FETCH_HEAD"],
                text=True, capture_output=True, timeout=15, check=False
            )
            if current_main.returncode != 0 or current_main.stdout.strip() != commit_sha:
                raise BridgeError("requested commit is not the current fetched origin/main")
            archive = subprocess.run(
                ["git", "-C", str(self.root), "archive", "--format=tar", commit_sha],
                capture_output=True, timeout=90, check=False
            )
            if archive.returncode != 0:
                raise BridgeError("could not create a clean source archive for the requested commit")
            with tempfile.TemporaryDirectory(prefix="raawaa-androidharness-") as temporary:
                workspace = Path(temporary)
                safe_extract_git_archive(archive.stdout, workspace)
                # Validate the policy documents from the exact commit being tested,
                # not only the possibly older checkout running this bridge.
                target_policy = verify_policy_files(workspace)
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                        cwd=workspace,
                        env=minimal_python_environment(workspace),
                        text=True,
                        capture_output=True,
                        timeout=task["timeout_seconds"],
                        check=False,
                    )
                    output = bounded_text((result.stdout or "") + "\n" + (result.stderr or ""),
                                          task["max_output_bytes"])
                    return {
                        "exit_code": result.returncode,
                        "commit_sha": commit_sha,
                        "test_command": "python -m unittest discover -s tests -v",
                        "output": output,
                    }
                except subprocess.TimeoutExpired as exc:
                    partial = (exc.stdout or "") + "\n" + (exc.stderr or "")
                    if isinstance(partial, bytes):
                        partial = partial.decode("utf-8", errors="replace")
                    return {
                        "exit_code": 124,
                        "commit_sha": commit_sha,
                        "test_command": "python -m unittest discover -s tests -v",
                        "output": bounded_text(partial + "\nTIMEOUT", task["max_output_bytes"]),
                    }

        raise BridgeError("operation is not allowlisted")

    def run_once(self) -> bool:
        issues = self._api("GET", f"repos/{REPOSITORY}/issues?state=open&per_page=50")
        if not isinstance(issues, list):
            raise BridgeError("GitHub issue listing returned an unexpected response")
        for issue in issues:
            if not is_candidate_issue(issue):
                continue
            number = issue["number"]
            original_title = issue["title"]
            try:
                task = parse_task(issue.get("body") or "")
            except BridgeError as exc:
                self._api("PATCH", f"repos/{REPOSITORY}/issues/{number}", {
                    "title": f"{REJECTED_PREFIX} {original_title[len(TASK_PREFIX):].strip()}"
                })
                self._api("POST", f"repos/{REPOSITORY}/issues/{number}/comments", {
                    "body": f"AndroidHarness bridge rejected this task before execution: {exc}"
                })
                return True
            # A missing or invalid policy is a fail-closed operational fault, not
            # a malformed task: leave the issue pending so it can be retried safely.
            policy = verify_policy_files(self.root)

            started = dt.datetime.now(dt.timezone.utc)
            self._audit({
                "issue": number,
                "operation": task["operation"],
                "commit_sha": task.get("commit_sha"),
                "started_at": started.isoformat(),
                "status": "claimed",
                "policy_sha256": policy,
            })
            running_title = f"{RUNNING_PREFIX} {original_title[len(TASK_PREFIX):].strip()}"
            self._api("PATCH", f"repos/{REPOSITORY}/issues/{number}", {"title": running_title})
            self._api("POST", f"repos/{REPOSITORY}/issues/{number}/comments", {
                "body": (
                    f"AndroidHarness bridge claimed this bounded task.\n\n"
                    f"Operation: {task['operation']}\n"
                    f"Commit: {task.get('commit_sha') or 'not applicable'}\n"
                    f"Policy files: verified ({len(policy)} fingerprints)\n"
                    "Arbitrary shell, root, provider calls, and production writes are not available."
                )
            })

            try:
                result = self._execute(task)
                success = result.get("exit_code") == 0
                status = "completed" if success else "failed"
            except Exception as exc:
                result = {"exit_code": 1, "error": str(exc)}
                success = False
                status = "failed"

            finished = dt.datetime.now(dt.timezone.utc)
            elapsed = (finished - started).total_seconds()
            audit_record = {
                "issue": number,
                "operation": task["operation"],
                "commit_sha": task.get("commit_sha"),
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_seconds": round(elapsed, 3),
                "status": status,
                "exit_code": result.get("exit_code"),
                "policy_sha256": policy,
            }
            self._audit(audit_record)
            final_prefix = COMPLETE_PREFIX if success else FAILED_PREFIX
            final_title = f"{final_prefix} {original_title[len(TASK_PREFIX):].strip()}"
            comment = {
                "task_id": number,
                "status": status,
                "operation": task["operation"],
                "commit_sha": task.get("commit_sha"),
                "duration_seconds": round(elapsed, 3),
                "exit_code": result.get("exit_code"),
                "policy_sha256": policy,
                "result": result,
            }
            self._api("PATCH", f"repos/{REPOSITORY}/issues/{number}", {"title": final_title})
            self._api("POST", f"repos/{REPOSITORY}/issues/{number}/comments", {
                "body": "AndroidHarness task result:\n\n" + json.dumps(comment, ensure_ascii=False, indent=2)
            })
            if success:
                self._api("PATCH", f"repos/{REPOSITORY}/issues/{number}", {"state": "closed"})
            return True
        return False

    def run_forever(self, interval: int, max_tasks: int) -> None:
        completed = 0
        while True:
            try:
                processed = self.run_once()
            except BridgeError as exc:
                print(f"AndroidHarness bridge poll failed safely: {exc}", file=sys.stderr)
                time.sleep(interval)
                continue
            if processed:
                completed += 1
                if max_tasks and completed >= max_tasks:
                    return
            else:
                time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded AndroidHarness GitHub issue bridge.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="process at most one eligible issue and exit")
    mode.add_argument("--poll", action="store_true", help="poll GitHub continuously")
    parser.add_argument("--poll-interval", type=int, default=30)
    parser.add_argument("--max-tasks", type=int, default=0, help="0 means no limit while polling")
    args = parser.parse_args()
    if args.poll_interval < 15 or args.poll_interval > 300:
        parser.error("--poll-interval must be between 15 and 300 seconds")
    if args.max_tasks < 0:
        parser.error("--max-tasks cannot be negative")

    lock_descriptor = None
    try:
        auth = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            text=True, capture_output=True, timeout=15, check=False
        )
        if auth.returncode != 0:
            raise BridgeError("GitHub CLI is not authenticated; no task was claimed")
        bridge = AndroidHarnessIssueBridge()
        verify_repository_root(bridge.root)
        verify_policy_files(bridge.root)
        lock_descriptor = bridge.acquire_single_instance_lock()
        if args.once:
            print("processed" if bridge.run_once() else "no eligible tasks")
        else:
            print("AndroidHarness bridge active; waiting for approved GitHub tasks")
            bridge.run_forever(args.poll_interval, args.max_tasks)
    except KeyboardInterrupt:
        print("AndroidHarness bridge stopped")
    except BridgeError as exc:
        print(f"AndroidHarness bridge stopped safely: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
