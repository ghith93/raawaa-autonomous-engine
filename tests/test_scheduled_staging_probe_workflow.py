from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "staging-supervised-process-probe.yml"


class ScheduledStagingProbeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = WORKFLOW.read_text(encoding="utf-8")

    def test_schedule_is_daily_and_not_manually_dispatchable(self):
        self.assertIn("schedule:", self.source)
        self.assertIn('cron: "47 3 * * *"', self.source)
        self.assertNotIn("workflow_dispatch:", self.source)
        self.assertIn("github.event_name == 'schedule'", self.source)
        self.assertIn("github.ref == 'refs/heads/main'", self.source)

    def test_workflow_is_staging_only_and_has_temporary_token_cleanup(self):
        self.assertIn("https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev", self.source)
        self.assertNotIn("https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev", self.source)
        self.assertIn("d1-staging-worker-verification", self.source)
        self.assertIn("secret delete ENGINE_API_TOKEN --config wrangler.staging.toml", self.source)
        self.assertIn("always() && steps.install_staging_token.outcome != 'skipped'", self.source)
        self.assertIn("::notice title=Scheduled Staging probe outcome::", self.source)
        self.assertIn("actions/upload-artifact@", self.source)
        self.assertIn("probe-output.txt", self.source)
        self.assertIn("Wait for the ephemeral Staging token to propagate", self.source)
        self.assertIn('"User-Agent": "RaawaaAutonomousEngine/1.0', self.source)
        self.assertIn("RAAWAA_ENGINE_PROCESS_SUPERVISOR: enabled", self.source)
        self.assertIn("RAAWAA_ENGINE_LIVE_INFERENCE: disabled", self.source)
        self.assertNotIn("id-token: write", self.source)
        self.assertIn("RAAWAA_ENGINE_SUPABASE_FALLBACK_ZERO_COST_VERIFIED: \"false\"", self.source)
        # The standby heartbeat RPC performs a persistent write; the
        # scheduled workflow must never enable it.
        self.assertNotIn("RAAWAA_ENGINE_FALLBACK_STANDBY_CHECK", self.source)
        self.assertIn("steps.cloudflare_credentials.outputs.available == 'true'", self.source)
        self.assertIn("continue-on-error: true", self.source)

    def test_zero_cost_gate_precedes_all_remote_work_and_disables_unverified_fallback(self):
        required = "name: Require Cloudflare credentials before any remote scheduled task"
        gate = "name: Verify Cloudflare zero-cost boundary before scheduled Staging effects"
        token = "name: Generate an ephemeral Staging-only bearer token"
        install = "name: Install temporary token on the isolated Staging Worker"
        probe = "name: Enqueue, claim, and execute exactly one read-only supervised task"
        self.assertLess(self.source.index(required), self.source.index(gate))
        self.assertLess(self.source.index(gate), self.source.index(token))
        self.assertLess(self.source.index(gate), self.source.index(install))
        self.assertLess(self.source.index(gate), self.source.index(probe))
        self.assertIn("scripts/verify_cloudflare_zero_cost_boundary.py", self.source)
        self.assertIn("remote Cloudflare and Supabase fallback execution are both blocked", self.source)
        self.assertNotIn("id-token: write", self.source)
        self.assertIn("RAAWAA_ENGINE_SUPABASE_FALLBACK_ZERO_COST_VERIFIED: \"false\"", self.source)

    def test_workflow_never_receives_provider_or_production_secrets(self):
        for secret in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(f"secrets.{secret}", self.source)
        self.assertNotIn("CLOUDFLARE_API_TOKEN.*wrangler.toml", self.source)
        self.assertIn("wrangler.staging.toml", self.source)
        self.assertNotIn("wrangler.toml deploy", self.source)

    def test_workflow_calls_only_the_narrow_staging_probe_runner(self):
        self.assertIn("python scripts/run_staging_scheduled_process_probe.py", self.source)
        self.assertIn("Enqueue, claim, and execute exactly one read-only supervised task", self.source)


if __name__ == "__main__":
    unittest.main()
