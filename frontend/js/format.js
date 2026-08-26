/* Number and date formatting.

   Locale is en-GB: 1,234.56 and 25/08/2026. Currency is USD throughout,
   because every price source in this project (Jupiter, DexScreener,
   CoinGecko) quotes USD. An earlier version rendered those same values with
   a euro sign, which was simply wrong — off by whatever EUR/USD happened to
   be. Converting would mean carrying an FX rate for no benefit; SOL and USD
   are the units this market actually trades in.
*/

const DECIMAL = new Intl.NumberFormat('en-GB', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const COMPACT = new Intl.NumberFormat('en-GB', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

export function usd(value, { compact = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return compact ? `$${COMPACT.format(value)}` : `$${DECIMAL.format(value)}`;
}

export function sol(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)} SOL`;
}

/** Absolute SOL amount, no sign — for costs, proceeds and balances. */
export function solPlain(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value.toFixed(digits)} SOL`;
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

/** Bare percentage for shares, where a sign would be nonsense. */
export function share(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  return `${value.toFixed(digits)}%`;
}

function trimZeros(text) {
  return text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text;
}

/** Token prices span many orders of magnitude, so significant digits win. */
function significant(value) {
  if (value >= 1) return trimZeros(value.toFixed(4));
  const digits = Math.min(12, Math.max(4, Math.ceil(-Math.log10(value)) + 3));
  return trimZeros(value.toFixed(digits));
}

export function price(value) {
  if (!value && value !== 0) return '—';
  if (value === 0) return '$0';
  return `$${significant(value)}`;
}

/** Same scale, but denominated in SOL — never prefix these with a $. */
export function priceSol(value) {
  if (!value && value !== 0) return '—';
  if (value === 0) return '0 SOL';
  return `${significant(value)} SOL`;
}

export function ageFromMinutes(minutes) {
  if (minutes === null || minutes === undefined) return null;
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 60 * 48) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

export function ageFromUnix(seconds) {
  if (!seconds) return null;
  return ageFromMinutes((Date.now() / 1000 - seconds) / 60);
}

export function shortDate(seconds) {
  if (!seconds) return '—';
  return new Date(seconds * 1000).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: '2-digit',
  });
}

export function dayLabel(iso) {
  const [, month, day] = iso.split('-');
  return `${day}/${month}`;
}

export function truncate(text, head = 4, tail = 4) {
  if (!text) return '';
  if (text.length <= head + tail + 1) return text;
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

export function direction(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'is-muted';
  if (value > 0) return 'is-up';
  if (value < 0) return 'is-down';
  return '';
}
