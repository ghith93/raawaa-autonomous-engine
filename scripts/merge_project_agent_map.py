"""Overlay the evidence-backed agent/component map onto a Graphify code graph.

This is a deterministic, offline transform. It does not call models or networks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


def _stable_id(prefix: str, value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:48] or "node"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}__{slug}__{digest}"


def merge_project_map(
    graph: dict[str, Any],
    project_map: dict[str, Any],
    *,
    map_path: str = "config/project-agent-map.json",
) -> dict[str, int]:
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise ValueError("Graphify graph must contain a nodes array")
    if not isinstance(graph.get("links"), list):
        raise ValueError("Graphify graph must contain a links array")
    if project_map.get("schema_version") != 1:
        raise ValueError("unsupported project-agent-map schema_version")

    map_nodes = project_map.get("nodes")
    map_edges = project_map.get("edges")
    if not isinstance(map_nodes, list) or not isinstance(map_edges, list):
        raise ValueError("project map nodes and edges must be arrays")

    # Make the transform idempotent: remove only the overlay created here.
    nodes = [n for n in graph["nodes"] if n.get("_origin") != "project_map"]
    links = [e for e in graph["links"] if e.get("_origin") != "project_map"]
    existing_ids = {n.get("id") for n in nodes}
    id_map: dict[str, str] = {}

    for index, item in enumerate(map_nodes):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError(f"invalid project map node at index {index}")
        source_id = item["id"]
        if source_id in id_map:
            raise ValueError(f"duplicate project map node id: {source_id}")
        node_id = _stable_id("project_map", source_id)
        if node_id in existing_ids:
            raise ValueError(f"project map node collides with Graphify node: {source_id}")
        id_map[source_id] = node_id
        nodes.append({
            "id": node_id,
            "label": item.get("label") or f"{item.get('kind', 'node')}: {source_id}",
            "file_type": "concept",
            "_origin": "project_map",
            "source_file": map_path,
            "source_location": f"nodes[{index}]",
            "metadata": {
                "map_node_id": source_id,
                "kind": item.get("kind"),
                "status": item.get("status"),
                "role": item.get("role"),
                "capabilities": item.get("capabilities", []),
                "evidence": item.get("evidence", []),
            },
        })
        existing_ids.add(node_id)

    # Resolve evidence paths to actual AST file nodes when available. For prose
    # excluded by --code-only, create an explicit path-reference node, not a
    # fabricated semantic summary.
    evidence_ids: dict[str, str] = {}
    evidence_links = 0
    for item in map_nodes:
        source_id = item["id"]
        for evidence_path in item.get("evidence", []):
            if not isinstance(evidence_path, str) or not evidence_path:
                raise ValueError(f"invalid evidence path on {source_id}")
            normalized = PurePosixPath(evidence_path.replace("\\", "/")).as_posix()
            matches = [
                n for n in nodes
                if n.get("_origin") == "ast"
                and n.get("file_type") == "code"
                and n.get("source_file") == normalized
                and not n.get("_callable")
            ]
            if matches:
                target = sorted(
                    matches,
                    key=lambda n: (0 if n.get("label") == PurePosixPath(normalized).name else 1, str(n.get("id"))),
                )[0]["id"]
                evidence_state = "indexed_code_node"
            else:
                target = evidence_ids.get(normalized, "")
                if not target:
                    target = _stable_id("project_evidence", normalized)
                    evidence_ids[normalized] = target
                    if target not in existing_ids:
                        nodes.append({
                            "id": target,
                            "label": normalized,
                            "file_type": "concept",
                            "_origin": "project_map",
                            "source_file": map_path,
                            "source_location": f"evidence:{normalized}",
                            "metadata": {
                                "evidence_path": normalized,
                                "status": "path_reference_only_not_semantically_indexed",
                            },
                        })
                        existing_ids.add(target)
                evidence_state = "path_reference_only"
            links.append({
                "source": id_map[source_id],
                "target": target,
                "relation": "evidenced_by",
                "_origin": "project_map",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "weight": 1.0,
                "evidence_state": evidence_state,
                "source_file": map_path,
                "source_location": f"evidence:{normalized}",
            })
            evidence_links += 1

    for index, edge in enumerate(map_edges):
        if not isinstance(edge, dict):
            raise ValueError(f"invalid project map edge at index {index}")
        source = id_map.get(edge.get("from"))
        target = id_map.get(edge.get("to"))
        if not source or not target:
            raise ValueError(f"project map edge {index} references an unknown node")
        links.append({
            "source": source,
            "target": target,
            "relation": edge["relation"],
            "_origin": "project_map",
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
            "weight": 1.0,
            "status": edge["status"],
            "source_file": map_path,
            "source_location": f"edges[{index}]",
        })

    graph["nodes"] = nodes
    graph["links"] = links
    metadata = graph.setdefault("graph", {})
    if not isinstance(metadata, dict):
        raise ValueError("Graphify graph metadata must be an object")
    metadata["project_agent_map_schema_version"] = project_map["schema_version"]
    metadata["project_agent_map_node_count"] = len(map_nodes)
    metadata["project_agent_map_edge_count"] = len(map_edges)
    return {
        "project_map_nodes": len(map_nodes),
        "project_map_edges": len(map_edges),
        "evidence_links": evidence_links,
        "evidence_reference_nodes": len(evidence_ids),
        "total_nodes": len(nodes),
        "total_links": len(links),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", default="graphify-out/graph.json")
    parser.add_argument("--map", default="config/project-agent-map.json")
    args = parser.parse_args()
    graph_path = Path(args.graph)
    map_path = Path(args.map)
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        project_map = json.loads(map_path.read_text(encoding="utf-8"))
        stats = merge_project_map(graph, project_map, map_path=map_path.as_posix())
        graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"project map graph merge failed: {type(exc).__name__}: {exc}")
    print("Project/agent map merged into Graphify graph: " + json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
