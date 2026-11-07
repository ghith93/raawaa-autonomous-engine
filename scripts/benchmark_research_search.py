"""Reproducible, offline benchmark for Arabic/English evidence retrieval.

This compares exact-substring lookup with SQLite FTS5 AND and weighted OR/BM25
using synthetic fixtures or a bounded public-metadata discovery snapshot. It
uses stdlib modules only and never contacts D1, Cloudflare, a model provider, or
an external search API.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ARABIC_TRANSLATION = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ة": "ه",
})
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalize_text(value: str) -> str:
    """Normalize Arabic diacritics/tatweel and common letter variants."""
    value = unicodedata.normalize("NFKC", value).translate(ARABIC_TRANSLATION)
    value = "".join(
        char for char in value
        if char != "\u0640" and unicodedata.category(char) not in {"Mn", "Me"}
    )
    return value.casefold()


def query_tokens(query: str) -> list[str]:
    return TOKEN_RE.findall(normalize_text(query))


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    query: str
    expected_ids: tuple[str, ...]
    language: str = "en"


def build_fixture() -> tuple[list[dict[str, str]], list[QueryCase]]:
    """Build 144 fixed records and 24 labelled queries (12 Arabic, 12 English)."""
    specs = [
        ("ar", "محرك بحث أدلة", ["محرك", "بحث", "أدلة"], "بحث الأدلة"),
        ("ar", "فهرسة نصوص عربية", ["فهرسة", "نصوص", "عربية"], "فهرسة النص"),
        ("ar", "تطبيع لغة عربية", ["تطبيع", "لغة", "عربية"], "تطبيع اللغة"),
        ("ar", "إزالة تشكيل عربي", ["إزالة", "تشكيل", "عربي"], "إزالة التشكيل"),
        ("ar", "ترتيب نتائج بحث", ["ترتيب", "نتائج", "بحث"], "ترتيب النتائج"),
        ("ar", "استرجاع مقاطع أدلة", ["استرجاع", "مقاطع", "أدلة"], "مقاطع الأدلة"),
        ("ar", "مقارنة جودة استرجاع", ["مقارنة", "جودة", "استرجاع"], "جودة الاسترجاع"),
        ("ar", "قياس زمن استجابة", ["قياس", "زمن", "استجابة"], "زمن الاستجابة"),
        ("ar", "مصادر أبحاث مفتوحة", ["مصادر", "أبحاث", "مفتوحة"], "مصادر الأبحاث"),
        ("ar", "كشف تكرار مصادر", ["كشف", "تكرار", "مصادر"], "تكرار المصادر"),
        ("ar", "تحديث أدلة زمنية", ["تحديث", "أدلة", "زمنية"], "الأدلة الزمنية"),
        ("ar", "بحث منتجات منافسة", ["بحث", "منتجات", "منافسة"], "المنتجات المنافسة"),
        ("en", "agent task graph", ["agent", "task", "graph"], "agent graph"),
        ("en", "durable workflow state", ["durable", "workflow", "state"], "workflow state"),
        ("en", "SQLite full text search", ["SQLite", "full-text", "search"], "SQLite search"),
        ("en", "Cloudflare D1 index", ["Cloudflare", "D1", "index"], "D1 index"),
        ("en", "secure tool execution", ["secure", "tool", "execution"], "tool execution"),
        ("en", "provider quota ledger", ["provider", "quota", "ledger"], "quota ledger"),
        ("en", "zero-cost inference policy", ["zero-cost", "inference", "policy"], "zero-cost policy"),
        ("en", "research source ranking", ["research", "source", "ranking"], "source ranking"),
        ("en", "evidence citation retrieval", ["evidence", "citation", "retrieval"], "citation retrieval"),
        ("en", "idempotent task processing", ["idempotent", "task", "processing"], "task processing"),
        ("en", "mobile storefront conversion", ["mobile", "storefront", "conversion"], "storefront conversion"),
        ("en", "hair serum marketing", ["hair", "serum", "marketing"], "serum marketing"),
    ]
    documents: list[dict[str, str]] = []
    queries: list[QueryCase] = []
    base_day = date(2026, 1, 1)
    for index, (language, query, terms, topic) in enumerate(specs):
        query_id = f"q{index + 1:02d}"
        expected: list[str] = []
        for variant in range(5):
            doc_id = f"{query_id}-relevant-{variant + 1}"
            expected.append(doc_id)
            body = (
                f"المراجعة رقم {variant + 1}: {terms[2]} ثم سياق إضافي، "
                f"وتناقش {terms[0]} مع تفاصيل {terms[1]}."
                if language == "ar"
                else f"Case {variant + 1}: context about {terms[2]}, then {terms[0]}, "
                     f"with supporting detail for {terms[1]} and its evidence."
            )
            documents.append({
                "id": doc_id,
                "title": f"{topic} — record {variant + 1}",
                "body": body,
                "language": language,
                "published_at": (base_day + timedelta(days=index * 5 + variant)).isoformat(),
            })
        documents.append({
            "id": f"{query_id}-distractor",
            "title": f"Related context {query_id}",
            "body": f"{terms[0]} {terms[1]} unrelated note without the final concept.",
            "language": language,
            "published_at": (base_day + timedelta(days=index * 5 + 4)).isoformat(),
        })
        queries.append(QueryCase(query_id, query, tuple(expected)))
    return documents, queries


def _create_database(documents: list[dict[str, str]]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            language TEXT NOT NULL,
            published_at TEXT NOT NULL,
            normalized_text TEXT NOT NULL
        )
    """)
    db.executemany(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
        [
            (d["id"], d["title"], d["body"], d["language"], d["published_at"],
             normalize_text(d["title"] + " " + d["body"]))
            for d in documents
        ],
    )
    db.execute("""
        CREATE VIRTUAL TABLE evidence_fts USING fts5(
            id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2'
        )
    """)
    db.executemany(
        "INSERT INTO evidence_fts(id, title, body) VALUES (?, ?, ?)",
        [(d["id"], normalize_text(d["title"]), normalize_text(d["body"])) for d in documents],
    )
    return db


