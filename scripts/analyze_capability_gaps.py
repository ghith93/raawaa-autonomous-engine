"""Deterministic capability-gap analysis over reviewed repository state and triage metadata.

This is an offline analysis step. It never calls models, performs network I/O,
creates tasks, adopts candidates, or executes discovered content.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOPIC_TO_CAPABILITY = {
    "agent_frameworks": "agent_orchestration",
    "coding_agents": "coding_execution",
    "agent_memory": "memory",
    "mcp_ecosystem": "tool_gateway",
    "model_routing": "model_routing",
    "durable_execution": "durable_execution",
    "agent_skills_security": "skill_security",
    "agent_evaluation": "evaluation",
    "skill_benchmarks": "evaluation",
}

CAPABILITY_DEFINITIONS = {
    "agent_orchestration": {
        "risk": "high",
        "cost": "high",
        "complexity": "high",
        "expected_benefit": "Reduce coordination overhead after a measurable task-orchestration gap is proven.",
        "title": "Model-driven agent orchestration",
        "impact": "high",
        "next_step": "Compare orchestrator options against the deterministic task contracts; do not add a framework until a measurable runtime gap is demonstrated.",
    },
    "coding_execution": {
        "risk": "high",
        "cost": "high",
        "complexity": "high",
        "expected_benefit": "Enable bounded code changes with isolated workspaces and review-only delivery.",
        "title": "Isolated coding-agent execution",
        "impact": "high",
        "next_step": "Define a secret-free, bounded coding workspace and review/PR-only execution contract before enabling a coding agent.",
    },
    "memory": {
        "risk": "medium",
        "cost": "medium",
        "complexity": "medium",
        "expected_benefit": "Preserve provenance-aware findings and decisions across scheduled research runs.",
        "title": "Durable cross-run research memory",
        "impact": "high",
        "next_step": "Verify the repository persistence setting and D1 readback path; do not enable external persistence without the required operational approval.",
    },
    "tool_gateway": {
        "risk": "high",
        "cost": "low",
        "complexity": "medium",
        "expected_benefit": "Expose only narrowly scoped tools through trusted adapters and durable audit.",
        "title": "Scoped tool gateway and adapters",
        "impact": "high",
        "next_step": "Complete independent security review, then add only read-only repository adapters with validated paths and durable audit storage.",
    },
    "model_routing": {
        "risk": "high",
        "cost": "low",
        "complexity": "medium",
        "expected_benefit": "Improve resilience and quality while respecting provider quotas and spending limits.",
        "title": "Verified model routing",
        "impact": "high",
        "next_step": "Keep inference disabled; validate provider hard limits, data policy, quota reservation and kill switch before any model call.",
    },
    "durable_execution": {
        "risk": "medium",
        "cost": "low",
        "complexity": "medium",
        "expected_benefit": "Improve resumability and observability without introducing an always-on process.",
        "title": "Resumable autonomous execution",
        "impact": "high",
        "next_step": "Improve checkpoint/resume and observability within the existing D1 task ledger and bounded Actions runner; do not add a second scheduler.",
    },
    "skill_security": {
        "risk": "medium",
        "cost": "low",
        "complexity": "medium",
        "expected_benefit": "Reduce supply-chain risk before any skill can be imported or executed.",
        "title": "Skill and tool supply-chain security",
        "impact": "high",
        "next_step": "Evaluate a static scanner in an ephemeral secret-free job; separately design sandboxing, provenance, signing and revocation.",
    },
    "evaluation": {
        "risk": "low",
        "cost": "low",
        "complexity": "low",
        "expected_benefit": "Measure regressions and utility with versioned synthetic and representative test corpora.",
        "title": "Agent and skill evaluation harness",
        "impact": "medium",
        "next_step": "Create a small versioned synthetic benchmark with fixed expected outcomes, cost limits and regression thresholds.",
    },
    "research_discovery": {
        "risk": "low",
        "cost": "low",
        "complexity": "low",
        "expected_benefit": "Increase discovery coverage while preserving provenance, freshness and rate limits.",
        "title": "Broader, source-verified research discovery",
        "impact": "medium",
        "next_step": "Add one source family at a time (official documentation/releases, security advisories, model hubs or additional academic indexes), with rate limits, freshness and provenance tests.",
    },
}

def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read repository configuration {path.name}: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise ValueError(f"repository configuration {path.name} must be an object")
    return value


def _exists(root: Path, relative: str) -> bool:
    return (root / relative).is_file()


def _candidate_summary(item: dict[str, Any]) -> dict[str, Any] | None:
    title = item.get("title")
    url = item.get("url")
    source_key = item.get("source_key")
    if not all(isinstance(value, str) and value.strip() for value in (title, url, source_key)):
        return None
    score = item.get("score")
    if type(score) is not int:
        score = None
    return {
        "source_key": source_key[:240],
        "title": title[:240],
        "url": url[:1000],
        "triage_score": score,
        "triage_priority": item.get("priority") if isinstance(item.get("priority"), str) and item.get("priority") in {"high", "medium", "low"} else "unknown",
        "license_review_required": item.get("license_review_required") is True,
        "security_review_required": item.get("security_review_required") is True,
    }


def _baseline(root: Path) -> dict[str, dict[str, Any]]:
    agents = _read_json(root / "config" / "agents.json")
    providers = _read_json(root / "config" / "provider_profiles.json")
    research = _read_json(root / "config" / "research_sources.json")
    tool_matrix = _read_json(root / "config" / "tool_permissions.json")
    workflow_path = root / ".github" / "workflows" / "research-discovery.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8") if workflow_path.is_file() else ""

    routes = providers.get("routes", [])
    if not isinstance(routes, list):
        routes = []
    enabled_routes = sum(1 for route in routes if isinstance(route, dict) and route.get("enabled") is True)

    tools = tool_matrix.get("tools", {})
    if not isinstance(tools, dict):
        tools = {}
    broker_exists = _exists(root, "engine/tool_broker.py") and bool(tools)
    worker_files = _exists(root, "engine/agent_runtime.py") and _exists(root, "engine/task_store.py")
    bounded_remote_runner = (
        _exists(root, "engine/remote_task_store.py")
        and _exists(root, ".github/workflows/task-runner.yml")
    )
    agent_note = str(agents.get("note", "")).lower()

    query_map = research.get("queries", {})
    query_names = sorted(name for name in query_map if isinstance(name, str)) if isinstance(query_map, dict) else []
    academic_source = research.get("academic_source") if isinstance(research.get("academic_source"), str) else None
    arxiv_source = research.get("arxiv_source") if isinstance(research.get("arxiv_source"), str) else None
    searxng_source = research.get("searxng_source") if isinstance(research.get("searxng_source"), str) else None
    persistence_is_optional = "RAAWAA_ENGINE_RESEARCH_PERSISTENCE" in workflow_text and "== 'enabled'" in workflow_text

    return {
        "agent_orchestration": {
            "status": "declarative_only" if "declarative only" in agent_note else ("task_contracts_present" if worker_files else "not_verified"),
            "evidence_paths": ["config/agents.json", "engine/agent_runtime.py"],
            "evidence": "Agent roster is declarative; deterministic task contracts exist, but this does not prove model-driven orchestration.",
        },
        "coding_execution": {
            "status": "not_integrated" if not _exists(root, "engine/coding_agent.py") else "implementation_present_unverified",
            "evidence_paths": ["config/agents.json", "engine/coding_agent.py"],
            "evidence": "An engineering role is not evidence of a coding agent runtime or an isolated code-execution environment.",
        },
        "memory": {
            "status": "local_memory_with_optional_research_persistence" if _exists(root, "engine/memory.py") and persistence_is_optional else ("local_memory_only" if _exists(root, "engine/memory.py") else "not_implemented"),
            "evidence_paths": ["engine/memory.py", "scripts/persist_research_candidates.py", ".github/workflows/research-discovery.yml"],
            "evidence": "Local provenance-aware SQLite memory exists; research persistence is gated by a repository variable and its current value is not inferred here.",
        },
        "tool_gateway": {
            "status": "broker_defined_not_integrated" if broker_exists else ("broker_code_without_matrix" if _exists(root, "engine/tool_broker.py") else "not_implemented"),
            "evidence_paths": ["engine/tool_broker.py", "config/tool_permissions.json", "docs/tool-broker-permissions.md"],
            "evidence": "A permission matrix and broker implementation do not prove that runtime adapters are registered or exposed to agents.",
        },
        "model_routing": {
            "status": "profiles_staged_disabled" if routes and enabled_routes == 0 else ("some_routes_enabled" if enabled_routes else "not_configured"),
            "evidence_paths": ["config/provider_profiles.json", "config/policy.json"],
            "evidence": f"{len(routes)} provider profiles found; {enabled_routes} enabled. This is configuration evidence, not proof of provider availability.",
        },
        "durable_execution": {
            "status": "bounded_scheduled_runner" if bounded_remote_runner else ("local_task_store_only" if _exists(root, "engine/task_store.py") else "not_verified"),
            "evidence_paths": ["engine/task_store.py", "engine/remote_task_store.py", ".github/workflows/task-runner.yml"],
            "evidence": "The remote task store plus scheduled runner indicate bounded, resumable batch execution; they do not constitute an always-on process or guarantee 24/7 execution.",
        },
        "skill_security": {
            "status": "static_preflight_implemented_not_verified" if _exists(root, "engine/skill_static_gate.py") else ("scanner_candidate_only" if _exists(root, "docs/research/skillspector-evaluation-2026-11-03.ar.md") else "not_implemented"),
            "evidence_paths": ["engine/skill_static_gate.py", "scripts/scan_skill_bundle.py", "tests/test_skill_static_gate.py", "docs/research/skillspector-evaluation-2026-11-03.ar.md"],
            "evidence": "A deterministic manifest/static-pattern preflight exists, but it is not a sandbox and does not verify dependencies, signatures, runtime behavior, independent review or revocation.",
        },
        "evaluation": {
            "status": "unit_tests_no_agent_benchmark" if _exists(root, "tests") and not (root / "benchmarks").exists() else ("benchmark_directory_present_unverified" if (root / "benchmarks").exists() else "not_verified"),
            "evidence_paths": ["tests", "benchmarks"],
            "evidence": "Unit tests validate code behavior; they do not by themselves measure agent task quality or skill utility.",
        },
        "research_discovery": {
            "status": "github_crossref_arxiv_searxng_optional" if query_names and academic_source and arxiv_source and searxng_source else ("github_crossref_arxiv_metadata" if query_names and academic_source and arxiv_source else ("github_plus_academic_metadata" if query_names and academic_source else ("github_metadata_only" if query_names else "not_configured"))),
            "evidence_paths": ["config/research_sources.json", "engine/research_discovery.py"],
            "evidence": "Configured discovery includes GitHub topics, Crossref and arXiv metadata, and an optional SearXNG adapter; configuration does not prove endpoint availability or successful retrieval in the latest run.",
            "configured_topics": query_names,
            "academic_source": academic_source,
            "arxiv_source": arxiv_source,
            "searxng_source": searxng_source,
            "searxng_runtime_endpoint_verified": False,
        },
    }



IMPACT_POINTS = {"high": 3, "medium": 2, "low": 1}
RISK_POINTS = {"low": 1, "medium": 2, "high": 3}
COST_POINTS = {"zero": 0, "low": 1, "medium": 2, "high": 3}
COMPLEXITY_POINTS = {"low": 1, "medium": 2, "high": 3}


def _score_opportunity(definition: dict[str, Any]) -> dict[str, Any]:
    impact = definition.get("impact", "low")
    risk = definition.get("risk", "high")
    cost = definition.get("cost", "high")
    complexity = definition.get("complexity", "high")
    score = max(
        0,
        IMPACT_POINTS.get(impact, 1) * 3
        - RISK_POINTS.get(risk, 3)
        - COST_POINTS.get(cost, 3)
        - COMPLEXITY_POINTS.get(complexity, 3),
    )
    band = "high" if score >= 6 else "medium" if score >= 3 else "low"
    return {
        "impact_band": impact,
        "risk_band": risk,
        "cost_band": cost,
        "complexity_band": complexity,
        "priority_score": score,
        "priority_band": band,
        "scoring_method": "max(0, 3*impact_points - risk_points - cost_points - complexity_points); qualitative heuristic, not measured ROI",
    }


def _proposal_for(opportunity: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_key": "capability_proposal:" + opportunity["capability"],
        "task_type": "capability_proposal",
        "status": "PROPOSED",
        "title": "Review capability: " + opportunity["title"],
        "capability": opportunity["capability"],
        "problem_statement": opportunity["baseline_evidence"],
        "evidence_paths": opportunity["evidence_paths"],
        "candidate_evidence": opportunity["candidate_shortlist"],
        "expected_benefit": opportunity["expected_benefit"],
        "proposed_action": opportunity["next_step"],
        "impact_band": opportunity["impact_band"],
        "risk_band": opportunity["risk_band"],
        "cost_band": opportunity["cost_band"],
        "complexity_band": opportunity["complexity_band"],
        "priority_score": opportunity["priority_score"],
        "priority_band": opportunity["priority_band"],
        "acceptance_criteria": [
            "Verify the primary source and record provenance, freshness and applicable license.",
            "Measure the current repository/runtime baseline before changing behavior.",
            "Keep the change on a feature branch and preserve a documented rollback path.",
            "Pass relevant unit, integration, security and cost-boundary tests.",
            "Obtain independent security/architecture review before enabling runtime behavior.",
            "Record a measured outcome and compare it with the baseline.",
        ],
        "rollback_plan": "Revert the feature branch or pull request. Do not mutate production resources; any future database or deployment change requires a separate reviewed migration and rollback plan.",
        "independent_review_required": True,
        "owner_approval_required": True,
        "automatic_task_creation": False,
        "task_store_write": False,
        "automatic_execution": False,
        "automatic_adoption": False,
        "approval_state": "not_requested",
    }


def analyze_capability_gaps(triage: dict[str, Any], repo_root: str | Path = ".") -> dict[str, Any]:
    if not isinstance(triage, dict) or triage.get("schema_version") != 1:
        raise ValueError("triage input must use schema_version 1")
    candidates = triage.get("github_candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("triage github_candidates must be a list")

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in CAPABILITY_DEFINITIONS}
    unmapped = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        topics = candidate.get("discovered_via", [])
        if not isinstance(topics, list):
            unmapped += 1
            continue
        mapped = {TOPIC_TO_CAPABILITY[topic] for topic in topics if isinstance(topic, str) and topic in TOPIC_TO_CAPABILITY}
        if not mapped:
            unmapped += 1
            continue
        summary = _candidate_summary(candidate)
        if summary is None:
            unmapped += 1
            continue
        for capability in mapped:
            grouped[capability].append(summary)

    root = Path(repo_root)
    baseline = _baseline(root)
    opportunities = []
    for capability, definition in CAPABILITY_DEFINITIONS.items():
        current = baseline.get(capability, {})
        related = sorted(
            grouped[capability],
            key=lambda item: (
                {"high": 3, "medium": 2, "low": 1}.get(item["triage_priority"], 0),
                item["triage_score"] if item["triage_score"] is not None else -1,
                item["title"].lower(),
            ),
            reverse=True,
        )
        opportunity = {
            "capability": capability,
            "title": definition["title"],
            "current_status": current.get("status", "not_verified"),
            **_score_opportunity(definition),
            "candidate_count": len(related),
            "candidate_shortlist": related[:3],
            "baseline_evidence": current.get("evidence", ""),
            "evidence_paths": current.get("evidence_paths", []),
            "expected_benefit": definition["expected_benefit"],
            "next_step": definition["next_step"],
            "decision_status": "PROPOSED",
            "automatic_task_creation": False,
            "automatic_adoption": False,
        }
        opportunities.append(opportunity)

    priority_order = {"high": 3, "medium": 2, "low": 1}
    opportunities.sort(key=lambda item: (
        item["priority_score"],
        priority_order.get(item["priority_band"], 0),
        item["candidate_count"],
        item["capability"],
    ), reverse=True)
    task_proposals = [_proposal_for(item) for item in opportunities]
    return {
        "schema_version": 1,
        "analysis_only": True,
        "model_calls": 0,
        "network_calls": 0,
        "code_execution": False,
        "task_creation": False,
        "automatic_adoption": False,
        "source_triage_counts": triage.get("source_counts", {}),
        "repository_baseline": baseline,
        "opportunities": opportunities,
        "task_proposals": task_proposals,
        "task_proposal_count": len(task_proposals),
        "task_creation": False,
        "unmapped_github_candidate_count": unmapped,
        "limitations": [
            "Candidate relevance is inferred only from the configured discovery topic; it is not a quality or security assessment.",
            "The analyzer reads repository files only and cannot inspect GitHub Actions variable values, D1 contents, account quotas, or deployed runtime state.",
            "Every opportunity remains PROPOSED until primary-source verification, baseline measurement, and independent review.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    input_path = Path(args[0]) if args else Path("research-triage.json")
    root = Path(args[1]) if len(args) > 1 else Path(".")
    try:
        triage = json.loads(input_path.read_text(encoding="utf-8"))
        result = analyze_capability_gaps(triage, root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Capability gap analysis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
