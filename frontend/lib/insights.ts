/** Insight lines (phase 3.3): one plain sentence under a headline figure or chart, built from a fixed template and the numbers already on the page. There is no
 *  language model and nothing is guessed: each function returns null when the figures it needs are missing or too small to say anything, and the caller then
 *  shows no line at all. Numbers are formatted by lib/format.ts like everywhere else. */
import { fmtNum, fmtRupees } from "./format";

const pct = (n: number) => `${fmtNum(Math.abs(n), Math.abs(n) < 10 ? 1 : 0)}%`;

/** "Net revenue is 9.8% above the previous 30 days, led by Olive Body Butter (21% of invoiced value)." */
export function revenueInsight(i: { net?: number | null; prev?: number | null; days: number; topName?: string; topValue?: number; total?: number }): string | null {
  const { net, prev } = i;
  if (net == null || prev == null || !Number.isFinite(net) || !Number.isFinite(prev) || prev <= 0) return null;
  const change = ((net - prev) / prev) * 100;
  const head = Math.abs(change) < 0.05 ? `Net revenue is level with the previous ${i.days} days` : `Net revenue is ${pct(change)} ${change > 0 ? "above" : "below"} the previous ${i.days} days`;
  const lead = i.topName && i.topValue && i.total && i.total > 0 ? `, led by ${i.topName} (${pct((i.topValue / i.total) * 100)} of invoiced value)` : "";
  return `${head}${lead}.`;
}

/** "824 of 850 planned units were made (96.9%); 26 are short." */
export function planInsight(made: number, planned: number): string | null {
  if (!(planned > 0) || !Number.isFinite(made)) return null;
  const short = Math.max(0, planned - made);
  return `${fmtNum(made)} of ${fmtNum(planned)} planned units were made (${fmtNum((made / planned) * 100, 1)}%)${short ? `; ${fmtNum(short)} ${short === 1 ? "is" : "are"} short` : ""}.`;
}

/** "5 items need action now and 6 more need review; the oldest overdue invoice is 88 days late." */
export function attentionInsight(i: { high: number; medium: number; oldestOverdueDays?: number; overdueAmount?: number }): string | null {
  if (i.high + i.medium === 0) return null;
  const parts = [i.high ? `${fmtNum(i.high)} ${i.high === 1 ? "item needs" : "items need"} action now` : "", i.medium ? `${i.high ? "and " : ""}${fmtNum(i.medium)} ${i.high ? "more " : ""}${i.medium === 1 ? "needs" : "need"} review` : ""].filter(Boolean).join(" ");
  const tail = i.oldestOverdueDays && i.overdueAmount ? `; ${fmtRupees(i.overdueAmount)} of invoices is overdue, the oldest by ${fmtNum(i.oldestOverdueDays)} days` : "";
  return `${parts}${tail}.`;
}
