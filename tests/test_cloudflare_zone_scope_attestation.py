"""All Cloudflare remote-effect workflows require an explicit all-zones token-scope attestation."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
GATED_WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/cloudflare-billing-inventory.yml",
    ".github/workflows/d1-migrate.yml",
    ".github/workflows/deploy-commerce-storefront.yml",
    ".github/workflows/deploy-worker.yml",
    ".github/workflows/groq-free-inference-staging-smoke.yml",
    ".github/workflows/issue-comment-ingest.yml",
    ".github/workflows/raawaa-image-route-check.yml",
    ".github/workflows/research-discovery.yml",
    ".github/workflows/staging-p1-task-proof.yml",
    ".github/workflows/staging-supervised-process-probe.yml",
    ".github/workflows/task-runner.yml",
)
ATTESTATION = "CLOUDFLARE_ZONE_READ_ALL_ZONES_CONFIRMED: ${{ vars.CLOUDFLARE_ZONE_READ_ALL_ZONES_CONFIRMED }}"


class CloudflareZoneScopeAttestationWorkflowTests(unittest.TestCase):
    def test_every_cloudflare_gate_receives_the_repository_attestation_variable(self):
        for relative in GATED_WORKFLOWS:
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                gate = source.index("run: python scripts/verify_cloudflare_zero_cost_boundary.py")
                start = source.rfind("      - name:", 0, gate)
                end = source.find("      - name:", gate)
                block = source[start:] if end < 0 else source[start:end]
                self.assertIn(ATTESTATION, block)
                self.assertNotIn('CLOUDFLARE_ZONE_READ_ALL_ZONES_CONFIRMED: "true"', block)


if __name__ == "__main__":
    unittest.main()
