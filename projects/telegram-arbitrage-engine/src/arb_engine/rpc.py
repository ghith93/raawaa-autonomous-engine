from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .v2 import V2PoolState

DEFAULT_ETHEREUM_RPC = "https://ethereum-rpc.publicnode.com"
MAX_RPC_RESPONSE_BYTES = 500_000


class RpcReadError(RuntimeError):
    """Raised when a read-only JSON-RPC request cannot be trusted."""


@dataclass(frozen=True, slots=True)
class EvmPoolSnapshot:
    state: V2PoolState
    token0_decimals: int
    token1_decimals: int
    block_timestamp: int


class EthereumV2PoolReader:
    """Read-only Ethereum V2 pool reader. It has no transaction or signing methods."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ETHEREUM_RPC,
        *,
        expected_chain_id: int = 1,
        timeout_seconds: float = 8.0,
        min_request_interval: float = 0.25,
    ):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https" or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment
        ):
            raise ValueError("RPC endpoint must be an HTTPS URL without embedded credentials or query parameters")
        if not isinstance(expected_chain_id, int) or isinstance(expected_chain_id, bool) or expected_chain_id <= 0:
            raise ValueError("expected_chain_id must be a positive integer")
        if timeout_seconds <= 0 or min_request_interval < 0:
            raise ValueError("Invalid RPC timing configuration")
        self.endpoint = endpoint
        self.expected_chain_id = expected_chain_id
        self.timeout_seconds = timeout_seconds
        self.min_request_interval = min_request_interval
        self._last_request = 0.0
        self._next_id = 1

    def _rpc(self, method: str, params: list[Any]) -> Any:
        wait = self.min_request_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        request_id = self._next_id
        self._next_id += 1
        body = json.dumps({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        }).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RPC_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RPC_RESPONSE_BYTES:
                    raise RpcReadError("RPC response exceeded 500 KB safety limit")
                payload = json.loads(raw)
        except HTTPError as exc:
            raise RpcReadError(f"Ethereum RPC HTTP error: {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RpcReadError(f"Ethereum RPC request failed: {type(exc).__name__}") from exc
        finally:
            self._last_request = time.monotonic()
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            raise RpcReadError("Malformed or mismatched JSON-RPC response")
        if payload.get("error") is not None:
            error = payload["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise RpcReadError(f"Ethereum RPC returned an error (code={code})")
        if "result" not in payload:
            raise RpcReadError("JSON-RPC response has no result")
        return payload["result"]

    @staticmethod
    def _uint(value: Any, label: str) -> int:
        if not isinstance(value, str) or not value.startswith("0x") or len(value) <= 2:
            raise RpcReadError(f"Invalid hexadecimal {label}")
        try:
            result = int(value, 16)
        except ValueError as exc:
            raise RpcReadError(f"Invalid hexadecimal {label}") from exc
        if result < 0:
            raise RpcReadError(f"Negative {label}")
        return result

    @staticmethod
    def _address(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.startswith("0x"):
            raise RpcReadError(f"Invalid {label} response")
        raw = value[2:]
        if len(raw) != 64:
            raise RpcReadError(f"Invalid ABI address word for {label}")
        try:
            int(raw, 16)
        except ValueError as exc:
            raise RpcReadError(f"Invalid ABI address word for {label}") from exc
        return "0x" + raw[-40:].lower()

    @staticmethod
    def _pool_address(value: str) -> str:
        if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
            raise ValueError("Pool address must be a 20-byte EVM address")
        try:
            int(value[2:], 16)
        except ValueError as exc:
            raise ValueError("Pool address must be hexadecimal") from exc
        return value.lower()

    def read_pools(self, pools: list[tuple[str, int]]) -> list[EvmPoolSnapshot]:
        """Read up to four pools against one pinned Ethereum block."""
        if not isinstance(pools, list) or not 1 <= len(pools) <= 4:
            raise ValueError("Provide between one and four pools")
        normalized = []
        seen = set()
        for address, fee_bps in pools:
            address = self._pool_address(address)
            if address in seen:
                raise ValueError("Duplicate pool address")
            seen.add(address)
            if not isinstance(fee_bps, int) or isinstance(fee_bps, bool) or not 0 <= fee_bps < 10000:
                raise ValueError("Pool fee must be integer basis points in [0, 10000)")
            normalized.append((address, fee_bps))

        chain_id = self._uint(self._rpc("eth_chainId", []), "chain ID")
        if chain_id != self.expected_chain_id:
            raise RpcReadError(f"Unexpected chain ID: expected {self.expected_chain_id}, received {chain_id}")
        block_number = self._uint(self._rpc("eth_blockNumber", []), "block number")
        block_tag = hex(block_number)
        block = self._rpc("eth_getBlockByNumber", [block_tag, False])
        if not isinstance(block, dict) or not isinstance(block.get("hash"), str):
            raise RpcReadError("Pinned block response is missing its hash")
        block_hash = block["hash"].lower()
        if len(block_hash) != 66 or not block_hash.startswith("0x"):
            raise RpcReadError("Pinned block hash has an invalid shape")
        try:
            int(block_hash[2:], 16)
        except ValueError as exc:
            raise RpcReadError("Pinned block hash is not hexadecimal") from exc
        try:
            int(block_hash[2:], 16)
        except ValueError as exc:
            raise RpcReadError("Pinned block hash is not hexadecimal") from exc
        block_timestamp = self._uint(block.get("timestamp"), "block timestamp")
        now_timestamp = int(datetime.now(timezone.utc).timestamp())
        if block_timestamp > now_timestamp + 30 or now_timestamp - block_timestamp > 60:
            raise RpcReadError("Latest block timestamp is future-dated or stale")

        raw_pools = []
        unique_tokens = set()
        for address, fee_bps in normalized:
            token0 = self._address(
                self._rpc("eth_call", [{"to": address, "data": "0x0dfe1681"}, block_tag]),
                "token0",
            )
            token1 = self._address(
                self._rpc("eth_call", [{"to": address, "data": "0xd21220a7"}, block_tag]),
                "token1",
            )
            reserves_raw = self._rpc(
                "eth_call", [{"to": address, "data": "0x0902f1ac"}, block_tag]
            )
            if not isinstance(reserves_raw, str) or not reserves_raw.startswith("0x"):
                raise RpcReadError("Invalid getReserves ABI response")
            data = reserves_raw[2:]
            if len(data) != 192:
                raise RpcReadError("getReserves ABI response must contain three 32-byte words")
            try:
                reserve0 = int(data[0:64], 16)
                reserve1 = int(data[64:128], 16)
                timestamp_last = int(data[128:192], 16)
            except ValueError as exc:
                raise RpcReadError("getReserves ABI response is not hexadecimal") from exc
            if reserve0 <= 0 or reserve1 <= 0:
                raise RpcReadError("Pool has empty reserves")
            raw_pools.append((address, token0, token1, reserve0, reserve1, fee_bps, timestamp_last))
            unique_tokens.update((token0, token1))

        decimals_by_token: dict[str, int] = {}
        for token in sorted(unique_tokens):
            value = self._uint(
                self._rpc("eth_call", [{"to": token, "data": "0x313ce567"}, block_tag]),
                "token decimals",
            )
            if value > 36:
                raise RpcReadError("Token decimals exceed supported safety bound (36)")
            decimals_by_token[token] = value

        # Re-check the pinned block after all eth_call reads to detect a reorg
        # that replaced the canonical block number while the snapshot was collected.
        final_block = self._rpc("eth_getBlockByNumber", [block_tag, False])
        if not isinstance(final_block, dict) or str(final_block.get("hash", "")).lower() != block_hash:
            raise RpcReadError("Pinned block changed during snapshot collection (possible reorg)")

        observed_at = datetime.now(timezone.utc)
        snapshots = []
        for address, token0, token1, reserve0, reserve1, fee_bps, _timestamp_last in raw_pools:
            state = V2PoolState(
                chain_id=chain_id,
                pair_address=address,
                token0=token0,
                token1=token1,
                reserve0_raw=reserve0,
                reserve1_raw=reserve1,
                fee_bps=fee_bps,
                block_number=block_number,
                block_hash=block_hash,
                observed_at=observed_at,
            )
            snapshots.append(EvmPoolSnapshot(
                state=state,
                token0_decimals=decimals_by_token[token0],
                token1_decimals=decimals_by_token[token1],
                block_timestamp=block_timestamp,
            ))
        return snapshots

    def read_pool(self, pair_address: str, fee_bps: int) -> EvmPoolSnapshot:
        return self.read_pools([(pair_address, fee_bps)])[0]
