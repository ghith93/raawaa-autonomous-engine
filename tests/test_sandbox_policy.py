"""The #440 contract, tested without a sandbox.

These tests do not prove isolation — nothing run on a workstation proves that. They
prove the thing that *can* be proven offline: that the vector handed to the
executor cannot express a weaker boundary, and that a hand-assembled vector is
refused when it tries. The live adversarial tests live in
``tests/adversarial/test_sandbox_escape.py`` and only run where a sandbox exists.

Unittest rather than pytest on purpose: root CI runs ``unittest discover``, so a
pytest-style file here would be imported and never executed.
"""
import unittest

from engine.sandbox_policy import (
    CEILINGS,
    NON_ROOT_USER,
    SANDBOX_RUNTIME,
    SandboxLimits,
    SandboxPolicyError,
    SandboxRequest,
    assert_sandbox_argv,
    build_docker_argv,
    reviewed_ref_only,
)

IMAGE = "ghcr.io/example/task-runner@sha256:" + "a" * 64


def request(**overrides):
    kwargs = dict(image=IMAGE, command=("python3", "-m", "pytest", "-q"), source_dir="/repo")
    kwargs.update(overrides)
    return SandboxRequest(**kwargs)


def argv(**overrides):
    return build_docker_argv(request(**overrides))


class BoundaryTest(unittest.TestCase):
    def test_the_runtime_is_gvisor_not_the_default_runc(self):
        # A plain container shares the host kernel; the whole point is not to.
        self.assertEqual(SANDBOX_RUNTIME, "runsc")
        self.assertIn(f"--runtime={SANDBOX_RUNTIME}", argv())

    def test_egress_is_denied_by_default(self):
        vector = argv()
        self.assertIn("--network=none", vector)
        self.assertIn("--entrypoint=", vector)
        self.assertFalse([a for a in vector if a.startswith("--network=") and "none" not in a])

    def test_the_root_filesystem_is_read_only_and_writable_paths_are_bounded(self):
        vector = argv()
        self.assertIn("--read-only", vector)
        tmpfs = [a for a in vector if a.startswith("--tmpfs=")]
        self.assertTrue(tmpfs, "a read-only rootfs needs at least one writable scratch path")
        for entry in tmpfs:
            self.assertIn("noexec", entry)
            self.assertIn("nosuid", entry)
            self.assertIn("size=", entry)
        self.assertIn("--tmpfs=/scratch:rw,noexec,nosuid,nodev,size=32m", vector)

    def test_capabilities_are_dropped_and_cannot_be_regained(self):
        vector = argv()
        self.assertIn("--cap-drop=ALL", vector)
        self.assertIn("--security-opt=no-new-privileges", vector)
        self.assertFalse([a for a in vector if a.startswith("--cap-add")])
        self.assertNotIn("--privileged", vector)

    def test_the_task_never_runs_as_root(self):
        vector = argv()
        self.assertIn(f"--user={NON_ROOT_USER}", vector)
        for bad in ("--user=0", "--user=0:0", "--user=root"):
            self.assertNotIn(bad, vector)

    def test_resource_limits_are_always_present(self):
        vector = argv()
        for prefix in ("--pids-limit=", "--memory=", "--memory-swap=", "--cpus=", "--ulimit="):
            self.assertTrue([a for a in vector if a.startswith(prefix)], prefix)
        # Swap must not quietly double the memory ceiling.
        memory = next(a for a in vector if a.startswith("--memory=")).split("=", 1)[1]
        swap = next(a for a in vector if a.startswith("--memory-swap=")).split("=", 1)[1]
        self.assertEqual(memory, swap)

    def test_the_source_is_mounted_read_only_and_is_the_only_host_path(self):
        mounts = [a for a in argv() if a.startswith("--mount=")]
        self.assertEqual(len(mounts), 1)
        self.assertIn("src=/repo", mounts[0])
        self.assertIn("dst=/workspace", mounts[0])
        self.assertIn("readonly", mounts[0])

    def test_no_environment_is_passed_at_all(self):
        vector = argv()
        self.assertFalse([a for a in vector if a.startswith("--env") or a == "-e"])

    def test_the_builder_output_passes_the_independent_check(self):
        self.assertEqual(assert_sandbox_argv(argv(), expected_source_dir="/repo"), argv())


