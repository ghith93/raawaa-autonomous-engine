const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const PENDING = "'reserved','in_flight','ambiguous'";
const COST_BASES = new Set(['provider_reported','verified_profile_estimate','verified_profile_floor','provider_rejected_429']);
const AMBIGUOUS_REASONS = new Set(['provider_timeout','transport_error','provider_5xx','invalid_response','adapter_error','settlement_uncertain']);
const RELEASE_REASONS = new Set(['quota_reservation_failed','pre_provider_validation_failed','provider_not_called']);

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}
function validText(value, max) {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= max;
}
function nonNegativeInt(value) {
  return Number.isSafeInteger(value) && value >= 0;
}
function positiveInt(value) {
  return Number.isSafeInteger(value) && value > 0;
}
function validReservationId(value) {
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
async function readBody(request) {
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 8192) return null;
  try { return JSON.parse(raw); } catch { return null; }
}
function utcParts(now) {
  return { day: now.slice(0,10), month: now.slice(0,7) };
}
function usd(nanos) { return Number(nanos || 0) / 1000000000; }

// A single conditional INSERT is the budget linearization point. D1 serializes
// writes; the predicates include settled spend plus every unresolved reservation
// across all providers. The policy key is fixed to 'default' in the Worker API;
// the exported builder also permits isolated CI policy rows in Staging.
export function buildReserveStatement(input, now, policyKey = 'default') {
  const { day, month } = utcParts(now);
  const sql = `
    INSERT OR IGNORE INTO monetary_reservations(
      reservation_id,policy_key,provider,model,task_kind,task_id,utc_day,utc_month,
      estimated_cost_nanos,status,created_at,updated_at
    )
    SELECT ?,?,?,?,?,?,?,?,?,'reserved',?,?
    FROM monetary_budget_policies p
    WHERE p.policy_key=? AND p.enabled=1
      AND (
        (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations
          WHERE policy_key=p.policy_key AND status='settled' AND utc_day=?)
        + (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations
          WHERE policy_key=p.policy_key AND status IN (${PENDING})) + ? <= p.daily_cap_nanos
      )
      AND (
        (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations
          WHERE policy_key=p.policy_key AND status='settled' AND utc_month=?)
        + (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations
          WHERE policy_key=p.policy_key AND status IN (${PENDING})) + ? <= p.monthly_cap_nanos
      )
      AND (
        (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations
          WHERE policy_key=p.policy_key AND status='settled' AND task_id=?)
        + (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations
          WHERE policy_key=p.policy_key AND status IN (${PENDING}) AND task_id=?) + ? <= p.per_task_cap_nanos
      )
    RETURNING reservation_id
  `;
  return { sql, params: [
    input.reservation_id, policyKey, input.provider, input.model, input.task_kind, input.task_id,
    day, month, input.estimated_cost_nanos, now, now, policyKey,
    day, input.estimated_cost_nanos, month, input.estimated_cost_nanos,
    input.task_id, input.task_id, input.estimated_cost_nanos,
  ] };
}

export function buildStatusStatement(policyKey, day, month, taskId = null) {
  return {
    sql: `SELECT p.daily_cap_nanos,p.monthly_cap_nanos,p.per_task_cap_nanos,
      (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE policy_key=p.policy_key AND status='settled' AND utc_day=?) daily_settled_nanos,
      (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE policy_key=p.policy_key AND status='settled' AND utc_month=?) monthly_settled_nanos,
      (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations WHERE policy_key=p.policy_key AND status IN (${PENDING})) pending_nanos,
      (SELECT COUNT(*) FROM monetary_reservations WHERE policy_key=p.policy_key AND status IN (${PENDING})) pending_count,
      (SELECT COUNT(*) FROM monetary_reservations WHERE policy_key=p.policy_key AND status='ambiguous') ambiguous_count,
      (SELECT COALESCE(SUM(actual_cost_nanos),0) FROM monetary_reservations WHERE policy_key=p.policy_key AND task_id=? AND status='settled') task_settled_nanos,
      (SELECT COALESCE(SUM(estimated_cost_nanos),0) FROM monetary_reservations WHERE policy_key=p.policy_key AND task_id=? AND status IN (${PENDING})) task_pending_nanos
      FROM monetary_budget_policies p WHERE p.policy_key=? AND p.enabled=1`,
    params: [day, month, taskId, taskId, policyKey],
  };
}

