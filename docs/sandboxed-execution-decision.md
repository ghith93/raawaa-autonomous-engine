# Where repository task code executes — decision record

**Status:** decided, contract implemented, live proof not yet run.
**Date:** 2026-11-04. **Issue:** #440. **Related:** #427 (stays disabled), #34.

## The finding that forced the decision

The Codespaces bridge launches repository-controlled commands as a child process of
the bridge itself: same Unix user, same filesystem, same `/proc/<ppid>/environ`.
Clearing a handful of variables and pointing `HOME` at a temp directory changes
nothing that matters. Fixed command names do not help either — the commands are
repository code, and repository code is what we are trying to contain.

The owner delegated the choice on 2026-11-04 with full authority; the only standing
gate is spend, and every option below is $0.

## Options considered

| | Boundary | Cost | Speed | Verdict |
|---|---|---|---|---|
| **A. Docker in the long-lived Codespace** | Container namespaces + cgroups, host kernel shared | $0 while the Codespace exists (its idle compute is the real cost) | fast | Rejected as the primary path: a long-lived VM with credentials on it is the wrong place to practise containment, and rootless Docker on it is not a boundary either. |
| **B. One GitHub Actions job per task** | Separate ephemeral VM per task | $0 on the free tier within included minutes | ~40 s/task | Kept as the fallback for tasks that need a different OS or heavy toolchain, not as the default: the latency is real and the isolation, while good, gives no syscall-level defence. |
| **C. No execution at all** | n/a | $0 | instant | Kept as the permanent posture for anything not clearly ours. Deterministic handlers stay; anything needing arbitrary code stays a proposal. |
| **D. Rootful Docker + gVisor (`runsc`) on an ephemeral runner** | User-space kernel (Sentry) between the task and the host kernel, plus namespaces, cgroups, no network, read-only rootfs | $0 | +0.07–0.12 s startup | **Chosen.** |

## Why D, and why not the obvious near-misses

- **A plain container is not the boundary.** `runc` shares the host kernel; a kernel
  bug is an escape. gVisor intercepts syscalls in a user-space kernel written in Go,
  so the host kernel never processes attacker-controlled syscall arguments.
- **Rootless `runsc do` is not the boundary either.** It exposes the host filesystem
  read-only and lacks the container and resource boundary that makes a sandbox
  usable for this purpose. The contract therefore runs *rootful Docker with the
  `runsc` runtime*, on a VM that is destroyed after the job — the VM is the outer
  boundary, gVisor is the inner one.
- **The ephemeral runner is a better host than the Codespace.** It has no lasting
  credentials, no user home, no SSH agent, and it ceases to exist. That is the
  property a sandbox wants underneath it.
- **gVisor needs no nested virtualisation.** That matters: GitHub-hosted runners
  cannot be assumed to provide it, and gVisor's `systrap` platform is a userspace
  ptrace-style path that works without KVM.
- **Known caveat, recorded rather than discovered later:** Ubuntu 24.04 restricts
  unprivileged user namespaces through AppArmor, which breaks *rootless* runsc. The
  chosen design is rootful Docker on a throwaway VM, so the restriction does not
  apply — but any future move to rootless must handle it explicitly.

## What is implemented

`engine/sandbox_policy.py` is the contract as data:

- `build_docker_argv()` emits the only vector the executor may run:
  `--runtime=runsc`, `--network=none`, `--read-only`, `--cap-drop=ALL`,
  `--security-opt=no-new-privileges`, a fixed non-root uid, `--pids-limit`,
  `--memory` with an equal `--memory-swap` (no swap headroom above the cap),
  `--cpus`, `noexec/nosuid/nodev` tmpfs mounts, the source bind-mounted
  **read-only**, and a single bounded writable scratch path.
- It exposes **no** way to pass environment variables, devices, capabilities or
  extra mounts. The easiest way to leak a token is an `--env`, so the contract has
  no `--env`.
