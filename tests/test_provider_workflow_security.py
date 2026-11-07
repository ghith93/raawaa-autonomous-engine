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
        self.assertIn("  eligibility:", source)
        self.assertIn("SOURCE_CONCLUSION: ${{ github.event.workflow_run.conclusion }}", source)
        self.assertIn("SOURCE_BRANCH: ${{ github.event.workflow_run.head_branch }}", source)
        self.assertIn("SOURCE_EVENT: ${{ github.event.workflow_run.event }}", source)
        self.assertIn("git log -1 --format=%B | grep -Fq '[groq-specialist-smoke]'", source)
        self.assertIn('[[ "$SOURCE_CONCLUSION" == "success" && "$SOURCE_BRANCH" == "main" && "$SOURCE_EVENT" == "push" && "$marker" == "true" ]]', source)
        self.assertIn("needs.eligibility.outputs.eligible == 'true'", source)
        self.assertIn("github.event.workflow_run.head_sha", source)
        self.assertEqual(source.count("persist-credentials: false"), 2)
        self.assertIn("Read back exact Staging task and invocation state (read-only)", source)
        self.assertIn("SELECT invocation_id,step_key,task_kind,status,last_route_id", source)
        self.assertIn("WHERE task_id='775916a6-8f2e-43c0-84aa-f3cc7712cf18'", source)
        self.assertIn("secret put ENGINE_RESEARCHER_LANE_TOKEN", source)
        self.assertIn("secret delete ENGINE_RESEARCHER_LANE_TOKEN", source)
        self.assertIn("RAAWAA_ENGINE_WORKER_LANE: specialist:researcher", source)
        self.assertIn('test -n "$RAAWAA_ENGINE_STAGING_RESEARCHER_LANE_TOKEN"', source)
        self.assertIn('export RAAWAA_ENGINE_LANE_TOKEN="$RAAWAA_ENGINE_STAGING_RESEARCHER_LANE_TOKEN"', source)
        self.assertIn('export RAAWAA_ENGINE_RESEARCHER_LANE_TOKEN="$RAAWAA_ENGINE_STAGING_RESEARCHER_LANE_TOKEN"', source)
        self.assertNotIn("RAAWAA_ENGINE_LANE_TOKEN: ${{ env.RAAWAA_ENGINE_STAGING_RESEARCHER_LANE_TOKEN }}", source)
        self.assertNotIn("RAAWAA_ENGINE_STAGING_API_TOKEN: ${{ env.RAAWAA_ENGINE_STAGING_API_TOKEN }}", source)
        self.assertIn("RAAWAA_ENGINE_LIVE_INFERENCE: enabled", source)
        self.assertIn("per_task_budget_nanos=0", source)
        self.assertIn('mp.get("paid_fallback_enabled") is not False', source) 
        script = (ROOT / "scripts" / "run_groq_free_inference_smoke.py").read_text(encoding="utf-8")
        self.assertIn('EXECUTION_LANE = "specialist:researcher"', script)
        self.assertIn("execution_lane=EXECUTION_LANE", script)
        self.assertIn("worker_lane=EXECUTION_LANE", script)
        self.assertIn("list_tasks(100, worker_lane=EXECUTION_LANE)", script)

    def test_groq_staging_smoke_requires_verified_cloudflare_zero_cost_before_any_side_effect(self):
        workflow = ROOT / ".github" / "workflows" / "groq-free-inference-staging-smoke.yml"
        source = workflow.read_text(encoding="utf-8")
        gate_name = "name: Verify Cloudflare zero-cost boundary before Groq Staging side effects"
        token = "name: Generate ephemeral Staging bearer token"
        deploy = "name: Deploy isolated Staging Worker with one temporary GPT-OSS 20B quota route"
        d1_write = "name: Enforce zero-USD hard caps in isolated Staging D1"
        install_token = "name: Install ephemeral Staging API token"
        inference = "name: Run one bounded Groq Free inference and verify zero spend"
        gate = source.index(gate_name)
        self.assertLess(gate, source.index(token))
        self.assertLess(gate, source.index(deploy))
        self.assertLess(gate, source.index(d1_write))
        self.assertLess(gate, source.index(install_token))
        self.assertLess(gate, source.index(inference))
        block = source[gate:source.index(token)]
        self.assertIn("scripts/verify_cloudflare_zero_cost_boundary.py", block)
        self.assertIn("CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}", block)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", block)
        self.assertNotIn("continue-on-error", block)
        self.assertNotIn("if: always()", block)

    def test_ci_telemetry_publisher_is_main_only_and_staging_only(self):
        workflow = ROOT / ".github" / "workflows" / "ci.yml"
        source = workflow.read_text(encoding="utf-8")
        marker = "  publish-staging-ops-telemetry:"
        start = source.index(marker)
        end = source.index("\n  staging-mobile-status-verify:", start)
        job = source[start:end]
        self.assertIn("needs: [test, staging-migrate, staging-worker-verify, zero_cost_preflight]", job)
        self.assertIn("github.event_name == 'push'", job)
        self.assertNotIn("workflow_dispatch", job)
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
        self.assertIn("needs.zero_cost_preflight.outputs.zero_cost_eligible == 'true'", verify)
        self.assertIn("needs.publish-staging-ops-telemetry.result == 'success'", verify)
        self.assertIn("needs.staging-worker-verify.result == 'success'", verify)
        self.assertIn("secret put ENGINE_API_TOKEN", verify)
        self.assertIn("secret delete ENGINE_API_TOKEN", verify)
        self.assertIn("python scripts/verify_staging_mobile_status.py", verify)
        self.assertIn("steps.install_staging_token.outcome == 'success'", verify)

