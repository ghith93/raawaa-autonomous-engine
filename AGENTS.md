# Agent Startup Policy

Before planning, delegating, coding, reviewing, merging, or reporting in any session, every coding agent MUST read and follow:

1. `docs/master-autonomous-execution-directive.ar.md`
2. `docs/continuous-improvement-charter.md`
3. `docs/architecture.md`
4. `docs/operations.md`
5. `docs/integrations/arena-agent-mode.ar.md`
6. `config/session_continuity.json`

## Mandatory Session Start Protocol (Every New or Resumed Session)

Never rely on chat-window memory alone. Reconstruct live project state from GitHub and the repository first:

1. **Run the deterministic continuity preflight:**
   ```bash
   python3 scripts/session_continuity.py status --fetch-live
   ```
2. **Read the permanent coordination channel (`ghith93/raawaa-autonomous-engine` Issue `#464`):**
   - Read the authoritative `CURRENT OWNER-AUTHORIZED OPERATING POLICY` in the top description of Issue `#464`.
   - Read **every** comment on Issue `#464` with `Comment ID` greater than the effective watermark (`max(config/session_continuity.json.watermark.last_processed_comment_id, latest ARENA REPORT watermark on #464)`).
   - Never ask the owner to manually copy or paste messages/reports between chats; GitHub Issue `#464` is the permanent handoff and decision channel.
3. **Inspect the canonical capability, tool, workflow, integration, and policy registers before proposing or building anything:**
   - `config/policy.json`, `config/agents.json`, `config/provider_profiles.json`, `config/integrations.json`, `config/tool_permissions.json`
   - `docs/operations/asset-register.md`, `docs/operations/workflow-catalog.md`, `docs/operations/tooling-capability-inventory-2026-11-03.md`
   - Live `.github/workflows/*.yml`, open PRs, open issues, exact-head CI runs, and deployed service endpoints.
   - Note: `engine/memory.py` (`docs/memory-v1.md`) is runner-local SQLite and does **not** persist across ephemeral Arena sandboxes or GitHub Actions runners; durable cross-session state must live in GitHub (`#464` + tracked repository files). Do **not** build a parallel memory database or competing agent framework without proven need.
4. **Resume on `.`:** A user message consisting only of `.` means: run this Session Start Protocol, reconstruct the latest verified repository and `#464` state, select the highest-priority safe unblocked task, execute, verify exact-head CI, and continue.

## Manual-Wake Boundary (Explicit Technical Constraint)

- **No automatic Arena session wake exists:** GitHub Issue `#464` is a durable asynchronous inbox and audit log; posting a comment on `#464` or running a GitHub Actions workflow does **not** automatically wake or start an Arena Agent Mode session.
- **Strict prohibition on unofficial interfaces:** Never use undocumented/private Arena endpoints, UI scraping, or browser automation to simulate session wake-ups. A new or resumed Arena session starts only via a user-initiated message in Arena Agent Mode, which then immediately reads `AGENTS.md`, `config/session_continuity.json`, and Issue `#464`.

## Standing Execution & Cost Guardrails

- **USD 0 spend (`$0` hard ceiling):** Zero new monetary spend across all models, APIs, cloud services, and infrastructure. Genuinely free provider quotas are permitted only when billing cannot be triggered and existing safety/privacy gates are respected.
- **No weakening of safety gates:** Preserve established contracts, `safe_dry_run` defaults, secret-handling rules (names only, never values), least-privilege workflow guards, and repository separation (`ghith93/raawaa-autonomous-engine` vs external repos unless explicitly scoped for read-only review).

## Mandatory Session End Protocol

Before ending any substantive session:

1. **Update repository watermark when committing repo changes:**
   ```bash
   python3 scripts/session_continuity.py record --comment-id <HIGHEST_PROCESSED_COMMENT_ID> --comment-created-at <ISO8601_UTC> --main-sha <VERIFIED_MAIN_SHA> --summary "<concise verified state>"
   ```
2. **Post a durable checkpoint comment on GitHub Issue `#464`** beginning literally with `ARENA REPORT` and recording:
   - Highest processed `Comment ID` (watermark)
   - Exact `main` SHA and active branch/PR SHA(s)
   - Tests run and exact-head CI / workflow run IDs and conclusions
   - Verified vs unverified runtime/deployment boundaries
   - Provider/quota usage and confirmed `USD 0` spend evidence
   - Remaining risks/blockers, next bounded task, and next actor
