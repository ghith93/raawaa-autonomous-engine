"""Offline regression checks for the task runner's zero-cost boundary."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TaskRunnerZeroCostGateTests(unittest.TestCase):
    def test_all_remote_control_worker_steps_require_successful_preflight(self):
        text = (ROOT / ".github/workflows/task-runner.yml").read_text(encoding="utf-8")
        self.assertIn("  schedule:", text)
        self.assertIn("  workflow_dispatch:", text)  # watchdog fallback
        self.assertIn("id: task_zero_cost_preflight", text)
        self.assertIn("python scripts/verify_cloudflare_zero_cost_boundary.py", text)
        for name in ("Diagnose public Worker health path", "Verify authenticated POST path before task operations",
                     "Read D1-backed mobile status (read-only)", "Process bounded D1 task batch"):
            with self.subTest(name=name):
                start = text.index("      - name: " + name)
                end = text.find("\n      - name:", start + 8)
                block = text[start:end if end != -1 else len(text)]
                condition = next(line for line in block.splitlines() if line.strip().startswith("if:"))
                self.assertIn("steps.task_zero_cost_preflight.outcome == 'success'", condition)
                self.assertIn("steps.task_zero_cost_preflight.outputs.zero_cost_eligible == 'true'", condition)
                self.assertNotIn("||", condition)
        report = text.split("      - name: Publish per-task outcomes", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("steps.batch.outcome != 'skipped'", report)
        self.assertIn("Billing preflight:", text)


if __name__ == "__main__":
    unittest.main()