class CloudflareZeroCostEffectWorkflowTests(unittest.TestCase):
    """Every workflow that mutates Cloudflare or invokes Workers AI must fail closed first."""

    def _assert_gate_precedes(self, path, gate, effects):
        source = (ROOT / path).read_text(encoding="utf-8")
        gate_at = source.index(gate)
        for effect in effects:
            with self.subTest(path=path, effect=effect):
                self.assertLess(gate_at, source.index(effect))
        block = source[gate_at:source.index(effects[0])]
        self.assertIn("scripts/verify_cloudflare_zero_cost_boundary.py", block)
        self.assertIn("CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}", block)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", block)
        self.assertNotIn("continue-on-error", block)
        self.assertNotIn("if: always()", block)

    def test_production_d1_migration_requires_zero_cost_preflight(self):
        self._assert_gate_precedes(
            ".github/workflows/d1-migrate.yml",
            "name: Verify Cloudflare zero-cost boundary before Production D1 migration",
            [
                "name: Show remote migration status",
                "name: Apply reviewed migrations to the confirmed D1 database",
            ],
        )

    def test_manual_staging_p1_requires_zero_cost_preflight(self):
        self._assert_gate_precedes(
            ".github/workflows/staging-p1-task-proof.yml",
            "name: Verify Cloudflare zero-cost boundary before Staging P1 side effects",
            [
                "name: Generate an ephemeral Staging-only bearer token",
                "name: Install temporary token on the isolated Staging Worker",
                "name: Enqueue, claim, execute, and read back exactly one harmless task",
            ],
        )

    def test_scheduled_probe_requires_cloudflare_free_proof_and_blocks_unverified_supabase_fallback(self):
        path = ".github/workflows/staging-supervised-process-probe.yml"
        source = (ROOT / path).read_text(encoding="utf-8")
        required = "name: Require Cloudflare credentials before any remote scheduled task"
        gate = "name: Verify Cloudflare zero-cost boundary before scheduled Staging effects"
        token = "name: Generate an ephemeral Staging-only bearer token"
        install = "name: Install temporary token on the isolated Staging Worker"
        probe = "name: Enqueue, claim, and execute exactly one read-only supervised task"
        self.assertLess(source.index(required), source.index(gate))
        self.assertLess(source.index(gate), source.index(token))
        self.assertLess(source.index(gate), source.index(install))
        self.assertLess(source.index(gate), source.index(probe))
        required_at = source.index(required)
        required_start = source.rfind("      - name:", 0, required_at + len(required))
        required_end = source.find("      - name:", required_start + 1)
        required_block = source[required_start:required_end]
        self.assertIn("steps.cloudflare_credentials.outputs.available != 'true'", required_block)
        gate_at = source.index(gate)
        gate_start = source.rfind("      - name:", 0, gate_at + len(gate))
        gate_end = source.find("      - name:", gate_start + 1)
        gate_step = source[gate_start:gate_end]
        self.assertIn("steps.cloudflare_credentials.outputs.available == 'true'", gate_step)
        block = source[source.index(gate):source.index(token)]
        self.assertIn("scripts/verify_cloudflare_zero_cost_boundary.py", block)
        self.assertNotIn("continue-on-error", block)
        self.assertNotIn("id-token: write", source)
        self.assertIn('RAAWAA_ENGINE_SUPABASE_FALLBACK_ZERO_COST_VERIFIED: "false"', source)

    def test_storefront_production_publish_requires_zero_cost_preflight(self):
        self._assert_gate_precedes(
            ".github/workflows/deploy-commerce-storefront.yml",
            "name: Verify Cloudflare zero-cost boundary before storefront deployment",
            ["name: Publish Brand-Only Pages URL & Retire Legacy Worker Subdomain"],
        )

    def test_workers_ai_image_generation_requires_zero_cost_preflight(self):
        self._assert_gate_precedes(
            ".github/workflows/raawaa-image-route-check.yml",
            "name: Verify Cloudflare zero-cost boundary before Workers AI inference",
            ["name: Generate one real image through the free route"],
        )


if __name__ == "__main__":
    unittest.main()
