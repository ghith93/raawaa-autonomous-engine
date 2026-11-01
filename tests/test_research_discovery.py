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

    def test_failure_is_reported_without_exposing_response_body(self):
        def fail(query, token): raise ResearchDiscoveryError("GitHub search returned HTTP 403; rate_limit_remaining=0")
        result = discover_repositories({"agents": "agent framework"}, {}, request_fn=fail)
        self.assertEqual(result["requests_made"], 0)
        self.assertIn("agents", result["unavailable"])
        self.assertEqual(result["candidate_count"], 0)

if __name__ == "__main__": unittest.main()
