"""Run exactly the live sandbox probes; skips or missing tests are failures.

This guard does not establish that gVisor is secure, nor does it activate an
executor. It prevents unittest's normal successful exit for skipped tests from
being mistaken for live proof.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unittest
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXPECTED_METHODS = frozenset({
    "test_the_probe_actually_ran",
    "test_a_host_created_file_outside_the_workspace_is_not_readable",
    "test_synthetic_environment_canary_detection",
    "test_the_host_helper_is_a_real_process_with_the_marker",
    "test_the_sandbox_cannot_read_a_host_process_environment",
    "test_the_public_network_is_unreachable",
    "test_credentials_and_the_docker_socket_are_not_accessible",
    "test_the_root_filesystem_cannot_be_written",
})

#: Criterion 5 of #440: the limits must hold when the task pushes on them. Kept
#: in a separate reviewed set because a configured flag is not evidence and this
#: is the only group that can produce evidence for it.
EXPECTED_PRESSURE_METHODS = frozenset({
    # The control exists because a container that dies for any reason at all
    # would otherwise read as the memory limit working.
    "test_the_positive_control_allocates_and_survives",
    # The memory claim is judged from the host: three dispatches established that
    # the kill destroys any in-container observer.
    "test_memory_overshoot_is_killed_by_the_limit",
    "test_the_process_count_is_bounded",
    "test_cpu_is_bounded_to_one_core",
    "test_the_wall_clock_holds",
    "test_a_setsid_descendant_is_not_left_running_under_pressure",
})
# Exact linux/amd64 manifest observed in Docker Hub's official Python metadata.
# Changing this identity requires a reviewed provenance update and fresh proof.
APPROVED_IMAGE = (
    "docker.io/library/python@sha256:"
    "05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177"
)


# Each probe name is reported as a fixed result token. The pattern is asserted
# before the name is placed in a workflow command, so a test name cannot forge
# annotations or extra log lines.
RESULT_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _RecordingResult(unittest.TextTestResult):
    """Records which tests actually started.

    Without this, a probe that never ran would simply be absent from the
    failures and skips, and would be reported as a pass - the exact false-pass
    this whole file exists to prevent. Only a test that started *and* produced
    no error, failure, skip or unexpected outcome may be reported as passed.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_ids: set[str] = set()

    def startTest(self, test):
        self.executed_ids.add(test.id())
        super().startTest(test)


def run_suite(suite: unittest.TestSuite, *, stream) -> _RecordingResult:
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=_RecordingResult)
    return runner.run(suite)


