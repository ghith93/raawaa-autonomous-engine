# Research Candidate Triage Confidence Gates

**Intent:** Make the automated research shortlist more trustworthy by preventing low-adoption, unlicensed, or copyleft repositories from being labelled high-priority solely because they are recent and match a search topic.

**Current evidence:** The 2026-11-03 discovery run produced 26 GitHub repositories and 10 Crossref records. The first-pass scorer marked several repositories with 0–4 stars, missing/unknown license metadata, or GPL/AGPL terms as high priority. The score remains useful for ordering, but the high-priority label overstates readiness for scarce manual review time.

**Scope:** GitHub repository triage only. Academic ranking, raw scores, source discovery, D1 persistence, and automatic-adoption policy remain unchanged.

**Chosen approach:** Keep the existing numeric score as a relevance/recency signal, but require three independent evidence gates before assigning GitHub priority=high: score >= 70, at least 10 public stars, and an SPDX identifier in the existing permissive-license allowlist. Archived repositories remain low priority. Candidates that miss a high-priority gate retain their numeric score and become medium priority unless already low.

**Alternatives considered:**
- Lower the score weights for recency and relevance: rejected because it mixes popularity/legal readiness into a relevance score and makes the ranking less explainable.
- Exclude low-signal or non-permissive candidates from output: rejected because they may still be worth a later manual review and should remain visible.
- Add explicit evidence gates to the high label: selected because it preserves all candidates and their scores while making the shortlist more conservative.

**Non-goals:** No repository cloning, code execution, package installation, license legal advice, automatic adoption, model calls, paid APIs, or D1 writes. The high label still means manual review candidate, never approved for use.

**Validation:** Add regression tests for low-star, unlicensed/unknown-license, copyleft, and qualifying permissive repositories; observe failures before changing production scoring; run the complete Engine CI suite on the exact PR head.
