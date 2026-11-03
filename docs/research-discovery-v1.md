# Scheduled Open-Source Research Discovery

## Purpose

The engine performs bounded, metadata-only discovery across public GitHub repositories, Crossref, arXiv, and an optional operator-hosted SearXNG endpoint. Discovery and deterministic triage do not imply endorsement, adoption, installation, execution, or trust.

## Sources and bounds

- GitHub: nine curated topic queries, at most five candidates per query.
- Crossref: one sequential public metadata request per daily scan, at most ten records.
- arXiv: one Atom API request per daily scan, at most ten records. Only bibliographic metadata is retained; abstracts and full text are excluded.
- SearXNG: optional single JSON request, at most ten results, skipped unless `RAAWAA_SEARXNG_BASE_URL` is explicitly configured to an operator-controlled HTTPS endpoint. JSON must be enabled in that instance's `search.formats`.
- Each source response is bounded to 1 MiB. arXiv and configured SearXNG requests reject redirects; authenticated D1 persistence also refuses redirects. Results remain untrusted metadata.
- The scheduled workflow records per-source request counts, result counts, freshness and failure status. It uploads bounded snapshots and deterministic triage/capability reports as 30-day artifacts.

## Persistent D1 research memory

Candidate persistence is separately gated by `RAAWAA_ENGINE_RESEARCH_PERSISTENCE=enabled`; the source code does not prove that this repository variable is enabled.

- The authenticated Worker accepts normalized GitHub and Crossref records today.
- PR #421 proposes migration `0009_research_source_expansion` plus strict Worker normalizers and persistence/readback support for arXiv and SearXNG. Until that PR is reviewed, merged, and the resulting Staging migration/readback are verified, the deployed D1 API remains limited to GitHub and Crossref.
- Re-ingestion is idempotent by normalized source key. It updates last-seen/discovery count without overwriting prior human review decisions.
- The Worker stores only bounded allowlisted metadata. It does not fetch SearXNG result URLs, abstracts, or full text.
- Authenticated listing and explicit review remain available through the existing research-candidate endpoints.

## Review before adoption

1. Verify the original source, maintainer identity, release history and exact revision.
2. Inspect source code, dependency tree, security advisories and known vulnerabilities.
3. Verify code, model, dataset and documentation licences independently.
4. Review data flows, network access, secrets exposure and execution isolation.
5. Compare with existing engine components and at least one alternative.
6. Test in an isolated branch/sandbox with synthetic data, measurable acceptance criteria and rollback.
7. Require independent security/architecture review before registry import or activation.

Metadata ranking is only a manual-review aid. Stars, recency, citations and snippets are not security, quality, licence or compatibility evidence. No candidate is automatically accepted or installed.

## Current limitations

- SearXNG is not active: no owner-controlled endpoint is configured or queried.
- The R&D workflow is scheduled daily, not an always-on autonomous research agent.
- Capability-gap analysis is deterministic and repository-local; it does not inspect live account variables, D1 contents, deployed service health or model-provider consoles.
- No external skill is currently auto-imported or executed by discovery.
- Research source expansion does not create new spending or enable paid model routes.

## Run manually

```bash
python -m engine.cli research-discover --max-per-query 5
python scripts/triage_research_candidates.py research-candidates.json
python scripts/analyze_capability_gaps.py research-triage.json .
```

Official references:
- SearXNG Search API: https://docs.searxng.org/dev/search_api.html
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- arXiv API: https://info.arxiv.org/help/api/user-manual.html
