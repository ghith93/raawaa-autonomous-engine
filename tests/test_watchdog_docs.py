"""Policy tests for the Task Runner watchdog documentation.

These exist because the same overstatement reached a published report once
already: the page opened with a "Purpose" section describing a five-minute
dispatch, and a reader (this agent included) cited it as if the dispatch were
running. The code has always been honest - it returns `disabled` without the
activation variable - so the fix belongs in the document's shape, not in a
promise to read more carefully.

The tests pin three things:
* current status appears before the mechanism, so it cannot be skipped;
* the page names the disabled state and the missing variable explicitly;
* the activation variable is declared in committed Wrangler configuration, so it
  cannot silently drift from the code that reads it (it did exactly that once,
  and the only symptom was a fallback that never dispatched).
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "operations" / "github-app-task-runner-watchdog.md"
WORKER = ROOT / "worker" / "task-runner-watchdog.js"


class WatchdogDocShapeTests(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def test_current_status_precedes_mechanism(self):
        status_at = self.text.find("## Current status")
        purpose_at = self.text.find("## Purpose")
        self.assertNotEqual(status_at, -1, "missing 'Current status' section")
        self.assertNotEqual(purpose_at, -1, "missing 'Purpose' section")
        self.assertLess(status_at, purpose_at, "current status must be readable before the mechanism")

    def test_documented_state_matches_code_disabled_default(self):
        worker = WORKER.read_text(encoding="utf-8")
        self.assertIn('env?.GITHUB_APP_WATCHDOG_ENABLED !== "enabled"', worker)
        self.assertIn('reason: "missing_configuration"', worker)
        self.assertIn("active and verified", self.text.lower())
        self.assertIn("missing_configuration", self.text)

    def test_page_states_activation_variable_is_required_and_version_controlled(self):
        self.assertIn("GITHUB_APP_WATCHDOG_ENABLED", self.text)
        self.assertIn("declared in `wrangler.toml`", self.text)

    def test_page_separates_fifteen_minute_recovery_from_dispatch(self):
        self.assertIn("D1 tasks only", self.text)
        self.assertIn("does not ingest GitHub comments", self.text)


class WatchdogConfigTests(unittest.TestCase):
    # Configuration pinning moved to tests/test_deployment_config.py, which reads
    # the TOML as TOML instead of matching substrings.

    def test_five_minute_cron_expression_is_declared(self):
        text = (ROOT / "wrangler.toml").read_text(encoding="utf-8")
        self.assertIn("1,6,11,16,21,26,31,36,41,46,51,56 * * * *", text)
        self.assertIn("*/15 * * * *", text)


if __name__ == "__main__":
    unittest.main()
