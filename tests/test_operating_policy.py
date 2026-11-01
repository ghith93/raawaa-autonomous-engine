import tempfile
import unittest
from pathlib import Path

from engine.operating_policy import (
    OperatingPolicyError,
    REQUIRED_POLICY_FILES,
    load_operating_policy,
)


class OperatingPolicyTests(unittest.TestCase):
    def test_loads_every_required_document_and_fingerprints_them(self):
        policy = load_operating_policy()
        self.assertEqual(policy["documents_loaded"], list(REQUIRED_POLICY_FILES))
        self.assertEqual(set(policy["fingerprints"]), set(REQUIRED_POLICY_FILES))
        self.assertIn("Permanent master autonomous execution directive", policy["prompt_context"])
        self.assertIn("sha256:", policy["prompt_context"])
        self.assertGreater(policy["prompt_context_bytes"], 1000)
        self.assertLess(policy["prompt_context_bytes"], 4000)

    def test_missing_document_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in REQUIRED_POLICY_FILES[:-1]:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("placeholder policy", encoding="utf-8")
            with self.assertRaises(OperatingPolicyError):
                load_operating_policy(root)

    def test_empty_document_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in REQUIRED_POLICY_FILES:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("", encoding="utf-8")
            with self.assertRaises(OperatingPolicyError):
                load_operating_policy(root)


if __name__ == "__main__":
    unittest.main()
