import json
import unittest
from unittest.mock import patch

from engine.codespace_bridge import execute_task, run_once, TEST_COMMANDS
from engine.remote_task_store import RemoteTaskStoreError
from engine.operating_policy import OperatingPolicyError


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeStore:
    def __init__(self, task=None):
        self.task = task
        self.calls = []
    def claim_next(self):
        self.calls.append("claim")
        return self.task
    def heartbeat(self, task_id, lease_token):
        self.calls.append(("heartbeat", task_id, lease_token))
        return "2999-01-01T00:00:00Z"
    def finish(self, task_id, status, result=None, error=None, lease_token=None):
        self.calls.append(("finish", task_id, status, result, error, lease_token))


class CodespaceBridgeTests(unittest.TestCase):
    def test_only_fixed_test_commands_are_available(self):
        self.assertEqual(set(TEST_COMMANDS), {
            "python_tests", "worker_tests", "workflow_tests"
        })
        result = execute_task({
            "task_type": "codespace_test",
            "payload_json": json.dumps({"operation": "python_tests", "command": "echo unsafe"}),
        }, runner=lambda *a, **k: FakeCompleted(0, "ok"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["operation"], "python_tests")

    def test_arbitrary_command_is_rejected(self):
        result = execute_task({
            "task_type": "codespace_test",
            "payload_json": json.dumps({"operation": "shell", "command": "rm -rf /"}),
        })
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "operation_not_allowlisted")

    def test_task_type_and_operation_must_match(self):
        result = execute_task({
            "task_type": "codespace_inspect",
            "payload_json": json.dumps({"operation": "python_tests"}),
        })
        self.assertEqual(result["status"], "blocked")

    def test_malformed_payload_fails_closed(self):
        result = execute_task({"task_type": "codespace_test", "payload_json": "not-json"})
        self.assertEqual(result["reason"], "invalid_task_payload")

    def test_test_command_uses_no_shell_and_fixed_working_directory(self):
        seen = {}
        def runner(command, **kwargs):
            seen["command"] = command
            seen.update(kwargs)
            return FakeCompleted(0, "all good")
        result = execute_task({
            "task_type": "codespace_test",
            "payload_json": json.dumps({"operation": "worker_tests"}),
        }, runner=runner)
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(seen["shell"])
        self.assertEqual(seen["command"], TEST_COMMANDS["worker_tests"])
        self.assertEqual(seen["timeout"], 240)
        self.assertEqual(set(seen["env"]), {"PATH", "HOME", "USERPROFILE", "CI", "LANG", "PYTHONDONTWRITEBYTECODE"})
        self.assertNotIn("RAAWAA_ENGINE_API_TOKEN", seen["env"])

    def test_timeout_is_reported_as_failure(self):
        import subprocess
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output=b"partial")
        result = execute_task({
            "task_type": "codespace_test",
            "payload_json": json.dumps({"operation": "python_tests"}),
        }, runner=runner)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["timed_out"])

    def test_policy_load_failure_prevents_any_task_claim(self):
        store = FakeStore({"id": "x", "lease_token": "l", "task_type": "codespace_test",
                           "payload_json": '{"operation":"python_tests"}'})
        with patch("engine.codespace_bridge.load_operating_policy", side_effect=OperatingPolicyError("blocked")):
            result = run_once(store)
        self.assertEqual(result["reason"], "operating_policy_unavailable")
        self.assertEqual(store.calls, [])

    def test_successful_task_heartbeats_then_finishes_with_policy_provenance(self):
        task = {"id": "task-1", "lease_token": "lease-1", "task_type": "codespace_test",
                "payload_json": '{"operation":"python_tests"}'}
        store = FakeStore(task)
        policy = {"documents_loaded": ["AGENTS.md"], "fingerprints": {"AGENTS.md": "abc"}}
        with patch("engine.codespace_bridge.load_operating_policy", return_value=policy):
            result = run_once(store, runner=lambda *a, **k: FakeCompleted(0, "tests passed"))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(store.calls[0], "claim")
        self.assertEqual(store.calls[1], ("heartbeat", "task-1", "lease-1"))
        finish = store.calls[2]
        self.assertEqual(finish[0:3], ("finish", "task-1", "succeeded"))
        self.assertEqual(finish[3]["policy_fingerprints"], {"AGENTS.md": "abc"})
        self.assertEqual(finish[3]["external_writes"], 0)


    def test_inspection_uses_only_fixed_git_commands(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return FakeCompleted(0, "abc123\\n" if command[-1] == "HEAD" else "## main\\n")
        result = execute_task({
            "task_type": "codespace_inspect",
            "payload_json": '{"operation":"inspect"}',
        }, runner=runner)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(calls, [["git", "rev-parse", "HEAD"], ["git", "status", "--short", "--branch"]])
        self.assertTrue(result["working_tree_clean"])

    def test_output_is_bounded(self):
        result = execute_task({
            "task_type": "codespace_test",
            "payload_json": '{"operation":"worker_tests"}',
        }, runner=lambda *a, **k: FakeCompleted(1, "x" * 12000))
        self.assertLessEqual(len(result["stdout"]), 8020)
        self.assertIn("output truncated", result["stdout"])

    def test_execution_exception_is_finished_as_blocked(self):
        task = {"id": "task-2", "lease_token": "lease-2", "task_type": "codespace_test",
                "payload_json": '{"operation":"python_tests"}'}
        store = FakeStore(task)
        policy = {"documents_loaded": [], "fingerprints": {}}
        def broken_runner(*args, **kwargs):
            raise OSError("do not expose exception details")
        with patch("engine.codespace_bridge.load_operating_policy", return_value=policy):
            result = run_once(store, runner=broken_runner)
        self.assertEqual(result["status"], "blocked")
        finish = [call for call in store.calls if isinstance(call, tuple) and call[0] == "finish"][0]
        self.assertEqual(finish[2], "blocked")
        self.assertIn("bridge_execution_failed:OSError", finish[3]["reason"])

    def test_lease_loss_never_writes_completion(self):
        class LostStore(FakeStore):
            def heartbeat(self, task_id, lease_token):
                raise RemoteTaskStoreError("lost")
        store = LostStore({"id": "task-1", "lease_token": "lease-1", "task_type": "codespace_test",
                           "payload_json": '{"operation":"python_tests"}'})
        with patch("engine.codespace_bridge.load_operating_policy", return_value={"documents_loaded": [], "fingerprints": {}}):
            result = run_once(store)
        self.assertEqual(result["status"], "lease_lost_or_remote_error")
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "finish" for call in store.calls))


if __name__ == "__main__":
    unittest.main()
