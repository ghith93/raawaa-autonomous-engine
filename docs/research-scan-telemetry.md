# Research scan and source freshness telemetry

The scheduled public discovery workflow records a separate `research_scan_completed` audit event and two `research_source_observation` events in D1. These records are distinct from Cron heartbeats, GitHub CI health snapshots, candidate ingestion, and the latest market snapshot.

## Contract

- Scan identity is the GitHub Actions `run_id:run_attempt`; the record also carries run number and exact commit SHA.
- Scan metadata contains only timestamps, workflow identity, aggregate request/candidate counts, unavailable-query count, and success/partial/failure state.
- Source observations are limited to GitHub public repository search and Crossref public works search. They contain observed time, request/item counts, a 24-hour freshness threshold, and a bounded reason code when unavailable.
- Raw provider errors, response bodies, candidate titles, prompts, API tokens, and credentials are not copied into telemetry.
- The authenticated `POST /v1/research/scan-runs` endpoint validates the workflow identity, timestamps, counters, source allowlist, and matching scan IDs. It is idempotent for the same scan ID and source.
- The existing D1 `audit_log` is reused; no schema migration is required.
- The mobile status endpoint reports only a validated latest scan and the two source observations belonging to that scan. Missing, malformed, future-dated, or old records remain unknown/stale rather than inferred from a scheduler heartbeat.
- Candidate persistence remains separately gated. Recording scan telemetry does not enable candidate persistence, repository cloning, installation, execution, model calls, or external actions.

## Freshness interpretation

A source observation is fresh for up to 24 hours from its timezone-aware `observed_at`. A source that failed to respond is `unavailable`; an old successful observation is `stale`. The aggregate is `fresh` only when both sources are fresh, `partial` when source states differ or one is unavailable while the other is fresh, `stale` when both are stale, `unavailable` when both are unavailable, and `unknown` when either observation is missing or invalid.

## Rollout

The workflow publishes telemetry to the existing Engine Worker and D1. This change does not deploy a production Worker or modify the production database. The PR CI and Staging verification must pass before any deployment decision.
