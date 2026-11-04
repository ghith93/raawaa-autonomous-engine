"""Pin the switches that decide whether a safety net is armed.

`GITHUB_APP_WATCHDOG_ENABLED` was dashboard-managed once. It went missing, the
deploy still uploaded, the post-deploy gate still ran, and the only symptom was a
fallback scheduler that silently never dispatched. This test makes that specific
drift impossible: the production Worker must carry the opt-in from version
control, and staging must not, because a staging Worker that dispatches
production workflows is not a staging anything.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG_FLAG = "GITHUB_APP_WATCHDOG_ENABLED"


def _config(name: str) -> dict:
    with open(ROOT / name, "rb") as handle:
        return tomllib.load(handle)


class ProductionWorkerConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = _config("wrangler.toml")

    def test_watchdog_opt_in_is_version_controlled(self):
        self.assertEqual(self.config["vars"].get(WATCHDOG_FLAG), "enabled")

    def test_the_exact_string_matters(self):
        # The Worker compares against the literal "enabled"; "true", "Enabled" or
        # a padded value all read as missing configuration.
        value = self.config["vars"].get(WATCHDOG_FLAG)
        self.assertIsInstance(value, str)
        self.assertEqual(value, value.strip())
        self.assertEqual(value.lower(), "enabled")

    def test_worker_entry_and_bindings_survive(self):
        self.assertEqual(self.config["main"], "worker/index.js")
        bindings = {item["binding"] for item in self.config["d1_databases"]}
        self.assertIn("DB", bindings)

    def test_both_cron_triggers_are_declared(self):
        crons = self.config["triggers"]["crons"]
        self.assertIn("*/15 * * * *", crons)
        dispatch = [cron for cron in crons if cron.startswith("1,6,11")]
        self.assertEqual(len(dispatch), 1, "the fallback dispatcher needs its own cron")


class StagingWorkerConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = _config("wrangler.staging.toml")

    def test_staging_does_not_arm_the_dispatch_watchdog(self):
        self.assertNotIn(WATCHDOG_FLAG, self.config.get("vars", {}))

    def test_staging_is_a_separate_worker(self):
        self.assertNotEqual(self.config["name"], _config("wrangler.toml")["name"])


if __name__ == "__main__":
    unittest.main()
