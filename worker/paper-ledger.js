const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const ZERO_HASH = "0".repeat(64);
const EVENT_TYPES = new Set([
  "candidate_detected", "snapshot_captured", "route_simulated", "gas_scenario_evaluated",
  "paper_order_simulated", "outcome_observed", "rejected", "expired",
]);
const RAW_FIELDS = ["gross_profit_raw", "gas_used", "gas_price_wei", "gas_cost_profit_raw", "net_profit_raw"];
const RECORD_FIELDS = [
  "candidate_id", "event_id", "event_type", "occurred_at", "chain_id", "block_number",
  "block_hash", "strategy_id", "source_pool", "destination_pool", "token_in", "token_out",
  ...RAW_FIELDS, "payload_json",
];
const REQUIRED_BLOCK_EVENTS = new Set([
  "snapshot_captured", "route_simulated", "gas_scenario_evaluated",
  "paper_order_simulated", "outcome_observed",
]);

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}
function validText(value, max = 128) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}
function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (isPlainObject(value)) {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  }
  return value;
}
function canonical(value) {
  // Match Python json.dumps(..., ensure_ascii=True) so both clients hash identical bytes.
  return JSON.stringify(stable(value)).replace(/[\u007f-\uffff]/g, char =>
    "\\u" + char.charCodeAt(0).toString(16).padStart(4, "0")
  );
}
function address(value, name) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(value) || /^0x0{40}$/i.test(value)) {
    throw new Error("invalid_" + name);
  }
  return value.toLowerCase();
}
function rawInteger(value, name, signed = false) {
  if (value === null || value === undefined) return null;
  const pattern = signed ? /^-?(0|[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)$/;
  if (typeof value !== "string" || value.length > 78 || !pattern.test(value)) {
    throw new Error("invalid_" + name);
  }
  return value;
}
function normalize(input) {
  if (!isPlainObject(input)) throw new Error("invalid_event");
  const candidate_id = input.candidate_id;
  const event_id = input.event_id;
  const event_type = input.event_type;
  if (!validText(candidate_id, 128) || !validText(event_id, 128) || !EVENT_TYPES.has(event_type)) {
    throw new Error("invalid_event_identity");
  }
  if (typeof input.occurred_at !== "string" || !Number.isFinite(Date.parse(input.occurred_at))) {
    throw new Error("invalid_occurred_at");
  }
  const occurred_at = new Date(input.occurred_at).toISOString().replace("Z", "+00:00");
  const payload = input.payload ?? {};
  if (!isPlainObject(payload)) throw new Error("invalid_payload");
  const payload_json = canonical(payload);
  if (new TextEncoder().encode(payload_json).byteLength > 24576) throw new Error("payload_too_large");

  const chain_id = input.chain_id ?? null;
  const block_number = input.block_number ?? null;
  const block_hash = input.block_hash ?? null;
  const supplied = [chain_id !== null, block_number !== null, block_hash !== null];
  if (supplied.some(Boolean) && !supplied.every(Boolean)) throw new Error("incomplete_block_provenance");
  if (chain_id !== null && (!Number.isSafeInteger(chain_id) || chain_id <= 0)) throw new Error("invalid_chain_id");
  if (block_number !== null && (!Number.isSafeInteger(block_number) || block_number < 0)) throw new Error("invalid_block_number");
  if (block_hash !== null && (typeof block_hash !== "string" || !/^0x[0-9a-fA-F]{64}$/.test(block_hash))) throw new Error("invalid_block_hash");
  if (REQUIRED_BLOCK_EVENTS.has(event_type) && chain_id === null) throw new Error("block_provenance_required");

  const strategy_id = input.strategy_id ?? null;
  if (strategy_id !== null && !validText(strategy_id, 80)) throw new Error("invalid_strategy_id");
  const source_pool = address(input.source_pool, "source_pool");
  const destination_pool = address(input.destination_pool, "destination_pool");
  const token_in = address(input.token_in, "token_in");
  const token_out = address(input.token_out, "token_out");
  if (event_type === "route_simulated") {
    if (!strategy_id || !source_pool || !destination_pool || !token_in || !token_out) throw new Error("route_fields_required");
    if (source_pool === destination_pool) throw new Error("route_pools_must_differ");
  }
  const raw = {
    gross_profit_raw: rawInteger(input.gross_profit_raw, "gross_profit_raw", true),
    gas_used: rawInteger(input.gas_used, "gas_used"),
    gas_price_wei: rawInteger(input.gas_price_wei, "gas_price_wei"),
    gas_cost_profit_raw: rawInteger(input.gas_cost_profit_raw, "gas_cost_profit_raw"),
    net_profit_raw: rawInteger(input.net_profit_raw, "net_profit_raw", true),
  };
  if (raw.gas_used !== null && raw.gas_used === "0") throw new Error("invalid_gas_used");
  if (event_type === "route_simulated" && raw.gross_profit_raw === null) throw new Error("gross_profit_required");
  if (event_type === "gas_scenario_evaluated" && ["gas_used", "gas_price_wei", "gas_cost_profit_raw", "net_profit_raw"].some(k => raw[k] === null)) {
    throw new Error("gas_economics_required");
  }
  return {
    candidate_id, event_id, event_type, occurred_at, chain_id, block_number,
    block_hash: block_hash?.toLowerCase() ?? null, strategy_id, source_pool,
    destination_pool, token_in, token_out, ...raw, payload_json,
  };
}
async function digest(record, sequence, previous_hash) {
  const envelope = { previous_hash, record: { sequence, ...record } };
  const bytes = new TextEncoder().encode(canonical(envelope));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2, "0")).join("");
}
function rowMatches(row, record) {
  return RECORD_FIELDS.every(key => (row[key] ?? null) === (record[key] ?? null));
}
function bindRecord(db, record, sequence, previous_hash, event_hash) {
  const values = RECORD_FIELDS.map(key => record[key] ?? null);
  return db.prepare(`INSERT INTO paper_events (
    ${RECORD_FIELDS.join(",")},sequence,previous_hash,event_hash
  ) SELECT ${RECORD_FIELDS.map(() => "?").join(",")},?,?,?
  WHERE EXISTS (
    SELECT 1 FROM paper_candidate_heads
    WHERE candidate_id=? AND sequence=? AND event_hash=?
  )`).bind(...values, sequence, previous_hash, event_hash,
    record.candidate_id, sequence, event_hash);
}
async function append(db, record) {
  const existing = await db.prepare("SELECT * FROM paper_events WHERE event_id=?").bind(record.event_id).first();
  if (existing) {
    if (!rowMatches(existing, record)) return json({ error: "event_id_conflict" }, 409);
    return json({ event_id: record.event_id, sequence: existing.sequence, event_hash: existing.event_hash, replay: true });
  }
  const now = new Date().toISOString();
  await db.prepare(`INSERT OR IGNORE INTO paper_candidate_heads(candidate_id,sequence,event_hash,updated_at)
    VALUES(?,0,?,?)`).bind(record.candidate_id, ZERO_HASH, now).run();
  const head = await db.prepare("SELECT sequence,event_hash FROM paper_candidate_heads WHERE candidate_id=?")
    .bind(record.candidate_id).first();
  if (!head || !Number.isSafeInteger(head.sequence) || head.sequence < 0 || typeof head.event_hash !== "string") {
    return json({ error: "storage_unavailable" }, 503);
  }
  const sequence = head.sequence + 1;
  const previous_hash = head.event_hash;
  const event_hash = await digest(record, sequence, previous_hash);
  try {
    const results = await db.batch([
      db.prepare(`UPDATE paper_candidate_heads SET sequence=?,event_hash=?,updated_at=?
        WHERE candidate_id=? AND sequence=? AND event_hash=?`)
        .bind(sequence, event_hash, now, record.candidate_id, head.sequence, previous_hash),
      bindRecord(db, record, sequence, previous_hash, event_hash),
      db.prepare(`INSERT INTO paper_event_checkpoints(candidate_id,sequence,event_id,event_hash,created_at)
        SELECT ?,?,?,?,? WHERE EXISTS (
          SELECT 1 FROM paper_events WHERE event_id=? AND candidate_id=? AND sequence=? AND event_hash=?
        )`).bind(record.candidate_id, sequence, record.event_id, event_hash, record.occurred_at,
          record.event_id, record.candidate_id, sequence, event_hash),
    ]);
    const changed = results?.[1]?.meta?.changes ?? 0;
    if (changed === 1) return json({ event_id: record.event_id, sequence, event_hash, replay: false }, 201);
  } catch {
    // A concurrent identical append can win the unique event_id race. Verify below.
  }
  const winner = await db.prepare("SELECT * FROM paper_events WHERE event_id=?").bind(record.event_id).first();
  if (winner) {
    if (!rowMatches(winner, record)) return json({ error: "event_id_conflict" }, 409);
    return json({ event_id: record.event_id, sequence: winner.sequence, event_hash: winner.event_hash, replay: true });
  }
  return json({ error: "candidate_head_conflict_retry" }, 409);
}
async function list(db, url) {
  const candidate = url.searchParams.get("candidate_id");
  const limit = Number(url.searchParams.get("limit") || 100);
  if (!validText(candidate, 128) || !Number.isInteger(limit) || limit < 1 || limit > 500) return json({ error: "invalid_query" }, 400);
  try {
    // One read statement gives events, checkpoints, and head sequence from one D1 snapshot.
    const result = await db.prepare(`SELECT h.sequence AS head_sequence,e.*,
      c.sequence AS checkpoint_sequence,c.event_id AS checkpoint_event_id,
      c.event_hash AS checkpoint_event_hash
      FROM paper_candidate_heads h
      LEFT JOIN paper_events e ON e.candidate_id=h.candidate_id
      LEFT JOIN paper_event_checkpoints c ON c.candidate_id=e.candidate_id AND c.sequence=e.sequence
      WHERE h.candidate_id=? ORDER BY e.sequence LIMIT ?`)
      .bind(candidate, limit).all();
    const rows = result.results || [];
    const events = rows.filter(row => row.event_id !== null && row.event_id !== undefined)
      .map(row => Object.fromEntries(RECORD_FIELDS.concat(["sequence","previous_hash","event_hash"]).map(key => [key, row[key]])));
    const checkpoints = rows.filter(row => row.event_id !== null && row.event_id !== undefined)
      .map(row => ({ sequence: row.checkpoint_sequence, event_id: row.checkpoint_event_id, event_hash: row.checkpoint_event_hash }));
    const headSequence = rows.length ? rows[0].head_sequence : 0;
    return json({ events, checkpoints, head_sequence: headSequence });
  } catch { return json({ error: "storage_unavailable" }, 503); }
}
export async function handlePaperLedger(request, env, url) {
  if (url.pathname !== "/v1/paper-ledger") return null;
  if (request.method === "GET") return list(env.DB, url);
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  let input;
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > 32768) return json({ error: "body_too_large" }, 400);
    input = JSON.parse(raw);
  } catch { return json({ error: "invalid_json" }, 400); }
  if (input?.action !== "append") return json({ error: "invalid_action" }, 400);
  let record;
  try { record = normalize(input.event); }
  catch (error) { return json({ error: error.message.startsWith("invalid_") || error.message.endsWith("_required") || error.message === "payload_too_large" ? error.message : "invalid_event" }, 400); }
  return append(env.DB, record);
}
