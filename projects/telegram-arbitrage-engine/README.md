# Telegram Arbitrage Engine

Simulation-first spot-arbitrage research module, isolated inside the autonomous-engine repository. This import does not alter the engine's existing runtime, task schema, provider routing, or storefront projects.

## Safety
- Read-only public DEX Screener market data.
- Deterministic risk calculations use Decimal; floats are rejected.
- SQLite stores observations only. A repeated-observation report measures persistence, not historical PnL or executable performance.
- No wallet connection, signing, approvals, swaps, exchange orders, or live trading code.
- Optional Telegram bot uses long polling, private-chat allowlisting, a per-chat rate limit, and a persisted update offset checkpointed after each processed update. This narrows the replay window but does not provide exactly-once command or message delivery; a crash between processing and checkpointing can still replay the current update. Configure the token and comma-separated allowed private chat IDs through environment variables; never commit secrets.
- DEX Screener USD marks are indicative only, not executable quotes or proof of available depth. Its search response does not provide an on-chain quote timestamp; retrieval age must not be mistaken for pool-state freshness.
- No AI decision can authorize a trade. Any future AI use is restricted to research and explanation.
- Telegram polling is **at-least-once**, not exactly-once: the bot durably checkpoints the next update offset after each completed update. For `/scan`, observation rows and a replayable response receipt are committed atomically under Telegram's `update_id`; a replay after that commit reuses the stored response without another DEX discovery request. A crash before the transaction commits can still repeat the read, and Telegram `sendMessage` delivery remains at-least-once because remote delivery cannot be committed atomically with SQLite.
- An optional local Anvil fork-call adapter accepts exact caller-supplied calldata and performs read-only eth_call/eth_estimateGas with optional callTracer output; it refuses non-loopback endpoints and never submits transactions. It simulates one call only, not an atomic two-venue arbitrage route.
- A read-only EVM JSON-RPC adapter retrieves two V2 pool states and token decimals at one pinned block. The V2 constant-product calculator uses integer raw units and requires two distinct pools at the same block; it is reserve math only and excludes gas, transfer taxes, dynamic fees, reverts, MEV, and execution. Pool fee basis points can be supplied manually or selected from the source-documented Ethereum V2 registry. Registry entries remain unverified until the CLI confirms pair.factory(), factory.getPair(), and router.factory() at the pinned block; the fee model is still a documented assumption.

## Read-only flash-swap simulation

The `flash-swap-sim` command reads two V2 pools at one canonical block, models the opposite-token repayment invariant, and optionally applies a caller-supplied gas-used/gas-price scenario. It can optionally record local research events with `--record-paper`.

Example (replace every placeholder with verified pair/token addresses):

```bash
python -m arb_engine.cli flash-swap-sim \
  --source-pair 0x<SOURCE_PAIR> --source-fee-bps 30 \
  --destination-pair 0x<DESTINATION_PAIR> --destination-fee-bps 30 \
  --borrow-token 0x<BORROW_TOKEN> --borrow-amount-raw 1000000000000000000
```

To add a gas scenario in which the repayment token is USDC and gas is paid in WETH, also supply `--gas-used`, `--gas-price-wei`, `--native-token`, and a same-block `--native-quote-pair` with its fee or venue. Gas units must be a conservative caller estimate including the overhead the caller intends to model; the command does not claim to measure full transaction gas.

Add `--record-paper` to append candidate/snapshot/route/gas events to the local `PaperTradingLedger`. Without this flag, no ledger file is written. The command never creates a paper order, wallet, signer, calldata, approval, or on-chain transaction. See [PAPER_TRADING_LEDGER.md](PAPER_TRADING_LEDGER.md).

## Paper-trading research ledger

An isolated SQLite event ledger is available for provenance-linked research records. It is append-only, hash-chained, idempotent by caller-supplied event ID, and stores raw-unit economics as decimal strings. It is a storage primitive only: no order is created or submitted, and its records are not realized PnL. See [PAPER_TRADING_LEDGER.md](PAPER_TRADING_LEDGER.md).

