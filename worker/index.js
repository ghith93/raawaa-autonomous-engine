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

    if (url.pathname !== "/v1/tasks" && url.pathname !== "/v1/tasks/enqueue" &&
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
