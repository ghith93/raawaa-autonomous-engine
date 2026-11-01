"""Command-line checks for the initial engine foundation."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


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
    import argparse
    parser = argparse.ArgumentParser(description="Raawaa Autonomous Engine checks")
    parser.add_argument("command", choices=["audit"])
    args = parser.parse_args()
    if args.command == "audit":
        return audit()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
