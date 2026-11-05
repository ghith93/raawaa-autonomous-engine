"""Prevent orphaned imports and scheduled jobs after owner cancellation."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RetiredProjectTests(unittest.TestCase):
    def test_project_and_its_import_facade_are_absent(self):
        self.assertFalse((ROOT / 'projects/telegram-arbitrage-engine').exists())
        self.assertFalse((ROOT / 'engine/d1_paper_ledger.py').exists())

    def test_no_workflow_or_script_references_retired_package(self):
        for folder, suffix in (('.github/workflows', '*.yml'), ('scripts', '*.py')):
            for path in (ROOT / folder).glob(suffix):
                text = path.read_text()
                self.assertNotIn('projects/telegram-arbitrage-engine', text, str(path))
                self.assertNotIn('arb_engine', text, str(path))
