"""Fail-closed trusted-main checkout policy for the legacy Codespace executor.

This is the documented trusted-code-only fallback, not an OS sandbox. It permits
only a clean checkout of the canonical repository's main branch whose exact HEAD
is already an ancestor of the public GitHub main ref.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

TRUSTED_REPOSITORY = "ghith93/raawaa-autonomous-engine"
TRUSTED_REMOTE_HTTPS = {
    "https://github.com/" + TRUSTED_REPOSITORY,
    "https://github.com/" + TRUSTED_REPOSITORY + ".git",
}
TRUSTED_REMOTE_SSH = {
    "git@github.com:" + TRUSTED_REPOSITORY,
    "git@github.com:" + TRUSTED_REPOSITORY + ".git",
}
GITHUB_API = "https://api.github.com"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_commit_is_on_main(commit_sha: str, opener_factory: Callable[..., Any] = build_opener) -> bool:
    """Return true only when the exact commit is an ancestor of canonical main."""
    if not isinstance(commit_sha, str) or not COMMIT_RE.fullmatch(commit_sha.lower()):
        return False
    url = (
        f"{GITHUB_API}/repos/{TRUSTED_REPOSITORY}/compare/"
        f"{commit_sha.lower()}...main"
    )
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RaawaaAutonomousEngine/1.0",
    }, method="GET")
    try:
        with opener_factory(_RejectRedirects).open(request, timeout=10) as response:
            raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            return False
        payload = json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") in {"ahead", "identical"}
        and isinstance(payload.get("base_commit"), dict)
        and isinstance(payload["base_commit"].get("sha"), str)
        and payload["base_commit"]["sha"].lower() == commit_sha.lower()
        and isinstance(payload.get("head_commit"), dict)
        and isinstance(payload["head_commit"].get("sha"), str)
        and bool(COMMIT_RE.fullmatch(payload["head_commit"]["sha"].lower()))
    )


def verify_trusted_checkout(
    root=None,
    runner: Callable[..., Any] | None = None,
    commit_verifier: Callable[[str], bool] = verify_commit_is_on_main,
) -> dict[str, Any]:
    """Verify canonical origin, clean main checkout, and immutable main ancestry."""
    from pathlib import Path
    import subprocess

    workspace = Path(root).resolve() if root is not None else Path(__file__).resolve().parent.parent
    run = runner or subprocess.run
    commands = {
        "root": ["git", "rev-parse", "--show-toplevel"],
        "branch": ["git", "branch", "--show-current"],
        "commit": ["git", "rev-parse", "HEAD"],
        "status": ["git", "status", "--porcelain", "--untracked-files=all"],
        "origin": ["git", "remote", "get-url", "origin"],
    }
    results = {}
    for name, command in commands.items():
        try:
            result = run(
                command, cwd=str(workspace), capture_output=True, text=True,
                timeout=10, check=False, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {"trusted": False, "reason": "git_preflight_failed"}
        if getattr(result, "returncode", 1) != 0:
            return {"trusted": False, "reason": "git_preflight_failed"}
        results[name] = (getattr(result, "stdout", "") or "").strip()

    try:
        git_root = Path(results["root"]).resolve()
    except (OSError, ValueError):
        return {"trusted": False, "reason": "git_root_invalid"}
    if git_root != workspace:
        return {"trusted": False, "reason": "workspace_root_mismatch"}
    if results["branch"] != "main":
        return {"trusted": False, "reason": "branch_not_main"}
    if results["origin"] not in TRUSTED_REMOTE_HTTPS | TRUSTED_REMOTE_SSH:
        return {"trusted": False, "reason": "origin_not_canonical"}
    if results["status"]:
        return {"trusted": False, "reason": "working_tree_not_clean"}
    commit_sha = results["commit"].lower()
    if not COMMIT_RE.fullmatch(commit_sha):
        return {"trusted": False, "reason": "commit_sha_invalid"}
    try:
        verified = commit_verifier(commit_sha) is True
    except Exception:
        verified = False
    if not verified:
        return {"trusted": False, "reason": "commit_not_on_canonical_main"}
    return {"trusted": True, "reason": "verified_trusted_main", "commit": commit_sha}
