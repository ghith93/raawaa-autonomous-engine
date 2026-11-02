from __future__ import annotations

import json
import re
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
        except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
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
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", value):
            raise RpcReadError(f"Invalid hexadecimal {label}")
        result = int(value[2:], 16)
        if result < 0:
            raise RpcReadError(f"Negative {label}")
        return result

    @staticmethod
    def _address(value: Any, label: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
            raise RpcReadError(f"Invalid ABI address word for {label}")
        raw = value[2:]
        if raw[:24] != "0" * 24:
            raise RpcReadError(f"ABI address word for {label} has non-zero address padding")
        return "0x" + raw[-40:].lower()

    @staticmethod
    def _pool_address(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
            raise ValueError("Pool address must be a 20-byte hexadecimal EVM address")
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
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", block_hash):
            raise RpcReadError("Pinned block hash is not a 32-byte hexadecimal value")
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




    def verify_v2_pool_factory(
        self,
        pair_address: str,
        expected_factory: str,
        token0: str,
        token1: str,
        block_number: int,
    ) -> None:
        """Verify pair.factory() and factory.getPair() at a pinned block."""
        pair = self._pool_address(pair_address)
        factory = self._pool_address(expected_factory)
        token_a = self._pool_address(token0)
        token_b = self._pool_address(token1)
        if token_a == token_b:
            raise ValueError("Factory verification requires two different tokens")
        if not isinstance(block_number, int) or isinstance(block_number, bool) or block_number < 0:
            raise ValueError("Factory verification block must be non-negative")
        block_tag = hex(block_number)
        factory_word = self._rpc(
            "eth_call", [{"to": pair, "data": "0xc45a0155"}, block_tag],
        )
        actual_factory = self._address(factory_word, "pair factory")
        if actual_factory != factory:
            raise RpcReadError("Pair factory does not match configured trusted factory")
        # getPair(address,address) selector; addresses are ABI-padded to 32 bytes.
        calldata = "0xe6a43905" + ("0" * 24 + token_a[2:]) + ("0" * 24 + token_b[2:])
        pair_word = self._rpc(
            "eth_call", [{"to": factory, "data": calldata}, block_tag],
        )
        registered_pair = self._address(pair_word, "factory getPair")
        if registered_pair != pair:
            raise RpcReadError("Configured factory does not register this pair for the supplied tokens")

    def verify_canonical_block(self, block_number: int, expected_hash: str) -> None:
        if not isinstance(block_number, int) or isinstance(block_number, bool) or block_number < 0:
            raise ValueError("Block number must be a non-negative integer")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", expected_hash):
            raise ValueError("Expected block hash must be a 32-byte hexadecimal value")
        block = self._rpc("eth_getBlockByNumber", [hex(block_number), False])
        if not isinstance(block, dict) or str(block.get("hash", "")).lower() != expected_hash.lower():
            raise RpcReadError("Pinned block is no longer canonical (possible reorg)")

    def quote_v2_router_exact_input(
        self,
        router_address: str,
        amount_in_raw: int,
        token_path: list[str],
        block_number: int,
    ) -> list[int]:
        """Read a V2 router getAmountsOut quote at a pinned block; never builds a transaction."""
        router = self._pool_address(router_address)
        if not isinstance(amount_in_raw, int) or isinstance(amount_in_raw, bool) or not 0 < amount_in_raw < 2**256:
            raise ValueError("Router input amount must be a positive uint256")
        if not isinstance(block_number, int) or isinstance(block_number, bool) or block_number < 0:
            raise ValueError("Router quote block number must be non-negative")
        if not isinstance(token_path, list) or not 2 <= len(token_path) <= 4:
            raise ValueError("Router path must contain between two and four token addresses")
        path = [self._pool_address(token) for token in token_path]
        if len(set(path)) != len(path):
            raise ValueError("Router path cannot repeat a token")
        # getAmountsOut(uint256,address[]) selector; ABI offset is two 32-byte words.
        calldata = (
            "0xd06ca61f"
            + amount_in_raw.to_bytes(32, "big").hex()
            + (64).to_bytes(32, "big").hex()
            + len(path).to_bytes(32, "big").hex()
            + "".join(("0" * 24 + token[2:]) for token in path)
        )
        raw = self._rpc(
            "eth_call",
            [{"to": router, "data": calldata}, hex(block_number)],
        )
        if not isinstance(raw, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{64})+", raw):
            raise RpcReadError("Router getAmountsOut returned malformed ABI data")
        data = raw[2:]
        if len(data) < 128:
            raise RpcReadError("Router quote ABI response is truncated")
        offset = int(data[0:64], 16)
        length = int(data[64:128], 16)
        if offset != 32 or length != len(path) or len(data) != 128 + 64 * length:
            raise RpcReadError("Router quote returned an unexpected amounts array")
        words = [int(data[128 + i * 64:192 + i * 64], 16) for i in range(length)]
        if words[0] != amount_in_raw:
            raise RpcReadError("Router quote input amount does not match request")
        if any(amount <= 0 for amount in words):
            raise RpcReadError("Router quote contains a zero or invalid hop amount")
        return words

    def read_pool(self, pair_address: str, fee_bps: int) -> EvmPoolSnapshot:
        return self.read_pools([(pair_address, fee_bps)])[0]
