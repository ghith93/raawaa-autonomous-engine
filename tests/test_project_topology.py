import unittest

from engine.project_topology import (
    ProjectTopologyError,
    build_agent_topology_context,
    load_project_topology,
)


def sample_map(edges=None, nodes=None, schema_version=1):
    return {
        "schema_version": schema_version,
        "map_id": "synthetic-project-map",
        "scope_note": "Static architecture inventory only; not a live process registry.",
        "nodes": nodes or [
            {"id": "agent:researcher", "kind": "agent", "status": "declared_only",
             "role": "researcher", "capabilities": ["research"], "evidence": ["config/agents.json"]},
            {"id": "component:alpha", "kind": "component", "status": "implemented",
             "evidence": ["engine/alpha.py"]},
            {"id": "component:beta", "kind": "component", "status": "pilot_only",
             "evidence": ["docs/beta.md"]},
        ],
        "edges": edges if edges is not None else [
            {"from": "agent:researcher", "to": "component:beta",
             "relation": "uses", "status": "role_intent_only"},
            {"from": "agent:researcher", "to": "component:alpha",
             "relation": "uses", "status": "implemented"},
        ],
    }


class ProjectTopologyTests(unittest.TestCase):
    def test_persisted_project_map_produces_bounded_advisory_context(self):
        context = build_agent_topology_context("researcher", load_project_topology())
        self.assertEqual(context["status"], "available")
        self.assertEqual(context["mode"], "read_only_advisory")
        self.assertEqual(context["agent_node"]["id"], "agent:researcher")
        self.assertRegex(context["map_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("does not prove a live agent process", context["authority_note"])
        self.assertLessEqual(len(context["relationships"]), 20)

    def test_relationships_are_sorted_deterministically(self):
        context = build_agent_topology_context("researcher", sample_map())
        self.assertEqual(
            [item["node"]["id"] for item in context["relationships"]],
            ["component:alpha", "component:beta"],
        )

    def test_missing_agent_node_returns_unavailable_context(self):
        value = sample_map(nodes=[
            {"id": "agent:reviewer", "kind": "agent", "status": "declared_only"},
        ], edges=[])
        context = build_agent_topology_context("researcher", value)
        self.assertEqual(context["status"], "unavailable")
        self.assertEqual(context["reason"], "agent_node_missing")

    def test_duplicate_nodes_and_unknown_edge_endpoints_are_rejected(self):
        duplicate_nodes = sample_map(nodes=[
            {"id": "agent:researcher", "kind": "agent", "status": "declared_only"},
            {"id": "agent:researcher", "kind": "agent", "status": "declared_only"},
        ], edges=[])
        with self.assertRaises(ProjectTopologyError):
            build_agent_topology_context("researcher", duplicate_nodes)

        invalid_edge = sample_map(edges=[
            {"from": "agent:researcher", "to": "component:missing",
             "relation": "uses", "status": "role_intent_only"},
        ])
        with self.assertRaises(ProjectTopologyError):
            build_agent_topology_context("researcher", invalid_edge)

    def test_boolean_or_unknown_schema_version_is_rejected(self):
        for version in (True, 2, "1"):
            with self.subTest(version=version):
                with self.assertRaises(ProjectTopologyError):
                    build_agent_topology_context("researcher", sample_map(schema_version=version))


if __name__ == "__main__":
    unittest.main()
