"""Deterministic, read-only Operational Resource & Capability Intelligence.

Derives a unified, provenance-backed inventory and drift/security audit over the
repository's existing authoritative registries, workflows, and migrations without
creating a competing database, scheduler, or runtime framework.

Invariants:
- Zero network calls, zero model calls, zero secret-value reads.
- Secret names only; suspicious literal credential prefixes fail validation.
- External operator surfaces (ChatGPT session connectors, Activepieces, Arena)
  are modeled in a separate execution plane from standalone engine capabilities.
- Groq (`groq`) and xAI Grok (`xai` / `x-ai/grok-4.20`) are strictly separated.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.check_workflow_dispatch_guards import (
    has_manual_dispatch,
    job_blocks,
    parse_push_branches,
    parse_triggers,
    production_dispatch_violations,
    push_branch_secret_violations,
    workflow_violations,
)

ROOT = Path(__file__).resolve().parents[1]

LIFECYCLE_STATES: tuple[str, ...] = (
    "catalogued",
    "configured",
    "connected",
    "permission_verified",
    "tested",
    "enabled",
    "deployed",
    "runtime_verified",
    "expired",
    "revoked",
    "blocked",
    "unknown",
)

EXECUTION_PLANES: tuple[str, ...] = (
    "engine_runtime",
    "cloudflare_edge",
    "staging_isolated",
    "external_operator_session",
    "catalog_only",
)

VERIFICATION_FACET_KEYS: tuple[str, ...] = (
    "present",
    "authenticated",
    "read_permission_verified",
    "catalog_visible",
    "inference_verified",
    "production_compatible",
)

ALLOWED_FACET_VALUES = (True, False, "not_applicable", "unknown")

SECRET_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")
WORKFLOW_SECRET_RE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}")
WORKFLOW_VAR_RE = re.compile(r"\$\{\{\s*vars\.([A-Za-z0-9_]+)\s*\}\}")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
PINNED_ACTION_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
WRITE_PERMISSION_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*:\s*write\b", re.MULTILINE)
SUSPICIOUS_SECRET_VALUE_RE = re.compile(
    r"\b(?:gsk_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|sbp_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{16,})\b"
)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _facets(
    *,
    present: bool | str = "unknown",
    authenticated: bool | str = "unknown",
    read_permission_verified: bool | str = "unknown",
    catalog_visible: bool | str = "not_applicable",
    inference_verified: bool | str = "not_applicable",
    production_compatible: bool | str = "unknown",
) -> dict[str, bool | str]:
    return {
        "present": present,
        "authenticated": authenticated,
        "read_permission_verified": read_permission_verified,
        "catalog_visible": catalog_visible,
        "inference_verified": inference_verified,
        "production_compatible": production_compatible,
    }


def _scan_workflows(root: Path) -> tuple[list[dict[str, Any]], dict[str, list[str]], list[dict[str, Any]]]:
    workflow_dir = root / ".github" / "workflows"
    summaries: list[dict[str, Any]] = []
    secret_to_workflows: dict[str, list[str]] = {}
    findings: list[dict[str, Any]] = []

    if not workflow_dir.is_dir():
        return summaries, secret_to_workflows, findings

    for path in sorted(workflow_dir.glob("*.yml")):
        rel = str(path.relative_to(root))
        content = path.read_text(encoding="utf-8")
        triggers = sorted(parse_triggers(content))
        push_branches = parse_push_branches(content)
        secrets_used = sorted({
            name for name in WORKFLOW_SECRET_RE.findall(content)
            if SECRET_ENV_NAME_RE.match(name)
        })
        vars_used = sorted(set(WORKFLOW_VAR_RE.findall(content)))
        uses_refs = USES_RE.findall(content)
        unpinned_actions = [
            ref for ref in uses_refs
            if not ref.startswith("./") and not PINNED_ACTION_RE.match(ref)
        ]
        write_scopes = sorted(set(WRITE_PERMISSION_RE.findall(content)))

        for sec in secrets_used:
            secret_to_workflows.setdefault(sec, []).append(path.name)

        guard_errors = (
            workflow_violations(rel, content)
            + production_dispatch_violations(rel, content)
            + push_branch_secret_violations(rel, content)
        )
        if guard_errors:
            for err in guard_errors:
                findings.append({
                    "severity": "high",
                    "code": "workflow_secret_or_dispatch_guard_violation",
                    "source": rel,
                    "detail": err,
                })
        if unpinned_actions:
            findings.append({
                "severity": "high",
                "code": "unpinned_github_action",
                "source": rel,
                "detail": f"Unpinned uses references: {', '.join(unpinned_actions)}",
            })
        if "inventory" in path.name and write_scopes:
            findings.append({
                "severity": "medium",
                "code": "inventory_workflow_declares_write_permission",
                "source": rel,
                "detail": (
                    f"{path.name} is named as an inventory check but declares write permission(s) "
                    f"({', '.join(write_scopes)}); do not dispatch under strict read-only zero-write gates."
                ),
            })

        summaries.append({
            "workflow_file": path.name,
            "path": rel,
            "triggers": triggers,
            "manual_dispatch": has_manual_dispatch(content),
            "push_branches": push_branches,
            "write_permissions": write_scopes,
            "secret_names": secrets_used,
            "variable_names": vars_used,
            "jobs_count": len(job_blocks(content)),
            "all_actions_sha_pinned": len(unpinned_actions) == 0,
            "guard_clean": len(guard_errors) == 0,
        })

    return summaries, secret_to_workflows, findings


def _scan_repository_hygiene(root: Path) -> dict[str, Any]:
    excluded_parts = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    by_top: dict[str, int] = {}
    total_files = 0
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        rel_parts = item.relative_to(root).parts
        if any(part in excluded_parts for part in rel_parts):
            continue
        total_files += 1
        top = rel_parts[0] if len(rel_parts) > 1 else "(root)"
        by_top[top] = by_top.get(top, 0) + 1

    pkg_path = root / "package.json"
    pkg = _read_json(pkg_path) if pkg_path.is_file() else {}
    npm_deps = sorted((pkg.get("dependencies") or {}).keys())
    npm_dev_deps = sorted((pkg.get("devDependencies") or {}).keys())

    migrations_dir = root / "migrations"
    migration_files = sorted(p.name for p in migrations_dir.glob("*.sql")) if migrations_dir.is_dir() else []

    skills_dir = root / "skills"
    admitted_skill_manifests = (
        sorted(str(p.relative_to(root)) for p in skills_dir.glob("*/skill.json"))
        if skills_dir.is_dir()
        else []
    )

    duplicate_experiment_dirs = sorted(
        p.name for p in (root / "experiments").iterdir()
        if p.is_dir() and "microsoft" in p.name
    ) if (root / "experiments").is_dir() else []

    return {
        "total_files": total_files,
        "files_by_top_directory": dict(sorted(by_top.items())),
        "npm_runtime_dependencies": npm_deps,
        "npm_dev_dependencies": npm_dev_deps,
        "python_core_third_party_dependencies": [],
        "d1_migrations_count": len(migration_files),
        "d1_migration_files": migration_files,
        "admitted_skill_manifests_count": len(admitted_skill_manifests),
        "admitted_skill_manifests": admitted_skill_manifests,
        "duplicate_experiment_directories": duplicate_experiment_dirs,
    }


def build_resource_inventory(
    root: Path = ROOT,
    *,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical derived resource and capability inventory."""
    root = Path(root).resolve()
    now = (reference_time or datetime.now(timezone.utc)).astimezone(timezone.utc)

    policy = _read_json(root / "config" / "policy.json")
    groq_smoke_policy = _read_json(root / "config" / "policy.groq-smoke.json")
    provider_catalog = _read_json(root / "config" / "provider_catalog.json")
    provider_profiles = _read_json(root / "config" / "provider_profiles.json")
    groq_smoke_profiles = _read_json(root / "config" / "provider_profiles.groq-smoke.json")
    free_programs = _read_json(root / "config" / "free_resource_programs.json")
    credentials_cfg = _read_json(root / "config" / "credentials.json")
    integrations_cfg = _read_json(root / "config" / "integrations.json")
    tool_permissions = _read_json(root / "config" / "tool_permissions.json")
    agents_cfg = _read_json(root / "config" / "agents.json")
    revocations_cfg = _read_json(root / "config" / "skills" / "revocations.json")

    workflows, secret_consumers, findings = _scan_workflows(root)
    repo_hygiene = _scan_repository_hygiene(root)

    live_observations = integrations_cfg.get("live_account_observations", [])
    obs_by_secret_present: set[str] = set()
    obs_by_secret_absent: set[str] = set()
    groq_auth_obs: dict[str, Any] | None = None
    workers_ai_obs: dict[str, Any] | None = None
    prod_d1_obs: dict[str, Any] | None = None
    billing_obs: dict[str, Any] | None = None

    for obs in live_observations if isinstance(live_observations, list) else []:
        if not isinstance(obs, dict):
            continue
        for name in obs.get("checked_secret_names_present", []):
            if isinstance(name, str):
                obs_by_secret_present.add(name)
        for name in obs.get("checked_secret_names_absent", []):
            if isinstance(name, str):
                obs_by_secret_absent.add(name)
        wf = str(obs.get("workflow", ""))
        if wf.endswith("groq-connection-check.yml"):
            groq_auth_obs = obs
        elif wf.endswith("cloudflare-workers-ai-inventory.yml"):
            workers_ai_obs = obs
        elif wf.endswith("d1-migration-inventory.yml"):
            prod_d1_obs = obs
        elif wf.endswith("cloudflare-billing-inventory.yml"):
            billing_obs = obs

        exp = _parse_iso_utc(obs.get("expires_at"))
        if exp is not None and exp <= now:
            findings.append({
                "severity": "medium",
                "code": "live_observation_expired",
                "source": str(obs.get("observation_id", wf)),
                "detail": f"Observation {obs.get('observation_id')} expired at {obs.get('expires_at')}.",
            })

    resources: list[dict[str, Any]] = []

    # 1. Credential roles & workflow secret names (secret names only, never values)
    roles = credentials_cfg.get("roles", {})
    configured_aliases: set[str] = set()
    for role_name, aliases in (roles.items() if isinstance(roles, dict) else []):
        alias_list = [a for a in aliases if isinstance(a, str)]
        configured_aliases.update(alias_list)
        any_present = any(a in obs_by_secret_present for a in alias_list)
        state = "connected" if any_present else "configured"
        consumers = sorted({
            wf for alias in alias_list for wf in secret_consumers.get(alias, [])
        })
        resources.append({
            "id": f"credential_role:{role_name}",
            "name": f"Credential Role — {role_name}",
            "category": "credential_role",
            "execution_plane": "engine_runtime",
            "lifecycle_state": state,
            "verification_facets": _facets(
                present=True if any_present else "unknown",
                authenticated=True if role_name == "cloudflare_token" and workers_ai_obs else "unknown",
                read_permission_verified=True if role_name == "cloudflare_token" and workers_ai_obs else "unknown",
                catalog_visible="not_applicable",
                inference_verified="not_applicable",
                production_compatible=True if any_present else "unknown",
            ),
            "secret_names": alias_list,
            "consumers": consumers or ["engine/credentials.py"],
            "dependencies": [],
            "quotas_and_cost": {"cost_usd": 0.0, "paid_allowed": False},
            "data_classes_allowed": ["internal_operational_metadata"],
            "provenance": {
                "source_path_or_url": "config/credentials.json",
                "verified_at": "2026-11-06T20:02:54Z" if any_present else None,
                "expires_at": "2026-11-13T20:02:54Z" if any_present else None,
                "evidence_ref": "secret-readiness.yml run 42284892504 (boolean name check; values withheld)",
                "confidence": "verified" if any_present else "configured_only",
            },
            "notes": "Role-based alias resolution; secret values are never read or stored in repository.",
        })

    # Additional workflow secrets not in credentials.json roles (e.g., GROQ_API_KEY, GEMINI_API_KEY, CF_ACCESS_*)
    for sec_name in sorted(secret_consumers):
        if sec_name in configured_aliases:
            continue
        if sec_name in obs_by_secret_present:
            present_val: bool | str = True
            state = "permission_verified" if sec_name == "GROQ_API_KEY" and groq_auth_obs else "connected"
        elif sec_name in obs_by_secret_absent:
            present_val = False
            state = "blocked"
        else:
            present_val = "unknown"
            state = "configured"
        auth_val: bool | str = True if (sec_name == "GROQ_API_KEY" and groq_auth_obs) else (False if present_val is False else "unknown")
        resources.append({
            "id": f"secret_name:{sec_name}",
            "name": f"GitHub Actions Secret Reference — {sec_name}",
            "category": "secret_reference",
            "execution_plane": "engine_runtime",
            "lifecycle_state": state,
            "verification_facets": _facets(
                present=present_val,
                authenticated=auth_val,
                read_permission_verified=auth_val,
                catalog_visible=True if (sec_name == "GROQ_API_KEY" and groq_auth_obs) else "not_applicable",
                inference_verified=False if sec_name in {"GROQ_API_KEY", "GEMINI_API_KEY"} else "not_applicable",
                production_compatible=False if sec_name in {"GROQ_API_KEY", "GEMINI_API_KEY"} else "unknown",
            ),
            "secret_names": [sec_name],
            "consumers": sorted(secret_consumers[sec_name]),
            "dependencies": [],
            "quotas_and_cost": {"cost_usd": 0.0, "paid_allowed": False},
            "data_classes_allowed": ["public"] if sec_name in {"GROQ_API_KEY", "GEMINI_API_KEY"} else ["internal_operational_metadata"],
            "provenance": {
                "source_path_or_url": ".github/workflows/",
                "verified_at": "2026-11-06T20:02:54Z" if present_val in (True, False) else None,
                "expires_at": "2026-11-13T20:02:54Z" if present_val in (True, False) else None,
                "evidence_ref": (
                    "secret-readiness.yml run 42284892504 + groq-connection-check.yml run 42284232726"
                    if sec_name == "GROQ_API_KEY"
                    else "secret-readiness.yml run 42284892504"
                    if present_val in (True, False)
                    else "Referenced in workflow YAML; not covered by 5-name secret-readiness check"
                ),
                "confidence": "verified" if present_val in (True, False) else "unverified",
            },
            "notes": "Name-only workflow reference; presence alone never implies scope or inference eligibility.",
        })

    # 2. Provider catalog & explicit Groq vs xAI Grok separation
    profile_routes = provider_profiles.get("routes", [])
    smoke_routes = {
        r.get("model"): r for r in groq_smoke_profiles.get("routes", []) if isinstance(r, dict)
    }
    routes_by_provider: dict[str, list[dict[str, Any]]] = {}
    for route in profile_routes if isinstance(profile_routes, list) else []:
        if isinstance(route, dict):
            routes_by_provider.setdefault(str(route.get("provider", "")), []).append(route)

    for prov in provider_catalog.get("providers", []):
        if not isinstance(prov, dict):
            continue
        pid = str(prov.get("id", "")).strip()
        prov_routes = routes_by_provider.get(pid, [])
        if pid == "groq":
            p_state = "permission_verified" if groq_auth_obs else "configured"
            facets = _facets(
                present=True if groq_auth_obs else "unknown",
                authenticated=True if groq_auth_obs else "unknown",
                read_permission_verified=True if groq_auth_obs else "unknown",
                catalog_visible=True if groq_auth_obs else "unknown",
                inference_verified=False,
                production_compatible=False,
            )
            note = (
                "Groq LPU Cloud (`https://api.groq.com/openai/v1`), completely distinct from xAI Grok. "
                "Default registry routes are disabled (`safe_dry_run`); read-only auth/catalog verified in "
                "run 42284232726 (`openai/gpt-oss-20b` & `120b` visible, 0 inference calls). `20b` has 1 "
                "historical isolated-Staging smoke (run 42254857112 attempt 1); `120b` is quarantined after HTTP 403."
            )
            secret_list = ["GROQ_API_KEY"]
        elif pid == "xai":
            p_state = "blocked"
            facets = _facets(
                present=False,
                authenticated=False,
                read_permission_verified=False,
                catalog_visible=False,
                inference_verified=False,
                production_compatible=False,
            )
            note = (
                "xAI Grok (`xai`, distinct from Groq LPU Cloud). Catalog-only entry (`network_calls_enabled=false`); "
                "no engine adapter, no `provider_profiles.json` route, no `XAI_API_KEY` credential, and no verified "
                "$0 free tier or hard stop. Activepieces lists `x-ai/grok-4.20` in its 21-model catalog, which is "
                "external catalog metadata only and strictly blocked under the USD 0 policy."
            )
            secret_list = []
        elif pid == "cloudflare_workers_ai":
            p_state = "permission_verified" if workers_ai_obs else "catalogued"
            facets = _facets(
                present=True if workers_ai_obs else "unknown",
                authenticated=True if workers_ai_obs else "unknown",
                read_permission_verified=True if workers_ai_obs else "unknown",
                catalog_visible=True if workers_ai_obs else "unknown",
                inference_verified=False,
                production_compatible=False,
            )
            note = (
                "Workers AI catalog read verified in run 42285042447 (`@cf/google/gemma-4-26b-a4b-it` visible, "
                "0 LLM inference calls). Shared 10,000 Neurons/day free allocation exists, but account plan and "
                "account-wide spend-policy hard stop (HTTP 404 in run 42285028161) are unverified for LLM routing."
            )
            secret_list = ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"]
        elif pid == "gemini":
            p_state = "blocked" if "GEMINI_API_KEY" in obs_by_secret_absent else "catalogued"
            facets = _facets(
                present=False if "GEMINI_API_KEY" in obs_by_secret_absent else "unknown",
                authenticated=False,
                read_permission_verified=False,
                catalog_visible=False,
                inference_verified=False,
                production_compatible=False,
            )
            note = "Adapter code exists in engine/live_adapters.py, but GEMINI_API_KEY is absent (run 42284892504) and no enabled profile exists."
            secret_list = ["GEMINI_API_KEY"]
        else:
            p_state = "configured" if prov_routes else "catalogued"
            facets = _facets(
                present=False,
                authenticated=False,
                read_permission_verified=False,
                catalog_visible=False,
                inference_verified=False,
                production_compatible=False,
            )
            note = f"Discovery-only catalog record (`free_tier_status={prov.get('free_tier_status')}`); network calls disabled."
            secret_list = sorted({str(r.get("api_key_env")) for r in prov_routes if r.get("api_key_env")})

        resources.append({
            "id": f"provider:{pid}",
            "name": f"Model Provider — {prov.get('name', pid)}",
            "category": "model_provider",
            "execution_plane": "engine_runtime" if prov_routes else "catalog_only",
            "lifecycle_state": p_state,
            "verification_facets": facets,
            "secret_names": secret_list,
            "consumers": [str(r.get("id")) for r in prov_routes] or ["config/provider_catalog.json"],
            "dependencies": ["engine/model_gateway.py", "engine/quota_manager.py"] if prov_routes else [],
            "quotas_and_cost": {
                "free_tier_status": prov.get("free_tier_status"),
                "network_calls_enabled": bool(prov.get("network_calls_enabled", False)),
                "cost_cap_usd": 0.0,
            },
            "data_classes_allowed": ["public"] if pid == "groq" else [],
            "provenance": {
                "source_path_or_url": str(prov.get("official_docs_url") or "config/provider_catalog.json"),
                "verified_at": (
                    "2026-11-06T19:58:01Z" if pid == "groq" and groq_auth_obs
                    else "2026-11-06T20:04:05Z" if pid == "cloudflare_workers_ai" and workers_ai_obs
                    else None
                ),
                "expires_at": (
                    "2026-11-13T19:58:01Z" if pid == "groq" and groq_auth_obs
                    else "2026-11-13T20:04:05Z" if pid == "cloudflare_workers_ai" and workers_ai_obs
                    else None
                ),
                "evidence_ref": (
                    "groq-connection-check.yml run 42284232726" if pid == "groq" and groq_auth_obs
                    else "cloudflare-workers-ai-inventory.yml run 42285042447" if pid == "cloudflare_workers_ai" and workers_ai_obs
                    else "config/provider_catalog.json"
                ),
                "confidence": "verified" if pid in {"groq", "cloudflare_workers_ai", "xai"} else "catalog_metadata",
            },
            "notes": note,
        })

    # 3. Provider profile routes (all 4 routes in config/provider_profiles.json)
    for route in profile_routes if isinstance(profile_routes, list) else []:
        if not isinstance(route, dict):
            continue
        rid = str(route.get("id", ""))
        model = str(route.get("model", ""))
        verified_dt = _parse_iso_utc(route.get("verified_at"))
        expires_dt = verified_dt + timedelta(days=7) if verified_dt else None
        is_expired = bool(expires_dt and expires_dt <= now)
        hours_to_expiry = ((expires_dt - now).total_seconds() / 3600.0) if expires_dt else None

        if hours_to_expiry is not None and 0 < hours_to_expiry <= 48 and route.get("free_verified") is True:
            findings.append({
                "severity": "medium",
                "code": "provider_profile_verification_expiring_soon",
                "source": f"config/provider_profiles.json#{rid}",
                "detail": (
                    f"Route {rid} ({model}) verified_at={route.get('verified_at')} expires in "
                    f"{hours_to_expiry:.1f}h ({expires_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}); "
                    "re-verify account quotas and hard-stop posture before any new live smoke."
                ),
            })

        smoke_overlay = smoke_routes.get(model)
        if model == "openai/gpt-oss-120b":
            r_state = "blocked"
            r_facets = _facets(
                present=True if groq_auth_obs else "unknown",
                authenticated=True if groq_auth_obs else "unknown",
                read_permission_verified=True if groq_auth_obs else "unknown",
                catalog_visible=True if groq_auth_obs else "unknown",
                inference_verified=False,
                production_compatible=False,
            )
        elif model == "openai/gpt-oss-20b":
            r_state = "expired" if is_expired else "tested"
            r_facets = _facets(
                present=True if groq_auth_obs else "unknown",
                authenticated=True if groq_auth_obs else "unknown",
                read_permission_verified=True if groq_auth_obs else "unknown",
                catalog_visible=True if groq_auth_obs else "unknown",
                inference_verified=True,
                production_compatible=False,
            )
        elif route.get("free_verified") is True:
            r_state = "expired" if is_expired else "configured"
            r_facets = _facets(
                present=True if groq_auth_obs else "unknown",
                authenticated=True if groq_auth_obs else "unknown",
                read_permission_verified=True if groq_auth_obs else "unknown",
                catalog_visible="unknown",
                inference_verified=False,
                production_compatible=False,
            )
        else:
            r_state = "blocked"
            r_facets = _facets(
                present=False,
                authenticated=False,
                read_permission_verified=False,
                catalog_visible=False,
                inference_verified=False,
                production_compatible=False,
            )

        resources.append({
            "id": f"route:{rid}",
            "name": f"Model Route — {rid} ({model})",
            "category": "model_route",
            "execution_plane": "staging_isolated" if (smoke_overlay and smoke_overlay.get("enabled")) else "engine_runtime",
            "lifecycle_state": "enabled" if route.get("enabled") is True else r_state,
            "verification_facets": r_facets,
            "secret_names": [str(route["api_key_env"])] if route.get("api_key_env") else [],
            "consumers": (
                ["scripts/run_groq_free_inference_smoke.py", "scripts/run_specialist_lanes.py"]
                if model == "openai/gpt-oss-20b"
                else ["engine/profile_routing.py"]
            ),
            "dependencies": [f"provider:{route.get('provider')}", "engine/quota_manager.py"],
            "quotas_and_cost": {
                "enabled_default": bool(route.get("enabled")),
                "enabled_in_groq_staging_smoke_overlay": bool(smoke_overlay and smoke_overlay.get("enabled")),
                "free_verified": bool(route.get("free_verified")),
                "production_use_allowed": bool(route.get("production_use_allowed")),
                "minute_request_limit": route.get("minute_request_limit"),
                "daily_request_limit": route.get("daily_request_limit"),
                "daily_token_limit": route.get("daily_token_limit"),
                "input_cost_per_million_usd": route.get("input_cost_per_million_usd"),
                "output_cost_per_million_usd": route.get("output_cost_per_million_usd"),
            },
            "data_classes_allowed": (
                list(groq_smoke_policy.get("live_inference_data_policy", {}).get("allowed_classifications", ["public"]))
                if model == "openai/gpt-oss-20b"
                else []
            ),
            "provenance": {
                "source_path_or_url": "config/provider_profiles.json",
                "verified_at": route.get("verified_at"),
                "expires_at": expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if expires_dt else None,
                "evidence_ref": str(route.get("verification_note", ""))[:220],
                "confidence": "verified" if route.get("free_verified") is True else "quarantined_or_unverified",
            },
            "notes": str(route.get("verification_note", "")),
        })

    # 4. Tool Broker permissions, Agent Roster, and Skills Registry
    tools_map = tool_permissions.get("tools", {})
    for tool_name, spec in (tools_map.items() if isinstance(tools_map, dict) else []):
        if not isinstance(spec, dict):
            continue
        grants = spec.get("grants", [])
        consumers = [
            f"{g.get('agent_role')}:{','.join(g.get('task_types', []))}"
            for g in grants if isinstance(g, dict)
        ]
        resources.append({
            "id": f"tool_permission:{tool_name}",
            "name": f"Tool Broker Grant — {tool_name}",
            "category": "engine_tool_permission",
            "execution_plane": "engine_runtime",
            "lifecycle_state": "configured",
            "verification_facets": _facets(
                present=True,
                authenticated="not_applicable",
                read_permission_verified=True,
                catalog_visible="not_applicable",
                inference_verified="not_applicable",
                production_compatible=False,
            ),
            "secret_names": [],
            "consumers": consumers,
            "dependencies": ["engine/tool_broker.py"],
            "quotas_and_cost": {"access": spec.get("access"), "cost_usd": 0.0, "runtime_adapter_wired": False},
            "data_classes_allowed": ["public", "repository_tracked_files"],
            "provenance": {
                "source_path_or_url": "config/tool_permissions.json",
                "verified_at": "2026-11-06T20:00:11Z",
                "expires_at": None,
                "evidence_ref": "Static matrix validated by engine.tool_broker.validate_permission_matrix; Worker registers 0 live adapters.",
                "confidence": "verified",
            },
            "notes": f"Allowed in environments {spec.get('environments')}; no adapter wired in live Worker.",
        })

    agents_list = [a for a in agents_cfg.get("agents", []) if isinstance(a, dict)]
    resources.append({
        "id": "engine:agent_roster",
        "name": "Declarative Agent Role Roster (8 roles, dry-run contracts only)",
        "category": "agent_roster",
        "execution_plane": "engine_runtime",
        "lifecycle_state": "configured",
        "verification_facets": _facets(
            present=True,
            authenticated="not_applicable",
            read_permission_verified="not_applicable",
            catalog_visible="not_applicable",
            inference_verified=False,
            production_compatible=True,
        ),
        "secret_names": [],
        "consumers": ["engine/agent_runtime.py", "engine/tool_broker.py", "scripts/run_specialist_lanes.py"],
        "dependencies": ["config/policy.json"],
        "quotas_and_cost": {
            "total_roles": len(agents_list),
            "enabled_role_descriptors": sum(1 for a in agents_list if a.get("enabled") is True),
            "autonomous_daemons_running": 0,
            "cost_usd": 0.0,
        },
        "data_classes_allowed": ["public"],
        "provenance": {
            "source_path_or_url": "config/agents.json",
            "verified_at": "2026-11-06T20:00:11Z",
            "expires_at": None,
            "evidence_ref": "config/agents.json + engine/agent_runtime.py",
            "confidence": "verified",
        },
        "notes": "Declarative task-to-role contract table; does not spawn autonomous processes or call models by itself.",
    })

    revoked_skills = revocations_cfg.get("revoked_skill_ids", [])
    resources.append({
        "id": "engine:skills_registry",
        "name": "Engine Local Skills Registry",
        "category": "skills_registry",
        "execution_plane": "engine_runtime",
        "lifecycle_state": "configured",
        "verification_facets": _facets(
            present=True,
            authenticated="not_applicable",
            read_permission_verified="not_applicable",
            catalog_visible="not_applicable",
            inference_verified="not_applicable",
            production_compatible=True,
        ),
        "secret_names": [],
        "consumers": ["engine/skills_registry.py", "engine/skill_static_gate.py"],
        "dependencies": ["config/skills/revocations.json", "schemas/skill-manifest.schema.json"],
        "quotas_and_cost": {
            "admitted_skills_count": repo_hygiene["admitted_skill_manifests_count"],
            "revoked_skills_count": len(revoked_skills) if isinstance(revoked_skills, list) else 0,
            "cost_usd": 0.0,
        },
        "data_classes_allowed": ["public"],
        "provenance": {
            "source_path_or_url": "skills/README.md",
            "verified_at": "2026-11-06T20:00:11Z",
            "expires_at": None,
            "evidence_ref": "engine/skills_registry.py + config/skills/revocations.json",
            "confidence": "verified",
        },
        "notes": "Zero packaged skills admitted in repo; ChatGPT's 271 session skills across 38 packs are external and never auto-imported.",
    })

    # 5. Registered integrations, Infrastructure assets, and External Operator Surfaces
    for key, item in integrations_cfg.get("integrations", {}).items():
        if not isinstance(item, dict):
            continue
        state = "connected" if key == "arena_agent_mode" else "blocked"
        resources.append({
            "id": f"integration:{key}",
            "name": str(item.get("name", key)),
            "category": "registered_integration",
            "execution_plane": "external_operator_session" if key == "arena_agent_mode" else "engine_runtime",
            "lifecycle_state": state,
            "verification_facets": _facets(
                present=True if key == "arena_agent_mode" else False,
                authenticated=True if key == "arena_agent_mode" else False,
                read_permission_verified=True if key == "arena_agent_mode" else False,
                catalog_visible="not_applicable",
                inference_verified=False,
                production_compatible=False,
            ),
            "secret_names": [s for s in item.get("required_environment_names", []) if isinstance(s, str)],
            "consumers": ["AGENTS.md", "scripts/session_continuity.py"] if key == "arena_agent_mode" else ["engine/cli.py"],
            "dependencies": [],
            "quotas_and_cost": item.get("cost_policy", {"new_infrastructure_spend_allowed_usd": 0}),
            "data_classes_allowed": ["public", "repository_tracked_files"],
            "provenance": {
                "source_path_or_url": str(item.get("source", "config/integrations.json")),
                "verified_at": "2026-11-06T20:04:44Z",
                "expires_at": None,
                "evidence_ref": str(item.get("status", "")),
                "confidence": "verified",
            },
            "notes": str(item.get("status_reason", "")),
        })

    for key, asset in integrations_cfg.get("infrastructure_assets", {}).items():
        if not isinstance(asset, dict):
            continue
        prov = asset.get("provenance", {}) if isinstance(asset.get("provenance"), dict) else {}
        pending_migs = asset.get("pending_production_migrations", [])
        if pending_migs:
            findings.append({
                "severity": "high",
                "code": "production_d1_pending_migrations_gate",
                "source": "wrangler.toml",
                "detail": (
                    f"Production D1 has {len(pending_migs)} unapplied migrations ({', '.join(pending_migs)}) "
                    "per run 42285058780. Currently deployed older Worker remains compatible with current D1, "
                    "but main worker/index.js requires both migrations and cannot be deployed until an "
                    "explicit owner-approved Production migration gate executes."
                ),
            })
        if key == "cloudflare_billing_and_access_posture" and asset.get("spend_policy_hard_stop_verified") is False:
            findings.append({
                "severity": "medium",
                "code": "cloudflare_external_spend_policy_unverified",
                "source": "cloudflare-billing-inventory.yml",
                "detail": (
                    "Cloudflare Spend Policies endpoint returned HTTP 404 (run 42285028161) and Access is disabled "
                    "(run 42285013735); never infer an external usage_block hard stop from HTTP 404."
                ),
            })
        prod_compat: bool | str = False if pending_migs or asset.get("spend_policy_hard_stop_verified") is False else True
        resources.append({
            "id": f"infra:{key}",
            "name": str(asset.get("name", key)),
            "category": "infrastructure_asset",
            "execution_plane": str(asset.get("execution_plane", "cloudflare_edge")),
            "lifecycle_state": str(asset.get("lifecycle_state", "deployed")),
            "verification_facets": _facets(
                present=True,
                authenticated=True,
                read_permission_verified=True,
                catalog_visible="not_applicable",
                inference_verified="not_applicable",
                production_compatible=prod_compat,
            ),
            "secret_names": [s for s in asset.get("secret_names", []) if isinstance(s, str)],
            "consumers": [".github/workflows/ci.yml", ".github/workflows/task-runner.yml", "worker/index.js"],
            "dependencies": list(pending_migs),
            "quotas_and_cost": {"plan": "cloudflare_free_only", "paid_overages_allowed": False, "cost_usd": 0.0},
            "data_classes_allowed": ["public", "internal_operational_metadata"],
            "provenance": {
                "source_path_or_url": str(prov.get("source_path_or_url", "config/integrations.json")),
                "verified_at": prov.get("verified_at"),
                "expires_at": prov.get("expires_at"),
                "evidence_ref": str(prov.get("evidence_ref", "")),
                "confidence": str(prov.get("confidence", "verified")),
            },
            "notes": str(asset.get("note") or asset.get("production_worker_schema_compatibility", {}).get("note", "")),
        })

    for key, surf in integrations_cfg.get("external_operator_surfaces", {}).items():
        if not isinstance(surf, dict):
            continue
        prov = surf.get("provenance", {}) if isinstance(surf.get("provenance"), dict) else {}
        connectors = surf.get("connectors", {})
        broad_connectors = [
            cname for cname, cinfo in (connectors.items() if isinstance(connectors, dict) else [])
            if isinstance(cinfo, dict) and cinfo.get("permission_posture") == "Allow all actions"
        ]
        if broad_connectors:
            findings.append({
                "severity": "medium",
                "code": "external_connector_broad_action_posture",
                "source": f"external_operator_surfaces:{key}",
                "detail": (
                    f"ChatGPT connectors ({', '.join(sorted(broad_connectors))}) have 'Allow all actions' "
                    "enabled; recommend owner least-privilege review (do not mutate without explicit approval)."
                ),
            })
        resources.append({
            "id": f"external_surface:{key}",
            "name": str(surf.get("name", key)),
            "category": "external_operator_surface",
            "execution_plane": "external_operator_session",
            "lifecycle_state": str(surf.get("lifecycle_state", "connected")),
            "verification_facets": _facets(
                present=True,
                authenticated=True,
                read_permission_verified=True,
                catalog_visible=True if surf.get("catalog_ai_models_count") else "not_applicable",
                inference_verified=False,
                production_compatible=False,
            ),
            "secret_names": [],
            "consumers": ["human_operator", "chatgpt_session"],
            "dependencies": [],
            "quotas_and_cost": {
                "can_engine_call_directly": False,
                "cost_usd": 0.0,
                "catalog_ai_models_count": surf.get("catalog_ai_models_count", 0),
                "session_tool_definitions_count": surf.get("session_tool_definitions_count", 0),
                "session_skills_count": surf.get("session_skills_count", 0),
            },
            "data_classes_allowed": ["public", "sanitized_operational_summaries"],
            "provenance": {
                "source_path_or_url": str(prov.get("source_path_or_url", "config/integrations.json")),
                "verified_at": prov.get("verified_at"),
                "expires_at": prov.get("expires_at"),
                "evidence_ref": str(prov.get("evidence_ref", "")),
                "confidence": str(prov.get("confidence", "verified")),
            },
            "notes": (
                "External operator session plane; capabilities and ephemeral tool/skill catalogs are NOT "
                "callable by standalone Python workers in GitHub Actions or Cloudflare Workers."
            ),
        })

    # Explicit catalog-only entry for Activepieces x-ai/grok-4.20 so it is never conflated with Groq
    resources.append({
        "id": "external_catalog_model:activepieces:x-ai/grok-4.20",
        "name": "Activepieces Catalog Model — x-ai/grok-4.20 (xAI Grok, NOT Groq)",
        "category": "external_catalog_model",
        "execution_plane": "catalog_only",
        "lifecycle_state": "blocked",
        "verification_facets": _facets(
            present=False,
            authenticated=False,
            read_permission_verified=False,
            catalog_visible=True,
            inference_verified=False,
            production_compatible=False,
        ),
        "secret_names": [],
        "consumers": [],
        "dependencies": ["external_surface:activepieces_ghiths_project"],
        "quotas_and_cost": {"free_verified": False, "hard_stop_verified": False, "cost_usd": None},
        "data_classes_allowed": [],
        "provenance": {
            "source_path_or_url": "https://github.com/ghith93/raawaa-autonomous-engine/issues/464#issuecomment-6694076722",
            "verified_at": "2026-11-06T19:54:18Z",
            "expires_at": "2026-11-13T19:54:18Z",
            "evidence_ref": "Issue #464 comment 6694076722 (Activepieces 21-model catalog listing)",
            "confidence": "verified",
        },
        "notes": "Listed in Activepieces AI provider catalog only; zero credential, zero free-tier proof, zero hard stop. Strictly blocked under USD 0 rule and distinct from Groq (`groq`).",
    })

    state_counts: dict[str, int] = {state: 0 for state in LIFECYCLE_STATES}
    plane_counts: dict[str, int] = {plane: 0 for plane in EXECUTION_PLANES}
    for res in resources:
        st = res["lifecycle_state"]
        pl = res["execution_plane"]
        state_counts[st] = state_counts.get(st, 0) + 1
        plane_counts[pl] = plane_counts.get(pl, 0) + 1

    inventory: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "engine.resource_intelligence",
        "reference_time_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy_posture": {
            "mode": policy.get("mode"),
            "monthly_budget_usd": policy.get("monthly_budget_usd"),
            "monthly_total_hard_cap_usd": policy.get("budget_envelope", {}).get("monthly_total_hard_cap_usd"),
            "allow_paid_model_calls": policy.get("allow_paid_model_calls"),
            "allow_external_writes": policy.get("allow_external_writes"),
        },
        "counts": {
            "total_resources": len(resources),
            "by_lifecycle_state": state_counts,
            "by_execution_plane": plane_counts,
            "workflows_total": len(workflows),
            "live_account_observations_total": len(live_observations) if isinstance(live_observations, list) else 0,
            "free_tier_programs_catalogued": len(free_programs.get("recurring_free_tiers", [])),
            "drift_and_risk_findings_total": len(findings),
        },
        "groq_vs_xai_grok_separation": {
            "groq": {
                "provider_id": "groq",
                "vendor": "Groq Inc. (LPU Inference Cloud)",
                "endpoint": "https://api.groq.com/openai/v1",
                "engine_adapter_implemented": True,
                "secret_name": "GROQ_API_KEY",
                "auth_and_catalog_verified_run_id": 42284232726,
                "gpt_oss_20b_status": "tested_in_isolated_staging_disabled_by_default_dispatch_gate_pending",
                "gpt_oss_120b_status": "quarantined_http_403_free_verified_false_production_disallowed",
            },
            "xai_grok": {
                "provider_id": "xai",
                "vendor": "xAI Corp. (Grok models)",
                "activepieces_catalog_entry": "x-ai/grok-4.20",
                "engine_adapter_implemented": False,
                "secret_name": None,
                "free_tier_or_hard_stop_verified": False,
                "status": "blocked_catalog_only_under_zero_usd_policy",
            },
        },
        "repository_hygiene": repo_hygiene,
        "workflows": workflows,
        "live_account_observations": live_observations if isinstance(live_observations, list) else [],
        "resources": resources,
        "drift_and_risk_findings": findings,
    }
    inventory["validation_errors"] = validate_resource_inventory(inventory, root=root)
    return inventory


