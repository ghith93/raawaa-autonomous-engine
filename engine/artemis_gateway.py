"""Read-only connection probe for an optional Google ARTEMIS daemon.

This adapter deliberately does not submit mobile tasks or execute device actions.
"""
from __future__ import annotations

import ipaddress
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 8


class ArtemisGatewayError(RuntimeError):
    """A safe ARTEMIS probe failed or its response was invalid."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_artemis_endpoint(base_url: str, environ: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Validate a local endpoint or an explicitly attested HTTPS remote proxy."""
    env = os.environ if environ is None else environ
    if not isinstance(base_url, str) or not base_url.strip() or len(base_url) > 500:
        raise ValueError("ARTEMIS_BASE_URL is missing or malformed")
    parsed = urlsplit(base_url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ARTEMIS_BASE_URL must be an origin URL without credentials, path, query, or fragment")

    host = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    local = host == "localhost" or host.endswith(".localhost") or (address is not None and address.is_loopback)
    token = env.get("ARTEMIS_TOKEN", "").strip() or None

    if local:
        return base_url.strip().rstrip("/"), token
    if parsed.scheme != "https":
        raise ValueError("Remote ARTEMIS endpoints must use HTTPS")
    if not token:
        raise ValueError("Remote ARTEMIS endpoints require ARTEMIS_TOKEN for the authenticated proxy")
    if env.get("ARTEMIS_REMOTE_AUTH_VERIFIED", "").lower() != "true":
        raise ValueError("Remote ARTEMIS is blocked until its HTTPS reverse-proxy bearer authentication is verified")
    return base_url.strip().rstrip("/"), token


def _request_json(
    base_url: str,
    path: str,
    token: str | None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | list[Any]:
    request = Request(
        base_url + path,
        headers={
            "Accept": "application/json",
            "User-Agent": "RaawaaAutonomousEngine-ArtemisProbe/1.0",
            **({"Authorization": "Bearer " + token} if token else {}),
        },
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise ArtemisGatewayError(f"ARTEMIS {path} returned HTTP {response.status}")
            if response.geturl() != base_url + path:
                raise ArtemisGatewayError("ARTEMIS endpoint redirected; redirects are not followed")
            content_type = response.headers.get("Content-Type", "")
            if content_type and "json" not in content_type.lower():
                raise ArtemisGatewayError(f"ARTEMIS {path} did not return JSON")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ArtemisGatewayError(f"ARTEMIS {path} returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ArtemisGatewayError(f"ARTEMIS {path} request failed: {type(exc).__name__}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ArtemisGatewayError(f"ARTEMIS {path} response exceeded the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtemisGatewayError(f"ARTEMIS {path} returned invalid JSON") from exc
    if not isinstance(payload, (dict, list)):
        raise ArtemisGatewayError(f"ARTEMIS {path} returned an unexpected JSON shape")
    return payload


def probe_artemis(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Check daemon health and attached devices; never starts a mobile task."""
    env = os.environ if environ is None else environ
    configured_url = env.get("ARTEMIS_BASE_URL", "").strip()
    if not configured_url:
        return {
            "integration": "google_artemis",
            "status": "NOT_CONFIGURED",
            "network_calls": 0,
            "task_submission_enabled": False,
            "message": "Set ARTEMIS_BASE_URL on the trusted ARTEMIS host connection before probing.",
        }
    try:
        base_url, token = validate_artemis_endpoint(configured_url, env)
        # /api/status is intentionally excluded: ARTEMIS's handler calls
        # ensure_worker_running(), which can start a dispatcher and process queued tasks.
        devices_payload = _request_json(base_url, "/api/devices", token)
    except ValueError as exc:
        return {
            "integration": "google_artemis",
            "status": "BLOCKED_CONFIGURATION",
            "network_calls": 0,
            "task_submission_enabled": False,
            "message": str(exc),
        }
    except ArtemisGatewayError as exc:
        return {
            "integration": "google_artemis",
            "status": "UNAVAILABLE",
            "network_calls": 1,
            "task_submission_enabled": False,
            "message": str(exc),
        }

    raw_devices = devices_payload.get("devices", []) if isinstance(devices_payload, dict) else devices_payload
    if not isinstance(raw_devices, list):
        return {
            "integration": "google_artemis",
            "status": "INVALID_RESPONSE",
            "network_calls": 1,
            "task_submission_enabled": False,
            "message": "ARTEMIS device response did not contain a list.",
        }
    devices = []
    for item in raw_devices[:20]:
        if not isinstance(item, dict):
            continue
        serial = item.get("serial") or item.get("device_serial") or item.get("device_id")
        state = item.get("state") or item.get("status") or "unknown"
        model = item.get("model")
        devices.append({
            "serial": serial[:120] if isinstance(serial, str) else None,
            "state": state[:40] if isinstance(state, str) else "unknown",
            "model": model[:120] if isinstance(model, str) else None,
            "busy": bool(item.get("busy", item.get("is_busy", False))),
        })

    return {
        "integration": "google_artemis",
        "status": "CONNECTED",
        "network_calls": 1,
        "api_status": "reachable",
        "device_count": len(devices),
        "devices": devices,
        "task_submission_enabled": False,
        "execution_note": "Read-only connectivity verified; mobile task execution remains disabled.",
    }
