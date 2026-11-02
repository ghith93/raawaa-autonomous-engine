from __future__ import annotations

from decimal import Decimal, localcontext

from .models import Opportunity, ProfitBreakdown

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


def calculate_profit(
    opportunity: Opportunity, safety_margin_usd: Decimal = Decimal("0.05")
) -> ProfitBreakdown:
    """Conservative round trip; buy fees are included inside the capital limit."""
    cap = opportunity.capital_usd
    buy = opportunity.buy
    sell = opportunity.sell

    with localcontext() as ctx:
        ctx.prec = 40
        buy_fee_rate = buy.fee_bps / TEN_THOUSAND
        sell_fee_rate = sell.fee_bps / TEN_THOUSAND
        buy_slippage = buy.slippage_bps / TEN_THOUSAND
        sell_slippage = sell.slippage_bps / TEN_THOUSAND

        buy_notional = cap / (Decimal("1") + buy_fee_rate)
        buy_fee = buy_notional * buy_fee_rate
        effective_buy_price = buy.price * (Decimal("1") + buy_slippage)
        quantity = buy_notional / effective_buy_price

        effective_sell_price = sell.price * (Decimal("1") - sell_slippage)
        sell_notional = quantity * effective_sell_price
        sell_fee = sell_notional * sell_fee_rate
        gas = buy.gas_cost_usd + sell.gas_cost_usd
        net = sell_notional - sell_fee - buy_notional - buy_fee - gas - safety_margin_usd
        net_bps = (net / cap) * TEN_THOUSAND

        return ProfitBreakdown(
            capital_usd=cap,
            buy_notional_usd=buy_notional,
            buy_fee_usd=buy_fee,
            asset_quantity=quantity,
            sell_notional_usd=sell_notional,
            sell_fee_usd=sell_fee,
            gas_cost_usd=gas,
            safety_margin_usd=safety_margin_usd,
            net_profit_usd=net,
            net_bps=net_bps,
        )
