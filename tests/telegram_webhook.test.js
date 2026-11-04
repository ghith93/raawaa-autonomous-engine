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
  const normalized = update && typeof update === "object" && update.message && !Object.hasOwn(update.message, "from")
    ? { ...update, message: { ...update.message, from: { id: update.message.chat?.id, is_bot: false } } }
    : update;
  const body = raw !== undefined ? raw : JSON.stringify(normalized);
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

test("webhook ignores private-chat updates whose sender is not the allowlisted human chat owner", async () => {
  const DB = dbMock();
  for (const from of [{ id: 99999, is_bot: false }, { id: 12345, is_bot: true }, { id: 12345 }, null]) {
    const response = await handleTelegramWebhook(
      request({ update_id: 70, message: { chat: { id: 12345, type: "private" }, from, text: "/help" } }),
      { ...ENV, DB },
      new URL("https://engine.example/v1/telegram/webhook"),
    );
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

test("/start confirms the owner control channel is connected", async () => {
  const originalFetch = globalThis.fetch;
  let sent;
  globalThis.fetch = async (_url, init) => {
    sent = JSON.parse(init.body);
    return new Response(JSON.stringify({ ok: true, result: { message_id: 10 } }), { status: 200 });
  };
  try {
    const DB = dbMock();
    const response = await handleTelegramWebhook(
      request({ update_id: 87, message: {
        chat: { id: 12345, type: "private" },
        from: { id: 12345, is_bot: false },
        text: "/start",
      } }),
      { ...ENV, DB },
      new URL("https://engine.example/v1/telegram/webhook"),
    );
    assert.equal(response.status, 200);
    assert.equal((await response.json()).delivery_status, "sent");
    assert.equal(sent.chat_id, "12345");
    assert.match(sent.text, /control is connected/);
  } finally { globalThis.fetch = originalFetch; }
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

test("D1 receipt failure returns retryable service error before Telegram send", async () => {
  const DB = dbMock({ fail: true });
  const originalFetch = globalThis.fetch;
  let sends = 0;
  globalThis.fetch = async () => { sends++; return new Response(JSON.stringify({ ok: true }), { status: 200 }); };
  try {
    const response = await handleTelegramWebhook(request({ update_id: 101, message: { chat: { id: 12345, type: "private" }, text: "/help" } }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
    assert.equal(response.status, 503);
    assert.equal(sends, 0);
  } finally { globalThis.fetch = originalFetch; }
});

test("out-of-order unique update IDs are independently processed and command arguments are rejected", async () => {
  const DB = dbMock();
  const originalFetch = globalThis.fetch;
  const replies = [];
  globalThis.fetch = async (_url, init) => {
    replies.push(JSON.parse(init.body).text);
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  try {
    for (const [update_id, text] of [[105, "/report"], [104, "/status ignored"]]) {
      const response = await handleTelegramWebhook(request({ update_id, message: { chat: { id: 12345, type: "private" }, text } }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
      assert.equal(response.status, 200);
      assert.equal((await response.json()).delivery_status, "sent");
    }
    assert.match(replies[0], /candidate_detected: 3/);
    assert.match(replies[1], /Unknown command/);
  } finally { globalThis.fetch = originalFetch; }
});

test("per-chat rate cap is enforced by the receipt insert statement", async () => {
  const DB = dbMock({ inserted: false });
  const response = await handleTelegramWebhook(request({ update_id: 106, message: { chat: { id: 12345, type: "private" }, text: "/help" } }), { ...ENV, DB }, new URL("https://engine.example/v1/telegram/webhook"));
  assert.equal(response.status, 200);
  assert.match(DB.calls[0].sql, /COUNT\(\*\).*created_at/s);
});
