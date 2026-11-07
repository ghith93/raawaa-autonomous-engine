"""The sandbox proof workflow must not execute its own dispatch input (#440).

``sandbox-adversarial.yml`` takes an ``image`` input from a manual dispatch. That
input passed a digest regex in one step and was then interpolated straight into a
``run:`` block in the summary step, which runs with ``if: always()``. So a value
the regex *rejected* still executed as shell: ``$(...)`` inside the echo ran, and
the job failed afterwards with nothing in the log to say why.

These tests parse the workflow, not a copy of it, and fail if any ``run:`` block
interpolates a dispatch input again. Scope is deliberately this one workflow:
``telegram-register.yml`` has a similar interpolation of ``inputs.chat_id`` that
belongs to a different issue and is not silently covered by this guard.
"""
import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "sandbox-adversarial.yml"

# `${{ inputs.* }}` (and the untrusted event context) must never reach a shell.
UNTRUSTED_EXPRESSION = re.compile(r"\$\{\{\s*(inputs|github\.event)\.")


def run_blocks(text):
    """Yield ``(first_line_number, body)`` for every ``run:`` block in the file."""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", lines[index])
        if not match:
            index += 1
            continue
        indent = len(match.group(1))
        remainder = match.group(2).strip()
        body = [] if remainder in ("", "|", ">", "|-", ">-", "|+", ">+") else [remainder]
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor]
            if not line.strip():
                cursor += 1
                continue
            if len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line)
            cursor += 1
        yield index + 1, "\n".join(body)
        index = cursor


class SandboxWorkflowInjectionTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_no_run_block_interpolates_an_untrusted_expression(self):
        hits = [
            (line_number, line.strip())
            for line_number, body in run_blocks(self.text)
            for line in body.splitlines()
            if UNTRUSTED_EXPRESSION.search(line)
        ]
        self.assertEqual(hits, [], f"dispatch input reached a shell script: {hits}")

    def test_the_image_input_reaches_the_shell_only_through_the_environment(self):
        self.assertIn("IMAGE: ${{ inputs.image }}", self.text)
        # The one place that prints the value must print the environment variable.
        self.assertIn("SAFE_IMAGE=", self.text)
        self.assertIn('printf -- \'- image: `%s`\\n\' "$SAFE_IMAGE"', self.text)

    def test_the_summary_step_still_runs_on_failure_but_sanitises_the_value(self):
        # It runs after a rejected digest by design (evidence of the failure), which
        # is exactly why the value must be data-only and control characters stripped.
        self.assertIn("if: always()", self.text)
        self.assertIn("tr -d '\\r\\n'", self.text)

    def test_live_workflow_uses_fail_closed_runner_and_no_persisted_checkout_token(self):
        self.assertIn("python3 scripts/run_sandbox_adversarial_tests.py --check-image", self.text)
        self.assertIn("RAAWAA_SANDBOX_IMAGE: ${{ inputs.image }}", self.text)
        self.assertIn("python3 scripts/run_sandbox_adversarial_tests.py", self.text)
        self.assertNotIn("python3 -m unittest tests.adversarial.test_sandbox_escape", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("does not satisfy all issue #440 acceptance criteria", self.text)

    def test_the_guard_detects_the_pattern_it_is_meant_to_catch(self):
        # A guard that cannot fail is not a guard.
        sample = "        run: |\n          echo \"- image: `${{ inputs.image }}`\"\n"
        self.assertTrue(any(UNTRUSTED_EXPRESSION.search(line) for _, body in run_blocks(sample) for line in body.splitlines()))


if __name__ == "__main__":
    unittest.main()
