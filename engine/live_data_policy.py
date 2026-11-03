"""Fail-closed data classification checks before any live provider request."""
from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:gsk|gh[pousr])_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}", re.IGNORECASE),
)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
SENSITIVE_CONTENT_PATTERN = re.compile(
    r"\b(?:formula|ingredients?|composition|proprietary|confidential|internal|"
    r"customer|payment|password|secret|credential|access\s+token|refresh\s+token|"
    r"personal\s+data|private\s+data)\b",
    re.IGNORECASE,
)

BASE_BLOCKED_KEY_FRAGMENTS = (
    "password", "passwd", "secret", "apikey", "access_token", "refresh_token",
    "authorization", "privatekey", "credential", "customer", "personaldata",
    "personalinformation", "payment", "cardnumber", "cvv", "bankaccount",
    "nationalid", "passport", "medicalrecord", "formula", "ingredient",
    "composition", "proprietary", "orderdata", "email", "phone", "address",
    "username", "contact", "birthdate", "dateofbirth", "ipaddress",
)
SUPPORTED_PAYLOAD_KEYS = {"data_classification", "topic", "questions", "source_urls"}
REQUIRED_PAYLOAD_KEYS = {"data_classification", "topic"}


def _valid_public_source_url(value: str) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme.lower() != "https" or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            return False
        host = host.lower().rstrip(".")
        if host == "localhost" or host.endswith((".local", ".internal", ".test", ".example")):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "." in host and " " not in host
        return address.is_global
    except ValueError:
        return False


def validate_live_payload(
    task_type: str, title: str, payload: dict, policy: dict
) -> tuple[bool, str]:
    """Allow only explicitly classified public research tasks with a strict schema."""
    if not isinstance(policy, dict):
        return False, "data_policy_missing"
    allowed = policy.get("allowed_classifications")
    allowed_task_types = policy.get("allowed_task_types")
    allowed_payload_keys = policy.get("allowed_payload_keys")
    blocked_fragments = policy.get("blocked_key_fragments")
    max_bytes = policy.get("max_prompt_bytes")
    if (policy.get("require_explicit_classification") is not True
            or not isinstance(allowed, list) or not allowed
            or any(not isinstance(item, str) for item in allowed)
            or {item.lower() for item in allowed} != {"public"}
            or allowed_task_types != ["research_brief"]
            or not isinstance(allowed_payload_keys, list) or not allowed_payload_keys
            or any(not isinstance(item, str) or item not in SUPPORTED_PAYLOAD_KEYS for item in allowed_payload_keys)
            or not REQUIRED_PAYLOAD_KEYS.issubset(set(allowed_payload_keys))
            or not isinstance(blocked_fragments, list)
            or any(not isinstance(item, str) or not item for item in blocked_fragments)
            or type(max_bytes) is not int or not 256 <= max_bytes <= 100000):
        return False, "data_policy_invalid"
    if not isinstance(task_type, str) or task_type not in set(allowed_task_types):
        return False, "task_type_not_allowed"
    if not isinstance(title, str) or not title.strip() or len(title) > 500 or not isinstance(payload, dict):
        return False, "task_payload_invalid"

    # Classification and byte limits are checked before schema rejection so
    # missing classification and oversized payloads retain precise block reasons.
    classification = payload.get("data_classification")
    if not isinstance(classification, str) or classification.lower() != "public":
        return False, "data_classification_required_or_disallowed"
    try:
        encoded = json.dumps(
            {"task_type": task_type, "title": title, "payload": payload},
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return False, "task_payload_invalid"
    if len(encoded) > max_bytes:
        return False, "task_payload_too_large"

    # Inspect every supplied field before the allowlist check. This preserves
    # specific sensitive-data findings even when the field itself is not allowed.
    source_urls = payload.get("source_urls", [])
    if (not isinstance(source_urls, list) or len(source_urls) > 5
            or any(not _valid_public_source_url(item) for item in source_urls)):
        return False, "source_url_invalid"
    fragments = tuple(
        item.lower().replace("_", "").replace("-", "")
        for item in (*BASE_BLOCKED_KEY_FRAGMENTS, *blocked_fragments)
    )
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
            if EMAIL_PATTERN.search(current):
                return False, "personal_data_detected"
            if any(pattern.search(current) for pattern in SECRET_PATTERNS):
                return False, "secret_pattern_detected"
            if SENSITIVE_CONTENT_PATTERN.search(current):
                return False, "sensitive_content_detected"
    if EMAIL_PATTERN.search(title):
        return False, "personal_data_detected"
    if any(pattern.search(title) for pattern in SECRET_PATTERNS):
        return False, "secret_pattern_detected"
    if SENSITIVE_CONTENT_PATTERN.search(title):
        return False, "sensitive_content_detected"

    if set(payload) - set(allowed_payload_keys) or not REQUIRED_PAYLOAD_KEYS.issubset(payload):
        return False, "payload_schema_invalid"
    topic = payload.get("topic")
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 500:
        return False, "payload_schema_invalid"
    questions = payload.get("questions", [])
    if (not isinstance(questions, list) or len(questions) > 5
            or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in questions)):
        return False, "payload_schema_invalid"
    return True, "allowed"
