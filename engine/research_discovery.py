"""Bounded, read-only GitHub repository discovery for proactive research.

This module retrieves public repository metadata only. It never clones, imports,
 executes, installs, or modifies discovered projects.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.github.com/search/repositories"
USER_AGENT = "RaawaaAutonomousEngine-Research/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
DEFAULT_QUERIES = {
    "agent_frameworks": "agent framework orchestration topic:agents archived:false",
    "coding_agents": "open source coding agent archived:false",
    "agent_memory": "agent memory long term memory framework archived:false",
    "mcp_ecosystem": "MCP server tools model context protocol archived:false",
    "model_routing": "LLM model router inference gateway quota archived:false",
    "durable_execution": "durable workflow orchestration agent execution archived:false",
}

class ResearchDiscoveryError(RuntimeError):
    """A bounded research metadata request failed safely."""


def _request_json(query: str, token: str = "", timeout: int = 15) -> dict:
    url = API + "?" + urlencode({"q": query, "sort": "updated", "order": "desc", "per_page": "10"})
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ResearchDiscoveryError(f"GitHub search returned HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining", "unknown")
        reset = exc.headers.get("X-RateLimit-Reset", "unknown")
        raise ResearchDiscoveryError(f"GitHub search returned HTTP {exc.code}; rate_limit_remaining={remaining}; reset_epoch={reset}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ResearchDiscoveryError(f"GitHub search failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ResearchDiscoveryError("GitHub search returned an unexpected response shape")
    return payload


def discover_repositories(queries=None, environ=None, max_per_query=5, max_queries=6, request_fn=None, now=None):
    """Search a bounded set of GitHub topics and return unreviewed candidates."""
    if isinstance(max_per_query, bool) or not isinstance(max_per_query, int) or not 1 <= max_per_query <= 10:
        raise ValueError("max_per_query must be an integer between 1 and 10")
    if isinstance(max_queries, bool) or not isinstance(max_queries, int) or not 1 <= max_queries <= 10:
        raise ValueError("max_queries must be an integer between 1 and 10")
    query_map = DEFAULT_QUERIES if queries is None else queries
    if not isinstance(query_map, dict) or not 1 <= len(query_map) <= 10:
        raise ValueError("queries must contain between 1 and 10 named queries")
    for name, query in query_map.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(query, str) or not query.strip():
            raise ValueError("query names and values must be non-empty strings")
    env = os.environ if environ is None else environ
    token = env.get("GITHUB_TOKEN", "").strip()
    fetch = request_fn or _request_json
    retrieved_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    candidates = {}
    unavailable = {}
    requests_made = 0
    query_items = list(query_map.items())[:max_queries]
    for name, query in query_items:
        try:
            payload = fetch(query, token)
            requests_made += 1
            for item in payload["items"][:max_per_query]:
                if not isinstance(item, dict):
                    continue
                full_name, html_url = item.get("full_name"), item.get("html_url")
                if not isinstance(full_name, str) or not isinstance(html_url, str):
                    continue
                license_data = item.get("license")
                license_id = license_data.get("spdx_id") if isinstance(license_data, dict) else None
                if full_name not in candidates:
                    candidates[full_name] = {
                        "full_name": full_name, "url": html_url,
                        "description": item.get("description") if isinstance(item.get("description"), str) else "",
                        "stars": item.get("stargazers_count") if type(item.get("stargazers_count")) is int else 0,
                        "language": item.get("language") if isinstance(item.get("language"), str) else None,
                        "license_spdx": license_id if isinstance(license_id, str) else None,
                        "pushed_at": item.get("pushed_at") if isinstance(item.get("pushed_at"), str) else None,
                        "archived": item.get("archived") is True,
                        "topics": item.get("topics") if isinstance(item.get("topics"), list) else [],
                        "discovered_via": [], "discovered_at": retrieved_at,
                        "review_status": "unreviewed", "execution_performed": False, "license_verified": False,
                    }
                if name not in candidates[full_name]["discovered_via"]:
                    candidates[full_name]["discovered_via"].append(name)
        except ResearchDiscoveryError as exc:
            unavailable[name] = str(exc)
    ordered = sorted(candidates.values(), key=lambda item: (item["pushed_at"] or "", item["stars"], item["full_name"]), reverse=True)
    return {
        "schema_version": 1, "discovery_only": True, "source": "github_public_repository_search",
        "retrieved_at": retrieved_at, "queries_attempted": len(query_items),
        "requests_made": requests_made, "generation_requests": 0, "code_execution": False,
        "installation_or_cloning": False, "candidate_count": len(ordered), "candidates": ordered,
        "unavailable": unavailable, "review_required": True,
    }
