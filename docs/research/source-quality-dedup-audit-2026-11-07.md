# Research discovery source-quality and dedup audit — 2026-11-07

## Scope and immutable inputs

Offline audit only. No new discovery request, network call, model/provider call, cloning, installation, candidate lifecycle mutation, D1 operation, or remote write was performed.

- Discovery run: [42326546406](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42326546406), main snapshot timestamp `2026-11-07T01:55:00.360528+00:00`.
- Candidate artifact: `research-candidates-42326546406`, artifact ID `13555796139`, GitHub artifact SHA-256 `d0378e84fd46a0e96d5b84735d7b7bef7c0e56e9a1f8e4c0696022810c880324`.
- Extracted JSON SHA-256: `b63903b1d8b03b3e2178334c99ff49b2206c25a829ddfa9dacf5491cb90a6141`.
- Benchmark artifact: `research-search-benchmark-42326546406`, artifact ID `13555855983`, SHA-256 `d0378e84fd46a0e96d5b84735d7b7bef7c0e56e9a1f8e4c0696022810c880324` (artifact digest is recorded by Actions; candidate and benchmark artifacts have separate IDs/digests).
- Qrels source: `config/research-search-benchmark-qrels.json` on main at the audited benchmark run.

The discovery JSON declares 84 metadata records and contains exactly 64 GitHub repositories, 10 Crossref works, 10 arXiv works, and zero SearXNG records. Its `discovery_only` marker is true; 11 source requests are recorded.

## Deduplication findings

Applied deterministic, conservative identity normalization to the fixed snapshot:

1. GitHub identity: lowercase `owner/repo`, strip a trailing slash and optional `.git`, and ignore path components after the repository root.
2. DOI URL: lowercase DOI path under `doi.org` / `dx.doi.org`.
3. General URL: lowercase scheme/host, remove fragment and trailing slash; remove only known tracking parameters (`utm_*`, `fbclid`, `gclid`) while preserving other query parameters.
4. Exact metadata fingerprint: SHA-256 over stable identity/title/description/language/licence/topics fields, excluding retrieval/provenance fields.
5. Near-title check: title similarity threshold 0.92, used only as a review signal, never as an automatic merge rule.

Results:

- 64 GitHub records yielded 64 unique canonical repository identities; zero duplicate repository groups.
- 84 records yielded 84 unique canonical URLs; zero URL collision groups.
- Zero exact duplicate metadata fingerprints.
- Zero cross-record title pairs at or above the 0.92 review threshold.
- `omnigent-ai/omnigent` is correctly represented once while retaining both discovery categories (`agent_frameworks` and `coding_agents`); this is provenance aggregation, not a duplicate.
- None of the 64 GitHub URLs had query parameters, so tracking-parameter collision behavior is implemented in the audit method but not exercised by this snapshot.

These are bounded snapshot findings, not proof that future snapshots cannot contain duplicates. The near-title threshold is intentionally a candidate-review heuristic and does not detect semantic duplicates with dissimilar titles.

## Source-quality findings

| Field | Snapshot result | Interpretation |
|---|---:|---|
| GitHub language | 62/64 present | 2 missing |
| GitHub SPDX licence field | 52/64 present | 12 missing; 4 values are `NOASSERTION` |
| GitHub licence verified | 0/64 true | A populated SPDX value is not proof that licence terms were independently verified |
| GitHub topics | 45/64 present | 19 missing |
| GitHub pushed timestamp | 64/64 present | Useful freshness signal, not repository creation/publication time |
| GitHub description | 64/64 present | Present but not independently quality-scored |
| Crossref publication date | 10/10 present | Source-provided precision/format varies |
| Crossref licence URL | 8/10 present | URL presence is not licence verification |
| Crossref licence verified | 0/10 true | No permission claim should be inferred |
| arXiv publication/update date | 10/10 each | Source metadata dates are present |
| arXiv licence metadata | 0/10 present | Unknown; do not infer open licence from public availability |

All 84 records remain `unreviewed`. Unknown/missing licence and language values must remain unknown rather than being filled by inference. Crossref's `license_urls` and GitHub's `license_spdx` are source declarations, not legal review. The snapshot has retrieval time and per-record discovery time; do not substitute these for publication/update time.

## Retrieval benchmark and qrels coverage

The paired benchmark uses 20 manually curated queries (10 English, 10 Arabic) against 84 metadata records. Only 98 of 118 expected query-document judgments were present: **83.05% label coverage**. Missing judgments by query family are symmetrical across English and Arabic:

- q01 orchestration: 9/12 present per language (3 missing each).
- q02 coding agents: 5/8 present per language (3 missing each).
- q04 MCP tools: 3/5 present per language (2 missing each).
- q05 agent memory: 5/6 present per language (1 missing each).
- q06 durable workflows: 5/6 present per language (1 missing each).
- q03, q07, q08, q09 and q10: all expected IDs present in both languages.

The benchmark artifact records both tracks. The **source-native** SQLite FTS5 OR/BM25 track scored P@10 **0.165**, R@20 **0.3715**, MRR@10 **0.4417**. The **alias-assisted** track scored P@10 **0.400**, R@20 **0.8615**, MRR@10 **0.9083**. Alias terms are editorial qrels annotations injected into labelled documents; this is a controlled upper-bound/assisted experiment, not native Arabic retrieval and not a production-quality claim. FTS5 AND remained weak (P@10 0.025, R@20 0.035, MRR@10 0.15); exact substring returned zero on these queries.

The reported latency is in-memory SQLite only (source-native OR/BM25 p50 0.037 ms, p95 0.103 ms; alias-assisted OR/BM25 p50 0.066 ms, p95 0.102 ms). It does **not** establish D1 FTS5 support, D1 parity, production latency, or a full-text evidence corpus. No page body/passages were collected.

## Recommended next increments

1. Add explicit qrels-miss triage so every absent expected ID is classified as source-recall miss, stale/deleted upstream project, or label error; keep source-native and alias-assisted metrics separate.
2. Add tests for URL normalization edge cases (case, trailing slash, `.git`, safe non-tracking query preservation, tracking removal, DOI case, fragments) and identity collisions. Never auto-merge by title.
3. Preserve source-declared licence fields separately from a future independently verified licence decision; keep current values unverified.
4. Improve source recall specifically for orchestration, coding agents, MCP and memory, where qrels coverage is lowest; evaluate the next fixed snapshot against the same qrels before changing ranking.
5. Keep SQLite FTS5 as the offline baseline. Do not add D1 FTS, a vector database, crawler, SearXNG host, or another search service until a capability probe and measured benchmark justify it.

## Reproduction

Download the named Actions artifact and verify the archive SHA-256 shown above. Extract `research-candidates.json`; verify its SHA-256 above. Use the versioned qrels file and `scripts/benchmark_research_search.py --real-corpus --input research-candidates.json --qrels config/research-search-benchmark-qrels.json --json` for the retrieval benchmark. The dedup/source-quality counts above are a separate offline audit of the same extracted JSON and do not mutate the source artifact.
