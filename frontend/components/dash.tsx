"use client";
/** Building blocks for the operational dashboards. Every figure goes through <Figure> and every label/support line through the same three slots,
 *  so a number looks the same wherever it appears: 28/34 weight 500, tabular, never resized for its length.
 *  Components receive already-formatted text from lib/format.ts (fmtRupees, fmtNum, fmtPct); they never format or restyle numbers themselves. */
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { fmtNum } from "@/lib/format";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
const FILL: Record<Tone, string> = { success: "var(--brand)", warning: "var(--gold)", danger: "var(--danger)", info: "var(--c-slate)", neutral: "var(--chart-fill)" };   // chart fills: teal, gold, red, slate, deep teal. Text tones stay on the AA colours below.
const TEXT: Record<Tone, string> = { success: "text-success", warning: "text-warning", danger: "text-danger", info: "text-info", neutral: "text-muted" };

/** The one KPI number style. Plain numbers are grouped by the shared formatter; money and percentages arrive already formatted (fmtRupees, fmtPct). */
export function Figure({ value }: { value: string | number }) {
  const text = typeof value === "number" ? fmtNum(value) : value;
  return <span className="kpi-fit"><span className="t-kpi" style={{ "--n": String(text.length) } as React.CSSProperties} title={text}>{text}</span></span>;
}

export function Section({ title, description, action, children, className = "", flush }: {
  title: string; description?: string; action?: React.ReactNode; children: React.ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <h2 className="t-section">{title}</h2>
          {description && <p className="t-support mt-0.5">{description}</p>}
        </div>
        {action}
      </div>
      <div className={flush ? "" : "px-5 pb-5"}>{children}</div>
    </section>
  );
}

export function SectionLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="t-support hover:text-fg inline-flex items-center gap-0.5 shrink-0 pt-1">{children}<ChevronRight size={12} /></Link>;
}

/* ------------------------------------------------------------------ the KPI cell: label, value, supporting text */
export type Cell = { label: string; value: string | number; hint?: string; tone?: Tone; href?: string; loading?: boolean };

/** Three stacked slots. The cell element is a subgrid spanning three rows of its parent grid, so within a row of cards the labels share one baseline row,
 *  the numbers share the next and the notes the last, even when one label wraps to two lines. Nothing is centred and nothing is resized for its length. */
const CELL = "grid grid-rows-subgrid row-span-3 gap-y-0 content-start";
function CellBody({ label, value, hint, tone, loading, size = "lg" }: Cell & { size?: "lg" | "md" }) {
  return (
    <>
      <div className="t-label line-clamp-2" title={label}>{label}</div>
      <div className="mt-2">{loading ? <span className="skeleton block h-6 w-24 my-1" /> : <Figure value={value} />}</div>
      <div className={`t-support mt-1.5 flex items-start gap-1.5 ${tone && tone !== "neutral" ? TEXT[tone] : ""}`}>
        {!loading && hint && tone && tone !== "neutral" && <span className="w-1.5 h-1.5 rounded-full shrink-0 mt-[6px]" style={{ background: FILL[tone] }} aria-hidden />}
        {!loading && hint && <span className="min-w-0">{hint}</span>}
      </div>
    </>
  );
}

/** One panel holding several KPIs separated by hairlines. Columns never narrow below the width of the longest realistic figure (crore-scale rupees at 28px);
 *  a strip that cannot fit its cells in one row wraps to a second row instead of clipping. Two columns on phones. */
export function StatStrip({ cells, label }: { cells: Cell[]; label: string }) {
  const n = cells.length, size = n >= 5 ? "md" : "lg";
  return (
    <section aria-label={label} className="panel overflow-hidden">
      <div className={`grid grid-cols-2 ${n <= 5 ? "lg:grid-cols-[repeat(var(--n),minmax(0,1fr))]" : "sm:grid-cols-[repeat(auto-fit,minmax(224px,1fr))]"}`} style={{ ["--n" as string]: n }}>
        {cells.map((c, idx) => {
          const wide = cells.length % 2 === 1 && idx === cells.length - 1 ? "col-span-2 sm:col-span-1" : "";   // an odd last cell fills the row on phones
          const cls = `${CELL} bg-surface p-4 sm:p-5 shadow-[inset_-1px_0_0_var(--line),inset_0_-1px_0_var(--line)] ${wide} ${idx === 0 && n >= 4 ? "cell-hero" : ""}`;   // hairlines drawn per cell, so a wrapped last row has no grey fill
          return c.href
            ? <Link key={c.label} href={c.href} className={`${cls} hover:brightness-105 transition-[filter]`}><CellBody {...c} size={size} /></Link>
            : <div key={c.label} className={cls}><CellBody {...c} size={size} /></div>;
        })}
      </div>
    </section>
  );
}

