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

The report now separates two evaluation tracks:
- **Alias-assisted:** editorial Arabic aliases are added to qrels-labelled candidates. This measures retrieval when the controlled vocabulary is available; because the aliases are attached using the relevance labels, these scores are intentionally label-conditioned and must not be interpreted as native Arabic retrieval performance.
- **Source-native:** the same snapshot and qrels are evaluated with only source-provided metadata fields; no qrels-derived aliases are injected. This is the evidence track for whether Arabic queries can retrieve documents from the metadata as actually collected. A low score can indicate that source metadata is English-only or lacks Arabic terminology, not necessarily a ranking defect.

Both tracks retain per-query label coverage and are reported separately. Never quote alias-assisted metrics without the source-native comparison.

A local exploratory run on discovery snapshot #42317405323 (61 records: 41 GitHub, 10 Crossref, 10 arXiv; 20 queries) showed that strict AND matching was too restrictive. Weighted OR/BM25 materially improved recall and ranking on this small labelled set. These exploratory numbers are not production claims and do not establish D1 FTS5 parity or latency. The first dual-track main artifact is run #42324928734 (artifact #13555026166): 61 documents (41 GitHub, 10 Crossref, 10 arXiv), 20 queries (10 English, 10 Arabic), and 86/118 expected query-document judgments present (72.88% label coverage). Weighted FTS5 OR/BM25 scored P@10 0.16, R@20 0.4375, MRR@10 0.4017 on source-native metadata, versus P@10 0.37, R@20 0.9304, MRR@10 0.9017 on the label-conditioned alias-assisted track. Strict AND scored P@10 0.025, R@20 0.0387, MRR@10 0.15; exact substring scored zero. These figures are exploratory and corpus-specific; the alias-assisted figures are not native Arabic retrieval. The next step is to expand relevance labels from real misses, improve discovery coverage, and measure D1 FTS5 separately before changing any production search endpoint.


## Discovery coverage adjustment — 2026-11-07

The GitHub discovery workflow retains up to 8 results per query (previously 5) while keeping the same 9 API queries and the API's hard per-page bound of 10. This increases candidate recall without adding request count or a new source. Candidates remain untrusted public metadata, uninstalled and unexecuted. The next benchmark artifact must show whether MCP and model-routing coverage improves; revert or tune the cap if precision/triage quality degrades.
