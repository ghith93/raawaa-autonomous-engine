# Permanent development loop

The repository already had three halves of a development process and no loop:
a scheduled health check (`maintenance.yml`, every 6 hours), a scheduled task
runner driven by a watchdog, and a set of read-only analysis commands
(`executive-status`, `brain-cycle`, `scripts/analyze_capability_gaps.py`). What
was missing was the step where those signals are turned into **an ordered list
of what to do next**, with the reason for the order and with the limits of what
the order permits.

`engine/development_cycle.py` is that step. This document describes the loop
around it: what runs on its own, what a human or agent does each round, and
which decisions never leave the owner's hands.

## The four parts

| Part | Runs | Can it change anything? |
|---|---|---|
| Collection — `scripts/build_development_state.py` | every 6 h, in `maintenance.yml` | No. GET requests and `git ls-remote` only. |
| Ranking — `engine/development_cycle.py` | every 6 h, in `maintenance.yml` | No. Pure function over the collected state; no network, no writes. |
| Execution — an agent round (Arena) | on demand, per round | Yes, but only through the normal gates: a branch, CI, a pull request, a merge. |
| Review — the council (ChatGPT) | on demand | No. Reviews diffs, CI evidence and the ranking; can veto by argument. |

The first two are the machinery; the last two are the work. Everything below
concerns how the two halves meet.

## One round

1. **Read the ranking.** `python -m engine.cli dev-cycle --state <state.json>` or
   the `development-cycle` artifact of the last `Engine Maintenance` run. The
   top item is where a round starts unless something newer outranks it.
2. **Choose honestly.** The score is an ordering device, not an instruction.
   If the top item cannot be done well in the time available, take the next one
   and say why in the round note — do not silently reorder the list.
3. **Act.** One logical change, on a branch, with tests. A red baseline is
   never merged over: if `main` is red, fixing it is the round.
4. **Verify.** Full suite locally, then CI. Evidence means a run id, not an
   opinion.
5. **Disclose.** Post the round note to the collaboration hub: what changed,
   what was verified, what was rejected and why, what is now blocked.

## What the ranking knows, and what it does not

It knows: main CI status, deploy status, watchdog freshness, the open pull
requests with their mergeability, base staleness and check state, the open
issues with their labels and age, remote branch count, and a caller-supplied
list of documentation drift.

It does **not** know: the live D1 queue depth (that needs the Worker's service
token, which CI does not hold), whether a green check on a stale base reflects
current `main` (the cycle therefore treats `BEHIND` as *stale evidence*, not as
verification), or whether a change is a good idea. Missing signals are reported
as `null` and the ranking degrades; no value is ever invented to fill a gap.

## Gates that survive every instruction

The loop can reorder work. It cannot authorise work. These stay outside it, in
every round, under every grant:

- no permission, GitHub App, ruleset, secret or billing change;
- no enabling of a registered-but-disabled provider, and no spend;
- no merge of a security-sensitive change on the strength of a green check — a
  human or the council reads the diff first;
- no raw stderr or secret material in a public surface (see
  `docs/operations/task-envelope-protocol.md`);
- every action reversible, and every action disclosed with its rollback.

## Why the ranking is deterministic

Two runs over the same state produce the same order, with ties broken on the
target string. This is deliberate: when the loop and a reviewer disagree about
priority, the disagreement can be argued at the level of a named weight
(`W_MAIN_CI_RED`, `W_READY_PR`, `W_ISSUE_LABEL`) rather than at the level of
who spoke last. Changing the loop's urgency is then a code change with a test,
not a mood.

## Adding a signal

1. Collect it in `scripts/build_development_state.py`, returning `null` when it
   is unavailable — never a placeholder.
2. Score it in `engine/development_cycle.py` with a named constant.
3. Add a test in `tests/test_development_cycle.py` that pins both the ordering
   and the gate text.
4. Document it in the table above.

A signal that cannot be collected without a secret is not collected. That is a
design constraint, not a gap to be closed later with a token in CI.