export function buildStartStatement(reservationId, now) {
  return { sql: "UPDATE monetary_reservations SET status='in_flight',provider_io_started_at=?,updated_at=? WHERE reservation_id=? AND status='reserved' AND provider_io_started_at IS NULL RETURNING reservation_id", params: [now, now, reservationId] };
}
export function buildAmbiguousStatement(reservationId, now, reason) {
  return { sql: "UPDATE monetary_reservations SET status='ambiguous',ambiguous_at=?,ambiguous_reason=?,updated_at=? WHERE reservation_id=? AND status='in_flight' AND provider_io_started_at IS NOT NULL RETURNING reservation_id", params: [now, reason, now, reservationId] };
}
export function buildSettleStatement(reservationId, now, input) {
  return { sql: "UPDATE monetary_reservations SET status='settled',actual_cost_nanos=?,input_tokens=?,output_tokens=?,cost_basis=?,settled_at=?,updated_at=? WHERE reservation_id=? AND status IN ('in_flight','ambiguous') AND provider_io_started_at IS NOT NULL RETURNING reservation_id", params: [input.actual_cost_nanos, input.input_tokens, input.output_tokens, input.cost_basis, now, now, reservationId] };
}
export function buildReleaseStatement(reservationId, now, reason) {
  return { sql: "UPDATE monetary_reservations SET status='released',released_at=?,release_reason=?,updated_at=? WHERE reservation_id=? AND status='reserved' AND provider_io_started_at IS NULL RETURNING reservation_id", params: [now, reason, now, reservationId] };
}

export async function readMonetaryBudgetStatus(db, taskId = null, policyKey = 'default') {
  const now = new Date().toISOString();
  const { day, month } = utcParts(now);
  const statement = buildStatusStatement(policyKey, day, month, taskId);
  const row = await db.prepare(statement.sql).bind(...statement.params).first();
  if (!row) return null;
  const dailyCap = Number(row.daily_cap_nanos);
  const monthlyCap = Number(row.monthly_cap_nanos);
  const taskCap = Number(row.per_task_cap_nanos);
  const pending = Number(row.pending_nanos) || 0;
  const dailySettled = Number(row.daily_settled_nanos) || 0;
  const monthlySettled = Number(row.monthly_settled_nanos) || 0;
  const taskSettled = Number(row.task_settled_nanos) || 0;
  const taskPending = Number(row.task_pending_nanos) || 0;
  const dailyTotal = dailySettled + pending;
  const monthlyTotal = monthlySettled + pending;
  const taskTotal = taskSettled + taskPending;
  return {
    available: true, currency: 'USD', policy_key: policyKey,
    caps: { daily_nanos: dailyCap, monthly_nanos: monthlyCap, per_task_nanos: taskCap, daily_usd: usd(dailyCap), monthly_usd: usd(monthlyCap), per_task_usd: usd(taskCap) },
    daily: { settled_nanos: dailySettled, pending_nanos: pending, total_nanos: dailyTotal, settled_usd: usd(dailySettled), pending_usd: usd(pending), total_usd: usd(dailyTotal), over_budget: dailyTotal > dailyCap },
    monthly: { settled_nanos: monthlySettled, pending_nanos: pending, total_nanos: monthlyTotal, settled_usd: usd(monthlySettled), pending_usd: usd(pending), total_usd: usd(monthlyTotal), over_budget: monthlyTotal > monthlyCap },
    per_task: taskId ? { settled_nanos: taskSettled, pending_nanos: taskPending, total_nanos: taskTotal, settled_usd: usd(taskSettled), pending_usd: usd(taskPending), total_usd: usd(taskTotal), over_budget: taskTotal > taskCap } : null,
    pending: { count: Number(row.pending_count) || 0, ambiguous_count: Number(row.ambiguous_count) || 0 },
  };
}

