# Agent startup policy

Before every work cycle, every executive/lead agent, specialist, coding agent, reviewer, and AI-driven automation MUST read, in order:
1. `docs/master-autonomous-execution-directive.ar.md`
2. `docs/continuous-improvement-charter.md`
3. `docs/architecture.md`
4. `docs/operations.md`, `docs/operations/asset-register.md`, `docs/operations/tooling-capability-inventory-2026-11-03.md`, `config/integrations.json`, `config/tool_permissions.json`, and applicable subsystem policies.

These are mandatory repository policies, not optional context. Re-read them each cycle; do not rely only on chat history or cached summaries. If a runtime cannot read them, report that limitation and do not claim policy loading succeeded.

A user message consisting only of `.` means resume autonomous execution from the latest verified repository state: inspect current GitHub state, PRs/issues, exact-head CI, workflows, deployments, and persisted task/memory state; execute the highest-priority safe unblocked task; test, review, update durable state, and continue. Do not return only a plan, ask for routine technical approval, or repeat completed work.

Use the highest legitimate access already granted and needed. The operator's current mandate is a strict zero-spend rule: USD 0 per billing month for this project, with every monetary cap in the active policy (`config/policy.json`) set to exactly USD 0 and paid model calls and paid fallback disabled. Only verified zero-cost routes may ever be selected, and unknown pricing is blocked, never treated as free. (Superseded historical policy: a USD 15 all-in envelope with a USD 12 model allocation applied until the owner's 2026-11-05 zero-spend directive; do not reintroduce any non-zero cap without a new explicit owner spend approval.) Paid model fallback remains disabled until provider-side hard-stop controls are independently verified and the policy is deliberately updated under such an approval. No automatic plan upgrades. Existing security, privacy, data-classification, provider eligibility, quota, kill-switch, deployment, external-write, and irreversible-action gates remain binding. Do not modify storefront repositories. Use isolated branches and merge only after exact-head validation. Never expose secret values. Distinguish implementation, tests, merge, deployment, and runtime verification; claims require evidence.
## Runtime enforcement requirement

The Python engine worker now loads and validates all five required policy documents at the start of every `run_once` cycle, before claiming a task. It reads the full documents locally, then supplies a compact trusted execution-policy summary plus SHA-256 fingerprints to the live model prompt before untrusted task payload. Regression tests cover missing/empty policy, fail-closed behavior, and prompt ordering. This enforcement applies to the Python worker/CLI Task Runner path; every new agent or provider invocation path must reuse `load_operating_policy()` and add equivalent tests. Do not imply that unrelated services or future runtimes inherit this enforcement automatically.


## Mandatory capability and asset preflight

Before creating a workflow, connector, agent, scheduler, provider route, account setup request, or new infrastructure, inspect the current tool/skill catalog, the dated tooling capability inventory, existing Activepieces projects/connections/flows, and the repository workflow tree. Distinguish catalog presence from an authenticated account, and distinguish ChatGPT-side connectors from credentials and adapters available inside the standalone engine. Reuse a verified existing mechanism before implementing another one. Refresh the durable inventory with evidence, last-verified time, permission scope, secret names only, costs/quotas, usage instructions, and limitations. Never record secret values.
