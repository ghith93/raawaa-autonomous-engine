import tempfile
import unittest
from pathlib import Path

from engine.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = MemoryStore(Path(self.tmp.name) / "memory.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_and_search_with_provenance(self):
        item = self.memory.add("decision", "Use free-first routing", "operator_note", 0.95, ["models"])
        found = self.memory.search("free-first")
        self.assertEqual(found[0]["id"], item["id"])
        self.assertEqual(found[0]["source"], "operator_note")
        self.assertEqual(found[0]["confidence"], 0.95)

    def test_category_filter(self):
        self.memory.add("project", "Engine roadmap", "test")
        self.memory.add("preference", "Concise reports", "test")
        self.assertEqual(len(self.memory.search("Engine", category="project")), 1)

    def test_archive_hides_memory(self):
        item = self.memory.add("fact", "Temporary fact", "test")
        self.assertTrue(self.memory.archive(item["id"]))
        self.assertEqual(self.memory.search("Temporary"), [])

    def test_invalid_confidence_rejected(self):
        with self.assertRaises(ValueError):
            self.memory.add("fact", "x", "test", 1.5)

    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            self.memory.search(" ")


if __name__ == "__main__":
    unittest.main()
