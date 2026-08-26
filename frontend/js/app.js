/* App shell: hash router, view lifecycle, service worker registration.

   A hash router keeps deep links working from the home screen without any
   server rewrite rules, and it works identically when the page is opened
   from the cache with no network at all.
*/

import { saveSettings } from './store.js';
import { toast } from './ui.js';
import { initCheck } from './views/check.js';
import { initCoins } from './views/coins.js';
import { initSetup } from './views/setup.js';
import { initWallet } from './views/wallet.js';

const TITLES = {
  check: 'Pre-buy',
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

/* Tap-to-configure.

   The installer prints a link carrying the access token so a 48-character
   secret never has to be typed on a phone keyboard. The values are moved
   into local settings and stripped from the URL immediately, so the token
   does not linger in history or get handed over by an accidental share.
*/
function consumeSetupLink() {
  const hash = location.hash;
  const query = hash.indexOf('?');
  if (query === -1) return false;

  const params = new URLSearchParams(hash.slice(query + 1));
  const patch = {};
  const token = params.get('token');
  const wallet = params.get('wallet');
  if (token) patch.token = token;
  if (wallet) patch.wallet = wallet;

  // replaceState, not location.replace: it drops the secret from the current
  // history entry without triggering a navigation.
  try {
    history.replaceState(
      null,
      '',
      `${location.pathname}${location.search}${hash.slice(0, query)}`,
    );
  } catch {
    location.hash = hash.slice(0, query);
  }

  if (Object.keys(patch).length === 0) return false;

  saveSettings(patch);
  const saved = [token && 'Token', wallet && 'wallet'].filter(Boolean).join(' and ');
  setTimeout(() => toast(`${saved} applied`), 300);
  return true;
}

function route() {
  show((location.hash.replace(/^#\/?/, '') || 'check').split('?')[0]);
}

function boot() {
  // Before the views read settings, so Setup renders the new values.
  consumeSetupLink();

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
