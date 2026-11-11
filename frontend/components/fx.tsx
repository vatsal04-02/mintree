"use client";
/** Data graphics for the dashboards: count-up figures, sparklines, a smooth interactive area chart, a ring chart and ranked bars.
 *  Everything animates once, on arrival (charts below the fold wait until they are scrolled into view), and prefers-reduced-motion shows the final state at once.
 *  The components draw; they never fetch. Figures are formatted by lib/format.ts. */
import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { fmtDate, fmtDay, fmtDelta, fmtNum, fmtPct, fmtRupees } from "@/lib/format";

/* ------------------------------------------------------------------ helpers */
const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const r1 = (n: number) => Math.round(n * 10) / 10;

/** Waits until it is on screen, then lets the animations inside it start (see .fx-wait in globals.css). */
export function InView({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") { el.dataset.seen = "1"; return; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { el.dataset.seen = "1"; io.disconnect(); } }, { threshold: 0.15 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return <div ref={ref} className={`fx-wait ${className}`}>{children}</div>;
}

/* ------------------------------------------------------------------ count-up */
/** Counts the first number inside an already-formatted string up from zero (₹35,79,067, 97%, 1,418). Anything else is shown as it is. */
export function CountUp({ text, ms = 900 }: { text: string; ms?: number }) {
  const m = /(-?\d[\d,]*(?:\.\d+)?)/.exec(text);
  const target = m ? parseFloat(m[1].replace(/,/g, "")) : NaN;
  const decimals = m && m[1].includes(".") ? m[1].split(".")[1].length : 0;
  const [v, setV] = useState<number>(0);
  const from = useRef(0);
  useEffect(() => {
    if (Number.isNaN(target)) return;
    if (reducedMotion()) { setV(target); from.current = target; return; }
    const start = performance.now(); const a = from.current; let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / ms); const e = 1 - Math.pow(1 - k, 3);
      setV(a + (target - a) * e);
      if (k < 1) raf = requestAnimationFrame(tick); else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  if (!m) return <>{text}</>;
  return <>{text.slice(0, m.index)}{fmtNum(Number.isNaN(target) ? 0 : v, decimals)}{text.slice(m.index + m[1].length)}</>;
}

/* ------------------------------------------------------------------ smooth line through points (monotone cubic: never overshoots the data) */
function monotone(p: [number, number][]): string {
  const n = p.length;
  if (n < 2) return "";
  const dx: number[] = [], m: number[] = [], t: number[] = new Array(n).fill(0);
  for (let i = 0; i < n - 1; i++) { dx[i] = p[i + 1][0] - p[i][0]; m[i] = (p[i + 1][1] - p[i][1]) / dx[i]; }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (m[i - 1] * m[i] <= 0) t[i] = 0;
    else { const w1 = 2 * dx[i] + dx[i - 1], w2 = dx[i] + 2 * dx[i - 1]; t[i] = (w1 + w2) / (w1 / m[i - 1] + w2 / m[i]); }
  }
  let d = `M${r1(p[0][0])},${r1(p[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i];
    d += ` C${r1(p[i][0] + h / 3)},${r1(p[i][1] + (t[i] * h) / 3)} ${r1(p[i + 1][0] - h / 3)},${r1(p[i + 1][1] - (t[i + 1] * h) / 3)} ${r1(p[i + 1][0])},${r1(p[i + 1][1])}`;
  }
  return d;
}
/** An axis maximum that fits the data closely and splits into four whole steps (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8 or 10 times a power of ten). */
const niceMax = (v: number) => {
  if (v <= 0) return 4;
  const raw = v / 4; const p = 10 ** Math.floor(Math.log10(raw)); const f = raw / p;
  const step = ([1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((x) => x >= f - 1e-9) ?? 10) * p;
  return step * 4;
};
function compactRupees(n: number) {
  if (n >= 1e7) return `₹${fmtNum(n / 1e7, 1)}Cr`;
  if (n >= 1e5) return `₹${fmtNum(n / 1e5, 1)}L`;
  if (n >= 1e3) return `₹${fmtNum(n / 1e3, n >= 1e4 ? 0 : 1)}k`;
  return `₹${fmtNum(n, 0)}`;
}

/* ------------------------------------------------------------------ sparkline */
/** A line under a figure: where it has been. Draws in, and the last value gets a small pulsing dot. */
export function Sparkline({ points, height = 34, label }: { points: number[]; height?: number; label: string }) {
  const id = useId().replace(/:/g, "");
  if (points.length < 2 || points.every((v) => v === 0)) return <div style={{ height }} aria-hidden />;
  const max = Math.max(...points, 1);
  const xy: [number, number][] = points.map((v, i) => [(i / (points.length - 1)) * 100, 92 - (v / max) * 84]);
  const line = monotone(xy);
  const last = xy[xy.length - 1];
  return (
    <div className="relative" style={{ height }} role="img" aria-label={label}>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 w-full h-full overflow-visible" aria-hidden>
        <defs><linearGradient id={`sg${id}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--chart-actual)" stopOpacity="0.22" /><stop offset="1" stopColor="var(--chart-actual)" stopOpacity="0" /></linearGradient></defs>
        <path d={`${line} L100,100 L0,100 Z`} fill={`url(#sg${id})`} className="fx-fade" />
        <path d={line} fill="none" stroke="var(--chart-actual)" strokeWidth="1.75" strokeLinecap="round" vectorEffect="non-scaling-stroke" pathLength={1} className="fx-draw" />
      </svg>
      <span className="absolute w-2 h-2 -ml-1 -mt-1 rounded-full fx-fade" style={{ left: `${last[0]}%`, top: `${last[1]}%`, background: "var(--chart-actual)", boxShadow: "0 0 0 3px var(--card)" }}>
        <span className="absolute inset-0 rounded-full fx-pulse" style={{ background: "var(--chart-actual)" }} />
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ segmented bar */
const SEG_COLOUR: Record<string, string> = { RELEASED: "var(--chart-actual)", QUARANTINE: "var(--amber)", REJECTED: "var(--danger)", ON_HOLD: "var(--amber)" };
export function StackBar({ data, label }: { data: Record<string, number>; label: string }) {
  const entries = Object.entries(data).filter(([, v]) => v > 0);
  const total = entries.reduce((a, [, v]) => a + v, 0) || 1;
  return (
    <div role="img" aria-label={label}>
      <div className="flex h-2 gap-[3px] overflow-hidden rounded-full">
        {entries.map(([k, v], i) => <span key={k} title={`${k.toLowerCase()}: ${fmtNum(v)}`} className="bar-grow-x h-full rounded-full" style={{ width: `${(v / total) * 100}%`, background: SEG_COLOUR[k] ?? "var(--chart-fill)", animationDelay: `${i * 90}ms` }} />)}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ area chart */
export type Point = { label: string; value: number };
/** The hero chart: a smooth line over a soft fade, the previous period as a dashed line, and a crosshair with the exact figures on hover. */
export function AreaChart({ points, compare, weekly = false, height = 252, name = "Revenue" }: { points: Point[]; compare?: Point[]; weekly?: boolean; height?: number; name?: string }) {
  const id = useId().replace(/:/g, "");
  const box = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const n = points.length;
  if (n < 2) return <p className="t-support px-5 pb-5">Not enough days in this period to draw a chart.</p>;
  const prev = compare && compare.length ? compare : null;
  const max = niceMax(Math.max(...points.map((p) => p.value), ...(prev ? prev.map((p) => p.value) : [0]), 1));
  const W = 1000, H = 300;
  const X = (i: number, len = n) => (len === 1 ? W / 2 : (i / (len - 1)) * W);
  const Y = (v: number) => H - 6 - (v / max) * (H - 24);
  const cur: [number, number][] = points.map((p, i) => [X(i), Y(p.value)]);
  const prv: [number, number][] | null = prev ? prev.slice(0, n).map((p, i) => [X(i, Math.min(n, prev.length)), Y(p.value)]) : null;
  const line = monotone(cur);
  const total = points.reduce((a, p) => a + p.value, 0);
  const peak = points.reduce((a, p) => (p.value > a.value ? p : a), points[0]);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const pick = (clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    setHover(Math.max(0, Math.min(n - 1, Math.round(((clientX - r.left) / r.width) * (n - 1)))));
  };
  const h = hover;
  const hp = h === null ? null : points[h];
  const hc = h === null || !prev ? null : prev[Math.min(h, prev.length - 1)];
  const delta = hp && hc ? fmtDelta(hp.value, hc.value) : null;
  return (
    <div className="px-5 pb-5">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 mb-3">
        <p className="t-support">Total <b className="text-fg font-semibold">{fmtRupees(total)}</b> · Peak <b className="text-fg font-semibold">{fmtRupees(peak.value)}</b> {weekly ? "week of" : "on"} {fmtDay(peak.label)}</p>
        <p className="t-support flex items-center gap-4" aria-hidden>
          <span className="inline-flex items-center gap-1.5"><span className="w-4 h-[3px] rounded-full" style={{ background: "var(--chart-actual)" }} />This period</span>
          {prev && <span className="inline-flex items-center gap-1.5"><span className="w-4 border-t-2 border-dashed" style={{ borderColor: "var(--subtle)" }} />Previous period</span>}
        </p>
      </div>
      <div className="relative pl-12 mt-4" style={{ height }}>
        {ticks.map((t, i) => (
          <div key={i} className="absolute left-0 right-0 flex items-center" style={{ bottom: `${(t / max) * 94 + 2}%` }} aria-hidden>
            <span className="w-11 pr-2 text-right t-support leading-none -translate-y-1/2">{compactRupees(t)}</span>
            <span className="flex-1 h-px" style={{ background: i === 0 ? "var(--line-strong)" : "var(--line)" }} />
          </div>
        ))}
        <div ref={box} className="absolute inset-y-0 right-0 left-12 touch-pan-y" onPointerMove={(e) => pick(e.clientX)} onPointerDown={(e) => pick(e.clientX)} onPointerLeave={() => setHover(null)}
          role="img" aria-label={`${name}: total ${fmtRupees(total)} over ${n} ${weekly ? "weeks" : "days"}, peak ${fmtRupees(peak.value)} on ${fmtDate(peak.label)}`}>
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 w-full h-full overflow-visible" aria-hidden>
            <defs>
              <linearGradient id={`ag${id}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--chart-actual)" stopOpacity="0.30" /><stop offset="0.7" stopColor="var(--chart-actual)" stopOpacity="0.05" /><stop offset="1" stopColor="var(--chart-actual)" stopOpacity="0" /></linearGradient>
            </defs>
            {prv && <path d={monotone(prv)} fill="none" stroke="var(--subtle)" strokeWidth="1.75" strokeDasharray="5 5" strokeLinecap="round" vectorEffect="non-scaling-stroke" className="fx-fade" style={{ animationDelay: "350ms" }} />}
            <path d={`${line} L${W},${H} L0,${H} Z`} fill={`url(#ag${id})`} className="fx-fade" style={{ animationDelay: "450ms" }} />
            <path d={line} fill="none" stroke="var(--chart-actual)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" pathLength={1} className="fx-draw" style={{ animationDuration: "1.3s" }} />
          </svg>
          {h !== null && hp && (
            <>
              <span className="absolute top-0 bottom-0 w-px pointer-events-none" style={{ left: `${(h / (n - 1)) * 100}%`, background: "var(--line-strong)" }} aria-hidden />
              {hc && <span className="absolute w-2.5 h-2.5 -ml-[5px] -mt-[5px] rounded-full pointer-events-none" style={{ left: `${(h / (n - 1)) * 100}%`, top: `${(Y(hc.value) / H) * 100}%`, background: "var(--card)", border: "2px solid var(--subtle)" }} aria-hidden />}
              <span className="absolute w-3 h-3 -ml-1.5 -mt-1.5 rounded-full pointer-events-none" style={{ left: `${(h / (n - 1)) * 100}%`, top: `${(Y(hp.value) / H) * 100}%`, background: "var(--chart-actual)", border: "2.5px solid var(--card)", boxShadow: "0 2px 8px rgb(0 0 0 / 0.25)" }} aria-hidden />
              <div className="absolute z-10 pointer-events-none panel px-3 py-2 min-w-[150px] anim-fade" style={{ top: 6, ...(h > n * 0.6 ? { right: `${100 - (h / (n - 1)) * 100 + 2}%` } : { left: `${(h / (n - 1)) * 100 + 2}%` }) } as CSSProperties}>
                <p className="t-support">{weekly ? "Week of " : ""}{fmtDate(hp.label)}</p>
                <p className="t-value !font-semibold text-[0.9375rem]">{fmtRupees(hp.value)}</p>
                {hc && <p className="t-support mt-0.5">Previous {fmtRupees(hc.value)}{delta && <b className={`ml-1.5 font-semibold ${delta.startsWith("+") ? "text-success" : "text-danger"}`}>{delta}</b>}</p>}
              </div>
            </>
          )}
        </div>
      </div>
      <div className="flex justify-between pl-12 mt-2 t-support" aria-hidden>
        <span>{fmtDay(points[0].label)}</span><span>{fmtDay(points[Math.floor(n / 2)].label)}</span><span>{fmtDay(points[n - 1].label)}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ ring chart */
const RING: Record<string, string> = { PAID: "var(--chart-actual)", OPEN: "var(--amber)", PARTIAL: "var(--butter-deep)", "Raw materials (RM)": "var(--brand-bright)", "Packaging (PM)": "#94a3b8" };
const RING_FALLBACK = ["var(--c-slate)", "var(--chart-fill)", "var(--danger)", "var(--subtle)"];
export function Ring({ data, centre, money = true }: { data: Record<string, number>; centre: string; money?: boolean }) {
  const entries = Object.entries(data).filter(([, v]) => v > 0);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  const [hot, setHot] = useState<string | null>(null);
  const R = 42, C = 2 * Math.PI * R;
  if (!total) return <p className="t-support px-5 pb-5">Nothing to show yet.</p>;
  let acc = 0;
  const colour = (k: string, i: number) => RING[k] ?? RING_FALLBACK[i % RING_FALLBACK.length];
  const fmt = (v: number) => (money ? fmtRupees(v) : fmtNum(v));
  const shown = hot ? entries.find(([k]) => k === hot) : null;
  const word = (k: string) => (/[a-z]/.test(k) ? k : k.charAt(0) + k.slice(1).toLowerCase());
  return (
    <div className="px-5 pb-5 flex flex-col items-center gap-5 sm:flex-row sm:items-center xl:flex-col xl:items-stretch 2xl:flex-row">
      <div className="relative w-[176px] h-[176px] shrink-0 mx-auto" role="img" aria-label={`${centre}: ${entries.map(([k, v]) => `${k.toLowerCase()} ${fmt(v)}`).join(", ")}`}>
        <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90" aria-hidden>
          <circle cx="50" cy="50" r={R} fill="none" stroke="var(--chart-track)" strokeWidth="11" />
          {entries.map(([k, v], i) => {
            const len = Math.max(0, (v / total) * C - 2.2); const off = acc; acc += (v / total) * C;
            return <circle key={k} cx="50" cy="50" r={R} fill="none" stroke={colour(k, i)} strokeWidth={hot === k ? 13 : 11} strokeDashoffset={-off} className="fx-sweep cursor-pointer"
              style={{ "--len": r1(len), "--gap": r1(C - len), "--c": r1(C), animationDelay: `${i * 140}ms`, opacity: hot && hot !== k ? 0.35 : 1, transition: "opacity 160ms, stroke-width 160ms" } as CSSProperties}
              onPointerEnter={() => setHot(k)} onPointerLeave={() => setHot(null)} />;
          })}
        </svg>
        <div className="absolute inset-0 grid place-content-center text-center pointer-events-none">
          <p className="t-support">{shown ? word(shown[0]) : centre}</p>
          <p className="t-kpi !text-[1.0625rem] !leading-6"><CountUp text={shown ? fmt(shown[1]) : fmt(total)} ms={700} /></p>
          {shown && <p className="t-support">{fmtPct((shown[1] / total) * 100)}</p>}
        </div>
      </div>
      <ul className="flex-1 min-w-0 w-full space-y-2.5">
        {entries.map(([k, v], i) => (
          <li key={k} className="flex items-center gap-2.5 text-[0.8125rem] rounded-md -mx-2 px-2 py-1 transition-colors" style={{ background: hot === k ? "var(--inset)" : undefined }} onPointerEnter={() => setHot(k)} onPointerLeave={() => setHot(null)}>
            <span className="w-2.5 h-2.5 rounded-[3px] shrink-0" style={{ background: colour(k, i) }} aria-hidden />
            <span className="flex-1 truncate">{word(k)}</span>
            <span className="tabular-nums font-semibold">{fmt(v)}</span>
            <span className="t-support w-10 text-right tabular-nums">{fmtPct((v / total) * 100)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ ranked bars */
export function RankedBars({ data, money = true, empty = "Nothing to rank yet." }: { data: Record<string, number>; money?: boolean; empty?: string }) {
  const entries = Object.entries(data);
  if (!entries.length) return <p className="t-support px-5 pb-5">{empty}</p>;
  const max = Math.max(...entries.map(([, v]) => v), 1);
  return (
    <ul className="px-5 pb-5 space-y-3.5">
      {entries.map(([k, v], i) => (
        <li key={k}>
          <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]"><span className="truncate" title={k}>{k}</span><span className="tabular-nums font-semibold shrink-0">{money ? fmtRupees(v) : fmtNum(v)}</span></div>
          <div className="mt-1.5 h-1.5 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
            <div className="h-1.5 rounded-full bar-grow-x" style={{ width: `${Math.max(2, (v / max) * 100)}%`, background: "var(--chart-actual)", opacity: 1 - i * 0.09, animationDelay: `${i * 70}ms` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}
