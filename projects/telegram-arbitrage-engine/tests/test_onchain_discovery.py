from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arb_engine.dexscreener import DexPair
from arb_engine.onchain_discovery import discover_pool_candidates, scan_onchain_v2
from arb_engine.rpc import EvmBlockSnapshot, EvmPoolSnapshot, RpcVerificationError
from arb_engine.v2 import V2PoolState


QUOTE = "0x" + "1" * 40
BASE = "0x" + "2" * 40
UNI_PAIR = "0x" + "3" * 40
INVALID_UNI_PAIR = "0x" + "9" * 40
SUSHI_PAIR = "0x" + "4" * 40
OTHER_PAIR = "0x" + "5" * 40
HASH = "0x" + "a" * 64
UNI_ROUTER = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
SUSHI_ROUTER = "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f"
UNI_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY = "0xc0aee478e3658e2610c5f7a4a2e1777ce9e4f2ac"


def pair(address, dex, base=BASE, quote=QUOTE, chain="ethereum", liquidity="50000"):
    return DexPair(
        chain=chain, dex=dex, pair_address=address,
        base_address=base, base_symbol="WETH",
        quote_address=quote, quote_symbol="USDC",
        price_usd=Decimal("2000"), liquidity_usd=Decimal(liquidity),
        volume_24h_usd=Decimal("100000"),
        fetched_at=datetime.now(timezone.utc), pair_created_at_ms=1,
        source_url=f"https://dexscreener.com/ethereum/{address}",
    )


def snapshot(address, token0=QUOTE, token1=BASE, decimals0=6, decimals1=18, block=100):
    return EvmPoolSnapshot(
        state=V2PoolState(
            chain_id=1, pair_address=address, token0=token0, token1=token1,
            reserve0_raw=2_000_000 * 10**6, reserve1_raw=1_000 * 10**18,
            fee_bps=30, block_number=block, block_hash=HASH,
            observed_at=datetime.now(timezone.utc),
        ),
        token0_decimals=decimals0, token1_decimals=decimals1,
        block_timestamp=int(datetime.now(timezone.utc).timestamp()),
    )


def candidates():
    return [pair(UNI_PAIR, "uniswap"), pair(SUSHI_PAIR, "sushiswap")]


class FakeReader:
    def __init__(self, states=None, invalid_pairs=None):
        self.states = states or {
            UNI_PAIR: snapshot(UNI_PAIR),
            SUSHI_PAIR: snapshot(SUSHI_PAIR),
        }
        self.invalid_pairs = set(invalid_pairs or [])
        self.verified_pairs = []
        self.verified_routers = []
        self.canonical = []
        self.quotes = []

    def read_latest_block_snapshot(self):
        return EvmBlockSnapshot(
            chain_id=1, block_number=100, block_hash=HASH,
            block_timestamp=1_798_000_000,
        )

    def read_pools(self, pools, *, pinned_block=None):
        assert len(pools) == 2
        assert pinned_block.block_number == 100
        return [self.states[address] for address, _fee in pools]

    def verify_v2_pool_factory(self, pair_address, factory, token0, token1, block):
        if pair_address in self.invalid_pairs:
            raise RpcVerificationError("Pair factory does not match configured trusted factory")
        expected_factory = UNI_FACTORY if pair_address == UNI_PAIR else SUSHI_FACTORY
        assert factory.casefold() == expected_factory.casefold()
        assert {token0, token1} == {QUOTE, BASE}
        assert block == 100
        self.verified_pairs.append((pair_address, factory, block))

    def verify_v2_router_factory(self, router, factory, block):
        expected_factory = UNI_FACTORY if router.casefold() == UNI_ROUTER.casefold() else SUSHI_FACTORY
        assert factory.casefold() == expected_factory.casefold()
        assert block == 100
        self.verified_routers.append((router, factory, block))

    def quote_v2_router_exact_input(self, router, amount, path, block):
        self.quotes.append((router, amount, path, block))
        if router.casefold() == UNI_ROUTER.casefold() and path == [QUOTE, BASE]:
            out = 26 * 10**15
        elif router.casefold() == SUSHI_ROUTER.casefold() and path == [QUOTE, BASE]:
            out = 25 * 10**15
        elif router.casefold() == UNI_ROUTER.casefold() and path == [BASE, QUOTE]:
            out = 49 * 10**6
        elif router.casefold() == SUSHI_ROUTER.casefold() and path == [BASE, QUOTE]:
            out = 51 * 10**6
        else:
            raise AssertionError("Unexpected router/path")
        return [amount, out]

    def verify_canonical_block(self, block, block_hash):
        self.canonical.append((block, block_hash))


