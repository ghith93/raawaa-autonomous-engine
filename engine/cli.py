"""CLI for policy audit, local task runtime, memory, and explicit read-only discovery."""
import argparse
import json
import os
from pathlib import Path

from engine.task_store import TaskStore
from engine.remote_task_store import RemoteTaskStore
from engine.memory import MemoryStore
from engine.model_gateway import ModelGateway, UsageLedger
from engine.worker import run_once
from engine.batch_runner import run_batch
from engine.free_provider_discovery import discover, discover_all, DiscoveryError
from engine.quota_config import load_provider_profile_registry
from engine.quota_manager import QuotaLedger, QuotaManager
from engine.live_runtime import build_live_gateway
from engine.research_discovery import discover_repositories

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


def audit() -> int:
    policy = load_json(ROOT / "config" / "policy.json")
    roster = load_json(ROOT / "config" / "agents.json")
    errors = []
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
    if policy.get("daily_budget_usd", 0) != 0 or policy.get("monthly_budget_usd", 0) != 0:
        errors.append("Initial release must have zero configured spend budgets.")
    if policy.get("allow_paid_model_calls") is not False:
        errors.append("Paid model calls must remain disabled.")
    if policy.get("allow_external_writes") is not False:
        errors.append("External writes must remain disabled.")
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
    print(f"Audit passed: {len(ids)} agent roles; {len(provider_profiles)} provider profiles; {mode_label}; zero spend; external writes disabled.")
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

    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=50)
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
    research = sub.add_parser("research-discover", help="Read-only discovery of public GitHub research candidates")
    research.add_argument("--max-per-query", type=int, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "audit":
        return audit()
    if args.command == "free-models":
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
        task = store().enqueue(args.task_type, args.title, payload, args.key, args.max_attempts)
        print(json.dumps(task, indent=2, ensure_ascii=False))
    elif args.command == "list":
        print(json.dumps(store().list_tasks(args.limit), indent=2, ensure_ascii=False))
    elif args.command == "run-once":
        gateway = build_live_gateway(
            db_path(), ROOT / "config" / "policy.json",
            ROOT / "config" / "provider_profiles.json",
        )
        print(json.dumps(run_once(store(), live_gateway=gateway, environ=os.environ),
                         indent=2, ensure_ascii=False))
    elif args.command == "run-batch":
        gateway = build_live_gateway(
            db_path(), ROOT / "config" / "policy.json",
            ROOT / "config" / "provider_profiles.json",
        )
        runner = lambda current_store: run_once(
            current_store, live_gateway=gateway, environ=os.environ
        )
        max_parallel = args.max_parallel
        if max_parallel is None:
            execution_policy = load_json(ROOT / "config" / "policy.json")
            max_parallel = execution_policy.get("max_parallel_agents", 1)
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
