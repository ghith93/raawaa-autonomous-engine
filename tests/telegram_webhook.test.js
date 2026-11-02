import test from "node:test";
import assert from "node:assert/strict";
import { handleTelegramWebhook } from "../worker/telegram-webhook.js";

const SECRET = "test_webhook_secret_123456";
const TOKEN = "12345678901234567890:test_bot_token";
const ENV = { TELEGRAM_WEBHOOK_SECRET: SECRET, TELEGRAM_BOT_TOKEN: TOKEN, TELEGRAM_ALLOWED_CHAT_IDS: "12345" };

function dbMock({ inserted = true, fail = false } = {}) {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      return {
        sql, values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          if (fail) throw new Error("db unavailable");
          return inserted ? { update_id: this.values[0] } : null;
        },
        async all() {
          calls.push({ kind: "all", sql, values: this.values });
          if (fail) throw new Error("db unavailable");
          if (sql.includes("FROM tasks")) return { results: [{ status: "pending", count: 2 }] };
          return { results: [{ event_type: "candidate_detected", count: 3 }] };
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          if (fail) throw new Error("db unavailable");
          return { meta: { changes: 1 } };
        },
      };
    },
  };
}

function request(update, { secret = SECRET, raw, method = "POST" } = {}) {
  const headers = {};
  if (secret !== null) headers["x-telegram-bot-api-secret-token"] = secret;
  if (raw !== undefined) headers["content-type"] = "application/json";
  const body = raw !== undefined ? raw : JSON.stringify(update);
  return new Request("https://engine.example/v1/telegram/webhook", {
    method, headers, body: method === "GET" ? undefined : body,
  });
}

test("webhook is disabled until all operator-owned secret and allowlist settings exist", async () => {
  const response = await handleTelegramWebhook(request({ update_id: 1 }), {}, new URL("https://engine.example/v1/telegram/webhook"));
  assert.equal(response.status, 503);
});

test("webhook rejects a missing or incorrect Telegram secret header", async () => {
  for (const secret of [null, "wrong_secret"]) {
    const response = await handleTelegramWebhook(request({ update_id: 1 }, { secret }), ENV, new URL("https://engine.example/v1/telegram/webhook"));
    assert.equal(response.status, 401);
  }
});

test("webhook rejects malformed and oversized bodies before database access", async () => {
  const DB = dbMock();
  const malformed = await handleTelegramWebhook(request(null, { raw: "{" }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
  assert.equal(malformed.status, 400);
  const oversized = await handleTelegramWebhook(request(null, { raw: "x".repeat(8193) }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
  assert.equal(oversized.status, 400);
  assert.equal(DB.calls.length, 0);
});

test("webhook ignores group and non-allowlisted chats", async () => {
  const DB = dbMock();
  for (const chat of [{ id: 12345, type: "group" }, { id: 99999, type: "private" }]) {
    const response = await handleTelegramWebhook(request({ update_id: chat.id, message: { chat, text: "/help" } }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true, ignored: true });
  }
  assert.equal(DB.calls.length, 0);
});

test("duplicate update IDs are acknowledged without sending duplicate replies", async () => {
  const DB = dbMock({ inserted: false });
  const response = await handleTelegramWebhook(request({ update_id: 77, message: { chat: { id: 12345, type: "private" }, text: "/help" } }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, duplicate_or_rate_limited: true });
});

test("allowed read-only commands send a bounded reply and record delivery", async () => {
  const originalFetch = globalThis.fetch;
  let sent;
  globalThis.fetch = async (url, init) => {
    sent = { url, body: JSON.parse(init.body) };
    return new Response(JSON.stringify({ ok: true, result: { message_id: 9 } }), { status: 200 });
  };
  try {
    const DB = dbMock();
    const response = await handleTelegramWebhook(request({ update_id: 88, message: { chat: { id: 12345, type: "private" }, text: "/status" } }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).delivery_status, "sent");
    assert.equal(sent.body.chat_id, "12345");
    assert.match(sent.body.text, /pending: 2/);
    assert.ok(DB.calls.some(call => call.kind === "run" && call.sql.includes("delivery_status")));
  } finally { globalThis.fetch = originalFetch; }
});

test("Telegram network ambiguity is recorded and never causes webhook replay", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("network timeout"); };
  try {
    const DB = dbMock();
    const response = await handleTelegramWebhook(request({ update_id: 99, message: { chat: { id: 12345, type: "private" }, text: "/help" } }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).delivery_status, "unknown");
    assert.ok(DB.calls.some(call => call.kind === "run" && call.values.includes("unknown")));
  } finally { globalThis.fetch = originalFetch; }
});

test("unsupported HTTP methods are rejected", async () => {
  const response = await handleTelegramWebhook(request(null, { method: "GET" }), ENV, new URL("https://engine.example/v1/telegram/webhook"));
  assert.equal(response.status, 405);
});
