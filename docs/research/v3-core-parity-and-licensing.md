# Uniswap V3 core parity fixtures and upstream licensing

## Scope

The Python implementation is independently authored and uses integer equations, protocol constants, and selected expected outputs from the public Uniswap V3 core test suite. This document records exact parity fixtures; it does not copy Solidity implementation code.

The fixture tests cover:
- Both directions of SqrtPriceMath exact-input and exact-output price movement.
- The uint256 overflow-safe token0-input fallback.
- Impossible output at or beyond virtual reserves.
- SwapMath exact-input/exact-output capped and partial steps.
- Fee rounding, output capping, one-raw-unit edge cases, and insufficient-liquidity edge cases.

## Upstream test provenance

- [Uniswap v3-core SwapMath.spec.ts](https://github.com/Uniswap/v3-core/blob/main/test/SwapMath.spec.ts)
- [Uniswap v3-core SqrtPriceMath.spec.ts](https://github.com/Uniswap/v3-core/blob/main/test/SqrtPriceMath.spec.ts)
- [Uniswap v3-core SqrtPriceMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/SqrtPriceMath.sol)
- [Uniswap v3-core SwapMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/SwapMath.sol)

## Licensing note

Do not describe the entire `Uniswap/v3-core` repository as having one uniform source-file license:
- The repository root `LICENSE` declares Business Source License 1.1 with a Change License of GPL-2.0-or-later, subject to the stated version/date and grant terms.
- Individual Solidity files carry their own SPDX identifiers. In the current upstream `main` tree, `TickMath.sol`, `LiquidityMath.sol`, and `UnsafeMath.sol` declare GPL-2.0-or-later; `TickBitmap.sol`, `SqrtPriceMath.sol`, and `SwapMath.sol` declare BUSL-1.1; `FullMath.sol` declares MIT.
- The TickMath file specifically referenced by the original math-foundation note declares GPL-2.0-or-later. That statement must not be generalized to other libraries.

The Raawaa Python modules in this repository are independently authored mathematical implementations. The parity fixtures use selected numeric test expectations and do not import, embed, or translate Solidity source code. Before any future direct source reuse, re-check the exact upstream file's SPDX header, repository license terms, version, and any applicable additional-use grant. This note is factual license identification, not legal advice.

## Safety boundary

Passing these arithmetic fixtures does not prove full pool simulation correctness, RPC state provenance, factory trust, token transfer behavior, gas cost, executable liquidity, net profitability, or transaction inclusion. The live V3 reader and simulator remain read-only research tools.
