import json
import unittest
from unittest.mock import patch

from engine import artemis_gateway as gateway


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload, url):
        self.body = json.dumps(payload).encode()
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body[:size]

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class ArtemisGatewayTests(unittest.TestCase):
    def test_unconfigured_probe_performs_no_network_calls(self):
        result = gateway.probe_artemis({})
        self.assertEqual(result["status"], "NOT_CONFIGURED")
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["task_submission_enabled"])

    def test_remote_endpoint_requires_https_token_and_auth_attestation(self):
        with self.assertRaises(ValueError):
            gateway.validate_artemis_endpoint("http://artemis.example", {"ARTEMIS_TOKEN": "x"})
        with self.assertRaises(ValueError):
            gateway.validate_artemis_endpoint("https://artemis.example", {})
        with self.assertRaises(ValueError):
            gateway.validate_artemis_endpoint("https://artemis.example", {
                "ARTEMIS_TOKEN": "x",
                "ARTEMIS_REMOTE_AUTH_VERIFIED": "false",
            })
        base, token = gateway.validate_artemis_endpoint("https://artemis.example", {
            "ARTEMIS_TOKEN": "synthetic-token",
            "ARTEMIS_REMOTE_AUTH_VERIFIED": "true",
        })
        self.assertEqual(base, "https://artemis.example")
        self.assertEqual(token, "synthetic-token")

    def test_localhost_is_allowed_without_remote_proxy_attestation(self):
        base, token = gateway.validate_artemis_endpoint("http://127.0.0.1:8000", {})
        self.assertEqual(base, "http://127.0.0.1:8000")
        self.assertIsNone(token)

    def test_endpoint_rejects_credentials_paths_queries_and_fragments(self):
        for url in (
            "https://user:pass@artemis.example",
            "https://artemis.example/admin",
            "https://artemis.example?token=x",
            "https://artemis.example#frag",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                gateway.validate_artemis_endpoint(url, {
                    "ARTEMIS_TOKEN": "synthetic-token",
                    "ARTEMIS_REMOTE_AUTH_VERIFIED": "true",
                })

    def test_probe_reports_connected_devices_without_enabling_task_execution(self):
        base = "http://127.0.0.1:8000"
        opener = FakeOpener([
            FakeResponse({"status": "running"}, base + "/api/status"),
            FakeResponse({"devices": [
                {"serial": "emulator-5554", "state": "device", "model": "Pixel_8"},
                {"serial": "offline-device", "state": "offline"},
            ]}, base + "/api/devices"),
        ])
        with patch.object(gateway, "build_opener", return_value=opener):
            result = gateway.probe_artemis({"ARTEMIS_BASE_URL": base})
        self.assertEqual(result["status"], "CONNECTED")
        self.assertEqual(result["network_calls"], 2)
        self.assertEqual(result["device_count"], 2)
        self.assertEqual(result["daemon_state"], "running")
        self.assertFalse(result["task_submission_enabled"])
        self.assertEqual(opener.calls[0][0].get_method(), "GET")
        self.assertEqual(opener.calls[1][0].get_method(), "GET")

    def test_probe_rejects_non_object_health_payload(self):
        base = "http://127.0.0.1:8000"
        opener = FakeOpener([
            FakeResponse([], base + "/api/status"),
            FakeResponse([], base + "/api/devices"),
        ])
        with patch.object(gateway, "build_opener", return_value=opener):
            result = gateway.probe_artemis({"ARTEMIS_BASE_URL": base})
        self.assertEqual(result["status"], "INVALID_RESPONSE")
        self.assertFalse(result["task_submission_enabled"])

    def test_redirect_is_not_followed(self):
        base = "http://127.0.0.1:8000"
        opener = FakeOpener([FakeResponse({"status": "running"}, "http://127.0.0.1:8000/other")])
        with patch.object(gateway, "build_opener", return_value=opener):
            with self.assertRaises(gateway.ArtemisGatewayError):
                gateway._request_json(base, "/api/status", None)

    def test_oversized_response_is_rejected(self):
        base = "http://127.0.0.1:8000"
        class Oversized:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return base + "/api/status"
            def read(self, size=-1): return b" " * (gateway.MAX_RESPONSE_BYTES + 1)
        opener = FakeOpener([Oversized()])
        with patch.object(gateway, "build_opener", return_value=opener):
            with self.assertRaises(gateway.ArtemisGatewayError):
                gateway._request_json(base, "/api/status", None)


if __name__ == "__main__":
    unittest.main()
