"""Raawaa unit economics: real numbers, refusal risk, and the break-even that matters.

Every figure here comes from `config/raawaa_brand.json`. Nothing is assumed.

The point of this module is to answer one question honestly:

    How much can a refused shipment cost before this business is not worth running?

A COD (cash-on-delivery) store that only counts profitable orders is doing
accounting for a business that does not exist. The loss on a refusal is real:
the courier still charges, and the parcel comes back.

Run:  python -m engine.raawaa_economics
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BRAND_PATH = ROOT / "config" / "raawaa_brand.json"

# Scenario refusal rates. 0.20 is deliberately included: it is the range
# documented for cash-on-delivery markets, and assuming 5% is how a founder
# discovers the real number on a spreadsheet three months too late.
REFUSAL_SCENARIOS = (0.0, 0.10, 0.20, 0.30)


def load_brand(path: str | Path | None = None) -> dict[str, Any]:
    with Path(path or BRAND_PATH).open(encoding="utf-8") as stream:
        return json.load(stream)


def _product(brand: dict[str, Any], sku: str) -> dict[str, Any]:
    for product in brand["products"]:
        if product["sku"] == sku:
            return product
    raise KeyError(f"unknown sku: {sku}")


def price_of(product: dict[str, Any], *, free_delivery_pricing: bool = False) -> float:
    """Selling price. In the free-delivery phase the courier cost is baked in."""
    key = "price_v2_usd" if free_delivery_pricing else "price_usd"
    return float(product.get(key, product["price_usd"]))


def delivery_cost(brand: dict[str, Any], *, delivered: bool, home: bool = True) -> float:
    """What the courier charges us.

    A delivered parcel costs the full rate. A refused one is charged at half,
    per the operator's courier agreement.
    """
    rate = brand["delivery"]["home_usd"] if home else brand["delivery"]["governorate_usd"]
    return float(rate) if delivered else float(brand["delivery"]["refused_shipment_charge_usd"])


def contribution(
    brand: dict[str, Any],
    sku: str,
    *,
    delivered: bool = True,
    home: bool = True,
    free_home_delivery: bool = False,
) -> dict[str, Any]:
    """Contribution margin on ONE shipped order, delivered or refused."""
    product = _product(brand, sku)
    price = price_of(product, free_delivery_pricing=free_home_delivery)
    unit_cost = float(product["unit_cost_usd"])
    ship = delivery_cost(brand, delivered=delivered, home=home)
    if delivered and free_home_delivery:
        # The customer pays nothing for delivery, but the courier still bills us.
        ship = delivery_cost(brand, delivered=True, home=home)

    if delivered:
        revenue = price
        cost = unit_cost + ship
    else:
        # No revenue. We still paid for the unit (it comes back, but it is
        # committed stock and the parcel is handled) and we still pay the
        # half-rate courier fee.
        revenue = 0.0
        cost = unit_cost + ship

    return {
        "sku": sku,
        "delivered": delivered,
        "price_usd": price,
        "unit_cost_usd": unit_cost,
        "delivery_usd": ship,
        "revenue_usd": revenue,
        "cost_usd": round(cost, 2),
        "contribution_usd": round(revenue - cost, 2),
    }


def expected_per_order(
    brand: dict[str, Any],
    sku: str,
    refusal_rate: float,
    *,
    home: bool = True,
) -> dict[str, Any]:
    """Expected contribution over one order *attempt* at a given refusal rate."""
    if not 0.0 <= refusal_rate <= 1.0:
        raise ValueError("refusal_rate must be between 0 and 1")
    ok = contribution(brand, sku, delivered=True, home=home)
    bad = contribution(brand, sku, delivered=False, home=home)
    expected = (1 - refusal_rate) * ok["contribution_usd"] + refusal_rate * bad["contribution_usd"]
    return {
        "sku": sku,
        "refusal_rate": refusal_rate,
        "if_delivered_usd": ok["contribution_usd"],
        "if_refused_usd": bad["contribution_usd"],
        "expected_per_attempt_usd": round(expected, 2),
    }


def break_even_refusal_rate(brand: dict[str, Any], sku: str, *, home: bool = True) -> dict[str, Any]:
    """The refusal rate at which expected contribution per attempt hits zero.

    With `p` the refusal rate, delivered contribution `G` and refused
    contribution `-L` (L > 0):

        (1 - p) * G - p * L = 0   ->   p* = G / (G + L)

    Above p* every attempt loses money on average, no matter how good the ad is.
    This is the single most important number for a COD store, and it has
    nothing to do with code.
    """
    ok = contribution(brand, sku, delivered=True, home=home)
    bad = contribution(brand, sku, delivered=False, home=home)
    gain = ok["contribution_usd"]
    loss = -bad["contribution_usd"]
    if gain <= 0:
        return {"sku": sku, "break_even_refusal_rate": None,
                "note": "delivered order is already unprofitable at this price"}
    if loss <= 0:
        return {"sku": sku, "break_even_refusal_rate": 1.0,
                "note": "a refused order costs nothing; no refusal ceiling"}
    rate = gain / (gain + loss)
    return {
        "sku": sku,
        "delivered_gain_usd": gain,
        "refused_loss_usd": round(loss, 2),
        "break_even_refusal_rate": round(rate, 4),
        "break_even_refusal_percent": round(rate * 100, 1),
    }


def bundle_contribution(brand: dict[str, Any], bundle_id: str, *, home: bool = True) -> dict[str, Any]:
    bundle = next(b for b in brand["bundles"] if b["id"] == bundle_id)
    price = float(bundle["price_usd"])
    unit_cost = sum(float(_product(brand, sku)["unit_cost_usd"]) for sku in bundle["skus"])
    ship = 0.0 if bundle.get("free_home_delivery") else delivery_cost(brand, delivered=True, home=home)
    return {
        "bundle": bundle_id,
        "label_ar": bundle["label_ar"],
        "price_usd": price,
        "unit_cost_usd": round(unit_cost, 2),
        "delivery_usd": ship,
        "contribution_usd": round(price - unit_cost - ship, 2),
    }


def inventory_runway(brand: dict[str, Any], units: int, *, mix: dict[str, int] | None = None) -> dict[str, Any]:
    """Revenue and gross contribution if the whole stock sells at list price."""
    mix = mix or {"30ml": int(units * 0.3), "50ml": int(units * 0.5), "100ml": int(units * 0.2)}
    # The mix must still add up to the stock we actually have.
    total_mix = sum(mix.values())
    if total_mix != units:
        raise ValueError(f"mix sums to {total_mix}, expected {units}")
    rows = []
    revenue = cost = 0.0
    for sku, count in mix.items():
        product = _product(brand, sku)
        revenue += float(product["price_usd"]) * count
        cost += float(product["unit_cost_usd"]) * count
        rows.append({"sku": sku, "units": count, "revenue_usd": float(product["price_usd"]) * count})
    return {
        "units": units,
        "mix": rows,
        "gross_revenue_usd": round(revenue, 2),
        "product_cost_usd": round(cost, 2),
        "gross_contribution_before_delivery_usd": round(revenue - cost, 2),
    }


def report(brand: dict[str, Any] | None = None, *, units: int = 10000) -> dict[str, Any]:
    brand = brand or load_brand()
    return {
        "mode": "raawaa_unit_economics_read_only",
        "note": "Deterministic. No network, no model calls, no task writes.",
        "products": [
            {
                "sku": p["sku"],
                "price_usd": p["price_usd"],
                "unit_cost_usd": p["unit_cost_usd"],
                "delivered_home": contribution(brand, p["sku"])["contribution_usd"],
                "refused_home": contribution(brand, p["sku"], delivered=False)["contribution_usd"],
                "break_even": break_even_refusal_rate(brand, p["sku"]),
                "scenarios": [expected_per_order(brand, p["sku"], r) for r in REFUSAL_SCENARIOS],
            }
            for p in brand["products"]
        ],
        "bundles": [bundle_contribution(brand, b["id"]) for b in brand["bundles"]],
        "inventory_10k": inventory_runway(brand, units=units),
    }


def _print(brand: dict[str, Any]) -> None:
    data = report(brand)
    print("== رواء | اقتصاديات الوحدة (سعر البيع مقابل الكلفة والتوصيل) ==\n")
    for p in data["products"]:
        print(f"[{p['sku']}] سعر ${p['price_usd']} | كلفة ${p['unit_cost_usd']}")
        print(f"   توصيل ناجح للبيت : +${p['delivered_home']}")
        print(f"   شحنة مرفوضة      : ${p['refused_home']}")
        be = p["break_even"]
        if be.get("break_even_refusal_percent") is not None:
            print(f"   حد التعادل       : رفض {be['break_even_refusal_percent']}% "
                  f"(فوقه كل طلب خاسر في المتوسط)")
        print()
    print("== متوسط متوقع لكل محاولة طلب ==")
    header = "  ".join(f"{int(r*100):>3}% رفض" for r in REFUSAL_SCENARIOS)
    print(f"{'sku':>8}  {header}")
    for p in data["products"]:
        cells = "  ".join(f"{s['expected_per_attempt_usd']:>8}" for s in p["scenarios"])
        print(f"{p['sku']:>8}  {cells}")
    print("\n== الحزم ==")
    for b in data["bundles"]:
        print(f"{b['label_ar']:<32} ${b['price_usd']:>3} -> ربح ${b['contribution_usd']}")
    inv = data["inventory_10k"]
    print(f"\n== مخزون {inv['units']:,} عبوة ==")
    print(f"إيراد إجمالي بسعر القائمة: ${inv['gross_revenue_usd']:,}")
    print(f"كلفة البضاعة            : ${inv['product_cost_usd']:,}")
    print(f"المساهمة قبل التوصيل    : ${inv['gross_contribution_before_delivery_usd']:,}")


if __name__ == "__main__":
    _print(load_brand())
