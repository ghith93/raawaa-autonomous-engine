"""Live read-only smoke test for the Ethereum Uniswap V3 USDC/WETH 0.3% pool.

Only public HTTPS JSON-RPC endpoints are used. Each endpoint attempt starts a
fresh reader and block context; a transport-level outage may fail over, while
pool identity, ABI, block-canonicality or arithmetic failures stop the smoke.
No transaction is built, signed or submitted.
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
import re
from urllib.parse import urlsplit

from arb_engine.rpc import DEFAULT_ETHEREUM_RPC, RpcReadError
from arb_engine.v3_math import MIN_TICK, get_sqrt_ratio_at_tick
from arb_engine.v3_rpc import EvmV3PoolReader
from arb_engine.v3_simulator import simulate_v3_swap


POOL = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
FALLBACK_ENDPOINTS = (
    DEFAULT_ETHEREUM_RPC,
    "https://eth.drpc.org",
    "https://public.1rpc.io/eth",
)


def configured_endpoints() -> tuple[str, ...]:
    configured = os.environ.get("V3_SMOKE_RPC_URLS", "").strip()
    candidates = tuple(value.strip() for value in configured.split(",") if value.strip()) if configured else FALLBACK_ENDPOINTS
    # Keep only unique HTTPS endpoints; the reader performs strict URL validation.
    unique = tuple(dict.fromkeys(candidates))
    if not unique:
        raise RuntimeError("No V3 smoke RPC endpoints configured")
    return unique


def is_retryable_transport_error(exc: RpcReadError) -> bool:
    message = str(exc)
    if message.startswith("EVM RPC request failed:"):
        return any(kind in message for kind in ("URLError", "TimeoutError"))
    prefix = "EVM RPC HTTP error: "
    if message.startswith(prefix):
        try:
            status = int(message[len(prefix):])
        except ValueError:
            return False
        return status in (403, 408, 425, 429) or 500 <= status <= 599
    return False


def _has_valid_runtime_bytecode(code: object) -> bool:
    return (
        isinstance(code, str)
        and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code) is not None
        and len(code) <= 250_002
    )


def run_endpoint(endpoint: str) -> dict[str, object]:
    reader = EvmV3PoolReader(
        endpoint,
        expected_chain_id=1,
        timeout_seconds=10,
        min_request_interval=0.05,
        max_rpc_requests=512,
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
        if not _has_valid_runtime_bytecode(code):
            raise RuntimeError(f"{label} token contract has invalid or oversized runtime bytecode at the pinned block")
    usdc_decimals = reader._eth_call_uint(USDC, "0x313ce567", block_tag, "USDC decimals")
    weth_decimals = reader._eth_call_uint(WETH, "0x313ce567", block_tag, "WETH decimals")
    if (usdc_decimals, weth_decimals) != (6, 18):
        raise RuntimeError("USDC/WETH decimals do not match the expected 6/18 metadata")
    reader.verify_canonical_block(snapshot.block_number, snapshot.block_hash)

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

    return {
        "mode": "live_read_only_v3_pool_smoke",
        "status": "success",
        "rpc_endpoint_host": urlsplit(endpoint).hostname,
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
        "rpc_requests_used": reader.rpc_request_count,
        "rpc_requests_budget": 512,
        "classification": result.classification,
        "live_execution": False,
        "transaction_submitted": False,
        "profit_claim": False,
    }


def main() -> None:
    failures: list[dict[str, str]] = []
    endpoints = configured_endpoints()
    for index, endpoint in enumerate(endpoints):
        try:
            result = run_endpoint(endpoint)
            result["rpc_endpoint_attempts"] = index + 1
            result["previous_endpoint_failures"] = failures
            print(json.dumps(result, indent=2))
            return
        except RpcReadError as exc:
            if not is_retryable_transport_error(exc) or index == len(endpoints) - 1:
                raise
            failures.append({
                "endpoint_host": urlsplit(endpoint).hostname or "unknown",
                "error": str(exc),
            })
        # Do not fail over on invalid pool state, malformed ABI, reorgs or math errors.
    raise RuntimeError("All configured V3 smoke RPC endpoints failed")


if __name__ == "__main__":
    main()
