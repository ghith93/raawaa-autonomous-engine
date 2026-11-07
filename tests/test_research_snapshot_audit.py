import unittest

from scripts.research_snapshot_audit import (
    canonical_github_identity,
    canonicalize_url,
    classify_expected_ids,
    stable_metadata_fingerprint,
)


class ResearchSnapshotAuditTests(unittest.TestCase):
    def test_github_identity_is_case_and_suffix_insensitive(self):
        self.assertEqual(canonical_github_identity("HTTPS://GitHub.com/Owner/Repo.git/"), "owner/repo")
        self.assertEqual(canonical_github_identity("owner/repo/issues/12"), "owner/repo")
        self.assertIsNone(canonical_github_identity("https://example.com/owner/repo"))

    def test_url_canonicalization_removes_only_tracking_and_fragment(self):
        self.assertEqual(
            canonicalize_url("HTTPS://Example.COM:443/a//b/?keep=1&utm_source=x&FBCLID=z#section"),
            "https://example.com/a/b?keep=1",
        )
        self.assertEqual(
            canonicalize_url("https://example.com/path?b=2&a=1"),
            "https://example.com/path?b=2&a=1",
        )

    def test_doi_host_and_case_are_canonicalized(self):
        self.assertEqual(canonicalize_url("https://DX.DOI.ORG/10.1000/ABC#ref"),
                         "https://doi.org/10.1000/abc")

    def test_invalid_or_credentialed_urls_are_rejected(self):
        self.assertIsNone(canonicalize_url("javascript:alert(1)"))
        self.assertIsNone(canonicalize_url("https://user:pass@example.com/a"))

    def test_fingerprint_ignores_retrieval_provenance(self):
        first = {"source": "github", "full_name": "a/b", "title": "A", "discovered_at": "2026-01-01"}
        second = {"source": "github", "full_name": "a/b", "title": "A", "discovered_at": "2026-02-01"}
        self.assertEqual(stable_metadata_fingerprint(first), stable_metadata_fingerprint(second))

    def test_expected_ids_distinguish_source_miss_from_ranking_miss(self):
        result = classify_expected_ids(
            ["missing", "present-but-not-ranked", "found", "found"],
            {"present-but-not-ranked", "found"},
            {"found"},
        )
        self.assertEqual([row["status"] for row in result],
                         ["source_snapshot_missing", "ranking_miss", "retrieved"])
        self.assertTrue(all(row["label_review"] == "human_review_required" for row in result))

    def test_query_level_audit_reports_source_and_ranking_misses_separately(self):
        from scripts.research_snapshot_audit import audit_qrels_retrieval

        snapshot = {
            "retrieved_at": "2026-11-07T00:00:00Z",
            "candidates": [
                {"full_name": "acme/agent-runtime", "description": "agent orchestration workflow engine"},
                {"full_name": "acme/quiet-project", "description": "garden flowers and irrigation"},
            ],
        }
        qrels = {
            "arabic_aliases": {},
            "queries": [{
                "query_id": "q1-en", "query": "agent orchestration workflow", "language": "en",
                "capability": "agents",
                "expected_ids": [
                    "github:acme/agent-runtime",
                    "github:acme/quiet-project",
                    "github:acme/not-in-snapshot",
                ],
            }],
        }
        report = audit_qrels_retrieval(snapshot, qrels)
        self.assertEqual(report["network_calls"], 0)
        self.assertEqual(report["alias_assisted"]["counts"], {
            "source_snapshot_missing": 1, "ranking_miss": 1, "retrieved": 1,
        })
        self.assertEqual(report["source_native"]["counts"], report["alias_assisted"]["counts"])
        statuses = [row["status"] for row in report["source_native"]["queries"][0]["expected"]]
        self.assertEqual(statuses, ["retrieved", "ranking_miss", "source_snapshot_missing"])


if __name__ == "__main__":
    unittest.main()
