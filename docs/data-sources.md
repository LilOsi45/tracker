# Data sources: where each number comes from, and what is missing

This document records which metric comes from which API, what it costs, and —
above all — which metrics are **not** available through the chosen APIs. None
of those are papered over with a placeholder: a field we cannot fill is `NULL`
and is shown in the UI as "unknown", never as `0`.

## APIs, keys, cost

| API | Key | Cost | Limit | Used for |
|---|---|---|---|---|
| DexScreener | no | free | ~300 req/min (pairs), ~60 req/min (profiles/boosts) | Price, market cap, FDV, LP size, volume, pair age |
| Jupiter Price v3 (Lite) | no | free | ~60 req/min | Current USD prices for open positions |
| RugCheck | optional | free | ~1 req/s | Mint/freeze authority, LP lock, top holders, holder count |
| Helius | yes | Free: 1M credits/month, 10 req/s | see left | Wallet trade history, token metadata, SOL balance |
| CoinGecko | no | free | ~10–30 req/min | Daily SOL/USD close |
| Discord webhook | webhook URL | free | 30 req/min | Phase 2 alerts |

Expected cost in normal operation: **$0/month.** The Helius free tier only gets
tight once Phase 2 solves new-pair detection through Helius webhooks (see gap
1). That would be $49/month for the Developer tier.

## Metric → source

| Metric | Source | Note |
|---|---|---|
| Market cap | DexScreener | `marketCap`, falls back to `fdv` when empty |
| LP size (USD) | DexScreener | `liquidity.usd` of the deepest Solana pool |
| Age | DexScreener | from `pairCreatedAt`, so **pool age**, not mint age |
| Mint authority | RugCheck | `null` = revoked |
| Freeze authority | RugCheck | `null` = revoked |
| LP burned/locked | RugCheck | as `lpLockedPct` (percent), **not** as a boolean |
| Top 10 holder share | RugCheck | pool and LP accounts are removed first |
| Holder count | RugCheck | often `NULL`, see gap 2 |
| Volume/LP ratio | computed | `volume_24h / liquidity_usd` |
| SOL balance | Helius | read live from chain, not derived from the ledger |

## Gaps

### 1. DexScreener has no "all new Solana pairs" endpoint

This is the most important limitation in the whole project.

`token-profiles/latest/v1` and `token-boosts/latest/v1` return **only tokens
whose teams paid for a profile or a boost**. That is a marketing feed, not a
launch feed. Everyone in it spent money on visibility — that is a selection,
but not the one a screener wants.

So new-pair detection in Phase 2 needs a different source:

- **PumpPortal WebSocket** — free, pushes new Pump.fun launches and migrations
  in real time. Does not cover every launchpad.
- **Helius webhooks / LaserStream** — subscribe to the Pump.fun and Raydium
  programs. Complete, but costs credits and needs a publicly reachable URL
  (which the Hetzner box has).
- **Bitquery / Moralis** — complete and convenient, paid.

The current coin overview therefore works from a watchlist plus search terms
plus the paid feeds. That is enough to *evaluate* tokens you already know
about. It is explicitly not a complete view of new launches.

### 2. Holder count is unreliable and expensive

RugCheck does not return `totalHolders` for every token. The exact number is
only available through `getTokenAccounts` or `getProgramAccounts` — one call
per token, which for hundreds of tokens on a 30-second cycle fits neither the
free tier nor any sensible budget.

Where RugCheck returns nothing, the field stays `NULL`. A filter on holder
count then excludes those tokens rather than waving them through.

Little is lost in practice: holder counts on Solana are systematically
inflated by sybil distribution, and as an absolute number it is the weakest
criterion on the list anyway.

### 3. Historical unrealised PnL cannot be reconstructed

Realised PnL per day is exact — it comes straight out of the ledger.

Unrealised PnL for past days would need the historical price of **every
memecoin held, per day**. Jupiter serves spot only, DexScreener has no time
series, and the APIs that do (Birdeye, Bitquery) cost money.

Solution: the scheduler writes a snapshot of the unrealised value into
`equity_daily` every evening at 23:55. The 30-day chart shows realised PnL in
full from the first sync, but unrealised only from the day the tool started
running. The API reports this through `has_unrealized_history`.

### 4. Token↔token swaps are not booked

Direct swaps between two memecoins with no SOL leg have no defensible mid
price. Rather than invent one, the parser skips them and reports them as
`unconverted_swaps`. For SOL-paired memecoin trading this affects almost
nothing in practice.

### 5. Sells with no cost basis

When a token was bought before the synced period, arrived by transfer, or was
airdropped, there is no entry price. Such sells land in the history with a cost
of `0` and are marked `basis_unknown: true` — they show up as pure profit and
have to be corrected by hand for tax purposes. The count is reported in the
sync response as `sells_without_basis`.

### 6. Blockpit export: asset mapping

The export follows Blockpit's generic template
(`Date (UTC); Integration Name; Label; Outgoing Asset; Outgoing Amount;
Incoming Asset; Incoming Amount; Fee Asset; Fee Amount`).

Memecoin tickers collide constantly — there are dozens of tokens called `MOON`.
The export therefore writes `TICKER-<first 4 characters of the mint>` instead
of the bare ticker. Blockpit cannot resolve these assets automatically; they
have to be mapped once on import. A bare ticker would instead map silently to
the wrong asset, which is worse.

Relevant for tax: every token↔SOL swap is its own disposal. The export is
therefore transaction-based (one row per swap leg), not position-based.

## Why Helius and not Solana RPC + Jupiter

Both, with a clear division of labour:

- **Helius for history.** Not because of RPC quality, but because
  `getSignaturesForAddress` on public RPCs returns only signatures and every
  transaction would have to be decoded by hand. Raydium, Pump.fun, Meteora and
  Jupiter all have different instruction layouts, and such a parser breaks with
  every new DEX. Public RPCs also lack the full archive.
- **Jupiter for spot prices.** Free, no key, built exactly for this.

We deliberately do **not** rely on Helius' `events.swap`. New memecoin routers
arrive there as `type: UNKNOWN` with no swap event. Instead the parser reads
`accountData` — the net balance changes of your own wallet, which Helius fills
in for every transaction. A trade is then simply whatever moved: SOL out and
exactly one token in is a buy. That works for any program, including the ones
that ship next month.
