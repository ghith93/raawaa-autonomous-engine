#!/usr/bin/env python3
"""Reject bad product photos before they reach the feed.

Usage:
    python scripts/raawaa_asset_check.py shot1.jpg shot2.png
    python scripts/raawaa_asset_check.py --checklist
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.raawaa_quality import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
