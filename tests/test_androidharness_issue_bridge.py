from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.androidharness_issue_bridge import (
    TASK_MARKER,
    TASK_PREFIX,
    AndroidHarnessIssueBridge,
    BridgeError,
    bounded_text,
    is_candidate_issue,
    parse_task,
    safe_extract_git_archive,
    minimal_python_environment,
    summarize_unittest_output,
    verify_policy_files,
    verify_repository_root,
    fetch_origin_main,
)


COMMIT = "a" * 40


def task_body(task):
    fence = chr(96) * 3
    return TASK_MARKER + "\n" + fence + "json\n" + json.dumps(task) + "\n" + fence


class AndroidHarnessBridgeTests(unittest.TestCase):
    def test_parses_only_allowlisted_operations_and_bounded_limits(self):
        parsed = parse_task(task_body({
            "schema_version": 1,
            "operation": "run_python_tests",
            "commit_sha": COMMIT,
            "timeout_seconds": 300,
            "max_output_bytes": 4096,
        }))
        self.assertEqual(parsed["operation"], "run_python_tests")
        self.assertEqual(parsed["commit_sha"], COMMIT)
        doctor_task = parse_task(task_body({"schema_version": 1, "operation": "run_androidharness_doctor"}))
        self.assertEqual(doctor_task["operation"], "run_androidharness_doctor")
        with self.assertRaisesRegex(BridgeError, "allowlisted"):
            parse_task(task_body({"schema_version": 1, "operation": "run_shell"}))
        with self.assertRaisesRegex(BridgeError, "unsupported fields"):
            parse_task(task_body({"schema_version": 1, "operation": "git_status", "command": "id"}))
        with self.assertRaisesRegex(BridgeError, "full 40-character"):
            parse_task(task_body({"schema_version": 1, "operation": "run_python_tests", "commit_sha": "main"}))
        with self.assertRaisesRegex(BridgeError, "allowlisted"):
            parse_task(task_body({"schema_version": 1, "operation": ["git_status"]}))
        with self.assertRaisesRegex(BridgeError, "unsupported task schema"):
            parse_task(task_body({"schema_version": True, "operation": "git_status"}))
        duplicate = TASK_MARKER + "\n" + chr(96) * 3 + "json\n" + (
            '{"schema_version":1,"schema_version":1,"operation":"git_status"}'
        ) + "\n" + chr(96) * 3
        with self.assertRaisesRegex(BridgeError, "duplicate keys"):
            parse_task(duplicate)


    def test_unittest_summary_exposes_only_aggregate_counts(self):
        passed = summarize_unittest_output("Ran 42 tests in 1.234s\n\nOK\n")
        self.assertEqual(passed, {
            "tests_run": 42,
            "status": "passed",
            "failure_summary": None,
        })
        failed = summarize_unittest_output(
            "Ran 42 tests in 1.234s\n\nFAILED (failures=2, errors=1)\n"
        )
        self.assertEqual(failed["tests_run"], 42)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_summary"], "failures=2, errors=1")
        self.assertNotIn("traceback", json.dumps(failed).lower())

    def test_target_policy_files_are_verified_from_the_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "AGENTS.md",
                "docs/master-autonomous-execution-directive.ar.md",
                "docs/continuous-improvement-charter.md",
                "docs/architecture.md",
                "docs/operations.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("required policy\n", encoding="utf-8")
            fingerprints = verify_policy_files(root)
            self.assertEqual(len(fingerprints), 5)
            (root / "docs/operations.md").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(BridgeError, "empty"):
                verify_policy_files(root)

    def test_fetch_origin_main_retries_transient_failures_with_bounded_backoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            outputs = [
                SimpleNamespace(returncode=1, stdout="", stderr="temporary network failure"),
                SimpleNamespace(returncode=1, stdout="", stderr="temporary network failure"),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout=COMMIT + "\\n", stderr=""),
            ]
            with patch("scripts.androidharness_issue_bridge.subprocess.run", side_effect=outputs) as run, \\
                 patch("scripts.androidharness_issue_bridge.time.sleep") as sleep:
                fetched = fetch_origin_main(Path(temporary))
            self.assertEqual(fetched, COMMIT)
            self.assertEqual(run.call_count, 4)
            self.assertEqual([call.args[0][0:2] for call in run.call_args_list[:3]],
                             [["git", "-C"], ["git", "-C"], ["git", "-C"]])
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    def test_fetch_origin_main_stops_after_bounded_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            failure = SimpleNamespace(returncode=1, stdout="", stderr="network unavailable")
            with patch("scripts.androidharness_issue_bridge.subprocess.run", return_value=failure) as run, \\
                 patch("scripts.androidharness_issue_bridge.time.sleep") as sleep:
                with self.assertRaisesRegex(BridgeError, "after 3 attempts"):
                    fetch_origin_main(Path(temporary))
            self.assertEqual(run.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    def test_bridge_requires_the_approved_repository_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            outputs = [
                SimpleNamespace(returncode=0, stdout=str(root), stderr=""),
                SimpleNamespace(returncode=0, stdout="https://github.com/ghith93/raawaa-autonomous-engine.git", stderr=""),
            ]
            with patch("scripts.androidharness_issue_bridge.subprocess.run", side_effect=outputs):
                verify_repository_root(root)
            outputs = [
                SimpleNamespace(returncode=0, stdout=str(root), stderr=""),
                SimpleNamespace(returncode=0, stdout="https://github.com/someone-else/fork.git", stderr=""),
            ]
            with patch("scripts.androidharness_issue_bridge.subprocess.run", side_effect=outputs):
                with self.assertRaisesRegex(BridgeError, "origin does not match"):
                    verify_repository_root(root)


    def test_read_only_androidharness_doctor_is_exposed_with_safety_assertions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doctor = root / "scripts" / "androidharness" / "doctor.py"
            doctor.parent.mkdir(parents=True)
            doctor.write_text("# read-only test fixture")
            report = {
                "schema_version": 1,
                "workspace": {"root": str(root)},
                "mutations_performed": False,
                "network_requests_performed": False,
                "secret_values_emitted": False,
                "tools": {"python": {"available": True}},
            }
            completed = SimpleNamespace(returncode=0, stdout=json.dumps(report), stderr="")
            bridge = AndroidHarnessIssueBridge(root=root)
            with patch("scripts.androidharness_issue_bridge.subprocess.run", return_value=completed) as run:
                result = bridge._execute({"operation": "run_androidharness_doctor", "max_output_bytes": 4096})
            self.assertEqual(result["exit_code"], 0)
            self.assertIn('"secret_values_emitted": false', result["output"])
            self.assertEqual(run.call_args.args[0][-1], "--json")
            self.assertEqual(run.call_args.kwargs["timeout"], 120)

            unsafe = {**report, "network_requests_performed": True}
            with patch(
                "scripts.androidharness_issue_bridge.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(unsafe), stderr=""),
            ):
                with self.assertRaisesRegex(BridgeError, "safety contract"):
                    bridge._execute({"operation": "run_androidharness_doctor", "max_output_bytes": 4096})

    def test_candidate_issue_requires_owner_prefix_and_open_issue(self):
        issue = {
            "number": 12,
            "state": "open",
            "title": TASK_PREFIX + " test run",
            "user": {"login": "ghith93"},
        }
        self.assertTrue(is_candidate_issue(issue))
        self.assertFalse(is_candidate_issue({**issue, "user": {"login": "someone-else"}}))
        self.assertFalse(is_candidate_issue({**issue, "pull_request": {"url": "https://example.invalid"}}))
        self.assertFalse(is_candidate_issue({**issue, "state": "closed"}))

    def test_safe_archive_extracts_regular_files_and_rejects_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "safe"
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                data = b"print('safe')\n"
                member = tarfile.TarInfo("tests/test_safe.py")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            safe_extract_git_archive(buffer.getvalue(), root)
            self.assertEqual((root / "tests/test_safe.py").read_bytes(), b"print('safe')\n")

        for member_name, member_type, linkname in [
            ("../escape.txt", tarfile.REGTYPE, ""),
            ("outside-link", tarfile.SYMTYPE, "/etc/passwd"),
        ]:
            with self.subTest(member_name=member_name), tempfile.TemporaryDirectory() as temporary:
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as archive:
                    member = tarfile.TarInfo(member_name)
                    member.type = member_type
                    member.linkname = linkname
                    if member_type == tarfile.REGTYPE:
                        member.size = 0
                    archive.addfile(member, io.BytesIO(b""))
                with self.assertRaises(BridgeError):
                    safe_extract_git_archive(buffer.getvalue(), Path(temporary) / "extract")

    def test_test_environment_excludes_provider_and_cloud_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {
                "PATH": "/usr/bin",
                "HOME": temporary,
                "OPENROUTER_API_KEY": "secret-value",
                "CLOUDFLARE_API_TOKEN": "another-secret",
            }, clear=True):
                env = minimal_python_environment(Path(temporary))
            self.assertEqual(env["PATH"], "/usr/bin")
            self.assertNotIn("OPENROUTER_API_KEY", env)
            self.assertNotIn("CLOUDFLARE_API_TOKEN", env)

    def test_output_is_bounded_and_token_patterns_are_redacted(self):
        output = "prefix ghp_" + ("x" * 40) + " suffix " + ("z" * 2000)
        safe = bounded_text(output, 1024)
        self.assertIn("[REDACTED]", safe)
        self.assertLessEqual(len(safe.encode()), 1024)

    def test_run_once_claims_executes_comments_and_closes_only_success(self):
        class FakeBridge(AndroidHarnessIssueBridge):
            def __init__(self, root):
                super().__init__(root=root, audit_path=Path(root) / "audit.jsonl")
                self.calls = []
                self.audit_records = []
                self.issue = {
                    "number": 123,
                    "state": "open",
                    "title": TASK_PREFIX + " python tests",
                    "body": task_body({"schema_version": 1, "operation": "git_status"}),
                    "user": {"login": "ghith93"},
                }

            def _api(self, method, endpoint, body=None):
                if method == "GET":
                    return [self.issue]
                self.calls.append((method, endpoint, body))
                return {}

            def _audit(self, record):
                self.audit_records.append(record)

            def _execute(self, task):
                return {"exit_code": 0, "branch": "main", "working_tree_clean": True}

        with tempfile.TemporaryDirectory() as temporary:
            bridge = FakeBridge(Path(temporary))
            with patch("scripts.androidharness_issue_bridge.verify_policy_files", return_value={"AGENTS.md": "a" * 64}):
                self.assertTrue(bridge.run_once())
            methods = [(method, endpoint, body) for method, endpoint, body in bridge.calls]
            titles = [body["title"] for method, endpoint, body in methods if method == "PATCH" and "title" in body]
            self.assertEqual(titles[0].split("]")[0] + "]", "[AndroidHarness Running]")
            self.assertEqual(titles[1].split("]")[0] + "]", "[AndroidHarness Complete]")
            self.assertTrue(any(body == {"state": "closed"} for _, _, body in methods))
            self.assertEqual(bridge.audit_records[0]["status"], "claimed")
            self.assertEqual(bridge.audit_records[1]["status"], "completed")


    def test_single_instance_lock_prevents_duplicate_pollers(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "private" / "bridge.jsonl"
            first = AndroidHarnessIssueBridge(audit_path=audit)
            second = AndroidHarnessIssueBridge(audit_path=audit)
            descriptor = first.acquire_single_instance_lock()
            try:
                with self.assertRaisesRegex(BridgeError, "already running"):
                    second.acquire_single_instance_lock()
            finally:
                os.close(descriptor)
            descriptor = second.acquire_single_instance_lock()
            os.close(descriptor)

    def test_missing_policy_fails_closed_without_claiming_task(self):
        class FakeBridge(AndroidHarnessIssueBridge):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.issue = {
                    "number": 124,
                    "state": "open",
                    "title": TASK_PREFIX + " should remain pending",
                    "body": task_body({"schema_version": 1, "operation": "git_status"}),
                    "user": {"login": "ghith93"},
                }

            def _api(self, method, endpoint, body=None):
                if method == "GET":
                    return [self.issue]
                self.calls.append((method, endpoint, body))
                return {}

        bridge = FakeBridge()
        with patch("scripts.androidharness_issue_bridge.verify_policy_files", side_effect=BridgeError("policy missing")):
            with self.assertRaisesRegex(BridgeError, "policy missing"):
                bridge.run_once()
        self.assertEqual(bridge.calls, [])


if __name__ == "__main__":
    unittest.main()
