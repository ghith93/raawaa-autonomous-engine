"""Regression coverage for standalone Staging lane proof invocation."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StagingLaneIsolationScriptTests(unittest.TestCase):
    def test_active_task_summary_omits_sensitive_content_and_is_bounded(self):
        from scripts.run_staging_lane_isolation import _active_task_summary

        rows = [{
            "id": f"task-{index}",
            "task_type": "research_brief",
            "title": "Potentially sensitive title",
            "status": "pending",
            "execution_lane": "general",
            "idempotency_key": "private-key",
            "payload_json": "{private payload}",
        } for index in range(12)]
        summary = _active_task_summary(rows)
        self.assertEqual(len(summary), 10)
        for field in ("title", "idempotency_key", "payload_json"):
            self.assertNotIn(field, summary[0])
        self.assertEqual(summary[0]["id"], "task-0")

    def test_running_tasks_are_reported_but_do_not_block_pending_lane_claims(self):
        from scripts.run_staging_lane_isolation import _queue_state

        pending, running = _queue_state([
            {"id": "running-1", "status": "running"},
            {"id": "pending-1", "status": "pending"},
            {"id": "done-1", "status": "succeeded"},
        ])
        self.assertEqual([task["id"] for task in pending], ["pending-1"])
        self.assertEqual([task["id"] for task in running], ["running-1"])

    def test_running_only_queue_is_safe_to_claim_new_proof_tasks(self):
        from scripts.run_staging_lane_isolation import _queue_state

        pending, running = _queue_state([
            {"id": "running-1", "status": "running"},
        ])
        self.assertEqual(pending, [])
        self.assertEqual([task["id"] for task in running], ["running-1"])

    def test_script_imports_repository_modules_when_run_outside_repo(self):
        script = ROOT / "scripts" / "run_staging_lane_isolation.py"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "RAAWAA_ENGINE_LIVE_INFERENCE": "disabled",
        }
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd="/tmp",
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing ephemeral researcher lane token", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
