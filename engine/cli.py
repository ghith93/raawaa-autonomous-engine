"""CLI for policy audit and the bounded local task queue."""
import argparse
import json
import os
from pathlib import Path

from engine.task_store import TaskStore
from engine.memory import MemoryStore
from engine.worker import run_once

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def db_path() -> str:
    return os.environ.get("RAAWAA_ENGINE_DB", str(ROOT / "data" / "engine.db"))


def store() -> TaskStore:
    return TaskStore(db_path())


def memory_store() -> MemoryStore:
    return MemoryStore(db_path())


def audit() -> int:
    policy = load_json(ROOT / "config" / "policy.json")
    roster = load_json(ROOT / "config" / "agents.json")
    errors = []
    if policy.get("mode") != "safe_dry_run":
        errors.append("Initial release must remain in safe_dry_run mode.")
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
    print(f"Audit passed: {len(ids)} agent roles; safe dry-run; zero spend; external writes disabled.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Raawaa Autonomous Engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--type", required=True, dest="task_type")
    enqueue.add_argument("--title", required=True)
    enqueue.add_argument("--payload", default="{}")
    enqueue.add_argument("--key")
    enqueue.add_argument("--max-attempts", type=int, default=2)
    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=50)
    sub.add_parser("run-once")
    log = sub.add_parser("audit-log")
    log.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    if args.command == "audit":
        return audit()
    if args.command == "enqueue":
        task = store().enqueue(args.task_type, args.title, json.loads(args.payload), args.key, args.max_attempts)
        print(json.dumps(task, indent=2))
    elif args.command == "list":
        print(json.dumps(store().list_tasks(args.limit), indent=2))
    elif args.command == "run-once":
        print(json.dumps(run_once(store()), indent=2))
    elif args.command == "audit-log":
        print(json.dumps(store().audit_events(args.limit), indent=2))
    elif args.command == "memory-add":
        item = memory_store().add(args.category, args.content, args.source, args.confidence,
                                  [tag for tag in args.tags.split(",") if tag], args.ttl_days)
        print(json.dumps(item, indent=2, ensure_ascii=False))
    elif args.command == "memory-search":
        print(json.dumps(memory_store().search(args.query, args.category, args.limit), indent=2, ensure_ascii=False))
    elif args.command == "memory-list":
        print(json.dumps(memory_store().list_recent(args.limit), indent=2, ensure_ascii=False))
    elif args.command == "memory-archive":
        print(json.dumps({"archived": memory_store().archive(args.id)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
