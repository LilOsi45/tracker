/* Local persistence.

   The prototype called window.storage.get/set, which does not exist in any
   browser — wrapped in try/catch, so the loss streak silently reset on every
   reload. That is the one number that must survive a reload, so it lives in
   localStorage now, with every access guarded: private mode and blocked site
   data both make localStorage throw on access, not just on write.
*/

const KEY_SETTINGS = 'tracker.settings';
const KEY_TILT = 'tracker.tilt';
const KEY_CHECK = 'tracker.check';

function read(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

const DEFAULT_SETTINGS = {
  apiUrl: '',
  token: '',
  wallet: '',
  budget: 300,
  risk: 5,
};

export function getSettings() {
  return { ...DEFAULT_SETTINGS, ...read(KEY_SETTINGS, {}) };
}

export function saveSettings(patch) {
  const next = { ...getSettings(), ...patch };
  write(KEY_SETTINGS, next);
  return next;
}

/* --- loss streak ------------------------------------------------------- */

function today() {
  return new Date().toDateString();
}

export function getStreak() {
  const stored = read(KEY_TILT, null);
  if (!stored || stored.date !== today()) return 0;
  return Number(stored.n) || 0;
}

export function setStreak(n) {
  write(KEY_TILT, { date: today(), n });
  return n;
}

/* --- in-progress checklist --------------------------------------------- */

/* Survives a reload mid-check, which happens constantly on mobile when the
   browser evicts a backgrounded tab. Cleared on reset. */

export function getCheckState() {
  const stored = read(KEY_CHECK, null);
  if (!stored || stored.date !== today()) return null;
  return stored;
}

export function saveCheckState(state) {
  write(KEY_CHECK, { ...state, date: today() });
}

export function clearCheckState() {
  try {
    window.localStorage.removeItem(KEY_CHECK);
  } catch {
    /* nothing we can do, and nothing that needs saying */
  }
}
