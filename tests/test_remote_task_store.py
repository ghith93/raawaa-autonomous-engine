import json
import os
import unittest
from unittest.mock import patch

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError


class FakeResponse:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return json.dumps(self.value).encode()


class RemoteTaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = RemoteTaskStore("https://engine.example", "secret-test-token")

    @patch("engine.remote_task_store.urlopen")
    def test_audit_events_reads_bounded_task_filtered_rows(self, open_url):
        open_url.return_value = FakeResponse({"events": [
            {"id": 9, "task_id": "task-1", "event": "task_succeeded",
             "details": {"status": "succeeded"}, "created_at": "2026-11-05T00:00:02.000Z"},
        ]})
        events = self.store.audit_events(limit=500, task_id="task-1")
        self.assertEqual(events[0]["event"], "task_succeeded")
        request = open_url.call_args.args[0]
        # Limit is clamped to the Worker's 100-row bound.
        self.assertEqual(request.full_url, "https://engine.example/v1/audit?limit=100&task_id=task-1")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-test-token")

    def test_audit_events_rejects_unsafe_task_id(self):
        with self.assertRaises(ValueError):
            self.store.audit_events(task_id="bad id")

    @patch("engine.remote_task_store.urlopen")
    def test_audit_events_rejects_malformed_payload(self, open_url):
        open_url.return_value = FakeResponse({"events": "not-a-list"})
        with self.assertRaises(RemoteTaskStoreError):
            self.store.audit_events()

    @patch("engine.remote_task_store.urlopen")
    def test_claim_returns_server_lease_token(self, open_url):
        open_url.return_value = FakeResponse({
            "task": {"id": "task-1", "payload_json": "{}", "status": "running"},
            "lease_token": "lease-1",
        })
        task = self.store.claim_next(execution_mode="dry_run")
        self.assertEqual(task["lease_token"], "lease-1")
        self.assertEqual(json.loads(open_url.call_args.args[0].data), {"execution_mode": "dry_run", "worker_lane": "general"})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/tasks/claim")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-test-token")
        self.assertEqual(request.get_header("User-agent"), "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)")
        self.assertIsNone(request.get_header("Cf-access-client-id"))

    @patch("engine.remote_task_store.urlopen")
    def test_claim_supervised_probe_targets_one_dry_run_task(self, open_url):
        open_url.return_value = FakeResponse({
            "task": {"id": "probe-123", "task_type": "supervised_probe",
                     "status": "running", "execution_mode": "dry_run"},
            "lease_token": "lease-123",
        })
        task = self.store.claim_supervised_probe("probe-123")
        self.assertEqual(task["lease_token"], "lease-123")
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://engine.example/v1/tasks/probe-123/claim-supervised-probe")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {})

    @patch("engine.remote_task_store.urlopen")
    def test_recover_supervised_probe_targets_only_the_exact_task_route(self, open_url):
        open_url.return_value = FakeResponse({"status": "pending", "recovered": True})
        result = self.store.recover_supervised_probe("probe-123")
        self.assertEqual(result, {"status": "pending", "recovered": True})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://engine.example/v1/tasks/probe-123/recover-supervised-probe")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {})

    def test_recover_supervised_probe_rejects_unsafe_task_ids_before_network(self):
        for task_id in ("", "../tasks", "has space", "x" * 129, None):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                self.store.recover_supervised_probe(task_id)

    @patch("engine.remote_task_store.urlopen")
    def test_recover_supervised_probe_rejects_invalid_response(self, open_url):
        for result in ({"status": "running"}, {"status": "pending", "recovered": "yes"}):
            open_url.return_value = FakeResponse(result)
            with self.subTest(result=result), self.assertRaises(RemoteTaskStoreError):
                self.store.recover_supervised_probe("probe-123")

    def test_claim_supervised_probe_rejects_unsafe_task_ids_before_network(self):
        for task_id in ("", "../tasks", "has space", "x" * 129, None):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                self.store.claim_supervised_probe(task_id)

    @patch("engine.remote_task_store.urlopen")
    def test_claim_supervised_probe_fails_closed_on_wrong_task(self, open_url):
        open_url.return_value = FakeResponse({
            "task": {"id": "other", "task_type": "system_audit",
                     "status": "running", "execution_mode": "dry_run"},
            "lease_token": "lease-123",
        })
        with self.assertRaisesRegex(RemoteTaskStoreError, "invalid supervised-probe lease"):
            self.store.claim_supervised_probe("probe-123")

    @patch("engine.remote_task_store.urlopen")
    def test_empty_supervised_probe_claim_returns_none(self, open_url):
        open_url.return_value = FakeResponse({"task": None})
        self.assertIsNone(self.store.claim_supervised_probe("probe-123"))

    @patch("engine.remote_task_store.urlopen")
    def test_enqueue_sends_priority_and_dependencies(self, open_url):
        open_url.return_value = FakeResponse({"task": {"id": "child"}})
        task = self.store.enqueue(
            "content_draft", "Child", idempotency_key="child-key",
            priority=85, depends_on=["parent-b", "parent-a"],
        )
        self.assertEqual(task["id"], "child")
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(payload["priority"], 85)
        self.assertEqual(payload["depends_on"], ["parent-a", "parent-b"])

    def test_enqueue_rejects_invalid_priority_and_dependencies(self):
        with self.assertRaises(ValueError):
            self.store.enqueue("research_brief", "Bad priority", priority=101)
        with self.assertRaises(ValueError):
            self.store.enqueue("research_brief", "Bad dependencies", depends_on=["x", "x"])
        with self.assertRaises(ValueError):
            self.store.enqueue("research_brief", "Blank dependency", depends_on=["   "])

    @patch("engine.remote_task_store.urlopen")
    def test_optional_cloudflare_access_service_token_headers(self, open_url):
        open_url.return_value = FakeResponse({"task": None})
        store = RemoteTaskStore(
            "https://engine.example", "app-bearer",
            access_client_id="access-client-id",
            access_client_secret="access-client-secret",
        )
        self.assertIsNone(store.claim_next())
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Cf-access-client-id"), "access-client-id")
        self.assertEqual(request.get_header("Cf-access-client-secret"), "access-client-secret")
        self.assertEqual(request.get_header("Authorization"), "Bearer app-bearer")

    def test_cloudflare_access_credentials_must_be_paired(self):
        with self.assertRaisesRegex(ValueError, "both service-token credentials"):
            RemoteTaskStore(
                "https://engine.example", "app-bearer",
                access_client_id="access-client-id",
            )

    @patch("engine.remote_task_store.urlopen")
    def test_empty_claim_returns_none(self, open_url):
        open_url.return_value = FakeResponse({"task": None})
        self.assertIsNone(self.store.claim_next())

    @patch("engine.remote_task_store.urlopen")
    def test_finish_sends_lease_and_terminal_result(self, open_url):
        open_url.return_value = FakeResponse({"ok": True, "status": "succeeded"})
        self.store.finish("task-1", "succeeded", {"safe": True, "mode": "dry_run"}, lease_token="lease-1")
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(payload["execution_mode"], "dry_run")
        self.assertEqual(payload["lease_token"], "lease-1")
        self.assertEqual(payload["result"], {"safe": True, "mode": "dry_run"})

    @patch("engine.remote_task_store.urlopen")
    def test_finish_rejects_not_started_execution_mode(self, open_url):
        with self.assertRaisesRegex(ValueError, "invalid execution_mode"):
            self.store.finish(
                "task-1", "succeeded", {"safe": True},
                lease_token="lease-1", execution_mode="not_started",
            )
        open_url.assert_not_called()

    @patch("engine.remote_task_store.urlopen")
    def test_recovery_is_bounded_and_validated(self, open_url):
        open_url.return_value = FakeResponse({"requeued": 2, "failed": 1})
        self.assertEqual(self.store.recover_stale(), {"requeued": 2, "failed": 1})
        self.assertEqual(open_url.call_args.args[0].full_url,
                         "https://engine.example/v1/tasks/recover")

    @patch("engine.remote_task_store.urlopen")
    def test_specialist_recovery_sends_lane_capability(self, open_url):
        self.store.lane_token = "researcher-lane-token-0123456789abcdef0123456789"
        open_url.return_value = FakeResponse({"requeued": 0, "failed": 0})
        self.store.recover_stale(worker_lane="specialist:researcher")
        request = open_url.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"worker_lane": "specialist:researcher"})
        self.assertEqual(request.get_header("X-engine-lane-token"), self.store.lane_token)

    def test_requires_https_and_token(self):
        with self.assertRaises(ValueError):
            RemoteTaskStore("http://engine.example", "token")
        with self.assertRaises(ValueError):
            RemoteTaskStore("https://engine.example", "")

    def test_forbidden_error_includes_safe_cloudflare_metadata_only(self):
        from urllib.error import HTTPError
        from email.message import Message
        headers = Message()
        headers["CF-Ray"] = "abc123-IAD"
        headers["cf-mitigated"] = "challenge"
        error = HTTPError("https://engine.example/v1/tasks/recover", 403,
                          "forbidden", headers, None)
        with patch("engine.remote_task_store.urlopen", side_effect=error):
            with self.assertRaisesRegex(RemoteTaskStoreError, "HTTP 403") as caught:
                self.store.recover_stale()
        message = str(caught.exception)
        self.assertIn("CF-Ray abc123-IAD", message)
        self.assertIn("cf-mitigated challenge", message)
        self.assertNotIn("secret-test-token", message)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertFalse(caught.exception.retryable)

    @patch("engine.remote_task_store.urlopen")
    def test_specialist_task_readback_sends_lane_capability(self, open_url):
        self.store.lane_token = "researcher-lane-token-0123456789abcdef0123456789"
        open_url.return_value = FakeResponse({"tasks": [{"id": "research-1"}]})
        tasks = self.store.list_tasks(25, worker_lane="specialist:researcher")
        request = open_url.call_args.args[0]
        self.assertEqual(tasks, [{"id": "research-1"}])
        self.assertTrue(request.full_url.endswith("&worker_lane=specialist:researcher"))
        self.assertEqual(request.get_header("X-engine-lane-token"), self.store.lane_token)

    def test_rate_limit_has_safe_retry_later_error(self):
        from urllib.error import HTTPError
        with patch("engine.remote_task_store.urlopen",
                   side_effect=HTTPError("https://engine.example/v1/tasks", 429,
                                         "limited", {}, None)):
            with self.assertRaisesRegex(RemoteTaskStoreError, "rate limit") as caught:
                self.store.list_tasks()
            self.assertNotIn("secret-test-token", str(caught.exception))
            self.assertEqual(caught.exception.status_code, 429)
            self.assertTrue(caught.exception.retryable)

    def test_server_error_is_classified_as_temporary(self):
        from urllib.error import HTTPError
        with patch("engine.remote_task_store.urlopen",
                   side_effect=HTTPError("https://engine.example/v1/tasks", 503,
                                         "unavailable", {}, None)):
            with self.assertRaisesRegex(RemoteTaskStoreError, "temporarily unavailable") as caught:
                self.store.list_tasks()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertTrue(caught.exception.retryable)

    def test_cloudflare_quota_1027_is_retryable(self):
        from urllib.error import HTTPError
        with patch("engine.remote_task_store.urlopen",
                   side_effect=HTTPError("https://engine.example/v1/tasks", 1027,
                                         "quota exceeded", {}, None)):
            with self.assertRaises(RemoteTaskStoreError) as caught:
                self.store.list_tasks()
        self.assertEqual(caught.exception.status_code, 1027)
        self.assertTrue(caught.exception.retryable)

    def test_server_error_carries_the_worker_reason(self):
        """A 503 without its reason is undiagnosable; the body must survive."""
        import io
        from urllib.error import HTTPError

        body = io.BytesIO(json.dumps({
            "error": "storage_unavailable",
            "detail": "D1_ERROR: too many SQL variables: SQLITE_ERROR",
        }).encode("utf-8"))
        with patch("engine.remote_task_store.urlopen",
                   side_effect=HTTPError("https://engine.example/v1/tasks/claim", 503,
                                         "unavailable", {}, body)):
            with self.assertRaises(RemoteTaskStoreError) as caught:
                self.store.claim_next()
        message = str(caught.exception)
        self.assertIn("HTTP 503", message)
        self.assertIn("storage_unavailable", message)
        self.assertIn("D1_ERROR", message)
        self.assertNotIn("secret-test-token", message)

    def test_error_detail_is_bounded_and_flattened(self):
        import io
        from urllib.error import HTTPError

        body = io.BytesIO(json.dumps({
            "error": "storage_unavailable",
            "detail": "line one\nline two " + "x" * 5000,
        }).encode("utf-8"))
        with patch("engine.remote_task_store.urlopen",
                   side_effect=HTTPError("https://engine.example/v1/tasks/claim", 503,
                                         "unavailable", {}, body)):
            with self.assertRaises(RemoteTaskStoreError) as caught:
                self.store.claim_next()
        message = str(caught.exception)
        self.assertNotIn("\n", message)
        self.assertLess(len(message), 900)

    def test_server_error_without_a_body_stays_generic(self):
        from urllib.error import HTTPError
        with patch("engine.remote_task_store.urlopen",
                   side_effect=HTTPError("https://engine.example/v1/tasks/claim", 503,
                                         "unavailable", {}, None)):
            with self.assertRaises(RemoteTaskStoreError) as caught:
                self.store.claim_next()
        self.assertEqual(
            str(caught.exception),
            "Remote task API temporarily unavailable (HTTP 503)",
        )

    def test_transport_oserror_is_sanitized(self):
        with patch("engine.remote_task_store.urlopen", side_effect=OSError("socket detail")):
            with self.assertRaisesRegex(RemoteTaskStoreError, "unavailable") as caught:
                self.store.list_tasks()
            self.assertNotIn("socket detail", str(caught.exception))
            self.assertTrue(caught.exception.retryable)
            self.assertIsNone(caught.exception.status_code)

    def test_rejects_invalid_remote_lease_timeout(self):
        with self.assertRaises(ValueError):
            self.store.claim_next(9000)


    @patch("engine.remote_task_store.urlopen")
    def test_specialist_claim_sends_lane_identity_and_capability_token(self, open_url):
        self.store.lane_token = "lane-token-" + "x" * 40
        open_url.return_value = FakeResponse({
            "task": {"id": "task-specialist", "execution_lane": "specialist:researcher",
                     "status": "running"},
            "lease_token": "lease-specialist",
        })
        task = self.store.claim_next(execution_mode="dry_run",
                                     worker_lane="specialist:researcher")
        request = open_url.call_args.args[0]
        self.assertEqual(task["execution_lane"], "specialist:researcher")
        self.assertEqual(json.loads(request.data), {
            "execution_mode": "dry_run", "worker_lane": "specialist:researcher"
        })
        self.assertEqual(request.get_header("X-engine-lane-token"), self.store.lane_token)

    @patch("engine.remote_task_store.urlopen")
    def test_specialist_finish_and_retry_preserve_lane_identity(self, open_url):
        self.store.lane_token = "lane-token-" + "x" * 40
        open_url.return_value = FakeResponse({"ok": True, "status": "succeeded"})
        self.store.finish("task-1", "succeeded", result={"mode": "dry_run"},
                          lease_token="lease-1", worker_lane="specialist:reviewer")
        request = open_url.call_args.args[0]
        self.assertEqual(json.loads(request.data)["worker_lane"], "specialist:reviewer")
        self.assertEqual(request.get_header("X-engine-lane-token"), self.store.lane_token)
        open_url.return_value = FakeResponse({"ok": True, "status": "pending"})
        self.store.retry_or_fail("task-1", "retry", "lease-1", worker_lane="specialist:reviewer")
        request = open_url.call_args.args[0]
        self.assertEqual(json.loads(request.data)["worker_lane"], "specialist:reviewer")
        self.assertEqual(request.get_header("X-engine-lane-token"), self.store.lane_token)


    @patch("engine.remote_task_store.urlopen")
    def test_explicit_lane_token_is_bound_to_this_store_instance(self, open_url):
        open_url.return_value = FakeResponse({"tasks": [{"id": "research-1"}]})
        explicit = "explicit-researcher-token-0123456789abcdef"
        store = RemoteTaskStore(
            "https://engine.example", "app-bearer", lane_token=explicit,
        )
        store.list_tasks(1, worker_lane="specialist:researcher")
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("X-engine-lane-token"), explicit)

    
if __name__ == "__main__":
    unittest.main()
