import os
import tempfile
import unittest
from unittest.mock import Mock

from engine.task_store import TaskStore
from engine.worker import run_once


class FakeResponse:
    provider = "groq"
    model = "verified-test-model"
    text = "A bounded specialist result."
    input_tokens = 12
    output_tokens = 8


class WorkerExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(os.path.join(self.temp.name, "tasks.db"))
        self.task = self.store.enqueue(
            "research_brief", "Research the requested topic",
            {"topic": "test topic", "constraints": ["no external writes"]},
            idempotency_key="worker-live-test",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_default_path_is_dry_run_and_never_calls_gateway(self):
        gateway = Mock()
        result = run_once(self.store, live_gateway=gateway, environ={})
        self.assertEqual(result["mode"], "dry_run")
        gateway.generate.assert_not_called()
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["result_json"] and __import__("json").loads(saved["result_json"])["provider_calls"], 0)

    def test_live_path_passes_task_id_and_persists_completion_under_lease(self):
        gateway = Mock()
        gateway.generate.return_value = FakeResponse()
        result = run_once(
            self.store, live_gateway=gateway,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.assertEqual(result["mode"], "live_inference")
        request = gateway.generate.call_args.args[0]
        self.assertEqual(request.task_id, self.task["id"])
        self.assertEqual(request.task_kind, "research")
        self.assertIn("test topic", request.prompt)
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "succeeded")
        import json
        stored = json.loads(saved["result_json"])
        self.assertEqual(stored["output"], FakeResponse.text)
        self.assertEqual(stored["external_writes"], 0)

    def test_live_switch_without_gateway_blocks_instead_of_silent_dry_run(self):
        result = run_once(
            self.store, environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "live_gateway_unavailable")
        self.assertEqual(self.store.list_tasks(1)[0]["status"], "blocked")

    def test_live_failure_is_blocked_and_never_auto_retried(self):
        gateway = Mock()
        gateway.generate.side_effect = TimeoutError("ambiguous provider outcome")
        result = run_once(
            self.store, live_gateway=gateway,
            environ={"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "live_inference_failed_no_replay")
        saved = self.store.list_tasks(1)[0]
        self.assertEqual(saved["status"], "blocked")
        self.assertEqual(saved["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
