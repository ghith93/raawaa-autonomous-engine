import test from "node:test";
import assert from "node:assert/strict";

import worker from "../commerce-worker/index.js";

function createFakeCommerceD1() {
  const orders = [];
  const events = [];
  const rateLimits = new Map();
  let nextId = 1;

  function makeStmt(sql, bound = []) {
    return {
      sql,
      bound,
      bind(...args) {
        return makeStmt(sql, args);
      },
      async first() {
        const s = sql.trim();
        if (s.includes("FROM commerce_rate_limits")) {
          return rateLimits.get(bound[0]) || null;
        }
        if (s.includes("WHERE idempotency_key =")) {
          return orders.find((o) => o.idempotency_key === bound[0]) || null;
        }
        if (s.includes("COUNT(*) AS cnt FROM commerce_orders")) {
          const cnt = orders.filter(
            (o) => o.customer_phone_hash === bound[0] && o.state === "refused"
          ).length;
          return { cnt };
        }
        if (s.includes("WHERE order_ref =")) {
          return orders.find((o) => o.order_ref === bound[0]) || null;
        }
        return null;
      },
      async all() {
        let filtered = [...orders];
        if (sql.includes("store_id =")) {
          filtered = filtered.filter((o) => o.store_id === bound[0]);
        }
        filtered.sort((a, b) => b.id - a.id);
        return { results: filtered };
      },
      async run() {
        const s = sql.trim();
        if (s.startsWith("INSERT INTO commerce_rate_limits")) {
          rateLimits.set(bound[0], { window_start: bound[1], hit_count: 1 });
        } else if (s.startsWith("UPDATE commerce_rate_limits")) {
          const cur = rateLimits.get(bound[0]);
          if (cur) cur.hit_count += 1;
        } else if (s.startsWith("INSERT INTO commerce_orders")) {
          orders.push({
            id: nextId++,
            store_id: bound[0],
            order_ref: bound[1],
            idempotency_key: bound[2],
            state: "requested",
            customer_name: bound[3],
            customer_phone: bound[4],
            customer_phone_hash: bound[5],
            zone_id: bound[6],
            zone_label_ar: bound[7],
            address_detail: bound[8],
            payment_method: bound[9],
            wallet_transfer_ref: bound[10],
            scent: bound[11],
            notes: bound[12],
            lines_json: bound[13],
            currency: bound[14],
            subtotal_cents: bound[15],
            discount_cents: bound[16],
            delivery_cents: bound[17],
            total_cents: bound[18],
            unit_cost_cents: bound[19],
            courier_cost_cents: bound[20],
            refusal_cost_cents: bound[21],
            spoilage_bps: bound[22],
            rto_level: bound[23],
            rto_score: bound[24],
            rto_gate: bound[25],
            rto_reasons_json: bound[26],
            whatsapp_confirmed: 0,
            created_at: bound[27],
            updated_at: bound[27],
          });
        } else if (s.startsWith("UPDATE commerce_orders SET state")) {
          const row = orders.find((o) => o.order_ref === bound[3]);
          if (row) {
            row.state = bound[0];
            row.whatsapp_confirmed = bound[1];
            row.updated_at = bound[2];
          }
        } else if (s.startsWith("INSERT INTO commerce_order_events")) {
          events.push({ bound });
        }
        return { success: true };
      },
    };
  }

  return {
    orders,
    events,
    prepare(sql) {
      return makeStmt(sql);
    },
    async batch(stmts) {
      const out = [];
      for (const st of stmts) out.push(await st.run());
      return out;
    },
  };
}

const OWNER_TOKEN = "owner-secret-token-for-unit-tests-12345";

function makeEnv(db = createFakeCommerceD1()) {
  return {
    COMMERCE_DB: db,
    COMMERCE_OWNER_TOKEN: OWNER_TOKEN,
    COMMERCE_REGION: "test-edge",
  };
}

test("commerce worker health and catalog routes respond without auth", async () => {
  const env = makeEnv();
  const healthRes = await worker.fetch(new Request("https://commerce.example/v1/health"), env);
  assert.equal(healthRes.status, 200);
  const health = await healthRes.json();
  assert.equal(health.ok, true);
  assert.ok(health.stores.includes("raawaa"));

  const catRes = await worker.fetch(new Request("https://commerce.example/v1/catalog/raawaa"), env);
  assert.equal(catRes.status, 200);
  const cat = await catRes.json();
  assert.equal(cat.catalog.brand_ar, "رواء");
  assert.equal(cat.catalog.items.length, 5);
});