class RefusalTest(unittest.TestCase):
    def test_an_image_tag_is_refused_because_it_is_a_moving_target(self):
        for bad in ("python:3.12", "ghcr.io/example/task-runner:latest",
                    IMAGE.replace("a" * 64, "A" * 64)):
            with self.assertRaisesRegex(SandboxPolicyError, "digest"):
                build_docker_argv(request(image=bad))

    def test_an_empty_or_malformed_command_is_refused(self):
        with self.assertRaisesRegex(SandboxPolicyError, "must not be empty"):
            build_docker_argv(request(command=()))
        with self.assertRaisesRegex(SandboxPolicyError, "non-empty string"):
            build_docker_argv(request(command=("python3", "")))
        with self.assertRaisesRegex(SandboxPolicyError, "NUL or newlines"):
            build_docker_argv(request(command=("python3\nrm -rf /",)))

    def test_a_dangerous_source_directory_is_refused(self):
        for bad in ("/", "", "repo", "/repo/../../etc", "/.."):
            with self.assertRaises(SandboxPolicyError, msg=bad):
                build_docker_argv(request(source_dir=bad))

    def test_limits_above_the_ceiling_are_refused_not_clamped(self):
        # Clamping silently would make the caller believe it got what it asked for.
        cases = (
            (SandboxLimits(memory_mb=CEILINGS["memory_mb"] + 1), "ceiling"),
            (SandboxLimits(pids=CEILINGS["pids"] + 1), "ceiling"),
            (SandboxLimits(wall_seconds=CEILINGS["wall_seconds"] + 1), "wall_seconds"),
            (SandboxLimits(cpus=CEILINGS["cpus"] + 0.5), "cpus"),
        )
        for limits, message in cases:
            with self.assertRaisesRegex(SandboxPolicyError, message):
                build_docker_argv(request(limits=limits))

    def test_non_integer_or_zero_limits_are_refused(self):
        for bad in (0, -1, 1.5, True, "512"):
            with self.assertRaises(SandboxPolicyError, msg=repr(bad)):
                build_docker_argv(request(limits=SandboxLimits(memory_mb=bad)))
        with self.assertRaises(SandboxPolicyError):
            build_docker_argv(request(limits=SandboxLimits(cpus=0)))

    def test_a_hand_built_vector_cannot_weaken_the_contract(self):
        # The realistic failure is not a malicious caller; it is a helpful one.
        cases = (
            ("--privileged", "privileged"),
            ("--cap-add=SYS_ADMIN", "capabilities"),
            ("--device=/dev/kvm", "devices"),
            ("--network=host", "networking"),
            ("--pid=host", "PID"),
            ("--userns=host", "user namespace"),
            ("-v", "volume flags"),
            ("--volume=/var/run/docker.sock:/var/run/docker.sock", "Docker socket"),
            ("--env=AWS_SECRET_ACCESS_KEY=x", "environment"),
            ("-e", "environment"),
            ("--mount=type=bind,src=/root/.ssh,dst=/root/.ssh", "trusted workspace bind mount"),
        )
        for weakener, message in cases:
            vector = argv()
            image_index = vector.index(IMAGE)
            vector.insert(image_index, weakener)
            if weakener in ("-v", "-e"):
                vector.insert(image_index + 1, "/root/.ssh:/root/.ssh")
            with self.assertRaisesRegex(SandboxPolicyError, message, msg=weakener):
                assert_sandbox_argv(vector, expected_source_dir="/repo")

    def test_resource_values_are_checked_not_merely_present(self):
        cases = (
            ("--pids-limit=999999", "pids limit"),
            ("--memory=999999m", "memory limit"),
            ("--cpus=999", "cpus"),
            ("--memory-swap=1024m", "memory-swap"),
        )
        for replacement, message in cases:
            vector = argv()
            for index, value in enumerate(vector):
                if value.startswith(replacement.split("=")[0] + "="):
                    vector[index] = replacement
                    break
            with self.assertRaisesRegex(SandboxPolicyError, message, msg=replacement):
                assert_sandbox_argv(vector, expected_source_dir="/repo")

    def test_extra_read_only_mounts_and_wrong_source_are_refused(self):
        for extra in (
            "--mount=type=bind,src=/root/.ssh,dst=/credentials,readonly",
            "--mount=type=bind,src=/etc,dst=/etc,readonly",
        ):
            with self.assertRaisesRegex(SandboxPolicyError, "trusted workspace bind mount"):
                vector = argv()
                vector.insert(vector.index(IMAGE), extra)
                assert_sandbox_argv(vector, expected_source_dir="/repo")
        with self.assertRaisesRegex(SandboxPolicyError, "trusted workspace bind mount"):
            assert_sandbox_argv(argv(source_dir="/etc"), expected_source_dir="/repo")

    def test_ambiguous_workspace_mount_paths_are_refused(self):
        with self.assertRaisesRegex(SandboxPolicyError, "specific directory"):
            assert_sandbox_argv(argv(), expected_source_dir="/repo,readonly=false")

    def test_duplicate_and_unrecognized_options_are_refused(self):
        for extra in ("--read-only", "--memory=512m", "--foo=bar"):
            with self.assertRaises(SandboxPolicyError, msg=extra):
                vector = argv()
                vector.insert(vector.index(IMAGE), extra)
                assert_sandbox_argv(vector, expected_source_dir="/repo")

    def test_command_options_are_not_reparsed_as_docker_options(self):
        vector = argv(command=("python3", "--version"))
        self.assertEqual(assert_sandbox_argv(vector, expected_source_dir="/repo"), vector)

    def test_a_vector_missing_a_boundary_flag_is_refused(self):
        for flag in (f"--runtime={SANDBOX_RUNTIME}", "--network=none", "--entrypoint=", "--read-only",
                     "--cap-drop=ALL", "--security-opt=no-new-privileges"):
            with self.assertRaisesRegex(SandboxPolicyError, "missing", msg=flag):
                assert_sandbox_argv([a for a in argv() if a != flag], expected_source_dir="/repo")

    def test_a_vector_without_limits_or_a_non_root_user_is_refused(self):
        with self.assertRaisesRegex(SandboxPolicyError, "non-root"):
            assert_sandbox_argv([a for a in argv() if not a.startswith("--user=")], expected_source_dir="/repo")
        with self.assertRaisesRegex(SandboxPolicyError, "not run as root"):
            root_vector = [a for a in argv() if a != f"--user={NON_ROOT_USER}"]
            root_vector.insert(root_vector.index(IMAGE), "--user=root")
            assert_sandbox_argv(root_vector, expected_source_dir="/repo")
        for prefix in ("--pids-limit=", "--memory=", "--cpus="):
            with self.assertRaisesRegex(SandboxPolicyError, "missing", msg=prefix):
                assert_sandbox_argv([a for a in argv() if not a.startswith(prefix)], expected_source_dir="/repo")

    def test_a_vector_that_does_not_start_with_docker_run_is_refused(self):
        for bad in ([], ["docker"], ["sudo", "docker", "run"], "docker run", ["docker", "exec"]):
            with self.assertRaises(SandboxPolicyError, msg=repr(bad)):
                assert_sandbox_argv(bad, expected_source_dir="/repo")


class ReviewedRefTest(unittest.TestCase):
    def test_only_reviewed_main_may_execute_task_code(self):
        self.assertEqual(reviewed_ref_only("refs/heads/main"), "/workspace")
        for ref in ("refs/pull/427/merge", "refs/heads/feature", "main", "refs/heads/main-evil"):
            with self.assertRaisesRegex(SandboxPolicyError, "reviewed", msg=ref):
                reviewed_ref_only(ref)

    def test_the_pr_head_escape_hatch_stays_narrow(self):
        self.assertEqual(reviewed_ref_only("refs/pull/12/merge", allow_pr_heads=True), "/workspace")
        with self.assertRaisesRegex(SandboxPolicyError, "refs/pull"):
            reviewed_ref_only("refs/heads/feature", allow_pr_heads=True)


if __name__ == "__main__":
    unittest.main()
