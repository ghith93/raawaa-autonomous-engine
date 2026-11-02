from arb_engine.rpc import (
    EvmBlockContext, EthereumV2PoolReader, PoolFactoryMismatch,
    RpcExecutionReverted, RpcReadError, RpcTransportError,
)


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
    assert snapshots[0].reserve_timestamp_last == 1234
    assert snapshots[0].seconds_since_last_reserve_update == (
        snapshots[0].block_timestamp - 1234
    )
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


def test_v2_router_factory_verification(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    factory = "0x" + "8" * 40
    monkeypatch.setattr(reader, "_rpc", lambda method, params: address_word(factory))
    reader.verify_v2_router_factory("0x" + "9" * 40, factory, 100)


def test_v2_router_factory_verification_rejects_mismatch(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(reader, "_rpc", lambda method, params: address_word("0x" + "7" * 40))
    with pytest.raises(RpcReadError, match="Router factory does not match"):
        reader.verify_v2_router_factory(
            "0x" + "9" * 40, "0x" + "8" * 40, 100,
        )


def test_gas_price_reader_returns_wei_and_rejects_unsafe_values(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(reader, "_rpc", lambda method, params: "0x3b9aca00")
    assert reader.read_gas_price_wei() == 1_000_000_000
    monkeypatch.setattr(reader, "_rpc", lambda method, params: "0x0")
    with pytest.raises(RpcReadError, match="zero"):
        reader.read_gas_price_wei()
    monkeypatch.setattr(reader, "_rpc", lambda method, params: hex(10**15))
    with pytest.raises(RpcReadError, match="safety ceiling"):
        reader.read_gas_price_wei()


def dynamic_string(value: str) -> str:
    encoded = value.encode("utf-8")
    padded = encoded + b"\\x00" * ((32 - len(encoded) % 32) % 32)
    return "0x" + word(32) + word(len(encoded)) + padded.hex()


def test_token_preflight_reads_code_and_optional_metadata_at_one_pinned_block(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    calls = []
    token = "0x" + "5" * 40

    def fake(method, params):
        calls.append((method, params))
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getBlockByNumber":
            return {"hash": BLOCK_HASH, "timestamp": hex(int(__import__("time").time()))}
        if method == "eth_getCode":
            assert params == [token, "0x64"]
            return "0x6001600055"
        if method == "eth_call":
            call, block = params
            assert block == "0x64"
            if call["data"] == "0x313ce567":
                return "0x" + word(18)
            if call["data"] == "0x95d89b41":
                return dynamic_string("TKN")
            if call["data"] == "0x06fdde03":
                return dynamic_string("Test Token")
        raise AssertionError((method, params))

    monkeypatch.setattr(reader, "_rpc", fake)
    result = reader.read_token_preflight(token)
    assert result.chain_id == 1
    assert result.block_number == 100
    assert result.block_hash == BLOCK_HASH
    assert result.token_address == token
    assert result.runtime_bytecode_size == 5
    assert result.decimals == 18
    assert result.symbol == "TKN"
    assert result.name == "Test Token"
    assert result.metadata_status == "metadata_available"
    assert [method for method, _ in calls].count("eth_getBlockByNumber") == 2


def test_token_preflight_rejects_eoa_and_wrong_chain(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    token = "0x" + "5" * 40
    monkeypatch.setattr(reader, "_rpc", lambda method, params: {
        "eth_chainId": "0x1",
        "eth_blockNumber": "0x64",
        "eth_getBlockByNumber": {"hash": BLOCK_HASH, "timestamp": hex(int(__import__("time").time()))},
        "eth_getCode": "0x",
    }.get(method))
    with pytest.raises(RpcReadError, match="no contract bytecode"):
        reader.read_token_preflight(token)

    monkeypatch.setattr(reader, "_rpc", lambda method, params: "0x89" if method == "eth_chainId" else None)
    with pytest.raises(RpcReadError, match="Unexpected chain ID"):
        reader.read_token_preflight(token)


def test_token_preflight_reports_missing_optional_metadata_without_guessing(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    token = "0x" + "5" * 40

    def fake(method, params):
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getBlockByNumber":
            return {"hash": BLOCK_HASH, "timestamp": hex(int(__import__("time").time()))}
        if method == "eth_getCode":
            return "0x6001"
        if method == "eth_call":
            raise RpcReadError("optional metadata missing")
        raise AssertionError(method)

    monkeypatch.setattr(reader, "_rpc", fake)
    result = reader.read_token_preflight(token)
    assert result.decimals is None
    assert result.symbol is None
    assert result.name is None
    assert result.metadata_status == "contract_code_only_metadata_incomplete"


def test_optional_metadata_decoder_supports_legacy_bytes32_and_rejects_malformed():
    from arb_engine.rpc import EthereumV2PoolReader

    assert EthereumV2PoolReader._decode_optional_string(
        "0x" + "USDC".encode().hex() + "00" * 28
    ) == "USDC"
    assert EthereumV2PoolReader._decode_optional_string("0x1234") is None
    assert EthereumV2PoolReader._decode_optional_string("0x" + "ff" * 32) is None


def test_token_preflight_rejects_stale_pinned_block(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    token = "0x" + "5" * 40
    monkeypatch.setattr(reader, "_rpc", lambda method, params: {
        "eth_chainId": "0x1",
        "eth_blockNumber": "0x64",
        "eth_getBlockByNumber": {"hash": BLOCK_HASH, "timestamp": "0x1"},
    }.get(method))
    with pytest.raises(RpcReadError, match="stale"):
        reader.read_token_preflight(token)



def test_latest_block_context_returns_one_canonical_chain_snapshot(monkeypatch):
    import time

    reader = EthereumV2PoolReader(min_request_interval=0)
    calls = []

    def fake(method, params):
        calls.append(method)
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getBlockByNumber":
            return {"hash": BLOCK_HASH, "timestamp": hex(int(time.time()))}
        raise AssertionError((method, params))

    monkeypatch.setattr(reader, "_rpc", fake)
    context = reader.latest_block_context()
    assert context.chain_id == 1
    assert context.block_number == 100
    assert context.block_hash == BLOCK_HASH
    assert 0 <= int(time.time()) - context.timestamp <= 2
    assert calls == ["eth_chainId", "eth_blockNumber", "eth_getBlockByNumber"]


def test_read_pools_reuses_supplied_block_context_and_rejects_hash_change(monkeypatch):
    import time
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    context = EvmBlockContext(1, 100, BLOCK_HASH, int(time.time()))
    calls = []

    def fake(method, params):
        calls.append((method, params))
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_getBlockByNumber":
            return {"hash": BLOCK_HASH, "timestamp": hex(int(time.time()))}
        return fake_rpc(method, params)

    monkeypatch.setattr(reader, "_rpc", fake)
    snapshots = reader.read_pools([(POOL_A, 30)], block_context=context)
    assert snapshots[0].state.block_hash == BLOCK_HASH
    assert not any(method == "eth_blockNumber" for method, _ in calls)

    def reorged(method, params):
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_getBlockByNumber":
            return {"hash": "0x" + "b" * 64, "timestamp": hex(int(time.time()))}
        raise AssertionError((method, params))

    monkeypatch.setattr(reader, "_rpc", reorged)
    with pytest.raises(RpcReadError, match="hash changed"):
        reader.read_pools([(POOL_A, 30)], block_context=context)


def test_rpc_request_budget_stops_before_the_next_network_request(monkeypatch):
    import json
    import pytest
    from io import BytesIO
    from unittest.mock import patch

    reader = EthereumV2PoolReader(min_request_interval=0, max_rpc_requests=1)
    responses = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        responses.append(body["method"])
        return BytesIO(json.dumps({
            "jsonrpc": "2.0", "id": body["id"], "result": "0x1",
        }).encode())

    with patch("arb_engine.rpc.urlopen", fake_urlopen):
        with pytest.raises(RpcReadError, match="request budget exceeded"):
            reader.latest_block_context()
    assert responses == ["eth_chainId"]
    assert reader.rpc_request_count == 1


def test_non_v2_pair_factory_is_a_candidate_mismatch_not_a_transport_failure(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(reader, "_rpc", lambda method, params: address_word("0x" + "7" * 40))
    with pytest.raises(PoolFactoryMismatch, match="does not match"):
        reader.verify_v2_pool_factory(
            POOL_A, "0x" + "8" * 40, TOKEN0, TOKEN1, 100,
        )

    def transport_failure(method, params):
        raise RpcReadError("EVM RPC request failed: timeout")

    monkeypatch.setattr(reader, "_rpc", transport_failure)
    with pytest.raises(RpcReadError, match="timeout") as exc:
        reader.verify_v2_pool_factory(
            POOL_A, "0x" + "8" * 40, TOKEN0, TOKEN1, 100,
        )
    assert not isinstance(exc.value, PoolFactoryMismatch)


def test_factory_verification_classifies_only_explicit_evm_reverts_as_candidate_mismatch(monkeypatch):
    import pytest

    reader = EthereumV2PoolReader(min_request_interval=0)

    def rate_limited(method, params):
        raise RpcReadError("EVM RPC returned an error (code=-32005)")

    monkeypatch.setattr(reader, "_rpc", rate_limited)
    with pytest.raises(RpcReadError, match="code=-32005") as exc:
        reader.verify_v2_pool_factory(POOL_A, "0x" + "8" * 40, TOKEN0, TOKEN1, 100)
    assert not isinstance(exc.value, PoolFactoryMismatch)

    def reverted(method, params):
        raise RpcExecutionReverted("EVM eth_call execution reverted")

    monkeypatch.setattr(reader, "_rpc", reverted)
    with pytest.raises(PoolFactoryMismatch, match="call reverted"):
        reader.verify_v2_pool_factory(POOL_A, "0x" + "8" * 40, TOKEN0, TOKEN1, 100)


def test_lookup_v2_pair_address_uses_factory_and_pinned_block(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    factory = "0x" + "8" * 40
    token_a = "0x" + "1" * 40
    token_b = "0x" + "2" * 40
    pair = "0x" + "9" * 40
    calls = []

    def fake(method, params):
        calls.append((method, params))
        return address_word(pair)

    monkeypatch.setattr(reader, "_rpc", fake)
    assert reader.lookup_v2_pair_address(factory, token_a, token_b, 123) == pair
    expected_data = "0xe6a43905" + ("0" * 24 + token_a[2:]) + ("0" * 24 + token_b[2:])
    assert calls == [("eth_call", [{"to": factory, "data": expected_data}, "0x7b"])]


def test_lookup_v2_pair_address_returns_none_for_factory_zero_address(monkeypatch):
    reader = EthereumV2PoolReader(min_request_interval=0)
    monkeypatch.setattr(reader, "_rpc", lambda method, params: address_word("0x" + "0" * 40))
    assert reader.lookup_v2_pair_address(
        "0x" + "8" * 40, "0x" + "1" * 40, "0x" + "2" * 40, 123,
    ) is None


def test_rpc_http_403_is_classified_as_retryable_transport_error(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError

    import pytest

    from arb_engine.rpc import RpcTransportError

    reader = EthereumV2PoolReader(min_request_interval=0)

    def forbidden(*args, **kwargs):
        raise HTTPError("https://example.invalid", 403, "Forbidden", hdrs=None, fp=BytesIO(b""))

    monkeypatch.setattr("arb_engine.rpc.urlopen", forbidden)
    with pytest.raises(RpcTransportError, match="HTTP error: 403"):
        reader._rpc("eth_chainId", [])
    assert reader.rpc_request_count == 1


def test_json_rpc_rate_limit_error_is_retryable_transport_failure(monkeypatch):
    import pytest

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (
                b'{"jsonrpc":"2.0","id":1,"error":'
                b'{"code":-32005,"message":"rate limit exceeded"}}'
            )

    monkeypatch.setattr("arb_engine.rpc.urlopen", lambda *args, **kwargs: FakeResponse())
    reader = EthereumV2PoolReader(min_request_interval=0)
    with pytest.raises(RpcTransportError, match="rate limit"):
        reader._rpc("eth_blockNumber", [])
