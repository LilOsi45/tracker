# tracker

Privates Solana-Memecoin-Tool. Single-User, mobile-first, PWA vor
FastAPI-Backend.

## Stand

**Phase 1 — Backend: fertig.** Wallet-Tracker (FIFO-PnL, offene Positionen,
Trade-Historie, CSV-Export) und Coin-Übersicht (DexScreener + RugCheck).

**Phase 1 — Frontend: offen.** Wartet auf den HTML-Prototyp der
Pre-Buy-Checkliste, der als Design-Basis dient.

**Phase 2 — Screener + Discord: offen.** Die Filter-Konfiguration liegt
bereits in `config.example.yaml`, wird aber noch nicht gelesen.

## Setup

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cd ..
cp .env.example .env              # HELIUS_API_KEY eintragen
cp config.example.yaml config.yaml

backend/.venv/bin/uvicorn app.main:app --app-dir backend --reload
```

Ohne `HELIUS_API_KEY` läuft alles außer dem Wallet-Sync — die Coin-Übersicht
braucht keinen Key.

### Erster Sync

```bash
curl -X POST localhost:8000/api/wallet/<ADRESSE>/sync
```

Der erste Aufruf holt bis zu 60 Seiten à 100 Transaktionen und läuft dann
weiter rückwärts durch die Historie. Bei einer aktiven Wallet sind mehrere
Aufrufe nötig, bis `backfill_complete` auf `true` steht. Danach ist der Sync
inkrementell und billig.

## API

| Endpoint | Zweck |
|---|---|
| `POST /api/wallet/{addr}/sync` | Historie holen und Ledger neu berechnen |
| `GET /api/wallet/{addr}/summary` | Tages-PnL, realisiert und unrealisiert getrennt |
| `GET /api/wallet/{addr}/positions` | Offene Positionen mit Einstieg, aktuellem Preis, PnL |
| `GET /api/wallet/{addr}/chart?days=30` | Tagesreihe für den Verlauf |
| `GET /api/wallet/{addr}/trades` | Geschlossene Trades |
| `GET /api/wallet/{addr}/export/blockpit.csv` | Steuer-Export, transaktionsbasiert |
| `GET /api/wallet/{addr}/export/trades.csv` | Trade-Übersicht zum Lesen |
| `GET /api/coins` | Coin-Übersicht, filter- und sortierbar |
| `POST /api/coins/refresh` | Marktdaten + RugCheck-Anreicherung |
| `GET /api/coins/{mint}` | Einzelner Token inkl. RugCheck-Details |

## Architektur

Rohe Swaps sind die Wahrheit; offene Lots und realisierte PnL-Zeilen sind
abgeleitet und werden bei jedem Sync aus der Swap-Tabelle neu gerechnet. Ein
Bug in der Buchhaltung kostet damit einen Neustart der Berechnung, keinen
erneuten API-Abruf.

Buchhaltungswährung ist **SOL**, weil das exakt aus der Chain kommt. USD ist
eine Darstellungsschicht auf Basis des Tagesschlusskurses — nie umgekehrt,
das würde einen Preisfehler in die Kostenbasis einbacken.

Kostenbasis ist **FIFO**.

## Was nicht geht

Steht vollständig in [`docs/data-sources.md`](docs/data-sources.md). Kurz:
DexScreener hat keinen Neuemissions-Feed, Holder-Zahlen sind lückenhaft,
und historischer unrealisierter PnL ist nicht rückwirkend rekonstruierbar —
er wird ab Inbetriebnahme täglich gesnapshottet.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```
