/**
 * Multi-Store Resilient Checkout Engine (`storefront/checkout.js`).
 *
 * Failover chain (Zero Single Point of Failure):
 *   1. Relative `/api/commerce` proxy when hosted on a preview/unified edge origin
 *   2. Live Supabase Primary (`eu-west-1` Frankfurt: `oyoyozdugicmnkfhjovo`)
 *   3. Live Supabase Standby (`us-east-1` Virginia: `xuwtuhfqjjwszxqkmivj`)
 *   4. Deterministic Client-Side Rescue + 1-tap customer-initiated `wa.me` confirmation
 */
(function () {
  "use strict";

  const DEFAULT_CATALOG = {
    store_id: "raawaa",
    brand_ar: "رواء",
    whatsapp_e164: "963997310856",
    currency: "USD",
    primary_zone_id: "hama",
    prepaid_discount_pct: 10,
    prepaid_discount_methods: ["sham_cash"],
    inspection_policy_ar:
      "افحص العبوة الزجاجية والملصق وتاريخ التحضير وسلامة ختم الأمان أمام المندوب قبل الدفع. إن لم تطابق ما طلبته، لا تدفع شيئًا.",
    preparation_promise_ar: "تُحضَّر كل عبوة عند الطلب وتُسلَّم للشحن خلال ٢٤ ساعة.",
    items: [
      {
        sku: "30ml",
        title_ar: "سيروم رواء ٣٠ مل — الحجم التجريبي",
        subtitle_ar: "للتعرّف على الملمس واللمعة أو لحقيبة السفر · التوصيل مشمول مجانًا",
        unit_price: 15.0,
        free_delivery: true,
        badge_ar: "",
      },
      {
        sku: "50ml",
        title_ar: "سيروم رواء ٥٠ مل — الحجم اليومي",
        subtitle_ar: "الحجم الأنسب للاستعمال المنتظم على الأطراف · التوصيل مشمول مجانًا",
        unit_price: 18.0,
        free_delivery: true,
        badge_ar: "الأكثر طلبًا",
        featured: true,
      },
      {
        sku: "100ml",
        title_ar: "سيروم رواء ١٠٠ مل — العبوة الكبيرة",
        subtitle_ar: "أفضل قيمة لكل ملليلتر للروتين المستمر · التوصيل مشمول مجانًا",
        unit_price: 27.0,
        free_delivery: true,
        badge_ar: "أفضل قيمة / مل",
      },
      {
        sku: "starter-30-50",
        title_ar: "حزمة التجربة والروتين (٣٠ مل + ٥٠ مل)",
        subtitle_ar: "عبوة للمنزل وعبوة للحقيبة في طرد واحد · التوصيل مشمول مجانًا",
        unit_price: 30.0,
        free_delivery: true,
        badge_ar: "توفير شحنة واحدة",
      },
      {
        sku: "duo-50",
        title_ar: "حزمة العبوتين (٢ × ٥٠ مل)",
        subtitle_ar: "عبوتان ٥٠ مل في شحنة واحدة لكِ أو لمشاركتها · التوصيل مشمول مجانًا",
        unit_price: 32.0,
        free_delivery: true,
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
      { zone_id: "hama", label_ar: "حماة", fee: 0.0 },
      { zone_id: "damascus", label_ar: "دمشق وريفها", fee: 0.0 },
      { zone_id: "homs", label_ar: "حمص", fee: 0.0 },
      { zone_id: "aleppo", label_ar: "حلب", fee: 0.0 },
      { zone_id: "latakia", label_ar: "اللاذقية", fee: 0.0 },
      { zone_id: "tartous", label_ar: "طرطوس", fee: 0.0 },
      { zone_id: "idlib", label_ar: "إدلب", fee: 0.0 },
      { zone_id: "other_gov", label_ar: "باقي المحافظات", fee: 0.0 },
    ],
  };

  function getEndpoints() {
    if (Array.isArray(window.RAAWAA_COMMERCE_ENDPOINTS) && window.RAAWAA_COMMERCE_ENDPOINTS.length) {
      return window.RAAWAA_COMMERCE_ENDPOINTS;
    }
    const list = [];
    // When served by the unified preview or custom reverse-proxy, relative `/api/commerce` goes first
    if (location.protocol.startsWith("http")) {
      list.push({ name: "local-edge-proxy", base: "/api/commerce" });
    }
    list.push(
      {
        name: "supabase-eu-west-1",
        base: "https://oyoyozdugicmnkfhjovo.supabase.co/functions/v1/commerce-api",
      },
      {
        name: "supabase-us-east-1-fallback",
        base: "https://xuwtuhfqjjwszxqkmivj.supabase.co/functions/v1/commerce-api",
      }
    );
    return list;
  }

  const PAYMENT_LABELS_AR = {
    cod: "الدفع عند الاستلام",
    sham_cash: "شام كاش",
    syriatel_cash: "سيريتل كاش",
    mtn_cash: "MTN كاش",
  };

  const ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";
  const EXT_ARABIC_DIGITS = "۰۱۲۳۴۵۶۷۸۹";

  function normaliseSyrianPhone(raw) {
    if (typeof raw !== "string" || !raw.trim()) {
      throw new Error("يرجى إدخال رقم الموبايل.");
    }
    let text = "";
    for (const ch of raw.trim()) {
      const i1 = ARABIC_DIGITS.indexOf(ch);
      const i2 = EXT_ARABIC_DIGITS.indexOf(ch);
      if (i1 >= 0) text += String(i1);
      else if (i2 >= 0) text += String(i2);
      else text += ch;
    }
    text = text.replace(/[\s\-().]/g, "");
    if (text.startsWith("+")) text = text.slice(1);
    if (text.startsWith("00")) text = text.slice(2);
    if (text.startsWith("963")) text = text.slice(3);
    if (text.startsWith("0")) text = text.slice(1);
    if (!/^\d+$/.test(text)) {
      throw new Error("رقم الموبايل يجب أن يحتوي على أرقام فقط.");
    }
    if (text.length !== 9 || !"345689".includes(text[0])) {
      throw new Error("يرجى إدخال رقم موبايل سوري صحيح من 10 خانات يبدأ بـ 09.");
    }
    return "0" + text;
  }

  function buildClientRescueOrder(catalog, payload) {
    const item = catalog.items.find((i) => i.sku === payload.lines[0].sku) || catalog.items[0];
    const qty = Number(payload.lines[0].quantity || 1);
    const zone = catalog.zones.find((z) => z.zone_id === payload.zone_id) || catalog.zones[0];
    const subtotalCents = Math.round(item.unit_price * 100) * qty;
    const discountCents =
      catalog.prepaid_discount_pct > 0 &&
      (catalog.prepaid_discount_methods || []).includes(payload.payment_method)
        ? Math.round((subtotalCents * catalog.prepaid_discount_pct) / 100)
        : 0;
    const deliveryCents = item.free_delivery ? 0 : Math.round((zone.fee || 0) * 100);
    const totalCents = Math.max(0, subtotalCents - discountCents) + deliveryCents;
    const code = String((Date.now() % 900000) + 100000);
    const orderRef = `RAW-${code}`;
    const scentEntry = (catalog.scents || []).find((s) => s.id === payload.scent);
    const scentLabel = scentEntry ? scentEntry.label_ar : payload.scent;

    const msgParts = [
      `تأكيد طلب #${orderRef} — ${catalog.brand_ar}`,
      `- ${item.title_ar} × ${qty} ($${((subtotalCents / 100)).toFixed(2)})`,
    ];
    if (scentLabel) msgParts.push(`العطر المختار: ${scentLabel}`);
    if (discountCents > 0) msgParts.push(`خصم الدفع المسبق: -$${(discountCents / 100).toFixed(2)}`);
    msgParts.push(
      `التوصيل (${zone.label_ar}): ${deliveryCents === 0 ? "مجاني" : "$" + (deliveryCents / 100).toFixed(2)}`,
      `الإجمالي: $${(totalCents / 100).toFixed(2)}`,
      `طريقة الدفع: ${PAYMENT_LABELS_AR[payload.payment_method] || payload.payment_method}` +
        (payload.wallet_transfer_ref ? ` (مرجع التحويل: ${payload.wallet_transfer_ref})` : ""),
      `الاسم: ${payload.customer_name}`,
      `الهاتف: ${payload.customer_phone}`,
      `العنوان: ${payload.address_detail}`
    );
    if (payload.notes) msgParts.push(`ملاحظات: ${payload.notes}`);
    msgParts.push("أؤكد صحة الرقم والعنوان لإرسال الطلب.");
    const msg = msgParts.join("\n");
    return {
      ok: true,
      served_by: "whatsapp-client-rescue",
      order_ref: orderRef,
      total: Number((totalCents / 100).toFixed(2)),
      whatsapp_url: `https://wa.me/${catalog.whatsapp_e164}?text=${encodeURIComponent(msg)}`,
    };
  }

  let catalog = DEFAULT_CATALOG;

  const skuListEl = document.getElementById("sku-list");
  const qtyEl = document.getElementById("quantity");
  const scentEl = document.getElementById("scent");
  const zoneEl = document.getElementById("zone_id");
  const formEl = document.getElementById("checkout-form");
  const alertEl = document.getElementById("form-alert");
  const walletWrapEl = document.getElementById("wallet-ref-wrap");
  const submitBtn = document.getElementById("submit-btn");
  const confBox = document.getElementById("confirmation-box");
  const confRef = document.getElementById("conf-ref");
  const confWaBtn = document.getElementById("conf-wa-btn");
  const confRouteMeta = document.getElementById("conf-route-meta");

  function getSelectedSku() {
    const checked = document.querySelector('input[name="sku"]:checked');
    return checked ? checked.value : catalog.items[1]?.sku || catalog.items[0]?.sku;
  }

  function getSelectedPayment() {
    const checked = document.querySelector('input[name="payment_method"]:checked');
    return checked ? checked.value : "cod";
  }

  function renderCatalog() {
    skuListEl.innerHTML = "";
    catalog.items.forEach((item, idx) => {
      const label = document.createElement("label");
      label.className = "sku-option";
      const isChecked = item.featured || (!catalog.items.some((x) => x.featured) && idx === 0);
      label.innerHTML = `
        <input type="radio" name="sku" value="${item.sku}" ${isChecked ? "checked" : ""}>
        <div>
          <div class="sku-title-row">
            <span class="sku-title">${item.title_ar}</span>
            ${item.badge_ar ? `<span class="sku-badge">${item.badge_ar}</span>` : ""}
          </div>
          <span class="sku-sub">${item.subtitle_ar || ""}</span>
        </div>
        <div class="sku-price">$${Number(item.unit_price).toFixed(0)}</div>
      `;
      label.querySelector("input").addEventListener("change", updateSummary);
      skuListEl.appendChild(label);
    });

    scentEl.innerHTML = "";
    (catalog.scents || []).forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.label_ar;
      scentEl.appendChild(opt);
    });

    zoneEl.innerHTML = "";
    catalog.zones.forEach((z) => {
      const opt = document.createElement("option");
      opt.value = z.zone_id;
      opt.textContent = z.label_ar;
      zoneEl.appendChild(opt);
    });

    updateSummary();
  }

  function updateSummary() {
    const sku = getSelectedSku();
    const item = catalog.items.find((i) => i.sku === sku) || catalog.items[0];
    const qty = Number(qtyEl.value || 1);
    const payment = getSelectedPayment();
    const zone = catalog.zones.find((z) => z.zone_id === zoneEl.value) || catalog.zones[0];

    const subtotal = item.unit_price * qty;
    const hasDiscount =
      catalog.prepaid_discount_pct > 0 &&
      (catalog.prepaid_discount_methods || []).includes(payment);
    const discount = hasDiscount ? Number(((subtotal * catalog.prepaid_discount_pct) / 100).toFixed(2)) : 0;
    const delivery = item.free_delivery ? 0 : Number(zone?.fee || 0);
    const total = Math.max(0, subtotal - discount) + delivery;

    document.getElementById("sum-item").textContent = `${item.title_ar.split("—")[0].trim()} × ${qty}`;
    document.getElementById("sum-subtotal").textContent = `$${subtotal.toFixed(2)}`;
    const discRow = document.getElementById("sum-discount-row");
    if (discount > 0) {
      discRow.hidden = false;
      document.getElementById("sum-discount").textContent = `-$${discount.toFixed(2)}`;
    } else {
      discRow.hidden = true;
    }
    document.getElementById("sum-delivery").textContent =
      delivery === 0 ? "مجاني (مشمول)" : `$${delivery.toFixed(2)}`;
    document.getElementById("sum-total").textContent = `$${total.toFixed(2)}`;

    walletWrapEl.hidden = payment === "cod";
  }

  qtyEl.addEventListener("change", updateSummary);
  zoneEl.addEventListener("change", updateSummary);
  document.querySelectorAll('input[name="payment_method"]').forEach((r) => {
    r.addEventListener("change", updateSummary);
  });

  async function submitWithFailover(payload) {
    const endpoints = getEndpoints();
    const failures = [];
    for (const ep of endpoints) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4500);
      try {
        const resp = await fetch(`${ep.base}/v1/orders`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (resp.status === 404 && ep.name === "local-edge-proxy") {
          failures.push(`${ep.name}:404`);
          continue;
        }
        const data = await resp.json();
        if (resp.ok && data && data.ok) {
          return { ...data, endpoint_used: ep.name, failures };
        }
        if (resp.status >= 400 && resp.status < 500 && data?.error) {
          const clientErr = new Error(data.error);
          clientErr.isValidation = resp.status !== 429;
          clientErr.whatsapp_url = data.whatsapp_url;
          if (clientErr.isValidation) throw clientErr;
        }
        failures.push(`${ep.name}:HTTP_${resp.status}`);
      } catch (err) {
        clearTimeout(timer);
        if (err && err.isValidation) throw err;
        failures.push(`${ep.name}:${err?.name || "net_err"}`);
      }
    }
    // Invincible fallback #3: Customer-initiated WhatsApp rescue
    const rescue = buildClientRescueOrder(catalog, payload);
    try {
      const queue = JSON.parse(localStorage.getItem("raawaa_pending_orders") || "[]");
      queue.push({ payload, rescue, at: new Date().toISOString() });
      localStorage.setItem("raawaa_pending_orders", JSON.stringify(queue.slice(-20)));
    } catch {
      // ignore storage quota errors in private mode
    }
    return { ...rescue, endpoint_used: "whatsapp-client-rescue", failures };
  }

  formEl.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    alertEl.hidden = true;

    const name = document.getElementById("customer_name").value.trim();
    const rawPhone = document.getElementById("customer_phone").value.trim();
    const address = document.getElementById("address_detail").value.trim();

    if (name.length < 2) {
      alertEl.textContent = "يرجى إدخال الاسم الكامل.";
      alertEl.hidden = false;
      return;
    }
    let normalizedPhone;
    try {
      normalizedPhone = normaliseSyrianPhone(rawPhone);
    } catch (err) {
      alertEl.textContent = err.message;
      alertEl.hidden = false;
      return;
    }
    if (address.length < 5) {
      alertEl.textContent = "يرجى كتابة العنوان وأقرب نقطة دالة لضمان وصول المندوب بدقة.";
      alertEl.hidden = false;
      return;
    }

    const payload = {
      store_id: catalog.store_id,
      customer_name: name,
      customer_phone: normalizedPhone,
      zone_id: zoneEl.value,
      address_detail: address,
      payment_method: getSelectedPayment(),
      wallet_transfer_ref: document.getElementById("wallet_transfer_ref").value.trim(),
      scent: scentEl.value,
      notes: document.getElementById("notes").value.trim(),
      website: document.getElementById("website").value,
      lines: [{ sku: getSelectedSku(), quantity: Number(qtyEl.value || 1) }],
    };

    submitBtn.disabled = true;
    submitBtn.textContent = "جارٍ حفظ الطلب وتجهيز رابط التأكيد...";

    try {
      const result = await submitWithFailover(payload);
      confRef.textContent = `#${result.order_ref}`;
      confWaBtn.href = result.whatsapp_url;
      confRouteMeta.textContent =
        result.endpoint_used === "whatsapp-client-rescue"
          ? "تم تجهيز الطلب مباشرة عبر مسار الإنقاذ الفوري — اضغطي زر واتساب لإتمام الإرسال."
          : `تم الحفظ الآمن في قاعدة البيانات (${result.served_by || result.endpoint_used}) · الإجمالي: $${Number(result.total).toFixed(2)}`;
      confBox.hidden = false;
      confBox.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (err) {
      alertEl.textContent = err.message || "تعذر إرسال الطلب، يرجى المحاولة مرة أخرى.";
      alertEl.hidden = false;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "تسجيل الطلب والانتقال لتأكيد واتساب";
    }
  });

  renderCatalog();
})();
