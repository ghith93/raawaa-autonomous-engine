const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_BODY_BYTES = 8192;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}
function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}
function safeInt(value) {
  return Number.isSafeInteger(value) && value >= 0;
}
function dbInt(value) {
  const number = Number(value ?? 0);
  if (!safeInt(number)) throw new Error("unsafe_integer");
  return number;
}
async function readBody(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) return null;
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) return null;
  try { return JSON.parse(raw); } catch { return null; }
}
function windowKeys(now) {
  const stamp = new Date(now);
  return {
    now: stamp.toISOString(),
    day: stamp.toISOString().slice(0, 10),
    month: stamp.toISOString().slice(0, 7),
  };
}

async function totals(db, now) {
  const { day, month } = windowKeys(now);
  const [daily, monthly, pending, pendingCount] = await Promise.all([
    db.prepare("SELECT COALESCE(SUM(cost_nanos),0) total_nanos FROM model_usage WHERE substr(created_at,1,10)=?").bind(day).first(),
    db.prepare("SELECT COALESCE(SUM(cost_nanos),0) total_nanos FROM model_usage WHERE substr(created_at,1,7)=?").bind(month).first(),
    db.prepare("SELECT COALESCE(SUM(estimated_cost_nanos),0) total_nanos FROM model_usage_reservations WHERE settled_at IS NULL").first(),
    db.prepare("SELECT COUNT(*) pending_count FROM model_usage_reservations WHERE settled_at IS NULL").first(),
  ]);
  return {
    daily_actual_nanos: dbInt(daily?.total_nanos),
    monthly_actual_nanos: dbInt(monthly?.total_nanos),
    pending_nanos: dbInt(pending?.total_nanos),
    pending_count: dbInt(pendingCount?.pending_count),
  };
}

async function taskTotals(db, taskId) {
  const [actual, reserved, pending] = await Promise.all([
    db.prepare("SELECT COALESCE(SUM(cost_nanos),0) total_nanos FROM model_usage WHERE task_id=?").bind(taskId).first(),
    db.prepare("SELECT COALESCE(SUM(estimated_cost_nanos),0) total_nanos FROM model_usage_reservations WHERE task_id=? AND settled_at IS NULL").bind(taskId).first(),
    db.prepare("SELECT COUNT(*) pending_count FROM model_usage_reservations WHERE task_id=? AND settled_at IS NULL").bind(taskId).first(),
  ]);
  return {
    task_actual_nanos: dbInt(actual?.total_nanos),
    task_reserved_nanos: dbInt(reserved?.total_nanos),
    task_pending_count: dbInt(pending?.pending_count),
  };
}

async function pendingReservations(db) {
  const result = await db.prepare(
    "SELECT id,provider,model,task_kind,task_id,created_at,estimated_cost_nanos FROM model_usage_reservations WHERE settled_at IS NULL ORDER BY id LIMIT 100"
  ).all();
  return (result.results || []).map(row => {
    const estimated = dbInt(row.estimated_cost_nanos);
    return {
      id: dbInt(row.id),
      provider: row.provider,
      model: row.model,
      task_kind: row.task_kind,
      task_id: row.task_id,
      created_at: row.created_at,
      estimated_cost_nanos: estimated,
      estimated_cost_usd: estimated / 1_000_000_000,
    };
  });
}

async function taskSummaries(db, taskCapNanos) {
  const [actualRows, reservedRows] = await Promise.all([
    db.prepare("SELECT task_id,COALESCE(SUM(cost_nanos),0) actual_nanos FROM model_usage WHERE task_id IS NOT NULL GROUP BY task_id ORDER BY task_id LIMIT 1000").all(),
    db.prepare("SELECT task_id,COALESCE(SUM(estimated_cost_nanos),0) reserved_nanos,COUNT(*) pending_count FROM model_usage_reservations WHERE task_id IS NOT NULL AND settled_at IS NULL GROUP BY task_id ORDER BY task_id LIMIT 1000").all(),
  ]);
  const map = new Map();
  for (const row of actualRows.results || []) {
    if (!validText(row.task_id, 200)) continue;
    map.set(row.task_id, { task_id: row.task_id, actual_nanos: dbInt(row.actual_nanos), reserved_nanos: 0, pending_reservations: 0 });
  }
  for (const row of reservedRows.results || []) {
    if (!validText(row.task_id, 200)) continue;
    const item = map.get(row.task_id) || { task_id: row.task_id, actual_nanos: 0, reserved_nanos: 0, pending_reservations: 0 };
    item.reserved_nanos = dbInt(row.reserved_nanos);
    item.pending_reservations = dbInt(row.pending_count);
    map.set(row.task_id, item);
  }
  return [...map.values()].sort((a, b) => a.task_id.localeCompare(b.task_id)).map(item => ({
    task_id: item.task_id,
    actual_usd: item.actual_nanos / 1_000_000_000,
    reserved_usd: item.reserved_nanos / 1_000_000_000,
    total_nanos: item.actual_nanos + item.reserved_nanos,
    pending_reservations: item.pending_reservations,
    over_budget: item.actual_nanos + item.reserved_nanos > taskCapNanos,
  }));
}

