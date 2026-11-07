"""Reproducible, offline benchmark for Arabic/English evidence retrieval.

This compares exact-substring lookup with local SQLite FTS5 using synthetic
fixtures and stdlib modules only. It never contacts D1, Cloudflare, a model
provider, or an external search API.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    try:
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
