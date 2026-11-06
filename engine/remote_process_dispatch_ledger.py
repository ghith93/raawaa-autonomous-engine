"""Lease-fenced D1 adapter for supervised process dispatches.

This is intentionally separate from TaskInvocationStore: subprocess outcomes
and at-most-once dispatch have different semantics from model route attempts.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import replace
from typing import Any, Mapping

from engine.process_supervisor import (
    CLASSIFICATIONS,
    DispatchLedgerError,
    DispatchRecord,
    SUPERVISOR_VERSION,
)
from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError

STEP_KEY = "supervised_process:v1"
MAX_STORED_OUTPUT_BYTES = 3500
OUTCOME_FIELDS = (
    "classification", "exit_code", "signal_number", "timed_out", "cancelled",
    "output_truncated", "stdout", "stderr", "duration_seconds", "descendants_killed",
)


def _bounded_text(value: object) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_STORED_OUTPUT_BYTES:
        return value, False
    return encoded[:MAX_STORED_OUTPUT_BYTES].decode("utf-8", "ignore"), True


class RemoteProcessDispatchLedger:
    """D1-backed DispatchLedger implementation for the remote task queue.

    ``begin`` performs durable prepare then start before the supervisor can spawn
    a child. A response lost after start intentionally leaves an in-flight row;
    every later delivery is quarantined rather than launched again.
    """

    def __init__(self, task_store: RemoteTaskStore):
        if not isinstance(task_store, RemoteTaskStore):
            raise TypeError("task_store must be a configured RemoteTaskStore")
        self.task_store = task_store
        self._records: dict[str, DispatchRecord] = {}
        self._lease_tokens: dict[str, str] = {}

    @staticmethod
    def _fingerprint(operation: str, argv_digest: str) -> str:
        material = json.dumps({
            "operation": operation,
            "operation_version": SUPERVISOR_VERSION,
            "argv_digest": argv_digest,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _post(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise ValueError("task_id is invalid")
        worker_lane = self.task_store.worker_lane
        request_payload = {**payload, "worker_lane": worker_lane}
        response = self.task_store._request(
            f"/v1/tasks/{task_id}/process-dispatches", "POST", request_payload,
            worker_lane=worker_lane,
        )
        if not isinstance(response, dict):
            raise RemoteTaskStoreError("Process dispatch API returned invalid data")
        return response

    @staticmethod
    def _record(value: object) -> DispatchRecord:
        if not isinstance(value, dict):
            raise RemoteTaskStoreError("Process dispatch API omitted the dispatch record")
        required = (
            "dispatch_id", "task_id", "step_key", "operation", "operation_version",
            "operation_fingerprint", "status", "outcome_version", "outcome", "detail",
            "created_at", "updated_at",
        )
        if any(key not in value for key in required):
            raise RemoteTaskStoreError("Process dispatch API returned an incomplete identity")
        for key in ("dispatch_id", "task_id", "step_key", "operation", "operation_version",
                    "operation_fingerprint", "status", "created_at", "updated_at"):
            if not isinstance(value[key], str):
                raise RemoteTaskStoreError("Process dispatch API returned invalid identity fields")
        if value["status"] not in {"prepared", "in_flight", "ambiguous", "settled"}:
            raise RemoteTaskStoreError("Process dispatch API returned an invalid state")
        outcome = value["outcome"]
        if value["status"] == "settled":
            if (value["outcome_version"] != 1 or not isinstance(outcome, dict) or
                    set(outcome) != set(OUTCOME_FIELDS)):
                raise RemoteTaskStoreError("Settled process dispatch omitted its versioned outcome")
            classification = outcome.get("classification")
            if classification not in CLASSIFICATIONS or type(outcome.get("output_truncated")) is not bool:
                raise RemoteTaskStoreError("Settled process dispatch outcome is invalid")
            stdout, stderr = outcome.get("stdout"), outcome.get("stderr")
            if (not isinstance(stdout, str) or not isinstance(stderr, str) or
                    len(stdout) > 4000 or len(stderr) > 4000):
                raise RemoteTaskStoreError("Settled process dispatch output is invalid")
            if len(json.dumps(outcome, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 12 * 1024:
                raise RemoteTaskStoreError("Settled process dispatch outcome exceeded its byte limit")
            exit_code = outcome.get("exit_code")
            signal_number = outcome.get("signal_number")
            if (type(exit_code) not in {int, type(None)} or
                    (exit_code is not None and not -255 <= exit_code <= 255) or
                    type(signal_number) not in {int, type(None)} or
                    (signal_number is not None and not 1 <= signal_number <= 64)):
                raise RemoteTaskStoreError("Settled process dispatch exit status is invalid")
            if (type(outcome.get("timed_out")) is not bool or
                    type(outcome.get("cancelled")) is not bool or
                    outcome["timed_out"] != (classification == "timed_out") or
                    outcome["cancelled"] != (classification == "cancelled")):
                raise RemoteTaskStoreError("Settled process dispatch termination flags are invalid")
            duration = outcome.get("duration_seconds")
            descendants = outcome.get("descendants_killed")
            if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
                    not math.isfinite(duration) or not 0 <= duration <= 900 or
                    type(descendants) is not int or not 0 <= descendants <= 10000):
                raise RemoteTaskStoreError("Settled process dispatch metadata is invalid")
            truncated = outcome["output_truncated"]
        else:
            if value["outcome_version"] is not None or outcome is not None:
                raise RemoteTaskStoreError("Unsettled process dispatch exposed an outcome")
            classification = None
            exit_code = None
            truncated = False
        detail = value["detail"]
        if detail is not None and not isinstance(detail, str):
            raise RemoteTaskStoreError("Process dispatch detail is invalid")
        return DispatchRecord(
            dispatch_id=value["dispatch_id"], task_id=value["task_id"],
            operation=value["operation"], argv_digest="",
            status=value["status"], classification=classification, exit_code=exit_code,
            output_truncated=truncated, detail=detail,
            created_at=value["created_at"], updated_at=value["updated_at"],
            result=outcome, step_key=value["step_key"],
            operation_version=value["operation_version"],
            operation_fingerprint=value["operation_fingerprint"],
        )

    def begin(
        self,
        dispatch_id: str,
        *,
        task_id: str,
        operation: str,
        argv_digest: str,
        lease_fingerprint: str | None = None,
        lease_token: str | None = None,
    ) -> DispatchRecord | None:
        if not isinstance(lease_token, str) or not lease_token.strip():
            raise DispatchLedgerError("remote process dispatch requires the active task lease")
        if len(dispatch_id) != 32 or any(ch not in "0123456789abcdef" for ch in dispatch_id):
            raise DispatchLedgerError("invalid process dispatch identifier")
        if len(argv_digest) != 64 or any(ch not in "0123456789abcdef" for ch in argv_digest):
            raise DispatchLedgerError("invalid process operation digest")
        fingerprint = self._fingerprint(operation, argv_digest)
        self._lease_tokens[dispatch_id] = lease_token
        lease_hash = lease_fingerprint or hashlib.sha256(lease_token.encode("utf-8")).hexdigest()[:16]
        response = self._post(task_id, {
            "action": "prepare", "lease_token": lease_token,
            "dispatch_id": dispatch_id, "step_key": STEP_KEY,
            "operation": operation, "operation_version": SUPERVISOR_VERSION,
            "operation_fingerprint": fingerprint, "lease_fingerprint": lease_hash,
        })
        record = self._record(response.get("dispatch"))
        if (record.dispatch_id != dispatch_id or record.task_id != task_id or
                record.step_key != STEP_KEY or record.operation != operation or
                record.operation_version != SUPERVISOR_VERSION or record.operation_fingerprint != fingerprint):
            raise DispatchLedgerError("remote process dispatch identity did not match the request")
        if record.status == "in_flight":
            # A new lease can only observe this after the prior task lease was
            # recovered. Fence the old worker out and record the uncertainty;
            # the child may have run, so this identity can never be started again.
            response = self._post(task_id, {
                "action": "ambiguous", "lease_token": lease_token,
                "dispatch_id": dispatch_id,
                "detail": "recovered_in_flight_dispatch",
            })
            if response.get("status") != "ambiguous":
                raise RemoteTaskStoreError("Process dispatch API did not quarantine the recovered dispatch")
            record = replace(record, status="ambiguous", detail="recovered_in_flight_dispatch")
        self._records[dispatch_id] = record
        if record.status == "prepared":
            started = self._post(task_id, {
                "action": "start", "lease_token": lease_token, "dispatch_id": dispatch_id,
            })
            if started.get("status") != "in_flight":
                raise RemoteTaskStoreError("Process dispatch API did not confirm durable start")
            self._records[dispatch_id] = DispatchRecord(
                dispatch_id=dispatch_id, task_id=task_id, operation=operation,
                argv_digest="", status="in_flight", classification=None,
                exit_code=None, output_truncated=False, detail=None,
                created_at=record.created_at, updated_at=record.updated_at,
                step_key=record.step_key, operation_version=record.operation_version,
                operation_fingerprint=record.operation_fingerprint,
            )
            return None
        return record

    def settle(
        self,
        dispatch_id: str,
        *,
        classification: str,
        exit_code: int | None,
        output_truncated: bool,
        result: dict[str, Any] | None = None,
    ) -> None:
        record = self._records.get(dispatch_id)
        if record is None:
            raise DispatchLedgerError("cannot settle a process dispatch not started by this ledger")
        outcome = dict(result or {})
        outcome["classification"] = classification
        outcome["exit_code"] = exit_code
        outcome["output_truncated"] = bool(output_truncated or outcome.get("output_truncated", False))
        for key in ("stdout", "stderr"):
            bounded, truncated = _bounded_text(outcome.get(key, ""))
            outcome[key] = bounded
            outcome["output_truncated"] = outcome["output_truncated"] or truncated
        outcome.setdefault("signal_number", None)
        outcome.setdefault("timed_out", classification == "timed_out")
        outcome.setdefault("cancelled", classification == "cancelled")
        outcome.setdefault("duration_seconds", 0.0)
        outcome.setdefault("descendants_killed", 0)
        if set(outcome) != set(OUTCOME_FIELDS):
            raise DispatchLedgerError("process dispatch outcome has an unsupported shape")
        response = self._post(record.task_id, {
            "action": "settle", "lease_token": self._lease_tokens.get(dispatch_id),
            "dispatch_id": dispatch_id, "outcome": outcome,
        })
        if response.get("status") != "settled":
            raise RemoteTaskStoreError("Process dispatch API did not confirm settlement")
        serialized = json.loads(json.dumps(outcome))
        self._records[dispatch_id] = DispatchRecord(
            dispatch_id=record.dispatch_id, task_id=record.task_id,
            operation=record.operation, argv_digest=record.argv_digest,
            status="settled", classification=classification, exit_code=exit_code,
            output_truncated=outcome["output_truncated"], detail=None,
            created_at=record.created_at, updated_at=record.updated_at, result=serialized,
            step_key=record.step_key, operation_version=record.operation_version,
            operation_fingerprint=record.operation_fingerprint,
        )

    def quarantine(self, dispatch_id: str, detail: str) -> None:
        record = self._records.get(dispatch_id)
        if record is None:
            raise DispatchLedgerError("cannot quarantine an unknown remote process dispatch")
        response = self._post(record.task_id, {
            "action": "ambiguous", "lease_token": self._lease_tokens.get(dispatch_id),
            "dispatch_id": dispatch_id, "detail": str(detail)[:400],
        })
        if response.get("status") != "ambiguous":
            raise RemoteTaskStoreError("Process dispatch API did not confirm ambiguity")

    def get(self, dispatch_id: str) -> DispatchRecord | None:
        return self._records.get(dispatch_id)

    def reconcile(self, stale_after_seconds: float = 900.0) -> int:
        raise DispatchLedgerError(
            "remote process dispatch reconciliation is unsupported; in-flight rows remain non-replayable"
        )
