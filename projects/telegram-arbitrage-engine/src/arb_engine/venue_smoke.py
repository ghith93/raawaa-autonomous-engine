from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from typing import Any, Protocol

from .venues import Venue


WETH_MAINNET = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_MAINNET = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
ZERO_ADDRESS = "0x" + "0" * 40
FACTORY_SELECTOR = "0xc45a0155"  # factory()
GET_PAIR_SELECTOR = "0xe6a43905"  # getPair(address,address)
TOKEN0_SELECTOR = "0x0dfe1681"  # token0()
TOKEN1_SELECTOR = "0xd21220a7"  # token1()


class VenueVerificationError(RuntimeError):
    pass


def _safe_endpoint_label(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.hostname:
        return "custom_rpc_endpoint"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


class RpcCaller(Protocol):
    def call(self, method: str, params: list[Any]) -> Any: ...


class JsonRpcClient:
    """Small bounded JSON-RPC client for public, read-only verification calls."""

    def __init__(self, endpoint: str, timeout_seconds: float = 8.0) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Venue verification requires an HTTPS RPC endpoint")
        if not 1 <= timeout_seconds <= 20:
            raise ValueError("RPC timeout must be between 1 and 20 seconds")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.request_id = 0
        self.min_request_interval_seconds = 3.1
        self._last_request_at = 0.0

    def call(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": self.request_id + 1, "method": method, "params": params}
        self.request_id += 1
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "RaawaaAutonomousEngine/0.16 (read-only venue verification; github.com/ghith93/raawaa-autonomous-engine)",
            },
            method="POST",
        )
        for attempt in range(4):
            wait = self.min_request_interval_seconds - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if body.get("id") != payload["id"]:
                    raise VenueVerificationError("JSON-RPC response ID mismatch")
                if body.get("error"):
                    raise VenueVerificationError(
                        f"{method} returned JSON-RPC error: {body['error'].get('message', 'unknown error')}"
                    )
                if "result" not in body:
                    raise VenueVerificationError(f"{method} response has no result")
                return body["result"]
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise VenueVerificationError(f"{method} HTTP {exc.code}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else 2 ** (attempt + 1)
                except ValueError:
                    delay = 2 ** (attempt + 1)
                time.sleep(min(15.0, max(2.0, delay)))
            except (URLError, TimeoutError, OSError) as exc:
                if attempt == 3:
                    raise VenueVerificationError(f"{method} transport failure ({type(exc).__name__})") from exc
                time.sleep(1 + attempt)
        raise VenueVerificationError(f"{method} exhausted bounded retries")


def _address_word(address: str) -> str:
    if not address.startswith("0x") or len(address) != 42:
        raise VenueVerificationError("Invalid address in venue verification")
    return address[2:].lower().rjust(64, "0")


def _decode_address(value: Any, method: str) -> str:
    if not isinstance(value, str) or len(value) != 66 or not value.startswith("0x"):
        raise VenueVerificationError(f"{method} returned malformed ABI data")
    if value[2:26] != "0" * 24:
        raise VenueVerificationError(f"{method} returned a non-canonical ABI address")
    address = "0x" + value[-40:].lower()
    if address == ZERO_ADDRESS:
        raise VenueVerificationError(f"{method} returned the zero address")
    return address


def _require_code(rpc: RpcCaller, address: str, block_tag: str, label: str) -> None:
    code = rpc.call("eth_getCode", [address, block_tag])
    if not isinstance(code, str) or not code.startswith("0x") or len(code) <= 2:
        raise VenueVerificationError(f"{label} has no runtime bytecode at pinned block")
    try:
        bytecode = bytes.fromhex(code[2:])
    except ValueError as exc:
        raise VenueVerificationError(f"{label} returned malformed bytecode") from exc
    if not any(bytecode):
        raise VenueVerificationError(f"{label} has only zero bytecode at pinned block")


def _call_address(
    rpc: RpcCaller, target: str, calldata: str, block_tag: str, label: str,
) -> str:
    value = rpc.call("eth_call", [{"to": target, "data": calldata}, block_tag])
    return _decode_address(value, label)


def verify_registry_venues(
    rpc: RpcCaller,
    venues: tuple[Venue, ...] | list[Venue],
    expected_chain_id: int = 1,
) -> dict[str, Any]:
    if expected_chain_id != 1:
        raise VenueVerificationError("The live venue smoke check currently supports Ethereum mainnet only")
    if not venues:
        raise VenueVerificationError("No source-documented venues are registered for this chain")

    chain_id = int(rpc.call("eth_chainId", []), 16)
    if chain_id != expected_chain_id:
        raise VenueVerificationError(f"RPC chain ID {chain_id} does not match expected {expected_chain_id}")
    block_number = int(rpc.call("eth_blockNumber", []), 16)
    if block_number <= 0:
        raise VenueVerificationError("RPC returned an invalid latest block number")
    block_tag = hex(block_number)
    block = rpc.call("eth_getBlockByNumber", [block_tag, False])
    block_hash = block.get("hash") if isinstance(block, dict) else None
    if not isinstance(block_hash, str) or len(block_hash) != 66 or not block_hash.startswith("0x"):
        raise VenueVerificationError("RPC returned a malformed pinned block hash")

    expected_tokens = {WETH_MAINNET.lower(), USDC_MAINNET.lower()}
    pair_results = []
    for venue in venues:
        if venue.chain_id != expected_chain_id:
            raise VenueVerificationError(f"Venue {venue.id} is not registered for chain {expected_chain_id}")
        _require_code(rpc, venue.factory, block_tag, f"{venue.id} factory")
        _require_code(rpc, venue.router02, block_tag, f"{venue.id} Router02")

        router_factory = _call_address(
            rpc, venue.router02, FACTORY_SELECTOR, block_tag, f"{venue.id} router.factory()",
        )
        if router_factory != venue.factory.lower():
            raise VenueVerificationError(f"{venue.id} Router02 factory does not match registry")

        get_pair_data = (
            GET_PAIR_SELECTOR
            + _address_word(WETH_MAINNET)
            + _address_word(USDC_MAINNET)
        )
        pair_address = _call_address(
            rpc, venue.factory, get_pair_data, block_tag, f"{venue.id} factory.getPair(WETH,USDC)",
        )
        _require_code(rpc, pair_address, block_tag, f"{venue.id} WETH/USDC pair")

        pair_factory = _call_address(
            rpc, pair_address, FACTORY_SELECTOR, block_tag, f"{venue.id} pair.factory()",
        )
        token0 = _call_address(
            rpc, pair_address, TOKEN0_SELECTOR, block_tag, f"{venue.id} pair.token0()",
        )
        token1 = _call_address(
            rpc, pair_address, TOKEN1_SELECTOR, block_tag, f"{venue.id} pair.token1()",
        )
        if pair_factory != venue.factory.lower():
            raise VenueVerificationError(f"{venue.id} pair.factory() does not match registry")
        if {token0, token1} != expected_tokens:
            raise VenueVerificationError(f"{venue.id} WETH/USDC pair token addresses do not match expected tokens")
        pair_results.append({
            "venue_id": venue.id,
            "factory": venue.factory,
            "router02": venue.router02,
            "pair_address": pair_address,
            "pair_factory": pair_factory,
            "router_factory": router_factory,
            "token0": token0,
            "token1": token1,
            "swap_fee_bps_assumption": venue.swap_fee_bps,
            "source_status": venue.source_status,
            "onchain_relationship_checks": "passed",
        })

    final_block = rpc.call("eth_getBlockByNumber", [block_tag, False])
    final_hash = final_block.get("hash") if isinstance(final_block, dict) else None
    if final_hash != block_hash:
        raise VenueVerificationError("Pinned block hash changed during venue verification")

    return {
        "mode": "read_only_live_venue_deployment_verification",
        "chain_id": chain_id,
        "block_number": block_number,
        "block_hash": block_hash,
        "rpc_endpoint": _safe_endpoint_label(rpc.endpoint) if getattr(rpc, "endpoint", None) else "injected_rpc_test_double",
        "verification_status": "live_read_only_onchain_checks_passed",
        "venues": pair_results,
        "warning": (
            "This verifies code presence and factory/router/pair relationships for the canonical WETH/USDC pair "
            "at one pinned block. It does not verify token safety, every pool's fee semantics, gas, atomic route "
            "execution, paper PnL, or profitability."
        ),
    }
