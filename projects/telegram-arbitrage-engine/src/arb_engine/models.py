from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MarketQuote:
    venue: str
    chain: str
    base_token: str
    quote_token: str
    price: Decimal
    liquidity_usd: Decimal
    fee_bps: Decimal
    slippage_bps: Decimal
    gas_cost_usd: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class Opportunity:
    capital_usd: Decimal
    buy: MarketQuote
    sell: MarketQuote


@dataclass(frozen=True, slots=True)
class ProfitBreakdown:
    capital_usd: Decimal
    buy_notional_usd: Decimal
    buy_fee_usd: Decimal
    asset_quantity: Decimal
    sell_notional_usd: Decimal
    sell_fee_usd: Decimal
    gas_cost_usd: Decimal
    safety_margin_usd: Decimal
    net_profit_usd: Decimal
    net_bps: Decimal


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    reason: str
    breakdown: ProfitBreakdown | None = None
