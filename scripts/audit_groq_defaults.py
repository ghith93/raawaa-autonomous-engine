#!/usr/bin/env python3
"""Offline default-policy audit; never contacts a provider or verifies billing."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(policy, profiles):
    errors = []
    if policy.get('mode') != 'safe_dry_run':
        errors.append('default policy must be safe_dry_run')
    for key in ('daily_budget_usd', 'monthly_budget_usd', 'per_task_budget_usd'):
        if type(policy.get(key)) not in (int, float) or policy[key] != 0:
            errors.append(f'{key} must be zero')
    for key in ('allow_paid_model_calls', 'allow_external_writes'):
        if policy.get(key) is not False:
            errors.append(f'{key} must be false')
    if policy.get('model_policy', {}).get('paid_fallback_enabled') is not False:
        errors.append('paid fallback must be disabled')
    routes = profiles.get('routes', [])
    if not isinstance(routes, list) or not routes:
        errors.append('provider routes are missing')
        return errors
    if any(route.get('enabled') is not False for route in routes):
        errors.append('all default provider routes must be disabled')
    groq20 = [route for route in routes if route.get('provider') == 'groq'
              and route.get('model') == 'openai/gpt-oss-20b']
    if len(groq20) != 1:
        errors.append('expected exactly one default Groq 20B profile')
    return errors


def main():
    policy = json.loads((ROOT / 'config/policy.json').read_text())
    profiles = json.loads((ROOT / 'config/provider_profiles.json').read_text())
    errors = validate(policy, profiles)
    if errors:
        for error in errors:
            print(f'FAIL: {error}')
        raise SystemExit(1)
    print('Default configuration checks passed; account tier and billing are not verified.')


if __name__ == '__main__':
    main()
