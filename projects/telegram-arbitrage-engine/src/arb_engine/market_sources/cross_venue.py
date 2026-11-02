from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping

from .instruments import InstrumentMetadata
from ..opportunity_costs import (
    OpportunityCosts,
    OpportunityEvaluation,
    OpportunityEvaluationError,
    evaluate_two_leg_spread,
)


class InstrumentMappingError(ValueError):
    """A venue instrument has no explicit, trusted canonical asset mapping."""


@dataclass(frozen=True)
class InstrumentSpec:
    venue_id: str
    instrument_id: str
    base_asset_id: str
    quote_currency_id: str
    base_step_size: Decimal | str
    market_family: str = "cex_spot"
    min_base_quantity: Decimal | str | None = None
    min_quote_notional: Decimal | str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.venue_id, "venue_id"),
            (self.instrument_id, "instrument_id"),
            (self.base_asset_id, "base_asset_id"),
            (self.quote_currency_id, "quote_currency_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise InstrumentMappingError(f"{label} is required")
        if self.market_family != "cex_spot":
            raise InstrumentMappingError("only cex_spot instruments are supported by this scanner")
        step = _positive_decimal(self.base_step_size, "base_step_size")
        object.__setattr__(self, "base_step_size", step)
        if self.min_base_quantity is not None:
            object.__setattr__(
                self, "min_base_quantity",
                _positive_decimal(self.min_base_quantity, "min_base_quantity"),
            )
        if self.min_quote_notional is not None:
            object.__setattr__(
                self, "min_quote_notional",
                _positive_decimal(self.min_quote_notional, "min_quote_notional"),
            )


class InstrumentRegistry:
    """Explicit venue/instrument -> canonical asset mapping; never joins on symbol alone."""

    def __init__(self, instruments: tuple[InstrumentSpec, ...] | list[InstrumentSpec] = ()) -> None:
        self._instruments: dict[tuple[str, str], InstrumentSpec] = {}
        for instrument in instruments:
            self.register(instrument)

    @classmethod
    def from_metadata(
        cls,
        metadata: tuple[InstrumentMetadata, ...] | list[InstrumentMetadata],
        canonical_asset_ids: Mapping[tuple[str, str], str],
    ) -> "InstrumentRegistry":
        """Build explicit venue instrument mappings from public metadata plus caller-approved asset IDs."""
        if not isinstance(metadata, (tuple, list)):
            raise InstrumentMappingError("metadata must be a list or tuple")
        if not isinstance(canonical_asset_ids, Mapping):
            raise InstrumentMappingError("canonical_asset_ids must be a mapping")
        registry = cls()
        for item in metadata:
            if not isinstance(item, InstrumentMetadata):
                raise InstrumentMappingError("metadata entries must be InstrumentMetadata")
            if item.status.upper() != "TRADING":
                continue
            base_id = canonical_asset_ids.get((item.venue_id, item.base_asset_code))
            quote_id = canonical_asset_ids.get((item.venue_id, item.quote_asset_code))
            if not isinstance(base_id, str) or not base_id.strip():
                raise InstrumentMappingError(
                    f"missing explicit canonical mapping for {item.venue_id}:{item.base_asset_code}"
                )
            if not isinstance(quote_id, str) or not quote_id.strip():
                raise InstrumentMappingError(
                    f"missing explicit canonical mapping for {item.venue_id}:{item.quote_asset_code}"
                )
            registry.register(InstrumentSpec(
                venue_id=item.venue_id,
                instrument_id=item.instrument_id,
                base_asset_id=base_id,
                quote_currency_id=quote_id,
                base_step_size=item.base_step_size,
                market_family=item.market_family,
                min_base_quantity=item.min_base_quantity,
                min_quote_notional=item.min_quote_notional,
            ))
        return registry

    def register(self, instrument: InstrumentSpec) -> None:
        if not isinstance(instrument, InstrumentSpec):
            raise InstrumentMappingError("instrument must be an InstrumentSpec")
        key = (instrument.venue_id, instrument.instrument_id)
        if key in self._instruments:
            raise InstrumentMappingError(f"duplicate instrument mapping: {key[0]}:{key[1]}")
        self._instruments[key] = instrument

    def resolve(self, venue_id: str, instrument_id: str) -> InstrumentSpec:
        try:
            return self._instruments[(venue_id, instrument_id)]
        except KeyError as exc:
            raise InstrumentMappingError(
                f"no explicit canonical mapping for {venue_id}:{instrument_id}"
            ) from exc


@dataclass(frozen=True)
class CrossVenueOpportunity:
    status: str
    buy_venue_id: str
    sell_venue_id: str
    base_asset_id: str
    quote_currency_id: str
    requested_base_quantity: Decimal
    buy_filled_base_quantity: Decimal
    sell_filled_base_quantity: Decimal
    buy_fill_fraction: Decimal
    sell_fill_fraction: Decimal
    buy_vwap: Decimal | None
    sell_vwap: Decimal | None
    buy_quote_spent: Decimal
    sell_quote_received: Decimal
    gross_profit_quote: Decimal | None
    evaluation: OpportunityEvaluation | None


def _positive_decimal(value: Decimal | str, field: str) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, (Decimal, str)):
        raise InstrumentMappingError(f"{field} must be Decimal or a decimal string")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as exc:
        raise InstrumentMappingError(f"invalid {field}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise InstrumentMappingError(f"invalid {field}")
    return parsed


def _aware_timestamp(book: object) -> datetime:
    fetched_at = getattr(book, "fetched_at", None)
    if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise InstrumentMappingError("order book must carry a timezone-aware local fetched_at timestamp")
    return fetched_at


def _validate_book(book: object, spec: InstrumentSpec) -> datetime:
    if getattr(book, "source", None) != spec.venue_id:
        raise InstrumentMappingError("order-book source does not match instrument venue")
    if getattr(book, "symbol", None) != spec.instrument_id:
        raise InstrumentMappingError("order-book symbol does not match explicit instrument mapping")
    category = getattr(book, "category", "spot")
    if category != "spot":
        raise InstrumentMappingError("only spot order books are supported")
    return _aware_timestamp(book)


def _sweep(
    levels: tuple[tuple[Decimal, Decimal], ...],
    requested_quantity: Decimal,
) -> tuple[Decimal, Decimal, Decimal | None, Decimal]:
    remaining = requested_quantity
    filled = Decimal("0")
    quote_total = Decimal("0")
    with localcontext() as ctx:
        ctx.prec = 50
        for price, available in levels:
            take = min(remaining, available)
            if take <= 0:
                continue
            filled += take
            quote_total += take * price
            remaining -= take
            if remaining == 0:
                break
        vwap = quote_total / filled if filled > 0 else None
        fraction = filled / requested_quantity
    return filled, quote_total, vwap, fraction


def _quantity_matches_step(quantity: Decimal, step: Decimal) -> bool:
    units = quantity / step
    return units == units.to_integral_value()


def _direction(
    buy_book: object,
    buy_spec: InstrumentSpec,
    buy_fetched_at: datetime,
    sell_book: object,
    sell_spec: InstrumentSpec,
    sell_fetched_at: datetime,
    quantity: Decimal,
    costs: OpportunityCosts,
    now: datetime | None,
    max_quote_age_seconds: int,
    max_quote_skew_seconds: int,
) -> CrossVenueOpportunity:
    buy_filled, buy_spent, buy_vwap, buy_fraction = _sweep(buy_book.asks, quantity)
    sell_filled, sell_received, sell_vwap, sell_fraction = _sweep(sell_book.bids, quantity)
    if buy_filled < quantity or sell_filled < quantity:
        return CrossVenueOpportunity(
            status="insufficient_depth",
            buy_venue_id=buy_spec.venue_id,
            sell_venue_id=sell_spec.venue_id,
            base_asset_id=buy_spec.base_asset_id,
            quote_currency_id=buy_spec.quote_currency_id,
            requested_base_quantity=quantity,
            buy_filled_base_quantity=buy_filled,
            sell_filled_base_quantity=sell_filled,
            buy_fill_fraction=buy_fraction,
            sell_fill_fraction=sell_fraction,
            buy_vwap=buy_vwap,
            sell_vwap=sell_vwap,
            buy_quote_spent=buy_spent,
            sell_quote_received=sell_received,
            gross_profit_quote=None,
            evaluation=None,
        )
    assert buy_vwap is not None and sell_vwap is not None
    minimum_status = None
    if (
        (buy_spec.min_base_quantity is not None and quantity < buy_spec.min_base_quantity)
        or (sell_spec.min_base_quantity is not None and quantity < sell_spec.min_base_quantity)
    ):
        minimum_status = "below_minimum_order_quantity"
    elif (
        (buy_spec.min_quote_notional is not None and buy_spent < buy_spec.min_quote_notional)
        or (sell_spec.min_quote_notional is not None and sell_received < sell_spec.min_quote_notional)
    ):
        minimum_status = "below_minimum_order_notional"
    if minimum_status is not None:
        return CrossVenueOpportunity(
            status=minimum_status,
            buy_venue_id=buy_spec.venue_id,
            sell_venue_id=sell_spec.venue_id,
            base_asset_id=buy_spec.base_asset_id,
            quote_currency_id=buy_spec.quote_currency_id,
            requested_base_quantity=quantity,
            buy_filled_base_quantity=buy_filled,
            sell_filled_base_quantity=sell_filled,
            buy_fill_fraction=buy_fraction,
            sell_fill_fraction=sell_fraction,
            buy_vwap=buy_vwap,
            sell_vwap=sell_vwap,
            buy_quote_spent=buy_spent,
            sell_quote_received=sell_received,
            gross_profit_quote=sell_received - buy_spent,
            evaluation=None,
        )
    evaluation = evaluate_two_leg_spread(
        buy_price_quote_per_base=buy_vwap,
        sell_price_quote_per_base=sell_vwap,
        notional_quote=buy_spent,
        gross_profit_quote_override=sell_received - buy_spent,
        base_asset_id_buy=buy_spec.base_asset_id,
        base_asset_id_sell=sell_spec.base_asset_id,
        quote_currency_id_buy=buy_spec.quote_currency_id,
        quote_currency_id_sell=sell_spec.quote_currency_id,
        buy_market_family="cex_spot",
        sell_market_family="cex_spot",
        buy_fetched_at=buy_fetched_at,
        sell_fetched_at=sell_fetched_at,
        costs=costs,
        now=now,
        max_quote_age_seconds=max_quote_age_seconds,
        max_quote_skew_seconds=max_quote_skew_seconds,
        buy_fill_fraction=buy_fraction,
        sell_fill_fraction=sell_fraction,
    )
    return CrossVenueOpportunity(
        status=evaluation.status,
        buy_venue_id=buy_spec.venue_id,
        sell_venue_id=sell_spec.venue_id,
        base_asset_id=buy_spec.base_asset_id,
        quote_currency_id=buy_spec.quote_currency_id,
        requested_base_quantity=quantity,
        buy_filled_base_quantity=buy_filled,
        sell_filled_base_quantity=sell_filled,
        buy_fill_fraction=buy_fraction,
        sell_fill_fraction=sell_fraction,
        buy_vwap=buy_vwap,
        sell_vwap=sell_vwap,
        buy_quote_spent=buy_spent,
        sell_quote_received=sell_received,
        gross_profit_quote=evaluation.gross_profit_quote,
        evaluation=evaluation,
    )


def compare_cex_spot_orderbooks(
    *,
    book_a: object,
    book_b: object,
    registry: InstrumentRegistry,
    base_quantity: Decimal | str,
    costs_by_direction: Mapping[tuple[str, str], OpportunityCosts] | None = None,
    now: datetime | None = None,
    max_quote_age_seconds: int = 5,
    max_quote_skew_seconds: int = 2,
) -> tuple[CrossVenueOpportunity, CrossVenueOpportunity]:
    """Sweep both books at a fixed base quantity and evaluate both directions.

    A caller must explicitly register both venue instruments to the same canonical
    base/quote IDs. A matching ticker symbol alone is never sufficient.
    """
    if not isinstance(registry, InstrumentRegistry):
        raise InstrumentMappingError("registry must be an InstrumentRegistry")
    if type(max_quote_skew_seconds) is not int or not 0 <= max_quote_skew_seconds <= 3600:
        raise InstrumentMappingError("max_quote_skew_seconds must be between 0 and 3600")
    quantity = _positive_decimal(base_quantity, "base_quantity")
    venue_a = getattr(book_a, "source", None)
    instrument_a = getattr(book_a, "symbol", None)
    venue_b = getattr(book_b, "source", None)
    instrument_b = getattr(book_b, "symbol", None)
    spec_a = registry.resolve(venue_a, instrument_a)
    spec_b = registry.resolve(venue_b, instrument_b)
    time_a = _validate_book(book_a, spec_a)
    time_b = _validate_book(book_b, spec_b)
    if not _quantity_matches_step(quantity, spec_a.base_step_size):
        raise InstrumentMappingError("base_quantity does not match venue A quantity step")
    if not _quantity_matches_step(quantity, spec_b.base_step_size):
        raise InstrumentMappingError("base_quantity does not match venue B quantity step")

    if costs_by_direction is None:
        costs_by_direction = {}
    if not isinstance(costs_by_direction, Mapping):
        raise OpportunityEvaluationError("costs_by_direction must be a mapping")
    if (
        spec_a.base_asset_id != spec_b.base_asset_id
        or spec_a.quote_currency_id != spec_b.quote_currency_id
    ):
        return tuple(
            CrossVenueOpportunity(
                status="asset_or_quote_identity_mismatch",
                buy_venue_id=buy_spec.venue_id,
                sell_venue_id=sell_spec.venue_id,
                base_asset_id=buy_spec.base_asset_id,
                quote_currency_id=buy_spec.quote_currency_id,
                requested_base_quantity=quantity,
                buy_filled_base_quantity=Decimal("0"),
                sell_filled_base_quantity=Decimal("0"),
                buy_fill_fraction=Decimal("0"),
                sell_fill_fraction=Decimal("0"),
                buy_vwap=None,
                sell_vwap=None,
                buy_quote_spent=Decimal("0"),
                sell_quote_received=Decimal("0"),
                gross_profit_quote=None,
                evaluation=None,
            )
            for buy_spec, sell_spec in ((spec_a, spec_b), (spec_b, spec_a))
        )

    def costs_for(buy: InstrumentSpec, sell: InstrumentSpec) -> OpportunityCosts:
        return costs_by_direction.get(
            (buy.venue_id, sell.venue_id),
            OpportunityCosts(),
        )

    return (
        _direction(book_a, spec_a, time_a, book_b, spec_b, time_b, quantity,
                   costs_for(spec_a, spec_b), now, max_quote_age_seconds, max_quote_skew_seconds),
        _direction(book_b, spec_b, time_b, book_a, spec_a, time_a, quantity,
                   costs_for(spec_b, spec_a), now, max_quote_age_seconds, max_quote_skew_seconds),
    )


@dataclass(frozen=True)
class CrossVenueUniverseResult:
    """Bounded pairwise comparison result across multiple independent CEX sources."""

    venue_ids: tuple[str, ...]
    opportunities: tuple[CrossVenueOpportunity, ...]
    skipped_pairs: tuple[tuple[str, str, str], ...]
    source_failures: tuple[tuple[str, str], ...]

    @property
    def status(self) -> str:
        if len(self.venue_ids) < 2:
            return "insufficient_sources"
        if self.skipped_pairs or self.source_failures:
            return "partial"
        return "complete"

    @property
    def compared_pair_count(self) -> int:
        return len(self.opportunities) // 2


def compare_cex_spot_orderbook_universe(
    *,
    books: tuple[object, ...] | list[object],
    registry: InstrumentRegistry,
    base_quantity: Decimal | str,
    costs_by_direction: Mapping[tuple[str, str], OpportunityCosts] | None = None,
    source_failures: Mapping[str, str] | None = None,
    now: datetime | None = None,
    max_quote_age_seconds: int = 5,
    max_quote_skew_seconds: int = 2,
    max_venues: int = 8,
) -> CrossVenueUniverseResult:
    """Compare all bounded CEX spot venue pairs, preserving partial source failures.

    Each available book must have an explicit venue/instrument mapping. A canonical
    base/quote mismatch is recorded as a skipped pair; it is never joined by symbol.
    The caller may pass sanitized source failure codes to preserve partial coverage.
    """
    if not isinstance(registry, InstrumentRegistry):
        raise InstrumentMappingError("registry must be an InstrumentRegistry")
    if not isinstance(books, (tuple, list)):
        raise InstrumentMappingError("books must be a list or tuple")
    if (
        isinstance(max_venues, bool) or not isinstance(max_venues, int)
        or not 2 <= max_venues <= 8
    ):
        raise InstrumentMappingError("max_venues must be between 2 and 8")
    if len(books) > max_venues:
        raise InstrumentMappingError(f"book count exceeds the {max_venues}-venue comparison bound")
    quantity = _positive_decimal(base_quantity, "base_quantity")
    if costs_by_direction is None:
        costs_by_direction = {}
    if not isinstance(costs_by_direction, Mapping):
        raise OpportunityEvaluationError("costs_by_direction must be a mapping")

    if source_failures is None:
        source_failures = {}
    if not isinstance(source_failures, Mapping):
        raise InstrumentMappingError("source_failures must be a mapping")
    normalized_failures: list[tuple[str, str]] = []
    for venue_id, reason in source_failures.items():
        if (
            not isinstance(venue_id, str) or not venue_id.strip()
            or not isinstance(reason, str) or not reason.strip()
            or len(reason) > 120 or "\n" in reason or "\r" in reason
        ):
            raise InstrumentMappingError("source failure entries must be bounded venue IDs and reason codes")
        normalized_failures.append((venue_id, reason))
    if len(normalized_failures) > max_venues:
        raise InstrumentMappingError("source failure count exceeds the venue comparison bound")

    venue_ids: list[str] = []
    for book in books:
        venue_id = getattr(book, "source", None)
        instrument_id = getattr(book, "symbol", None)
        if not isinstance(venue_id, str) or not venue_id:
            raise InstrumentMappingError("each order book must identify its source venue")
        if venue_id in venue_ids:
            raise InstrumentMappingError(f"duplicate order-book source venue: {venue_id}")
        if any(failed_id == venue_id for failed_id, _ in normalized_failures):
            raise InstrumentMappingError(f"venue {venue_id} cannot be both available and failed")
        spec = registry.resolve(venue_id, instrument_id)
        _validate_book(book, spec)
        venue_ids.append(venue_id)

    if len(venue_ids) + len(normalized_failures) > max_venues:
        raise InstrumentMappingError("total venue count exceeds the comparison bound")

    opportunities: list[CrossVenueOpportunity] = []
    skipped_pairs: list[tuple[str, str, str]] = []
    for index, book_a in enumerate(books):
        for book_b in books[index + 1:]:
            pair_results = compare_cex_spot_orderbooks(
                book_a=book_a,
                book_b=book_b,
                registry=registry,
                base_quantity=quantity,
                costs_by_direction=costs_by_direction,
                now=now,
                max_quote_age_seconds=max_quote_age_seconds,
                max_quote_skew_seconds=max_quote_skew_seconds,
            )
            if all(item.status == "asset_or_quote_identity_mismatch" for item in pair_results):
                skipped_pairs.append((
                    pair_results[0].buy_venue_id,
                    pair_results[0].sell_venue_id,
                    "canonical_base_or_quote_identity_mismatch",
                ))
            else:
                opportunities.extend(pair_results)

    return CrossVenueUniverseResult(
        venue_ids=tuple(venue_ids),
        opportunities=tuple(opportunities),
        skipped_pairs=tuple(skipped_pairs),
        source_failures=tuple(sorted(normalized_failures)),
    )
