"""Catalogue validation for any product, not just the serum.

A store grows by adding SKUs, and every SKU added without these checks is a
future refund, a customs problem, or a legal one. The checks are grouped by the
question they answer, so a failure tells you what to fix rather than that
something is wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


REQUIRED_FIELDS = ("sku", "name_ar", "category", "price", "currency", "weight_g")
REGULATED_CATEGORIES = {
    "supplement": "ingestible: requires composition, dosage, and origin documentation",
    "medical_device": "requires registration before sale",
    "cosmetic_medical": "borderline claims must be reviewed by a pharmacist",
    "food": "requires production and expiry labelling",
}

# Words that create regulatory exposure when used in marketing claims.
RISKY_CLAIM_WORDS = {
    "يعالج": "treatment claim",
    "يشفي": "cure claim",
    "علاج": "treatment claim",
    "مضمون": "guarantee claim",
    "بديل عن الطبيب": "medical substitution claim",
    "ينبت الشعر": "regrowth claim",
}


@dataclass
class Product:
    sku: str
    name_ar: str
    category: str
    price: float
    currency: str = "USD"
    weight_g: float | None = None
    cost: float | None = None
    images: list[str] = field(default_factory=list)
    label_fields: dict[str, str] = field(default_factory=dict)
    claims_ar: str = ""
    expiry: date | None = None
    origin_country: str | None = None
    hs_code: str | None = None


def validate(product: Product) -> dict[str, list[str]]:
    """Return problems grouped by area. An empty result means publishable."""
    problems: dict[str, list[str]] = {"identity": [], "pricing": [], "media": [],
                                      "regulatory": [], "logistics": []}

    for name in REQUIRED_FIELDS:
        if getattr(product, name, None) in (None, "", []):
            problems["identity"].append(f"missing {name}")
    if product.sku and not re.fullmatch(r"[a-z0-9][a-z0-9\-]{1,31}", product.sku):
        problems["identity"].append("sku must be lowercase alphanumeric with dashes, max 32 chars")

    if product.price is None or product.price <= 0:
        problems["pricing"].append("price must be positive")
    if product.cost is not None:
        if product.cost < 0:
            problems["pricing"].append("cost cannot be negative")
        elif product.price and product.cost >= product.price:
            problems["pricing"].append("cost is at or above price: this SKU loses money on every sale")
        elif product.price and (product.price - product.cost) / product.price < 0.35:
            problems["pricing"].append("gross margin below 35%: delivery and refusals will erase it")

    if not product.images:
        problems["media"].append("no product image")
    else:
        if not any(str(img).lower().endswith((".jpg", ".jpeg", ".png", ".webp")) for img in product.images):
            problems["media"].append("no supported image format found")

    if product.category in REGULATED_CATEGORIES:
        note = REGULATED_CATEGORIES[product.category]
        problems["regulatory"].append(f"{product.category}: {note}")
        if product.category in {"supplement", "food"} and not product.label_fields.get("batch"):
            problems["regulatory"].append("batch number is required on the label")
        if product.category in {"supplement", "food"} and not product.expiry:
            problems["regulatory"].append("expiry date is required before sale")

    for word, reason in RISKY_CLAIM_WORDS.items():
        if word in (product.claims_ar or ""):
            problems["regulatory"].append(f"claim '{word}' is a {reason}; rewrite as a cosmetic effect")

    if product.weight_g is not None and product.weight_g <= 0:
        problems["logistics"].append("weight must be positive for costing")
    if product.origin_country and not product.hs_code:
        problems["logistics"].append("imported goods need an HS code for customs")

    return {k: v for k, v in problems.items() if v}


def is_publishable(product: Product) -> bool:
    """Regulatory notes are warnings, not blockers for non-regulated goods.

    This intentionally returns True only when nothing is flagged, so a regulated
    category is never silently published while a note is outstanding.
    """
    return not validate(product)
