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
        with _open_without_redirects(request, timeout) as response:
            if response.status != 200:
                raise ResearchDiscoveryError(f"SearXNG returned HTTP {response.status}")
            final_url = response.geturl() if callable(getattr(response, "geturl", None)) else request_url
            final = urlsplit(final_url)
            if final.scheme != "https" or final.netloc != parsed.netloc:
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
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or len(url) > 1000):
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


def discover_repositories(queries=None, environ=None, max_per_query=5, max_queries=6, request_fn=None, now=None, academic_query=None, max_academic_results=10, academic_request_fn=None, arxiv_query=None, max_arxiv_results=10, arxiv_request_fn=None, searxng_query=None, searxng_base_url=None, max_searxng_results=10, searxng_request_fn=None):
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
    if searxng_query is not None and (not isinstance(searxng_query, str) or not searxng_query.strip()):
        raise ValueError("searxng_query must be a non-empty string when supplied")
    if isinstance(max_searxng_results, bool) or not isinstance(max_searxng_results, int) or not 1 <= max_searxng_results <= 10:
        raise ValueError("max_searxng_results must be an integer between 1 and 10")
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
    searxng_candidates, searxng_unavailable, searxng_requests_made = [], None, 0
    if searxng_query:
        if not searxng_base_url:
            searxng_unavailable = "endpoint_not_configured"
        else:
            try:
                searxng_fetch = searxng_request_fn or _request_searxng
                searxng_requests_made = 1
                payload = searxng_fetch(searxng_query, searxng_base_url, max_searxng_results)
                results = payload.get("results", []) if isinstance(payload, dict) else None
                if not isinstance(results, list):
                    raise ResearchDiscoveryError("SearXNG result list is malformed")
                for item in results[:max_searxng_results]:
                    if isinstance(item, dict):
                        candidate = _normalize_searxng_result(item, retrieved_at)
                        if candidate is not None:
                            searxng_candidates.append(candidate)
            except ResearchDiscoveryError as exc:
                searxng_unavailable = str(exc)
    return {
        "schema_version": 1, "discovery_only": True, "source": "multi_source_public_research_metadata",
        "retrieved_at": retrieved_at, "queries_attempted": len(query_items),
        "requests_made": requests_made, "academic_requests_made": academic_requests_made,
        "total_requests_made": requests_made + academic_requests_made + arxiv_requests_made + searxng_requests_made,
        "academic_candidates": academic_candidates, "academic_unavailable": academic_unavailable,
        "arxiv_candidates": arxiv_candidates, "arxiv_unavailable": arxiv_unavailable,
        "arxiv_requests_made": arxiv_requests_made,
        "searxng_candidates": searxng_candidates, "searxng_unavailable": searxng_unavailable,
        "searxng_requests_made": searxng_requests_made,
        "searxng_endpoint_configured": bool(searxng_base_url),
        "generation_requests": 0, "code_execution": False,
        "installation_or_cloning": False, "candidate_count": len(ordered),
        "total_metadata_candidate_count": len(ordered) + len(academic_candidates) + len(arxiv_candidates) + len(searxng_candidates),
        "candidates": ordered,
        "unavailable": unavailable, "review_required": True,
    }
