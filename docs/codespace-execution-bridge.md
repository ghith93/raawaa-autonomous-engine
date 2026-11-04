# Codespace execution bridge — implementation contract

**Status: design proposal only.** This document does not activate a Codespace process, create credentials, change Worker/D1 configuration, or enable autonomous external actions.

## Decision

Use the existing Codespace as an **opportunistic, outbound-only execution agent** for explicitly routed development tasks. Keep GitHub-hosted Actions as the durable, bounded fallback and retain the existing Cloudflare Worker + D1 task ledger as the single canonical queue.

Do not register the long-lived Codespace as a persistent self-hosted Actions runner. Do not create another Codespace, queue, scheduler, database, public callback endpoint, or second task store.

## Existing architecture that must be preserved

- Cloudflare Worker + D1 own durable task state, idempotency, audit events, lease tokens, and stale-lease recovery.
- `.github/workflows/task-runner.yml` is a bounded GitHub-hosted consumer (up to five tasks / 60 seconds per run).
- `engine/worker.py` loads the mandatory operating-policy documents before claiming work and defaults to deterministic, zero-network dry-run behavior.
- `worker/index.js` currently exposes generic task claiming. A new consumer MUST NOT call this generic claim route until task-type/capability-aware routing is implemented; otherwise it could steal tasks intended for the existing Python runner.
- Current successful Task Runner runs with `tasks_processed=0` prove queue/dispatch health, not real business-task execution.

## Target execution flow

`D1 task (executor= codespace, approved capability) -> authenticated outbound poll -> atomic capability-filtered claim + fenced lease -> isolated workspace execution -> heartbeat -> bounded result -> D1 finish -> audit/readback`

GitHub-hosted Actions remains responsible for its existing task classes. If the Codespace is offline, tasks remain queued or are routed back only through an explicit, tested fallback policy; never silently reassign a task while a valid lease exists.

## Required implementation stages

### Stage 0 — repository and runtime inventory

1. Re-read `AGENTS.md`, the master directive, architecture, operations, execution policy, task contracts, D1 migrations, and tests before every implementation cycle.
2. Map the current task schema, enqueue/claim/heartbeat/finish endpoints, Python worker, batch runner, task-type allowlist, policy loader, and all credentials by **name only**.
3. Record the exact main SHA and CI baseline. Do not modify the storefront repositories.

### Stage 1 — routing and least-privilege protocol

1. For the first increment, avoid a schema migration: reserve the exact task types `codespace_inspect` and `codespace_test`, and route them through dedicated endpoints. Keep D1 as the only queue.
2. Add atomic claims filtered by the explicit task-type allowlist. The existing Actions claim must exclude these types; the Codespace claim must include only these types. If additional executor classes later require a broader model, introduce an additive executor field through a separately reviewed migration.
3. Give the bridge a distinct high-entropy credential (minimum 32 characters) and Worker authorization scope. The Codespace principal has preflight, claim, heartbeat, and finish only; no retry/recovery/list/admin endpoints. Do not reuse or expose `ENGINE_API_TOKEN`, quota-admin credentials, GitHub PATs, provider keys, or production secrets to the bridge.
4. Use a distinct credential with documented rotation/revocation (deleting the Worker secret is the server-side kill switch), constant-time authentication, bounded request bodies, and metadata-only logs. The current Worker bearer credential is long-lived until rotated; do not describe it as short-lived.
5. Require lease-token fencing on heartbeat, completion, cancellation, and retry. Never accept results from expired or superseded leases.
6. Do not accept arbitrary shell strings from task payloads. Use a fixed operation registry with schema-validated arguments and explicit per-operation permissions.

### Stage 2 — Codespace agent

