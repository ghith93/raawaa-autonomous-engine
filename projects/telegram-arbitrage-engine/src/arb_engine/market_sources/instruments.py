from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation


class InstrumentMetadataError(ValueError):
    """Invalid or incomplete public exchange instrument metadata."""


def _decimal(value: Decimal | str, field: str, *, optional: bool = False) -> Decimal | None:
    if optional and value is None:
        return None
    if isinstance(value, (bool, float)) or not isinstance(value, (Decimal, str)):
        raise InstrumentMetadataError(f"{field} must be Decimal or a decimal string")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as exc:
        raise InstrumentMetadataError(f"invalid {field}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise InstrumentMetadataError(f"invalid {field}")
    return parsed


@dataclass(frozen=True)
class InstrumentMetadata:
    venue_id: str
    instrument_id: str
    market_family: str
    base_asset_code: str
    quote_asset_code: str
    base_step_size: Decimal | str
    price_tick_size: Decimal | str
    status: str
    fetched_at: datetime
    min_base_quantity: Decimal | str | None = None
    min_quote_notional: Decimal | str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.venue_id, "venue_id"),
            (self.instrument_id, "instrument_id"),
            (self.market_family, "market_family"),
            (self.base_asset_code, "base_asset_code"),
            (self.quote_asset_code, "quote_asset_code"),
            (self.status, "status"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise InstrumentMetadataError(f"{label} is required")
        if self.market_family != "cex_spot":
            raise InstrumentMetadataError("only cex_spot instrument metadata is supported")
        if self.base_asset_code != self.base_asset_code.upper() or self.quote_asset_code != self.quote_asset_code.upper():
            raise InstrumentMetadataError("asset codes must be uppercase")
        if (
            not isinstance(self.fetched_at, datetime)
            or self.fetched_at.tzinfo is None
            or self.fetched_at.utcoffset() is None
        ):
            raise InstrumentMetadataError("fetched_at must be timezone-aware")
        object.__setattr__(self, "base_step_size", _decimal(self.base_step_size, "base_step_size"))
        object.__setattr__(self, "price_tick_size", _decimal(self.price_tick_size, "price_tick_size"))
        object.__setattr__(
            self, "min_base_quantity",
            _decimal(self.min_base_quantity, "min_base_quantity", optional=True),
        )
        object.__setattr__(
            self, "min_quote_notional",
            _decimal(self.min_quote_notional, "min_quote_notional", optional=True),
        )