- `assert_sandbox_argv()` re-checks a finished vector, including ones a human
  assembled. It refuses `--privileged`, `--cap-add`, `--device`, host networking,
  host PID/IPC/user namespaces, `-v`/`--volume`, `--env`/`-e`, any Docker socket
  reference, and any bind mount without `readonly` — the realistic failure is a
  helpful flag added to make something pass, not a malicious caller.
- `reviewed_ref_only()` fails closed on anything but `refs/heads/main`, with a
  deliberately narrow `refs/pull/*` escape hatch for when a reviewed PR head must
  run.
- Limits above the ceiling are **refused, not clamped**, because a limit the code
  under it can raise is not a limit.
- The source path must be **canonical and symlink-free**. `--mount=type=bind`
  resolves symlinks on the host, so a lexically innocent source directory that
  points at `/` mounts the host root under `/workspace` — and read-only still
  reads `/etc/shadow` and a runner's `$HOME`. `os.path.realpath()` must equal the
  given path, and a comma is refused because it is how a mount value gains options.

`tests/test_sandbox_policy.py` (30 tests) pins each of those properties.
`tests/adversarial/test_sandbox_escape.py` holds the probes that can only run under
a real sandbox, and `.github/workflows/sandbox-adversarial.yml` runs them on an
ephemeral runner with gVisor installed. Those probes carry a positive control — a
read of `/workspace/README.md` that must succeed — and the host canary is created
in a world-traversable directory outside the resolved workspace with a per-run
marker. `tests/adversarial/test_probe_harness.py` (17 offline tests) pins that
harness contract, and `tests/test_sandbox_adversarial_workflow.py` pins that the
workflow never interpolates its dispatch input into a shell script.

## Environment isolation is proved against a real host process

The finding above is specific: the Codespaces bridge exposed
`/proc/<ppid>/environ` to repository code. A probe that scans *its own*
environment, or a marker patched into the test process with `patch.dict`, cannot
show whether that exposure was removed — it only shows what the sandbox does with
an environment it already owns. Two earlier revisions of the harness made that
mistake, and both would have produced a green run without evidence.

The live suite therefore proves it in three linked steps:

1. **A real helper process.** `_start_host_helper` execs a separate process with
   the marker in its *initial* `env` (via `subprocess.Popen(env=…)`), not patched in
   afterwards, and waits until `/proc/<pid>/environ` exists.
2. **A host positive control.** `_helper_environ_visible_on_host` reads that exact
   PID's `/proc` entry on the host and requires the `NAME=value` pair to be present.
   If the marker never reached exec time, or the PID is wrong, the run fails before
   the sandbox result is interpreted — otherwise "unreadable" would be meaningless.
3. **A typed sandbox read attempt.** The probe reads only that PID's
   `/proc/<pid>/environ` and reports one of five statuses: `marker_readable` (leak),
   `unreadable` or `missing` (isolated), `marker_absent`, or `inconclusive`. Only
   `unreadable`/`missing` pass, and `marker_absent` deliberately does **not**: a read
   that succeeds but finds nothing may have hit a different process on a recycled
   PID. The marker value is never echoed into logs.

Because same-uid access makes the read *succeed* on an ordinary host, the offline
test `ParentEnvironmentEndToEndTest` runs the real generated probe against a real
helper and asserts the result is `marker_readable` **and** that the isolation
assertion rejects it. A probe that always answered "unreadable" would pass every
unit test while proving nothing; that end-to-end case is what prevents it.

### What this probe can and cannot conclude

A passing read of `marker_readable`/`unreadable` is a statement about **one PID's
`/proc/<pid>/environ`, reached from inside the container**. It is worth being
explicit about which mechanism is doing the work, because two different things
can make that read fail and only one of them is containment:

- **PID namespace separation.** Inside a fresh PID namespace the helper's host
  PID does not exist under the same number, so the read misses. This is genuine
  isolation, and it is what gVisor plus a private PID namespace should produce.
- **Same-uid, no namespace, but `/proc` restricted or the PID simply not
  visible.** On an ordinary host the read *succeeds* because the process is the
  same uid; that is exactly the Codespaces finding this section exists to
  prevent from recurring. A run in which the read fails for this second reason
  is not evidence of a namespace boundary.

