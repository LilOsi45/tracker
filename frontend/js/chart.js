/* 30-day chart, hand-drawn SVG.

   Deliberately axis-free: bars are the day's realized PnL against a zero
   hairline, the brass line is the running total on its own scale. Gridlines
   and tick labels would add three more visual layers to read a shape that is
   already obvious — up or down, and how lumpy.
*/

import { dayLabel, sol } from './format.js';

const NS = 'http://www.w3.org/2000/svg';

const W = 320;
const H = 96;
const PAD_Y = 8;

function el(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

export function renderChart(container, series) {
  container.replaceChildren();

  if (!series || series.length === 0) {
    container.append(Object.assign(document.createElement('p'), {
      className: 'empty',
      textContent: 'No data yet.',
    }));
    return;
  }

  const values = series.map((point) => point.realized_sol || 0);
  const cumulative = series.map((point) => point.cumulative_realized_sol || 0);
  const peak = Math.max(...values.map(Math.abs), 1e-9);

  const svg = el('svg', {
    viewBox: `0 0 ${W} ${H}`,
    role: 'img',
    'aria-label': `Realised PnL over the last ${series.length} days`,
    preserveAspectRatio: 'none',
  });

  const mid = H / 2;
  const usable = mid - PAD_Y;
  const slot = W / series.length;
  const barWidth = Math.max(2, slot * 0.62);

  values.forEach((value, index) => {
    if (value === 0) return;
    const height = Math.max(1, (Math.abs(value) / peak) * usable);
    const x = index * slot + (slot - barWidth) / 2;
    svg.append(el('rect', {
      x: x.toFixed(2),
      y: (value > 0 ? mid - height : mid).toFixed(2),
      width: barWidth.toFixed(2),
      height: height.toFixed(2),
      class: value > 0 ? 'bar-up' : 'bar-down',
    }));
  });

  svg.append(el('line', {
    x1: 0, y1: mid, x2: W, y2: mid, class: 'baseline',
  }));

  // The running total gets its own scale so a flat month is still visible.
  const cumMin = Math.min(...cumulative, 0);
  const cumMax = Math.max(...cumulative, 0);
  const cumSpan = cumMax - cumMin || 1e-9;

  if (cumMax !== cumMin) {
    const points = cumulative
      .map((value, index) => {
        const x = index * slot + slot / 2;
        const y = H - PAD_Y - ((value - cumMin) / cumSpan) * (H - PAD_Y * 2);
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
    svg.append(el('polyline', { points, class: 'cume' }));
  }

  const caption = document.createElement('div');
  caption.className = 'chart__caption';
  caption.append(
    Object.assign(document.createElement('span'), {
      textContent: dayLabel(series[0].day),
    }),
    Object.assign(document.createElement('span'), {
      textContent: `Total ${sol(cumulative[cumulative.length - 1])}`,
    }),
    Object.assign(document.createElement('span'), {
      textContent: dayLabel(series[series.length - 1].day),
    }),
  );

  container.append(svg, caption);
}
