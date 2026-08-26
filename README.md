# tracker

Private Solana memecoin tool. Single user, mobile-first, a PWA in front of a
FastAPI backend.

## Status

**Phase 1: done and deployable.** Backend (wallet tracker with FIFO PnL, coin
overview, CSV export) and PWA (pre-buy check, wallet, coins, setup),
installable on the home screen and usable offline.

**Phase 2 — screener + Discord: open.** The filter configuration already sits
in `config.example.yaml` but is not read yet.

## Frontend

Vanilla HTML/CSS/JS as ES modules, no build step. The backend serves
`frontend/` directly, so the PWA talks to the same origin by default — a
different API URL can be set under *Setup*.

The design rule `css/app.css` follows: **one** layer of visible structure.
Grouping is done with whitespace, not with borders and panels; a hairline only
where two rows would otherwise run into each other. Exactly **one** element per
screen carries weight — the verdict on Check, the day's PnL on Wallet.
Everything else recedes so that one thing reads in a single glance.

All prices are USD, because every source in this project (Jupiter,
DexScreener, CoinGecko) quotes USD. There is no FX conversion.

The icons are generated, not checked in and forgotten:

```bash
python3 scripts/make_icons.py
```

## Local setup

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cd ..
cp .env.example .env              # add HELIUS_API_KEY
cp config.example.yaml config.yaml

backend/.venv/bin/uvicorn app.main:app --app-dir backend --reload
```

Without `HELIUS_API_KEY` everything works except the wallet sync — the coin
overview needs no key.

### First sync

```bash
curl -X POST localhost:8000/api/wallet/<ADDRESS>/sync
```

The first call fetches up to 60 pages of 100 transactions and then keeps
walking backwards through the history. An active wallet needs several calls
before `backfill_complete` turns `true`. After that the sync is incremental and
cheap.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/wallet/{addr}/sync` | Fetch history and recompute the ledger |
| `GET /api/wallet/{addr}/summary` | Day PnL, realised and unrealised kept apart, SOL balance |
| `GET /api/wallet/{addr}/positions` | Open positions with entry, current price, PnL |
| `GET /api/wallet/{addr}/chart?days=30` | Daily series for the history chart |
| `GET /api/wallet/{addr}/trades` | Closed trades |
| `GET /api/wallet/{addr}/export/blockpit.csv` | Tax export, transaction-based |
| `GET /api/wallet/{addr}/export/trades.csv` | Trade overview for reading |
| `GET /api/coins` | Coin overview, filterable and sortable |
| `POST /api/coins/refresh` | Market data + RugCheck enrichment |
| `GET /api/coins/{mint}` | Single token including RugCheck detail |

## Architecture

Raw swaps are the truth; open lots and realised PnL rows are derived and
recomputed from the swap table on every sync. A bug in the accounting therefore
costs a recomputation, not another round of API calls.

The accounting currency is **SOL**, because that is what comes off the chain
exactly. USD is a presentation layer on top of the daily close — never the
other way round, which would bake a price error into the cost basis itself.

Cost basis is **FIFO**.

## What does not work

Written out in full in [`docs/data-sources.md`](docs/data-sources.md). Briefly:
DexScreener has no new-issue feed, holder counts are patchy, and historical
unrealised PnL cannot be reconstructed retroactively — it is snapshotted daily
from the day the tool goes live.

## Deployment (Hetzner)

On a fresh Debian or Ubuntu box, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/LilOsi45/tracker/claude/solana-memecoin-trading-igtdv3/deploy/install.sh | bash
```

If the repository is private, both fetching the script and cloning need a
GitHub token with read access (fine-grained, this repository only,
*Contents: Read-only*):

```bash
export GH_TOKEN=github_pat_...
curl -fsSL -H "Authorization: Bearer $GH_TOKEN" \
  https://raw.githubusercontent.com/LilOsi45/tracker/claude/solana-memecoin-trading-igtdv3/deploy/install.sh \
  | GH_TOKEN=$GH_TOKEN bash
```

That token is used only for the transfer itself and never lands in
`.git/config` — the cost being that a later re-run needs it again.

Instead of answering the prompts, every value can be set beforehand. On a phone
that is the nicer path, because a pasted value without a closing Return looks
exactly like a hung script:

```bash
export DOMAIN=tracker.your-domain.com
export HELIUS_KEY=...
export WALLET=...
export EMAIL=you@example.com
curl -fsSL https://raw.githubusercontent.com/LilOsi45/tracker/claude/solana-memecoin-trading-igtdv3/deploy/install.sh | bash
```

The script asks for domain, Helius key and wallet, generates the access token
itself, and sets up the service, nginx and the certificate. At the end it
prints a link that carries token and wallet into the app when opened, so
nothing long has to be typed on a phone. A re-run updates the checkout, keeps
the existing token, and restarts the service so the new code actually takes
effect.

<details>
<summary>The same steps individually</summary>

```bash
# service account with no login and no home
useradd --system --no-create-home --shell /usr/sbin/nologin tracker

git clone <repo> /opt/tracker && cd /opt/tracker
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cp .env.example .env && cp config.example.yaml config.yaml   # add key + wallet
install -o tracker -g tracker -d /opt/tracker/data

# .env holds secrets and is nobody else's business
chown tracker:tracker .env && chmod 600 .env

cp deploy/tracker.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable tracker && systemctl restart tracker
systemctl status tracker --no-pager

ln -s /opt/tracker/deploy/nginx.conf /etc/nginx/sites-enabled/tracker
# point server_name in the config at your own domain, then:
certbot --nginx -d tracker.example.com
```

</details>

`deploy/nginx.conf` is deliberately plain HTTP. Certbot writes the TLS block
into it itself. A pre-written `listen 443 ssl` would stop nginx from starting
at all without a certificate on disk, and a premature HTTPS redirect would also
stop certbot from answering its own HTTP-01 challenge.

TLS is not optional: without HTTPS no browser registers the service worker, and
without that the app is neither installable nor usable offline.

`ACCESS_TOKEN` protects the whole API. Without it, anyone who knows the URL can
read your wallet history and burn your Helius credits.

The wallet is configured in **two** places, and they serve different purposes:
under *Setup* in the app for what you see on screen, and in `config.yaml` under
`app.wallets` for the scheduler that syncs automatically and writes the nightly
equity snapshot. Only the second one keeps the 30-day unrealised history
filling in.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```
