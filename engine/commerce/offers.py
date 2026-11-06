"""Offer validation: a promotion is allowed only if it is literally true.

The rule that makes this module worth having is that scarcity must be backed by
a mechanism, not by wording. A countdown that resets is a lie even if the copy is
true at the instant it is read, because the condition is not actually enforced.

Each claim therefore declares an `enforcement` kind, and the validator refuses
any scarcity claim without one. This is the cheapest possible defence against
the single most common way a small brand loses its account and its reputation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .money import Money


class OfferError(ValueError):
    pass


# Phrases that are false by construction in small-store usage.
FORBIDDEN_PATTERNS = [
    (r"آخر\s*(قطعة|حبة|عبوة)", "last item — always false once restocked"),
    (r"ينتهي\s*(بعد|خلال)\s*\d+", "resetting countdown"),
    (r"خصم\s*[7-9]\d\s*٪", "implausible discount; a real margin cannot support it"),
    (r"\d{2,}\s*(عميلة|زبون|شخص)\s*(اشتر|طلب)", "unverifiable social proof"),
    (r"لفترة\s*محدودة", "vague deadline with no enforcement"),
]

ENFORCEMENT_KINDS = {
    "counter",        # a counted number in storage, decremented on use
    "date_range",     # a real start and end date that is honoured
    "price_change",   # the price actually changes when the condition is met
    "quota",          # a fixed list of slots that closes
    "none_needed",    # not a scarcity claim at all (e.g. a guarantee)
}


@dataclass
class ScarcityClaim:
    id: str
    claim_ar: str
    enforcement: str
    limit: int | None = None
    enforced_value: int | None = None   # how many have actually been used
    starts: date | None = None
    ends: date | None = None

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.enforcement not in ENFORCEMENT_KINDS:
            problems.append(f"unknown enforcement kind: {self.enforcement!r}")
        if self.enforcement in {"counter", "quota"}:
            if self.limit is None:
                problems.append("counter/quota claim needs a numeric limit")
            if self.enforced_value is None:
                problems.append("counter/quota claim needs the real used count")
            elif self.limit is not None and 0 <= self.limit <= self.enforced_value:
                problems.append("claim is already exhausted; wording would be false")
        if self.enforcement == "date_range":
            if not self.starts or not self.ends:
                problems.append("date_range claim needs both dates")
            elif self.ends < self.starts:
                problems.append("end date precedes start date")
            elif self.ends < date.today():
                problems.append("promotion window has passed; copy is stale")
        if self.enforcement == "price_change":
            # A price change is only honest if a different price is defined.
            if not self.claim_ar.strip():
                problems.append("price_change claim needs copy")
        return problems


@dataclass
class Offer:
    id: str
    name_ar: str
    bundle_price: Money
    items_count: int
    list_price: Money
    delivery_included: bool = True
    valid_until: date | None = None
    scarcity: list[ScarcityClaim] = field(default_factory=list)

    def savings(self) -> Money:
        return self.list_price - self.bundle_price

    def discount_pct(self) -> float:
        if self.list_price.minor <= 0:
            return 0.0
        return self.savings().minor / self.list_price.minor * 100

    def validate(self, max_honest_discount_pct: float = 35.0) -> list[str]:
        problems: list[str] = []
        if self.bundle_price.minor <= 0:
            problems.append("bundle price must be positive")
        if self.list_price.minor <= 0:
            problems.append("list price must be positive")
        if self.bundle_price.minor > self.list_price.minor:
            problems.append("bundle is more expensive than buying separately")
        if self.discount_pct() > max_honest_discount_pct:
            problems.append(
                f"discount {self.discount_pct():.1f}% exceeds the honest ceiling "
                f"{max_honest_discount_pct}% for a small store"
            )
        for claim in self.scarcity:
            problems.extend(f"{claim.id}: {p}" for p in claim.validate())
        return problems


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def is_forbidden(copy: str) -> list[str]:
    """Return the reasons a piece of copy is a banned scarcity claim.

    Arabic-Indic numerals are normalised first. Without that, "خصم ٧٠٪" would pass
    the check written for "خصم 70٪", which is the exact shape of a real Arabic ad.
    """
    normalised = copy.translate(_DIGITS)
    hits = []
    for pattern, reason in FORBIDDEN_PATTERNS:
        if re.search(pattern, normalised):
            hits.append(reason)
    return hits


def copy_is_honest(copy: str, offer: Offer | None = None) -> tuple[bool, list[str]]:
    reasons = is_forbidden(copy)
    if offer is not None:
        reasons.extend(offer.validate())
    return (not reasons), reasons
