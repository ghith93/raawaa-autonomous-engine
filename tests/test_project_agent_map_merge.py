"""Tests for the deterministic project-map / Graphify graph overlay."""
from __future__ import annotations

import unittest

from scripts.merge_project_agent_map import merge_project_map


class ProjectAgentMapMergeTests(unittest.TestCase):
    def setUp(self):
        self.graph = {
            "directed": False,
            "multigraph": False,
            "graph": {"schema_version": 1, "graphify_version": "test"},
            "nodes": [
                {
                    "id": "src_task_store",
                    "label": "task_store.py",
                    "file_type": "code",
                    "_origin": "ast",
                    "source_file": "engine/task_store.py",
                }
            ],
            "links": [],
        }
        self.project_map = {
            "schema_version": 1,
            "nodes": [
                {
                    "id": "agent:engineer",
                    "kind": "agent_role",
                    "status": "declared_only",
                    "capabilities": ["inspect_code", "test"],
                    "evidence": ["config/agents.json"],
                },
                {
                    "id": "component:task_store",
                    "kind": "component",
                    "status": "implemented",
                    "evidence": ["engine/task_store.py", "docs/task-store.md"],
                },
            ],
            "edges": [
                {
                    "from": "agent:engineer",
                    "to": "component:task_store",
                    "relation": "uses",
                    "status": "role_intent_only",
                }
            ],
        }

    def test_merges_roles_edges_and_evidence(self):
        stats = merge_project_map(self.graph, self.project_map)
        self.assertEqual(stats["project_map_nodes"], 2)
        self.assertEqual(stats["project_map_edges"], 1)
        self.assertEqual(stats["evidence_links"], 3)
        self.assertEqual(stats["evidence_reference_nodes"], 2)
        ids = {n["id"] for n in self.graph["nodes"]}
        self.assertIn("src_task_store", ids)
        self.assertTrue(any(n.get("metadata", {}).get("map_node_id") == "agent:engineer" for n in self.graph["nodes"]))
        self.assertTrue(any(n.get("metadata", {}).get("evidence_path") == "docs/task-store.md" for n in self.graph["nodes"]))
        self.assertEqual(
            self.graph["graph"]["project_agent_map_node_count"],
            2,
        )
        self.assertTrue(all(e["source"] in ids and e["target"] in ids for e in self.graph["links"]))

    def test_is_idempotent(self):
        merge_project_map(self.graph, self.project_map)
        first_nodes = len(self.graph["nodes"])
        first_links = len(self.graph["links"])
        merge_project_map(self.graph, self.project_map)
        self.assertEqual(len(self.graph["nodes"]), first_nodes)
        self.assertEqual(len(self.graph["links"]), first_links)

    def test_rejects_unknown_edge_endpoint(self):
        invalid = dict(self.project_map)
        invalid["edges"] = [{
            "from": "agent:engineer",
            "to": "missing",
            "relation": "uses",
            "status": "bad",
        }]
        with self.assertRaises(ValueError):
            merge_project_map(self.graph, invalid)


if __name__ == "__main__":
    unittest.main()
