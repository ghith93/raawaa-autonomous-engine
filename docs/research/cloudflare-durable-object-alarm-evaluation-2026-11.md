# Cloudflare Durable Objects Alarms — evaluation (2026-11-02)

## Decision

**Candidate for an isolated wake-up prototype; not adopted or deployed.** The current Cloudflare Worker + D1 task lifecycle remains the production control plane and durable task ledger. GitHub Actions remains the bounded scheduled consumer. AndroidHarness is optional and must not be a runtime dependency.

A Durable Object alarm could remove the need for GitHub Actions to provide every periodic wake-up. It must not replace D1, add a parallel task queue, or bypass the current authenticated task lifecycle.

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
| Stale lease recovery | Worker Cron Trigger | Could request bounded recovery |
| Periodic wake-up | GitHub Actions every 15 minutes | Potential durable alarm |
| AI/provider spend control | Existing quota ledger and disabled routes | No authority; never bypass |
| External writes | Disabled / approval-gated | No authority |

## Proposed isolated experiment

1. Add one singleton SQLite-backed Durable Object class with an alarm; its storage contains only scheduler metadata (next wake-up, last outcome, bounded retry state), never tasks or provider quota data.
2. The alarm handler calls the existing Worker task-batch entry point or an internal shared function, with a strict maximum batch size and wall-clock/CPU budget.
3. Schedule the next alarm only after recording the current invocation outcome. Rely on alarm retry semantics for thrown/transient failures, but make every operation idempotent because delivery is at least once.
4. Keep D1 lease tokens and fencing authoritative. Duplicate alarm delivery must not create duplicate claims or accept stale task results.
5. Add tests for cold start/restart, duplicate alarms, concurrent invocation, alarm failure/retry, D1 outage, quota exhaustion, and no pending work.
6. Run a non-production soak and compare latency, failure rate, request/compute use, and operational complexity against the current 15-minute Actions consumer.
7. Only then propose a migration. Keep GitHub Actions as an emergency/manual fallback until the new path has demonstrated recovery.

## Rejection criteria

Reject the Durable Object path if it requires a second queue, a paid plan, broader credentials, a production migration without a rollback, or weakens the existing D1 lease/fencing and zero-spend controls.

## Licence and maintenance notes

Cloudflare's platform documentation is CC-BY-4.0; the Loams design page is a public project design document and should be rechecked against the repository's current licence and maintenance activity before copying code. No third-party code or dependency was imported in this evaluation.

## Outcome

Research only. No Durable Object namespace/class, migration, binding, deployment, account setting, or provider call was created or changed.