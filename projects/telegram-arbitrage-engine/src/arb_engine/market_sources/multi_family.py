"""Compare two market legs from *different* market families.

`cross_venue.py` compares CEX spot order books with each other. That is the only
comparison the codebase could express until now, even though
`opportunity_costs.py` has been able to price four families for a while: it
requires gas and priority fees from an `evm_amm` leg, network and priority fees
from a `solana_aggregator` leg, funding from a `cex_perp` leg, and refuses to
label anything net-positive while a required cost is unknown.

This module is the missing seam: it accepts one observation per side, from any of
those families, and hands them to the same evaluator with the same gates. What it
does *not* do is pretend the families are the same thing:

- each leg must declare its ``data_type``, because an order book, an executable
  quote and an indicative aggregator quote are not interchangeable evidence;
- an ``indicative_quote`` leg caps the result at "indicative" — a price nobody has
  committed to cannot produce a profit label;
- depth is whatever the leg can actually fill (``max_base_quantity``), and a leg
  that cannot fill the requested size is reported as insufficient depth rather
  than priced at a size it never quoted;
- identity is canonical asset IDs, never symbols.

No network access, no signing, no order placement. Every input is a value.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..opportunity_costs import (
    OpportunityCosts,
    OpportunityEvaluation,
    OpportunityEvaluationError,
    evaluate_two_leg_spread,
)

FAMILIES = ("cex_spot", "cex_perp", "evm_amm", "solana_aggregator")
# Ordered weakest-to-strongest. A comparison is only as good as its weaker side.
DATA_TYPES = ("indicative_quote", "on_chain_state", "order_book", "executable_quote")
_INDICATIVE = "indicative_quote"


class MultiFamilyError(ValueError):
    """A leg observation is malformed, or the pair cannot be compared at all."""


def _decimal(value, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise MultiFamilyError(f"{field} must be a Decimal, string or integer (never a float)")
    try:
        parsed = Decimal(value)
    except Exception as exc:  # noqa: BLE001 - Decimal raises several types
        raise MultiFamilyError(f"{field} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise MultiFamilyError(f"{field} must be finite")
    if positive and parsed <= 0:
        raise MultiFamilyError(f"{field} must be positive")
    return parsed


@dataclass(frozen=True)
class LegQuote:
    """One side of a comparison, as the source actually reported it."""

    market_family: str
    venue_id: str
    instrument_id: str
    base_asset_id: str
    quote_currency_id: str
    price_quote_per_base: Decimal | str
    observed_at: datetime
    data_type: str
    provenance: str
    # How much base the price actually covers. None means "the source made no
    # depth claim", which is not the same as "unlimited" — see compare_legs.
    max_base_quantity: Decimal | str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.market_family, "market_family"),
            (self.venue_id, "venue_id"),
            (self.instrument_id, "instrument_id"),
            (self.base_asset_id, "base_asset_id"),
            (self.quote_currency_id, "quote_currency_id"),
            (self.data_type, "data_type"),
            (self.provenance, "provenance"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise MultiFamilyError(f"{label} is required")
        if self.market_family not in FAMILIES:
            raise MultiFamilyError(f"unsupported market family: {self.market_family}")
        if self.data_type not in DATA_TYPES:
            raise MultiFamilyError(f"unsupported data type: {self.data_type}")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise MultiFamilyError("observed_at must be a timezone-aware datetime")
        object.__setattr__(
            self, "price_quote_per_base",
            _decimal(self.price_quote_per_base, "price_quote_per_base", positive=True),
        )
        if self.max_base_quantity is not None:
            object.__setattr__(
                self, "max_base_quantity",
                _decimal(self.max_base_quantity, "max_base_quantity", positive=True),
            )


@dataclass(frozen=True)
class MultiFamilyComparison:
    evaluation: OpportunityEvaluation
    label: str
    buy: LegQuote
    sell: LegQuote
    requested_base_quantity: Decimal
    buy_fill_fraction: Decimal | None
    sell_fill_fraction: Decimal | None
    evidence: tuple[tuple[str, str], ...]

    @property
    def status(self) -> str:
        return self.evaluation.status

    @property
    def is_net_profit_claim(self) -> bool:
        return self.label == "net_positive_candidate"


def _fill_fraction(leg: LegQuote, requested_base: Decimal) -> Decimal | None:
    """Return None when the source did not declare depth; never infer a full fill."""
    if leg.max_base_quantity is None:
        return None
    if leg.max_base_quantity >= requested_base:
        return Decimal("1")
    return leg.max_base_quantity / requested_base


def compare_legs(
    *,
    buy: LegQuote,
    sell: LegQuote,
    notional_quote: Decimal | str,
    costs: OpportunityCosts,
    now: datetime | None = None,
    max_quote_age_seconds: int = 5,
    max_quote_skew_seconds: int = 2,
    cross_chain: bool = False,
    uses_borrow: bool = False,
) -> MultiFamilyComparison:
    """Compare two legs from any two families, with every gate the evaluator has.

    The result is a comparison, not a recommendation: ``label`` is
    ``net_positive_candidate`` only when the evaluator returns
    ``net_positive_estimate`` *and* neither leg is merely indicative. Everything
    else keeps the evaluator's own status, so a refusal to answer is never
    rendered as a small profit.
    """
    if not isinstance(buy, LegQuote) or not isinstance(sell, LegQuote):
        raise MultiFamilyError("both legs must be LegQuote instances")
    if buy.venue_id == sell.venue_id and buy.instrument_id == sell.instrument_id:
        raise MultiFamilyError("both legs are the same venue and instrument")
    if not isinstance(costs, OpportunityCosts):
        raise MultiFamilyError("costs must be an OpportunityCosts instance")

    notional = _decimal(notional_quote, "notional_quote", positive=True)
    requested_base = notional / buy.price_quote_per_base
    buy_fill = _fill_fraction(buy, requested_base)
    sell_fill = _fill_fraction(sell, requested_base)

    try:
        evaluation = evaluate_two_leg_spread(
            buy_price_quote_per_base=buy.price_quote_per_base,
            sell_price_quote_per_base=sell.price_quote_per_base,
            notional_quote=notional,
            base_asset_id_buy=buy.base_asset_id,
            base_asset_id_sell=sell.base_asset_id,
            quote_currency_id_buy=buy.quote_currency_id,
            quote_currency_id_sell=sell.quote_currency_id,
            buy_market_family=buy.market_family,
            sell_market_family=sell.market_family,
            buy_fetched_at=buy.observed_at,
            sell_fetched_at=sell.observed_at,
            costs=costs,
            now=now or datetime.now(timezone.utc),
            max_quote_age_seconds=max_quote_age_seconds,
            max_quote_skew_seconds=max_quote_skew_seconds,
            buy_fill_fraction=buy_fill,
            sell_fill_fraction=sell_fill,
            cross_chain=cross_chain,
            uses_borrow=uses_borrow,
        )
    except OpportunityEvaluationError as exc:
        raise MultiFamilyError(str(exc)) from exc

    evidence = (
        ("buy_family", buy.market_family),
        ("buy_data_type", buy.data_type),
        ("buy_provenance", buy.provenance),
        ("sell_family", sell.market_family),
        ("sell_data_type", sell.data_type),
        ("sell_provenance", sell.provenance),
        ("buy_depth_basis", "quoted" if buy.max_base_quantity is not None else "undeclared"),
        ("sell_depth_basis", "quoted" if sell.max_base_quantity is not None else "undeclared"),
        (
            "depth_basis",
            "quoted" if buy.max_base_quantity is not None and sell.max_base_quantity is not None
            else "partially_declared" if buy.max_base_quantity is not None or sell.max_base_quantity is not None
            else "undeclared",
        ),
    )

    if evaluation.status == "net_positive_estimate":
        if _INDICATIVE in (buy.data_type, sell.data_type):
            label = "indicative_spread_only"
        else:
            label = "net_positive_candidate"
    else:
        label = evaluation.status

    return MultiFamilyComparison(
        evaluation=evaluation,
        label=label,
        buy=buy,
        sell=sell,
        requested_base_quantity=requested_base,
        buy_fill_fraction=buy_fill,
        sell_fill_fraction=sell_fill,
        evidence=evidence,
    )
