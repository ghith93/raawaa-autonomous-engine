"""Authenticated HTTP adapter for the Cloudflare D1 task queue.

Set RAAWAA_ENGINE_API_URL and RAAWAA_ENGINE_API_TOKEN to opt in. Optional
Cloudflare Access service-token headers are supported for protected Workers.
The local SQLite TaskStore remains the default and memory continues to use local SQLite.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from typing import Any


class RemoteTaskStoreError(RuntimeError):
    """Remote task API failure with a safe, non-secret message."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RemoteTaskStore:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = 15.0, access_client_id: str | None = None,
                 access_client_secret: str | None = None, reject_redirects: bool = False):
        self.base_url = (base_url or os.environ.get("RAAWAA_ENGINE_API_URL", "")).rstrip("/")
        self.token = token or os.environ.get("RAAWAA_ENGINE_API_TOKEN", "")
        self.access_client_id = (
            access_client_id if access_client_id is not None
            else os.environ.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "")
        )
        self.access_client_secret = (
            access_client_secret if access_client_secret is not None
            else os.environ.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "")
        )
        self.timeout = timeout
        self.reject_redirects = bool(reject_redirects)
        if not self.base_url.startswith("https://") or not self.token:
            raise ValueError("Remote task store requires an HTTPS API URL and token")
        if bool(self.access_client_id) != bool(self.access_client_secret):
            raise ValueError("Cloudflare Access requires both service-token credentials")

    def _request(self, path: str, method: str = "GET",
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)",
            **({"Content-Type": "application/json"} if body is not None else {}),
        }
        if self.access_client_id and self.access_client_secret:
            headers["CF-Access-Client-Id"] = self.access_client_id
            headers["CF-Access-Client-Secret"] = self.access_client_secret
        request = Request(self.base_url + path, data=body, method=method, headers=headers)
        try:
            opener = build_opener(_RejectRedirects) if self.reject_redirects else None
            response_context = opener.open(request, timeout=self.timeout) if opener else urlopen(request, timeout=self.timeout)
            with response_context as response:
                data = response.read(64 * 1024 + 1)
                if len(data) > 64 * 1024:
                    raise RemoteTaskStoreError("Remote task API response exceeded limit")
                parsed = json.loads(data.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise RemoteTaskStoreError("Remote task API returned invalid data")
                return parsed
        except HTTPError as exc:
            # Do not include response bodies, request headers, or credentials in errors.
            if exc.code == 401:
                raise RemoteTaskStoreError("Remote task API authentication failed") from None
            if exc.code == 403:
                metadata = []
                ray_id = exc.headers.get("CF-Ray") if exc.headers else None
                mitigated = exc.headers.get("cf-mitigated") if exc.headers else None
                if ray_id:
                    metadata.append(f"CF-Ray {ray_id}")
                if mitigated:
                    metadata.append(f"cf-mitigated {mitigated}")
                detail = f" (response metadata: {', '.join(metadata)})" if metadata else ""
                raise RemoteTaskStoreError(
                    f"Remote task API returned HTTP 403; inspect edge/access policy{detail}"
                ) from None
            if exc.code == 409:
                raise RemoteTaskStoreError("Remote task lease was lost") from None
            if exc.code == 404:
                raise RemoteTaskStoreError("Remote task was not found") from None
            if exc.code == 429:
                raise RemoteTaskStoreError("Remote task API rate limit reached; retry in a later run") from None
            if 500 <= exc.code <= 599 or exc.code == 408:
                raise RemoteTaskStoreError(f"Remote task API temporarily unavailable (HTTP {exc.code})") from None
            raise RemoteTaskStoreError(f"Remote task API returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise RemoteTaskStoreError("Remote task API is unavailable or returned invalid JSON") from None

    def enqueue(self, task_type: str, title: str, payload: dict[str, Any] | None = None,
                idempotency_key: str | None = None, max_attempts: int = 2,
                priority: int | None = None, depends_on: list[str] | None = None) -> dict[str, Any]:
        import uuid
        request_payload = {
            "task_type": task_type, "title": title, "payload": payload or {},
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
            "max_attempts": max_attempts,
        }
        if priority is not None:
            if type(priority) is not int or not 0 <= priority <= 100:
                raise ValueError("priority must be an integer between 0 and 100")
            request_payload["priority"] = priority
        if depends_on is not None:
            if (not isinstance(depends_on, list) or len(depends_on) > 20
                    or any(not isinstance(dep, str) or not dep.strip() or len(dep) > 128 for dep in depends_on)
                    or len(depends_on) != len(set(depends_on))):
                raise ValueError("depends_on must be a unique list of at most 20 task IDs")
            request_payload["depends_on"] = sorted(depends_on)
        result = self._request("/v1/tasks/enqueue", "POST", request_payload)
        task = result.get("task")
        if not isinstance(task, dict):
            raise RemoteTaskStoreError("Remote task API omitted the task")
        return task

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        result = self._request(f"/v1/tasks?limit={limit}")
        tasks = result.get("tasks")
        if not isinstance(tasks, list):
            raise RemoteTaskStoreError("Remote task API returned an invalid task list")
        return tasks

    def claim_next(self, lease_timeout_seconds: int = 900,
                   execution_mode: str = "unknown") -> dict[str, Any] | None:
        if not 1 <= int(lease_timeout_seconds) <= 900:
            raise ValueError("remote lease timeout must be between 1 and 900 seconds")
        if execution_mode not in {"unknown", "dry_run", "live_inference"}:
            raise ValueError("invalid execution_mode")
        result = self._request("/v1/tasks/claim", "POST", {"execution_mode": execution_mode})
        task = result.get("task")
        if task is None:
            return None
        if not isinstance(task, dict) or not result.get("lease_token"):
            raise RemoteTaskStoreError("Remote task API returned an invalid lease")
        task["lease_token"] = result["lease_token"]
        return task

    def heartbeat(self, task_id: str, lease_token: str,
                  lease_timeout_seconds: int = 900) -> str:
        if not 1 <= int(lease_timeout_seconds) <= 900:
            raise ValueError("remote lease timeout must be between 1 and 900 seconds")
        result = self._request(f"/v1/tasks/{task_id}/heartbeat", "POST",
                               {"lease_token": lease_token})
        expiry = result.get("lease_expires_at")
        if not isinstance(expiry, str):
            raise RemoteTaskStoreError("Remote task API omitted lease expiry")
        return expiry

    def finish(self, task_id: str, status: str, result: dict[str, Any] | None = None,
               error: str | None = None, lease_token: str | None = None,
               execution_mode: str | None = None) -> None:
        if status not in {"succeeded", "failed", "blocked"}:
            raise ValueError("invalid terminal status")
        payload: dict[str, Any] = {"lease_token": lease_token, "status": status}
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error
        if execution_mode is not None:
            if execution_mode not in {"unknown", "dry_run", "live_inference"}:
                raise ValueError("invalid execution_mode")
            payload["execution_mode"] = execution_mode
        elif result is not None and result.get("mode") in {"dry_run", "live_inference"}:
            payload["execution_mode"] = result["mode"]
        self._request(f"/v1/tasks/{task_id}/finish", "POST", payload)

    def retry_or_fail(self, task_id: str, error: str,
                      lease_token: str | None = None) -> str:
        response = self._request(f"/v1/tasks/{task_id}/retry", "POST",
                                 {"lease_token": lease_token, "error": error})
        status = response.get("status")
        if status not in {"pending", "failed"}:
            raise RemoteTaskStoreError("Remote task API returned an invalid retry status")
        return status

    def recover_stale(self, lease_timeout_seconds: int = 900) -> dict[str, int]:
        if not 1 <= int(lease_timeout_seconds) <= 900:
            raise ValueError("remote lease timeout must be between 1 and 900 seconds")
        result = self._request("/v1/tasks/recover", "POST", {})
        if not isinstance(result.get("requeued"), int) or not isinstance(result.get("failed"), int):
            raise RemoteTaskStoreError("Remote task API returned invalid recovery counts")
        return {"requeued": result["requeued"], "failed": result["failed"]}

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        # The public API intentionally does not expose audit records yet.
        raise NotImplementedError("Remote audit listing is not exposed by the control API")
