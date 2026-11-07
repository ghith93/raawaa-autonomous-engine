# #440 acceptance record

This closes #440 as **deferred scope with a recorded acceptance state**, not as a
completed security milestone. The rule this document follows is the one the
issue itself states: *a green ordinary unit-test CI run is not proof of
isolation*. So every row below separates what is **proven**, what is **proven
only offline**, and what is **not proven at all**.

The release flag stays `false`. PR #427 stays disabled. Nothing here turns
execution on.

## Why this is a deferral rather than a completion

Six of the seven acceptance criteria are now met by code with tests. The seventh
cannot be met in this repository's current circumstances:

> **Criterion 5 depends on resource limits holding under real saturation.**

Proving that requires dispatching the adversarial workflow against a real
runner with a real container. That authorization was spent once
([run `42329064427`](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42329064427))
and the owner has said it is not to be re-dispatched without new authorization.
An offline test cannot substitute: it would be asserting a memory limit was
enforced by a stand-in `docker` that never enforces one.

Keeping #440 open on a promise that cannot currently be kept is worse than
closing it with an honest record, so this document is that record.

## Acceptance criteria, one row each

| # | Criterion | State | Evidence |
|---|---|---|---|
| 1 | A child cannot read the bridge token or any parent process environment | **Proven on one runner, once** | Eight reviewed probes passed in `42329064427`, including the host-helper positive control and the host-process environment read. The claim is narrow: one PID's `/proc/<pid>/environ` was not readable from inside the sandbox. No probe enumerates the whole host process table, and this document does not claim it does. |
| 2 | A child cannot read host Git/Codespaces credentials or files outside its workspace | **Proven on one runner, once** | Same run. The container mounts source read-only plus one bounded tmpfs scratch; `--mount` is asserted against trusted configuration by `assert_sandbox_argv`, and `--volume`/`-v` are refused. |
| 3 | A child cannot reach the public network by default | **Proven on one runner, once, narrowly** | `--network=none` is asserted in the reviewed vector. The probe showed one public address was unreachable while a separate control proved the *host* could reach that same address immediately before — without that control a firewall or offline runner would produce a vacuous "denied". One address from one container is not a general reachability proof. |
| 4 | A child cannot access Docker socket, SSH agent, or host credential mounts | **Proven offline** | `engine/sandbox_policy.py` refuses any `docker.sock` occurrence and every `--volume`/`-v` form; the environment path does not exist at all (`-e`/`--env` refused), so secrets cannot be passed in. 30 policy tests. |
| 5 | Resource limits and timeout kill all descendants and release/revoke the task lease safely | **Partly proven; the limits-under-pressure half is NOT proven** | **Descendants: proven.** `killpg` could not reach a descendant that called `setsid`; it survived with `descendants_killed` reading `0`. Fixed with `PR_SET_CHILD_SUBREAPER` in #707, plus `descendants_escaped`/`subreaper_enabled` so a blind census cannot read as clean. **Lease revoke: proven.** `SandboxExecutor.run` never passed a `cancel_check`, so a revoked lease left the container running; fixed in #708 by requiring an explicit `SandboxLease`, verified to stop a running container mid-run. **Wall clock: proven offline.** A 30 s grace keeps the container's own deadline inside the supervisor ceiling. **Memory/pids/cpu under real saturation: NOT PROVEN.** |
| 6 | Attempts to execute a PR/fork/unreviewed ref fail closed | **Proven offline** | `reviewed_ref_only` had no caller until #705. `SandboxExecutor.prepare` now calls it first, before any process exists. Verified: `refs/pull/42/head`, `refs/heads/feature/x`, `refs/tags/v1`, `refs/heads/fork/main`, bare `main`, and a ref with a trailing newline are all refused; only `refs/heads/main` passes. |
| 7 | CI runs adversarial tests in an isolated environment; no Codespace is activated in production until all pass | **Partly met** | `sandbox-adversarial.yml` is `workflow_dispatch`-only with a pinned image and pinned gVisor runtime; it ran once and was green. It is not wired to fire automatically, which is deliberate. No Codespace is activated in production, and the release flag remains `false`. |

## What this record deliberately does not say

- It does not say the sandbox is production-ready. Seven criteria, one of which
  is half-met and one of which is met-but-manual, is not "done".
- It does not say "the host process table is hidden". One failed read is one
  failed read.
- It does not say a green CI run proves containment. Criterion 7 exists precisely
  because it does not.
- It does not say resource limits work. They are configured and asserted; they
  are not observed under load.

## What would reopen this

A new owner authorization to dispatch `sandbox-adversarial.yml` against a runner,
with a probe that saturates memory, pids and cpu and observes that the container
is bounded rather than the host. That is the only remaining gap, and it is one
run plus one probe — not a redesign.

## Rollback

Nothing here changed runtime behaviour: the flag is `false`, and this record is
prose. Delete the file to revert.
