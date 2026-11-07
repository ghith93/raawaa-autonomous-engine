"""Offline adversarial tests for the live proof's fail-closed runner."""
from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from scripts import run_sandbox_adversarial_tests as runner


class SandboxAdversarialRunnerTests(unittest.TestCase):
    def setUp(self):
        class Passing(unittest.TestCase):
            def test_one(self):
                pass

        class Skipped(unittest.TestCase):
            @unittest.skip("simulated missing live runtime")
            def test_one(self):
                pass

        class Failing(unittest.TestCase):
            def test_one(self):
                self.fail("simulated containment failure")

        self.Passing, self.Skipped, self.Failing = Passing, Skipped, Failing

    def run_fixture(self, case, expected=None):
        suite = unittest.TestLoader().loadTestsFromTestCase(case)
        expected = expected if expected is not None else {case("test_one").id()}
        return runner.run_checked(suite, expected, stream=io.StringIO())

    def test_reviewed_live_suite_matches_exact_expected_identities(self):
        from tests.adversarial.test_sandbox_escape import SandboxEscapeTest
        suite = unittest.TestLoader().loadTestsFromTestCase(SandboxEscapeTest)
        expected = {f"{SandboxEscapeTest.__module__}.{SandboxEscapeTest.__qualname__}.{name}"
                    for name in runner.EXPECTED_METHODS}
        observed = runner._test_ids(suite)
        # Eight reviewed proofs: the six original probes plus the host-helper
        # positive control and the parent-environment isolation attempt.
        self.assertEqual(len(observed), 8)
        self.assertEqual(set(observed), expected)

    def test_exact_passing_suite_is_accepted(self):
        self.assertTrue(self.run_fixture(self.Passing))

    def test_skipped_or_failed_suite_is_rejected(self):
        self.assertFalse(self.run_fixture(self.Skipped))
        self.assertFalse(self.run_fixture(self.Failing))

    def test_expected_failure_and_unexpected_success_are_rejected(self):
        class ExpectedFailure(unittest.TestCase):
            @unittest.expectedFailure
            def test_one(self):
                self.fail("cannot use expected failure as containment proof")

        class UnexpectedSuccess(unittest.TestCase):
            @unittest.expectedFailure
            def test_one(self):
                pass

        self.assertFalse(self.run_fixture(ExpectedFailure))
        self.assertFalse(self.run_fixture(UnexpectedSuccess))

    def test_missing_extra_or_wrong_tests_are_rejected(self):
        actual = self.Passing("test_one").id()
        for expected in (set(), {"missing"}, {actual, "extra"}):
            with self.subTest(expected=expected):
                self.assertFalse(self.run_fixture(self.Passing, expected))

    def test_duplicate_test_identity_is_rejected(self):
        suite = unittest.TestSuite([self.Passing("test_one"), self.Passing("test_one")])
        self.assertFalse(runner.run_checked(suite, {self.Passing("test_one").id()}, stream=io.StringIO()))

    def annotations_for(self, case):
        suite = unittest.TestLoader().loadTestsFromTestCase(case)
        result = runner.run_suite(suite, stream=io.StringIO())
        lines, _outcomes = runner.result_annotations(result, [case("test_one").id()])
        return lines

    def test_each_reviewed_probe_is_reported_individually(self):
        """A green job must say which proofs ran, readable without the job log."""
        self.assertEqual(self.annotations_for(self.Passing), [
            "::notice title=Sandbox probe one::passed",
        ])
        # A skip is never allowed to look like success.
        self.assertEqual(self.annotations_for(self.Skipped), [
            "::error title=Sandbox probe one::skipped",
        ])
        self.assertEqual(self.annotations_for(self.Failing), [
            "::error title=Sandbox probe one::failed",
        ])

    def test_annotations_cover_every_expected_probe_even_if_it_never_ran(self):
        """A probe that is absent from the result is still reported, as an error."""
        # A bare TestResult records no executions, which is what an aborted run
        # looks like; it must be an error, never a pass.
        lines, outcomes = runner.result_annotations(
            unittest.TestResult(), ["pkg.Case.test_the_probe_actually_ran"])
        self.assertEqual(outcomes, {"the_probe_actually_ran": "not_run"})
        self.assertEqual(lines, ["::error title=Sandbox probe the_probe_actually_ran::not_run"])

    def test_annotation_text_carries_no_paths_values_or_output(self):
        class Leaky(unittest.TestCase):
            def test_one(self):
                self.fail("/tmp/secret canary=abc token=xyz")

        line = self.annotations_for(Leaky)[0]
        self.assertNotIn("secret", line)
        self.assertNotIn("canary", line)
        self.assertNotIn("token", line)
        self.assertNotIn("/tmp", line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_unsafe_probe_name_is_refused_before_it_reaches_a_workflow_command(self):
        for name in ("test_bad\n::error::injected", "test_Bad", "test_" + "a" * 70,
                     "test_", "test_bad name"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner.result_annotations(unittest.TestResult(), [f"pkg.Case.{name}"])

    def test_the_report_file_carries_every_probe_because_annotations_are_capped(self):
        """GitHub keeps ten annotations per step; the reviewed set is fourteen.

        On run 42379096458 the four that were dropped were exactly the four
        carrying the criterion-5 evidence, so the artifact is the record of truth.
        """
        import json as _json
        import os as _os
        import tempfile as _tempfile

        with _tempfile.TemporaryDirectory() as tmp:
            report_path = _os.path.join(tmp, "report.json")
            summary_path = _os.path.join(tmp, "summary.md")
            previous = _os.environ.get("GITHUB_STEP_SUMMARY")
            _os.environ["GITHUB_STEP_SUMMARY"] = summary_path
            try:
                ok = runner.run_checked(
                    unittest.TestLoader().loadTestsFromTestCase(self.Passing),
                    [self.Passing("test_one").id()],
                    stream=io.StringIO(), report_path=report_path)
            finally:
                if previous is None:
                    _os.environ.pop("GITHUB_STEP_SUMMARY", None)
                else:
                    _os.environ["GITHUB_STEP_SUMMARY"] = previous
            self.assertTrue(ok)
            report = _json.load(open(report_path, encoding="utf-8"))
            self.assertEqual(report["probes"], {"one": "passed"})
            self.assertTrue(report["all_passed"])
            self.assertEqual(report["counts"], {"expected": 1, "passed": 1})
            summary = open(summary_path, encoding="utf-8").read()
            self.assertIn("`one`", summary)
            self.assertIn("1 of 1 passed", summary)

    def test_a_failed_probe_reaches_the_report_as_not_passing(self):
        import json as _json
        import os as _os
        import tempfile as _tempfile

        with _tempfile.TemporaryDirectory() as tmp:
            report_path = _os.path.join(tmp, "report.json")
            ok = runner.run_checked(
                unittest.TestLoader().loadTestsFromTestCase(self.Failing),
                [self.Failing("test_one").id()],
                stream=io.StringIO(), report_path=report_path)
            self.assertFalse(ok)
            report = _json.load(open(report_path, encoding="utf-8"))
            self.assertFalse(report["all_passed"])
            self.assertEqual(report["probes"], {"one": "failed"})

    def test_only_exact_approved_image_passes_identity_preflight(self):
        approved = runner.APPROVED_IMAGE
        alternatives = (
            "python:3.12-slim", "library/python@sha256:" + "a" * 64,
            approved.replace("docker.io", "registry.example"),
            approved.replace("library/python", "library/ubuntu"),
            approved.replace("05a994", "15a994"),
            approved + "\n", approved + ":tag", "",
            "docker.io/library/python@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54",
        )
        for image in (approved, *alternatives):
            with self.subTest(image=image), patch.dict(os.environ, {"RAAWAA_SANDBOX_IMAGE": image}, clear=True):
                with patch("sys.stderr", new=io.StringIO()), patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(runner.main(["--check-image"]), 0 if image == approved else 1)
        with patch.dict(os.environ, {"RAAWAA_SANDBOX_IMAGE": approved}, clear=True):
            with patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(runner.main(["--unknown"]), 1)

    def test_preflight_refuses_no_opt_in_invalid_image_or_missing_docker(self):
        image = runner.APPROVED_IMAGE
        for env, docker in (({}, "/usr/bin/docker"),
                            ({"RAAWAA_SANDBOX_LIVE": "1", "RAAWAA_SANDBOX_IMAGE": "python:latest"}, "/usr/bin/docker"),
                            ({"RAAWAA_SANDBOX_LIVE": "1", "RAAWAA_SANDBOX_IMAGE": image}, None)):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), patch.object(runner.shutil, "which", return_value=docker):
                with patch("sys.stderr", new=io.StringIO()):
                    self.assertEqual(runner.main([]), 1)


if __name__ == "__main__":
    unittest.main()
