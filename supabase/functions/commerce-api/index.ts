// Isolated Multi-Store Commerce Spine Edge Function (`commerce-api`).
// Speaks the exact same REST contract as `commerce-worker/index.js` so
// storefronts and the owner dashboard fail over seamlessly across
// Supabase (`eu-west-1` Primary / `us-east-1` Standby) and Cloudflare Edge/D1.

import postgres from "npm:postgres@3.4.7";

const MAX_BODY_BYTES = 16 * 1024;
const RATE_WINDOW_SECONDS = 600;
const RATE_LIMIT_MAX_ORDERS = 10;

const PREPAID_WALLETS = new Set(["sham_cash", "syriatel_cash", "mtn_cash"]);

const PAYMENT_LABELS_AR: Record<string, string> = {
  cod: "الدفع عند الاستلام",
  sham_cash: "شام كاش",
  syriatel_cash: "سيريتل كاش",
  mtn_cash: "MTN كاش",
};

const ALLOWED_TRANSITIONS: Record<string, Set<string>> = {
  requested: new Set(["confirmed", "cancelled"]),
  confirmed: new Set(["prepared", "cancelled"]),
  prepared: new Set(["shipped", "cancelled"]),
  shipped: new Set(["delivered", "refused"]),
  delivered: new Set([]),
  refused: new Set([]),
  cancelled: new Set([]),
};

interface CatalogItem {
  sku: string;
  title_ar: string;
  short_ar: string;
  subtitle_ar?: string;
  size_ml?: number;
  unit_price: number;
  unit_cost_estimate: number;
  free_delivery: boolean;
  spoilage_rate: number;
  badge_ar?: string;
  featured?: boolean;
}

interface CatalogZone {
  zone_id: string;
  label_ar: string;
  fee: number;
  courier_cost: number;
  refusal_cost: number;
}

interface StoreCatalog {
  schema_version: number;
  store_id: string;
  brand_ar: string;
  brand_latin: string;
  tagline_ar: string;
  subtitle_ar?: string;
  currency: string;
  whatsapp_e164: string;
  whatsapp_display: string;
  primary_zone_id: string;
  prepaid_discount_pct: number;
  prepaid_discount_methods: string[];
  allowed_payments: string[];
  inspection_policy_ar: string;
  preparation_promise_ar: string;
  items: CatalogItem[];
  scents: Array<{ id: string; label_ar: string }>;
  zones: CatalogZone[];
}

