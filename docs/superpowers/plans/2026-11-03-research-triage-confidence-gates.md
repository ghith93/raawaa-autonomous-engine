# Research Triage Confidence Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Prevent low-adoption and license-uncertain GitHub repositories from entering the high-priority manual-review shortlist.

**Architecture:** Preserve the existing numeric score and all candidate records. Apply explicit evidence gates only when deriving GitHub priority.

**Tech Stack:** Python 3.11+, unittest, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-11-03-research-triage-confidence-gates-design.md

## Global Constraints
- No dependencies, model calls, paid APIs, cloning, code execution, D1 persistence, or automatic adoption.
- Academic candidate priority remains unchanged.
- Archived GitHub candidates remain low priority.
- High priority requires score >= 70, stars >= 10, and an existing permissive SPDX identifier.

## Review Focus
- Missing, negative, boolean, or malformed star counts must not satisfy the adoption gate.
- Missing, NOASSERTION, unknown, GPL, and AGPL licenses must not satisfy the permissive-license gate.
- High-scoring candidates that miss an evidence gate retain their numeric score and become medium, not low.
- Archived candidates remain low regardless of score.
- Academic ranking must remain unchanged.

---

## Task 1: Regression tests

**Files:** tests/test_triage_research_candidates.py

- [x] Add a test proving a fresh Apache-2.0 repo with 4 stars has a high numeric score but medium priority.
- [x] Add tests proving missing/NOASSERTION and GPL/AGPL license metadata cannot produce high priority.
- [x] Keep the existing well-adopted MIT/Python high-priority test unchanged.
- [x] PR CI #275 verified the red phase: 272 tests ran, with exactly 5 expected failures across the new adoption/license cases.

## Task 2: Minimal scoring change

**File:** scripts/triage_research_candidates.py

- [x] Derive GitHub priority from score, archived state, stars >= 10, and membership in PERMISSIVE_LICENSES.
- [x] Do not alter numeric score, candidate output fields, academic priority, or discovery behavior.
- [ ] Run focused triage tests; expected: all pass.

## Task 3: Full validation and PR

- [ ] Run python -m unittest discover -s tests -v and python -m engine.cli audit.
- [ ] Review exact diff for unchanged numeric scores and no side effects.
- [ ] Open a PR and require exact-head Engine CI success before merge.

