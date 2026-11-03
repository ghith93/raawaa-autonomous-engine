import sqlite3
import unittest
from pathlib import Path

from scripts.persist_research_candidates import (
    ResearchPersistenceError,
    load_candidates,
    persist_candidates,
)


class ResearchPersistenceTests(unittest.TestCase):
    def test_load_candidates_merges_sources_and_deduplicates(self):
        snapshot = {
            "candidates": [
                {"full_name": "Org/Agent", "url": "https://github.com/Org/Agent"},
                {"full_name": "org/agent", "url": "https://github.com/org/agent"},
            ],
            "academic_candidates": [
                {"doi": "10.1234/paper", "title": "Research"},
                {"doi": "10.1234/PAPER", "title": "Duplicate"},
            ],
        }
        candidates = load_candidates(snapshot)
        self.assertEqual(len(candidates), 2)
        self.assertEqual([item["source"] for item in candidates], ["github", "crossref"])

    def test_load_candidates_includes_arxiv_and_searxng_with_deduplication(self):
        snapshot = {
            "candidates": [],
            "academic_candidates": [],
            "arxiv_candidates": [
                {"arxiv_id": "2601.01234", "title": "Agent safety", "url": "https://arxiv.org/abs/2601.01234"},
                {"arxiv_id": "2601.01234", "title": "Duplicate", "url": "https://arxiv.org/abs/2601.01234"},
            ],
            "searxng_candidates": [
                {"url": "https://example.org/agent-security", "title": "Security"},
                {"url": "https://example.org/agent-security", "title": "Duplicate"},
            ],
        }
        candidates = load_candidates(snapshot)
        self.assertEqual(len(candidates), 2)
        self.assertEqual([item["source"] for item in candidates], ["arxiv", "searxng"])

    def test_persistence_and_readback_cover_all_four_supported_sources(self):
        snapshot = {
            "candidates": [{"full_name": "org/agent"}],
            "academic_candidates": [{"doi": "10.1234/paper", "title": "Paper"}],
            "arxiv_candidates": [{"arxiv_id": "2601.01234", "title": "arXiv", "url": "https://arxiv.org/abs/2601.01234"}],
            "searxng_candidates": [{"url": "https://example.org/agent", "title": "Web result"}],
        }
        expected = [
            "github:org/agent", "crossref:10.1234/paper",
            "arxiv:2601.01234", "searxng:https://example.org/agent",
        ]
        calls = []
        def fake_post(base_url, token, batch, access_id, access_secret):
            calls.append([item["source"] for item in batch])
            return {"accepted_count": len(batch)}
        result = persist_candidates(
            snapshot,
            environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret"},
            request_fn=fake_post,
            verify_fn=lambda *args: {"candidates": [{"source_key": key} for key in expected]},
        )
        self.assertEqual(result["sources"], ["arxiv", "crossref", "github", "searxng"])
        self.assertEqual(result["discovered"], 4)
        self.assertEqual(result["verified"], 4)
        self.assertEqual(result["persisted"], 4)
        self.assertEqual(calls, [["github", "crossref", "arxiv", "searxng"]])

    def test_source_expansion_migration_preserves_existing_review_history(self):
        root = Path(__file__).resolve().parents[1]
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        connection.executescript((root / "migrations" / "0002_research_candidates.sql").read_text(encoding="utf-8"))
        connection.execute(
            """INSERT INTO research_candidates(
                source_key,source,title,url,metadata_json,review_status,review_note,
                first_seen_at,last_seen_at,reviewed_at,discovery_count,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("github:org/agent", "github", "org/agent", "https://github.com/org/agent", "{}",
             "accepted", "reviewed before source expansion", "2026-01-01", "2026-01-02",
             "2026-01-02", 3, "2026-01-02"),
        )
        connection.executescript((root / "migrations" / "0008_research_source_expansion.sql").read_text(encoding="utf-8"))
        existing = connection.execute(
            "SELECT source,review_status,review_note,first_seen_at,last_seen_at,reviewed_at,discovery_count FROM research_candidates WHERE source_key=?",
            ("github:org/agent",),
        ).fetchone()
        self.assertEqual(existing, ("github", "accepted", "reviewed before source expansion",
                                    "2026-01-01", "2026-01-02", "2026-01-02", 3))
        for source in ("arxiv", "searxng"):
            connection.execute(
                """INSERT INTO research_candidates(
                    source_key,source,title,url,metadata_json,first_seen_at,last_seen_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (source + ":candidate", source, "Candidate", "https://example.org/item", "{}",
                 "2026-11-03", "2026-11-03", "2026-11-03"),
            )
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM research_candidates").fetchone()[0], 3)
        connection.close()

    def test_empty_snapshot_does_not_need_credentials_or_network(self):
        result = persist_candidates({"candidates": [], "academic_candidates": []}, environ={})
        self.assertEqual(result, {"discovered": 0, "persisted": 0, "batches": 0, "sources": []})

    def test_persistence_is_batched_and_uses_existing_secret_environment(self):
        snapshot = {
            "candidates": [{"full_name": f"org/repo-{i}", "url": f"https://github.com/org/repo-{i}"} for i in range(7)],
            "academic_candidates": [{"doi": "10.1234/paper", "title": "Research"}],
        }
        calls = []
        def fake_post(base_url, token, batch, access_id, access_secret):
            calls.append((base_url, token, len(batch), access_id, access_secret))
            return {"accepted_count": len(batch)}
        result = persist_candidates(
            snapshot,
            environ={
                "RAAWAA_ENGINE_API_URL": "https://engine.example",
                "RAAWAA_ENGINE_API_TOKEN": "test-secret",
                "RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID": "access-id",
                "RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET": "access-secret",
            },
            request_fn=fake_post,
            batch_size=5,
            verify_fn=lambda *args: {"candidates": [{"source_key": "github:org/repo-" + str(i)} for i in range(7)] + [{"source_key": "crossref:10.1234/paper"}]},
        )
        self.assertEqual([call[2] for call in calls], [5, 3])
        self.assertEqual(calls[0], ("https://engine.example", "test-secret", 5, "access-id", "access-secret"))
        self.assertEqual(result["persisted"], 8)
        self.assertEqual(result["verified"], 8)
        self.assertEqual(result["batches"], 2)
        self.assertEqual(result["sources"], ["crossref", "github"])

    def test_missing_credentials_and_unpaired_access_fail_closed(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        with self.assertRaises(ValueError):
            persist_candidates(snapshot, environ={})
        with self.assertRaises(ValueError):
            persist_candidates(snapshot, environ={
                "RAAWAA_ENGINE_API_URL": "https://engine.example",
                "RAAWAA_ENGINE_API_TOKEN": "test-secret",
                "RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID": "only-one",
            })

    def test_retries_temporary_worker_deploy_or_migration_errors(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        calls, sleeps = [], []
        def flaky_post(*args):
            calls.append(1)
            if len(calls) < 3:
                raise ResearchPersistenceError("research persistence endpoint returned HTTP 404")
            return {"accepted_count": 1}
        result = persist_candidates(
            snapshot,
            environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret"},
            request_fn=flaky_post,
            sleep_fn=sleeps.append,
            verify_fn=lambda *args: {"candidates": [{"source_key": "github:org/repo"}]},
        )
        self.assertEqual(result["persisted"], 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [5, 10])

    def test_does_not_retry_auth_or_validation_errors(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        calls = []
        def unauthorized(*args):
            calls.append(1)
            raise ResearchPersistenceError("research persistence endpoint returned HTTP 401")
        with self.assertRaises(ResearchPersistenceError):
            persist_candidates(
                snapshot,
                environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret"},
                request_fn=unauthorized,
                sleep_fn=lambda _: self.fail("auth errors must not be retried"),
                verify_fn=lambda *args: {"candidates": []},
            )
        self.assertEqual(len(calls), 1)

    def test_readback_must_confirm_every_submitted_candidate(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        with self.assertRaises(ResearchPersistenceError):
            persist_candidates(
                snapshot,
                environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret"},
                request_fn=lambda *args: {"accepted_count": 1},
                verify_fn=lambda *args: {"candidates": []},
            )

    def test_partial_acceptance_stops_for_safe_retry(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        def partial(*args):
            return {"accepted_count": 0}
        with self.assertRaises(ResearchPersistenceError):
            persist_candidates(snapshot, environ={
                "RAAWAA_ENGINE_API_URL": "https://engine.example",
                "RAAWAA_ENGINE_API_TOKEN": "test-secret",
            }, request_fn=partial)


if __name__ == "__main__":
    unittest.main()
