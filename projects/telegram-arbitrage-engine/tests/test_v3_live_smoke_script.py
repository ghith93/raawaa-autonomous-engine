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
    assert "DEFAULT_V3_ETHEREUM_RPC" in source
    assert '"https://eth.drpc.org"' in source
    assert '"https://public.1rpc.io/eth"' in source
    assert "MAX_TOTAL_RPC_REQUESTS = 512" in source
    assert "remaining_budget = MAX_TOTAL_RPC_REQUESTS - requests_used" in source
    assert "DEFAULT_V3_ETHEREUM_RPC" in source
    assert "min_request_interval=0.55" in source
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


def test_live_smoke_retries_next_provider_with_one_shared_request_budget(monkeypatch, capsys) -> None:
    import importlib.util

    from arb_engine.rpc import RpcTransportError

    repository_root = Path(__file__).resolve().parents[3]
    script = repository_root / "scripts" / "v3_live_readonly_smoke.py"
    spec = importlib.util.spec_from_file_location("v3_live_readonly_smoke_tested", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    attempts = []

    class FakeReader:
        def __init__(self, endpoint, **kwargs):
            self.endpoint = endpoint
            self.max_rpc_requests = kwargs["max_rpc_requests"]
            self.rpc_request_count = 0
            attempts.append(self)

    def fake_run(reader):
        if reader.endpoint in module.RPC_ENDPOINTS[:3]:
            reader.rpc_request_count = 3
            status = 403 if reader.endpoint == module.RPC_ENDPOINTS[0] else 429
            raise RpcTransportError(f"EVM RPC HTTP error: {status}")
        reader.rpc_request_count = 7
        return {"status": "success", "rpc_requests_used": 0}

    monkeypatch.setattr(module, "EvmV3PoolReader", FakeReader)
    monkeypatch.setattr(module, "_run_smoke", fake_run)
    module.main()

    assert [attempt.endpoint for attempt in attempts] == list(module.RPC_ENDPOINTS)
    assert [attempt.max_rpc_requests for attempt in attempts] == [512, 509, 506, 503]
    output = __import__("json").loads(capsys.readouterr().out)
    assert output["rpc_endpoint"] == module.RPC_ENDPOINTS[3]
    assert output["rpc_requests_used"] == 16
    assert output["rpc_requests_budget"] == module.MAX_TOTAL_RPC_REQUESTS
