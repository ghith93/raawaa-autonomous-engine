"""Delivery cost, risk, and free-shipping thresholds for cash-on-delivery markets.

The problem this solves: in a COD market the courier relationship is the largest
controllable cost and the largest controllable loss, and it is invisible in most
store dashboards. Two numbers decide whether a store scales profitably:

* the refusal rate, and
* whether delivery is billed to the buyer or absorbed into price.

Absorbing it is a legitimate strategy only when the margin covers refusals, and
the break-even refusal rate is the number that proves it or breaks it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .money import Money, safe_div


class DeliveryError(ValueError):
    pass


@dataclass(frozen=True)
class DeliveryRate:
    """Per-shipment economics quoted by the courier, or measured in practice."""

    fee: Money                 # charged when the parcel is delivered
    refusal_fee: Money         # charged when it comes back (usually non-zero)
    currency: str = "USD"

    def validate(self) -> None:
        if self.fee.minor < 0 or self.refusal_fee.minor < 0:
            raise DeliveryError("delivery fees cannot be negative")
        if self.currency != self.fee.currency or self.currency != self.refusal_fee.currency:
            raise DeliveryError("delivery currency must match both fees")


@dataclass
class Order:
    """One shipment and what it actually cost. Filled in after the courier reports."""

    order_id: str
    revenue: Money                 # what the buyer pays when delivered, delivery included
    goods_cost: Money              # materials + packaging + labour
    delivery: DeliveryRate
    delivered: bool | None = None  # None = still in flight
    cod_collected: Money | None = None  # cash actually received
    attempts: int = 1
    spoilage_rate: float = 1.0     # fraction of goods_cost lost on refusal (0.0 = returned intact)
    repack_cost: Money | None = None
    notes: list[str] = field(default_factory=list)

    def contribution(self) -> Money | None:
        """Profit after goods and delivery. None while the outcome is unknown.

        On a refused COD shipment (`delivered is False`), zero cash is collected
        from the buyer; the merchant loses the refusal courier fee, any repack
        cost, and only the spoiled/unusable fraction of `goods_cost`.
        """
        if self.delivered is None:
            return None
        if not 0.0 <= self.spoilage_rate <= 1.0:
            raise DeliveryError("spoilage_rate must be within 0..1")
        zero = Money.zero(self.revenue.currency)
        repack = self.repack_cost or zero
        if self.delivered:
            return self.revenue - (self.goods_cost + self.delivery.fee)
        spoiled_goods = self.goods_cost * self.spoilage_rate
        return zero - (spoiled_goods + self.delivery.refusal_fee + repack)

    def cash_reconciles(self, tolerance: Money | None = None) -> bool:
        """A delivered parcel must show cash equal to revenue, within tolerance."""
        if not self.delivered or self.cod_collected is None:
            return True
        allowed = tolerance or Money.zero(self.revenue.currency)
        return abs((self.cod_collected - self.revenue).minor) <= allowed.minor


def break_even_refusal_rate(
    price: Money,
    goods_cost: Money,
    delivery: DeliveryRate,
    *,
    spoilage_rate: float = 1.0,
    repack_cost: Money | None = None,
) -> float | None:
    """Refusal rate above which absorbing delivery loses money.

    At refusal rate r, expected contribution per shipment is
        (1-r) * (price - goods - fee) - r * (spoilage * goods + refusal_fee + repack)
    Setting that to zero gives
        r* = good / (good + loss)
    When `spoilage_rate == 1.0` and `repack_cost == 0`, this simplifies to
        (price - goods - fee) / (price - refusal_fee).
    """
    delivery.validate()
    if not 0.0 <= spoilage_rate <= 1.0:
        raise DeliveryError("spoilage_rate must be within 0..1")
    repack = repack_cost or Money.zero(price.currency)
    good = price - goods_cost - delivery.fee
    if good.minor <= 0:
        return 0.0
    loss = (goods_cost * spoilage_rate) + delivery.refusal_fee + repack
    if loss.minor <= 0:
        return 1.0
    denominator = good + loss
    ratio = safe_div(good.minor, denominator.minor)
    return None if ratio is None else ratio


def free_shipping_threshold(price: Money, goods_cost: Money, delivery: DeliveryRate,
                            target_margin: float = 0.5) -> Money | None:
    """Price that keeps `target_margin` of revenue after goods and delivery."""
    delivery.validate()
    if not 0 < target_margin < 1:
        raise DeliveryError("target_margin must be between 0 and 1")
    needed = goods_cost + delivery.fee
    # price - (goods + fee) = target * price  ->  price * (1 - target) = goods + fee
    return needed / (1 - target_margin)


@dataclass
class DeliveryBook:
    """Aggregates shipments and reports the numbers a dashboard usually hides."""

    orders: list[Order] = field(default_factory=list)

    def add(self, order: Order) -> None:
        if any(o.order_id == order.order_id for o in self.orders):
            raise DeliveryError(f"duplicate order id: {order.order_id}")
        self.orders.append(order)

    def report(self) -> dict:
        shipped = [o for o in self.orders if o.delivered is not None]
        delivered = [o for o in shipped if o.delivered]
        refused = [o for o in shipped if not o.delivered]
        settled = [o for o in shipped if o.contribution() is not None]
        contribution = sum((o.contribution().minor for o in settled), 0)  # type: ignore[union-attr]
        currency = self.orders[0].revenue.currency if self.orders else "USD"
        refusal_rate = safe_div(len(refused), len(shipped))
        return {
            "shipped": len(shipped),
            "delivered": len(delivered),
            "refused": len(refused),
            "in_flight": len(self.orders) - len(shipped),
            "refusal_rate": refusal_rate,
            "contribution": Money(contribution, currency).major,
            "contribution_per_shipment": (
                Money(round(contribution / len(settled)), currency).major if settled else None
            ),
            "revenue_collected": Money(sum(
                (o.cod_collected.minor for o in delivered if o.cod_collected), 0
            ), currency).major,
            "unreconciled": [o.order_id for o in delivered if not o.cash_reconciles()],
        }
