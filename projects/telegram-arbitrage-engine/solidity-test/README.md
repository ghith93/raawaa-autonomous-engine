# Test-only V2 flash-swap callback harness

This isolated Foundry project tests the Python engine's cross-venue V2 flash-swap model. All EVM state changes occur in local test execution; no transaction is broadcast.

## Run deterministic mock-pair tests

```bash
cd projects/telegram-arbitrage-engine/solidity-test
forge test --match-path test/FlashSwapHarness.t.sol -vvv
```

## Run the pinned Ethereum fork test

The CI workflow obtains a current block number, block hash and parent hash from the keyless public Ethereum RPC, then forks at that exact block:

```bash
RPC_URL=https://ethereum-rpc.publicnode.com
BLOCK_RESPONSE="$(curl --fail --silent --show-error --retry 4 --retry-all-errors --retry-delay 1 --max-time 15 -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' "$RPC_URL")"
BLOCK_HEX="$(jq -r '.result' <<< "$BLOCK_RESPONSE")"
BLOCK_DIGITS="${BLOCK_HEX#0x}"
BLOCK="$((16#$BLOCK_DIGITS))"
BLOCK_JSON="$(curl --fail --silent --show-error --retry 4 --retry-all-errors --retry-delay 1 --max-time 15 -H "Content-Type: application/json" --data "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"eth_getBlockByNumber\",\"params\":[\"$BLOCK_HEX\",false]}" "$RPC_URL")"
MAINNET_RPC_URL="$RPC_URL" \
FORK_BLOCK="$BLOCK" \
FORK_BLOCK_HASH="$(jq -r '.result.hash' <<< "$BLOCK_JSON")" \
FORK_PARENT_HASH="$(jq -r '.result.parentHash' <<< "$BLOCK_JSON")" \
forge test --match-path test/AnvilForkFlashSwap.t.sol -vvv
```

The fork test resolves the real Uniswap V2 and SushiSwap V2 WETH/USDC pairs, checks deployed code, factory/router bindings and token order, then searches a bounded range of borrow sizes. If a positive raw-USDC or raw-WETH surplus exists, it executes the callback harness against the actual pair bytecode inside the ephemeral fork and checks the exact result. If no positive route exists, it expects the swap to revert and verifies that neither pair's reserves changed. The test emits the pinned block number/hash, parent hash, selected route and expected gross raw-unit surplus.

## Coverage

- Source/destination pair registration against their respective factories.
- Callback caller/sender binding, callback payload and borrowed-amount consistency.
- A 30 bps V2 exact-input destination swap and opposite-token source-pair repayment using integer arithmetic and the `getAmountIn` rounding convention.
- Fee-adjusted constant-product invariant in local mock pairs.
- Minimum-profit failure rolls back both pairs.
- Owner-only invocation, deadlines, distinct pairs, matching token contracts and reserve bounds.
- Real Ethereum fork: actual deployed Uniswap/Sushi pair bytecode, factory/router bindings, WETH/USDC token order, and atomic route success-or-revert behavior at a pinned block.

## Important limits

Everything here is **test-only**. The harness is not a production trading contract. There is no deployment script, wallet, signer, approval flow, external transaction submission, or live trading path. The fork test reads remote state through a public RPC but all simulated transactions execute only in the ephemeral local fork. It does not broadcast or alter Ethereum mainnet.

The fork test's gross surplus is not net profitability: it excludes gas valuation, builder/MEV costs, inclusion risk, transfer-tax/rebase/blacklist behavior, and any token-specific restrictions. A positive local fork result is not authorization to deploy or trade. The configured 30 bps fee is a source-backed assumption for these two V2 deployments and must be revalidated if the venues or pair implementations change.

Protocol references:
- [Uniswap v2 flash swaps](https://developers.uniswap.org/docs/protocols/v2/guides/flash-swaps)
- [Uniswap v2 flash-swap concepts](https://developers.uniswap.org/docs/protocols/v2/concepts/flash-swap)
- [UniswapV2Library.getAmountIn](https://github.com/Uniswap/v2-periphery/blob/master/contracts/libraries/UniswapV2Library.sol)
- [Foundry fork cheatcode](https://www.getfoundry.sh/reference/cheatcodes/create-select-fork)
