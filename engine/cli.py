"""CLI for policy audit, local task runtime, memory, and explicit read-only discovery."""
import argparse
import json
import os
from pathlib import Path

from engine.task_store import TaskStore
from engine.remote_task_store import RemoteTaskStore
from engine.task_invocation_store import TaskInvocationStore
from engine.memory import MemoryStore
from engine.model_gateway import ModelGateway, UsageLedger
from engine.worker import run_once
from engine.batch_runner import run_batch
from engine.free_provider_discovery import discover, discover_all, DiscoveryError
from engine.quota_config import load_provider_profile_registry
from engine.quota_manager import QuotaLedger, QuotaManager
from engine.live_runtime import build_live_gateway
from engine.research_discovery import discover_repositories
from engine.budget_policy import validate_budget_policy
from engine.tool_broker import validate_permission_matrix
from engine.artemis_gateway import probe_artemis
from engine.executive_intelligence import executive_status
from engine.brain_cycle import DEFAULT_SNAPSHOT_LIMIT, build_brain_cycle
from engine.development_cycle import build_development_cycle, render_markdown as render_development_markdown
from engine.profile_routing import build_route_plan, render_markdown as render_route_plan
from engine.orchestrator import validate_and_plan
from engine.workflow_dispatch import dispatch_body, render_markdown as render_dispatch_markdown
from engine.workflow_manifest import build_workflow_run_manifest
from engine.process_supervisor import (
    ProcessSupervisor,
    child_environment_names,
    SqliteDispatchLedger,
    SupervisorRefusal,
    default_registry,
)

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def db_path() -> str:
    return os.environ.get("RAAWAA_ENGINE_DB", str(ROOT / "data" / "engine.db"))


def store():
    if os.environ.get("RAAWAA_ENGINE_API_URL"):
        return RemoteTaskStore()
    return TaskStore(db_path())


def memory_store() -> MemoryStore:
    return MemoryStore(db_path())

def build_gateway_for_task_store(task_store):
    """Bind durable invocation checkpoints only to the authenticated remote D1 queue."""
    invocation_store = TaskInvocationStore(task_store) if isinstance(task_store, RemoteTaskStore) else None
    return build_live_gateway(
        db_path(), ROOT / "config" / "policy.json",
        ROOT / "config" / "provider_profiles.json",
        invocation_store=invocation_store,
    )


