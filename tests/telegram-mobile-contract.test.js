import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";

const TOKEN = "test-engine-token";
const WEBHOOK_SECRET = "telegram-webhook-secret-123";
const BOT_TOKEN = "123456:abcdefghijklmnopqrstuvwxyz";

function request(path, { method = "GET", token = TOKEN, body, raw, headers = {} } = {}) {
  const requestHeaders = { ...headers };
  if (token) requestHeaders.authorization = "Bearer " + token;
  let payload;
  if (raw !== undefined) {
    payload = raw;
  } else if (body !== undefined) {
    requestHeaders["content-type"] = "application/json";
    payload = JSON.stringify(body);
  }
  return new Request("https://engine.example" + path, {
    method,
    headers: requestHeaders,
    body: method === "GET" || method === "HEAD" ? undefined : payload,
  });
}

function update(updateId = 10, chatId = 123456789, text = "/status", chatType = "private") {
  return { update_id: updateId, message: { chat: { id: chatId, type: chatType }, text } };
}

function envFor(DB, overrides = {}) {
  return {
    DB,
    ENGINE_API_TOKEN: TOKEN,
    TELEGRAM_WEBHOOK_SECRET: WEBHOOK_SECRET,
    TELEGRAM_BOT_TOKEN: BOT_TOKEN,
    TELEGRAM_ALLOWED_CHAT_IDS: "123456789",
    ...overrides,
  };
}

function database({ duplicate = false, failReceipt = false, migrations = true } = {}) {
  const calls = [];
  let inserted = false;
  return {
    calls,
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ method: "first", sql, values: this.values });
          if (sql.includes("INSERT OR IGNORE INTO telegram_update_receipts")) {
            if (failReceipt) throw new Error("simulated D1 outage");
            if (duplicate || inserted) return null;
            inserted = true;
            return { update_id: this.values[0] };
          }
          if (sql.includes("ORDER BY occurred_at DESC,event_id DESC LIMIT 1")) {
            return { event_type: "route_simulated", candidate_id: "candidate-1", occurred_at: "2026-11-03T10:00:00.000Z" };
          }
          if (sql.includes("WHERE event_type='snapshot_captured'")) {
            return { candidate_id: "candidate-1", occurred_at: "2026-11-03T09:59:00.000Z", chain_id: 1, block_number: 123 };
          }
          return null;
        },
        async all() {
          calls.push({ method: "all", sql, values: this.values });
          if (sql.includes("FROM schema_migrations")) {
            return { results: migrations ? [
              { version: "0003_durable_quota_ledger" },
              { version: "0004_paper_event_ledger" },
            ] : [] };
          }
          if (sql.includes("FROM tasks GROUP BY status")) {
            return { results: [{ status: "pending", count: 2 }, { status: "running", count: 1 }] };
          }
          if (sql.includes("FROM paper_events GROUP BY event_type")) {
            return { results: [{ event_type: "candidate_detected", count: 3 }] };
          }
          if (sql.includes("FROM quota_usage GROUP BY route_id")) {
            return { results: [{ route_id: "groq:primary:gpt-oss-20b", requests: 4, tokens: 1200 }] };
          }
          if (sql.includes("FROM model_usage GROUP BY provider")) {
            return { results: [{ provider: "groq", requests: 2, input_tokens: 100, output_tokens: 50, cost_usd: 0 }] };
          }
          return { results: [] };
        },
        async run() {
          calls.push({ method: "run", sql, values: this.values });
          return { meta: { changes: 1 } };
        },
      };
    },
  };
}

async function withFetch(stub, run) {
  const original = globalThis.fetch;
  globalThis.fetch = stub;
  try { return await run(); }
  finally { globalThis.fetch = original; }
}

test("Telegram webhook stays disabled without its complete operator configuration", async () => {
  const DB = database();
  const response = await worker.fetch(request("/v1/telegram/webhook", {
    method: "POST",
    headers: { "x-telegram-bot-api-secret-token": WEBHOOK_SECRET },
    body: update(),
  }), { DB, ENGINE_API_TOKEN: TOKEN });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "webhook_disabled" });
  assert.equal(DB.calls.length, 0);
});

test("Telegram webhook rejects an invalid secret before parsing or database access", async () => {
  const DB = database();
  const response = await worker.fetch(request("/v1/telegram/webhook", {
    method: "POST",
    headers: { "x-telegram-bot-api-secret-token": "wrong-secret" },
    body: update(),
  }), envFor(DB));
  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), { error: "unauthorized" });
  assert.equal(DB.calls.length, 0);
});

test("Telegram webhook ignores non-private and non-allowlisted chats", async () => {
  for (const message of [update(11, 123456789, "/status", "group"), update(12, 999999999)]) {
    const DB = database();
    const response = await worker.fetch(request("/v1/telegram/webhook", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": WEBHOOK_SECRET },
      body: message,
    }), envFor(DB));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true, ignored: true });
    assert.equal(DB.calls.length, 0);
  }
});

