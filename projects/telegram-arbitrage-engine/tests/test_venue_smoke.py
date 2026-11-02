import pytest

from arb_engine.venue_smoke import (
    FACTORY_SELECTOR,
    GET_PAIR_SELECTOR,
    TOKEN0_SELECTOR,
    TOKEN1_SELECTOR,
    USDC_MAINNET,
    WETH_MAINNET,
    JsonRpcClient,
    VenueVerificationError,
    verify_registry_venues,
)
from arb_engine.venues import list_venues


HASH = "0x" + "a" * 64
PAIR_A = "0x" + "3" * 40
PAIR_B = "0x" + "4" * 40


def word(address):
    return "0x" + "0" * 24 + address[2:].lower()


class FakeRpc:
    endpoint = "injected://test-double"

    def __init__(self, *, wrong_router=False, reorg=False):
        venues = list_venues(1)
        self.router_factory = {v.router02.lower(): v.factory.lower() for v in venues}
        if wrong_router:
            self.router_factory[venues[0].router02.lower()] = "0x" + "9" * 40
        self.pairs = {
            venues[0].factory.lower(): PAIR_A,
            venues[1].factory.lower(): PAIR_B,
        }
        self.pair_factory = {
            PAIR_A: venues[0].factory.lower(),
            PAIR_B: venues[1].factory.lower(),
        }
        self.reorg = reorg
        self.block_reads = 0

    def call(self, method, params):
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x123"
        if method == "eth_getBlockByNumber":
            self.block_reads += 1
            if self.reorg and self.block_reads > 1:
                return {"hash": "0x" + "b" * 64}
            return {"hash": HASH}
        if method == "eth_getCode":
            return "0x60006000"
        if method != "eth_call":
            raise AssertionError(f"Unexpected RPC method: {method}")

        target = params[0]["to"].lower()
        data = params[0]["data"].lower()
        assert params[1] == "0x123"
        if data == FACTORY_SELECTOR:
            if target in self.router_factory:
                return word(self.router_factory[target])
            if target in self.pair_factory:
                return word(self.pair_factory[target])
        if data.startswith(GET_PAIR_SELECTOR):
            assert data == (
                GET_PAIR_SELECTOR
                + "0" * 24 + WETH_MAINNET[2:].lower()
                + "0" * 24 + USDC_MAINNET[2:].lower()
            )
            return word(self.pairs[target])
        if target in self.pair_factory and data == TOKEN0_SELECTOR:
            return word(USDC_MAINNET)
        if target in self.pair_factory and data == TOKEN1_SELECTOR:
            return word(WETH_MAINNET)
        raise AssertionError(f"Unexpected eth_call target/data: {target} {data}")


def test_live_venue_check_verifies_factory_router_and_pair_at_one_block():
    result = verify_registry_venues(FakeRpc(), list_venues(1))
    assert result["verification_status"] == "live_read_only_onchain_checks_passed"
    assert result["chain_id"] == 1
    assert result["block_number"] == 0x123
    assert result["block_hash"] == HASH
    assert len(result["venues"]) == 2
    assert {v["pair_address"] for v in result["venues"]} == {PAIR_A, PAIR_B}
    assert all(v["onchain_relationship_checks"] == "passed" for v in result["venues"])


def test_live_venue_check_fails_closed_when_router_factory_mismatches():
    with pytest.raises(VenueVerificationError, match="Router02 factory"):
        verify_registry_venues(FakeRpc(wrong_router=True), list_venues(1))


def test_live_venue_check_fails_closed_if_pinned_block_hash_changes():
    with pytest.raises(VenueVerificationError, match="Pinned block hash changed"):
        verify_registry_venues(FakeRpc(reorg=True), list_venues(1))


def test_live_venue_check_rejects_wrong_chain_and_empty_registry():
    with pytest.raises(VenueVerificationError, match="Ethereum mainnet only"):
        verify_registry_venues(FakeRpc(), list_venues(1), expected_chain_id=8453)
    with pytest.raises(VenueVerificationError, match="No source-documented venues"):
        verify_registry_venues(FakeRpc(), [], expected_chain_id=1)


def test_rpc_client_requires_https_endpoint():
    with pytest.raises(ValueError, match="HTTPS"):
        JsonRpcClient("http://127.0.0.1:8545")
