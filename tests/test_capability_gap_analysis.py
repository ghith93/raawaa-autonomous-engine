import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_capability_gaps import analyze_capability_gaps


class CapabilityGapAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "engine").mkdir()
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "config" / "agents.json").write_text(
            json.dumps({"note": "Roster is declarative only."}), encoding="utf-8"
        )
        (self.root / "config" / "provider_profiles.json").write_text(
            json.dumps({"routes": [{"enabled": False}, {"enabled": False}]}), encoding="utf-8"
        )
        (self.root / "config" / "research_sources.json").write_text(
            json.dumps({
                "queries": {"agent_frameworks": "agents"},
                "academic_source": "Crossref public metadata",
                "arxiv_source": "arXiv Atom API",
                "searxng_source": "Optional self-hosted SearXNG",
            }), encoding="utf-8"
        )
        (self.root / "engine" / "agent_runtime.py").write_text("# deterministic", encoding="utf-8")
        (self.root / "engine" / "task_store.py").write_text("# durable task ledger", encoding="utf-8")
        (self.root / "engine" / "remote_task_store.py").write_text("# D1-backed task store", encoding="utf-8")
        (self.root / ".github" / "workflows" / "task-runner.yml").write_text("schedule: cron", encoding="utf-8")
        (self.root / "engine" / "memory.py").write_text("# local memory", encoding="utf-8")
        (self.root / "engine" / "skill_static_gate.py").write_text("# deterministic static preflight", encoding="utf-8")
        (self.root / ".github" / "workflows" / "research-discovery.yml").write_text(
            "if: vars.RAAWAA_ENGINE_RESEARCH_PERSISTENCE == 'enabled'", encoding="utf-8"
        )
        (self.root / "tests" / "test_basic.py").write_text("# tests", encoding="utf-8")

    def test_maps_topic_provenance_to_gaps_without_approving_candidates(self):
        triage = {
            "schema_version": 1,
            "source_counts": {"github": 2, "crossref": 1},
            "github_candidates": [
                {
                    "source_key": "github:example/agent",
                    "title": "example/agent",
                    "url": "https://example.invalid/agent",
                    "discovered_via": ["agent_frameworks", "coding_agents"],
                    "score": 90,
                    "priority": "high",
                    "license_review_required": True,
                    "security_review_required": True,
                },
                {
                    "source_key": "github:example/skills",
                    "title": "example/skills",
                    "url": "https://example.invalid/skills",
                    "discovered_via": ["agent_skills_security"],
                    "score": 70,
                    "priority": "medium",
                },
            ],
        }
        result = analyze_capability_gaps(triage, self.root)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["task_creation"])
        by_id = {item["capability"]: item for item in result["opportunities"]}
        self.assertEqual(by_id["agent_orchestration"]["candidate_count"], 1)
        self.assertEqual(by_id["coding_execution"]["candidate_count"], 1)
        self.assertEqual(by_id["skill_security"]["candidate_count"], 1)
        self.assertEqual(by_id["agent_orchestration"]["decision_status"], "PROPOSED")
        self.assertEqual(by_id["agent_orchestration"]["current_status"], "declarative_only")
        self.assertEqual(by_id["memory"]["current_status"], "local_memory_with_optional_research_persistence")
        self.assertEqual(by_id["model_routing"]["current_status"], "profiles_staged_disabled")
        self.assertEqual(by_id["skill_security"]["current_status"], "static_preflight_implemented_not_verified")
        self.assertEqual(by_id["skill_security"]["priority_score"], 4)
        self.assertEqual(by_id["skill_security"]["priority_band"], "medium")
        self.assertIn("qualitative heuristic", by_id["skill_security"]["scoring_method"])
        self.assertEqual(by_id["durable_execution"]["current_status"], "bounded_scheduled_runner")

    def test_missing_broker_and_research_sources_are_reported_without_guessing(self):
        result = analyze_capability_gaps(
            {"schema_version": 1, "github_candidates": []}, self.root
        )
        by_id = {item["capability"]: item for item in result["opportunities"]}
        self.assertEqual(by_id["tool_gateway"]["current_status"], "not_implemented")
        self.assertEqual(by_id["research_discovery"]["current_status"], "github_crossref_arxiv_searxng_optional")
        self.assertEqual(by_id["research_discovery"]["candidate_count"], 0)

    def test_malformed_priority_is_treated_as_unknown(self):
        triage = {
            "schema_version": 1,
            "github_candidates": [{
                "source_key": "github:example/one",
                "title": "example/one",
                "url": "https://example.invalid/one",
                "discovered_via": ["agent_frameworks"],
                "score": 50,
                "priority": [],
            }],
        }
        result = analyze_capability_gaps(triage, self.root)
        item = next(x for x in result["opportunities"] if x["capability"] == "agent_orchestration")
        self.assertEqual(item["candidate_shortlist"][0]["triage_priority"], "unknown")

    def test_candidates_without_topic_provenance_are_not_assigned_to_gaps(self):
        triage = {
            "schema_version": 1,
            "github_candidates": [{
                "source_key": "github:legacy/item",
                "title": "legacy/item",
                "url": "https://example.invalid/legacy",
                "score": 80,
                "priority": "high",
            }],
        }
        result = analyze_capability_gaps(triage, self.root)
        self.assertEqual(result["unmapped_github_candidate_count"], 1)
        self.assertTrue(all(item["candidate_count"] == 0 for item in result["opportunities"]))

    def test_all_opportunities_have_bounded_scores_and_reviewable_proposals(self):
        result = analyze_capability_gaps(
            {"schema_version": 1, "github_candidates": []}, self.root
        )
        self.assertEqual(len(result["task_proposals"]), 9)
        for opportunity in result["opportunities"]:
            self.assertGreaterEqual(opportunity["priority_score"], 0)
            self.assertLessEqual(opportunity["priority_score"], 7)
            self.assertIn(opportunity["risk_band"], {"low", "medium", "high"})
            self.assertIn(opportunity["cost_band"], {"zero", "low", "medium", "high"})
            self.assertIn(opportunity["complexity_band"], {"low", "medium", "high"})
        self.assertFalse(result["task_creation"])
        self.assertFalse(result["automatic_adoption"])
        for opportunity in result["opportunities"]:
            self.assertEqual(opportunity["decision_status"], "PROPOSED")
            self.assertFalse(opportunity["automatic_task_creation"])
            self.assertFalse(opportunity["automatic_adoption"])
        for proposal in result["task_proposals"]:
            self.assertEqual(proposal["task_type"], "capability_proposal")
            self.assertEqual(proposal["status"], "PROPOSED")
            self.assertTrue(proposal["acceptance_criteria"])
            self.assertTrue(proposal["rollback_plan"])
            self.assertTrue(proposal["independent_review_required"])
            self.assertTrue(proposal["owner_approval_required"])
            self.assertFalse(proposal["automatic_task_creation"])
            self.assertFalse(proposal["task_store_write"])
            self.assertFalse(proposal["automatic_execution"])
            self.assertFalse(proposal["automatic_adoption"])
            self.assertEqual(proposal["approval_state"], "not_requested")


    def test_rejects_unsupported_triage_schema(self):
        with self.assertRaisesRegex(ValueError, "schema_version 1"):
            analyze_capability_gaps({"schema_version": 2}, self.root)


if __name__ == "__main__":
    unittest.main()
