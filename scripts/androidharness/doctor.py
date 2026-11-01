#!/usr/bin/env python3
"""Read-only AndroidHarness environment diagnostic.

This script intentionally does not install packages, access credential files,
make network requests, or print environment-variable values.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
COMMAND_TIMEOUT_SECONDS = 5
SECRET_MARKERS = (
    "TOKEN",
    "API_KEY",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
)


def _run(arguments: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    """Run one bounded, explicitly supplied read-only command."""
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__}

    output = (result.stdout + "\n" + result.stderr).strip()
    first_lines = [line.strip() for line in output.splitlines() if line.strip()]
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "summary": " | ".join(first_lines[:2])[:240] if first_lines else "",
    }


def _run_full(arguments: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    """Run a bounded read-only command when the full stdout is needed locally."""
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__}
    return {"ok": result.returncode == 0, "stdout": result.stdout}



def _tool(name: str, arguments: list[str]) -> dict[str, Any]:
    executable = shutil.which(name)
    if executable is None:
        return {"available": False, "version_check": None}
    result = _run([executable, *arguments])
    return {
        "available": True,
        "version_check": result.get("summary") or result.get("error"),
        "version_command_ok": result["ok"],
    }


def _property(name: str) -> str | None:
    executable = shutil.which("getprop")
    if executable is None:
        return None
    result = _run([executable, name])
    if not result["ok"]:
        return None
    value = result.get("summary", "").strip()
    return value or None


def _memory() -> dict[str, int | None]:
    values: dict[str, int | None] = {"total_bytes": None, "available_bytes": None}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                amount = int(rest.strip().split()[0]) * 1024
                values["total_bytes" if key == "MemTotal" else "available_bytes"] = amount
    except (OSError, ValueError, IndexError):
        pass
    return values


def _environment_marker(name: str) -> dict[str, bool]:
    value = os.environ.get(name)
    if not value:
        return {"set": False, "path_exists": False, "writable": False}
    path = Path(value).expanduser()
    return {
        "set": True,
        "path_exists": path.exists(),
        "writable": path.is_dir() and os.access(path, os.W_OK),
    }


def _workspace() -> dict[str, Any]:
    if shutil.which("git") is None:
        return {"is_git_repository": False, "branch": None, "commit": None, "dirty_file_count": None}
    top = _run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT)
    if not top["ok"]:
        return {"is_git_repository": False, "branch": None, "commit": None, "dirty_file_count": None}
    branch = _run(["git", "branch", "--show-current"], cwd=ROOT)
    commit = _run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT)
    status = _run_full(["git", "status", "--porcelain"], cwd=ROOT)
    dirty_count = (
        sum(1 for line in status["stdout"].splitlines() if line.strip())
        if status["ok"] else None
    )
    return {
        "is_git_repository": True,
        "branch": branch.get("summary") if branch["ok"] else None,
        "commit": commit.get("summary") if commit["ok"] else None,
        "dirty_file_count": dirty_count,
    }


def build_report() -> dict[str, Any]:
    try:
        disk = shutil.disk_usage(ROOT)
        free_disk = disk.free
    except OSError:
        free_disk = None

    tools = {
        "python": {"available": True, "version_check": platform.python_version()},
        "git": _tool("git", ["--version"]),
        "gh": _tool("gh", ["--version"]),
        "node": _tool("node", ["--version"]),
        "npm": _tool("npm", ["--version"]),
        "pip": _tool("pip", ["--version"]),
        "java": _tool("java", ["-version"]),
        "d8": _tool("d8", ["--version"]),
        "aapt2": _tool("aapt2", ["version"]),
        "apksigner": _tool("apksigner", ["version"]),
        "gradle": _tool("gradle", ["--version"]),
        "ssh": _tool("ssh", ["-V"]),
        "clang": _tool("clang", ["--version"]),
        "cmake": _tool("cmake", ["--version"]),
    }

    sdk_platforms: list[int] = []
    sdk_root = Path.home() / "android-sdk" / "platforms"
    if sdk_root.is_dir():
        for candidate in sdk_root.glob("android-*"):
            api = candidate.name.removeprefix("android-")
            if api.isdigit() and (candidate / "android.jar").is_file():
                sdk_platforms.append(int(api))

    markers = {
        name: _environment_marker(name)
        for name in ("PREFIX", "HARNESS_SCRATCH", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT")
    }
    android_jar = os.environ.get("ANDROID_JAR")
    markers["ANDROID_JAR"] = {
        "set": bool(android_jar),
        "path_exists": bool(android_jar and Path(android_jar).expanduser().is_file()),
        "writable": False,
    }

    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workspace": {
            "root": str(ROOT),
            **_workspace(),
        },
        "device": {
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "cpu_cores": os.cpu_count(),
            "android_model": _property("ro.product.model"),
            "android_release": _property("ro.build.version.release"),
            "primary_abi": _property("ro.product.cpu.abi"),
            "memory": _memory(),
            "free_disk_bytes": free_disk,
        },
        "tools": tools,
        "environment_markers": markers,
        "android_sdk": {"installed_platform_apis": sorted(sdk_platforms)},
        "mutations_performed": False,
        "network_requests_performed": False,
        "secret_values_emitted": False,
        "note": "Read-only inventory. Missing optional tools do not block the Python-first engine workflow.",
    }
    # Defensive invariant: do not serialize values of environment variables
    # whose names look like credentials, even if later fields are added.
    serialized = json.dumps(report, ensure_ascii=False)
    for name, value in os.environ.items():
        if value and any(marker in name.upper() for marker in SECRET_MARKERS):
            if value in serialized:
                raise RuntimeError("Diagnostic output unexpectedly contains a credential value")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON (the default).")
    parser.parse_args()
    print(json.dumps(build_report(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
