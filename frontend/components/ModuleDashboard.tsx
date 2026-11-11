"use client";
/** The module landing-page dashboard. It reads whatever the module's /dashboard endpoint returns and draws it by shape, so every module gets the same treatment:
 *    plain numbers            -> figure cards (ReportView)
 *    "<name>_trend" arrays    -> a daily bar chart with its total, daily average and peak
 *    { label: number } objects -> a ranked bar panel (status mix, platform split, top products, spend by campaign)
 *  Whether a figure is money is decided from its name, as everywhere else in the module screens. */
import { ReportView, isMoneyKey } from "./ModuleKit";
import { InView } from "./fx";
import { fieldLabel, fmtDay, fmtNum, fmtRupees, titleCase } from "@/lib/format";

type Point = { label: string; value: number };
const isTrend = (k: string, v: unknown): v is Point[] => k.endsWith("_trend") && Array.isArray(v) && v.length > 0 && v.every((p) => p && typeof (p as Point).value === "number");
const isBars = (v: unknown): v is Record<string, number> => !!v && typeof v === "object" && !Array.isArray(v) && Object.values(v).length > 0 && Object.values(v).every((x) => typeof x === "number");

const fmt = (k: string, n: number) => (isMoneyKey(k) ? fmtRupees(n) : fmtNum(n, Number.isInteger(n) ? 0 : 1));
/** Workflow order for status and stage lists, so a pipeline reads start to finish instead of by size. Unknown labels keep their order after these. */
const FLOW = ["NEW", "OPEN", "CONTACTED", "PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION", "DRAFT", "SUBMITTED", "SENT", "PENDING", "INVESTIGATING", "IN_PROGRESS",
  "ACCEPTED", "APPROVED", "WON", "RESOLVED", "CLOSED", "COMPLETED", "REJECTED", "LOST", "CANCELLED", "EXPIRED"];
const flowIndex = (k: string) => { const i = FLOW.indexOf(k.toUpperCase().replace(/\s+/g, "_")); return i < 0 ? FLOW.length : i; };
/** Columns for a row of panels that leave no hole: 4 sit 2+2 or 4 across, 2 sit side by side, otherwise 3 across. */
const colsFor = (n: number) => (n === 1 ? "" : n === 2 ? "md:grid-cols-2" : n % 4 === 0 ? "md:grid-cols-2 xl:grid-cols-4" : "md:grid-cols-2 xl:grid-cols-3");
/** Axis labels in the Indian grouping people read: ₹1.2Cr, ₹4.5L, ₹12k. */
function compact(k: string, n: number) {
  const p = isMoneyKey(k) ? "₹" : "";
  if (n >= 1e7) return `${p}${fmtNum(n / 1e7, 1)}Cr`;
  if (n >= 1e5) return `${p}${fmtNum(n / 1e5, 1)}L`;
  if (n >= 1e3) return `${p}${fmtNum(n / 1e3, n >= 1e4 ? 0 : 1)}k`;
  return `${p}${fmtNum(n, 0)}`;
}
/** Titles come from field names: keep acronyms upper-case. */
const nice = (t: string) => t.replace(/\b(skus|sku|gst|qc|rfq|capa|coa|po|so)\b/gi, (m) => (m.toLowerCase() === "skus" ? "SKUs" : m.toUpperCase()))
  .replace(/\b(F|f)ollowups\b/g, "$1ollow-ups").replace(/\b(F|f)ollowup\b/g, "$1ollow-up");
/** An axis maximum that splits into four clean steps: 1, 2, 4, 8 or 10 times a power of ten. Small counts round up to a multiple of four so the ticks are whole numbers. */
const niceMax = (v: number, whole = false) => {
  if (v <= 0) return 4;
  if (whole && v <= 20) return Math.ceil(v / 4) * 4;
  const p = 10 ** Math.floor(Math.log10(v)); const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 4 ? 4 : f <= 8 ? 8 : 10) * p;
};

