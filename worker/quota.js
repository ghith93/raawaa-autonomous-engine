// Durable provider quota ledger API backed by D1.
//
// Atomicity: a reservation is ONE conditional INSERT ... SELECT ... WHERE
// statement, so the limit checks and the row insert cannot interleave with
// another writer. All timestamps come from the Worker clock (authoritative),
// never from the caller.
const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_BODY_BYTES = 8 * 1024;
const MAX_INT = 1_000_000_000_000;
const MAX_COOLDOWN_SECONDS = 7 * 24 * 3600;
const AUDIT_DETAIL_KEYS = [
  "reason", "error_type", "http_status", "estimated_tokens",
  "actual_tokens", "cooldown_seconds", "cost_usd",
];

export const QUOTA_PATHS = new Set([
  "/v1/quota/reserve", "/v1/quota/settle", "/v1/quota/record",
  "/v1/quota/status", "/v1/quota/task-usage", "/v1/quota/cooldown",
  "/v1/quota/audit", "/v1/quota/audit/list",
]);

export const RESERVE_SQL = `
INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,task_id,idempotency_key)
SELECT ?1,1,?2,?3,?4,?5
WHERE (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=?1 AND occurred_at>=?6) < ?7
  AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=?1 AND occurred_at>=?6) + ?2 <= ?8
  AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=?1 AND occurred_at>=?9) < ?10
  AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=?1 AND occurred_at>=?9) + ?2 <= ?11
  AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=?1 AND occurred_at>?12) < ?13
  AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=?1 AND occurred_at>?12) + ?2 <= ?14
  AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE task_id=?4) < ?15
  AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE task_id=?4) + ?2 <= ?16
RETURNING id`;

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

function authorized(request, env) {
  const value = request.headers.get("authorization") || "";
  const token = value.startsWith("Bearer ") ? value.slice(7) : "";
  return Boolean(env.ENGINE_API_TOKEN) && constantTimeEqual(token, env.ENGINE_API_TOKEN);
}

async function readJson(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("body_too_large");
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) throw new Error("body_too_large");
  try { return JSON.parse(raw); } catch { throw new Error("invalid_json"); }
}

const posInt = v => Number.isSafeInteger(v) && v >= 1 && v <= MAX_INT;
const nonNegInt = v => Number.isSafeInteger(v) && v >= 0 && v <= MAX_INT;
const text = (v, max) => typeof v === "string" && v.trim().length > 0 && v.length <= max;

export function windows(now) {
  const iso = d => d.toISOString();
  const y = now.getUTCFullYear(), m = now.getUTCMonth(), d = now.getUTCDate();
  return {
    now: iso(now),
    minuteStart: iso(new Date(now.getTime() - 60_000)),
    dayStart: iso(new Date(Date.UTC(y, m, d))),
    monthStart: iso(new Date(Date.UTC(y, m, 1))),
  };
}

async function reserve(input, env, clock) {
  const limitKeys = [
    "estimated_tokens", "request_limit", "token_limit",
    "monthly_request_limit", "monthly_token_limit",
    "per_task_request_limit", "per_task_token_limit",
    "minute_request_limit", "minute_token_limit",
  ];
  if (!text(input.route_id, 200) || !text(input.task_id, 200) ||
      !text(input.idempotency_key, 100) || limitKeys.some(k => !posInt(input[k]))) {
    return json({ error: "invalid_reservation" }, 400);
  }
  const lookup = () => env.DB.prepare(
    "SELECT id FROM quota_usage WHERE idempotency_key=?1"
  ).bind(input.idempotency_key).first();
  const replay = await lookup();
  if (replay) return json({ reserved: true, reservation_id: replay.id, replayed: true });

  const w = windows(clock());
  let row;
  try {
    row = await env.DB.prepare(RESERVE_SQL).bind(
      input.route_id, input.estimated_tokens, w.now, input.task_id, input.idempotency_key,
      w.dayStart, input.request_limit, input.token_limit,
      w.monthStart, input.monthly_request_limit, input.monthly_token_limit,
      w.minuteStart, input.minute_request_limit, input.minute_token_limit,
      input.per_task_request_limit, input.per_task_token_limit,
    ).first();
  } catch (error) {
    // A concurrent retry with the same idempotency key hits the unique index.
    const raced = await lookup();
    if (raced) return json({ reserved: true, reservation_id: raced.id, replayed: true });
    throw error;
  }
  if (!row) return json({ reserved: false });
  return json({ reserved: true, reservation_id: row.id }, 201);
}

