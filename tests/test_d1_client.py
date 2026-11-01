import json
import unittest
from unittest.mock import patch

from engine.d1_client import D1Client, D1ConfigurationError, D1APIError


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, *args): return self.payload


class D1ClientTests(unittest.TestCase):
    def client(self):
        return D1Client("a" * 32, "12345678-1234-1234-1234-123456789abc", "secret-test-token")

    def test_requires_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(D1ConfigurationError):
                D1Client()

    @patch("engine.d1_client.urllib.request.urlopen")
    def test_query_uses_bearer_auth_and_parameterized_body(self, open_url):
        open_url.return_value = FakeResponse({"success": True, "result": [
            {"success": True, "results": [{"ok": 1}], "meta": {"rows_read": 1}}
        ]})
        result = self.client().query("SELECT ? AS ok", [1])
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-test-token")
        self.assertEqual(json.loads(request.data), {"sql": "SELECT ? AS ok", "params": [1]})
        self.assertEqual(result["results"][0]["ok"], 1)

    @patch("engine.d1_client.urllib.request.urlopen")
    def test_healthcheck(self, open_url):
        open_url.return_value = FakeResponse({"success": True, "result": [
            {"success": True, "results": [{"ok": 1}], "meta": {"rows_read": 1, "duration": 0.2}}
        ]})
        self.assertTrue(self.client().healthcheck()["ok"])

    @patch("engine.d1_client.urllib.request.urlopen")
    def test_api_failure_does_not_echo_token(self, open_url):
        open_url.return_value = FakeResponse({"success": False, "errors": [
            {"code": 10000, "message": "permission denied"}
        ]})
        with self.assertRaises(D1APIError) as ctx:
            self.client().query("SELECT 1")
        self.assertNotIn("secret-test-token", str(ctx.exception))

    def test_rejects_empty_sql_and_bad_batch(self):
        client = self.client()
        with self.assertRaises(ValueError): client.query(" ")
        with self.assertRaises(ValueError): client.batch([])


if __name__ == "__main__":
    unittest.main()