async function getUsage(request, env, url) {
  const now = new Date().toISOString();
  const result = await totals(env.DB, now);
  const taskId = url.searchParams.get("task_id");
  if (taskId !== null) {
    if (!validText(taskId, 200)) return json({ error: "invalid_task_id" }, 400);
    Object.assign(result, await taskTotals(env.DB, taskId));
  }
  const pendingFlag = url.searchParams.get("include_pending") || "false";
  const tasksFlag = url.searchParams.get("include_tasks") || "false";
  if (!["true", "false"].includes(pendingFlag) || !["true", "false"].includes(tasksFlag)) {
    return json({ error: "invalid_query" }, 400);
  }
  if (pendingFlag === "true") result.pending_reservations = await pendingReservations(env.DB);
  if (tasksFlag === "true") {
    const cap = Number(url.searchParams.get("task_cap_nanos") || 0);
    if (!safeInt(cap)) return json({ error: "invalid_task_cap" }, 400);
    result.task_summaries = await taskSummaries(env.DB, cap);
  }
  return json(result);
}

async function reserve(env, input) {
  if (!validText(input.provider, 100) || !validText(input.model, 200) ||
      !validText(input.task_kind, 100) ||
      (input.task_id !== null && input.task_id !== undefined && !validText(input.task_id, 200)) ||
      !safeInt(input.estimated_cost_nanos) || !safeInt(input.daily_budget_nanos) ||
      !safeInt(input.monthly_budget_nanos) || !safeInt(input.per_task_budget_nanos)) {
    return json({ error: "invalid_reservation" }, 400);
  }
  if (input.estimated_cost_nanos > 0 && !validText(input.task_id, 200)) {
    return json({ error: "task_id_required" }, 400);
  }
  const { now, day, month } = windowKeys(new Date().toISOString());
  const taskId = input.task_id || null;
  const sql = `INSERT INTO model_usage_reservations(
      provider,model,task_kind,task_id,created_at,estimated_cost_nanos
    )
    SELECT ?,?,?,?,?,?
    WHERE
      (SELECT COALESCE(SUM(cost_nanos),0) FROM model_usage WHERE substr(created_at,1,10)=?)
      + (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM model_usage_reservations WHERE settled_at IS NULL)
      + ? <= ?
      AND
      (SELECT COALESCE(SUM(cost_nanos),0) FROM model_usage WHERE substr(created_at,1,7)=?)
      + (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM model_usage_reservations WHERE settled_at IS NULL)
      + ? <= ?
      AND
      (SELECT COALESCE(SUM(cost_nanos),0) FROM model_usage WHERE task_id=?)
      + (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM model_usage_reservations WHERE task_id=? AND settled_at IS NULL)
      + ? <= ?
    RETURNING id`;
  const result = await env.DB.prepare(sql).bind(
    input.provider, input.model, input.task_kind, taskId, now, input.estimated_cost_nanos,
    day, input.estimated_cost_nanos, input.daily_budget_nanos,
    month, input.estimated_cost_nanos, input.monthly_budget_nanos,
    taskId, taskId, input.estimated_cost_nanos, input.per_task_budget_nanos,
  ).first();
  if (!result) return json({ error: "budget_exceeded" }, 409);
  return json({ reservation_id: dbInt(result.id) }, 201);
}

async function release(env, input) {
  if (!Number.isSafeInteger(input.reservation_id) || input.reservation_id < 1) {
    return json({ error: "invalid_reservation_id" }, 400);
  }
  const deleted = await env.DB.prepare(
    "DELETE FROM model_usage_reservations WHERE id=? AND settled_at IS NULL RETURNING id"
  ).bind(input.reservation_id).first();
  if (deleted) return json({ ok: true });
  const existing = await env.DB.prepare(
    "SELECT id,settled_at FROM model_usage_reservations WHERE id=?"
  ).bind(input.reservation_id).first();
  if (!existing) return json({ error: "reservation_not_found" }, 404);
  return json({ error: "settled_reservation_cannot_be_released" }, 409);
}

