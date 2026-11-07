"""Validate the static project/agent topology map without network or model calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def validate(path: Path, root: Path) -> tuple[int, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported project map schema_version")
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("nodes and edges must be arrays")

    ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"node[{index}] must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip() or node_id in ids:
            raise ValueError(f"node[{index}] has a missing or duplicate id")
        ids.add(node_id)
        if not isinstance(node.get("kind"), str) or not isinstance(node.get("status"), str):
            raise ValueError(f"node {node_id} requires kind and status")
        evidence = node.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"node {node_id} must include evidence paths")
        for raw in evidence:
            if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
                raise ValueError(f"node {node_id} has an unsafe evidence path")
            if not (root / raw).is_file():
                raise ValueError(f"node {node_id} references missing evidence: {raw}")

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError(f"edge[{index}] must be an object")
        if edge.get("from") not in ids or edge.get("to") not in ids:
            raise ValueError(f"edge[{index}] references an unknown node")
        if not isinstance(edge.get("relation"), str) or not isinstance(edge.get("status"), str):
            raise ValueError(f"edge[{index}] requires relation and status")

    return len(nodes), len(edges)


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/project-agent-map.json")
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    try:
        nodes, edges = validate(path, root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"project-agent-map validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"project-agent-map valid: nodes={nodes}, edges={edges}; no network/model calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
