/**
 * Isolated Multi-Store Commerce Spine Worker (`raawaa-commerce-api`).
 *
 * Strictly separated from `worker/index.js` (`raawaa-autonomous-engine-control`)
 * so public checkout traffic and customer PII never touch control-plane secrets
 * or task queues.
 */
import {
  ALLOWED_TRANSITIONS,
  STORE_CATALOGS,
  sha256Hex,
  validateAndPriceOrder,
} from "./catalogs.js";

const MAX_BODY_BYTES = 16 * 1024;
const RATE_WINDOW_SECONDS = 600;
const RATE_LIMIT_MAX_ORDERS = 10;

function corsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "*";
  const configured = (env.COMMERCE_ALLOWED_ORIGINS || "*").trim();
  let allowOrigin = "*";
  if (configured !== "*") {
    const allowed = configured.split(",").map((s) => s.trim()).filter(Boolean);
    allowOrigin = allowed.includes(origin) ? origin : allowed[0] || "null";
  }
  return {
    "Access-Control-Allow-Origin": allowOrigin,
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Idempotency-Key",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function jsonResponse(body, status, request, env) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      ...corsHeaders(request, env),
    },
  });
}

async function readBoundedJson(request, maxBytes = MAX_BODY_BYTES) {
  const declared = request.headers.get("Content-Length");
  if (declared && Number(declared) > maxBytes) {
    const err = new Error("حجم الطلب يتجاوز الحد المسموح.");
    err.status = 413;
    throw err;
  }
  if (!request.body) return {};
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        const err = new Error("حجم الطلب يتجاوز الحد المسموح.");
        err.status = 413;
        throw err;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    merged.set(c, offset);
    offset += c.byteLength;
  }
  const text = new TextDecoder().decode(merged);
  try {
    return JSON.parse(text);
  } catch {
    const err = new Error("صيغة JSON غير صالحة.");
    err.status = 400;
    throw err;
  }
}

async function timingSafeBearerCheck(request, secret) {
  if (!secret || typeof secret !== "string" || secret.length < 16) return false;
  const header = request.headers.get("Authorization") || "";
  if (!header.startsWith("Bearer ")) return false;
  const candidate = header.slice(7).trim();
  if (!candidate) return false;
  const enc = new TextEncoder();
  const a = await crypto.subtle.digest("SHA-256", enc.encode(candidate));
  const b = await crypto.subtle.digest("SHA-256", enc.encode(secret));
  const va = new Uint8Array(a);
  const vb = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < va.length; i++) diff |= va[i] ^ vb[i];
  return diff === 0;
}

async function checkRateLimit(db, bucketKey) {
  const nowSec = Math.floor(Date.now() / 1000);
  const row = await db
    .prepare("SELECT window_start, hit_count FROM commerce_rate_limits WHERE bucket_key = ?1")
    .bind(bucketKey)
    .first();
  if (!row || nowSec - Number(row.window_start) >= RATE_WINDOW_SECONDS) {
    await db
      .prepare(
        "INSERT INTO commerce_rate_limits (bucket_key, window_start, hit_count) VALUES (?1, ?2, 1) " +
          "ON CONFLICT(bucket_key) DO UPDATE SET window_start = excluded.window_start, hit_count = 1"
      )
      .bind(bucketKey, nowSec)
      .run();
    return true;
  }
  if (Number(row.hit_count) >= RATE_LIMIT_MAX_ORDERS) {
    return false;
  }
  await db
    .prepare("UPDATE commerce_rate_limits SET hit_count = hit_count + 1 WHERE bucket_key = ?1")
    .bind(bucketKey)
    .run();
  return true;
}

function formatOrderRow(row, catalog) {
  let lines = [];
  let reasons = [];
  try {
    lines = JSON.parse(row.lines_json || "[]");
  } catch {
    lines = [];
  }
  try {
    reasons = JSON.parse(row.rto_reasons_json || "[]");
  } catch {
    reasons = [];
  }
  return {
    store_id: row.store_id,
    order_ref: row.order_ref,
    state: row.state,
    customer_name: row.customer_name,
    customer_phone: row.customer_phone,
    zone_id: row.zone_id,
    zone_label_ar: row.zone_label_ar,
    address_detail: row.address_detail,
    payment_method: row.payment_method,
    wallet_transfer_ref: row.wallet_transfer_ref,
    scent: row.scent,
    notes: row.notes,
    lines,
    currency: row.currency,
    subtotal: Number((Number(row.subtotal_cents) / 100).toFixed(2)),
    discount: Number((Number(row.discount_cents) / 100).toFixed(2)),
    delivery_fee: Number((Number(row.delivery_cents) / 100).toFixed(2)),
    total: Number((Number(row.total_cents) / 100).toFixed(2)),
    whatsapp_confirmed: Boolean(row.whatsapp_confirmed),
    rto_risk: {
      level: row.rto_level,
      score: Number(row.rto_score),
      required_gate: row.rto_gate,
      reasons,
    },
    whatsapp_owner_reply_url: catalog
      ? `https://wa.me/963${String(row.customer_phone).replace(/^0/, "")}?text=${encodeURIComponent(
          `مرحبًا ${row.customer_name}، بخصوص طلبك #${row.order_ref} من ${catalog.brand_ar} ($${(
            Number(row.total_cents) / 100
          ).toFixed(2)}) — نؤكد تجهيز طلبك وإرساله إلى ${row.zone_label_ar}.`
        )}`
      : "",
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

