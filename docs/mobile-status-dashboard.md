# Mobile status dashboard

Open the Cloudflare Worker origin at `/mobile` from an iPhone or Android browser. The static page is public but contains no operational data. To load the dashboard, enter the existing `ENGINE_API_TOKEN`; it is held only in page memory and is not written to local/session storage, cookies, URLs, or logs. Disconnect or close the page to clear it.

The page calls the authenticated, read-only `GET /v1/mobile/status` endpoint. It reports task-state counts, append-only paper-event counts, latest paper event, and D1 provider request/token totals. It does not expose mutation controls. The endpoint checks the D1 schema migration ledger and omits paper/provider metrics cleanly when their additive migrations have not yet been applied.

The following are intentionally shown as unavailable rather than guessed: last scan, market-source freshness, GitHub CI health, monetary costs (still local SQLite), and the engine kill-switch state (not mirrored into D1). These require explicit, separately verified ingestion contracts before they can be displayed as current facts.

Security headers include no-store, no-referrer, nosniff, and a restrictive Content Security Policy. The page makes no third-party requests and stores no access token persistently.

This is an additive mobile view only. It does not deploy the Worker, change secrets, apply D1 migrations, or enable external actions.
