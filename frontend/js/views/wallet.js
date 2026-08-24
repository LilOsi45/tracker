/* Wallet screen: today's PnL, 30-day shape, open positions, closed trades. */

import { api, downloadCsv } from '../api.js';
import { renderChart } from '../chart.js';
import {
  direction,
  eur,
  price,
  priceSol,
  pct,
  shortDate,
  sol,
  solPlain,
  truncate,
} from '../format.js';
import { getSettings } from '../store.js';
import { toast } from '../ui.js';

const PAGE = 25;

let els = {};
let offset = 0;
let loading = false;
let hasUnrealizedHistory = null;

function wallet() {
  return getSettings().wallet.trim();
}

function empty(node, text) {
  node.replaceChildren(
    Object.assign(document.createElement('p'), { className: 'empty', textContent: text }),
  );
}

function renderSummary(summary, stale) {
  const today = summary.realized_today_sol ?? 0;
  els.pnlToday.textContent = sol(today);
  els.pnlToday.className = `hero__value ${direction(today)}`;

  const fiat = summary.realized_today_usd;
  const parts = [];
  if (fiat !== null && fiat !== undefined) parts.push(eur(fiat));
  if (summary.closes_today) {
    parts.push(`${summary.closes_today} Abschluss${summary.closes_today === 1 ? '' : 'e'}`);
  }
  els.pnlTodayFiat.textContent = parts.join('  ·  ');

  const unreal = summary.unrealized_sol;
  els.pnlUnreal.textContent = sol(unreal);
  els.pnlUnreal.className = direction(unreal);

  els.posCount.textContent = String(summary.open_positions ?? 0);
  els.pnlLifetime.textContent = sol(summary.realized_lifetime_sol);

  const notes = [];
  if (stale) notes.push('Offline — zwischengespeicherte Daten.');
  if (summary.sync && !summary.sync.backfill_complete) {
    notes.push('Historie wird noch rückwärts geladen. Sync erneut ausführen, bis sie steht.');
  }
  if (hasUnrealizedHistory === false) {
    // Not a placeholder and not a bug: per-token daily history is not
    // reconstructible from the free APIs, so the chart only carries realized
    // PnL until the nightly snapshots have accumulated.
    notes.push('Chart zeigt nur realisierten PnL — unrealisiert wird ab jetzt täglich mitgeschrieben.');
  }
  if (summary.sync?.last_synced_at) {
    const when = new Date(summary.sync.last_synced_at * 1000);
    notes.push(`Letzter Sync ${when.toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}.`);
  }
  els.footnote.textContent = notes.join(' ');
}

function positionRow(position) {
  const row = document.createElement('div');
  row.className = 'row';

  const top = document.createElement('div');
  top.className = 'row__top';

  const name = document.createElement('span');
  name.className = 'row__name';
  name.textContent = position.symbol || truncate(position.mint, 5, 4);

  const value = document.createElement('span');
  value.className = `row__value ${direction(position.unrealized_sol)}`;
  value.textContent =
    position.unrealized_sol === null ? 'kein Preis' : sol(position.unrealized_sol);

  top.append(name, value);

  const meta = document.createElement('div');
  meta.className = 'row__meta';

  if (position.current_price_usd === null || position.current_price_usd === undefined) {
    // Not routable any more. Showing the entry price as "current" would
    // pretend the position still has its original value.
    meta.innerHTML =
      `<span class="nb">Ein <b>${price(position.entry_price_usd)}</b></span> · ` +
      `<span class="unknown">kein aktueller Preis — Token nicht mehr handelbar</span>`;
  } else {
    meta.innerHTML =
      `<span class="nb">Ein <b>${price(position.entry_price_usd)}</b></span> · ` +
      `<span class="nb">Jetzt <b>${price(position.current_price_usd)}</b></span> · ` +
      `<span class="nb"><b>${pct(position.unrealized_pct)}</b></span> · ` +
      `<span class="nb">Einsatz ${solPlain(position.cost_sol)}</span>`;
  }

  row.append(top, meta);
  return row;
}

