"""Live read-only smoke test for the Ethereum Uniswap V3 USDC/WETH 0.3% pool.

This performs bounded eth_call reads only. It never builds, signs or submits a
transaction and does not estimate profit. Provider fallback restarts the whole
snapshot on a single endpoint; it never combines state from different RPCs.
"""
from __future__ import annotations

from decimal import Decimal
import json
import re

from arb_engine.rpc import DEFAULT_ETHEREUM_RPC, RpcReadError, RpcTransportError
from arb_engine.v3_math import MIN_TICK, get_sqrt_ratio_at_tick
from arb_engine.v3_rpc import DEFAULT_V3_ETHEREUM_RPC, EvmV3PoolReader
from arb_engine.v3_simulator import simulate_v3_swap


POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
RPC_ENDPOINTS = (
    DEFAULT_V3_ETHEREUM_RPC,
    "https://rpcfree.com/ethereum-rpc",
    "https://eth.drpc.org",
    "https://public.1rpc.io/eth",
    DEFAULT_ETHEREUM_RPC,
)
MAX_TOTAL_RPC_REQUESTS = 512


def _run_smoke(reader: EvmV3PoolReader) -> dict[str, object]:
    context, _, current_tick = reader.read_slot0(POOL)
    limit_tick = max(MIN_TICK + 1, current_tick - 120)
    if limit_tick >= current_tick:
        raise RuntimeError("Unable to construct a valid lower price limit from current pool tick")

    limit_sqrt_price = get_sqrt_ratio_at_tick(limit_tick)
    snapshot = reader.read_snapshot(
        POOL,
        zero_for_one=True,
        sqrt_price_limit_x96=limit_sqrt_price,
        block_context=context,
        max_bitmap_words=2,
        max_initialized_ticks=256,
    )
    if not snapshot.rpc_verified:
        raise RuntimeError("Reader did not mark the same-block snapshot as RPC verified")
    if (
        snapshot.token0 != USDC
        or snapshot.token1 != WETH
        or snapshot.fee_pips != 3000
        or snapshot.tick_spacing != 60
    ):
        raise RuntimeError("Live pool identity or fee tier differs from the pinned USDC/WETH 0.3% fixture")
    if snapshot.liquidity <= 0:
        raise RuntimeError("Live pool has zero active liquidity at the pinned block")

    block_tag = hex(snapshot.block_number)
    for token, label in ((USDC, "USDC"), (WETH, "WETH")):
        code = reader._rpc("eth_getCode", [token, block_tag])
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code)
            or len(code) > 250_002
        ):
            raise RuntimeError(f"{label} token contract has invalid or oversized runtime bytecode at the pinned block")
    usdc_decimals = reader._eth_call_uint(USDC, "0x313ce567", block_tag, "USDC decimals")
    weth_decimals = reader._eth_call_uint(WETH, "0x313ce567", block_tag, "WETH decimals")
    if (usdc_decimals, weth_decimals) != (6, 18):
        raise RuntimeError("USDC/WETH decimals do not match the expected 6/18 metadata")
    reader.verify_canonical_block(snapshot.block_number, snapshot.block_hash)

    # One USDC of exact input; raw units avoid floating-point assumptions.
    result = simulate_v3_swap(
        snapshot,
        amount_specified=1_000_000,
        zero_for_one=True,
        sqrt_price_limit_x96=limit_sqrt_price,
        max_steps=64,
    )
    if not result.complete:
        raise RuntimeError(f"Live V3 smoke simulation was incomplete: {result.stop_reason}")
    if result.amount_out <= 0:
        raise RuntimeError("Live V3 smoke simulation returned no WETH output")
    if result.classification != "rpc_snapshot_factory_registered_block_hash_rechecked":
        raise RuntimeError("Simulation provenance did not preserve the verified RPC snapshot classification")

    return {
        "mode": "live_read_only_v3_pool_smoke",
        "status": "success",
        "chain_id": snapshot.chain_id,
        "pool": snapshot.pool_address,
        "token0": {"address": snapshot.token0, "symbol": "USDC", "decimals": usdc_decimals},
        "token1": {"address": snapshot.token1, "symbol": "WETH", "decimals": weth_decimals},
        "fee_pips": snapshot.fee_pips,
        "tick_spacing": snapshot.tick_spacing,
        "block_number": snapshot.block_number,
        "block_hash": snapshot.block_hash,
        "current_tick": snapshot.tick,
        "limit_tick": limit_tick,
        "active_liquidity": str(snapshot.liquidity),
        "amount_in_raw_excluding_fee": str(result.amount_in),
        "fee_amount_raw": str(result.fee_amount),
        "gross_usdc_input": str(Decimal(result.amount_in + result.fee_amount) / (10 ** usdc_decimals)),
        "weth_output": str(Decimal(result.amount_out) / (10 ** weth_decimals)),
        "crossed_ticks": list(result.crossed_ticks),
        "steps": result.steps,
        "classification": result.classification,
        "live_execution": False,
        "transaction_submitted": False,
        "profit_claim": False,
    }


def _is_provider_availability_error(exc: RpcReadError) -> bool:
    return (
        isinstance(exc, RpcTransportError)
        or "code=-32005" in str(exc)
        or "rate limit" in str(exc).casefold()
    )


def main() -> None:
    requests_used = 0
    transport_errors: list[str] = []
    for endpoint in RPC_ENDPOINTS:
        remaining_budget = MAX_TOTAL_RPC_REQUESTS - requests_used
        if remaining_budget <= 0:
            break
        reader = EvmV3PoolReader(
            endpoint,
            expected_chain_id=1,
            timeout_seconds=10,
            # Nodely limits heavy eth_call requests to 2/sec; remain below that ceiling.
            min_request_interval=0.55,
            max_rpc_requests=remaining_budget,
        )
        try:
            result = _run_smoke(reader)
        except RpcReadError as exc:
            if not _is_provider_availability_error(exc):
                raise
            requests_used += reader.rpc_request_count
            transport_errors.append(f"{endpoint}: {exc}")
            continue

        requests_used += reader.rpc_request_count
        result["rpc_endpoint"] = endpoint.split("/", 3)[0] + "//" + endpoint.split("/", 3)[2]
        result["rpc_requests_used"] = requests_used
        result["rpc_requests_budget"] = MAX_TOTAL_RPC_REQUESTS
        print(json.dumps(result, indent=2))
        return

    details = "; ".join(transport_errors) if transport_errors else "request budget exhausted"
    raise RuntimeError(f"All configured public Ethereum RPC endpoints failed: {details}")


if __name__ == "__main__":
    main()
