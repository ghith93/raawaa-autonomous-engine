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

1. Extend the existing D1 task record/API additively with a validated executor/capability route; keep D1 as the only queue.
2. Add an atomic claim operation filtered by executor and allowlisted task capabilities. Prove the existing Actions consumer cannot claim Codespace-only work and vice versa.
3. Give the bridge a distinct credential and Worker authorization scope. Do not reuse or expose `ENGINE_API_TOKEN`, quota-admin credentials, GitHub PATs, provider keys, or production secrets to the bridge.
4. Use short-lived credentials where supported, rotation/revocation, constant-time authentication, bounded request bodies, rate limits, and metadata-only logs.
5. Require lease-token fencing on heartbeat, completion, cancellation, and retry. Never accept results from expired or superseded leases.
6. Do not accept arbitrary shell strings from task payloads. Use a fixed operation registry with schema-validated arguments and explicit per-operation permissions.

### Stage 2 — Codespace agent

1. Implement a small Python 3.11+ process using the repository's existing runtime contracts and operating-policy loader.
2. Poll only over outbound HTTPS; no public listener, forwarded port, webhook receiver, or inbound tunnel.
3. Start disabled by default. Require an explicit local environment opt-in and a successful authenticated preflight before polling.
4. Support only non-production development operations initially: inspect repository state, run allowlisted tests/static checks, create a task-scoped branch, apply reviewed patch operations, and return bounded sanitized results.
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

Initial bridge scope: `safe + review` only:
- allowed: read project files, run allowlisted local checks, edit task-scoped branches, commit, and open draft PRs;
- denied: merge, deploy, production access, secret access, paid inference, billing changes, external/customer/financial writes, and arbitrary shell execution.

“Highest permissions” means the broadest permissions needed for the approved development workflow, not unrestricted account, organization, production, or billing access.

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
