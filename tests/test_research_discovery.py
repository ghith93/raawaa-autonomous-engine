import unittest
from datetime import datetime, timezone
from engine.research_discovery import ResearchDiscoveryError, discover_repositories

class ResearchDiscoveryTests(unittest.TestCase):
    def test_discovery_returns_metadata_only_candidates_and_deduplicates(self):
        payloads = [
            {"items": [{"full_name": "org/agent-a", "html_url": "https://github.com/org/agent-a", "description": "agent framework", "stargazers_count": 120, "language": "Python", "license": {"spdx_id": "MIT"}, "pushed_at": "2026-10-20T00:00:00Z", "archived": False, "topics": ["agents"]}, {"full_name": "org/shared", "html_url": "https://github.com/org/shared", "stargazers_count": 5, "pushed_at": "2026-10-01T00:00:00Z"}]},
            {"items": [{"full_name": "org/shared", "html_url": "https://github.com/org/shared", "stargazers_count": 5, "pushed_at": "2026-10-01T00:00:00Z"}, {"full_name": "org/agent-b", "html_url": "https://github.com/org/agent-b", "stargazers_count": 20, "license": None, "archived": False}]},
        ]
        calls = []
        def fake_request(query, token):
            calls.append((query, token))
            return payloads[len(calls) - 1]
        result = discover_repositories({"agents": "agent framework", "memory": "agent memory"}, {"GITHUB_TOKEN": "test-token"}, request_fn=fake_request, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "test-token")
        self.assertEqual(result["candidate_count"], 3)
        shared = next(item for item in result["candidates"] if item["full_name"] == "org/shared")
        self.assertEqual(shared["discovered_via"], ["agents", "memory"])
        candidate = next(item for item in result["candidates"] if item["full_name"] == "org/agent-a")
        self.assertEqual(candidate["license_spdx"], "MIT")
        self.assertEqual(candidate["review_status"], "unreviewed")
        self.assertFalse(candidate["license_verified"])
        self.assertFalse(result["code_execution"])
        self.assertFalse(result["installation_or_cloning"])
        self.assertEqual(result["generation_requests"], 0)

    def test_query_and_limit_validation(self):
        with self.assertRaises(ValueError): discover_repositories({}, {})
        with self.assertRaises(ValueError): discover_repositories({"x": "y"}, {}, max_per_query=11)
        calls = []
        bounded = discover_repositories({"x": "y", "z": "q"}, {}, max_queries=1, request_fn=lambda q, token: (calls.append(q) or {"items": []}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(bounded["queries_attempted"], 1)

    def test_query_count_is_bounded(self):
        calls = []
        result = discover_repositories({"a": "one", "b": "two", "c": "three"}, {}, max_queries=2, request_fn=lambda q, token: (calls.append(q) or {"items": []}))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["queries_attempted"], 2)

    def test_crossref_metadata_is_normalized_without_abstract_or_licence_claims(self):
        def fake_crossref(query, limit):
            self.assertEqual(limit, 5)
            return {"items": [{"DOI": "10.1234/agent-paper", "title": ["Agent orchestration research"], "author": [{"given": "Ada", "family": "Lovelace"}], "published": {"date-parts": [[2025, 4, 2]]}, "URL": "https://doi.org/10.1234/agent-paper", "container-title": ["Journal of Agents"], "is-referenced-by-count": 12, "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}], "type": "journal-article"}, {"DOI": "10.1234/no-title", "title": []}]}
        result = discover_repositories({"agents": "agent framework"}, {}, request_fn=lambda q, token: {"items": []}, academic_query="agent memory", max_academic_results=5, academic_request_fn=fake_crossref)
        self.assertEqual(result["academic_requests_made"], 1)
        self.assertEqual(result["academic_candidates"][0]["doi"], "10.1234/agent-paper")
        self.assertEqual(result["academic_candidates"][0]["authors"], ["Ada Lovelace"])
        self.assertEqual(result["academic_candidates"][0]["published"], "2025-4-2")
        self.assertEqual(result["academic_candidates"][0]["citation_count"], 12)
        self.assertFalse(result["academic_candidates"][0]["license_verified"])
        self.assertFalse(result["academic_candidates"][0]["abstract_retrieved"])
        self.assertEqual(result["generation_requests"], 0)

    def test_crossref_failure_is_nonfatal_and_reported(self):
        def fail(query, limit): raise ResearchDiscoveryError("Crossref returned HTTP 429")
        result = discover_repositories({"agents": "agent framework"}, {}, request_fn=lambda q, token: {"items": []}, academic_query="agent memory", academic_request_fn=fail)
        self.assertEqual(result["academic_requests_made"], 0)
        self.assertIn("429", result["academic_unavailable"])
        self.assertEqual(result["candidate_count"], 0)
    def test_failure_is_reported_without_exposing_response_body(self):
        def fail(query, token): raise ResearchDiscoveryError("GitHub search returned HTTP 403; rate_limit_remaining=0")
        result = discover_repositories({"agents": "agent framework"}, {}, request_fn=fail)
        self.assertEqual(result["requests_made"], 0)
        self.assertIn("agents", result["unavailable"])
        self.assertEqual(result["candidate_count"], 0)

if __name__ == "__main__": unittest.main()
