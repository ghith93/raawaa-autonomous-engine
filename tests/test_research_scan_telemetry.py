import io
import json
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from unittest.mock import patch

from scripts.publish_research_scan_telemetry import (
    ScanTelemetryError, build_payload, publish,
)

class Response:
    status = 201
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return self.payload

def env(**changes):
    values = {
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_NUMBER": "7",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40,
        "GITHUB_WORKFLOW": "Scheduled Open-Source Research Discovery",
        "RESEARCH_SCAN_STARTED_AT": "2026-11-03T12:00:00Z",
        "RAAWAA_ENGINE_API_URL": "https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev",
        "RAAWAA_ENGINE_API_TOKEN": "synthetic-token",
    }
    values.update(changes)
    return values

def snapshot(**changes):
    value = {
        "requests_made": 6, "academic_requests_made": 1,
        "candidates": [{"full_name": "org/project"}],
        "academic_candidates": [{"doi": "10.1234/test"}],
        "unavailable": {}, "academic_unavailable": None,
    }
    value.update(changes)
    return value

class ResearchScanTelemetryTests(unittest.TestCase):
    def test_builds_successful_explicit_scan_and_two_source_observations(self):
        payload = build_payload(snapshot(), env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        self.assertEqual(payload["scan"]["status"], "success")
        self.assertEqual(payload["scan"]["scan_id"], "123:1")
        self.assertEqual({x["source"] for x in payload["source_observations"]}, {"github","crossref"})
        self.assertTrue(all(x["status"] == "fresh" for x in payload["source_observations"]))
        self.assertEqual(payload["source_observations"][0]["freshness_hours"], 24)

    def test_marks_partial_when_one_source_is_unavailable_without_exposing_raw_error(self):
        payload = build_payload(snapshot(academic_unavailable="Crossref returned HTTP 429 with secret-body"),
                                env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        self.assertEqual(payload["scan"]["status"], "partial")
        crossref = next(x for x in payload["source_observations"] if x["source"] == "crossref")
        self.assertEqual(crossref["status"], "unavailable")
        self.assertEqual(crossref["reason_code"], "http_429")
        self.assertNotIn("secret-body", json.dumps(payload))

    def test_marks_failure_when_both_sources_unavailable(self):
        payload = build_payload(snapshot(unavailable={"q":"HTTP 403"},academic_unavailable="HTTP 503"),
                                env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        self.assertEqual(payload["scan"]["status"], "failure")

    def test_rejects_missing_workflow_identity_or_naive_timestamp(self):
        for changes in ({"GITHUB_WORKFLOW":"other"}, {"GITHUB_SHA":"bad"},
                        {"RESEARCH_SCAN_STARTED_AT":"2026-11-03T12:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(ScanTelemetryError):
                build_payload(snapshot(), env(**changes))

    def test_publish_uses_only_authenticated_post_to_fixed_origin(self):
        payload = build_payload(snapshot(), env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        seen = {}
        def opener(request, timeout):
            seen.update(url=request.full_url, method=request.get_method(), timeout=timeout,
                        auth=request.get_header("Authorization"), body=json.loads(request.data))
            return Response({"ok":True,"scan_id":"123:1","recorded_events":3})
        result = publish(payload, env(), opener=opener)
        self.assertEqual(result["recorded_events"], 3)
        self.assertEqual(seen["method"], "POST")
        self.assertTrue(seen["url"].endswith("/v1/research/scan-runs"))
        self.assertEqual(seen["auth"], "Bearer synthetic-token")
        self.assertEqual(seen["body"]["scan"]["scan_id"], "123:1")

    def test_refuses_wrong_origin_and_partial_access_credentials(self):
        payload = build_payload(snapshot(), env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        with self.assertRaises(ScanTelemetryError):
            publish(payload, env(RAAWAA_ENGINE_API_URL="https://evil.example"))
        with self.assertRaises(ScanTelemetryError):
            publish(payload, env(RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID="only-id"))

    def test_rejects_invalid_acknowledgement(self):
        payload = build_payload(snapshot(), env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        with self.assertRaisesRegex(ScanTelemetryError, "acknowledgement"):
            publish(payload, env(), opener=lambda *a,**k: Response({"ok":True,"scan_id":"wrong"}))

if __name__ == "__main__":
    unittest.main()
