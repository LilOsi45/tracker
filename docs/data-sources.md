# Datenquellen: was woher kommt, und was fehlt

Dieses Dokument hält fest, welche Kennzahl aus welcher API stammt, was sie
kostet, und — vor allem — welche Kennzahlen über die gewählten APIs **nicht**
verfügbar sind. Nichts davon ist mit einem Platzhalter überdeckt: ein Feld,
das wir nicht befüllen können, ist `NULL` und wird in der UI als „unbekannt"
angezeigt, nicht als `0`.

## APIs, Keys, Kosten

| API | Key | Kosten | Limit | Wofür |
|---|---|---|---|---|
| DexScreener | nein | kostenlos | ~300 req/min (Pairs), ~60 req/min (Profiles/Boosts) | Preis, Market Cap, FDV, LP-Größe, Volumen, Pair-Alter |
| Jupiter Price v3 (Lite) | nein | kostenlos | ~60 req/min | Aktuelle USD-Preise für offene Positionen |
| RugCheck | optional | kostenlos | ~1 req/s | Mint-/Freeze-Authority, LP-Lock, Top-Holder, Holder-Anzahl |
| Helius | ja | Free: 1 Mio Credits/Mon, 10 req/s | s. links | Trade-Historie der Wallet, Token-Metadaten |
| CoinGecko | nein | kostenlos | ~10–30 req/min | Tages-Schlusskurs SOL/USD |
| Discord Webhook | Webhook-URL | kostenlos | 30 req/min | Phase 2 Alerts |

Erwartete Kosten im Normalbetrieb: **0 €/Monat.** Der Helius-Free-Tier wird
erst eng, wenn Phase 2 die Neuemissions-Erkennung über Helius-Webhooks
löst (siehe Lücke 1). Dann sind es 49 $/Monat für den Developer-Tier.

## Kennzahl → Quelle

| Kennzahl | Quelle | Anmerkung |
|---|---|---|
| Market Cap | DexScreener | `marketCap`, fällt auf `fdv` zurück wenn leer |
| LP-Größe (USD) | DexScreener | `liquidity.usd` des tiefsten Solana-Pools |
| Alter | DexScreener | aus `pairCreatedAt`, also **Pool-Alter**, nicht Mint-Alter |
| Mint Authority | RugCheck | `null` = revoked |
| Freeze Authority | RugCheck | `null` = revoked |
| LP burned/locked | RugCheck | als `lpLockedPct` (Prozent), **nicht** als Boolean |
| Top-10-Holder-Anteil | RugCheck | Pool- und LP-Accounts werden vorher entfernt |
| Holder-Anzahl | RugCheck | oft `NULL`, siehe Lücke 2 |
| Volumen/LP-Ratio | berechnet | `volume_24h / liquidity_usd` |

## Lücken

### 1. Es gibt keinen „alle neuen Solana-Pairs"-Endpoint bei DexScreener

Das ist die wichtigste Einschränkung im ganzen Projekt.

`token-profiles/latest/v1` und `token-boosts/latest/v1` liefern **nur Token,
deren Teams für ein Profil oder einen Boost bezahlt haben**. Das ist ein
Marketing-Feed, kein Launch-Feed. Wer dort auftaucht, hat Geld für Sichtbarkeit
ausgegeben — das ist eine Auswahl, aber nicht die, die man für einen Screener
will.

Für Phase 2 braucht die Neuemissions-Erkennung also eine andere Quelle:

- **PumpPortal WebSocket** — kostenlos, pusht neue Pump.fun-Launches und
  Migrationen in Echtzeit. Deckt nicht alle Launchpads ab.
- **Helius Webhooks / LaserStream** — auf die Pump.fun- und Raydium-Programme
  abonnieren. Vollständig, kostet aber Credits und braucht eine öffentlich
  erreichbare URL (auf der Hetzner-Box vorhanden).
- **Bitquery / Moralis** — vollständig und bequem, kostenpflichtig.

Die aktuelle Coin-Übersicht arbeitet deshalb mit Watchlist + Suchbegriffen +
den Paid-Feeds. Das ist ausreichend, um Token zu *bewerten*, die man schon
kennt. Es ist ausdrücklich keine vollständige Sicht auf neue Launches.

### 2. Holder-Anzahl ist unzuverlässig und teuer

