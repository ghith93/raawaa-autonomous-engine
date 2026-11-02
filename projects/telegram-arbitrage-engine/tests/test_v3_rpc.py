from __future__ import annotations

import time

import pytest

from arb_engine.rpc import EvmBlockContext, RpcReadError
from arb_engine.v3_math import Q96, get_sqrt_ratio_at_tick
from arb_engine.v3_rpc import EvmV3PoolReader, V3PoolFactoryMismatch


TOKEN0 = "0x" + "1" * 40
TOKEN1 = "0x" + "2" * 40
POOL = "0x" + "3" * 40
FACTORY = "0x" + "8" * 40
BLOCK_HASH = "0x" + "a" * 64


def word(value: int) -> str:
    return f"{value % (1 << 256):064x}"


def address_word(address: str) -> str:
    return "0x" + ("0" * 24) + address[2:].lower()


def signed_arg(data: str, bits: int) -> int:
    value = int(data[10:], 16)
    low = value & ((1 << bits) - 1)
    return low - (1 << bits) if low & (1 << (bits - 1)) else low


def make_fake_rpc(*, bitmap=None, liquidity_net=None, pool_factory=FACTORY,
                  registered_pool=POOL, factory_spacing=60, unlocked=1, slot0_tick=0,
                  reorg=False, code="0x6001"):
    calls = []
    bitmap = {0: (1 << 1) | (1 << 2)} if bitmap is None else bitmap
    liquidity_net = {60: 500, 120: -500} if liquidity_net is None else liquidity_net
    block_reads = 0

    def fake(method, params):
        nonlocal block_reads
        calls.append((method, params))
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getBlockByNumber":
            block_reads += 1
            block_hash = ("0x" + "b" * 64) if reorg and block_reads > 1 else BLOCK_HASH
            return {"hash": block_hash, "timestamp": hex(int(time.time()))}
        if method == "eth_getCode":
            return code
        if method == "eth_call":
            call, block_tag = params
            assert block_tag == "0x64"
            target = call["to"].lower()
            data = call["data"]
            if target == POOL and data == "0xc45a0155":
                return address_word(pool_factory)
            if target == POOL and data == "0x0dfe1681":
                return address_word(TOKEN0)
            if target == POOL and data == "0xd21220a7":
                return address_word(TOKEN1)
            if target == POOL and data == "0xddca3f43":
                return "0x" + word(3000)
            if target == POOL and data == "0xd0c93a7c":
                return "0x" + word(60)
            if target == FACTORY and data.startswith("0x1698ee82"):
                assert int(data[-64:], 16) == 3000
                return address_word(registered_pool)
            if target == FACTORY and data.startswith("0x22afcccb"):
                return "0x" + word(factory_spacing)
            if target == POOL and data == "0x3850c7bd":
                return "0x" + "".join(word(value) for value in (Q96, slot0_tick, 0, 0, 0, 0, unlocked))
            if target == POOL and data == "0x1a686502":
                return "0x" + word(100_000)
            if target == POOL and data.startswith("0x5339c296"):
                word_pos = signed_arg(data, 16)
                return "0x" + word(bitmap.get(word_pos, 0))
            if target == POOL and data.startswith("0xf30dba93"):
                tick = signed_arg(data, 24)
                if tick not in liquidity_net:
                    raise AssertionError(f"Unexpected tick read {tick}")
                return "0x" + "".join(word(value) for value in (
                    1000, liquidity_net[tick], 0, 0, 0, 0, 0, 1,
                ))
        raise AssertionError(f"Unexpected RPC request: {method} {params}")

    return fake, calls


def reader():
    return EvmV3PoolReader(
        expected_chain_id=1, min_request_interval=0, max_rpc_requests=256,
    )


