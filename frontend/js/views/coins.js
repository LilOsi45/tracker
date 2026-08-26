/* Coin overview. Filters and sorting run server-side against the snapshot
   table, so the phone only ever holds one page. */

import { api } from '../api.js';
import { ageFromMinutes, price, pct, share, truncate, usd } from '../format.js';
import { toast } from '../ui.js';

const PAGE = 30;

const FILTERS = {
  authorities: { authorities_revoked: true },
  lp: { min_liquidity: 15000 },
  top10: { max_top10: 25 },
  fresh: { max_age_minutes: 240 },
};

let els = {};
let active = new Set();
let sort = { field: 'liquidity_usd', dir: 'desc' };
let offset = 0;
let total = 0;
let searchTimer = null;

function params() {
  const query = {
    sort: sort.field,
    direction: sort.dir,
    limit: PAGE,
    offset,
  };
  for (const key of active) Object.assign(query, FILTERS[key]);

  const term = els.search.value.trim();
  if (term) query.search = term;

  return query;
}

/* A metric we could not fetch is shown as "?" rather than as a number. The
   backend sends null for exactly that reason — see docs/data-sources.md.

   Each metric is one non-breaking unit so a wrap never splits a label from
   its value, which reads as two unrelated fragments. */
function metric(label, value, { bad = false } = {}) {
  if (value === null || value === undefined) {
    return `<span class="nb">${label} <span class="unknown">?</span></span>`;
  }
  return `<span class="nb">${label} <b class="${bad ? 'flag-bad' : ''}">${value}</b></span>`;
}

function authorityFlag(token) {
  const mint = token.mint_authority;
  const freeze = token.freeze_authority;

  if (mint === null || freeze === null || mint === undefined || freeze === undefined) {
    return '<span class="unknown">Authority ?</span>';
  }
  if (mint === 0 && freeze === 0) {
    return '<span class="flag-ok">Authority revoked</span>';
  }

  const open = [];
  if (mint === 1) open.push('Mint');
  if (freeze === 1) open.push('Freeze');
  return `<span class="flag-bad">${open.join(' + ')} open</span>`;
}

function coinRow(token) {
  const row = document.createElement('div');
  row.className = 'row';

  const top = document.createElement('div');
  top.className = 'row__top';

  const name = document.createElement('span');
  name.className = 'row__name';
  name.textContent = token.symbol || truncate(token.mint, 5, 4);

  const change = document.createElement('span');
  const value = token.price_change_24h;
  change.className = `row__value ${value > 0 ? 'is-up' : value < 0 ? 'is-down' : 'is-muted'}`;
  change.textContent = value === null || value === undefined ? price(token.price_usd) : pct(value);

  top.append(name, change);

  const line1 = document.createElement('div');
  line1.className = 'row__meta';
  line1.innerHTML = [
    metric('MCap', token.market_cap ? usd(token.market_cap, { compact: true }) : null),
    metric('LP', token.liquidity_usd ? usd(token.liquidity_usd, { compact: true }) : null),
    metric('Age', ageFromMinutes(token.age_minutes)),
    metric(
      'Vol/LP',
      token.volume_liquidity_ratio === null || token.volume_liquidity_ratio === undefined
        ? null
        : token.volume_liquidity_ratio.toFixed(1),
    ),
  ].join(' · ');

  const line2 = document.createElement('div');
  line2.className = 'row__meta';
  line2.innerHTML = [
    metric('Top 10', share(token.top10_pct), { bad: token.top10_pct > 25 }),
    metric('Holders', token.holder_count),
    metric('LP locked', share(token.lp_locked_pct), { bad: token.lp_locked_pct < 90 }),
    `<span class="nb">${authorityFlag(token)}</span>`,
  ].join(' · ');

  const links = document.createElement('div');
  links.className = 'row__links';
  for (const [label, key] of [
    ['RugCheck', 'rugcheck'],
    ['Bubblemaps', 'bubblemaps'],
    ['Axiom', 'axiom'],
    ['DexScreener', 'dexscreener'],
  ]) {
    const anchor = document.createElement('a');
    anchor.textContent = label;
    anchor.href = token.links[key];
    anchor.target = '_blank';
    anchor.rel = 'noopener';
    links.append(anchor);
  }

  row.append(top, line1, line2, links);
  return row;
}

async function load({ append = false } = {}) {
  try {
    const { data, stale } = await api.coins(params());
    total = data.total;

    const rows = data.tokens.map(coinRow);
    if (append) els.list.append(...rows);
    else if (rows.length) els.list.replaceChildren(...rows);
    else {
      els.list.replaceChildren(
        Object.assign(document.createElement('p'), {
          className: 'empty',
          textContent: active.size || els.search.value.trim()
            ? 'No token matches these filters.'
            : 'Nothing loaded yet. Press Refresh.',
        }),
      );
    }

    offset += data.tokens.length;
    els.more.hidden = offset >= total;

    const notes = [`${total} tokens cached.`];
    if (stale) notes.push('Offline — showing cached data.');
    notes.push('DexScreener has no new-pairs feed; this list is watchlist plus search.');
    els.footnote.textContent = notes.join(' ');
  } catch (error) {
    toast(error.message || 'Loading failed', true);
  }
}

function reload() {
  offset = 0;
  return load();
}

function paintChips() {
  for (const chip of els.filters.querySelectorAll('.chip')) {
    chip.setAttribute('aria-pressed', String(active.has(chip.dataset.filter)));
  }
  for (const chip of els.sort.querySelectorAll('.chip')) {
    const on = chip.dataset.sort === sort.field;
    chip.setAttribute('aria-pressed', String(on));
    if (on) chip.dataset.dir = sort.dir;
    else delete chip.dataset.dir;
  }
}

export function initCoins() {
  els = {
    list: document.getElementById('coins'),
    filters: document.getElementById('coinFilters'),
    sort: document.getElementById('coinSort'),
    search: document.getElementById('coinSearch'),
    more: document.getElementById('bCoinMore'),
    footnote: document.getElementById('coinsFootnote'),
  };

  els.filters.addEventListener('click', (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    const key = chip.dataset.filter;
    if (active.has(key)) active.delete(key);
    else active.add(key);
    paintChips();
    reload();
  });

  els.sort.addEventListener('click', (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    const field = chip.dataset.sort;
    // Tapping the active sort flips direction, which is what you want when
    // you switch from "biggest LP" to "smallest LP".
    if (sort.field === field) sort.dir = sort.dir === 'desc' ? 'asc' : 'desc';
    else sort = { field, dir: 'desc' };
    paintChips();
    reload();
  });

  els.search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(reload, 300);
  });

  els.more.addEventListener('click', () => load({ append: true }));

  document.getElementById('bCoinRefresh').addEventListener('click', async (event) => {
    event.target.disabled = true;
    toast('Fetching market data …');
    try {
      const result = await api.refreshCoins();
      toast(`${result.market_updated} updated, ${result.safety_updated} checked`);
      await reload();
    } catch (error) {
      toast(error.message || 'Refresh failed', true);
    } finally {
      event.target.disabled = false;
    }
  });

  paintChips();

  return { onShow: () => (offset === 0 ? load() : undefined) };
}
