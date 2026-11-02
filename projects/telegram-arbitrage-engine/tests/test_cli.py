import json
import sys
from datetime import datetime, timezone

from arb_engine import cli
from arb_engine.rpc import EvmPoolSnapshot
from arb_engine.v2 import V2PoolState


QUOTE = "0x" + "1" * 40
BASE = "0x" + "2" * 40
HASH = "0x" + "a" * 64
POOL_A = "0x" + "3" * 40
POOL_B = "0x" + "4" * 40


def snapshot(address, reserve0, reserve1, fee):
    return EvmPoolSnapshot(
        state=V2PoolState(
            chain_id=1, pair_address=address, token0=QUOTE, token1=BASE,
            reserve0_raw=reserve0, reserve1_raw=reserve1, fee_bps=fee,
            block_number=123, block_hash=HASH, observed_at=datetime.now(timezone.utc),
        ),
        token0_decimals=6, token1_decimals=18, block_timestamp=1_700_000_000,
    )


def test_v2_sim_cli_converts_human_amount_to_raw_units(monkeypatch, capsys):
    class FakeReader:
        def __init__(self, endpoint):
            assert endpoint == "https://rpc.example"

        def read_pools(self, pools):
            assert pools == [(POOL_A, 30), (POOL_B, 25)]
            return [
                snapshot(POOL_A, 1_000_000_000_000, 10**24, 30),
                snapshot(POOL_B, 1_000_000_000_000, 10**24, 25),
            ]

    monkeypatch.setattr(cli, "EthereumV2PoolReader", FakeReader)
    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50", "--rpc-url", "https://rpc.example",
    ])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "read_only_same_block_v2_reserve_simulation"
    assert result["capital_quote"] == "50"
    assert result["block_number"] == 123
    assert result["live_execution"] is False
    assert "Excludes gas" in result["warning"]


def test_v2_sim_cli_router_validation_is_read_only(monkeypatch, capsys):
    class FakeReader:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def read_pools(self, pools):
            return [
                snapshot(POOL_A, 1_000_000_000_000, 10**24, 30),
                snapshot(POOL_B, 1_000_000_000_000, 10**24, 25),
            ]

        def quote_v2_router_exact_input(self, router, amount, path, block):
            assert block == 123
            if path == [QUOTE, BASE]:
                assert amount == 50_000_000
                return [amount, 10**18]
            assert path == [BASE, QUOTE]
            assert amount == 10**18
            return [amount, 51_000_000]

    monkeypatch.setattr(cli, "EthereumV2PoolReader", FakeReader)
    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50", "--rpc-url", "https://rpc.example",
        "--buy-router", "0x" + "5" * 40, "--sell-router", "0x" + "6" * 40,
    ])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["router_validation"]["mode"] == "read_only_router_eth_call"
    assert result["router_validation"]["sell_quote_out"] == "51"
    assert result["live_execution"] is False


def test_v2_sim_cli_requires_both_router_addresses(monkeypatch):
    import pytest

    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50", "--buy-router", "0x" + "5" * 40,
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
