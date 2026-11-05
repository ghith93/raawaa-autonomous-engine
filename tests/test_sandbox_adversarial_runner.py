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
        self.assertEqual(len(observed), 6)
        self.assertEqual(set(observed), expected)

    def test_exact_passing_suite_is_accepted(self):
        self.assertTrue(self.run_fixture(self.Passing))

    def test_skipped_or_failed_suite_is_rejected(self):
        self.assertFalse(self.run_fixture(self.Skipped))
        self.assertFalse(self.run_fixture(self.Failing))

    def test_missing_extra_or_wrong_tests_are_rejected(self):
        actual = self.Passing("test_one").id()
        for expected in (set(), {"missing"}, {actual, "extra"}):
            with self.subTest(expected=expected):
                self.assertFalse(self.run_fixture(self.Passing, expected))

    def test_duplicate_test_identity_is_rejected(self):
        suite = unittest.TestSuite([self.Passing("test_one"), self.Passing("test_one")])
        self.assertFalse(runner.run_checked(suite, {self.Passing("test_one").id()}, stream=io.StringIO()))

    def test_preflight_refuses_no_opt_in_invalid_image_or_missing_docker(self):
        image = "library/python@sha256:" + "a" * 64
        for env, docker in (({}, "/usr/bin/docker"),
                            ({"RAAWAA_SANDBOX_LIVE": "1", "RAAWAA_SANDBOX_IMAGE": "python:latest"}, "/usr/bin/docker"),
                            ({"RAAWAA_SANDBOX_LIVE": "1", "RAAWAA_SANDBOX_IMAGE": image}, None)):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), patch.object(runner.shutil, "which", return_value=docker):
                with patch("sys.stderr", new=io.StringIO()):
                    self.assertEqual(runner.main(), 1)


if __name__ == "__main__":
    unittest.main()
