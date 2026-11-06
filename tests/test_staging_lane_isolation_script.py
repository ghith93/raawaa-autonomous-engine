"""Regression coverage for standalone Staging lane proof invocation."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StagingLaneIsolationScriptTests(unittest.TestCase):
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
