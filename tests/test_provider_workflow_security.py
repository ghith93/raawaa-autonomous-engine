"""Static security contract for Task Runner provider credential scoping."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "task-runner.yml"

class ProviderWorkflowSecurityTests(unittest.TestCase):
    def test_provider_credentials_are_scoped_only_to_bounded_batch_step(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        start = source.index("      - name: Process bounded D1 task batch")
        end = source.index("      - name: Publish bounded Task Runner status", start)
        batch = source[start:end]
        expected = {
            "GROQ_API_KEY": "${{ secrets.GROQ_API_KEY }}",
            "GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}",
            "RAAWAA_ENGINE_API_TOKEN": "${{ secrets.ENGINE_API_TOKEN }}",
            "RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID": "${{ secrets.CF_ACCESS_CLIENT_ID }}",
            "RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET": "${{ secrets.CF_ACCESS_CLIENT_SECRET }}",
        }
        for variable, expression in expected.items():
            self.assertIn(variable + ": " + expression, batch)
            assignment_count = sum(
                1 for line in source.splitlines()
                if line.strip().startswith(variable + ":")
            )
            self.assertEqual(assignment_count, 1, f"{variable} was assigned outside the batch step")
        for expression in ("$" + "{{ secrets.GROQ_API_KEY }}", "$" + "{{ secrets.GEMINI_API_KEY }}"):
            self.assertEqual(source.count(expression), 1, "provider API key reference leaked outside the batch step")
        self.assertIn(
            "RAAWAA_ENGINE_API_URL: https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev",
            batch,
        )

    def test_provider_credentials_are_not_echoed_or_published(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        for variable in ("GROQ_API_KEY", "GEMINI_API_KEY", "RAAWAA_ENGINE_API_TOKEN"):
            self.assertNotIn(f'echo "${{{variable}}}"', source)
            self.assertNotIn(f'output.write(f"{variable}=', source)

    def test_groq_key_is_scoped_only_to_read_only_catalog_step(self):
        workflow = ROOT / ".github" / "workflows" / "groq-connection-check.yml"
        source = workflow.read_text(encoding="utf-8")
        step_name = "      - name: Verify Groq API authentication and Free model catalog (read only)"
        start = source.index(step_name)
        step = source[start:]
        expression = "${{ secrets.GROQ_API_KEY }}"
        self.assertIn("GROQ_API_KEY: " + expression, step)
        self.assertEqual(source.count(expression), 1)
        self.assertNotIn("    env:\n      GROQ_API_KEY:", source)
        self.assertIn("python scripts/groq_connection_check.py", step)

    def test_staging_verification_waits_for_both_consumer_capabilities(self):
        workflow = ROOT / ".github" / "workflows" / "ci.yml"
        source = workflow.read_text(encoding="utf-8")
        start = source.index("  staging-worker-verify:")
        end = source.index("\n  publish-staging-ops-telemetry:", start)
        job = source[start:end]
        self.assertIn("Wait for temporary Staging consumer capabilities to propagate", job)
        self.assertIn('("general", "/v1/tasks?limit=1", {})', job)
        self.assertIn('("researcher", "/v1/tasks?limit=1&worker_lane=specialist%3Aresearcher"', job)
        self.assertIn('"X-Engine-Lane-Token": lane_token', job)
        self.assertIn('statuses == {"general": 200, "researcher": 200}', job)
        self.assertIn("secret delete ENGINE_RESEARCHER_LANE_TOKEN", job)

    def test_groq_free_smoke_uses_ephemeral_researcher_lane_capability(self):
        workflow = ROOT / ".github" / "workflows" / "groq-free-inference-staging-smoke.yml"
        source = workflow.read_text(encoding="utf-8")
        self.assertIn('workflows: ["Engine CI"]', source)
        self.assertIn("types: [completed]", source)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", source)
        self.assertIn("[groq-specialist-smoke]", source)
        self.assertIn("github.event.workflow_run.head_sha", source)
        self.assertIn("secret put ENGINE_RESEARCHER_LANE_TOKEN", source)
        self.assertIn("secret delete ENGINE_RESEARCHER_LANE_TOKEN", source)
        self.assertIn("RAAWAA_ENGINE_WORKER_LANE: specialist:researcher", source)
        self.assertIn("RAAWAA_ENGINE_LANE_TOKEN: ${{ env.RAAWAA_ENGINE_STAGING_RESEARCHER_LANE_TOKEN }}", source)
        self.assertIn("RAAWAA_ENGINE_LIVE_INFERENCE: enabled", source)
        self.assertIn("per_task_budget_nanos=0", source)
        self.assertIn('mp.get("paid_fallback_enabled") is not False', source) 
        script = (ROOT / "scripts" / "run_groq_free_inference_smoke.py").read_text(encoding="utf-8")
        self.assertIn('EXECUTION_LANE = "specialist:researcher"', script)
        self.assertIn("execution_lane=EXECUTION_LANE", script)
        self.assertIn("worker_lane=EXECUTION_LANE", script)
        self.assertIn("list_tasks(100, worker_lane=EXECUTION_LANE)", script)

    def test_ci_telemetry_publisher_is_main_only_and_staging_only(self):
        workflow = ROOT / ".github" / "workflows" / "ci.yml"
        source = workflow.read_text(encoding="utf-8")
        marker = "  publish-staging-ops-telemetry:"
        start = source.index(marker)
        end = source.index("\n  staging-mobile-status-verify:", start)
        job = source[start:end]
        self.assertIn("needs: [test, staging-migrate, staging-worker-verify]", job)
        self.assertIn("github.event_name == 'push'", job)
        self.assertIn("github.ref == 'refs/heads/main'", job)
        self.assertIn("python scripts/publish_staging_ops_telemetry.py", job)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", job)
        self.assertNotIn("ENGINE_API_TOKEN", job)
        self.assertNotIn("wrangler.toml", job)
        script = (ROOT / "scripts" / "publish_staging_ops_telemetry.py").read_text(encoding="utf-8")
        self.assertIn('STAGING_DATABASE_ID = "231dd948-04a0-49e3-ad73-444f77d69477"', script)
        self.assertIn('PRODUCTION_DATABASE_ID = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"', script)
        self.assertIn("client.query(", script)

    def test_staging_mobile_status_verification_uses_only_ephemeral_token(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        verify = workflow.split("  staging-mobile-status-verify:", 1)[1]
        self.assertIn("needs.publish-staging-ops-telemetry.result == 'success'", verify)
        self.assertIn("needs.staging-worker-verify.result == 'success'", verify)
        self.assertIn("secret put ENGINE_API_TOKEN", verify)
        self.assertIn("secret delete ENGINE_API_TOKEN", verify)
        self.assertIn("python scripts/verify_staging_mobile_status.py", verify)
        self.assertIn("steps.install_staging_token.outcome == 'success'", verify)

if __name__ == "__main__":
    unittest.main()
