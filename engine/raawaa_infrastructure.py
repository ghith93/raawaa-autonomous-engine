"""Raawaa launch infrastructure: order intake contract, offers, and reach plan.

This is the piece the operator asked for first: the infrastructure that carries
an order from a stranger to a delivered parcel, before any content is produced.

Three rules shape it, and each is a reaction to a real failure mode:

1. **No payment rail inside Syria means the form is a request, not a sale.**
   The order is `requested` until a human confirms it. Pretending a form
   submission is revenue is how a founder celebrates a week that lost money.

2. **Phone validation is the cheapest refusal-prevention device that exists.**
   A malformed or obviously fake number becomes a refused parcel two days later,
   and a refused parcel costs money while a rejected form costs nothing.

3. **Capacity is a gate, not a target.** Twenty compounds a day is a hard limit,
   so the twentieth order is the last accepted one. Accepting order
   twenty-one is a promise the founder cannot keep, and a late parcel is the
   most reliable way to manufacture a refusal.

Run:
    python -m engine.raawaa_infrastructure --plan
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STOREFRONT_PATH = ROOT / "config" / "raawaa_storefront.json"
OFFERS_PATH = ROOT / "config" / "raawaa_offers.json"

ORDER_STATES = ("requested", "confirmed", "prepared", "shipped", "delivered", "refused", "cancelled")

# A refused parcel costs the courier half-fee plus a committed unit; a rejected
# form costs nothing. So validation is deliberately strict at the edge.
SYRIAN_PHONE = re.compile(r"^(09\d{8}|\+9639\d{8}|009639\d{8})$")


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def load_storefront() -> dict[str, Any]:
    return load_json(STOREFRONT_PATH)


def load_offers() -> dict[str, Any]:
    return load_json(OFFERS_PATH)


def normalise_phone(raw: str) -> str | None:
    """Return canonical 09xxxxxxxx, or None when the number cannot be real.

    Arabic-Indic digits are converted first: a customer typing ٠٩ on an Arabic
    keyboard is common in Syria and would otherwise be rejected as garbage.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    digits = "".join(
        str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" else ch
        for ch in text
    )
    if digits.startswith("009639"):
        digits = "0" + digits[5:]
    elif digits.startswith("+9639"):
        digits = "0" + digits[4:]
    return digits if SYRIAN_PHONE.fullmatch(digits) else None