const STORE_CATALOGS: Record<string, StoreCatalog> = {
  raawaa: {
    schema_version: 1,
    store_id: "raawaa",
    brand_ar: "رواء",
    brand_latin: "RAWAA",
    tagline_ar: "لمعان يُرى، ونعومة تُلمَس",
    subtitle_ar:
      "سيروم تصفيف وعناية تجميلية بالشعر بالأرغان والبانثينول — بإشراف صيدلاني ومختصين، يُحضَّر عند الطلب خلال ٢٤ ساعة.",
    currency: "USD",
    whatsapp_e164: "963997310856",
    whatsapp_display: "0997 310 856",
    primary_zone_id: "hama",
    prepaid_discount_pct: 10,
    prepaid_discount_methods: ["sham_cash"],
    allowed_payments: ["cod", "sham_cash", "syriatel_cash", "mtn_cash"],
    inspection_policy_ar:
      "افحص العبوة الزجاجية والملصق وتاريخ التحضير وسلامة ختم الأمان أمام المندوب قبل الدفع. إن لم تطابق ما طلبته، لا تدفع شيئًا.",
    preparation_promise_ar: "تُحضَّر كل عبوة عند الطلب وتُسلَّم للشحن خلال ٢٤ ساعة.",
    items: [
      {
        sku: "30ml",
        title_ar: "سيروم رواء ٣٠ مل — الحجم التجريبي",
        short_ar: "٣٠ مل",
        subtitle_ar: "للتعرّف على الملمس واللمعة أو لحقيبة السفر · التوصيل مشمول مجانًا",
        size_ml: 30,
        unit_price: 15.0,
        unit_cost_estimate: 3.0,
        free_delivery: true,
        spoilage_rate: 1.0,
        badge_ar: "",
      },
      {
        sku: "50ml",
        title_ar: "سيروم رواء ٥٠ مل — الحجم اليومي",
        short_ar: "٥٠ مل",
        subtitle_ar: "الحجم الأنسب للاستعمال المنتظم على الأطراف · التوصيل مشمول مجانًا",
        size_ml: 50,
        unit_price: 18.0,
        unit_cost_estimate: 3.5,
        free_delivery: true,
        spoilage_rate: 1.0,
        badge_ar: "الأكثر طلبًا",
        featured: true,
      },
      {
        sku: "100ml",
        title_ar: "سيروم رواء ١٠٠ مل — العبوة الكبيرة",
        short_ar: "١٠٠ مل",
        subtitle_ar: "أفضل قيمة لكل ملليلتر للروتين المستمر · التوصيل مشمول مجانًا",
        size_ml: 100,
        unit_price: 27.0,
        unit_cost_estimate: 4.0,
        free_delivery: true,
        spoilage_rate: 1.0,
        badge_ar: "أفضل قيمة / مل",
      },
      {
        sku: "starter-30-50",
        title_ar: "حزمة التجربة والروتين (٣٠ مل + ٥٠ مل)",
        short_ar: "٣٠ + ٥٠ مل",
        subtitle_ar: "عبوة للمنزل وعبوة للحقيبة في طرد واحد · التوصيل مشمول مجانًا",
        size_ml: 80,
        unit_price: 30.0,
        unit_cost_estimate: 6.5,
        free_delivery: true,
        spoilage_rate: 1.0,
        badge_ar: "توفير شحنة واحدة",
      },
      {
        sku: "duo-50",
        title_ar: "حزمة العبوتين (٢ × ٥٠ مل)",
        short_ar: "٢ × ٥٠ مل",
        subtitle_ar: "عبوتان ٥٠ مل في شحنة واحدة لكِ أو لمشاركتها · التوصيل مشمول مجانًا",
        size_ml: 100,
        unit_price: 32.0,
        unit_cost_estimate: 7.0,
        free_delivery: true,
        spoilage_rate: 1.0,
        badge_ar: "عرض الإطلاق الصادق",
      },
    ],
    scents: [
      { id: "unscented", label_ar: "بلا عطر (التركيبة النقية)" },
      { id: "vanilla", label_ar: "فانيلا دافئة" },
      { id: "rose", label_ar: "ورد دمشقي هادئ" },
      { id: "oud", label_ar: "عود شرقي ناعم" },
      { id: "lavender", label_ar: "لافندر" },
      { id: "rosemary", label_ar: "إكليل الجبل" },
    ],
    zones: [
      { zone_id: "hama", label_ar: "حماة", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "damascus", label_ar: "دمشق وريفها", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "homs", label_ar: "حمص", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "aleppo", label_ar: "حلب", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "latakia", label_ar: "اللاذقية", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "tartous", label_ar: "طرطوس", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "idlib", label_ar: "إدلب", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "other_gov", label_ar: "باقي المحافظات", fee: 0.0, courier_cost: 2.0, refusal_cost: 1.0 },
    ],
  },
  "general-demo": {
    schema_version: 1,
    store_id: "general-demo",
    brand_ar: "قالب متجر عام",
    brand_latin: "GENERAL STORE",
    tagline_ar: "قالب جاهز لإطلاق أي متجر أو منتج جديد خلال دقائق",
    currency: "USD",
    whatsapp_e164: "963997310856",
    whatsapp_display: "0997 310 856",
    primary_zone_id: "hama",
    prepaid_discount_pct: 5,
    prepaid_discount_methods: ["sham_cash"],
    allowed_payments: ["cod", "sham_cash", "syriatel_cash", "mtn_cash"],
    inspection_policy_ar: "افحص سلامة الطرد ومطابقة الصنف والختم الخارجي أمام المندوب قبل الدفع.",
    preparation_promise_ar: "يُجهَّز الطلب ويُسلَّم لشركة الشحن خلال ٢٤ ساعة عمل.",
    items: [
      {
        sku: "sku-standard",
        title_ar: "صنف قياسي (مرتجع قابل لإعادة البيع)",
        short_ar: "قياسي",
        unit_price: 24.0,
        unit_cost_estimate: 11.0,
        free_delivery: false,
        spoilage_rate: 0.05,
      },
    ],
    scents: [],
    zones: [
      { zone_id: "hama", label_ar: "حماة", fee: 2.0, courier_cost: 2.0, refusal_cost: 1.0 },
      { zone_id: "other_gov", label_ar: "باقي المحافظات", fee: 2.0, courier_cost: 2.0, refusal_cost: 1.0 },
    ],
  },
};

const ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";
const EXT_ARABIC_DIGITS = "۰۱۲۳۴۵۶۷۸۹";

