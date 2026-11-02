# Cloudflare Durable Objects Alarms — evaluation (2026-11-02)

## Decision

**Deferred; do not implement now.** The current Cloudflare Worker + D1 task lifecycle remains the remote control plane and durable task ledger. GitHub Actions remains the bounded scheduled Python consumer. AndroidHarness is optional and must not be a runtime dependency.

A Durable Object alarm cannot execute the current Python task handlers. The existing Worker Cron already performs stale-lease recovery, so adding a Durable Object today would duplicate scheduling without replacing the Python consumer. Reconsider only after a measured decision to port the deterministic dry-run handlers into the Worker or provide an existing safe remote Python runtime.

## Evidence reviewed

- Cloudflare documents that Durable Object alarms have at-least-once execution and automatic retries with exponential backoff, beginning at two seconds and allowing up to six retries. One alarm is scheduled per object. [Official alarm documentation](https://developers.cloudflare.com/durable-objects/api/alarms/).
- Cloudflare documents SQLite-backed Durable Objects on the Workers Free plan. Free-plan exhaustion causes further operations of the relevant type to fail rather than granting unlimited capacity. [Official Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/).
- The published free allocation currently includes 100,000 requests/day and 13,000 GB-seconds/day for Durable Objects. Treat these as hard ceilings and re-check before implementation. [Official pricing table](https://developers.cloudflare.com/durable-objects/platform/pricing/).
- GitHub states that scheduled workflow events may be delayed during high load and, in some cases, queued jobs may be dropped. Public-repository schedules may also be disabled after 60 days without repository activity. [Official schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
- An open-source queue design review (Loams) documents the relevant delivery model: at-least-once delivery, lease-expiry redelivery, and fencing tokens to prevent stale workers from recording results. [Loams job API design](https://github.com/ostrium-labs/loams/blob/main/docs/design/26-jobs-api.md).

## Fit with the existing engine

| Requirement | Existing implementation | Durable Object role |
|---|---|---|
| Canonical task state | D1 tasks table | None; do not duplicate |
| Atomic claim and lease token | Authenticated Worker API | Call existing API / shared handler |
| Heartbeat, finish, retry | Existing D1 lifecycle | Preserve unchanged |
| Stale lease recovery | Worker Cron Trigger | Duplicates existing capability |
| Periodic Python execution | GitHub Actions every 15 minutes | Cannot run current Python handlers |
| AI/provider spend control | Existing quota ledger and disabled routes | No authority; never bypass |
| External writes | Disabled / approval-gated | No authority |

## Proposed isolated experiment

1. First decide whether moving the deterministic dry-run handlers from Python to Worker JavaScript is worth the maintenance and parity cost; do not maintain two independent handler implementations.
2. If a single canonical Worker-side executor is established, prototype one singleton SQLite-backed Durable Object alarm with scheduler metadata only (no task or quota ledger).
3. Preserve D1 claim/heartbeat/finish/retry and lease-token fencing as the only task lifecycle.
4. Test cold-start/restart, duplicate alarms, concurrency, alarm failure/retry, D1 outage, quota exhaustion, and no pending work.
5. Compare operational complexity and free-tier use against the existing Worker Cron + Actions consumer before proposing adoption.

## Rejection criteria

Reject the Durable Object path if it requires a second queue, a paid plan, broader credentials, a production migration without a rollback, or weakens the existing D1 lease/fencing and zero-spend controls.

## Licence and maintenance notes

Cloudflare's platform documentation is CC-BY-4.0; the Loams design page is a public project design document and should be rechecked against the repository's current licence and maintenance activity before copying code. No third-party code or dependency was imported in this evaluation.

## Outcome

Deferred / not adopted. The present design has no Worker-side Python task execution entry point, and the Worker Cron already handles stale leases. No Durable Object namespace/class, migration, binding, deployment, account setting, or provider call was created or changed.