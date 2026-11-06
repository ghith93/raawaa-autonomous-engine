"""Supplier scoring for a store that does not manufacture everything.

The founder is a pharmacist compounding one serum, but he is building an
e-commerce operation, so most future products will be bought in. The expensive
mistake in that model is choosing a supplier on unit price alone, because landed
cost and failure rate decide the actual margin.

Scoring is deliberately transparent: each factor returns a 0-1 sub-score with a
stated direction, and the weights are configuration, not code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .money import Money, safe_div

DEFAULT_WEIGHTS = {
    "margin": 0.35,
    "reliability": 0.30,
    "lead_time": 0.15,
    "moq_fit": 0.10,
    "defect_rate": 0.10,
}


class SourcingError(ValueError):
    pass


@dataclass
class Supplier:
    id: str
    name: str
    unit_cost: Money
    lead_time_days: int
    moq: int                    # minimum order quantity
    defect_rate: float          # 0..1, measured, not claimed
    on_time_rate: float         # 0..1, measured from past purchase orders
    notes: str = ""

    def validate(self) -> list[str]:
        problems = []
        if self.unit_cost.minor <= 0:
            problems.append("unit cost must be positive")
        if self.lead_time_days < 0:
            problems.append("lead time cannot be negative")
        if self.moq <= 0:
            problems.append("MOQ must be positive")
        if not 0 <= self.defect_rate <= 1:
            problems.append("defect rate must be within 0..1")
        if not 0 <= self.on_time_rate <= 1:
            problems.append("on-time rate must be within 0..1")
        return problems


@dataclass
class Score:
    supplier_id: str
    total: float
    components: dict[str, float]
    rejected_reason: str | None = None


def landed_cost(supplier: Supplier, quantity: int, freight: Money,
                duty_rate: float = 0.0) -> Money:
    """Per-unit cost after freight and duty, and after paying for defects."""
    if quantity < supplier.moq:
        raise SourcingError(f"quantity {quantity} is below MOQ {supplier.moq}")
    goods = supplier.unit_cost * quantity
    landed = goods + freight + (goods * duty_rate)
    usable = quantity * (1 - supplier.defect_rate)
    if usable <= 0:
        raise SourcingError("defect rate leaves no usable units")
    return landed / usable


def score(supplier: Supplier, *, sell_price: Money, cash_available: Money,
          weights: dict[str, float] | None = None) -> Score:
    """Rank a supplier on what actually decides margin, not on unit price."""
    problems = supplier.validate()
    if problems:
        raise SourcingError("; ".join(problems))
    w = weights or DEFAULT_WEIGHTS
    if abs(sum(w.values()) - 1.0) > 1e-6:
        raise SourcingError("weights must sum to 1.0")

    committed = supplier.unit_cost * supplier.moq
    if committed.minor > cash_available.minor:
        return Score(supplier.id, 0.0, {},
                     rejected_reason=f"MOQ needs {committed}, only {cash_available} available")

    # Margin: gross margin at list price, normalised at 60%.
    gross = sell_price - supplier.unit_cost
    margin_ratio = safe_div(gross.minor, sell_price.minor) or 0.0
    margin = max(0.0, min(1.0, margin_ratio / 0.6))

    reliability = max(0.0, min(1.0, supplier.on_time_rate))
    # 0 days -> 1.0, 30+ days -> 0.0
    lead_time = max(0.0, min(1.0, 1 - supplier.lead_time_days / 30))
    # MOQ the founder can actually fund at 3x over: smaller is better.
    moq_fit = max(0.0, min(1.0, 1 - (committed.minor / max(1, cash_available.minor * 3))))
    defect_rate = max(0.0, min(1.0, 1 - supplier.defect_rate * 10))

    components = {
        "margin": margin,
        "reliability": reliability,
        "lead_time": lead_time,
        "moq_fit": moq_fit,
        "defect_rate": defect_rate,
    }
    total = sum(components[k] * w[k] for k in w)
    return Score(supplier.id, round(total, 4), {k: round(v, 4) for k, v in components.items()})


def rank(suppliers: list[Supplier], *, sell_price: Money, cash_available: Money,
         weights: dict[str, float] | None = None) -> list[Score]:
    scored = [score(s, sell_price=sell_price, cash_available=cash_available, weights=weights)
              for s in suppliers]
    return sorted(scored, key=lambda s: (-s.total, s.supplier_id))
