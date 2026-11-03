import tempfile
import unittest
from pathlib import Path

from scripts.check_workflow_action_pins import find_unpinned_actions


class WorkflowActionPinTests(unittest.TestCase):
    def test_accepts_full_sha_and_local_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "steps:\n"
                "  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "  - uses: ./.github/actions/local\n"
                "  - uses: $/.github/actions/reusable\n",
                encoding="utf-8",
            )
            self.assertEqual(find_unpinned_actions(root), [])

    def test_rejects_tags_branches_short_shas_and_missing_refs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "steps:\n"
                "  - uses: actions/checkout@v7\n"
                "  - uses: actions/setup-python@main\n"
                "  - uses: actions/setup-node@1234abcd\n"
                "  - uses: actions/upload-artifact\n",
                encoding="utf-8",
            )
            violations = find_unpinned_actions(root)
            self.assertEqual(len(violations), 4)


if __name__ == "__main__":
    unittest.main()
