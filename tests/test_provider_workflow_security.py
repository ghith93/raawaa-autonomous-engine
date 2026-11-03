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
        }
        for variable, expression in expected.items():
            self.assertIn(variable + ": " + expression, batch)
            self.assertEqual(source.count(expression), 1, f"{variable} secret reference leaked outside the batch step")
        self.assertIn(
            "RAAWAA_ENGINE_API_URL: https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev",
            batch,
        )

    def test_provider_credentials_are_not_echoed_or_published(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        for variable in ("GROQ_API_KEY", "GEMINI_API_KEY", "RAAWAA_ENGINE_API_TOKEN"):
            self.assertNotIn(f'echo "${{{variable}}}"', source)
            self.assertNotIn(f'output.write(f"{variable}=', source)

if __name__ == "__main__":
    unittest.main()
