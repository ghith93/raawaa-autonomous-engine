import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.publish_research_scan_telemetry import (
    ScanTelemetryError, build_payload, build_statements, publish,
)

def env(**changes):
    values = {
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_NUMBER": "7",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40,
        "GITHUB_WORKFLOW": "Scheduled Open-Source Research Discovery",
        "RESEARCH_SCAN_STARTED_AT": "2026-11-03T12:00:00Z",
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "CLOUDFLARE_API_TOKEN": "synthetic-staging-token",
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

def config_root():
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    (root / "wrangler.staging.toml").write_text(
        'name = "raawaa-autonomous-engine-staging"\n'
        '[[d1_databases]]\n'
        'database_name = "raawaa-engine-staging"\n'
        'database_id = "231dd948-04a0-49e3-ad73-444f77d69477"\n',
        encoding="utf-8")
    (root / "wrangler.toml").write_text(
        'name = "raawaa-autonomous-engine-control"\n'
        '[[d1_databases]]\n'
        'database_name = "raawaa-autonomous-e"\n'
        'database_id = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"\n',
        encoding="utf-8")
    return temp, root

class FakeD1:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.payload = None
    def query(self, sql, params=None):
        self.calls.append(("query", sql, params))
        if sql.startswith("SELECT 1"):
            return {"results":[{"ok":1}]}
        return {"results":[
            {"event":"research_scan_completed","details_json":json.dumps(self.payload["scan"])},
            *[{"event":"research_source_observation","details_json":json.dumps(item)}
              for item in self.payload["source_observations"]],
        ]}
    def batch(self, statements):
        self.calls.append(("batch", statements))
        return [{"meta":{"changes":1}} for _ in statements]

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

    def test_builds_idempotent_parameterized_staging_audit_statements(self):
        payload = build_payload(snapshot(), env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        statements = build_statements(payload)
        self.assertEqual(len(statements),3)
        self.assertTrue(all("NOT EXISTS" in item["sql"] for item in statements))
        self.assertNotIn("synthetic-staging-token", json.dumps(statements))
        self.assertEqual(statements[0]["params"][2],"123:1")

    def test_publishes_only_to_verified_staging_d1_and_reads_back_all_events(self):
        payload = build_payload(snapshot(), env(), datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        temp, root = config_root()
        clients=[]
        def factory(**kwargs):
            client=FakeD1(**kwargs);client.payload=payload;clients.append(client);return client
        try:
            result=publish(payload,env(),root=root,client_factory=factory)
        finally:
            temp.cleanup()
        self.assertEqual(result["database_id"],"231dd948-04a0-49e3-ad73-444f77d69477")
        self.assertEqual(result["verified_events"],3)
        self.assertEqual(result["inserted_events"],3)
        self.assertEqual(len(clients),1)
        self.assertEqual(clients[0].kwargs["database_id"],"231dd948-04a0-49e3-ad73-444f77d69477")
        self.assertEqual(clients[0].calls[1][0],"batch")
        self.assertEqual(len(clients[0].calls[1][1]),3)

    def test_refuses_production_database_identity_before_client_creation(self):
        payload=build_payload(snapshot(),env(),datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        temp,root=config_root()
        (root/"wrangler.staging.toml").write_text(
            'name = "raawaa-autonomous-engine-staging"\n[[d1_databases]]\n'
            'database_name = "raawaa-engine-staging"\n'
            'database_id = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"\n',encoding="utf-8")
        called=[]
        try:
            with self.assertRaisesRegex(ScanTelemetryError,"identity mismatch"):
                publish(payload,env(),root=root,client_factory=lambda **kw:called.append(kw))
        finally:
            temp.cleanup()
        self.assertEqual(called,[])

    def test_requires_cloudflare_staging_credentials(self):
        payload=build_payload(snapshot(),env(),datetime(2026,11,3,12,5,tzinfo=timezone.utc))
        temp,root=config_root()
        try:
            with self.assertRaisesRegex(ScanTelemetryError,"credentials"):
                publish(payload,{"CLOUDFLARE_ACCOUNT_ID":"a"*32},root=root,client_factory=lambda **kw:None)
        finally:
            temp.cleanup()

if __name__ == "__main__":
    unittest.main()
