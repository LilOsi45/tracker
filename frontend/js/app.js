/* App shell: hash router, view lifecycle, service worker registration.

   A hash router keeps deep links working from the home screen without any
   server rewrite rules, and it works identically when the page is opened
   from the cache with no network at all.
*/

import { initCheck } from './views/check.js';
import { initCoins } from './views/coins.js';
import { initSetup } from './views/setup.js';
import { initWallet } from './views/wallet.js';

const TITLES = {
  check: 'Pre-Buy',
  wallet: 'Wallet',
  coins: 'Coins',
  setup: 'Setup',
};

const views = {};
let current = null;

function show(name) {
  if (!TITLES[name]) name = 'check';

  for (const [key, view] of Object.entries(views)) {
    document.getElementById(`view-${key}`).hidden = key !== name;
    void view;
  }

  for (const tab of document.querySelectorAll('.tabs a')) {
    if (tab.dataset.tab === name) tab.setAttribute('aria-current', 'page');
    else tab.removeAttribute('aria-current');
  }

  document.getElementById('viewTitle').textContent = TITLES[name];

  // The loss streak belongs to the checklist; every other view owns the slot
  // itself or leaves it empty.
  const slot = document.getElementById('topbarSlot');
  if (name !== 'check') {
    slot.textContent = '';
    slot.classList.remove('is-hot');
  }

  current = name;
  views[name]?.onShow?.();
}

function route() {
  show((location.hash.replace(/^#\/?/, '') || 'check').split('?')[0]);
}

function boot() {
  views.check = initCheck();
  views.wallet = initWallet();
  views.coins = initCoins();
  views.setup = initSetup();

  window.addEventListener('hashchange', route);
  route();

  // Coming back to a backgrounded tab should not show yesterday's numbers.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && current) {
      views[current]?.onShow?.();
    }
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js').catch(() => {
        /* offline support is a bonus, never a hard requirement */
      });
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
