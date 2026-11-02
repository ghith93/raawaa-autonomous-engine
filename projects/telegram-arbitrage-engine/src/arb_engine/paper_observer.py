"""Forward-only observer for previously captured V2 flash-swap research candidates.

This module reads later canonical pool snapshots and appends observations. It never
constructs, signs, broadcasts, or submits transactions.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from .d1_paper_ledger import D1PaperTradingLedger
from .flash_swap import simulate_cross_venue_flash_swap
from .flash_swap_economics import estimate_flash_swap_economics
from .paper_ledger import PaperTradingLedger
from .rpc import DEFAULT_ETHEREUM_RPC, EthereumV2PoolReader


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json")
    if not isinstance(raw, str):
        raise ValueError("Paper event is missing its canonical payload")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Paper event payload is malformed") from exc
    if not isinstance(value, dict):
        raise ValueError("Paper event payload must be an object")
    return value


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.isdigit():
        result = int(value)
    else:
        raise ValueError(f"{name} must be an integer")
    if result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def _resolve_ledger(ledger_path: str):
    backend = os.getenv("RAAWAA_PAPER_LEDGER_BACKEND", "sqlite").strip().casefold()
    if backend == "sqlite":
        return PaperTradingLedger(ledger_path)
    if backend == "d1":
        # The D1 adapter is explicitly selected and fails closed; never fall back to SQLite.
        return D1PaperTradingLedger()
    raise ValueError("RAAWAA_PAPER_LEDGER_BACKEND must be either 'sqlite' or 'd1'")


def observe_flash_swap_outcome(
    candidate_id: str,
    *,
    ledger=None,
    ledger_path: str = "data/paper-trading.sqlite3",
    rpc_url: str = DEFAULT_ETHEREUM_RPC,
    reader=None,
) -> dict[str, Any]:
    """Observe one captured candidate at a later canonical block, with no look-ahead.

    Only candidates captured with observer_schema=1 are supported. The observer reads
    current pool state, requires a strictly later block and timestamp, then appends one
    idempotent outcome event keyed by the observed block hash.
    """
    if not isinstance(candidate_id, str) or not candidate_id.strip() or len(candidate_id) > 128:
        raise ValueError("candidate_id must contain 1 to 128 characters")
    ledger = ledger or _resolve_ledger(ledger_path)
    if not ledger.verify_chain(candidate_id):
        raise ValueError("Paper candidate hash chain is missing or invalid")
    events = ledger.events(candidate_id, limit=500)
    if not events:
        raise ValueError("Paper candidate does not exist")

    detected = next((item for item in events if item.get("event_type") == "candidate_detected"), None)
    captured = next((item for item in events if item.get("event_type") == "snapshot_captured"), None)
    route_event = next((item for item in events if item.get("event_type") == "route_simulated"), None)
    if detected is None or captured is None or route_event is None:
        raise ValueError("Candidate is missing detection, snapshot, or route evidence")

    metadata = _payload(detected)
    if metadata.get("observer_schema") != 1:
        raise ValueError("Candidate predates observer metadata; recapture it with the current flash-swap simulator")
    route = metadata.get("route_parameters")
    if not isinstance(route, dict):
        raise ValueError("Candidate is missing observer route parameters")
    source_pool = route_event.get("source_pool")
    destination_pool = route_event.get("destination_pool")
    borrowed_token = route_event.get("token_in")
    strategy_id = route_event.get("strategy_id")
    if not all(isinstance(value, str) and value for value in (
        source_pool, destination_pool, borrowed_token, strategy_id,
    )):
        raise ValueError("Candidate route provenance is incomplete")

    source_fee = _integer(route.get("source_fee_bps"), "source_fee_bps")
    destination_fee = _integer(route.get("destination_fee_bps"), "destination_fee_bps")
    amount_borrowed = _integer(route.get("amount_borrowed_raw"), "amount_borrowed_raw", minimum=1)
    route_borrowed_token = route.get("borrowed_token")
    if not isinstance(route_borrowed_token, str) or route_borrowed_token.casefold() != borrowed_token.casefold():
        raise ValueError("Candidate borrowed-token metadata does not match its route event")

    chain_id = _integer(captured.get("chain_id"), "candidate chain_id", minimum=1)
    candidate_block = _integer(captured.get("block_number"), "candidate block_number")
    candidate_hash = captured.get("block_hash")
    if not isinstance(candidate_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", candidate_hash):
        raise ValueError("Candidate snapshot is missing its canonical block hash")
    if (
        _integer(route_event.get("chain_id"), "route chain_id", minimum=1) != chain_id
        or _integer(route_event.get("block_number"), "route block_number") != candidate_block
        or str(route_event.get("block_hash", "")).casefold() != candidate_hash.casefold()
    ):
        raise ValueError("Candidate route and snapshot must share the same canonical block")
    route_payload = _payload(route_event)
    if route_payload.get("amount_borrowed_raw") != str(amount_borrowed):
        raise ValueError("Candidate route amount does not match observer metadata")
    snapshot_payload = _payload(captured)
    pool_addresses = snapshot_payload.get("pool_addresses")
    if not isinstance(pool_addresses, list) or any(
        address.casefold() not in {str(value).casefold() for value in pool_addresses}
        for address in (source_pool, destination_pool)
    ):
        raise ValueError("Candidate snapshot does not contain its route pools")
    candidate_time = datetime.fromisoformat(str(detected.get("occurred_at")).replace("Z", "+00:00"))
    if candidate_time.tzinfo is None or candidate_time.utcoffset() is None:
        raise ValueError("Candidate timestamp must be timezone-aware")

    gas = metadata.get("gas_scenario")
    gas_event = next((item for item in events if item.get("event_type") == "gas_scenario_evaluated"), None)
    if gas is not None and not isinstance(gas, dict):
        raise ValueError("Candidate gas scenario metadata is malformed")
    if (gas is None) != (gas_event is None):
        raise ValueError("Candidate gas scenario metadata and ledger event disagree")
    if gas_event is not None and (
        _integer(gas_event.get("chain_id"), "gas chain_id", minimum=1) != chain_id
        or _integer(gas_event.get("block_number"), "gas block_number") != candidate_block
        or str(gas_event.get("block_hash", "")).casefold() != candidate_hash.casefold()
    ):
        raise ValueError("Candidate gas scenario must share the original canonical block")
    pool_requests = [(source_pool, source_fee), (destination_pool, destination_fee)]
    native_quote_pair = None
    if gas is not None:
        native_token = gas.get("native_token")
        if not isinstance(native_token, str) or not native_token:
            raise ValueError("Candidate gas scenario is missing its native token")
        native_quote_pair = gas.get("native_quote_pair")
        if native_quote_pair:
            if not isinstance(native_quote_pair, str) or native_quote_pair.casefold() not in {
                str(value).casefold() for value in pool_addresses
            }:
                raise ValueError("Candidate snapshot does not contain its native quote pool")
            native_quote_fee = _integer(gas.get("native_quote_fee_bps"), "native_quote_fee_bps")
            pool_requests.append((native_quote_pair, native_quote_fee))

    reader = reader or EthereumV2PoolReader(rpc_url, expected_chain_id=chain_id)
    # Re-check the candidate's original block before reading forward state. A
    # reorged candidate must never be compared with a later canonical snapshot.
    reader.verify_canonical_block(candidate_block, candidate_hash)
    snapshots = reader.read_pools(pool_requests)
    if len(snapshots) != len(pool_requests):
        raise ValueError("RPC reader returned an incomplete forward-observation snapshot")
    observed = snapshots[0]
    observed_state = observed.state
    for item, (expected_pool, _fee_bps) in zip(snapshots, pool_requests, strict=True):
        if item.state.pair_address.casefold() != expected_pool.casefold():
            raise ValueError("RPC reader returned a different pool than the requested snapshot")
    if observed_state.chain_id != chain_id:
        raise ValueError("Forward observation belongs to a different chain")
    if observed_state.block_number <= candidate_block:
        raise ValueError("No-look-ahead rule: observed block must be strictly later than candidate block")
    if observed.block_timestamp <= candidate_time.timestamp():
        raise ValueError("No-look-ahead rule: observed block timestamp must be later than candidate timestamp")
    if any(
        item.state.chain_id != chain_id
        or item.state.block_number != observed_state.block_number
        or item.state.block_hash.casefold() != observed_state.block_hash.casefold()
        for item in snapshots
    ):
        raise ValueError("All forward-observation pools must share one canonical chain and block")
    # Re-check both ends after the read to detect a reorg during observation.
    reader.verify_canonical_block(candidate_block, candidate_hash)
    reader.verify_canonical_block(observed_state.block_number, observed_state.block_hash)

    initial_decimals = snapshot_payload.get("token_decimals")
    if not isinstance(initial_decimals, dict):
        raise ValueError("Candidate snapshot is missing token-decimal provenance")
    original = {str(key).casefold(): value for key, value in initial_decimals.items()}
    for item in snapshots:
        for token, decimals in (
            (item.state.token0, item.token0_decimals),
            (item.state.token1, item.token1_decimals),
        ):
            token_key = token.casefold()
            if token_key not in original:
                raise ValueError("Forward snapshot contains a token absent from candidate decimal provenance")
            if original[token_key] != decimals:
                raise ValueError("Token decimals changed since candidate capture; refusing incomparable outcome")

    simulation = simulate_cross_venue_flash_swap(
        observed_state, snapshots[1].state,
        borrowed_token=borrowed_token,
        amount_borrowed_raw=amount_borrowed,
    )
    if simulation.repayment_token.casefold() != str(route_event.get("token_out", "")).casefold():
        raise ValueError("Forward route repayment token differs from the captured candidate")
    economics = None
    if gas is not None:
        gas_used = _integer(gas.get("gas_used"), "gas_used", minimum=1)
        gas_price_wei = _integer(gas.get("gas_price_wei"), "gas_price_wei")
        native_token = gas["native_token"]
        native_quote_snapshot = snapshots[2] if len(snapshots) == 3 else None
        if native_token.casefold() != simulation.repayment_token.casefold() and native_quote_snapshot is None:
            raise ValueError("Candidate is missing the native/repayment-token quote pool")
        decimals_by_token = {
            item.state.token0.casefold(): item.token0_decimals
            for item in snapshots
        } | {
            item.state.token1.casefold(): item.token1_decimals
            for item in snapshots
        }
        profit_decimals = decimals_by_token.get(simulation.repayment_token.casefold())
        native_decimals = decimals_by_token.get(native_token.casefold())
        if profit_decimals is None or native_decimals is None:
            raise ValueError("Forward snapshot is missing token-decimal metadata")
        economics = estimate_flash_swap_economics(
            simulation,
            gas_used=gas_used,
            gas_price_wei=gas_price_wei,
            native_token=native_token,
            native_token_decimals=native_decimals,
            profit_token_decimals=profit_decimals,
            native_quote_pool=native_quote_snapshot.state if native_quote_snapshot else None,
        )

    observed_time = datetime.fromtimestamp(observed.block_timestamp, timezone.utc)
    event_id = f"{candidate_id}:outcome:{observed_state.block_hash[2:].lower()}"
    gas_payload = None
    gas_fields = {}
    if economics is not None:
        gas_payload = {
            "gas_used_scenario": economics.gas_used,
            "gas_price_wei_scenario": str(economics.gas_price_wei),
            "gas_cost_profit_token_raw": str(economics.gas_cost_profit_token_raw),
            "net_profit_raw": str(economics.net_profit_raw),
            "net_positive_under_same_gas_scenario": economics.net_positive,
        }
        gas_fields = {
            "gas_used": economics.gas_used,
            "gas_price_wei": economics.gas_price_wei,
            "gas_cost_profit_raw": economics.gas_cost_profit_token_raw,
            "net_profit_raw": economics.net_profit_raw,
        }
    append_result = ledger.append_event(
        candidate_id=candidate_id,
        event_id=event_id,
        event_type="outcome_observed",
        occurred_at=observed_time,
        payload={
            "classification": "forward_only_paper_observation_no_order_or_execution",
            "candidate_block_number": candidate_block,
            "observed_block_number": observed_state.block_number,
            "observed_block_age": observed_state.block_number - candidate_block,
            "amount_borrowed_raw": str(amount_borrowed),
            "repayment_amount_raw": str(simulation.repayment_amount_raw),
            "sale_proceeds_raw": str(simulation.sale_proceeds_raw),
            "gross_profit_raw": str(simulation.gross_profit_raw),
            "gas_scenario": gas_payload,
        },
        chain_id=chain_id,
        block_number=observed_state.block_number,
        block_hash=observed_state.block_hash,
        strategy_id=strategy_id,
        source_pool=source_pool,
        destination_pool=destination_pool,
        token_in=borrowed_token,
        token_out=simulation.repayment_token,
        gross_profit_raw=simulation.gross_profit_raw,
        **gas_fields,
    )
    if not ledger.verify_chain(candidate_id):
        raise RuntimeError("Paper ledger chain verification failed after forward observation")
    replayed = append_result is False or (
        isinstance(append_result, dict) and append_result.get("replay") is True
    )
    return {
        "mode": "read_only_forward_paper_observation",
        "candidate_id": candidate_id,
        "candidate_block_number": candidate_block,
        "observed_block_number": observed_state.block_number,
        "observed_block_hash": observed_state.block_hash,
        "block_age": observed_state.block_number - candidate_block,
        "gross_profit_raw": str(simulation.gross_profit_raw),
        "gas_economics": gas_payload,
        "event_id": event_id,
        "recorded": not replayed,
        "replayed": replayed,
        "chain_verified": True,
        "transaction_submitted": False,
        "wallet_or_signer_used": False,
        "live_execution": False,
        "classification": "forward_only_paper_observation_no_order_or_execution",
    }
