from datetime import datetime, timezone
import sys

import pytest

from arb_engine.cli import main
from arb_engine.dexscreener import DexPairCandidate
from arb_engine.onchain_v2_scan import scan_onchain_v2_candidates
from arb_engine.rpc import EvmBlockContext, EvmPoolSnapshot, PoolFactoryMismatch
from arb_engine.v2 import V2PoolState
from arb_engine.venues import get_venue


BASE = "0x" + "1" * 40
QUOTE = "0x" + "2" * 40
UNISWAP_PAIR = "0x" + "3" * 40
SUSHI_PAIR = "0x" + "4" * 40
UNISWAP_V3_PAIR = "0x" + "5" * 40
HASH = "0x" + "a" * 64
UNISWAP = get_venue("uniswap_v2_ethereum", 1)
SUSHI = get_venue("sushiswap_v2_ethereum", 1)


def candidate(dex, pair_address):
    return DexPairCandidate(
        chain="ethereum", dex=dex, pair_address=pair_address,
        base_address=BASE, base_symbol="BASE",
        quote_address=QUOTE, quote_symbol="QUOTE",
        fetched_at=datetime.now(timezone.utc), pair_created_at_ms=None,
        source_url="https://dexscreener.com/ethereum/" + pair_address,
        labels=("v2",),
    )


class FakeDexScreener:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    def token_pair_candidates(self, chain, token):
        self.calls.append((chain, token))
        return self.candidates


class FakeReader:
    def __init__(self, *, mismatch_quote_decimals=False):
        self.context = EvmBlockContext(
            1, 100, HASH, int(datetime.now(timezone.utc).timestamp()),
        )
        self.verified = []
        self.read_requests = []
        self.router_checks = []
        self.quotes = []
        self.canonical_checks = []
        self.rpc_request_count = 29
        self.mismatch_quote_decimals = mismatch_quote_decimals

    def latest_block_context(self):
        return self.context

    def verify_v2_pool_factory(self, pair, factory, token0, token1, block_number):
        self.verified.append((pair, factory, token0, token1, block_number))
        if pair == UNISWAP_V3_PAIR:
            raise PoolFactoryMismatch("not the registered V2 factory pair")

    def read_pools(self, pools, *, block_context=None):
        self.read_requests.append((pools, block_context))
        now = datetime.now(timezone.utc)
        snapshots = []
        for pair, fee in pools:
            state = V2PoolState(
                chain_id=1, pair_address=pair, token0=BASE, token1=QUOTE,
                reserve0_raw=1_000 * 10**18, reserve1_raw=2_000_000 * 10**6,
                fee_bps=fee, block_number=100, block_hash=HASH,
                observed_at=now,
            )
            snapshots.append(EvmPoolSnapshot(
                state=state, token0_decimals=18,
                token1_decimals=18 if self.mismatch_quote_decimals and pair == SUSHI_PAIR else 6,
                block_timestamp=self.context.timestamp,
            ))
        return snapshots

    def verify_v2_router_factory(self, router, factory, block_number):
        self.router_checks.append((router, factory, block_number))

    def quote_v2_router_exact_input(self, router, amount_in, token_path, block_number):
        self.quotes.append((router, amount_in, token_path, block_number))
        if router == UNISWAP.router02 and token_path == [QUOTE, BASE]:
            return [amount_in, 500 * 10**15]
        if router == SUSHI.router02 and token_path == [BASE, QUOTE]:
            return [amount_in, 1_010 * 10**6]
        if router == SUSHI.router02 and token_path == [QUOTE, BASE]:
            return [amount_in, 490 * 10**15]
        if router == UNISWAP.router02 and token_path == [BASE, QUOTE]:
            return [amount_in, 980 * 10**6]
        raise AssertionError((router, token_path))

    def verify_canonical_block(self, block_number, block_hash):
        self.canonical_checks.append((block_number, block_hash))


def test_scan_uses_dexscreener_only_for_discovery_and_quotes_both_routes_onchain():
    dex = FakeDexScreener([
        candidate("uniswap", UNISWAP_V3_PAIR),
        candidate("uniswap", UNISWAP_PAIR),
        candidate("sushiswap", SUSHI_PAIR),
    ])
    reader = FakeReader()
    result = scan_onchain_v2_candidates(
        token_address=BASE, quote_token_address=QUOTE,
        capital_quote="1000", dex_client=dex, reader=reader,
    )

    assert dex.calls == [("ethereum", BASE)]
    assert result["status"] == "quoted"
    assert result["block_number"] == 100
    assert result["block_hash"] == HASH
    assert result["base_token_decimals"] == 18
    assert result["quote_token_decimals"] == 6
    assert result["capital_quote_raw"] == "1000000000"
    assert len(result["routes"]) == 2
    assert result["routes"][0]["buy_venue_id"] == UNISWAP.id
    assert result["routes"][0]["sell_venue_id"] == SUSHI.id
    assert result["routes"][0]["gross_quote_surplus_raw"] == "10000000"
    assert result["routes"][0]["positive_gross_surplus"] is True
    assert result["routes"][1]["gross_quote_surplus_raw"] == "-20000000"
    assert result["routes"][1]["positive_gross_surplus"] is False
    assert result["gas_included"] is False
    assert result["net_profit_claim"] is False
    assert result["live_execution"] is False
    assert result["transaction_submitted"] is False
    assert result["wallet_or_signer_used"] is False
    assert reader.read_requests[0][1] == reader.context
    assert len(reader.router_checks) == 2
    assert len(reader.quotes) == 4
    assert reader.canonical_checks == [(100, HASH)]