async function settle(input, env, clock) {
  if (!Number.isSafeInteger(input.reservation_id) || input.reservation_id < 1 ||
      !nonNegInt(input.actual_tokens)) {
    return json({ error: "invalid_settlement" }, 400);
  }
  const now = clock().toISOString();
  const update = await env.DB.prepare(
    "UPDATE quota_usage SET tokens=?1, settled_at=?2 WHERE id=?3 AND settled_at IS NULL"
  ).bind(input.actual_tokens, now, input.reservation_id).run();
  if (update.meta?.changes === 1) return json({ ok: true });
  const row = await env.DB.prepare(
    "SELECT tokens,settled_at FROM quota_usage WHERE id=?1"
  ).bind(input.reservation_id).first();
  if (!row) return json({ ok: false, error: "not_found" });
  if (row.settled_at && row.tokens === input.actual_tokens) return json({ ok: true, replayed: true });
  return json({ ok: false, error: "conflict" });
}

async function record(input, env, clock) {
  if (!text(input.route_id, 200) || !nonNegInt(input.tokens)) {
    return json({ error: "invalid_record" }, 400);
  }
  const now = clock().toISOString();
  const row = await env.DB.prepare(
    "INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,settled_at) VALUES(?1,1,?2,?3,?3) RETURNING id"
  ).bind(input.route_id, input.tokens, now).first();
  return json({ ok: true, id: row?.id ?? null }, 201);
}

async function status(input, env, clock) {
  if (!text(input.route_id, 200)) return json({ error: "invalid_status_request" }, 400);
  const w = windows(clock());
  const earliest = w.monthStart < w.minuteStart ? w.monthStart : w.minuteStart;
  const usage = await env.DB.prepare(`
SELECT
  COALESCE(SUM(CASE WHEN occurred_at>?2 THEN requests END),0) AS minute_requests,
  COALESCE(SUM(CASE WHEN occurred_at>?2 THEN tokens END),0) AS minute_tokens,
  COALESCE(SUM(CASE WHEN occurred_at>=?3 THEN requests END),0) AS day_requests,
  COALESCE(SUM(CASE WHEN occurred_at>=?3 THEN tokens END),0) AS day_tokens,
  COALESCE(SUM(CASE WHEN occurred_at>=?4 THEN requests END),0) AS month_requests,
  COALESCE(SUM(CASE WHEN occurred_at>=?4 THEN tokens END),0) AS month_tokens
FROM quota_usage WHERE route_id=?1 AND occurred_at>=?5`
  ).bind(input.route_id, w.minuteStart, w.dayStart, w.monthStart, earliest).first() || {};
  const cooldown = await env.DB.prepare(
    "SELECT until_at FROM quota_cooldowns WHERE route_id=?1"
  ).bind(input.route_id).first();
  const n = k => Number(usage[k] || 0);
  return json({
    minute: { requests: n("minute_requests"), tokens: n("minute_tokens") },
    day: { requests: n("day_requests"), tokens: n("day_tokens") },
    month: { requests: n("month_requests"), tokens: n("month_tokens") },
    cooldown_active: Boolean(cooldown && cooldown.until_at > w.now),
    cooldown_until: cooldown ? cooldown.until_at : null,
  });
}

async function taskUsage(input, env) {
  if (!text(input.task_id, 200)) return json({ error: "invalid_task_usage_request" }, 400);
  const row = await env.DB.prepare(
    "SELECT COALESCE(SUM(requests),0) AS requests, COALESCE(SUM(tokens),0) AS tokens FROM quota_usage WHERE task_id=?1"
  ).bind(input.task_id).first() || {};
  return json({ requests: Number(row.requests || 0), tokens: Number(row.tokens || 0) });
}