function tradeRow(trade) {
  const row = document.createElement('div');
  row.className = 'row';

  const top = document.createElement('div');
  top.className = 'row__top';

  const name = document.createElement('span');
  name.className = 'row__name';
  name.textContent = `${shortDate(trade.closed_at)}  ${trade.symbol || truncate(trade.mint, 5, 4)}`;

  const value = document.createElement('span');
  value.className = `row__value ${direction(trade.pnl_sol)}`;
  value.textContent = sol(trade.pnl_sol);

  top.append(name, value);

  const meta = document.createElement('div');
  meta.className = 'row__meta';

  if (trade.basis_unknown) {
    meta.innerHTML =
      `<span class="nb">Aus <b>${solPlain(trade.proceeds_sol)}</b></span> · ` +
      `<span class="flag-bad">Einstieg unbekannt — PnL zu hoch</span>`;
  } else {
    meta.innerHTML =
      `<span class="nb">Ein <b>${priceSol(trade.entry_price_sol)}</b></span> · ` +
      `<span class="nb">Aus <b>${priceSol(trade.exit_price_sol)}</b></span> · ` +
      `<span class="nb"><b>${pct(trade.pnl_pct)}</b></span>`;
  }

  row.append(top, meta);
  return row;
}

async function loadTrades({ append = false } = {}) {
  const address = wallet();
  if (!address) return;

  const { data } = await api.trades(address, { limit: PAGE, offset });
  const rows = data.trades.map(tradeRow);

  if (append) els.trades.append(...rows);
  else if (rows.length) els.trades.replaceChildren(...rows);
  else empty(els.trades, 'Noch keine geschlossenen Trades.');

  offset += data.trades.length;
  els.moreTrades.hidden = offset >= data.total;
  els.tradesNote.textContent = data.total ? `${data.total} gesamt` : '';
}

export async function loadWallet() {
  const address = wallet();

  if (!address) {
    els.pnlToday.textContent = '—';
    els.pnlToday.className = 'hero__value';
    empty(els.positions, 'Keine Wallet hinterlegt.');
    empty(els.trades, '');
    els.footnote.textContent = 'Unter Setup eine Wallet-Adresse eintragen.';
    return;
  }

  if (loading) return;
  loading = true;

  try {
    const [summary, positions, chart] = await Promise.all([
      api.summary(address),
      api.positions(address),
      api.chart(address, 30),
    ]);

    // Set before rendering the summary: the footnote reports on the chart.
    hasUnrealizedHistory = chart.data.has_unrealized_history;
    renderSummary(summary.data, summary.stale);

    const list = positions.data.positions;
    if (list.length) els.positions.replaceChildren(...list.map(positionRow));
    else empty(els.positions, 'Keine offenen Positionen.');
    els.posNote.textContent = list.length
      ? `Wert ${solPlain(positions.data.totals.value_sol)}`
      : '';

    renderChart(els.chart, chart.data.series);
    els.chartNote.textContent = 'realisiert pro Tag';

    offset = 0;
    await loadTrades();
  } catch (error) {
    toast(error.message || 'Laden fehlgeschlagen', true);
  } finally {
    loading = false;
  }
}

export function initWallet() {
  els = {
    pnlToday: document.getElementById('pnlToday'),
    pnlTodayFiat: document.getElementById('pnlTodayFiat'),
    pnlUnreal: document.getElementById('pnlUnreal'),
    posCount: document.getElementById('posCount'),
    pnlLifetime: document.getElementById('pnlLifetime'),
    chart: document.getElementById('chart'),
    chartNote: document.getElementById('chartNote'),
    positions: document.getElementById('positions'),
    posNote: document.getElementById('posNote'),
    trades: document.getElementById('trades'),
    tradesNote: document.getElementById('tradesNote'),
    moreTrades: document.getElementById('moreTrades'),
    footnote: document.getElementById('walletFootnote'),
  };

  els.moreTrades.addEventListener('click', () => {
    loadTrades({ append: true }).catch((error) => toast(error.message, true));
  });

  document.getElementById('bSync').addEventListener('click', async (event) => {
    const address = wallet();
    if (!address) return toast('Keine Wallet hinterlegt', true);

    event.target.disabled = true;
    toast('Sync läuft …');
    try {
      const result = await api.sync(address);
      const notes = [`${result.new_swaps} neue Swaps`];
      if (!result.backfill_complete) notes.push('Historie unvollständig — nochmal syncen');
      if (result.sells_without_basis) {
        notes.push(`${result.sells_without_basis} Verkäufe ohne Einstieg`);
      }
      toast(notes.join(' · '));
      await loadWallet();
    } catch (error) {
      toast(error.message || 'Sync fehlgeschlagen', true);
    } finally {
      event.target.disabled = false;
    }
  });

  document.getElementById('bExport').addEventListener('click', async () => {
    const address = wallet();
    if (!address) return toast('Keine Wallet hinterlegt', true);
    try {
      await downloadCsv(address, 'blockpit', `blockpit-${address.slice(0, 6)}.csv`);
      toast('Blockpit-CSV exportiert');
    } catch (error) {
      toast(error.message || 'Export fehlgeschlagen', true);
    }
  });

  return { onShow: loadWallet };
}