export async function handleMonetaryLedger(request, env, url) {
  if (url.pathname !== '/v1/monetary-ledger') return null;
  if (request.method === 'GET') {
    const taskId = url.searchParams.get('task_id');
    if (taskId !== null && !validText(taskId, 200)) return json({ error: 'invalid_query' }, 400);
    try {
      const status = await readMonetaryBudgetStatus(env.DB, taskId);
      return status ? json(status) : json({ error: 'policy_unavailable' }, 503);
    } catch { return json({ error: 'storage_unavailable' }, 503); }
  }
  if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
  const input = await readBody(request);
  if (!input || !validText(input.action, 30)) return json({ error: 'invalid_body' }, 400);
  const now = new Date().toISOString();
  try {
    if (input.action === 'reserve') {
      if (!validReservationId(input.reservation_id) || !validText(input.provider,100) || !validText(input.model,200) || !validText(input.task_kind,100) || !validText(input.task_id,200) || !nonNegativeInt(input.estimated_cost_nanos) || !positiveInt(input.daily_cap_nanos) || !positiveInt(input.monthly_cap_nanos) || !positiveInt(input.per_task_cap_nanos)) return json({ error: 'invalid_reservation' }, 400);
      const policy = await env.DB.prepare("SELECT daily_cap_nanos,monthly_cap_nanos,per_task_cap_nanos FROM monetary_budget_policies WHERE policy_key='default' AND enabled=1").first();
      if (!policy) return json({ error: 'policy_unavailable' }, 503);
      if (Number(policy.daily_cap_nanos) !== input.daily_cap_nanos || Number(policy.monthly_cap_nanos) !== input.monthly_cap_nanos || Number(policy.per_task_cap_nanos) !== input.per_task_cap_nanos) return json({ error: 'budget_policy_mismatch' }, 409);
      const statement = buildReserveStatement(input, now, 'default');
      const inserted = await env.DB.prepare(statement.sql).bind(...statement.params).first();
      if (inserted?.reservation_id) return json({ reservation_id: inserted.reservation_id, status: 'reserved', estimated_cost_nanos: input.estimated_cost_nanos }, 201);
      const existing = await env.DB.prepare('SELECT policy_key,provider,model,task_kind,task_id,estimated_cost_nanos,status FROM monetary_reservations WHERE reservation_id=?').bind(input.reservation_id).first();
      if (existing) {
        const same = existing.policy_key === 'default' && existing.provider === input.provider && existing.model === input.model && existing.task_kind === input.task_kind && existing.task_id === input.task_id && Number(existing.estimated_cost_nanos) === input.estimated_cost_nanos;
        if (same && existing.status !== 'released') return json({ reservation_id: input.reservation_id, status: existing.status, replay: true }, 200);
        return json({ error: 'reservation_conflict' }, 409);
      }
      return json({ error: 'budget_exceeded' }, 409);
    }
    if (!validReservationId(input.reservation_id)) return json({ error: 'invalid_reservation_id' }, 400);
    if (input.action === 'mark_started') {
      const statement = buildStartStatement(input.reservation_id, now);
      const changed = await env.DB.prepare(statement.sql).bind(...statement.params).first();
      if (changed?.reservation_id) return json({ ok: true, status: 'in_flight' });
      const existing = await env.DB.prepare('SELECT status FROM monetary_reservations WHERE reservation_id=?').bind(input.reservation_id).first();
      if (!existing) return json({ error: 'reservation_not_found' }, 404);
      if (existing.status === 'in_flight') return json({ ok: true, status: 'in_flight', replay: true });
      return json({ error: 'reservation_state_conflict' }, 409);
    }
    if (input.action === 'mark_ambiguous') {
      if (!AMBIGUOUS_REASONS.has(input.reason)) return json({ error: 'invalid_ambiguous_reason' }, 400);
      const statement = buildAmbiguousStatement(input.reservation_id, now, input.reason);
      const changed = await env.DB.prepare(statement.sql).bind(...statement.params).first();
      if (changed?.reservation_id) return json({ ok: true, status: 'ambiguous' });
      const existing = await env.DB.prepare('SELECT status FROM monetary_reservations WHERE reservation_id=?').bind(input.reservation_id).first();
      if (!existing) return json({ error: 'reservation_not_found' }, 404);
      if (existing.status === 'ambiguous' || existing.status === 'settled') return json({ ok: true, status: existing.status, replay: true });
      return json({ error: 'reservation_state_conflict' }, 409);
    }
    if (input.action === 'settle') {
      if (!nonNegativeInt(input.actual_cost_nanos) || !nonNegativeInt(input.input_tokens) || !nonNegativeInt(input.output_tokens) || !COST_BASES.has(input.cost_basis)) return json({ error: 'invalid_settlement' }, 400);
      const statement = buildSettleStatement(input.reservation_id, now, input);
      const changed = await env.DB.prepare(statement.sql).bind(...statement.params).first();
      if (!changed?.reservation_id) {
        const existing = await env.DB.prepare('SELECT status,actual_cost_nanos,input_tokens,output_tokens,cost_basis,task_id FROM monetary_reservations WHERE reservation_id=?').bind(input.reservation_id).first();
        if (!existing) return json({ error: 'reservation_not_found' }, 404);
        const same = existing.status === 'settled' && Number(existing.actual_cost_nanos) === input.actual_cost_nanos && Number(existing.input_tokens) === input.input_tokens && Number(existing.output_tokens) === input.output_tokens && existing.cost_basis === input.cost_basis;
        if (!same) return json({ error: 'settlement_conflict' }, 409);
        const status = await readMonetaryBudgetStatus(env.DB, existing.task_id);
        return json({ ok: true, status: 'settled', replay: true, over_budget: Boolean(status?.daily.over_budget || status?.monthly.over_budget || status?.per_task?.over_budget), budget_status: status });
      }
      const existing = await env.DB.prepare('SELECT task_id FROM monetary_reservations WHERE reservation_id=?').bind(input.reservation_id).first();
      const status = await readMonetaryBudgetStatus(env.DB, existing?.task_id || null);
      return json({ ok: true, status: 'settled', replay: false, over_budget: Boolean(status?.daily.over_budget || status?.monthly.over_budget || status?.per_task?.over_budget), budget_status: status });
    }
    if (input.action === 'release') {
      if (!RELEASE_REASONS.has(input.reason)) return json({ error: 'invalid_release_reason' }, 400);
      const statement = buildReleaseStatement(input.reservation_id, now, input.reason);
      const changed = await env.DB.prepare(statement.sql).bind(...statement.params).first();
      if (changed?.reservation_id) return json({ ok: true, status: 'released' });
      const existing = await env.DB.prepare('SELECT status,release_reason FROM monetary_reservations WHERE reservation_id=?').bind(input.reservation_id).first();
      if (!existing) return json({ error: 'reservation_not_found' }, 404);
      if (existing.status === 'released' && existing.release_reason === input.reason) return json({ ok: true, status: 'released', replay: true });
      return json({ error: 'release_after_provider_io_forbidden' }, 409);
    }
    return json({ error: 'unsupported_action' }, 400);
  } catch { return json({ error: 'storage_unavailable' }, 503); }
}
