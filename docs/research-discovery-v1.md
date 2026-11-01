# Scheduled Open-Source Research Discovery v1

## Purpose

The engine now has a bounded, read-only GitHub repository discovery capability. It complements provider model-catalog discovery; it does not replace the broader research and engineering review pipeline defined in the Continuous Improvement Charter.

## What it does

- Searches six curated areas: agent frameworks, coding agents, agent memory, MCP ecosystem, model routing, and durable execution.
- Uses GitHub public repository Search API with an optional GITHUB_TOKEN for authenticated API rate limits, plus one daily Crossref public scholarly-metadata query without an API key.
- Caps GitHub and Crossref responses at 1 MiB before parsing and reports invalid UTF-8 as a safe discovery failure.
- Counts attempted source requests, including failures, in run telemetry.
- Limits each run to six queries and at most five candidates per query.
- Deduplicates repositories and records source query, discovery time, stars, last push, language, SPDX licence label, and topics.
- Marks every result unreviewed. A licence label is metadata only, not a legal/licence review.
- Makes no model-generation requests, repository clones, code execution, installations, writes, or permission changes.
- Crossref returns bibliographic metadata only; abstracts are not retrieved. Licence URLs are metadata and are not treated as licence verification. One sequential list request is made per daily run to respect public-pool rate limits.

## Run manually

    python -m engine.cli research-discover --max-per-query 5

The command writes a JSON snapshot to stdout. The scheduled GitHub Actions workflow runs daily and uploads the snapshot as a 30-day artifact. It uses read-only repository permissions.

## Review before adoption

1. Open the original repository and verify ownership/authenticity.
2. Inspect the exact licence file and any model/data licences; do not rely on SPDX metadata alone.
3. Check recent releases, commits, maintainers, security advisories, dependencies, and open issues.
4. Assess data flows, secrets exposure, network access, and execution isolation.
5. Compare with the current engine component and alternatives.
6. Test in an isolated branch/sandbox, measure benefit/cost, then decide whether to integrate.

Discovery does not imply endorsement, installation, execution, or permission to use external services.

## Current limitations

- GitHub and Crossref are initial sources, not a complete web/official-documentation/scientific/MCP/plugin research engine.
- Snapshots are workflow artifacts with 30-day retention; durable cross-run candidate memory and deduplication over time are future work.
- No autonomous agent reads and evaluates candidates yet. Results require engineering/security/licence review.
- The schedule is daily and bounded, not always-on.

Official Crossref references:
- REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/
- Access and rate limits: https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/