test("guest COD checkout normalizes Arabic digits, deduplicates double-taps, and builds wa.me intent", async () => {
  const env = makeEnv();
  const payload = {
    store_id: "raawaa",
    customer_name: "ريم الحسن",
    customer_phone: "٠٩٩٧ ٣١٠ ٨٥٦",
    zone_id: "hama",
    address_detail: "حي المحطة قرب صيدلية الشفاء",
    payment_method: "cod",
    scent: "vanilla",
    lines: [{ sku: "50ml", quantity: 1 }],
  };

  const r1 = await worker.fetch(
    new Request("https://commerce.example/v1/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
    env
  );
  assert.equal(r1.status, 201);
  const b1 = await r1.json();
  assert.equal(b1.ok, true);
  assert.equal(b1.duplicate, false);
  assert.equal(b1.total, 18);
  assert.equal(b1.delivery_fee, 0);
  assert.ok(b1.order_ref.startsWith("RAA-"));
  assert.ok(b1.whatsapp_url.startsWith("https://wa.me/963997310856?text="));

  // Second tap on the same day must return 200 duplicate without creating a second row
  const r2 = await worker.fetch(
    new Request("https://commerce.example/v1/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
    env
  );
  assert.equal(r2.status, 200);
  const b2 = await r2.json();
  assert.equal(b2.duplicate, true);
  assert.equal(b2.order_ref, b1.order_ref);
  assert.equal(env.COMMERCE_DB.orders.length, 1);
});

test("sham_cash applies 10% genuine prepaid discount and lowers RTO risk", async () => {
  const env = makeEnv();
  const res = await worker.fetch(
    new Request("https://commerce.example/v1/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        store_id: "raawaa",
        customer_name: "نور العلي",
        customer_phone: "+963944112233",
        zone_id: "hama",
        address_detail: "ساحة العاصي بناية البرج",
        payment_method: "sham_cash",
        wallet_transfer_ref: "SHAM-8821",
        lines: [{ sku: "duo-50", quantity: 1 }],
      }),
    }),
    env
  );
  assert.equal(res.status, 201);
  const body = await res.json();
  assert.equal(body.subtotal, 32);
  assert.equal(body.discount, 3.2);
  assert.equal(body.total, 28.8);
  assert.equal(body.rto_risk.level, "low");
});

test("owner endpoints enforce bearer auth, transitions, and accurate COD refusal economics", async () => {
  const env = makeEnv();

  // Unauthenticated owner call fails with 401
  const unauth = await worker.fetch(new Request("https://commerce.example/v1/owner/orders"), env);
  assert.equal(unauth.status, 401);

  // Create two orders: one will be delivered, one refused
  const createOrder = async (phone) => {
    const r = await worker.fetch(
      new Request("https://commerce.example/v1/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          store_id: "raawaa",
          customer_name: "عميلة اختبار",
          customer_phone: phone,
          zone_id: "hama",
          address_detail: "شارع العلمين مقابل الحديقة",
          payment_method: "cod",
          lines: [{ sku: "50ml", quantity: 1 }],
        }),
      }),
      env
    );
    return (await r.json()).order_ref;
  };

  const refDelivered = await createOrder("0991111111");
  const refRefused = await createOrder("0992222222");

  const patch = async (ref, state, reason = "") =>
    worker.fetch(
      new Request(`https://commerce.example/v1/owner/orders/${ref}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${OWNER_TOKEN}`,
        },
        body: JSON.stringify({ state, reason }),
      }),
      env
    );

  // Walk refDelivered: requested -> confirmed -> prepared -> shipped -> delivered
  for (const st of ["confirmed", "prepared", "shipped", "delivered"]) {
    const resp = await patch(refDelivered, st);
    assert.equal(resp.status, 200);
  }

  // Walk refRefused: requested -> confirmed -> prepared -> shipped -> refused
  for (const st of ["confirmed", "prepared", "shipped"]) {
    assert.equal((await patch(refRefused, st)).status, 200);
  }
  // Refusal without reason must fail 400
  assert.equal((await patch(refRefused, "refused", "")).status, 400);
  assert.equal((await patch(refRefused, "refused", "رفضت الاستلام عند الباب")).status, 200);

  // Check summary math:
  // Delivered 50ml: revenue = $18, contribution = $18 - $3.50 unit - $2.00 courier = $12.50
  // Refused 50ml: revenue = $0, loss = $1.00 refusal courier + $3.50 spoiled unit = $4.50
  // Net contribution = $12.50 - $4.50 = $8.00, RTO rate = 50%
  const listRes = await worker.fetch(
    new Request("https://commerce.example/v1/owner/orders?store_id=raawaa", {
      headers: { Authorization: `Bearer ${OWNER_TOKEN}` },
    }),
    env
  );
  assert.equal(listRes.status, 200);
  const summary = (await listRes.json()).summary;
  assert.equal(summary.delivered_revenue_usd, 18);
  assert.equal(summary.delivered_contribution_usd, 12.5);
  assert.equal(summary.refused_loss_usd, 4.5);
  assert.equal(summary.net_contribution_after_refusals_usd, 8.0);
  assert.equal(summary.refusal_rate_pct, 50.0);
});
