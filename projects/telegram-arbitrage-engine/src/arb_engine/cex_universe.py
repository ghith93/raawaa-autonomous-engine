"""Bounded multi-venue CEX spot depth collection and comparison."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from .market_sources.binance import BinancePublicMarketData
from .market_sources.bybit import BybitPublicMarketData
from .market_sources.coinbase import CoinbaseExchangePublicMarketData
from .market_sources.cross_venue import (
    CrossVenueUniverseResult,
    InstrumentRegistry,
    compare_cex_spot_orderbook_universe,
)
from .market_sources.instruments import InstrumentMetadata
from .market_sources.kraken import KrakenPublicMarketData
from .market_sources.okx import OKXPublicSpotMarketData
from .opportunity_costs import OpportunityCosts


SUPPORTED_VENUES = ("binance", "bybit", "coinbase", "kraken", "okx")


class CexUniverseScanError(ValueError):
    """Invalid input for a bounded multi-venue CEX scan."""


@dataclass(frozen=True)
class VenueRequest:
    venue_id: str
    instrument_id: str
    base_asset_id: str
    quote_currency_id: str
    fee_bps: Decimal | str | None = None

    def __post_init__(self) -> None:
        if self.venue_id not in SUPPORTED_VENUES:
            raise CexUniverseScanError("unsupported_venue")
        for value, field in (
            (self.instrument_id, "instrument_id"),
            (self.base_asset_id, "base_asset_id"),
            (self.quote_currency_id, "quote_currency_id"),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 200:
                raise CexUniverseScanError(f"invalid_{field}")
        if self.base_asset_id == self.quote_currency_id:
            raise CexUniverseScanError("canonical_asset_ids_must_be_distinct")
        if self.fee_bps is not None:
            fee = _nonnegative_decimal(self.fee_bps, "fee_bps")
            if fee > Decimal("1000"):
                raise CexUniverseScanError("fee_bps_exceeds_1000")
            object.__setattr__(self, "fee_bps", fee)


def _nonnegative_decimal(value: Decimal | str, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise CexUniverseScanError(f"invalid_{field}")
    if not isinstance(value, (Decimal, str)) or (isinstance(value, str) and (not value or len(value) > 80)):
        raise CexUniverseScanError(f"invalid_{field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as exc:
        raise CexUniverseScanError(f"invalid_{field}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise CexUniverseScanError(f"invalid_{field}")
    return parsed


def _fetch_snapshot(request: VenueRequest):
    venue = request.venue_id
    instrument = request.instrument_id
    if venue == "binance":
        client = BinancePublicMarketData()
        metadata = client.fetch_instrument(symbol=instrument)
        book = client.fetch_orderbook(symbol=metadata.instrument_id, limit=100)
    elif venue == "bybit":
        client = BybitPublicMarketData()
        metadata = client.fetch_instrument(symbol=instrument, category="spot")
        book = client.fetch_orderbook(category="spot", symbol=metadata.instrument_id, limit=50)
    elif venue == "coinbase":
        client = CoinbaseExchangePublicMarketData()
        metadata = client.fetch_instrument(product_id=instrument)
        book = client.fetch_orderbook(product_id=metadata.instrument_id, level=2)
    elif venue == "kraken":
        client = KrakenPublicMarketData()
        metadata = client.fetch_instrument(pair=instrument)
        book = client.fetch_orderbook(pair=metadata.instrument_id, count=100)
    elif venue == "okx":
        client = OKXPublicSpotMarketData()
        metadata = client.fetch_instrument(inst_id=instrument)
        book = client.fetch_orderbook(inst_id=metadata.instrument_id, depth=20)
    else:
        raise CexUniverseScanError("unsupported_venue")

    if metadata.venue_id != venue:
        raise CexUniverseScanError("instrument_venue_mismatch")
    if metadata.status.upper() != "TRADING":
        raise CexUniverseScanError("instrument_not_trading")
    if book.source != venue or book.symbol != metadata.instrument_id:
        raise CexUniverseScanError("orderbook_identity_mismatch")
    return metadata, book


def _evaluation_dict(evaluation):
    if evaluation is None:
        return None
    return {
        "status": evaluation.status,
        "gross_spread_bps": str(evaluation.gross_spread_bps) if evaluation.gross_spread_bps is not None else None,
        "gross_profit_quote": str(evaluation.gross_profit_quote) if evaluation.gross_profit_quote is not None else None,
        "known_cost_quote": str(evaluation.known_cost_quote),
        "net_profit_quote_estimate": str(evaluation.net_profit_quote) if evaluation.net_profit_quote is not None else None,
        "missing_required_costs": list(evaluation.missing_required_costs),
        "max_quote_age_seconds": str(evaluation.max_quote_age_seconds) if evaluation.max_quote_age_seconds is not None else None,
        "execution_verified": evaluation.execution_verified,
    }


def _opportunity_dict(item):
    return {
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
        "buy_vwap": str(item.buy_vwap) if item.buy_vwap is not None else None,
        "sell_vwap": str(item.sell_vwap) if item.sell_vwap is not None else None,
        "buy_quote_spent": str(item.buy_quote_spent),
        "sell_quote_received": str(item.sell_quote_received),
        "gross_profit_quote": str(item.gross_profit_quote) if item.gross_profit_quote is not None else None,
        "evaluation": _evaluation_dict(item.evaluation),
        "execution_verified": False,
    }


def _instrument_dict(metadata: InstrumentMetadata, book: object, request: VenueRequest) -> dict:
    return {
        "venue": metadata.venue_id,
        "instrument_id": metadata.instrument_id,
        "base_asset_code": metadata.base_asset_code,
        "quote_asset_code": metadata.quote_asset_code,
        "canonical_base_asset_id": request.base_asset_id,
        "canonical_quote_currency_id": request.quote_currency_id,
        "status": metadata.status,
        "base_step_size": str(metadata.base_step_size),
        "price_tick_size": str(metadata.price_tick_size),
        "min_base_quantity": str(metadata.min_base_quantity) if metadata.min_base_quantity is not None else None,
        "min_quote_notional": str(metadata.min_quote_notional) if metadata.min_quote_notional is not None else None,
        "metadata_fetched_at": metadata.fetched_at.isoformat(),
        "book_fetched_at": book.fetched_at.isoformat(),
        "bid_levels": len(book.bids),
        "ask_levels": len(book.asks),
    }


def scan_cex_universe(
    *,
    venues: tuple[VenueRequest, ...] | list[VenueRequest],
    base_quantity: Decimal | str,
    latency_cost_bps: Decimal | str | None = None,
    max_quote_age_seconds: int = 5,
    max_quote_skew_seconds: int = 2,
    now: datetime | None = None,
    fetcher=_fetch_snapshot,
) -> dict:
    """Fetch up to five public CEX books concurrently; compare every compatible pair.

    Asset identities and fee tiers are explicit per venue. A source outage produces
    a sanitized partial result; it never causes the remaining healthy venues to be
    discarded. Missing fees/latency remain unknown in the shared evaluator.
    """
    if not isinstance(venues, (tuple, list)) or not 2 <= len(venues) <= len(SUPPORTED_VENUES):
        raise CexUniverseScanError("supply between two and five venue requests")
    if any(not isinstance(item, VenueRequest) for item in venues):
        raise CexUniverseScanError("venues must contain VenueRequest values")
    ids = [item.venue_id for item in venues]
    if len(set(ids)) != len(ids):
        raise CexUniverseScanError("duplicate_venue")
    if isinstance(base_quantity, str) and (not base_quantity or base_quantity != base_quantity.strip() or len(base_quantity) > 80):
        raise CexUniverseScanError("invalid_base_quantity")
    try:
        quantity = base_quantity if isinstance(base_quantity, Decimal) else Decimal(base_quantity)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CexUniverseScanError("invalid_base_quantity") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise CexUniverseScanError("invalid_base_quantity")
    if latency_cost_bps is not None:
        latency_cost_bps = _nonnegative_decimal(latency_cost_bps, "latency_cost_bps")
        if latency_cost_bps > Decimal("10000"):
            raise CexUniverseScanError("latency_cost_bps_exceeds_10000")
    if type(max_quote_age_seconds) is not int or not 1 <= max_quote_age_seconds <= 3600:
        raise CexUniverseScanError("invalid_max_quote_age_seconds")
    if type(max_quote_skew_seconds) is not int or not 0 <= max_quote_skew_seconds <= 3600:
        raise CexUniverseScanError("invalid_max_quote_skew_seconds")
    current_time = now or datetime.now(timezone.utc)
    if not isinstance(current_time, datetime) or current_time.tzinfo is None or current_time.utcoffset() is None:
        raise CexUniverseScanError("now_must_be_timezone_aware")

    fetched: dict[str, tuple[VenueRequest, InstrumentMetadata, object]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(venues), thread_name_prefix="cex-universe") as pool:
        futures = {pool.submit(fetcher, request): request for request in venues}
        for future in as_completed(futures):
            request = futures[future]
            try:
                metadata, book = future.result()
                if metadata.venue_id != request.venue_id or not metadata.instrument_id or book.symbol != metadata.instrument_id:
                    raise CexUniverseScanError("instrument_identity_mismatch")
                if metadata.status.upper() != "TRADING":
                    raise CexUniverseScanError("instrument_not_trading")
                fetched[request.venue_id] = (request, metadata, book)
            except CexUniverseScanError as exc:
                failures[request.venue_id] = str(exc)[:120]
            except ValueError:
                failures[request.venue_id] = "invalid_instrument"
            except Exception:
                # Never expose raw provider response text, URLs, or credentials.
                failures[request.venue_id] = "provider_request_failed"

    venue_ids = tuple(sorted(fetched))
    if venue_ids:
        required_quote_code = fetched[venue_ids[0]][1].quote_asset_code
        for venue in tuple(venue_ids):
            if fetched[venue][1].quote_asset_code != required_quote_code:
                failures[venue] = "quote_asset_code_mismatch"
                del fetched[venue]
        venue_ids = tuple(sorted(fetched))
    if len(fetched) < 2:
        return {
            "mode": "read_only_cex_spot_universe_scan",
            "status": "insufficient_sources",
            "venues": [
                {"venue": venue, "instrument_id": fetched[venue][1].instrument_id}
                for venue in venue_ids
            ],
            "source_failures": failures,
            "opportunities": [],
            "compared_pair_count": 0,
            "base_quantity": str(quantity),
            "live_execution": False,
            "orders_submitted": False,
            "net_profit_claim": False,
            "warning": "Fewer than two public spot books were available; no comparison was produced.",
        }

    metadata = [fetched[venue][1] for venue in venue_ids]
    books = [fetched[venue][2] for venue in venue_ids]
    requests = {venue: fetched[venue][0] for venue in venue_ids}
    canonical_ids = {}
    for venue in venue_ids:
        item = fetched[venue][1]
        request = requests[venue]
        canonical_ids[(venue, item.base_asset_code)] = request.base_asset_id
        canonical_ids[(venue, item.quote_asset_code)] = request.quote_currency_id
    registry = InstrumentRegistry.from_metadata(metadata, canonical_ids)

    costs_by_direction = {}
    for buy_venue in venue_ids:
        for sell_venue in venue_ids:
            if buy_venue == sell_venue:
                continue
            costs_by_direction[(buy_venue, sell_venue)] = OpportunityCosts(
                buy_fee_bps=requests[buy_venue].fee_bps,
                sell_fee_bps=requests[sell_venue].fee_bps,
                buy_slippage_bps="0",
                sell_slippage_bps="0",
                latency_cost_bps=latency_cost_bps,
            )

    result: CrossVenueUniverseResult = compare_cex_spot_orderbook_universe(
        books=books,
        registry=registry,
        base_quantity=quantity,
        costs_by_direction=costs_by_direction,
        source_failures=failures,
        now=current_time,
        max_quote_age_seconds=max_quote_age_seconds,
        max_quote_skew_seconds=max_quote_skew_seconds,
        max_venues=len(SUPPORTED_VENUES),
    )
    return {
        "mode": "read_only_cex_spot_universe_scan",
        "status": result.status,
        "venues": [
            _instrument_dict(fetched[venue][1], fetched[venue][2], fetched[venue][0])
            for venue in result.venue_ids
        ],
        "source_failures": dict(result.source_failures),
        "skipped_pairs": [list(item) for item in result.skipped_pairs],
        "compared_pair_count": result.compared_pair_count,
        "base_quantity": str(quantity),
        "latency_cost_bps": str(latency_cost_bps) if latency_cost_bps is not None else None,
        "opportunities": [_opportunity_dict(item) for item in result.opportunities],
        "fee_profiles": {
            venue: str(requests[venue].fee_bps) if requests[venue].fee_bps is not None else None
            for venue in result.venue_ids
        },
        "net_profit_claim": False,
        "execution_verified": False,
        "live_execution": False,
        "orders_submitted": False,
        "warning": (
            "Pairwise depth-swept research estimates only. Asset identity is operator-supplied; fee tiers, "
            "latency, regional eligibility, balances, custody/rebalancing, coordinated fills, transfers, "
            "settlement and realized PnL are not independently verified. Missing required costs leave "
            "net estimates unset. No order or external financial action is performed."
        ),
    }
