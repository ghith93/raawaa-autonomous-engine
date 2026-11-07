"""Read-only queries over the evidence-backed project/agent topology map.

The map is advisory architecture context, never an execution or permission source.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP_PATH = ROOT / "config" / "project-agent-map.json"
MAX_NODES = 500
MAX_EDGES = 2000
MAX_EVIDENCE_ITEMS = 3
MAX_RELATIONSHIPS = 8
MAX_CONTEXT_BYTES = 2500


class ProjectTopologyError(ValueError):
    """The persisted project topology cannot be trusted as a valid map."""


def load_project_topology(path: Path | None = None) -> dict[str, Any]:
    source = path or DEFAULT_MAP_PATH
    try:
        raw = source.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectTopologyError("project topology file is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ProjectTopologyError("project topology must be a JSON object")
    return value


def _bounded_text(value: Any, field: str, *, maximum: int = 256, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ProjectTopologyError(f"project topology has an invalid {field}")
    return value


def _bounded_string_list(value: Any, field: str, *, maximum_items: int = 30,
                         maximum_length: int = 256) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ProjectTopologyError(f"project topology has an invalid {field}")
    result: list[str] = []
    for item in value:
        result.append(_bounded_text(item, field, maximum=maximum_length))
    return result


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "id": node["id"],
        "kind": node["kind"],
        "status": node["status"],
        "evidence": _bounded_string_list(
            node.get("evidence", []), "node evidence",
            maximum_items=MAX_EVIDENCE_ITEMS,
        ),
    }
    for key in ("role", "label"):
        value = _bounded_text(node.get(key), f"node {key}", optional=True)
        if value is not None:
            summary[key] = value
    capabilities = _bounded_string_list(
        node.get("capabilities", []), "node capabilities", maximum_items=8,
        maximum_length=80,
    )
    if capabilities:
        summary["capabilities"] = capabilities
    return summary


def build_agent_topology_context(agent_id: str, project_map: dict[str, Any]) -> dict[str, Any]:
    """Return bounded direct relationships for one agent without changing routing."""
    if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 128:
        raise ProjectTopologyError("agent id is invalid")
    if not isinstance(project_map, dict):
        raise ProjectTopologyError("project topology must be an object")
    schema_version = project_map.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        raise ProjectTopologyError("unsupported project topology schema")
    map_id = _bounded_text(project_map.get("map_id"), "map_id", maximum=128)
    scope_note = _bounded_text(project_map.get("scope_note"), "scope_note", maximum=500)
    nodes = project_map.get("nodes")
    edges = project_map.get("edges")
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        raise ProjectTopologyError("project topology nodes are invalid")
    if not isinstance(edges, list) or len(edges) > MAX_EDGES:
        raise ProjectTopologyError("project topology edges are invalid")

    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ProjectTopologyError("project topology contains an invalid node")
        node_id = _bounded_text(node.get("id"), "node id", maximum=160)
        if node_id in by_id:
            raise ProjectTopologyError("project topology contains duplicate node ids")
        _bounded_text(node.get("kind"), "node kind", maximum=64)
        _bounded_text(node.get("status"), "node status", maximum=160)
        # Validate every node's fields, not only the selected agent's fields.
        _node_summary(node)
        by_id[node_id] = node

    normalized_edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ProjectTopologyError("project topology contains an invalid edge")
        source = _bounded_text(edge.get("from"), "edge source", maximum=160)
        target = _bounded_text(edge.get("to"), "edge target", maximum=160)
        relation = _bounded_text(edge.get("relation"), "edge relation", maximum=128)
        status = _bounded_text(edge.get("status"), "edge status", maximum=200)
        if source not in by_id or target not in by_id:
            raise ProjectTopologyError("project topology edge references an unknown node")
        identity = (source, target, relation, status)
        if identity in seen_edges:
            raise ProjectTopologyError("project topology contains duplicate edges")
        seen_edges.add(identity)
        normalized_edges.append({
            "from": source, "to": target, "relation": relation, "status": status,
        })

    agent_node_id = f"agent:{agent_id}"
    agent_node = by_id.get(agent_node_id)
    if agent_node is None:
        return {
            "status": "unavailable",
            "mode": "read_only_advisory",
            "reason": "agent_node_missing",
            "map_id": map_id,
        }

    relationships: list[dict[str, Any]] = []
    for edge in normalized_edges:
        if edge["from"] == agent_node_id:
            direction = "outgoing"
            related_id = edge["to"]
        elif edge["to"] == agent_node_id:
            direction = "incoming"
            related_id = edge["from"]
        else:
            continue
        relationships.append({
            "direction": direction,
            "relation": edge["relation"],
            "edge_status": edge["status"],
            "node": _node_summary(by_id[related_id]),
        })
    relationships.sort(key=lambda item: (
        item["direction"], item["relation"], item["node"]["id"],
    ))
    relationship_count = len(relationships)
    relationships = relationships[:MAX_RELATIONSHIPS]

    try:
        canonical = json.dumps(
            project_map, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProjectTopologyError("project topology is not canonical JSON data") from exc
    context = {
        "status": "available",
        "mode": "read_only_advisory",
        "map_id": map_id,
        "schema_version": 1,
        "map_sha256": hashlib.sha256(canonical).hexdigest(),
        "scope_note": scope_note,
        "agent_node": _node_summary(agent_node),
        "relationships": relationships,
        "relationship_count_total": relationship_count,
        "truncated": relationship_count > len(relationships),
        "authority_note": (
            "Topology is descriptive context only. It does not prove a live agent process, "
            "authorize tools, change task assignment, or override operating policy."
        ),
    }
    while len(json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")) > MAX_CONTEXT_BYTES and context["relationships"]:
        context["relationships"].pop()
        context["truncated"] = True
    if len(json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ProjectTopologyError("project topology context exceeds the prompt-size bound")
    context["relationship_count_included"] = len(context["relationships"])
    return context
