import json
import os
import unittest
from unittest.mock import patch

from engine.codespace_task_store import CodespaceTaskStore
from engine.remote_task_store import RemoteTaskStoreError


class FakeResponse:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return json.dumps(self.value).encode()


class CodespaceTaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "RAAWAA_ENGINE_API_TOKEN": "general-token",
            "RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN": "admin-token",
        })
        self.env.start()
    def tearDown(self):
        self.env.stop()

    def test_requires_a_distinct_codespace_token(self):
        with self.assertRaisesRegex(ValueError, "distinct scoped token"):
            CodespaceTaskStore("https://engine.example", "general-token")
        with self.assertRaisesRegex(ValueError, "distinct scoped token"):
            CodespaceTaskStore("https://engine.example", "admin-token")

    @patch("engine.remote_task_store.urlopen")
    def test_preflight_uses_scoped_read_only_route(self, open_url):
        open_url.return_value = FakeResponse({
            "ok": True, "executor": "codespace", "task_types": ["codespace_inspect", "codespace_test"],
        })
        store = CodespaceTaskStore("https://engine.example", "codespace-token")
        self.assertEqual(store.preflight()["executor"], "codespace")
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/codespace/status")
        self.assertEqual(request.get_header("Authorization"), "Bearer codespace-token")

    @patch("engine.remote_task_store.urlopen")
    def test_claim_uses_only_codespace_endpoint_and_validates_task_type(self, open_url):
        open_url.return_value = FakeResponse({
            "task": {"id": "t1", "task_type": "codespace_test"},
            "lease_token": "lease-1",
        })
        store = CodespaceTaskStore("https://engine.example", "codespace-token")
        task = store.claim_next()
        self.assertEqual(task["lease_token"], "lease-1")
        self.assertEqual(open_url.call_args.args[0].full_url,
                         "https://engine.example/v1/codespace/tasks/claim")
        open_url.return_value = FakeResponse({
            "task": {"id": "t2", "task_type": "research_brief"},
            "lease_token": "lease-2",
        })
        with self.assertRaisesRegex(RemoteTaskStoreError, "out-of-scope"):
            store.claim_next()

    @patch("engine.remote_task_store.urlopen")
    def test_heartbeat_and_finish_use_codespace_scoped_paths(self, open_url):
        store = CodespaceTaskStore("https://engine.example", "codespace-token")
        open_url.return_value = FakeResponse({"lease_expires_at": "2999-01-01T00:00:00Z"})
        store.heartbeat("t1", "lease-1")
        self.assertEqual(open_url.call_args.args[0].full_url,
                         "https://engine.example/v1/codespace/tasks/t1/heartbeat")
        open_url.return_value = FakeResponse({"ok": True, "status": "succeeded"})
        store.finish("t1", "succeeded", {"external_writes": 0}, lease_token="lease-1")
        self.assertEqual(open_url.call_args.args[0].full_url,
                         "https://engine.example/v1/codespace/tasks/t1/finish")
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(payload["lease_token"], "lease-1")


if __name__ == "__main__":
    unittest.main()
