"""Compatibility facade for the shared arbitrage-package D1 ledger client."""
from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ARBITRAGE_SOURCE = _REPOSITORY_ROOT / "projects" / "telegram-arbitrage-engine" / "src"
if _ARBITRAGE_SOURCE.is_dir() and str(_ARBITRAGE_SOURCE) not in sys.path:
    sys.path.insert(0, str(_ARBITRAGE_SOURCE))

_client = import_module("arb_engine.d1_paper_ledger")
D1PaperLedgerError = _client.D1PaperLedgerError
D1PaperTradingLedger = _client.D1PaperTradingLedger

__all__ = ["D1PaperLedgerError", "D1PaperTradingLedger"]