RugCheck liefert `totalHolders` nicht für jeden Token. Exakt bekommt man die
Zahl nur über `getTokenAccounts` bzw. `getProgramAccounts` — ein Call pro
Token, der bei hunderten Token im 30-Sekunden-Takt weder in den Free-Tier
noch in irgendein sinnvolles Budget passt.

Wo RugCheck nichts liefert, bleibt das Feld `NULL`. Ein Filter auf
Holder-Anzahl schließt solche Token dann aus, statt sie durchzuwinken.

Fachlich ist das kein großer Verlust: Holder-Counts sind auf Solana durch
Sybil-Verteilung systematisch aufgeblasen und als absolute Zahl ohnehin
das schwächste Kriterium der Liste.

### 3. Historischer unrealisierter PnL ist nicht rekonstruierbar

Realisierter PnL pro Tag ist exakt — er ergibt sich aus dem Ledger.

Unrealisierter PnL für vergangene Tage bräuchte den historischen Preis
**jedes gehaltenen Memecoins pro Tag**. Jupiter liefert nur Spot, DexScreener
keine Zeitreihen, und die APIs, die das können (Birdeye, Bitquery), kosten.

Lösung: Der Scheduler schreibt jeden Abend um 23:55 einen Snapshot des
unrealisierten Werts in `equity_daily`. Der 30-Tage-Chart zeigt realisierten
PnL vollständig ab dem ersten Sync, unrealisierten erst ab dem Tag, an dem
das Tool läuft. Die API meldet das über `has_unrealized_history`.

### 4. Token↔Token-Swaps werden nicht verbucht

Direkte Swaps zwischen zwei Memecoins ohne SOL-Leg haben keinen belastbaren
Mittelkurs. Statt einen zu erfinden, überspringt der Parser sie und meldet
sie als `unconverted_swaps`. Bei SOL-gepaartem Memecoin-Handel betrifft das
in der Praxis fast nichts.

### 5. Sells ohne Kostenbasis

Wenn ein Token vor dem synchronisierten Zeitraum gekauft, per Transfer
eingegangen oder geairdroppt wurde, gibt es keinen Einstiegspreis. Solche
Verkäufe landen mit Kosten `0` in der Historie und sind als
`basis_unknown: true` markiert — sie erscheinen als voller Gewinn und müssen
für die Steuer von Hand korrigiert werden. Die Anzahl steht in der
Sync-Antwort als `sells_without_basis`.

### 6. Blockpit-Export: Asset-Zuordnung

Der Export folgt Blockpits generischem Template
(`Date (UTC); Integration Name; Label; Outgoing Asset; Outgoing Amount;
Incoming Asset; Incoming Amount; Fee Asset; Fee Amount`).

Memecoin-Ticker kollidieren ständig — es gibt dutzende Token namens `MOON`.
Der Export schreibt deshalb `TICKER-<erste 4 Zeichen der Mint>` statt des
nackten Tickers. Blockpit kann diese Assets nicht automatisch auflösen; sie
müssen beim Import einmalig gemappt werden. Ein nackter Ticker würde
stattdessen still auf das falsche Asset mappen, was schlechter ist.

Steuerlich relevant: Jeder Token↔SOL-Swap ist ein eigener Vorgang. Der Export
ist deshalb transaktionsbasiert (eine Zeile pro Swap-Leg), nicht
positionsbasiert.

## Warum Helius und nicht Solana-RPC + Jupiter

Beides zusammen, mit klarer Rollenteilung:

- **Helius für die Historie.** Nicht wegen der RPC-Qualität, sondern weil
  `getSignaturesForAddress` auf öffentlichen RPCs nur Signaturen liefert und
  jede Transaktion selbst dekodiert werden müsste. Raydium, Pump.fun, Meteora
  und Jupiter haben unterschiedliche Instruction-Layouts, und bei jedem neuen
  DEX bricht so ein Parser. Dazu fehlt öffentlichen RPCs das vollständige
  Archiv.
- **Jupiter für Spot-Preise.** Kostenlos, kein Key, genau dafür gebaut.

Wir verlassen uns dabei bewusst **nicht** auf Helius' `events.swap`. Neue
Memecoin-Router kommen dort als `type: UNKNOWN` ohne Swap-Event an. Stattdessen
liest der Parser `accountData` — die Netto-Bilanzveränderungen der eigenen
Wallet, die Helius für jede Transaktion befüllt. Ein Trade ist dann einfach,
was sich bewegt hat: SOL raus und genau ein Token rein ist ein Kauf. Das
funktioniert für jedes Programm, auch für die, die es nächsten Monat gibt.
