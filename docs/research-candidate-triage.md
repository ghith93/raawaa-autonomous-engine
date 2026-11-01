# Research candidate triage — first-pass ranking

The daily research workflow now runs a deterministic, zero-model-call triage over GitHub repository and Crossref metadata.

## Signals

- GitHub: SPDX licence metadata, recent push date, stars, language fit, repository description, research category, and archived state.
- Crossref: publication date, citation count, and topic terms in the title.
- Outputs transparent scores, reasons, and risk flags; all candidates remain unreviewed.

## What the score does not mean

- A GitHub SPDX identifier is metadata, not a verified licence review. The exact licence file and any model/data licences still need inspection.
- Stars and recent activity are popularity/maintenance signals, not security evidence.
- Crossref citation counts and deposited licence URLs are not proof of paper quality or full-text reuse rights.
- The triage does not inspect source code, dependencies, security advisories, sandbox behaviour, or runtime compatibility.
- No candidate is automatically accepted, installed, executed, or integrated.

## Workflow output

- research-candidates.json: raw public metadata snapshot.
- research-triage.json: ranked candidate list, scores, reasons, and risk flags.
- D1 research_candidates: persistent source metadata and human review status.

The report is intended to narrow the manual review queue, not replace a full security/licence/compatibility review.