def test_v3_reader_returns_same_block_snapshot_and_simulates_it(monkeypatch):
    rpc = reader()
    fake, calls = make_fake_rpc()
    monkeypatch.setattr(rpc, "_rpc", fake)

    snapshot = rpc.read_snapshot(
        POOL,
        zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
        expected_factory=FACTORY,
    )
    assert snapshot.chain_id == 1
    assert snapshot.pool_address == POOL
    assert snapshot.token0 == TOKEN0
    assert snapshot.token1 == TOKEN1
    assert snapshot.fee_pips == 3000
    assert snapshot.tick_spacing == 60
    assert snapshot.sqrt_price_x96 == Q96
    assert snapshot.tick == 0
    assert snapshot.liquidity == 100_000
    assert snapshot.block_number == 100
    assert snapshot.block_hash == BLOCK_HASH
    assert snapshot.rpc_verified is True
    assert snapshot.source == "evm_rpc_same_block_factory_registered_block_hash_rechecked"
    assert dict(snapshot.bitmap_words) == {0: (1 << 1) | (1 << 2)}
    assert dict(snapshot.initialized_ticks) == {60: 500, 120: -500}
    assert [method for method, _ in calls].count("eth_getBlockByNumber") == 2
    assert all(params[1] == "0x64" for method, params in calls if method == "eth_call")

    from arb_engine.v3_simulator import simulate_v3_swap
    result = simulate_v3_swap(
        snapshot, amount_specified=10**9, zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
    )
    assert result.complete
    assert result.classification == "rpc_snapshot_factory_registered_block_hash_rechecked"
    assert result.crossed_ticks == (60, 120)


def test_v3_reader_encodes_negative_bitmap_word_as_sign_extended_int16(monkeypatch):
    rpc = reader()
    fake, calls = make_fake_rpc(
        bitmap={0: 0, -1: 1 << 255},
        liquidity_net={-60: 500},
    )
    monkeypatch.setattr(rpc, "_rpc", fake)
    snapshot = rpc.read_snapshot(
        POOL,
        zero_for_one=True,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(-60),
        expected_factory=FACTORY,
    )
    bitmap_calls = [
        call["data"] for method, params in calls if method == "eth_call"
        for call, _ in [params] if call["data"].startswith("0x5339c296")
    ]
    assert "0x5339c296" + ("f" * 64) in bitmap_calls
    assert dict(snapshot.initialized_ticks) == {-60: 500}


def test_v3_reader_rejects_pool_factory_mismatch(monkeypatch):
    rpc = reader()
    fake, _ = make_fake_rpc(pool_factory="0x" + "7" * 40)
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(V3PoolFactoryMismatch, match="pool factory"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
            expected_factory=FACTORY,
        )


def test_v3_reader_rejects_factory_get_pool_mismatch(monkeypatch):
    rpc = reader()
    fake, _ = make_fake_rpc(registered_pool="0x" + "7" * 40)
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(V3PoolFactoryMismatch, match="does not register"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
            expected_factory=FACTORY,
        )


def test_v3_reader_rejects_factory_fee_spacing_mismatch(monkeypatch):
    rpc = reader()
    fake, _ = make_fake_rpc(factory_spacing=10)
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(RpcReadError, match="does not match trusted factory fee tier"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
            expected_factory=FACTORY,
        )


def test_v3_reader_rejects_unlocked_false_and_missing_code(monkeypatch):
    rpc = reader()
    fake, _ = make_fake_rpc(unlocked=0)
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(RpcReadError, match="locked"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
            expected_factory=FACTORY,
        )

    rpc = reader()
    fake, _ = make_fake_rpc(code="0x")
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(RpcReadError, match="no valid runtime bytecode"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
            expected_factory=FACTORY,
        )


def test_v3_reader_rechecks_block_hash_after_all_reads(monkeypatch):
    rpc = reader()
    fake, _ = make_fake_rpc(reorg=True)
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(RpcReadError, match="no longer canonical"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
            expected_factory=FACTORY,
        )


def test_v3_reader_enforces_bitmap_word_bound_before_fetching_all_words(monkeypatch):
    rpc = reader()
    fake, calls = make_fake_rpc()
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(RpcReadError, match="bitmap words"):
        rpc.read_snapshot(
            POOL, zero_for_one=True,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(-15360),
            expected_factory=FACTORY,
            max_bitmap_words=1,
        )
    assert not any(
        method == "eth_call" and params[0]["data"].startswith("0x5339c296")
        for method, params in calls
    )


def test_v3_reader_requires_explicit_factory_for_non_ethereum_chain():
    rpc = EvmV3PoolReader(expected_chain_id=8453, min_request_interval=0)
    with pytest.raises(ValueError, match="expected_factory is required"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
        )


