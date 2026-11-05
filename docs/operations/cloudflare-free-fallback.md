# Cloudflare Free quota research and independent scheduled-probe fallback

**Research snapshot:** 2026-11-05. Limits and product behavior are subject to change; re-check the linked vendor documentation before relying on a quota or price.

## Decision and scope

Cloudflare remains the primary host for the engine's Staging task API and D1 queue. A separate Supabase **Free** project is provisioned as a deliberately narrow fallback for one job only: the scheduled, read-only `python_platform_probe` supervised process. It is not a replacement for Cloudflare, a general queue consumer, a Production path, or a failover for arbitrary Staging/business tasks.

The fallback is selected only when the Cloudflare API reports a retryable service/transport failure, Cloudflare Error 1027, or the workflow cannot install the temporary Staging bearer token. Authentication/authorization errors (401/403), lease conflicts, invalid task data, failed safeguards, and non-retryable responses continue to fail closed. If both providers are unavailable, the run fails closed; there is no ephemeral SQLite fallback.

The failed scheduled run `42020505385` still has an unknown `RemoteTaskStoreError` cause. This fallback does **not** establish that Cloudflare quota exhaustion caused that error or that Cloudflare needs a paid upgrade.

## Verified vendor limits

| Service | Relevant Free limit/behavior | Operational implication |
|---|---|---|
| Cloudflare Workers | 100,000 requests per day; the limit resets at midnight UTC. Exceeding it can return Error 1027. Free CPU time is 10 ms per request and there are 50 subrequests per invocation. | A Worker cron/watchdog on the same account is not an independent quota fallback. |
| Cloudflare D1 | 10 databases per account, 500 MB per database, 5 GB total account storage, 50 D1 queries per Worker invocation, and 7-day Time Travel on Free. | Request quota exhaustion and database/storage limits are distinct failure modes; this design does not move general D1 data. |
| Supabase Free | Two Free projects across organizations where the user is Owner/Admin; 500 MB database per project, 5 GB egress, 1 GB file storage, and 500,000 Edge Function invocations. | The dedicated fallback is small and is invoked only on actual fallback use; the scheduled workflow no longer performs a daily standby heartbeat (see below). Shared/account-wide usage and vendor limits still apply. |
| Supabase Free inactivity | A Free project with too little user database activity over a seven-day window may pause. A few user requests per day typically suffice; paused projects may be restored for up to one year. | The scheduled workflow makes no standby heartbeat (removed 2026-11-05, below), so inactivity pausing is possible; restoration remains available and the probe fails safely while paused. |
| GitHub Actions | Standard GitHub-hosted runners in public repositories remain free independently of included compute usage; this repository is public and uses `ubuntu-latest`. | This avoids runner-minute charges for this job under the documented standard-runner policy, but does not make scheduled delivery reliable or guaranteed. |

