/* Pre-buy checklist. */

import {
  clearCheckState,
  getCheckState,
  getSettings,
  getStreak,
  saveCheckState,
  saveSettings,
  setStreak,
} from '../store.js';

const HARD = [
  ['Mint authority not revoked', 'Dev can print more at will'],
  ['Freeze authority not revoked', 'Dev can freeze your wallet'],
  ['LP not burned or locked', 'Liquidity can be pulled any time'],
  ['Dev holds over 10%', 'One sell tips the whole chart'],
  ['Visible bundle cluster', 'Hidden supply spread across wallets'],
  ['Dev has already sold', 'No reason left for them to deliver'],
];

const SOFT = [
  ['Top 10 holders over 25%', 'excluding the LP'],
  ['LP under $15k', 'Exit gets expensive, you move the price'],
  ['Absurd volume/LP ratio', 'Points at wash trading'],
  ['Holder count grew implausibly fast', 'Bots, not buyers'],
  ['Socials look botted', 'Replies from accounts with no history'],
  ['Ticker already exists elsewhere', 'Copy launch riding someone else’s hype'],
];

const TOTAL = HARD.length + SOFT.length;

const LINKS = {
  rugcheck: (ca) => `https://rugcheck.xyz/tokens/${ca}`,
  bubblemaps: (ca) => `https://v2.bubblemaps.io/map?address=${ca}&chain=solana`,
  axiom: (ca) => `https://axiom.trade/t/${ca}`,
  dexscreener: (ca) => `https://dexscreener.com/solana/${ca}`,
  solscan: (ca) => `https://solscan.io/token/${ca}`,
};

let streak = 0;
let els = {};

function buildRow(text, hint, soft) {
  const row = document.createElement('div');
  row.className = soft ? 'check check--soft' : 'check';
  row.dataset.on = 'false';
  row.tabIndex = 0;
  row.setAttribute('role', 'checkbox');
  row.setAttribute('aria-checked', 'false');

  const box = document.createElement('div');
  box.className = 'check__box';
  box.textContent = '✕';

  const label = document.createElement('div');
  label.className = 'check__text';
  label.textContent = text;

  const note = document.createElement('span');
  note.className = 'check__hint';
  note.textContent = hint;
  label.append(note);

  row.append(box, label);

  const toggle = () => {
    const on = row.dataset.on === 'true';
    row.dataset.on = on ? 'false' : 'true';
    row.setAttribute('aria-checked', on ? 'false' : 'true');
    score();
    persist();
  };

  row.addEventListener('click', toggle);
  row.addEventListener('keydown', (event) => {
    if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      toggle();
    }
  });

  return row;
}

function build(container, rows, soft) {
  container.replaceChildren(...rows.map(([text, hint]) => buildRow(text, hint, soft)));
}

function marked(container) {
  return [...container.querySelectorAll('.check')].map((row) => row.dataset.on === 'true');
}

function countOn(container) {
  return marked(container).filter(Boolean).length;
}

function setVerdict(state, word, note) {
  els.verdict.className = state ? `verdict is-${state}` : 'verdict';
  els.word.textContent = word;
  els.note.textContent = note;
  els.pos.classList.toggle('is-locked', state === 'stop');
}

function score() {
  if (streak >= 3) {
    setVerdict('stop', 'Done', 'Three losses in a row. Tilt costs more than any rug.');
    return;
  }

  const hard = countOn(els.hard);
  const soft = countOn(els.soft);

  if (hard > 0) {
    setVerdict(
      'stop',
      'Walk away',
      `${hard} deal breaker${hard > 1 ? 's' : ''} hit — do not buy`,
    );
    return;
  }

  if (soft >= 2) {
    setVerdict('warn', 'Careful', `${soft} warning signs — half size at most, if at all`);
    return;
  }

  if (soft === 1) {
    setVerdict('warn', 'Careful', '1 warning sign — decide on purpose, do not click past it');
    return;
  }

  // Nothing marked and no contract is not a clean token, it is an unread
  // checklist. Saying "clean" there would be the app lying to you.
  if (!els.ca.value.trim()) {
    setVerdict('', 'Ready', 'Paste a contract, then work through the list');
    return;
  }

  setVerdict('go', 'Clean', `0 of ${TOTAL} flagged. Set your auto-sell before you buy.`);
}

