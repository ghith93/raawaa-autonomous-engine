"""Background lease renewal for synchronous task handlers.

The keeper renews a claimed task lease while a handler is running. Any renewal
failure is surfaced to the caller; callers must not persist a result after loss
of confirmed lease ownership.
"""
from __future__ import annotations

import threading
from typing import Any


class LeaseHeartbeatLost(RuntimeError):
    """The worker could not confirm continued ownership of its task lease."""


class LeaseHeartbeat:
    def __init__(
        self,
        store: Any,
        task_id: str,
        lease_token: str,
        interval_seconds: float = 300.0,
        lease_timeout_seconds: int = 900,
        worker_lane: str = "general",
    ):
        if not task_id or not lease_token:
            raise ValueError("task ID and lease token are required")
        if interval_seconds <= 0 or lease_timeout_seconds < 1:
            raise ValueError("heartbeat interval and lease timeout must be positive")
        if interval_seconds >= lease_timeout_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease timeout")
        self.store = store
        self.task_id = task_id
        self.lease_token = lease_token
        self.interval_seconds = interval_seconds
        self.lease_timeout_seconds = lease_timeout_seconds
        self.worker_lane = worker_lane
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._lock = threading.Lock()

    def start(self) -> "LeaseHeartbeat":
        if self._thread is not None:
            raise RuntimeError("lease heartbeat already started")
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-heartbeat-{self.task_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.store.heartbeat(
                    self.task_id,
                    self.lease_token,
                    self.lease_timeout_seconds,
                    worker_lane=self.worker_lane,
                )
            except BaseException as exc:
                with self._lock:
                    self._error = exc
                self._stop.set()
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # RemoteTaskStore requests have a 15-second timeout. Do not block
            # task shutdown indefinitely if the network is unavailable.
            self._thread.join(timeout=16.0)

    def check(self) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise LeaseHeartbeatLost(
                "Task lease heartbeat failed; result persistence is blocked."
            ) from error