async function cooldown(input, env, clock) {
  const reason = input.reason === undefined ? "rate_limited" : input.reason;
  if (!text(input.route_id, 200) || !Number.isSafeInteger(input.seconds) ||
      input.seconds < 1 || input.seconds > MAX_COOLDOWN_SECONDS || !text(reason, 120)) {
    return json({ error: "invalid_cooldown" }, 400);
  }
  const now = clock();
  const until = new Date(now.getTime() + input.seconds * 1000).toISOString();
  // A cooldown can only be extended, never shortened, by a later call.
  await env.DB.prepare(`
INSERT INTO quota_cooldowns(route_id,until_at,reason) VALUES(?1,?2,?3)
ON CONFLICT(route_id) DO UPDATE SET
  reason=CASE WHEN excluded.until_at > quota_cooldowns.until_at THEN excluded.reason ELSE quota_cooldowns.reason END,
  until_at=CASE WHEN excluded.until_at > quota_cooldowns.until_at THEN excluded.until_at ELSE quota_cooldowns.until_at END`
  ).bind(input.route_id, until, reason).run();
  return json({ ok: true, until_at: until });
}

async function audit(input, env, clock) {
  const routeOk = input.route_id === undefined || input.route_id === null || text(input.route_id, 200);
  const kindOk = input.task_kind === undefined || input.task_kind === null || text(input.task_kind, 100);
  if (!text(input.event, 80) || !routeOk || !kindOk) return json({ error: "invalid_audit_event" }, 400);
  const safe = {};
  const details = input.details && typeof input.details === "object" && !Array.isArray(input.details) ? input.details : {};
  for (const key of AUDIT_DETAIL_KEYS) {
    const value = details[key];
    if (typeof value === "string") safe[key] = value.slice(0, 100);
    else if (typeof value === "number" && Number.isFinite(value)) safe[key] = value;
    else if (typeof value === "boolean") safe[key] = value;
  }
  const row = await env.DB.prepare(
    "INSERT INTO quota_audit_events(event,route_id,task_kind,details_json,occurred_at) VALUES(?1,?2,?3,?4,?5) RETURNING id"
  ).bind(input.event, input.route_id ?? null, input.task_kind ?? null, JSON.stringify(safe), clock().toISOString()).first();
  return json({ ok: true, id: row?.id ?? null }, 201);
}

async function auditList(input, env) {
  const limit = input.limit === undefined ? 100 : input.limit;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500) return json({ error: "invalid_limit" }, 400);
  const result = await env.DB.prepare(
    "SELECT id,event,route_id,task_kind,details_json,occurred_at FROM quota_audit_events ORDER BY id DESC LIMIT ?1"
  ).bind(limit).all();
  const events = (result.results || []).map(r => {
    let parsed = {};
    try { parsed = JSON.parse(r.details_json); } catch { parsed = {}; }
    return { id: r.id, event: r.event, route_id: r.route_id, task_kind: r.task_kind, occurred_at: r.occurred_at, details: parsed };
  });
  return json({ events });
}

export async function handleQuota(request, url, env, { now = () => new Date() } = {}) {
  if (!QUOTA_PATHS.has(url.pathname)) return json({ error: "not_found" }, 404);
  if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  let input;
  try { input = await readJson(request); }
  catch (error) { return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400); }
  if (!input || typeof input !== "object" || Array.isArray(input)) return json({ error: "invalid_json" }, 400);
  try {
    switch (url.pathname) {
      case "/v1/quota/reserve": return await reserve(input, env, now);
      case "/v1/quota/settle": return await settle(input, env, now);
      case "/v1/quota/record": return await record(input, env, now);
      case "/v1/quota/status": return await status(input, env, now);
      case "/v1/quota/task-usage": return await taskUsage(input, env);
      case "/v1/quota/cooldown": return await cooldown(input, env, now);
      case "/v1/quota/audit": return await audit(input, env, now);
      default: return await auditList(input, env);
    }
  } catch {
    return json({ error: "storage_unavailable" }, 503);
  }
}
