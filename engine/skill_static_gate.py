"""Deterministic, non-executing preflight for untrusted agent-skill bundles.

This is a static policy gate only. It is not a sandbox, malware detector,
license determination, signature verifier, or permission to install a skill.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any

MAX_FILES = 100
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_PATH_CHARS = 240
MAX_MANIFEST_TEXT = 16 * 1024

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
LICENSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{1,79}$")
SOURCE_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/tree/[0-9a-f]{40}(?:/[^?#]*)?)?$")
ALLOWED_PERMISSIONS = {"read:public_metadata"}

RULES = (
    ("SKILL_PROMPT_OVERRIDE", "critical", re.compile(
        r"\b(ignore|disregard|override)\b.{0,60}\b(previous|prior|above|system)\b.{0,40}\b(instructions?|prompt|rules?)\b", re.I)),
    ("SKILL_SECRET_EXFILTRATION", "critical", re.compile(
        r"\b(send|upload|exfiltrat|print|dump|reveal|expose)\w*\b.{0,100}\b(secret|token|api[_ -]?key|credential|private key)\b", re.I)),
    ("SKILL_DYNAMIC_EXECUTION", "high", re.compile(
        r"\b(eval|exec)\s*\(|\bsubprocess\b|\bos\.system\s*\(|\bchild_process\b|\bshell\s*=\s*True|\bPopen\s*\(", re.I)),
    ("SKILL_NETWORK_ACCESS", "high", re.compile(
        r"\b(requests\.(get|post|put|delete)|urllib\.request|httpx\.|aiohttp\.|fetch\s*\(|curl\s|wget\s|socket\.)", re.I)),
    ("SKILL_SECRET_ENV_ACCESS", "high", re.compile(
        r"\b(os\.environ|getenv\s*\(|process\.env|\.ssh/|\.env\b|credential[s]?\b)", re.I)),
    ("SKILL_PRIVILEGE_OR_DESTRUCTIVE_COMMAND", "high", re.compile(
        r"\b(sudo\b|chmod\s+777|rm\s+-rf|docker\.sock|setcap\b|mount\s+-)", re.I)),
    ("SKILL_REMOTE_INSTALL", "high", re.compile(
        r"\b(pip|npm|pnpm|yarn)\s+install\b|\bcurl\b.{0,100}\|\s*(sh|bash)\b", re.I)),
)

def _finding(rule_id: str, severity: str, file: str, line: int, text: str, summary: str) -> dict:
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return {
        "rule_id": rule_id, "severity": severity, "file": file,
        "line": line, "evidence_sha256_16": digest, "summary": summary,
    }


def _manifest_errors(manifest: Any, files: dict[str, str]) -> list[dict]:
    errors = []
    if not isinstance(manifest, dict):
        return [{"rule_id": "MANIFEST_NOT_OBJECT", "severity": "critical", "summary": "Manifest must be a JSON object."}]
    required = ("schema_version", "name", "version", "source_url", "source_commit", "license_spdx", "entrypoint", "permissions", "capabilities")
    missing = [key for key in required if key not in manifest]
    if missing:
        errors.append({"rule_id": "MANIFEST_FIELDS_MISSING", "severity": "high", "summary": "Missing required manifest fields: " + ",".join(missing)})
    if manifest.get("schema_version") != 1:
        errors.append({"rule_id": "MANIFEST_SCHEMA_UNSUPPORTED", "severity": "high", "summary": "Only manifest schema_version 1 is supported."})
    if not isinstance(manifest.get("name"), str) or not NAME_RE.fullmatch(manifest.get("name", "")):
        errors.append({"rule_id": "MANIFEST_NAME_INVALID", "severity": "high", "summary": "Skill name must be a bounded lowercase slug."})
    if not isinstance(manifest.get("version"), str) or not VERSION_RE.fullmatch(manifest.get("version", "")):
        errors.append({"rule_id": "MANIFEST_VERSION_UNPINNED", "severity": "high", "summary": "Skill version must be exact semantic version x.y.z."})
    source_url = manifest.get("source_url")
    if not isinstance(source_url, str) or len(source_url) > 500 or not SOURCE_RE.fullmatch(source_url):
        errors.append({"rule_id": "MANIFEST_SOURCE_UNTRUSTED", "severity": "critical", "summary": "Source must be a GitHub repository or commit-pinned tree URL over HTTPS."})
    if not isinstance(manifest.get("source_commit"), str) or not COMMIT_RE.fullmatch(manifest.get("source_commit", "")):
        errors.append({"rule_id": "MANIFEST_COMMIT_UNPINNED", "severity": "critical", "summary": "Source commit must be a full 40-character lowercase Git SHA."})
    elif isinstance(source_url, str):
        pinned = re.search(r"/tree/([0-9a-f]{40})(?:/|$)", source_url)
        if pinned and pinned.group(1) != manifest["source_commit"]:
            errors.append({"rule_id": "MANIFEST_SOURCE_COMMIT_MISMATCH", "severity": "critical", "summary": "Commit in source URL does not match source_commit."})
    if not isinstance(manifest.get("license_spdx"), str) or not LICENSE_RE.fullmatch(manifest.get("license_spdx", "")):
        errors.append({"rule_id": "MANIFEST_LICENSE_MISSING", "severity": "high", "summary": "An SPDX-like license identifier is required; this is not a license approval."})
    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, str) or len(entrypoint) > MAX_PATH_CHARS or entrypoint not in files:
        errors.append({"rule_id": "MANIFEST_ENTRYPOINT_MISSING", "severity": "high", "summary": "Entrypoint must reference a file in the supplied bundle."})
    permissions = manifest.get("permissions")
    if not isinstance(permissions, list) or len(permissions) > 20 or any(not isinstance(x, str) or len(x) > 100 for x in permissions):
        errors.append({"rule_id": "MANIFEST_PERMISSIONS_INVALID", "severity": "critical", "summary": "Permissions must be a list of strings."})
    else:
        denied = sorted(set(permissions) - ALLOWED_PERMISSIONS)
        if denied:
            errors.append({"rule_id": "MANIFEST_PERMISSION_ESCALATION", "severity": "critical", "summary": "Requested permissions exceed the static preflight allowlist: " + ",".join(value[:100] for value in denied[:8])})
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in capabilities):
        errors.append({"rule_id": "MANIFEST_CAPABILITIES_INVALID", "severity": "high", "summary": "Capabilities must be a non-empty list of bounded strings."})
    return errors


def scan_skill_bundle(manifest: Any, files: Any) -> dict:
    """Scan supplied text files and manifest without filesystem, network, or code execution."""
    if not isinstance(files, dict):
        files = {}
    findings: list[dict] = []
    errors = []
    normalized: dict[str, str] = {}
    seen_paths = set()
    total_bytes = 0
    if len(files) > MAX_FILES:
        errors.append({"rule_id": "BUNDLE_FILE_COUNT_LIMIT", "severity": "critical", "summary": f"Bundle exceeds {MAX_FILES} files."})
    for raw_path, content in list(files.items())[:MAX_FILES]:
        if not isinstance(raw_path, str) or len(raw_path) > MAX_PATH_CHARS or "\\" in raw_path:
            errors.append({"rule_id": "BUNDLE_PATH_INVALID", "severity": "critical", "summary": "Bundle contains an invalid or overlong path."})
            continue
        path = PurePosixPath(raw_path)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in path.parts):
            errors.append({"rule_id": "BUNDLE_PATH_TRAVERSAL", "severity": "critical", "summary": "Bundle path is absolute, traversing, or inside .git."})
            continue
        folded = raw_path.casefold()
        if folded in seen_paths:
            errors.append({"rule_id": "BUNDLE_PATH_COLLISION", "severity": "high", "summary": "Bundle contains duplicate paths under case-insensitive comparison."})
            continue
        seen_paths.add(folded)
        if not isinstance(content, str):
            errors.append({"rule_id": "BUNDLE_BINARY_FILE_UNSUPPORTED", "severity": "medium", "summary": "Binary or non-text files require a separate controlled scanner."})
            continue
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            errors.append({"rule_id": "BUNDLE_FILE_SIZE_LIMIT", "severity": "critical", "summary": f"A file exceeds {MAX_FILE_BYTES} bytes."})
            continue
        total_bytes += len(encoded)
        if total_bytes > MAX_TOTAL_BYTES:
            errors.append({"rule_id": "BUNDLE_TOTAL_SIZE_LIMIT", "severity": "critical", "summary": f"Bundle exceeds {MAX_TOTAL_BYTES} bytes."})
            break
        normalized[raw_path] = content
    errors.extend(_manifest_errors(manifest, normalized))
    manifest_text = ""
    if isinstance(manifest, dict):
        try:
            manifest_text = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
            if len(manifest_text.encode("utf-8")) > MAX_MANIFEST_TEXT:
                errors.append({"rule_id": "MANIFEST_SIZE_LIMIT", "severity": "critical", "summary": "Manifest exceeds the 16 KiB preflight limit."})
                manifest_text = manifest_text[:MAX_MANIFEST_TEXT]
        except (TypeError, ValueError, RecursionError):
            errors.append({"rule_id": "MANIFEST_SERIALIZATION_INVALID", "severity": "critical", "summary": "Manifest cannot be serialized as bounded JSON."})
    scan_files = dict(normalized)
    scan_files["<manifest>"] = manifest_text
    for file_path, content in scan_files.items():
        for line_number, line in enumerate(content.splitlines(), 1):
            if len(line) > 4000:
                findings.append(_finding("SKILL_LINE_LENGTH", "medium", file_path, line_number, line[:4000], "Unusually long line should receive manual review."))
            for rule_id, severity, pattern in RULES:
                match = pattern.search(line)
                if match:
                    findings.append(_finding(rule_id, severity, file_path, line_number, line, "Static pattern matched; inspect the file in an isolated review environment."))
    findings.sort(key=lambda item: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(item["severity"], 9), item["file"], item["line"], item["rule_id"]))
    errors.sort(key=lambda item: (item["severity"], item["rule_id"]))
    blocked = bool(errors) or any(item["severity"] in {"critical", "high"} for item in findings)
    return {
        "schema_version": 1,
        "status": "BLOCKED" if blocked else "PROPOSED",
        "analysis_only": True,
        "network_calls": 0,
        "model_calls": 0,
        "code_execution": False,
        "installation": False,
        "files_scanned": len(normalized),
        "total_bytes_scanned": total_bytes,
        "manifest_errors": errors,
        "findings": findings,
        "license_review_required": True,
        "independent_security_review_required": True,
        "sandbox_validation_required": True,
        "signature_verification_required": True,
        "runtime_permission_grant": False,
        "automatic_installation": False,
        "automatic_execution": False,
        "limitations": [
            "A clean static scan does not prove a skill is safe, useful, correctly licensed, or safe at runtime.",
            "This preflight does not execute code, resolve dependencies, fetch sources, verify signatures, or provide a sandbox.",
            "Static patterns can produce false positives and false negatives; independent review and sandbox validation remain mandatory.",
        ],
    }
