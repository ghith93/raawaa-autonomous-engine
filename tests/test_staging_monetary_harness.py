import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import verify_staging_monetary_ledger as harness


class StagingMonetaryHarnessTests(unittest.TestCase):
    def test_default_policy_uses_integer_nano_usd(self):
        self.assertEqual(
            harness.default_policy_nanos(),
            (400_000_000, 12_000_000_000, 200_000_000),
        )

    def test_staging_target_matches_isolated_database_config(self):
        self.assertIsNone(harness.verify_staging_target())

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_policy_update_uses_remote_staging_database_only(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(harness, "verify_staging_target") as verify:
            harness.set_staging_policy(100, 200, 50)
        verify.assert_called_once_with()
        command = run.call_args.args[0]
        self.assertEqual(command[0:5], [
            "npx", "--yes", "wrangler@4.156.1", "d1", "execute"
        ])
        self.assertIn("raawaa-engine-staging", command)
        self.assertIn("--remote", command)
        config_index = command.index("--config")
        self.assertEqual(command[config_index + 1], "wrangler.staging.toml")
        sql = command[command.index("--command") + 1]
        self.assertIn("daily_budget_nanos=100", sql)
        self.assertIn("monthly_budget_nanos=200", sql)
        self.assertIn("per_task_budget_nanos=50", sql)

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_invalid_policy_values_are_rejected_before_remote_write(self, run):
        with self.assertRaisesRegex(RuntimeError, "invalid Staging monetary policy"):
            harness.set_staging_policy(True, 200, 50)
        run.assert_not_called()

    @patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "test-cloudflare-token",
        "CLOUDFLARE_ACCOUNT_ID": "test-account",
    })
    @patch("scripts.verify_staging_monetary_ledger.subprocess.run")
    def test_wrong_target_stops_before_remote_write(self, run):
        with patch.object(harness, "verify_staging_target", side_effect=RuntimeError("wrong D1 target")):
            with self.assertRaisesRegex(RuntimeError, "wrong D1 target"):
                harness.set_staging_policy(100, 200, 50)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
