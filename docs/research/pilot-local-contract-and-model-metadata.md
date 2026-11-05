# Offline pilot contract and model metadata — 2026-11-05

Baseline `a72c48fa0b56eeb0a45fa18a6d369a2eaa1ce8b2`. This is an isolated
SQLite experiment, not an enabled pilot. `research_pilot_local` uses the existing
local task schema and transaction/lease machinery, exposes no provider client,
and is not wired into CLI, workflow, Worker or production D1. Admission compares
identity fields under `BEGIN IMMEDIATE`, one attempt is allowed, claim requires an
exact task ID and matching identity, and an expired lease is fenced by TaskStore.
Tests use threads and separate SQLite connections; they are **not remote D1
concurrency evidence**.

**Important limitation:** the existing generic `claim_next` can still claim a
pilot-shaped row; the prototype does not reserve the global queue or isolate a
live runner from other consumers. It is not safe to connect to a shared queue as
is. A real implementation needs atomic admission/ownership and targeted claims
at the D1 boundary, interoperation with all producers/consumers, documented D1
transaction semantics, fault tests and staging evidence. No evidence freshness,
secret-presence, provider routing, ambiguous-dispatch handling or billing checks
are implemented here: no provider invocation exists to test. Those are mandatory
before live work; never use mocks as evidence of actual cost or delivery.

## Model source metadata only

No weights downloaded, byte hashes independently calculated, model executed or
cost verified. Values below are observed in publicly indexed official Hugging
Face pages via search, not direct verified snapshots; recheck official immutable
metadata before any use.

- **Qwen2.5-1.5B-Instruct-GGUF** official [artifact metadata at revision
  `62a8d092b0a1047016f3edbd0fde387598727aa5`](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/blob/62a8d092b0a1047016f3edbd0fde387598727aa5/qwen2.5-1.5b-instruct-q4_k_m.gguf):
  file `qwen2.5-1.5b-instruct-q4_k_m.gguf`; published SHA-256
  `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`,
  approximately 1.12 GB. [Model card](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)
  describes Apache-2.0 and multilingual capability. Verify license at pinned
  revision and hash downloaded bytes before any future execution.
- **Qwen3-0.6B** official [mutable model.safetensors page](https://huggingface.co/Qwen/Qwen3-0.6B/blob/main/model.safetensors)
  reports SHA-256 `f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b`
  and approximately 1.5 GB. This is a mutable `main` reference, not a pinned
  GGUF or a proven digest for an immutable revision. [Model card](https://huggingface.co/Qwen/Qwen3-0.6B)
  describes Apache-2.0 and multilingual capabilities. A compatible quantized
  artifact, immutable revision, hash and exact license provenance are still
  unresolved; do not treat this as a runnable pin.

Any later benchmark must use identical public Arabic/English fixtures and measure
factuality/citation discipline, structured JSON, tool-schema adherence, wall
time, peak RSS and download/build size. SmolLM2 is an English baseline only.
