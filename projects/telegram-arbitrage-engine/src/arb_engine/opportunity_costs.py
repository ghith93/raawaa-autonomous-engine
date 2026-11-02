from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal


MarketFamily = Literal["cex_spot", "cex_perp", "evm_amm", "solana_aggregator"]


class OpportunityEvaluationError(ValueError):
    """Invalid inputs for conservative two-leg opportunity evaluation."""


@dataclass(frozen=True)
class OpportunityCosts:
    buy_fee_bps: Decimal | str | None = None
    sell_fee_bps: Decimal | str | None = None
    buy_slippage_bps: Decimal | str | None = None
    sell_slippage_bps: Decimal | str | None = None
    latency_cost_bps: Decimal | str | None = None
    gas_cost_quote: Decimal | str | None = None
    priority_fee_quote: Decimal | str | None = None
    network_fee_quote: Decimal | str | None = None
    bridge_cost_quote: Decimal | str | None = None
    settlement_cost_quote: Decimal | str | None = None
    funding_cost_quote: Decimal | str | None = None
    borrow_cost_quote: Decimal | str | None = None


@dataclass(frozen=True)
class OpportunityEvaluation:
    status: str
    base_asset_id: str
    quote_currency_id: str
    buy_market_family: str
    sell_market_family: str
    gross_spread_bps: Decimal | None
    gross_profit_quote: Decimal | None
    known_cost_quote: Decimal
    net_profit_quote: Decimal | None
    missing_required_costs: tuple[str, ...]
    max_quote_age_seconds: Decimal | None
    execution_verified: bool = False


_BPS_FIELDS = (
    "buy_fee_bps",
    "sell_fee_bps",
    "buy_slippage_bps",
    "sell_slippage_bps",
    "latency_cost_bps",
)
_FLAT_COST_FIELDS = (
    "gas_cost_quote",
    "priority_fee_quote",
    "network_fee_quote",
    "bridge_cost_quote",
    "settlement_cost_quote",
    "funding_cost_quote",
    "borrow_cost_quote",
)
_ALLOWED_FAMILIES = {"cex_spot", "cex_perp", "evm_amm", "solana_aggregator"}


