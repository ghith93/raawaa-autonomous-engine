"""Offline safety contract; not evidence about GitHub Actions or provider billing."""
import pathlib
import re
import subprocess
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class OneShotDisabledTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / '.github/workflows/task-runner.yml').read_text()

    def test_manual_opt_in_fails_before_checkout_and_network_steps(self):
        guard = self.source.split('      - name: Refuse unverified one-shot live research\n', 1)[1].split('      - name:', 1)[0]
        self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.run_public_research_once", guard)
        script = textwrap.dedent(guard.split('        run: |\n', 1)[1])
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('disabled', result.stdout)
        self.assertLess(self.source.index('Refuse unverified one-shot live research'), self.source.index('Check out reviewed main'))
        for name in ("Publish per-task outcomes", "Publish bounded Task Runner status"):
            block = self.source.split("      - name: " + name + "\n", 1)[1].split("      - name:", 1)[0]
            self.assertIn("if: always() && !(github.event_name == 'workflow_dispatch' && inputs.run_public_research_once)", block)
        # Any future always-run step needs explicit analysis of the rejected input.
        for block in self.source.split("      - name: ")[1:]:
            if "if: always()" in block:
                self.assertIn("!(github.event_name == 'workflow_dispatch' && inputs.run_public_research_once)", block)

    def test_no_live_route_evidence_fabrication_or_enqueue_remains(self):
        for token in ('provider_hard_stop_verified', 'provider_hard_stop_evidence', 'verified_at',
                      'RAAWAA_ENGINE_LIVE_INFERENCE:', '/v1/tasks/enqueue', 'steps.research.outputs',
                      'groq-free-gpt-oss-20b-primary'):
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)
        self.assertIn('"--max-tasks", "5", "--max-seconds", "60"', self.source)

    def test_no_main_push_smoke_is_triggered_by_workflow_change(self):
        triggers = self.source.split('\npermissions:', 1)[0]
        self.assertNotRegex(triggers, r'(?m)^  push:')
        self.assertRegex(triggers, r'(?m)^  schedule:')
        self.assertRegex(triggers, r'(?m)^  workflow_dispatch:')
        self.assertIn('default: false', triggers)


if __name__ == '__main__':
    unittest.main()