function buildSummary(rows) {
  const byState = {
    requested: 0,
    confirmed: 0,
    prepared: 0,
    shipped: 0,
    delivered: 0,
    refused: 0,
    cancelled: 0,
  };
  let deliveredRevenueCents = 0;
  let deliveredNetCents = 0;
  let refusedLossCents = 0;
  let pipelineCents = 0;

  for (const r of rows) {
    byState[r.state] = (byState[r.state] || 0) + 1;
    if (r.state === "delivered") {
      deliveredRevenueCents += Number(r.total_cents);
      deliveredNetCents +=
        Number(r.total_cents) - Number(r.unit_cost_cents || 0) - Number(r.courier_cost_cents || 0);
    } else if (r.state === "refused") {
      const spoiledCost = Math.round(
        (Number(r.unit_cost_cents || 0) * Number(r.spoilage_bps ?? 10000)) / 10000
      );
      refusedLossCents += Number(r.refusal_cost_cents || 100) + spoiledCost;
    } else if (["requested", "confirmed", "prepared", "shipped"].includes(r.state)) {
      pipelineCents += Number(r.total_cents);
    }
  }
  const resolved = byState.delivered + byState.refused;
  return {
    total_orders: rows.length,
    by_state: byState,
    pipeline_value_usd: Number((pipelineCents / 100).toFixed(2)),
    delivered_revenue_usd: Number((deliveredRevenueCents / 100).toFixed(2)),
    delivered_contribution_usd: Number((deliveredNetCents / 100).toFixed(2)),
    refused_loss_usd: Number((refusedLossCents / 100).toFixed(2)),
    net_contribution_after_refusals_usd: Number(
      ((deliveredNetCents - refusedLossCents) / 100).toFixed(2)
    ),
    refusal_rate_pct: resolved > 0 ? Number(((byState.refused / resolved) * 100).toFixed(1)) : null,
  };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "GET" && (path === "/v1/health" || path === "/health")) {
      return jsonResponse(
        {
          ok: true,
          service: "raawaa-commerce-api",
          region: env.COMMERCE_REGION || "cloudflare-edge",
          stores: Object.keys(STORE_CATALOGS),
        },
        200,
        request,
        env
      );
    }

    const catalogMatch = /^\/v1\/catalog\/([a-z0-9-]+)$/.exec(path);
    if (request.method === "GET" && catalogMatch) {
      const catalog = STORE_CATALOGS[catalogMatch[1]];
      if (!catalog) {
        return jsonResponse({ ok: false, error: "unknown_store" }, 404, request, env);
      }
      return jsonResponse({ ok: true, catalog }, 200, request, env);
    }

    if (!env.COMMERCE_DB) {
      return jsonResponse({ ok: false, error: "storage_unconfigured" }, 503, request, env);
    }

    // Public guest order intake
    if (request.method === "POST" && path === "/v1/orders") {
      let body;
      try {
        body = await readBoundedJson(request);
      } catch (err) {
        return jsonResponse({ ok: false, error: err.message }, err.status || 400, request, env);
      }

      // Soft honeypot trap for automated spam bots
      if (typeof body.website === "string" && body.website.trim().length > 0) {
        return jsonResponse({ ok: false, error: "invalid_submission" }, 400, request, env);
      }

      let prepared;
      try {
        prepared = await validateAndPriceOrder(body);
      } catch (err) {
        return jsonResponse({ ok: false, error: err.message }, 400, request, env);
      }

      // Check idempotent replay BEFORE rate limiting so double-taps always succeed
      const existing = await env.COMMERCE_DB.prepare(
        "SELECT order_ref, state, created_at FROM commerce_orders WHERE idempotency_key = ?1"
      )
        .bind(prepared.idempotency_key)
        .first();
      if (existing) {
        return jsonResponse(
          {
            ok: true,
            duplicate: true,
            served_by: env.COMMERCE_REGION || "cloudflare-edge",
            store_id: prepared.store_id,
            order_ref: existing.order_ref,
            state: existing.state,
            subtotal: Number((prepared.subtotal_cents / 100).toFixed(2)),
            discount: Number((prepared.discount_cents / 100).toFixed(2)),
            delivery_fee: Number((prepared.delivery_cents / 100).toFixed(2)),
            total: Number((prepared.total_cents / 100).toFixed(2)),
            currency: prepared.currency,
            rto_risk: prepared.rto_risk,
            whatsapp_url: prepared.whatsapp_url,
            inspection_policy_ar: prepared.inspection_policy_ar,
            created_at: existing.created_at,
          },
          200,
          request,
          env
        );
      }

      const ipRaw = request.headers.get("CF-Connecting-IP") || "unknown";
      const ipBucket = "ip:" + (await sha256Hex(`${ipRaw}|${prepared.store_id}`)).slice(0, 24);
      const phoneBucket = "ph:" + prepared.customer_phone_hash.slice(0, 24);
      const ipAllowed = await checkRateLimit(env.COMMERCE_DB, ipBucket);
      const phoneAllowed = await checkRateLimit(env.COMMERCE_DB, phoneBucket);
      if (!ipAllowed || !phoneAllowed) {
        return jsonResponse(
          {
            ok: false,
            error: "تم إرسال عدة طلبات متتالية؛ يمكنك تأكيد طلبك مباشرة عبر واتساب.",
            whatsapp_url: prepared.whatsapp_url,
          },
          429,
          request,
          env
        );
      }

      // Re-score with prior refusal history if phone hash previously refused shipments
      const priorRefusalRow = await env.COMMERCE_DB.prepare(
        "SELECT COUNT(*) AS cnt FROM commerce_orders WHERE customer_phone_hash = ?1 AND state = 'refused'"
      )
        .bind(prepared.customer_phone_hash)
        .first();
      const priorRefusals = Number(priorRefusalRow?.cnt || 0);
      if (priorRefusals > 0) {
        prepared = await validateAndPriceOrder(body, priorRefusals);
      }

      await env.COMMERCE_DB.batch([
        env.COMMERCE_DB.prepare(
          `INSERT INTO commerce_orders (
            store_id, order_ref, idempotency_key, state,
            customer_name, customer_phone, customer_phone_hash,
            zone_id, zone_label_ar, address_detail,
            payment_method, wallet_transfer_ref, scent, notes,
            lines_json, currency,
            subtotal_cents, discount_cents, delivery_cents, total_cents,
            unit_cost_cents, courier_cost_cents, refusal_cost_cents, spoilage_bps,
            rto_level, rto_score, rto_gate, rto_reasons_json,
            whatsapp_confirmed, created_at, updated_at
          ) VALUES (
            ?1, ?2, ?3, 'requested',
            ?4, ?5, ?6,
            ?7, ?8, ?9,
            ?10, ?11, ?12, ?13,
            ?14, ?15,
            ?16, ?17, ?18, ?19,
            ?20, ?21, ?22, ?23,
            ?24, ?25, ?26, ?27,
            0, ?28, ?28
          )`
        ).bind(
          prepared.store_id,
          prepared.order_ref,
          prepared.idempotency_key,
          prepared.customer_name,
          prepared.customer_phone,
          prepared.customer_phone_hash,
          prepared.zone_id,
          prepared.zone_label_ar,
          prepared.address_detail,
          prepared.payment_method,
          prepared.wallet_transfer_ref,
          prepared.scent,
          prepared.notes,
          JSON.stringify(prepared.lines),
          prepared.currency,
          prepared.subtotal_cents,
          prepared.discount_cents,
          prepared.delivery_cents,
          prepared.total_cents,
          prepared.unit_cost_cents,
          prepared.courier_cost_cents,
          prepared.refusal_cost_cents,
          prepared.spoilage_bps,
          prepared.rto_risk.level,
          prepared.rto_risk.score,
          prepared.rto_risk.required_gate,
          JSON.stringify(prepared.rto_risk.reasons),
          prepared.created_at
        ),
        env.COMMERCE_DB.prepare(
          `INSERT INTO commerce_order_events (order_ref, from_state, to_state, actor, reason, created_at)
           VALUES (?1, NULL, 'requested', 'customer', 'created', ?2)`
        ).bind(prepared.order_ref, prepared.created_at),
      ]);

      return jsonResponse(
        {
          ok: true,
          duplicate: false,
          served_by: env.COMMERCE_REGION || "cloudflare-edge",
          store_id: prepared.store_id,
          order_ref: prepared.order_ref,
          state: "requested",
          subtotal: Number((prepared.subtotal_cents / 100).toFixed(2)),
          discount: Number((prepared.discount_cents / 100).toFixed(2)),
          delivery_fee: Number((prepared.delivery_cents / 100).toFixed(2)),
          total: Number((prepared.total_cents / 100).toFixed(2)),
          currency: prepared.currency,
          rto_risk: prepared.rto_risk,
          whatsapp_url: prepared.whatsapp_url,
          inspection_policy_ar: prepared.inspection_policy_ar,
          created_at: prepared.created_at,
        },
        201,
        request,
        env
      );
    }

    // Authenticated Owner Routes
    if (path.startsWith("/v1/owner/")) {
      const authorized = await timingSafeBearerCheck(request, env.COMMERCE_OWNER_TOKEN);
      if (!authorized) {
        return jsonResponse({ ok: false, error: "unauthorized" }, 401, request, env);
      }

      if (request.method === "GET" && path === "/v1/owner/orders") {
        const storeFilter = (url.searchParams.get("store_id") || "").trim().toLowerCase();
        const stateFilter = (url.searchParams.get("state") || "").trim().toLowerCase();
        const limit = Math.min(200, Math.max(1, Number(url.searchParams.get("limit") || 100)));

        let sql = "SELECT * FROM commerce_orders WHERE 1=1";
        const binds = [];
        if (storeFilter && storeFilter !== "all") {
          binds.push(storeFilter);
          sql += ` AND store_id = ?${binds.length}`;
        }
        if (stateFilter && stateFilter !== "all") {
          binds.push(stateFilter);
          sql += ` AND state = ?${binds.length}`;
        }
        binds.push(limit);
        sql += ` ORDER BY created_at DESC, id DESC LIMIT ?${binds.length}`;

        const result = await env.COMMERCE_DB.prepare(sql).bind(...binds).all();
        const rows = result.results || [];
        return jsonResponse(
          {
            ok: true,
            served_by: env.COMMERCE_REGION || "cloudflare-edge",
            summary: buildSummary(rows),
            orders: rows.map((r) => formatOrderRow(r, STORE_CATALOGS[r.store_id])),
          },
          200,
          request,
          env
        );
      }

      const patchMatch = /^\/v1\/owner\/orders\/([A-Z0-9-]+)$/.exec(path);
      if (request.method === "PATCH" && patchMatch) {
        const orderRef = patchMatch[1];
        let body;
        try {
          body = await readBoundedJson(request);
        } catch (err) {
          return jsonResponse({ ok: false, error: err.message }, err.status || 400, request, env);
        }
        const nextState = String(body.state || "").trim().toLowerCase();
        const reason = String(body.reason || "").trim().slice(0, 200);
        const current = await env.COMMERCE_DB.prepare(
          "SELECT * FROM commerce_orders WHERE order_ref = ?1"
        )
          .bind(orderRef)
          .first();
        if (!current) {
          return jsonResponse({ ok: false, error: "order_not_found" }, 404, request, env);
        }
        const allowed = ALLOWED_TRANSITIONS[current.state];
        if (!allowed || !allowed.has(nextState)) {
          return jsonResponse(
            {
              ok: false,
              error: `لا يمكن الانتقال من الحالة (${current.state}) إلى (${nextState}).`,
            },
            409,
            request,
            env
          );
        }
        if ((nextState === "refused" || nextState === "cancelled") && !reason) {
          return jsonResponse(
            { ok: false, error: "يجب تسجيل سبب الرفض أو الإلغاء لحساب معدل الرفض بدقة." },
            400,
            request,
            env
          );
        }

        const nowIso = new Date().toISOString();
        const markWaConfirmed = nextState === "confirmed" ? 1 : Number(current.whatsapp_confirmed);
        await env.COMMERCE_DB.batch([
          env.COMMERCE_DB.prepare(
            "UPDATE commerce_orders SET state = ?1, whatsapp_confirmed = ?2, updated_at = ?3 WHERE order_ref = ?4"
          ).bind(nextState, markWaConfirmed, nowIso, orderRef),
          env.COMMERCE_DB.prepare(
            `INSERT INTO commerce_order_events (order_ref, from_state, to_state, actor, reason, created_at)
             VALUES (?1, ?2, ?3, 'owner', ?4, ?5)`
          ).bind(orderRef, current.state, nextState, reason || nextState, nowIso),
        ]);

        const updated = await env.COMMERCE_DB.prepare(
          "SELECT * FROM commerce_orders WHERE order_ref = ?1"
        )
          .bind(orderRef)
          .first();
        return jsonResponse(
          {
            ok: true,
            order: formatOrderRow(updated, STORE_CATALOGS[updated.store_id]),
          },
          200,
          request,
          env
        );
      }
    }

    return jsonResponse({ ok: false, error: "not_found" }, 404, request, env);
  },
};
