"""Run exactly the live sandbox probes; skips or missing tests are failures.

This guard does not establish that gVisor is secure, nor does it activate an
executor. It prevents unittest's normal successful exit for skipped tests from
being mistaken for live proof.
"""
from __future__ import annotations

import os
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
    "test_the_public_network_is_unreachable",
    "test_credentials_and_the_docker_socket_are_not_accessible",
    "test_the_root_filesystem_cannot_be_written",
})
# Exact linux/amd64 manifest observed in Docker Hub's official Python metadata.
# Changing this identity requires a reviewed provenance update and fresh proof.
APPROVED_IMAGE = (
    "docker.io/library/python@sha256:"
    "05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177"
)


def _test_ids(suite: unittest.TestSuite) -> list[str]:
    result = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(_test_ids(item))
        else:
            result.append(item.id())
    return result


def run_checked(suite: unittest.TestSuite, expected_ids: Iterable[str], *, stream) -> bool:
    """Fail for different test identities, partial execution, skip, or failure."""
    expected = set(expected_ids)
    observed = _test_ids(suite)
    if not expected or len(observed) != len(expected) or set(observed) != expected:
        print("Sandbox proof refused: probe suite differs from the reviewed test set.", file=stream)
        return False
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if (result.testsRun != len(expected) or result.skipped or result.expectedFailures
            or result.unexpectedSuccesses or not result.wasSuccessful()):
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
    from tests.adversarial.test_sandbox_escape import SandboxEscapeTest

    suite = unittest.TestLoader().loadTestsFromTestCase(SandboxEscapeTest)
    expected = {f"{SandboxEscapeTest.__module__}.{SandboxEscapeTest.__qualname__}.{name}"
                for name in EXPECTED_METHODS}
    return 0 if run_checked(suite, expected, stream=sys.stderr) else 1


if __name__ == "__main__":
    raise SystemExit(main())
