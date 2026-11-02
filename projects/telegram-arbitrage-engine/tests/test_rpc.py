from arb_engine.rpc import EthereumV2PoolReader, RpcReadError


TOKEN0 = "0x" + "1" * 40
TOKEN1 = "0x" + "2" * 40
POOL_A = "0x" + "3" * 40
POOL_B = "0x" + "4" * 40
BLOCK_HASH = "0x" + "a" * 64


def word(value: int) -> str:
    return f"{value:064x}"


def address_word(address: str) -> str:
    return "0x" + ("0" * 24) + address[2:].lower()


def fake_rpc(method, params):
    if method == "eth_chainId":
        return "0x1"
    if method == "eth_blockNumber":
        return "0x64"
    if method == "eth_getBlockByNumber":
        assert params == ["0x64", False]
        return {"hash": BLOCK_HASH, "timestamp": hex(int(__import__("time").time()))}
    if method == "eth_call":
        call, block_tag = params
        assert block_tag == "0x64"
        selector = call["data"]
        target = call["to"].lower()
        if selector == "0x0dfe1681":
            return address_word(TOKEN0)
        if selector == "0xd21220a7":
            return address_word(TOKEN1)
        if selector == "0x0902f1ac":
            reserve0 = 1_000_000 if target == POOL_A else 2_000_000
            reserve1 = 3_000_000 if target == POOL_A else 4_000_000
            return "0x" + word(reserve0) + word(reserve1) + word(1234)
        if selector == "0x313ce567":
            return "0x" + word(6 if call["to"].lower() == TOKEN0 else 18)
    raise AssertionError(f"Unexpected RPC request: {method} {params}")


