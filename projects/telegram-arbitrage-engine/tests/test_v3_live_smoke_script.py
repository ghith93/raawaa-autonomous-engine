from __future__ import annotations

import ast
from pathlib import Path


def test_live_v3_smoke_script_parses_and_contains_no_transaction_submission() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    script = repository_root / "scripts" / "v3_live_readonly_smoke.py"
    source = script.read_text(encoding="utf-8")
    ast.parse(source, filename=str(script))
    assert "eth_sendTransaction" not in source
    assert "eth_sendRawTransaction" not in source
    assert "live_execution" in source
    assert "transaction_submitted" in source
    assert "re.fullmatch" in source
    assert "250_002" in source
