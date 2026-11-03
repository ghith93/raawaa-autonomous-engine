# Engine-local skills

This directory is reserved for reviewed, version-pinned skill packages.

A package must contain:
- `skill.json`: manifest conforming to `schemas/skill-manifest.schema.json`.
- `SKILL.md`: instruction content whose exact UTF-8 bytes match `content_sha256`.

No skills are approved by default. Do not copy external skill catalogs wholesale. Admission requires provenance, license evidence, static scanning, manual review, explicit role/task mapping and a reviewed commit. The current registry is read-only and never executes instructions or adapters.

Revocations live in `config/skills/revocations.json`.
