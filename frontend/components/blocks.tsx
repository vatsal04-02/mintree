"use client";
/** Dashboard primitives. They take already-formatted text (lib/format.ts) and raw series values; they never format numbers or fetch data themselves, so a figure looks
 *  the same wherever it appears and every module dashboard can reuse them. Surfaces are flat: a hairline border, no shadow. Structure comes from spacing and alignment. */
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { fmtDay, fmtNum } from "@/lib/format";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
const TONE_TEXT: Record<Tone, string> = { success: "text-success", warning: "text-warning", danger: "text-danger", info: "text-info", neutral: "text-muted" };

/* ------------------------------------------------------------------ structure */
/** A titled card (default) or, with `bare`, a heading with no box above content that brings its own cards. */
export function Block({ title, meta, action, children, className = "", bare }: { title: string; meta?: string; action?: React.ReactNode; children: React.ReactNode; className?: string; bare?: boolean }) {
  const head = (
    <div className="flex items-center justify-between gap-3">
      <h2 className="t-section">{title}{meta && <span className="t-support font-normal ml-2">{meta}</span>}</h2>
      {action}
    </div>
  );
  return (
    <section aria-label={title} className={`${bare ? "" : "panel p-5 rise"} ${className}`}>
      {head}
      <div className={bare ? "mt-3" : "mt-4"}>{children}</div>
    </section>
  );
}

/** The card for content that deserves a frame (the chart and the exception list). Header, body, optional footer. */
export function Panel({ title, meta, action, children, className = "", bodyClass = "" }: { title: string; meta?: string; action?: React.ReactNode; children: React.ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section aria-label={title} className={`panel rise flex flex-col min-w-0 ${className}`}>
      <div className="flex items-start justify-between gap-3 px-5 py-3.5 mb-3 border-b border-line">
        <div className="min-w-0"><h2 className="t-section">{title}</h2>{meta && <p className="t-support mt-0.5">{meta}</p>}</div>
        {action}
      </div>
      <div className={`flex-1 min-h-0 ${bodyClass}`}>{children}</div>
    </section>
  );
}

