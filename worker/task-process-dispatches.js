function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
  });
}

const ROUTE = /^\/v1\/tasks\/([A-Za-z0-9_-]+)\/process-dispatches$/;
const PROCESS_TASK_TYPE = "supervised_probe";
const STEP_KEY = "supervised_process:v1";
const OPERATION_VERSION = "process-supervisor:v1";
const OPERATIONS = new Set(["git_head_revision", "python_platform_probe"]);
const OUTCOME_FIELDS = [
  "classification", "exit_code", "signal_number", "timed_out", "cancelled",
  "output_truncated", "stdout", "stderr", "duration_seconds", "descendants_killed",
];
const CLASSIFICATIONS = new Set(["succeeded", "failed", "timed_out", "cancelled", "refused"]);

function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

function validDigest(value, length) {
  return typeof value === "string" && new RegExp(`^[0-9a-f]{${length}}$`).test(value);
}

async function readBody(request) {
  const limit = 16 * 1024;
  const declared = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > limit) {
    try { await request.body?.cancel("body_too_large"); } catch { /* stream may already be closed */ }
    throw new Error("body_too_large");
  }
  if (!request.body) throw new Error("invalid_json");
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      try { await reader.cancel(); } catch { /* the limit is already enforced */ }
      throw new Error("body_too_large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { throw new Error("invalid_json"); }
}

function normalizeOutcome(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).length !== OUTCOME_FIELDS.length ||
      OUTCOME_FIELDS.some(key => !Object.hasOwn(value, key))) return null;
  if (!CLASSIFICATIONS.has(value.classification) ||
      !(value.exit_code === null || (Number.isSafeInteger(value.exit_code) && value.exit_code >= -255 && value.exit_code <= 255)) ||
      !(value.signal_number === null || (Number.isSafeInteger(value.signal_number) && value.signal_number > 0 && value.signal_number <= 64)) ||
      typeof value.timed_out !== "boolean" || typeof value.cancelled !== "boolean" ||
      typeof value.output_truncated !== "boolean" ||
      typeof value.stdout !== "string" || value.stdout.length > 4000 ||
      typeof value.stderr !== "string" || value.stderr.length > 4000 ||
      typeof value.duration_seconds !== "number" || !Number.isFinite(value.duration_seconds) ||
      value.duration_seconds < 0 || value.duration_seconds > 900 ||
      !Number.isInteger(value.descendants_killed) || value.descendants_killed < 0 ||
      value.descendants_killed > 10000) return null;
  if (value.timed_out !== (value.classification === "timed_out") ||
      value.cancelled !== (value.classification === "cancelled")) return null;
  const normalized = Object.fromEntries(OUTCOME_FIELDS.map(key => [key, value[key]]));
  const serialized = JSON.stringify(normalized);
  if (new TextEncoder().encode(serialized).byteLength > 12 * 1024) return null;
  return { normalized, serialized };
}

function publicDispatch(row) {
  let outcome = null;
  if (row.status === "settled") {
    if (row.outcome_version !== 1 || typeof row.outcome_json !== "string") return null;
    let decoded;
    try { decoded = JSON.parse(row.outcome_json); }
    catch { return null; }
    const validated = normalizeOutcome(decoded);
    if (!validated || validated.serialized !== row.outcome_json) return null;
    outcome = validated.normalized;
  } else if (row.outcome_version !== null && row.outcome_version !== undefined ||
             row.outcome_json !== null && row.outcome_json !== undefined) {
    return null;
  }
  return {
    dispatch_id: row.dispatch_id,
    task_id: row.task_id,
    step_key: row.step_key,
    operation: row.operation,
    operation_version: row.operation_version,
    operation_fingerprint: row.operation_fingerprint,
    status: row.status,
    outcome_version: row.outcome_version,
    outcome,
    detail: row.detail,
    created_at: row.created_at,
    updated_at: row.updated_at,
    started_at: row.started_at,
    settled_at: row.settled_at,
  };
}

async function readCurrent(db, taskId, leaseToken, now) {
  return db.prepare(
    "SELECT id,task_type,status,lease_token,lease_expires_at FROM tasks WHERE id=?"
  ).bind(taskId).first().then(task => Boolean(task && task.task_type === PROCESS_TASK_TYPE &&
    task.status === "running" && task.lease_token === leaseToken &&
    task.lease_expires_at && task.lease_expires_at >= now));
}