The probe deliberately does not try to distinguish these two by inspection — it
reports one typed status and the run treats only `unreadable`/`missing` as a
pass. The claim this document makes is therefore **narrow and stated as such**:

> the eight reviewed probes passed on one ephemeral runner with the reviewed
> flags, and the supported host-process environment read was not readable from
> inside the sandbox.

It is **not** a claim that the whole host process table was enumerated and found
unreachable. No probe in this suite enumerates other PIDs; such an enumeration
would itself require the visibility the sandbox is supposed to remove. Treating
"we read one entry and it failed" as "the host process table is hidden" would be
an unsupported inference, and this document does not make it.

The self-environment control (`RAAWAA_PROBE_SECRET`) is kept as a separate,
narrower claim and is no longer presented as parent-process isolation.

## What is still missing before #427 may merge

1. ~~**The probes must run, and be green, on a real runner.**~~ **Done on
   2026-11-07, once.** `sandbox-adversarial.yml` run
   [42329064427](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42329064427)
   succeeded on `3a9987e` on a standard `ubuntu-24.04` GitHub-hosted runner: the
   image was refused unless it matched the pinned digest, gVisor `runsc` was
   installed and registered, the runtime assertion passed, the image was pulled,
   and all eight reviewed probes passed — including the host-helper positive
   control and the host-process environment read. Per-probe outcomes are now
   published as check annotations so this is independently checkable.

   What that run does **and does not** establish, stated precisely:

   - **It does establish** that on this runner, with `--network=none`,
     `--read-only`, `--cap-drop=ALL`, `no-new-privileges`, a non-root user,
     `--pids-limit`, `--memory`/`--memory-swap` equal, and `--cpus` set, the
     boundary behaved as specified for the eight probed properties.
   - **The network probe's meaning is narrow and worth stating.** It shows the
     container could not reach the public address while `_require_host_network_control`
     separately proved the *host running the suite* could reach that same address
     immediately before the sandbox attempt. Without that control a firewall or
     offline runner would produce a vacuous "denied". It is still a claim about
     one address from one container, not a general internet-reachability proof.
   - **One dispatch is not the acceptance gate for #440.** Resource-limit
     behaviour under pressure, descendant cleanup with lease/capability
     revocation, and rejection of PR/fork/unreviewed refs are still unproven
     (items 2-4 below), and the release flag stays `false`.
2. ~~**The wall clock is not a Docker flag.**~~ **Partly addressed.**
   `engine/sandbox_executor.py` now owns this: `prepare()` refuses a request whose
   `wall_seconds` leaves less than `KILL_GRACE_SECONDS` (30) of headroom inside
   `ProcessSupervisor`'s ceiling, so the container's own deadline fires *before*
   the supervisor's and the overrun is observable rather than a silent group kill.
   `run()` hands the literal vector to the supervisor, which already puts the
   child in its own session and terminates then force-kills the whole group,
   recording `descendants_killed`.

   What remains unproven is the behaviour under real pressure: that a *grandchild*
   spawned inside the container is actually reaped, and that the task lease is
   revoked rather than merely expired. The ledger path (`SqliteDispatchLedger`)
   makes a retry after a crash replay instead of starting a second container, which
   is at-most-once for *dispatch*; it is not lease revocation. Item 2 stays open
   for those two facts.
3. **Disk is bounded by the tmpfs sizes only.** A task that writes to its scratch
   path is capped; anything larger needs a filesystem quota, which is not part of
   this contract yet.
4. **`#427` stays disabled.** Nothing in this decision turns execution on, and the
   default posture remains C for anything not clearly ours.

## Rollback

Delete `engine/sandbox_executor.py`, its test file, and the workflow; nothing in
production imports the module, and no migration, deployment, secret or provider
state is involved. `engine/process_supervisor.py` gained
`OperationRegistry.with_operation()`, `CEILINGS["max_arguments"]` and a
per-operation `max_arguments` whose default (16) is unchanged, so removing the
executor leaves the supervisor behaving exactly as before for every existing
operation. Execution stays off either way, so the rollback has no operational
surface.
