# Multi-CLI execution plan — status per phase

Source: the six-phase plan recorded in issue #34 on 2026-11-03 17:16Z. That issue is now a tracking umbrella and no longer holds live state; this file does. Each phase carries **one** status word, because the original log used the same sections for shipped, blocked and imagined work and the three became indistinguishable.

Status vocabulary:

- **shipped** — implemented, tested, merged, and verified in the running system.
- **partial** — some of it exists and passes tests; the named gap is real.
- **blocked** — cannot progress without an action only the owner can take.
- **not started** — nothing exists beyond this description.

Verified on 2026-11-04 unless the row says otherwise.

## Phase 1 — Codex CLI — `blocked`

Install verified on 2026-11-03. Authentication requires an **interactive account sign-in** through a device-code flow. A GitHub-hosted runner is ephemeral and must not hold the owner's personal ChatGPT session, so this cannot be automated from CI and must not be automated by storing a token.

Owner action: sign in once on a machine the owner controls, or decide the phase is not worth it. Nothing else unblocks it.

## Phase 2 — Antigravity CLI — `blocked`

Same shape as phase 1: install verified, Google account OAuth requires interactive sign-in. The recorded guidance is to use the account login, not a Gemini API key.

## Phase 3 — Model router — `partial`

`engine/model_router.py` is a 30-line selection stub: it filters configured options by `enabled` and refuses to fall back to a paid model when `paid_calls_enabled` is false. It performs **no network calls** by design.

The gap that makes this `partial`, not `shipped`: it selects from a hand-constructed list of `ModelOption` objects. It does not read the provider profile registry (`config/provider_profiles.json`), so a routed selection cannot be traced back to a registered profile with its quota, tier and terms. Multi-CLI routing — the actual phase goal — is absent.

## Phase 4 — Execution manager — `partial`

The foundation exists: `engine/orchestrator.py`, the D1 task ledger, lease fencing and heartbeat, bounded batches (`engine/batch_runner.py`), and now a trusted-main-only executor contract (`engine/trusted_main_policy.py`, merged with #459).

The gap: there is **no CLI process manager**. Nothing spawns, supervises, times out and reaps an external agent process. `subprocess` is used for fixed operations only.

## Phase 5 — Reviewer — `not started`

CI and PR controls exist. There is no independent reviewer module: no code reviews a diff and returns a verdict. Today that role is played by a human-readable council protocol, not by the engine.

Before this is built, the question worth answering is whether it should be: a reviewer that is not independent of the executor is worse than no reviewer, and the separation of permissions it needs (read-only, no push token) is a design constraint, not a detail.

## Phase 6 — Autonomous cycle — `partial`

The read-only half shipped: `engine/development_cycle.py` collects live state, ranks the next actions with named weights, and publishes them every six hours inside `Engine Maintenance` (#481). It cannot merge, deploy, spend or activate anything.

The gap: nothing executes the ranking. Execution is a person or an agent reading the artifact and doing the work under the gates in `permanent-development-loop.md`.

## The honest summary

Two phases are blocked on an owner action that cannot be delegated. Two are partial with a named gap. One has not started and should not start until the independence question is answered. One has its read-only half, which is the half that is safe to automate first.

Nothing in this plan is blocked on a decision from the council, and nothing in it needs new spend. The next phase to move is **3**, because it is the smallest gap with a real consumer: the provider registry already exists, and a router that cannot see it is the reason routing looks implemented when it is not.
