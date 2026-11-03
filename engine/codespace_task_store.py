"""Least-privilege client for the Codespace-only task API.

This client intentionally exposes no enqueue, list, quota, recovery, or admin methods.
"""
from __future__ import annotations

import os
from typing import Any

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
        self._client = RemoteTaskStore(
            base_url=base_url,
            token=code_token,
            timeout=timeout,
            access_client_id=access_client_id,
            access_client_secret=access_client_secret,
        )

    def _request(self, path: str, method: str = "GET",
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._client._request(path, method, payload)

    def preflight(self) -> dict[str, Any]:
        result = self._request("/v1/codespace/status")
        if (result.get("ok") is not True or result.get("executor") != "codespace" or
                set(result.get("task_types", [])) != self.ALLOWED_TASK_TYPES):
            raise RemoteTaskStoreError("Codespace bridge preflight returned an unexpected scope")
        return result

    def claim_next(self) -> dict[str, Any] | None:
        result = self._request("/v1/codespace/tasks/claim", "POST", {})
        task = result.get("task")
        if task is None:
            return None
        if (not isinstance(task, dict) or task.get("task_type") not in self.ALLOWED_TASK_TYPES
                or not isinstance(result.get("lease_token"), str)
                or not result["lease_token"]):
            raise RemoteTaskStoreError("Codespace API returned an invalid or out-of-scope lease")
        task["lease_token"] = result["lease_token"]
        return task

    def heartbeat(self, task_id: str, lease_token: str) -> str:
        result = self._request(
            f"/v1/codespace/tasks/{task_id}/heartbeat", "POST", {"lease_token": lease_token}
        )
        expiry = result.get("lease_expires_at")
        if not isinstance(expiry, str):
            raise RemoteTaskStoreError("Codespace API omitted lease expiry")
        return expiry

    def finish(self, task_id: str, status: str, result: dict[str, Any] | None = None,
               error: str | None = None, lease_token: str | None = None) -> None:
        if status not in {"succeeded", "failed", "blocked"}:
            raise ValueError("invalid terminal status")
        payload: dict[str, Any] = {"lease_token": lease_token, "status": status}
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error
        self._request(f"/v1/codespace/tasks/{task_id}/finish", "POST", payload)
