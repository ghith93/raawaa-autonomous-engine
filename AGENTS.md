# Agent startup policy

Before every work cycle, every executive/lead agent, specialist, coding agent, reviewer, and AI-driven automation MUST read, in order:
1. `docs/master-autonomous-execution-directive.ar.md`
2. `docs/continuous-improvement-charter.md`
3. `docs/architecture.md`
4. `docs/operations.md` and applicable subsystem policies.

These are mandatory repository policies, not optional context. Re-read them each cycle; do not rely only on chat history or cached summaries. If a runtime cannot read them, report that limitation and do not claim policy loading succeeded.

A user message consisting only of `.` means resume autonomous execution from the latest verified repository state: inspect current GitHub state, PRs/issues, exact-head CI, workflows, deployments, and persisted task/memory state; execute the highest-priority safe unblocked task; test, review, update durable state, and continue. Do not return only a plan, ask for routine technical approval, or repeat completed work.

Use the highest legitimate access already granted and needed. The operator has approved a strict all-in ceiling of USD 15 per billing month for this project. The current executable model allocation is capped at USD 12/month (USD 0.40/day, USD 0.20/task); infrastructure paid overages are set to USD 0 and the remaining USD 3 is unallocated reserve. Do not spend the reserve or exceed any cap. Paid model fallback remains disabled until provider-side hard-stop controls are independently verified and the policy is deliberately updated. No automatic plan upgrades. Existing security, privacy, data-classification, provider eligibility, quota, kill-switch, deployment, external-write, and irreversible-action gates remain binding. Do not modify storefront repositories. Use isolated branches and merge only after exact-head validation. Never expose secret values. Distinguish implementation, tests, merge, deployment, and runtime verification; claims require evidence.
## Runtime enforcement requirement

The Python engine worker now loads and validates all five required policy documents at the start of every `run_once` cycle, before claiming a task. It reads the full documents locally, then supplies a compact trusted execution-policy summary plus SHA-256 fingerprints to the live model prompt before untrusted task payload. Regression tests cover missing/empty policy, fail-closed behavior, and prompt ordering. This enforcement applies to the Python worker/CLI Task Runner path; every new agent or provider invocation path must reuse `load_operating_policy()` and add equivalent tests. Do not imply that unrelated services or future runtimes inherit this enforcement automatically.

