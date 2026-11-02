from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .economics import calculate_profit
from .models import Opportunity, ValidationResult

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


def _valid_quote(q) -> str | None:
    numeric_fields = (q.price, q.liquidity_usd, q.fee_bps, q.slippage_bps, q.gas_cost_usd)
    if any(not isinstance(value, Decimal) for value in numeric_fields):
        return "All market numeric fields must be Decimal (float is forbidden)"
    if not q.venue.strip() or not q.chain.strip() or not q.base_token.strip() or not q.quote_token.strip():
        return "Missing venue, chain, or token identity"
    if not q.price.is_finite() or q.price <= ZERO:
        return "Price must be finite and positive"
    if not q.liquidity_usd.is_finite() or q.liquidity_usd <= ZERO:
        return "Liquidity must be finite and positive"
    for label, value in (("fee", q.fee_bps), ("slippage", q.slippage_bps)):
        if not value.is_finite() or value < ZERO or value >= TEN_THOUSAND:
            return f"{label.title()} bps must be finite and in [0, 10000)"
    if not q.gas_cost_usd.is_finite() or q.gas_cost_usd < ZERO:
        return "Gas cost must be finite and non-negative"
    if q.observed_at.tzinfo is None or q.observed_at.utcoffset() is None:
        return "Quote timestamp must be timezone-aware"
    now = datetime.now(timezone.utc)
    observed = q.observed_at.astimezone(timezone.utc)
    if observed > now:
        return "Quote timestamp is in the future"
    if (now - observed).total_seconds() > 30:
        return "Quote is stale (older than 30 seconds)"
    return None


def validate_opportunity(
    opportunity: Opportunity,
    min_net_bps: Decimal = Decimal("35"),
    safety_margin_usd: Decimal = Decimal("0.05"),
) -> ValidationResult:
    cap = opportunity.capital_usd
    if not isinstance(cap, Decimal):
        return ValidationResult(False, "Capital must be Decimal (float is forbidden)")
    if not isinstance(min_net_bps, Decimal) or not isinstance(safety_margin_usd, Decimal):
        return ValidationResult(False, "Threshold and safety margin must be Decimal")
    if not cap.is_finite() or cap <= ZERO:
        return ValidationResult(False, "Capital must be finite and positive")
    if not min_net_bps.is_finite() or min_net_bps < ZERO:
        return ValidationResult(False, "Minimum return threshold must be finite and non-negative")
    if not safety_margin_usd.is_finite() or safety_margin_usd < ZERO:
        return ValidationResult(False, "Safety margin must be finite and non-negative")

    for label, quote in (("Buy", opportunity.buy), ("Sell", opportunity.sell)):
        problem = _valid_quote(quote)
        if problem:
            return ValidationResult(False, f"{label}: {problem}")

    buy, sell = opportunity.buy, opportunity.sell
    if buy.chain.casefold() != sell.chain.casefold():
        return ValidationResult(False, "Buy and sell quotes are on different chains")
    if buy.base_token.casefold() != sell.base_token.casefold() or buy.quote_token.casefold() != sell.quote_token.casefold():
        return ValidationResult(False, "Buy and sell quotes do not describe the same token pair")

    try:
        breakdown = calculate_profit(opportunity, safety_margin_usd)
    except (ArithmeticError, ValueError) as exc:
        return ValidationResult(False, f"Profit calculation failed: {type(exc).__name__}")

    if buy.liquidity_usd < breakdown.buy_notional_usd:
        return ValidationResult(False, "Insufficient buy-side liquidity", breakdown)
    if sell.liquidity_usd < breakdown.sell_notional_usd:
        return ValidationResult(False, "Insufficient sell-side liquidity", breakdown)
    if breakdown.net_bps < min_net_bps:
        return ValidationResult(False, f"Net return below threshold ({breakdown.net_bps:.2f} < {min_net_bps} bps)", breakdown)
    return ValidationResult(True, "Paper-only opportunity passed risk checks", breakdown)
