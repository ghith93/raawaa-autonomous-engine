"""Bounded, read-only GitHub repository discovery for proactive research.

This module retrieves public repository metadata only. It never clones, imports,
 executes, installs, or modifies discovered projects.
"""
from __future__ import annotations

import json
import os
import ipaddress
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

API = "https://api.github.com/search/repositories"
MAX_RESPONSE_BYTES = 1048576
CROSSREF_API = "https://api.crossref.org/works"
ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
USER_AGENT = "RaawaaAutonomousEngine-Research/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
DEFAULT_QUERIES = {
    "agent_frameworks": "agent framework orchestration topic:agents archived:false",
    "coding_agents": "open source coding agent archived:false",
    "agent_memory": "agent memory long term memory framework archived:false",
    "mcp_ecosystem": "MCP server tools model context protocol archived:false",
    "model_routing": "LLM model router inference gateway quota archived:false",
    "durable_execution": "durable workflow orchestration agent execution archived:false",
    "agent_skills_security": "agent skill scanner archived:false",
    "agent_evaluation": "agent evaluation benchmark harness archived:false",
    "skill_benchmarks": "skillsbench archived:false",
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
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ResearchDiscoveryError("Research response exceeded the 1 MiB safety limit")
            payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining", "unknown")
        reset = exc.headers.get("X-RateLimit-Reset", "unknown")
        raise ResearchDiscoveryError(f"GitHub search returned HTTP {exc.code}; rate_limit_remaining={remaining}; reset_epoch={reset}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResearchDiscoveryError(f"GitHub search failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ResearchDiscoveryError("GitHub search returned an unexpected response shape")
    return payload


def _request_crossref(query: str, max_results: int = 10, timeout: int = 20) -> dict:
    """Retrieve public Crossref scholarly metadata; one bounded request per run."""
    params = {"query.bibliographic": query, "rows": str(max_results),
              "select": "DOI,title,author,published,URL,container-title,is-referenced-by-count,license,type"}
    request = Request(CROSSREF_API + "?" + urlencode(params),
                      headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ResearchDiscoveryError(f"Crossref returned HTTP {response.status}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ResearchDiscoveryError("Research response exceeded the 1 MiB safety limit")
            payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        limit = exc.headers.get("X-Rate-Limit-Limit", "unknown") if exc.headers else "unknown"
        interval = exc.headers.get("X-Rate-Limit-Interval", "unknown") if exc.headers else "unknown"
        raise ResearchDiscoveryError(f"Crossref returned HTTP {exc.code}; rate_limit={limit}; interval={interval}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResearchDiscoveryError(f"Crossref request failed: {type(exc).__name__}") from exc
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("items"), list):
        raise ResearchDiscoveryError("Crossref returned an unexpected response shape")
    return message


def _request_arxiv(query: str, max_results: int = 10, timeout: int = 20) -> dict:
    """Retrieve one bounded arXiv Atom metadata page; never fetch paper full text."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("arxiv query must be a non-empty string")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 10:
        raise ValueError("arxiv max_results must be an integer between 1 and 10")
    params = {"search_query": query, "start": "0", "max_results": str(max_results),
              "sortBy": "submittedDate", "sortOrder": "descending"}
    request = Request(ARXIV_API + "?" + urlencode(params),
                      headers={"Accept": "application/atom+xml", "User-Agent": USER_AGENT},
                      method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ResearchDiscoveryError(f"arXiv returned HTTP {response.status}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ResearchDiscoveryError("arXiv response exceeded the 1 MiB safety limit")
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ResearchDiscoveryError("arXiv XML declarations with DTD/entities are not accepted")
            root = ET.fromstring(raw)
    except HTTPError as exc:
        raise ResearchDiscoveryError(f"arXiv returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ResearchDiscoveryError(f"arXiv request failed: {type(exc).__name__}") from exc
    except ET.ParseError as exc:
        raise ResearchDiscoveryError(f"arXiv returned malformed Atom XML: {type(exc).__name__}") from exc
    if root.tag != f"{{{ATOM_NS}}}feed":
        raise ResearchDiscoveryError("arXiv returned an unexpected Atom response shape")
    return {"entries": root.findall(f"{{{ATOM_NS}}}entry")}


def _normalize_arxiv_entry(entry: ET.Element, retrieved_at: str) -> dict | None:
    """Keep bounded bibliographic metadata only; abstracts/full text stay un-ingested."""
    def value(tag: str) -> str | None:
        node = entry.find(f"{{{ATOM_NS}}}{tag}")
        text = " ".join((node.text or "").split()) if node is not None else ""
        return text[:1000] if text else None

    paper_id = value("id")
    title = value("title")
    if not paper_id or not title or not paper_id.startswith(("https://arxiv.org/abs/", "http://arxiv.org/abs/")):
        return None
    authors = []
    for author in entry.findall(f"{{{ATOM_NS}}}author")[:5]:
        name = author.find(f"{{{ATOM_NS}}}name")
        if name is not None and isinstance(name.text, str) and name.text.strip():
            authors.append(" ".join(name.text.split())[:160])
    categories = []
    for category in entry.findall(f"{{{ATOM_NS}}}category")[:8]:
        term = category.attrib.get("term")
        if isinstance(term, str) and term:
            categories.append(term[:80])
    primary = entry.find(f"{{{ARXIV_NS}}}primary_category")
    primary_category = primary.attrib.get("term") if primary is not None else None
    doi_node = entry.find(f"{{{ARXIV_NS}}}doi")
    doi = " ".join((doi_node.text or "").split())[:160] if doi_node is not None and doi_node.text else None
    return {
        "arxiv_id": paper_id.rsplit("/", 1)[-1],
        "title": title,
        "url": paper_id,
        "authors": authors,
        "published": value("published"),
        "updated": value("updated"),
        "categories": categories,
        "primary_category": primary_category[:80] if isinstance(primary_category, str) else None,
        "doi": doi,
        "source": "arxiv",
        "discovered_at": retrieved_at,
        "review_status": "unreviewed",
        "license_verified": False,
        "abstract_retrieved": False,
        "full_text_retrieved": False,
    }


def _normalize_crossref_item(item: dict, retrieved_at: str) -> dict | None:
    doi, titles = item.get("DOI"), item.get("title")
    if not isinstance(doi, str) or not doi or not isinstance(titles, list) or not titles:
        return None
    title = titles[0] if isinstance(titles[0], str) else ""
    if not title:
        return None
    authors = []
    for author in item.get("author", [])[:5] if isinstance(item.get("author"), list) else []:
        if isinstance(author, dict):
            name = " ".join(part for part in (author.get("given"), author.get("family")) if isinstance(part, str))
            if name:
                authors.append(name)
    published = item.get("published")
    date_parts = published.get("date-parts") if isinstance(published, dict) else None
    date_label = "-".join(str(part) for part in date_parts[0][:3]) if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) else None
    licenses = item.get("license")
    license_urls = [entry["URL"] for entry in licenses if isinstance(entry, dict) and isinstance(entry.get("URL"), str)] if isinstance(licenses, list) else []
    containers = item.get("container-title")
    journal = containers[0] if isinstance(containers, list) and containers and isinstance(containers[0], str) else None
    citations = item.get("is-referenced-by-count")
    return {"doi": doi, "title": title, "url": item.get("URL") if isinstance(item.get("URL"), str) else "https://doi.org/" + doi,
            "authors": authors, "published": date_label, "container_title": journal,
            "citation_count": citations if type(citations) is int and citations >= 0 else 0,
            "license_urls": license_urls, "work_type": item.get("type") if isinstance(item.get("type"), str) else None,
            "source": "crossref", "discovered_at": retrieved_at, "review_status": "unreviewed",
            "license_verified": False, "abstract_retrieved": False}

def _validate_searxng_base_url(base_url: str) -> str:
    """Accept only an operator-configured public HTTPS SearXNG origin/path."""
    if not isinstance(base_url, str) or not base_url.strip() or len(base_url) > 500:
        raise ResearchDiscoveryError("SearXNG endpoint is missing or malformed")
    parsed = urlsplit(base_url.strip())
    host = parsed.hostname
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ResearchDiscoveryError("SearXNG endpoint must be HTTPS without credentials, query, or fragment")
    normalized_host = host.lower().rstrip(".")
    if normalized_host in {"localhost", "localhost.localdomain"} or normalized_host.endswith((".local", ".internal")):
        raise ResearchDiscoveryError("SearXNG endpoint must not target a local hostname")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ResearchDiscoveryError("SearXNG endpoint must not target a private or reserved IP")
    return base_url.strip().rstrip("/")


def _request_searxng(query: str, base_url: str, max_results: int = 10, timeout: int = 15) -> dict:
    """Request one JSON page from an operator-configured SearXNG instance."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("SearXNG query must be a non-empty string")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 10:
        raise ValueError("SearXNG max_results must be an integer between 1 and 10")
    endpoint = _validate_searxng_base_url(base_url)
    parsed = urlsplit(endpoint)
    params = {"q": query, "format": "json", "pageno": "1", "safesearch": "1"}
    request_url = endpoint + "/search?" + urlencode(params)
    request = Request(request_url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ResearchDiscoveryError(f"SearXNG returned HTTP {response.status}")
            final_url = response.geturl() if callable(getattr(response, "geturl", None)) else request_url
            final = urlsplit(final_url)
            if final.scheme != "https" or final.hostname != parsed.hostname:
                raise ResearchDiscoveryError("SearXNG redirected outside the configured HTTPS host")
            content_type = response.headers.get("Content-Type", "") if getattr(response, "headers", None) else ""
            if content_type and "json" not in content_type.lower():
                raise ResearchDiscoveryError("SearXNG did not return JSON; enable search.formats=json on the instance")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ResearchDiscoveryError("SearXNG response exceeded the 1 MiB safety limit")
            payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        raise ResearchDiscoveryError(f"SearXNG returned HTTP {exc.code}; verify JSON format is enabled") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResearchDiscoveryError(f"SearXNG request failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ResearchDiscoveryError("SearXNG returned an unexpected JSON response shape")
    return payload


def _normalize_searxng_result(item: dict, retrieved_at: str) -> dict | None:
    title, url = item.get("title"), item.get("url")
    if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    engines = item.get("engines", [])
    if not isinstance(engines, list):
        engines = []
    score = item.get("score")
    return {
        "source": "searxng",
        "source_key": "searxng:" + url[:900],
        "title": " ".join(title.split())[:240],
        "url": url[:1000],
        "snippet": " ".join(item.get("content", "").split())[:500] if isinstance(item.get("content"), str) else "",
        "engines": [engine[:80] for engine in engines[:5] if isinstance(engine, str)],
        "category": item.get("category")[:80] if isinstance(item.get("category"), str) else None,
        "score": score if type(score) in (int, float) and math.isfinite(score) and score >= 0 else None,
        "discovered_at": retrieved_at,
        "review_status": "unreviewed",
        "license_verified": False,
        "automatic_adoption": False,
    }


def discover_repositories(queries=None, environ=None, max_per_query=5, max_queries=6, request_fn=None, now=None, academic_query=None, max_academic_results=10, academic_request_fn=None, arxiv_query=None, max_arxiv_results=10, arxiv_request_fn=None):
    """Search a bounded set of GitHub topics and return unreviewed candidates."""
    if isinstance(max_per_query, bool) or not isinstance(max_per_query, int) or not 1 <= max_per_query <= 10:
        raise ValueError("max_per_query must be an integer between 1 and 10")
    if isinstance(max_queries, bool) or not isinstance(max_queries, int) or not 1 <= max_queries <= 10:
        raise ValueError("max_queries must be an integer between 1 and 10")
    if isinstance(max_academic_results, bool) or not isinstance(max_academic_results, int) or not 1 <= max_academic_results <= 10:
        raise ValueError("max_academic_results must be an integer between 1 and 10")
    if academic_query is not None and (not isinstance(academic_query, str) or not academic_query.strip()):
        raise ValueError("academic_query must be a non-empty string when supplied")
    if arxiv_query is not None and (not isinstance(arxiv_query, str) or not arxiv_query.strip()):
        raise ValueError("arxiv_query must be a non-empty string when supplied")
    if isinstance(max_arxiv_results, bool) or not isinstance(max_arxiv_results, int) or not 1 <= max_arxiv_results <= 10:
        raise ValueError("max_arxiv_results must be an integer between 1 and 10")
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
            # Count attempted requests, including failures, in run telemetry.
            requests_made += 1
            payload = fetch(query, token)
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
    academic_candidates, academic_unavailable, academic_requests_made = [], None, 0
    if academic_query:
        try:
            academic_fetch = academic_request_fn or _request_crossref
            # Count attempted requests, including failures, in run telemetry.
            academic_requests_made = 1
            message = academic_fetch(academic_query, max_academic_results)
            items = message.get("items", []) if isinstance(message, dict) else []
            if not isinstance(items, list):
                raise ResearchDiscoveryError("Crossref result items are malformed")
            for item in items[:max_academic_results]:
                if isinstance(item, dict):
                    candidate = _normalize_crossref_item(item, retrieved_at)
                    if candidate is not None:
                        academic_candidates.append(candidate)
            academic_candidates.sort(key=lambda item: (item["published"] or "", item["citation_count"]), reverse=True)
        except ResearchDiscoveryError as exc:
            academic_unavailable = str(exc)
    arxiv_candidates, arxiv_unavailable, arxiv_requests_made = [], None, 0
    if arxiv_query:
        try:
            arxiv_fetch = arxiv_request_fn or _request_arxiv
            # One bounded request per daily run; upstream guidance asks clients
            # to leave at least three seconds between consecutive requests.
            arxiv_requests_made = 1
            payload = arxiv_fetch(arxiv_query, max_arxiv_results)
            entries = payload.get("entries", []) if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                raise ResearchDiscoveryError("arXiv result entries are malformed")
            for entry in entries[:max_arxiv_results]:
                if isinstance(entry, ET.Element):
                    candidate = _normalize_arxiv_entry(entry, retrieved_at)
                    if candidate is not None:
                        arxiv_candidates.append(candidate)
                elif isinstance(entry, dict):
                    allowed = {"arxiv_id", "title", "url", "authors", "published", "updated",
                               "categories", "primary_category", "doi"}
                    candidate = {key: entry[key] for key in allowed if key in entry}
                    if all(isinstance(candidate.get(key), str) and candidate[key].strip()
                           for key in ("arxiv_id", "title", "url")):
                        arxiv_candidates.append({
                            **candidate, "source": "arxiv", "discovered_at": retrieved_at,
                            "review_status": "unreviewed", "license_verified": False,
                            "abstract_retrieved": False, "full_text_retrieved": False,
                        })
            arxiv_candidates.sort(key=lambda item: (item.get("published") or "", item["title"]), reverse=True)
        except ResearchDiscoveryError as exc:
            arxiv_unavailable = str(exc)
    return {
        "schema_version": 1, "discovery_only": True, "source": "multi_source_public_research_metadata",
        "retrieved_at": retrieved_at, "queries_attempted": len(query_items),
        "requests_made": requests_made, "academic_requests_made": academic_requests_made,
        "total_requests_made": requests_made + academic_requests_made + arxiv_requests_made,
        "academic_candidates": academic_candidates, "academic_unavailable": academic_unavailable,
        "arxiv_candidates": arxiv_candidates, "arxiv_unavailable": arxiv_unavailable,
        "arxiv_requests_made": arxiv_requests_made,
        "generation_requests": 0, "code_execution": False,
        "installation_or_cloning": False, "candidate_count": len(ordered),
        "total_metadata_candidate_count": len(ordered) + len(academic_candidates) + len(arxiv_candidates),
        "candidates": ordered,
        "unavailable": unavailable, "review_required": True,
    }