def _fts_match_expression(query: str) -> str:
    tokens = query_tokens(query)
    if not tokens:
        raise ValueError("query contains no searchable tokens")
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def search_like(db: sqlite3.Connection, query: str, limit: int = 20) -> list[str]:
    phrase = normalize_text(query).strip()
    rows = db.execute(
        "SELECT id FROM documents WHERE normalized_text LIKE ? ORDER BY published_at DESC, id LIMIT ?",
        ("%" + phrase + "%", limit),
    ).fetchall()
    return [row["id"] for row in rows]


def search_fts(db: sqlite3.Connection, query: str, limit: int = 20) -> list[str]:
    expression = _fts_match_expression(query)
    rows = db.execute(
        "SELECT id FROM evidence_fts WHERE evidence_fts MATCH ? ORDER BY bm25(evidence_fts), id LIMIT ?",
        (expression, limit),
    ).fetchall()
    return [row["id"] for row in rows]


def search_fts_or(db: sqlite3.Connection, query: str, limit: int = 20) -> list[str]:
    """Recall-oriented FTS5 search: match any token, rank title above metadata."""
    tokens = query_tokens(query)
    if not tokens:
        raise ValueError("query contains no searchable tokens")
    expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    rows = db.execute(
        "SELECT id FROM evidence_fts WHERE evidence_fts MATCH ? "
        "ORDER BY bm25(evidence_fts, 0.0, 3.0, 1.0), id LIMIT ?",
        (expression, limit),
    ).fetchall()
    return [row["id"] for row in rows]


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 3)


