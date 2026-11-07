"""Deterministic offline audit helpers for research discovery snapshots.

No network access, source adoption, lifecycle mutation, or persistence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {"fbclid", "gclid"}
_STABLE_METADATA_FIELDS = (
    "source", "full_name", "doi", "arxiv_id", "url", "title", "description",
    "language", "license_spdx", "license_urls", "topics", "authors",
)


def canonical_github_identity(value: str) -> str | None:
    """Return owner/repository identity for a GitHub URL or owner/repo string."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if "://" in candidate:
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https"} or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
            return None
        path = parsed.path
    else:
        path = candidate
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repository = parts[0].casefold(), parts[1].casefold()
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository or owner in {".", ".."} or repository in {".", ".."}:
        return None
    return owner + "/" + repository


def canonicalize_url(value: str) -> str | None:
    """Normalize URL identity while preserving all non-tracking query parameters."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    host = host.casefold().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    netloc = host + ((":" + str(port)) if port is not None else "")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    if host in {"doi.org", "dx.doi.org"}:
        netloc = "doi.org"
        path = path.casefold()
    query = [
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.casefold().startswith("utm_") or key.casefold() in _TRACKING_KEYS)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def stable_metadata_fingerprint(record: dict) -> str:
    """Hash stable metadata only; discovery timestamps/provenance are excluded."""
    stable = {field: record[field] for field in _STABLE_METADATA_FIELDS if field in record}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def classify_expected_ids(
    expected_ids: list[str],
    snapshot_ids: set[str],
    retrieved_ids: set[str],
) -> list[dict[str, str]]:
    """Classify each expected ID without guessing whether an editorial label is wrong."""
    output = []
    for doc_id in dict.fromkeys(item for item in expected_ids if isinstance(item, str)):
        if doc_id not in snapshot_ids:
            status = "source_snapshot_missing"
        elif doc_id in retrieved_ids:
            status = "retrieved"
        else:
            status = "ranking_miss"
        output.append({
            "document_id": doc_id,
            "status": status,
            "label_review": "human_review_required",
        })
    return output


def audit_qrels_retrieval(snapshot: dict, qrels: dict) -> dict:
    """Classify qrels IDs as source misses or ranking misses on both retrieval tracks."""
    from scripts.benchmark_research_search import (
        _create_database,
        build_real_corpus_from_data,
        build_real_corpus_source_native_from_data,
        search_fts_or,
    )

    alias_docs, alias_cases, _ = build_real_corpus_from_data(snapshot, qrels)
    native_docs, native_cases, _ = build_real_corpus_source_native_from_data(snapshot, qrels)

    def track(documents, cases):
        db = _create_database(documents)
        try:
            retrieved = {case.query_id: set(search_fts_or(db, case.query, 20)) for case in cases}
        finally:
            db.close()
        snapshot_ids = {doc["id"] for doc in documents}
        case_by_id = {case.query_id: case for case in cases}
        rows = []
        for query in qrels.get("queries", []):
            if not isinstance(query, dict):
                continue
            query_id = query.get("query_id")
            if not isinstance(query_id, str):
                continue
            expected = query.get("expected_ids", [])
            if not isinstance(expected, list):
                expected = []
            rows.append({
                "query_id": query_id,
                "language": query.get("language"),
                "capability": query.get("capability"),
                "expected": classify_expected_ids(expected, snapshot_ids, retrieved.get(query_id, set())),
            })
        counts = {"source_snapshot_missing": 0, "ranking_miss": 0, "retrieved": 0}
        for row in rows:
            for item in row["expected"]:
                counts[item["status"]] += 1
        return {"counts": counts, "queries": rows}

    return {
        "audit": "raawaa_research_qrels_triage_v1",
        "network_calls": 0,
        "provider_calls": 0,
        "remote_writes": 0,
        "snapshot_retrieved_at": snapshot.get("retrieved_at"),
        "snapshot_documents": len(alias_docs),
        "alias_assisted": track(alias_docs, alias_cases),
        "source_native": track(native_docs, native_cases),
        "label_policy": "No label is automatically classified as incorrect; every expected ID remains subject to human label review.",
        "limitations": [
            "A source_snapshot_missing classification means absent from this fixed snapshot, not absent from the upstream source.",
            "A ranking_miss means present in the indexed snapshot but absent from top-20 FTS5 OR/BM25 results.",
            "Alias-assisted retrieval includes editorial qrels annotations and is not native Arabic search.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="research-candidates.json")
    parser.add_argument("--qrels", default="config/research-search-benchmark-qrels.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    snapshot = json.loads(Path(args.input).read_text(encoding="utf-8"))
    qrels = json.loads(Path(args.qrels).read_text(encoding="utf-8"))
    report = audit_qrels_retrieval(snapshot, qrels)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) if args.json else
          "Research qrels triage: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
