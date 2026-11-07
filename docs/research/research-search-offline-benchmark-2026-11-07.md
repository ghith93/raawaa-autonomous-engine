# Offline research-search benchmark — 2026-11-07

## Status

Implemented and locally testable: a deterministic benchmark fixture and two retrieval baselines.
Not implemented: a production search API, document fetch/extraction, D1 schema changes, D1 FTS deployment, or automatic adoption of research candidates.

## Why this is the next smallest step

The existing discovery pipeline already gathers bounded metadata from GitHub, Crossref and arXiv and stores reviewable candidates. The missing capability is evidence retrieval, not another crawler or source registry. This benchmark deliberately adds no service, queue, database, model, or external dependency.

## Reproducible experiment

Run:

    python scripts/benchmark_research_search.py --json
    python -m unittest tests.test_research_search_benchmark -v

The fixture is generated deterministically in memory: 144 short synthetic Arabic/English records, 24 labelled queries (12 per language), and five relevant records per query. Query terms are deliberately scattered/reordered in relevant documents so the baseline exact-substring lookup and token-aware FTS retrieval can be compared.

Methods:
- substring_like: normalized exact phrase substring, ordered by publication date.
- sqlite_fts5: token AND retrieval with BM25 ordering.

Metrics:
- Precision@10
- Recall@20
- MRR@10
- p50/p95 query latency in milliseconds

Arabic normalization applies Unicode NFKC, removes combining marks and tatweel, normalizes alef variants, and maps alef maqsura/ta marbuta consistently. The fixture includes diacritics/letter variants in unit tests.


## Observed exact-head CI result

Engine CI run 42322290258 on commit 5f136be708cb33abc9fbf0cf4abb3d318f394ff0 reported:

| Method | P@10 | R@20 | MRR@10 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|
| Normalized substring LIKE | 0.0000 | 0.0000 | 0.0000 | 0.061 | 0.095 |
| SQLite FTS5 + BM25 | 0.5000 | 1.0000 | 1.0000 | 0.035 | 0.056 |

P@10 is 0.5 because the fixture has five relevant documents per query and the metric denominator is ten; all five relevant records were retrieved within the top 20 and the first relevant result ranked first. These are local in-memory measurements on a tiny synthetic corpus, not production latency or relevance estimates. The exact-head run reported FTS5 available, 144 documents, 24 queries (12 Arabic / 12 English), and zero network/provider/remote writes.

## Safety and scope

- Uses only Python standard library and an in-memory SQLite database.
- Zero network calls, provider calls, D1 writes, migrations, secrets, or deployments.
- Query terms are quoted before building FTS MATCH expressions.
- Fixture text is synthetic and does not assert anything about real products, competitors, or sources.

## Decision gate

This result is an offline SQLite benchmark only. It does not establish Cloudflare D1 FTS5 support or production performance. The next step after exact-head CI passes is a read-only capability probe against the existing isolated D1 environment, only when the zero-cost preflight proves eligibility. If the D1 feature/plan remains unknown, keep production search on the existing metadata path and retain SQLite FTS5 only as an offline fallback. Do not add a new database or search service before measured evidence requires one.
