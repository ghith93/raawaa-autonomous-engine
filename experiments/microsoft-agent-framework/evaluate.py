"""Isolated, zero-provider-call evaluation of Microsoft Agent Framework."""
from __future__ import annotations

import asyncio
import inspect
import json

from agent_framework import step, workflow
from agent_framework.openai import OpenAIChatCompletionClient

from experiments.microsoft_agent_framework.framework_checkpoint_storage import (
    InMemoryEnvelopeCheckpointStorage,
)


CALLS = {"source_a": 0, "source_b": 0, "review": 0}


@step
async def source_a(topic: str) -> str:
    CALLS["source_a"] += 1
    return f"source-a:{topic}"


@step
async def source_b(topic: str) -> str:
    CALLS["source_b"] += 1
    return f"source-b:{topic}"


@step
async def review(left: str, right: str) -> str:
    CALLS["review"] += 1
    return f"reviewed[{left}|{right}]"


@workflow
async def parallel_research(topic: str) -> str:
    left, right = await asyncio.gather(source_a(topic), source_b(topic))
    return await review(left, right)


async def run_evaluation() -> dict:
    storage = InMemoryEnvelopeCheckpointStorage()
    instance = parallel_research.build(checkpoint_storage=storage)

    first = await instance.run("agent orchestration")
    outputs = first.get_outputs()
    if outputs != ["reviewed[source-a:agent orchestration|source-b:agent orchestration]"]:
        raise AssertionError(f"unexpected first-run output: {outputs!r}")
    first_counts = dict(CALLS)

    checkpoint = await storage.get_latest(workflow_name="parallel_research")
    if checkpoint is None:
        raise AssertionError("workflow did not persist a checkpoint")

    resumed = await instance.run(checkpoint_id=checkpoint.checkpoint_id)
    if resumed.get_outputs() != outputs:
        raise AssertionError("resumed workflow output differs from first run")
    if CALLS != first_counts:
        raise AssertionError(f"checkpoint replay repeated completed steps: {CALLS!r}")

    signature = inspect.signature(OpenAIChatCompletionClient)
    if "base_url" not in signature.parameters:
        raise AssertionError("OpenAI-compatible base_url support is missing")

    return {
        "framework": "microsoft-agent-framework",
        "core_version": "1.24.0",
        "openai_integration_version": "1.18.0",
        "workflow": "parallel research branches -> reviewer",
        "checkpoint_storage": "InMemoryEnvelopeCheckpointStorage",
        "checkpoint_resume_reuses_completed_steps": True,
        "openai_chat_completions_base_url_supported": True,
        "groq_base_url": "https://api.groq.com/openai/v1",
        "provider_requests_sent": 0,
        "paid_services_used": 0,
        "calls_after_first_run": first_counts,
        "calls_after_resume": dict(CALLS),
        "integration_status": "isolated_evaluation_only",
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_evaluation()), indent=2, sort_keys=True))
