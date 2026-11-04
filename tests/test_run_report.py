"""The batch reporter exists so a run states its own outcome.

Downloading Actions logs has never worked from this project's tooling, so the
only readable channels are annotations and the step summary. These tests pin the
exact strings those channels depend on.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from engine import run_report


BATCH = {
    "tasks_processed": 2,
    "stop_reason": "queue_idle",
    "results": [
        {"id": "t-1", "status": "succeeded", "reason": "issue_comment_recorded",
         "mode": "none", "output_preview": "ignored"},
        {"id": "t-2", "status": "blocked", "reason": "ingest_cost_cap_exceeded"},
        {"id": "t-3", "status": "idle"},
    ],
}


class SummaryTests(unittest.TestCase):
    def test_counts_by_status_and_excludes_idle(self):
        summary = run_report.summarize(BATCH)
        self.assertEqual(summary["tasks_processed"], 2)
        self.assertEqual(summary["counts"], {"succeeded": 1, "blocked": 1})
        self.assertEqual(summary["stop_reason"], "queue_idle")

    def test_malformed_batch_does_not_raise(self):
        for payload in (None, {}, {"results": "nope"}, {"results": [None, 3]}):
            summary = run_report.summarize(payload)
            self.assertEqual(summary["tasks_processed"], 0)
            self.assertEqual(summary["counts"], {})


class NoticeTests(unittest.TestCase):
    def test_one_notice_per_task_plus_summary(self):
        lines = run_report.notices(BATCH)
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(line.startswith("::notice ") for line in lines))
        self.assertIn("task=t-1 status=succeeded reason=issue_comment_recorded", lines[0])
        self.assertIn("task=t-2 status=blocked reason=ingest_cost_cap_exceeded", lines[1])
        self.assertIn("processed=2", lines[-1])
        self.assertIn("statuses=blocked=1 succeeded=1", lines[-1])

    def test_empty_batch_still_reports_a_line(self):
        lines = run_report.notices({"stop_reason": "queue_idle", "results": []})
        self.assertEqual(len(lines), 1)
        self.assertIn("tasks_processed=0", lines[0])
        self.assertIn("stop_reason=queue_idle", lines[0])

    def test_detail_is_single_line_and_cannot_inject_commands(self):
        batch = {"results": [{"id": "t-1", "status": "blocked",
                              "reason": "bad\n::error title=x::y"}]}
        line = run_report.notices(batch)[0]
        self.assertNotIn("\n", line)
        self.assertNotIn("::error", line)

    def test_long_reason_is_truncated(self):
        batch = {"results": [{"id": "t-1", "status": "blocked", "reason": "x" * 500}]}
        line = run_report.notices(batch)[0]
        self.assertLess(len(line), 400)

    def test_truncates_beyond_the_notice_cap(self):
        batch = {"results": [{"id": f"t-{i}", "status": "succeeded"}
                             for i in range(run_report.MAX_NOTICE_ITEMS + 5)]}
        lines = run_report.notices(batch)
        self.assertTrue(any("truncated=5" in line for line in lines))


class MarkdownTests(unittest.TestCase):
    def test_table_lists_every_task(self):
        text = run_report.markdown(BATCH)
        self.assertIn("Tasks processed: **2**", text)
        self.assertIn("| `t-1` | succeeded |", text)
        self.assertIn("| `t-2` | blocked |", text)

    def test_pipes_in_values_cannot_break_the_table(self):
        batch = {"results": [{"id": "t|1", "status": "blocked", "reason": "a|b"}]}
        row = [line for line in run_report.markdown(batch).splitlines() if line.startswith("| `")]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].count("|"), 5)


class EmitTests(unittest.TestCase):
    def test_emit_writes_notices_and_appends_to_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            summary.write_text("header\n", encoding="utf-8")
            stream = io.StringIO()
            run_report.emit(BATCH, str(summary), stream=stream)
            self.assertIn("::notice", stream.getvalue())
            written = summary.read_text(encoding="utf-8")
            self.assertTrue(written.startswith("header\n"))
            self.assertIn("Bounded task batch", written)

    def _main(self, argv):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = run_report.main(argv)
        return code, stream.getvalue()

    def test_main_warns_when_the_batch_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main([str(Path(tmp) / "absent.json")])
        self.assertEqual(code, 0)
        self.assertIn("no batch result", out)

    def test_main_reads_a_real_batch_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.json"
            path.write_text(json.dumps(BATCH), encoding="utf-8")
            code, out = self._main([str(path)])
        self.assertEqual(code, 0)
        self.assertIn("processed=2", out)


class StderrTailTests(unittest.TestCase):
    """A crashing batch must say why it crashed, not just that it did.

    The two failed 2026-11-04 task-runner runs (41818670566, 41820241562) left
    only ``tasks_processed=0 stop_reason=unknown`` because a crash produces no
    JSON on stdout and the traceback went to a log nobody can download.
    """

    def _main(self, argv):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = run_report.main(argv)
        return code, stream.getvalue()

    def test_crash_tail_becomes_one_error_annotation(self):
        tail = 'Traceback (most recent call last):\n  File "engine/x.py", line 1\nValueError: remote said ::oops::\n'
        line = run_report.stderr_notice(tail)
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("::error title=Task batch crashed::"))
        self.assertNotIn("\n", line)
        # A raw traceback must not be able to inject a workflow command.
        self.assertNotIn("::oops", line)

    def test_blank_tail_is_silent(self):
        self.assertIsNone(run_report.stderr_notice(""))
        self.assertIsNone(run_report.stderr_notice("  \n\n "))
        self.assertEqual(run_report.stderr_markdown(""), "")

    def test_emit_adds_the_crash_to_the_stream_and_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            stream = io.StringIO()
            run_report.emit({}, str(summary), stream=stream, stderr_text="ValueError: boom")
            self.assertIn("::error title=Task batch crashed::", stream.getvalue())
            written = summary.read_text(encoding="utf-8")
            self.assertIn("#### stderr tail", written)
            self.assertIn("ValueError: boom", written)

    def test_main_reads_the_stderr_file_beside_the_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "batch.json"
            batch.write_text("", encoding="utf-8")
            tail = Path(tmp) / "batch.stderr"
            tail.write_text("KeyError: 'task_type'", encoding="utf-8")
            code, out = self._main([str(batch), str(tail)])
        self.assertEqual(code, 0)
        self.assertIn("::error", out)
        self.assertIn("KeyError", out)

    def test_missing_stderr_file_stays_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "batch.json"
            batch.write_text(json.dumps(BATCH), encoding="utf-8")
            code, out = self._main([str(batch), str(Path(tmp) / "absent.stderr")])
        self.assertEqual(code, 0)
        self.assertNotIn("::error", out)
        self.assertIn("processed=2", out)

    def test_long_tail_is_bounded(self):
        line = run_report.stderr_notice("x" * 50000)
        self.assertLessEqual(len(line), run_report.MAX_ANNOTATION_CHARS + 60)


if __name__ == "__main__":
    unittest.main()
