const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_BODY_BYTES = 8 * 1024;
const RATE_WINDOW_MS = 60_000;
const MAX_UPDATES_PER_CHAT_PER_WINDOW = 10;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !a || !b) return false;
  let diff = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i++) {
    diff |= (a.charCodeAt(i % a.length) || 0) ^ (b.charCodeAt(i % b.length) || 0);
  }
  return diff === 0;
}

function configured(env) {
  return typeof env.TELEGRAM_WEBHOOK_SECRET === "string" &&
    /^[A-Za-z0-9_-]{16,256}$/.test(env.TELEGRAM_WEBHOOK_SECRET) &&
    typeof env.TELEGRAM_BOT_TOKEN === "string" && env.TELEGRAM_BOT_TOKEN.length >= 20 &&
    typeof env.TELEGRAM_ALLOWED_CHAT_IDS === "string" &&
    env.TELEGRAM_ALLOWED_CHAT_IDS.split(",").some(value => /^-?\d{1,20}$/.test(value.trim()));
}

async function parseUpdate(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("body_too_large");
  const bytes = await request.arrayBuffer();
  if (bytes.byteLength > MAX_BODY_BYTES) throw new Error("body_too_large");
  let input;
  try { input = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { throw new Error("invalid_json"); }
  if (!input || typeof input !== "object" || Array.isArray(input) ||
      !Number.isSafeInteger(input.update_id) || input.update_id < 0) throw new Error("invalid_update");
  return input;
}

function allowedChatIds(env) {
  return new Set(env.TELEGRAM_ALLOWED_CHAT_IDS.split(",").map(value => value.trim()).filter(value => /^-?\d{1,20}$/.test(value)));
}

function commandOf(text) {
  if (typeof text !== "string" || text.length > 256) return "unknown";
  const trimmed = text.trim();
  if (!trimmed || /\s/.test(trimmed)) return "unknown";
  const token = trimmed;
  const match = token.match(/^\/([a-z]{1,20})(?:@[a-z0-9_]{5,32})?$/i);
  return match ? match[1].toLowerCase() : "unknown";
}

async function commandReply(command, env) {
  if (command === "help") {
    return "Raawaa Engine — read-only controls\n/help — show commands\n/status — task queue health\n/report — paper research event counts";
  }
  if (command === "status") {
    const rows = await env.DB.prepare("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status").all();
    const counts = (rows.results || []).map(row => String(row.status) + ": " + String(Number(row.count) || 0)).join("\n") || "No tasks recorded";
    return "Engine task status\n" + counts;
  }
  if (command === "report") {
    const rows = await env.DB.prepare("SELECT event_type,COUNT(*) AS count FROM paper_events GROUP BY event_type").all();
    const counts = (rows.results || []).map(row => String(row.event_type) + ": " + String(Number(row.count) || 0)).join("\n") || "No paper events recorded";
    return "Read-only paper research report\n" + counts;
  }
  return "Unknown command. Use /help.";
}

async function sendTelegram(env, chatId, text) {
  const response = await fetch("https://api.telegram.org/bot" + env.TELEGRAM_BOT_TOKEN + "/sendMessage", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
    signal: AbortSignal.timeout(8_000),
  });
  let payload;
  try { payload = await response.json(); } catch { return "unknown"; }
  if (response.ok && payload?.ok === true) return "sent";
  if (response.status >= 500 || response.status === 429) return "unknown";
  return "failed";
}

export async function handleTelegramWebhook(request, env, url) {
  if (url.pathname !== "/v1/telegram/webhook") return null;
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  if (!configured(env)) return json({ error: "webhook_disabled" }, 503);
  const supplied = request.headers.get("x-telegram-bot-api-secret-token") || "";
  if (!constantTimeEqual(supplied, env.TELEGRAM_WEBHOOK_SECRET)) return json({ error: "unauthorized" }, 401);

  let update;
  try { update = await parseUpdate(request); }
  catch (error) {
    return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_update" }, 400);
  }

  const message = update.message;
  const chat = message?.chat;
  const sender = message?.from;
  if (!message || !chat || chat.type !== "private" ||
      !Number.isSafeInteger(chat.id) || !allowedChatIds(env).has(String(chat.id)) ||
      !sender || !Number.isSafeInteger(sender.id) || sender.id !== chat.id || sender.is_bot !== false ||
      typeof message.text !== "string") return json({ ok: true, ignored: true });

  const chatId = String(chat.id);
  const command = commandOf(message.text);
  const now = new Date();
  const nowIso = now.toISOString();
  const windowStart = new Date(now.getTime() - RATE_WINDOW_MS).toISOString();

  let receipt;
  try {
    receipt = await env.DB.prepare(`
      INSERT OR IGNORE INTO telegram_update_receipts(update_id,chat_id,command,processing_status,delivery_status,created_at,updated_at)
      SELECT ?,?,?, 'processing','pending',?,?
      WHERE (SELECT COUNT(*) FROM telegram_update_receipts WHERE chat_id=? AND created_at>=?) < ?
      RETURNING update_id
    `).bind(update.update_id, chatId, command, nowIso, nowIso, chatId, windowStart, MAX_UPDATES_PER_CHAT_PER_WINDOW).first();
  } catch {
    return json({ error: "storage_unavailable" }, 503);
  }
  if (!receipt) {
    // A missing RETURNING row means either a duplicate update or the per-chat rate cap.
    return json({ ok: true, duplicate_or_rate_limited: true });
  }

  try {
    await env.DB.prepare("INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(NULL,?,?,?)")
      .bind("telegram_update_received", JSON.stringify({ update_id: update.update_id, chat_id: chatId, command }), nowIso).run();
  } catch {
    // The receipt remains durable; do not replay a potentially side-effecting response.
    try {
      await env.DB.prepare("UPDATE telegram_update_receipts SET processing_status='unknown',delivery_status='unknown',updated_at=? WHERE update_id=?")
        .bind(new Date().toISOString(), update.update_id).run();
    } catch {}
    return json({ ok: true, delivery_status: "unknown" });
  }

  let delivery = "unknown";
  let processing = "completed";
  try {
    const reply = await commandReply(command, env);
    delivery = await sendTelegram(env, chatId, reply);
  } catch {
    delivery = "unknown";
    processing = "failed";
  }

  try {
    await env.DB.prepare("UPDATE telegram_update_receipts SET processing_status=?,delivery_status=?,updated_at=? WHERE update_id=?")
      .bind(processing, delivery, new Date().toISOString(), update.update_id).run();
    await env.DB.prepare("INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(NULL,?,?,?)")
      .bind("telegram_reply_" + delivery, JSON.stringify({ update_id: update.update_id, chat_id: chatId, command }), new Date().toISOString()).run();
  } catch {
    // Telegram may already have accepted the message. A duplicate update must not send it again.
  }
  return json({ ok: true, delivery_status: delivery });
}