## Run in isolation
Requires Python 3.11+.

```bash
cd projects/telegram-arbitrage-engine
python -m pip install -e '.[dev]'
pytest
arb-engine demo
arb-engine report --limit 500
arb-engine token-check --token-address 0x... --chain-id 1
arb-engine venues --chain-id 1  # source-documented Ethereum V2 deployments
arb-engine venue-check --chain-id 1  # read-only on-chain factory/router/pair verification
arb-engine scan WETH/USDC --min-liquidity 10000 --min-volume 1000
arb-telegram  # optional read-only polling bot; requires TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_IDS
arb-engine v2-sim --buy-pair 0x... --buy-venue uniswap_v2_ethereum --sell-pair 0x... --sell-venue sushiswap_v2_ethereum --quote-token 0x... --base-token 0x... --capital-quote 50 --chain-id 1 --slippage-bps 50 --gas-units-assumption 350000 --gas-safety-bps 2000
# optional exact-calldata simulation against a separately started local Anvil fork
anvil --fork-url https://ethereum-rpc.publicnode.com --fork-block-number 20000000 --chain-id 1 --host 127.0.0.1 --port 8545
arb-engine fork-call --chain-id 1 --fork-block 20000000 --to 0x... --data 0x... --from-address 0x... --trace
```

Scan snapshots are saved to `data/opportunities.sqlite3`. Avoid tight polling and respect upstream rate limits.

See [data sources and trust boundaries](DATA_SOURCES.md) for source rate limits, freshness limitations, and the release gates for future pool-state simulation.

## Before any paper-profit claim
The source-documented venue registry reduces manual address/fee entry for Ethereum Uniswap V2 and SushiSwap V2. Selecting a registry venue causes the CLI to verify pair.factory(), factory.getPair(), and router.factory() at the pinned block, but registry deployment addresses are not pre-verified on-chain and fee provenance remains a documented assumption. Still required: live fork validation, pool-state/block freshness, token transfer-tax/blacklist behavior, independently verified fee semantics, gas valuation, and a composed atomic multi-venue route simulation with full state-diff/revert analysis. The current Anvil adapter only proves a single supplied call on a pinned local fork and does not satisfy this gate. Cross-chain spreads remain informational only and require bridge cost, settlement delay, and custody/bridge risk modelling.

## Current milestone
0.16: adds a bounded read-only live verification command and CI smoke job for the registered Ethereum V2 factory/router/WETH-USDC pair relationships at one pinned block, using the public Ethereum RPC with bounded retries and no API key. This is deployment relationship verification, not token safety or fee proof. 0.15: adds a source-documented Ethereum V2 venue registry for Uniswap and SushiSwap; registry selections auto-fill fee/factory/router values and force same-block on-chain pair/factory/router verification. Registry source status is explicitly not live-verified; manual fee assumptions remain supported and visibly labelled. 0.14: adds a pinned-block read-only token contract preflight (runtime bytecode presence, optional ERC-20 decimals/symbol/name metadata) and per-update Telegram offset checkpoints. Metadata is not token-safety verification. Also adds a local-Anvil-only exact-calldata call simulator with strict loopback RPC validation, pinned fork block and chain checks, read-only eth_call/eth_estimateGas, optional callTracer diagnostics, and explicit no-submission guarantees. Retains the read-only market scanner, address-aware SQLite ledger, Telegram read-only controller, canonical-block V2 reserve/router quote checks, per-leg slippage bounds, and explicit native gas assumptions. Atomic multi-DEX route construction, token-behavior guarantees, independently verified fee provenance, gas-to-quote valuation, and paper-PnL remain unimplemented. Live market connectivity must be verified in the deployment environment; local CI uses fixtures and does not prove live API availability.
