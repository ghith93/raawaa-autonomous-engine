import json
import sys
from datetime import datetime, timezone
from decimal import Decimal

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
        def __init__(self, endpoint, expected_chain_id=1):
            assert endpoint == "https://rpc.example"
            assert expected_chain_id == 1

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
    assert result["buy_reserve_timestamp_last"] == 0
    assert result["buy_seconds_since_last_reserve_update"] == 0
    assert result["sell_seconds_since_last_reserve_update"] == 0
    assert result["buy_pool_fee_bps_assumed"] == 30
    assert result["sell_pool_fee_bps_assumed"] == 25
    assert Decimal(result["buy_price_impact_bps_excluding_fee"]) >= 0
    assert Decimal(result["sell_price_impact_bps_excluding_fee"]) >= 0
    assert result["live_execution"] is False
    assert "Excludes gas" in result["warning"]


def test_v2_sim_cli_router_validation_is_read_only(monkeypatch, capsys):
    class FakeReader:
        def __init__(self, endpoint, expected_chain_id=1):
            self.endpoint = endpoint
            assert expected_chain_id == 1

        def read_pools(self, pools):
            return [
                snapshot(POOL_A, 1_000_000_000_000, 10**24, 30),
                snapshot(POOL_B, 1_000_000_000_000, 10**24, 25),
            ]

        def verify_v2_pool_factory(self, pair, factory, token0, token1, block):
            assert pair in {POOL_A, POOL_B}
            assert factory in {"0x" + "7" * 40, "0x" + "8" * 40}
            assert token0 == QUOTE and token1 == BASE
            assert block == 123

        def verify_v2_router_factory(self, router, factory, block):
            assert router in {"0x" + "5" * 40, "0x" + "6" * 40}
            assert factory in {"0x" + "7" * 40, "0x" + "8" * 40}
            assert block == 123

        def verify_canonical_block(self, block, expected_hash):
            assert block == 123
            assert expected_hash == HASH

        def quote_v2_router_exact_input(self, router, amount, path, block):
            assert block == 123
            if path == [QUOTE, BASE]:
                assert amount == 50_000_000
                return [amount, 10**18]
            assert path == [BASE, QUOTE]
            assert amount == 995_000_000_000_000_000
            return [amount, 50_745_000]

    monkeypatch.setattr(cli, "EthereumV2PoolReader", FakeReader)
    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50", "--rpc-url", "https://rpc.example",
        "--buy-router", "0x" + "5" * 40, "--sell-router", "0x" + "6" * 40,
        "--buy-factory", "0x" + "7" * 40, "--sell-factory", "0x" + "8" * 40,
    ])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["router_validation"]["mode"] == "read_only_router_eth_call"
    assert result["router_validation"]["buy_min_base_out"] == "0.995"
    assert result["router_validation"]["sell_quote_out_at_buy_min"] == "50.745"
    assert result["router_validation"]["sell_min_quote_out"] == "50.491275"
    assert result["router_validation"]["slippage_tolerance_bps_per_leg"] == 50
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


def test_v2_sim_cli_requires_both_factory_addresses(monkeypatch):
    import pytest

    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50", "--buy-factory", "0x" + "7" * 40,
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_v2_sim_cli_router_validation_requires_factories(monkeypatch):
    import pytest

    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50",
        "--buy-router", "0x" + "5" * 40, "--sell-router", "0x" + "6" * 40,
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_v2_sim_cli_reports_native_gas_budget_separately(monkeypatch, capsys):
    class FakeReader:
        def __init__(self, endpoint, expected_chain_id=1):
            pass

        def read_pools(self, pools):
            return [
                snapshot(POOL_A, 1_000_000_000_000, 10**24, 30),
                snapshot(POOL_B, 1_000_000_000_000, 10**24, 25),
            ]

        def read_gas_price_wei(self):
            return 2_000_000_000

    monkeypatch.setattr(cli, "EthereumV2PoolReader", FakeReader)
    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50", "--gas-units-assumption", "300000",
        "--gas-safety-bps", "2000",
    ])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    gas = result["gas_cost_estimate"]
    assert gas["buffered_gas_units"] == 360000
    assert gas["gas_price_source"] == "EVM JSON-RPC eth_gasPrice"
    assert gas["gas_price_retrieved_at"].endswith("+00:00")
    assert gas["estimated_native_asset_cost_wei"] == "720000000000000"
    assert gas["valuation_in_quote_token"] is None
    assert "gas" in result["warning"].lower()


def test_v2_sim_cli_rejects_excessive_slippage_tolerance(monkeypatch):
    import pytest

    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "v2-sim",
        "--buy-pair", POOL_A, "--buy-fee-bps", "30",
        "--sell-pair", POOL_B, "--sell-fee-bps", "25",
        "--quote-token", QUOTE, "--base-token", BASE,
        "--capital-quote", "50", "--slippage-bps", "5001",
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
