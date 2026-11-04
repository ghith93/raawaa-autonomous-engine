"""A deploy must not outrun the schema it depends on.

On 2026-11-04 the Worker shipped claim SQL that reads task_dependencies before
migration 0010 was applied, and the queue failed closed with a 503 nobody could
explain for an hour. These tests pin the comparison that would have stopped the
deploy.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from engine import migrations


def repo_migrations() -> list[str]:
    return migrations.available_migrations(Path(__file__).resolve().parent.parent / "migrations")


class PendingMigrationTests(unittest.TestCase):
    def test_every_reviewed_migration_is_detected_in_the_repository(self):
        names = repo_migrations()
        self.assertIn("0010_task_priority_dependencies", names)
        self.assertEqual(names, sorted(names))

    def test_wrangler_json_with_results_is_read(self):
        payload = [{"results": [{"name": "0001_initial"}, {"name": "0002_research_candidates.sql"}]}]
        self.assertEqual(
            migrations.applied_migrations(payload),
            {"0001_initial", "0002_research_candidates"},
        )

    def test_a_missing_migration_is_pending(self):
        applied = [{"name": name} for name in repo_migrations()
                   if name != "0010_task_priority_dependencies"]
        outstanding = migrations.pending(
            Path(__file__).resolve().parent.parent / "migrations",
            [{"results": applied}],
        )
        self.assertEqual(outstanding, ["0010_task_priority_dependencies"])

    def test_an_empty_database_pends_everything(self):
        outstanding = migrations.pending(
            Path(__file__).resolve().parent.parent / "migrations", []
        )
        self.assertEqual(outstanding, repo_migrations())

    def test_sql_suffixes_and_whitespace_do_not_create_false_positives(self):
        payload = [{"results": [{"name": " 0010_task_priority_dependencies.sql "}]}]
        self.assertEqual(
            migrations.applied_migrations(payload),
            {"0010_task_priority_dependencies"},
        )


class MigrationCommandTests(unittest.TestCase):
    def _run(self, argv):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = migrations.main(argv)
        return code, stream.getvalue()

    def test_command_fails_when_a_migration_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "0001_initial.sql").write_text("-- a", encoding="utf-8")
            (Path(tmp) / "0002_next.sql").write_text("-- b", encoding="utf-8")
            applied = Path(tmp) / "applied.json"
            applied.write_text(json.dumps([{"results": [{"name": "0001_initial"}]}]), encoding="utf-8")
            code, out = self._run([tmp, str(applied)])
        self.assertEqual(code, 1)
        self.assertIn("0002_next", out)
        self.assertIn("::error", out)

    def test_command_passes_when_everything_is_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "0001_initial.sql").write_text("-- a", encoding="utf-8")
            applied = Path(tmp) / "applied.json"
            applied.write_text(json.dumps([{"results": [{"name": "0001_initial.sql"}]}]), encoding="utf-8")
            code, out = self._run([tmp, str(applied)])
        self.assertEqual(code, 0)
        self.assertIn("are applied", out)

    def test_unreadable_applied_list_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "0001_initial.sql").write_text("-- a", encoding="utf-8")
            broken = Path(tmp) / "applied.json"
            broken.write_text("{not json", encoding="utf-8")
            code, out = self._run([tmp, str(broken)])
        self.assertEqual(code, 1)
        self.assertIn("unreadable", out)

    def test_missing_applied_list_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "0001_initial.sql").write_text("-- a", encoding="utf-8")
            code, out = self._run([tmp, str(Path(tmp) / "absent.json")])
        self.assertEqual(code, 1)
        self.assertIn("unreadable", out)


if __name__ == "__main__":
    unittest.main()
