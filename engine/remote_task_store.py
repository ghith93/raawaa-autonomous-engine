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

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = bool(retryable)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


#: The Worker answers a storage failure with ``{"error": ..., "detail": ...}``.
#: Reading it back is the difference between "temporarily unavailable" and a
#: diagnosable cause; the body is truncated and control characters are dropped.
MAX_ERROR_DETAIL_CHARS = 240
VALID_WORKER_LANES = frozenset({
    "general", "specialist:researcher", "specialist:reviewer",
    "specialist:marketer", "specialist:operator", "specialist:mobile_tester",
})


def _error_detail(exc: HTTPError) -> str:
    """Return a bounded reason from an error response body, or "" when absent."""
    try:
        body = exc.read(1024)
    except Exception:  # noqa: BLE001 - a missing body must never mask the status
        return ""
    if not body:
        return ""
    try:
        parsed = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    parts = []
    for key in ("error", "detail"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(" ".join(value.split())[:MAX_ERROR_DETAIL_CHARS])
    return f": {' | '.join(parts)}" if parts else ""


class RemoteTaskStore:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = 15.0, access_client_id: str | None = None,
                 access_client_secret: str | None = None, reject_redirects: bool = False,
                 lane_token: str | None = None, worker_lane: str | None = None):
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
        self.lane_token = lane_token if lane_token is not None else os.environ.get("RAAWAA_ENGINE_LANE_TOKEN", "")
        self.worker_lane = worker_lane or os.environ.get("RAAWAA_ENGINE_WORKER_LANE", "general")
        if self.worker_lane not in VALID_WORKER_LANES:
            raise ValueError("invalid worker lane")
        self.reject_redirects = bool(reject_redirects)
        if not self.base_url.startswith("https://") or not self.token:
            raise ValueError("Remote task store requires an HTTPS API URL and token")
        if bool(self.access_client_id) != bool(self.access_client_secret):
            raise ValueError("Cloudflare Access requires both service-token credentials")

    def _request(self, path: str, method: str = "GET",
                 payload: dict[str, Any] | None = None,
                 worker_lane: str | None = None) -> dict[str, Any]:
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
        if worker_lane is not None:
            if worker_lane not in VALID_WORKER_LANES:
                raise ValueError("invalid worker_lane")
            if self.lane_token:
                headers["X-Engine-Lane-Token"] = self.lane_token
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
                raise RemoteTaskStoreError("Remote task API authentication failed", status_code=401) from None
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
                    f"Remote task API returned HTTP 403; inspect edge/access policy{detail}",
                    status_code=403,
                ) from None
            if exc.code == 409:
                raise RemoteTaskStoreError("Remote task lease was lost", status_code=409) from None
            if exc.code == 404:
                raise RemoteTaskStoreError("Remote task was not found", status_code=404) from None
            if exc.code == 429:
                raise RemoteTaskStoreError(
                    "Remote task API rate limit reached; retry in a later run",
                    status_code=429,
                    retryable=True,
                ) from None
            if 500 <= exc.code <= 599 or exc.code == 408 or exc.code == 1027:
                raise RemoteTaskStoreError(
                    f"Remote task API temporarily unavailable (HTTP {exc.code}){_error_detail(exc)}",
                    status_code=exc.code,
                    retryable=True,
                ) from None
            raise RemoteTaskStoreError(f"Remote task API returned HTTP {exc.code}", status_code=exc.code) from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise RemoteTaskStoreError(
                "Remote task API is unavailable or returned invalid JSON",
                retryable=True,
            ) from None

    def enqueue(self, task_type: str, title: str, payload: dict[str, Any] | None = None,
                idempotency_key: str | None = None, max_attempts: int = 2,
                priority: int | None = None, depends_on: list[str] | None = None,
                execution_lane: str = "general") -> dict[str, Any]:
        import uuid
        request_payload = {
            "task_type": task_type, "title": title, "payload": payload or {},
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
            "max_attempts": max_attempts, "execution_lane": execution_lane,
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

    def list_tasks(self, limit: int = 50, worker_lane: str = "general") -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        if worker_lane not in {"general", "specialist:researcher", "specialist:reviewer",
                               "specialist:marketer", "specialist:operator"}:
            raise ValueError("invalid worker_lane")
        path = f"/v1/tasks?limit={limit}"
        if worker_lane != "general":
            path += f"&worker_lane={worker_lane}"
        result = self._request(path, worker_lane=worker_lane)
        tasks = result.get("tasks")
        if not isinstance(tasks, list):
            raise RemoteTaskStoreError("Remote task API returned an invalid task list")
        return tasks

    def claim_next(self, lease_timeout_seconds: int = 900,
                   execution_mode: str = "unknown", worker_lane: str | None = None) -> dict[str, Any] | None:
        if not 1 <= int(lease_timeout_seconds) <= 900:
            raise ValueError("remote lease timeout must be between 1 and 900 seconds")
        if execution_mode not in {"unknown", "dry_run", "live_inference"}:
            raise ValueError("invalid execution_mode")
        worker_lane = worker_lane or self.worker_lane
        if worker_lane not in VALID_WORKER_LANES:
            raise ValueError("invalid worker_lane")
        result = self._request("/v1/tasks/claim", "POST",
                               {"execution_mode": execution_mode, "worker_lane": worker_lane},
                               worker_lane=worker_lane)
        task = result.get("task")
        if task is None:
            return None
        if not isinstance(task, dict) or not result.get("lease_token"):
            raise RemoteTaskStoreError("Remote task API returned an invalid lease")
        task["lease_token"] = result["lease_token"]
        return task

    def claim_supervised_probe(self, task_id: str) -> dict[str, Any] | None:
        """Claim only this exact, pending ``supervised_probe`` row in dry-run mode.

        The Worker route deliberately avoids the general queue's unrelated-task
        dependency sweep. It returns no lease for any other task type or state.
        """
        import re

        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise ValueError("task_id must be 1-128 safe identifier characters")
        result = self._request(
            f"/v1/tasks/{task_id}/claim-supervised-probe", "POST",
            {"worker_lane": self.worker_lane}, worker_lane=self.worker_lane
        )
        task = result.get("task")
        if task is None:
            return None
        lease_token = result.get("lease_token")
        if (not isinstance(task, dict) or not isinstance(lease_token, str) or not lease_token
                or task.get("id") != task_id or task.get("task_type") != "supervised_probe"
                or task.get("status") != "running" or task.get("execution_mode") != "dry_run"):
            raise RemoteTaskStoreError("Remote task API returned an invalid supervised-probe lease")
        task["lease_token"] = lease_token
        return task

    def recover_supervised_probe(self, task_id: str) -> dict[str, Any]:
        """Recover only one expired, exact ``supervised_probe`` lease.

        The Worker preserves its process-dispatch row; a replacement lease
        quarantines ``in_flight`` before launch, or exhausted recovery marks it
        ambiguous without issuing another lease.
        """
        import re

        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise ValueError("task_id must be 1-128 safe identifier characters")
        result = self._request(
            f"/v1/tasks/{task_id}/recover-supervised-probe", "POST",
            {"worker_lane": self.worker_lane}, worker_lane=self.worker_lane
        )
        status = result.get("status")
        if status not in {"pending", "failed", "succeeded", "blocked"}:
            raise RemoteTaskStoreError("Remote task API returned an invalid supervised-probe recovery")
        if type(result.get("recovered")) is not bool:
            raise RemoteTaskStoreError("Remote task API omitted supervised-probe recovery status")
        return result

    def heartbeat(self, task_id: str, lease_token: str,
                  lease_timeout_seconds: int = 900, worker_lane: str | None = None) -> str:
        if not 1 <= int(lease_timeout_seconds) <= 900:
            raise ValueError("remote lease timeout must be between 1 and 900 seconds")
        worker_lane = worker_lane or self.worker_lane
        result = self._request(f"/v1/tasks/{task_id}/heartbeat", "POST",
                               {"lease_token": lease_token, "worker_lane": worker_lane},
                               worker_lane=worker_lane)
        expiry = result.get("lease_expires_at")
        if not isinstance(expiry, str):
            raise RemoteTaskStoreError("Remote task API omitted lease expiry")
        return expiry

    def finish(self, task_id: str, status: str, result: dict[str, Any] | None = None,
               error: str | None = None, lease_token: str | None = None,
               execution_mode: str | None = None, worker_lane: str | None = None) -> None:
        if status not in {"succeeded", "failed", "blocked"}:
            raise ValueError("invalid terminal status")
        worker_lane = worker_lane or self.worker_lane
        payload: dict[str, Any] = {"lease_token": lease_token, "status": status,
                                   "worker_lane": worker_lane}
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
        self._request(f"/v1/tasks/{task_id}/finish", "POST", payload, worker_lane=worker_lane)

    def retry_or_fail(self, task_id: str, error: str,
                      lease_token: str | None = None, worker_lane: str | None = None) -> str:
        worker_lane = worker_lane or self.worker_lane
        response = self._request(f"/v1/tasks/{task_id}/retry", "POST",
                                 {"lease_token": lease_token, "error": error,
                                  "worker_lane": worker_lane}, worker_lane=worker_lane)
        status = response.get("status")
        if status not in {"pending", "failed"}:
            raise RemoteTaskStoreError("Remote task API returned an invalid retry status")
        return status

    def recover_stale(self, lease_timeout_seconds: int = 900,
                      worker_lane: str | None = None) -> dict[str, int]:
        if not 1 <= int(lease_timeout_seconds) <= 900:
            raise ValueError("remote lease timeout must be between 1 and 900 seconds")
        worker_lane = worker_lane or self.worker_lane
        result = self._request("/v1/tasks/recover", "POST",
                               {"worker_lane": worker_lane}, worker_lane=worker_lane)
        if not isinstance(result.get("requeued"), int) or not isinstance(result.get("failed"), int):
            raise RemoteTaskStoreError("Remote task API returned invalid recovery counts")
        return {"requeued": result["requeued"], "failed": result["failed"]}

    def audit_events(self, limit: int = 100, task_id: str | None = None) -> list[dict[str, Any]]:
        """Read bounded audit rows through the authenticated control API.

        Mirrors the Worker's ``GET /v1/audit`` contract: newest first, at
        most 100 rows, optionally filtered to one exact task id.
        """
        import re

        limit = max(1, min(int(limit), 100))
        query = f"limit={limit}"
        if task_id is not None:
            if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
                raise ValueError("task_id must be 1-128 safe identifier characters")
            query += f"&task_id={task_id}"
        result = self._request(f"/v1/audit?{query}")
        events = result.get("events")
        if not isinstance(events, list):
            raise RemoteTaskStoreError("Remote task API returned an invalid audit list")
        return events
