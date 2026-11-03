"""Small, testable classification for optional Cloudflare Access state."""

from __future__ import annotations

from typing import Any


def is_access_not_enabled(http_status: int, errors: Any) -> bool:
    """Return true only for Cloudflare's explicit account-level Access-disabled error."""
    if http_status != 403 or not isinstance(errors, list):
        return False
    for item in errors:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        message = item.get("message")
        if str(code) == "9999" and isinstance(message, str):
            if "access.api.error.not_enabled" in message.lower():
                return True
    return False
