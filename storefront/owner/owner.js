/**
 * Mobile-First Multi-Store Owner Orders Dashboard (`storefront/owner/owner.js`).
 *
 * Supports:
 *  - Local live preview auto-session (`/api/commerce` when running in sandbox preview)
 *  - Supabase Owner Auth (`Ghaioooth-core` `eu-west-1` email + password -> JWT accepted by BOTH `eu-west-1` and `us-east-1`!)
 *  - Direct Bearer Token (`COMMERCE_OWNER_TOKEN`)
 */
(function () {
  "use strict";

  const SUPABASE_AUTH_URL = "https://oyoyozdugicmnkfhjovo.supabase.co/auth/v1/token?grant_type=password";
  const SUPABASE_PUB_KEY = "sb_publishable_WtaxbQDb4FrtlGXt-w377w_RoNSfVJP";
  const STORAGE_KEY = "raawaa_owner_bearer";

  const STATE_LABELS_AR = {
    requested: "جديد — بانتظار التأكيد",
    confirmed: "مؤكد — جاهز للتحضير",
    prepared: "تم التحضير",
    shipped: "في الشحن",
    delivered: "مُسلَّم ومحصَّل",
    refused: "مرفوض / مرتجع",
    cancelled: "ملغى",
  };

  const PAYMENT_LABELS_AR = {
    cod: "الدفع عند الاستلام",
    sham_cash: "شام كاش",
    syriatel_cash: "سيريتل كاش",
    mtn_cash: "MTN كاش",
  };

  function getEndpoints() {
    if (Array.isArray(window.RAAWAA_COMMERCE_ENDPOINTS) && window.RAAWAA_COMMERCE_ENDPOINTS.length) {
      return window.RAAWAA_COMMERCE_ENDPOINTS;
    }
    const list = [];
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

  let bearerToken = sessionStorage.getItem(STORAGE_KEY) || "";
  let currentStateFilter = "all";

  const authPanel = document.getElementById("auth-panel");
  const dashPanel = document.getElementById("dashboard-panel");
  const authError = document.getElementById("auth-error");
  const dashAlert = document.getElementById("dash-alert");
  const logoutBtn = document.getElementById("logout-btn");
  const backendLabel = document.getElementById("active-backend-label");
  const storeSelect = document.getElementById("store-filter");
  const ordersContainer = document.getElementById("orders-container");

  async function apiFetchWithFailover(path, options = {}) {
    const endpoints = getEndpoints();
    let lastErr = null;
    for (const ep of endpoints) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5000);
      try {
        const resp = await fetch(`${ep.base}${path}`, {
          ...options,
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            Authorization: `Bearer ${bearerToken}`,
            ...(options.headers || {}),
          },
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (resp.status === 404 && ep.name === "local-edge-proxy") continue;
        const data = await resp.json();
        if (resp.status === 401) {
          const authErr = new Error("انتهت الجلسة أو بيانات الدخول غير صحيحة.");
          authErr.status = 401;
          throw authErr;
        }
        if (!resp.ok) {
          throw new Error(data?.error || `HTTP ${resp.status}`);
        }
        backendLabel.textContent = `متصل: ${data.served_by || ep.name}`;
        return data;
      } catch (err) {
        clearTimeout(timer);
        if (err && err.status === 401) throw err;
        lastErr = err;
      }
    }
    throw lastErr || new Error("تعذر الاتصال بخوادم التجارة.");
  }

  function renderSummary(summary) {
    document.getElementById("kpi-requested").textContent = String(summary.by_state?.requested || 0);
    document.getElementById("kpi-pipeline").textContent = `$${Number(summary.pipeline_value_usd || 0).toFixed(2)}`;
    document.getElementById("kpi-revenue").textContent = `$${Number(summary.delivered_revenue_usd || 0).toFixed(2)}`;
    const net = Number(summary.net_contribution_after_refusals_usd || 0);
    const netEl = document.getElementById("kpi-net");
    netEl.textContent = `${net >= 0 ? "+" : ""}$${net.toFixed(2)}`;
    netEl.style.color = net >= 0 ? "var(--ok)" : "var(--danger)";
    const rtoEl = document.getElementById("kpi-rto");
    rtoEl.textContent =
      summary.refusal_rate_pct === null || summary.refusal_rate_pct === undefined
        ? `لا مرتجعات ($0)`
        : `${summary.refusal_rate_pct}% (-$${Number(summary.refused_loss_usd || 0).toFixed(2)})`;
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderOrders(orders) {
    if (!orders.length) {
      ordersContainer.innerHTML = `<div class="panel" style="text-align:center;color:var(--ink-muted);">لا توجد طلبات مطابقة للفلتر الحالي. جرّب إرسال طلب تجريبي من <a href="../">صفحة المتجر</a> ليظهر هنا فورًا.</div>`;
      return;
    }

    ordersContainer.innerHTML = orders
      .map((o) => {
        const itemsHtml = (o.lines || [])
          .map((l) => `${escapeHtml(l.title_ar)} × ${Number(l.quantity)}`)
          .join(" + ");
        const riskLevel = o.rto_risk?.level || "medium";
        const riskText =
          riskLevel === "high"
            ? "خطر رفض مرتفع — أكّد عبر واتساب والعنوان قبل الشحن"
            : riskLevel === "medium"
              ? "خطر متوسط — أكّد عبر واتساب"
              : "خطر منخفض";

        const buttons = [];
        if (o.state === "requested") {
          buttons.push(`<button class="btn btn-primary btn-sm" data-ref="${escapeHtml(o.order_ref)}" data-next="confirmed">تأكيد الطلب ✓</button>`);
          buttons.push(`<button class="btn btn-outline btn-sm" data-ref="${escapeHtml(o.order_ref)}" data-next="cancelled" data-need-reason="1">إلغاء</button>`);
        } else if (o.state === "confirmed") {
          buttons.push(`<button class="btn btn-primary btn-sm" data-ref="${escapeHtml(o.order_ref)}" data-next="prepared">تم التحضير 🧪</button>`);
          buttons.push(`<button class="btn btn-outline btn-sm" data-ref="${escapeHtml(o.order_ref)}" data-next="cancelled" data-need-reason="1">إلغاء</button>`);
        } else if (o.state === "prepared") {
          buttons.push(`<button class="btn btn-primary btn-sm" data-ref="${escapeHtml(o.order_ref)}" data-next="shipped">تم التسليم للشحن 🚚</button>`);
        } else if (o.state === "shipped") {
          buttons.push(`<button class="btn btn-primary btn-sm" data-ref="${escapeHtml(o.order_ref)}" data-next="delivered">تم التسليم والتحصيل 💵</button>`);
          buttons.push(`<button class="btn btn-outline btn-sm" data-ref="${escapeHtml(o.order_ref)}" data-next="refused" data-need-reason="1" style="color:var(--danger);border-color:#f1bcbc;">رفض الاستلام ↩</button>`);
        }
        if (o.whatsapp_owner_reply_url) {
          buttons.push(`<a class="btn btn-outline btn-sm" href="${escapeHtml(o.whatsapp_owner_reply_url)}" target="_blank" rel="noopener noreferrer">مراسلة واتساب 💬</a>`);
        }

        return `
          <article class="order-card">
            <div class="order-top">
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <span class="ref-pill">#${escapeHtml(o.order_ref)}</span>
                <span class="badge badge-${escapeHtml(o.state)}">${escapeHtml(STATE_LABELS_AR[o.state] || o.state)}</span>
                <span class="badge risk-${escapeHtml(riskLevel)}">${escapeHtml(riskText)}</span>
              </div>
              <strong dir="ltr" style="font-size:1.15rem;color:var(--olive);">$${Number(o.total).toFixed(2)}</strong>
            </div>

            <div style="font-weight:700;margin-bottom:4px;">${escapeHtml(itemsHtml)} ${o.scent ? `· (${escapeHtml(o.scent)})` : ""}</div>
            <div style="font-size:0.92rem;color:var(--ink-muted);">
              <strong>${escapeHtml(o.customer_name)}</strong> · <span dir="ltr">${escapeHtml(o.customer_phone)}</span> · ${escapeHtml(o.zone_label_ar)} — ${escapeHtml(o.address_detail || "بدون تفصيل")}<br>
              الدفع: <strong>${escapeHtml(PAYMENT_LABELS_AR[o.payment_method] || o.payment_method)}</strong>
              ${o.wallet_transfer_ref ? ` · مرجع التحويل: <code dir="ltr">${escapeHtml(o.wallet_transfer_ref)}</code>` : ""}
              ${o.notes ? `<br>ملاحظة: ${escapeHtml(o.notes)}` : ""}
            </div>

            <div class="action-row">${buttons.join("")}</div>
          </article>
        `;
      })
      .join("");

    ordersContainer.querySelectorAll("button[data-ref]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const ref = btn.getAttribute("data-ref");
        const next = btn.getAttribute("data-next");
        let reason = "";
        if (btn.getAttribute("data-need-reason") === "1") {
          reason = window.prompt("اكتب سبب الرفض أو الإلغاء باختصار (لتتبع أسباب المرتجعات):", "عدم رد الزبون على المندوب") || "";
          if (!reason.trim()) return;
        }
        btn.disabled = true;
        try {
          await apiFetchWithFailover(`/v1/owner/orders/${encodeURIComponent(ref)}`, {
            method: "PATCH",
            body: JSON.stringify({ state: next, reason }),
          });
          await loadDashboard();
        } catch (err) {
          dashAlert.textContent = err.message;
          dashAlert.hidden = false;
          btn.disabled = false;
        }
      });
    });
  }

  async function loadDashboard() {
    dashAlert.hidden = true;
    try {
      const storeVal = storeSelect.value;
      const data = await apiFetchWithFailover(
        `/v1/owner/orders?store_id=${encodeURIComponent(storeVal)}&state=${encodeURIComponent(currentStateFilter)}&limit=100`
      );
      authPanel.hidden = true;
      dashPanel.hidden = false;
      logoutBtn.hidden = false;
      renderSummary(data.summary || {});
      renderOrders(data.orders || []);
    } catch (err) {
      if (err.status === 401) {
        sessionStorage.removeItem(STORAGE_KEY);
        bearerToken = "";
        authPanel.hidden = false;
        dashPanel.hidden = true;
        logoutBtn.hidden = true;
        authError.textContent = err.message;
        authError.hidden = false;
      } else {
        dashAlert.textContent = err.message;
        dashAlert.hidden = false;
      }
    }
  }

  document.getElementById("email-login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    authError.hidden = true;
    const email = document.getElementById("owner_email").value.trim();
    const password = document.getElementById("owner_password").value;
    if (!email || !password) {
      authError.textContent = "يرجى إدخال البريد الإلكتروني وكلمة المرور.";
      authError.hidden = false;
      return;
    }
    try {
      const resp = await fetch(SUPABASE_AUTH_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          apikey: SUPABASE_PUB_KEY,
        },
        body: JSON.stringify({ email, password }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.access_token) {
        throw new Error(data.error_description || data.msg || "بيانات الدخول غير صحيحة.");
      }
      bearerToken = data.access_token;
      sessionStorage.setItem(STORAGE_KEY, bearerToken);
      await loadDashboard();
    } catch (err) {
      authError.textContent = err.message;
      authError.hidden = false;
    }
  });

  document.getElementById("bearer-login-btn").addEventListener("click", async () => {
    const val = document.getElementById("bearer_token_input").value.trim();
    if (!val) return;
    bearerToken = val;
    sessionStorage.setItem(STORAGE_KEY, bearerToken);
    await loadDashboard();
  });

  logoutBtn.addEventListener("click", () => {
    sessionStorage.removeItem(STORAGE_KEY);
    bearerToken = "";
    dashPanel.hidden = true;
    authPanel.hidden = false;
    logoutBtn.hidden = true;
  });

  storeSelect.addEventListener("change", loadDashboard);
  document.getElementById("refresh-btn").addEventListener("click", loadDashboard);
  const notifyBtn = document.getElementById("notify-test-btn");
  const notifyFeedback = document.getElementById("notify-feedback");
  if (notifyBtn) {
    notifyBtn.addEventListener("click", async () => {
      notifyBtn.disabled = true;
      if (notifyFeedback) notifyFeedback.hidden = true;
      try {
        const res = await apiFetchWithFailover("/v1/owner/notify-test", {
          method: "POST",
          body: JSON.stringify({}),
        });
        if (notifyFeedback) {
          notifyFeedback.className = "alert-box";
          notifyFeedback.style.borderColor = "var(--ok)";
          notifyFeedback.textContent = `✅ تم إرسال إشعار تجريبي فوري إلى البوت المربوط (${res.channel || "direct"} — ${res.served_by || "live"}).`;
          notifyFeedback.hidden = false;
        }
      } catch (err) {
        if (notifyFeedback) {
          notifyFeedback.className = "alert-box alert-error";
          notifyFeedback.textContent = `تعذر إرسال الإشعار التجريبي: ${err.message}`;
          notifyFeedback.hidden = false;
        }
      } finally {
        notifyBtn.disabled = false;
      }
    });
  }
  document.querySelectorAll("#state-chips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll("#state-chips .chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      currentStateFilter = chip.getAttribute("data-state") || "all";
      loadDashboard();
    });
  });

  // Auto-probe local preview mode so sandbox browser preview opens immediately in 1 click
  (async function bootstrap() {
    if (!bearerToken && location.protocol.startsWith("http")) {
      try {
        const probe = await fetch("/api/commerce/v1/preview-session", { method: "GET" });
        if (probe.ok) {
          const info = await probe.json();
          if (info && info.preview_token) {
            bearerToken = info.preview_token;
          }
        }
      } catch {
        // Not running in local preview mode; normal login applies
      }
    }
    if (bearerToken) {
      await loadDashboard();
    }
  })();
})();
