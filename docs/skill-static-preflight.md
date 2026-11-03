# Skill static preflight — current boundary

## Status

- Deterministic manifest and text-pattern preflight: `IMPLEMENTED_NOT_VERIFIED`
- Sandbox execution: `NOT_IMPLEMENTED`
- Dependency/SBOM audit: `NOT_IMPLEMENTED`
- Signature verification and revocation: `NOT_IMPLEMENTED`
- Skill registry or runtime permission grants: `NOT_IMPLEMENTED`

## What exists

`engine.skill_static_gate.scan_skill_bundle(manifest, files)` performs an offline scan over an explicitly supplied JSON manifest and bounded text-file mapping. It validates a pinned GitHub source/commit, exact version, entrypoint, declared license identifier, capability list, and a very small permission allowlist. It rejects traversal paths, case-colliding paths, excessive file counts/sizes and manifest overflows.

The scanner applies deterministic patterns for prompt override, secret exfiltration, dynamic execution, network access, secret-environment access, privilege escalation and remote package installation. Findings contain rule IDs, severity, file/line and a short evidence hash; matched source lines are not copied into the report.

The CLI is `python scripts/scan_skill_bundle.py <bundle.json>`. It reads at most 2 MiB and returns `BLOCKED` with exit code 2 for invalid or risky bundles. A clean result is only `PROPOSED`, never `VERIFIED`.

A synthetic regression corpus is available via `python scripts/evaluate_skill_static_gate.py`. It currently contains seven hand-authored cases: five malicious fixtures detected, one benign fixture accepted, and one benign documentation quote falsely blocked. This intentionally exposes a false positive rather than hiding it. The benchmark is synthetic only and does not measure real-world malware-detection accuracy.

## Mandatory limits

- The scanner makes zero network calls, model calls, filesystem writes or code-execution calls.
- It does not download source, resolve dependencies, inspect binary files, run a skill, create a sandbox, verify signatures, approve licenses or grant runtime permissions.
- Pattern scanning has false positives and false negatives. It is a triage aid, not a malware detector or proof of safety.
- All external content remains untrusted. A skill remains non-installable until independent source/license/security review, dependency review, isolated sandbox testing, signature verification and explicit registry policy are completed.

## Why SkillSpector is still separate

The NVIDIA SkillSpector research note remains a proposed external scanner evaluation. This in-repository preflight does not import SkillSpector or claim its detection coverage. A future isolated CI experiment may compare both scanners on a synthetic labelled corpus, with LLM analysis disabled and no repository secrets available.

## Next evidence required

1. Run benign/malicious synthetic fixtures and record false-positive/false-negative behavior.
2. Independently review the rules and manifest contract.
3. Design an ephemeral no-secret sandbox for dependency installation and runtime behavior.
4. Add provenance/signature verification and a revocation mechanism.
5. Only then evaluate one pinned, non-production skill candidate. Do not install it as part of this change.
