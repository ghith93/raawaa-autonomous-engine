# Trusted skills registry — threat model and admission controls

## Scope and current boundary

This first increment is a **read-only metadata and content-integrity registry**. It does not execute SKILL.md, import skill code, install dependencies, make network requests, invoke models, or grant tools. Existing agent roles remain deterministic and model inference remains disabled. A manifest's declared capabilities are descriptive metadata, not executable authority.

## Threats and controls

| Threat | Control in this increment | Residual limitation |
|---|---|---|
| Prompt injection in skill instructions | Treat SKILL.md as untrusted data; no execution or authority transfer | Semantic review still required before prompt use |
| Undeclared tool invocation | Tool allowlist is metadata only; no broker is wired | A future broker must enforce every call |
| Path traversal | Skill IDs are strict; resolved paths must remain under registry root | OS-level race protection requires immutable checkout/sandbox |
| Symlink escape | Symlinked manifest/instruction/revocation files rejected | Hard links are not distinguished |
| Content substitution | SHA-256 pin checked before returning a manifest | Does not prove publisher identity |
| Dependency fetching | No dependency installer or network client exists | Future adapters need a separate dependency lock and review |
| Secret exfiltration | No runtime, network, or secret access is provided | Future runtime needs secret isolation and egress controls |
| Revoked skill reuse | Revocation list checked on every read | Revocation updates need reviewed commits |
| Permission escalation | Explicit role + task mapping; deny by default | Mapping is not a substitute for broker-side authorization |
| Malicious scanner result | Scanner is advisory only; manual review remains mandatory | Static scanners have false positives/negatives |

## Manifest requirements

Every admitted skill must record a stable ID/version, immutable source and revision, owner, SPDX license plus evidence URL, purpose, JSON input/output schemas, declared capabilities, allowed tools, network policy, risk classification, SHA-256 of exact SKILL.md bytes, explicit role/task permissions, and manual approval state.

Use immutable upstream commit IDs, not moving branch names. Pin dependencies separately with hashes and license evidence. Never execute install hooks during admission.

## Admission sequence

1. Fetch source in a disposable, network-restricted review environment.
2. Record source URL, immutable commit, owner, license text and SPDX evidence.
3. Inspect every file and dependency; reject binaries, symlinks, path escapes, unbounded scripts and opaque downloads.
4. Run local static checks. Evaluate Cisco AI Skill Scanner and/or NVIDIA SkillSpector as advisory tools only; optional cloud/LLM analysis stays disabled.
5. Review critical/high findings manually; record false-positive rationale and scanner version.
6. Create a versioned manifest and exact content checksum.
7. Require a human reviewer to approve the manifest and change.
8. Add it to the read-only registry; retain an audit trail and revocation path.

## Runtime prerequisites (not implemented here)

Before any skill can influence a live agent:
- A tool broker must enforce role/task/tool permissions independently of skill text.
- No shell, filesystem write, credential, or network access by default.
- Use per-task sandboxing, read-only mounts, resource/time limits and explicit egress allowlists.
- Separate public research inputs from private/customer/business data.
- Reject undeclared tool requests and secret-like payload fields.
- Revalidate checksum, revocation and approval immediately before each use.
- Log manifest digest, source revision, decision, tool calls and outputs without logging secrets.
- Require explicit approval for publishing, messaging, spending, production deploys, access changes and destructive operations.

## Current safe defaults

- Registry is not connected to the live task runner.
- No skill is approved by default.
- Empty revocation list is only a baseline; it does not imply that any skill is trusted.
- No new dependencies, paid services, model calls or production changes.
