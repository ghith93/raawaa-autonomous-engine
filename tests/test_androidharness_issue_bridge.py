from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
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
    safe_python_environment,
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
        with self.assertRaisesRegex(BridgeError, "allowlisted"):
            parse_task(task_body({"schema_version": 1, "operation": "run_shell", "command": "id"}))
        with self.assertRaisesRegex(BridgeError, "unsupported fields"):
            parse_task(task_body({"schema_version": 1, "operation": "git_status", "command": "id"}))
        with self.assertRaisesRegex(BridgeError, "full 40-character"):
            parse_task(task_body({"schema_version": 1, "operation": "run_python_tests", "commit_sha": "main"}))
        with self.assertRaisesRegex(BridgeError, "allowlisted"):
            parse_task(task_body({"schema_version": 1, "operation": ["git_status"]}))
        with self.assertRaisesRegex(BridgeError, "unsupported schema"):
            parse_task(task_body({"schema_version": True, "operation": "git_status"}))
        duplicate = TASK_MARKER + "\\n" + chr(96) * 3 + "json\\n" + (
            '{"schema_version":1,"schema_version":1,"operation":"git_status"}'
        ) + "\\n" + chr(96) * 3
        with self.assertRaisesRegex(BridgeError, "duplicate keys"):
            parse_task(duplicate)

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
                env = safe_python_environment(Path(temporary))
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