function paintStreak() {
  els.slot.textContent = `${streak} loss${streak === 1 ? '' : 'es'}`;
  els.slot.classList.toggle('is-hot', streak >= 3);
}

function updateLinks() {
  const ca = els.ca.value.trim();
  const ok = ca.length > 30;
  for (const anchor of els.jump.querySelectorAll('a')) {
    anchor.setAttribute('aria-disabled', ok ? 'false' : 'true');
    if (ok) anchor.href = LINKS[anchor.dataset.jump](encodeURIComponent(ca));
    else anchor.removeAttribute('href');
  }
}

function calc() {
  const budget = Number.parseFloat(els.budget.value) || 0;
  const risk = Number.parseFloat(els.risk.value) || 0;
  const size = (budget * risk) / 100;
  const money = size.toLocaleString('en-GB', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  els.size.textContent = `$${money}`;
  els.exit.textContent = `$${money}`;
  els.runs.textContent = risk > 0 ? String(Math.floor(100 / risk)) : '—';
}

function persist() {
  saveCheckState({
    ca: els.ca.value,
    hard: marked(els.hard),
    soft: marked(els.soft),
  });
}

function restore() {
  const state = getCheckState();
  if (!state) return;

  els.ca.value = state.ca || '';
  const apply = (container, flags = []) => {
    [...container.querySelectorAll('.check')].forEach((row, index) => {
      const on = Boolean(flags[index]);
      row.dataset.on = String(on);
      row.setAttribute('aria-checked', String(on));
    });
  };
  apply(els.hard, state.hard);
  apply(els.soft, state.soft);
}

function reset() {
  for (const row of document.querySelectorAll('#hard .check, #soft .check')) {
    row.dataset.on = 'false';
    row.setAttribute('aria-checked', 'false');
  }
  els.ca.value = '';
  clearCheckState();
  updateLinks();
  score();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

export function initCheck() {
  els = {
    verdict: document.getElementById('verdict'),
    word: document.getElementById('verdictWord'),
    note: document.getElementById('verdictNote'),
    hard: document.getElementById('hard'),
    soft: document.getElementById('soft'),
    ca: document.getElementById('ca'),
    jump: document.getElementById('jump'),
    pos: document.getElementById('posBlock'),
    budget: document.getElementById('budget'),
    risk: document.getElementById('risk'),
    size: document.getElementById('oSize'),
    exit: document.getElementById('oExit'),
    runs: document.getElementById('oRuns'),
    slot: document.getElementById('topbarSlot'),
  };

  build(els.hard, HARD, false);
  build(els.soft, SOFT, true);

  const settings = getSettings();
  els.budget.value = settings.budget;
  els.risk.value = settings.risk;

  els.ca.addEventListener('input', () => {
    updateLinks();
    score();
    persist();
  });

  const onSizing = () => {
    calc();
    saveSettings({
      budget: Number.parseFloat(els.budget.value) || 0,
      risk: Number.parseFloat(els.risk.value) || 0,
    });
  };
  els.budget.addEventListener('input', onSizing);
  els.risk.addEventListener('input', onSizing);

  document.getElementById('bLoss').addEventListener('click', () => {
    streak = setStreak(streak + 1);
    paintStreak();
    score();
  });

  document.getElementById('bWin').addEventListener('click', () => {
    streak = setStreak(0);
    paintStreak();
    reset();
  });

  document.getElementById('bReset').addEventListener('click', reset);

  streak = getStreak();
  restore();
  calc();
  updateLinks();
  score();

  return { onShow: paintStreak };
}
