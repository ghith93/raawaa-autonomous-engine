# #440 acceptance record

All seven acceptance criteria are now met, criterion 5 only as of
2026-11-07 and only on one ephemeral runner. The rule this document follows is
the one the issue itself states: *a green ordinary unit-test CI run is not proof
of isolation*. So every row below separates what is **proven on a real runner**,
what is **proven only offline**, and where a claim is narrower than it looks.

**The release flag stays `false` and PR #427 stays disabled.** Meeting the
acceptance criteria is not the same as deciding to run untrusted code in
production, and that decision is not made here.

## How criterion 5 became provable

Criterion 5 was the one gap, and it took five dispatches because the first four
each failed in a way that taught something the tests then locked in:

| Dispatch | Outcome | What it taught |
|---|---|---|
| `42329064427` | green, 8 probes | the eight original escape probes hold |
| `42377430224` | 8 pass, 5 pressure fail | a cgroup memory kill SIGKILLs the group; it never raises `MemoryError`, so an in-process observer dies with its own evidence |
| `42378111101` | 5 of 6 pass | writing a file at increasing offsets measures the filesystem, not RAM — it "reached 2048 MiB" inside a 32 MiB tmpfs |
| `42378776731` | regressed | under gVisor the kill takes the *whole sandbox*; no arrangement of parent-and-child inside it can report |
| `42379479414` | **green, 14 probes** | the memory claim is judged from the host on exit status, beside a positive control |

The last row is the evidence. The four failures are recorded too, because a
record that only lists successes would imply the probes worked first time.

## A limit on how this evidence can be read

GitHub renders at most **ten annotations per step**, and the reviewed set is
fourteen. On `42379479414` the four dropped were exactly the four carrying the
criterion-5 evidence. The step being green is still valid evidence — the runner
exits 0 only through a gate that requires all fourteen identities to run with no
skip and no failure, and a test pins that gate — but a reader who wants to *see*
the per-probe outcomes must fetch the `sandbox-probe-report` artifact. Do not
describe the annotation list as complete, and do not treat its omissions as
failures.

## Acceptance criteria, one row each

| # | Criterion | State | Evidence |
|---|---|---|---|
| 1 | A child cannot read the bridge token or any parent process environment | **Proven on one runner, once** | Eight reviewed probes passed in `42329064427`, including the host-helper positive control and the host-process environment read. The claim is narrow: one PID's `/proc/<pid>/environ` was not readable from inside the sandbox. No probe enumerates the whole host process table, and this document does not claim it does. |
| 2 | A child cannot read host Git/Codespaces credentials or files outside its workspace | **Proven on one runner, once** | Same run. The container mounts source read-only plus one bounded tmpfs scratch; `--mount` is asserted against trusted configuration by `assert_sandbox_argv`, and `--volume`/`-v` are refused. |
| 3 | A child cannot reach the public network by default | **Proven on one runner, once, narrowly** | `--network=none` is asserted in the reviewed vector. The probe showed one public address was unreachable while a separate control proved the *host* could reach that same address immediately before — without that control a firewall or offline runner would produce a vacuous "denied". One address from one container is not a general reachability proof. |
| 4 | A child cannot access Docker socket, SSH agent, or host credential mounts | **Proven offline** | `engine/sandbox_policy.py` refuses any `docker.sock` occurrence and every `--volume`/`-v` form; the environment path does not exist at all (`-e`/`--env` refused), so secrets cannot be passed in. 30 policy tests. |
| 5 | Resource limits and timeout kill all descendants and release/revoke the task lease safely | **Proven on one runner, once** | **Descendants: proven.** `killpg` could not reach a descendant that called `setsid`; it survived with `descendants_killed` reading `0`. Fixed with `PR_SET_CHILD_SUBREAPER` in #707, plus `descendants_escaped`/`subreaper_enabled` so a blind census cannot read as clean. **Lease revoke: proven.** `SandboxExecutor.run` never passed a `cancel_check`, so a revoked lease left the container running; fixed in #708 by requiring an explicit `SandboxLease`, verified to stop a running container mid-run. **Wall clock: proven offline.** A 30 s grace keeps the container's own deadline inside the supervisor ceiling. **Memory/pids/cpu under real saturation: proven 2026-11-07.** Six pressure probes ran green in [run `42379479414`](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42379479414) on `ab97b18`: a positive control that allocates 16 MiB and survives, an overshoot judged by exit status from the host, the pids bound, the cpu bound against a busy loop, the wall clock, and a `setsid` descendant under load. |
| 6 | Attempts to execute a PR/fork/unreviewed ref fail closed | **Proven offline** | `reviewed_ref_only` had no caller until #705. `SandboxExecutor.prepare` now calls it first, before any process exists. Verified: `refs/pull/42/head`, `refs/heads/feature/x`, `refs/tags/v1`, `refs/heads/fork/main`, bare `main`, and a ref with a trailing newline are all refused; only `refs/heads/main` passes. |
| 7 | CI runs adversarial tests in an isolated environment; no Codespace is activated in production until all pass | **Partly met** | `sandbox-adversarial.yml` is `workflow_dispatch`-only with a pinned image and pinned gVisor runtime; it ran once and was green. It is not wired to fire automatically, which is deliberate. No Codespace is activated in production, and the release flag remains `false`. |

## What this record deliberately does not say

- It does not say the sandbox is production-ready. Seven criteria met once, on
  one ephemeral runner, is a milestone and not a deployment.
- It does not say the evidence is repeatable. Criterion 5 has one green run;
  a second, independent day would be needed before calling it stable.
- It does not say "the host process table is hidden". One failed read is one
  failed read.
- It does not say a green CI run proves containment. Criterion 7 exists precisely
  because it does not.
- It does not say the limits were observed under *sustained* load. Each probe
  asks for a bounded overshoot and records the refusal; none tries to saturate
  the runner for minutes.

## What would reopen this

- A second green dispatch on a different day, to show criterion 5 is stable
  rather than a single fortunate run.
- Any change to the reviewed flags, the pinned image, or gVisor's version, which
  would make this record describe a container that no longer exists.
- A decision to enable the release flag, which this document does not make and
  which needs its own independent review.

## Rollback

Nothing here changed runtime behaviour: the flag is `false`, and this record is
prose. Delete the file to revert.
