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


    def test_arxiv_candidate_is_scored_without_license_or_adoption_claims(self):
        from scripts.triage_research_candidates import score_arxiv_candidate, triage_candidates
        paper = {
            "arxiv_id": "2611.00001v1",
            "title": "Multi-agent LLM tool use and evaluation",
            "url": "https://arxiv.org/abs/2611.00001v1",
            "published": "2026-11-02T00:00:00Z",
            "categories": ["cs.AI"],
            "primary_category": "cs.AI",
        }
        scored = score_arxiv_candidate(paper, datetime(2026, 11, 3, tzinfo=timezone.utc).date())
        self.assertEqual(scored["source"], "arxiv")
        self.assertEqual(scored["source_key"], "arxiv:2611.00001v1")
        self.assertTrue(scored["license_review_required"])
        self.assertIn("license_not_verified", scored["risk_flags"])
        self.assertFalse(scored["automatic_adoption"])
        result = triage_candidates({
            "candidates": [],
            "academic_candidates": [],
            "arxiv_candidates": [paper],
        }, datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertEqual(result["source_counts"]["arxiv"], 1)
        self.assertEqual(len(result["arxiv_candidates"]), 1)
        self.assertEqual(result["all_candidates"][0]["source"], "arxiv")


    def test_searxng_candidates_are_manual_review_only_and_license_unverified(self):
        from scripts.triage_research_candidates import score_searxng_candidate, triage_candidates
        result = score_searxng_candidate({
            "source_key": "searxng:https://example.org/agent",
            "title": "Agent tool security",
            "url": "https://example.org/agent",
            "snippet": "Prompt injection and tool security research",
            "engines": ["brave"],
        }, datetime(2026, 11, 3, tzinfo=timezone.utc).date())
        self.assertEqual(result["source"], "searxng")
        self.assertTrue(result["license_review_required"])
        self.assertTrue(result["security_review_required"])
        self.assertIn("license_not_verified", result["risk_flags"])
        self.assertFalse(result["automatic_adoption"])
        triaged = triage_candidates({
            "candidates": [],
            "academic_candidates": [],
            "arxiv_candidates": [],
            "searxng_candidates": [{
                "source_key": "searxng:https://example.org/agent",
                "title": "Agent tool security",
                "url": "https://example.org/agent",
                "snippet": "Prompt injection and tool security research",
            }],
        }, datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertEqual(triaged["source_counts"]["searxng"], 1)
        self.assertEqual(triaged["searxng_candidates"][0]["source"], "searxng")


if __name__ == "__main__":
    unittest.main()
