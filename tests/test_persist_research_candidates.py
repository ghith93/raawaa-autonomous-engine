import sqlite3
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
from pathlib import Path

from scripts.persist_research_candidates import (
    ResearchPersistenceError,
    load_candidates,
    persist_candidates,
    _RejectRedirects,
    _get,
    _post,
)


class ResearchPersistenceTests(unittest.TestCase):
    def test_authenticated_d1_redirects_are_refused_before_credentials_can_be_forwarded(self):
        handler = _RejectRedirects()
        request = Request("https://engine.example/v1/research/candidates")
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                request, None, 307, "Temporary Redirect", {},
                "https://evil.example/collect",
            )

    def test_authenticated_readback_uses_redirect_rejecting_transport(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit):
                return b'{"matched_keys":["github:org/repo"]}'

        with patch("scripts.persist_research_candidates._open_without_redirects", return_value=Response()) as opened:
            result = _get(
                "https://engine.example",
                "synthetic-test-token",
                ["github:org/repo"],
                "synthetic-access-id",
                "synthetic-access-secret",
            )
        self.assertEqual(result["matched_keys"], ["github:org/repo"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/research/candidates/verify")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-test-token")
        self.assertEqual(request.get_header("Cf-access-client-id"), "synthetic-access-id")
        self.assertEqual(request.get_header("Cf-access-client-secret"), "synthetic-access-secret")
        self.assertEqual(opened.call_args.kwargs["timeout"], 20)
        self.assertIn("github:org/repo", request.data.decode("utf-8"))

    def test_research_workflow_automatically_gates_all_staging_writes_on_zero_cost_preflight(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "research-discovery.yml").read_text(encoding="utf-8")
        self.assertIn("id: research_zero_cost_preflight", workflow)
        self.assertNotIn("\n  workflow_dispatch:\n", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("python scripts/verify_cloudflare_zero_cost_boundary.py", workflow)
        self.assertIn("steps.research_zero_cost_preflight.outputs.zero_cost_eligible == 'true'", workflow)
        self.assertNotIn("vars.RAAWAA_ENGINE_RESEARCH_PERSISTENCE", workflow)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", workflow)
        self.assertIn("RAAWAA_ENGINE_API_TOKEN: ${{ secrets.ENGINE_STAGING_API_TOKEN }}", workflow)
        telemetry = workflow.split("      - name: Publish explicit scan and source freshness telemetry", 1)[1]
        self.assertIn("steps.research_zero_cost_preflight.outputs.zero_cost_eligible == 'true'", telemetry)

    def test_workflow_persistence_is_staging_only_and_uses_dedicated_token(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "research-discovery.yml").read_text(encoding="utf-8")
        start = workflow.index("      - name: Persist research metadata in isolated Staging D1 only")
        end = workflow.index("\n      - name:", start + 8)
        step = workflow[start:end]
        self.assertIn("RAAWAA_ENGINE_API_URL: https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev", step)
        self.assertIn("RAAWAA_ENGINE_API_TOKEN: ${{ secrets.ENGINE_STAGING_API_TOKEN }}", step)
        self.assertNotIn("raawaa-autonomous-engine-control", step)
        self.assertNotIn("secrets.ENGINE_API_TOKEN", step)
        self.assertNotIn("CF_ACCESS", step)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", step)
        self.assertNotIn("CLOUDFLARE_ACCOUNT_ID", step)


    def test_post_carries_stable_batch_id_without_exposing_credentials(self):
        class Response:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit):
                return b'{"accepted_count":1,"batch_id":"github:run:1","replayed":false}'

        candidate = {"source": "github", "full_name": "org/repo"}
        with patch("scripts.persist_research_candidates._open_without_redirects", return_value=Response()) as opened:
            result = _post(
                "https://engine.example", "synthetic-token", [candidate], "github:run:1",
                "synthetic-access-id", "synthetic-access-secret",
            )
        self.assertEqual(result["batch_id"], "github:run:1")
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/research/candidates")
        self.assertEqual(request.method, "POST")
        self.assertIn('"batch_id":"github:run:1"', request.data.decode("utf-8"))
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(opened.call_args.args[1], 20)

    def test_authenticated_readback_rejects_redirects(self):
        handler = _RejectRedirects()
        request = Request("https://engine.example/v1/research/candidates?status=all")
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                request, None, 302, "Found", {},
                "https://redirect-attacker.example/collect",
            )

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
        def fake_post(base_url, token, batch, batch_id, access_id, access_secret):
            calls.append([item["source"] for item in batch])
            return {"accepted_count": len(batch), "batch_id": batch_id, "replayed": False}
        result = persist_candidates(
            snapshot,
            environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret", "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run"},
            request_fn=fake_post,
            verify_fn=lambda *args: {"matched_keys": expected},
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
        connection.executescript((root / "migrations" / "0011_research_source_expansion.sql").read_text(encoding="utf-8"))
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

    def test_research_ingest_idempotency_migration_creates_durable_unique_keys(self):
        root = Path(__file__).resolve().parents[1]
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        connection.executescript((root / "migrations" / "0002_research_candidates.sql").read_text(encoding="utf-8"))
        connection.executescript((root / "migrations" / "0015_research_ingest_idempotency.sql").read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO research_ingest_batches(batch_id,payload_sha256,candidate_count,created_at) VALUES(?,?,?,?)",
            ("github:run:1", "a" * 64, 1, "2026-11-07T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO research_ingest_events(batch_id,source_key,payload_sha256,created_at) VALUES(?,?,?,?)",
            ("github:run:1", "github:org/repo", "a" * 64, "2026-11-07T00:00:00Z"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO research_ingest_events(batch_id,source_key,payload_sha256,created_at) VALUES(?,?,?,?)",
                ("github:run:1", "github:org/repo", "a" * 64, "2026-11-07T00:00:01Z"),
            )
        connection.close()

    def test_empty_snapshot_does_not_need_credentials_or_network(self):
        result = persist_candidates({"candidates": [], "academic_candidates": []}, environ={})
        self.assertEqual(result, {"discovered": 0, "persisted": 0, "verified": 0, "batches": 0, "replayed_batches": 0, "sources": []})

    def test_persistence_is_batched_and_uses_existing_secret_environment(self):
        snapshot = {
            "candidates": [{"full_name": f"org/repo-{i}", "url": f"https://github.com/org/repo-{i}"} for i in range(7)],
            "academic_candidates": [{"doi": "10.1234/paper", "title": "Research"}],
        }
        calls = []
        def fake_post(base_url, token, batch, batch_id, access_id, access_secret):
            calls.append((base_url, token, len(batch), access_id, access_secret))
            return {"accepted_count": len(batch), "batch_id": batch_id, "replayed": False}
        result = persist_candidates(
            snapshot,
            environ={
                "RAAWAA_ENGINE_API_URL": "https://engine.example",
                "RAAWAA_ENGINE_API_TOKEN": "test-secret", "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run",
                "RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID": "access-id",
                "RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET": "access-secret",
            },
            request_fn=fake_post,
            batch_size=5,
            verify_fn=lambda *args: {"matched_keys": args[2]},
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
                "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run",
                "RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID": "only-one",
            })

    def test_retries_temporary_worker_deploy_or_migration_errors(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        calls, sleeps, batch_ids = [], [], []
        def flaky_post(*args):
            calls.append(1)
            batch_ids.append(args[3])
            if len(calls) < 3:
                raise ResearchPersistenceError("research persistence endpoint returned HTTP 404")
            return {"accepted_count": 1, "batch_id": args[3], "replayed": False}
        result = persist_candidates(
            snapshot,
            environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret", "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run"},
            request_fn=flaky_post,
            sleep_fn=sleeps.append,
            verify_fn=lambda *args: {"matched_keys": ["github:org/repo"]},
        )
        self.assertEqual(result["persisted"], 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(batch_ids)), 1, "all retries must reuse the same idempotency key")
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
                environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret", "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run"},
                request_fn=unauthorized,
                sleep_fn=lambda _: self.fail("auth errors must not be retried"),
                verify_fn=lambda *args: {"matched_keys": []},
            )
        self.assertEqual(len(calls), 1)

    def test_readback_rejects_duplicate_unexpected_and_non_string_keys(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        bad_results = (
            {"matched_keys": ["github:org/repo", "github:org/repo"]},
            {"matched_keys": ["github:org/other"]},
            {"matched_keys": ["github:org/repo", {"unexpected": "object"}]},
        )
        for bad in bad_results:
            with self.subTest(bad=bad), self.assertRaises(ResearchPersistenceError):
                persist_candidates(
                    snapshot,
                    environ={"RAAWAA_ENGINE_API_URL": "https://engine.example",
                             "RAAWAA_ENGINE_API_TOKEN": "test-secret",
                             "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run"},
                    request_fn=lambda *args: {"accepted_count": 1, "batch_id": args[3], "replayed": False},
                    verify_fn=lambda *args, response=bad: response,
                )

    def test_readback_must_confirm_every_submitted_candidate(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        with self.assertRaises(ResearchPersistenceError):
            persist_candidates(
                snapshot,
                environ={"RAAWAA_ENGINE_API_URL": "https://engine.example", "RAAWAA_ENGINE_API_TOKEN": "test-secret", "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run"},
                request_fn=lambda *args: {"accepted_count": 1, "batch_id": args[3], "replayed": False},
                verify_fn=lambda *args: {"matched_keys": []},
            )

    def test_exact_key_verification_is_batched_and_not_limited_to_latest_100(self):
        snapshot = {"candidates": [
            {"full_name": f"org/repo-{i}", "url": f"https://github.com/org/repo-{i}"}
            for i in range(111)
        ]}
        post_ids, verified_batches = [], []
        def fake_post(base_url, token, batch, batch_id, access_id, access_secret):
            post_ids.append(batch_id)
            return {"accepted_count": len(batch), "batch_id": batch_id, "replayed": False}
        def fake_verify(base_url, token, keys, access_id, access_secret):
            verified_batches.append(list(keys))
            return {"matched_keys": list(keys)}
        result = persist_candidates(
            snapshot,
            environ={"RAAWAA_ENGINE_API_URL": "https://engine.example",
                     "RAAWAA_ENGINE_API_TOKEN": "test-secret",
                     "RAAWAA_RESEARCH_SCAN_ID": "large-scan"},
            request_fn=fake_post,
            batch_size=10,
            verify_fn=fake_verify,
        )
        self.assertEqual(result["discovered"], 111)
        self.assertEqual(result["verified"], 111)
        self.assertEqual(result["batches"], 12)
        self.assertEqual(len(post_ids), 12)
        self.assertEqual(post_ids[0], "github:large-scan:1")
        self.assertEqual(post_ids[-1], "github:large-scan:12")
        self.assertEqual(sum(map(len, verified_batches)), 111)
        self.assertLessEqual(max(map(len, verified_batches)), 10)

    def test_replayed_batch_receipt_is_counted_without_changing_exact_verification(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        result = persist_candidates(
            snapshot,
            environ={"RAAWAA_ENGINE_API_URL": "https://engine.example",
                     "RAAWAA_ENGINE_API_TOKEN": "test-secret",
                     "RAAWAA_RESEARCH_SCAN_ID": "retry-scan"},
            request_fn=lambda base, token, batch, batch_id, access_id, access_secret:
                {"accepted_count": len(batch), "batch_id": batch_id, "replayed": True},
            verify_fn=lambda base, token, keys, access_id, access_secret: {"matched_keys": keys},
        )
        self.assertEqual(result["replayed_batches"], 1)
        self.assertEqual(result["verified"], 1)

    def test_partial_acceptance_stops_for_safe_retry(self):
        snapshot = {"candidates": [{"full_name": "org/repo"}]}
        def partial(*args):
            return {"accepted_count": 0, "batch_id": args[3], "replayed": False}
        with self.assertRaises(ResearchPersistenceError):
            persist_candidates(snapshot, environ={
                "RAAWAA_ENGINE_API_URL": "https://engine.example",
                "RAAWAA_ENGINE_API_TOKEN": "test-secret",
                "RAAWAA_RESEARCH_SCAN_ID": "synthetic-run",
            }, request_fn=partial)


if __name__ == "__main__":
    unittest.main()
