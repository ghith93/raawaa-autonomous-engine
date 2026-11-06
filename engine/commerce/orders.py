"""Order state machine for a cash-on-delivery store.

Two rules that exist because of how COD actually fails:

1. `requested` is not a sale. A form submission with no payment rail behind it is
   an expression of interest. Counting it as revenue is how founders talk
   themselves into buying more stock than they can sell.
2. Every transition is recorded with a timestamp and a reason, because the only
   dispute resolution available in a COD market is your own log.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .money import Money

STATES = ("requested", "confirmed", "prepared", "shipped", "delivered", "refused", "cancelled")
TERMINAL = {"delivered", "refused", "cancelled"}

ALLOWED: dict[str, set[str]] = {
    "requested": {"confirmed", "cancelled"},
    "confirmed": {"prepared", "cancelled"},
    "prepared": {"shipped", "cancelled"},
    "shipped": {"delivered", "refused"},
    "delivered": set(),
    "refused": set(),
    "cancelled": set(),
}

# Revenue is recognised only here. Everything upstream is pipeline.
REVENUE_STATES = {"delivered"}


class OrderError(ValueError):
    pass


class TransitionError(OrderError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_EXTENDED_ARABIC = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def normalise_phone(raw: str, country_code: str = "963") -> str:
    """Return a canonical local number, or raise with the reason it cannot be one.

    Accepts Arabic-Indic digits, spaces, dashes, parentheses, and +963 / 00963
    prefixes. Rejects anything that is not a plausible mobile line, because a bad
    number is only discovered at the door, where it costs a wasted shipment.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise OrderError("phone is empty")
    text = raw.translate(_ARABIC_INDIC).translate(_EXTENDED_ARABIC)
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(r"[\s\-()\.]", "", text)
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("00"):
        text = text[2:]
    if text.startswith(country_code):
        text = text[len(country_code):]
    if text.startswith("0"):
        text = text[1:]
    if not text.isdigit():
        raise OrderError("phone contains non-digit characters")
    if len(text) != 9:
        raise OrderError(f"local number must be 9 digits, got {len(text)}")
    if not text[0] in "345689":
        raise OrderError("not a recognised mobile prefix")
    return "0" + text


def idempotency_key(phone: str, sku: str, day: str | None = None) -> str:
    """Two taps are one order. Scoped per day so a repeat purchase still works."""
    stamp = day or utc_now().strftime("%Y-%m-%d")
    payload = f"{normalise_phone(phone)}|{sku.strip().lower()}|{stamp}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class OrderLine:
    sku: str
    quantity: int
    unit_price: Money

    def total(self) -> Money:
        return self.unit_price * self.quantity


@dataclass
class Order:
    order_id: str
    customer_phone: str
    city: str
    lines: list[OrderLine]
    delivery_fee: Money
    state: str = "requested"
    created_at: datetime = field(default_factory=utc_now)
    history: list[dict] = field(default_factory=list)
    cod_collected: Money | None = None

    def __post_init__(self) -> None:
        self.customer_phone = normalise_phone(self.customer_phone)
        if self.state not in STATES:
            raise OrderError(f"unknown state: {self.state}")
        if not self.lines:
            raise OrderError("an order needs at least one line")
        self.history.append({"state": self.state, "at": self.created_at.isoformat(), "reason": "created"})

    def subtotal(self) -> Money:
        if not self.lines:
            return Money.zero(self.delivery_fee.currency)
        running = Money.zero(self.lines[0].unit_price.currency)
        for line in self.lines:
            running = running + line.total()
        return running

    def total(self) -> Money:
        return self.subtotal() + self.delivery_fee

    def transition(self, new_state: str, reason: str = "") -> None:
        if new_state not in STATES:
            raise TransitionError(f"unknown state: {new_state}")
        if new_state not in ALLOWED[self.state]:
            raise TransitionError(f"cannot go from {self.state} to {new_state}")
        if new_state in {"refused", "cancelled"} and not reason.strip():
            raise TransitionError("a refusal or cancellation needs a recorded reason")
        self.state = new_state
        self.history.append({"state": new_state, "at": utc_now().isoformat(), "reason": reason})

    def is_revenue(self) -> bool:
        """Recognise delivered COD only when cash matches the total and currency."""
        return (self.state in REVENUE_STATES and self.cod_collected is not None
                and self.cod_collected == self.total())


@dataclass
class OrderBook:
    """In-memory store with the funnel metrics that matter."""

    orders: dict[str, Order] = field(default_factory=dict)
    seen_keys: set[str] = field(default_factory=set)

    def submit(self, order: Order, key: str) -> bool:
        """Return False when this is a duplicate submission, not an error."""
        if key in self.seen_keys:
            return False
        if order.order_id in self.orders:
            raise OrderError(f"duplicate order id: {order.order_id}")
        self.seen_keys.add(key)
        self.orders[order.order_id] = order
        return True

    def by_state(self, state: str) -> list[Order]:
        return [o for o in self.orders.values() if o.state == state]

    def funnel(self) -> dict:
        counts = {state: 0 for state in STATES}
        for order in self.orders.values():
            counts[order.state] += 1
        revenue = Money.zero("USD")
        for order in self.orders.values():
            if order.is_revenue():
                revenue = revenue + order.total()
        submitted = len(self.orders)
        delivered = counts["delivered"]
        refused = counts["refused"]
        resolved = delivered + refused
        return {
            "counts": counts,
            "submitted": submitted,
            "revenue_recognised": revenue.major,
            "revenue_currency": revenue.currency,
            "cancel_rate": None if not submitted else counts["cancelled"] / submitted,
            "refusal_rate": None if not resolved else refused / resolved,
            "fulfilment_rate": None if not submitted else delivered / submitted,
            "note": "revenue requires delivery and matching COD cash settlement",
        }

    def unreconciled(self) -> list[str]:
        """Delivered orders whose cash does not match the expected total."""
        return [
            o.order_id for o in self.orders.values()
            if o.state == "delivered" and not o.is_revenue()
        ]
