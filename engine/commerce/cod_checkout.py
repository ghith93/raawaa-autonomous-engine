"""Product-agnostic COD checkout, WhatsApp intent confirmation, and multi-sink failover.

Built from verified 2026 MENA / Syrian e-commerce realities:

1. Store-agnostic (`store_id` + `StoreCatalog`): serves Raawaa, Sulaf, or any
   future store without duplicating repositories, schemas, or pricing logic.
2. Customer-initiated WhatsApp confirmation (`wa.me` deep link):
   - avoids WhatsApp cold-outbound spam bans on new merchant numbers;
   - verifies phone ownership at $0 SMS OTP cost;
   - guarantees graceful degradation even if Syrian mobile ISPs (Syriatel/MTN)
     throttle edge APIs or block Turnstile scripts.
3. Safe local wallet rails (`cod`, `sham_cash`, `syriatel_cash`, `mtn_cash`):
   uses transfer-reference proof rather than handing personal wallet credentials
   to unaudited third-party API wrappers.
4. Hygiene-safe open-box inspection: invites buyers to inspect the outer parcel,
   glass bottle, ingredients/batch label, and intact hygiene seal in front of
   the courier before paying — never inviting unsealing of cosmetics unpaid.
5. Multi-backend `FailoverOrderSink`: tries ordered persistence sinks (e.g.
   isolated Commerce Edge/D1 -> Supabase eu-west-1 -> local durable JSONL) and
   records every attempt reason.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .money import Money
from .orders import OrderError, idempotency_key, normalise_phone

PAYMENT_METHODS = ("cod", "sham_cash", "syriatel_cash", "mtn_cash")
PREPAID_WALLETS = {"sham_cash", "syriatel_cash", "mtn_cash"}

HYGIENE_SAFE_INSPECTION_AR = (
    "افحص العبوة والملصق وتاريخ التحضير وسلامة ختم الأمان أمام المندوب قبل الدفع. "
    "إن لم تطابق ما طلبته، لا تدفع شيئًا."
)


class CheckoutError(ValueError):
    pass


class SinkUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogItem:
    sku: str
    title_ar: str
    unit_price: Money
    free_delivery: bool = False


@dataclass(frozen=True)
class DeliveryZone:
    zone_id: str
    label_ar: str
    fee: Money


@dataclass
class StoreCatalog:
    store_id: str
    brand_ar: str
    whatsapp_e164: str
    currency: str = "USD"
    items: dict[str, CatalogItem] = field(default_factory=dict)
    zones: dict[str, DeliveryZone] = field(default_factory=dict)
    free_shipping_from: Money | None = None
    allowed_payments: tuple[str, ...] = PAYMENT_METHODS
    inspection_policy_ar: str = HYGIENE_SAFE_INSPECTION_AR

    def __post_init__(self) -> None:
        if not self.store_id.strip():
            raise CheckoutError("store_id is required")
        cleaned_wa = "".join(ch for ch in self.whatsapp_e164 if ch.isdigit())
        if len(cleaned_wa) < 10:
            raise CheckoutError("whatsapp_e164 must be an international digits string")
        self.whatsapp_e164 = cleaned_wa


@dataclass(frozen=True)
class CheckoutLineInput:
    sku: str
    quantity: int = 1


@dataclass
class CheckoutRequest:
    customer_name: str
    customer_phone: str
    zone_id: str
    lines: list[CheckoutLineInput]
    address_detail: str = ""
    payment_method: str = "cod"
    wallet_transfer_ref: str = ""
    notes: str = ""
    country_code: str = "963"


@dataclass
class RtoRiskAssessment:
    level: str                 # "low", "medium", "high"
    score: int                 # 0..100
    reasons: list[str]
    required_gate: str         # "none", "whatsapp_confirm", "address_landmark_and_confirm"


@dataclass
class PreparedOrder:
    store_id: str
    order_ref: str
    idempotency_key: str
    customer_name: str
    customer_phone: str
    zone_id: str
    zone_label_ar: str
    address_detail: str
    payment_method: str
    wallet_transfer_ref: str
    lines: list[dict[str, Any]]
    subtotal: Money
    delivery_fee: Money
    total: Money
    whatsapp_url: str
    whatsapp_message_ar: str
    inspection_policy_ar: str
    rto_risk: RtoRiskAssessment
    created_at: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def short_order_ref(store_id: str, idem_key: str) -> str:
    prefix = "".join(ch for ch in store_id.upper() if ch.isalnum())[:3] or "ORD"
    code = int(idem_key[:8], 16) % 1_000_000
    return f"{prefix}-{code:06d}"


def assess_rto_risk(
    *,
    payment_method: str,
    total: Money,
    zone_id: str,
    primary_zone_id: str,
    address_detail: str,
    whatsapp_confirmed: bool = False,
    prior_refusals: int = 0,
) -> RtoRiskAssessment:
    """Score Return-to-Origin (RTO) risk before spending courier fees."""
    score = 0
    reasons: list[str] = []

    if payment_method == "cod" and not whatsapp_confirmed:
        score += 35
        reasons.append("cod_unconfirmed_intent")
    if zone_id != primary_zone_id:
        score += 15
        reasons.append("inter_governorate_shipment")
    if len(address_detail.strip()) < 8:
        score += 20
        reasons.append("sparse_address_landmark")
    if payment_method == "cod" and total.major >= 40:
        score += 15
        reasons.append("high_aov_first_cod")
    if prior_refusals > 0:
        score += min(40, prior_refusals * 25)
        reasons.append("prior_refusal_history")
    if payment_method in PREPAID_WALLETS:
        score = max(0, score - 25)

    score = min(100, score)
    if score >= 55:
        level = "high"
        gate = (
            "address_landmark_and_confirm"
            if "sparse_address_landmark" in reasons
            else "whatsapp_confirm"
        )
    elif score >= 30:
        level = "medium"
        gate = "whatsapp_confirm" if payment_method == "cod" and not whatsapp_confirmed else "none"
    else:
        level = "low"
        gate = "none"

    return RtoRiskAssessment(level=level, score=score, reasons=reasons, required_gate=gate)


def prepare_checkout(
    catalog: StoreCatalog,
    request: CheckoutRequest,
    *,
    primary_zone_id: str | None = None,
    day: str | None = None,
) -> PreparedOrder:
    """Validate inputs, compute authoritative server totals, and build WhatsApp intent."""
    name = (request.customer_name or "").strip()
    if len(name) < 2 or len(name) > 60:
        raise CheckoutError("customer_name must be 2..60 characters")

    try:
        phone = normalise_phone(request.customer_phone, country_code=request.country_code)
    except OrderError as exc:
        raise CheckoutError(f"invalid_phone: {exc}") from exc

    if request.zone_id not in catalog.zones:
        raise CheckoutError(f"unknown zone_id: {request.zone_id!r}")
    zone = catalog.zones[request.zone_id]

    if request.payment_method not in catalog.allowed_payments:
        raise CheckoutError(f"unsupported payment_method: {request.payment_method!r}")

    transfer_ref = (request.wallet_transfer_ref or "").strip()
    if len(transfer_ref) > 64:
        raise CheckoutError("wallet_transfer_ref exceeds 64 characters")

    address = (request.address_detail or "").strip()
    if len(address) > 180:
        raise CheckoutError("address_detail exceeds 180 characters")
    notes = (request.notes or "").strip()
    if len(notes) > 300:
        raise CheckoutError("notes exceeds 300 characters")

    if not request.lines:
        raise CheckoutError("at least one line item is required")

    subtotal = Money.zero(catalog.currency)
    resolved_lines: list[dict[str, Any]] = []
    any_item_free_delivery = False
    sku_signature_parts: list[str] = []

    for line in request.lines:
        if line.sku not in catalog.items:
            raise CheckoutError(f"unknown sku: {line.sku!r}")
        if not isinstance(line.quantity, int) or not 1 <= line.quantity <= 20:
            raise CheckoutError("line quantity must be an integer within 1..20")
        item = catalog.items[line.sku]
        line_total = item.unit_price * line.quantity
        subtotal = subtotal + line_total
        any_item_free_delivery = any_item_free_delivery or item.free_delivery
        sku_signature_parts.append(f"{item.sku}x{line.quantity}")
        resolved_lines.append({
            "sku": item.sku,
            "title_ar": item.title_ar,
            "quantity": line.quantity,
            "unit_price": item.unit_price.major,
            "line_total": line_total.major,
        })

    qualifies_threshold = (
        catalog.free_shipping_from is not None
        and subtotal.minor >= catalog.free_shipping_from.minor
    )
    delivery_fee = (
        Money.zero(catalog.currency)
        if (any_item_free_delivery or qualifies_threshold)
        else zone.fee
    )
    total = subtotal + delivery_fee

    sku_sig = "+".join(sorted(sku_signature_parts))
    stamp = day or _utc_now().strftime("%Y-%m-%d")
    idem = idempotency_key(phone, f"{catalog.store_id}:{sku_sig}", day=stamp)
    order_ref = short_order_ref(catalog.store_id, idem)

    home_zone = primary_zone_id or next(iter(catalog.zones))
    risk = assess_rto_risk(
        payment_method=request.payment_method,
        total=total,
        zone_id=zone.zone_id,
        primary_zone_id=home_zone,
        address_detail=address,
    )

    msg = _build_whatsapp_message(
        catalog=catalog,
        order_ref=order_ref,
        name=name,
        phone=phone,
        zone=zone,
        address=address,
        payment_method=request.payment_method,
        transfer_ref=transfer_ref,
        lines=resolved_lines,
        delivery_fee=delivery_fee,
        total=total,
        notes=notes,
    )
    wa_url = f"https://wa.me/{catalog.whatsapp_e164}?text={urllib.parse.quote(msg)}"

    return PreparedOrder(
        store_id=catalog.store_id,
        order_ref=order_ref,
        idempotency_key=idem,
        customer_name=name,
        customer_phone=phone,
        zone_id=zone.zone_id,
        zone_label_ar=zone.label_ar,
        address_detail=address,
        payment_method=request.payment_method,
        wallet_transfer_ref=transfer_ref,
        lines=resolved_lines,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
        whatsapp_url=wa_url,
        whatsapp_message_ar=msg,
        inspection_policy_ar=catalog.inspection_policy_ar,
        rto_risk=risk,
        created_at=_utc_now().isoformat(),
    )


_PAYMENT_LABELS_AR = {
    "cod": "الدفع عند الاستلام",
    "sham_cash": "شام كاش",
    "syriatel_cash": "سيريتل كاش",
    "mtn_cash": "MTN كاش",
}


def _build_whatsapp_message(
    *,
    catalog: StoreCatalog,
    order_ref: str,
    name: str,
    phone: str,
    zone: DeliveryZone,
    address: str,
    payment_method: str,
    transfer_ref: str,
    lines: list[dict[str, Any]],
    delivery_fee: Money,
    total: Money,
    notes: str,
) -> str:
    items_text = "\n".join(
        f"- {row['title_ar']} × {row['quantity']} (${row['line_total']:.2f})"
        for row in lines
    )
    ship_text = "مجاني" if delivery_fee.minor == 0 else f"${delivery_fee.major:.2f}"
    pay_label = _PAYMENT_LABELS_AR.get(payment_method, payment_method)
    parts = [
        f"تأكيد طلب #{order_ref} — {catalog.brand_ar}",
        items_text,
        f"التوصيل ({zone.label_ar}): {ship_text}",
        f"الإجمالي: ${total.major:.2f}",
        f"طريقة الدفع: {pay_label}" + (f" (مرجع التحويل: {transfer_ref})" if transfer_ref else ""),
        f"الاسم: {name}",
        f"الهاتف: {phone}",
    ]
    if address:
        parts.append(f"العنوان: {address}")
    if notes:
        parts.append(f"ملاحظات: {notes}")
    parts.append("أؤكد صحة الرقم والعنوان لإرسال الطلب.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Multi-Backend Failover Sinks (No Single Point of Failure)
# ---------------------------------------------------------------------------

class OrderSink(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...
    def write(self, order: PreparedOrder) -> str: ...


class LocalJsonlSink:
    """Always-available durable local append-only outbox."""

    name = "local_jsonl"

    def __init__(self, path: str | Path = "data/orders-outbox.jsonl"):
        self.path = Path(path)

    def available(self) -> tuple[bool, str]:
        return True, "local filesystem available"

    def write(self, order: PreparedOrder) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_keys: set[str] = set()
        if self.path.exists():
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    existing_keys.add(json.loads(raw).get("idempotency_key", ""))
                except json.JSONDecodeError:
                    continue
        if order.idempotency_key in existing_keys:
            return f"duplicate:{order.order_ref}"
        record = _serialize_order(order)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return f"saved:{order.order_ref}"


class HttpEndpointSink:
    """Generic HTTPS JSON sink (works with dedicated Cloudflare Worker or Supabase Edge)."""

    def __init__(self, name: str, endpoint_url: str, bearer_token: str = "",
                 extra_headers: dict[str, str] | None = None, timeout: float = 10.0):
        self.name = name
        self.endpoint_url = (endpoint_url or "").strip()
        self.bearer_token = (bearer_token or "").strip()
        self.extra_headers = dict(extra_headers or {})
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        if not self.endpoint_url.startswith("https://"):
            return False, "missing or non-HTTPS endpoint_url"
        return True, "endpoint configured"

    def write(self, order: PreparedOrder) -> str:
        ok, reason = self.available()
        if not ok:
            raise SinkUnavailable(f"{self.name}: {reason}")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": order.idempotency_key,
            **self.extra_headers,
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        req = urllib.request.Request(
            self.endpoint_url,
            data=json.dumps(_serialize_order(order), ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if 200 <= resp.status < 300:
                    return f"{self.name}:{order.order_ref}"
                raise SinkUnavailable(f"{self.name}: HTTP {resp.status}")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SinkUnavailable(f"{self.name}: {type(exc).__name__}: {exc}") from exc


class FailoverOrderSink:
    """Write to the first healthy backend; fall through on network/ISP/server failure."""

    def __init__(self, sinks: list[OrderSink]):
        if not sinks:
            raise ValueError("at least one sink is required")
        self.sinks = sinks

    def submit(self, order: PreparedOrder) -> dict[str, Any]:
        attempts: list[dict[str, str]] = []
        for sink in self.sinks:
            ok, reason = sink.available()
            if not ok:
                attempts.append({"sink": sink.name, "outcome": "unavailable", "reason": reason})
                continue
            try:
                receipt = sink.write(order)
                return {
                    "saved": True,
                    "served_by": sink.name,
                    "receipt": receipt,
                    "order_ref": order.order_ref,
                    "whatsapp_rescue_url": order.whatsapp_url,
                    "attempts": attempts,
                }
            except Exception as exc:
                attempts.append({
                    "sink": sink.name,
                    "outcome": "failed",
                    "reason": f"{type(exc).__name__}: {exc}"[:200],
                })
        return {
            "saved": False,
            "served_by": "whatsapp_client_rescue",
            "receipt": None,
            "order_ref": order.order_ref,
            "whatsapp_rescue_url": order.whatsapp_url,
            "attempts": attempts,
        }


def _serialize_order(order: PreparedOrder) -> dict[str, Any]:
    return {
        "store_id": order.store_id,
        "order_ref": order.order_ref,
        "idempotency_key": order.idempotency_key,
        "customer_name": order.customer_name,
        "customer_phone_sha256": hashlib.sha256(order.customer_phone.encode("utf-8")).hexdigest(),
        "customer_phone": order.customer_phone,
        "zone_id": order.zone_id,
        "address_detail": order.address_detail,
        "payment_method": order.payment_method,
        "wallet_transfer_ref": order.wallet_transfer_ref,
        "lines": order.lines,
        "subtotal": order.subtotal.major,
        "delivery_fee": order.delivery_fee.major,
        "total": order.total.major,
        "currency": order.total.currency,
        "rto_risk": {
            "level": order.rto_risk.level,
            "score": order.rto_risk.score,
            "reasons": order.rto_risk.reasons,
            "required_gate": order.rto_risk.required_gate,
        },
        "created_at": order.created_at,
    }
