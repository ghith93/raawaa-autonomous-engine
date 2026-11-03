# SearXNG integration decision — 2026-11-03

## Decision

**Adapter status:** `IMPLEMENTED_NOT_VERIFIED`
**Runtime status:** `BLOCKED`

The engine contains an optional, read-only SearXNG JSON adapter. It is not active until the operator configures `RAAWAA_SEARXNG_BASE_URL`. No public SearXNG instance is selected, no self-hosted instance is deployed, and no SearXNG request has been sent by the current scheduled discovery configuration.

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

## Why deployment is not included

The repository has no verified, owner-controlled SearXNG host configured. Deploying a new always-on service would require selecting and operating compute, storage, upgrades, monitoring and network controls. No such infrastructure or additional recurring cost is assumed under the zero-new-spend constraint.

SearXNG can aggregate many configured search engines, but the actual enabled engines and their availability depend on the instance configuration. The adapter does not claim a fixed count such as 200+ engines.

## Activation checklist

1. Provide an owner-controlled SearXNG HTTPS endpoint on an approved host.
2. Confirm JSON is enabled under `search.formats` in that instance.
3. Configure `RAAWAA_SEARXNG_BASE_URL` in the scheduled discovery environment.
4. Run one bounded manual scan and verify endpoint identity, JSON response shape, result count and source telemetry.
5. Record actual enabled engines and availability; do not infer them from SearXNG marketing or generic documentation.

Until all five checks pass, keep the runtime marked `BLOCKED`.
