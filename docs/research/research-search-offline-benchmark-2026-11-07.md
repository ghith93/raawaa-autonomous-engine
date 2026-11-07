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


## Real-corpus benchmark — 2026-11-07

The daily discovery workflow now runs a second benchmark against the current `research-candidates.json` snapshot using the versioned editorial qrels in `config/research-search-benchmark-qrels.json`. It evaluates exact substring lookup, strict FTS5 AND, and weighted FTS5 OR/BM25 across English and Arabic query pairs. The benchmark is offline: it consumes the discovery file already produced by the workflow and makes no additional network, model, or write calls.

Relevance labels are manually curated from public metadata and are not exhaustive ground truth. Arabic keywords are editorial aliases attached only to labelled candidates; this measures cross-language metadata retrieval with a controlled vocabulary, not automatic translation. The report records per-query label coverage so a changing daily corpus cannot silently look complete when relevant candidates are absent. Reports are uploaded as 30-day workflow artifacts.

A local exploratory run on discovery snapshot #42317405323 (61 records: 41 GitHub, 10 Crossref, 10 arXiv; 20 queries) showed that strict AND matching was too restrictive. Weighted OR/BM25 materially improved recall and ranking on this small labelled set. These exploratory numbers are not production claims and do not establish D1 FTS5 parity or latency. The next step is to review the first scheduled benchmark artifacts and refine labels from observed misses before changing any production search endpoint.


## Benchmark interpretation correction — source-native versus alias-assisted

The first workflow artifact (#42324442467) measured the alias-assisted track only: editorial Arabic terms were added to candidates selected by the same qrels. This is a useful controlled vocabulary experiment, but it is label-conditioned and is **not** evidence that source-native metadata supports Arabic queries.

The benchmark now reports two separate tracks for every retrieval method, with overall, English-only and Arabic-only metrics:

- **Source-native:** indexes only titles and metadata actually present in the GitHub/Crossref/arXiv/SearXNG snapshot. No Arabic aliases are injected. This is the honest baseline for current native multilingual retrieval.
- **Alias-assisted:** adds the editorial Arabic vocabulary to labelled candidates. This measures the potential gain from a future curated synonym/translation metadata layer. It is not native Arabic retrieval and must never be merged into the source-native score.

The earlier artifact's FTS5 OR/BM25 figures (P@10 0.38, R@20 0.9079, MRR@10 0.9042 at 76.27% label coverage) should be read as alias-assisted only. The next artifact will expose both tracks side by side. Do not change a production endpoint based on the alias-assisted result alone.
