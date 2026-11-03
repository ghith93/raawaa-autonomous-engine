import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.publish_staging_scan_telemetry import (
    PRODUCTION_DATABASE_ID,
    STAGING_DATABASE_ID,
    build_events,
    publish,
)


class FakeD1Client:
    def __init__(self):
        self.batches = []

    def batch(self, statements):
        self.batches.append(statements)
        return [{"success": True} for _ in statements]


def snapshot(**overrides):
    value = {
        "schema_version": 1,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "queries_attempted": 2,
        "requests_made": 2,
        "unavailable": {},
        "candidate_count": 4,
        "academic_requests_made": 1,
        "academic_candidates": [{"doi": "10.0000/example"}],
        "academic_unavailable": None,
        "total_requests_made": 3,
    }
    value.update(overrides)
    return value


def metadata(**overrides):
    value = {
        "run_id": 41640000000,
        "run_number": 220,
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    value.update(overrides)
    return value


class StagingScanTelemetryTests(unittest.TestCase):
    def test_builds_successful_scan_and_per_source_events(self):
        events = build_events(snapshot(), metadata())
        self.assertEqual([event["event"] for event in events], [
            "scan_completed", "source_observation", "source_observation"
        ])
        self.assertEqual(events[0]["details"]["status"], "success")
        self.assertEqual(events[0]["details"]["source_count"], 2)
        self.assertEqual(events[1]["details"]["source_key"], "github_public_repository_search")
        self.assertEqual(events[1]["details"]["status"], "fresh")
        self.assertEqual(events[2]["details"]["source_key"], "crossref_public_works_search")
        self.assertEqual(events[2]["details"]["status"], "fresh")

    def test_marks_partial_source_failures_without_storing_error_messages(self):
        events = build_events(
            snapshot(unavailable={"agent_memory": "HTTP 403 secret echo"}),
            metadata(),
        )
        self.assertEqual(events[0]["details"]["status"], "partial")
        self.assertEqual(events[1]["details"]["status"], "partial")
        serialized = json.dumps(events)
        self.assertNotIn("secret echo", serialized)
        self.assertEqual(events[1]["details"]["reason_code"], "partial_failure")

    def test_marks_all_sources_failed_when_every_source_is_unavailable(self):
        events = build_events(
            snapshot(
                unavailable={"agent_frameworks": "HTTP 403", "coding_agents": "HTTP 403"},
                academic_unavailable="HTTP 503",
            ),
            metadata(),
        )
        self.assertEqual(events[0]["details"]["status"], "failed")
        self.assertEqual(events[1]["details"]["status"], "unavailable")
        self.assertEqual(events[2]["details"]["status"], "unavailable")

    def test_rejects_future_or_timezone_naive_scan_timestamps(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_events(snapshot(retrieved_at="2026-11-03T12:00:00"), metadata())
        with self.assertRaisesRegex(ValueError, "future"):
            build_events(snapshot(retrieved_at="2099-11-03T12:00:00+00:00"), metadata())

    def test_publish_writes_idempotent_events_only_to_exact_staging_database(self):
        client = FakeD1Client()
        env = {
            "GITHUB_RUN_ID": "41640000000",
            "GITHUB_RUN_NUMBER": "220",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
            "RAAWAA_SCAN_STARTED_AT": datetime.now(timezone.utc).isoformat(),
            "D1_DATABASE_ID": STAGING_DATABASE_ID,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            result = publish(path, env, client)
        self.assertEqual(result["database"], "raawaa-engine-staging")
        self.assertEqual(result["source_events"], 2)
        self.assertEqual(len(client.batches), 1)
        statements = client.batches[0]
        self.assertEqual(len(statements), 3)
        self.assertTrue(all("NOT EXISTS" in item["sql"] for item in statements))
        self.assertTrue(all("json_extract" in item["sql"] for item in statements))
        self.assertTrue(all(STAGING_DATABASE_ID not in json.dumps(item) for item in statements))

    def test_refuses_production_database_id_before_any_write(self):
        client = FakeD1Client()
        env = {
            "GITHUB_RUN_ID": "41640000000", "GITHUB_RUN_NUMBER": "220",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40,
            "RAAWAA_SCAN_STARTED_AT": datetime.now(timezone.utc).isoformat(),
            "D1_DATABASE_ID": PRODUCTION_DATABASE_ID,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exact Staging database"):
                publish(path, env, client)
        self.assertEqual(client.batches, [])


if __name__ == "__main__":
    unittest.main()