async function settle(env, input) {
  if (!Number.isSafeInteger(input.reservation_id) || input.reservation_id < 1 ||
      !safeInt(input.input_tokens) || !safeInt(input.output_tokens) ||
      !safeInt(input.actual_cost_nanos)) {
    return json({ error: "invalid_settlement" }, 400);
  }
  const before = await env.DB.prepare(
    "SELECT id,settled_at,actual_cost_nanos,input_tokens,output_tokens FROM model_usage_reservations WHERE id=?"
  ).bind(input.reservation_id).first();
  if (!before) return json({ error: "reservation_not_found" }, 404);
  if (before.settled_at !== null) {
    const same = dbInt(before.actual_cost_nanos) === input.actual_cost_nanos &&
      dbInt(before.input_tokens) === input.input_tokens &&
      dbInt(before.output_tokens) === input.output_tokens;
    if (!same) return json({ error: "settlement_conflict" }, 409);
    const current = await totals(env.DB, new Date().toISOString());
    return json({ ...current, replay: true });
  }

  const now = new Date().toISOString();
  const update = env.DB.prepare(
    "UPDATE model_usage_reservations SET settled_at=?,actual_cost_nanos=?,input_tokens=?,output_tokens=? WHERE id=? AND settled_at IS NULL"
  ).bind(now, input.actual_cost_nanos, input.input_tokens, input.output_tokens, input.reservation_id);
  const insert = env.DB.prepare(
    `INSERT INTO model_usage(
      provider,model,task_kind,input_tokens,output_tokens,cost_usd,created_at,cost_nanos,task_id,reservation_id
    )
    SELECT provider,model,task_kind,?,?,?,?,?,task_id,id
    FROM model_usage_reservations
    WHERE id=? AND settled_at=? AND actual_cost_nanos=? AND input_tokens=? AND output_tokens=?
    ON CONFLICT(reservation_id) DO NOTHING`
  ).bind(
    input.input_tokens, input.output_tokens, input.actual_cost_nanos / 1_000_000_000,
    now, input.actual_cost_nanos, input.reservation_id, now,
    input.actual_cost_nanos, input.input_tokens, input.output_tokens,
  );
  await env.DB.batch([update, insert]);

  const [after, usage] = await Promise.all([
    env.DB.prepare(
      "SELECT settled_at,actual_cost_nanos,input_tokens,output_tokens FROM model_usage_reservations WHERE id=?"
    ).bind(input.reservation_id).first(),
    env.DB.prepare(
      "SELECT id FROM model_usage WHERE reservation_id=?"
    ).bind(input.reservation_id).first(),
  ]);
  if (!after || after.settled_at === null || !usage) return json({ error: "settlement_unavailable" }, 503);
  const same = dbInt(after.actual_cost_nanos) === input.actual_cost_nanos &&
    dbInt(after.input_tokens) === input.input_tokens &&
    dbInt(after.output_tokens) === input.output_tokens;
  if (!same) return json({ error: "settlement_conflict" }, 409);
  const current = await totals(env.DB, new Date().toISOString());
  return json({ ...current, replay: false });
}

async function record(env, input) {
  if (!validText(input.provider, 100) || !validText(input.model, 200) ||
      !validText(input.task_kind, 100) ||
      (input.task_id !== null && input.task_id !== undefined && !validText(input.task_id, 200)) ||
      !safeInt(input.input_tokens) || !safeInt(input.output_tokens) || !safeInt(input.cost_nanos)) {
    return json({ error: "invalid_usage_record" }, 400);
  }
  if (input.cost_nanos > 0 && !validText(input.task_id, 200)) {
    return json({ error: "task_id_required" }, 400);
  }
  const now = new Date().toISOString();
  const result = await env.DB.prepare(
    "INSERT INTO model_usage(provider,model,task_kind,input_tokens,output_tokens,cost_usd,created_at,cost_nanos,task_id) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id"
  ).bind(
    input.provider, input.model, input.task_kind, input.input_tokens, input.output_tokens,
    input.cost_nanos / 1_000_000_000, now, input.cost_nanos, input.task_id || null,
  ).first();
  return json({ id: dbInt(result.id) }, 201);
}

export async function handleUsage(request, env, url) {
  if (url.pathname !== "/v1/usage") return null;
  if (request.method === "GET") {
    try { return await getUsage(request, env, url); }
    catch { return json({ error: "storage_unavailable" }, 503); }
  }
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  const input = await readBody(request);
  if (!input || typeof input !== "object" || Array.isArray(input)) return json({ error: "invalid_json" }, 400);
  try {
    if (input.action === "reserve") return await reserve(env, input);
    if (input.action === "release") return await release(env, input);
    if (input.action === "settle") return await settle(env, input);
    if (input.action === "record") return await record(env, input);
    return json({ error: "unsupported_action" }, 400);
  } catch { return json({ error: "storage_unavailable" }, 503); }
}