function TrendChart({ name, points }: { name: string; points: Point[] }) {
  const money = isMoneyKey(name);
  const total = points.reduce((a, p) => a + p.value, 0);
  const peak = points.reduce((a, p) => (p.value > a.value ? p : a), points[0]);
  const max = niceMax(peak.value, !money);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const weekly = /_weekly_trend$/.test(name);
  const title = nice(fieldLabel(name.replace(/_(weekly_)?trend$/, ""))) + (weekly ? ", by week" : ", last " + points.length + " days");
  return (
    <section className="panel p-5 min-w-0" aria-label={title}>
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="t-section">{title}</h3>
        <p className="t-support">Total <b className="text-fg font-semibold">{fmt(name, total)}</b> · {weekly ? "Weekly" : "Daily"} average <b className="text-fg font-semibold">{fmt(name, total / points.length)}</b> · Peak <b className="text-fg font-semibold">{fmt(name, peak.value)}</b> {weekly ? "week of" : "on"} {fmtDay(peak.label)}</p>
      </header>
      <div className="relative mt-7 h-[176px] pl-12" role="img" aria-label={`${title}: total ${fmt(name, total)}`}>
        {ticks.map((t, i) => (
          <div key={i} className="absolute left-0 right-0 flex items-center" style={{ bottom: `${(t / max) * 100}%` }} aria-hidden>
            <span className="w-11 pr-2 text-right t-support leading-none -translate-y-1/2">{compact(name, t)}</span>
            <span className="flex-1 h-px" style={{ background: i === 0 ? "var(--line-strong)" : "var(--line)" }} />
          </div>
        ))}
        <div className="absolute inset-0 left-12 flex items-end gap-[3px]">
          {points.map((p, i) => (
            <div key={p.label} className="flex-1 min-w-0 h-full flex items-end group">
              <div className="w-full rounded-t-[3px] bar-grow transition-opacity group-hover:opacity-70" title={`${fmtDay(p.label)}: ${fmt(name, p.value)}`}
                style={{ height: `${Math.max(p.value > 0 ? 1.5 : 0, (p.value / max) * 100)}%`, background: "var(--chart-actual)", animationDelay: `${Math.min(i * 22, 600)}ms` }} />
            </div>
          ))}
        </div>
      </div>
      <div className="flex justify-between pl-12 mt-1.5 t-support" aria-hidden>
        <span>{fmtDay(points[0].label)}</span>
        <span>{fmtDay(points[Math.floor(points.length / 2)].label)}</span>
        <span>{fmtDay(points[points.length - 1].label)}</span>
      </div>
      {money && <span className="sr-only">Amounts in rupees</span>}
    </section>
  );
}

const BAD = /(exception|reject|lost|fail|overdue|breakdown|expired|revoked)/i;
const WARN = /(pending|review|draft|paused|investigat|quarantine|submitted|open|unmatched|partial)/i;
function BarPanel({ name, data }: { name: string; data: Record<string, number> }) {
  const semantic = /(status|decision|stage)/i.test(name);   // colour by meaning only where the labels are states
  const entries = Object.entries(data);
  if (semantic && entries.some(([k]) => flowIndex(k) < FLOW.length)) entries.sort(([a], [b]) => flowIndex(a) - flowIndex(b));
  const max = Math.max(...entries.map(([, v]) => v), 1);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  const title = nice(fieldLabel(name));
  return (
    <section className="panel p-5 min-w-0" aria-label={title}>
      <header className="flex items-baseline justify-between gap-3">
        <h3 className="t-section">{title}</h3>
        <span className="t-support shrink-0">{fmt(name, total)} total</span>
      </header>
      <ul className="mt-3.5 space-y-3">
        {entries.map(([k, v], i) => {
          const color = semantic && BAD.test(k) ? "var(--danger)" : semantic && WARN.test(k) ? "var(--amber)" : "var(--chart-actual)";
          return (
            <li key={k}>
              <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
                <span className="truncate" title={k}>{/^[A-Z0-9_]+$/.test(k) && (semantic || k.length > 4) ? titleCase(k) : k}</span>
                <span className="tabular-nums font-semibold shrink-0">{fmt(name, v)}</span>
              </div>
              <div className="mt-1 h-1.5 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
                <div className="h-1.5 rounded-full bar-grow-x" style={{ width: `${Math.max(v > 0 ? 2 : 0, (v / max) * 100)}%`, background: color, animationDelay: `${i * 70}ms` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function ModuleDashboard({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  const scalars = Object.fromEntries(entries.filter(([, v]) => v === null || typeof v !== "object"));
  const trends = entries.filter(([k, v]) => isTrend(k, v)) as [string, Point[]][];
  const bars = entries.filter(([k, v]) => !isTrend(k, v) && isBars(v)) as [string, Record<string, number>][];
  const other = Object.fromEntries(entries.filter(([k, v]) => !isTrend(k, v) && !isBars(v) && v !== null && typeof v === "object"));
  return (
    <div className="space-y-5 mb-6">
      {Object.keys(scalars).length > 0 && <ReportView data={scalars} />}
      {trends.length > 0 && <div className={`grid gap-4 ${trends.length > 1 ? "xl:grid-cols-2" : ""}`}>{trends.map(([k, pts], i) => <InView key={k} className={trends.length % 2 === 1 && i === trends.length - 1 && trends.length > 1 ? "xl:col-span-2" : ""}><TrendChart name={k} points={pts} /></InView>)}</div>}
      {bars.length > 0 && <div className={`grid gap-4 items-start ${colsFor(bars.length)}`}>{bars.map(([k, d]) => <InView key={k}><BarPanel name={k} data={d} /></InView>)}</div>}
      {Object.keys(other).length > 0 && <ReportView data={other} />}
    </div>
  );
}
