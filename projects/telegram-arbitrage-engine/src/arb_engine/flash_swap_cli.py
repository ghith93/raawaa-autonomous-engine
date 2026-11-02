from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from .flash_swap import simulate_cross_venue_flash_swap
from .flash_swap_economics import estimate_flash_swap_economics
from .paper_ledger import PaperTradingLedger
from .rpc import DEFAULT_ETHEREUM_RPC, EthereumV2PoolReader
from .venues import resolve_venue_config


def _decimals_for(snapshot, token: str) -> int:
    if token.casefold() == snapshot.state.token0.casefold():
        return snapshot.token0_decimals
    if token.casefold() == snapshot.state.token1.casefold():
        return snapshot.token1_decimals
    raise ValueError("Token is not present in the pinned pool snapshot")


def _human(raw: int, decimals: int) -> str:
    return str(Decimal(raw) / (Decimal(10) ** decimals))


def run_flash_swap_simulation(
    *,
    source_pair: str,
    source_venue: str | None,
    source_fee_bps: int | None,
    destination_pair: str,
    destination_venue: str | None,
    destination_fee_bps: int | None,
    borrowed_token: str,
    amount_borrowed_raw: int,
    chain_id: int = 1,
    rpc_url: str = DEFAULT_ETHEREUM_RPC,
    gas_used: int | None = None,
    gas_price_wei: int | None = None,
    native_token: str | None = None,
    native_quote_pair: str | None = None,
    native_quote_venue: str | None = None,
    native_quote_fee_bps: int | None = None,
    record_paper: bool = False,
    ledger_path: str = "data/paper-trading.sqlite3",
    reader=None,
) -> dict:
    """Read pinned V2 pools, simulate a flash-swap route, and optionally record research events.

    This command never constructs calldata, signs, broadcasts, or places a paper/live order.
    Gas inputs are explicit scenarios, not a measured full-transaction gas quote.
    """
    if (gas_used is None) != (gas_price_wei is None):
        raise ValueError("Supply both --gas-used and --gas-price-wei, or neither")
    has_gas_scenario = gas_used is not None
    if has_gas_scenario and not native_token:
        raise ValueError("A native token address is required for a gas scenario")
    if not has_gas_scenario and any((native_token, native_quote_pair, native_quote_venue, native_quote_fee_bps is not None)):
        raise ValueError("Native-token and quote-pool options require a gas scenario")
    if native_quote_pair and not native_token:
        raise ValueError("A native quote pool requires a native token")

    source_fee, source_factory, source_router, source_venue_info = resolve_venue_config(
        source_venue, chain_id, source_fee_bps, None, None,
    )
    destination_fee, destination_factory, destination_router, destination_venue_info = resolve_venue_config(
        destination_venue, chain_id, destination_fee_bps, None, None,
    )

    reader = reader or EthereumV2PoolReader(rpc_url, expected_chain_id=chain_id)
    pool_requests = [(source_pair, source_fee), (destination_pair, destination_fee)]
    native_quote_info = None
    if native_quote_pair:
        native_quote_fee, native_quote_factory, native_quote_router, native_quote_info = resolve_venue_config(
            native_quote_venue, chain_id, native_quote_fee_bps, None, None,
        )
        pool_requests.append((native_quote_pair, native_quote_fee))
    else:
        native_quote_factory = native_quote_router = None
        native_quote_fee = None

    snapshots = reader.read_pools(pool_requests)
    source_snapshot, destination_snapshot = snapshots[:2]
    native_quote_snapshot = snapshots[2] if len(snapshots) == 3 else None
    source_state = source_snapshot.state
    destination_state = destination_snapshot.state
    block_number = source_state.block_number
    block_hash = source_state.block_hash
    if any(
        snapshot.state.chain_id != source_state.chain_id
        or snapshot.state.block_number != block_number
        or snapshot.state.block_hash.casefold() != block_hash.casefold()
        for snapshot in snapshots
    ):
        raise ValueError("All route and gas quote pools must share one canonical chain and block")

    for snapshot, factory, router in (
        (source_snapshot, source_factory, source_router),
        (destination_snapshot, destination_factory, destination_router),
        (native_quote_snapshot, native_quote_factory, native_quote_router),
    ):
        if snapshot is None:
            continue
        if factory:
            reader.verify_v2_pool_factory(
                snapshot.state.pair_address, factory,
                snapshot.state.token0, snapshot.state.token1, block_number,
            )
        if router and factory:
            reader.verify_v2_router_factory(router, factory, block_number)

    simulation = simulate_cross_venue_flash_swap(
        source_state, destination_state,
        borrowed_token=borrowed_token,
        amount_borrowed_raw=amount_borrowed_raw,
    )
    borrowed_decimals = _decimals_for(source_snapshot, borrowed_token)
    profit_decimals = _decimals_for(source_snapshot, simulation.repayment_token)
    router_sale_quote_raw = None
    if destination_router:
        amounts = reader.quote_v2_router_exact_input(
            destination_router, amount_borrowed_raw,
            [borrowed_token, simulation.repayment_token], block_number,
        )
        router_sale_quote_raw = amounts[-1]
        if router_sale_quote_raw != simulation.sale_proceeds_raw:
            raise ValueError("Destination router quote differs from reserve-math sale output")

    economics = None
    native_quote_metadata = None
    if has_gas_scenario:
        if native_token.casefold() == simulation.repayment_token.casefold():
            if native_quote_snapshot is not None:
                raise ValueError("Native quote pool is unnecessary when repayment token is native")
            native_decimals = profit_decimals
        else:
            if native_quote_snapshot is None:
                raise ValueError("A same-block --native-quote-pair is required when gas token differs from repayment token")
            native_decimals = _decimals_for(native_quote_snapshot, native_token)
            quote_profit_decimals = _decimals_for(native_quote_snapshot, simulation.repayment_token)
            if quote_profit_decimals != profit_decimals:
                raise ValueError("Repayment-token decimals disagree between route and native quote pools")
            native_quote_metadata = {
                "pair_address": native_quote_snapshot.state.pair_address,
                "token0": native_quote_snapshot.state.token0,
                "token1": native_quote_snapshot.state.token1,
                "reserve0_raw": str(native_quote_snapshot.state.reserve0_raw),
                "reserve1_raw": str(native_quote_snapshot.state.reserve1_raw),
                "block_number": native_quote_snapshot.state.block_number,
                "block_hash": native_quote_snapshot.state.block_hash,
            }
        economics = estimate_flash_swap_economics(
            simulation,
            gas_used=gas_used,
            gas_price_wei=gas_price_wei,
            native_token=native_token,
            native_token_decimals=native_decimals,
            profit_token_decimals=profit_decimals,
            native_quote_pool=native_quote_snapshot.state if native_quote_snapshot else None,
        )

    reader.verify_canonical_block(block_number, block_hash)
    output = {
        "mode": "read_only_v2_flash_swap_simulation",
        "rpc_endpoint": rpc_url,
        "chain_id": source_state.chain_id,
        "block_number": block_number,
        "block_hash": block_hash,
        "block_timestamp": source_snapshot.block_timestamp,
        "source_pool": source_state.pair_address,
        "destination_pool": destination_state.pair_address,
        "source_fee_bps_assumed": source_state.fee_bps,
        "destination_fee_bps_assumed": destination_state.fee_bps,
        "source_venue_registry_id": source_venue_info.id if source_venue_info else None,
        "destination_venue_registry_id": destination_venue_info.id if destination_venue_info else None,
        "source_factory_verification": "passed" if source_factory else "not_requested",
        "destination_factory_verification": "passed" if destination_factory else "not_requested",
        "borrowed_token": borrowed_token.lower(),
        "borrowed_token_decimals": borrowed_decimals,
        "amount_borrowed_raw": str(amount_borrowed_raw),
        "amount_borrowed_human": _human(amount_borrowed_raw, borrowed_decimals),
        "repayment_token": simulation.repayment_token,
        "repayment_token_decimals": profit_decimals,
        "repayment_amount_raw": str(simulation.repayment_amount_raw),
        "repayment_amount_human": _human(simulation.repayment_amount_raw, profit_decimals),
        "sale_proceeds_raw": str(simulation.sale_proceeds_raw),
        "sale_proceeds_human": _human(simulation.sale_proceeds_raw, profit_decimals),
        "gross_profit_raw": str(simulation.gross_profit_raw),
        "gross_profit_human": _human(simulation.gross_profit_raw, profit_decimals),
        "router_sale_quote_raw": str(router_sale_quote_raw) if router_sale_quote_raw is not None else None,
        "classification": simulation.classification,
        "gas_economics": None,
        "paper_ledger": None,
        "transaction_submitted": False,
        "wallet_or_signer_used": False,
        "live_execution": False,
        "warning": (
            "Gross reserve-math surplus is not net profit. This command does not construct or "
            "submit a transaction. Gas is only estimated when explicit gas-used and gas-price "
            "scenarios are supplied; MEV, inclusion risk, transfer taxes and unmodelled overhead "
            "remain excluded."
        ),
    }
    if economics is not None:
        output["gas_economics"] = {
            "classification": economics.classification,
            "gas_used_scenario": economics.gas_used,
            "gas_price_wei_scenario": str(economics.gas_price_wei),
            "native_token": economics.native_token,
            "native_token_decimals": economics.native_token_decimals,
            "gas_cost_native_raw": str(economics.gas_cost_native_raw),
            "gas_cost_profit_token_raw": str(economics.gas_cost_profit_token_raw),
            "net_profit_raw": str(economics.net_profit_raw),
            "net_profit_human": _human(economics.net_profit_raw, profit_decimals),
            "break_even_gas_price_wei": str(economics.break_even_gas_price_wei),
            "net_positive_under_scenario": economics.net_positive,
            "native_quote_pool": native_quote_metadata,
        }

    if record_paper:
        now = datetime.now(timezone.utc)
        run_id = uuid.uuid4().hex
        candidate_id = "flashswap-" + hashlib.sha256(
            f"{run_id}|{source_state.chain_id}|{block_hash}|{source_state.pair_address}|"
            f"{destination_state.pair_address}|{borrowed_token.lower()}|{amount_borrowed_raw}".encode()
        ).hexdigest()[:32]
        ledger = PaperTradingLedger(ledger_path)
        common = {
            "candidate_id": candidate_id,
            "occurred_at": now,
        }
        ledger.append_event(
            **common, event_id=f"{candidate_id}:detected",
            event_type="candidate_detected",
            payload={"source": "flash-swap-sim", "classification": "read_only_research_candidate"},
        )
        ledger.append_event(
            **common, event_id=f"{candidate_id}:snapshot",
            event_type="snapshot_captured",
            payload={
                "rpc_endpoint": rpc_url,
                "pool_addresses": [item.state.pair_address for item in snapshots],
                "token_decimals": {
                    item.state.token0: item.token0_decimals for item in snapshots
                } | {
                    item.state.token1: item.token1_decimals for item in snapshots
                },
            },
            chain_id=source_state.chain_id,
            block_number=block_number,
            block_hash=block_hash,
        )
        ledger.append_event(
            **common, event_id=f"{candidate_id}:route",
            event_type="route_simulated",
            payload={
                "classification": simulation.classification,
                "amount_borrowed_raw": str(amount_borrowed_raw),
                "repayment_amount_raw": str(simulation.repayment_amount_raw),
                "sale_proceeds_raw": str(simulation.sale_proceeds_raw),
            },
            chain_id=source_state.chain_id,
            block_number=block_number,
            block_hash=block_hash,
            strategy_id="v2-cross-venue-flash-swap",
            source_pool=source_state.pair_address,
            destination_pool=destination_state.pair_address,
            token_in=borrowed_token,
            token_out=simulation.repayment_token,
            gross_profit_raw=simulation.gross_profit_raw,
        )
        recorded = 3
        if economics is not None:
            ledger.append_event(
                **common, event_id=f"{candidate_id}:gas",
                event_type="gas_scenario_evaluated",
                payload={
                    "classification": economics.classification,
                    "native_token": economics.native_token,
                    "native_quote_pool": native_quote_metadata,
                },
                chain_id=source_state.chain_id,
                block_number=block_number,
                block_hash=block_hash,
                gas_used=economics.gas_used,
                gas_price_wei=economics.gas_price_wei,
                gas_cost_profit_raw=economics.gas_cost_profit_token_raw,
                net_profit_raw=economics.net_profit_raw,
            )
            recorded += 1
        if simulation.gross_profit_raw <= 0 or (economics is not None and not economics.net_positive):
            ledger.append_event(
                **common, event_id=f"{candidate_id}:rejected",
                event_type="rejected",
                payload={
                    "reason": "non_positive_gross_surplus" if simulation.gross_profit_raw <= 0
                    else "non_positive_net_under_supplied_gas_scenario",
                },
                chain_id=source_state.chain_id,
                block_number=block_number,
                block_hash=block_hash,
            )
            recorded += 1
        if not ledger.verify_chain(candidate_id):
            raise RuntimeError("Paper ledger chain verification failed after capture")
        output["paper_ledger"] = {
            "recorded": True,
            "candidate_id": candidate_id,
            "event_count": recorded,
            "chain_verified": True,
            "classification": "local_research_events_only_no_order_created_or_submitted",
        }
    return output
