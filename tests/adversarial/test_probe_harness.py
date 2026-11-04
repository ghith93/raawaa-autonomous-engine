"""Offline tests for the adversarial probe harness (#440).

The probes need a real sandbox; the harness around them does not. These tests run
in ordinary CI and pin the properties whose absence would make a green live run
meaningless:

* a probe that did not run, is missing a key, or cannot read a file it is
  *supposed* to see is reported as a broken probe — never as containment;
* the host canary lives outside the (symlink-resolved) workspace, is readable by
  the sandbox uid, and carries a per-run marker;
* a canary that would be unreadable because of its own permissions, or because a
  symlink puts it inside the mount, is refused instead of silently used;
* the probe program embeds exact ``repr``-quoted paths rather than raw text.

The first item is the false-test this file exists for: ``open()`` failing for any
reason produces ``denied:<Error>``, so a wrong canary path used to satisfy the
"file outside the mount is unreadable" assertion while proving nothing.
"""
import importlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def _load_probe_module():
    for name in (
        "adversarial.test_sandbox_escape",       # discovered from `tests/`
        "tests.adversarial.test_sandbox_escape",  # run as `tests.adversarial...`
        "test_sandbox_escape",                    # run from this directory
    ):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise ImportError("cannot import the adversarial probe module")


probe = _load_probe_module()


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=["probe"], returncode=returncode, stdout=stdout, stderr="")


def _good_output(**overrides):
    output = {
        "control": "raawaa-autonomous-engine\n\nAn independent, cost-controlled",
        "host_canary": "denied:FileNotFoundError",
        "network": "denied:OSError",
        "root_write": "denied:OSError",
        "exists:/var/run/docker.sock": False,
        "exists:/root/.ssh": False,
    }
    output.update(overrides)
    return output


class FalseTestGuardTest(unittest.TestCase):
    def test_no_output_is_not_containment(self):
        with self.assertRaisesRegex(AssertionError, "may not have run"):
            probe._assert_probe_usable({})

    def test_a_missing_key_is_not_containment(self):
        # The old tests used dict.get(), so a vanished key read as "False".
        for key in probe.REQUIRED_KEYS:
            output = _good_output()
            del output[key]
            with self.assertRaisesRegex(AssertionError, "missing", msg=key):
                probe._assert_probe_usable(output)

    def test_a_failed_positive_control_is_not_containment(self):
        with self.assertRaisesRegex(AssertionError, "positive control failed"):
            probe._assert_probe_usable(_good_output(control="denied:FileNotFoundError"))

    def test_a_positive_control_with_the_wrong_content_is_not_containment(self):
        with self.assertRaisesRegex(AssertionError, "positive control read"):
            probe._assert_probe_usable(_good_output(control="something else entirely"))

    def test_a_complete_observation_is_accepted(self):
        self.assertIsNone(probe._assert_probe_usable(_good_output()))

    def test_a_probe_that_exits_non_zero_produces_no_output(self):
        completed = _completed(stdout='{"control": "raawaa-autonomous-engine"}', returncode=1)
        self.assertEqual(probe._parse_probe_output(completed), {})

    def test_unparseable_or_non_object_output_is_empty(self):
        for stdout in ("", "no json here", "{not json}", "[1, 2, 3]"):
            self.assertEqual(probe._parse_probe_output(_completed(stdout=stdout)), {}, stdout)


class CanaryTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.base = self.root / "base"
        self.base.mkdir()

    def test_the_canary_is_outside_the_workspace_readable_and_unique(self):
        first, first_marker = probe._create_canary(self.base)
        second, second_marker = probe._create_canary(self.base)
        self.addCleanup(shutil.rmtree, first.parent, True)
        self.addCleanup(shutil.rmtree, second.parent, True)
        self.assertFalse(first.resolve().is_relative_to(self.workspace))
        self.assertTrue(first.resolve().is_relative_to(self.base))
        self.assertEqual(first.read_text(encoding="utf-8"), first_marker)
        self.assertNotEqual(first_marker, second_marker)
        self.assertEqual(first.stat().st_mode & 0o777, 0o644)
        self.assertEqual(first.parent.stat().st_mode & 0o777, 0o755)

    def test_a_canary_directory_inside_the_workspace_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "world-traversable"):
            probe._canary_base_dir(self.workspace, candidates=[self.workspace / "inside"])

    def test_a_symlink_into_the_workspace_is_not_a_canary_location(self):
        # Path.resolve() is what makes this fail; a lexical is_relative_to() check
        # would accept a link that actually sits inside the mounted tree.
        link = self.root / "link"
        link.symlink_to(self.workspace, target_is_directory=True)
        self.addCleanup(link.unlink)
        with self.assertRaisesRegex(RuntimeError, "world-traversable"):
            probe._canary_base_dir(self.workspace, candidates=[link])

    def test_a_non_traversable_base_is_refused(self):
        # A 0700 home directory would deny the read by permission, not by sandbox.
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError, "world-traversable"):
            probe._canary_base_dir(self.workspace, candidates=[private])

    def test_a_world_traversable_base_outside_the_workspace_is_accepted(self):
        os.chmod(self.base, 0o755)
        self.assertEqual(probe._canary_base_dir(self.workspace, candidates=[self.base]), self.base)


class ProbeProgramTest(unittest.TestCase):
    def test_paths_are_embedded_as_repr_and_not_as_raw_text(self):
        tricky = Path(tempfile.mkdtemp()) / "we'ird\"name"
        self.addCleanup(shutil.rmtree, tricky.parent, True)
        program = probe._probe_program(tricky)
        self.assertIn(repr(str(tricky)), program)
        self.assertNotIn("__HOST_CANARY_PATH__", program)
        self.assertNotIn("__CONTROL_PATH__", program)
        compile(program, "<probe>", "exec")

    def test_a_template_without_the_canary_placeholder_is_refused(self):
        # Otherwise an edit that drops the placeholder yields a probe that reads
        # nothing, and "reads nothing" would look exactly like "was denied".
        original = probe.PROBE_TEMPLATE
        probe.PROBE_TEMPLATE = original.replace("__HOST_CANARY_PATH__", "/tmp/not-the-canary")
        self.addCleanup(setattr, probe, "PROBE_TEMPLATE", original)
        with self.assertRaisesRegex(RuntimeError, "__HOST_CANARY_PATH__"):
            probe._probe_program("/tmp/canary")

    def test_the_probe_actually_reads_something_it_must_see(self):
        # The control path must be inside the mounted workspace, or it proves
        # nothing about the mount being present.
        self.assertTrue(probe.CONTROL_PATH.startswith("/workspace/"))
        self.assertIn(repr(probe.CONTROL_PATH), probe._probe_program("/tmp/canary"))


class DiscoveryTest(unittest.TestCase):
    def test_the_live_probe_module_is_importable_in_ordinary_ci(self):
        # Before the package marker existed, `unittest discover -s tests` never
        # imported this module, so a syntax error would only appear in a live run.
        self.assertTrue(hasattr(probe, "SandboxEscapeTest"))

    def test_the_live_suite_is_skipped_without_the_two_environment_variables(self):
        if probe.LIVE:
            self.skipTest("a live sandbox is configured; the live suite is not skipped")
        self.assertFalse(probe._sandbox_available())


if __name__ == "__main__":
    unittest.main()
