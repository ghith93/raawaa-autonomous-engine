# Operations and contributor guide

## Current operating mode

Core v1 is merged. The system has a local SQLite task store, structured memory, deterministic workflow planner, provider discovery, quota-aware routing primitives, a provider-neutral model gateway, and a CLI.

The default posture remains conservative:

- Worker execution is dry-run oriented; do not treat it as an always-on production worker.
- Paid model calls and external writes are disabled by default.
- Provider catalog discovery is read-only and requires a configured provider credential.
- Live inference adapters are opt-in code paths; do not enable them until provider eligibility, quota limits, and credentials are explicitly configured and verified.
- Safe task types are now assigned to a named specialist role and required capability through a deterministic contract. This remains a dry-run assignment: it does not mean a persistent multi-agent team or model inference is running.

## Requirements and checks

Python 3.11 or later is required. Runtime dependencies are kept minimal.

```bash
python -m engine.cli audit
python -m unittest discover -s tests -v
```

CI runs the policy/agent-roster audit and unit tests on pushes to `main`, `bootstrap/**`, `feature/**`, and `fix/**`, and on pull requests targeting `main`. A workflow file is not proof of a successful run; verify the Actions result for the exact commit SHA.

## Task operations

```bash
python -m engine.cli enqueue --type research_brief --title "Example" --payload '{"topic":"agent frameworks"}'
python -m engine.cli list
python -m engine.cli run-once
python -m engine.cli audit-log
```

The SQLite store supports task lifecycle operations, idempotency, bounded retries, and stale-task recovery. The current worker is a bounded invocation, not a persistent service. The optional remote D1 task adapter is selected only when both `RAAWAA_ENGINE_API_URL` and `RAAWAA_ENGINE_API_TOKEN` are configured; otherwise SQLite remains the default. The remote adapter covers enqueue, list, claim, heartbeat, finish, retry, and stale-lease recovery. Memory and model-usage ledgers remain local SQLite. D1 leases are capped at 15 minutes. This adapter does not make task execution persistent or enable external actions; those still require handler-level idempotency and explicit policy gates.

## Memory operations

```bash
python -m engine.cli memory-add --category decision --content "Example decision" --source "operator"
python -m engine.cli memory-search --query "Example"
python -m engine.cli memory-list
python -m engine.cli memory-archive --id MEMORY_ID
```

Memory is local SQLite storage in the current implementation. Do not store API keys, passwords, sensitive customer data, or private order details in long-term memory. Back up and protect the database if it contains operational decisions.

## Model and provider operations

```bash
python -m engine.cli model-status
python -m engine.cli free-models --provider all
```

Provider catalog discovery is a deliberate, read-only network command. It requires the relevant provider credential and lists model identifiers only; it does not generate text or establish that a model is free. Treat unknown pricing, quota, regional availability, and commercial-use eligibility as unavailable.

The model gateway and quota controls provide foundations for opt-in inference. Keep live inference disabled until:
1. the provider's current free-tier and commercial-use terms are verified;
2. token and request limits are configured conservatively;
3. credential handling and response validation tests pass;
4. quota reservation and settlement behavior is verified;
5. a clear operator kill switch and audit trail are available.

Never add paid fallback or automatic billing upgrades without explicit approval.

## GitHub Actions and deployment

GitHub Actions runs CI and a scheduled, bounded D1 queue consumer every 15 minutes. Each invocation runs at most five tasks or admits new tasks for at most 60 seconds, whichever comes first; GitHub schedule delivery can be delayed and is not a real-time SLA. The Cloudflare Worker separately recovers expired leases every 15 minutes. Neither scheduler is an always-on process.

Before selecting a host, verify free-tier limits, sleep behavior, persistent disk availability, outbound network rules, and whether the service can run without a payment method. Do not assume a free trial is a recurring free tier.

## Secrets and permissions

Never commit API keys, access tokens, passwords, customer data, or private order data. Configure secrets only through repository Settings → Secrets and variables → Actions, and only for a reviewed integration that needs them. Prefer short-lived, narrowly scoped credentials. Do not paste secrets into chat, issues, logs, or memory.

Agents may research and propose changes. Policy gates must control execution. Keep external writes, deployment, financial actions, and consequential customer-facing operations behind explicit approval.

## Change process

