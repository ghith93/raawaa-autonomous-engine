"""Deterministic task-to-specialist contract; this module never calls a model."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

TASK_AGENT_CONTRACTS = {
    "research_brief": ("researcher", "research"),
    "code_review": ("reviewer", "review"),
    "content_draft": ("marketer", "draft_content"),
    "system_audit": ("operator", "monitor"),
}


class AgentContractError(ValueError):
    """A task cannot be safely assigned to an enabled specialist."""


def load_agent_roster() -> dict[str, Any]:
    with (ROOT / "config" / "agents.json").open(encoding="utf-8") as stream:
        roster = json.load(stream)
    if not isinstance(roster, dict) or not isinstance(roster.get("agents"), list):
        raise AgentContractError("agent roster has an invalid shape")
    return roster


def build_task_contract(task: dict[str, Any], roster: dict[str, Any]) -> dict[str, Any]:
    """Resolve an allowlisted task to one enabled agent and its required capability."""
    task_type = task.get("task_type")
    assignment = TASK_AGENT_CONTRACTS.get(task_type)
    if assignment is None:
        raise AgentContractError("task type has no specialist assignment")
    agent_id, capability = assignment
    agents = roster.get("agents", [])
    agent = next(
        (item for item in agents if isinstance(item, dict) and item.get("id") == agent_id),
        None,
    )
    if not agent or agent.get("enabled") is not True:
        raise AgentContractError(f"required specialist is unavailable: {agent_id}")
    capabilities = agent.get("capabilities", [])
    if not isinstance(capabilities, list) or capability not in capabilities:
        raise AgentContractError(f"specialist lacks required capability: {capability}")

    try:
        payload = json.loads(task.get("payload_json", "{}"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentContractError("task payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise AgentContractError("task payload must be a JSON object")
    title = task.get("title")
    if not isinstance(title, str) or not title.strip():
        raise AgentContractError("task title is required")

    return {
        "mode": "dry_run",
        "task_type": task_type,
        "title": title,
        "agent_id": agent_id,
        "agent_role": agent.get("role", agent_id),
        "required_capability": capability,
        "payload_received": payload,
        "execution_started": False,
        "provider_calls": 0,
        "external_writes": 0,
        "next_step": "A quota-gated agent executor and verified provider profile are required before inference.",
    }
