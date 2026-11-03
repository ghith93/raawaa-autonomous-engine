import unittest
from datetime import datetime, timezone

from scripts.triage_research_candidates import score_github_candidate


class ResearchCandidateTriageTests(unittest.TestCase):
    def test_discovery_topic_provenance_is_preserved(self):
        candidate = {
            "full_name": "example/agent",
            "url": "https://example.invalid/agent",
            "license_spdx": "MIT",
            "pushed_at": "2026-11-02T00:00:00Z",
            "stars": 100,
            "language": "Python",
            "description": "agent framework",
            "archived": False,
            "topics": ["agents"],
            "discovered_via": ["agent_frameworks", "coding_agents"],
        }
        result = score_github_candidate(
            candidate, datetime(2026, 11, 3, tzinfo=timezone.utc)
        )
        self.assertEqual(result["discovered_via"], ["agent_frameworks", "coding_agents"])
        self.assertEqual(result["automatic_adoption"], False)
        self.assertEqual(result["review_status"], "unreviewed")


if __name__ == "__main__":
    unittest.main()
