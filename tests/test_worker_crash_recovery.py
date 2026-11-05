"""Crash recovery proven by killing a real worker process with SIGKILL.

Everything else in this repository's runtime evidence is "the code handles the
failure it was told about". This test is different: a separate operating-system
process claims a task, starts a real child process, and is then killed with
``SIGKILL`` — no handler, no ``finally``, no atexit. Whatever survives is what
the durable store actually holds, and the question is whether the next worker
replays the external operation or fails closed.

The durable target is a real SQLite file on disk, so the state genuinely
outlives the killed process. The supervised child is *not* killed by the crash:
it is in its own session and keeps running, which is exactly the orphan an
operator or init would have to clean up. The test does that cleanup itself and
records what it had to do.
"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "supervised_worker_crash.py"
REPOSITORY_ROOT = FIXTURE.parents[2]
SIGKILL = getattr(signal, "SIGKILL", None)


def _children_of(ppid: int) -> set[int]:
    """Live pids whose parent is ``ppid``, read from /proc (Linux only)."""
    children: set[int] = set()
    if not os.path.isdir("/proc"):
        return children
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as stream:
                raw = stream.read()
        except OSError:
            continue
        try:
            tail = raw.rsplit(b")", 1)[1].split()
            state, parent = tail[0], int(tail[1])
        except (IndexError, ValueError):
            continue
        if parent == ppid and state not in (b"Z", b"X"):
            children.add(int(entry))
    return children


def _reap(pid: int, timeout: float = 5.0) -> None:
    """SIGKILL a pid we do not own the Popen handle for, then wait for it to go."""
    if SIGKILL is None:  # pragma: no cover - POSIX only
        return
    try:
        os.kill(pid, SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)


@unittest.skipUnless(SIGKILL is not None and os.path.isdir("/proc"), "POSIX/Linux only")
class WorkerCrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "engine.db"
        self.orphans: list[int] = []
        self.addCleanup(self._kill_orphans)

    def _kill_orphans(self):
        for pid in self.orphans:
            _reap(pid)

    # -- helpers ---------------------------------------------------------

    def _enqueue(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        from engine.supervised_task import SUPERVISED_TASK_TYPE
        from engine.task_store import TaskStore

        store = TaskStore(self.db_path)
        return store.enqueue(SUPERVISED_TASK_TYPE, "Crash probe",
                             {"operation": "slow_probe"}, idempotency_key="crash-probe")

    def _spawn(self, mode: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, str(FIXTURE), str(self.db_path), mode],
            cwd=str(REPOSITORY_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _rows(self, query: str, parameters: tuple = ()) -> list[dict]:
        """Read rows, treating "table not created yet" as "no rows yet"."""
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in db.execute(query, parameters)]
            except sqlite3.OperationalError:
                return []

    def _wait_for(self, predicate, timeout: float, description: str, process=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                raise AssertionError(
                    f"{description}: worker exited early with {process.returncode}: {stderr}"
                )
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {description}")

    # -- the proof -------------------------------------------------------

    def test_a_sigkilled_worker_does_not_replay_the_dispatch(self):
        task = self._enqueue()

        worker = self._spawn("run")
        self.addCleanup(lambda: self._close(worker))

        # 1. The durable state a crash leaves behind: a running task and a
        #    dispatch row with no outcome.
        def dispatched():
            rows = self._rows("SELECT status FROM process_dispatches")
            return rows if rows else None

        self._wait_for(dispatched, timeout=40, description="dispatch record", process=worker)

        # 2. The child process must genuinely exist, otherwise the kill proves
        #    nothing about an in-flight operation.
        child_pids = self._wait_for(
            lambda: _children_of(worker.pid), timeout=40,
            description="supervised child process", process=worker,
        )
        child_pid = sorted(child_pids)[0]
        self.orphans.append(child_pid)

        # 3. Kill the worker where it cannot clean up.
        os.kill(worker.pid, SIGKILL)
        worker.wait(timeout=10)
        self.assertEqual(worker.returncode, -SIGKILL)

        # 4. The supervised child survived the crash: it is in its own session,
        #    which is the containment boundary and the orphan risk at once.
        self.assertTrue(self._pid_alive(child_pid),
                        "the supervised child should outlive the killed worker")

        task_row = self._rows("SELECT * FROM tasks WHERE id=?", (task["id"],))[0]
        dispatch_row = self._rows("SELECT * FROM process_dispatches")[0]
        self.assertEqual(task_row["status"], "running")
        self.assertIsNone(task_row["result_json"])
        self.assertEqual(dispatch_row["status"], "in_flight")
        self.assertIsNone(dispatch_row["classification"])

        # 5. Clean up the orphan the way an operator would, and expire the lease
        #    the way time would.
        _reap(child_pid)
        self.orphans.remove(child_pid)
        self.assertFalse(self._pid_alive(child_pid))
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at=? WHERE id=?",
                       ("2000-01-01T00:00:00+00:00", task["id"]))

        # 6. A second, fresh worker must refuse to replay the operation.
        resumed = self._spawn("resume")
        self.addCleanup(lambda: self._close(resumed))
        stdout, stderr = resumed.communicate(timeout=60)
        self.assertEqual(resumed.returncode, 0, stderr)
        messages = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        finished = [message for message in messages if message.get("phase") == "finished"]
        self.assertTrue(finished, messages)
        summary = finished[0]

        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["classification"], "ambiguous")
        self.assertIn("in_flight", summary["reason"])
        self.assertEqual(summary["recovery"], {"requeued": 1, "failed": 0})

        # 7. The dispatch row is untouched: still in flight, still unsettled.
        dispatch_rows = self._rows("SELECT * FROM process_dispatches")
        self.assertEqual(len(dispatch_rows), 1)
        self.assertEqual(dispatch_rows[0]["status"], "in_flight")
        self.assertIsNone(dispatch_rows[0]["classification"])

        # 8. And the task is terminally blocked rather than retried forever.
        final = self._rows("SELECT * FROM tasks WHERE id=?", (task["id"],))[0]
        self.assertEqual(final["status"], "blocked")
        self.assertEqual(final["attempts"], 2)
        self.assertIsNone(final["lease_token"])

    def _close(self, process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        try:
            with open(f"/proc/{pid}/stat", "rb") as stream:
                state = stream.read().rsplit(b")", 1)[1].split()[0]
            return state not in (b"Z", b"X")
        except OSError:
            return False


if __name__ == "__main__":
    unittest.main()
