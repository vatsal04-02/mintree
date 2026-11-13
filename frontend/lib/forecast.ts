/** Projection (phase 3.5). A straight line fitted to the latest points of a series and carried forward, with a band one standard deviation of the fit's
 *  misses either side. It is only a picture of where the recent trend points: charts that show it label it "Projected", and nothing here feeds a figure,
 *  a KPI, an alert or any calculation. The same rule as the telemetry in lib/demo-telemetry.ts: visible, marked, never counted. */
import { addDays } from "./format";

export type ProjectedPoint = { label: string; value: number; lo: number; hi: number };

/** Fit y = a + b x to the last `window` points and extend `ahead` steps (`stepDays` days apart). Returns [] when there is too little to say anything. */
export function projectSeries(points: { label: string; value: number }[], ahead: number, stepDays: number, window = 21): ProjectedPoint[] {
  const pts = points.slice(-window);
  const n = pts.length;
  if (n < 6 || pts.every((p) => p.value === 0)) return [];
  const xs = pts.map((_, i) => i), ys = pts.map((p) => p.value);
  const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
  const sxx = xs.reduce((a, x) => a + (x - mx) ** 2, 0), sxy = xs.reduce((a, x, i) => a + (x - mx) * (ys[i] - my), 0);
  const b = sxx ? sxy / sxx : 0, a = my - b * mx;
  const sd = Math.sqrt(xs.reduce((acc, x, i) => acc + (ys[i] - (a + b * x)) ** 2, 0) / Math.max(n - 2, 1));
  const last = points[points.length - 1].label;
  return Array.from({ length: ahead }, (_, k) => {
    const v = Math.max(0, a + b * (n + k));
    return { label: addDays(last, stepDays * (k + 1)), value: v, lo: Math.max(0, v - sd), hi: v + sd };
  });
}
