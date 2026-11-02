"""Bounded, read-only CEX spot depth scan across public exchange APIs.

This orchestration layer deliberately does not infer asset equivalence, fee tiers,
or profitability. The caller supplies canonical asset identifiers and exact
venue instrument IDs. Missing fees/latency remain unknown in the shared evaluator.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping

from .market_sources.binance import BinancePublicMarketData
from .market_sources.bybit import BybitPublicMarketData
from .market_sources.coinbase import CoinbaseExchangePublicMarketData
from .market_sources.cross_venue import InstrumentRegistry, compare_cex_spot_orderbook_universe
from .market_sources.okx import OKXPublicSpotMarketData


class CexScanError(ValueError):
    """Invalid or insufficient inputs for a bounded read-only CEX scan."""


def _default_factories(timeout_seconds: float) -> dict[str, Callable[[], object]]:
    return {
        "binance": lambda: BinancePublicMarketData(
            timeout_seconds=timeout_seconds, min_interval_seconds=1.0
        ),
        "bybit": lambda: BybitPublicMarketData(
            timeout_seconds=timeout_seconds, min_interval_seconds=1.0
        ),
        "coinbase": lambda: CoinbaseExchangePublicMarketData(
            timeout_seconds=timeout_seconds, min_interval_seconds=1.0
        ),
        "okx": lambda: OKXPublicSpotMarketData(
            timeout_seconds=timeout_seconds, min_interval_seconds=1.0
        ),
    }


def _venue_fetch(client: object, venue: str, instrument_id: str):
    if venue == "binance":
        metadata = client.fetch_instrument(symbol=instrument_id)
        book = client.fetch_orderbook(symbol=instrument_id, limit=20)
    elif venue == "bybit":
        metadata = client.fetch_instrument(symbol=instrument_id, category="spot")
        book = client.fetch_orderbook(category="spot", symbol=instrument_id, limit=50)
    elif venue == "coinbase":
        metadata = client.fetch_instrument(product_id=instrument_id)
        book = client.fetch_orderbook(product_id=instrument_id, level=2)
    elif venue == "okx":
        metadata = client.fetch_instrument(inst_id=instrument_id)
        book = client.fetch_orderbook(inst_id=instrument_id, depth=20)
    else:
        raise CexScanError("unsupported_venue")
    return metadata, book


def _decimal_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def scan_cex_spot_depth(
    *,
    venue_instruments: Mapping[str, str],
    base_code: str,
    quote_code: str,
    base_asset_id: str,
    quote_currency_id: str,
    base_quantity: Decimal | str,
    timeout_seconds: float = 8.0,
    max_quote_age_seconds: int = 5,
    max_quote_skew_seconds: int = 2,
    now: datetime | None = None,
    client_factories: Mapping[str, Callable[[], object]] | None = None,
) -> dict:
    """Fetch at most four public spot books concurrently and compare full requested depth.

    Canonical IDs are operator-supplied. This function only maps an exchange's
    returned asset codes to those IDs after exact base/quote code validation.
    Kraken's XBT/BTC alias and other exchange-specific aliases are intentionally
    not inferred; add them only through a separately reviewed explicit mapping.
    """
    if not isinstance(venue_instruments, Mapping) or not 2 <= len(venue_instruments) <= 4:
        raise CexScanError("supply between two and four venue=instrument entries")
    if len(set(venue_instruments)) != len(venue_instruments):
        raise CexScanError("duplicate venue")
    if any(
        venue not in {"binance", "bybit", "coinbase", "okx"}
        or not isinstance(instrument, str) or not instrument.strip()
        for venue, instrument in venue_instruments.items()
    ):
        raise CexScanError("unsupported_venue_or_invalid_instrument")
    for value, label in (
        (base_code, "base_code"),
        (quote_code, "quote_code"),
        (base_asset_id, "base_asset_id"),
        (quote_currency_id, "quote_currency_id"),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise CexScanError(f"invalid_{label}")
    if base_code != base_code.upper() or quote_code != quote_code.upper() or base_code == quote_code:
        raise CexScanError("asset_codes_must_be_distinct_uppercase")
    try:
        quantity = base_quantity if isinstance(base_quantity, Decimal) else Decimal(base_quantity)
    except Exception as exc:
        raise CexScanError("invalid_base_quantity") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise CexScanError("invalid_base_quantity")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 30:
        raise CexScanError("timeout_seconds_must_be_between_1_and_30")
    if type(max_quote_age_seconds) is not int or not 1 <= max_quote_age_seconds <= 3600:
        raise CexScanError("invalid_max_quote_age_seconds")
    if type(max_quote_skew_seconds) is not int or not 0 <= max_quote_skew_seconds <= 3600:
        raise CexScanError("invalid_max_quote_skew_seconds")
    current_time = now or datetime.now(timezone.utc)
    if not isinstance(current_time, datetime) or current_time.tzinfo is None or current_time.utcoffset() is None:
        raise CexScanError("now_must_be_timezone_aware")

    factories = dict(client_factories or _default_factories(float(timeout_seconds)))
    if set(venue_instruments) - set(factories):
        raise CexScanError("missing_client_factory")

    fetched: dict[str, tuple[object, object]] = {}
    failures: dict[str, str] = {}

    def collect(venue: str, instrument_id: str):
        metadata, book = _venue_fetch(factories[venue](), venue, instrument_id)
        if metadata.venue_id != venue or metadata.instrument_id != instrument_id:
            raise CexScanError("instrument_identity_mismatch")
        if metadata.status.upper() != "TRADING":
            raise CexScanError("instrument_not_trading")
        if metadata.base_asset_code != base_code or metadata.quote_asset_code != quote_code:
            raise CexScanError("asset_code_mismatch")
        if book.source != venue or book.symbol != instrument_id:
            raise CexScanError("orderbook_identity_mismatch")
        return metadata, book

    with ThreadPoolExecutor(max_workers=len(venue_instruments), thread_name_prefix="cex-read") as pool:
        futures = {
            pool.submit(collect, venue, instrument): venue
            for venue, instrument in venue_instruments.items()
        }
        for future in as_completed(futures):
            venue = futures[future]
            try:
                fetched[venue] = future.result()
            except Exception as exc:
                # Do not leak raw provider payloads, URLs, or exception text into reports.
                reason = str(exc) if isinstance(exc, CexScanError) else "provider_request_failed"
                failures[venue] = reason[:120]

    venue_ids = tuple(sorted(fetched))
    base_quantity_text = str(quantity)
    if len(fetched) < 2:
        return {
            "mode": "read_only_cex_spot_depth_scan",
            "status": "insufficient_sources",
            "venue_ids": list(venue_ids),
            "source_failures": failures,
            "opportunities": [],
            "base_quantity": base_quantity_text,
            "live_execution": False,
            "net_profit_claim": False,
            "warning": "Fewer than two compatible live spot books were available; no comparison was produced.",
        }

    metadata = [fetched[venue][0] for venue in venue_ids]
    books = [fetched[venue][1] for venue in venue_ids]
    canonical_ids = {}
    for item in metadata:
        canonical_ids[(item.venue_id, item.base_asset_code)] = base_asset_id
        canonical_ids[(item.venue_id, item.quote_asset_code)] = quote_currency_id
    registry = InstrumentRegistry.from_metadata(metadata, canonical_ids)
    result = compare_cex_spot_orderbook_universe(
        books=books,
        registry=registry,
        base_quantity=quantity,
        source_failures=failures,
        now=current_time,
        max_quote_age_seconds=max_quote_age_seconds,
        max_quote_skew_seconds=max_quote_skew_seconds,
        max_venues=4,
    )
    opportunities = []
    for item in result.opportunities:
        evaluation = item.evaluation
        opportunities.append({
            "status": item.status,
            "buy_venue": item.buy_venue_id,
            "sell_venue": item.sell_venue_id,
            "base_asset_id": item.base_asset_id,
            "quote_currency_id": item.quote_currency_id,
            "requested_base_quantity": str(item.requested_base_quantity),
            "buy_filled_base_quantity": str(item.buy_filled_base_quantity),
            "sell_filled_base_quantity": str(item.sell_filled_base_quantity),
            "buy_fill_fraction": str(item.buy_fill_fraction),
            "sell_fill_fraction": str(item.sell_fill_fraction),
            "buy_vwap": _decimal_string(item.buy_vwap),
            "sell_vwap": _decimal_string(item.sell_vwap),
            "buy_quote_spent": str(item.buy_quote_spent),
            "sell_quote_received": str(item.sell_quote_received),
            "gross_profit_quote": _decimal_string(item.gross_profit_quote),
            "evaluation": None if evaluation is None else {
                "status": evaluation.status,
                "gross_spread_bps": _decimal_string(evaluation.gross_spread_bps),
                "known_cost_quote": str(evaluation.known_cost_quote),
                "net_profit_quote": _decimal_string(evaluation.net_profit_quote),
                "missing_required_costs": list(evaluation.missing_required_costs),
                "max_quote_age_seconds": _decimal_string(evaluation.max_quote_age_seconds),
                "execution_verified": evaluation.execution_verified,
            },
            "execution_verified": False,
        })
    return {
        "mode": "read_only_cex_spot_depth_scan",
        "status": result.status,
        "venue_ids": list(result.venue_ids),
        "compared_pair_count": result.compared_pair_count,
        "skipped_pairs": [list(item) for item in result.skipped_pairs],
        "source_failures": dict(result.source_failures),
        "base_quantity": base_quantity_text,
        "quote_age_limit_seconds": max_quote_age_seconds,
        "quote_skew_limit_seconds": max_quote_skew_seconds,
        "opportunities": opportunities,
        "fee_profile": "not_supplied",
        "net_profit_claim": False,
        "execution_verified": False,
        "live_execution": False,
        "transaction_submitted": False,
        "warning": (
            "Depth-swept gross spread only. Exchange fee tiers, latency, settlement, "
            "withdrawal/deposit availability and account restrictions are not supplied; "
            "net profit remains unknown. Public books are not executable account quotes."
        ),
    }