def result_annotations(result: unittest.TestResult,
                       expected_ids: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    """Render one workflow command per reviewed probe.

    The job log is served from GitHub's results blob storage, which is not
    reachable from every reviewer environment, so "the job was green" does not
    by itself say *which* proofs ran. These annotations carry the outcome of
    each reviewed probe and nothing else: no paths, canary values, markers,
    environment data or container output. Every non-passing outcome is reported
    as an error, so neither a skip nor a test that never started can look like
    success.
    """
    failed = {test.id() for test, _ in result.failures + result.errors}
    skipped = {test.id() for test, _ in result.skipped}
    # expectedFailures is a (test, reason) list; unexpectedSuccesses is a list
    # of bare TestCase instances. Normalise both to ids before use.
    unexpected = {test.id() for test, _ in result.expectedFailures}
    unexpected |= {test.id() for test in result.unexpectedSuccesses}
    executed = getattr(result, "executed_ids", set())
    lines = []
    outcomes: dict[str, str] = {}
    for test_id in sorted(expected_ids):
        token = test_id.rsplit(".", 1)[-1].removeprefix("test_")
        if not RESULT_TOKEN_RE.match(token):
            raise ValueError("unsafe_probe_result_token")
        if test_id in failed:
            outcome, command = "failed", "::error"
        elif test_id in skipped:
            outcome, command = "skipped", "::error"
        elif test_id in unexpected:
            outcome, command = "unexpected", "::error"
        elif test_id in executed:
            outcome, command = "passed", "::notice"
        else:
            outcome, command = "not_run", "::error"
        lines.append(f"{command} title=Sandbox probe {token}::{outcome}")
        outcomes[token] = outcome
    return lines, outcomes


def check_result(result: unittest.TestResult, expected_ids: Iterable[str]) -> bool:
    """Fail for wrong identities, partial execution, skip or failure."""
    expected = set(expected_ids)
    return (
        bool(expected)
        and result.testsRun == len(expected)
        and not result.skipped
        and not result.expectedFailures
        and not result.unexpectedSuccesses
        and result.wasSuccessful()
    )


def _test_ids(suite: unittest.TestSuite) -> list[str]:
    result = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(_test_ids(item))
        else:
            result.append(item.id())
    return result


def run_checked(suite: unittest.TestSuite, expected_ids: Iterable[str], *, stream,
                report_path: str | None = None) -> bool:
    """Refuse a different test set, then run it and publish per-probe outcomes."""
    expected = set(expected_ids)
    observed = _test_ids(suite)
    if not expected or len(observed) != len(expected) or set(observed) != expected:
        print("Sandbox proof refused: probe suite differs from the reviewed test set.", file=stream)
        return False
    result = run_suite(suite, stream=stream)
    annotations, outcomes = result_annotations(result, expected)
    for line in annotations:
        print(line)

    # GitHub keeps at most ten annotations per step. With fourteen reviewed
    # probes, the four that came back missing on run 42379096458 were exactly
    # the four that carry the criterion-5 evidence, so annotations cannot be the
    # only channel. This file is uploaded as an artifact, where nothing is
    # truncated, and the step summary carries the same table for a reader who
    # only looks at the run page.
    passed = check_result(result, expected)
    if report_path:
        report = {
            "probes": dict(sorted(outcomes.items())),
            "counts": {"expected": len(expected), "passed": sum(
                1 for v in outcomes.values() if v == "passed")},
            "all_passed": passed,
            "note": "a probe that did not run is reported as not_run, never as passed",
        }
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("\n#### Reviewed probes\n\n")
                handle.write("| probe | outcome |\n| --- | --- |\n")
                for token, outcome in sorted(outcomes.items()):
                    handle.write(f"| `{token}` | {outcome} |\n")
                handle.write(f"\n{report['counts']['passed']} of "
                             f"{report['counts']['expected']} passed.\n")

    if not passed:
        print("Sandbox proof refused: probes failed, skipped, or did not all run.", file=stream)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    image = os.environ.get("RAAWAA_SANDBOX_IMAGE", "")
    if image != APPROVED_IMAGE:
        print("Sandbox proof refused: image is not the approved immutable manifest.", file=sys.stderr)
        return 1
    # The workflow uses the same allowlist before installing or pulling anything.
    if args == ["--check-image"]:
        print("Approved sandbox image identity verified.")
        return 0
    if args:
        print("Sandbox proof refused: unexpected command arguments.", file=sys.stderr)
        return 1
    if os.environ.get("RAAWAA_SANDBOX_LIVE") != "1" or shutil.which("docker") is None:
        print("Sandbox proof refused: live opt-in and docker are required.", file=sys.stderr)
        return 1
    from tests.adversarial.test_sandbox_escape import (
        SandboxEscapeTest,
        SandboxResourceLimitTest,
    )

    loader = unittest.TestLoader()
    # Both reviewed groups run in one pass, and the refusal in ``run_checked``
    # still applies: a suite that differs from the reviewed set is refused rather
    # than reported on.
    suite = unittest.TestSuite([
        loader.loadTestsFromTestCase(SandboxEscapeTest),
        loader.loadTestsFromTestCase(SandboxResourceLimitTest),
    ])
    expected = {
        f"{case.__module__}.{case.__qualname__}.{name}"
        for case, methods in ((SandboxEscapeTest, EXPECTED_METHODS),
                              (SandboxResourceLimitTest, EXPECTED_PRESSURE_METHODS))
        for name in methods
    }
    report_path = os.environ.get("RAAWAA_PROBE_REPORT", "")
    ok = run_checked(suite, expected, stream=sys.stderr, report_path=report_path or None)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
