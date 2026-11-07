import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from scripts.verify_research_persistence_worker import (
    REQUIRED_TABLES,
    ResearchWorkerError,
    STAGING_HOST,
    verify_worker,
)


def response(body, status=200):
    class Response:
        def __init__(self):
            self.status = status
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit): return body
    return Response()


class ResearchPersistenceWorkerTests(unittest.TestCase):
    def test_exact_staging_worker_and_required_tables_are_verified_read_only(self):
        captured = []
        body = json.dumps({"ok": True, "missing": [], "checked": sorted(REQUIRED_TABLES)}).encode()
        def opener(request, timeout):
            captured.append((request, timeout))
            return response(body)
        result = verify_worker(
            "https://" + STAGING_HOST,
            "synthetic-token",
            opener=opener,
        )
        self.assertTrue(result["ready"])
        request, timeout = captured[0]
        self.assertEqual(request.full_url, f"https://{STAGING_HOST}/v1/diagnostics/schema")
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(timeout, 10)

    def test_refuses_production_or_noncanonical_urls_before_network(self):
        called = []
        for url in (
            "https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev",
            "http://" + STAGING_HOST,
            "https://" + STAGING_HOST + "/path",
            "https://" + STAGING_HOST + "?x=1",
        ):
            with self.subTest(url=url), self.assertRaises(ResearchWorkerError):
                verify_worker(url, "synthetic-token", opener=lambda *a, **k: called.append(a))
        self.assertEqual(called, [])

    def test_missing_or_incomplete_research_schema_fails_closed(self):
        bodies = [
            {"ok": False, "missing": ["research_ingest_events"], "checked": []},
            {"ok": True, "missing": [], "checked": ["research_ingest_batches"]},
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ResearchWorkerError):
                verify_worker(
                    "https://" + STAGING_HOST,
                    "synthetic-token",
                    opener=lambda *_a, **_k: response(json.dumps(body).encode()),
                )

    def test_oversized_and_non_json_responses_fail_closed(self):
        for raw in (b"x" * (64 * 1024 + 1), b"not-json"):
            with self.subTest(length=len(raw)), self.assertRaises(ResearchWorkerError):
                verify_worker(
                    "https://" + STAGING_HOST,
                    "synthetic-token",
                    opener=lambda *_a, **_k: response(raw),
                )

    def test_redirects_are_rejected(self):
        handler = __import__("scripts.verify_research_persistence_worker", fromlist=["_RejectRedirects"])._RejectRedirects()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                Request(f"https://{STAGING_HOST}/v1/diagnostics/schema"),
                None, 302, "Found", {}, "https://attacker.example/collect",
            )


if __name__ == "__main__":
    unittest.main()
