"""Run enabled specialist consumers as bounded live-inference batches.

Dry-run completion is deliberately refused here: it would mark business work as
succeeded without doing it. The live gateway still enforces policy, quota and
zero-cost provider eligibility before a task lease is claimed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


LANES = (
    ("specialist:researcher", "RAAWAA_ENGINE_RESEARCHER_LANE_TOKEN"),
    ("specialist:reviewer", "RAAWAA_ENGINE_REVIEWER_LANE_TOKEN"),
    ("specialist:marketer", "RAAWAA_ENGINE_MARKETER_LANE_TOKEN"),
    ("specialist:operator", "RAAWAA_ENGINE_OPERATOR_LANE_TOKEN"),
    ("specialist:mobile_tester", "RAAWAA_ENGINE_MOBILE_TESTER_LANE_TOKEN"),
)


def main() -> int:
    if not os.environ.get("RAAWAA_ENGINE_API_URL") or not os.environ.get("RAAWAA_ENGINE_API_TOKEN"):
        print("Specialist runner blocked: authenticated engine API configuration is missing.")
        return 1
    base = os.environ.copy()
    if base.get("RAAWAA_ENGINE_LIVE_INFERENCE", "disabled").lower() != "enabled":
        print("Specialist runner dormant: live inference is disabled; refusing to complete tasks as dry runs.")
        return 0
    enabled = 0
    failures = 0
    for lane, token_name in LANES:
        lane_token = base.get(token_name, "")
        if len(lane_token) < 32 or lane_token == base.get("RAAWAA_ENGINE_API_TOKEN"):
            print(f"{lane}: dormant (dedicated capability not configured)")
            continue
        enabled += 1
        env = base.copy()
        env["RAAWAA_ENGINE_WORKER_LANE"] = lane
        env["RAAWAA_ENGINE_LANE_TOKEN"] = lane_token
        command = [
            sys.executable, "-m", "engine.cli", "run-batch",
            "--max-tasks", "1", "--max-seconds", "35",
        ]
        completed = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        try:
            report = json.loads(completed.stdout or "{}")
        except (TypeError, ValueError):
            report = {}
        processed = report.get("tasks_processed", "unknown")
        reason = report.get("stop_reason", "invalid_or_missing_report")
        print(f"{lane}: exit={completed.returncode}, tasks_processed={processed}, stop_reason={reason}")
        if completed.returncode != 0:
            # Emit a bounded, credential-free diagnostic; never print task payloads or env.
            diagnostic = (completed.stderr or "").strip().splitlines()
            if diagnostic:
                print(f"{lane}: {diagnostic[-1][:300]}")
            failures += 1
    if enabled == 0:
        print("No specialist lane capabilities are configured; no specialist tasks were claimed.")
    print(f"Specialist lanes enabled={enabled}, failed={failures}; live inference=enabled and policy-gated.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
