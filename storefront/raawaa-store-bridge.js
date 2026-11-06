/**
 * Drop-in Multi-Region Commerce Spine Bridge for `ghith93/Raawaa-store`.
 *
 * Usage inside `Raawaa-store/index.html`:
 *   <script src="./raawaa-store-bridge.js" defer></script>
 *
 * Exposes `window.RaawaaCommerceSpine.submitOrder(payload)` which persists orders
 * to `eu-west-1` Primary -> `us-east-1` Standby -> WhatsApp `wa.me` confirmation.
 */
(function () {
  "use strict";

  const ENDPOINTS = [
    "https://oyoyozdugicmnkfhjovo.supabase.co/functions/v1/commerce-api",
    "https://xuwtuhfqjjwszxqkmivj.supabase.co/functions/v1/commerce-api",
  ];

  async function submitOrder(orderPayload) {
    const body = {
      store_id: orderPayload.store_id || "raawaa",
      customer_name: orderPayload.customer_name,
      customer_phone: orderPayload.customer_phone,
      zone_id: orderPayload.zone_id || "hama",
      address_detail: orderPayload.address_detail || "",
      payment_method: orderPayload.payment_method || "cod",
      wallet_transfer_ref: orderPayload.wallet_transfer_ref || "",
      scent: orderPayload.scent || "",
      notes: orderPayload.notes || "",
      lines: orderPayload.lines || [{ sku: "50ml", quantity: 1 }],
    };

    for (const base of ENDPOINTS) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4500);
      try {
        const res = await fetch(`${base}/v1/orders`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        clearTimeout(timer);
        const json = await res.json();
        if (res.ok && json?.ok) return json;
        if (res.status >= 400 && res.status < 500 && res.status !== 429) {
          throw new Error(json?.error || "validation_error");
        }
      } catch (err) {
        clearTimeout(timer);
        if (err && err.message && !["AbortError", "TypeError", "Failed to fetch"].includes(err.name) && !err.message.includes("fetch")) {
          throw err;
        }
      }
    }

    const fallbackRef = `RAW-${String((Date.now() % 900000) + 100000)}`;
    const text = `تأكيد طلب #${fallbackRef} — رواء\nالاسم: ${body.customer_name}\nالهاتف: ${body.customer_phone}\nالعنوان: ${body.address_detail}`;
    return {
      ok: true,
      served_by: "whatsapp-client-rescue",
      order_ref: fallbackRef,
      whatsapp_url: `https://wa.me/963997310856?text=${encodeURIComponent(text)}`,
    };
  }

  window.RaawaaCommerceSpine = Object.freeze({ submitOrder, endpoints: ENDPOINTS });
})();
