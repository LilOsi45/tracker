# tracker

Privates Solana-Memecoin-Tool. Single-User, mobile-first, PWA vor
FastAPI-Backend.

## Stand

**Phase 1: fertig und deploybar.** Backend (Wallet-Tracker mit FIFO-PnL,
Coin-Übersicht, CSV-Export) und PWA (Pre-Buy-Check, Wallet, Coins, Setup),
installierbar auf dem Homescreen und offline nutzbar.

**Phase 2 — Screener + Discord: offen.** Die Filter-Konfiguration liegt
bereits in `config.example.yaml`, wird aber noch nicht gelesen.

## Frontend

Vanilla HTML/CSS/JS als ES-Module, kein Build-Step. Das Backend liefert
`frontend/` direkt aus, die PWA spricht also standardmäßig mit derselben
Herkunft — unter *Setup* lässt sich eine abweichende API-URL setzen.

Gestaltungsregel, an die sich `css/app.css` hält: **eine** Ebene sichtbarer
Struktur. Gruppiert wird über Weißraum, nicht über Rahmen und Panels; eine
Haarlinie nur dort, wo zwei Zeilen sonst ineinanderlaufen. Pro Screen trägt
genau **ein** Element Gewicht — das Verdict auf Check, der Tages-PnL auf
Wallet. Alles andere tritt zurück, damit diese eine Sache in einem Blick
lesbar ist.

Die Icons sind generiert, nicht eingecheckt-und-vergessen:

```bash
python3 scripts/make_icons.py
```

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

## Deployment (Hetzner)

Auf einer frischen Debian- oder Ubuntu-Kiste als root:

```bash
curl -fsSL https://raw.githubusercontent.com/LilOsi45/tracker/claude/solana-memecoin-trading-igtdv3/deploy/install.sh | bash
```

Das Skript fragt Domain, Helius-Key und Wallet ab, erzeugt den Access-Token
selbst und richtet Dienst, nginx und Zertifikat ein. Am Ende druckt es einen
Link, der Token und Wallet beim Öffnen in die App überträgt — auf einem
Handy muss dadurch nichts abgetippt werden. Ein erneuter Aufruf aktualisiert
den Checkout und behält den bestehenden Token.

<details>
<summary>Dieselben Schritte einzeln</summary>

```bash
# Dienstkonto ohne Login und ohne Home
useradd --system --no-create-home --shell /usr/sbin/nologin tracker

git clone <repo> /opt/tracker && cd /opt/tracker
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
cp .env.example .env && cp config.example.yaml config.yaml   # Key + Wallet eintragen
install -o tracker -g tracker -d /opt/tracker/data

# .env enthält Secrets und geht niemanden sonst etwas an
chown tracker:tracker .env && chmod 600 .env

cp deploy/tracker.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tracker
systemctl status tracker --no-pager

ln -s /opt/tracker/deploy/nginx.conf /etc/nginx/sites-enabled/tracker
# server_name in der Config auf die eigene Domain setzen, dann:
certbot --nginx -d tracker.example.de
```

</details>

`deploy/nginx.conf` ist bewusst reines HTTP. Certbot schreibt den TLS-Block
selbst hinein. Ein vorab eingetragenes `listen 443 ssl` würde nginx ohne
vorhandenes Zertifikat gar nicht starten lassen, und eine vorzeitige
HTTPS-Weiterleitung verhindert zusätzlich, dass certbot seine eigene
HTTP-01-Challenge beantworten kann.

TLS ist nicht optional: ohne HTTPS registriert kein Browser den Service
Worker, und ohne den ist die App nicht installierbar und nicht offline
nutzbar.

Der `ACCESS_TOKEN` schützt die gesamte API. Ohne ihn liest jeder, der die
URL kennt, deine Wallet-Historie und verbrennt deine Helius-Credits.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```
