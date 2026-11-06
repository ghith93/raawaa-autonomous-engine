"""Money that cannot silently lose cents.

Every amount is an integer number of minor units (piasters, cents) in a single
currency. Floats are banned from storage and arithmetic because a business that
rounds badly loses money in ways nobody notices until the month closes.

Cash-on-delivery markets have a specific reason to be strict: the money you
count at the end of the day is the only record. If the ledger disagrees with the
cash box, you cannot fix it by re-reading a payment provider's dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

MINOR_PER_MAJOR = 100


class CurrencyMismatch(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Money:
    minor: int
    currency: str = "USD"

    # -- construction -------------------------------------------------------
    @classmethod
    def of(cls, major: float | int | str, currency: str = "USD") -> "Money":
        """Build from a human amount, e.g. Money.of("32") or Money.of(4.5)."""
        if isinstance(major, str):
            major = major.strip().replace(",", "")
        return cls(minor=round(float(major) * MINOR_PER_MAJOR), currency=currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> "Money":
        return cls(0, currency)

    # -- arithmetic ---------------------------------------------------------
    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, factor: float | int) -> "Money":
        return Money(round(self.minor * factor), self.currency)

    def __truediv__(self, divisor: float | int) -> "Money":
        if divisor == 0:
            raise ZeroDivisionError("cannot divide money by zero")
        return Money(round(self.minor / divisor), self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor, self.currency)

    def max(self, other: "Money") -> "Money":
        self._check(other)
        return self if self.minor >= other.minor else other

    # -- presentation -------------------------------------------------------
    @property
    def major(self) -> float:
        return self.minor / MINOR_PER_MAJOR

    def __str__(self) -> str:
        return f"{self.major:.2f} {self.currency}"

    def __repr__(self) -> str:
        return f"Money({self.minor}, {self.currency!r})"


def total(amounts: Iterable[Money], currency: str = "USD") -> Money:
    running = Money.zero(currency)
    for amount in amounts:
        running = running + amount
    return running


def split_share(amount: Money, share: float) -> Money:
    """Apply a proportion to a monetary amount, rounding half-up at minor units."""
    return Money(round(amount.minor * share), amount.currency)


def safe_div(numerator: float, denominator: float) -> float | None:
    """Return None rather than infinity; a ratio with no denominator is unknown."""
    if denominator == 0:
        return None
    return numerator / denominator
