"""Durable task-step identity client over the authenticated D1 control Worker."""
from __future__ import annotations

from typing import Any

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError


class TaskInvocationStore:
    """Lease-fenced logical model-call checkpoints; never performs model I/O."""

    def __init__(self, task_store: RemoteTaskStore, worker_lane: str = "general"):
        if not isinstance(task_store, RemoteTaskStore):
            raise TypeError("task_store must be a configured RemoteTaskStore")
        if worker_lane not in {
            "general", "specialist:researcher", "specialist:reviewer",
            "specialist:marketer", "specialist:operator", "specialist:mobile_tester",
        }:
            raise ValueError("invalid worker_lane")
        self.task_store = task_store
        self.worker_lane = worker_lane

    def _post(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        result = self.task_store._request(
            f"/v1/tasks/{task_id}/invocations", "POST", payload,
            worker_lane=self.worker_lane,
        )
        if not isinstance(result, dict):
            raise RemoteTaskStoreError("Invocation API returned invalid data")
        return result

    def prepare(self, task_id: str, lease_token: str, step_key: str,
                task_kind: str) -> dict[str, str]:
        if not all(isinstance(value, str) and value.strip() for value in
                   (lease_token, step_key, task_kind)):
            raise ValueError("lease_token, step_key and task_kind are required")
        result = self._post(task_id, {
            "action": "prepare", "lease_token": lease_token,
            "step_key": step_key, "task_kind": task_kind,
        })
        invocation = result.get("invocation")
        if not isinstance(invocation, dict):
            raise RemoteTaskStoreError("Invocation API omitted the prepared invocation")
        for key in ("invocation_id", "request_id", "step_key", "status"):
            if not isinstance(invocation.get(key), str):
                raise RemoteTaskStoreError("Invocation API returned an invalid identity")
        if invocation["status"] != "prepared":
            raise RemoteTaskStoreError("Invocation is not in the prepared state")
        return {
            "invocation_id": invocation["invocation_id"],
            "request_id": invocation["request_id"],
            "step_key": invocation["step_key"],
            "status": invocation["status"],
        }

    def start(self, task_id: str, lease_token: str,
              invocation_id: str, route_id: str) -> None:
        self._post(task_id, {
            "action": "start", "lease_token": lease_token,
            "invocation_id": invocation_id, "route_id": route_id,
        })

    def reject(self, task_id: str, lease_token: str, invocation_id: str,
               route_id: str, rejection_code: str) -> None:
        self._post(task_id, {
            "action": "reject", "lease_token": lease_token,
            "invocation_id": invocation_id, "route_id": route_id,
            "rejection_code": rejection_code,
        })

    def mark_ambiguous(self, task_id: str, lease_token: str,
                       invocation_id: str) -> None:
        self._post(task_id, {
            "action": "ambiguous", "lease_token": lease_token,
            "invocation_id": invocation_id,
        })

    def settle(self, task_id: str, lease_token: str, invocation_id: str) -> None:
        self._post(task_id, {
            "action": "settle", "lease_token": lease_token,
            "invocation_id": invocation_id,
        })
