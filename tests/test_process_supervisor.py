"""Failure-injection tests for the bounded process supervisor.

Every test here starts a real process; none of them mocks a process. The whole
value of the module is what happens to a real child at a real deadline, so a
mocked ``Popen`` would test the test. The operations below are fixtures owned by
this file, which is exactly the trust model: registries are written by trusted
code, never supplied by a task payload.
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import process_supervisor as supervisor_module
from engine.process_supervisor import (
    CEILINGS,
    DispatchLedgerError,
    ArgSlot,
    LeaseFenceLost,
    LeaseWatchdog,
    OperationRegistry,
    OperationSpec,
    ProcessSupervisor,
    SqliteDispatchLedger,
    SupervisorConfigurationError,
    SupervisorRefusal,
    build_child_environment,
    default_registry,
    make_lease_fence,
)

PYTHON = os.path.realpath(sys.executable)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _test_registry(**overrides) -> OperationRegistry:
    """Fixture operations: fixed programs, fixed code, no parameters in code slots."""
    specs = [
        OperationSpec(
            name="env_dump",
            executable=PYTHON,
            arguments=(
                "-c",
                "import json,os;print(json.dumps(dict(os.environ),sort_keys=True));"
                "print('CWD '+os.getcwd())",
            ),
            output_bytes=32768,
            wall_seconds=30.0,
        ),
        OperationSpec(
            name="print_pid",
            executable=PYTHON,
            arguments=("-c", "import os;print(os.getpid())"),
            output_bytes=1024,
        ),
        OperationSpec(
            name="sleep_long",
            executable=PYTHON,
            arguments=("-c", "import time;time.sleep(30)"),
            wall_seconds=0.5,
            grace_seconds=0.4,
        ),
        OperationSpec(
            name="spawn_grandchild",
            executable=PYTHON,
            arguments=(
                "-c",
                "import subprocess,sys,time;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                "print(child.pid,flush=True);time.sleep(60)",
            ),
            wall_seconds=0.6,
            grace_seconds=0.4,
        ),
        OperationSpec(
            name="orphan_child",
            executable=PYTHON,
            arguments=(
                "-c",
                "import subprocess,sys;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                "print(child.pid,flush=True)",
            ),
            wall_seconds=10.0,
            grace_seconds=0.4,
        ),
        OperationSpec(
            name="noisy",
            executable=PYTHON,
            arguments=(
                "-c",
                "import sys;sys.stdout.write(('line of text'+chr(10))*100000);sys.stdout.flush()",
            ),
            output_bytes=2048,
            wall_seconds=15.0,
            grace_seconds=0.4,
        ),
        OperationSpec(
            name="leaks_a_token",
            executable=PYTHON,
            arguments=(
                "-c",
                "print('Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz012345');"
                "print('ENGINE_API_TOKEN=shhh-this-should-never-be-published')",
            ),
            output_bytes=4096,
        ),
        OperationSpec(
            name="exiting_three",
            executable=PYTHON,
            arguments=("-c", "import sys;sys.exit(3)"),
            output_bytes=1024,
        ),
        OperationSpec(
            name="echo_slot",
            executable=PYTHON,
            arguments=("-c", "import sys;print(sys.argv[1])", ArgSlot("value", r"[a-z0-9-]{1,16}")),
            output_bytes=1024,
        ),
    ]
    specs.extend(overrides.values())
    return OperationRegistry(specs)


class FakeLeaseStore:
    """A task store whose heartbeat succeeds ``fail_after`` times, then fails."""

    def __init__(self, fail_after: int = 10**6):
        self.calls = 0
        self.fail_after = fail_after

    def heartbeat(self, task_id, lease_token, lease_timeout_seconds=900):
        self.calls += 1
        if self.calls > self.fail_after:
            raise ValueError("lease lost or expired")
        return "2099-01-01T00:00:00+00:00"


def _pid_is_alive(pid: int, timeout: float = 5.0) -> bool:
    """Poll until ``pid`` disappears; zombies count as gone."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # pragma: no cover - not our process
            return True
        try:
            with open(f"/proc/{pid}/stat", "rb") as stream:
                state = stream.read().rsplit(b")", 1)[1].split()[0]
            if state in (b"Z", b"X"):
                return False
        except OSError:
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.02)


class SupervisorHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "engine.db"
        self.ledger = SqliteDispatchLedger(self.db_path)
        self.registry = _test_registry()
        self.supervisor = ProcessSupervisor(self.registry, self.ledger)

    def ledger_rows(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM process_dispatches")]


class RefusalTests(SupervisorHarness):
    def test_unknown_operation_is_refused_without_a_ledger_row(self):
        outcome = self.supervisor.run_operation("rm_rf_everything", task_id="t-1")
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("not allowlisted", outcome.reason)
        self.assertFalse(outcome.started)
        self.assertEqual(self.ledger_rows(), [])

    def test_a_parameter_cannot_choose_the_executable(self):
        with self.assertRaises(SupervisorRefusal) as caught:
            self.registry.get("print_pid").build_argv({"executable": "/bin/sh"})
        self.assertIn("no parameter", str(caught.exception))
        outcome = self.supervisor.run_operation(
            "print_pid", {"executable": "/bin/sh", "command": "id"}, task_id="t-2"
        )
        self.assertEqual(outcome.classification, "refused")
        self.assertEqual(self.ledger_rows(), [])

    def test_parameter_outside_the_declared_schema_is_refused(self):
        outcome = self.supervisor.run_operation(
            "echo_slot", {"value": "ok; rm -rf /"}, task_id="t-3"
        )
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("does not match the declared schema", outcome.reason)

    def test_missing_required_parameter_is_refused(self):
        outcome = self.supervisor.run_operation("echo_slot", task_id="t-4")
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("requires parameter", outcome.reason)

    def test_declared_slot_still_works(self):
        outcome = self.supervisor.run_operation("echo_slot", {"value": "abc-123"}, task_id="t-5")
        self.assertEqual(outcome.classification, "succeeded")
        self.assertEqual(outcome.stdout.strip(), "abc-123")

    def test_wall_clock_above_the_ceiling_is_refused(self):
        outcome = self.supervisor.run_operation(
            "print_pid", task_id="t-6", wall_seconds=CEILINGS["wall_seconds"] + 1
        )
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("wall_seconds", outcome.reason)

    def test_ledger_failure_blocks_execution(self):
        class BrokenLedger:
            def begin(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("database is locked")

        supervisor = ProcessSupervisor(self.registry, BrokenLedger())
        outcome = supervisor.run_operation("print_pid", task_id="t-7")
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("dispatch_ledger_unavailable", outcome.reason)


class RegistryConfigurationTests(unittest.TestCase):
    def test_a_shell_can_never_be_an_allowlisted_executable(self):
        shell = "/bin/sh" if os.path.exists("/bin/sh") else "/bin/bash"
        with self.assertRaises(SupervisorConfigurationError) as caught:
            OperationSpec(name="shell", executable=shell, arguments=("-c", "id")).validated()
        self.assertIn("refusing a shell", str(caught.exception))

    def test_a_parameter_may_never_occupy_a_code_flag_slot(self):
        with self.assertRaises(SupervisorConfigurationError) as caught:
            OperationSpec(
                name="code_slot",
                executable=PYTHON,
                arguments=("-c", ArgSlot("script", r".+")),
            ).validated()
        self.assertIn("interpreted code", str(caught.exception))

    def test_relative_executable_is_refused(self):
        with self.assertRaises(SupervisorConfigurationError):
            OperationSpec(name="relative", executable="git").validated()

    def test_missing_executable_is_refused(self):
        with self.assertRaises(SupervisorConfigurationError):
            OperationSpec(name="missing", executable="/nonexistent/binary").validated()

    def test_duplicate_operation_names_are_refused(self):
        spec = OperationSpec(name="twice", executable=PYTHON, arguments=("-c", "print(1)")).validated()
        with self.assertRaises(SupervisorConfigurationError):
            OperationRegistry([spec, spec])

    def test_empty_slot_pattern_is_refused(self):
        with self.assertRaises(SupervisorConfigurationError):
            ArgSlot("value", r".*").validated()

    def test_default_registry_is_absolute_and_shell_free(self):
        registry = default_registry(REPOSITORY_ROOT)
        self.assertTrue(registry.names())
        for entry in registry.describe():
            self.assertTrue(entry["executable"].startswith("/"))
            self.assertNotIn(os.path.basename(entry["executable"]), {"sh", "bash", "zsh"})
            self.assertTrue(entry["purpose"])


class TimeoutAndTreeTests(SupervisorHarness):
    def test_hard_timeout_kills_the_process(self):
        started = time.monotonic()
        outcome = self.supervisor.run_operation("sleep_long", task_id="t-timeout")
        elapsed = time.monotonic() - started
        self.assertEqual(outcome.classification, "timed_out")
        self.assertIsNotNone(outcome.signal_number)
        self.assertLess(elapsed, 5.0)
        self.assertEqual(self.ledger_rows()[0]["classification"], "timed_out")

    def test_descendant_tree_is_killed_when_the_deadline_expires(self):
        outcome = self.supervisor.run_operation("spawn_grandchild", task_id="t-descendants")
        self.assertEqual(outcome.classification, "timed_out")
        grandchild = int(outcome.stdout.strip().splitlines()[0])
        self.assertFalse(_pid_is_alive(grandchild), "grandchild survived the group kill")
        self.assertGreaterEqual(outcome.descendants_killed, 1)

    def test_orphaned_descendant_is_killed_after_the_parent_exits(self):
        started = time.monotonic()
        outcome = self.supervisor.run_operation("orphan_child", task_id="t-orphan")
        elapsed = time.monotonic() - started
        self.assertEqual(outcome.classification, "succeeded")
        orphan = int(outcome.stdout.strip().splitlines()[0])
        self.assertFalse(_pid_is_alive(orphan), "orphan kept the output pipes open")
        self.assertGreaterEqual(outcome.descendants_killed, 1)
        self.assertLess(elapsed, 5.0)

    def test_nonzero_exit_is_classified_as_failed(self):
        outcome = self.supervisor.run_operation("exiting_three", task_id="t-exit")
        self.assertEqual(outcome.classification, "failed")
        self.assertEqual(outcome.exit_code, 3)


class OutputTests(SupervisorHarness):
    def test_output_is_bounded_and_flagged(self):
        started = time.monotonic()
        outcome = self.supervisor.run_operation("noisy", task_id="t-noisy")
        self.assertTrue(outcome.output_truncated)
        self.assertLessEqual(len(outcome.stdout), 2048 + len("\n[output truncated]"))
        self.assertLess(time.monotonic() - started, 5.0)

    def test_output_is_redacted_before_it_can_be_recorded(self):
        outcome = self.supervisor.run_operation("leaks_a_token", task_id="t-redact")
        self.assertNotIn("shhh-this-should-never-be-published", outcome.stdout)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz012345", outcome.stdout)
        self.assertIn("[redacted]", outcome.stdout)
        payload = outcome.as_dict()
        self.assertNotIn("shhh-this-should-never-be-published", payload["stdout"])
        self.assertNotIn("shhh-this-should-never-be-published", payload["stderr"])

    def test_outcome_carries_no_environment_field(self):
        payload = self.supervisor.run_operation("print_pid", task_id="t-serialise").as_dict()
        self.assertNotIn("environment", payload)
        self.assertNotIn("env", payload)
        self.assertNotIn("lease_token", payload)
        self.assertEqual(payload["supervisor_version"], supervisor_module.SUPERVISOR_VERSION)
        # The only argv a caller can ever see is the registry-authored vector.
        self.assertEqual(payload["argv"][0], PYTHON)


class EnvironmentTests(SupervisorHarness):
    def test_child_inherits_no_secret_bearing_variable(self):
        secret = "sentinel-value-abc123"
        token = "ghp_abcdefghijklmnopqrstuvwxyz0123"
        with mock.patch.dict(
            os.environ, {"ENGINE_API_TOKEN": secret, "GITHUB_TOKEN": token}, clear=False
        ):
            outcome = self.supervisor.run_operation("env_dump", task_id="t-env")
        self.assertEqual(outcome.classification, "succeeded")
        self.assertNotIn(secret, outcome.stdout)
        self.assertNotIn(token, outcome.stdout)
        child_environment = json.loads(outcome.stdout.strip().splitlines()[0])
        self.assertNotIn("ENGINE_API_TOKEN", child_environment)
        self.assertNotIn("GITHUB_TOKEN", child_environment)
        self.assertNotIn("PYTHONPATH", child_environment)
        self.assertNotIn("PYTHONSTARTUP", child_environment)

    def test_child_runs_in_a_disposable_home_and_directory(self):
        outcome = self.supervisor.run_operation("env_dump", task_id="t-home")
        lines = outcome.stdout.strip().splitlines()
        child_environment = json.loads(lines[0])
        working_directory = lines[1].removeprefix("CWD ")
        self.assertTrue(child_environment["HOME"].startswith("/tmp/raawaa-supervisor-"))
        self.assertEqual(working_directory, child_environment["HOME"])
        self.assertNotEqual(working_directory, os.getcwd())
        self.assertFalse(Path(child_environment["HOME"]).exists())

    def test_a_credential_shaped_inherited_value_is_refused(self):
        with self.assertRaises(SupervisorRefusal) as caught:
            build_child_environment(
                "/tmp/raawaa-isolated",
                base={"PATH": "/usr/bin", "LANG": "ghp_abcdefghijklmnopqrstuvwxyz0123"},
            )
        self.assertIn("credential shape", str(caught.exception))

    def test_environment_construction_copies_only_the_allowlist(self):
        environment = build_child_environment(
            "/tmp/raawaa-isolated",
            base={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "SOME_DEPLOY_KEY": "not-copied",
                "AWS_SECRET_ACCESS_KEY": "not-copied",
                "TZ": "UTC",
            },
        )
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        self.assertEqual(environment["TZ"], "UTC")
        self.assertNotIn("SOME_DEPLOY_KEY", environment)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)
        for value in environment.values():
            self.assertNotIn("not-copied", value)


class LeaseTests(SupervisorHarness):
    def test_lost_lease_before_start_prevents_execution_and_leaves_no_record(self):
        store = FakeLeaseStore(fail_after=0)
        outcome = self.supervisor.run_operation(
            "print_pid",
            task_id="t-lease-before",
            fence_before=make_lease_fence(store, "t-lease-before", "lease-token"),
        )
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("lease_fence_lost_before_execution", outcome.reason)
        self.assertFalse(outcome.started)
        self.assertEqual(self.ledger_rows(), [])

    def test_confirmed_lease_lets_the_operation_run(self):
        store = FakeLeaseStore(fail_after=10)
        outcome = self.supervisor.run_operation(
            "print_pid",
            task_id="t-lease-ok",
            fence_before=make_lease_fence(store, "t-lease-ok", "lease-token"),
            fence_after=make_lease_fence(store, "t-lease-ok", "lease-token"),
        )
        self.assertEqual(outcome.classification, "succeeded")
        self.assertEqual(outcome.fence, {"before": "ok", "after": "ok"})
        self.assertGreaterEqual(store.calls, 2)

    def test_lease_loss_during_execution_cancels_and_kills_the_child(self):
        store = FakeLeaseStore(fail_after=1)
        watchdog = LeaseWatchdog(
            store, "t-lease-during", "lease-token", renew_interval_seconds=0.05
        )
        registry = OperationRegistry(
            [
                OperationSpec(
                    name="sleep_long",
                    executable=PYTHON,
                    arguments=("-c", "import time;time.sleep(30)"),
                    wall_seconds=20.0,
                    grace_seconds=0.4,
                )
            ]
        )
        supervisor = ProcessSupervisor(registry, self.ledger)
        started = time.monotonic()
        outcome = supervisor.run_operation(
            "sleep_long",
            task_id="t-lease-during",
            cancel_check=watchdog.cancel_requested,
        )
        self.assertEqual(outcome.classification, "cancelled")
        self.assertTrue(watchdog.lease_lost)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(self.ledger_rows()[0]["classification"], "cancelled")

    def test_lease_lost_after_execution_makes_the_outcome_ambiguous(self):
        store = FakeLeaseStore(fail_after=1)
        fence = make_lease_fence(store, "t-lease-after", "lease-token")
        outcome = self.supervisor.run_operation(
            "print_pid", task_id="t-lease-after", fence_before=fence, fence_after=fence
        )
        self.assertEqual(outcome.classification, "ambiguous")
        self.assertEqual(outcome.observed_classification, "succeeded")
        self.assertEqual(outcome.fence["after"], "lost:LeaseFenceLost")
        self.assertEqual(self.ledger_rows()[0]["status"], "quarantined")

    def test_an_expired_lease_in_the_real_task_store_blocks_the_fence(self):
        """The fence is only meaningful if the *real* store can make it fail."""
        from engine.task_store import TaskStore

        store = TaskStore(Path(self._tmp.name) / "tasks.db")
        store.enqueue("research_brief", "Lease expiry", {"topic": "expiry"},
                      idempotency_key="lease-expiry")
        claimed = store.claim_next(lease_timeout_seconds=600)
        self.assertIsNotNone(claimed)
        # Simulate the lease having run out while the worker was busy: this is
        # the exact row state a heartbeat is designed to detect.
        with sqlite3.connect(Path(self._tmp.name) / "tasks.db") as db:
            db.execute("UPDATE tasks SET lease_expires_at=? WHERE id=?",
                       ("2000-01-01T00:00:00+00:00", claimed["id"]))

        fence = make_lease_fence(store, claimed["id"], claimed["lease_token"])
        with self.assertRaises(LeaseFenceLost):
            fence()
        outcome = self.supervisor.run_operation(
            "print_pid", task_id=claimed["id"], fence_before=fence,
            fence_after=fence,
        )
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("lease_fence_lost_before_execution", outcome.reason)
        self.assertFalse(outcome.started)
        self.assertEqual(self.ledger_rows(), [])

    def test_watchdog_requires_a_renewal_interval_shorter_than_the_lease(self):
        with self.assertRaises(ValueError):
            LeaseWatchdog(FakeLeaseStore(), "t", "token", renew_interval_seconds=900)
        with self.assertRaises(ValueError):
            make_lease_fence(FakeLeaseStore(), "", "token")

    def test_fence_raises_the_documented_error_type(self):
        fence = make_lease_fence(FakeLeaseStore(fail_after=0), "t", "token")
        with self.assertRaises(LeaseFenceLost):
            fence()


class DispatchIdentityTests(SupervisorHarness):
    def test_duplicate_dispatch_returns_the_record_and_never_runs_twice(self):
        first = self.supervisor.run_operation("print_pid", task_id="t-dup")
        second = self.supervisor.run_operation("print_pid", task_id="t-dup")
        self.assertEqual(first.classification, "succeeded")
        self.assertTrue(second.replayed)
        self.assertFalse(second.started)
        self.assertEqual(second.reason, "replayed_from_ledger")
        # The recorded exit code, not a second process: the pid is not replayed
        # because output is deliberately not durable.
        self.assertEqual(second.exit_code, first.exit_code)
        rows = self.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "settled")

    def test_different_parameters_get_different_dispatch_identities(self):
        left = self.supervisor.dispatch_id("t-1", "echo_slot", {"value": "one"})
        right = self.supervisor.dispatch_id("t-1", "echo_slot", {"value": "two"})
        self.assertNotEqual(left, right)
        self.assertEqual(left, self.supervisor.dispatch_id("t-1", "echo_slot", {"value": "one"}))
        self.assertEqual(len(left), 32)

    def test_an_interrupted_dispatch_is_quarantined_not_replayed(self):
        parameters = {"value": "abc"}
        dispatch_id = self.supervisor.dispatch_id("t-crash", "echo_slot", parameters)
        # Simulate a supervisor that died between recording the intent and
        # recording an outcome: only the in-flight row survives.
        self.ledger.begin(dispatch_id, task_id="t-crash", operation="echo_slot", argv_digest="0" * 32)
        outcome = self.supervisor.run_operation(
            "echo_slot", parameters, task_id="t-crash"
        )
        self.assertEqual(outcome.classification, "ambiguous")
        self.assertFalse(outcome.started)
        self.assertIn("in_flight", outcome.reason)

        quarantined = self.ledger.reconcile(stale_after_seconds=0)
        self.assertEqual(quarantined, 1)
        again = self.supervisor.run_operation("echo_slot", parameters, task_id="t-crash")
        self.assertEqual(again.classification, "ambiguous")
        self.assertIn("quarantined", again.reason)

    def test_reconcile_respects_the_stale_window(self):
        self.ledger.begin("a" * 32, task_id="t", operation="echo_slot", argv_digest="0" * 32)
        self.assertEqual(self.ledger.reconcile(stale_after_seconds=3600), 0)
        self.assertEqual(self.ledger.reconcile(stale_after_seconds=0), 1)

    def test_settling_twice_is_refused(self):
        outcome = self.supervisor.run_operation("print_pid", task_id="t-settle")
        with self.assertRaises(DispatchLedgerError):
            self.ledger.settle(
                outcome.dispatch_id, classification="succeeded", exit_code=0, output_truncated=False
            )

    def test_ledger_stores_a_lease_fingerprint_not_the_lease_token(self):
        token = "super-secret-lease-token"
        self.supervisor.run_operation("print_pid", task_id="t-fingerprint", lease_token=token)
        row = self.ledger_rows()[0]
        self.assertNotIn(token, json.dumps(row))
        self.assertEqual(
            row["lease_fingerprint"], hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        )

    def test_dispatch_key_overrides_the_derived_identity(self):
        left = self.supervisor.dispatch_id("t", "print_pid", dispatch_key="step-one")
        right = self.supervisor.dispatch_id("t", "print_pid", dispatch_key="step-two")
        self.assertNotEqual(left, right)
        with self.assertRaises(SupervisorRefusal):
            self.supervisor.dispatch_id("t", "print_pid", dispatch_key="bad\nkey")

    def test_a_failed_spawn_is_recorded_as_a_refusal(self):
        def explode(*_args, **_kwargs):
            raise OSError("cannot allocate process")

        supervisor = ProcessSupervisor(self.registry, self.ledger, spawn=explode)
        outcome = supervisor.run_operation("print_pid", task_id="t-spawn-fail")
        self.assertEqual(outcome.classification, "refused")
        self.assertIn("spawn_failed", outcome.reason)
        rows = self.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "settled")
        self.assertEqual(rows[0]["classification"], "refused")


