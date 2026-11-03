const MODEL_ID = "@cf/google/gemma-4-26b-a4b-it";
const DAILY_CAP_NEURONS = 10000;
const ACTIVE_STATUSES = "'reserved','in_flight','ambiguous','settled'";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}
function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}
function positiveInt(value) {
  return Number.isSafeInteger(value) && value > 0;
}
function nonNegativeInt(value) {
  return Number.isSafeInteger(value) && value >= 0;
}
function uuid(value) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
function tokenMatches(request, expected) {
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
function isApiCaller(request, env) {
  return tokenMatches(request, env.ENGINE_API_TOKEN);
}
function isAdminCaller(request, env) {
  return Boolean(env.ENGINE_QUOTA_ADMIN_TOKEN) &&
    env.ENGINE_QUOTA_ADMIN_TOKEN !== env.ENGINE_API_TOKEN &&
    tokenMatches(request, env.ENGINE_QUOTA_ADMIN_TOKEN);
}
async function readBody(request) {
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
function sameReservation(row, input) {
  return row.reservation_id === input.reservation_id &&
    row.model_id === input.model_id &&
    row.task_kind === input.task_kind &&
    row.task_id === input.task_id &&
    row.data_classification === input.data_classification &&
    Number(row.estimated_neurons) === input.estimated_neurons;
}
async function policyRow(db) {
  return db.prepare(
    "SELECT policy_key,daily_cap_neurons,allowed_model_id,enabled,updated_at " +
    "FROM workers_ai_neuron_policy WHERE policy_key='default'"
  ).first();
}
async function snapshot(db, now) {
  const day = now.slice(0, 10);
  const [policy, totals, pending, quarantines] = await Promise.all([
    policyRow(db),
    db.prepare(`
      SELECT
        COALESCE(SUM(CASE WHEN status='settled' THEN COALESCE(actual_neurons,estimated_neurons) ELSE 0 END),0) settled_neurons,
        COALESCE(SUM(CASE WHEN status IN ('reserved','in_flight','ambiguous') THEN estimated_neurons ELSE 0 END),0) pending_neurons,
        COALESCE(SUM(CASE WHEN status IN (${ACTIVE_STATUSES}) THEN
          CASE WHEN status='settled' THEN COALESCE(actual_neurons,estimated_neurons) ELSE estimated_neurons END
        ELSE 0 END),0) total_neurons
      FROM workers_ai_neuron_reservations WHERE day_utc=?
    `).bind(day).first(),
    db.prepare(
      "SELECT reservation_id,model_id,task_kind,task_id,status,estimated_neurons,created_at,started_at " +
      "FROM workers_ai_neuron_reservations WHERE day_utc=? AND status IN ('reserved','in_flight','ambiguous') " +
      "ORDER BY created_at LIMIT 50"
    ).bind(day).all(),
    db.prepare(
      "SELECT model_id,reason,reservation_id,quarantined_at FROM workers_ai_neuron_quarantines ORDER BY quarantined_at DESC LIMIT 20"
    ).all(),
  ]);
  if (!policy) return null;
  const settled = Number(totals?.settled_neurons || 0);
  const pendingNeurons = Number(totals?.pending_neurons || 0);
  const total = Number(totals?.total_neurons || 0);
  const cap = Number(policy.daily_cap_neurons);
  return {
    policy: {
      policy_key: policy.policy_key,
      daily_cap_neurons: cap,
      allowed_model_id: policy.allowed_model_id,
      enabled: Number(policy.enabled) === 1,
      updated_at: policy.updated_at,
    },
    usage: {
      day_utc: day,
      settled_neurons: settled,
      pending_neurons: pendingNeurons,
      total_neurons: total,
      remaining_neurons: Math.max(0, cap - total),
    },
    unresolved: (pending.results || []).map(row => ({
      reservation_id: row.reservation_id,
      model_id: row.model_id,
      task_kind: row.task_kind,
      task_id: row.task_id,
      status: row.status,
      estimated_neurons: Number(row.estimated_neurons),
      created_at: row.created_at,
      started_at: row.started_at,
    })),
    quarantined_models: quarantines.results || [],
  };
}

async function reserve(db, input, now) {
  if (!uuid(input.reservation_id) || !uuid(input.idempotency_key) ||
      input.model_id !== MODEL_ID || input.task_kind !== "research_brief" ||
      !validText(input.task_id, 200) || input.data_classification !== "public" ||
      !positiveInt(input.estimated_neurons) || input.estimated_neurons > DAILY_CAP_NEURONS) {
    return json({ error: "invalid_neuron_reservation" }, 400);
  }
  const existing = await db.prepare(
    "SELECT reservation_id,idempotency_key,model_id,task_kind,task_id,data_classification,status,estimated_neurons " +
    "FROM workers_ai_neuron_reservations WHERE idempotency_key=?"
  ).bind(input.idempotency_key).first();
  if (existing) {
    return sameReservation(existing, input)
      ? json({ reservation_id: existing.reservation_id, status: existing.status, replay: true })
      : json({ error: "idempotency_conflict" }, 409);
  }

  const policy = await policyRow(db);
  if (!policy) return json({ error: "neuron_policy_unavailable" }, 503);
  if (Number(policy.enabled) !== 1) return json({ error: "neuron_ledger_disabled" }, 403);
  if (Number(policy.daily_cap_neurons) !== DAILY_CAP_NEURONS || policy.allowed_model_id !== MODEL_ID) {
    return json({ error: "neuron_policy_mismatch" }, 503);
  }
  const day = now.slice(0, 10);
  try {
    // This single conditional INSERT is the reservation linearization point.
    // D1 serializes writes; concurrent callers cannot exceed the UTC-day cap.
    const inserted = await db.prepare(`
      INSERT INTO workers_ai_neuron_reservations(
        reservation_id,idempotency_key,model_id,task_kind,task_id,data_classification,
        status,estimated_neurons,actual_neurons,day_utc,created_at
      )
      SELECT ?,?,?,?,?,?,'reserved',?,NULL,?,?
      WHERE EXISTS (
        SELECT 1 FROM workers_ai_neuron_policy
        WHERE policy_key='default' AND enabled=1
          AND daily_cap_neurons=10000 AND allowed_model_id=?
      )
      AND NOT EXISTS (
        SELECT 1 FROM workers_ai_neuron_quarantines WHERE model_id=?
      )
      AND (
        SELECT COALESCE(SUM(
          CASE WHEN status='settled' THEN COALESCE(actual_neurons,estimated_neurons)
               ELSE estimated_neurons END
        ),0)
        FROM workers_ai_neuron_reservations
        WHERE day_utc=? AND status IN ('reserved','in_flight','ambiguous','settled')
      ) + ? <= (
        SELECT daily_cap_neurons FROM workers_ai_neuron_policy WHERE policy_key='default'
      )
      RETURNING reservation_id
    `).bind(
      input.reservation_id, input.idempotency_key, input.model_id, input.task_kind,
      input.task_id, input.data_classification, input.estimated_neurons, day, now,
      input.model_id, input.model_id, day, input.estimated_neurons,
    ).first();
    if (inserted?.reservation_id) {
      return json({ reservation_id: inserted.reservation_id, status: "reserved", replay: false }, 201);
    }
  } catch {
    const raced = await db.prepare(
      "SELECT reservation_id,idempotency_key,model_id,task_kind,task_id,data_classification,status,estimated_neurons " +
      "FROM workers_ai_neuron_reservations WHERE idempotency_key=?"
    ).bind(input.idempotency_key).first();
    if (raced) {
      return sameReservation(raced, input)
        ? json({ reservation_id: raced.reservation_id, status: raced.status, replay: true })
        : json({ error: "idempotency_conflict" }, 409);
    }
    throw new Error("neuron_reservation_storage_unavailable");
  }

  const [quarantine, totals] = await Promise.all([
    db.prepare("SELECT model_id FROM workers_ai_neuron_quarantines WHERE model_id=?").bind(input.model_id).first(),
    db.prepare(`
      SELECT COALESCE(SUM(
        CASE WHEN status='settled' THEN COALESCE(actual_neurons,estimated_neurons)
             ELSE estimated_neurons END
      ),0) total_neurons
      FROM workers_ai_neuron_reservations
      WHERE day_utc=? AND status IN ('reserved','in_flight','ambiguous','settled')
    `).bind(day).first(),
  ]);
  if (quarantine) return json({ error: "model_quarantined" }, 409);
  if (Number(totals?.total_neurons || 0) + input.estimated_neurons > DAILY_CAP_NEURONS) {
    return json({ error: "daily_neuron_cap_exceeded" }, 409);
  }
  return json({ error: "neuron_reservation_rejected" }, 409);
}

async function transition(db, input, action, now) {
  if (!uuid(input.reservation_id)) return json({ error: "invalid_reservation_id" }, 400);
  const row = await db.prepare(
    "SELECT reservation_id,model_id,task_kind,task_id,status,estimated_neurons,actual_neurons " +
    "FROM workers_ai_neuron_reservations WHERE reservation_id=?"
  ).bind(input.reservation_id).first();
  if (!row) return json({ error: "reservation_not_found" }, 404);

  if (action === "mark_in_flight") {
    if (row.status === "in_flight") return json({ reservation_id: row.reservation_id, status: row.status, replay: true });
    if (row.status !== "reserved") return json({ error: "invalid_reservation_transition" }, 409);
    const policy = await policyRow(db);
    if (!policy || Number(policy.enabled) !== 1) return json({ error: "neuron_ledger_disabled" }, 403);
    const quarantine = await db.prepare(
      "SELECT model_id FROM workers_ai_neuron_quarantines WHERE model_id=?"
    ).bind(row.model_id).first();
    if (quarantine) return json({ error: "model_quarantined" }, 409);
    const result = await db.prepare(
      "UPDATE workers_ai_neuron_reservations SET status='in_flight',started_at=? " +
      "WHERE reservation_id=? AND status='reserved'"
    ).bind(now, input.reservation_id).run();
    return Number(result.meta?.changes || 0) === 1
      ? json({ reservation_id: row.reservation_id, status: "in_flight", replay: false })
      : json({ error: "reservation_transition_conflict" }, 409);
  }

  if (action === "ambiguous") {
    if (row.status === "ambiguous") return json({ reservation_id: row.reservation_id, status: row.status, replay: true });
    if (row.status !== "in_flight") return json({ error: "invalid_reservation_transition" }, 409);
    const result = await db.prepare(
      "UPDATE workers_ai_neuron_reservations SET status='ambiguous' " +
      "WHERE reservation_id=? AND status='in_flight'"
    ).bind(input.reservation_id).run();
    return Number(result.meta?.changes || 0) === 1
      ? json({ reservation_id: row.reservation_id, status: "ambiguous", replay: false, reservation_preserved: true })
      : json({ error: "reservation_transition_conflict" }, 409);
  }

  if (action === "release") {
    if (row.status === "released") return json({ reservation_id: row.reservation_id, status: row.status, replay: true });
    if (row.status !== "reserved") return json({ error: "release_after_provider_io_forbidden" }, 409);
    const result = await db.prepare(
      "UPDATE workers_ai_neuron_reservations SET status='released',released_at=? " +
      "WHERE reservation_id=? AND status='reserved'"
    ).bind(now, input.reservation_id).run();
    return Number(result.meta?.changes || 0) === 1
      ? json({ reservation_id: row.reservation_id, status: "released", replay: false })
      : json({ error: "reservation_transition_conflict" }, 409);
  }

  if (action === "settle") {
    if (!nonNegativeInt(input.actual_neurons) || input.actual_neurons > 100000000) {
      return json({ error: "invalid_actual_neurons" }, 400);
    }
    if (row.status === "settled") {
      return Number(row.actual_neurons) === input.actual_neurons
        ? json({ reservation_id: row.reservation_id, status: "settled", replay: true, over_estimate: input.actual_neurons > Number(row.estimated_neurons) })
        : json({ error: "settlement_conflict" }, 409);
    }
    if (!["in_flight", "ambiguous"].includes(row.status)) {
      return json({ error: "settlement_before_provider_io_forbidden" }, 409);
    }
    const results = await db.batch([
      db.prepare(
        "UPDATE workers_ai_neuron_reservations SET status='settled',actual_neurons=?,settled_at=? " +
        "WHERE reservation_id=? AND status IN ('in_flight','ambiguous')"
      ).bind(input.actual_neurons, now, input.reservation_id),
      db.prepare(`
        INSERT OR IGNORE INTO workers_ai_neuron_quarantines(model_id,reason,reservation_id,quarantined_at)
        SELECT model_id,'actual_neurons_exceeded_reservation',reservation_id,?
        FROM workers_ai_neuron_reservations
        WHERE reservation_id=? AND status='settled' AND actual_neurons>estimated_neurons
      `).bind(now, input.reservation_id),
    ]);
    if (Number(results?.[0]?.meta?.changes || 0) !== 1) {
      return json({ error: "reservation_transition_conflict" }, 409);
    }
    return json({
      reservation_id: row.reservation_id,
      status: "settled",
      actual_neurons: input.actual_neurons,
      over_estimate: input.actual_neurons > Number(row.estimated_neurons),
      model_quarantined: input.actual_neurons > Number(row.estimated_neurons),
      replay: false,
    });
  }
  return json({ error: "unsupported_neuron_action" }, 400);
}

export async function handleWorkersAiNeuronLedger(request, env, url) {
  if (url.pathname !== "/v1/workers-ai-neurons") return null;
  // This is an accounting-only Staging control. Production has no matching
  // environment flag and therefore never exposes the route.
  if (env.WORKERS_AI_LEDGER_CONTROL !== "staging-only") return json({ error: "not_found" }, 404);

  const apiCaller = isApiCaller(request, env);
  const adminCaller = isAdminCaller(request, env);
  if (request.method === "GET") {
    if (!apiCaller && !adminCaller) return json({ error: "unauthorized" }, 401);
    try {
      const result = await snapshot(env.DB, new Date().toISOString());
      return result ? json(result) : json({ error: "neuron_policy_unavailable" }, 503);
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  const input = await readBody(request);
  if (!input || !validText(input.action, 24)) return json({ error: "invalid_body" }, 400);
  const now = new Date().toISOString();

  if (input.action === "enable" || input.action === "disable") {
    if (!adminCaller) return json({ error: "forbidden_operation_scope" }, 403);
    try {
      if (input.action === "enable") {
        const quarantine = await env.DB.prepare(
          "SELECT model_id FROM workers_ai_neuron_quarantines WHERE model_id=?"
        ).bind(MODEL_ID).first();
        if (quarantine) return json({ error: "model_quarantined" }, 409);
      }
      const result = await env.DB.prepare(
        "UPDATE workers_ai_neuron_policy SET enabled=?,updated_at=? " +
        "WHERE policy_key='default' AND daily_cap_neurons=10000 AND allowed_model_id=?"
      ).bind(input.action === "enable" ? 1 : 0, now, MODEL_ID).run();
      if (Number(result.meta?.changes || 0) !== 1) return json({ error: "neuron_policy_unavailable" }, 503);
      return json({ enabled: input.action === "enable", daily_cap_neurons: DAILY_CAP_NEURONS });
    } catch {
      return json({ error: "storage_unavailable" }, 503);
    }
  }

  if (!apiCaller) return json({ error: "forbidden_operation_scope" }, 403);
  try {
    if (input.action === "reserve") return await reserve(env.DB, input, now);
    if (["mark_in_flight", "ambiguous", "settle", "release"].includes(input.action)) {
      return await transition(env.DB, input, input.action, now);
    }
    return json({ error: "unsupported_neuron_action" }, 400);
  } catch (error) {
    return json({ error: error.message === "neuron_reservation_storage_unavailable" ? error.message : "storage_unavailable" }, 503);
  }
}
