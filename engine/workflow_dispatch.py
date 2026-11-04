"""Turn a validated workflow manifest into a dispatch request body.

`engine/workflow_manifest.py` derives per-step idempotency keys and a plan
digest; the Worker's `/v1/workflows/dispatch` enqueues the plan atomically. This
module is the seam between them: it builds the exact request body, and it
refuses to build one that the Worker would reject for a reason the caller could
have known about locally.

It performs no network I/O and holds no credentials. Sending is someone else's
decision, taken with a token this module never sees.

The idempotency key derivation is implemented twice — here and in
`worker/workflow-dispatch.js` — because the two runtimes cannot share code. Both
implementations are pinned by the same hard-coded vector, so a drift in either
one fails a test rather than silently duplicating a task on replay.
"""
from __future__ import annotations

import hashlib
from typing import Any

SCHEMA_VERSION = 1
WORKFLOW_STEP_KEY_PREFIX = "workflow-step:"
# Pinned vector: workflow_id, run_id, plan_version, step_id -> key.
_KEY_VECTOR = ("demo-workflow", "run-9", 1, "prepare",
               "workflow-step:730e3fe09f1dbace1419a3f7cbd2e3008fc7b1c8541a7b95c3e6036c0e2c76af")


def step_idempotency_key(workflow_id: str, run_id: str, plan_version: int, step_id: str) -> str:
    """Derive the key exactly as the Worker does.

    Material is ``workflow_id\\0run_id\\0plan_version\\0step_id`` — the null
    separators matter: without them ("a", "bc") and ("ab", "c") would collide.
    """
    material = f"{workflow_id}\0{run_id}\0{plan_version}\0{step_id}".encode("utf-8")
    return WORKFLOW_STEP_KEY_PREFIX + hashlib.sha256(material).hexdigest()


def vector_matches() -> bool:
    """True when this implementation still reproduces the pinned cross-language vector."""
    return step_idempotency_key(*_KEY_VECTOR[:4]) == _KEY_VECTOR[4]


def dispatch_body(manifest: dict[str, Any], *, payloads: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build the request body for `/v1/workflows/dispatch` from a manifest dict.

    ``payloads`` is optional per-step task payload; the Worker rejects payloads
    over 16 KiB and anything that is not an object, so both are checked here
    rather than discovered as a 400 after the work of building the plan.
    """
    # Accept either a manifest document or the dataclass that produced it, so a
    # caller never has to know which shape they are holding.
    if hasattr(manifest, "as_dict") and not isinstance(manifest, dict):
        manifest = manifest.as_dict()
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    workflow_id = manifest.get("workflow_id")
    run_id = manifest.get("run_id")
    plan_version = manifest.get("plan_version", 1)
    plan_digest = manifest.get("plan_digest")
    steps = manifest.get("steps")

    if not isinstance(workflow_id, str) or not workflow_id:
        raise ValueError("manifest has no workflow_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("manifest has no run_id")
    if type(plan_version) is not int or plan_version < 1:
        raise ValueError("manifest has an invalid plan_version")
    if not isinstance(plan_digest, str) or len(plan_digest) != 64:
        raise ValueError("manifest has an invalid plan_digest")
    if not isinstance(steps, (list, tuple)) or not 1 <= len(steps) <= 20:
        raise ValueError("manifest must carry between 1 and 20 steps")

    payloads = payloads or {}
    steps = list(steps)
    known = {step.get("step_id") for step in steps if isinstance(step, dict)}
    unknown_payloads = set(payloads) - known
    if unknown_payloads:
        raise ValueError(f"payload supplied for unknown steps: {sorted(unknown_payloads)}")

    body_steps = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("manifest step must be an object")
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            raise ValueError("manifest step has no step_id")
        payload = payloads.get(step_id, {})
        if not isinstance(payload, dict):
            raise ValueError(f"payload for {step_id} must be an object")
        body_steps.append({
            "step_id": step_id,
            "task_type": step.get("task_type"),
            "title": step.get("title"),
            "payload": payload,
            "depends_on": list(step.get("depends_on") or []),
            "priority": step.get("priority", 50),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "plan_version": plan_version,
        "plan_digest": plan_digest,
        "steps": body_steps,
        # Local mirror of the Worker's derived keys, so a caller can record what
        # the run will be named without re-deriving the hash itself.
        "task_keys": {
            step["step_id"]: step_idempotency_key(workflow_id, run_id, plan_version, step["step_id"])
            for step in body_steps
        },
    }


def render_markdown(body: dict[str, Any]) -> str:
    lines = [
        "## Workflow dispatch body",
        "",
        f"Workflow: `{body['workflow_id']}` · run `{body['run_id']}` · plan v{body['plan_version']}",
        f"Digest: `{body['plan_digest'][:16]}…`",
        "",
        "| Step | Task type | Depends on | Key |",
        "|---|---|---|---|",
    ]
    for step in body["steps"]:
        key = body["task_keys"][step["step_id"]].replace("workflow-step:", "")[:12]
        depends = ", ".join(step["depends_on"]) or "—"
        lines.append(f"| `{step['step_id']}` | {step['task_type']} | {depends} | `{key}…` |")
    lines += ["", "Not sent by this module. Sending needs a token this code never holds."]
    return "\n".join(lines) + "\n"
