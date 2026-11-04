"""Turn a batch result into readable run evidence.

The bounded batch prints per-task results as JSON to stdout. On GitHub Actions
that stdout is effectively unreachable from outside: downloading job logs from
this project's own tooling has failed repeatedly, which is why "did the task
succeed?" used to cost a full investigation round. Annotations are reachable
through the checks API and the step summary is reachable on the run page, so
this module renders the batch outcome into both.

No network calls, no policy decisions: it only reports what the batch returned.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_NOTICE_ITEMS = 20

INTERESTING_FIELDS = ("status", "reason", "mode", "provider", "model", "error")


def _items(batch: object) -> list[dict]:
    if not isinstance(batch, dict):
        return []
    results = batch.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def summarize(batch: object) -> dict:
    """Count the batch outcome without inventing fields the worker never returned."""
    counts: dict[str, int] = {}
    for item in _items(batch):
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    counts.pop("idle", None)
    return {
        "tasks_processed": sum(counts.values()),
        "counts": counts,
        "stop_reason": str(batch.get("stop_reason") or "unknown") if isinstance(batch, dict) else "unknown",
    }


def _describe(item: dict) -> str:
    parts = [f"task={item.get('id', 'unknown')}"]
    for field in INTERESTING_FIELDS:
        value = item.get(field)
        if value:
            text = str(value).replace("\n", " ")[:120].replace("::", ":")
            parts.append(f"{field}={text}")
    return " ".join(parts)


def notices(batch: object) -> list[str]:
    """One ``::notice`` per task, plus a summary line. Empty when nothing ran."""
    items = _items(batch)
    if not items:
        summary = summarize(batch)
        return [f"::notice title=Task batch::tasks_processed=0 stop_reason={summary['stop_reason']}"]
    lines = []
    for item in items[:MAX_NOTICE_ITEMS]:
        status = str(item.get("status") or "unknown")
        lines.append(f"::notice title=Task {status}::{_describe(item)}")
    if len(items) > MAX_NOTICE_ITEMS:
        lines.append(f"::notice title=Task batch::truncated={len(items) - MAX_NOTICE_ITEMS}")
    summary = summarize(batch)
    tally = " ".join(f"{key}={value}" for key, value in sorted(summary["counts"].items())) or "none"
    lines.append(
        f"::notice title=Task batch summary::processed={summary['tasks_processed']} "
        f"stop_reason={summary['stop_reason']} statuses={tally}"
    )
    return lines


def markdown(batch: object) -> str:
    summary = summarize(batch)
    lines = [
        "### Bounded task batch",
        "",
        f"- Tasks processed: **{summary['tasks_processed']}**",
        f"- Stop reason: `{summary['stop_reason']}`",
    ]
    tally = ", ".join(f"`{key}`={value}" for key, value in sorted(summary["counts"].items()))
    lines.append(f"- Statuses: {tally or 'none'}")
    items = _items(batch)
    if items:
        lines += ["", "| task | status | reason | mode |", "|---|---|---|---|"]
        for item in items[:MAX_NOTICE_ITEMS]:
            lines.append(
                "| `{id}` | {status} | {reason} | {mode} |".format(
                    id=str(item.get("id", "unknown")).replace("|", "/"),
                    status=str(item.get("status", "unknown")).replace("|", "/"),
                    reason=str(item.get("reason", "")).replace("|", "/") or "-",
                    mode=str(item.get("mode", "")).replace("|", "/") or "-",
                )
            )
    return "\n".join(lines) + "\n"


def emit(batch: object, summary_path: str | None = None, stream=None) -> list[str]:
    """Print notices and append the markdown table to the step summary if asked."""
    stream = stream or sys.stdout
    lines = notices(batch)
    for line in lines:
        print(line, file=stream)
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(markdown(batch) + "\n")
        except OSError:
            print("::warning title=Task batch::step summary unavailable", file=stream)
    return lines


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else None
    raw = ""
    if path is not None and path.exists():
        raw = path.read_text(encoding="utf-8")
    elif path is not None:
        print(f"::warning title=Task batch::no batch result at {path}", file=sys.stdout)
    try:
        batch = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("::warning title=Task batch::batch result was not valid JSON", file=sys.stdout)
        batch = {}
    emit(batch, os.environ.get("GITHUB_STEP_SUMMARY"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
