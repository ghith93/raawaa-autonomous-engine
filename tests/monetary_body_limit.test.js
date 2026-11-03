import test from "node:test";
import assert from "node:assert/strict";
import { handleMonetaryLedger } from "../worker/monetary-ledger.js";

const url = new URL("https://engine.test/v1/monetary-ledger");

async function invoke(request) {
  const response = await handleMonetaryLedger(request, {}, url);
  return { status: response.status, body: await response.json() };
}

test("rejects oversized declared Content-Length before reading the stream", async () => {
  let reads = 0;
  let cancelled = false;
  const body = new ReadableStream({
    pull(controller) {
      reads += 1;
      controller.enqueue(new Uint8Array([123]));
    },
    cancel() { cancelled = true; },
  });
  const request = new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json", "content-length": "8193" },
    body,
    duplex: "half",
  });
  const result = await invoke(request);
  assert.equal(result.status, 400);
  assert.equal(result.body.error, "body_too_large");
  assert.equal(reads, 0);
  assert.equal(cancelled, true);
});

test("cancels a chunked body as soon as the streamed byte ceiling is crossed", async () => {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(4096));
      controller.enqueue(new Uint8Array(4097));
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

test("accepts a valid bounded JSON body and reaches normal action validation", async () => {
  const request = new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "not-a-supported-action" }),
  });
  const result = await invoke(request);
  assert.equal(result.status, 400);
  assert.equal(result.body.error, "unsupported_action");
});
