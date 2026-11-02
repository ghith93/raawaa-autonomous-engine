# Cloudflare Workflows durability spike — evaluation (2026-11-02)

**Decision: defer production adoption; retain the isolated prototype as a promising orchestration candidate.**

This evaluation continues issue [#120](https://github.com/ghith93/raawaa-autonomous-engine/issues/120). It does not authorize deployment, production D1 access, schema changes, provider inference, or external actions.

## Executive result

Cloudflare Workflows provides the strongest built-in fit for durable, multi-step execution and human approval waits on the existing Cloudflare platform. It could remove some custom checkpoint/retry/wait orchestration without requiring a separate always-on VM. It is not yet a replacement for the engine's D1 task ledger or its current scheduled queue consumer.

Keep the current GitHub Actions D1 Task Runner as the **only active queue consumer**. Keep D1 as the authoritative task, idempotency, lease, and audit ledger. Do not connect the prototype to the production Worker until a separately reviewed integration proves the existing policy loader, authenticated D1 claim/heartbeat/finish contract, lease fencing, and approval semantics.

## Existing baseline

| Path | Verified capability | Current limitation |
|---|---|---|
| GitHub Actions + D1 Task Runner | Scheduled every 5 minutes; bounded batch of up to 5 tasks / 60 seconds; authenticated Worker preflight; D1 claim/heartbeat/finish; deterministic dry-run specialist assignment | Each Actions invocation is ephemeral; polling cadence is not a durable long-running workflow and does not provide 24/7 agent execution |
| Microsoft Agent Framework checkpoint adapter | HMAC-authenticated checkpoint envelopes; isolated file-backed restart/recovery tests; completed-step replay checks | Test adapter only; pinned framework/private APIs and pickle-bearing payloads; no distributed D1 storage or production worker integration |
| Cloudflare Workflows prototype | Durable step API, bounded retry policy, deterministic instance IDs, approval wait/resume, restart/replay, cancellation, lease-expiry and timeout paths in a local Wrangler runtime | Fake adapters only; no D1/Queue integration, no production policy-loader wiring, no authenticated production endpoint, no model/provider calls |

The existing Task Runner remains the most mature production-adjacent execution path. The Microsoft Agent Framework adapter is useful as a checkpoint experiment but currently carries a larger custom storage/security burden than a managed Workflow runtime.

## Prototype scope and evidence

The prototype is deliberately isolated:

- Separate Worker class and Wrangler configuration; no production Worker entrypoint change.
- No routes, account ID, secrets, D1, Queues, AI binding, provider credentials, or production database.
- Local smoke test binds only to `127.0.0.1` and launches Wrangler with a filtered environment that excludes cloud/provider/GitHub credentials.
- Fake deterministic adapters only: claim → research stub → review stub → checkpoint → optional approval wait → completion.
- Strict payload allowlist and size limit; task and idempotency identifiers are bounded; retry count is fixed and small.
- Approval rejection/timeout, duplicate create, restart/replay, cancellation, and expired-lease behavior are exercised by tests.

The prototype's local HTTP endpoints are **not authenticated** and are safe only because the test runtime is loopback-only and the Wrangler config has no public route or production binding. They must never be deployed as-is.

## Free-plan and platform constraints

Verified against Cloudflare's current Workflows limits documentation on 2026-11-02:

- 10 ms active CPU per step on Workers Free; wall-clock duration may be unlimited while waiting on I/O.
- 1 MiB maximum event payload and non-stream step result.
- 100 MB maximum persisted state per Workflow instance.
- 1,024 steps per Workflow.
- 100,000 Workflow executions per day, shared with the Workers daily request limit.
- 100 concurrently running Workflow instances; waiting instances do not count toward that concurrency limit.
- 100,000 queued instances.
- Three-day retention for completed Workflow state on Free.
- Maximum 100 Workflow instance creations per second on Free.
- Free-plan retention for completed Workflow state is three days.

### Billing update verified 2026-11-02

Cloudflare's pricing page was updated 2026-09-21 and states that Workflows step and storage billing began 2026-08-10. The Free plan currently includes **3,000 steps/day**, **1 GB-month of Workflow storage**, and **100,000 requests/day shared with Workers**. It also lists 10 ms active CPU per invocation on Free. Therefore, "available on Free" does not mean unlimited or unmetered. Step count and persisted state are explicit billing dimensions; completed, errored, sleeping, and running instance state contributes to storage until retention/deletion. See the [official Workflows pricing page](https://developers.cloudflare.com/workflows/reference/pricing/) and [limits page](https://developers.cloudflare.com/workflows/reference/limits/).

Decision remains unchanged: keep Workflows isolated and undeployed. Do not connect it to production D1 or activate it as a second queue consumer. Before reconsideration, measure expected steps per task, state size/retention, request volume, and account-specific limits; require an explicit no-surprise-spend control and a reviewed rollback plan. Do not enable a paid plan or accept overage charges automatically.

These are platform ceilings, not a guarantee of account eligibility or a cost forecast. Recheck the account plan, current limits, and billing semantics before any production adoption. Keep payloads to IDs and bounded references; store durable business/task truth in D1, not in Workflow event bodies or large step outputs.

## Toolchain failure found and corrected

The earlier spike CI run [41362857123](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41362857123) passed Python tests, Worker tests, and the deterministic Workflows unit tests, then failed before the local runtime smoke could start. The embedded `workerd` in Wrangler 4.156.0 supported compatibility date `2026-10-30`, while the isolated spike config correctly used `2026-10-31`.

Cloudflare Workers SDK released Wrangler **4.156.1** at 2026-11-01 23:24 UTC. Its release updates the bundled `workerd` dependency to `1.20261101.1` and the default compatibility date to `2026-11-01`. The CI and local smoke test now pin that exact patch release rather than lowering the Worker compatibility date. This preserves deterministic tooling and avoids changing runtime compatibility merely to accommodate an older local emulator.

## Exact PR-head validation

PR [#125](https://github.com/ghith93/raawaa-autonomous-engine/pull/125) head `58ba78460db3ec9568c1f7ac93ebaf5377fc3c81` passed Engine CI on 2026-11-02: [run 41367225843](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41367225843).

| Check | Result |
|---|---:|
| Policy audit | Passed |
| Python unit suite | 227 passed (5.826 s) |
| Existing Cloudflare Worker tests | 25 passed, 0 failed |
| Workflows deterministic unit tests | 10 passed, 0 failed |
| Pinned Wrangler CLI | 4.156.1 |
| Local Workflows runtime smoke | Passed in 5.5 s |

The local runtime smoke exercised duplicate instance creation, step replay after restart, approval event delivery/resume, termination, expired lease handling, and approval timeout. The test runs with fake adapters and a filtered environment; it does not contact Cloudflare or a model provider. This is exact PR-head CI evidence, not production deployment or remote-runtime verification.

## Comparative assessment

| Criterion | GitHub Actions + D1 | Cloudflare Workflows | MAF checkpoint adapter |
|---|---|---|---|
| Durable step history / replay | Implemented in D1 task lifecycle, but orchestration is per scheduled invocation | Native persisted steps and replay; locally exercised in this spike | Checkpoint envelope and local file restart tests |
| Long approval wait | Requires another poll/event mechanism around D1 | Native `waitForEvent` | Resume from stored checkpoint; external event wiring is custom |
| Queue/task source of truth | D1 | Must remain D1; Workflow state is orchestration progress | Local file store only in current experiment |
| Distributed lease fencing | Existing D1 lease/heartbeat path | Not integrated yet; must call the existing D1 contract | Not integrated with D1 |
| Operational overhead | Existing Actions and Worker already configured | Adds Workflow binding, instance lifecycle and retention monitoring; no separate VM | Additional framework, pinned internal APIs and storage adapter |
| Free-tier fit | Existing bounded Actions cadence; runner quotas apply | Useful for I/O waits and orchestration, but 10 ms CPU/step and daily shared execution limits require bounded steps | Local compute/storage only; not a hosted durable runtime |
| Current readiness | Existing bounded dry-run path has been exercised | Local-only prototype; no deployment | Isolated test adapter only |

## Recommendation and next gates

**Defer production adoption for now.** Continue with the prototype only as an isolated candidate. A follow-up integration task may proceed after this spike's exact-head CI passes, but it must remain a separate feature branch and must not change production runtime behavior.

Before adoption, require all of the following:

1. Reuse `load_operating_policy()` before any task claim or agent/provider work; fail closed if any required policy document is missing or invalid.
2. Implement a narrowly authenticated trigger and a D1 adapter that reuses the existing claim, heartbeat, finish, idempotency, and lease-token fencing contract.
3. Prove one authoritative task result across duplicate Workflow events, duplicate instance creation, retries, cancellation, timeout, lease expiry, and process/Worker restarts.
4. Keep D1 as the task/audit source of truth; pass only IDs and bounded references in Workflow parameters/events.
5. Add explicit Workflow concurrency, retry, step CPU, event-size, state-retention, and daily-execution budgets with observable stop reasons.
6. Verify the actual Cloudflare account plan and current billing/limits before any production Workflow creation.
7. Preserve one queue consumer during migration. Do not run the existing D1 poller and a new Workflow dispatcher against the same tasks until a reviewed ownership/fencing design prevents races.
8. Run a non-production integration test with no real customer data, no live model calls, and no external writes. Production integration/deployment requires separate explicit approval.

## AndroidHarness and zero-touch boundary

AndroidHarness 3.2 provides two relevant device-side options:

- **WorkManager scheduled messages:** recurring prompt runs are managed by Android WorkManager. They can continue while the app is backgrounded, but remain subject to the device being powered, online, not force-stopped, and allowed to run under Android/OEM battery policy. They are not cloud-hosted execution.
- **Telegram remote control:** the upstream README and 3.2 changelog document a private Telegram bot gateway for Code conversations. Setup requires a bot token, a prepared bot link, a short-lived one-time pairing code, and confirmation of the Telegram account inside the AndroidHarness app. After pairing, the confirmed account can submit work and answer approval prompts while the app is minimized, but the phone must remain online and the foreground Telegram service must be enabled. The app's security guidance explicitly treats the paired account as authorized to submit work and interact with approvals; the bot token and paired account therefore require protection.

The relevant upstream instructions are [AndroidHarness README](https://github.com/Sanuu7/AndroidHarness/blob/main/README.md), [3.2 changelog](https://github.com/Sanuu7/AndroidHarness/blob/main/CHANGELOG.md), [Telegram setup guide](https://github.com/Sanuu7/AndroidHarness/blob/main/app/src/main/assets/learn/guide.json), and [security guidance](https://github.com/Sanuu7/AndroidHarness/blob/main/SECURITY.md). The Telegram setup guide says the bot and phone both need network access and Telegram does not host the AndroidHarness task on the user's behalf.

Repository changes cannot perform the first-time pairing confirmation, approve a device permission dialog, or start the local issue-bridge poller. The bridge remains inactive until a local AndroidHarness process is started; no authenticated live AndroidHarness session is exposed to this GitHub integration. Thus Telegram is a remote-control channel after one-time device setup, not a way to remove the tablet from the execution path.

A deployed Cloudflare Workflow can execute and resume independently of the tablet after an authorized deployment and authenticated trigger are in place. This prototype is not deployed and does not yet execute AI agents. It therefore improves the path toward device-independent orchestration but does not yet deliver autonomous AI execution or guaranteed 24/7 service.

## Sources

- [Cloudflare Workflows limits](https://developers.cloudflare.com/workflows/reference/limits/) — current platform ceilings.
- [Cloudflare Workflows local development](https://developers.cloudflare.com/workflows/build/local-development/) — Wrangler/Miniflare local runtime requirements and limitations.
- [Wrangler 4.156.1 release](https://github.com/cloudflare/workers-sdk/releases/tag/wrangler%404.156.1) — bundled workerd update and compatibility-date change.
- [Issue #120](https://github.com/ghith93/raawaa-autonomous-engine/issues/120) — spike scope and acceptance criteria.
- [Issue #34](https://github.com/ghith93/raawaa-autonomous-engine/issues/34) — current runtime, quota, AndroidHarness and checkpoint-adapter evidence.
