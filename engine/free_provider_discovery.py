"""Explicit, read-only discovery of free-tier model catalogs.

No generation requests are made here. A user must invoke the CLI command deliberately.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DiscoveryError(RuntimeError):
    pass


PROVIDERS = {
    "gemini": {
        "env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "kind": "gemini",
    },
    "groq": {
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/models",
        "kind": "openai",
    },
    "cerebras": {
        "env": "CEREBRAS_API_KEY",
        "url": "https://api.cerebras.ai/v1/models",
        "kind": "openai",
    },
}


def _get_json(url: str, headers: dict[str, str], timeout: int = 15) -> dict:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise DiscoveryError(f"Provider returned HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise DiscoveryError(f"Provider returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"Model discovery failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise DiscoveryError("Provider returned an unexpected response shape")
    return payload


def discover(provider: str, environ: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Discover model identifiers; never infer that every returned model is free."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDERS:
        raise DiscoveryError(f"Unsupported provider: {provider}")
    spec = PROVIDERS[provider]
    key = env.get(spec["env"], "").strip()
    if not key:
        raise DiscoveryError(f"Missing environment variable: {spec['env']}")

    headers = {"Accept": "application/json"}
    url = spec["url"]
    if provider == "gemini":
        # API keys in URLs are commonly captured by proxies and access logs.
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"

    payload = _get_json(url, headers)
    raw = payload.get("models" if spec["kind"] == "gemini" else "data", [])
    if not isinstance(raw, list):
        raise DiscoveryError("Provider model list is malformed")

    models = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name") if provider == "gemini" else item.get("id")
        if isinstance(name, str) and name:
            models.append({"provider": provider, "model": name,
                           "free_tier_verified": False,
                           "note": "Catalog presence does not establish free-tier eligibility."})
    return sorted(models, key=lambda item: item["model"])


def discover_all(environ: dict[str, str] | None = None) -> dict:
    results, errors = {}, {}
    for provider in PROVIDERS:
        try:
            results[provider] = discover(provider, environ)
        except DiscoveryError as exc:
            errors[provider] = str(exc)
    return {"discovery_only": True, "generation_requests": 0,
            "models": results, "unavailable": errors}
