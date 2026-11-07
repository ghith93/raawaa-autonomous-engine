from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "research-discovery.yml"


class ResearchDiscoveryWorkflowAutomationTests(unittest.TestCase):
    def setUp(self):
        self.content = WORKFLOW.read_text(encoding="utf-8")

    def test_routine_research_has_schedule_and_no_manual_dispatch(self):
        self.assertIn('cron: "31 5 * * *"', self.content)
        self.assertNotIn("\n  workflow_dispatch:", self.content)
        self.assertNotIn("RAAWAA_ENGINE_RESEARCH_PERSISTENCE", self.content)
        self.assertIn("cancel-in-progress: false", self.content)

    def test_research_writes_require_the_zero_cost_preflight(self):
        self.assertIn("scripts/verify_cloudflare_zero_cost_boundary.py", self.content)
        self.assertIn("id: research_zero_cost_preflight", self.content)
        self.assertIn("continue-on-error: true", self.content)
        self.assertIn("steps.research_zero_cost_preflight.outputs.zero_cost_eligible == 'true'", self.content)

    def test_ephemeral_token_and_schema_check_precede_persistence_and_cleanup(self):
        names = [
            "Verify idempotent research schema in isolated Staging D1",
            "Generate ephemeral Staging research API token",
            "Install ephemeral Staging research API token",
            "Verify Staging Worker exposes the research idempotency schema",
            "Persist public research metadata in isolated Staging Worker",
            "Revoke ephemeral Staging research API token",
        ]
        positions = [self.content.index("- name: " + name) for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("scripts/verify_research_persistence_schema.py", self.content)
        self.assertIn("wrangler@4.156.1 secret put ENGINE_API_TOKEN --config wrangler.staging.toml", self.content)
        self.assertIn("wrangler@4.156.1 secret delete ENGINE_API_TOKEN --config wrangler.staging.toml", self.content)
        self.assertNotIn("secrets.ENGINE_STAGING_API_TOKEN", self.content)
        self.assertIn("https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev", self.content)
        self.assertIn("scripts/verify_research_persistence_worker.py", self.content)

    def test_research_telemetry_is_staging_d1_only_and_uses_no_engine_control_token(self):
        self.assertIn("Publish explicit scan and source freshness telemetry to Staging D1", self.content)
        self.assertNotIn("raawaa-autonomous-engine-control", self.content)
        self.assertNotIn("secrets.ENGINE_API_TOKEN", self.content)
        self.assertNotIn("secrets.CF_ACCESS_CLIENT_ID", self.content)
        self.assertNotIn("secrets.CF_ACCESS_CLIENT_SECRET", self.content)

    def test_research_persistence_serializes_with_staging_worker_verification(self):
        discover = self.content.split("  knowledge_graph:", 1)[0]
        self.assertIn("group: d1-staging-worker-verification", discover)
        self.assertIn("cancel-in-progress: false", discover)


if __name__ == "__main__":
    unittest.main()
