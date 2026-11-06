"""Per-SKU unit economics with the costs a dashboard forgets.

Contribution margin is not profit. At this scale the difference is refunds,
refusals, payment fees, and the founder's own time. This module keeps them
separate so a SKU that looks healthy at 60% gross can be seen losing money once
a 20% refusal rate is applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .money import Money, safe_div


class EconomicsError(ValueError):
    pass


@dataclass
class Sku:
    sku: str
    sell_price: Money            # what the buyer pays, delivery included
    goods_cost: Money            # materials, packaging, label
    delivery_cost: Money         # what the courier charges you
    refusal_delivery_cost: Money  # what the courier charges on a return
    payment_fee_rate: float = 0.0  # charge-on-delivery fee, if any
    labour_minutes: float = 0.0
    return_spoilage_rate: float = 1.0  # 1.0 = total write-off; <1.0 when sealed stock is reusable
    repack_cost: Money | None = None

    def validate(self) -> list[str]:
        problems = []
        for name in ("sell_price", "goods_cost", "delivery_cost", "refusal_delivery_cost"):
            if getattr(self, name).minor < 0:
                problems.append(f"{name} cannot be negative")
        if self.repack_cost is not None and self.repack_cost.minor < 0:
            problems.append("repack_cost cannot be negative")
        if self.sell_price.minor <= 0:
            problems.append("sell price must be positive")
        if not 0 <= self.payment_fee_rate < 1:
            problems.append("payment fee rate must be within 0..1")
        if not 0 <= self.return_spoilage_rate <= 1:
            problems.append("return spoilage rate must be within 0..1")
        if self.labour_minutes < 0:
            problems.append("labour minutes cannot be negative")
        if (self.goods_cost + self.delivery_cost).minor >= self.sell_price.minor:
            problems.append("goods plus delivery is at or above the sell price: loss on every sale")
        return problems


@dataclass
class Scenario:
    label: str
    refusal_rate: float
    return_rate: float = 0.0
    review_rate: float = 0.0


def _refusal_loss(sku: Sku) -> Money:
    zero = Money.zero(sku.sell_price.currency)
    repack = sku.repack_cost or zero
    spoiled = sku.goods_cost * sku.return_spoilage_rate
    return spoiled + sku.refusal_delivery_cost + repack


def contribution(sku: Sku, refusal_rate: float = 0.0, return_rate: float = 0.0) -> Money:
    """Expected contribution per shipped order, after refusals and returns."""
    problems = sku.validate()
    if problems:
        raise EconomicsError("; ".join(problems))
    if not 0 <= refusal_rate < 1 or not 0 <= return_rate < 1:
        raise EconomicsError("rates must be within 0..1")

    fee = sku.sell_price * sku.payment_fee_rate
    good_case = sku.sell_price - fee - sku.goods_cost - sku.delivery_cost
    bad_case = Money.zero(sku.sell_price.currency) - _refusal_loss(sku)
    expected = good_case * (1 - refusal_rate - return_rate) + bad_case * (refusal_rate + return_rate)
    return Money(round(expected.minor), sku.sell_price.currency)


def break_even_refusal(sku: Sku, return_rate: float = 0.0) -> float | None:
    """Refusal rate at which expected contribution reaches zero."""
    if sku.validate():
        raise EconomicsError("; ".join(sku.validate()))
    # good*(1-r) + bad*r = 0  ->  r = good / (good - bad)
    fee = sku.sell_price * sku.payment_fee_rate
    good = (sku.sell_price - fee - sku.goods_cost - sku.delivery_cost).minor
    bad = -_refusal_loss(sku).minor
    if good <= 0:
        return 0.0
    denominator = good - bad
    ratio = safe_div(good, denominator)
    if ratio is None:
        return None
    return max(0.0, ratio - return_rate)


def minimum_price_for_margin(sku: Sku, target_margin: float, refusal_rate: float = 0.0) -> Money | None:
    """Price needed so that expected contribution is `target_margin` of revenue."""
    if not 0 < target_margin < 1:
        raise EconomicsError("target margin must be within 0..1")
    expected_revenue = sku.sell_price * (1 - refusal_rate)
    target = expected_revenue * target_margin
    shortfall = target - contribution(sku, refusal_rate=refusal_rate)
    return sku.sell_price + shortfall / max(1e-9, (1 - refusal_rate))


@dataclass
class SkuReport:
    sku: str
    scenarios: list[dict] = field(default_factory=list)
    break_even_refusal: float | None = None
    labour_cost_per_hour: Money | None = None


def report(sku: Sku, scenarios: list[Scenario] | None = None,
           labour_hourly: Money | None = None) -> SkuReport:
    scenarios = scenarios or [
        Scenario("ideal", 0.0),
        Scenario("good", 0.10),
        Scenario("realistic", 0.20),
        Scenario("bad", 0.35),
        Scenario("break-even probe", 0.0),
    ]
    rows = []
    for scenario in scenarios:
        amount = contribution(sku, refusal_rate=scenario.refusal_rate, return_rate=scenario.return_rate)
        revenue = sku.sell_price * (1 - scenario.refusal_rate)
        rows.append({
            "scenario": scenario.label,
            "refusal_rate": scenario.refusal_rate,
            "contribution": amount.major,
            "margin_pct": round(amount.minor / revenue.minor * 100, 1) if revenue.minor else None,
            "labour_cost": (
                round(labour_hourly.minor * sku.labour_minutes / 60, 0) / 100
                if labour_hourly else None
            ),
        })
    return SkuReport(sku=sku.sku, scenarios=rows,
                     break_even_refusal=break_even_refusal(sku),
                     labour_cost_per_hour=labour_hourly)
