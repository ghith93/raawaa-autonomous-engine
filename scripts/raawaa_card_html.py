#!/usr/bin/env python3
"""Deterministic RTL HTML/CSS social & price card builder for multi-store catalogs.

Why this exists:
Image diffusion models misspell Arabic ligatures, invent digits, and force a
full regeneration whenever a price changes. Rendering authoritative catalog JSON
into deterministic RTL HTML/CSS (HarfBuzz shaping in Chromium/Playwright/Takumi)
guarantees 100% accurate Arabic typography, prices, and claim validation.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.commerce.validation import RISKY_CLAIM_WORDS  # noqa: E402


def render_store_card_html(
    store_data: dict[str, Any],
    sku: str,
    *,
    forbidden_claims: list[str] | None = None,
    aspect: str = "4x5",
) -> str:
    items = store_data.get("items", [])
    item = next((row for row in items if row["sku"] == sku), None)
    if item is None:
        raise ValueError(f"SKU {sku!r} not found in store {store_data.get('store_id')!r}")

    dims = {
        "1x1": (1080, 1080),
        "4x5": (1080, 1350),
        "3x4": (1080, 1440),
        "9x16": (1080, 1920),
    }
    if aspect not in dims:
        raise ValueError(f"Unsupported aspect {aspect!r}; choose from {sorted(dims)}")
    width, height = dims[aspect]

    text_blob = " ".join([
        str(store_data.get("brand_ar", "")),
        str(store_data.get("tagline_ar", "")),
        str(item.get("title_ar", "")),
        str(item.get("subtitle_ar", "")),
        str(store_data.get("inspection_policy_ar", "")),
    ])
    for word, reason in RISKY_CLAIM_WORDS.items():
        if word in text_blob:
            raise ValueError(f"Card text triggered risky regulatory claim {word!r} ({reason})")
    for term in forbidden_claims or []:
        if term and term in text_blob:
            raise ValueError(f"Card text contains forbidden medical/exaggerated claim: {term!r}")

    brand_ar = html.escape(store_data["brand_ar"])
    brand_latin = html.escape(store_data.get("brand_latin", ""))
    tagline = html.escape(store_data.get("tagline_ar", ""))
    title = html.escape(item["title_ar"])
    subtitle = html.escape(item.get("subtitle_ar", ""))
    badge = html.escape(item.get("badge_ar", ""))
    price = f"${float(item['unit_price']):.0f}"
    prep = html.escape(store_data.get("preparation_promise_ar", ""))
    inspect_note = html.escape(store_data.get("inspection_policy_ar", ""))

    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>{brand_ar} — {title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: {width}px;
    height: {height}px;
    background: radial-gradient(circle at 30% 25%, #fcf9f4 0%, #ede2d1 100%);
    color: #221914;
    font-family: "IBM Plex Sans Arabic", "Noto Sans Arabic", Tahoma, sans-serif;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 84px;
  }}
  .header {{ display: flex; justify-content: space-between; align-items: center; }}
  .brand {{ font-size: 46px; font-weight: 800; color: #1d3b2a; }}
  .latin {{ font-size: 24px; letter-spacing: 0.16em; color: #65564c; }}
  .badge {{
    display: inline-block;
    background: #1d3b2a;
    color: #faf7f2;
    padding: 12px 28px;
    border-radius: 999px;
    font-size: 26px;
    font-weight: 700;
    margin-bottom: 28px;
  }}
  h1 {{ font-size: 64px; line-height: 1.25; margin-bottom: 22px; }}
  .sub {{ font-size: 32px; line-height: 1.6; color: #57493f; margin-bottom: 36px; }}
  .price-row {{
    display: flex;
    align-items: center;
    gap: 28px;
    background: #ffffff;
    border: 2px solid #dfd3c1;
    border-radius: 28px;
    padding: 32px 42px;
  }}
  .price {{ font-size: 76px; font-weight: 800; color: #1d3b2a; direction: ltr; }}
  .price-meta {{ font-size: 28px; color: #221914; line-height: 1.5; }}
  .footer {{
    border-top: 2px solid #d9cbb7;
    padding-top: 28px;
    font-size: 25px;
    color: #57493f;
    line-height: 1.6;
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="brand">{brand_ar}</div>
    <div class="latin">{brand_latin}</div>
  </div>
  <div>
    {f'<div class="badge">{badge}</div>' if badge else ''}
    <h1>{title}</h1>
    <p class="sub">{subtitle or tagline}</p>
    <div class="price-row">
      <div class="price">{price}</div>
      <div class="price-meta">
        <strong>شامل التوصيل لباب المنزل مجانًا</strong><br>
        {prep}
      </div>
    </div>
  </div>
  <div class="footer">{inspect_note}</div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="raawaa")
    parser.add_argument("--sku", default="50ml")
    parser.add_argument("--aspect", default="4x5", choices=["1x1", "4x5", "3x4", "9x16"])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    store_path = ROOT / "config" / "stores" / f"{args.store}.json"
    store_data = json.loads(store_path.read_text(encoding="utf-8"))
    brand_file = ROOT / "config" / "raawaa_brand.json"
    forbidden = []
    if args.store == "raawaa" and brand_file.exists():
        forbidden = json.loads(brand_file.read_text(encoding="utf-8")).get("claims", {}).get("forbidden_ar", [])

    rendered = render_store_card_html(store_data, args.sku, forbidden_claims=forbidden, aspect=args.aspect)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(f"OK   wrote deterministic RTL card {out} ({len(rendered)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