def test_discovery_uses_exact_addresses_and_only_registered_ethereum_venues():
    found = discover_pool_candidates(
        candidates() + [
            pair(OTHER_PAIR, "uniswap", base="0x" + "6" * 40),
            pair("0x" + "7" * 40, "pancakeswap"),
            pair("0x" + "8" * 40, "uniswap", chain="base"),
        ],
        quote_token=QUOTE,
    )
    assert len(found) == 1
    assert {found[0][0].venue.id, found[0][1].venue.id} == {
        "uniswap_v2_ethereum", "sushiswap_v2_ethereum",
    }


def test_discovery_requires_both_distinct_venue_pools_and_liquidity():
    assert discover_pool_candidates([pair(UNI_PAIR, "uniswap")], quote_token=QUOTE) == []
    assert discover_pool_candidates(
        [pair(UNI_PAIR, "uniswap"), pair(SUSHI_PAIR, "sushiswap", liquidity="999")],
        quote_token=QUOTE, min_liquidity_usd=Decimal("10000"),
    ) == []


def test_discovery_rejects_invalid_limits_and_quote_token():
    with pytest.raises(ValueError, match="quote_token"):
        discover_pool_candidates(candidates(), quote_token="USDC")
    with pytest.raises(ValueError, match="max_candidates"):
        discover_pool_candidates(candidates(), quote_token=QUOTE, max_candidates=11)


def test_onchain_scan_verifies_both_venues_and_quotes_both_directions():
    fake = FakeReader()
    result = scan_onchain_v2(
        "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
        min_liquidity_usd=Decimal("10000"), max_candidates=5,
        rpc_url="https://rpc.example", reader=fake, pairs=candidates(),
    )
    assert result["candidate_pairs_discovered"] == 1
    assert result["candidate_pairs_verified"] == 1
    opportunity = result["opportunities"][0]
    assert opportunity["block_number"] == 100
    assert opportunity["quote_decimals"] == 6
    assert opportunity["base_decimals"] == 18
    assert opportunity["directions"][0]["gross_profit_quote_raw"] == "1000000"
    assert opportunity["directions"][0]["gross_positive_before_gas"] is True
    assert opportunity["directions"][1]["gross_profit_quote_raw"] == "-1000000"
    assert opportunity["best_direction"]["buy_venue"] == "Uniswap V2"
    assert opportunity["best_direction"]["gross_profit_quote"] == "1"
    assert len(fake.verified_pairs) == 2
    assert len(fake.verified_routers) == 2
    assert len(fake.quotes) == 4
    assert fake.canonical == [(100, HASH)]
    assert result["transaction_submitted"] is False
    assert result["wallet_or_signer_used"] is False


def test_onchain_scan_handles_reversed_dexscreener_token_orientation():
    reversed_candidates = [
        pair(UNI_PAIR, "uniswap", base=QUOTE, quote=BASE),
        pair(SUSHI_PAIR, "sushiswap", base=QUOTE, quote=BASE),
    ]
    result = scan_onchain_v2(
        "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
        rpc_url="https://rpc.example", reader=FakeReader(), pairs=reversed_candidates,
    )
    assert result["candidate_pairs_verified"] == 1
    assert result["opportunities"][0]["base_token"] == BASE


