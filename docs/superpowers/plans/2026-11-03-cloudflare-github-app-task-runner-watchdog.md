# Cloudflare GitHub App Task Runner Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a fail-closed Cloudflare Cron watchdog that dispatches the existing GitHub Task Runner only when its GitHub schedule is stale and no run is active.

**Architecture:** Add a small Worker module for GitHub App JWT/token creation and a separate watchdog module for run inspection and fixed workflow dispatch. Integrate it into the existing scheduled handler with a second Cron expression; retain D1 as the task ledger and retain the existing GitHub schedule as the primary path.

**Tech Stack:** Cloudflare Workers Web Crypto and fetch; GitHub REST API; Node built-in test runner; existing D1 audit log. No new dependencies.

**Spec:** docs/superpowers/specs/2026-11-03-cloudflare-github-app-task-runner-watchdog-design.md

## Global Constraints

- Preserve safe_dry_run, zero budgets, disabled live inference, and disabled external writes.
- No paid APIs, new subscriptions, or personal access tokens.
- The GitHub App is restricted to one repository and Actions: write.
- The watchdog can dispatch only task-runner.yml on main.
- Missing or malformed credentials fail closed; secret values never enter logs, D1, Git, or chat.
- Preserve the existing 15-minute stale-lease recovery Cron and D1 task ledger.
- No D1 migration or new runtime dependency.
- Production deployment remains separate from merge and requires the existing deployment gate.

## Review Focus

- PKCS#1 versus PKCS#8 PEM inputs must both import correctly without logging key material.
- GitHub API errors, invalid JSON, and malformed responses must fail closed with sanitized errors.
- A queued/running workflow must suppress a new dispatch.
- Any recent Task Runner run on main must suppress a new dispatch, regardless of trigger type or conclusion.
- A stale or absent Task Runner run must cause at most one fixed workflow dispatch, protected by an atomic D1 dispatch-intent lock.
- The watchdog Cron and recovery Cron must not execute each other's work.

---

### Task 1: GitHub App authentication

**Files:**
- Create: worker/github-app-auth.js
- Create: tests/github_app_auth.test.js

**Interfaces:**
- createAppJwt({ appId, privateKeyPem, nowSeconds, cryptoImpl }) -> Promise<string>
- createInstallationToken({ appId, installationId, privateKeyPem, fetchImpl, cryptoImpl, nowSeconds }) -> Promise<string>
- The token request is fixed to repository ID 1441352567 and permission actions: write.

- [x] Step 1: Write failing tests for PKCS#1 wrapping, PKCS#8 pass-through, JWT claims/signature, and installation token scoping.
- [x] Step 2: Run the targeted tests and confirm failure because the module does not exist.
- [x] Step 3: Implement minimal Web Crypto authentication with no dependencies; use RSASSA-PKCS1-v1_5 + SHA-256 and short-lived JWT claims.
- [x] Step 4: Run targeted tests and verify signature and request body using ephemeral test keys.
- [x] Step 5: Run all Worker tests and commit.

### Task 2: Stale-schedule watchdog

**Files:**
- Create: worker/task-runner-watchdog.js
- Create: tests/task_runner_watchdog.test.js

**Interfaces:**
- dispatchTaskRunnerIfStale({ env, fetchImpl, nowMs, scheduledTime }) -> Promise<{ status, reason?, httpStatus? }>
- Fixed repo: ghith93/raawaa-autonomous-engine
- Fixed workflow: task-runner.yml
- Fixed ref: main
- Stale threshold: 10 minutes.

- [x] Step 1: Write failing tests for absent settings (zero network calls), active-run suppression, recent-run suppression for schedule/workflow_dispatch/push events, D1 intent deduplication, stale-run dispatch, HTTP 200/204 success, malformed responses, and sanitized errors.
- [x] Step 2: Run targeted tests and confirm expected failures.
- [x] Step 3: Implement run inspection and fixed workflow dispatch using the Task 1 token helper.
- [x] Step 4: Verify that no caller input can alter repository, workflow, branch, permission, or API host.
- [x] Step 5: Run all Worker tests and commit.