/** Ghost pill link to the source module. */
export function LinkMore({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="inline-flex items-center gap-1 min-h-8 pointer-coarse:min-h-11 pl-3 pr-2.5 rounded-full border border-line text-[0.75rem] font-medium text-brand hover:text-[var(--brand-hover)] hover:border-line-strong transition-colors shrink-0">{children}<ChevronRight size={13} aria-hidden /></Link>;
}

/** Designed empty state: a ghost chart (dashed baseline, faint bars), a title, the reason, and a way to the source module. */
export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="px-5 py-8 flex flex-col items-center text-center">
      <div className="flex items-end gap-2.5 h-[72px] mb-5 px-3 border-b border-dashed border-line-strong" aria-hidden>
        {[30, 48, 34, 62, 42, 24].map((h, i) => <span key={i} className="w-5 rounded-t-md" style={{ height: h, background: "var(--chart-track)" }} />)}
      </div>
      <p className="t-value">{title}</p>
      {hint && <p className="t-support mt-1 max-w-sm">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export const Skeleton = ({ className = "" }: { className?: string }) => <span className={`skeleton block ${className}`} aria-hidden />;

/* ------------------------------------------------------------------ metric cards */
export type Metric = { label: string; value: string; note?: string; noteTone?: Tone; href?: string; loading?: boolean; hero?: boolean; icon?: LucideIcon; delta?: { text: string; tone: Tone } };

/** One connected row of headline figures: hairline dividers, no icons, no pills. The whole cell is the link. A delta appears only when a real previous-period figure exists. */
export function MetricStrip({ items, label }: { items: Metric[]; label: string }) {
  return (
    <section aria-label={label} className="rise">
      <ul className="grid grid-cols-2 xl:grid-cols-4 gap-4 [&>li]:min-w-0">
        {items.map((m) => {
          const tone = m.noteTone && m.noteTone !== "neutral" ? m.noteTone : null;
          const body = (
            <div className={`panel h-full px-5 py-4 flex flex-col ${m.href ? "panel-link" : ""}`}>
              <span className="t-label line-clamp-1" title={m.label}>{m.label}</span>
              <div className="mt-2">
                {m.loading ? <Skeleton className="h-8 w-32" /> : <span className="kpi-fit" style={{ "--kpi-max": "1.5rem" } as React.CSSProperties}><span className="t-kpi" title={m.value} style={{ "--n": String(m.value.length) } as React.CSSProperties}>{m.value}</span></span>}
              </div>
              <div className="mt-1.5 t-support flex flex-wrap items-center gap-x-2.5 gap-y-0.5 min-h-[1.2rem]">
                {m.loading ? <Skeleton className="h-4 w-28" /> : (
                  <>
                    {m.delta && <span className={`font-semibold ${TONE_TEXT[m.delta.tone]}`}>{m.delta.text}</span>}
                    <span className={`inline-flex items-center gap-1.5 min-w-0 ${tone ? TONE_TEXT[tone] : ""}`} title={m.note}>
                      {tone && <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" aria-hidden />}
                      <span className="truncate">{m.note ?? "\u00a0"}</span>
                    </span>
                  </>
                )}
              </div>
            </div>
          );
          return (
            <li key={m.label}>
              {m.href ? <Link href={m.href} className="block h-full" aria-label={`${m.label}: ${m.loading ? "loading" : m.value}${m.delta ? `, ${m.delta.text}` : ""}${m.note ? `, ${m.note}` : ""}`}>{body}</Link> : body}
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
                {b.planned !== null && <rect x={cx - bw - 1.5} y={y(b.planned)} width={bw} height={Math.max(0, M.t + ih - y(b.planned))} rx={5} fill="var(--butter)" fillOpacity={0.45} stroke="var(--chart-plan)" strokeWidth={1.25} strokeDasharray="4 3" />}
                {b.actual !== null && <rect x={cx + 1.5} y={y(b.actual)} width={bw} height={Math.max(0, M.t + ih - y(b.actual))} rx={6} fill="var(--chart-actual)" />}
                {i % every === 0 && <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">{fmtDay(b.start)}</text>}
              </g>
            );
          })}
          <line x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} stroke="var(--line-strong)" strokeWidth={1} />
        </svg>
      )}
      {cur && cur.orders > 0 && (
        <div role="tooltip" className="pointer-events-none absolute z-10 rounded-xl px-3 py-2 text-[0.75rem] leading-[1.125rem] shadow-lg" 
          style={{ background: "var(--ink)", color: "var(--fg-on-ink)", left: Math.min(Math.max(M.l + band * (hot as number) + band / 2 - 80, 4), Math.max(W - 168, 4)), top: 8, width: 160 }}>
          <div className="font-medium">Week of {fmtDay(cur.start)}</div>
          <div className="flex justify-between gap-3"><span className="opacity-70">{labels[0]}</span><span className="tnum">{fmtNum(cur.planned)}</span></div>
          <div className="flex justify-between gap-3"><span className="opacity-70">{labels[1]}</span><span className="tnum">{fmtNum(cur.actual)}</span></div>
          <div className="flex justify-between gap-3"><span className="opacity-70">Orders</span><span className="tnum">{fmtNum(cur.orders)}</span></div>
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
export type Exception = { key: string; label: string; detail?: string; value?: string; severity: "high" | "medium"; href?: string; ratio?: number };

/** Needs attention: a plain ruled list, most severe first. Each row is a direct link; the count is a figure, not a badge.
 *  A `ratio` (available / reorder level) draws stock on a scale where the tick at the middle is the reorder point; at zero the bar is empty. */
export function ExceptionList({ items, empty }: { items: Exception[]; empty: React.ReactNode }) {
  if (items.length === 0) return <div className="px-5 pb-6 t-label">{empty}</div>;
  const groups = [{ name: "Act now", rows: items.filter((i) => i.severity === "high") }, { name: "Review", rows: items.filter((i) => i.severity === "medium") }].filter((g) => g.rows.length);
  return (
    <div className="px-4 pb-4 space-y-3">
      {groups.map((g) => (
        <div key={g.name} className="px-3">
          <h3 className={`eyebrow pt-1 pb-1 ${g.name === "Act now" ? "!text-danger font-semibold" : ""}`}>{g.name}</h3>
          <ul>
            {g.rows.map((r) => {
              const dot = r.severity === "high" ? "var(--danger)" : "var(--amber)";
              const inner = (
                <>
                  <span className="w-2 h-2 rounded-full shrink-0 mt-[7px] self-start" style={{ background: dot }} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[0.8125rem] leading-5 font-medium">{r.label}</span>
                    {r.detail && <span className="block truncate t-support">{r.detail}</span>}
                    {r.ratio !== undefined && (
                      <span className="relative block mt-1.5 h-1 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
                        <span className="block h-1 rounded-full" style={{ width: `${Math.max(0, Math.min(r.ratio, 2)) * 50}%`, background: dot }} />
                        <span className="absolute -top-[3px] left-1/2 w-px h-2.5" style={{ background: "var(--fg)", opacity: 0.45 }} />
                      </span>
                    )}
                  </span>
                  {r.value && <span className={`shrink-0 tabular-nums text-[0.9375rem] font-semibold ${r.severity === "high" ? "text-danger" : ""}`}>{r.value}</span>}
                  {r.href && <ChevronRight size={14} className="text-faint shrink-0" aria-hidden />}
                </>
              );
              const cls = "flex items-center gap-3 py-2.5 border-t border-line";
              return <li key={r.key}>{r.href ? <Link href={r.href} className={`${cls} -mx-2 px-2 rounded-md transition-colors hover:bg-[var(--inset)]`}>{inner}</Link> : <div className={cls}>{inner}</div>}</li>;
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ pipeline */
export type PipelineStage = { label: string; value: string; note: string; flag?: string; href?: string; active?: boolean; alert?: "warn" | "danger" };

/** Procurement to dispatch as one joined strip, left to right. Idle: a quiet figure. Has work: dark figure and a brand underline. Needs action: amber or red underline and dot. */
export function Pipeline({ stages }: { stages: PipelineStage[] }) {
  return (
    <ol className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 rise">
      {stages.map((s) => {
        const color = s.alert === "danger" ? "var(--danger)" : s.alert === "warn" ? "var(--amber)" : s.active ? "var(--brand)" : null;
        const body = (
          <div className={`panel h-full px-4 py-3.5 flex flex-col gap-0.5 overflow-hidden ${s.href ? "panel-link" : ""}`} style={color ? { boxShadow: `inset 0 -2px 0 ${color}, var(--shadow-md)` } : undefined}>
            <div className="flex items-center gap-2 t-label">
              <span className="truncate">{s.label}</span>
              {s.alert && <span className="w-2 h-2 rounded-full ml-auto shrink-0" style={{ background: color ?? undefined }} aria-hidden />}
            </div>
            <div className="t-stage mt-1 truncate" title={s.value} style={{ color: color ? "var(--fg)" : "var(--subtle)" }}>{s.value}</div>
            <div className="t-support">{s.note}</div>
            <div className={`t-support min-h-[18px] ${s.flag ? "!text-danger" : ""}`}>{s.flag ?? " "}</div>
          </div>
        );
        return (
          <li key={s.label} className="min-w-0">
            {s.href ? <Link href={s.href} className="block h-full" aria-label={`${s.label}: ${s.value} ${s.note}`}>{body}</Link> : body}
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
    <div className="overflow-x-auto -mx-1 px-1">
      <table className="w-full text-[0.8125rem] leading-5 border-separate border-spacing-0 min-w-[560px]">
        <thead><tr className="text-left">
          {["Reference", "Activity", "Related record", "Date", "Status"].map((h) => <th key={h} className="eyebrow py-2 pr-4 border-b border-line whitespace-nowrap last:pr-0">{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const cell = "h-12 pr-4 border-b border-line group-last:border-b-0 whitespace-nowrap max-w-[220px] truncate";
            return (
              <tr key={r.key} className="group row-link relative">
                <td className={`${cell} t-ref`}>{r.href ? <Link href={r.href} className="inline-block py-1.5 pointer-coarse:py-3 hover:underline underline-offset-2">{r.reference}</Link> : r.reference}</td>
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
/** Vertical timeline: a dot per entry on a hairline rail. */
export function ActivityTimeline({ items }: { items: ActivityItem[] }) {
  return (
    <ol className="ml-1 border-l border-line">
      {items.map((e) => {
        const body = (
          <div className="flex items-baseline gap-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-[0.8125rem] leading-5"><span className="font-medium">{e.title}</span> <span className="text-muted">{e.record}</span></div>
              <div className="t-support truncate">{e.who}</div>
            </div>
            <time className="t-support shrink-0 tnum" title={e.at}>{e.ago}</time>
          </div>
        );
        return (
          <li key={e.id} className="relative pl-4">
            <span className="absolute -left-[4.5px] top-[17px] w-2 h-2 rounded-full ring-2 ring-[var(--card)]" style={{ background: "var(--line-strong)" }} aria-hidden />
            {e.href ? <Link href={e.href} className="block row-link -mx-2 px-2 rounded-xl">{body}</Link> : body}
          </li>
        );
      })}
    </ol>
  );
}

/** Segmented pill tabs for short, mutually exclusive choices such as the reporting period. The active tab is ink. */
export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>)}
    </div>
  );
}


