"""Regression tests for the remote Staging lane proof entry point."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_staging_lane_isolation.py"


class StagingLaneProofEntryPointTests(unittest.TestCase):
    def test_script_imports_engine_from_an_unrelated_working_directory(self):
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": "",
            "RAAWAA_ENGINE_LIVE_INFERENCE": "disabled",
        }
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=working_directory,
                env=environment,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing ephemeral researcher lane token", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
