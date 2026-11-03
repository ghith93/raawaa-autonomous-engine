"""Provider contracts and a deterministic mock adapter; no network implementation is enabled."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GenerationRequest:
    task_kind: str
    prompt: str
    max_output_tokens: int = 512
    task_id: str | None = None


@dataclass(frozen=True)
class GenerationResponse:
    provider: str
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    mocked: bool = True


class ProviderAdapter(Protocol):
    provider: str
    def generate(self, model: str, request: GenerationRequest) -> GenerationResponse: ...


class MockAdapter:
    """Safe test adapter. It never contacts an external service."""
    provider = "mock"

    def generate(self, model: str, request: GenerationRequest) -> GenerationResponse:
        if not request.prompt.strip():
            raise ValueError("prompt is required")
        if request.max_output_tokens < 1 or request.max_output_tokens > 4096:
            raise ValueError("max_output_tokens must be between 1 and 4096")
        return GenerationResponse(
            provider=self.provider, model=model,
            text=f"[MOCK:{request.task_kind}] Request accepted for offline adapter testing.",
            input_tokens=max(1, len(request.prompt.split())),
            output_tokens=12, cost_usd=0.0, mocked=True,
        )