function normaliseSyrianPhone(raw: string, countryCode = "963"): string {
  if (typeof raw !== "string" || !raw.trim()) {
    throw new Error("يرجى إدخال رقم الموبايل.");
  }
  let text = "";
  for (const ch of raw.trim()) {
    const idx1 = ARABIC_DIGITS.indexOf(ch);
    const idx2 = EXT_ARABIC_DIGITS.indexOf(ch);
    if (idx1 >= 0) text += String(idx1);
    else if (idx2 >= 0) text += String(idx2);
    else text += ch;
  }
  text = text.replace(/[\s\-().]/g, "");
  if (text.startsWith("+")) text = text.slice(1);
  if (text.startsWith("00")) text = text.slice(2);
  if (text.startsWith(countryCode)) text = text.slice(countryCode.length);
  if (text.startsWith("0")) text = text.slice(1);
  if (!/^\d+$/.test(text)) {
    throw new Error("رقم الهاتف يجب أن يحتوي على أرقام فقط.");
  }
  if (text.length !== 9) {
    throw new Error("رقم الموبايل السوري يجب أن يتكون من 10 خانات تبدأ بـ 09.");
  }
  if (!"345689".includes(text[0])) {
    throw new Error("بادئة رقم الموبايل غير صحيحة (مثال: 09xxxxxxxx).");
  }
  return "0" + text;
}

