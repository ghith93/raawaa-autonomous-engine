# Scheduled Open-Source Research Discovery v1

## Purpose

The engine has a bounded, read-only GitHub and Crossref discovery capability. It complements provider model-catalog discovery and does not replace the broader research and engineering review pipeline in the Continuous Improvement Charter.

## What it does

- Searches six curated areas: agent frameworks, coding agents, agent memory, MCP ecosystem, model routing, and durable execution.
- Uses GitHub public repository Search API and one daily Crossref scholarly-metadata query.
- Caps source responses at 1 MiB before parsing and reports invalid UTF-8 as a safe discovery failure.
- Counts attempted source requests, including failures, in run telemetry.
- Limits each run to six queries and at most five candidates per query.
- Deduplicates repositories and records source query, discovery time, stars, last push, language, SPDX licence label, and topics.
- Marks every result unreviewed. A licence label is metadata only, not legal/licence review.
- Makes no model-generation requests, repository clones, code execution, installations, writes, or permission changes.
- Crossref returns bibliographic metadata only; abstracts are not retrieved.

## Run and review

Run manually with `python -m engine.cli research-discover --max-per-query 5`. The scheduled workflow uploads a 30-day artifact. Review authenticity, licence, maintenance, security, dependencies, data flows, compatibility, and cost before adoption. Discovery does not imply endorsement, installation, execution, or permission to use external services.

## Persistent D1 research memory

The authenticated ingestion, listing, and review endpoints store only allowlisted normalized GitHub/Crossref metadata. Re-ingestion is idempotent and preserves review decisions. No source code, abstracts, full text, secrets, or model outputs are stored. Keys-only readback verifies ingestion.

**Persistence is disabled by default.** The scheduled workflow executes the D1 persistence step only when the repository Actions variable `RAAWAA_ENGINE_RESEARCH_PERSISTENCE` is explicitly set to the exact value `enabled`. Leave it unset unless the operator deliberately enables persistent research writes. Workflow artifacts continue to be uploaded when persistence is disabled.

Migrations and Worker deployments remain subject to the repository's existing production-change approval policy.