Sources: [Cloudflare Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [Cloudflare D1 limits](https://developers.cloudflare.com/d1/platform/limits/), [Supabase billing](https://supabase.com/docs/guides/platform/billing-on-supabase), [Supabase Free project pausing](https://supabase.com/docs/guides/platform/free-project-pausing), [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [GitHub scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Provisioned resources

- Dedicated project: `raawaa-engine-fallback`, Supabase project ref `xuwtuhfqjjwszxqkmivj`, region `us-east-1`, created with the explicit `free` plan and reported `ACTIVE_HEALTHY`.
- The existing Rawaa application project `Ghaioooth-core` was not modified or reused.
- Migrations `engine_fallback_probe_v2` and `engine_fallback_outcome_shape_guard` create the private `engine_fallback` schema and apply a corrective process-outcome validator. The first synthetic RPC lifecycle test exposed an unsupported `jsonb_object_length()` call; it rolled back without leaving rows, and the guard migration replaced it with PostgreSQL's JSONB key-existence operators. A second enqueue → exact claim → prepare/start/settle → finish lifecycle test passed and deleted its synthetic rows. Direct SQL health ping passed; task and dispatch tables were empty afterward. These are database-level checks, not an Edge Function/OIDC or Cron execution proof. The schema includes a narrowly constrained task table, process-dispatch ledger, audit log, and heartbeat. Row-level security is enabled; anonymous/authenticated table access is revoked. Public RPCs are executable only by `service_role` and are called from the Edge Function, never from the runner.
- Edge Function `engine-fallback` is active (version 2) with gateway JWT validation disabled because the caller presents GitHub OIDC rather than a Supabase JWT. The function itself cryptographically verifies GitHub's RS256 signature and requires the exact repository ID, `main` ref, `schedule` event, audience, and reviewed workflow path before using Supabase's injected service key. No service-role key is placed in GitHub or repository files.
- The function serves only the heartbeat and the exact supervised-probe enqueue, exact-ID claim, lease heartbeat, finish, and process-dispatch operations. It does not expose list/claim-next/recovery routes. The SQL envelope further pins task type, title, payload, one attempt, dry-run mode, and successful dispatch outcome.

## Queue and execution safety

The Cloudflare and Supabase ledgers are separate; the fallback is **not** a replicated queue and does not synchronize task state. The GitHub runner executes only the fixed read-only platform probe under the existing process supervisor. The fallback cannot claim arbitrary queue entries, invoke a model, access a provider credential, or perform an external write. `RAAWAA_ENGINE_LIVE_INFERENCE` must remain disabled and provider credentials are rejected by the script.

A Cloudflare transport failure can occur after the remote operation committed but before its response reached the runner. A retry on the separate fallback ledger can therefore create a second **read-only probe** task. This is acceptable only for this no-business-side-effect probe; the same pattern must not be generalized to customer work. The durable process-dispatch ledger and lease fencing remain in force within each backend. An ambiguous or stale dispatch is quarantined, not replayed.

The fallback workflow previously performed one signed daily standby heartbeat to reduce the chance of Supabase Free inactivity pausing the project. That heartbeat was **removed from the scheduled path on 2026-11-05**: `engine_fallback_ping()` executes `INSERT ... ON CONFLICT DO UPDATE` on `engine_fallback.heartbeat` and refreshes `last_seen_at` on every call, i.e. a persistent external write that the run output classified as `external_writes=0`. The RPC remains deployed for explicit manual diagnostics only; automation no longer calls it, and the possible inactivity pause is accepted (the project can be restored, and the probe fails safely meanwhile). Live state independently re-verified 2026-11-05: one heartbeat row (`last_seen_at` 2026-11-05T14:10:24Z, predating this removal), task/audit/dispatch tables empty. The workflow records the selected backend in its JSON output. A successful Supabase-backed run (`backend=supabase_free_fallback`) is evidence for the **fallback runner and Supabase ledger only**; it is not evidence that Cloudflare Staging/D1 worked and never counts toward the Runtime-004 Cloudflare-primary acceptance, which requires `backend=cloudflare_staging`. A successful Cloudflare-backed run is reported separately. The first real scheduled fallback invocation is still required to validate GitHub OIDC claim compatibility, database RPC execution, and the complete queue/supervisor lifecycle. RLS/no-policy advisor findings on the four `engine_fallback` tables are intentional: RLS is enabled, all table/schema access is revoked from `anon`/`authenticated`, and the security-definer RPCs (executable only by `service_role`) are the sole access boundary — do not add broad policies to silence the linter.

## Cost, downtime, and remaining risks

This setup uses a project explicitly created on Supabase Free and introduces no paid upgrade or paid fallback. It is not a zero-cost or uninterrupted-service guarantee:

- Free quotas can be reached or changed, provider services can be unavailable, and Supabase may pause an inactive project.
- A public Edge Function can still consume invocation quota under unauthenticated traffic even though its data operations require a verified GitHub token; the strict OIDC check protects queue access, not vendor availability against denial of service.
- GitHub Actions scheduled runs are best-effort. With the standby heartbeat removed, a long schedule outage can allow the Free project to pause; the probe then fails safely instead of writing to keep it warm.
- Cloudflare's existing Worker watchdog depends on Cloudflare Worker/D1 and does not provide provider independence.
- If the fallback is down, the exact probe stops safely. No local ephemeral state is promoted to durable queue state.
- This fallback covers only this supervised probe; it does not keep the full engine, mobile status API, D1 ledgers, or other queued work operating through Cloudflare quota exhaustion.

No request in the failed schedule is retroactively considered successful. Do not claim unattended Cron proof until a genuinely schedule-triggered run completes and its backend is identified in the output.

## Repository implementation

The versioned implementation is in:

- `supabase/migrations/20261105140337_engine_fallback_probe_v2.sql`
- `supabase/migrations/20261105141213_engine_fallback_outcome_shape_guard.sql`
- `supabase/functions/engine-fallback/index.ts`
- `engine/supabase_fallback_task_store.py`
- `engine/github_oidc.py`
- `scripts/run_staging_scheduled_process_probe.py`
- `.github/workflows/staging-supervised-process-probe.yml`

The workflow keeps the normal Cloudflare path primary, makes its temporary Staging token best-effort, and falls back only for the bounded error classes above. Cloudflare credential setup and revocation remain guarded; cleanup is attempted after any token-install attempt. Production execution remains disabled.
