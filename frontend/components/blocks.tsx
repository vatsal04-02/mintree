"use client";
/** Dashboard primitives. They take already-formatted text (lib/format.ts) and raw series values; they never format numbers or fetch data themselves, so a figure looks
 *  the same wherever it appears and every module dashboard can reuse them. Surfaces are flat: a hairline border, no shadow. Structure comes from spacing and alignment. */
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { fmtDay, fmtNum } from "@/lib/format";
import { CountUp, Sparkline } from "./fx";

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
      <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-3">
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
export type Metric = { label: string; value: string; note?: string; noteTone?: Tone; href?: string; loading?: boolean; hero?: boolean; icon?: LucideIcon; delta?: { text: string; tone: Tone }; spark?: { points: number[]; label: string; caption?: string }; extra?: React.ReactNode; unit?: string; accent?: string };

/** Headline figures as separate cards: a coloured top edge, an uppercase label with its icon in a soft square, a large figure (with its unit), tinted chips for the
 *  comparison and the note, and a small graphic underneath (sparkline or a segmented bar). The whole card is the link. */
export function MetricStrip({ items, label }: { items: Metric[]; label: string }) {
  return (
    <section aria-label={label}>
      <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 [&>li]:min-w-0">
        {items.map((m) => {
          const tone = m.noteTone && m.noteTone !== "neutral" ? m.noteTone : null;
          const Icon = m.icon;
          const body = (
            <div className={`panel kpi-card h-full p-4 pt-[18px] flex flex-col ${m.href ? "panel-link" : ""}`} style={{ "--kpi-accent": m.accent ?? "var(--brand)" } as React.CSSProperties}>
              <div className="flex items-start justify-between gap-3">
                <span className="label-caps line-clamp-2" title={m.label}>{m.label}</span>
                {Icon && <span className="icon-sq icon-sq-sm" aria-hidden><Icon size={15} strokeWidth={2} /></span>}
              </div>
              <div className="mt-1.5 min-w-0">
                {/* the unit sits inside the figure: the auto-fit box needs the full card width, which a flex row would take away */}
                {m.loading ? <Skeleton className="h-8 w-32" /> : <span className="kpi-fit"><span className="t-kpi" title={m.value} style={{ "--n": String(m.value.length + (m.unit ? m.unit.length * 0.45 : 0)) } as React.CSSProperties}><CountUp text={m.value} />{m.unit && <span className="t-label !font-semibold ml-1.5 tracking-normal">{m.unit}</span>}</span></span>}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 min-h-[1.5rem]">
                {m.loading ? <Skeleton className="h-5 w-28" /> : (
                  <>
                    {m.delta && <span className={`badge tone-${m.delta.tone}`}>{m.delta.text}</span>}
                    {m.note && (tone ? <span className={`badge tone-${tone} max-w-full`} title={m.note}><span className="truncate">{m.note}</span></span> : <span className="t-support truncate" title={m.note}>{m.note}</span>)}
                  </>
                )}
              </div>
              {!m.loading && (m.spark || m.extra) && <div className="mt-auto pt-3">{m.spark ? <><Sparkline points={m.spark.points} label={m.spark.label} />{m.spark.caption && <p className="t-support mt-1.5">{m.spark.caption}</p>}</> : m.extra}</div>}
            </div>
          );
          return (
            <li key={m.label}>
              {m.href ? <Link href={m.href} className="block h-full rounded-[14px]" aria-label={`${m.label}: ${m.loading ? "loading" : m.value}${m.delta ? `, ${m.delta.text}` : ""}${m.note ? `, ${m.note}` : ""}`}>{body}</Link> : body}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ chart */
export type Bucket = { start: string; planned: number | null; actual: number | null; orders: number; from?: string; to?: string; days?: number; partial?: boolean };
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
                {b.planned !== null && <rect x={cx - bw - 1.5} y={y(b.planned)} width={bw} height={Math.max(0, M.t + ih - y(b.planned))} rx={4} fill="var(--line-strong)" />}
                {b.actual !== null && <rect x={cx + 1.5} y={y(b.actual)} width={bw} height={Math.max(0, M.t + ih - y(b.actual))} rx={4} fill="var(--chart-actual)" />}
                {i % every === 0 && <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">{fmtDay(b.start)}</text>}
              </g>
            );
          })}
          <line x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} stroke="var(--line-strong)" strokeWidth={1} />
        </svg>
      )}
      {cur && cur.orders > 0 && (
        <div role="tooltip" className="pointer-events-none absolute z-10 rounded-xl px-3 py-2 text-[0.75rem] leading-[1.125rem] shadow-lg" 
          style={{ background: "var(--ink)", color: "var(--fg-on-ink)", left: Math.min(Math.max(M.l + band * (hot as number) + band / 2 - 100, 4), Math.max(W - 208, 4)), top: 8, width: 200 }}>
          <div className="font-medium">Week of {fmtDay(cur.start)}</div>
          {cur.partial && cur.from && cur.to && <div className="opacity-70">Partial week: {fmtDay(cur.from)} to {fmtDay(cur.to)} ({cur.days} {cur.days === 1 ? "day" : "days"})</div>}
          <div className="flex justify-between gap-3"><span className="opacity-70">{labels[0]}</span><span className="tnum">{fmtNum(cur.planned)} {unit}</span></div>
          <div className="flex justify-between gap-3"><span className="opacity-70">{labels[1]}</span><span className="tnum">{fmtNum(cur.actual)} {unit}</span></div>
          <div className="flex justify-between gap-3"><span className="opacity-70">Orders completed</span><span className="tnum">{fmtNum(cur.orders)}</span></div>
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
export type Exception = { key: string; label: string; detail?: string; value?: string; severity: "high" | "medium" | "low"; href?: string; ratio?: number };

/** Needs attention: a plain ruled list in three groups (Act now, Review, Monitor), most severe first. The five most important entries show at first; "View all" reveals the rest.
 *  Each row is a direct link; the count is a figure, not a badge. A `ratio` (available / reorder level) draws stock on a scale where the tick at the middle is the reorder point. */
const GROUPS: { name: string; severity: Exception["severity"]; dot: string }[] = [
  { name: "Act now", severity: "high", dot: "var(--danger)" }, { name: "Review", severity: "medium", dot: "var(--amber)" }, { name: "Monitor", severity: "low", dot: "var(--subtle)" },
];
export function ExceptionList({ items, empty, initial = 5 }: { items: Exception[]; empty: React.ReactNode; initial?: number }) {
  const [all, setAll] = useState(false);
  if (items.length === 0) return <div className="px-5 pb-6 t-label">{empty}</div>;
  const rank = { high: 0, medium: 1, low: 2 } as const;
  const sorted = [...items].sort((a, b) => rank[a.severity] - rank[b.severity]);
  const shown = all ? sorted : sorted.slice(0, initial);
  return (
    <div className="px-5 pb-4">
      {GROUPS.map((g) => {
        const rows = shown.filter((i) => i.severity === g.severity);
        if (!rows.length) return null;
        return (
          <div key={g.name} className="mb-2">
            <h3 className={`eyebrow pt-1 pb-1 ${g.severity === "high" ? "!text-danger font-semibold" : ""}`}>{g.name}</h3>
            <ul>
              {rows.map((r) => {
                const inner = (
                  <>
                    <span className="w-2 h-2 rounded-full shrink-0 mt-[7px] self-start" style={{ background: g.dot }} aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[0.8125rem] leading-5 font-medium">{r.label}</span>
                      {r.detail && <span className="block truncate t-support">{r.detail}</span>}
                      {r.ratio !== undefined && (
                        <span className="relative block mt-1.5 h-1 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
                          <span className="block h-1 rounded-full" style={{ width: `${Math.max(0, Math.min(r.ratio, 2)) * 50}%`, background: g.dot }} />
                          <span className="absolute -top-[3px] left-1/2 w-px h-2.5" style={{ background: "var(--fg)", opacity: 0.45 }} />
                        </span>
                      )}
                    </span>
                    {r.value && <span className={`shrink-0 tabular-nums text-[0.9375rem] font-semibold ${g.severity === "high" ? "text-danger" : ""}`}>{r.value}</span>}
                    {r.href && <ChevronRight size={14} className="text-faint shrink-0" aria-hidden />}
                  </>
                );
                const cls = "flex items-center gap-3 py-2.5 border-t border-line";
                return <li key={r.key}>{r.href ? <Link href={r.href} className={`${cls} -mx-2 px-2 rounded-md transition-colors hover:bg-[var(--inset)]`}>{inner}</Link> : <div className={cls}>{inner}</div>}</li>;
              })}
            </ul>
          </div>
        );
      })}
      {sorted.length > initial && (
        <button type="button" className="mt-1 text-[0.8125rem] font-medium text-brand hover:underline underline-offset-2 min-h-8 pointer-coarse:min-h-11" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `View all ${sorted.length}`}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ pipeline */
export type PipelineStage = { label: string; value: string; note: string; flag?: string; href?: string; active?: boolean; alert?: "warn" | "danger" };

/** Procurement to dispatch as numbered stage boxes in one panel. Each box has a coloured top edge (green when work is waiting, amber or red when it needs action),
 *  the stage number, its name, how many items wait there and a one-line status. The whole box is the link. */
export function Pipeline({ stages }: { stages: PipelineStage[] }) {
  /* A compact rail: one panel, one cell per stage divided by hairlines. The count carries the cell; a late or short flag is the only coloured text. */
  return (
    <section className="panel overflow-hidden" aria-label="Order to dispatch workflow">
      <ol className={`grid grid-cols-2 md:grid-cols-3 ${stages.length === 5 ? "xl:grid-cols-5" : stages.length === 4 ? "xl:grid-cols-4" : "xl:grid-cols-6"} gap-px bg-[var(--line)]`}>
        {stages.map((s, i) => {
          const flagColor = s.alert === "warn" ? "text-warning" : "text-danger";
          const body = (
            <div className={`h-full bg-[var(--card)] px-4 py-3 flex flex-col transition-colors ${s.href ? "hover:bg-[#fafbfa]" : ""}`}>
              <span className="text-[0.75rem] font-medium text-muted">{String(i + 1).padStart(2, "0")} · {s.label}</span>
              <span className="t-stage tabular-nums mt-1" style={{ color: s.value === "0" ? "var(--faint)" : "var(--fg)" }}>{s.value}</span>
              <span className="t-support">{s.note}</span>
              {s.flag && <span className={`text-[0.75rem] font-medium mt-0.5 ${flagColor}`}>{s.flag}</span>}
            </div>
          );
          return (
            <li key={s.label} className="min-w-0 bg-[var(--card)]">
              {s.href ? <Link href={s.href} className="block h-full" aria-label={`${s.label}: ${s.value} ${s.note}${s.flag ? `, ${s.flag}` : ""}`}>{body}</Link> : body}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/* ------------------------------------------------------------------ transactions */
export type TxRow = { key: string; reference: string; activity: string; related: string; date: string; status: React.ReactNode; href?: string };
export function TransactionTable({ rows, empty }: { rows: TxRow[]; empty: React.ReactNode }) {
  if (rows.length === 0) return <div className="py-6 t-label">{empty}</div>;
  return (
    <div className="overflow-x-auto -mx-1 px-1">
      <table className="tx-table w-full text-[0.8125rem] leading-5 border-separate border-spacing-0 sm:min-w-[480px]">
        <thead><tr className="text-left">
          {["Reference", "Related record", "Date", "Status"].map((h) => <th key={h} className={`label-caps py-2 pr-4 whitespace-nowrap last:pr-0 ${h === "Date" || h === "Related record" ? "max-sm:hidden" : ""}`}>{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const cell = "h-[52px] pr-4 border-b border-line group-last:border-b-0 whitespace-nowrap max-w-[200px] truncate";
            return (
              <tr key={r.key} className="group row-link relative">
                <td className={`${cell} !max-w-none max-sm:!max-w-[210px]`}>
                  {r.href ? <Link href={r.href} className="t-ref block hover:text-brand hover:underline underline-offset-2">{r.reference}</Link> : <span className="t-ref block">{r.reference}</span>}
                  <span className="block t-support truncate">{r.activity}<span className="sm:hidden"> · {r.related}</span></span>
                </td>
                <td className={`${cell} text-muted max-sm:hidden`}>{r.related}</td>
                <td className={`${cell} text-muted tnum max-sm:hidden`}>{r.date}</td>
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


