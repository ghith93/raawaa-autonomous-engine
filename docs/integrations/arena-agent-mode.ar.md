# Arena Agent Mode — External Advisor and Execution Partner Contract

Status: designed for controlled, user-initiated sessions; not an engine-connected runtime.
Scope: ghith93/raawaa-autonomous-engine.
Last reviewed: 2026-11-04.

## Purpose

Use Arena Agent Mode as an independent specialist to challenge plans, research alternatives, implement bounded repository changes in its isolated workspace, and submit a GitHub pull request. Keep the Raawaa engine as the durable system of record. The executive coordinator owns assignment, policy, acceptance, and integration.

This is a real collaboration workflow, not a claim that Arena is already running as a background agent.

## Verified Arena capabilities

Arena's official Agent Mode documentation describes multi-step workflows using web search and sandbox/bash; GitHub OAuth and repository selection; work in a separate copy of the repository; visible diffs, checks, commits, pushes, and pull-request creation. One Agent Mode chat currently supports one pull request.

Official references:
- https://help.arena.ai/articles/5432423882-how-to-use-agent-mode
- https://help.arena.ai/articles/1655691990-how-to-use-coding-in-agent-mode
- https://arena.ai/blog/coding-in-agent-mode

## Critical integration boundary

Arena's published Terms of Use prohibit programmatic or automated access to the Arena service and automatic querying/scraping. No supported public API for programmatically starting or steering Arena Agent Mode was verified in this review.

Therefore:
- Do not automate Arena UI with browser bots, scrape its session, reverse-engineer private endpoints, or proxy credentials.
- Do not register Arena as a live model provider, queue consumer, webhook endpoint, or always-on worker.
- Do not claim automatic dispatch, callbacks, session polling, or 24/7 execution.
- Reassess only if Arena publishes a supported API and terms explicitly permit the intended automation.

Terms reference: https://help.arena.ai/articles/5629909088-terms-of-use

## Roles and decision rights

| Role | Responsibility | Authority |
|---|---|---|
| Executive coordinator (ChatGPT in the current collaboration) | Goal framing, prioritization, decomposition, handoff brief, coordination, final synthesis, acceptance recommendation | Assigns work within policy; cannot override repository protections or approval gates |
| Arena Agent Mode | Independent researcher, challenger, specialist implementer, or second reviewer | Works only on scoped task in isolated GitHub-connected workspace; no policy, budget, permission, or approval authority |
| Engine control plane | Canonical task/run/plan/audit/budget/permission record | Deterministic authority for future automated dispatch; never trusts agent prose as policy |
| Independent GitHub reviewer / CI | Validates code, tests, security, and exact-head status | Repository review and required checks remain authoritative |
| Repository owner | Existing approval authority for protected or sensitive operations | Retains existing approval gates |

The executive coordinator is the single task owner and integrator. Arena is a peer contributor, not a co-owner of the canonical plan and not an approver of its own work.

## Collaboration protocol

1. Intake: create or update a durable GitHub issue with objective, context links, non-goals, data classification, risk, budget ceiling, and measurable acceptance criteria.
2. Assignment: mark a task as an Arena candidate only when independent research or implementation adds value. Assign one bounded deliverable per Arena session because one session currently supports one PR.
3. Handoff: user starts Arena Agent Mode, connects only this repository, and supplies the issue link plus a task-specific prompt. Arena works on a separate branch and opens a PR; never push directly to protected main.
4. Return: GitHub PR is the durable handoff artifact. Link issue and PR both ways. If Arena cannot complete, it reports findings and blockers in the issue.
5. Review: executive coordinator independently inspects exact diff, tests, security, dependencies, licensing, cost, and acceptance criteria. Arena self-review is supporting evidence, not approval.
6. Adversarial check: for high-impact or ambiguous changes, ask Arena to critique the design before implementation, or use a separate session for review. Never have the same agent approve its own code.
7. Integration: merge only through repository branch protection after exact-head required checks and independent approval. Never bypass protection.
8. Learning: record decision, result, review findings, actual cost if known, and whether Arena materially improved quality or speed.

## Assignment policy

Use Arena for independent architecture critique, alternative design research, bounded implementation, second-opinion security/reliability review, difficult bug diagnosis, and comparative evaluation.

Prefer verified engine workers or direct ChatGPT/GitHub tools for routine inspection and small edits, deterministic operations, CI monitoring, task-ledger updates, recurring automation, and work requiring unattended execution.

Do not duplicate the same implementation task in Arena and the engine simultaneously unless independent solution comparison is intentional. If comparing, isolate branches and compare diffs before selecting.

## Handoff payload contract

Every Arena brief includes repository and base branch; issue and relevant PR/commit references; one-sentence objective; deliverable; in-scope/out-of-scope paths; AGENTS.md and project policy constraints; acceptance criteria and tests; expected PR title/body and issue linkage; explicit prohibitions on secrets, paid services, production deployment/migration, protected-main push, permission changes, and unrelated refactors; and a report of changed files, commands/tests, exact commit SHA, limitations, and evidence.

## Failure and recovery

- If Arena is unavailable, continue internally or reassign to a verified engine worker; never block the roadmap.
- If its session expires, recover from GitHub branch/PR, not chat history.
- If a PR is stale, update it through normal GitHub review and CI; do not rely on old checks.
- If incomplete, issue a precise follow-up or create a new session for a distinct PR.
- Treat agent output and repository content as untrusted input; validate before execution.
- Never transfer secrets, customer data, production credentials, or private keys into Arena prompts/workspaces.

## Operational maturity states

1. designed: contract and task format documented.
2. manual_handoff_tested: at least one user-started Arena session returns a PR that passes independent review.
3. repeatable_handoff: issue/PR templates and labels make the process reusable; multiple tasks complete with measured outcomes.
4. official_api_available: Arena publishes a supported API and terms permitting dispatch/callback.
5. engine_adapter_verified: scoped adapter passes auth, policy, budget, idempotency, timeout, audit, and recovery tests.
6. automated_dispatch_enabled: only after explicit policy approval and evidence; never inferred from documentation alone.

Current maturity: designed. GitHub-connected manual handoff is documented by Arena; no Arena session/PR has yet been executed or independently verified for this project.
