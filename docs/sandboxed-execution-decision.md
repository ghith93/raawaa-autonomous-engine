# Where repository task code executes — decision record

**Status:** decided, contract implemented, live proof not yet run.
**Date:** 2026-11-04. **Issue:** #440. **Related:** #427 (stays disabled), #34.

## Probe-hardening update — 2026-11-05 (offline only)

The live workflow now runs an explicit six-test identity/count gate and treats
skips, expected failures, failures, or partial runs as failure. A synthetic
host-environment marker is checked as a required boolean without outputting
values; a separate live negative control injects only that harmless marker
*inside* the sandbox process and must detect it. An offline negative control also
exercises the generated probe program. The multiline probe is encoded in one
newline-free command argument: the independent argv validator rejects literal
newlines, so the old raw template could not start. Checkout token persistence is
disabled for the proof job. These are harness changes, not live sandbox evidence.

**Release posture unchanged:** #427 was already merged but its Codespaces
executor release gate remains false. #440 remains open; this proof still does
not verify all resource-exhaustion, descendant-cleanup, lease-revocation, and
unreviewed-ref acceptance criteria. Do not dispatch the live workflow until its
exact changes and provenance have been independently reviewed and a verified
immutable image digest and zero-cost runner eligibility are established.

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

## What is still missing before #427 may merge

1. **The probes must run, and be green, on a real runner.** Everything so far is the
   contract, not the proof. Until `sandbox-adversarial.yml` produces a green run for
   a specific image digest, #440's acceptance criteria are *implemented*, not *met*.
2. **The wall clock is not a Docker flag.** The executor must run the vector under a
   timeout, kill the whole process group, and revoke the task lease. That code is
   the next increment and belongs with the executor, not here.
3. **Disk is bounded by the tmpfs sizes only.** A task that writes to its scratch
   path is capped; anything larger needs a filesystem quota, which is not part of
   this contract yet.
4. **`#427` stays disabled.** Nothing in this decision turns execution on, and the
   default posture remains C for anything not clearly ours.

## Rollback

Delete the two new files and the workflow; nothing else imports the module, no
migration, deployment, secret or provider state is involved. Execution stays off
either way, so the rollback has no operational surface.