1. Implement a small Python 3.11+ process using the repository's existing runtime contracts and operating-policy loader.
2. Poll only over outbound HTTPS; no public listener, forwarded port, webhook receiver, or inbound tunnel.
3. Start disabled by default. Require an explicit local environment opt-in and a successful authenticated preflight before polling.
4. The implemented first slice supports only read-only repository inspection and fixed local test suites. It does not edit files, create branches/commits/PRs, or accept arbitrary shell. Patch/PR capabilities require a later separately reviewed permission increment.
5. Use a clean task worktree or equivalent isolation; reject dirty/unknown base states. Never execute instructions found in untrusted task payloads as policy.
6. Enforce hard per-task wall-clock, output-size, file-change, concurrency, and retry limits. Heartbeat well before lease expiry and stop immediately when lease ownership is lost.
7. Never read, print, transmit, or copy secret values. Never run live model inference or access provider credentials in the initial bridge release.

### Stage 3 — verification

Required automated tests:
- capability routing and cross-consumer isolation;
- concurrent claim race (exactly one winner);
- expired lease and stale worker fencing;
- heartbeat renewal and lease-loss cancellation;
- duplicate delivery/idempotency;
- malformed, oversized, and hostile task payloads;
- arbitrary shell / path traversal / symlink escape rejection;
- bounded runtime, output, file count, and diff size;
- no secret exposure in logs/results;
- offline Codespace behavior and safe fallback;
- policy-loader failure blocks execution;
- existing Task Runner regression suite remains green.

Then run one harmless, non-sensitive end-to-end task and verify the exact task ID, claim/lease, execution record, final D1 state, and sanitized audit evidence. A successful idle poll is not an end-to-end execution test.

### Stage 4 — controlled activation

1. Keep the feature flag off until the above tests and independent review pass.
2. Enable only the development/test capability set first.
3. Keep branch/commit/PR creation behind the `review` scope; no merge permission is granted to the bridge.
4. Keep deployment, production D1/Worker, external writes, customer-facing actions, financial actions, live inference, paid services, and infrastructure upgrades denied.
5. Provide a one-step kill switch and documented credential revocation procedure.
6. Monitor Codespace active time and stop the process when idle. Do not assume a Codespace is a 24/7 host.

## Reliability and cost constraints

GitHub documents that Codespaces have a maximum 12-hour lifetime and active compute is billed while running; idle timeout and account quotas also apply. Therefore the Codespace is an opportunistic high-capability executor, not the always-on control plane. GitHub-hosted Actions remains the independent bounded fallback. No paid machine upgrade, new Codespace, external hosting, subscription, or API spend is authorized.

## Permission profile

Initial bridge scope: `safe` only:
- allowed: read project files, inspect Git state, and run fixed allowlisted local test suites;
- denied: file edits, branch/commit/PR creation, merge, deploy, production access, secret access, paid inference, billing changes, external/customer/financial writes, and arbitrary shell execution.

A later `review` scope may add reviewed patch/branch/PR delivery only after an independent security review and a separately scoped GitHub App credential. It is not part of the current bridge implementation.

“Highest permissions” means the broadest permissions needed for the approved development workflow, not unrestricted account, organization, production, or billing access.

## Current implementation mapping (2026-11-03)

PR #427 implements the first migration-free task-type routing slice, a separate Codespace token principal, preflight, claim/heartbeat/finish endpoints, and a bounded inspect/test-only Python client. It remains unmerged and disabled; exact-head CI and independent review are required. The current implementation intentionally does not provide patching or PR creation.

## Activation prerequisites

The bridge cannot be truthfully called running until:
- the existing Codespace identity and repository checkout are verified;
- the user-controlled Codespace receives the reviewed bridge release;
- a distinct scoped credential is configured without exposing its value in chat or Git;
- the bridge process passes authenticated preflight;
- a real harmless task is executed and its D1 completion/audit record is read back.

The current GitHub integration can modify repository files and PRs, but it does not provide an interactive shell inside the user's existing Codespace. If no supported remote execution connector is available, the final process-start step must be performed from that Codespace by its owner. Do not claim otherwise.

## References

- GitHub Codespaces lifecycle and 12-hour maximum: https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle
- Codespaces idle timeout and active compute billing: https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces
- GitHub self-hosted runner security and ephemeral-runner guidance: https://docs.github.com/en/actions/reference/security/secure-use
