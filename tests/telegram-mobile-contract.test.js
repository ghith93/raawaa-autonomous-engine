import test from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/index.js";
import { handleTelegramWebhook } from "../worker/telegram-webhook.js";

const SECRET = "telegram-webhook-secret-123";
const BOT_TOKEN = "123456:abcdefghijklmnopqrstuvwxyz";

function webhookDb() {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async first() {
          calls.push({ kind: "first", sql, values: this.values });
          return { update_id: this.values[0] };
        },
        async run() {
          calls.push({ kind: "run", sql, values: this.values });
          return { meta: { changes: 1 } };
        },
      };
    },
  };
}

test("Telegram HTTP 429 is recorded as ambiguous delivery, not a retryable duplicate", async () => {
  const DB = webhookDb();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: false }), { status: 429 });
  try {
    const request = new Request("https://engine.example/v1/telegram/webhook", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": SECRET, "content-type": "application/json" },
      body: JSON.stringify({
        update_id: 201,
        message: { chat: { id: 12345, type: "private" }, from: { id: 12345, is_bot: false }, text: "/help" },
      }),
    });
    const env = {
      DB,
      TELEGRAM_WEBHOOK_SECRET: SECRET,
      TELEGRAM_BOT_TOKEN: BOT_TOKEN,
      TELEGRAM_ALLOWED_CHAT_IDS: "12345",
    };
    const response = await handleTelegramWebhook(
      request, env, new URL("https://engine.example/v1/telegram/webhook"),
    );
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true, delivery_status: "unknown" });
    assert.ok(DB.calls.some(call =>
      call.sql.includes("UPDATE telegram_update_receipts") && call.values.includes("unknown")
    ));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("mobile status remains explicit when optional D1 migrations are absent", async () => {
  const calls = [];
  const DB = {
    prepare(sql) {
      return {
        sql,
        values: [],
        bind(...values) { this.values = values; return this; },
        async all() {
          calls.push(sql);
          if (sql.includes("FROM tasks")) return { results: [] };
          if (sql.includes("schema_migrations")) return { results: [] };
          if (sql.includes("FROM model_usage")) return { results: [] };
          return { results: [] };
        },
        async first() { calls.push(sql); return null; },
      };
    },
  };
  const response = await worker.fetch(new Request("https://engine.example/v1/mobile/status", {
    headers: { authorization: "Bearer test-engine-token" },
  }), { DB, ENGINE_API_TOKEN: "test-engine-token" });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.deepEqual(payload.paper_events, []);
  assert.deepEqual(payload.provider_usage, { requests: 0, tokens: 0 });
  assert.match(payload.unavailable_metrics.paper_ledger, /Migration 0004 is not applied/);
  assert.match(payload.unavailable_metrics.provider_usage, /Migration 0003 is not applied/);
  assert.ok(calls.some(sql => sql.includes("schema_migrations")));
});

test("mobile page adds no-referrer and nosniff protections", async () => {
  const response = await worker.fetch(new Request("https://engine.example/mobile"));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.match(response.headers.get("cache-control"), /no-store/);
  assert.match(response.headers.get("content-security-policy"), /frame-ancestors 'none'/);
});
