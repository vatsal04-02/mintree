"use client";
/** Building blocks for the operational dashboards. Each renders only what it is given: no placeholder numbers, no decorative fill.
 *  Callers pass values calculated from API responses. */
import Link from "next/link";
import { ChevronRight } from "lucide-react";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
const FILL: Record<Tone, string> = { success: "var(--success)", warning: "var(--warning)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--chart-fill)" };
const TEXT: Record<Tone, string> = { success: "text-success", warning: "text-warning", danger: "text-danger", info: "text-info", neutral: "text-muted" };

/** A headline figure. Currency strings are split so the rupee sign and paise recede; the amount itself is not altered. */
export function Figure({ value, className = "text-[28px]" }: { value: React.ReactNode; className?: string }) {
  if (typeof value === "string") {
    const m = /^(₹)?\s?([\d,]+)(\.\d+)?$/.exec(value);
    if (m && m[1]) return <span className={`fig ${className}`}><span className="cur">₹</span>{m[2]}{m[3] && <span className="dec">{m[3]}</span>}</span>;
  }
  return <span className={`fig ${className}`}>{value}</span>;
}

export function Section({ title, description, action, children, className = "", flush }: {
  title: string; description?: string; action?: React.ReactNode; children: React.ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <div className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <h2 className="text-[14px] font-semibold leading-5 tracking-[-0.005em]">{title}</h2>
          {description && <p className="text-muted text-[12px] mt-0.5">{description}</p>}
        </div>
        {action}
      </div>
      <div className={flush ? "" : "px-5 pb-5"}>{children}</div>
    </section>
  );
}

export function SectionLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="text-[12px] text-muted hover:text-fg inline-flex items-center gap-0.5 shrink-0">{children}<ChevronRight size={12} /></Link>;
}

/* ------------------------------------------------------------------ figure strips */
export type Cell = { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: Tone; href?: string; loading?: boolean };
const COLS: Record<number, string> = { 2: "md:grid-cols-2", 3: "md:grid-cols-3", 4: "md:grid-cols-4", 5: "md:grid-cols-5", 6: "md:grid-cols-6" };

/** One panel holding several figures separated by hairlines: reads as a ledger line, not as a row of unrelated tiles. */
export function StatStrip({ cells, size = "text-[22px] sm:text-[28px]", label }: { cells: Cell[]; size?: string; label: string }) {
  return (
    <section aria-label={label} className="panel overflow-hidden">
      <div className={`grid grid-cols-2 ${COLS[cells.length] ?? "md:grid-cols-4"} gap-px`} style={{ background: "var(--line)" }}>
        {cells.map((c, idx) => {
          const wide = cells.length % 2 === 1 && idx === cells.length - 1 ? "col-span-2 md:col-span-1" : "";   // an odd last cell fills the row on phones instead of leaving a gap
          const inner = (
            <>
              <div className="text-[12px] font-medium text-muted leading-4">{c.label}</div>
              <div className="mt-2.5 min-h-[28px] flex items-end">{c.loading ? <span className="skeleton inline-block w-16 h-7" /> : <Figure value={c.value} className={size} />}</div>
              {c.hint && !c.loading && (
                <div className={`mt-2 text-[12px] leading-4 tnum flex items-center gap-1.5 ${c.tone && c.tone !== "neutral" ? TEXT[c.tone] : "text-muted"}`}>
                  {c.tone && c.tone !== "neutral" && <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: FILL[c.tone] }} aria-hidden />}
                  <span>{c.hint}</span>
                </div>
              )}
            </>
          );
          return c.href
            ? <Link key={c.label} href={c.href} className={`bg-surface p-4 sm:p-5 block hover:bg-surface-2 transition-colors ${wide}`}>{inner}</Link>
            : <div key={c.label} className={`bg-surface p-4 sm:p-5 ${wide}`}>{inner}</div>;
        })}
      </div>
    </section>
  );
}

