/* Wallet screen: today's PnL, 30-day shape, open positions, closed trades. */

import { api, downloadCsv } from '../api.js';
import { renderChart } from '../chart.js';
import {
  bare,
  direction,
  usd,
  pct,
  sci,
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

/* Both lists are tables now, so an empty state has to be a row that spans
   the columns — a stray <p> inside a <tbody> is invalid and the browser
   hoists it out of the table. */
function empty(tbody, text) {
  const row = document.createElement('tr');
  const cell = document.createElement('td');
  cell.colSpan = 4;
  cell.className = 'empty';
  cell.textContent = text;
  row.append(cell);
  tbody.replaceChildren(row);
}

function cell(text, className = '') {
  const td = document.createElement('td');
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function renderSummary(summary, stale) {
  const today = summary.realized_today_sol ?? 0;
  els.pnlToday.textContent = sol(today);
  els.pnlToday.className = `hero__value ${direction(today)}`;

  const fiat = summary.realized_today_usd;
  const parts = [];
  if (fiat !== null && fiat !== undefined) parts.push(usd(fiat));
  if (summary.closes_today) {
    parts.push(`${summary.closes_today} close${summary.closes_today === 1 ? '' : 's'}`);
  }
  els.pnlTodayFiat.textContent = parts.join('  ·  ');

  // null means the balance could not be read, which is not the same as an
  // empty wallet — show a dash, never a zero.
  const balance = summary.sol_balance;
  els.solBalance.textContent = balance === null || balance === undefined
    ? '—'
    : solPlain(balance);
  els.solBalance.title = summary.sol_balance_usd ? usd(summary.sol_balance_usd) : '';

  const unreal = summary.unrealized_sol;
  els.pnlUnreal.textContent = sol(unreal);
  els.pnlUnreal.className = direction(unreal);

  els.posCount.textContent = String(summary.open_positions ?? 0);
  els.pnlLifetime.textContent = sol(summary.realized_lifetime_sol);

  const notes = [];
  if (stale) notes.push('Offline — showing cached data.');
  if (summary.sync && !summary.sync.backfill_complete) {
    notes.push('History is still loading backwards. Keep pressing Sync until it completes.');
  }
  if (hasUnrealizedHistory === false) {
    // Not a placeholder and not a bug: per-token daily history is not
    // reconstructible from the free APIs, so the chart only carries realized
    // PnL until the nightly snapshots have accumulated.
    notes.push('Chart shows realised PnL only — unrealised is recorded daily from now on.');
  }
  if (summary.sync?.last_synced_at) {
    const when = new Date(summary.sync.last_synced_at * 1000);
    notes.push(`Last sync ${when.toLocaleString('en-GB', { dateStyle: 'short', timeStyle: 'short' })}.`);
  }
  els.footnote.textContent = notes.join(' ');
}

function positionRow(position) {
  const row = document.createElement('tr');
  const priced =
    position.current_price_usd !== null && position.current_price_usd !== undefined;

  row.append(cell(position.symbol || truncate(position.mint, 5, 4)));
  row.append(cell(sci(position.entry_price_usd), 'num'));
  // Not routable any more. Showing the entry price as "current" would pretend
  // the position still has its original value, so it stays a dash.
  row.append(cell(priced ? sci(position.current_price_usd) : '—', 'num'));
  row.append(
    cell(
      priced ? bare(sol(position.unrealized_sol)) : '—',
      priced ? direction(position.unrealized_sol) : 'is-muted',
    ),
  );
  row.title = priced
    ? `${pct(position.unrealized_pct)} · cost ${solPlain(position.cost_sol)}`
    : 'no current price — token is no longer tradable';
  return row;
}

function tradeRow(trade) {
  const row = document.createElement('tr');

  const first = document.createElement('td');
  first.append(
    `${shortDate(trade.closed_at)} ${trade.symbol || truncate(trade.mint, 5, 4)}`,
  );
  if (trade.basis_unknown) {
    const flag = document.createElement('span');
    flag.className = 'flag';
    flag.textContent = ' no entry';
    first.append(flag);
  }
  row.append(first);

  row.append(cell(trade.basis_unknown ? '—' : sci(trade.entry_price_sol), 'num'));
  row.append(cell(sci(trade.exit_price_sol), 'num'));
  row.append(cell(bare(sol(trade.pnl_sol)), direction(trade.pnl_sol)));

  row.title = trade.basis_unknown
    ? 'entry unknown — PnL overstated'
    : `${pct(trade.pnl_pct)} · cost ${solPlain(trade.cost_sol)}`;
  return row;
}

async function loadTrades({ append = false } = {}) {
  const address = wallet();
  if (!address) return;

  const { data } = await api.trades(address, { limit: PAGE, offset });
  const rows = data.trades.map(tradeRow);

  if (append) els.trades.append(...rows);
  else if (rows.length) els.trades.replaceChildren(...rows);
  else empty(els.trades, 'No closed trades yet.');

  offset += data.trades.length;
  els.moreTrades.hidden = offset >= data.total;
  els.tradesNote.textContent = data.total ? `${data.total} total` : '';
}

function setState(state, message = '') {
  els.view.dataset.state = state;
  els.noticeText.textContent = message;
}

export async function loadWallet() {
  const address = wallet();

  if (!address) {
    setState('empty', 'No wallet configured.');
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

    // A never-synced wallet has no data, not zero data. Rendering the normal
    // layout would show "+0.000 SOL" for today, which reads like a fact.
    if (!summary.data.sync?.last_synced_at) {
      setState('unsynced', 'Not synced yet.\nSync pulls in your trade history.');
      return;
    }

    setState('ready');

    // Set before rendering the summary: the footnote reports on the chart.
    hasUnrealizedHistory = chart.data.has_unrealized_history;
    renderSummary(summary.data, summary.stale);

    const list = positions.data.positions;
    if (list.length) els.positions.replaceChildren(...list.map(positionRow));
    else empty(els.positions, 'No open positions.');
    els.posNote.textContent = list.length
      ? `Value ${solPlain(positions.data.totals.value_sol)}`
      : '';

    renderChart(els.chart, chart.data.series);
    els.chartNote.textContent = 'realised per day';

    offset = 0;
    await loadTrades();
  } catch (error) {
    toast(error.message || 'Loading failed', true);
  } finally {
    loading = false;
  }
}

export function initWallet() {
  els = {
    view: document.getElementById('view-wallet'),
    noticeText: document.getElementById('walletNoticeText'),
    pnlToday: document.getElementById('pnlToday'),
    pnlTodayFiat: document.getElementById('pnlTodayFiat'),
    solBalance: document.getElementById('solBalance'),
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
    if (!address) return toast('No wallet configured', true);

    event.target.disabled = true;
    toast('Syncing …');
    try {
      const result = await api.sync(address);
      const notes = [`${result.new_swaps} new swaps`];
      if (!result.backfill_complete) notes.push('history incomplete — sync again');
      if (result.sells_without_basis) {
        notes.push(`${result.sells_without_basis} sells with no entry`);
      }
      toast(notes.join(' · '));
      await loadWallet();
    } catch (error) {
      toast(error.message || 'Sync failed', true);
    } finally {
      event.target.disabled = false;
    }
  });

  document.getElementById('bExport').addEventListener('click', async () => {
    const address = wallet();
    if (!address) return toast('No wallet configured', true);
    try {
      await downloadCsv(address, 'blockpit', `blockpit-${address.slice(0, 6)}.csv`);
      toast('Blockpit CSV exported');
    } catch (error) {
      toast(error.message || 'Export failed', true);
    }
  });

  return { onShow: loadWallet };
}
