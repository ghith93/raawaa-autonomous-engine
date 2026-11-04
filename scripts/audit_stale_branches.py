"""Classify remote branches so the branch list can work as a signal again.

465 remote branches on a repository whose entire history is a few days old means
the branch list has stopped being information: nobody can read it, so nobody
checks it, so abandoned work and live work look identical. This script sorts
them into four buckets, and only one of them is safe to delete automatically.

The rule for deletion is deliberately narrow:

    a remote branch is deletable only when
      (a) its head commit is the exact head of a *merged* pull request,
      (b) the PR was merged into main, and
      (c) the branch's current tip still equals that PR head — so a branch
          reused after its merge is never touched.

Everything else is reported and left alone. Unmerged work is never deleted,
because "nobody opened a PR for it" is not evidence that it is worthless.

Usage:
    python scripts/audit_stale_branches.py                # report only
    python scripts/audit_stale_branches.py --apply        # delete bucket 1
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def _gh(args: list[str]) -> Any:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {result.stderr.strip()[:200]}")
    return json.loads(result.stdout) if result.stdout.strip() else []


def _remote_branches() -> dict[str, str]:
    out = subprocess.run(
        ["git", "ls-remote", "--heads", "origin"], capture_output=True, text=True, timeout=120, check=False
    )
    if out.returncode != 0:
        raise SystemExit("git ls-remote failed")
    branches: dict[str, str] = {}
    for line in out.stdout.strip().splitlines():
        sha, ref = line.split("\t", 1)
        branches[ref[len("refs/heads/"):]] = sha
    return branches


def classify(repo: str, *, protect: set[str]) -> dict[str, list[dict[str, str]]]:
    branches = _remote_branches()
    merged = _gh([
        "pr", "list", "--repo", repo, "--state", "merged", "--limit", "500",
        "--json", "number,headRefName,headRefOid,baseRefName",
    ])
    open_prs = _gh([
        "pr", "list", "--repo", repo, "--state", "open", "--limit", "200", "--json", "number,headRefName",
    ])

    open_heads = {pr["headRefName"] for pr in open_prs}
    merged_by_branch: dict[str, dict[str, Any]] = {}
    for pr in merged:
        if pr.get("baseRefName") != "main":
            continue
        merged_by_branch[pr["headRefName"]] = pr

    deletable: list[dict[str, str]] = []
    active: list[dict[str, str]] = []
    diverged: list[dict[str, str]] = []
    unknown: list[dict[str, str]] = []

    for name, sha in sorted(branches.items()):
        if name in protect or name in open_heads:
            active.append({"branch": name, "reason": "protected or has an open pull request"})
            continue
        pr = merged_by_branch.get(name)
        if pr is None:
            unknown.append({"branch": name, "tip": sha[:12], "reason": "no merged pull request on main"})
            continue
        if sha != pr["headRefOid"]:
            diverged.append({
                "branch": name,
                "tip": sha[:12],
                "reason": f"advanced past merged PR #{pr['number']} head {pr['headRefOid'][:12]}",
            })
            continue
        deletable.append({"branch": name, "pr": f"#{pr['number']}", "tip": sha[:12]})

    return {"deletable": deletable, "active": active, "diverged": diverged, "unknown": unknown}


def apply_deletions(repo: str, deletable: list[dict[str, str]]) -> int:
    deleted = 0
    for item in deletable:
        name = item["branch"]
        result = subprocess.run(
            ["gh", "api", "-X", "DELETE", f"repos/{repo}/git/refs/heads/{name}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode == 0:
            deleted += 1
        else:
            print(f"  failed {name}: {result.stderr.strip()[:120]}")
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify (and optionally prune) remote branches")
    parser.add_argument("--repo", default="ghith93/raawaa-autonomous-engine")
    parser.add_argument("--apply", action="store_true", help="delete branches whose tip is a merged PR head")
    parser.add_argument("--manifest", help="write the classified branches (with SHAs) to this path before deleting")
    args = parser.parse_args()

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=30, check=False
    ).stdout.strip()
    protect = {"main", current} if current else {"main"}
    buckets = classify(args.repo, protect=protect)
    for name in ("deletable", "active", "diverged", "unknown"):
        print(f"{name:10} {len(buckets[name])}")

    print("\nDeletable (tip is exactly a merged PR head):")
    for item in buckets["deletable"][:15]:
        print(f"  {item['branch']}  ({item['pr']})")
    if len(buckets["deletable"]) > 15:
        print(f"  ... and {len(buckets['deletable']) - 15} more")

    print("\nDiverged (merged PR exists, but the branch moved after it — kept):")
    for item in buckets["diverged"][:10]:
        print(f"  {item['branch']}  {item['reason']}")

    if args.manifest:
        # Record the exact SHAs before anything is removed: a deleted branch is
        # recoverable from a SHA, and unrecoverable from a memory.
        Path(args.manifest).write_text(
            json.dumps({"repo": args.repo, "buckets": buckets}, indent=2), encoding="utf-8"
        )
        print(f"\nWrote manifest to {args.manifest}")

    if args.apply:
        deleted = apply_deletions(args.repo, buckets["deletable"])
        print(f"\nDeleted {deleted}/{len(buckets['deletable'])} branches.")
    else:
        print("\nReport only. Re-run with --apply to delete the deletable bucket.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