def _decimal(value: Decimal | str, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise OpportunityEvaluationError(f"{field} must be Decimal or a decimal string, not float")
    if not isinstance(value, (Decimal, str)) or (isinstance(value, str) and (not value or len(value) > 80)):
        raise OpportunityEvaluationError(f"invalid {field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as exc:
        raise OpportunityEvaluationError(f"invalid {field}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (not positive and parsed < 0):
        raise OpportunityEvaluationError(f"invalid {field}")
    return parsed


def _signed_decimal(value: Decimal | str, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise OpportunityEvaluationError(f"{field} must be Decimal or a decimal string, not float")
    if not isinstance(value, (Decimal, str)) or (isinstance(value, str) and (not value or len(value) > 80)):
        raise OpportunityEvaluationError(f"invalid {field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as exc:
        raise OpportunityEvaluationError(f"invalid {field}") from exc
    if not parsed.is_finite():
        raise OpportunityEvaluationError(f"invalid {field}")
    return parsed


def _aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OpportunityEvaluationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def evaluate_two_leg_spread(
    *,
    buy_price_quote_per_base: Decimal | str,
    sell_price_quote_per_base: Decimal | str,
    notional_quote: Decimal | str,
    base_asset_id_buy: str,
    base_asset_id_sell: str,
    quote_currency_id_buy: str,
    quote_currency_id_sell: str,
    buy_market_family: MarketFamily,
    sell_market_family: MarketFamily,
    buy_fetched_at: datetime,
    sell_fetched_at: datetime,
    costs: OpportunityCosts,
    gross_profit_quote_override: Decimal | str | None = None,
    now: datetime | None = None,
    max_quote_age_seconds: int = 5,
    buy_fill_fraction: Decimal | str = "1",
    sell_fill_fraction: Decimal | str = "1",
    cross_chain: bool = False,
    uses_borrow: bool = False,
) -> OpportunityEvaluation:
    """Estimate a two-leg spread without ever treating unknown costs as zero.

    Asset IDs must be canonical, explicitly mapped identifiers (not symbols). Both
    quote currencies must be identical; callers must perform a separately evidenced
    conversion before comparing different quote currencies.
    """
    if buy_market_family not in _ALLOWED_FAMILIES or sell_market_family not in _ALLOWED_FAMILIES:
        raise OpportunityEvaluationError("unsupported market family")
    if not isinstance(costs, OpportunityCosts):
        raise OpportunityEvaluationError("costs must be an OpportunityCosts instance")
    for value, label in (
        (base_asset_id_buy, "base_asset_id_buy"),
        (base_asset_id_sell, "base_asset_id_sell"),
        (quote_currency_id_buy, "quote_currency_id_buy"),
        (quote_currency_id_sell, "quote_currency_id_sell"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise OpportunityEvaluationError(f"{label} is required")
    if type(max_quote_age_seconds) is not int or not 1 <= max_quote_age_seconds <= 3600:
        raise OpportunityEvaluationError("max_quote_age_seconds must be between 1 and 3600")

    buy_price = _decimal(buy_price_quote_per_base, "buy_price_quote_per_base", positive=True)
    sell_price = _decimal(sell_price_quote_per_base, "sell_price_quote_per_base", positive=True)
    notional = _decimal(notional_quote, "notional_quote", positive=True)
    buy_fill = _decimal(buy_fill_fraction, "buy_fill_fraction")
    sell_fill = _decimal(sell_fill_fraction, "sell_fill_fraction")
    if buy_fill > 1 or sell_fill > 1:
        raise OpportunityEvaluationError("fill fractions cannot exceed 1")

    buy_time = _aware(buy_fetched_at, "buy_fetched_at")
    sell_time = _aware(sell_fetched_at, "sell_fetched_at")
    current_time = _aware(now or datetime.now(timezone.utc), "now")
    buy_age_raw = Decimal(str((current_time - buy_time).total_seconds()))
    sell_age_raw = Decimal(str((current_time - sell_time).total_seconds()))
    future_dated = min(buy_age_raw, sell_age_raw) < Decimal("-1")
    buy_age = max(Decimal("0"), buy_age_raw)
    sell_age = max(Decimal("0"), sell_age_raw)
    max_age = max(buy_age, sell_age)

    identity_matches = (
        base_asset_id_buy == base_asset_id_sell
        and quote_currency_id_buy == quote_currency_id_sell
    )
    if not identity_matches:
        return OpportunityEvaluation(
            status="asset_or_quote_identity_mismatch",
            base_asset_id=base_asset_id_buy,
            quote_currency_id=quote_currency_id_buy,
            buy_market_family=buy_market_family,
            sell_market_family=sell_market_family,
            gross_spread_bps=None,
            gross_profit_quote=None,
            known_cost_quote=Decimal("0"),
            net_profit_quote=None,
            missing_required_costs=(),
            max_quote_age_seconds=max_age,
        )

    gross_spread_bps = (sell_price / buy_price - Decimal("1")) * Decimal("10000")
    # Depth-swept callers can provide the exact quote proceeds minus exact quote spend.
    # This avoids rounding repeating VWAP ratios into monetary PnL.
    gross_profit = (
        _signed_decimal(gross_profit_quote_override, "gross_profit_quote_override")
        if gross_profit_quote_override is not None
        else notional * (sell_price / buy_price - Decimal("1"))
    )

    required = list(_BPS_FIELDS)
    families = {buy_market_family, sell_market_family}
    if "evm_amm" in families:
        required.extend(("gas_cost_quote", "priority_fee_quote"))
    if "solana_aggregator" in families:
        required.extend(("network_fee_quote", "priority_fee_quote"))
    if "cex_perp" in families:
        required.append("funding_cost_quote")
    if cross_chain:
        required.extend(("bridge_cost_quote", "settlement_cost_quote"))
    if uses_borrow:
        required.append("borrow_cost_quote")
    required = list(dict.fromkeys(required))

    known_bps = Decimal("0")
    known_flat = Decimal("0")
    missing = []
    for field in _BPS_FIELDS:
        value = getattr(costs, field)
        if value is None:
            if field in required:
                missing.append(field)
        else:
            known_bps += _decimal(value, field)
    for field in _FLAT_COST_FIELDS:
        value = getattr(costs, field)
        if value is None:
            if field in required:
                missing.append(field)
        else:
            known_flat += _decimal(value, field)
    known_cost = notional * known_bps / Decimal("10000") + known_flat

    stale = future_dated or max_age > Decimal(max_quote_age_seconds)
    insufficient_depth = buy_fill < 1 or sell_fill < 1
    if stale:
        status = "stale_data"
    elif insufficient_depth:
        status = "insufficient_depth"
    elif missing:
        status = "incomplete_costs"
    else:
        net_profit = gross_profit - known_cost
        status = "net_positive_estimate" if net_profit > 0 else "net_nonpositive"
        return OpportunityEvaluation(
            status=status,
            base_asset_id=base_asset_id_buy,
            quote_currency_id=quote_currency_id_buy,
            buy_market_family=buy_market_family,
            sell_market_family=sell_market_family,
            gross_spread_bps=gross_spread_bps,
            gross_profit_quote=gross_profit,
            known_cost_quote=known_cost,
            net_profit_quote=net_profit,
            missing_required_costs=(),
            max_quote_age_seconds=max_age,
        )

    return OpportunityEvaluation(
        status=status,
        base_asset_id=base_asset_id_buy,
        quote_currency_id=quote_currency_id_buy,
        buy_market_family=buy_market_family,
        sell_market_family=sell_market_family,
        gross_spread_bps=gross_spread_bps,
        gross_profit_quote=gross_profit,
        known_cost_quote=known_cost,
        net_profit_quote=None,
        missing_required_costs=tuple(missing),
        max_quote_age_seconds=max_age,
    )
