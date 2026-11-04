"""Collect the state document consumed by `engine.development_cycle`.

Kept separate from the ranking logic on purpose: this half touches the network
(GitHub API, `git`), the other half must never do so. The collector is allowed
to fail — a missing signal is reported as `None` and the ranking degrades, it
does not invent a value.

Read-only by construction: every call is a GET, or `git` reading the remote.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any


def _gh(args: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _git(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _runs(repo: str, workflow: str, limit: int = 5) -> list[dict[str, Any]]:
    data = _gh(
        [
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow,
            "--limit",
            str(limit),
            "--json",
            "headSha,status,conclusion,event,createdAt,updatedAt",
        ]
    )
    return data or []


def collect(repo: str | None = None) -> dict[str, Any]:
    repo = repo or "ghith93/raawaa-autonomous-engine"

    pulls = _gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "50",
            "--json",
            "number,title,createdAt,mergeable,mergeStateStatus,isDraft,statusCheckRollup",
        ]
    ) or []
    open_pull_requests = []
    for pr in pulls:
        rollup = pr.get("statusCheckRollup") or []
        conclusions = {
            str(check.get("conclusion") or check.get("state") or "").lower()
            for check in rollup
            if isinstance(check, dict)
        }
        if conclusions and conclusions <= {"skipped"}:
            checks = "unknown"
        elif "failure" in conclusions or "timed_out" in conclusions:
            checks = "failure"
        elif conclusions and conclusions <= {"success", "skipped", "neutral", ""}:
            checks = "success"
        elif not conclusions:
            checks = "unknown"
        else:
            checks = "pending"
        open_pull_requests.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "created_at": pr.get("createdAt"),
                "mergeable": pr.get("mergeable"),
                "merge_state_status": pr.get("mergeStateStatus"),
                "is_draft": pr.get("isDraft"),
                "checks": checks,
            }
        )

    issues = _gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "50",
            "--json",
            "number,title,createdAt,labels",
        ]
    ) or []
    open_issues = [
        {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "created_at": issue.get("createdAt"),
            "labels": [label.get("name") for label in issue.get("labels") or []],
        }
        for issue in issues
    ]

    branches = _git(["ls-remote", "--heads", "origin"])
    remote_branch_count = len(branches.strip().splitlines()) if branches else None

    ci_runs = _runs(repo, "ci.yml", 5)
    main_push_runs = [run for run in ci_runs if run.get("event") == "push"]
    main_ci_status = main_push_runs[0]["conclusion"] if main_push_runs else None
    main_head_sha = main_push_runs[0]["headSha"] if main_push_runs else None

    deploy_runs = _runs(repo, "deploy-worker.yml", 5)
    deploy_status = deploy_runs[0]["conclusion"] if deploy_runs else None

    runner_runs = _runs(repo, "task-runner.yml", 5)
    successful = [run for run in runner_runs if run.get("conclusion") == "success"]
    watchdog_last_success_at = successful[0]["updatedAt"] if successful else None

    return {
        "collected_by": "scripts/build_development_state.py",
        "repo": repo,
        "open_pull_requests": open_pull_requests,
        "open_issues": open_issues,
        "remote_branch_count": remote_branch_count,
        "signals": {
            "main_ci_status": main_ci_status,
            "main_head_sha": main_head_sha,
            "deploy_status": deploy_status,
            "watchdog_last_success_at": watchdog_last_success_at,
            "oldest_pending_age_minutes": None,
            "note": (
                "oldest_pending_age_minutes is None here: the live D1 queue is not "
                "reachable from CI without the Worker's service token, and a made-up "
                "number would be worse than a missing one."
            ),
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Collect development-cycle state (read-only)")
    parser.add_argument("--repo")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    state = collect(args.repo)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
    print(f"wrote {args.out}: {len(state['open_pull_requests'])} PRs, "
          f"{len(state['open_issues'])} issues, {state['remote_branch_count']} branches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
