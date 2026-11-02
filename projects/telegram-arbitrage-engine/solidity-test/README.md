# Test-only V2 flash-swap callback harness

This isolated Foundry project tests the atomic callback mechanics for the Python engine's cross-venue V2 flash-swap reserve model.

## Run

```bash
cd projects/telegram-arbitrage-engine/solidity-test
forge test -vvv
```

## What is tested

- Source and destination pair registration against their respective factories.
- Callback caller and sender binding, plus callback payload and borrowed-amount consistency.
- A 30 bps V2 exact-input destination swap and opposite-token source-pair repayment using integer arithmetic and the `getAmountIn` rounding convention.
- The fee-adjusted constant-product invariant in the mock pair.
- Minimum-profit failure reverts the entire EVM call, leaving both pair reserves unchanged.
- Owner-only invocation, deadlines, distinct pairs, matching token contracts, and reserve bounds.

## Important limits

Everything here is a **local mock-EVM test fixture**. It is not a production trading contract, does not import or deploy official Uniswap/Sushi contracts, and does not prove that a live pair uses the assumed fee or token behavior. There is no deployment script, wallet, signer, external RPC, transaction submission, or live trading path.

A future pinned Anvil fork test must separately verify the exact deployed pair bytecode/factory bindings and actual token behavior. It must not be treated as an authorization to deploy or trade.

Protocol references:
- [Uniswap v2 flash swaps](https://developers.uniswap.org/docs/protocols/v2/guides/flash-swaps)
- [Uniswap v2 flash-swap concepts](https://developers.uniswap.org/docs/protocols/v2/concepts/flash-swap)
- [UniswapV2Library.getAmountIn](https://github.com/Uniswap/v2-periphery/blob/master/contracts/libraries/UniswapV2Library.sol)