def test_v3_reader_rejects_bad_price_limit_before_rpc(monkeypatch):
    rpc = reader()
    calls = []
    monkeypatch.setattr(rpc, "_rpc", lambda method, params: calls.append(method))
    with pytest.raises(ValueError, match="strictly within"):
        rpc.read_snapshot(
            POOL, zero_for_one=False, sqrt_price_limit_x96=1,
            expected_factory=FACTORY,
        )
    assert calls == []


def test_v3_reader_validates_signed_abi_words_and_int_arguments():
    from arb_engine.v3_rpc import EvmV3PoolReader

    assert EvmV3PoolReader._signed_word(word(-60), 24, "tick") == -60
    with pytest.raises(RpcReadError, match="sign extension"):
        EvmV3PoolReader._signed_word(word(1 << 24), 24, "tick")
    assert EvmV3PoolReader._encode_signed(-1, 16, "word") == "f" * 64


def test_v3_reader_reuses_explicit_block_context(monkeypatch):
    rpc = reader()
    fake, calls = make_fake_rpc()
    monkeypatch.setattr(rpc, "_rpc", fake)
    context = EvmBlockContext(1, 100, BLOCK_HASH, int(time.time()))
    snapshot = rpc.read_snapshot(
        POOL, zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
        expected_factory=FACTORY,
        block_context=context,
    )
    assert snapshot.block_hash == BLOCK_HASH
    assert not any(method == "eth_blockNumber" for method, _ in calls)


def test_v3_reader_enforces_initialized_tick_bound(monkeypatch):
    rpc = reader()
    fake, calls = make_fake_rpc()
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(RpcReadError, match="initialized-tick bound"):
        rpc.read_snapshot(
            POOL, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
            expected_factory=FACTORY,
            max_initialized_ticks=1,
        )
    assert not any(
        method == "eth_call" and params[0]["data"].startswith("0xf30dba93")
        for method, params in calls
    )


def test_default_ethereum_factory_is_official_deployment():
    from arb_engine.v3_rpc import UNISWAP_V3_FACTORY_ETHEREUM

    assert UNISWAP_V3_FACTORY_ETHEREUM == "0x1f98431c8ad98523631ae4a59f267346ea31f984"

def test_read_slot0_returns_block_context_and_current_tick(monkeypatch):
    rpc = reader()
    fake, calls = make_fake_rpc()
    monkeypatch.setattr(rpc, "_rpc", fake)
    context, sqrt_price_x96, tick = rpc.read_slot0(POOL)
    assert context.chain_id == 1
    assert context.block_number == 100
    assert context.block_hash == BLOCK_HASH
    assert sqrt_price_x96 == Q96
    assert tick == 0
    assert [method for method, _ in calls].count("eth_getBlockByNumber") == 2


def test_read_slot0_rejects_inconsistent_tick_and_price(monkeypatch):
    rpc = reader()
    fake, _ = make_fake_rpc(slot0_tick=1)
    monkeypatch.setattr(rpc, "_rpc", fake)
    with pytest.raises(RpcReadError, match="inconsistent"):
        rpc.read_slot0(POOL)


def test_v3_reader_defaults_to_keyless_nodely_with_documented_heavy_call_spacing():
    rpc = EvmV3PoolReader()
    assert rpc.endpoint == "https://eth.nodely.dev"
    assert rpc.min_request_interval >= 0.5


def test_v3_reader_fetches_only_initialized_ticks_inside_requested_price_path(monkeypatch):
    rpc = reader()
    fake, calls = make_fake_rpc(
        bitmap={
            -1: 1 << 255,  # tick -60: inside zeroForOne path
            0: (1 << 0) | (1 << 1) | (1 << 2),  # ticks 0, 60, 120
        },
        liquidity_net={-60: 500, 0: -500},
    )
    monkeypatch.setattr(rpc, "_rpc", fake)
    snapshot = rpc.read_snapshot(
        POOL,
        zero_for_one=True,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(-60),
        expected_factory=FACTORY,
        max_bitmap_words=2,
        max_initialized_ticks=2,
    )

    tick_calls = [
        signed_arg(call["data"], 24)
        for method, params in calls
        if method == "eth_call"
        for call, _ in [params]
        if call["data"].startswith("0xf30dba93")
    ]
    assert tick_calls == [0, -60]
    assert dict(snapshot.initialized_ticks) == {0: -500, -60: 500}
