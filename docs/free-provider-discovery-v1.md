# Free Provider Discovery v1

This phase adds explicit, read-only model catalog discovery for Gemini, Groq and Cerebras.

A provider model-list endpoint proves that a model exists for the supplied account. It does not prove free-tier eligibility or remaining quota. Results are marked `free_tier_verified: false`.

## Credentials
Set these only as GitHub Actions secrets or local environment variables: `GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`. Never commit keys or print them in logs.

## Explicit invocation
```bash
python -m engine.cli free-models
```
The command performs read-only GET requests to provider model-list endpoints. It makes zero generation requests. Missing credentials are reported as unavailable.

## Safeguards
- Catalog presence is not a free-tier guarantee.
- Verify model, account tier, regional availability, rate limits, retention and training terms before activation.
- No automatic upgrade, paid fallback, billing activation or purchase is permitted.
- Any future inference adapter must have explicit model allowlists and remain blocked until cost and quota checks pass.
