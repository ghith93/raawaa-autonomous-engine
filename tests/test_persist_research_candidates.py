import unittest

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
        )
        self.assertEqual([call[2] for call in calls], [5, 3])
        self.assertEqual(calls[0], ("https://engine.example", "test-secret", 5, "access-id", "access-secret"))
        self.assertEqual(result["persisted"], 8)
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