def validate_order(payload: dict[str, Any], storefront: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate an order request. Never raises: returns errors a page can show."""
    storefront = storefront or load_storefront()
    schema = storefront["schema"]
    errors: dict[str, str] = {}

    for field in schema["required"]:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors[field] = "required"

    for field, limit in schema["max_lengths"].items():
        value = payload.get(field)
        if isinstance(value, str) and len(value) > limit:
            errors[field] = f"max_{limit}"

    phone = normalise_phone(payload.get("phone", "")) if "phone" not in errors else None
    if "phone" not in errors and phone is None:
        errors["phone"] = "invalid_syrian_number"

    sku = payload.get("sku")
    if isinstance(sku, str) and sku not in schema["sku_values"]:
        errors["sku"] = "unknown_sku"

    delivery = payload.get("delivery_type")
    if delivery is not None and delivery not in schema["delivery_values"]:
        errors["delivery_type"] = "unknown_delivery_type"

    return {
        "ok": not errors,
        "errors": errors,
        "normalised": {"phone": phone} if phone else {},
    }


def idempotency_key(payload: dict[str, Any], *, today: date | None = None) -> str:
    """Same customer, same product, same day is one order, not two."""
    import hashlib

    phone = normalise_phone(payload.get("phone", "")) or "invalid"
    sku = str(payload.get("sku", ""))
    day = (today or datetime.now(timezone.utc).date()).isoformat()
    return hashlib.sha256(f"{phone}|{sku}|{day}".encode()).hexdigest()[:32]


def capacity_gate(orders_today: int, storefront: dict[str, Any] | None = None) -> dict[str, Any]:
    storefront = storefront or load_storefront()
    limit = int(storefront["capacity"]["max_orders_per_day"])
    if orders_today < 0:
        raise ValueError("orders_today must be >= 0")
    open_now = orders_today < limit
    return {
        "limit": limit,
        "orders_today": orders_today,
        "accepting": open_now,
        "message_ar": None if open_now else storefront["capacity"]["when_full_ar"],
    }


def active_offer(offers: dict[str, Any] | None = None) -> dict[str, Any]:
    """The offer to lead with. One at a time, always the highest contribution."""
    offers = offers or load_offers()
    return sorted(offers["offers"], key=lambda o: o["priority"])[0]


def funnel_report(events: dict[str, int]) -> dict[str, Any]:
    """Five numbers, because a dashboard nobody reads is not infrastructure."""
    stages = ["visit", "product_view", "form_start", "form_submit", "confirmed", "delivered"]
    rates = {}
    for previous, current in zip(stages, stages[1:]):
        a, b = events.get(previous, 0), events.get(current, 0)
        rates[f"{previous}->{current}"] = round(b / a, 3) if a else None
    delivered = events.get("delivered", 0)
    refused = events.get("refused", 0)
    attempts = delivered + refused
    return {
        "counts": {stage: events.get(stage, 0) for stage in stages},
        "conversion": rates,
        "refusal_rate": round(refused / attempts, 4) if attempts else None,
        "weakest_step": min(
            ((k, v) for k, v in rates.items() if v is not None),
            key=lambda kv: kv[1],
            default=(None, None),
        )[0],
    }


def launch_plan() -> dict[str, Any]:
    """The infrastructure packet, in the order it has to be built."""
    storefront = load_storefront()
    offers = load_offers()
    return {
        "mode": "raawaa_infrastructure_read_only",
        "note": "Contract + plan. No deployment, no network, no task writes.",
        "phase": "infrastructure_first",
        "order_contract": {
            "states": list(ORDER_STATES),
            "payment": storefront["mode"],
            "phone_rule_ar": storefront["phone_rule_ar"],
            "confirmation_ar": storefront["confirmation_ar"],
            "idempotency_ar": storefront["idempotency_ar"],
        },
        "capacity": storefront["capacity"],
        "lead_offer": active_offer(offers),
        "guarantee": offers["guarantee"],
        "honesty_rule_ar": offers["honesty_rule_ar"],
        "forbidden_scarcity": offers["forbidden_fake_scarcity"],
        "reach_stack": {
            "priority_ar": "الوصول العضوي أولًا (صفر تكلفة)، والمقايضة ثانيًا، والإعلان آخرًا",
            "lanes": [
                {"id": "organic_ig", "when": "الآن", "cost_usd": 0,
                 "note_ar": "٣ ريلز + ٢ كاروسيل + ستوري يومي"},
                {"id": "barter_10", "when": "فور وجود ٢٠ عبوة", "cost_usd": 35,
                 "note_ar": "١٠ عبوات ٥٠ مل = كلفة $35، تُنتج أول دليل اجتماعي"},
                {"id": "salon_pharmacy", "when": "أسبوع ٢", "cost_usd": 0,
                 "note_ar": "توزيع عيّنات على صالونات وصيدليات في حماة"},
                {"id": "meta_ads_abroad", "when": "بعد ١٠ شهادات حقيقية", "cost_usd": 90,
                 "note_ar": "$3/يوم × ٣٠ يومًا من حساب خارج سوريا"}
            ],
            "why_this_order_ar": (
                "الإعلان قبل الدليل يحرق الميزانية: العميلة تصل إلى صفحة بلا شهادات "
                "فتغادر. الدليل أولًا يجعل كل دولار إعلاني يعمل ضعفين."
            )
        },
        "build_order": [
            "1) نموذج الطلب + التحقق من الهاتف + عدّاد الطاقة في D1",
            "2) صفحة منتج واحدة بصور حقيقية",
            "3) عرض الإطلاق + الضمان",
            "4) لوحة الطلبات في /mobile",
            "5) قياس القمع (٥ أرقام أسبوعيًا)",
            "6) المحتوى (المصنع الآلي جاهز)",
            "7) المقايضة",
            "8) الإعلان آخر شيء"
        ]
    }


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if "--validate" in args:
        raw = sys.stdin.read()
        result = validate_order(json.loads(raw))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    print(json.dumps(launch_plan(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