def test_reader_returns_two_pool_states_pinned_to_same_block(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    calls = []
    def fake(method, params):
        calls.append((method, params))
        return fake_rpc(method, params)
    monkeypatch.setattr(reader, "_rpc", fake)

    snapshots = reader.read_pools([(POOL_A, 30), (POOL_B, 25)])
    assert len(snapshots) == 2
    assert snapshots[0].state.block_number == snapshots[1].state.block_number == 100
    assert snapshots[0].state.block_hash == snapshots[1].state.block_hash == BLOCK_HASH
    assert snapshots[0].state.fee_bps == 30
    assert snapshots[1].state.fee_bps == 25
    assert snapshots[0].token0_decimals == 6
    assert snapshots[0].token1_decimals == 18
    assert all(params[1] == "0x64" for method, params in calls if method == "eth_call")


def test_reader_rejects_wrong_chain(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(reader, "_rpc", lambda method, params: "0x89" if method == "eth_chainId" else None)
    try:
        reader.read_pool(POOL_A, 30)
    except RpcReadError as exc:
        assert "Unexpected chain ID" in str(exc)
    else:
        raise AssertionError("wrong chain must be rejected")


def test_reader_requires_https_endpoint_without_credentials():
    import pytest

    with pytest.raises(ValueError, match="HTTPS"):
        EthereumV2PoolReader("http://example.com")
    with pytest.raises(ValueError, match="credentials"):
        EthereumV2PoolReader("https://user:secret@example.com/rpc")
    with pytest.raises(ValueError, match="query"):
        EthereumV2PoolReader("https://example.com/rpc?key=secret")


def test_reader_rejects_duplicate_pool_addresses():
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    with pytest.raises(ValueError, match="Duplicate"):
        reader.read_pools([(POOL_A, 30), (POOL_A, 25)])


def test_reader_rejects_reorg_during_snapshot(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    block_reads = 0

    def changing_rpc(method, params):
        nonlocal block_reads
        if method == "eth_getBlockByNumber":
            block_reads += 1
            current_hash = BLOCK_HASH if block_reads == 1 else "0x" + "b" * 64
            return {"hash": current_hash, "timestamp": hex(int(__import__("time").time()))}
        return fake_rpc(method, params)

    monkeypatch.setattr(reader, "_rpc", changing_rpc)
    import pytest
    with pytest.raises(RpcReadError, match="possible reorg"):
        reader.read_pool(POOL_A, 30)

def test_rpc_rejects_noncanonical_hex_and_nonzero_abi_address_padding():
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    with pytest.raises(RpcReadError, match="Invalid hexadecimal"):
        reader._uint("0x+1", "chain ID")
    with pytest.raises(RpcReadError, match="non-zero address padding"):
        reader._address("0x" + "1" * 24 + "2" * 40, "token0")
    with pytest.raises(ValueError, match="hexadecimal EVM address"):
        reader._pool_address("0x+" + "1" * 39)
    assert reader._address("0x" + "0" * 24 + "2" * 40, "token0") == "0x" + "2" * 40


def test_router_quote_encodes_exact_input_and_decodes_dynamic_amounts(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    seen = {}

    def fake(method, params):
        seen["method"] = method
        seen["params"] = params
        amounts = [123456, 654321]
        encoded = "0x" + word(32) + word(len(amounts)) + "".join(word(value) for value in amounts)
        return encoded

    monkeypatch.setattr(reader, "_rpc", fake)
    result = reader.quote_v2_router_exact_input(
        "0x" + "9" * 40, 123456, [TOKEN0, TOKEN1], 100,
    )
    assert result == [123456, 654321]
    call, block_tag = seen["params"]
    assert seen["method"] == "eth_call"
    assert block_tag == "0x64"
    assert call["to"] == "0x" + "9" * 40
    assert call["data"].startswith("0xd06ca61f")
    assert int(call["data"][10:74], 16) == 123456
    assert int(call["data"][74:138], 16) == 64


def test_router_quote_rejects_bad_abi_array(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(reader, "_rpc", lambda method, params: "0x" + word(32) + word(1) + word(100))
    with pytest.raises(RpcReadError, match="unexpected amounts array"):
        reader.quote_v2_router_exact_input(
            "0x" + "9" * 40, 123456, [TOKEN0, TOKEN1], 100,
        )


def test_router_quote_rejects_bad_path_and_amount():
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    with pytest.raises(ValueError, match="uint256"):
        reader.quote_v2_router_exact_input("0x" + "9" * 40, True, [TOKEN0, TOKEN1], 100)
    with pytest.raises(ValueError, match="repeat"):
        reader.quote_v2_router_exact_input("0x" + "9" * 40, 10, [TOKEN0, TOKEN0], 100)


def test_canonical_block_revalidation_detects_late_reorg(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(
        reader, "_rpc",
        lambda method, params: {"hash": "0x" + "b" * 64} if method == "eth_getBlockByNumber" else None,
    )
    with pytest.raises(RpcReadError, match="no longer canonical"):
        reader.verify_canonical_block(100, BLOCK_HASH)


def test_canonical_block_revalidation_accepts_matching_hash(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(
        reader, "_rpc",
        lambda method, params: {"hash": BLOCK_HASH} if method == "eth_getBlockByNumber" else None,
    )
    reader.verify_canonical_block(100, BLOCK_HASH)


def test_v2_factory_verification_checks_pair_factory_and_registration(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    factory = "0x" + "8" * 40
    calls = []

    def fake(method, params):
        calls.append((method, params))
        call, block_tag = params
        assert method == "eth_call"
        assert block_tag == "0x64"
        if call["to"] == POOL_A and call["data"] == "0xc45a0155":
            return address_word(factory)
        if call["to"] == factory and call["data"].startswith("0xe6a43905"):
            return address_word(POOL_A)
        raise AssertionError(f"Unexpected factory call: {call}")

    monkeypatch.setattr(reader, "_rpc", fake)
    reader.verify_v2_pool_factory(POOL_A, factory, TOKEN0, TOKEN1, 100)
    assert len(calls) == 2


def test_v2_factory_verification_rejects_untrusted_factory(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(reader, "_rpc", lambda method, params: address_word("0x" + "7" * 40))
    with pytest.raises(RpcReadError, match="does not match"):
        reader.verify_v2_pool_factory(
            POOL_A, "0x" + "8" * 40, TOKEN0, TOKEN1, 100,
        )


def test_zero_address_is_rejected_for_all_contract_roles():
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    with pytest.raises(ValueError, match="Zero address"):
        reader._pool_address("0x" + "0" * 40)
