"""Read-only, fail-closed registry for reviewed agent skills.

This module validates metadata and pinned instruction bytes only. It never
executes skill content, imports adapters, fetches dependencies, or grants tools.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SkillRegistryError(ValueError):
    """A skill registry entry is invalid, untrusted, or not authorized."""


_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RISKS = {"low", "medium", "high", "critical"}
_NETWORK = {"none", "allowlisted"}
_REQUIRED = {
    "schema_version", "skill_id", "version", "source", "revision", "owner",
    "license", "purpose", "input_schema", "output_schema", "capabilities",
    "allowed_tools", "network_policy", "risk_classification",
    "content_sha256", "permissions", "approved",
}


@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    version: str
    owner: str
    license_id: str
    purpose: str
    capabilities: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    network_policy: str
    risk_classification: str
    content_sha256: str
    manifest_path: str
    instruction_path: str


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillRegistryError(f"cannot read valid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise SkillRegistryError(f"expected JSON object: {path.name}")
    return value


def _inside(root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise SkillRegistryError("skill path escapes registry or does not exist") from exc
    current = candidate
    boundary = root.resolve(strict=True)
    while current != boundary:
        if current.is_symlink():
            raise SkillRegistryError("symlinked skill paths are not permitted")
        current = current.parent
    if not resolved.is_file():
        raise SkillRegistryError("skill instruction must be a regular file")
    return resolved


def load_approved_skill(
    registry_root: str | Path,
    skill_id: str,
    *,
    agent_role: str,
    task_type: str,
) -> SkillManifest:
    """Load one approved and hash-pinned skill for an explicit role/task pair.

    The default is deny. A skill manifest never grants execution authority.
    """
    if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
        raise SkillRegistryError("invalid skill id")
    if not isinstance(agent_role, str) or not agent_role.strip():
        raise SkillRegistryError("agent role is required")
    if not isinstance(task_type, str) or not task_type.strip():
        raise SkillRegistryError("task type is required")

    repository_root = Path(registry_root).resolve(strict=True)
    root = repository_root / "skills"
    if root.is_symlink() or not root.is_dir():
        raise SkillRegistryError("skills directory is missing or symlinked")
    manifest_path = _inside(root, root / skill_id / "skill.json")
    instruction_path = _inside(root, root / skill_id / "SKILL.md")
    manifest = _json_object(manifest_path)
    missing = _REQUIRED - manifest.keys()
    if missing:
        raise SkillRegistryError("manifest missing required fields")
    if manifest["schema_version"] != 1 or manifest["skill_id"] != skill_id:
        raise SkillRegistryError("unsupported schema or mismatched skill id")
    if manifest["approved"] is not True:
        raise SkillRegistryError("skill has not been manually approved")

    for field in ("version", "source", "revision", "owner", "purpose"):
        if not isinstance(manifest[field], str) or not manifest[field].strip():
            raise SkillRegistryError(f"invalid {field}")
    if not re.fullmatch(r"[a-fA-F0-9]{7,40}", manifest["revision"]):
        raise SkillRegistryError("revision must be an immutable commit identifier")
    license_info = manifest["license"]
    if not isinstance(license_info, dict) or not all(
        isinstance(license_info.get(key), str) and license_info[key].strip()
        for key in ("spdx_id", "evidence_url")
    ):
        raise SkillRegistryError("license evidence is required")
    for field in ("input_schema", "output_schema"):
        if not isinstance(manifest[field], dict):
            raise SkillRegistryError(f"{field} must be a JSON schema object")
    for field in ("capabilities", "allowed_tools"):
        values = manifest[field]
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item.strip() for item in values
        ):
            raise SkillRegistryError(f"invalid {field}")
    if manifest["network_policy"] not in _NETWORK:
        raise SkillRegistryError("unsupported network policy")
    if manifest["risk_classification"] not in _RISKS:
        raise SkillRegistryError("unsupported risk classification")

    permissions = manifest.get("permissions")
    if not isinstance(permissions, dict):
        raise SkillRegistryError("explicit role/task permission map is required")
    role_tasks = permissions.get(agent_role)
    if not isinstance(role_tasks, list) or task_type not in role_tasks:
        raise SkillRegistryError("skill is not approved for this role and task type")

    revocations = repository_root / "config" / "skills" / "revocations.json"
    if revocations.exists():
        revoked = _json_object(_inside(repository_root, revocations))
        entries = revoked.get("revoked_skill_ids", [])
        if not isinstance(entries, list) or any(not isinstance(x, str) for x in entries):
            raise SkillRegistryError("invalid revocation registry")
        if skill_id in entries:
            raise SkillRegistryError("skill has been revoked")

    content = instruction_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    expected = manifest["content_sha256"]
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise SkillRegistryError("invalid content checksum")
    if digest != expected:
        raise SkillRegistryError("skill content checksum mismatch")

    return SkillManifest(
        skill_id=skill_id,
        version=manifest["version"],
        owner=manifest["owner"],
        license_id=license_info["spdx_id"],
        purpose=manifest["purpose"],
        capabilities=tuple(manifest["capabilities"]),
        allowed_tools=tuple(manifest["allowed_tools"]),
        network_policy=manifest["network_policy"],
        risk_classification=manifest["risk_classification"],
        content_sha256=digest,
        manifest_path=str(manifest_path),
        instruction_path=str(instruction_path),
    )