def test_onchain_scan_rejects_block_mismatch_before_router_quotes():
    fake = FakeReader({
        UNI_PAIR: snapshot(UNI_PAIR, block=100),
        SUSHI_PAIR: snapshot(SUSHI_PAIR, block=101),
    })
    with pytest.raises(ValueError, match="one canonical block"):
        scan_onchain_v2(
            "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
            rpc_url="https://rpc.example", reader=fake, pairs=candidates(),
        )
    assert len(fake.verified_pairs) == 2
    assert len(fake.verified_routers) == 2
    assert fake.quotes == []


def test_onchain_scan_rejects_onchain_token_identity_mismatch():
    fake = FakeReader({
        UNI_PAIR: snapshot(UNI_PAIR),
        SUSHI_PAIR: snapshot(SUSHI_PAIR, token1="0x" + "9" * 40),
    })
    with pytest.raises(ValueError, match="destination pair tokens"):
        scan_onchain_v2(
            "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
            rpc_url="https://rpc.example", reader=fake, pairs=candidates(),
        )


def test_onchain_scan_rejects_decimal_mismatch_between_pools():
    fake = FakeReader({
        UNI_PAIR: snapshot(UNI_PAIR, decimals1=18),
        SUSHI_PAIR: snapshot(SUSHI_PAIR, decimals1=17),
    })
    with pytest.raises(ValueError, match="decimals disagree"):
        scan_onchain_v2(
            "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
            rpc_url="https://rpc.example", reader=fake, pairs=candidates(),
        )


def test_onchain_scan_rejects_capital_above_quote_precision():
    with pytest.raises(ValueError, match="precision"):
        scan_onchain_v2(
            "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("0.0000001"),
            rpc_url="https://rpc.example", reader=FakeReader(), pairs=candidates(),
        )


def test_onchain_scan_returns_empty_without_a_verified_pair():
    result = scan_onchain_v2(
        "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
        rpc_url="https://rpc.example", reader=FakeReader(), pairs=[pair(UNI_PAIR, "uniswap")],
    )
    assert result["candidate_pairs_discovered"] == 0
    assert result["candidate_pairs_verified"] == 0
    assert result["opportunities"] == []



def test_invalid_candidate_limit_is_rejected_before_dexscreener_network_call(monkeypatch):
    import arb_engine.dexscreener as dexscreener

    class NeverCall:
        def search_pairs(self, query):
            raise AssertionError("network must not be called for invalid bounds")

    monkeypatch.setattr(dexscreener, "DexScreenerClient", NeverCall)
    with pytest.raises(ValueError, match="max_candidates"):
        scan_onchain_v2(
            "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
            rpc_url="https://rpc.example", max_candidates=11,
        )



def test_scan_skips_unverified_high_liquidity_dex_label_and_uses_valid_fallback():
    fake = FakeReader(
        {
            INVALID_UNI_PAIR: snapshot(INVALID_UNI_PAIR),
            UNI_PAIR: snapshot(UNI_PAIR),
            SUSHI_PAIR: snapshot(SUSHI_PAIR),
        },
        invalid_pairs={INVALID_UNI_PAIR},
    )
    result = scan_onchain_v2(
        "WETH USDC", quote_token=QUOTE, capital_quote=Decimal("50"),
        rpc_url="https://rpc.example", reader=fake,
        pairs=[
            pair(INVALID_UNI_PAIR, "uniswap", liquidity="90000"),
            pair(UNI_PAIR, "uniswap", liquidity="50000"),
            pair(SUSHI_PAIR, "sushiswap", liquidity="50000"),
        ],
    )
    assert result["candidate_pairs_discovered"] == 2
    assert result["candidate_pairs_rejected"] == 1
    assert result["candidate_pairs_verified"] == 1
    assert result["opportunities"][0]["source_pairs"][0]["pair"] == UNI_PAIR