async function sha256Hex(message: string): Promise<string> {
  const bytes = new TextEncoder().encode(message);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function assessRtoRisk(params: {
  paymentMethod: string;
  totalCents: number;
  zoneId: string;
  primaryZoneId: string;
  addressDetail: string;
  whatsappConfirmed?: boolean;
  priorRefusals?: number;
}) {
  let score = 0;
  const reasons: string[] = [];
  if (params.paymentMethod === "cod" && !params.whatsappConfirmed) {
    score += 35;
    reasons.push("cod_unconfirmed_intent");
  }
  if (params.zoneId !== params.primaryZoneId) {
    score += 15;
    reasons.push("inter_governorate_shipment");
  }
  if ((params.addressDetail || "").trim().length < 8) {
    score += 20;
    reasons.push("sparse_address_landmark");
  }
  if (params.paymentMethod === "cod" && params.totalCents >= 4000) {
    score += 15;
    reasons.push("high_aov_first_cod");
  }
  if ((params.priorRefusals || 0) > 0) {
    score += Math.min(40, (params.priorRefusals || 0) * 25);
    reasons.push("prior_refusal_history");
  }
  if (PREPAID_WALLETS.has(params.paymentMethod)) {
    score = Math.max(0, score - 25);
  }
  score = Math.min(100, score);
  let level = "low";
  let requiredGate = "none";
  if (score >= 55) {
    level = "high";
    requiredGate = reasons.includes("sparse_address_landmark")
      ? "address_landmark_and_confirm"
      : "whatsapp_confirm";
  } else if (score >= 30) {
    level = "medium";
    requiredGate =
      params.paymentMethod === "cod" && !params.whatsappConfirmed ? "whatsapp_confirm" : "none";
  }
  return { level, score, reasons, required_gate: requiredGate };
}

async function validateAndPriceOrder(payload: Record<string, unknown>, priorRefusals = 0) {
  const storeId = String(payload.store_id || "raawaa").trim().toLowerCase();
  const catalog = STORE_CATALOGS[storeId];
  if (!catalog) throw new Error(`متجر غير معروف: ${storeId}`);

  const name = String(payload.customer_name || "").trim();
  if (name.length < 2 || name.length > 60) {
    throw new Error("يرجى كتابة الاسم الكامل (حرفين إلى 60 حرفًا).");
  }
  const phone = normaliseSyrianPhone(String(payload.customer_phone || ""));

  const zoneId = String(payload.zone_id || catalog.primary_zone_id).trim();
  const zone = catalog.zones.find((z) => z.zone_id === zoneId);
  if (!zone) throw new Error("يرجى اختيار المحافظة من القائمة.");

  const paymentMethod = String(payload.payment_method || "cod").trim();
  if (!catalog.allowed_payments.includes(paymentMethod)) {
    throw new Error("طريقة الدفع المختارة غير مدعومة.");
  }

  const transferRef = String(payload.wallet_transfer_ref || "").trim().slice(0, 64);
  const addressDetail = String(payload.address_detail || "").trim().slice(0, 180);
  const notes = String(payload.notes || "").trim().slice(0, 300);
  const scentId = String(payload.scent || "").trim().slice(0, 40);
  const scentEntry = catalog.scents.find((s) => s.id === scentId || s.label_ar === scentId);
  const scentLabel = scentEntry ? scentEntry.label_ar : scentId;

  const rawLines = Array.isArray(payload.lines) ? payload.lines : [];
  if (rawLines.length === 0 || rawLines.length > 10) {
    throw new Error("يرجى اختيار منتج واحد على الأقل.");
  }

  let subtotalCents = 0;
  let unitCostCents = 0;
  let anyFreeDelivery = false;
  let weightedSpoilage = 0;
  const resolvedLines: Array<Record<string, unknown>> = [];
  const sigParts: string[] = [];

  for (const rawLine of rawLines as Array<Record<string, unknown>>) {
    const sku = String(rawLine?.sku || "").trim();
    const qty = Number(rawLine?.quantity ?? 1);
    if (!Number.isInteger(qty) || qty < 1 || qty > 20) {
      throw new Error("الكمية يجب أن تكون رقمًا صحيحًا بين 1 و 20.");
    }
    const item = catalog.items.find((it) => it.sku === sku);
    if (!item) throw new Error(`الصنف غير معروف: ${sku}`);
    const unitCents = Math.round(item.unit_price * 100);
    const lineCents = unitCents * qty;
    subtotalCents += lineCents;
    unitCostCents += Math.round((item.unit_cost_estimate || 0) * 100) * qty;
    weightedSpoilage = Math.max(weightedSpoilage, Math.round((item.spoilage_rate ?? 1.0) * 10000));
    if (item.free_delivery) anyFreeDelivery = true;
    sigParts.push(`${item.sku}x${qty}`);
    resolvedLines.push({
      sku: item.sku,
      title_ar: item.title_ar,
      quantity: qty,
      unit_price: item.unit_price,
      line_total: Number((lineCents / 100).toFixed(2)),
    });
  }

  let discountCents = 0;
  if (
    catalog.prepaid_discount_pct > 0 &&
    catalog.prepaid_discount_methods.includes(paymentMethod)
  ) {
    discountCents = Math.round((subtotalCents * catalog.prepaid_discount_pct) / 100);
  }
  const deliveryCents = anyFreeDelivery ? 0 : Math.round((zone.fee || 0) * 100);
  const totalCents = Math.max(0, subtotalCents - discountCents) + deliveryCents;

  sigParts.sort();
  const nowIso = new Date().toISOString();
  const day = nowIso.slice(0, 10);
  const fullHash = await sha256Hex(`${phone}|${catalog.store_id}:${sigParts.join("+")}|${day}`);
  const idempotencyKey = fullHash.slice(0, 32);
  const phoneHash = await sha256Hex(phone);

  const prefix =
    catalog.store_id
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, "")
      .slice(0, 3) || "ORD";
  const refNum = parseInt(idempotencyKey.slice(0, 8), 16) % 1000000;
  const orderRef = `${prefix}-${String(refNum).padStart(6, "0")}`;

  const rtoRisk = assessRtoRisk({
    paymentMethod,
    totalCents,
    zoneId: zone.zone_id,
    primaryZoneId: catalog.primary_zone_id,
    addressDetail,
    whatsappConfirmed: false,
    priorRefusals,
  });

  const itemsText = resolvedLines
    .map((r) => `- ${r.title_ar} × ${r.quantity} ($${Number(r.line_total).toFixed(2)})`)
    .join("\n");
  const shipText = deliveryCents === 0 ? "مجاني" : `$${(deliveryCents / 100).toFixed(2)}`;
  const payLabel = PAYMENT_LABELS_AR[paymentMethod] || paymentMethod;
  const msgParts = [`تأكيد طلب #${orderRef} — ${catalog.brand_ar}`, itemsText];
  if (scentLabel) msgParts.push(`العطر المختار: ${scentLabel}`);
  if (discountCents > 0) msgParts.push(`خصم الدفع المسبق: -$${(discountCents / 100).toFixed(2)}`);
  msgParts.push(
    `التوصيل (${zone.label_ar}): ${shipText}`,
    `الإجمالي: $${(totalCents / 100).toFixed(2)}`,
    `طريقة الدفع: ${payLabel}` + (transferRef ? ` (مرجع التحويل: ${transferRef})` : ""),
    `الاسم: ${name}`,
    `الهاتف: ${phone}`
  );
  if (addressDetail) msgParts.push(`العنوان: ${addressDetail}`);
  if (notes) msgParts.push(`ملاحظات: ${notes}`);
  msgParts.push("أؤكد صحة الرقم والعنوان لإرسال الطلب.");
  const whatsappMessageAr = msgParts.join("\n");
  const whatsappUrl = `https://wa.me/${catalog.whatsapp_e164}?text=${encodeURIComponent(
    whatsappMessageAr
  )}`;

  return {
    store_id: catalog.store_id,
    order_ref: orderRef,
    idempotency_key: idempotencyKey,
    customer_name: name,
    customer_phone: phone,
    customer_phone_hash: phoneHash,
    zone_id: zone.zone_id,
    zone_label_ar: zone.label_ar,
    address_detail: addressDetail,
    payment_method: paymentMethod,
    wallet_transfer_ref: transferRef,
    scent: scentLabel,
    notes,
    lines: resolvedLines,
    currency: catalog.currency,
    subtotal_cents: subtotalCents,
    discount_cents: discountCents,
    delivery_cents: deliveryCents,
    total_cents: totalCents,
    unit_cost_cents: unitCostCents,
    courier_cost_cents: Math.round((zone.courier_cost ?? 2.0) * 100),
    refusal_cost_cents: Math.round((zone.refusal_cost ?? 1.0) * 100),
    spoilage_bps: weightedSpoilage || 10000,
    rto_risk: rtoRisk,
    whatsapp_url: whatsappUrl,
    inspection_policy_ar: catalog.inspection_policy_ar,
    created_at: nowIso,
  };
}