def audit() -> int:
    policy = load_json(ROOT / "config" / "policy.json")
    roster = load_json(ROOT / "config" / "agents.json")
    tool_permissions = load_json(ROOT / "config" / "tool_permissions.json")
    integrations = load_json(ROOT / "config" / "integrations.json")
    errors = []
    errors.extend(validate_permission_matrix(tool_permissions, roster))
    integration_map = integrations.get("integrations") if isinstance(integrations, dict) else None
    artemis = integration_map.get("google_artemis") if isinstance(integration_map, dict) else None
    security = artemis.get("security") if isinstance(artemis, dict) else None
    if (
        not isinstance(integrations, dict)
        or integrations.get("schema_version") != 1
        or not isinstance(artemis, dict)
    ):
        errors.append("Integration registry is missing schema_version 1 or the Google ARTEMIS entry.")
    elif artemis.get("enabled") is not False or artemis.get("execution_enabled") is not False:
        errors.append("Google ARTEMIS must remain disabled until its separate runtime activation gates are verified.")
    elif not isinstance(security, dict) or security.get("read_only_probe_only") is not True:
        errors.append("Google ARTEMIS must remain read-only in the current integration stage.")
    try:
        provider_profiles = load_provider_profile_registry(ROOT / "config" / "provider_profiles.json")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        provider_profiles = []
        errors.append(f"Provider profile registry failed closed: {type(exc).__name__}.")
    mode = policy.get("mode")
    if mode not in {"safe_dry_run", "live_inference_opt_in"}:
        errors.append("Policy mode must be safe_dry_run or live_inference_opt_in.")
    enabled_profiles = [entry for entry in provider_profiles if entry.enabled]
    if mode == "safe_dry_run" and enabled_profiles:
        errors.append("Provider routes must remain disabled in safe_dry_run mode.")
    if mode == "live_inference_opt_in":
        if not enabled_profiles:
            errors.append("live_inference_opt_in requires at least one explicitly enabled provider profile.")
        if any(not entry.ready_for_routing for entry in enabled_profiles):
            errors.append("Every enabled provider profile must pass static eligibility checks.")
    errors.extend(validate_budget_policy(policy))
    if policy.get("allow_external_writes") is not False:
        errors.append("External writes must remain disabled.")
    parallel_limit = policy.get("max_parallel_agents")
    if type(parallel_limit) is not int or not 1 <= parallel_limit <= 5:
        errors.append("max_parallel_agents must be an integer between 1 and 5.")
    ids = [agent.get("id") for agent in roster.get("agents", [])]
    if len(ids) != len(set(ids)):
        errors.append("Agent IDs must be unique.")
    if not ids:
        errors.append("At least one agent definition is required.")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    mode_label = "safe dry-run" if mode == "safe_dry_run" else "live opt-in configured; operator kill switch still required"
    tool_count = len(tool_permissions.get("tools", {}))
    print("Audit passed: " + str(len(ids)) + " agent roles; " + str(len(provider_profiles)) + " provider profiles; " + str(tool_count) + " deny-by-default tool definitions; " + mode_label + "; hybrid/free-first; model cap USD " + format(policy["monthly_budget_usd"], ".2f") + "/month; all-in hard cap USD " + format(policy["budget_envelope"]["monthly_total_hard_cap_usd"], ".2f") + "/month; paid fallback and infrastructure overages disabled; external writes disabled.")
    return 0