def validate_resource_inventory(inventory: dict[str, Any], *, root: Path = ROOT) -> list[str]:
    """Validate structural, security, zero-cost, and quarantine invariants."""
    errors: list[str] = []
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        return ["inventory must be a schema_version 1 object"]

    serialized = json.dumps(inventory, sort_keys=True)
    if SUSPICIOUS_SECRET_VALUE_RE.search(serialized):
        errors.append("inventory contains a suspicious literal credential token prefix")
    active_surface = json.dumps(
        {"resources": inventory.get("resources", []), "workflows": inventory.get("workflows", [])},
        sort_keys=True,
    )
    if "telegram" in active_surface.lower():
        errors.append("inventory resources or workflows contain retired Telegram references")

    posture = inventory.get("policy_posture", {})
    if posture.get("mode") != "safe_dry_run":
        errors.append("default policy.json mode must remain safe_dry_run")
    if posture.get("monthly_budget_usd") != 0 or posture.get("monthly_total_hard_cap_usd") != 0:
        errors.append("default monthly model and all-in hard caps must remain USD 0")
    if posture.get("allow_paid_model_calls") is not False or posture.get("allow_external_writes") is not False:
        errors.append("paid model calls and external writes must remain disabled by default")

    seen_ids: set[str] = set()
    for item in inventory.get("resources", []):
        if not isinstance(item, dict):
            errors.append("resource entry must be an object")
            continue
        rid = item.get("id")
        if not isinstance(rid, str) or not rid.strip():
            errors.append("resource entry missing non-empty id")
            continue
        if rid in seen_ids:
            errors.append(f"duplicate resource id: {rid}")
        seen_ids.add(rid)

        state = item.get("lifecycle_state")
        if state not in LIFECYCLE_STATES:
            errors.append(f"{rid}: unsupported lifecycle_state {state!r}")
        plane = item.get("execution_plane")
        if plane not in EXECUTION_PLANES:
            errors.append(f"{rid}: unsupported execution_plane {plane!r}")

        facets = item.get("verification_facets")
        if not isinstance(facets, dict) or set(facets.keys()) != set(VERIFICATION_FACET_KEYS):
            errors.append(f"{rid}: verification_facets must contain exact keys {VERIFICATION_FACET_KEYS}")
        else:
            for fkey, fval in facets.items():
                if fval not in ALLOWED_FACET_VALUES:
                    errors.append(f"{rid}: invalid facet {fkey}={fval!r}")

        for sec in item.get("secret_names", []):
            if not isinstance(sec, str) or not SECRET_ENV_NAME_RE.match(sec):
                errors.append(f"{rid}: invalid secret environment variable name {sec!r}")

        if item.get("category") == "model_route":
            qc = item.get("quotas_and_cost", {})
            if posture.get("mode") == "safe_dry_run" and qc.get("enabled_default") is True:
                errors.append(f"{rid}: model route must not be enabled in safe_dry_run mode")
            if "120b" in rid and (qc.get("free_verified") is True or qc.get("production_use_allowed") is True):
                errors.append(f"{rid}: quarantined 120B route must set free_verified=False and production_use_allowed=False")

        if rid in {"provider:xai", "external_catalog_model:activepieces:x-ai/grok-4.20"} and state != "blocked":
            errors.append(f"{rid}: xAI Grok must remain blocked until $0 hard-stop eligibility is verified")

        if plane == "external_operator_session":
            qc = item.get("quotas_and_cost", {})
            if item.get("category") == "external_operator_surface" and qc.get("can_engine_call_directly") is not False:
                errors.append(f"{rid}: external operator surface cannot be marked directly callable by engine")

    for finding in inventory.get("drift_and_risk_findings", []):
        if isinstance(finding, dict) and finding.get("code") in {
            "workflow_secret_or_dispatch_guard_violation",
            "unpinned_github_action",
        }:
            errors.append(f"workflow security violation: {finding.get('detail')}")

    return errors


