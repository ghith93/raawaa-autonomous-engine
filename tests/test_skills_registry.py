import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from engine.skills_registry import SkillRegistryError, load_approved_skill


class SkillsRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill_dir = self.root / "skills" / "example-skill"
        self.skill_dir.mkdir(parents=True)
        self.instructions = b"# Example\n\nUntrusted instruction text.\n"
        (self.skill_dir / "SKILL.md").write_bytes(self.instructions)
        self.manifest = {
            "schema_version": 1,
            "skill_id": "example-skill",
            "version": "1.0.0",
            "source": "https://example.invalid/repository",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "owner": "example-owner",
            "license": {"spdx_id": "MIT", "evidence_url": "https://example.invalid/LICENSE"},
            "purpose": "Test-only skill",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "capabilities": ["research"],
            "allowed_tools": ["web.search"],
            "network_policy": "none",
            "risk_classification": "low",
            "content_sha256": hashlib.sha256(self.instructions).hexdigest(),
            "permissions": {"researcher": ["research_brief"]},
            "approved": True,
        }
        self.write_manifest()
        revocations = self.root / "config" / "skills" / "revocations.json"
        revocations.parent.mkdir(parents=True)
        revocations.write_text(
            json.dumps({"schema_version": 1, "revoked_skill_ids": []}), encoding="utf-8"
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self):
        (self.skill_dir / "skill.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def load(self, **kwargs):
        return load_approved_skill(
            self.root, "example-skill",
            agent_role=kwargs.get("agent_role", "researcher"),
            task_type=kwargs.get("task_type", "research_brief"),
        )

    def test_loads_pinned_skill_as_metadata_only(self):
        skill = self.load()
        self.assertEqual(skill.skill_id, "example-skill")
        self.assertEqual(skill.capabilities, ("research",))
        self.assertEqual(skill.allowed_tools, ("web.search",))
        self.assertEqual(skill.network_policy, "none")

    def test_unapproved_skill_is_rejected(self):
        self.manifest["approved"] = False
        self.write_manifest()
        with self.assertRaisesRegex(SkillRegistryError, "manually approved"):
            self.load()

    def test_role_and_task_mapping_is_deny_by_default(self):
        with self.assertRaisesRegex(SkillRegistryError, "not approved"):
            self.load(agent_role="engineer")

    def test_modified_instruction_fails_checksum(self):
        (self.skill_dir / "SKILL.md").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(SkillRegistryError, "checksum mismatch"):
            self.load()

    def test_revoked_skill_is_rejected(self):
        (self.root / "config" / "skills" / "revocations.json").write_text(
            json.dumps({"schema_version": 1, "revoked_skill_ids": ["example-skill"]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SkillRegistryError, "revoked"):
            self.load()

    def test_path_traversal_id_is_rejected(self):
        with self.assertRaises(SkillRegistryError):
            load_approved_skill(self.root, "../outside", agent_role="researcher", task_type="research_brief")

    def test_malformed_manifest_is_rejected(self):
        (self.skill_dir / "skill.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(SkillRegistryError):
            self.load()

    def test_missing_license_evidence_is_rejected(self):
        self.manifest["license"] = {"spdx_id": "MIT"}
        self.write_manifest()
        with self.assertRaisesRegex(SkillRegistryError, "license evidence"):
            self.load()

    def test_manifest_does_not_start_execution_or_grant_tools(self):
        skill = self.load()
        self.assertFalse(hasattr(skill, "execute"))
        self.assertFalse(hasattr(skill, "invoke_tool"))


if __name__ == "__main__":
    unittest.main()
