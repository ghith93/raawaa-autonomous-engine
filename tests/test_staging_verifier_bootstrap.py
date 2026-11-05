import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_staging_monetary_ledger.py"


class StagingVerifierBootstrapTests(unittest.TestCase):
    def test_direct_script_invocation_imports_engine_before_failing_closed_for_missing_token(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for name in (
            "RAAWAA_ENGINE_STAGING_API_TOKEN",
            "RAAWAA_ENGINE_STAGING_QUOTA_ADMIN_TOKEN",
            "RAAWAA_ENGINE_STAGING_URL",
            "CLOUDFLARE_API_TOKEN",
            "CLOUDFLARE_ACCOUNT_ID",
        ):
            env.pop(name, None)

        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("RAAWAA_ENGINE_STAGING_API_TOKEN is required", result.stdout)
        self.assertNotIn("ModuleNotFoundError", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
