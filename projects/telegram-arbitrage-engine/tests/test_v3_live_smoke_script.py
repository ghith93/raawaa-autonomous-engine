from __future__ import annotations

import ast
from pathlib import Path
import sys

import pytest


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
    assert "RpcTransportError" in source
    assert '"https://eth.nodely.dev"' in source
    assert "MAX_TOTAL_RPC_REQUESTS = 512" in source
    assert "remaining_budget = MAX_TOTAL_RPC_REQUESTS - requests_used" in source
    assert "rpc_endpoint" in source

def test_v3_cli_help_exposes_absolute_and_relative_tick_limits(monkeypatch, capsys) -> None:
    from arb_engine.cli import main

    monkeypatch.setattr(sys, "argv", ["arb-engine", "v3-sim", "--help"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "--limit-tick" in output
    assert "--limit-ticks" in output


def test_v3_cli_rejects_both_limit_modes_before_network_access(monkeypatch) -> None:
    from arb_engine.cli import main

    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v3-sim",
        "--pool", "0x" + "1" * 40,
        "--direction", "token0_to_token1",
        "--amount-specified", "1000",
        "--limit-tick", "-120",
        "--limit-ticks", "120",
    ])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
