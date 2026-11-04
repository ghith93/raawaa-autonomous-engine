# Trusted-main-only execution contract

Status: implementation proposal on branch `security/trusted-main-only-codespace-bridge`. This is not yet merged, independently reviewed, or activated.

## Purpose

The legacy Codespace bridge is **not an OS sandbox**. It runs test processes with the same Unix identity and repository filesystem as its parent. Minimal subprocess environment variables and a temporary HOME do not change that fact.

This proposal implements the trusted-code-only fallback from issue #440. It does not claim isolation from a malicious commit. It narrows eligibility to code already present on the canonical, protected `main` branch.

## Required checks before any claim

1. The workspace root must equal the repository root.
2. The checked-out branch must be exactly `main`.
3. The Git origin must be exactly `ghith93/raawaa-autonomous-engine` over the canonical HTTPS or SSH URL.
4. The working tree, including tracked changes, untracked files, and ignored files, must be clean. The implementation checks `git status --porcelain --untracked-files=all --ignored=matching` and fails closed on any output.
5. The exact 40-character HEAD SHA must be verified through GitHub's public compare API as an ancestor of or identical to canonical `main`.
6. The same checks run again after task claim and before heartbeat or execution. If the second check fails, the task is completed as blocked without running a command.
7. Task payloads must contain only the single `operation` field. Operations remain a fixed allowlist; user-supplied commands, refs, paths, and arbitrary arguments are rejected.

GitHub verification uses an unauthenticated read-only request, rejects redirects, bounds the response, and fails closed on network, API, parsing, or ancestry uncertainty.

## Scope and retained controls

- Existing D1 task ledger, scoped Codespace principal, task allowlist, lease fencing, heartbeat/finish routes, and hard release constant remain unchanged.
- The executor has no enqueue, retry, recovery, list, quota, monetary, provider, admin, patch, merge, deploy, or external-write capability.
- Per-command timeout and bounded output remain in place.
- No new queue, scheduler, database, migration, secret, or paid service is introduced.
- `CODESPACE_EXECUTOR_RELEASE_ENABLED` remains `false`. Do not provision a Codespace token or start the process as part of this proposal.

## Limitations

This contract is **not** a container/VM sandbox. It is acceptable only for owner-reviewed code already reachable from protected `main`; it is not suitable for arbitrary PRs, forks, feature branches, unreviewed commits, or untrusted payloads. It does not protect against a malicious or compromised commit that is merged to main.

If the project later needs to execute untrusted PR/fork code, use a disposable VM/container with separate UID/PID namespaces, read-only source, isolated scratch storage, default-deny network, no host credentials or sockets, hard resource limits, and kill-on-lease-loss. Do not weaken this contract to accommodate untrusted code.

## Validation required before closing issue #440

- Exact-head Python, Worker, D1 integration, and security-policy CI pass.
- Tests prove rejection of feature branches, dirty trees, noncanonical remotes, invalid SHAs, diverged/unmerged commits, API failures, and payload extension fields.
- Tests prove revalidation after claim blocks execution if the checkout changes.
- Independent security review confirms that the GitHub compare semantics and branch-protection assumptions match the repository's actual rules.
- The release constant remains false until a separate owner-reviewed activation decision. Passing these tests does not itself authorize activation.
