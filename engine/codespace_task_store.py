"""Least-privilege client for the Codespace-only task API.

This client intentionally exposes no enqueue, list, quota, recovery, or admin methods.
"""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError


class CodespaceTaskStore:
    """Codespace executor principal; cannot use the general engine API scope."""

    ALLOWED_TASK_TYPES = {"codespace_inspect", "codespace_test"}

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = 15.0, access_client_id: str | None = None,
                 access_client_secret: str | None = None):
        code_token = token if token is not None else os.environ.get("RAAWAA_ENGINE_CODESPACE_TOKEN", "")
        shared_token = os.environ.get("RAAWAA_ENGINE_API_TOKEN", "")
        admin_token = os.environ.get("RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN", "")
        if not code_token or code_token == shared_token or code_token == admin_token:
            raise ValueError("Codespace bridge requires a distinct scoped token")
        resolved_url = (base_url or os.environ.get("RAAWAA_ENGINE_API_URL", "")).rstrip("/")
        parsed = urlsplit(resolved_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("Codespace bridge requires a clean HTTPS Worker origin")
        self._client = RemoteTaskStore(
            base_url=resolved_url,
            token=code_token,
            timeout=timeout,
            access_client_id=access_client_id,
            access_client_secret=access_client_secret,
            reject_redirects=True,
        )

    def _request(self, path: str, method: str = "GET",
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._client._request(path, method, payload)

    def preflight(self) -> dict[str, Any]:
        result = self._request("/v1/codespace/status")
        task_types = result.get("task_types")
        if (result.get("ok") is not True or result.get("executor") != "codespace" or
                not isinstance(task_types, list) or
                any(not isinstance(item, str) for item in task_types) or
                set(task_types) != self.ALLOWED_TASK_TYPES):
            raise RemoteTaskStoreError("Codespace bridge preflight returned an unexpected scope")
        return result

    @staticmethod
    def _validate_task_id(task_id: str) -> str:
        if not isinstance(task_id, str) or not re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
                task_id):
            raise ValueError("invalid Codespace task identifier")
        return task_id

    @staticmethod
    def _validate_lease_token(lease_token: str) -> str:
        if not isinstance(lease_token, str) or not lease_token.strip() or len(lease_token) > 200:
            raise ValueError("invalid Codespace lease token")
        return lease_token

    def claim_next(self) -> dict[str, Any] | None:
        result = self._request("/v1/codespace/tasks/claim", "POST", {})
        task = result.get("task")
        if task is None:
            return None
        if (not isinstance(task, dict) or task.get("task_type") not in self.ALLOWED_TASK_TYPES
                or not isinstance(result.get("lease_token"), str)
                or not result["lease_token"]):
            raise RemoteTaskStoreError("Codespace API returned an invalid or out-of-scope lease")
        try:
            self._validate_task_id(task.get("id"))
            self._validate_lease_token(result["lease_token"])
        except ValueError as exc:
            raise RemoteTaskStoreError("Codespace API returned an invalid lease identity") from exc
        task["lease_token"] = result["lease_token"]
        return task

    def heartbeat(self, task_id: str, lease_token: str) -> str:
        task_id = self._validate_task_id(task_id)
        lease_token = self._validate_lease_token(lease_token)
        result = self._request(
            f"/v1/codespace/tasks/{task_id}/heartbeat", "POST", {"lease_token": lease_token}
        )
        expiry = result.get("lease_expires_at")
        if not isinstance(expiry, str):
            raise RemoteTaskStoreError("Codespace API omitted lease expiry")
        return expiry

    def finish(self, task_id: str, status: str, result: dict[str, Any] | None = None,
               error: str | None = None, lease_token: str | None = None) -> None:
        task_id = self._validate_task_id(task_id)
        lease_token = self._validate_lease_token(lease_token)
        if status not in {"succeeded", "failed", "blocked"}:
            raise ValueError("invalid terminal status")
        payload: dict[str, Any] = {"lease_token": lease_token, "status": status}
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error
        self._request(f"/v1/codespace/tasks/{task_id}/finish", "POST", payload)
