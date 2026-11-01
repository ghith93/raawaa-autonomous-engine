# Operations and contributor guide

## Current operating mode

Core v1 is merged. The system has a local SQLite task store, structured memory, deterministic workflow planner, provider discovery, quota-aware routing primitives, a provider-neutral model gateway, and a CLI.

The default posture remains conservative:

- Worker execution is dry-run oriented; do not treat it as an always-on production worker.
- Paid model calls and external writes are disabled by default.
- Provider catalog discovery is read-only and requires a configured provider credential.
- Live inference adapters are opt-in code paths; do not enable them until provider eligibility, quota limits, and credentials are explicitly configured and verified.
- Agent roster entries describe specialist roles; they do not mean a persistent multi-agent team is currently running.

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
- Side-effecting task handlers still need heartbeat/ownership fencing and handler-level idempotency before activation.
- Local SQLite memory and task state need an explicit backup/retention strategy for production.

Recommended next work: harden task ownership and concurrent recovery, add side-effect handler contracts, then implement a bounded scheduler/worker on a verified free persistent host.
