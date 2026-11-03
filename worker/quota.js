function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
  });
}

function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}
function positiveInt(value) { return Number.isSafeInteger(value) && value > 0; }
function nonNegativeInt(value) { return Number.isSafeInteger(value) && value >= 0; }

const LIMIT_FIELDS = [
  "daily_request_limit", "daily_token_limit", "monthly_request_limit",
  "monthly_token_limit", "minute_request_limit", "minute_token_limit",
  "per_task_request_limit", "per_task_token_limit",
];

function constantTimeTokenMatch(request, expected) {
  const supplied = request.headers.get("authorization") || "";
  const actual = supplied.startsWith("Bearer ") ? supplied.slice(7) : "";
  if (typeof expected !== "string" || !expected || !actual) return false;
  let diff = actual.length ^ expected.length;
  const length = Math.max(actual.length, expected.length);
  for (let i = 0; i < length; i++) {
    diff |= (actual.charCodeAt(i % actual.length) || 0) ^
      (expected.charCodeAt(i % expected.length) || 0);
  }
  return diff === 0;
}

function isAdmin(request, env) {
  return constantTimeTokenMatch(request, env.ENGINE_QUOTA_ADMIN_TOKEN);
}

function configuredRouteLimits(env, routeId) {
  if (typeof env.QUOTA_ROUTE_LIMITS_JSON !== "string" || !env.QUOTA_ROUTE_LIMITS_JSON) return null;
  let routes;
  try { routes = JSON.parse(env.QUOTA_ROUTE_LIMITS_JSON); } catch { return null; }
  if (!routes || typeof routes !== "object" || Array.isArray(routes)) return null;
  let config = routes[routeId];
  if (!config) {
    const wildcard = Object.keys(routes).find(key =>
      key.endsWith("*") && routeId.startsWith(key.slice(0, -1))
    );
    if (wildcard) config = routes[wildcard];
  }
  if (!config || typeof config !== "object" || Array.isArray(config) ||
      typeof config.enabled !== "boolean" ||
      LIMIT_FIELDS.some(key => !positiveInt(config[key]))) return null;
  return config;
}

async function body(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > 8192) return null;
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > 8192) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } catch {
    return null;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try { return JSON.parse(new TextDecoder().decode(bytes)); } catch { return null; }
}
function iso(value) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT/.test(value)) return false;
  return Number.isFinite(Date.parse(value));
}
function usageRow(row) {
  return { requests: Number(row?.requests || 0), tokens: Number(row?.tokens || 0) };
}
async function usage(db, routeId, taskId, now) {
  const current = new Date(now);
  const day = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), current.getUTCDate())).toISOString();
  const month = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), 1)).toISOString();
  const minute = new Date(current.getTime() - 60000).toISOString();
  const [daily, monthly, rolling, task] = await Promise.all([
    db.prepare("SELECT COALESCE(SUM(requests),0) requests,COALESCE(SUM(tokens),0) tokens FROM quota_usage WHERE route_id=? AND occurred_at>=?").bind(routeId, day).first(),
    db.prepare("SELECT COALESCE(SUM(requests),0) requests,COALESCE(SUM(tokens),0) tokens FROM quota_usage WHERE route_id=? AND occurred_at>=?").bind(routeId, month).first(),
    db.prepare("SELECT COALESCE(SUM(requests),0) requests,COALESCE(SUM(tokens),0) tokens FROM quota_usage WHERE route_id=? AND occurred_at>?").bind(routeId, minute).first(),
    db.prepare("SELECT COALESCE(SUM(requests),0) requests,COALESCE(SUM(tokens),0) tokens FROM quota_usage WHERE route_id=? AND task_id=?").bind(routeId, taskId).first()
  ]);
  return { daily: usageRow(daily), monthly: usageRow(monthly), minute: usageRow(rolling), task: usageRow(task) };
}

