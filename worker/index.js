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

function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

export default {
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

    if (!["/v1/tasks", "/v1/tasks/enqueue"].includes(url.pathname)) {
      return json({ error: "not_found" }, 404);
    }
    if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);

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
