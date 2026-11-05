"""GitHub-OIDC-authenticated adapter for the isolated Supabase Free fallback.

This backend is deliberately limited to the exact Staging scheduled probe. It
is not a general queue client and never claims an arbitrary pending task.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError

SUPABASE_FALLBACK_REF = "xuwtuhfqjjwszxqkmivj"
SUPABASE_FALLBACK_URL = (
    f"https://{SUPABASE_FALLBACK_REF}.supabase.co/functions/v1/engine-fallback"
)
SUPABASE_FALLBACK_PUBLISHABLE_KEY = "sb_publishable_RXyG-EgaTR4Lj9CkFfubeQ_BJOKN0l_"
SUPABASE_OIDC_AUDIENCE = "raawaa-engine-staging-fallback"
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SupabaseFallbackTaskStore(RemoteTaskStore):
    """Narrow HTTP adapter that preserves the existing remote-store contract."""

    def __init__(self, function_url: str, publishable_key: str, oidc_token: str,
                 timeout: float = 15.0):
        parsed = urlsplit(function_url)
        if (function_url != SUPABASE_FALLBACK_URL or parsed.scheme != "https"
                or parsed.netloc != f"{SUPABASE_FALLBACK_REF}.supabase.co"
                or parsed.path != "/functions/v1/engine-fallback"
                or parsed.query or parsed.fragment):
            raise ValueError("Supabase fallback URL is not the pinned isolated project")
        if publishable_key != SUPABASE_FALLBACK_PUBLISHABLE_KEY:
            raise ValueError("Supabase fallback publishable key did not match the pinned project")
        if not isinstance(oidc_token, str) or len(oidc_token) < 100 or oidc_token.count(".") != 2:
            raise ValueError("A GitHub OIDC token is required for Supabase fallback")
        super().__init__(
            function_url,
            oidc_token,
            timeout=timeout,
            access_client_id="",
            access_client_secret="",
            reject_redirects=True,
        )
        self.publishable_key = publishable_key

    def _request(self, path: str, method: str = "GET",
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if method != "POST" or not isinstance(path, str) or not path.startswith("/"):
            raise RemoteTaskStoreError("Supabase fallback rejected an unsupported request")
        body = json.dumps({
            "path": path,
            "method": method,
            "payload": payload or {},
        }, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise RemoteTaskStoreError("Supabase fallback request exceeded its size limit")
        request = Request(
            self.base_url,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + self.token,
                "apikey": self.publishable_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "RaawaaAutonomousEngine/1.0",
            },
        )
        try:
            with build_opener(_RejectRedirects).open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RemoteTaskStoreError("Supabase fallback response exceeded its size limit")
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise RemoteTaskStoreError("Supabase fallback returned invalid data")
            return parsed
        except HTTPError as exc:
            if exc.code == 401:
                raise RemoteTaskStoreError(
                    "Supabase fallback authentication failed", status_code=401,
                ) from None
            if exc.code == 403:
                raise RemoteTaskStoreError(
                    "Supabase fallback rejected the scheduled workflow identity", status_code=403,
                ) from None
            retryable = exc.code == 429 or exc.code == 408 or 500 <= exc.code <= 599
            raise RemoteTaskStoreError(
                f"Supabase fallback returned HTTP {exc.code}",
                status_code=exc.code,
                retryable=retryable,
            ) from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise RemoteTaskStoreError(
                "Supabase fallback is unavailable or returned invalid JSON", retryable=True,
            ) from None