- Work on an isolated feature branch and open a pull request.
- Add regression tests for behavior and policy changes.
- Mock provider calls in tests; do not use real paid inference in CI.
- Verify the CI result against the exact PR head SHA.
- Review permission, budget, integration, and deployment changes carefully.
- Never bypass a failing test or a policy gate to force a merge.

## Known limitations and next steps

- No persistent scheduler or always-on worker.
- No unattended autonomous agent loop.
- No notification channel.
- No production deployment.
- Long-running task handlers still need periodic heartbeat renewal; the stores already enforce lease-token ownership fencing, and external side effects need handler-level idempotency before activation.
- Local SQLite memory and task state need an explicit backup/retention strategy for production.

Recommended next work: harden task ownership and concurrent recovery, add side-effect handler contracts, then implement a bounded scheduler/worker on a verified free persistent host.

## Diagnosing the authenticated D1 task API

The scheduled task runner performs a read-free authenticated POST preflight to `/v1/diagnostics/auth` before it calls any task-management endpoint. A successful preflight returns HTTP 200 with an authentication confirmation and does not read or write D1. It exists to separate bearer-token/edge failures from failures in an individual task route.

Interpret the preflight result conservatively:

- **200:** the POST reached the Worker and the bearer token was accepted. If a later task endpoint fails, investigate that endpoint's route, method, handler, and D1 binding rather than changing the shared authentication policy.
- **401:** the request reached the Worker authentication guard, but the supplied token was not accepted. Verify that the GitHub Actions secret and deployed Worker secret were synchronized by the reviewed deployment workflow. Never print or paste either secret.
- **403:** inspect the safe `CF-Ray` and `cf-mitigated` response headers, when present, to determine whether Cloudflare edge controls intervened. Do not infer that every 403 is a WAF event, and do not disable Access, WAF, or authentication as a workaround.
- **5xx or transport failure:** investigate Worker execution, availability, and D1 binding health. Do not retry task mutations blindly unless their idempotency contract makes that safe.

The endpoint accepts POST only; GET returning 405 is expected and is not evidence that authenticated POST is broken. An unauthenticated task-list GET returning 401 is also expected. Neither result substitutes for the scheduled runner's authenticated POST test.

The preflight does not prove that `/v1/tasks/recover`, claim, heartbeat, or finish works. A successful end-to-end verification requires a subsequent scheduled Task Runner execution to pass the preflight and complete the bounded task batch. Check the exact Actions run and job logs; do not claim the 403 is resolved from a successful Worker deployment or CI run alone.


## Quota ledger and live-adapter safety

The optional quota-aware live gateway is disabled by default. When explicitly enabled in a controlled test or approved deployment, it selects only a route with an HTTPS endpoint, separate HTTPS pricing/quota/terms evidence URLs (valid hostnames, no embedded credentials), a timezone-aware verification timestamp no more than 30 days old, verified free-tier and commercial-use eligibility, daily request/token caps, and a maximum output-token ceiling. Missing, malformed, future-dated, stale, or unknown profile data is not eligible. The gateway enforces the output ceiling before reserving quota or contacting an adapter. No real provider profile is currently configured by this repository.

Before provider I/O, the gateway reserves one request and a conservative token estimate in SQLite using an immediate write transaction. Concurrent reservations therefore compete against the same persisted daily caps. On a valid response, the reservation is settled to actual token usage. Settlement is idempotent only when a repeated call reports the same actual token count; a conflicting second settlement fails closed. Existing quota databases are migrated additively.

For timeouts, transport failures, malformed responses, and unexpected adapter exceptions, retain the full estimate because the provider may already have processed the request. HTTP 429 applies a route cooldown. Do not release an ambiguous reservation or automatically retry the same request against another route unless the provider outcome is known to be safe.

The live gateway rejects mocked completions, empty text, mismatched provider/model identities, invalid token counts, and non-finite or negative costs. A verified-free route that reports a non-zero cost is quarantined; the gateway does not return the completion or rotate to another provider. The adapter's placeholder `cost_usd=0.0` is not evidence of free usage. Verify account-specific provider terms and pricing independently before enabling any live route.

The quota ledger currently enforces daily request and token limits. Do not assume the optional `monthly_credit_usd` profile field is an active monthly monetary cap. Paid model calls remain disabled by default, and there is no automatic paid fallback.
