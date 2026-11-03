# Research candidate triage — first-pass ranking

The daily research workflow now runs a deterministic, zero-model-call triage over GitHub repository, Crossref, and arXiv metadata.

## Signals

- GitHub: SPDX licence metadata, recent push date, stars, language fit, repository description, research category, and archived state.
- Crossref: publication date, citation count, and topic terms in the title.\n- arXiv: publication date and relevant title terms; citation counts and licence verification are not available from this feed.
- Outputs transparent scores, reasons, and risk flags; all candidates remain unreviewed.

## What the score does not mean

- A GitHub SPDX identifier is metadata, not a verified licence review. The exact licence file and any model/data licences still need inspection.
- Stars and recent activity are popularity/maintenance signals, not security evidence.
- Crossref citation counts and deposited licence URLs are not proof of paper quality or full-text reuse rights.\n- arXiv title/date ranking is not a paper-quality score; rights and reuse terms require separate review.
- The triage does not inspect source code, dependencies, security advisories, sandbox behaviour, or runtime compatibility.
- No candidate is automatically accepted, installed, executed, or integrated.

## Workflow output

- research-candidates.json: raw public metadata snapshot.
- research-triage.json: ranked candidate list, scores, reasons, and risk flags for all three source families.
- D1 research_candidates: optional persistent source metadata and human review status for GitHub, Crossref, and arXiv.

The report is intended to narrow the manual review queue, not replace a full security/licence/compatibility review.
