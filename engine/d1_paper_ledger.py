"""Compatibility facade for the shared arbitrage-package D1 ledger client."""
from __future__ import annotations

import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ARBITRAGE_SOURCE = _REPOSITORY_ROOT / "projects" / "telegram-arbitrage-engine" / "src"
if _ARBITRAGE_SOURCE.is_dir() and str(_ARBITRAGE_SOURCE) not in sys.path:
    sys.path.insert(0, str(_ARBITRAGE_SOURCE))

from arb_engine.d1_paper_ledger import D1PaperLedgerError, D1PaperTradingLedger

__all__ = ["D1PaperLedgerError", "D1PaperTradingLedger"]
