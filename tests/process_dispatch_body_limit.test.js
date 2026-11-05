import test from "node:test";
import assert from "node:assert/strict";
import { handleTaskProcessDispatch } from "../worker/task-process-dispatches.js";

const url = new URL("https://engine.test/v1/tasks/task-1/process-dispatches");

async function invoke(request) {
  const response = await handleTaskProcessDispatch(request, { DB: {} }, url);
  return { status: response.status, body: await response.json() };
}

test("process dispatch rejects an oversized declared body without reading it", async () => {
  let cancelled = false;
  const body = new ReadableStream({
    pull(controller) {
      controller.enqueue(new Uint8Array([123]));
    },
    cancel() { cancelled = true; },
  });
  const request = new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json", "content-length": "16385" },
    body,
    duplex: "half",
  });
  const result = await invoke(request);
  assert.equal(result.status, 400);
  assert.equal(result.body.error, "body_too_large");
  assert.equal(cancelled, true);
});

test("process dispatch cancels an oversized streamed body at the byte limit", async () => {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(8192));
      controller.enqueue(new Uint8Array(8193));
    },
    cancel() { cancelled = true; },
  });
  const request = new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    duplex: "half",
  });
  const result = await invoke(request);
  assert.equal(result.status, 400);
  assert.equal(result.body.error, "body_too_large");
  assert.equal(cancelled, true);
});

test("process dispatch parses a bounded body before validating its action", async () => {
  const request = new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "unknown", lease_token: "lease-token" }),
  });
  const result = await invoke(request);
  assert.equal(result.status, 400);
  assert.equal(result.body.error, "unsupported_action");
});