def test_scan_skips_non_v2_candidates_but_does_not_hide_rpc_failures():
    dex = FakeDexScreener([
        candidate("uniswap", UNISWAP_V3_PAIR),
        candidate("uniswap", UNISWAP_PAIR),
        candidate("sushiswap", SUSHI_PAIR),
    ])
    reader = FakeReader()
    result = scan_onchain_v2_candidates(
        token_address=BASE, quote_token_address=QUOTE,
        capital_quote="1000", dex_client=dex, reader=reader,
    )
    rejected = [item for item in result["candidate_verification"] if not item["factory_verified"]]
    assert len(rejected) == 1
    assert rejected[0]["rejection_reason"] == "v2_factory_mismatch"


def test_scan_returns_no_routes_when_one_trusted_venue_is_missing():
    dex = FakeDexScreener([candidate("uniswap", UNISWAP_PAIR)])
    reader = FakeReader()
    result = scan_onchain_v2_candidates(
        token_address=BASE, quote_token_address=QUOTE,
        capital_quote="1000", dex_client=dex, reader=reader,
    )
    assert result["status"] == "insufficient_factory_verified_venues"
    assert result["routes"] == []
    assert reader.read_requests == []
    assert reader.canonical_checks == [(100, HASH)]


def test_scan_rejects_candidate_and_rpc_budget_overruns_before_onchain_work():
    dex = FakeDexScreener([
        candidate("uniswap", "0x" + f"{i + 1:040x}") for i in range(5)
    ])
    reader = FakeReader()
    with pytest.raises(ValueError, match="candidate count"):
        scan_onchain_v2_candidates(
            token_address=BASE, quote_token_address=QUOTE,
            capital_quote="1000", dex_client=dex, reader=reader,
        )
    with pytest.raises(ValueError, match="max_rpc_requests"):
        scan_onchain_v2_candidates(
            token_address=BASE, quote_token_address=QUOTE,
            capital_quote="1000", dex_client=dex, reader=reader,
            max_rpc_requests=0,
        )
    assert reader.verified == []


def test_scan_rejects_fractional_capital_beyond_quote_token_decimals():
    dex = FakeDexScreener([
        candidate("uniswap", UNISWAP_PAIR), candidate("sushiswap", SUSHI_PAIR),
    ])
    reader = FakeReader()
    with pytest.raises(ValueError, match="fractional digits"):
        scan_onchain_v2_candidates(
            token_address=BASE, quote_token_address=QUOTE,
            capital_quote="1000.0000001", dex_client=dex, reader=reader,
        )


def test_scan_rejects_decimal_disagreement_between_verified_pools():
    dex = FakeDexScreener([
        candidate("uniswap", UNISWAP_PAIR), candidate("sushiswap", SUSHI_PAIR),
    ])
    reader = FakeReader(mismatch_quote_decimals=True)
    with pytest.raises(ValueError, match="Quote-token decimals disagree"):
        scan_onchain_v2_candidates(
            token_address=BASE, quote_token_address=QUOTE,
            capital_quote="1000", dex_client=dex, reader=reader,
        )


def test_scan_requires_explicit_nonzero_addresses_and_rejects_float_capital():
    dex = FakeDexScreener([])
    reader = FakeReader()
    with pytest.raises(ValueError, match="quote_token_address"):
        scan_onchain_v2_candidates(
            token_address=BASE, quote_token_address="0x" + "0" * 40,
            capital_quote="1000", dex_client=dex, reader=reader,
        )
    with pytest.raises(ValueError, match="not a float"):
        scan_onchain_v2_candidates(
            token_address=BASE, quote_token_address=QUOTE,
            capital_quote=1000.0, dex_client=dex, reader=reader,
        )



def test_onchain_v2_scan_cli_requires_explicit_quote_and_capital(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["arb-engine", "onchain-v2-scan", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--quote-token-address" in help_text
    assert "--capital-quote" in help_text
