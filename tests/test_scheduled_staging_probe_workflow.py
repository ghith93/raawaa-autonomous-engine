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
        self.assertIn("Wait for the ephemeral Staging token to propagate", self.source)
        self.assertIn("RAAWAA_ENGINE_PROCESS_SUPERVISOR: enabled", self.source)
        self.assertIn("RAAWAA_ENGINE_LIVE_INFERENCE: disabled", self.source)
        self.assertIn("id-token: write", self.source)
        self.assertIn("RAAWAA_ENGINE_FALLBACK_STANDBY_CHECK: enabled", self.source)
        self.assertIn("steps.cloudflare_credentials.outputs.available == 'true'", self.source)
        self.assertIn("continue-on-error: true", self.source)

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
