# Live inference adapters v1

This phase adds opt-in HTTP adapters for Gemini and OpenAI-compatible providers such as Groq, Cerebras, and OpenRouter.

- Adapters are library components only; CLI, CI, worker, and scheduler do not instantiate them.
- A caller must explicitly construct an adapter and enable network execution in the model gateway.
- API keys are read only from environment variables and are never printed.
- Token counts are recorded when the provider supplies them.
- The adapter's zero-cost field is a placeholder, NOT proof of free usage. Production routing must require a verified free route profile and reject unknown cost/quota before calling.
- Provider errors are sanitized; response bodies are not echoed.
- No automatic paid fallback, billing upgrade, or retries are implemented.

Gemini uses the Google Generative Language generateContent endpoint. Groq, Cerebras, and OpenRouter can use the OpenAI-compatible chat completions endpoint with an explicit base URL.

Next: integrate with QuotaManager and ModelGateway, reserve quota before calls, record usage, and cool down on HTTP 429. Live execution remains disabled by default.