function corsHeaders(req: Request): Record<string, string> {
  const origin = req.headers.get("origin") || "*";
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, PATCH, OPTIONS",
    "access-control-allow-headers": "content-type, authorization, apikey, idempotency-key",
    "access-control-max-age": "86400",
    vary: "Origin",
  };
}

function jsonResp(body: unknown, status: number, req: Request): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      ...corsHeaders(req),
    },
  });
}

async function readBoundedJson(req: Request): Promise<Record<string, unknown>> {
  const declared = req.headers.get("content-length");
  if (declared && Number(declared) > MAX_BODY_BYTES) {
    throw Object.assign(new Error("حجم الطلب يتجاوز الحد المسموح."), { status: 413 });
  }
  if (!req.body) return {};
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_BODY_BYTES) {
        await reader.cancel();
        throw Object.assign(new Error("حجم الطلب يتجاوز الحد المسموح."), { status: 413 });
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
  const parsed = JSON.parse(new TextDecoder().decode(merged));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw Object.assign(new Error("صيغة JSON غير صالحة."), { status: 400 });
  }
  return parsed as Record<string, unknown>;
}

async function verifyOwnerAccess(req: Request): Promise<boolean> {
  const auth = req.headers.get("authorization") || "";
  if (!auth.startsWith("Bearer ")) return false;
  const candidate = auth.slice(7).trim();
  if (!candidate) return false;

  // 1) Timing-safe COMMERCE_OWNER_TOKEN check
  const ownerToken = Deno.env.get("COMMERCE_OWNER_TOKEN") || "";
  if (ownerToken.length >= 16) {
    const enc = new TextEncoder();
    const a = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(candidate)));
    const b = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(ownerToken)));
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
    if (diff === 0) return true;
  }

  // 2) Supabase Auth session verification (`RAWAA_OWNER_USER_ID` on Ghaioooth-core)
  const supabaseUrl = Deno.env.get("OWNER_AUTH_ORIGIN_URL") || Deno.env.get("SUPABASE_URL") || "";
  const ownerUserId = Deno.env.get("RAWAA_OWNER_USER_ID") || "";
  const pubKeys = Deno.env.get("SUPABASE_PUBLISHABLE_KEYS");
  let anonKey = Deno.env.get("OWNER_AUTH_PUB_KEY") || Deno.env.get("SUPABASE_ANON_KEY") || "";
  if (!Deno.env.get("OWNER_AUTH_PUB_KEY") && pubKeys) {
    try {
      anonKey = JSON.parse(pubKeys).default || anonKey;
    } catch {
      // keep fallback anonKey
    }
  }
  if (supabaseUrl && anonKey && ownerUserId) {
    try {
      const resp = await fetch(`${new URL(supabaseUrl).origin}/auth/v1/user`, {
        method: "GET",
        headers: { apikey: anonKey, authorization: `Bearer ${candidate}` },
        redirect: "error",
        signal: AbortSignal.timeout(5000),
      });
      if (resp.ok) {
        const user = await resp.json();
        if (
          typeof user?.id === "string" &&
          user.id.toLowerCase() === ownerUserId.trim().toLowerCase()
        ) {
          return true;
        }
      }
    } catch {
      return false;
    }
  }
  return false;
}