/** Label / figure / note without a box, for dense departmental sections. */
export function Metric({ label, value, hint, tone, href }: Omit<Cell, "loading">) {
  const body = (
    <>
      <div className="text-[12px] text-muted leading-4">{label}</div>
      <div className="mt-1"><Figure value={value} className="text-[22px]" /></div>
      {hint && <div className={`mt-1 text-[11.5px] leading-4 tnum ${tone && tone !== "neutral" ? TEXT[tone] : "text-muted"}`}>{hint}</div>}
    </>
  );
  return href ? <Link href={href} className="block rounded-md -m-2 p-2 hover:bg-surface-2 transition-colors">{body}</Link> : <div>{body}</div>;
}
export function MetricGrid({ children, cols = 3 }: { children: React.ReactNode; cols?: 2 | 3 | 4 }) {
  return <div className={`grid grid-cols-2 ${cols === 4 ? "md:grid-cols-4" : cols === 2 ? "" : "md:grid-cols-3"} gap-x-6 gap-y-5`}>{children}</div>;
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

/** One labelled row: name and total on the left, a single segmented bar with its legend on the right. */
export function StackRow({ label, items, href, empty }: { label: string; items: Seg[]; href?: string; empty: string }) {
  const segs = colorize(items);
  const total = segs.reduce((a, s) => a + s.value, 0);
  const head = (
    <div className="min-w-0">
      <div className="text-[13px] font-medium">{label}</div>
      <div className="text-[12px] text-muted tnum">{total === 0 ? "none yet" : `${total} total`}</div>
    </div>
  );
  return (
    <div className="grid md:grid-cols-[170px_minmax(0,1fr)] gap-x-6 gap-y-2 py-3.5 first:pt-0 last:pb-0 border-t border-line first:border-t-0">
      {href ? <Link href={href} className="hover:underline underline-offset-2 decoration-line-strong">{head}</Link> : head}
      {total === 0 ? <p className="text-[12.5px] text-muted self-center">{empty}</p> : (
        <div className="self-center min-w-0">
          <div className="flex h-2.5 rounded-full overflow-hidden gap-0.5" role="img" aria-label={segs.map((s) => `${s.label} ${s.value}`).join(", ")}>
            {segs.map((s) => <div key={s.label} className="first:rounded-l-full last:rounded-r-full" style={{ width: `${(s.value / total) * 100}%`, background: s.color }} title={`${s.label}: ${s.value}`} />)}
          </div>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 text-[12px]">
            {segs.map((s) => (
              <li key={s.label} className="flex items-center gap-1.5 tnum"><span className="w-2 h-2 rounded-full" style={{ background: s.color }} aria-hidden /><span className="text-muted">{s.label}</span><span className="font-semibold">{s.value}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Horizontal comparison bars on a shared scale. */
export function BarRows({ rows }: { rows: { label: string; value: number; display: string; tone?: Tone }[] }) {
  const max = Math.max(...rows.map((r) => r.value), 0);
  if (rows.length === 0 || max <= 0) return <p className="text-muted text-[12.5px]">Nothing to compare yet.</p>;
  return (
    <ul className="space-y-3.5">
      {rows.map((r) => (
        <li key={r.label}>
          <div className="flex items-baseline justify-between gap-3 mb-1.5"><span className="text-[12.5px] text-muted">{r.label}</span><span className="tnum font-semibold text-[14px] tracking-[-0.01em]">{r.display}</span></div>
          <div className="h-2 rounded-full" style={{ background: "var(--chart-track)" }}><div className="h-2 rounded-full" style={{ width: `${(r.value / max) * 100}%`, background: FILL[r.tone ?? "neutral"] }} /></div>
        </li>
      ))}
    </ul>
  );
}

/** Stock against its reorder level. The bar is available / reorder level, capped at full. */
export function StockWatch({ rows }: { rows: { code: string; name: string; available: number; reorder: number }[] }) {
  return (
    <ul className="divide-y divide-line border-t border-line">
      {rows.map((r) => {
        const ratio = r.reorder > 0 ? Math.min(1, r.available / r.reorder) : 0;
        return (
          <li key={r.code} className="px-5 py-2.5 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 items-center">
            <div className="min-w-0"><span className="text-[13px] block truncate">{r.name}</span><span className="font-mono text-[11px] text-muted">{r.code}</span></div>
            <div className="text-right tnum text-[13px]"><span className="font-semibold">{r.available}</span><span className="text-muted"> of {r.reorder}</span></div>
            <div className="col-span-2 h-1.5 rounded-full" style={{ background: "var(--chart-track)" }}><div className="h-1.5 rounded-full" style={{ width: `${ratio * 100}%`, background: ratio === 0 ? FILL.danger : FILL.warning }} /></div>
          </li>
        );
      })}
    </ul>
  );
}

/* ------------------------------------------------------------------ workflow, queue, activity */
export type Stage = { label: string; count: number | null; unit: string; href?: string; flags: { label: string; n: number; tone: Tone }[] };

/** Workflow stages in order, joined by connectors. A stage's top rule takes the colour of its most severe open exception. */
export function Flow({ stages }: { stages: Stage[] }) {
  return (
    <ol className="grid sm:grid-cols-2 xl:grid-cols-6 gap-y-4 gap-x-0">
      {stages.map((s, i) => {
        const flagged = s.flags.filter((f) => f.n > 0);
        const sev: Tone = flagged.some((f) => f.tone === "danger") ? "danger" : flagged.length ? "warning" : "neutral";
        const inner = (
          <>
            <div className="h-0.5 rounded-full mb-3" style={{ background: sev === "neutral" ? "var(--line-strong)" : FILL[sev] }} aria-hidden />
            <div className="text-[12.5px] font-medium">{s.label}</div>
            <div className="mt-2 flex items-baseline gap-1.5"><Figure value={s.count ?? "—"} className="text-[26px]" /><span className="text-[12px] text-muted leading-4">{s.unit}</span></div>
            <div className="mt-2.5 flex flex-wrap gap-1.5 min-h-[22px]">
              {flagged.length === 0 ? <span className="text-[11.5px] text-muted">Clear</span>
                : flagged.map((f) => (
                  <span key={f.label} className={`text-[11.5px] tnum leading-4 px-1.5 py-0.5 rounded ${TEXT[f.tone]}`} style={{ background: `color-mix(in srgb, ${FILL[f.tone]} 11%, transparent)` }}>{f.n} {f.label}</span>
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
  if (items.length === 0) return <div className="px-5 pb-5 text-[13px] text-muted">{empty}</div>;
  return (
    <ul className="divide-y divide-line border-t border-line">
      {items.map((q) => {
        const inner = (
          <>
            <span className="w-[3px] self-stretch rounded-full shrink-0" style={{ background: FILL[q.tone] }} aria-hidden />
            <span className="min-w-0 flex-1"><span className="block text-[13px] truncate">{q.label}</span>{q.detail && <span className="block text-[12px] text-muted truncate">{q.detail}</span>}</span>
            {q.count != null && <Figure value={q.count} className="text-[18px]" />}
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
              <div className="text-[13px] truncate"><span className="font-medium">{e.action}</span> <span className="text-muted">{e.record}</span></div>
              <div className="text-muted text-[11.5px] truncate">{e.user}</div>
            </div>
            <time className="text-[11.5px] text-muted tnum shrink-0" title={e.at}>{e.ago}</time>
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
  return <div role={tone === "danger" ? "alert" : "status"} className={`panel px-4 py-3 text-[13px] ${tone === "danger" ? "text-danger" : "text-muted"}`}>{children}</div>;
}

export function SkeletonRows({ n = 4 }: { n?: number }) {
  return <div className="px-5 pb-5 space-y-3" aria-busy="true">{Array.from({ length: n }).map((_, i) => <div key={i} className="skeleton h-4" />)}</div>;
}