class PositiveRegressionTests(SupervisorHarness):
    def test_default_registry_reports_the_real_checked_out_commit(self):
        supervisor = ProcessSupervisor(default_registry(REPOSITORY_ROOT), self.ledger)
        outcome = supervisor.run_operation("git_head_revision", task_id="t-git-head")
        self.assertEqual(outcome.classification, "succeeded")
        head = (REPOSITORY_ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head.removeprefix("ref: ")
            expected = (REPOSITORY_ROOT / ".git" / ref).read_text(encoding="utf-8").strip()
        else:
            expected = head
        self.assertEqual(outcome.stdout.strip(), expected)

    def test_default_registry_bounded_count_operation(self):
        supervisor = ProcessSupervisor(default_registry(REPOSITORY_ROOT), self.ledger)
        outcome = supervisor.run_operation("git_recent_subjects", {"count": "2"}, task_id="t-git-log")
        self.assertEqual(outcome.classification, "succeeded")
        self.assertLessEqual(len(outcome.stdout.strip().splitlines()), 2)
        refused = supervisor.run_operation(
            "git_recent_subjects", {"count": "500"}, task_id="t-git-log-bad"
        )
        self.assertEqual(refused.classification, "refused")

    def test_ordinary_task_lifecycle_still_passes_through_the_existing_worker(self):
        """The supervisor is additive: the deterministic task path is unchanged."""
        from engine.task_store import TaskStore
        from engine.worker import run_once

        store = TaskStore(Path(self._tmp.name) / "tasks.db")
        store.enqueue("research_brief", "Supervisor regression", {"topic": "bounded execution"},
                      idempotency_key="supervisor-regression")
        result = run_once(store)
        self.assertEqual(result["status"], "succeeded")
        task = store.list_tasks()[0]
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["execution_mode"], "dry_run")


if __name__ == "__main__":
    unittest.main()
