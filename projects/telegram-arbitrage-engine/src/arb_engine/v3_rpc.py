"""Read-only, block-pinned Uniswap V3 pool snapshot reader.

The reader reuses the repository's bounded HTTPS JSON-RPC transport and
verifies pool.factory() plus factory.getPool() before reading V3 state.
It fetches only bitmap words in the caller's requested price direction/range.
"""
from __future__ import annotations

import re
from typing import Any

from .rpc import (
    EvmBlockContext,
    EthereumV2PoolReader,
    RpcReadError,
)

from .tick_bitmap import tick_bitmap_position
from .v3_math import (
    MAX_SQRT_RATIO, MIN_SQRT_RATIO, MIN_TICK,
    get_sqrt_ratio_at_tick, get_tick_at_sqrt_ratio,
)
from .v3_simulator import V3PoolSnapshot, V3SnapshotError


DEFAULT_V3_ETHEREUM_RPC = "https://eth.nodely.dev"
UNISWAP_V3_FACTORY_ETHEREUM = "0x1f98431c8ad98523631ae4a59f267346ea31f984"


class V3PoolFactoryMismatch(RpcReadError):
    """Raised when pool.factory() or factory.getPool() fails trusted identity checks."""


class EvmV3PoolReader(EthereumV2PoolReader):
    """Bounded read-only V3 reader; inherited methods are used only as RPC transport."""

    def __init__(
        self,
        endpoint: str = DEFAULT_V3_ETHEREUM_RPC,
        *,
        expected_chain_id: int = 1,
        timeout_seconds: float = 8.0,
        min_request_interval: float = 0.55,
        max_rpc_requests: int = 256,
    ) -> None:
        super().__init__(
            endpoint,
            expected_chain_id=expected_chain_id,
            timeout_seconds=timeout_seconds,
            min_request_interval=min_request_interval,
            max_rpc_requests=max_rpc_requests,
        )

    @staticmethod
    def _abi_words(raw: Any, expected_words: int, label: str) -> list[str]:
        if (
            not isinstance(raw, str)
            or not re.fullmatch(r"0x(?:[0-9a-fA-F]{64})+", raw)
            or len(raw) != 2 + expected_words * 64
        ):
            raise RpcReadError(f"Malformed V3 ABI response for {label}")
        data = raw[2:]
        return [data[index:index + 64] for index in range(0, len(data), 64)]

    @staticmethod
    def _signed_word(word: str, bits: int, label: str) -> int:
        if type(bits) is not int or not 1 <= bits <= 255:
            raise ValueError("signed ABI width must be between 1 and 255")
        raw = int(word, 16)
        mask = (1 << bits) - 1
        value = raw & mask
        negative = bool(value & (1 << (bits - 1)))
        expected = value | (((1 << 256) - (1 << bits)) if negative else 0)
        if raw != expected:
            raise RpcReadError(f"Invalid sign extension for V3 {label}")
        return value - (1 << bits) if negative else value

    @staticmethod
    def _encode_signed(value: int, bits: int, label: str) -> str:
        if type(value) is not int or not -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
            raise ValueError(f"{label} must fit int{bits}")
        return (value % (1 << 256)).to_bytes(32, "big").hex()

    def _eth_call_word(self, target: str, calldata: str, block_tag: str, label: str) -> str:
        raw = self._rpc("eth_call", [{"to": target, "data": calldata}, block_tag])
        return self._abi_words(raw, 1, label)[0]

    def _eth_call_address(self, target: str, calldata: str, block_tag: str, label: str) -> str:
        return self._address(
            "0x" + self._eth_call_word(target, calldata, block_tag, label), label,
        )

    def _eth_call_uint(self, target: str, calldata: str, block_tag: str, label: str) -> int:
        return self._uint(
            "0x" + self._eth_call_word(target, calldata, block_tag, label), label,
        )

    def _resolve_block_context(
        self, block_context: EvmBlockContext | None,
    ) -> EvmBlockContext:
        if block_context is None:
            return self.latest_block_context()
        if not isinstance(block_context, EvmBlockContext):
            raise ValueError("block_context must be an EvmBlockContext")
        if (
            type(block_context.chain_id) is not int
            or type(block_context.block_number) is not int
            or block_context.block_number < 0
            or not isinstance(block_context.block_hash, str)
            or not re.fullmatch(r"0x[0-9a-fA-F]{64}", block_context.block_hash)
        ):
            raise ValueError("block_context contains invalid chain, block or hash fields")
        chain_id = self._uint(self._rpc("eth_chainId", []), "chain ID")
        if chain_id != self.expected_chain_id or chain_id != block_context.chain_id:
            raise RpcReadError("Pinned V3 block context belongs to a different chain")
        return self._block_context_at(
            block_context.block_number, chain_id, expected_hash=block_context.block_hash,
        )

    def _read_slot0(self, pool: str, block_tag: str) -> tuple[int, int]:
        words = self._abi_words(
            self._rpc("eth_call", [{"to": pool, "data": "0x3850c7bd"}, block_tag]),
            7,
            "pool.slot0()",
        )
        sqrt_price_x96 = int(words[0], 16)
        tick = self._signed_word(words[1], 24, "slot0.tick")
        if not MIN_SQRT_RATIO <= sqrt_price_x96 < MAX_SQRT_RATIO:
            raise RpcReadError("V3 slot0 sqrtPriceX96 is outside global price bounds")
        if not -(1 << 23) <= tick < (1 << 23):
            raise RpcReadError("V3 slot0 tick exceeds int24")
        if any(int(words[index], 16) >= (1 << 16) for index in (2, 3, 4)):
            raise RpcReadError("V3 slot0 observation fields exceed uint16")
        if int(words[5], 16) >= (1 << 8):
            raise RpcReadError("V3 slot0 feeProtocol exceeds uint8")
        unlocked = int(words[6], 16)
        if unlocked not in (0, 1):
            raise RpcReadError("V3 slot0 unlocked flag is not a canonical bool")
        if unlocked != 1:
            raise RpcReadError("V3 pool is locked at the pinned block")
        derived_tick = get_tick_at_sqrt_ratio(sqrt_price_x96)
        allowed_ticks = {derived_tick}
        if sqrt_price_x96 == get_sqrt_ratio_at_tick(derived_tick) and derived_tick > MIN_TICK:
            allowed_ticks.add(derived_tick - 1)
        if tick not in allowed_ticks:
            raise RpcReadError("V3 slot0 tick is inconsistent with sqrtPriceX96")
        return sqrt_price_x96, tick

    def read_slot0(
        self,
        pool_address: str,
        *,
        block_context: EvmBlockContext | None = None,
    ) -> tuple[EvmBlockContext, int, int]:
        """Read and canonically re-check slot0; useful for deriving a relative price limit."""
        pool = self._pool_address(pool_address)
        context = self._resolve_block_context(block_context)
        block_tag = hex(context.block_number)
        code = self._rpc("eth_getCode", [pool, block_tag])
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", code)
            or len(code) <= 2
            or len(code) > 250_002
        ):
            raise RpcReadError("V3 pool has no valid runtime bytecode at the pinned block")
        sqrt_price_x96, tick = self._read_slot0(pool, block_tag)
        self.verify_canonical_block(context.block_number, context.block_hash)
        return context, sqrt_price_x96, tick

    def read_snapshot(
        self,
        pool_address: str,
        *,
        zero_for_one: bool,
        sqrt_price_limit_x96: int,
        expected_factory: str | None = None,
        block_context: EvmBlockContext | None = None,
        max_bitmap_words: int = 16,
        max_initialized_ticks: int = 128,
    ) -> V3PoolSnapshot:
        """Read a bounded same-block pool snapshot for the requested swap direction.

        The default trusted factory is Uniswap V3 Ethereum mainnet. Other chains
        require an explicit expected_factory; never infer a factory from a pool.
        """
        pool = self._pool_address(pool_address)
        if type(zero_for_one) is not bool:
            raise ValueError("zero_for_one must be bool")
        if (
            type(sqrt_price_limit_x96) is not int
            or not MIN_SQRT_RATIO < sqrt_price_limit_x96 < MAX_SQRT_RATIO
        ):
            raise ValueError("sqrt_price_limit_x96 must be strictly within global V3 price bounds")
        if type(max_bitmap_words) is not int or not 1 <= max_bitmap_words <= 64:
            raise ValueError("max_bitmap_words must be between 1 and 64")
        if type(max_initialized_ticks) is not int or not 1 <= max_initialized_ticks <= 256:
            raise ValueError("max_initialized_ticks must be between 1 and 256")

        if expected_factory is None:
            if self.expected_chain_id != 1:
                raise ValueError("expected_factory is required for non-Ethereum networks")
            factory = self._pool_address(UNISWAP_V3_FACTORY_ETHEREUM)
        else:
            factory = self._pool_address(expected_factory)

        context = self._resolve_block_context(block_context)
        block_tag = hex(context.block_number)

        # Require contract code at the same block before interpreting any ABI response.
        for address, label in ((pool, "pool"), (factory, "factory")):
            code = self._rpc("eth_getCode", [address, block_tag])
            if (
                not isinstance(code, str)
                or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", code)
                or len(code) <= 2
                or len(code) > 250_002
            ):
                raise RpcReadError(f"V3 {label} has no valid runtime bytecode at the pinned block")

        actual_factory = self._eth_call_address(
            pool, "0xc45a0155", block_tag, "V3 pool factory",
        )
        if actual_factory != factory:
            raise V3PoolFactoryMismatch("V3 pool factory does not match the configured trusted factory")

        token0 = self._eth_call_address(pool, "0x0dfe1681", block_tag, "V3 token0")
        token1 = self._eth_call_address(pool, "0xd21220a7", block_tag, "V3 token1")
        if token0 >= token1:
            raise RpcReadError("V3 pool token0/token1 order is not canonical")

        fee = self._eth_call_uint(pool, "0xddca3f43", block_tag, "V3 fee")
        tick_spacing = self._signed_word(
            self._eth_call_word(pool, "0xd0c93a7c", block_tag, "pool.tickSpacing()"),
            24,
            "tickSpacing",
        )
        if fee >= 1_000_000 or fee >= (1 << 24):
            raise RpcReadError("V3 pool fee is outside uint24 or fee denominator bounds")
        if not 1 <= tick_spacing < 16_384:
            raise RpcReadError("V3 pool tickSpacing is outside Uniswap V3 factory bounds")

        # Verify both directions of factory identity, not only pool.factory().
        get_pool_calldata = (
            "0x1698ee82"
            + ("0" * 24 + token0[2:])
            + ("0" * 24 + token1[2:])
            + fee.to_bytes(32, "big").hex()
        )
        registered_pool = self._eth_call_address(
            factory, get_pool_calldata, block_tag, "V3 factory getPool",
        )
        if registered_pool != pool:
            raise V3PoolFactoryMismatch("Trusted V3 factory does not register this pool for its tokens and fee")

        factory_spacing = self._signed_word(
            self._eth_call_word(
                factory, "0x22afcccb" + fee.to_bytes(32, "big").hex(),
                block_tag, "factory.feeAmountTickSpacing()",
            ),
            24,
            "factory feeAmountTickSpacing",
        )
        if factory_spacing != tick_spacing:
            raise RpcReadError("V3 pool tickSpacing does not match trusted factory fee tier")

        sqrt_price_x96, tick = self._read_slot0(pool, block_tag)

        liquidity = self._eth_call_uint(pool, "0x1a686502", block_tag, "V3 active liquidity")
        if liquidity >= (1 << 128):
            raise RpcReadError("V3 active liquidity exceeds uint128")

        # The requested price limit determines exactly which bitmap words are needed.
        limit_tick = get_tick_at_sqrt_ratio(sqrt_price_limit_x96)
        if zero_for_one and not sqrt_price_limit_x96 < sqrt_price_x96:
            raise ValueError("zeroForOne price limit must be below the current pool price")
        if not zero_for_one and not sqrt_price_limit_x96 > sqrt_price_x96:
            raise ValueError("oneForZero price limit must be above the current pool price")
        start_compressed = tick // tick_spacing
        end_compressed = limit_tick // tick_spacing
        if not zero_for_one:
            start_compressed += 1
            end_compressed += 1
        start_word, _ = tick_bitmap_position(start_compressed)
        end_word, _ = tick_bitmap_position(end_compressed)
        direction = -1 if zero_for_one else 1
        word_positions = list(range(start_word, end_word + direction, direction))
        if not word_positions or len(word_positions) > max_bitmap_words:
            raise RpcReadError(
                f"V3 price-limit path requires {len(word_positions)} bitmap words; "
                f"configured bound is {max_bitmap_words}"
            )

        bitmap_words: dict[int, int] = {}
        initialized_tick_indices: list[int] = []
        for word_pos in word_positions:
            calldata = "0x5339c296" + self._encode_signed(word_pos, 16, "bitmap word position")
            word = int(self._eth_call_word(pool, calldata, block_tag, "pool.tickBitmap(int16)"), 16)
            bitmap_words[word_pos] = word
            remaining_bits = word
            while remaining_bits:
                bit_pos = (remaining_bits & -remaining_bits).bit_length() - 1
                compressed_tick = (word_pos << 8) + bit_pos
                initialized_tick = compressed_tick * tick_spacing
                if -(887272) <= initialized_tick <= 887272:
                    initialized_tick_indices.append(initialized_tick)
                remaining_bits &= remaining_bits - 1
                if len(initialized_tick_indices) > max_initialized_ticks:
                    raise RpcReadError(
                        "V3 path exceeds configured initialized-tick bound "
                        f"({max_initialized_ticks})"
                    )

        initialized_ticks: dict[int, int] = {}
        for initialized_tick in initialized_tick_indices:
            calldata = "0xf30dba93" + self._encode_signed(initialized_tick, 24, "initialized tick")
            words = self._abi_words(
                self._rpc("eth_call", [{"to": pool, "data": calldata}, block_tag]),
                8,
                "pool.ticks(int24)",
            )
            liquidity_gross = int(words[0], 16)
            liquidity_net = self._signed_word(words[1], 128, "ticks.liquidityNet")
            initialized_flag = int(words[7], 16)
            if initialized_flag not in (0, 1) or initialized_flag != 1 or liquidity_gross == 0:
                raise RpcReadError(
                    f"Bitmap marks tick {initialized_tick} initialized but tick storage does not"
                )
            if liquidity_gross >= (1 << 128):
                raise RpcReadError("V3 tick liquidityGross exceeds uint128")
            initialized_ticks[initialized_tick] = liquidity_net

        # Re-check canonicality after every state read; no mixed-block snapshot is returned.
        self.verify_canonical_block(context.block_number, context.block_hash)
        try:
            return V3PoolSnapshot(
                chain_id=context.chain_id,
                pool_address=pool,
                token0=token0,
                token1=token1,
                fee_pips=fee,
                tick_spacing=tick_spacing,
                sqrt_price_x96=sqrt_price_x96,
                tick=tick,
                liquidity=liquidity,
                block_number=context.block_number,
                block_hash=context.block_hash,
                bitmap_words=bitmap_words,
                initialized_ticks=initialized_ticks,
                rpc_verified=True,
                source="evm_rpc_same_block_factory_registered_block_hash_rechecked",
            )
        except V3SnapshotError as exc:
            raise RpcReadError(f"V3 snapshot validation failed: {exc}") from exc