export async function handleQuota(request, env, url) {
  if (url.pathname !== "/v1/quota") return null;
  if (request.method === "GET") {
    const routeId = url.searchParams.get("route_id");
    const taskId = url.searchParams.get("task_id") || "read-only";
    const limit = Number(url.searchParams.get("limit") || 100);
    if ((routeId !== null && !validText(routeId, 200)) || !validText(taskId, 200) || !Number.isInteger(limit) || limit < 1 || limit > 500) return json({ error: "invalid_query" }, 400);
    const now = new Date().toISOString();
    try {
      const snapshot = await usage(env.DB, routeId || "__no_route__", taskId, now);
      const cooldown = routeId ? await env.DB.prepare("SELECT until_at,reason FROM quota_cooldowns WHERE route_id=?").bind(routeId).first() : null;
      const audit = routeId
        ? await env.DB.prepare("SELECT id,event,route_id,task_kind,details_json,occurred_at FROM quota_audit_events WHERE route_id=? ORDER BY id DESC LIMIT ?").bind(routeId, limit).all()
        : await env.DB.prepare("SELECT id,event,route_id,task_kind,details_json,occurred_at FROM quota_audit_events ORDER BY id DESC LIMIT ?").bind(limit).all();
      return json({ usage: snapshot, cooldown: cooldown || null, audit: (audit.results || []).map(row => ({...row, details: JSON.parse(row.details_json)})) });
    } catch { return json({ error: "storage_unavailable" }, 503); }
  }
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  const input = await body(request);
  if (!input || !validText(input.action, 20)) return json({ error: "invalid_body" }, 400);
  const adminActions = new Set(["record", "cool_down", "audit_event"]);
  if (adminActions.has(input.action)) {
    if (!isAdmin(request, env)) return json({ error: "forbidden_operation_scope" }, 403);
  } else if (!constantTimeTokenMatch(request, env.ENGINE_API_TOKEN)) {
    return json({ error: "forbidden_operation_scope" }, 403);
  }
  const now = new Date().toISOString();
  try {
    if (input.action === "reserve") {
      if (!validText(input.route_id, 200) || !validText(input.task_id, 200) ||
          !positiveInt(input.estimated_tokens)) return json({ error: "invalid_reservation" }, 400);
      const limits = configuredRouteLimits(env, input.route_id);
      if (!limits) return json({ error: "route_not_configured" }, 403);
      if (!limits.enabled) return json({ error: "route_disabled" }, 403);
      if (input.estimated_tokens > limits.per_task_token_limit) return json({ error: "quota_unavailable" }, 409);
      const day = new Date(now); day.setUTCHours(0,0,0,0);
      const month = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), 1));
      const minute = new Date(Date.now() - 60000);
      // One conditional INSERT is the linearization point: concurrent callers
      // cannot both pass the quota predicates and insert beyond a cap.
      const result = await env.DB.prepare(`
        INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,task_id)
        SELECT ?,1,?,?,?
        WHERE NOT EXISTS (SELECT 1 FROM quota_cooldowns WHERE route_id=? AND until_at>?)
          AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) < ?
          AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) + ? <= ?
          AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) < ?
          AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) + ? <= ?
          AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=? AND occurred_at>?) < ?
          AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=? AND occurred_at>?) + ? <= ?
          AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE task_id=?) < ?
          AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE task_id=?) + ? <= ?
        RETURNING id
      `).bind(
        input.route_id, input.estimated_tokens, now, input.task_id,
        input.route_id, now,
        input.route_id, day.toISOString(), limits.daily_request_limit,
        input.route_id, day.toISOString(), input.estimated_tokens, limits.daily_token_limit,
        input.route_id, month.toISOString(), limits.monthly_request_limit,
        input.route_id, month.toISOString(), input.estimated_tokens, limits.monthly_token_limit,
        input.route_id, minute.toISOString(), limits.minute_request_limit,
        input.route_id, minute.toISOString(), input.estimated_tokens, limits.minute_token_limit,
        input.task_id, limits.per_task_request_limit,
        input.task_id, input.estimated_tokens, limits.per_task_token_limit
      ).first();
      if (!result) return json({ error: "quota_unavailable" }, 409);
      return json({ reservation_id: result.id, occurred_at: now }, 201);
    }
    if (input.action === "settle") {
      if (!positiveInt(input.reservation_id) || !validText(input.route_id, 200) ||
          !validText(input.task_id, 200) || !nonNegativeInt(input.actual_tokens)) {
        return json({ error: "invalid_settlement" }, 400);
      }
      const limits = configuredRouteLimits(env, input.route_id);
      if (!limits || input.actual_tokens > limits.per_task_token_limit) {
        return json({ error: "invalid_settlement" }, 400);
      }
      const updated = await env.DB.prepare(
        "UPDATE quota_usage SET tokens=MAX(tokens,?),settled_at=? WHERE id=? AND route_id=? AND task_id=? AND settled_at IS NULL"
      ).bind(input.actual_tokens, now, input.reservation_id, input.route_id, input.task_id).run();
      if (updated.meta?.changes === 1) return json({ ok: true, settled: true });
      const existing = await env.DB.prepare(
        "SELECT tokens,settled_at FROM quota_usage WHERE id=? AND route_id=? AND task_id=?"
      ).bind(input.reservation_id, input.route_id, input.task_id).first();
      if (!existing) return json({ error: "reservation_not_found" }, 404);
      if (existing.settled_at && Number(existing.tokens) >= input.actual_tokens) {
        return json({ ok: true, settled: true, replay: true });
      }
      return json({ error: "settlement_conflict" }, 409);
    }
    if (input.action === "record") {
      if (!validText(input.route_id, 200) || !nonNegativeInt(input.tokens)) return json({ error: "invalid_record" }, 400);
      const result = await env.DB.prepare("INSERT INTO quota_usage(route_id,requests,tokens,occurred_at,settled_at,task_id) VALUES(?,1,?,?,?,?) RETURNING id").bind(input.route_id,input.tokens,now,now,validText(input.task_id,200)?input.task_id:null).first();
      return json({ id: result.id }, 201);
    }
    if (input.action === "cool_down") {
      if (!validText(input.route_id, 200) || !positiveInt(input.seconds)) return json({ error: "invalid_cooldown" }, 400);
      const until = new Date(Date.now() + Math.min(input.seconds, 86400) * 1000).toISOString();
      const reason = typeof input.reason === "string" ? input.reason.slice(0,120) : "rate_limited";
      await env.DB.prepare("INSERT INTO quota_cooldowns(route_id,until_at,reason,updated_at) VALUES(?,?,?,?) ON CONFLICT(route_id) DO UPDATE SET until_at=excluded.until_at,reason=excluded.reason,updated_at=excluded.updated_at").bind(input.route_id,until,reason,now).run();
      return json({ ok: true, until_at: until });
    }
    if (input.action === "audit_event") {
      if (!validText(input.event,80) ||
          (input.route_id !== undefined && input.route_id !== null && !validText(input.route_id,200)) ||
          (input.task_kind !== undefined && input.task_kind !== null && !validText(input.task_kind,100)) ||
          (input.details !== undefined && (!input.details || typeof input.details !== "object" || Array.isArray(input.details)))) {
        return json({ error: "invalid_audit_event" }, 400);
      }
      const allowed = ["reason","error_type","http_status","error_code","estimated_tokens","actual_tokens","cooldown_seconds","cost_usd","cost_basis","profile_estimated_cost_usd","provider_reported_cost_usd","provider_reported_cost_available"];
      const safe = {};
      if (input.details && typeof input.details === "object" && !Array.isArray(input.details)) {
        for (const [key,value] of Object.entries(input.details)) if (allowed.includes(key) && (typeof value === "string" || (typeof value === "number" && Number.isFinite(value)) || typeof value === "boolean")) safe[key] = typeof value === "string" ? value.slice(0,100) : value;
      }
      const result = await env.DB.prepare("INSERT INTO quota_audit_events(event,route_id,task_kind,details_json,occurred_at) VALUES(?,?,?,?,?) RETURNING id").bind(input.event,input.route_id?.slice(0,200) || null,input.task_kind?.slice(0,100) || null,JSON.stringify(safe),now).first();
      return json({ id: result.id }, 201);
    }
    return json({ error: "unsupported_action" }, 400);
  } catch { return json({ error: "storage_unavailable" }, 503); }
}
