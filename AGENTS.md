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

AndroidHarness is registered in `config/runtime_backends.json` and documented in `docs/android-harness.md`. Treat it as an optional external Android/Linux runtime. Before assigning work to it, verify that an authenticated live harness session/tool is actually available; documentation or an operator-reported snapshot does not grant remote access. Follow the runtime's bounded-command, workspace, no-secret, no-root-by-default, resource-limit, and audit requirements. Until a tested adapter exists, do not claim the engine can invoke AndroidHarness autonomously. Use it for compatibility experiments and device-side tests only when its session is available; keep GitHub Actions as reproducible CI and retain all existing policy gates.

## Runtime enforcement requirement

The Python engine worker now loads and validates all five required policy documents at the start of every `run_once` cycle, before claiming a task. It reads the full documents locally, then supplies a compact trusted execution-policy summary plus SHA-256 fingerprints to the live model prompt before untrusted task payload. Regression tests cover missing/empty policy, fail-closed behavior, and prompt ordering. This enforcement applies to the Python worker/CLI Task Runner path; every new agent or provider invocation path must reuse `load_operating_policy()` and add equivalent tests. Do not imply that unrelated services or future runtimes inherit this enforcement automatically.