def evaluate(search_fn, db: sqlite3.Connection, queries: list[QueryCase]) -> dict[str, Any]:
    precisions: list[float] = []
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    latencies_ms: list[float] = []
    for case in queries:
        started = time.perf_counter_ns()
        found = search_fn(db, case.query, 20)
        latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        relevant = set(case.expected_ids)
        top10 = found[:10]
        hits10 = [item for item in top10 if item in relevant]
        hits20 = sum(item in relevant for item in found)
        precisions.append(len(hits10) / 10)
        recalls.append(hits20 / len(relevant))
        rank = next((i + 1 for i, item in enumerate(top10) if item in relevant), None)
        reciprocal_ranks.append(1 / rank if rank else 0.0)
    return {
        "queries": len(queries),
        "precision_at_10": round(statistics.mean(precisions), 4),
        "recall_at_20": round(statistics.mean(recalls), 4),
        "mrr_at_10": round(statistics.mean(reciprocal_ranks), 4),
        "latency_ms_p50": _percentile(latencies_ms, 0.50),
        "latency_ms_p95": _percentile(latencies_ms, 0.95),
    }


def run_benchmark() -> dict[str, Any]:
    documents, queries = build_fixture()
    db = _create_database(documents)
    try:
        like = evaluate(search_like, db, queries)
        fts = evaluate(search_fts, db, queries)
        return {
            "benchmark": "raawaa_research_search_offline_v1",
            "network_calls": 0,
            "remote_writes": 0,
            "provider_calls": 0,
            "database": "in_memory_sqlite",
            "fts5": "available",
            "fixture": {
                "documents": len(documents),
                "queries": len(queries),
                "arabic_queries": sum(1 for q in queries if any("\u0600" <= c <= "\u06ff" for c in q.query)),
                "english_queries": sum(1 for q in queries if not any("\u0600" <= c <= "\u06ff" for c in q.query)),
                "relevant_documents_per_query": 5,
            },
            "methods": {"substring_like": like, "sqlite_fts5": fts},
            "limitations": [
                "Synthetic fixture only; not a representative production corpus.",
                "Local SQLite FTS5 result does not prove Cloudflare D1 runtime support or performance.",
                "No semantic embeddings, synonym expansion, page extraction, or production ranking.",
                "The benchmark measures retrieval against fixed labels, not source truth or research quality.",
            ],
        }
    finally:
        db.close()