/** The same KPI without the dividers, for departmental sections. Two columns so a crore-scale amount has room at 28px. */
export function Metric(c: Omit<Cell, "loading">) {
  const cls = `${CELL} pb-6`;
  return c.href
    ? <Link href={c.href} className={`${cls} hover:opacity-80 transition-opacity`}><CellBody {...c} size="md" /></Link>
    : <div className={cls}><CellBody {...c} size="md" /></div>;
}
export function MetricGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-x-8 gap-y-0 -mb-6">{children}</div>;
}

/* ------------------------------------------------------------------ distributions */
type Seg = { label: string; value: number; tone?: Tone };
function colorize(items: Seg[]) {
  const shown = items.filter((i) => i.value > 0);
  const greys = shown.filter((i) => !i.tone || i.tone === "neutral").length;
  let g = 0;
  return shown.map((i) => ({
    ...i,
    color: i.tone && i.tone !== "neutral" ? FILL[i.tone] : `color-mix(in srgb, var(--chart-fill) ${greys <= 1 ? 100 : Math.round(100 - (g++ / (greys - 1)) * 70)}%, var(--surface))`,
  }));
}

/** One labelled row: name and total on the left, one segmented bar with its legend on the right. */
export function StackRow({ label, items, href, empty }: { label: string; items: Seg[]; href?: string; empty: string }) {
  const segs = colorize(items);
  const total = segs.reduce((a, s) => a + s.value, 0);
  const head = (
    <div className="min-w-0">
      <div className="t-value">{label}</div>
      <div className="t-support">{total === 0 ? "none yet" : `${total} total`}</div>
    </div>
  );
  return (
    <div className="grid md:grid-cols-[170px_minmax(0,1fr)] gap-x-6 gap-y-2 py-3.5 first:pt-0 last:pb-0 border-t border-line first:border-t-0">
      {href ? <Link href={href} className="hover:underline underline-offset-2 decoration-line-strong">{head}</Link> : head}
      {total === 0 ? <p className="t-support self-center">{empty}</p> : (
        <div className="self-center min-w-0">
          <div className="flex h-2.5 rounded-full overflow-hidden gap-0.5" role="img" aria-label={segs.map((s) => `${s.label} ${s.value}`).join(", ")}>
            {segs.map((s) => <div key={s.label} className="first:rounded-l-full last:rounded-r-full" style={{ width: `${(s.value / total) * 100}%`, background: s.color }} title={`${s.label}: ${s.value}`} />)}
          </div>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 t-support">
            {segs.map((s) => (
              <li key={s.label} className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: s.color }} aria-hidden />{s.label}<span className="text-fg font-medium">{s.value}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Horizontal comparison bars on a shared scale. */
export function BarRows({ rows }: { rows: { label: string; value: number; display: string; tone?: Tone }[] }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 0);
  if (rows.length === 0 || max <= 0) return <p className="t-support">Nothing to compare yet.</p>;
  return (
    <ul className="space-y-3.5">
      {rows.map((r) => (
        <li key={r.label}>
          <div className="flex items-baseline justify-between gap-3 mb-1.5"><span className="t-label">{r.label}</span><span className="t-value">{r.display}</span></div>
          <div className="h-2 rounded-full" style={{ background: "var(--chart-track)" }}><div className="h-2 rounded-full" style={{ width: `${(Math.abs(r.value) / max) * 100}%`, background: FILL[r.value < 0 ? "danger" : r.tone ?? "neutral"] }} /></div>
        </li>
      ))}
    </ul>
  );
}

/** Stock against its reorder level. The bar is available / reorder level, capped at full. Values arrive formatted. */
export function StockWatch({ rows }: { rows: { code: string; name: string; available: string; reorder: string; ratio: number }[] }) {
  return (
    <ul className="divide-y divide-line border-t border-line">
      {rows.map((r) => (
        <li key={r.code} className="px-5 py-2.5 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 items-center">
          <div className="min-w-0"><span className="t-value block truncate">{r.name}</span><span className="font-mono t-support">{r.code}</span></div>
          <div className="text-right t-value"><span>{r.available}</span><span className="text-muted font-normal"> of {r.reorder}</span></div>
          <div className="col-span-2 h-1.5 rounded-full" style={{ background: "var(--chart-track)" }}><div className="h-1.5 rounded-full" style={{ width: `${Math.max(0, Math.min(1, r.ratio)) * 100}%`, background: r.ratio <= 0 ? FILL.danger : FILL.warning }} /></div>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ workflow, queue, activity */
export type Stage = { label: string; count: string | number; unit: string; href?: string; flags: { label: string; n: number; tone: Tone }[] };

/** Workflow stages in order, joined by connectors. A stage's top rule takes the colour of its most severe open exception. */
export function Flow({ stages }: { stages: Stage[] }) {
  return (
    <ol className="grid sm:grid-cols-2 xl:grid-cols-6 gap-y-5 gap-x-0">
      {stages.map((s, i) => {
        const flagged = s.flags.filter((f) => f.n > 0);
        const sev: Tone = flagged.some((f) => f.tone === "danger") ? "danger" : flagged.length ? "warning" : "neutral";
        const inner = (
          <>
            <div className="h-0.5 rounded-full mb-3" style={{ background: sev === "neutral" ? "var(--line-strong)" : FILL[sev] }} aria-hidden />
            <div className="t-label line-clamp-1" title={s.label}>{s.label}</div>
            <div className="mt-2 min-h-[30px] sm:min-h-[34px]"><Figure value={s.count} /></div>
            <div className="t-support mt-1.5 min-h-[18px]">{s.unit}</div>
            <div className="mt-2 flex flex-wrap gap-1.5 min-h-[22px]">
              {flagged.length === 0 ? <span className="t-support">Clear</span>
                : flagged.map((f) => (
                  <span key={f.label} className={`t-support px-1.5 rounded ${TEXT[f.tone]}`} style={{ background: `color-mix(in srgb, ${FILL[f.tone]} 11%, transparent)` }}>{f.n} {f.label}</span>
                ))}
            </div>
          </>
        );
        return (
          <li key={s.label} className="relative px-4 first:pl-0 last:pr-0 xl:[&:not(:last-child)]:border-r border-line">
            {s.href ? <Link href={s.href} className="block hover:opacity-80 transition-opacity" aria-label={`${i + 1}. ${s.label}`}>{inner}</Link> : inner}
            {i < stages.length - 1 && <ChevronRight size={14} className="hidden xl:block absolute -right-[7px] top-[46px] text-muted bg-surface rounded-full" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}

export type QueueItem = { key: string; label: string; detail?: string; count?: number | null; tone: Tone; href?: string };
/** Prioritised work: callers pass items already ordered by severity. */
export function WorkQueue({ items, empty }: { items: QueueItem[]; empty: React.ReactNode }) {
  if (items.length === 0) return <div className="px-5 pb-5 t-label">{empty}</div>;
  return (
    <ul className="divide-y divide-line border-t border-line">
      {items.map((q) => {
        const inner = (
          <>
            <span className="w-[3px] self-stretch rounded-full shrink-0" style={{ background: FILL[q.tone] }} aria-hidden />
            <span className="min-w-0 flex-1"><span className="t-value font-normal block truncate">{q.label}</span>{q.detail && <span className="t-support block truncate">{q.detail}</span>}</span>
            {q.count != null && <span className="t-value">{fmtNum(q.count)}</span>}
            {q.href && <ChevronRight size={14} className="text-muted shrink-0" />}
          </>
        );
        return <li key={q.key}>{q.href ? <Link href={q.href} className="flex items-center gap-3 px-5 py-3 hover:bg-surface-2 transition-colors">{inner}</Link> : <div className="flex items-center gap-3 px-5 py-3">{inner}</div>}</li>;
      })}
    </ul>
  );
}

export type TimelineEntry = { id: string | number; action: string; record: string; user: string; ago: string; at: string; href?: string };
export function Timeline({ items }: { items: TimelineEntry[] }) {
  return (
    <ol className="px-5 pb-2">
      {items.map((e, i) => {
        const body = (
          <div className="flex items-baseline gap-3">
            <div className="min-w-0 flex-1">
              <div className="t-value font-normal truncate"><span className="font-medium">{e.action}</span> <span className="text-muted">{e.record}</span></div>
              <div className="t-support truncate">{e.user}</div>
            </div>
            <time className="t-support shrink-0" title={e.at}>{e.ago}</time>
          </div>
        );
        return (
          <li key={e.id} className={`py-2.5 ${i > 0 ? "border-t border-line" : ""}`}>
            {e.href ? <Link href={e.href} className="block hover:bg-surface-2 -mx-2 px-2 rounded transition-colors">{body}</Link> : body}
          </li>
        );
      })}
    </ol>
  );
}

export function InlineNotice({ tone = "neutral", children }: { tone?: Tone; children: React.ReactNode }) {
  return <div role={tone === "danger" ? "alert" : "status"} className={`panel px-4 py-3 text-[0.8125rem] leading-5 ${tone === "danger" ? "text-danger" : "text-muted"}`}>{children}</div>;
}

export function SkeletonRows({ n = 4 }: { n?: number }) {
  return <div className="px-5 pb-5 space-y-3" aria-busy="true">{Array.from({ length: n }).map((_, i) => <div key={i} className="skeleton h-4" />)}</div>;
}

