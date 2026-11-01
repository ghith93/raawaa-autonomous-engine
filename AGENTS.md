# Agent startup policy

Before every work cycle, every executive/lead agent, specialist, coding agent, reviewer, and AI-driven automation MUST read, in order:
1. `docs/master-autonomous-execution-directive.ar.md`
2. `docs/continuous-improvement-charter.md`
3. `docs/architecture.md`
4. `docs/operations.md` and applicable subsystem policies.

These are mandatory repository policies, not optional context. Re-read them each cycle; do not rely only on chat history or cached summaries. If a runtime cannot read them, report that limitation and do not claim policy loading succeeded.

A user message consisting only of `.` means resume autonomous execution from the latest verified repository state: inspect current GitHub state, PRs/issues, exact-head CI, workflows, deployments, and persisted task/memory state; execute the highest-priority safe unblocked task; test, review, update durable state, and continue. Do not return only a plan, ask for routine technical approval, or repeat completed work.

Use the highest legitimate access already granted and needed. No new spending, paid subscription, paid API use, or potentially billable action without explicit approval. Existing security, privacy, data-classification, provider eligibility, quota, kill-switch, deployment, external-write, and irreversible-action gates remain binding. Do not modify storefront repositories. Use isolated branches and merge only after exact-head validation. Never expose secret values. Distinguish implementation, tests, merge, deployment, and runtime verification; claims require evidence.

## AndroidHarness execution policy

AndroidHarness is registered in `config/runtime_backends.json`, configured by `harness.json`, guided by `.harness/skills/raawaa-engine-androidharness/SKILL.md`, and documented in `docs/android-harness.md`. Use `python scripts/androidharness/doctor.py --json` for a read-only environment snapshot. Treat it as an optional external Android/Linux runtime. Before assigning work to it, verify that an authenticated live harness session/tool is actually available; documentation or an operator-reported snapshot does not grant remote access. Follow the runtime's bounded-command, workspace, no-secret, no-root-by-default, resource-limit, and audit requirements. A bounded GitHub issue bridge is implemented for four fixed operations only; it is not a direct MCP or general tool session. The engine may dispatch those operations only after the local poller has been started and a GitHub issue contains the result posted by that poller. For all other device operations, require an authenticated live harness session. Do not claim general remote access. Keep GitHub Actions as reproducible CI and retain all existing policy gates.

## Runtime enforcement requirement

The Python engine worker now loads and validates all five required policy documents at the start of every `run_once` cycle, before claiming a task. It reads the full documents locally, then supplies a compact trusted execution-policy summary plus SHA-256 fingerprints to the live model prompt before untrusted task payload. Regression tests cover missing/empty policy, fail-closed behavior, and prompt ordering. This enforcement applies to the Python worker/CLI Task Runner path; every new agent or provider invocation path must reuse `load_operating_policy()` and add equivalent tests. Do not imply that unrelated services or future runtimes inherit this enforcement automatically.


## AndroidHarness issue bridge policy

The optional GitHub-mediated AndroidHarness issue bridge is implemented in scripts/androidharness_issue_bridge.py but remains inactive until a local AndroidHarness process is explicitly started. It is not direct MCP or general remote shell. Only tasks authored by ghith93 with the exact task title prefix and schema are eligible. The only allowed operations are environment inspection, Git status inspection, Python unit tests against the exact fetched origin/main SHA, and the project's read-only AndroidHarness doctor. Python tests run under the AndroidHarness Linux/app UID and are not OS-sandboxed; only use this operation for trusted owner-controlled main code. Never enqueue arbitrary commands, root operations, package installs, provider calls, Cloudflare operations, production writes, or deployment through this bridge. It must verify the five mandatory policy documents, use bounded time/output, keep a private audit log, and use the existing local GitHub CLI auth without exposing its token. Do not claim a device task ran until the GitHub issue contains the result posted by the running bridge.