### Task 3: Cron integration

**Files:**
- Modify: worker/index.js
- Modify: wrangler.toml
- Modify: tests/worker.test.js

**Interfaces:**
- Recovery Cron: */15 * * * * — unchanged.
- Watchdog Cron: 1,6,11,16,21,26,31,36,41,46,51,56 * * * * (two minutes after the primary Task Runner cron).
- Dispatch configuration: GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY.

- [x] Step 1: Write failing Worker tests proving each Cron expression invokes only its intended handler and the watchdog is dormant with missing configuration.
- [x] Step 2: Run the targeted Worker tests and confirm the expected failure.
- [x] Step 3: Integrate the two explicit Cron paths; keep recovery heartbeat behavior unchanged.
- [x] Step 4: Run npm run test:worker and verify all existing scheduled recovery tests remain green.
- [x] Step 5: Commit.

### Task 4: Operations documentation

**Files:**
- Modify: docs/operations.md
- Modify: README.md
- Create: docs/operations/github-app-task-runner-watchdog.md

- [x] Document GitHub App creation and one-repository installation.
- [x] Document exact permission: Actions: write only.
- [x] Document the three Cloudflare settings and mobile-friendly dashboard path; never ask for private-key text in chat.
- [x] Document the disabled-before-configuration behavior, dispatch verification, and rollback.
- [x] Distinguish GitHub scheduled runs, watchdog dispatches, and actual completed Task Runner batches.

### Task 5: Final validation and merge

- [ ] Run Python unit tests, Worker tests, local D1 smoke, and Cloudflare Workflows smoke via existing CI.
- [ ] Inspect the full diff and confirm no secrets or new dependencies.
- [ ] Verify exact-head CI on the PR SHA.
- [ ] Merge only after all checks pass.
- [ ] Deploy only through the existing reviewed Worker deployment workflow; no production D1 migration.
- [ ] Verify the dormant watchdog produces no network/D1 side effects before App configuration.
- [ ] After the owner configures App settings, verify a real workflow_dispatch run and its bounded Task Runner result separately.

## Execution ledger

- Isolated workspace: dedicated GitHub feature branch. A local checkout/worktree was not available through the connected repository tool, so implementation and tests were driven through branch commits and GitHub Actions CI.
- Task 1 (GitHub App authentication): tests were added before implementation; the initial CI red run confirmed the missing module. Implemented RS256 JWT signing, PKCS#1/PKCS#8 PEM support, scoped installation-token exchange, sanitized errors, and required User-Agent header.
- Task 2 (watchdog): tests were added before implementation; the initial CI red run confirmed the missing watchdog. Implemented fixed workflow targeting, active/recent-run suppression across schedule, push, and workflow_dispatch events, atomic D1 dispatch-intent reservation, sanitized accepted-dispatch audit, and fail-closed configuration.
- Task 3 (Cron integration): tests first failed on missing watchdog routing. Added the offset watchdog Cron while preserving the 15-minute recovery Cron. A review caught and corrected a Cron-expression mismatch between wrangler.toml, Worker handler, and tests; all now use 1,6,11,16,21,26,31,36,41,46,51,56 UTC.
- Task 4 (operations docs): added the mobile-friendly GitHub App setup guide, deployment/rollback instructions, and explicit disabled-by-default behavior. No private key or App credentials were added to the repository.
- Security review: a missing User-Agent header on the App token exchange and missing-D1 preflight were fixed test-first. Durable dispatch intent is retained if the post-dispatch audit write fails, preventing immediate duplicate requests.
- Related security PR #292 was independently repaired, passed full CI run #41558964883, and was merged as #1db70e2. It tightens live inference to explicitly classified public payloads and is separate from this Worker watchdog.
- Current feature PR head: a23a1d4e137dee289e0a965165e7a7d259379357. Exact-head CI run #41559223218 is pending; do not merge or deploy until it completes successfully.

## Stop conditions

Stop activation (not documentation or testing) if GitHub App installation, Cloudflare secrets, or Actions write permission is missing. Do not substitute a personal token, ask for the private key in chat, or weaken the permission boundary.
