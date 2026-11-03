# Deterministic Capability Gap Analysis

## Purpose

`scripts/analyze_capability_gaps.py` converts the existing first-pass research triage into a bounded, inspectable capability-gap report. It is deliberately deterministic and offline: it does not call a model, make network requests, create tasks, approve candidates, install tools, or execute discovered content.

## Workflow

The scheduled research workflow now performs:

1. Public GitHub, Crossref, and arXiv metadata discovery (one bounded arXiv request per daily run), plus an optional SearXNG adapter that remains inactive until an operator-hosted HTTPS endpoint is configured.
2. Deterministic candidate triage.
3. Preservation of each GitHub candidate's `discovered_via` topic provenance.
4. Capability-gap analysis against repository files and configuration.
5. Upload of a separate `capability-gaps-<run_id>` artifact with 30-day retention.
6. Existing optional D1 candidate persistence and scan telemetry, unchanged.

## Baseline signals

The analyzer inspects the current repository state for:

- Declarative agent roster and deterministic task contracts.
- Presence of a coding-agent runtime.
- Local SQLite memory and the optional D1 research-persistence gate.
- Tool Broker implementation and permission matrix presence.
- Provider profile count and enabled state.
- Existing bounded task-runner foundations.
- Presence of a skill security gate versus a research-only scanner candidate.
- Unit-test presence versus a dedicated benchmark directory.
- Configured GitHub discovery topics, Crossref and arXiv metadata sources, and optional SearXNG endpoint configuration.

It does not query GitHub Actions variable values, D1 rows, provider consoles, deployed Workers, billing or runtime health. A file's presence is not proof that a service is active.

## Output and interpretation

Each opportunity contains:
- capability identifier and title;
- current repository-derived status;
- impact band;
- count and bounded shortlist of candidates discovered through the related topic;
- repository evidence paths and explanatory evidence;
- a proposed next step;
- `decision_status: PROPOSED`;
- explicit `automatic_task_creation: false` and `automatic_adoption: false`.

The triage score is a metadata heuristic, not a security, quality, compatibility or adoption score. Candidate shortlists are prompts for human/engineering review only.

## Safety properties

- Candidate titles, URLs and source identifiers are treated as data and length-bounded.
- SearXNG is skipped without `RAAWAA_SEARXNG_BASE_URL`; the analyzer cannot inspect that runtime environment variable or prove the endpoint is reachable.
- SearXNG JSON results are accepted only from the configured HTTPS host; redirects to another host, private IPs, local hostnames and malformed/oversized JSON are rejected.
- No candidate description or repository content is executed or inserted into an agent instruction.
- No model or network call is made by the analyzer.
- No task, issue, PR, permission, secret, migration or D1 record is created.
- Missing or malformed repository configuration fails closed with a clear error.
- Unmapped candidates are counted rather than assigned to an invented capability gap.

## Limitations

- The baseline is a repository snapshot; it cannot prove live runtime state or external account settings.
- Crossref and arXiv records are triaged as research candidates but are not automatically mapped to capability gaps because title-only topic inference would be too weak. arXiv abstracts and full text are not ingested.
- SearXNG endpoint deployment and successful retrieval are not implied by the presence of the optional adapter; the endpoint is not configured in the current repository environment.
- A capability may be partially implemented even when the repository contains no corresponding candidate in a scan.
- The current report ranks by predefined impact band and count of related candidates; it is not a statistically calibrated value model.
- All opportunities remain `PROPOSED` until primary-source verification, measurable baseline, controlled experiment and independent review.

## Validation

Run:

```bash
python scripts/triage_research_candidates.py research-candidates.json > research-triage.json
python scripts/analyze_capability_gaps.py research-triage.json . > capability-gaps.json
python -m unittest tests.test_capability_gap_analysis
```
