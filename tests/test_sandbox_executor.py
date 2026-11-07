"""Offline contract tests for the sandbox executor.

No container runtime is required: the tests use a stand-in executable so the
supervisor path is exercised for real, while the *container* contract is checked
against the vector this module builds. The one thing these tests deliberately do
not prove is that gVisor confines anything -- that is the live suite's job, and
claiming it here is exactly the confusion this repository has been correcting.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from engine.process_supervisor import (
    CEILINGS as SUPERVISOR_CEILINGS,
    OperationSpec,
    SqliteDispatchLedger,
    SupervisorRefusal,
    default_registry,
)
from engine.sandbox_executor import (
    KILL_GRACE_SECONDS,
    SANDBOX_OPERATION,
    SandboxExecutor,
    SandboxExecutorError,
    sandbox_registry,
)
from engine.sandbox_policy import SandboxLimits, SandboxRequest

DIGEST_IMAGE = "docker.io/library/python@sha256:" + "05a994d2" + "0" * 56


class _StandIn:
    """A temporary directory holding a fake ``docker`` that echoes and exits."""

    def __init__(self, body: str = "#!/bin/sh\necho stand-in-ran\nexit 0\n"):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.binary = self.dir / "docker"
        self.binary.write_text(body)
        self.binary.chmod(0o755)

    def close(self) -> None:
        self._tmp.cleanup()


class SandboxExecutorRefusalTests(unittest.TestCase):
    def setUp(self):
        self.stand_in = _StandIn()
        self.addCleanup(self.stand_in.close)

    def request(self, **limits):
        return SandboxRequest(
            image=DIGEST_IMAGE,
            command=("python3", "-c", "print(1)"),
            source_dir=str(self.stand_in.dir),
            limits=SandboxLimits(**limits),
        )

    def test_release_flag_is_closed_by_default(self):
        executor = SandboxExecutor(docker_binary=str(self.stand_in.binary))
        self.assertFalse(executor.release_enabled)
        with self.assertRaises(SandboxExecutorError) as caught:
            executor.prepare(self.request(), ref="refs/heads/main")
        self.assertIn("disabled", str(caught.exception))

    def test_release_flag_requires_exactly_true(self):
        """A truthy value must not arm the sandbox."""
        for value in (1, "true", "yes", object()):
            with self.subTest(value=value):
                executor = SandboxExecutor(release_enabled=value,
                                           docker_binary=str(self.stand_in.binary))
                self.assertFalse(executor.release_enabled)
                with self.assertRaises(SandboxExecutorError):
                    executor.prepare(self.request(), ref="refs/heads/main")

    def test_unreviewed_refs_are_refused_before_anything_else(self):
        """A PR head, a fork branch or a tag is not a reviewed ref."""
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary))
        for ref in ("refs/pull/700/head", "refs/heads/topic", "main", "refs/tags/v1",
                    "", None, "refs/heads/main "):
            with self.subTest(ref=ref):
                with self.assertRaises(SandboxExecutorError) as caught:
                    executor.prepare(self.request(), ref=ref)
                self.assertIn("ref refused", str(caught.exception))

    def test_reviewed_main_is_accepted(self):
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary))
        plan = executor.prepare(self.request(), ref="refs/heads/main")
        self.assertEqual(plan.ref, "refs/heads/main")

    def test_pull_request_heads_can_only_be_admitted_explicitly(self):
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary))
        plan = executor.prepare(self.request(), ref="refs/pull/700/head",
                                allow_pr_heads=True)
        self.assertEqual(plan.ref, "refs/pull/700/head")
        # ...and the escape hatch does not admit a plain branch.
        with self.assertRaises(SandboxExecutorError):
            executor.prepare(self.request(), ref="refs/heads/topic", allow_pr_heads=True)

    def test_a_tag_image_is_refused_because_it_is_a_moving_target(self):
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary))
        request = SandboxRequest(image="python:3.12", command=("python3", "-c", "1"),
                                 source_dir=str(self.stand_in.dir))
        with self.assertRaises(SandboxExecutorError) as caught:
            executor.prepare(request, ref="refs/heads/main")
        self.assertIn("digest", str(caught.exception))

    def test_container_wall_clock_must_fire_before_the_supervisor_ceiling(self):
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary))
        too_long = int(SUPERVISOR_CEILINGS["wall_seconds"])
        with self.assertRaises(SandboxExecutorError) as caught:
            executor.prepare(self.request(wall_seconds=too_long), ref="refs/heads/main")
        self.assertIn("observable", str(caught.exception))

    def test_missing_runtime_is_a_configuration_error_not_an_outcome(self):
        executor = SandboxExecutor(release_enabled=True, docker_binary="/nonexistent/docker")
        with self.assertRaises(SandboxExecutorError) as caught:
            executor.run(self.request(), task_id="t", ref="refs/heads/main")
        self.assertIn("not a runnable file", str(caught.exception))


class SandboxPlanTests(unittest.TestCase):
    def setUp(self):
        self.stand_in = _StandIn()
        self.addCleanup(self.stand_in.close)
        self.executor = SandboxExecutor(release_enabled=True,
                                        docker_binary=str(self.stand_in.binary))
        self.request = SandboxRequest(
            image=DIGEST_IMAGE,
            command=("python3", "-c", "print(1)"),
            source_dir=str(self.stand_in.dir),
            limits=SandboxLimits(wall_seconds=45),
        )

    def test_plan_carries_the_hardened_boundary(self):
        plan = self.executor.prepare(self.request, ref="refs/heads/main")
        self.assertIn("--runtime=runsc", plan.argv)
        self.assertIn("--network=none", plan.argv)
        self.assertIn("--read-only", plan.argv)
        self.assertIn("--cap-drop=ALL", plan.argv)
        self.assertIn("--security-opt=no-new-privileges", plan.argv)
        self.assertTrue(any(a.startswith("--user=") for a in plan.argv))
        self.assertEqual(plan.wall_seconds, 45)

    def test_summary_leaks_no_paths_or_argv(self):
        plan = self.executor.prepare(self.request, ref="refs/heads/main")
        summary = plan.summary()
        self.assertEqual(set(summary), {"image", "wall_seconds", "ref"})
        rendered = repr(summary)
        self.assertNotIn(str(self.stand_in.dir), rendered)


class SandboxRunTests(unittest.TestCase):
    def setUp(self):
        self.stand_in = _StandIn()
        self.addCleanup(self.stand_in.close)
        self.executor = SandboxExecutor(release_enabled=True,
                                        docker_binary=str(self.stand_in.binary))

    def request(self, **limits):
        return SandboxRequest(
            image=DIGEST_IMAGE,
            command=("python3", "-c", "print(1)"),
            source_dir=str(self.stand_in.dir),
            limits=SandboxLimits(**limits),
        )

    def test_a_reviewed_run_reaches_the_supervisor_and_succeeds(self):
        outcome = self.executor.run(self.request(), task_id="task-1", ref="refs/heads/main")
        self.assertEqual(outcome.classification, "succeeded")
        self.assertEqual(outcome.exit_code, 0)
        self.assertIn("stand-in-ran", outcome.stdout)

    def test_supervisor_ceiling_is_later_than_the_container_deadline(self):
        """The gap is the whole point: it keeps the two deadlines distinguishable."""
        from engine.sandbox_executor import SandboxExecutor as _E  # noqa: F401
        plan = self.executor.prepare(self.request(wall_seconds=60), ref="refs/heads/main")
        self.assertGreater(plan.wall_seconds + KILL_GRACE_SECONDS, plan.wall_seconds)
        self.assertLessEqual(plan.wall_seconds + KILL_GRACE_SECONDS,
                             SUPERVISOR_CEILINGS["wall_seconds"])

    def test_a_failing_container_is_classified_not_raised(self):
        self.stand_in.close()
        failing = _StandIn("#!/bin/sh\necho boom >&2\nexit 7\n")
        self.addCleanup(failing.close)
        executor = SandboxExecutor(release_enabled=True, docker_binary=str(failing.binary))
        request = SandboxRequest(image=DIGEST_IMAGE, command=("true",),
                                 source_dir=str(failing.dir))
        outcome = executor.run(request, task_id="task-2", ref="refs/heads/main")
        # A container that ran and failed is an outcome, not an exception; the
        # distinction matters because only one of them means "no process existed".
        self.assertEqual(outcome.classification, "failed")
        self.assertEqual(outcome.exit_code, 7)


class SandboxLedgerTests(unittest.TestCase):
    """At-most-once across a crash needs the durable ledger, not just a process."""

    def setUp(self):
        self.stand_in = _StandIn()
        self.addCleanup(self.stand_in.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _request(self):
        return SandboxRequest(image=DIGEST_IMAGE, command=("true",),
                              source_dir=str(self.stand_in.dir),
                              limits=SandboxLimits(wall_seconds=30))

    def test_a_repeated_task_id_replays_instead_of_starting_a_second_container(self):
        ledger = SqliteDispatchLedger(Path(self.tmp.name) / "dispatch.db")
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary),
                                   ledger=ledger)
        first = executor.run(self._request(), task_id="same-task", ref="refs/heads/main")
        second = executor.run(self._request(), task_id="same-task", ref="refs/heads/main")
        self.assertEqual(first.classification, "succeeded")
        self.assertTrue(getattr(second, "replayed", False))
        self.assertEqual(first.dispatch_id, second.dispatch_id)

    def test_without_a_ledger_the_caller_is_told_nothing_was_deduplicated(self):
        """The absence of a ledger must not quietly look like at-most-once."""
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary))
        outcome = executor.run(self._request(), task_id="t", ref="refs/heads/main")
        self.assertFalse(getattr(outcome, "replayed", False))
        self.assertFalse(executor.has_durable_ledger)


class ContainmentReportTests(unittest.TestCase):
    """A zero must never be reported as containment when the census was blind."""

    class _Outcome:
        def __init__(self, killed, escaped, observable):
            self.descendants_killed = killed
            self.descendants_escaped = escaped
            self.subreaper_enabled = observable

    def test_a_clean_census_is_clean(self):
        report = SandboxExecutor.containment_report(self._Outcome(2, 0, True))
        self.assertEqual(report["verdict"], "clean")

    def test_zero_escaped_without_the_census_reads_as_unverified(self):
        report = SandboxExecutor.containment_report(self._Outcome(0, 0, False))
        self.assertEqual(report["verdict"], "unverified")

    def test_a_surviving_escapee_reads_as_leaked(self):
        report = SandboxExecutor.containment_report(self._Outcome(0, 3, True))
        self.assertEqual(report["verdict"], "leaked")

    def test_the_report_does_not_claim_more_than_the_supervisor_measured(self):
        report = SandboxExecutor.containment_report(self._Outcome(1, 0, True))
        self.assertEqual(set(report), {
            "descendants_killed", "descendants_escaped",
            "census_observable", "verdict",
        })


class SandboxRegistryTests(unittest.TestCase):
    def setUp(self):
        self.stand_in = _StandIn()
        self.addCleanup(self.stand_in.close)

    def test_the_container_operation_is_added_without_losing_existing_ones(self):
        base = default_registry()
        request = SandboxRequest(
            image=DIGEST_IMAGE, command=("true",), source_dir=str(self.stand_in.dir))
        executor = SandboxExecutor(release_enabled=True,
                                   docker_binary=str(self.stand_in.binary))
        plan = executor.prepare(request, ref="refs/heads/main")
        registry = sandbox_registry(plan.argv, wall_seconds=plan.wall_seconds,
                                    docker=str(self.stand_in.binary))
        self.assertIn(SANDBOX_OPERATION, registry.names())
        for name in base.names():
            self.assertIn(name, registry.names())
        self.assertEqual(len(registry.names()), len(base.names()) + 1)

    def test_the_registry_refuses_a_vector_that_is_not_a_container_run(self):
        with self.assertRaises(SandboxExecutorError):
            sandbox_registry(("docker", "build", "x"), wall_seconds=10,
                             docker=str(self.stand_in.binary))

    def test_a_long_container_vector_needs_an_explicit_per_operation_cap(self):
        """The default 16-argument bound is for probes, not for ``docker run``.

        This is the failure the executor hit first: a hardened container vector is
        about twenty reviewed flags, so the operation declares its own cap. The
        global default stays where it was, so no existing operation is loosened.
        """
        long_vector = ("docker", "run") + tuple(f"--flag-{i}" for i in range(20))
        with self.assertRaises(Exception):
            OperationSpec(name="too_long", executable=str(self.stand_in.binary),
                          arguments=long_vector[2:]).validated()
        registry = sandbox_registry(long_vector, wall_seconds=10,
                                    docker=str(self.stand_in.binary))
        spec = registry.get(SANDBOX_OPERATION)
        self.assertEqual(len(spec.arguments), 20)
        self.assertEqual(spec.max_arguments, SUPERVISOR_CEILINGS["max_arguments"])

    def test_the_ceiling_cannot_be_raised_beyond_the_hard_limit(self):
        with self.assertRaises(Exception):
            OperationSpec(name="greedy", executable=str(self.stand_in.binary),
                          max_arguments=SUPERVISOR_CEILINGS["max_arguments"] + 1).validated()

    def test_build_argv_enforces_the_per_operation_bound_not_the_global_default(self):
        """One bound, checked in both places, so validation and spawn agree.

        A vector that validated under its own cap but was then rejected at spawn
        by the *global* default would fail confusingly, and only at run time.
        """
        spec = OperationSpec(
            name="long_ok", executable=str(self.stand_in.binary),
            arguments=tuple(f"arg-{i}" for i in range(20)),
            max_arguments=SUPERVISOR_CEILINGS["max_arguments"],
        ).validated()
        self.assertEqual(len(spec.build_argv({})), 21)

        # Declared cap below the argument count: refused at validation.
        with self.assertRaises(Exception) as caught:
            OperationSpec(name="long_bad", executable=str(self.stand_in.binary),
                          arguments=tuple(f"arg-{i}" for i in range(20))).validated()
        self.assertIn("at most 16", str(caught.exception))

        # And build_argv refuses independently when handed an over-long spec,
        # because that is the last line of defence before a spawn.
        forged = object.__new__(OperationSpec)
        object.__setattr__(forged, "name", "forged")
        object.__setattr__(forged, "executable", str(self.stand_in.binary))
        object.__setattr__(forged, "arguments", tuple(f"a{i}" for i in range(20)))
        object.__setattr__(forged, "max_arguments", 16)
        object.__setattr__(forged, "slot_names_", ())
        with self.assertRaises(SupervisorRefusal) as refusal:
            OperationSpec.build_argv(forged, {})
        self.assertIn("longer than the ceiling", str(refusal.exception))


if __name__ == "__main__":
    unittest.main()
