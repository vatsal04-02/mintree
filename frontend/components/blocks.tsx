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
  return <Link href={href} className="inline-flex items-center gap-1 h-8 pl-3 pr-2.5 rounded-full border border-line text-[12.5px] font-medium text-brand hover:text-[var(--brand-hover)] hover:border-line-strong transition-colors shrink-0">{children}<ChevronRight size={13} aria-hidden /></Link>;
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

/** Four separate KPI cards. The whole card is the link. `hero` inverts one of them (ink card, cream figure). A delta chip appears only when a real previous-period figure exists. */
export function MetricStrip({ items, label }: { items: Metric[]; label: string }) {
  return (
    <section aria-label={label}>
      <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 [&>li]:min-w-0">
        {items.map((m) => {
          const hero = !!m.hero;
          const Icon = m.icon;
          const chipStyle = hero ? { background: "rgb(255 255 255 / 0.10)", color: "rgb(255 255 255 / 0.9)" } : undefined;
          const body = (
            <div className={`${hero ? "panel-ink" : "panel"} ${m.href ? "panel-link" : ""} h-full ${hero ? "p-6" : "p-5"} flex flex-col`}>
              <div className="flex items-start justify-between gap-3">
                <span className={`t-label line-clamp-2 ${hero ? "!text-[var(--fg-on-ink)] opacity-75" : ""}`} title={m.label}>{m.label}</span>
                {Icon && <span style={hero ? undefined : { background: "var(--brand-tint)" }} className={`grid place-items-center w-8 h-8 rounded-full shrink-0 -mt-1 -mr-1 ${hero ? "border border-white/25" : "text-[var(--brand)]"}`} aria-hidden><Icon size={15} strokeWidth={1.75} /></span>}
              </div>
              <div className="mt-3 mb-3">
                {m.loading ? <Skeleton className="h-8 w-32" /> : <span className="t-kpi" style={hero ? { color: "#ffffff" } : undefined}>{m.value}</span>}
              </div>
              <div className="mt-auto flex flex-wrap items-center gap-2">
                {m.loading ? <Skeleton className="h-5 w-36" /> : (
                  <>
                    {m.delta && <span className={`badge tone-${m.delta.tone}`} style={hero ? { background: "rgb(95 208 192 / 0.18)", color: "var(--brand-on-ink)" } : m.delta.tone === "success" ? { background: "var(--brand-tint)", color: "var(--brand)" } : undefined}>{m.delta.text}</span>}
                    <span className={`badge ${m.noteTone ? `tone-${m.noteTone}` : "tone-neutral"} max-w-full`} style={chipStyle} title={m.note}><span className="truncate">{m.note ?? "\u00a0"}</span></span>
                  </>
                )}
              </div>
            </div>
          );
          return (
            <li key={m.label} className="rise">
              {m.href ? <Link href={m.href} className="block h-full rounded-2xl" aria-label={`${m.label}: ${m.loading ? "loading" : m.value}${m.delta ? `, ${m.delta.text}` : ""}${m.note ? `, ${m.note}` : ""}`}>{body}</Link> : body}
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
        <div role="tooltip" className="pointer-events-none absolute z-10 rounded-xl px-3 py-2 text-[12px] leading-[18px] shadow-lg" 
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

/** Needs attention: severity-ordered inset rows (48px, 64px with a second line), each a direct link. Callers pass only real, non-zero exceptions.
 *  A `ratio` (available / reorder level) draws stock on a scale where the tick at the middle is the reorder point; at zero the bar is empty. */
export function ExceptionList({ items, empty }: { items: Exception[]; empty: React.ReactNode }) {
  if (items.length === 0) return <div className="px-5 pb-6 t-label">{empty}</div>;
  const groups = [{ name: "Act now", rows: items.filter((i) => i.severity === "high") }, { name: "Review", rows: items.filter((i) => i.severity === "medium") }].filter((g) => g.rows.length);
  return (
    <div className="px-4 pb-4">
      {groups.map((g) => (
        <div key={g.name} className="mt-1">
          <h3 className="eyebrow px-1 pt-3 pb-2">{g.name}</h3>
          <ul className="flex flex-col gap-2">
            {g.rows.map((r) => {
              const dot = r.severity === "high" ? "var(--danger)" : "var(--amber)";
              const two = !!r.detail || r.ratio !== undefined;
              const inner = (
                <>
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: dot }} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] leading-5 font-medium">{r.label}</span>
                    {r.detail && <span className="block truncate t-support">{r.detail}</span>}
                    {r.ratio !== undefined && (
                      <span className="relative block mt-1.5 h-1.5 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
                        <span className="block h-1.5 rounded-full" style={{ width: `${Math.max(0, Math.min(r.ratio, 2)) * 50}%`, background: dot }} />
                        <span className="absolute -top-[3px] left-1/2 w-px h-3" style={{ background: "var(--fg)", opacity: 0.45 }} />
                      </span>
                    )}
                  </span>
                  {r.value && <span className={`shrink-0 rounded-full bg-surface border border-line px-2.5 h-6 inline-flex items-center text-[12px] font-semibold ${r.severity === "high" ? "text-danger" : ""}`}>{r.value}</span>}
                  {r.href && <ChevronRight size={14} className="text-faint shrink-0" aria-hidden />}
                </>
              );
              const cls = `inset flex items-center gap-3 px-3.5 ${two ? "min-h-16 py-2" : "min-h-12 py-2"}`;
              return <li key={r.key}>{r.href ? <Link href={r.href} className={`${cls} transition-colors hover:border-[var(--line-strong)] hover:bg-surface`}>{inner}</Link> : <div className={cls}>{inner}</div>}</li>;
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ pipeline */
export type PipelineStage = { label: string; value: string; note: string; flag?: string; href?: string; active?: boolean; alert?: "warn" | "danger" };

/** Procurement to dispatch as six white cards on a route line. Idle: flat, quiet number. Has work: shadow and a brand bar. Needs action: amber or red bar and dot. */
export function Pipeline({ stages }: { stages: PipelineStage[] }) {
  return (
    <ol className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 xl:gap-5">
      {stages.map((s, i) => {
        const state = s.alert === "danger" ? "stage-danger" : s.alert === "warn" ? "stage-warn" : s.active ? "stage-work" : "";
        const idle = !state;
        const body = (
          <div className={`stage ${state} h-full p-4 flex flex-col gap-1`}>
            <div className="flex items-center gap-2 t-label">
              <span className="grid place-items-center w-5 h-5 rounded-full text-[11px] font-medium border" style={idle ? { borderColor: "var(--line-strong)", background: "var(--inset)" } : { borderColor: "transparent", background: "var(--brand-tint)", color: "var(--brand-hover)" }}>{i + 1}</span>
              <span className="truncate">{s.label}</span>
              {s.alert && <span className="w-2 h-2 rounded-full ml-auto shrink-0" style={{ background: s.alert === "danger" ? "var(--danger)" : "var(--amber)" }} aria-hidden />}
            </div>
            <div className="t-stage mt-1.5" style={{ color: idle ? "var(--subtle)" : "var(--fg)" }}>{s.value}</div>
            <div className="t-support">{s.note}</div>
            <div className={`t-support min-h-[18px] ${s.flag ? "!text-danger" : ""}`}>{s.flag ?? " "}</div>
          </div>
        );
        return (
          <li key={s.label} className="relative rise">
            {s.href ? <Link href={s.href} className="block h-full rounded-2xl" aria-label={`${s.label}: ${s.value} ${s.note}`}>{body}</Link> : body}
            {i < stages.length - 1 && (
              <span aria-hidden className="hidden xl:block absolute top-[25px] -right-5 w-5 h-[1.5px] z-10" style={{ background: "var(--line-strong)" }}>
                <span className="absolute left-1/2 -top-[2.25px] -ml-[3px] w-1.5 h-1.5 rounded-full" style={{ background: "var(--line-strong)" }} />
              </span>
            )}
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
      <table className="w-full text-[13px] leading-5 border-separate border-spacing-0 min-w-[560px]">
        <thead><tr className="text-left">
          {["Reference", "Activity", "Related record", "Date", "Status"].map((h) => <th key={h} className="eyebrow py-2 pr-4 border-b border-line whitespace-nowrap last:pr-0">{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const cell = "h-12 pr-4 border-b border-line group-last:border-b-0 whitespace-nowrap max-w-[220px] truncate";
            return (
              <tr key={r.key} className="group row-link relative">
                <td className={`${cell} t-ref`}>{r.href ? <Link href={r.href} className="hover:underline underline-offset-2">{r.reference}</Link> : r.reference}</td>
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
              <div className="truncate text-[13px] leading-5"><span className="font-medium">{e.title}</span> <span className="text-muted">{e.record}</span></div>
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