/** Authenticated D1 lifecycle dedicated to supervised subprocess dispatches. */
export async function handleTaskProcessDispatch(request, env, url) {
  const match = url.pathname.match(ROUTE);
  if (!match) return null;
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  let input;
  try { input = await readBody(request); }
  catch (error) { return json({ error: error.message }, 400); }

  const taskId = match[1];
  const action = input?.action;
  const leaseToken = input?.lease_token;
  if (!validText(leaseToken, 200)) return json({ error: "invalid_lease" }, 400);
  if (!["prepare", "start", "settle", "ambiguous"].includes(action))
    return json({ error: "unsupported_action" }, 400);
  const now = new Date().toISOString();

  try {
    if (!(await readCurrent(env.DB, taskId, leaseToken, now)))
      return json({ error: "lease_lost_or_wrong_task_type" }, 409);

    if (action === "prepare") {
      if (!validDigest(input.dispatch_id, 32) || input.step_key !== STEP_KEY ||
          input.operation_version !== OPERATION_VERSION || !OPERATIONS.has(input.operation) ||
          !validDigest(input.operation_fingerprint, 64) || !validDigest(input.lease_fingerprint, 16))
        return json({ error: "invalid_dispatch_identity" }, 400);
      await env.DB.prepare(
        "INSERT OR IGNORE INTO task_process_dispatches(" +
        "dispatch_id,task_id,step_key,operation,operation_version,operation_fingerprint,lease_fingerprint,status,created_at,updated_at) " +
        "SELECT ?,?,?,?,?,?,?,'prepared',?,? WHERE EXISTS (" +
        "SELECT 1 FROM tasks WHERE id=? AND task_type=? AND status='running' " +
        "AND lease_token=? AND lease_expires_at>=?)"
      ).bind(input.dispatch_id, taskId, input.step_key, input.operation,
        input.operation_version, input.operation_fingerprint, input.lease_fingerprint, now, now, taskId,
        PROCESS_TASK_TYPE, leaseToken, now).run();
      const row = await env.DB.prepare(
        "SELECT * FROM task_process_dispatches WHERE task_id=? AND step_key=?"
      ).bind(taskId, STEP_KEY).first();
      if (!row) return json({ error: "lease_lost_or_dispatch_not_created" }, 409);
      if (row.dispatch_id !== input.dispatch_id || row.operation !== input.operation ||
          row.operation_version !== input.operation_version ||
          row.operation_fingerprint !== input.operation_fingerprint)
        return json({ error: "dispatch_identity_conflict" }, 409);
      const dispatch = publicDispatch(row);
      if (!dispatch) return json({ error: "invalid_stored_outcome" }, 503);
      return json({ dispatch });
    }

    if (!validDigest(input.dispatch_id, 32)) return json({ error: "invalid_dispatch_id" }, 400);
    const row = await env.DB.prepare(
      "SELECT * FROM task_process_dispatches WHERE task_id=? AND dispatch_id=?"
    ).bind(taskId, input.dispatch_id).first();
    if (!row) return json({ error: "dispatch_not_found" }, 404);

    if (action === "start") {
      const update = await env.DB.prepare(
        "UPDATE task_process_dispatches SET status='in_flight',started_at=?,updated_at=? " +
        "WHERE task_id=? AND dispatch_id=? AND status='prepared' " +
        "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND task_type=? AND status='running' " +
        "AND lease_token=? AND lease_expires_at>=?)"
      ).bind(now, now, taskId, input.dispatch_id, taskId, PROCESS_TASK_TYPE,
        leaseToken, now).run();
      if (update?.meta?.changes !== 1) return json({ error: "dispatch_state_conflict", status: row.status }, 409);
      return json({ ok: true, status: "in_flight" });
    }

    if (action === "settle") {
      const parsed = normalizeOutcome(input.outcome);
      if (!parsed) return json({ error: "invalid_dispatch_outcome" }, 400);
      if (row.status === "settled") {
        if (row.outcome_version === 1 && row.outcome_json === parsed.serialized)
          return json({ ok: true, status: "settled", replay: true });
        return json({ error: "dispatch_outcome_conflict" }, 409);
      }
      if (row.status !== "in_flight")
        return json({ error: "dispatch_state_conflict", status: row.status }, 409);
      const update = await env.DB.prepare(
        "UPDATE task_process_dispatches SET status='settled',outcome_version=1,outcome_json=?," +
        "detail=NULL,settled_at=?,updated_at=? WHERE task_id=? AND dispatch_id=? AND status='in_flight' " +
        "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND task_type=? AND status='running' " +
        "AND lease_token=? AND lease_expires_at>=?)"
      ).bind(parsed.serialized, now, now, taskId, input.dispatch_id, taskId,
        PROCESS_TASK_TYPE, leaseToken, now).run();
      if (update?.meta?.changes !== 1) return json({ error: "lease_lost_or_dispatch_conflict" }, 409);
      return json({ ok: true, status: "settled", replay: false });
    }

    if (!validText(input.detail, 400)) return json({ error: "invalid_ambiguity_detail" }, 400);
    const detail = input.detail.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 400);
    if (row.status === "ambiguous") {
      if (row.detail === detail) return json({ ok: true, status: "ambiguous", replay: true });
      return json({ error: "dispatch_outcome_conflict" }, 409);
    }
    if (row.status !== "in_flight")
      return json({ error: "dispatch_state_conflict", status: row.status }, 409);
    const update = await env.DB.prepare(
      "UPDATE task_process_dispatches SET status='ambiguous',detail=?,updated_at=? " +
      "WHERE task_id=? AND dispatch_id=? AND status='in_flight' " +
      "AND EXISTS (SELECT 1 FROM tasks WHERE id=? AND task_type=? AND status='running' " +
      "AND lease_token=? AND lease_expires_at>=?)"
    ).bind(detail, now, taskId, input.dispatch_id, taskId, PROCESS_TASK_TYPE,
      leaseToken, now).run();
    if (update?.meta?.changes !== 1) return json({ error: "lease_lost_or_dispatch_conflict" }, 409);
    return json({ ok: true, status: "ambiguous", replay: false });
  } catch {
    return json({ error: "storage_unavailable" }, 503);
  }
}