def build_real_corpus_from_data(snapshot: dict[str, Any], qrels: dict[str, Any], *,
                                include_arabic_aliases: bool = True):
    """Transform a bounded discovery snapshot and editorial qrels into offline search cases."""
    if not isinstance(snapshot, dict) or not isinstance(qrels, dict):
        raise ValueError("snapshot and qrels must be JSON objects")
    documents: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_document(doc_id: str, source: str, title: str, body: str, published_at: str = "") -> None:
        if not doc_id or doc_id in seen or not isinstance(title, str) or not title.strip():
            return
        seen.add(doc_id)
        documents.append({
            "id": doc_id, "title": title.strip(), "body": body.strip(),
            "language": source,
            "published_at": published_at if isinstance(published_at, str) and published_at else "1970-01-01",
        })

    for item in snapshot.get("candidates", []) if isinstance(snapshot.get("candidates"), list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("full_name")
        if not isinstance(name, str) or not name.strip():
            continue
        topics = item.get("topics") if isinstance(item.get("topics"), list) else []
        discovered = item.get("discovered_via") if isinstance(item.get("discovered_via"), list) else []
        body = " ".join(str(x) for x in [
            item.get("description", ""), " ".join(str(x) for x in topics),
            " ".join(str(x) for x in discovered), item.get("language", ""),
            item.get("license_spdx", ""),
        ] if x)
        add_document("github:" + name.lower(), "github", name, body,
                     item.get("pushed_at") or item.get("discovered_at") or "")

    for item in snapshot.get("academic_candidates", []) if isinstance(snapshot.get("academic_candidates"), list) else []:
        if not isinstance(item, dict):
            continue
        doi, title = item.get("doi"), item.get("title")
        if not isinstance(doi, str) or not doi.strip() or not isinstance(title, str):
            continue
        authors = item.get("authors") if isinstance(item.get("authors"), list) else []
        body = " ".join(str(x) for x in [
            item.get("container_title", ""), " ".join(str(x) for x in authors),
            item.get("work_type", ""), item.get("citation_count", ""),
        ] if x)
        add_document("crossref:" + doi.lower(), "crossref", title, body, str(item.get("published") or ""))

    for item in snapshot.get("arxiv_candidates", []) if isinstance(snapshot.get("arxiv_candidates"), list) else []:
        if not isinstance(item, dict):
            continue
        arxiv_id, title = item.get("arxiv_id"), item.get("title")
        if not isinstance(arxiv_id, str) or not arxiv_id.strip() or not isinstance(title, str):
            continue
        categories = item.get("categories") if isinstance(item.get("categories"), list) else []
        authors = item.get("authors") if isinstance(item.get("authors"), list) else []
        body = " ".join(str(x) for x in [
            " ".join(str(x) for x in categories), item.get("primary_category", ""),
            " ".join(str(x) for x in authors),
        ] if x)
        add_document("arxiv:" + arxiv_id.lower(), "arxiv", title, body, str(item.get("published") or ""))

    for item in snapshot.get("searxng_candidates", []) if isinstance(snapshot.get("searxng_candidates"), list) else []:
        if not isinstance(item, dict):
            continue
        url, title = item.get("url"), item.get("title")
        if not isinstance(url, str) or not url.startswith("https://") or not isinstance(title, str):
            continue
        body = " ".join(str(x) for x in [item.get("content", ""), item.get("engine", ""), item.get("category", "")] if x)
        add_document("searxng:" + url.lower(), "searxng", title, body, str(item.get("publishedDate") or ""))

    aliases, query_rows = qrels.get("arabic_aliases", {}), qrels.get("queries", [])
    if not isinstance(aliases, dict) or not isinstance(query_rows, list):
        raise ValueError("qrels requires arabic_aliases object and queries list")
    expected_by_capability: dict[str, set[str]] = {}
    for query in query_rows:
        if not isinstance(query, dict):
            continue
        capability, expected = query.get("capability"), query.get("expected_ids")
        if isinstance(capability, str) and isinstance(expected, list):
            expected_by_capability.setdefault(capability, set()).update(x for x in expected if isinstance(x, str))
    if include_arabic_aliases:
        for document in documents:
            matching = [capability for capability, ids in expected_by_capability.items() if document["id"] in ids]
            document["body"] += " " + " ".join(
                term for capability in matching
                if isinstance((term := aliases.get(capability, "")), str) and term
            )

    present = {document["id"] for document in documents}
    cases: list[QueryCase] = []
    coverage: list[dict[str, Any]] = []
    for query in query_rows:
        if not isinstance(query, dict):
            raise ValueError("qrels query must be an object")
        query_id, text_value, language, expected = (
            query.get("query_id"), query.get("query"), query.get("language"), query.get("expected_ids")
        )
        if not all(isinstance(value, str) and value.strip() for value in (query_id, text_value, language)):
            raise ValueError("qrels query identity, text and language are required")
        if language not in {"ar", "en"} or not isinstance(expected, list) or not expected:
            raise ValueError("qrels language or expected_ids are invalid")
        expected_ids = tuple(dict.fromkeys(x for x in expected if isinstance(x, str) and x in present))
        coverage.append({
            "query_id": query_id, "language": language, "expected_count": len(expected),
            "present_count": len(expected_ids), "coverage": round(len(expected_ids) / len(expected), 4),
            "evaluated": bool(expected_ids),
        })
        if expected_ids:
            cases.append(QueryCase(query_id, text_value, expected_ids, language))
    if not documents or not cases:
        raise ValueError("real corpus snapshot has no documents or no covered qrels")
    return documents, cases, coverage


def evaluate_by_language(search_fn, db: sqlite3.Connection,
                         queries: list[QueryCase]) -> dict[str, Any]:
    """Return overall and per-language metrics for the same judgement set."""
    report: dict[str, Any] = {"all": evaluate(search_fn, db, queries)}
    for language in ("en", "ar"):
        subset = [query for query in queries if query.language == language]
        if subset:
            report[language] = evaluate(search_fn, db, subset)
    return report


def run_real_corpus_benchmark(snapshot: dict[str, Any], qrels: dict[str, Any]) -> dict[str, Any]:
    """Benchmark source-native and alias-assisted retrieval; no network or remote writes."""
    native_documents, queries, coverage = build_real_corpus_from_data(
        snapshot, qrels, include_arabic_aliases=False
    )
    alias_documents, _, _ = build_real_corpus_from_data(
        snapshot, qrels, include_arabic_aliases=True
    )

    def track(documents: list[dict[str, str]]) -> dict[str, Any]:
        db = _create_database(documents)
        try:
            return {
                "methods": {
                    "substring_like": evaluate_by_language(search_like, db, queries),
                    "sqlite_fts5_and": evaluate_by_language(search_fts, db, queries),
                    "sqlite_fts5_or_bm25": evaluate_by_language(search_fts_or, db, queries),
                }
            }
        finally:
            db.close()

    source_counts: dict[str, int] = {}
    for document in native_documents:
        source_counts[document["language"]] = source_counts.get(document["language"], 0) + 1
    expected_total = sum(row["expected_count"] for row in coverage)
    present_total = sum(row["present_count"] for row in coverage)
    label_coverage = round(present_total / expected_total, 4) if expected_total else 0.0
    evaluated = len(queries)
    return {
        "benchmark": "raawaa_research_search_real_corpus_v2",
        "network_calls": 0, "remote_writes": 0, "provider_calls": 0,
        "database": "in_memory_sqlite", "fts5": "available",
        "snapshot": {
            "retrieved_at": snapshot.get("retrieved_at"), "source_counts": source_counts,
            "documents": len(native_documents), "qrels_queries": len(coverage), "evaluated_queries": evaluated,
            "arabic_queries": sum(1 for row in coverage if row["language"] == "ar" and row["evaluated"]),
            "english_queries": sum(1 for row in coverage if row["language"] == "en" and row["evaluated"]),
            "label_coverage": label_coverage,
            "benchmark_status": "sufficient_coverage" if evaluated >= 10 and expected_total and label_coverage >= 0.5 else "insufficient_coverage",
            "coverage_by_query": coverage,
        },
        "tracks": {
            "source_native": {
                "description": "Indexes only title and metadata actually present in the public discovery snapshot; no editorial aliases.",
                **track(native_documents),
            },
            "alias_assisted": {
                "description": "Adds editorial Arabic aliases to qrels-labelled candidates; label-conditioned controlled experiment, not native Arabic retrieval.",
                **track(alias_documents),
            },
        },
        "limitations": [
            "One bounded public-metadata discovery snapshot; results vary with source availability.",
            "Relevance labels are manually curated and are not exhaustive ground truth.",
            "The alias-assisted track is label-conditioned and must not be presented as native Arabic retrieval.",
            "The source-native track measures only source-provided metadata; it does not perform automatic translation.",
            "Neither track proves Cloudflare D1 FTS5 parity or production latency.",
            "No full text, semantic embeddings, crawling, model calls or source adoption are included.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--real-corpus", action="store_true", help="benchmark a public research discovery snapshot")
    parser.add_argument("--input", default="research-candidates.json", help="discovery snapshot JSON path")
    parser.add_argument("--qrels", default="config/research-search-benchmark-qrels.json", help="editorial relevance labels JSON path")
    args = parser.parse_args()
    try:
        if args.real_corpus:
            snapshot = json.loads(Path(args.input).read_text(encoding="utf-8"))
            qrels_data = json.loads(Path(args.qrels).read_text(encoding="utf-8"))
            result = run_real_corpus_benchmark(snapshot, qrels_data)
        else:
            result = run_benchmark()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            result = {
                "benchmark": "raawaa_research_search_offline_v1",
                "fts5": "unavailable",
                "reason": "sqlite_fts5_not_compiled",
                "network_calls": 0,
                "remote_writes": 0,
                "provider_calls": 0,
            }
        else:
            raise
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) if args.json else
          "Research search offline benchmark: " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
