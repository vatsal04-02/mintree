"use client";
/** Dashboard primitives. They take already-formatted text (lib/format.ts) and raw series values; they never format numbers or fetch data themselves, so a figure looks
 *  the same wherever it appears and every module dashboard can reuse them. Surfaces are flat: a hairline border, no shadow. Structure comes from spacing and alignment. */
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { fmtDay, fmtNum } from "@/lib/format";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
const TONE_TEXT: Record<Tone, string> = { success: "text-success", warning: "text-warning", danger: "text-danger", info: "text-info", neutral: "text-muted" };

/* ------------------------------------------------------------------ structure */
/** A heading with a hairline under it and no box: the default way to start a section. */
export function Block({ title, meta, action, children, className = "" }: { title: string; meta?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section aria-label={title} className={className}>
      <div className="flex items-baseline justify-between gap-3 pb-2.5 border-b border-line">
        <h2 className="t-section">{title}{meta && <span className="t-support font-normal ml-2">{meta}</span>}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** The one bordered surface, reserved for the content that deserves a frame (the chart and the exception list). */
export function Panel({ title, meta, action, children, className = "", bodyClass = "" }: { title: string; meta?: string; action?: React.ReactNode; children: React.ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section aria-label={title} className={`panel flex flex-col min-w-0 ${className}`}>
      <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-3">
        <div className="min-w-0"><h2 className="t-section">{title}</h2>{meta && <p className="t-support mt-0.5">{meta}</p>}</div>
        {action}
      </div>
      <div className={`flex-1 min-h-0 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function LinkMore({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="t-support hover:text-fg transition-colors inline-flex items-center gap-0.5 shrink-0">{children}<ChevronRight size={12} aria-hidden /></Link>;
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="px-5 py-10 text-center">
      <p className="t-value">{title}</p>
      {hint && <p className="t-support mt-1 max-w-sm mx-auto">{hint}</p>}
      {action && <div className="mt-3 t-support">{action}</div>}
    </div>
  );
}

export const Skeleton = ({ className = "" }: { className?: string }) => <span className={`skeleton block ${className}`} aria-hidden />;

/* ------------------------------------------------------------------ metric strip */
export type Metric = { label: string; value: string; note?: string; noteTone?: Tone; href?: string; loading?: boolean };

/** One surface, up to four indicators, hairline separators. Every cell has the same three rows (label, value, note) so labels, figures and notes share baselines. */
export function MetricStrip({ items, label }: { items: Metric[]; label: string }) {
  return (
    <section aria-label={label} className="panel">
      <ul className="grid grid-cols-2 xl:grid-cols-4 [&>li]:min-w-0">
        {items.map((m, i) => {
          const body = (
            <div className="px-4 sm:px-5 py-4 grid grid-rows-[auto_30px_auto] gap-y-1.5 h-full">
              <span className="t-label line-clamp-2 min-h-[36px] sm:min-h-[18px] sm:line-clamp-1" title={m.label}>{m.label}</span>
              {m.loading ? <Skeleton className="h-6 w-28 self-center" /> : <span className="t-kpi">{m.value}</span>}
              {m.loading ? <Skeleton className="h-3 w-36" /> : <span className={`t-support line-clamp-2 min-h-[36px] sm:min-h-[18px] sm:line-clamp-1 ${m.noteTone ? TONE_TEXT[m.noteTone] : ""}`} title={m.note}>{m.note ?? "\u00a0"}</span>}
            </div>
          );
          const sep = `${i % 2 === 0 ? "border-r" : ""} ${i < 2 ? "border-b xl:border-b-0" : ""} ${i < items.length - 1 ? "xl:border-r" : "xl:border-r-0"} border-line`;
          return (
            <li key={m.label} className={sep}>
              {m.href ? <Link href={m.href} className="block h-full row-link focus-visible:outline-offset-[-2px]">{body}</Link> : body}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ chart */
export type Bucket = { start: string; planned: number | null; actual: number | null; orders: number };
const niceMax = (v: number) => { if (v <= 0) return 10; const p = 10 ** Math.floor(Math.log10(v)); const f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; };
function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

/** Grouped bars, planned beside actual per period. Real values only: a period with no data draws nothing. Hover or focus a period for its figures; a text table is provided for assistive tech. */
export function GroupedBars({ buckets, labels, unit, minHeight = 240 }: { buckets: Bucket[]; labels: [string, string]; unit: string; minHeight?: number }) {
  const [ref, size] = useSize<HTMLDivElement>();
  const width = size.w, height = Math.max(size.h, minHeight);
  const [hot, setHot] = useState<number | null>(null);
  const id = useId();
  const M = { l: 44, r: 8, t: 10, b: 26 };
  const W = Math.max(width, 280), iw = W - M.l - M.r, ih = height - M.t - M.b;
  const max = niceMax(Math.max(0, ...buckets.flatMap((b) => [b.planned ?? 0, b.actual ?? 0])));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const band = iw / Math.max(buckets.length, 1), bw = Math.min(22, band * 0.3);
  const y = (v: number) => M.t + ih - (v / max) * ih;
  const every = Math.ceil(buckets.length / Math.max(1, Math.floor(iw / 64)));
  const cur = hot === null ? null : buckets[hot];
  return (
    <div className="relative flex-1" style={{ minHeight }}>
     <div ref={ref} className="absolute inset-0">
      {width > 0 && (
        <svg width={W} height={height} role="img" aria-label={`${labels[0]} against ${labels[1]} ${unit} by week`} onMouseLeave={() => setHot(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" strokeWidth={1} />
              <text x={M.l - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--muted)" style={{ fontVariantNumeric: "tabular-nums" }}>{fmtNum(t)}</text>
            </g>
          ))}
          {buckets.map((b, i) => {
            const cx = M.l + band * i + band / 2;
            return (
              <g key={b.start} onMouseEnter={() => setHot(i)} onFocus={() => setHot(i)} onBlur={() => setHot(null)} tabIndex={b.orders ? 0 : -1} aria-label={b.orders ? `Week of ${fmtDay(b.start)}: planned ${b.planned}, actual ${b.actual}` : undefined}>
                <rect x={cx - band / 2} y={M.t} width={band} height={ih} fill={hot === i ? "var(--surface-2)" : "transparent"} style={{ transition: "fill 140ms" }} />
                {b.planned !== null && <rect x={cx - bw - 1.5} y={y(b.planned)} width={bw} height={Math.max(0, M.t + ih - y(b.planned))} rx={2} fill="var(--line-strong)" />}
                {b.actual !== null && <rect x={cx + 1.5} y={y(b.actual)} width={bw} height={Math.max(0, M.t + ih - y(b.actual))} rx={2} fill="var(--accent)" />}
                {i % every === 0 && <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">{fmtDay(b.start)}</text>}
              </g>
            );
          })}
          <line x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} stroke="var(--line-strong)" strokeWidth={1} />
        </svg>
      )}
      {cur && cur.orders > 0 && (
        <div role="tooltip" className="pointer-events-none absolute z-10 panel px-3 py-2 text-[12px] leading-[18px] shadow-[0_4px_16px_rgb(0_0_0/0.08)]"
          style={{ left: Math.min(Math.max(M.l + band * (hot as number) + band / 2 - 80, 4), Math.max(W - 168, 4)), top: 8, width: 160 }}>
          <div className="font-medium">Week of {fmtDay(cur.start)}</div>
          <div className="flex justify-between gap-3 text-muted"><span>{labels[0]}</span><span className="text-fg tnum">{fmtNum(cur.planned)}</span></div>
          <div className="flex justify-between gap-3 text-muted"><span>{labels[1]}</span><span className="text-fg tnum">{fmtNum(cur.actual)}</span></div>
          <div className="flex justify-between gap-3 text-muted"><span>Orders</span><span className="text-fg tnum">{fmtNum(cur.orders)}</span></div>
        </div>
      )}
      <table className="sr-only" id={id}><caption>{labels[0]} and {labels[1]} {unit} by week</caption>
        <thead><tr><th>Week of</th><th>{labels[0]}</th><th>{labels[1]}</th><th>Orders</th></tr></thead>
        <tbody>{buckets.filter((b) => b.orders).map((b) => <tr key={b.start}><td>{fmtDay(b.start)}</td><td>{fmtNum(b.planned)}</td><td>{fmtNum(b.actual)}</td><td>{fmtNum(b.orders)}</td></tr>)}</tbody></table>
     </div>
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return <ul className="flex items-center gap-4 t-support" aria-label="Legend">{items.map((i) => <li key={i.label} className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-[2px]" style={{ background: i.color }} aria-hidden />{i.label}</li>)}</ul>;
}

/* ------------------------------------------------------------------ exceptions */
export type Exception = { key: string; label: string; detail?: string; value?: string; severity: "high" | "medium"; href?: string };

/** Needs attention: compact rows ordered by severity, each a direct link. Callers pass only real, non-zero exceptions. */
export function ExceptionList({ items, empty }: { items: Exception[]; empty: React.ReactNode }) {
  if (items.length === 0) return <div className="px-5 pb-6 t-label">{empty}</div>;
  const groups = [{ name: "Act now", rows: items.filter((i) => i.severity === "high") }, { name: "Review", rows: items.filter((i) => i.severity === "medium") }].filter((g) => g.rows.length);
  return (
    <div className="pb-2">
      {groups.map((g) => (
        <div key={g.name}>
          <h3 className="px-5 pt-3 pb-1 text-[11.5px] font-medium text-muted tracking-[0.02em]">{g.name}</h3>
          <ul>
            {g.rows.map((r) => {
              const inner = (
                <>
                  <span className="min-w-0 flex-1"><span className="block truncate text-[13px] leading-5">{r.label}</span>{r.detail && <span className="block truncate t-support">{r.detail}</span>}</span>
                  {r.value && <span className={`t-value shrink-0 ${r.severity === "high" ? "text-danger" : ""}`}>{r.value}</span>}
                  {r.href && <ChevronRight size={13} className="text-faint shrink-0" aria-hidden />}
                </>
              );
              const cls = "flex items-center gap-3 px-5 py-2 border-t border-line first:border-t-0";
              return <li key={r.key}>{r.href ? <Link href={r.href} className={`${cls} row-link`}>{inner}</Link> : <div className={cls}>{inner}</div>}</li>;
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ pipeline */
export type PipelineStage = { label: string; value: string; note: string; flag?: string; href?: string };

/** Procurement to dispatch as one horizontal sequence. Stages are separated by hairlines; a stage with an open exception says so in words. Scrolls sideways when narrow. */
export function Pipeline({ stages }: { stages: PipelineStage[] }) {
  return (
    <ol className="flex overflow-x-auto [scrollbar-width:thin] snap-x xl:overflow-visible">
      {stages.map((s, i) => {
        const body = (
          <div className={`py-3 pr-4 h-full flex flex-col gap-0.5 ${i === 0 ? "pl-0" : "pl-4"}`}>
            <div className="flex items-center gap-1.5 t-label"><span className="tnum text-faint">{i + 1}</span><span className="truncate">{s.label}</span></div>
            <div className="text-[18px] leading-6 font-medium tnum">{s.value}</div>
            <div className="t-support">{s.note}</div>
            <div className={`t-support min-h-[18px] ${s.flag ? "text-danger" : ""}`}>{s.flag ?? " "}</div>
          </div>
        );
        return (
          <li key={s.label} className={`snap-start shrink-0 w-[172px] xl:w-auto xl:flex-1 xl:min-w-0 ${i > 0 ? "border-l border-line" : ""}`}>
            {s.href ? <Link href={s.href} className="block h-full row-link" aria-label={`${s.label}: ${s.value} ${s.note}`}>{body}</Link> : body}
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ transactions */
export type TxRow = { key: string; reference: string; activity: string; related: string; date: string; status: React.ReactNode; href?: string };
export function TransactionTable({ rows, empty }: { rows: TxRow[]; empty: React.ReactNode }) {
  if (rows.length === 0) return <div className="py-6 t-label">{empty}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px] leading-5 border-separate border-spacing-0 min-w-[560px]">
        <thead><tr className="text-left text-[11.5px] font-medium text-muted">
          {["Reference", "Activity", "Related record", "Date", "Status"].map((h) => <th key={h} className="py-2 pr-4 font-medium border-b border-line whitespace-nowrap last:pr-0">{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const cell = "py-2.5 pr-4 border-b border-line group-last:border-b-0 whitespace-nowrap max-w-[220px] truncate";
            return (
              <tr key={r.key} className="group row-link relative">
                <td className={`${cell} font-mono text-[12.5px]`}>{r.href ? <Link href={r.href} className="hover:underline underline-offset-2">{r.reference}</Link> : r.reference}</td>
                <td className={cell}>{r.activity}</td>
                <td className={`${cell} text-muted`}>{r.related}</td>
                <td className={`${cell} text-muted tnum`}>{r.date}</td>
                <td className={`${cell} pr-0`}>{r.status}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ activity */
export type ActivityItem = { id: string | number; title: string; record: string; who: string; ago: string; at: string; href?: string };
export function ActivityTimeline({ items }: { items: ActivityItem[] }) {
  return (
    <ol>
      {items.map((e) => {
        const body = (
          <div className="flex items-baseline gap-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] leading-5"><span className="font-medium">{e.title}</span> <span className="text-muted">{e.record}</span></div>
              <div className="t-support truncate">{e.who}</div>
            </div>
            <time className="t-support shrink-0 tnum" title={e.at}>{e.ago}</time>
          </div>
        );
        return <li key={e.id} className="border-t border-line first:border-t-0">{e.href ? <Link href={e.href} className="block row-link -mx-2 px-2 rounded-md">{body}</Link> : body}</li>;
      })}
    </ol>
  );
}

/** Segmented control for short, mutually exclusive choices such as the reporting period. */
export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>)}
    </div>
  );
}
