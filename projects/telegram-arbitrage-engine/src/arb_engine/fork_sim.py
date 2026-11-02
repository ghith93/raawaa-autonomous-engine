from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 2_000_000
MAX_CALLDATA_HEX_CHARS = 131_072
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
HEX_DATA_RE = re.compile(r"0x(?:[0-9a-fA-F]{2})*")


class ForkSimulationError(RuntimeError):
    """Raised when a local Anvil fork simulation cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ForkCallResult:
    chain_id: int
    fork_block: int
    block_hash: str
    client_version: str
    call_output: str
    estimated_gas: int
    trace: dict | None
    classification: str = "anvil_fork_eth_call_estimate_only_no_transaction_submitted"


class AnvilForkSimulator:
    """Read-only call/estimate/trace client for an operator-managed local Anvil fork."""

    def __init__(self, endpoint: str = "http://127.0.0.1:8545", *, timeout_seconds: float = 10):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise ValueError("Anvil endpoint must be a local loopback HTTP URL without credentials or query")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("timeout_seconds must be in (0, 60]")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self._request_id = 0

    def _rpc(self, method: str, params: list) -> object:
        allowed = {
            "web3_clientVersion", "eth_chainId", "eth_blockNumber",
            "eth_getBlockByNumber", "eth_call", "eth_estimateGas", "debug_traceCall",
        }
        if method not in allowed:
            raise ForkSimulationError("RPC method is not permitted by the read-only simulator")
        self._request_id += 1
        request_id = self._request_id
        body = json.dumps({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        }).encode()
        request = Request(
            self.endpoint, data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ForkSimulationError("Anvil response exceeded the 2 MB safety limit")
                payload = json.loads(raw)
        except HTTPError as exc:
            raise ForkSimulationError(f"Local Anvil HTTP error: {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ForkSimulationError(f"Local Anvil RPC failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            raise ForkSimulationError("Malformed or mismatched Anvil JSON-RPC response")
        if payload.get("error") is not None:
            error = payload["error"]
            message = error.get("message", "RPC error") if isinstance(error, dict) else "RPC error"
            raise ForkSimulationError(f"{method} failed: {str(message)[:300]}")
        if "result" not in payload:
            raise ForkSimulationError(f"{method} response has no result")
        return payload["result"]

    @staticmethod
    def _quantity(value: object, label: str) -> int:
        if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value):
            raise ForkSimulationError(f"Invalid {label} quantity")
        return int(value, 16)

    def simulate_call(
        self,
        *,
        expected_chain_id: int,
        fork_block: int,
        to: str,
        data: str,
        from_address: str | None = None,
        value_wei: int = 0,
        gas_limit: int | None = None,
        include_trace: bool = True,
    ) -> ForkCallResult:
        if not isinstance(expected_chain_id, int) or isinstance(expected_chain_id, bool) or expected_chain_id <= 0:
            raise ValueError("expected_chain_id must be a positive integer")
        if not isinstance(fork_block, int) or isinstance(fork_block, bool) or fork_block < 0:
            raise ValueError("fork_block must be a non-negative integer")
        if not isinstance(to, str) or not ADDRESS_RE.fullmatch(to) or int(to[2:], 16) == 0:
            raise ValueError("to must be a non-zero 20-byte EVM address")
        if (
            not isinstance(data, str) or len(data) > MAX_CALLDATA_HEX_CHARS
            or not HEX_DATA_RE.fullmatch(data) or len(data) < 10
        ):
            raise ValueError("data must be bounded even-length hex calldata with a 4-byte selector")
        if from_address is not None and (
            not isinstance(from_address, str) or not ADDRESS_RE.fullmatch(from_address)
            or int(from_address[2:], 16) == 0
        ):
            raise ValueError("from_address must be a non-zero 20-byte EVM address")
        if not isinstance(value_wei, int) or isinstance(value_wei, bool) or not 0 <= value_wei < 2**256:
            raise ValueError("value_wei must be a uint256")
        if gas_limit is not None and (
            not isinstance(gas_limit, int) or isinstance(gas_limit, bool)
            or not 21_000 <= gas_limit <= 30_000_000
        ):
            raise ValueError("gas_limit must be between 21000 and 30000000")

        client_version = self._rpc("web3_clientVersion", [])
        if not isinstance(client_version, str) or "anvil" not in client_version.casefold():
            raise ForkSimulationError("RPC endpoint did not identify itself as Anvil")
        chain_id = self._quantity(self._rpc("eth_chainId", []), "chain ID")
        if chain_id != expected_chain_id:
            raise ForkSimulationError(
                f"Wrong fork chain ID: expected {expected_chain_id}, received {chain_id}"
            )
        head = self._quantity(self._rpc("eth_blockNumber", []), "head block")
        if head != fork_block:
            raise ForkSimulationError(
                f"Anvil head {head} differs from requested pinned fork block {fork_block}; disable mining"
            )
        block_tag = hex(fork_block)
        block = self._rpc("eth_getBlockByNumber", [block_tag, False])
        if not isinstance(block, dict) or not isinstance(block.get("hash"), str):
            raise ForkSimulationError("Pinned fork block is missing its hash")
        block_hash = block["hash"].lower()
        if not re.fullmatch(r"0x[0-9a-f]{64}", block_hash):
            raise ForkSimulationError("Pinned fork block hash is malformed")

        tx = {"to": to.lower(), "data": data.lower(), "value": hex(value_wei)}
        if from_address is not None:
            tx["from"] = from_address.lower()
        if gas_limit is not None:
            tx["gas"] = hex(gas_limit)

        call_output = self._rpc("eth_call", [tx, block_tag])
        if not isinstance(call_output, str) or not HEX_DATA_RE.fullmatch(call_output):
            raise ForkSimulationError("eth_call returned malformed output")
        estimated_gas = self._quantity(
            self._rpc("eth_estimateGas", [tx, block_tag]), "estimated gas",
        )
        if estimated_gas <= 0 or estimated_gas > 30_000_000:
            raise ForkSimulationError("Estimated gas is outside the supported safety bound")

        trace = None
        if include_trace:
            raw_trace = self._rpc(
                "debug_traceCall",
                [tx, block_tag, {"tracer": "callTracer", "timeout": "5s"}],
            )
            if not isinstance(raw_trace, dict):
                raise ForkSimulationError("debug_traceCall returned a malformed trace")
            if raw_trace.get("error") or raw_trace.get("failed") is True:
                raise ForkSimulationError("Call trace indicates execution failure")
            trace = {
                "type": raw_trace.get("type"),
                "from": raw_trace.get("from"),
                "to": raw_trace.get("to"),
                "gas": raw_trace.get("gas"),
                "gasUsed": raw_trace.get("gasUsed"),
                "output": raw_trace.get("output"),
                "calls_count": len(raw_trace.get("calls", [])) if isinstance(raw_trace.get("calls", []), list) else None,
            }

        # Re-read the fork block after all simulations to detect accidental mining/replacement.
        final_block = self._rpc("eth_getBlockByNumber", [block_tag, False])
        if not isinstance(final_block, dict) or str(final_block.get("hash", "")).lower() != block_hash:
            raise ForkSimulationError("Pinned fork block changed during simulation")
        final_head = self._quantity(self._rpc("eth_blockNumber", []), "final head block")
        if final_head != fork_block:
            raise ForkSimulationError("Anvil fork advanced during simulation; result is not reproducible")

        return ForkCallResult(
            chain_id=chain_id, fork_block=fork_block, block_hash=block_hash,
            client_version=client_version, call_output=call_output,
            estimated_gas=estimated_gas, trace=trace,
        )
