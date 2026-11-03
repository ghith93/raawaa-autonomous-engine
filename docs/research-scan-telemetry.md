# Research scan and source freshness telemetry

The scheduled public discovery workflow records a separate `research_scan_completed` audit event and two `research_source_observation` events in D1. These records are distinct from Cron heartbeats, GitHub CI health snapshots, candidate ingestion, and the latest market snapshot.

## Contract

- Scan identity is the GitHub Actions `run_id:run_attempt`; the record also carries run number and exact commit SHA.
- Scan metadata contains only timestamps, workflow identity, aggregate request/candidate counts, unavailable-query count, and success/partial/failure state.
- Source observations are limited to GitHub public repository search and Crossref public works search. They contain observed time, request/item counts, a 24-hour freshness threshold, and a bounded reason code when unavailable.
- Raw provider errors, response bodies, candidate titles, prompts, API tokens, and credentials are not copied into telemetry.
- The publisher validates the workflow identity, timestamps, counters, source allowlist, and matching scan IDs before writing. Parameterized D1 statements use `NOT EXISTS` guards, making retries idempotent for the same scan ID and source.
- The existing D1 `audit_log` is reused through the Cloudflare D1 REST client. The publisher hard-pins the Staging database ID, verifies it against `wrangler.staging.toml`, verifies that it differs from the production ID in `wrangler.toml`, then reads back the three events. No Worker API token or production Worker route is used, and no schema migration is required.
- The mobile status endpoint reports only a validated latest scan and the two source observations belonging to that scan. Missing, malformed, future-dated, or old records remain unknown/stale rather than inferred from a scheduler heartbeat.
- Candidate persistence remains separately gated. Recording scan telemetry does not enable candidate persistence, repository cloning, installation, execution, model calls, or external actions.

## Freshness interpretation

A source observation is fresh for up to 24 hours from its timezone-aware `observed_at`. A source that failed to respond is `unavailable`; an old successful observation is `stale`. The aggregate is `fresh` only when both sources are fresh, `partial` when source states differ or one is unavailable while the other is fresh, `stale` when both are stale, `unavailable` when both are unavailable, and `unknown` when either observation is missing or invalid.

## Rollout

The workflow publishes telemetry directly to the verified Staging D1 database. It does not call the production Worker or write to the production database. The PR CI and Staging verification must pass before any deployment decision.
