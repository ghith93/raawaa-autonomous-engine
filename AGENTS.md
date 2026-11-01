# Agent startup policy

Before every work cycle, every executive/lead agent, specialist, coding agent, reviewer, and AI-driven automation MUST read, in order:
1. `docs/master-autonomous-execution-directive.ar.md`
2. `docs/continuous-improvement-charter.md`
3. `docs/architecture.md`
4. `docs/operations.md` and applicable subsystem policies.

These are mandatory repository policies, not optional context. Re-read them each cycle; do not rely only on chat history or cached summaries. If a runtime cannot read them, report that limitation and do not claim policy loading succeeded.

A user message consisting only of `.` means resume autonomous execution from the latest verified repository state: inspect current GitHub state, PRs/issues, exact-head CI, workflows, deployments, and persisted task/memory state; execute the highest-priority safe unblocked task; test, review, update durable state, and continue. Do not return only a plan, ask for routine technical approval, or repeat completed work.

Use the highest legitimate access already granted and needed. No new spending, paid subscription, paid API use, or potentially billable action without explicit approval. Existing security, privacy, data-classification, provider eligibility, quota, kill-switch, deployment, external-write, and irreversible-action gates remain binding. Do not modify storefront repositories. Use isolated branches and merge only after exact-head validation. Never expose secret values. Distinguish implementation, tests, merge, deployment, and runtime verification; claims require evidence.

## Runtime enforcement requirement

The engine's own orchestrator/agent prompt-construction path must load the directive and applicable policies as mandatory context before each agent task. Add tests proving this. AGENTS.md is repository-level guidance only; it does not prove deployed workers automatically read the directive. Until runtime loading is implemented and verified, state this limitation accurately.
