"""Contract tests for the AndroidHarness 3.2 project setup."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AndroidHarnessInfrastructureTests(unittest.TestCase):
    def test_project_configuration_references_real_files_and_skill(self):
        config = json.loads((ROOT / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(config["default_agent"], "default")
        self.assertIn("raawaa-engine-androidharness", config["skills"]["auto_load"])
        skill = ROOT / ".harness/skills/raawaa-engine-androidharness/SKILL.md"
        self.assertTrue(skill.is_file())
        skill_text = skill.read_text(encoding="utf-8")
        self.assertIn("diagnostic-only", skill_text)
        for reference in config["references"]:
            self.assertTrue((ROOT / reference["path"]).is_file(), reference["path"])
        self.assertTrue((ROOT / config["memory"]["file"]).is_file())

    def test_project_agent_profiles_are_read_only_and_scoped(self):
        config = json.loads((ROOT / "harness.json").read_text(encoding="utf-8"))
        for name in ("researcher", "reviewer", "security_reviewer"):
            tools = config["agents"][name]["tools"]
            self.assertTrue(tools.get("read"))
            self.assertFalse(tools.get("write"))
            self.assertFalse(tools.get("edit"))
            self.assertFalse(tools.get("delete"))
            self.assertFalse(tools.get("shell"))
        self.assertEqual(config["permissions"]["delete"], "ask")
        self.assertEqual(config["permissions"]["outside_workspace"], "deny")

    def test_no_unimplemented_or_unverified_mcp_server_is_enabled(self):
        config = json.loads((ROOT / "harness.json").read_text(encoding="utf-8"))
        self.assertNotIn("mcp", config)

    def test_read_only_doctor_emits_json_and_never_emits_secret_values(self):
        marker = "DO_NOT_PRINT_ANDROIDHARNESS_TEST_SECRET_9f72"
        env = os.environ.copy()
        env.update({
            "CLOUDFLARE_API_TOKEN": marker,
            "ENGINE_API_TOKEN": marker,
            "OPENROUTER_API_KEY": marker,
            "GITHUB_TOKEN": marker,
        })
        before = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        result = subprocess.run(
            [sys.executable, "scripts/androidharness/doctor.py", "--json"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["mutations_performed"])
        self.assertFalse(report["network_requests_performed"])
        self.assertFalse(report["secret_values_emitted"])
        self.assertIn("tools", report)
        self.assertIn("device", report)
        self.assertIn("workspace", report)
        self.assertNotIn(marker, result.stdout)
        self.assertNotIn(marker, result.stderr)
        after = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