function formatRow(row: Record<string, unknown>) {
  const storeId = String(row.store_id || "raawaa");
  const catalog = STORE_CATALOGS[storeId];
  const lines = Array.isArray(row.lines_json)
    ? row.lines_json
    : typeof row.lines_json === "string"
      ? JSON.parse(row.lines_json)
      : [];
  const reasons = Array.isArray(row.rto_reasons_json)
    ? row.rto_reasons_json
    : typeof row.rto_reasons_json === "string"
      ? JSON.parse(row.rto_reasons_json)
      : [];
  const totalUsd = Number((Number(row.total_cents) / 100).toFixed(2));
  const phone = String(row.customer_phone || "");
  return {
    store_id: storeId,
    order_ref: row.order_ref,
    state: row.state,
    customer_name: row.customer_name,
    customer_phone: phone,
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
    total: totalUsd,
    whatsapp_confirmed: Boolean(row.whatsapp_confirmed),
    rto_risk: {
      level: row.rto_level,
      score: Number(row.rto_score),
      required_gate: row.rto_gate,
      reasons,
    },
    whatsapp_owner_reply_url: catalog
      ? `https://wa.me/963${phone.replace(/^0/, "")}?text=${encodeURIComponent(
          `مرحبًا ${row.customer_name}، بخصوص طلبك #${row.order_ref} من ${catalog.brand_ar} ($${totalUsd.toFixed(
            2
          )}) — نؤكد تجهيز طلبك وإرساله إلى ${row.zone_label_ar}.`
        )}`
      : "",
    created_at:
      row.created_at instanceof Date ? row.created_at.toISOString() : String(row.created_at),
    updated_at:
      row.updated_at instanceof Date ? row.updated_at.toISOString() : String(row.updated_at),
  };
}

