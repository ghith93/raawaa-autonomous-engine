from scripts.reconcile_exact_ids import reconcile_exact_id


class FakeStore:
    def __init__(self, tasks, events=()):
        self.tasks = tasks
        self.events = list(events)
        self.calls = []

    def list_tasks(self, *, limit, task_id):
        self.calls.append(("list_tasks", limit, task_id))
        return self.tasks

    def audit_events(self, *, limit, task_id):
        self.calls.append(("audit_events", limit, task_id))
        return self.events

    def __getattr__(self, name):
        if name in {"enqueue", "claim", "recover", "mutate", "sweep", "delete"}:
            raise AssertionError(f"mutation method called: {name}")
        raise AttributeError(name)


def test_exact_id_returns_only_allowlisted_metadata():
    task_id = "237660ed-2a97-415f-8a24-fc3e20f5f641"
    store = FakeStore(
        [{
            "id": task_id,
            "status": "success",
            "task_type": "supervised_probe",
            "enqueued_at": "2026-11-01T00:00:00Z",
            "claimed_at": None,
            "finished_at": "2026-11-01T00:00:03Z",
            "lease_timeout_seconds": 30,
            "lease_expired": False,
            "attempt": 1,
            "max_attempts": 2,
            "payload_json": '{"secret":"must-not-leak","customer":"private"}',
        }],
        [{"task_id": task_id, "event": "task.completed",
          "payload": {"secret": "must-not-leak"}}],
    )
    result = reconcile_exact_id(store, task_id)
    rendered = str(result)
    assert result["task_id"] == task_id
    assert result["status"] == "success"
    assert result["audit_event_names"] == ["task.completed"]
    assert "payload" not in result
    assert "redacted_payload" not in result
    assert "must-not-leak" not in rendered
    assert "customer" not in rendered
    assert [call[0] for call in store.calls] == ["list_tasks", "audit_events"]


def test_wrong_id_is_rejected_without_audit_lookup():
    requested = "237660ed-2a97-415f-8a24-fc3e20f5f641"
    store = FakeStore([{"id": "different-id", "status": "success"}])
    result = reconcile_exact_id(store, requested)
    assert result["status"] == "not_found"
    assert result["audit_event_names"] == []
    assert [call[0] for call in store.calls] == ["list_tasks"]


def test_missing_id_is_reported_without_mutation():
    requested = "0bb6b3cb-c16e-4c12-92c8-2602206c7937"
    store = FakeStore([])
    result = reconcile_exact_id(store, requested)
    assert result["task_id"] == requested
    assert result["status"] == "not_found"
    assert [call[0] for call in store.calls] == ["list_tasks"]


def test_audit_events_are_scoped_and_event_names_validated():
    task_id = "237660ed-2a97-415f-8a24-fc3e20f5f641"
    store = FakeStore(
        [{"id": task_id, "status": "queued"}],
        [
            {"task_id": task_id, "event": "task.claimed"},
            {"task_id": "other-id", "event": "task.secret"},
            {"task_id": task_id, "event": "bad event with spaces"},
            {"task_id": task_id, "event": "task.finished"},
        ],
    )
    result = reconcile_exact_id(store, task_id)
    assert result["audit_event_names"] == ["task.claimed", "task.finished"]
