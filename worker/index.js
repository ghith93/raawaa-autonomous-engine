const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_BODY_BYTES = 32 * 1024;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function bearer(request) {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7) : "";
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

async function authorized(request, env) {
  return Boolean(env.ENGINE_API_TOKEN) &&
    constantTimeEqual(bearer(request), env.ENGINE_API_TOKEN);
}

async function readJson(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("body_too_large");
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) throw new Error("body_too_large");
  try { return JSON.parse(raw); } catch { throw new Error("invalid_json"); }
}

async function recoverStaleTasks(env) {
  const nowIso = new Date().toISOString();
  const stale = await env.DB.prepare(
    "SELECT id,attempts,max_attempts,lease_token FROM tasks WHERE status='running' AND (lease_expires_at IS NULL OR lease_expires_at < ?) ORDER BY updated_at,id LIMIT 100"
  ).bind(nowIso).all();
  const statements = [];
  for (const task of stale.results || []) {
    const status = task.attempts < task.max_attempts ? "pending" : "failed";
    statements.push(env.DB.prepare(
      "UPDATE tasks SET status=?,error=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND status='running' AND lease_token IS ? AND (lease_expires_at IS NULL OR lease_expires_at < ?)"
    ).bind(status, "Recovered expired worker lease.", nowIso, task.id, task.lease_token, nowIso));
    statements.push(env.DB.prepare(
      "INSERT INTO audit_log(task_id,event,details_json,created_at) SELECT id,'task_lease_recovered',?,? FROM tasks WHERE id=? AND status=? AND updated_at=?"
    ).bind(JSON.stringify({ new_status: status, attempt: task.attempts, max_attempts: task.max_attempts }), nowIso, task.id, status, nowIso));
  }
  if (statements.length) await env.DB.batch(statements);
  const requeued = (stale.results || []).filter(t => t.attempts < t.max_attempts).length;
  const failed = (stale.results || []).length - requeued;
  return { requeued, failed };
}

function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

function validQuotaInteger(value, min = 1, max = 1000000000) {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}

async function quotaUsageSnapshot(env, routeId) {
  const now = new Date();
  const nowIso = now.toISOString();
  const minuteStart = new Date(now.getTime() - 60_000).toISOString();
  const dayStart = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()
  )).toISOString();
  const monthStart = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), 1
  )).toISOString();
  const sum = async (sql, ...values) => {
    const row = await env.DB.prepare(sql).bind(...values).first();
    return { requests: Number(row?.requests || 0), tokens: Number(row?.tokens || 0) };
  };
  const [minute, daily, monthly, cooldown] = await Promise.all([
    sum("SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens FROM quota_usage WHERE route_id=? AND occurred_at>?", routeId, minuteStart),
    sum("SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens FROM quota_usage WHERE route_id=? AND occurred_at>=?", routeId, dayStart),
    sum("SELECT COALESCE(SUM(requests),0) requests, COALESCE(SUM(tokens),0) tokens FROM quota_usage WHERE route_id=? AND occurred_at>=?", routeId, monthStart),
    env.DB.prepare("SELECT until_at FROM quota_cooldowns WHERE route_id=?").bind(routeId).first(),
  ]);
  return {
    minute, daily, monthly,
    cooldown_active: Boolean(cooldown && cooldown.until_at > nowIso),
  };
}