def render_resource_markdown(inventory: dict[str, Any]) -> str:
    """Render a concise, mobile-readable Markdown summary of the derived inventory."""
    counts = inventory["counts"]
    posture = inventory["policy_posture"]
    hygiene = inventory["repository_hygiene"]
    sep = inventory["groq_vs_xai_grok_separation"]

    lines = [
        "# Operational Resource & Capability Inventory (Generated)",
        "",
        f"- **Generated by:** `python3 -m engine.cli resource-inventory --format markdown`",
        f"- **Reference UTC:** `{inventory['reference_time_utc']}`",
        f"- **Default Policy:** `{posture['mode']}` · Model cap `${posture['monthly_budget_usd']}` · All-in cap `${posture['monthly_total_hard_cap_usd']}` · Paid calls `{posture['allow_paid_model_calls']}` · External writes `{posture['allow_external_writes']}`",
        f"- **Validation Errors:** `{len(inventory['validation_errors'])}` · **Active Drift / Gate Findings:** `{counts['drift_and_risk_findings_total']}`",
        "",
        "## 1. Executive Counts by Plane & Lifecycle State",
        "",
        "| Execution Plane | Count |",
        "|---|---:|",
    ]
    for plane, val in counts["by_execution_plane"].items():
        lines.append(f"| `{plane}` | {val} |")

    lines.extend([
        "",
        "| Lifecycle State | Count |",
        "|---|---:|",
    ])
    for state, val in counts["by_lifecycle_state"].items():
        if val > 0:
            lines.append(f"| `{state}` | {val} |")

    lines.extend([
        "",
        "## 2. Explicit Separation: Groq (`groq`) vs xAI Grok (`xai`)",
        "",
        "| Dimension | Groq (`groq`) | xAI Grok (`xai` / `x-ai/grok-4.20`) |",
        "|---|---|---|",
        f"| Vendor & Endpoint | {sep['groq']['vendor']} (`{sep['groq']['endpoint']}`) | {sep['xai_grok']['vendor']} (Activepieces catalog `{sep['xai_grok']['activepieces_catalog_entry']}`) |",
        f"| Engine Adapter & Secret Name | `True` (`{sep['groq']['secret_name']}`) | `False` (`None`) |",
        f"| Live Auth / Catalog Evidence | Run `{sep['groq']['auth_and_catalog_verified_run_id']}` (`0` inference calls) | Catalog metadata only; zero auth/quota proof |",
        f"| Route Posture | `20B`: `{sep['groq']['gpt_oss_20b_status']}`; `120B`: `{sep['groq']['gpt_oss_120b_status']}` | `{sep['xai_grok']['status']}` |",
        "",
        "## 3. Live Read-Only Account Observations (Exact Provenance & Facets)",
        "",
        "| Run ID | Workflow | SHA | Present | Auth | Read Perm | Catalog | Inference | Prod Compat | Expires (UTC) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])
    for obs in inventory.get("live_account_observations", []):
        vf = obs.get("verification_facets", {})
        wf_short = Path(str(obs.get("workflow", ""))).name
        sha_short = str(obs.get("head_sha", ""))[:8]
        lines.append(
            f"| `{obs.get('run_id')}` | `{wf_short}` | `{sha_short}` | "
            f"`{vf.get('present')}` | `{vf.get('authenticated')}` | `{vf.get('read_permission_verified')}` | "
            f"`{vf.get('catalog_visible')}` | `{vf.get('inference_verified')}` | `{vf.get('production_compatible')}` | "
            f"`{obs.get('expires_at')}` |"
        )

    lines.extend([
        "",
        "## 4. Resource & Capability Ledger (Secret Names Only)",
        "",
        "| Resource ID | Plane | State | Secret Names | Verification Summary |",
        "|---|---|---|---|---|",
    ])
    for res in inventory.get("resources", []):
        vf = res.get("verification_facets", {})
        secs = ", ".join(f"`{s}`" for s in res.get("secret_names", [])) or "—"
        summary = (
            f"present={vf.get('present')}, auth={vf.get('authenticated')}, "
            f"infer={vf.get('inference_verified')}, prod={vf.get('production_compatible')}"
        )
        lines.append(
            f"| `{res['id']}` | `{res['execution_plane']}` | `{res['lifecycle_state']}` | {secs} | `{summary}` |"
        )

    lines.extend([
        "",
        "## 5. Repository & Workflow Hygiene Snapshot",
        "",
        f"- **Tracked workspace files:** `{hygiene['total_files']}` · **D1 SQL migrations:** `{hygiene['d1_migrations_count']}` · **GitHub workflows:** `{counts['workflows_total']}`",
        f"- **Third-party runtime dependencies:** Python `{len(hygiene['python_core_third_party_dependencies'])}`, npm `{len(hygiene['npm_runtime_dependencies'])}`",
        f"- **Admitted local engine skills:** `{hygiene['admitted_skill_manifests_count']}` (ChatGPT session skills remain external)",
        "",
        "## 6. Active Drift, Security & Operational Gates",
        "",
    ])
    if not inventory["drift_and_risk_findings"]:
        lines.append("- None.")
    else:
        for item in inventory["drift_and_risk_findings"]:
            lines.append(
                f"- **[{str(item.get('severity', 'info')).upper()}] `{item.get('code')}`** (`{item.get('source')}`): {item.get('detail')}"
            )
    lines.append("")
    return "\n".join(lines)