test("Telegram webhook rejects oversized and malformed updates without D1 writes", async () => {
  const oversized = "x".repeat(9 * 1024);
  const largeDB = database();
  const largeResponse = await worker.fetch(request("/v1/telegram/webhook", {
    method: "POST",
    headers: {
      "x-telegram-bot-api-secret-token": WEBHOOK_SECRET,
      "content-length": String(oversized.length),
      "content-type": "application/json",
    },
    raw: oversized,
  }), envFor(largeDB));
  assert.equal(largeResponse.status, 400);
  assert.deepEqual(await largeResponse.json(), { error: "body_too_large" });
  assert.equal(largeDB.calls.length, 0);

  const invalidDB = database();
  const invalidResponse = await worker.fetch(request("/v1/telegram/webhook", {
    method: "POST",
    headers: { "x-telegram-bot-api-secret-token": WEBHOOK_SECRET },
    raw: "{invalid",
  }), envFor(invalidDB));
  assert.equal(invalidResponse.status, 400);
  assert.deepEqual(await invalidResponse.json(), { error: "invalid_update" });
  assert.equal(invalidDB.calls.length, 0);
});

test("Telegram webhook sends an allowlisted read-only status reply and records its receipt", async () => {
  const DB = database();
  await withFetch(async (url, options) => {
    assert.match(String(url), /^https:\/\/api\.telegram\.org\/bot/);
    assert.equal(options.method, "POST");
    const payload = JSON.parse(options.body);
    assert.equal(payload.chat_id, "123456789");
    assert.match(payload.text, /pending: 2/);
    return new Response(JSON.stringify({ ok: true, result: { message_id: 1 } }), { status: 200 });
  }, async () => {
    const response = await worker.fetch(request("/v1/telegram/webhook", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": WEBHOOK_SECRET },
      body: update(13),
    }), envFor(DB));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true, delivery_status: "sent" });
  });
  assert.ok(DB.calls.some(call => call.sql.includes("INSERT OR IGNORE INTO telegram_update_receipts")));
  assert.ok(DB.calls.some(call => call.sql.includes("UPDATE telegram_update_receipts")));
  assert.ok(DB.calls.some(call => call.sql.includes("telegram_reply_sent")));
});

test("duplicate Telegram update is acknowledged without sending a second message", async () => {
  const DB = database({ duplicate: true });
  await withFetch(async () => { throw new Error("duplicate must not send"); }, async () => {
    const response = await worker.fetch(request("/v1/telegram/webhook", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": WEBHOOK_SECRET },
      body: update(14),
    }), envFor(DB));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true, duplicate_or_rate_limited: true });
  });
});

test("Telegram webhook reports ambiguous delivery after Telegram rate limiting", async () => {
  const DB = database();
  await withFetch(async () => new Response(JSON.stringify({ ok: false }), { status: 429 }), async () => {
    const response = await worker.fetch(request("/v1/telegram/webhook", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": WEBHOOK_SECRET },
      body: update(15, 123456789, "/help"),
    }), envFor(DB));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true, delivery_status: "unknown" });
  });
  assert.ok(DB.calls.some(call => call.sql.includes("UPDATE telegram_update_receipts") && call.values.includes("unknown")));
});

test("Telegram webhook fails closed when the durable receipt cannot be stored", async () => {
  const DB = database({ failReceipt: true });
  await withFetch(async () => { throw new Error("must not send without a receipt"); }, async () => {
    const response = await worker.fetch(request("/v1/telegram/webhook", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": WEBHOOK_SECRET },
      body: update(16),
    }), envFor(DB));
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "storage_unavailable" });
  });
});

test("mobile status API requires the existing Engine bearer token", async () => {
  const DB = database();
  const response = await worker.fetch(request("/v1/mobile/status", { token: "wrong" }), envFor(DB));
  assert.equal(response.status, 401);
  assert.equal(DB.calls.length, 0);
});

test("mobile status API exposes only ledger-backed metrics and labels unavailable telemetry", async () => {
  const DB = database();
  const response = await worker.fetch(request("/v1/mobile/status"), envFor(DB));
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.tasks.find(row => row.status === "pending").count, 2);
  assert.equal(payload.paper_events[0].event_type, "candidate_detected");
  assert.equal(payload.provider_usage[0].tokens, 1200);
  assert.equal(payload.model_costs_usd.total, 0);
  assert.match(payload.unavailable_metrics.last_scan, /not recorded/i);
  assert.match(payload.unavailable_metrics.source_freshness, /not yet aggregated/i);
  assert.match(payload.unavailable_metrics.kill_switch, /not yet mirrored/i);
});

test("mobile page is read-only and sends no-store, no-referrer, and restrictive CSP headers", async () => {
  const response = await worker.fetch(request("/mobile"));
  assert.equal(response.status, 200);
  assert.match(response.headers.get("cache-control"), /no-store/);
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.match(response.headers.get("content-security-policy"), /frame-ancestors 'none'/);
  assert.match(response.headers.get("x-content-type-options"), /nosniff/);
  const page = await response.text();
  assert.match(page, /The token stays in this page's memory only/);
  assert.doesNotMatch(page, /localStorage|sessionStorage/);
});
