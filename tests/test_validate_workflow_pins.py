"""Tests for immutable GitHub Actions dependency pin enforcement."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.validate_workflow_pins import find_unpinned_actions


class WorkflowPinValidationTests(unittest.TestCase):
    def test_flags_mutable_tags_and_branches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "ci.yml").write_text(
                "steps:\n"
                "  - uses: actions/checkout@v7\n"
                "  - uses: actions/setup-python@main\n",
                encoding="utf-8",
            )
            problems = find_unpinned_actions(root)
            self.assertEqual(len(problems), 2)
            self.assertEqual(problems[0][1], 2)
            self.assertIn("actions/checkout@v7", problems[0][2])

    def test_accepts_verified_full_sha_and_local_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow_dir = root / ".github" / "workflows"
            workflow_dir.mkdir(parents=True)
            (workflow_dir / "ci.yaml").write_text(
                "steps:\n"
                "  - uses: actions/checkout@" + "a" * 40 + " # v7\n"
                "  - uses: ./.github/actions/local-step\n"
                "  - uses: docker://alpine:3.20\n",
                encoding="utf-8",
            )
            self.assertEqual(find_unpinned_actions(root), [])

    def test_missing_workflow_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(find_unpinned_actions(Path(temp)))


if __name__ == "__main__":
    unittest.main()
