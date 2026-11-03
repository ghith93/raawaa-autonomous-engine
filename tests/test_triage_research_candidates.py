import unittest
from datetime import datetime, timezone

from scripts.triage_research_candidates import triage_candidates


class ResearchTriageTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def test_recent_permissively_licensed_python_project_ranks_high(self):
        result = triage_candidates({
            "candidates": [{
                "full_name": "microsoft/agent-framework",
                "url": "https://github.com/microsoft/agent-framework",
                "license_spdx": "MIT",
                "pushed_at": "2026-10-30T00:00:00Z",
                "stars": 13000,
                "language": "Python",
                "archived": False,
                "description": "Agent framework",
                "discovered_via": ["agent_frameworks"],
            }],
            "academic_candidates": [],
        }, self.now)
        item = result["github_candidates"][0]
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["score"], 100)
        self.assertTrue(item["permissive_license_signal"])
        self.assertTrue(item["license_review_required"])
        self.assertTrue(item["cost_review_required"])
        self.assertTrue(item["security_review_required"])
        self.assertFalse(item["automatic_adoption"])
        self.assertEqual(result["model_calls"], 0)

    def test_low_adoption_repository_keeps_score_but_is_not_high_priority(self):
        result = triage_candidates({
            "candidates": [{
                "full_name": "org/new-project",
                "url": "https://github.com/org/new-project",
                "license_spdx": "Apache-2.0",
                "pushed_at": "2026-10-30T00:00:00Z",
                "stars": 4,
                "language": "Python",
                "archived": False,
                "description": "Agent framework",
                "discovered_via": ["agent_frameworks"],
            }],
            "academic_candidates": [],
        }, self.now)
        item = result["github_candidates"][0]
        self.assertGreaterEqual(item["score"], 70)
        self.assertEqual(item["priority"], "medium")
        self.assertIn("low_public_adoption_signal", item["risk_flags"])

    def test_missing_and_unrecognized_licenses_cannot_be_high_priority(self):
        for license_id in (None, "NOASSERTION"):
            with self.subTest(license_id=license_id):
                result = triage_candidates({
                    "candidates": [{
                        "full_name": "org/license-unknown",
                        "url": "https://github.com/org/license-unknown",
                        "license_spdx": license_id,
                        "pushed_at": "2026-10-30T00:00:00Z",
                        "stars": 5000,
                        "language": "Python",
                        "archived": False,
                        "description": "Agent framework",
                        "discovered_via": ["agent_frameworks"],
                    }],
                    "academic_candidates": [],
                }, self.now)
                item = result["github_candidates"][0]
                self.assertGreaterEqual(item["score"], 70)
                self.assertEqual(item["priority"], "medium")

    def test_copyleft_license_cannot_be_high_priority(self):
        for license_id in ("GPL-3.0", "AGPL-3.0"):
            with self.subTest(license_id=license_id):
                result = triage_candidates({
                    "candidates": [{
                        "full_name": "org/copyleft-project",
                        "url": "https://github.com/org/copyleft-project",
                        "license_spdx": license_id,
                        "pushed_at": "2026-10-30T00:00:00Z",
                        "stars": 5000,
                        "language": "Python",
                        "archived": False,
                        "description": "Agent framework",
                        "discovered_via": ["agent_frameworks"],
                    }],
                    "academic_candidates": [],
                }, self.now)
                item = result["github_candidates"][0]
                self.assertGreaterEqual(item["score"], 70)
                self.assertEqual(item["priority"], "medium")

    def test_archived_and_unlicensed_repository_is_low_priority(self):
        result = triage_candidates({
            "candidates": [{
                "full_name": "org/old",
                "url": "https://github.com/org/old",
                "license_spdx": None,
                "pushed_at": "2020-01-01T00:00:00Z",
                "stars": 0,
                "language": "PHP",
                "archived": True,
                "description": "",
            }],
            "academic_candidates": [],
        }, self.now)
        item = result["github_candidates"][0]
        self.assertEqual(item["priority"], "low")
        self.assertIn("repository_archived", item["risk_flags"])
        self.assertIn("no_license_metadata", item["risk_flags"])
        self.assertTrue(item["license_review_required"])

    def test_recent_academic_work_is_prioritized_but_rights_review_remains_required(self):
        result = triage_candidates({
            "candidates": [],
            "academic_candidates": [{
                "doi": "10.1234/agents",
                "title": "Autonomous LLM Agents and Multi-Agent Orchestration",
                "url": "https://doi.org/10.1234/agents",
                "published": "2026-4-1",
                "citation_count": 20,
                "license_urls": [],
            }],
        }, self.now)
        item = result["academic_candidates"][0]
        self.assertEqual(item["priority"], "high")
        self.assertTrue(item["full_text_rights_review_required"])
        self.assertTrue(item["license_review_required"])
        self.assertEqual(item["review_status"], "unreviewed")

    def test_unknown_dates_and_bad_shapes_fail_safe(self):
        result = triage_candidates({
            "candidates": [{"full_name": "org/no-date", "pushed_at": "not-a-date", "stars": -1}],
            "academic_candidates": [{"doi": "10.1234/x", "title": "Other", "published": "bad"}],
        }, self.now)
        self.assertEqual(result["source_counts"], {"github": 1, "crossref": 1})
        self.assertIn("stale_or_missing_activity_timestamp", result["github_candidates"][0]["risk_flags"])
        self.assertIn("publication_date_missing", result["academic_candidates"][0]["risk_flags"])
        self.assertFalse(result["automatic_adoption"])


if __name__ == "__main__":
    unittest.main()