def quota_alerts(snapshot: list[dict], threshold: float = 0.8) -> list[dict]:
    """Return local threshold alerts for configured request/token windows."""
    alerts = []
    windows = (
        ("minute", "minute_requests_used", "minute_request_limit", "requests"),
        ("minute", "minute_tokens_used", "minute_token_limit", "tokens"),
        ("daily", "daily_requests_used", "daily_request_limit", "requests"),
        ("daily", "daily_tokens_used", "daily_token_limit", "tokens"),
        ("monthly", "monthly_requests_used", "monthly_request_limit", "requests"),
        ("monthly", "monthly_tokens_used", "monthly_token_limit", "tokens"),
    )
    for route in snapshot:
        for window, used_key, limit_key, unit in windows:
            used, limit = route.get(used_key), route.get(limit_key)
            if (type(used) is not int or type(limit) is not int or limit < 1
                    or used < 0):
                continue
            ratio = used / limit
            if ratio >= 1:
                level = "critical"
            elif ratio >= threshold:
                level = "warning"
            else:
                continue
            alerts.append({
                "route_id": route["route_id"],
                "window": window,
                "unit": unit,
                "used": used,
                "limit": limit,
                "level": level,
            })
    return alerts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Raawaa Autonomous Engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    executive = sub.add_parser("executive-status", help="Read-only executive portfolio snapshot and explainable task priorities")
    executive.add_argument("--limit", type=int, default=10)
    brain = sub.add_parser("brain-cycle", help="Run one deterministic, read-only executive brain and outcome-feedback cycle")
    brain.add_argument("--limit", type=int, default=10)
    dev = sub.add_parser(
        "dev-cycle",
        help="Rank the next development actions from collected state (read-only; no merge, deploy or spend)",
    )
    dev.add_argument("--state", help="Path to a state document; omit to rank an empty state")
    dev.add_argument("--limit", type=int, default=12)
    dev.add_argument("--format", choices=["json", "markdown"], default="json")
    workflow_plan = sub.add_parser("workflow-plan", help="Validate a delegated workflow DAG without enqueuing or executing it")
    workflow_plan.add_argument("--file", required=True, help="Path to a JSON workflow proposal")
    workflow_manifest = sub.add_parser(
        "workflow-manifest",
        help="Create a stable, non-dispatching run manifest from a validated workflow plan",
    )
    workflow_manifest.add_argument("--file", required=True, help="Path to a JSON workflow proposal")
    workflow_manifest.add_argument("--run-id", required=True, help="Stable caller-supplied run identifier")
    workflow_manifest.add_argument("--plan-version", type=int, default=1)
    workflow_dispatch = sub.add_parser(
        "workflow-dispatch-body",
        help="Build the /v1/workflows/dispatch request body for a manifest (prints it, sends nothing)",
    )
    workflow_dispatch.add_argument("--file", required=True, help="Path to a JSON workflow proposal")
    workflow_dispatch.add_argument("--run-id", required=True, help="Stable caller-supplied run identifier")
    workflow_dispatch.add_argument("--plan-version", type=int, default=1)
    workflow_dispatch.add_argument("--format", choices=("json", "markdown"), default="json")
    sub.add_parser("artemis-status", help="Read-only ARTEMIS daemon/device probe; never submits a mobile task")
    route = sub.add_parser(
        "route-plan",
        help="Explain which registered provider profile routing would choose, and why the rest are excluded",
    )
    route.add_argument("--task-kind", default="default")
    route.add_argument("--format", choices=["json", "markdown"], default="json")

    discovery = sub.add_parser(
        "free-models",
        help="Explicitly discover provider model catalogs (read-only; no inference)",
    )
    discovery.add_argument("--provider", choices=["gemini", "groq", "cerebras", "all"],
                           default="all")

    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--type", required=True, dest="task_type")
    enqueue.add_argument("--title", required=True)
    enqueue.add_argument("--payload", default="{}")
    enqueue.add_argument("--key")
    enqueue.add_argument("--max-attempts", type=int, default=2)
    enqueue.add_argument("--lane", choices=["general", "specialist:researcher", "specialist:reviewer", "specialist:marketer", "specialist:operator"], default="general")

    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--lane", choices=["general", "specialist:researcher", "specialist:reviewer", "specialist:marketer", "specialist:operator", "specialist:mobile_tester"], default="general")
    sub.add_parser("run-once")

    batch = sub.add_parser("run-batch", help="Run a bounded task batch with policy-limited concurrency")
    batch.add_argument("--max-tasks", type=int, default=5)
    batch.add_argument("--max-seconds", type=int, default=60)
    batch.add_argument("--max-parallel", type=int, default=None)

    log = sub.add_parser("audit-log")
    log.add_argument("--limit", type=int, default=100)

    add = sub.add_parser("memory-add")
    add.add_argument("--category", required=True)
    add.add_argument("--content", required=True)
    add.add_argument("--source", required=True)
    add.add_argument("--confidence", type=float, default=0.8)
    add.add_argument("--tags", default="")
    add.add_argument("--ttl-days", type=int)

    search = sub.add_parser("memory-search")
    search.add_argument("--query", required=True)
    search.add_argument("--category")
    search.add_argument("--limit", type=int, default=20)

    memory_listing = sub.add_parser("memory-list")
    memory_listing.add_argument("--limit", type=int, default=50)

    archive = sub.add_parser("memory-archive")
    archive.add_argument("--id", required=True)

    sub.add_parser("model-status")
    sub.add_parser(
        "monetary-status",
        help="Inspect spend budgets, actual usage, and unresolved reservations; no network calls",
    )
    sub.add_parser("provider-profile-status", help="Inspect provider profiles; never enables inference")
    sub.add_parser("quota-status", help="Inspect local provider quota usage and threshold alerts; no network calls")
    research = sub.add_parser("research-discover", help="Read-only discovery of public GitHub and academic research metadata")
    research.add_argument("--max-per-query", type=int, default=None)

    sub.add_parser(
        "supervisor-operations",
        help="List the allowlisted operations the process supervisor may start; starts nothing",
    )
    run_operation = sub.add_parser(
        "run-operation",
        help="Show, and only with --confirm run, one allowlisted supervised operation",
    )
    run_operation.add_argument("--operation", required=True)
    run_operation.add_argument("--task-id", required=True)
    run_operation.add_argument(
        "--parameter", action="append", default=[], metavar="NAME=VALUE",
        help="Value for one declared argument slot; repeat for each slot",
    )
    run_operation.add_argument("--wall-seconds", type=float, default=None)
    run_operation.add_argument(
        "--confirm", action="store_true",
        help="Actually start the process. Without it the resolved plan is printed and nothing runs.",
    )
    run_operation.add_argument(
        "--reconcile-stale-seconds", type=float, default=900.0,
        help="Quarantine interrupted dispatches older than this before running",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "audit":
        return audit()
    elif args.command == "executive-status":
        print(json.dumps(executive_status(store(), limit=args.limit), indent=2, ensure_ascii=False))
    elif args.command == "brain-cycle":
        # DEFAULT_SNAPSHOT_LIMIT is 100: the remote D1 adapter clamps list_tasks
        # at 100 and rejects responses over 64 KB, so asking for 200 would
        # silently return 100 in production.
        print(json.dumps(build_brain_cycle(store().list_tasks(limit=DEFAULT_SNAPSHOT_LIMIT), limit=args.limit), indent=2, ensure_ascii=False))
    elif args.command == "dev-cycle":
        state = load_json(Path(args.state)) if args.state else {}
        report = build_development_cycle(state, limit=args.limit)
        if args.format == "markdown":
            print(render_development_markdown(report), end="")
        else:
            print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.command in {"workflow-plan", "workflow-manifest", "workflow-dispatch-body"}:
        proposal = load_json(Path(args.file))
        roster = load_json(ROOT / "config" / "agents.json")
        policy = load_json(ROOT / "config" / "policy.json")
        plan = validate_and_plan(proposal, roster, policy).as_dict()
        if args.command == "workflow-manifest":
            plan = build_workflow_run_manifest(plan, args.run_id, args.plan_version).as_dict()
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        elif args.command == "workflow-dispatch-body":
            manifest = build_workflow_run_manifest(plan, args.run_id, args.plan_version)
            body = dispatch_body(manifest)
            if args.format == "markdown":
                print(render_dispatch_markdown(body), end="")
            else:
                print(json.dumps(body, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
    elif args.command == "artemis-status":
        print(json.dumps(probe_artemis(os.environ), indent=2, ensure_ascii=False))
    elif args.command == "route-plan":
        plan = build_route_plan(task_kind=args.task_kind)
        if args.format == "markdown":
            print(render_route_plan(plan), end="")
        else:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
    elif args.command == "free-models":
        if args.provider == "all":
            result = discover_all()
        else:
            try:
                result = {
                    "discovery_only": True,
                    "generation_requests": 0,
                    "models": {args.provider: discover(args.provider)},
                    "unavailable": {},
                }
            except DiscoveryError as exc:
                result = {
                    "discovery_only": True,
                    "generation_requests": 0,
                    "models": {},
                    "unavailable": {args.provider: str(exc)},
                }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "enqueue":
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            raise ValueError("--payload must decode to a JSON object")
        task = store().enqueue(args.task_type, args.title, payload, args.key, args.max_attempts,
                                 execution_lane=args.lane)
        print(json.dumps(task, indent=2, ensure_ascii=False))
    elif args.command == "list":
        print(json.dumps(store().list_tasks(args.limit, worker_lane=args.lane), indent=2, ensure_ascii=False))
    elif args.command == "run-once":
        task_store = store()
        gateway = build_gateway_for_task_store(task_store)
        print(json.dumps(run_once(task_store, live_gateway=gateway, environ=os.environ),
                         indent=2, ensure_ascii=False))
    elif args.command == "run-batch":
        task_store = store()
        gateway = build_gateway_for_task_store(task_store)
        runner = lambda current_store: run_once(
            current_store, live_gateway=gateway, environ=os.environ
        )
        execution_policy = load_json(ROOT / "config" / "policy.json")
        policy_parallel_limit = execution_policy.get("max_parallel_agents")
        if type(policy_parallel_limit) is not int or not 1 <= policy_parallel_limit <= 5:
            raise ValueError("max_parallel_agents policy must be an integer between 1 and 5")
        max_parallel = policy_parallel_limit if args.max_parallel is None else args.max_parallel
        if type(max_parallel) is not int or not 1 <= max_parallel <= policy_parallel_limit:
            raise ValueError("max_parallel must be between 1 and the configured max_parallel_agents policy")
        print(json.dumps(run_batch(
            store(), args.max_tasks, args.max_seconds,
            runner=runner, max_parallel=max_parallel,
        ), indent=2, ensure_ascii=False))
    elif args.command == "audit-log":
        print(json.dumps(store().audit_events(args.limit), indent=2, ensure_ascii=False))
    elif args.command == "memory-add":
        item = memory_store().add(
            args.category, args.content, args.source, args.confidence,
            [tag.strip() for tag in args.tags.split(",") if tag.strip()], args.ttl_days
        )
        print(json.dumps(item, indent=2, ensure_ascii=False))
    elif args.command == "memory-search":
        print(json.dumps(memory_store().search(args.query, args.category, args.limit),
                         indent=2, ensure_ascii=False))
    elif args.command == "memory-list":
        print(json.dumps(memory_store().list_recent(args.limit), indent=2, ensure_ascii=False))
    elif args.command == "memory-archive":
        print(json.dumps({"archived": memory_store().archive(args.id)}))
    elif args.command == "quota-status":
        entries = load_provider_profile_registry(ROOT / "config" / "provider_profiles.json")
        manager = QuotaManager([entry.profile for entry in entries], QuotaLedger(db_path()))
        snapshot = manager.snapshot()
        print(json.dumps({
            "network_calls_performed": 0,
            "route_count": len(snapshot),
            "eligible_routes": sum(bool(route["eligible"]) for route in snapshot),
            "alerts": quota_alerts(snapshot),
            "routes": snapshot,
        }, indent=2))
    elif args.command == "research-discover":
        research_config = load_json(ROOT / "config" / "research_sources.json")
        result = discover_repositories(
            queries=research_config["queries"],
            max_per_query=(research_config["max_results_per_query"] if args.max_per_query is None else args.max_per_query),
            max_queries=research_config["max_queries_per_run"],
            academic_query=research_config.get("academic_query"),
            max_academic_results=research_config.get("max_academic_results", 10),
            arxiv_query=research_config.get("arxiv_query"),
            max_arxiv_results=research_config.get("max_arxiv_results", 10),
            searxng_query=research_config.get("searxng_query"),
            searxng_base_url=os.environ.get("RAAWAA_SEARXNG_BASE_URL"),
            max_searxng_results=research_config.get("max_searxng_results", 10),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "provider-profile-status":
        entries = load_provider_profile_registry(ROOT / "config" / "provider_profiles.json")
        result = [{
            "profile_id": entry.profile_id,
            "provider": entry.profile.provider,
            "model": entry.profile.model,
            "account_tier": entry.account_tier,
            "model_stage": entry.model_stage,
            "model_terms_source_url": entry.model_terms_source_url,
            "priority": entry.profile.priority,
            "production_use_allowed": entry.production_use_allowed,
            "enabled": entry.enabled,
            "ready_for_routing": entry.ready_for_routing,
            "api_key_env_configured_by_name_only": entry.api_key_env,
            "minute_request_limit": entry.profile.minute_request_limit,
            "minute_token_limit": entry.profile.minute_token_limit,
            "daily_request_limit": entry.profile.daily_request_limit,
            "daily_token_limit": entry.profile.daily_token_limit,
            "commercial_use_allowed": entry.profile.commercial_use_allowed,
            "pricing_verified_zero": (
                entry.profile.input_cost_per_million_usd == 0.0
                and entry.profile.output_cost_per_million_usd == 0.0
            ),
            "verification_note": entry.verification_note,
        } for entry in entries]
        print(json.dumps({"network_calls_performed": 0, "runtime_activation": "separate_explicit_gates", "profiles": result}, indent=2))
    elif args.command == "monetary-status":
        policy = load_json(ROOT / "config" / "policy.json")
        ledger = UsageLedger(db_path())
        daily_budget = policy.get("daily_budget_usd", 0)
        monthly_budget = policy.get("monthly_budget_usd", 0)
        per_task_budget = policy.get("per_task_budget_usd", 0)
        print(json.dumps({
            "network_calls_performed": 0,
            "paid_calls_enabled": policy.get("allow_paid_model_calls", False),
            "budgets": {
                "daily_usd": daily_budget,
                "monthly_usd": monthly_budget,
                "per_task_usd": per_task_budget,
            },
            "budget_status": ledger.budget_status(daily_budget, monthly_budget),
            "usage": ledger.totals(),
            "per_task_usage": ledger.task_summaries(per_task_budget),
            "per_task_attribution_note": (
                "Legacy usage rows without task_id remain included in global totals but "
                "cannot be attributed to an individual task."
            ),
            "pending_reservations": ledger.pending_reservations(),
            "reconciliation_note": (
                "Ambiguous reservations are intentionally retained. Do not release one "
                "without provider-side evidence that the request was not billed."
            ),
        }, indent=2, ensure_ascii=False))
    elif args.command == "supervisor-operations":
        print(json.dumps(default_registry(ROOT).describe(), indent=2, ensure_ascii=False))
    elif args.command == "run-operation":
        parameters = {}
        for item in args.parameter:
            name, separator, value = item.partition("=")
            if not separator or not name.strip():
                raise ValueError("--parameter must be NAME=VALUE")
            parameters[name.strip()] = value
        registry = default_registry(ROOT)
        try:
            spec = registry.get(args.operation)
            argv = spec.build_argv(parameters)
        except SupervisorRefusal as exc:
            print(json.dumps({"status": "refused", "reason": str(exc)}, indent=2))
            return 1
        if not args.confirm:
            # The dry run is the inspectable half: the exact vector, the exact
            # limits, and the names of the variables the child will see. Values
            # are never printed, because one of them could be a credential.
            print(json.dumps({
                "status": "dry_run",
                "note": "Nothing was started. Pass --confirm to run this operation.",
                "operation": spec.name,
                "argv": list(argv),
                "wall_seconds": args.wall_seconds or spec.wall_seconds,
                "output_bytes": spec.output_bytes,
                "working_directory": spec.working_directory or "<disposable temporary directory>",
                "child_environment_variables": list(child_environment_names()),
            }, indent=2, ensure_ascii=False))
            return 0
        supervisor = ProcessSupervisor(registry, SqliteDispatchLedger(db_path()))
        reconciled = supervisor.reconcile(stale_after_seconds=args.reconcile_stale_seconds)
        outcome = supervisor.run_operation(
            args.operation, parameters, task_id=args.task_id,
            wall_seconds=args.wall_seconds,
        )
        payload = outcome.as_dict()
        payload["reconciled_dispatches"] = reconciled
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if outcome.classification == "succeeded" else 1
    elif args.command == "model-status":
        policy = load_json(ROOT / "config" / "policy.json")
        gateway = ModelGateway(
            [], {}, UsageLedger(db_path()),
            daily_budget_usd=policy.get("daily_budget_usd", 0),
            monthly_budget_usd=policy.get("monthly_budget_usd", 0),
            allow_paid_calls=policy.get("allow_paid_model_calls", False),
            per_task_budget_usd=policy.get("per_task_budget_usd", 0),
            network_calls_enabled=False,
        )
        print(json.dumps(gateway.status(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
