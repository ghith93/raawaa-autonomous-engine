# Scheduled Open-Source Research Discovery v1

## Purpose

The engine now has bounded, read-only GitHub repository and academic metadata discovery. It complements provider model-catalog discovery; it does not replace the broader research and engineering review pipeline defined in the Continuous Improvement Charter.

## What it does

- Searches nine curated areas configured in `config/research_sources.json`: agent frameworks, coding agents, agent memory, MCP ecosystem, model routing, durable execution, agent-skill security, agent evaluation, and skill benchmarks.
- Uses GitHub public repository Search API with an optional GITHUB_TOKEN, one daily Crossref public scholarly-metadata query, and one bounded arXiv Atom metadata request (up to ten entries).
- Caps each source response at 1 MiB. arXiv XML must be UTF-8, comes from the fixed HTTPS arXiv host, and is rejected if it contains DTD/entity declarations or an unexpected Atom shape.
- Counts attempted source requests, including failures, in run telemetry.
- Limits each run to the configured nine GitHub queries and at most five candidates per query, one Crossref metadata request for up to ten records, and one arXiv metadata request for up to ten records. The configuration is authoritative for these bounds.
- Deduplicates repositories and records source query, discovery time, stars, last push, language, SPDX licence label, and topics.
- Marks every result unreviewed. A licence label is metadata only, not a legal/licence review.
- Makes no model-generation requests, repository clones, code execution, installations, writes, or permission changes.
- Crossref and arXiv return bibliographic metadata only; abstracts and full text are not retrieved. Licence URLs/labels are metadata and are not treated as licence verification. Each academic source makes one bounded request per daily run.

## Run manually

    python -m engine.cli research-discover --max-per-query 5

The command writes a JSON snapshot to stdout. The scheduled GitHub Actions workflow runs daily, also runs after relevant research/Worker/migration changes reach main, and uploads the snapshot as a 30-day artifact. It uses read-only repository permissions.

## Review before adoption

1. Open the original repository and verify ownership/authenticity.
2. Inspect the exact licence file and any model/data licences; do not rely on SPDX metadata alone.
3. Check recent releases, commits, maintainers, security advisories, dependencies, and open issues.
4. Assess data flows, secrets exposure, network access, and execution isolation.
5. Compare with the current engine component and alternatives.
6. Test in an isolated branch/sandbox, measure benefit/cost, then decide whether to integrate.

Discovery does not imply endorsement, installation, execution, or permission to use external services.

## Current limitations

- GitHub, Crossref, and arXiv are initial sources, not a complete web/official-documentation/scientific/MCP/plugin research engine.
- Snapshots are workflow artifacts with 30-day retention. Candidate metadata is upserted into authenticated Cloudflare D1 only when the operator explicitly enables persistence. Rediscovery updates last_seen_at and discovery_count while preserving human review_status. The authenticated review endpoint records accepted/rejected/deferred decisions. The D1 store is durable cross-run metadata memory when the explicit persistence gate is enabled; it accepts normalized GitHub, Crossref, and arXiv records. Artifacts remain convenient per-run snapshots. After ingestion, the persistence client performs an authenticated keys-only readback and confirms every submitted source key exists before reporting success. The persistence client retries only temporary deployment/migration/availability errors with bounded delays; authentication and validation failures stop immediately.
- No autonomous agent reads and evaluates candidates yet. Results require engineering/security/licence review.
- The schedule is daily and bounded, not always-on. D1 ingestion uses the existing Worker application token and accepts only normalized GitHub/Crossref/arXiv bibliographic metadata; abstracts and full text remain excluded.

Official Crossref references:
- REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- Access and rate limits: https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/

## Persistent D1 research memory

- Authenticated ingestion: POST /v1/research/candidates (batch size is capped at 25 by the Worker; the workflow sends chunks of five).
- Authenticated listing: GET /v1/research/candidates?status=unreviewed&limit=50. Use keys_only=true for compact verification or include_metadata=true for detailed records.
- Explicit review: POST /v1/research/candidates/review with source_key and review_status set to accepted, rejected, or deferred.
- Re-ingestion is idempotent by normalized GitHub repository name, Crossref DOI, or arXiv ID. It updates last-seen/discovery count but does not overwrite a prior review decision.
- Only an allowlisted metadata subset is stored. No source code, abstracts, full text, secrets, or model outputs are stored.
- Migration 0002 is additive and applied by the existing D1 migration workflow after merge to main. Worker deployment is handled by the existing deploy workflow.
