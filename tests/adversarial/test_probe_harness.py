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
import errno
import importlib
import json
from unittest.mock import MagicMock, patch
import os
import shutil
import subprocess
import sys
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
        "secret_marker_present": False,
        "network": "denied:OSError",
        "root_readonly": True,
        "var_tmp_readonly": True,
        "var_tmp_world_writable": True,
        "root_write_errno": errno.EROFS,
        "path:/var/run/docker.sock": "missing",
        "path:/root/.ssh": "inaccessible",
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

    def test_secret_result_is_required_typed_and_detects_exposure(self):
        for value in (None, "false", 0, 1):
            with self.subTest(value=value), self.assertRaisesRegex(AssertionError, "boolean"):
                probe._assert_probe_usable(_good_output(secret_marker_present=value))
        with self.assertRaisesRegex(AssertionError, "synthetic host secret"):
            probe._assert_secret_not_in_environ(_good_output(secret_marker_present=True))
        with self.assertRaisesRegex(AssertionError, "explicitly report"):
            probe._assert_secret_not_in_environ({})
        probe._assert_secret_not_in_environ(_good_output(secret_marker_present=False))

    def test_sensitive_path_states_never_confuse_missing_and_permission_denied(self):
        good = _good_output()
        probe._assert_paths_not_accessible(good)
        for state in ("present", "error:OSError", None, False):
            output = _good_output(**{"path:/root/.ssh": state})
            with self.subTest(state=state):
                with self.assertRaises(AssertionError):
                    probe._assert_paths_not_accessible(output)
                if state != "present":
                    with self.assertRaises(AssertionError):
                        probe._assert_probe_usable(output)
        with self.assertRaises(AssertionError):
            probe._assert_paths_not_accessible({})

    def test_host_egress_positive_control_must_connect(self):
        connection = MagicMock()
        with patch.object(probe.socket, "create_connection", return_value=connection) as connect:
            probe._require_host_network_control()
        connect.assert_called_once_with(("1.1.1.1", 80), timeout=3)
        connection.__enter__.assert_called_once()
        connection.__exit__.assert_called_once()
        with patch.object(probe.socket, "create_connection", side_effect=TimeoutError):
            with self.assertRaisesRegex(AssertionError, "positive control failed"):
                probe._require_host_network_control()

    def test_root_readonly_proof_rejects_permissions_or_missing_observations(self):
        probe._assert_root_read_only(_good_output())
        for key, value in (("root_readonly", False), ("var_tmp_readonly", False),
                           ("var_tmp_world_writable", False), ("root_write_errno", errno.EACCES),
                           ("root_write_errno", errno.ENOENT), ("root_write_errno", 0),
                           ("root_write_errno", None), ("root_write_errno", True)):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(AssertionError, "inconclusive"):
                    probe._assert_root_read_only(_good_output(**{key: value}))
        for key in ("root_readonly", "var_tmp_readonly", "var_tmp_world_writable", "root_write_errno"):
            broken = _good_output()
            del broken[key]
            with self.assertRaises(AssertionError):
                probe._assert_probe_usable(broken)

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

    def test_multiline_program_is_a_valid_newline_free_sandbox_command(self):
        from engine.sandbox_policy import SandboxRequest, assert_sandbox_argv, build_docker_argv

        program = probe._probe_program("/tmp/canary")
        command = probe._python_command(program)
        self.assertTrue(all("\n" not in arg and "\r" not in arg for arg in command))
        request = SandboxRequest(
            image="python@sha256:" + "a" * 64, command=command,
            source_dir=str(Path(__file__).resolve().parents[2]),
        )
        vector = build_docker_argv(request)
        assert_sandbox_argv(vector, expected_source_dir=request.source_dir)
        small = probe._python_command("print('line one')\nprint('line two')\n")
        result = subprocess.run([sys.executable, *small[1:]], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "line one\nline two\n")

    def test_environment_canary_positive_and_negative_controls(self):
        # Execute the actual generated program, but stub network and root writes.
        # Only a synthetic marker is supplied, never real environment contents.
        program = probe._probe_program("/no-such-host-canary", control_path=str(Path(__file__).resolve()))
        wrapper = """
import builtins, errno, os, socket
_original_open = builtins.open
_original_os_open = os.open
def safe_os_open(path, *args, **kwargs):
    if isinstance(path, str) and path.startswith('/var/tmp/raawaa-rootfs-proof-'):
        raise OSError(errno.EROFS, 'disabled by offline test')
    return _original_os_open(path, *args, **kwargs)
os.open = safe_os_open
def safe_open(path, *args, **kwargs):
    if path == '/escaped':
        raise OSError('disabled by offline test')
    return _original_open(path, *args, **kwargs)
def no_network(*args, **kwargs):
    raise OSError('disabled by offline test')
builtins.open = safe_open
socket.create_connection = no_network
exec(PROGRAM)
""".replace("PROGRAM", repr(program))
        for env, expected in (({}, False), ({"SYNTHETIC_CANARY": "prefix-" + probe.SECRET_MARKER}, True)):
            with self.subTest(expected=expected):
                completed = subprocess.run(
                    [sys.executable, "-I", "-c", wrapper], env=env,
                    capture_output=True, text=True, timeout=10, check=True,
                )
                output = json.loads(completed.stdout)
                self.assertIs(output["secret_marker_present"], expected)
                self.assertNotIn(probe.SECRET_MARKER, completed.stdout)
                if expected:
                    with self.assertRaisesRegex(AssertionError, "synthetic host secret"):
                        probe._assert_secret_not_in_environ(output)
                else:
                    probe._assert_secret_not_in_environ(output)

    def test_generated_probe_distinguishes_erofs_from_permission_denial(self):
        program = probe._probe_program("/no-such-host-canary", control_path=str(Path(__file__).resolve().parents[2] / "README.md"))
        wrapper_template = """
import errno, os, socket, stat, types
_original_lstat = os.lstat
_original_open = os.open
def fake_lstat(path, *args, **kwargs):
    if path == '/var/tmp':
        return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o1777)
    return _original_lstat(path, *args, **kwargs)
def fake_statvfs(path):
    return types.SimpleNamespace(f_flag=os.ST_RDONLY)
def fake_open(path, *args, **kwargs):
    if isinstance(path, str) and path.startswith('/var/tmp/raawaa-rootfs-proof-'):
        raise OSError(WRITE_ERRNO, 'synthetic failure')
    return _original_open(path, *args, **kwargs)
def no_network(*args, **kwargs):
    raise OSError('disabled by offline test')
os.lstat = fake_lstat
os.statvfs = fake_statvfs
os.open = fake_open
socket.create_connection = no_network
exec(PROGRAM)
"""
        for code in (errno.EROFS, errno.EACCES):
            with self.subTest(errno=code):
                wrapper = wrapper_template.replace("WRITE_ERRNO", str(code)).replace("PROGRAM", repr(program))
                completed = subprocess.run([sys.executable, "-I", "-c", wrapper], env={},
                                           capture_output=True, text=True, timeout=10, check=True)
                output = json.loads(completed.stdout)
                probe._assert_probe_usable(output)
                self.assertEqual(output["root_write_errno"], code)
                if code == errno.EROFS:
                    probe._assert_root_read_only(output)
                else:
                    with self.assertRaisesRegex(AssertionError, "inconclusive"):
                        probe._assert_root_read_only(output)

    def test_generated_probe_distinguishes_permission_denied_from_missing(self):
        program = probe._probe_program("/no-such-host-canary", control_path=str(Path(__file__).resolve()))
        wrapper = """
import builtins, errno, os, socket
_original_open = builtins.open
_original_os_open = os.open
def safe_os_open(path, *args, **kwargs):
    if isinstance(path, str) and path.startswith('/var/tmp/raawaa-rootfs-proof-'):
        raise OSError(errno.EROFS, 'disabled by offline test')
    return _original_os_open(path, *args, **kwargs)
os.open = safe_os_open
_original_lstat = os.lstat
def safe_open(path, *args, **kwargs):
    if path == '/escaped':
        raise OSError('disabled by offline test')
    return _original_open(path, *args, **kwargs)
def stub_lstat(path, *args, **kwargs):
    if path == '/var/run/docker.sock':
        raise PermissionError('synthetic permission denial')
    if path == '/root/.ssh':
        raise FileNotFoundError('synthetic absent path')
    return _original_lstat(path, *args, **kwargs)
def no_network(*args, **kwargs):
    raise OSError('disabled by offline test')
builtins.open = safe_open
os.lstat = stub_lstat
socket.create_connection = no_network
exec(PROGRAM)
""".replace("PROGRAM", repr(program))
        completed = subprocess.run([sys.executable, "-I", "-c", wrapper], env={},
                                   capture_output=True, text=True, timeout=10, check=True)
        output = json.loads(completed.stdout)
        self.assertEqual(output["path:/var/run/docker.sock"], "inaccessible")
        self.assertEqual(output["path:/root/.ssh"], "missing")
        probe._assert_paths_not_accessible(output)

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
