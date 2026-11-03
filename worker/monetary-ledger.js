function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
  });
}
function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}
function safeInt(value) { return Number.isSafeInteger(value) && value >= 0; }
function uuid(value) {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
async function readBody(request) {
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 8192) throw new Error("body_too_large");
  try { return JSON.parse(raw); } catch { throw new Error("invalid_json"); }
}
function sameReservation(row, input) {
  return row.provider === input.provider && row.model === input.model &&
    row.task_kind === input.task_kind && row.task_id === input.task_id &&
    Number(row.estimated_cost_nanos) === input.estimated_cost_nanos &&
    Number(row.daily_budget_nanos) === input.daily_budget_nanos &&
    Number(row.monthly_budget_nanos) === input.monthly_budget_nanos &&
    Number(row.per_task_budget_nanos) === input.per_task_budget_nanos;
}
async function spendSnapshot(db, taskId, now) {
  const date = new Date(now);
  const day = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())).toISOString();
  const month = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)).toISOString();
  const pendingWhere = "status IN ('reserved','in_flight','ambiguous')";
  const [daily, monthly, pending, taskActual, taskPending, pendingRows] = await Promise.all([
    db.prepare("SELECT COALESCE(SUM(actual_cost_nanos),0) n FROM monetary_reservations WHERE status='settled' AND settled_at>=?").bind(day).first(),
    db.prepare("SELECT COALESCE(SUM(actual_cost_nanos),0) n FROM monetary_reservations WHERE status='settled' AND settled_at>=?").bind(month).first(),
    db.prepare("SELECT COALESCE(SUM(estimated_cost_nanos),0) n,COUNT(*) c FROM monetary_reservations WHERE "+pendingWhere).first(),
    taskId ? db.prepare("SELECT COALESCE(SUM(actual_cost_nanos),0) n FROM monetary_reservations WHERE task_id=? AND status='settled'").bind(taskId).first() : Promise.resolve({n:0}),
    taskId ? db.prepare("SELECT COALESCE(SUM(estimated_cost_nanos),0) n,COUNT(*) c FROM monetary_reservations WHERE task_id=? AND "+pendingWhere).bind(taskId).first() : Promise.resolve({n:0,c:0}),
    db.prepare("SELECT reservation_id,provider,model,task_kind,task_id,status,estimated_cost_nanos,created_at,started_at FROM monetary_reservations WHERE "+pendingWhere+(taskId?" AND task_id=?":"")+" ORDER BY created_at LIMIT 100").bind(...(taskId?[taskId]:[])).all()
  ]);
  const reserved = Number(pending?.n || 0);
  return {
    daily_spent_nanos: Number(daily?.n || 0),
    monthly_spent_nanos: Number(monthly?.n || 0),
    pending_reserved_nanos: reserved,
    daily_total_nanos: Number(daily?.n || 0)+reserved,
    monthly_total_nanos: Number(monthly?.n || 0)+reserved,
    pending_count: Number(pending?.c || 0),
    task_id: taskId || null,
    task_spent_nanos: Number(taskActual?.n || 0),
    task_reserved_nanos: Number(taskPending?.n || 0),
    task_total_nanos: Number(taskActual?.n || 0)+Number(taskPending?.n || 0),
    task_pending_count: Number(taskPending?.c || 0),
    unresolved: (pendingRows.results || []).map(row => ({
      reservation_id: row.reservation_id, provider: row.provider, model: row.model,
      task_kind: row.task_kind, task_id: row.task_id, status: row.status,
      estimated_cost_nanos: Number(row.estimated_cost_nanos), created_at: row.created_at,
      started_at: row.started_at
    }))
  };
}
export async function handleMonetaryLedger(request, env, url) {
  if (url.pathname !== "/v1/monetary-ledger") return null;
  if (request.method === "GET") {
    const taskId = url.searchParams.get("task_id");
    if (taskId !== null && !validText(taskId, 200)) return json({error:"invalid_query"},400);
    try {
      const now = new Date().toISOString();
      const snapshot = await spendSnapshot(env.DB, taskId, now);
      const quarantines = await env.DB.prepare("SELECT route_id,reason,reservation_id,quarantined_at FROM monetary_route_quarantines ORDER BY quarantined_at DESC LIMIT 100").all();
      return json({snapshot,quarantined_routes:quarantines.results || []});
    } catch { return json({error:"storage_unavailable"},503); }
  }
  if (request.method !== "POST") return json({error:"method_not_allowed"},405);
  let input;
  try { input = await readBody(request); }
  catch (error) { return json({error:error.message},400); }
  if (!input || !validText(input.action,24)) return json({error:"invalid_body"},400);
  const now = new Date().toISOString();
  try {
    if (input.action === "reserve") {
      const required = ["provider","model","task_kind","task_id"];
      if (required.some(key => !validText(input[key],200)) ||
          !uuid(input.reservation_id) || !uuid(input.idempotency_key) ||
          !["estimated_cost_nanos","daily_budget_nanos","monthly_budget_nanos","per_task_budget_nanos"].every(key => safeInt(input[key]))) {
        return json({error:"invalid_reservation"},400);
      }
      const existing = await env.DB.prepare("SELECT * FROM monetary_reservations WHERE idempotency_key=?").bind(input.idempotency_key).first();
      if (existing) return sameReservation(existing,input)
        ? json({reservation_id:existing.reservation_id,status:existing.status,replay:true})
        : json({error:"idempotency_conflict"},409);
      const date = new Date(now);
      const day = new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),date.getUTCDate())).toISOString();
      const month = new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),1)).toISOString();
      const pending = "status IN ('reserved','in_flight','ambiguous')";
      let row;
      try {
        row = await env.DB.prepare(`
        INSERT INTO monetary_reservations(
          reservation_id,idempotency_key,provider,model,task_kind,task_id,status,
          estimated_cost_nanos,daily_budget_nanos,monthly_budget_nanos,per_task_budget_nanos,created_at
        )
        SELECT ?,?,?,?,?,?,'reserved',?,?,?,?,?
        WHERE NOT EXISTS (SELECT 1 FROM monetary_route_quarantines WHERE route_id=?)
          AND (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE status='settled' AND settled_at>=?) +
          (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations WHERE ${pending}) + ? <= ?
          AND
          (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE status='settled' AND settled_at>=?) +
          (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations WHERE ${pending}) + ? <= ?
          AND
          (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE task_id=? AND status='settled') +
          (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations WHERE task_id=? AND ${pending}) + ? <= ?
        RETURNING reservation_id,status
      `).bind(
        input.reservation_id,input.idempotency_key,input.provider,input.model,input.task_kind,input.task_id,
        input.estimated_cost_nanos,input.daily_budget_nanos,input.monthly_budget_nanos,input.per_task_budget_nanos,now,
        input.provider+":"+input.model,
        day,input.estimated_cost_nanos,input.daily_budget_nanos,
        month,input.estimated_cost_nanos,input.monthly_budget_nanos,
        input.task_id,input.task_id,input.estimated_cost_nanos,input.per_task_budget_nanos
        ).first();
      } catch {
        // A concurrent retry with the same idempotency key may win the unique insert.
        const raced = await env.DB.prepare("SELECT * FROM monetary_reservations WHERE idempotency_key=?").bind(input.idempotency_key).first();
        if (raced) return sameReservation(raced,input)
          ? json({reservation_id:raced.reservation_id,status:raced.status,replay:true})
          : json({error:"idempotency_conflict"},409);
        throw new Error("monetary_reservation_insert_failed");
      }
      if (row) return json({reservation_id:row.reservation_id,status:row.status,replay:false},201);
      const raced = await env.DB.prepare("SELECT * FROM monetary_reservations WHERE idempotency_key=?").bind(input.idempotency_key).first();
      if (raced) return sameReservation(raced,input)
        ? json({reservation_id:raced.reservation_id,status:raced.status,replay:true})
        : json({error:"idempotency_conflict"},409);
      return json({error:"budget_exceeded"},409);
    }
    if (input.action === "start") {
      if (!uuid(input.reservation_id)) return json({error:"invalid_reservation_id"},400);
      const updated = await env.DB.prepare("UPDATE monetary_reservations SET status='in_flight',started_at=? WHERE reservation_id=? AND status='reserved'").bind(now,input.reservation_id).run();
      if (updated.meta?.changes === 1) return json({ok:true,status:"in_flight"});
      const row = await env.DB.prepare("SELECT status FROM monetary_reservations WHERE reservation_id=?").bind(input.reservation_id).first();
      if (!row) return json({error:"reservation_not_found"},404);
      return row.status === "in_flight" ? json({ok:true,status:"in_flight",replay:true}) : json({error:"invalid_reservation_state"},409);
    }
    if (input.action === "ambiguous") {
      if (!uuid(input.reservation_id)) return json({error:"invalid_reservation_id"},400);
      const updated = await env.DB.prepare("UPDATE monetary_reservations SET status='ambiguous' WHERE reservation_id=? AND status='in_flight'").bind(input.reservation_id).run();
      if (updated.meta?.changes === 1) return json({ok:true,status:"ambiguous"});
      const row = await env.DB.prepare("SELECT status FROM monetary_reservations WHERE reservation_id=?").bind(input.reservation_id).first();
      if (!row) return json({error:"reservation_not_found"},404);
      return row.status === "ambiguous" ? json({ok:true,status:"ambiguous",replay:true}) : json({error:"invalid_reservation_state"},409);
    }
    if (input.action === "release") {
      if (!uuid(input.reservation_id)) return json({error:"invalid_reservation_id"},400);
      const updated = await env.DB.prepare("UPDATE monetary_reservations SET status='released',released_at=? WHERE reservation_id=? AND status='reserved'").bind(now,input.reservation_id).run();
      if (updated.meta?.changes === 1) return json({ok:true,released:true});
      const row = await env.DB.prepare("SELECT status FROM monetary_reservations WHERE reservation_id=?").bind(input.reservation_id).first();
      if (!row) return json({error:"reservation_not_found"},404);
      return row.status === "released" ? json({ok:true,released:true,replay:true}) : json({error:"release_after_provider_start_forbidden"},409);
    }
    if (input.action === "settle") {
      if (!uuid(input.reservation_id) || !safeInt(input.actual_cost_nanos) ||
          !safeInt(input.input_tokens) || !safeInt(input.output_tokens)) return json({error:"invalid_settlement"},400);
      const row = await env.DB.prepare("SELECT * FROM monetary_reservations WHERE reservation_id=?").bind(input.reservation_id).first();
      if (!row) return json({error:"reservation_not_found"},404);
      if (row.status === "settled") {
        const same = Number(row.actual_cost_nanos) === input.actual_cost_nanos &&
          Number(row.input_tokens) === input.input_tokens && Number(row.output_tokens) === input.output_tokens;
        return same ? json({ok:true,settled:true,replay:true}) : json({error:"settlement_conflict"},409);
      }
      if (!["in_flight","ambiguous"].includes(row.status)) return json({error:"invalid_reservation_state"},409);
      const updated = await env.DB.prepare("UPDATE monetary_reservations SET status='settled',actual_cost_nanos=?,input_tokens=?,output_tokens=?,settled_at=? WHERE reservation_id=? AND status IN ('in_flight','ambiguous')").bind(input.actual_cost_nanos,input.input_tokens,input.output_tokens,now,input.reservation_id).run();
      if (updated.meta?.changes !== 1) {
        const current = await env.DB.prepare("SELECT status,actual_cost_nanos,input_tokens,output_tokens FROM monetary_reservations WHERE reservation_id=?").bind(input.reservation_id).first();
        if (current?.status === "settled" && Number(current.actual_cost_nanos) === input.actual_cost_nanos &&
            Number(current.input_tokens) === input.input_tokens && Number(current.output_tokens) === input.output_tokens) {
          return json({ok:true,settled:true,replay:true});
        }
        return json({error:"settlement_conflict"},409);
      }
      const totals = await spendSnapshot(env.DB,row.task_id,now);
      const exceeded = totals.daily_total_nanos > Number(row.daily_budget_nanos) ||
        totals.monthly_total_nanos > Number(row.monthly_budget_nanos) ||
        totals.task_total_nanos > Number(row.per_task_budget_nanos) ||
        input.actual_cost_nanos > Number(row.estimated_cost_nanos);
      if (exceeded) {
        const reason = input.actual_cost_nanos > Number(row.estimated_cost_nanos) ? "actual_cost_exceeded_estimate" : "monetary_budget_exceeded";
        await env.DB.prepare("INSERT INTO monetary_route_quarantines(route_id,reason,reservation_id,quarantined_at) VALUES(?,?,?,?) ON CONFLICT(route_id) DO UPDATE SET reason=excluded.reason,reservation_id=excluded.reservation_id,quarantined_at=excluded.quarantined_at").bind(row.provider+":"+row.model,reason,row.reservation_id,now).run();
      }
      return json({ok:true,settled:true,over_budget:exceeded,totals});
    }
    return json({error:"unsupported_action"},400);
  } catch { return json({error:"storage_unavailable"},503); }
}
