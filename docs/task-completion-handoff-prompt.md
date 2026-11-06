# Reusable Task-Completion Handoff Prompt

Copy and send this prompt after a task or whenever you want work to continue (or send `.` alone to resume from the latest verified state). An Arabic version is available in `task-completion-handoff-prompt.ar.md`:

---

Continue the existing Raawaa Autonomous Engine project from the **latest verified repository and GitHub Issue #464 state**, not from chat memory alone. Do not start over, rebuild existing components, or treat this message as a new project.

1. Run `python3 scripts/session_continuity.py status --fetch-live` first, read `AGENTS.md`, read the authoritative operating policy at the top of `GitHub Issue #464`, and read every unprocessed comment on `#464` after the effective `Comment ID` watermark. Then inspect current `main`, recent commits, open issues and PRs, exact CI/workflow results, tests, and the canonical capability/integration/workflow registers before changing anything.
2. Read and follow `docs/master-autonomous-execution-directive.ar.md`, `docs/continuous-improvement-charter.md`, `docs/architecture.md`, `docs/operations.md`, and `docs/integrations/arena-agent-mode.ar.md`. The existing architecture and safety/cost gates take precedence; extend existing components instead of creating parallel memory databases or duplicate agent systems.
3. Verify what was actually completed in the previous cycle. Never ask the owner to manually copy/paste reports between chats: `GitHub Issue #464` is the permanent communication channel (noting explicitly that Arena has no supported automatic session-wake API and unofficial wake/scraping interfaces are prohibited).
4. Select the highest-impact safe and unblocked task. Research, implement, test, review, document, and verify it. Do not stop at planning or merely give recommendations.
5. Reuse existing agents, tools, workflows, memory, schedulers, quota controls, and integrations wherever possible. Parallelize independent workstreams on separate branches/PRs without overlapping file ownership, and integrate only after exact-head CI passes.
6. Continue to the next safe task after each completed task. Do not wait for approval for routine technical decisions or ordinary code/test/documentation work.
7. Preserve strict **USD 0 spend** (`$0` ceiling), `safe_dry_run` defaults, disabled paid calls/fallback, provider quota protections, and data-classification gates. Free-tier quotas may be used only when verifiably zero-cost and incapable of triggering billing.
8. Use only permissions and integrations actually available. Never expose secrets or ask me to repeat a setup that is already verified. If a genuine external blocker remains, specify the exact action and minimum permission needed, then continue independent work.
9. For every change, use an isolated branch and appropriate tests. Inspect the diff and verify CI against the exact PR head SHA. Do not merge untested or failing changes.
10. Update `config/session_continuity.json` when committing repository work, and post a durable `ARENA REPORT` checkpoint comment on `GitHub Issue #464` recording the highest processed `Comment ID`, exact `main`/PR SHAs, CI/workflow run IDs, deployed vs unverified boundaries, confirmed `USD 0` spend, and next bounded task.
11. End with a concise summary: what changed, evidence/links, tests and CI status, blockers (if any), and the next task.

**Standing instruction:** Treat this as authorization to continue the existing work autonomously within the project's already granted permissions and USD 0 safety policy—not as authorization to spend money, bypass safeguards, expose secrets, or use unofficial wake interfaces.

---