function buildSummary(rows: Array<Record<string, unknown>>) {
  const byState: Record<string, number> = {
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
    const st = String(r.state);
    byState[st] = (byState[st] || 0) + 1;
    if (st === "delivered") {
      deliveredRevenueCents += Number(r.total_cents);
      deliveredNetCents +=
        Number(r.total_cents) - Number(r.unit_cost_cents || 0) - Number(r.courier_cost_cents || 0);
    } else if (st === "refused") {
      const spoiledCost = Math.round(
        (Number(r.unit_cost_cents || 0) * Number(r.spoilage_bps ?? 10000)) / 10000
      );
      refusedLossCents += Number(r.refusal_cost_cents || 100) + spoiledCost;
    } else if (["requested", "confirmed", "prepared", "shipped"].includes(st)) {
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

let sqlPool: ReturnType<typeof postgres> | null = null;
function getSql() {
  if (!sqlPool) {
    const dbUrl = Deno.env.get("SUPABASE_DB_URL");
    if (!dbUrl) throw new Error("missing SUPABASE_DB_URL");
    sqlPool = postgres(dbUrl, { max: 2, prepare: false, idle_timeout: 20, connect_timeout: 5 });
  }
  return sqlPool;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(req) });
  }

  const url = new URL(req.url);
  // Strip optional `/functions/v1/commerce-api` prefix so paths work identically
  const rawPath = url.pathname.replace(/^\/functions\/v1\/commerce-api/, "").replace(/^\/commerce-api/, "");
  const path = rawPath.replace(/\/+$/, "") || "/";
  const regionLabel = Deno.env.get("DENO_REGION") || "supabase-edge";

  if (req.method === "GET" && (path === "/" || path === "/v1/health" || path === "/health")) {
    return jsonResp(
      {
        ok: true,
        service: "commerce-api",
        region: regionLabel,
        stores: Object.keys(STORE_CATALOGS),
      },
      200,
      req
    );
  }

  const catalogMatch = /^\/v1\/catalog\/([a-z0-9-]+)$/.exec(path);
  if (req.method === "GET" && catalogMatch) {
    const catalog = STORE_CATALOGS[catalogMatch[1]];
    if (!catalog) return jsonResp({ ok: false, error: "unknown_store" }, 404, req);
    return jsonResp({ ok: true, catalog }, 200, req);
  }

  let sql: ReturnType<typeof postgres>;
  try {
    sql = getSql();
  } catch {
    return jsonResp({ ok: false, error: "storage_unconfigured" }, 503, req);
  }

  // Public Order Intake
  if (req.method === "POST" && path === "/v1/orders") {
    let body: Record<string, unknown>;
    try {
      body = await readBoundedJson(req);
    } catch (err) {
      const e = err as { message?: string; status?: number };
      return jsonResp({ ok: false, error: e.message || "invalid_json" }, e.status || 400, req);
    }

    if (typeof body.website === "string" && body.website.trim().length > 0) {
      return jsonResp({ ok: false, error: "invalid_submission" }, 400, req);
    }

    let prepared;
    try {
      prepared = await validateAndPriceOrder(body);
    } catch (err) {
      return jsonResp({ ok: false, error: (err as Error).message }, 400, req);
    }

    try {
      const existingRows = await sql`
        SELECT order_ref, state, created_at
        FROM commerce.orders
        WHERE idempotency_key = ${prepared.idempotency_key}
        LIMIT 1
      `;
      if (existingRows.length > 0) {
        const ex = existingRows[0];
        return jsonResp(
          {
            ok: true,
            duplicate: true,
            served_by: regionLabel,
            store_id: prepared.store_id,
            order_ref: ex.order_ref,
            state: ex.state,
            subtotal: Number((prepared.subtotal_cents / 100).toFixed(2)),
            discount: Number((prepared.discount_cents / 100).toFixed(2)),
            delivery_fee: Number((prepared.delivery_cents / 100).toFixed(2)),
            total: Number((prepared.total_cents / 100).toFixed(2)),
            currency: prepared.currency,
            rto_risk: prepared.rto_risk,
            whatsapp_url: prepared.whatsapp_url,
            inspection_policy_ar: prepared.inspection_policy_ar,
            created_at:
              ex.created_at instanceof Date ? ex.created_at.toISOString() : String(ex.created_at),
          },
          200,
          req
        );
      }

      const ipRaw =
        req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
        req.headers.get("cf-connecting-ip") ||
        "unknown";
      const ipBucket = "ip:" + (await sha256Hex(`${ipRaw}|${prepared.store_id}`)).slice(0, 24);
      const phoneBucket = "ph:" + prepared.customer_phone_hash.slice(0, 24);
      const nowSec = Math.floor(Date.now() / 1000);

      for (const bKey of [ipBucket, phoneBucket]) {
        const rlRows = await sql`
          INSERT INTO commerce.rate_limits (bucket_key, window_start, hit_count)
          VALUES (${bKey}, ${nowSec}, 1)
          ON CONFLICT (bucket_key) DO UPDATE SET
            hit_count = CASE
              WHEN (${nowSec} - commerce.rate_limits.window_start) >= ${RATE_WINDOW_SECONDS} THEN 1
              ELSE commerce.rate_limits.hit_count + 1
            END,
            window_start = CASE
              WHEN (${nowSec} - commerce.rate_limits.window_start) >= ${RATE_WINDOW_SECONDS} THEN ${nowSec}
              ELSE commerce.rate_limits.window_start
            END
          RETURNING hit_count
        `;
        if (Number(rlRows[0]?.hit_count || 0) > RATE_LIMIT_MAX_ORDERS) {
          return jsonResp(
            {
              ok: false,
              error: "تم إرسال عدة طلبات متتالية؛ يمكنك تأكيد طلبك مباشرة عبر واتساب.",
              whatsapp_url: prepared.whatsapp_url,
            },
            429,
            req
          );
        }
      }

      const refusalHist = await sql`
        SELECT COUNT(*)::int AS cnt
        FROM commerce.orders
        WHERE customer_phone_hash = ${prepared.customer_phone_hash} AND state = 'refused'
      `;
      const priorRefusals = Number(refusalHist[0]?.cnt || 0);
      if (priorRefusals > 0) {
        prepared = await validateAndPriceOrder(body, priorRefusals);
      }

      await sql.begin(async (tx) => {
        await tx`
          INSERT INTO commerce.orders (
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
            ${prepared.store_id}, ${prepared.order_ref}, ${prepared.idempotency_key}, 'requested',
            ${prepared.customer_name}, ${prepared.customer_phone}, ${prepared.customer_phone_hash},
            ${prepared.zone_id}, ${prepared.zone_label_ar}, ${prepared.address_detail},
            ${prepared.payment_method}, ${prepared.wallet_transfer_ref}, ${prepared.scent}, ${prepared.notes},
            ${JSON.stringify(prepared.lines)}::jsonb, ${prepared.currency},
            ${prepared.subtotal_cents}, ${prepared.discount_cents}, ${prepared.delivery_cents}, ${prepared.total_cents},
            ${prepared.unit_cost_cents}, ${prepared.courier_cost_cents}, ${prepared.refusal_cost_cents}, ${prepared.spoilage_bps},
            ${prepared.rto_risk.level}, ${prepared.rto_risk.score}, ${prepared.rto_risk.required_gate}, ${JSON.stringify(prepared.rto_risk.reasons)}::jsonb,
            false, ${prepared.created_at}::timestamptz, ${prepared.created_at}::timestamptz
          )
        `;
        await tx`
          INSERT INTO commerce.order_events (order_ref, from_state, to_state, actor, reason, created_at)
          VALUES (${prepared.order_ref}, NULL, 'requested', 'customer', 'created', ${prepared.created_at}::timestamptz)
        `;
      });

      return jsonResp(
        {
          ok: true,
          duplicate: false,
          served_by: regionLabel,
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
        req
      );
    } catch {
      return jsonResp({ ok: false, error: "storage_error" }, 503, req);
    }
  }

  // Authenticated Owner Endpoints
  if (path.startsWith("/v1/owner/")) {
    const okAuth = await verifyOwnerAccess(req);
    if (!okAuth) return jsonResp({ ok: false, error: "unauthorized" }, 401, req);

    if (req.method === "GET" && path === "/v1/owner/orders") {
      const storeFilter = (url.searchParams.get("store_id") || "all").trim().toLowerCase();
      const stateFilter = (url.searchParams.get("state") || "all").trim().toLowerCase();
      const limit = Math.min(200, Math.max(1, Number(url.searchParams.get("limit") || 100)));

      try {
        const rows = await sql`
          SELECT *
          FROM commerce.orders
          WHERE (${storeFilter === "all"} OR store_id = ${storeFilter})
            AND (${stateFilter === "all"} OR state = ${stateFilter})
          ORDER BY created_at DESC, id DESC
          LIMIT ${limit}
        `;
        return jsonResp(
          {
            ok: true,
            served_by: regionLabel,
            summary: buildSummary(rows as unknown as Array<Record<string, unknown>>),
            orders: (rows as unknown as Array<Record<string, unknown>>).map(formatRow),
          },
          200,
          req
        );
      } catch {
        return jsonResp({ ok: false, error: "storage_error" }, 503, req);
      }
    }

    const patchMatch = /^\/v1\/owner\/orders\/([A-Z0-9-]+)$/.exec(path);
    if (req.method === "PATCH" && patchMatch) {
      const orderRef = patchMatch[1];
      let body: Record<string, unknown>;
      try {
        body = await readBoundedJson(req);
      } catch (err) {
        const e = err as { message?: string; status?: number };
        return jsonResp({ ok: false, error: e.message || "invalid_json" }, e.status || 400, req);
      }
      const nextState = String(body.state || "").trim().toLowerCase();
      const reason = String(body.reason || "").trim().slice(0, 200);

      try {
        const found = await sql`
          SELECT * FROM commerce.orders WHERE order_ref = ${orderRef} LIMIT 1
        `;
        if (found.length === 0) {
          return jsonResp({ ok: false, error: "order_not_found" }, 404, req);
        }
        const current = found[0] as Record<string, unknown>;
        const curState = String(current.state);
        const allowed = ALLOWED_TRANSITIONS[curState];
        if (!allowed || !allowed.has(nextState)) {
          return jsonResp(
            { ok: false, error: `لا يمكن الانتقال من الحالة (${curState}) إلى (${nextState}).` },
            409,
            req
          );
        }
        if ((nextState === "refused" || nextState === "cancelled") && !reason) {
          return jsonResp(
            { ok: false, error: "يجب تسجيل سبب الرفض أو الإلغاء لحساب معدل الرفض بدقة." },
            400,
            req
          );
        }

        const nowIso = new Date().toISOString();
        const markConfirmed = nextState === "confirmed" ? true : Boolean(current.whatsapp_confirmed);
        const updatedRows = await sql.begin(async (tx) => {
          const upd = await tx`
            UPDATE commerce.orders
            SET state = ${nextState},
                whatsapp_confirmed = ${markConfirmed},
                updated_at = ${nowIso}::timestamptz
            WHERE order_ref = ${orderRef}
            RETURNING *
          `;
          await tx`
            INSERT INTO commerce.order_events (order_ref, from_state, to_state, actor, reason, created_at)
            VALUES (${orderRef}, ${curState}, ${nextState}, 'owner', ${reason || nextState}, ${nowIso}::timestamptz)
          `;
          return upd;
        });

        return jsonResp(
          { ok: true, order: formatRow(updatedRows[0] as unknown as Record<string, unknown>) },
          200,
          req
        );
      } catch {
        return jsonResp({ ok: false, error: "storage_error" }, 503, req);
      }
    }
  }

  return jsonResp({ ok: false, error: "not_found" }, 404, req);
});
