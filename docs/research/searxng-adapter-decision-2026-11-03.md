# SearXNG integration decision — 2026-11-03

## Decision

**Adapter status:** `IMPLEMENTED_NOT_VERIFIED`
**Runtime status:** `NOT_VERIFIED`

The engine contains an optional, read-only SearXNG JSON adapter. It runs only when `RAAWAA_SEARXNG_BASE_URL` is present in the scheduled workflow environment. The current value of that GitHub Actions variable was not inspected, and no scheduled discovery artifact containing `searxng_endpoint_configured`/`searxng_requests_made` was verified in this review. Therefore actual activation is `NOT_VERIFIED`; no claim is made that the endpoint is currently configured or unconfigured. No public instance is selected or hardcoded, and this PR does not deploy a self-hosted instance.

## Evidence

The official [SearXNG Search API documentation](https://docs.searxng.org/dev/search_api.html) documents GET/POST search endpoints and the `format=json` parameter. JSON output must be enabled in the instance's `search.formats` setting; otherwise the API can return HTTP 403. The documentation also warns that many public instances disable machine-readable formats.

## Safety and limits in this adapter

- Requires an operator-supplied HTTPS endpoint; no public instance is hardcoded.
- Rejects URL credentials, query strings, fragments, local hostnames, private/reserved IP literals and redirects to another host or port.
- Requests only one search page per daily run, with at most 10 results.
- Requires JSON response shape and caps the response at 1 MiB.
- Stores bounded titles, URLs, snippets and engine names as untrusted metadata only.
- Does not fetch result pages, execute content, install tools, or approve candidates.
- All results remain `unreviewed`; license, source quality, security and compatibility review remain mandatory.
- The current D1 candidate-persistence API/schema accepts only GitHub and Crossref records. arXiv and SearXNG candidate metadata therefore remains in the bounded workflow artifacts until a separately reviewed, data-preserving D1 schema/API migration is approved. Scan counts and source-freshness observations can still be recorded in the existing audit log.

## Why deployment is not included

The repository has no verified, owner-controlled SearXNG host configured. Deploying a new always-on service would require selecting and operating compute, storage, upgrades, monitoring and network controls. No such infrastructure or additional recurring cost is assumed under the zero-new-spend constraint.

SearXNG can aggregate many configured search engines, but the actual enabled engines and their availability depend on the instance configuration. The adapter does not claim a fixed count such as 200+ engines.

## Activation checklist

1. Provide an owner-controlled SearXNG HTTPS endpoint on an approved host.
2. Confirm JSON is enabled under `search.formats` in that instance.
3. Configure `RAAWAA_SEARXNG_BASE_URL` in the scheduled discovery environment.
4. Run one bounded manual scan and verify endpoint identity, JSON response shape, result count and source telemetry.
5. Record actual enabled engines and availability; do not infer them from SearXNG marketing or generic documentation.

Until all five checks pass and a scan artifact verifies endpoint identity, request count and telemetry, keep runtime activation marked `NOT_VERIFIED`.
