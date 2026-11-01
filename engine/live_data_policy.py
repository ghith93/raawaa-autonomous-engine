"""Fail-closed data classification checks before any live provider request."""
from __future__ import annotations

import json
import re

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:gsk|gh[pousr])_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}", re.IGNORECASE),
)

def validate_live_payload(title: str, payload: dict, policy: dict) -> tuple[bool, str]:
    """Allow only explicitly classified, bounded, non-sensitive task content."""
    if not isinstance(policy, dict):
        return False, "data_policy_missing"
    allowed = policy.get("allowed_classifications")
    blocked_fragments = policy.get("blocked_key_fragments")
    max_bytes = policy.get("max_prompt_bytes")
    if (not isinstance(allowed, list) or not allowed
            or any(not isinstance(item, str) for item in allowed)
            or not isinstance(blocked_fragments, list)
            or any(not isinstance(item, str) or not item for item in blocked_fragments)
            or type(max_bytes) is not int or not 256 <= max_bytes <= 100000):
        return False, "data_policy_invalid"
    if not isinstance(title, str) or not isinstance(payload, dict):
        return False, "task_payload_invalid"
    classification = payload.get("data_classification")
    if not isinstance(classification, str) or classification.lower() not in {x.lower() for x in allowed}:
        return False, "data_classification_required_or_disallowed"
    try:
        serialized = json.dumps({"title": title, "payload": payload}, ensure_ascii=False, sort_keys=True)
        encoded = serialized.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return False, "task_payload_invalid"
    if len(encoded) > max_bytes:
        return False, "task_payload_too_large"
    fragments = tuple(item.lower().replace("_", "").replace("-", "") for item in blocked_fragments)
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if any(fragment in normalized for fragment in fragments):
                    return False, "sensitive_field_detected"
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            if any(pattern.search(current) for pattern in SECRET_PATTERNS):
                return False, "secret_pattern_detected"
    if any(pattern.search(title) for pattern in SECRET_PATTERNS):
        return False, "secret_pattern_detected"
    return True, "allowed"
