/**
 * Canonical Multi-Store Catalogs & Deterministic COD Checkout Engine (JS/Edge).
 * Mirrors `config/stores/*.json` and `engine/commerce/cod_checkout.py` 1:1.
 */

export const PAYMENT_METHODS = new Set(["cod", "sham_cash", "syriatel_cash", "mtn_cash"]);
export const PREPAID_WALLETS = new Set(["sham_cash", "syriatel_cash", "mtn_cash"]);

export const PAYMENT_LABELS_AR = {
  cod: "الدفع عند الاستلام",
  sham_cash: "شام كاش",
  syriatel_cash: "سيريتل كاش",
  mtn_cash: "MTN كاش",
};

export const ALLOWED_TRANSITIONS = {
  requested: new Set(["confirmed", "cancelled"]),
  confirmed: new Set(["prepared", "cancelled"]),
  prepared: new Set(["shipped", "cancelled"]),
  shipped: new Set(["delivered", "refused"]),
  delivered: new Set([]),
  refused: new Set([]),
  cancelled: new Set([]),
};

export const STORE_CATALOGS = {
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

export function normaliseSyrianPhone(raw, countryCode = "963") {
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

export async function sha256Hex(message) {
  const bytes = new TextEncoder().encode(message);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export function assessRtoRisk({
  paymentMethod,
  totalCents,
  zoneId,
  primaryZoneId,
  addressDetail,
  whatsappConfirmed = false,
  priorRefusals = 0,
}) {
  let score = 0;
  const reasons = [];
  if (paymentMethod === "cod" && !whatsappConfirmed) {
    score += 35;
    reasons.push("cod_unconfirmed_intent");
  }
  if (zoneId !== primaryZoneId) {
    score += 15;
    reasons.push("inter_governorate_shipment");
  }
  if ((addressDetail || "").trim().length < 8) {
    score += 20;
    reasons.push("sparse_address_landmark");
  }
  if (paymentMethod === "cod" && totalCents >= 4000) {
    score += 15;
    reasons.push("high_aov_first_cod");
  }
  if (priorRefusals > 0) {
    score += Math.min(40, priorRefusals * 25);
    reasons.push("prior_refusal_history");
  }
  if (PREPAID_WALLETS.has(paymentMethod)) {
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
    requiredGate = paymentMethod === "cod" && !whatsappConfirmed ? "whatsapp_confirm" : "none";
  }
  return { level, score, reasons, required_gate: requiredGate };
}

export async function validateAndPriceOrder(payload, priorRefusals = 0, dayOverride = null) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("طلب غير صالح.");
  }
  const storeId = String(payload.store_id || "raawaa").trim().toLowerCase();
  const catalog = STORE_CATALOGS[storeId];
  if (!catalog) {
    throw new Error(`متجر غير معروف: ${storeId}`);
  }

  const name = String(payload.customer_name || "").trim();
  if (name.length < 2 || name.length > 60) {
    throw new Error("يرجى كتابة الاسم الكامل (حرفين إلى 60 حرفًا).");
  }
  const phone = normaliseSyrianPhone(String(payload.customer_phone || ""));

  const zoneId = String(payload.zone_id || catalog.primary_zone_id).trim();
  const zone = catalog.zones.find((z) => z.zone_id === zoneId);
  if (!zone) {
    throw new Error("يرجى اختيار المحافظة من القائمة.");
  }

  const paymentMethod = String(payload.payment_method || "cod").trim();
  if (!catalog.allowed_payments.includes(paymentMethod)) {
    throw new Error("طريقة الدفع المختارة غير مدعومة.");
  }

  const transferRef = String(payload.wallet_transfer_ref || "").trim().slice(0, 64);
  const addressDetail = String(payload.address_detail || "").trim().slice(0, 180);
  const notes = String(payload.notes || "").trim().slice(0, 300);
  const scentId = String(payload.scent || "").trim().slice(0, 40);
  const scentEntry = (catalog.scents || []).find((s) => s.id === scentId || s.label_ar === scentId);
  const scentLabel = scentEntry ? scentEntry.label_ar : scentId;

  const rawLines = Array.isArray(payload.lines) ? payload.lines : [];
  if (rawLines.length === 0 || rawLines.length > 10) {
    throw new Error("يرجى اختيار منتج واحد على الأقل.");
  }

  let subtotalCents = 0;
  let unitCostCents = 0;
  let anyFreeDelivery = false;
  let weightedSpoilage = 0;
  const resolvedLines = [];
  const sigParts = [];

  for (const rawLine of rawLines) {
    const sku = String(rawLine?.sku || "").trim();
    const qty = Number(rawLine?.quantity ?? 1);
    if (!Number.isInteger(qty) || qty < 1 || qty > 20) {
      throw new Error("الكمية يجب أن تكون رقمًا صحيحًا بين 1 و 20.");
    }
    const item = catalog.items.find((it) => it.sku === sku);
    if (!item) {
      throw new Error(`الصنف غير معروف: ${sku}`);
    }
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
    (catalog.prepaid_discount_methods || []).includes(paymentMethod)
  ) {
    discountCents = Math.round((subtotalCents * catalog.prepaid_discount_pct) / 100);
  }

  const deliveryCents = anyFreeDelivery ? 0 : Math.round((zone.fee || 0) * 100);
  const totalCents = Math.max(0, subtotalCents - discountCents) + deliveryCents;

  sigParts.sort();
  const nowIso = new Date().toISOString();
  const day = dayOverride || nowIso.slice(0, 10);
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
    .map((r) => `- ${r.title_ar} × ${r.quantity} ($${r.line_total.toFixed(2)})`)
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
  const whatsappUrl = `https://wa.me/${catalog.whatsapp_e164}?text=${encodeURIComponent(whatsappMessageAr)}`;

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
    whatsapp_message_ar: whatsappMessageAr,
    inspection_policy_ar: catalog.inspection_policy_ar,
    created_at: nowIso,
  };
}
