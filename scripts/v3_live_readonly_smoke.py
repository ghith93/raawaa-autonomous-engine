"""Live read-only smoke test for the Ethereum Uniswap V3 USDC/WETH 0.3% pool.

Uses keyless public RPC endpoints with bounded failover. It performs eth_call
and eth_getCode reads only; it never builds, signs or submits a transaction.
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
import re
from urllib.parse import urlsplit

from arb_engine.rpc import RpcReadError
from arb_engine.v3_math import MIN_TICK, get_sqrt_ratio_at_tick
from arb_engine.v3_rpc import DEFAULT_V3_ETHEREUM_RPC, EvmV3PoolReader
from arb_engine.v3_simulator import simulate_v3_swap


POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
MAX_TOTAL_RPC_REQUESTS = 512


def safe_endpoint_label(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.hostname:
        return "custom_rpc_endpoint"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def endpoint_candidates() -> list[str]:
    override = os.environ.get("RAAWAA_V3_RPC_URL")
    if override:
        return [override]
    return [
        DEFAULT_V3_ETHEREUM_RPC,
        "https://rpcfree.com/ethereum-rpc",
        "https://cloudflare-eth.com",
        "https://ethereum-rpc.publicnode.com",
    ]


def is_endpoint_transport_failure(exc: RpcReadError) -> bool:
    message = str(exc)
    return message.startswith((
        "EVM RPC HTTP error:",
        "EVM RPC request failed:",
        "EVM RPC returned an error (",
    ))


def run_smoke(endpoint: str, request_budget: int) -> tuple[dict, int]:
    reader = EvmV3PoolReader(
        endpoint,
        expected_chain_id=1,
        timeout_seconds=10,
        # Nodely limits heavy eth_call requests to 2/sec; this interval is below that ceiling.
        min_request_interval=0.55,
        max_rpc_requests=request_budget,
    )
    context, _, current_tick = reader.read_slot0(POOL)
    limit_tick = max(MIN_TICK + 1, current_tick - 120)
    if limit_tick >= current_tick:
        raise RuntimeError("Unable to construct a valid lower price limit from current pool tick")

    snapshot = reader.read_snapshot(
        POOL,
        zero_for_one=True,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(limit_tick),
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
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(limit_tick),
        max_steps=64,
    )
    if not result.complete:
        raise RuntimeError(f"Live V3 smoke simulation was incomplete: {result.stop_reason}")
    if result.amount_out <= 0:
        raise RuntimeError("Live V3 smoke simulation returned no WETH output")
    if result.classification != "rpc_snapshot_factory_registered_block_hash_rechecked":
        raise RuntimeError("Simulation provenance did not preserve the verified RPC snapshot classification")

    return ({
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
        "rpc_endpoint": safe_endpoint_label(endpoint),
        "rpc_requests_used": reader.rpc_request_count,
        "classification": result.classification,
        "live_execution": False,
        "transaction_submitted": False,
        "profit_claim": False,
    }, reader.rpc_request_count)


def main() -> None:
    failures = []
    total_requests = 0
    for endpoint in endpoint_candidates():
        remaining_budget = MAX_TOTAL_RPC_REQUESTS - total_requests
        if remaining_budget <= 0:
            break
        try:
            result, used = run_smoke(endpoint, remaining_budget)
            total_requests += used
            result["rpc_requests_used_across_all_endpoints"] = total_requests
            result["rpc_requests_budget"] = MAX_TOTAL_RPC_REQUESTS
            result["rpc_fallback_failures"] = failures
            print(json.dumps(result, indent=2))
            return
        except RpcReadError as exc:
            if not is_endpoint_transport_failure(exc):
                raise
            total_requests += getattr(locals().get("reader"), "rpc_request_count", 0)
            failures.append({
                "endpoint": safe_endpoint_label(endpoint),
                "error": str(exc),
            })
    raise RuntimeError(
        "All configured keyless Ethereum RPC endpoints failed: "
        + json.dumps({"failures": failures, "rpc_requests_used": total_requests})
    )


if __name__ == "__main__":
    main()
