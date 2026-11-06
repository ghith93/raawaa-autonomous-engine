#!/usr/bin/env python3
"""One entry point for the commerce toolkit.

    python scripts/commerce.py sku --price 18 --cost 4.5 --delivery 2 --refusal 1
    python scripts/commerce.py offers --check "آخر قطعة!"
    python scripts/commerce.py suppliers --config raawaa/suppliers.json
    python scripts/commerce.py doctor
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.commerce.money import Money  # noqa: E402
from engine.commerce.offers import is_forbidden  # noqa: E402
from engine.commerce.sourcing import Supplier, rank  # noqa: E402
from engine.commerce.unit_economics import Sku, report  # noqa: E402
from engine.commerce.validation import Product, validate  # noqa: E402


def cmd_sku(args) -> int:
    sku = Sku(
        sku=args.sku,
        sell_price=Money.of(args.price),
        goods_cost=Money.of(args.cost),
        delivery_cost=Money.of(args.delivery),
        refusal_delivery_cost=Money.of(args.refusal),
        payment_fee_rate=args.fee_rate,
        labour_minutes=args.minutes,
    )
    result = report(sku, labour_hourly=Money.of(args.labour_hourly) if args.labour_hourly else None)
    print(f"SKU {result.sku}")
    print(f"break-even refusal rate: {result.break_even_refusal * 100:.1f}%" if result.break_even_refusal is not None else "break-even: undefined")
    print(f"{'scenario':<18}{'refusal':>9}{'contribution':>15}{'margin':>10}")
    for row in result.scenarios:
        margin = f"{row['margin_pct']:.1f}%" if row["margin_pct"] is not None else "-"
        print(f"{row['scenario']:<18}{row['refusal_rate']*100:>8.0f}%"
              f"{row['contribution']:>15.2f}{margin:>10}")
    return 0


def cmd_offers(args) -> int:
    problems = is_forbidden(args.check)
    if problems:
        print("REJECT")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASS: no banned scarcity pattern found")
    return 0


def cmd_suppliers(args) -> int:
    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    suppliers = [
        Supplier(s["id"], s["name"], Money.of(s["unit_cost"]), s["lead_time_days"],
                 s["moq"], s["defect_rate"], s["on_time_rate"])
        for s in raw["suppliers"]
    ]
    ranked = rank(suppliers, sell_price=Money.of(raw["sell_price"]),
                  cash_available=Money.of(raw["cash_available"]))
    for position, score in enumerate(ranked, 1):
        if score.rejected_reason:
            print(f"{position}. {score.supplier_id} REJECTED: {score.rejected_reason}")
        else:
            print(f"{position}. {score.supplier_id} score={score.total} {score.components}")
    return 0


def cmd_product(args) -> int:
    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    problems = validate(Product(**raw))
    if not problems:
        print("PASS: publishable")
        return 0
    print("BLOCKED")
    for area, items in problems.items():
        for item in items:
            print(f"  {area}: {item}")
    return 1


def cmd_doctor(_args) -> int:
    checks = []
    checks.append(("ImageMagick", bool(__import__("shutil").which("convert"))))
    try:
        import engine.image_providers  # noqa: F401
        checks.append(("image provider chain imports", True))
    except Exception as exc:  # pragma: no cover
        checks.append((f"image provider chain ({exc})", False))
    try:
        import engine.commerce.orders  # noqa: F401
        checks.append(("commerce package imports", True))
    except Exception as exc:  # pragma: no cover
        checks.append((f"commerce package ({exc})", False))
    for name, ok in checks:
        print(f"{'OK  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Raawaa commerce toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    sku = sub.add_parser("sku", help="per-SKU contribution and break-even refusal")
    sku.add_argument("--sku", default="serum-50")
    sku.add_argument("--price", default="18")
    sku.add_argument("--cost", default="4.5")
    sku.add_argument("--delivery", default="2")
    sku.add_argument("--refusal", default="1")
    sku.add_argument("--fee-rate", type=float, default=0.0)
    sku.add_argument("--minutes", type=float, default=0.0)
    sku.add_argument("--labour-hourly", default=None)
    sku.set_defaults(func=cmd_sku)

    offers = sub.add_parser("offers", help="check copy for fake scarcity")
    offers.add_argument("--check", required=True)
    offers.set_defaults(func=cmd_offers)

    suppliers = sub.add_parser("suppliers", help="rank suppliers")
    suppliers.add_argument("--config", required=True)
    suppliers.set_defaults(func=cmd_suppliers)

    product = sub.add_parser("product", help="validate a catalogue entry")
    product.add_argument("--config", required=True)
    product.set_defaults(func=cmd_product)

    doctor = sub.add_parser("doctor", help="environment self-check")
    doctor.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
