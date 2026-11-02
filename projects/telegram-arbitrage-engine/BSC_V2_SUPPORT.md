BNB Chain V2 support

Status: registry entries and chain-parameterized scanner implemented; deployments are source-documented but not yet live-verified by this repository's CI.

Chain configuration

- BNB Smart Chain mainnet chain ID: 56.
- DEX Screener chain slug: bsc.
- Non-Ethereum scans require an explicit read-only RPC URL. The scanner verifies eth_chainId against the selected chain ID and refuses a mismatch.
- The same pinned-block factory/pair/router checks used on Ethereum apply to BSC. DEX Screener remains discovery-only.

Registered V2 venues

| Venue | Factory | Router | Fee assumption |
|---|---|---|---|
| PancakeSwap V2 | 0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73 | 0x10ED43C718714eb63d5aA57B78B54704E256024E | 25 bps |
| ApeSwap V2 | 0x0841BD0B734E4F5853f0dD8d7Ea041c241fb0Da6 | 0xcF0feBd3f17CEf5b47b0cD257aCf6025c5BFf3b7 | 20 bps |

PancakeSwap router and factory are identified on BscScan; PancakeSwap's published V2 fee is 0.25% per pool. ApeSwap's official repository lists its BNB Chain factory/router; the 20 bps fee is cited from its audit report. The registry deliberately retains source_status=source_documented_not_live_verified. Runtime scanning must still confirm router.factory(), factory.getPair(), pair.factory(), token identities and pinned block state before quotes are accepted. Fee values are assumptions for metadata; the router's own getAmountsOut result is used for the read-only route quote.

Example command

raawaa-engine onchain-v2-scan --chain-id 56 --venue-ids pancakeswap_v2_bsc apeswap_v2_bsc --rpc-url https://bsc-rpc.publicnode.com --token-address <BASE_TOKEN> --quote-token-address <QUOTE_TOKEN> --capital-quote 1000

Replace both token addresses with verified BNB Chain contracts. No token symbol inference, wallet, signing, transaction building or execution is used. No BSC live smoke is claimed until a successful read-only run verifies the deployments and pair at a real pinned block.
