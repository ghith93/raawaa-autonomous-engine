"""The CI workflow must compile every branch without granting production reach.

Before this test, `.github/workflows/ci.yml` triggered only on `push` to `main`
and on pull requests. An agent working branch was therefore compiled for the
first time when its PR opened, which is the latest possible moment to discover a
YAML error or an import mistake.

Widening `push` is only safe while the production-adjacent jobs stay gated on
`refs/heads/main`. That is what these tests pin: a branch push runs `test`, and
nothing that touches Staging, D1, or a deployed Worker.

The file is read as YAML where a YAML parser is available (CI installs one via
the `test` job's environment) and by text otherwise, because the repository's
Python test job does not depend on PyYAML. The text fallback asserts the same
properties in a narrower way rather than skipping silently.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"

PRODUCTION_ADJACENT_JOBS = (
    "staging-migrate",
    "staging-worker-verify",
    "publish-staging-ops-telemetry",
    "staging-mobile-status-verify",
)


def _load_yaml():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, probed deliberately
    except ModuleNotFoundError:
        return None
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


class CiBranchCoverageTests(unittest.TestCase):
    def test_push_trigger_has_no_main_only_branch_filter(self):
        """`push: branches: [main]` would leave working branches uncompiled."""
        text = CI.read_text(encoding="utf-8")
        trigger_block = text.split("permissions:", 1)[0]
        self.assertNotRegex(
            trigger_block,
            r"push:\s*\n\s+branches:\s*\[\s*main\s*\]",
            "the push trigger must not be restricted to main",
        )
        self.assertRegex(trigger_block, r"(?m)^  push:\s*$", "expected a bare push trigger")

    def test_trigger_block_is_valid_yaml(self):
        document = _load_yaml()
        if document is None:
            self.skipTest("PyYAML is not installed in this interpreter")
        # PyYAML resolves the bare `on:` key to the boolean True.
        triggers = document.get(True, document.get("on"))
        self.assertIn("push", triggers)
        self.assertIsNone(triggers["push"], "a bare `push:` compiles every branch")
        self.assertEqual(triggers["pull_request"], {"branches": ["main"]})

    def test_no_production_adjacent_job_runs_on_a_branch_push(self):
        document = _load_yaml()
        if document is None:
            self.skipTest("PyYAML is not installed in this interpreter")
        for name in PRODUCTION_ADJACENT_JOBS:
            self.assertIn(name, document["jobs"], f"{name} disappeared from CI")
            condition = document["jobs"][name].get("if")
            self.assertIsInstance(condition, str, f"{name} has no job-level gate")
            self.assertIn(
                "refs/heads/main",
                condition,
                f"{name} must stay gated on refs/heads/main",
            )

    def test_text_fallback_confirms_every_staging_job_is_main_gated(self):
        """>= the YAML check, for interpreters without PyYAML."""
        text = CI.read_text(encoding="utf-8")
        for name in PRODUCTION_ADJACENT_JOBS:
            match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z]|\Z)", text, re.S | re.M)
            self.assertIsNotNone(match, f"{name} not found in ci.yml")
            block = match.group(1)
            self.assertIn("refs/heads/main", block, f"{name} lost its main-only gate")


if __name__ == "__main__":
    unittest.main()