async function handleQuotaRequest(path, input, env) {
  if (path === "/v1/quota/usage-snapshot") {
    if (!validText(input?.route_id, 200)) return json({ error: "invalid_route_id" }, 400);
    try {
      return json(await quotaUsageSnapshot(env, input.route_id));
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }

  if (path === "/v1/quota/reserve") {
    const fields = [
      "estimated_tokens", "daily_request_limit", "daily_token_limit",
      "monthly_request_limit", "monthly_token_limit", "minute_request_limit",
      "minute_token_limit", "per_task_request_limit", "per_task_token_limit",
    ];
    if (!validText(input?.route_id, 200) || !validText(input?.task_id, 200) ||
        fields.some(name => !validQuotaInteger(input?.[name]))) {
      return json({ error: "invalid_reservation" }, 400);
    }
    const now = new Date();
    const nowIso = now.toISOString();
    const dayStart = new Date(Date.UTC(
      now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()
    )).toISOString();
    const monthStart = new Date(Date.UTC(
      now.getUTCFullYear(), now.getUTCMonth(), 1
    )).toISOString();
    const minuteStart = new Date(now.getTime() - 60_000).toISOString();
    const id = crypto.randomUUID();
    // One conditional INSERT is the atomic reservation boundary. SQLite serializes
    // competing writes, so parallel workers cannot each claim the final quota slot.
    const sql = `
      INSERT INTO quota_usage(id,route_id,requests,tokens,occurred_at,task_id)
      SELECT ?, ?, 1, ?, ?, ?
      WHERE
        (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) < ?
        AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) + ? <= ?
        AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) < ?
        AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=? AND occurred_at>=?) + ? <= ?
        AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE route_id=? AND occurred_at>?) < ?
        AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE route_id=? AND occurred_at>?) + ? <= ?
        AND (SELECT COALESCE(SUM(requests),0) FROM quota_usage WHERE task_id=?) < ?
        AND (SELECT COALESCE(SUM(tokens),0) FROM quota_usage WHERE task_id=?) + ? <= ?
        AND NOT EXISTS (SELECT 1 FROM quota_cooldowns WHERE route_id=? AND until_at>?)
      RETURNING id
    `;
    try {
      const reservation = await env.DB.prepare(sql).bind(
        id, input.route_id, input.estimated_tokens, nowIso, input.task_id,
        input.route_id, dayStart, input.daily_request_limit,
        input.route_id, dayStart, input.estimated_tokens, input.daily_token_limit,
        input.route_id, monthStart, input.monthly_request_limit,
        input.route_id, monthStart, input.estimated_tokens, input.monthly_token_limit,
        input.route_id, minuteStart, input.minute_request_limit,
        input.route_id, minuteStart, input.estimated_tokens, input.minute_token_limit,
        input.task_id, input.per_task_request_limit,
        input.task_id, input.estimated_tokens, input.per_task_token_limit,
        input.route_id, nowIso,
      ).first();
      if (!reservation) return json({ error: "quota_unavailable" }, 409);
      return json({ reservation_id: reservation.id });
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }

  if (path === "/v1/quota/settle") {
    const id = input?.reservation_id;
    const actual = input?.actual_tokens;
    if (!validText(id, 200) || !validQuotaInteger(actual, 0)) {
      return json({ error: "invalid_settlement" }, 400);
    }
    try {
      const current = await env.DB.prepare(
        "SELECT id,tokens,settled_at FROM quota_usage WHERE id=?"
      ).bind(id).first();
      if (!current) return json({ error: "reservation_not_found" }, 404);
      if (current.settled_at) {
        return Number(current.tokens) === actual
          ? json({ ok: true, settled: true })
          : json({ error: "settlement_conflict" }, 409);
      }
      const nowIso = new Date().toISOString();
      const update = await env.DB.prepare(
        "UPDATE quota_usage SET tokens=?,settled_at=? WHERE id=? AND settled_at IS NULL"
      ).bind(actual, nowIso, id).run();
      if (update.meta?.changes) return json({ ok: true, settled: true });
      const after = await env.DB.prepare(
        "SELECT tokens,settled_at FROM quota_usage WHERE id=?"
      ).bind(id).first();
      if (after?.settled_at && Number(after.tokens) === actual) {
        return json({ ok: true, settled: true });
      }
      return json({ error: "settlement_conflict" }, 409);
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }

  if (path === "/v1/quota/cooldown") {
    if (!validText(input?.route_id, 200) ||
        !validQuotaInteger(input?.seconds, 1, 86400) ||
        (input.reason !== undefined && !validText(input.reason, 120))) {
      return json({ error: "invalid_cooldown" }, 400);
    }
    const now = new Date();
    const until = new Date(now.getTime() + input.seconds * 1000).toISOString();
    try {
      await env.DB.prepare(
        "INSERT INTO quota_cooldowns(route_id,until_at,reason) VALUES(?,?,?) " +
        "ON CONFLICT(route_id) DO UPDATE SET until_at=excluded.until_at,reason=excluded.reason"
      ).bind(input.route_id, until, (input.reason || "rate_limited").slice(0, 120)).run();
      return json({ ok: true, until_at: until });
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }

  if (path === "/v1/quota/audit") {
    if (!validText(input?.event, 80) ||
        (input.route_id !== undefined && input.route_id !== null && !validText(input.route_id, 200)) ||
        (input.task_kind !== undefined && input.task_kind !== null && !validText(input.task_kind, 100)) ||
        (input.details !== undefined && (!input.details || typeof input.details !== "object" || Array.isArray(input.details)))) {
      return json({ error: "invalid_audit_event" }, 400);
    }
    const allowed = new Set([
      "reason", "error_type", "http_status", "estimated_tokens",
      "actual_tokens", "cooldown_seconds", "cost_usd",
    ]);
    const safe = {};
    for (const [key, value] of Object.entries(input.details || {})) {
      if (!allowed.has(key)) continue;
      if (typeof value === "string") safe[key] = value.slice(0, 100);
      else if (typeof value === "number" && Number.isFinite(value)) safe[key] = value;
      else if (typeof value === "boolean") safe[key] = value;
    }
    try {
      const result = await env.DB.prepare(
        "INSERT INTO quota_audit_events(event,route_id,task_kind,details_json,occurred_at) VALUES(?,?,?,?,?) RETURNING id"
      ).bind(
        input.event, input.route_id || null, input.task_kind || null,
        JSON.stringify(safe), new Date().toISOString(),
      ).first();
      return json({ id: result?.id ?? null });
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }

  return json({ error: "not_found" }, 404);
}


export default {
  async scheduled(_controller, env) {
    await recoverStaleTasks(env);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      try {
        const row = await env.DB.prepare("SELECT 1 AS ok").first();
        if (!row || row.ok !== 1) return json({ ok: false }, 503);
        return json({ ok: true });
      } catch {
        return json({ ok: false }, 503);
      }
    }

    const quotaPaths = new Set(["/v1/quota/usage-snapshot", "/v1/quota/reserve", "/v1/quota/settle", "/v1/quota/cooldown", "/v1/quota/audit"]);
    if (!quotaPaths.has(url.pathname) && url.pathname !== "/v1/tasks" && url.pathname !== "/v1/tasks/enqueue" &&
        url.pathname !== "/v1/tasks/claim" && url.pathname !== "/v1/tasks/recover" &&
        url.pathname !== "/v1/diagnostics/auth" &&
        !/^\/v1\/tasks\/[A-Za-z0-9-]+\/(heartbeat|finish|retry)$/.test(url.pathname)) {
      return json({ error: "not_found" }, 404);
    }
    if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);

    // Authenticated, read-only POST probe used to distinguish application auth
    // from edge blocks before the runner mutates task state.
    if (url.pathname === "/v1/diagnostics/auth") {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      return json({ ok: true, authenticated: true });
    }

    if (quotaPaths.has(url.pathname)) {
      if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
      let input;
      try { input = await readJson(request); }
      catch (error) {
        return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
      }
      return handleQuotaRequest(url.pathname, input, env);
    }

    if (request.method === "GET" && url.pathname === "/v1/tasks") {
      const rawLimit = Number(url.searchParams.get("limit") || 50);
      if (!Number.isInteger(rawLimit) || rawLimit < 1 || rawLimit > 100) {
        return json({ error: "invalid_limit" }, 400);
      }
      try {
        const result = await env.DB.prepare(
          "SELECT id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at FROM tasks ORDER BY created_at DESC,id DESC LIMIT ?"
        ).bind(rawLimit).all();
        return json({ tasks: result.results || [] });
      } catch {
        return json({ error: "storage_unavailable" }, 503);
      }
    }

    if (request.method === "POST" && url.pathname === "/v1/tasks/recover") {
      try { return json(await recoverStaleTasks(env)); }
      catch { return json({ error: "storage_unavailable" }, 503); }
    }

    // Claim one pending task atomically. The lease token is generated server-side
    // and returned only to the authenticated runner that claimed the task.
    if (request.method === "POST" && url.pathname === "/v1/tasks/claim") {
      const leaseSeconds = 900;
      const now = new Date();
      const nowIso = now.toISOString();
      const expires = new Date(now.getTime() + leaseSeconds * 1000).toISOString();
      const leaseToken = crypto.randomUUID();
      try {
        const claim = await env.DB.prepare(`
          UPDATE tasks SET status='running', attempts=attempts+1, updated_at=?,
            lease_token=?, lease_expires_at=?
          WHERE id=(SELECT id FROM tasks WHERE status='pending'
            ORDER BY created_at,id LIMIT 1)
            AND status='pending'
          RETURNING id,idempotency_key,task_type,title,payload_json,status,attempts,
            max_attempts,created_at,updated_at,lease_token,lease_expires_at
        `).bind(nowIso, leaseToken, expires).first();
        if (!claim) return json({ task: null });
        await env.DB.prepare(
          "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)"
        ).bind(claim.id, "task_claimed", JSON.stringify({
          attempt: claim.attempts, lease_expires_at: expires
        }), nowIso).run();
        return json({ task: claim, lease_token: leaseToken, lease_expires_at: expires });
      } catch {
        return json({ error: "storage_unavailable" }, 503);
      }
    }

    const taskAction = url.pathname.match(/^\/v1\/tasks\/([A-Za-z0-9-]+)\/(heartbeat|finish|retry)$/);
    if (request.method === "POST" && taskAction) {
      const [, taskId, action] = taskAction;
      let input;
      try { input = await readJson(request); }
      catch (error) {
        return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
      }
      const leaseToken = input?.lease_token;
      if (!validText(leaseToken, 200)) return json({ error: "invalid_lease" }, 400);
      const nowIso = new Date().toISOString();
      try {
        const current = await env.DB.prepare(
          "SELECT id,status,attempts,max_attempts,lease_token,lease_expires_at FROM tasks WHERE id=?"
        ).bind(taskId).first();
        if (!current) return json({ error: "task_not_found" }, 404);
        if (current.status !== "running" || current.lease_token !== leaseToken ||
            !current.lease_expires_at || current.lease_expires_at < nowIso) {
          return json({ error: "lease_lost" }, 409);
        }

        if (action === "heartbeat") {
          const seconds = 900;
          const expires = new Date(Date.now() + seconds * 1000).toISOString();
          const result = await env.DB.prepare(
            "UPDATE tasks SET updated_at=?,lease_expires_at=? WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?"
          ).bind(nowIso, expires, taskId, leaseToken, nowIso).run();
          if (!result.meta?.changes) return json({ error: "lease_lost" }, 409);
          await env.DB.prepare(
            "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)"
          ).bind(taskId, "task_heartbeat", JSON.stringify({ lease_expires_at: expires }), nowIso).run();
          return json({ ok: true, lease_expires_at: expires });
        }

        let status, resultJson = null, error = null;
        if (action === "finish") {
          status = input.status;
          if (!["succeeded", "failed", "blocked"].includes(status)) {
            return json({ error: "invalid_status" }, 400);
          }
          if (input.result !== undefined) {
            if (!input.result || typeof input.result !== "object" || Array.isArray(input.result)) {
              return json({ error: "invalid_result" }, 400);
            }
            resultJson = JSON.stringify(input.result);
            if (new TextEncoder().encode(resultJson).byteLength > 16 * 1024) {
              return json({ error: "result_too_large" }, 400);
            }
          }
          if (input.error !== undefined) {
            if (typeof input.error !== "string" || input.error.length > 2000) {
              return json({ error: "invalid_error" }, 400);
            }
            error = input.error;
          }
        } else {
          status = current.attempts < current.max_attempts ? "pending" : "failed";
          error = typeof input.error === "string" ? input.error.slice(0, 2000) : "Task retry requested.";
        }

        const update = await env.DB.prepare(
          "UPDATE tasks SET status=?,result_json=?,error=?,updated_at=?,lease_token=NULL,lease_expires_at=NULL WHERE id=? AND status='running' AND lease_token=? AND lease_expires_at>=?"
        ).bind(status, resultJson, error, nowIso, taskId, leaseToken, nowIso).run();
        if (!update.meta?.changes) return json({ error: "lease_lost" }, 409);
        await env.DB.prepare(
          "INSERT INTO audit_log(task_id,event,details_json,created_at) VALUES(?,?,?,?)"
        ).bind(taskId, action === "retry" ? (status === "pending" ? "task_retry_scheduled" : "task_failed") : "task_" + status,
          JSON.stringify({ error }), nowIso).run();
        return json({ ok: true, status });
      } catch {
        return json({ error: "storage_unavailable" }, 503);
      }
    }

    if (request.method !== "POST" || url.pathname !== "/v1/tasks/enqueue") {
      return json({ error: "method_not_allowed" }, 405);
    }

    let input;
    try { input = await readJson(request); }
    catch (error) {
      return json({ error: error.message === "body_too_large" ? "body_too_large" : "invalid_json" }, 400);
    }

    const taskType = input?.task_type;
    const title = input?.title;
    const key = input?.idempotency_key;
    const payload = input?.payload ?? {};
    const maxAttempts = input?.max_attempts ?? 2;
    if (!validText(taskType, 100) || !validText(title, 240) ||
        !validText(key, 200) || !payload || typeof payload !== "object" ||
        Array.isArray(payload) || !Number.isInteger(maxAttempts) ||
        maxAttempts < 1 || maxAttempts > 5) {
      return json({ error: "invalid_task" }, 400);
    }

    let payloadJson;
    try {
      payloadJson = JSON.stringify(payload);
      if (new TextEncoder().encode(payloadJson).byteLength > 16 * 1024) {
        return json({ error: "payload_too_large" }, 400);
      }
    } catch {
      return json({ error: "invalid_payload" }, 400);
    }

    const id = crypto.randomUUID();
    const now = new Date().toISOString();
    try {
      const insert = env.DB.prepare(
        "INSERT OR IGNORE INTO tasks(id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at) VALUES(?,?,?,?,?,'pending',0,?,?,?)"
      ).bind(id, key, taskType.trim(), title.trim(), payloadJson, maxAttempts, now, now);
      const audit = env.DB.prepare(
        "INSERT INTO audit_log(task_id,event,details_json,created_at) SELECT id,'task_enqueued',?,? FROM tasks WHERE idempotency_key=? AND changes() > 0"
      ).bind(JSON.stringify({ task_type: taskType.trim() }), now, key);
      await env.DB.batch([insert, audit]);
      const task = await env.DB.prepare(
        "SELECT id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at FROM tasks WHERE idempotency_key=?"
      ).bind(key).first();
      if (!task) return json({ error: "storage_unavailable" }, 503);
      return json({ task, created: task.id === id }, task.id === id ? 201 : 200);
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }
};
