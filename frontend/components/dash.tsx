"use client";
/** Reusable building blocks for operational dashboards. Every component renders what it is given and nothing else:
 *  no defaults that look like data, no decorative placeholders. Callers pass values calculated from API responses. */
import Link from "next/link";
import { ArrowUpRight, ChevronRight } from "lucide-react";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
const FILL: Record<Tone, string> = {
  success: "var(--success)", warning: "var(--warning)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--chart-fill)",
};
const TEXT: Record<Tone, string> = { success: "text-success", warning: "text-warning", danger: "text-danger", info: "text-info", neutral: "text-muted" };

export function Section({ title, description, action, children, className = "", flush }: {
  title: string; description?: string; action?: React.ReactNode; children: React.ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <div className="flex items-start justify-between gap-3 px-4 pt-3.5 pb-3">
        <div className="min-w-0">
          <h2 className="text-[13.5px] font-semibold leading-5">{title}</h2>
          {description && <p className="text-muted text-[12px] mt-0.5">{description}</p>}
        </div>
        {action}
      </div>
      <div className={flush ? "" : "px-4 pb-4"}>{children}</div>
    </section>
  );
}

export function SectionLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="text-[12px] text-muted hover:text-fg inline-flex items-center gap-0.5 shrink-0">{children}<ChevronRight size={12} /></Link>;
}

/** A single operational figure. `progress` (0–1) draws a thin meter and must be a real ratio; omit it otherwise. */
export function KpiCard({ label, value, hint, tone = "neutral", href, loading, progress }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: Tone; href?: string; loading?: boolean; progress?: number | null;
}) {
  const body = (
    <>
      <div className="flex items-center justify-between text-[12px] text-muted">
        <span>{label}</span>
        {href && <ArrowUpRight size={13} className="opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity shrink-0" />}
      </div>
      <div className="text-[28px] leading-9 font-semibold tnum mt-1 tracking-[-0.02em]">
        {loading ? <span className="skeleton inline-block w-14 h-6 align-middle" /> : value}
      </div>
      {progress != null && !loading && (
        <div className="h-1 rounded-full mt-2" style={{ background: "var(--chart-track)" }} role="img" aria-label={`${Math.round(progress * 100)}%`}>
          <div className="h-1 rounded-full" style={{ width: `${Math.max(0, Math.min(1, progress)) * 100}%`, background: FILL[tone] }} />
        </div>
      )}
      {hint && !loading && <div className={`text-[12px] mt-1.5 tnum ${tone === "neutral" ? "text-muted" : TEXT[tone]}`}>{hint}</div>}
    </>
  );
  const cls = "panel p-4 block h-full";
  return href
    ? <Link href={href} className={`${cls} panel-link group`}>{body}</Link>
    : <div className={cls}>{body}</div>;
}

/** One stacked bar for a distribution across statuses (e.g. orders by status). Zero-count entries are omitted. */
export function StatusBar({ items, unit = "", empty }: { items: { label: string; value: number; tone?: Tone }[]; unit?: string; empty?: { text: string; href?: string; cta?: string } }) {
  const shown = items.filter((i) => i.value > 0);
  const total = shown.reduce((a, i) => a + i.value, 0);
  if (total === 0) return (
    <p className="text-muted text-[12.5px]">{empty?.text ?? "No records yet."}{empty?.href && <> <Link href={empty.href} className="text-fg underline underline-offset-2">{empty.cta ?? "Open"}</Link></>}</p>
  );
  // Neutral segments get graduated greys so adjacent segments stay distinguishable without adding hues.
  let grey = 0;
  const greys = shown.filter((i) => !i.tone || i.tone === "neutral").length;
  const colorOf = (i: { tone?: Tone }) => {
    if (i.tone && i.tone !== "neutral") return FILL[i.tone];
    const mix = greys <= 1 ? 100 : Math.round(100 - (grey++ / (greys - 1)) * 70);
    return `color-mix(in srgb, var(--chart-fill) ${mix}%, var(--surface))`;
  };
  const segs = shown.map((i) => ({ ...i, color: colorOf(i) }));
  return (
    <div>
      <div className="flex h-2.5 rounded-full overflow-hidden gap-px" role="img" aria-label={segs.map((s) => `${s.label} ${s.value}`).join(", ")}>
        {segs.map((s) => <div key={s.label} style={{ width: `${(s.value / total) * 100}%`, background: s.color }} title={`${s.label}: ${s.value}`} />)}
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
        {segs.map((s) => (
          <li key={s.label} className="flex items-center gap-2 text-[12.5px] min-w-0">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: s.color }} aria-hidden />
            <span className="truncate text-muted">{s.label}</span>
            <span className="ml-auto tnum font-medium">{s.value}{unit}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Horizontal comparison bars on a shared scale. */
export function BarRows({ rows }: { rows: { label: string; value: number; display: string; tone?: Tone }[] }) {
  const max = Math.max(...rows.map((r) => r.value), 0);
  if (rows.length === 0 || max <= 0) return <p className="text-muted text-[12.5px]">Nothing to compare yet.</p>;
  return (
    <ul className="space-y-3">
      {rows.map((r) => (
        <li key={r.label}>
          <div className="flex items-baseline justify-between text-[12.5px] mb-1"><span className="text-muted">{r.label}</span><span className="tnum font-medium">{r.display}</span></div>
          <div className="h-2 rounded-full" style={{ background: "var(--chart-track)" }}>
            <div className="h-2 rounded-full" style={{ width: `${(r.value / max) * 100}%`, background: FILL[r.tone ?? "neutral"] }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export type Stage = { label: string; count: number | null; unit: string; href?: string; flags: { label: string; n: number; tone: Tone }[] };
/** Workflow stages in order. A stage with open exceptions is marked; the stage carrying the most is named as the bottleneck by the caller. */
export function Pipeline({ stages, bottleneck }: { stages: Stage[]; bottleneck?: string | null }) {
  return (
    <ol className="grid sm:grid-cols-2 xl:grid-cols-6 gap-px rounded-lg overflow-hidden border border-line" style={{ background: "var(--line)" }}>
      {stages.map((s, i) => {
        const flagged = s.flags.filter((f) => f.n > 0);
        const inner = (
          <>
            <div className="flex items-center gap-1.5 text-[12px] text-muted"><span className="tnum">{i + 1}</span><span className="truncate">{s.label}</span>
              {bottleneck === s.label && <span className="ml-auto text-[10.5px] font-medium text-danger">Most exceptions</span>}</div>
            <div className="mt-1.5 flex items-baseline gap-1.5"><span className="text-[22px] font-semibold tnum leading-7">{s.count ?? "—"}</span><span className="text-[12px] text-muted">{s.unit}</span></div>
            <div className="mt-2 space-y-0.5 min-h-[18px]">
              {flagged.length === 0 ? <div className="text-[11.5px] text-muted">No exceptions</div>
                : flagged.map((f) => <div key={f.label} className={`text-[11.5px] tnum ${TEXT[f.tone]}`}>{f.n} {f.label}</div>)}
            </div>
          </>
        );
        return (
          <li key={s.label} className="bg-surface">
            {s.href ? <Link href={s.href} className="block p-3.5 hover:bg-surface-2 transition-colors h-full">{inner}</Link> : <div className="p-3.5 h-full">{inner}</div>}
          </li>
        );
      })}
    </ol>
  );
}

export type QueueItem = { key: string; label: string; detail?: string; count?: number | null; tone: Tone; href?: string };
/** Prioritised work: callers pass items already ordered by severity. */
export function WorkQueue({ items, empty }: { items: QueueItem[]; empty: React.ReactNode }) {
  if (items.length === 0) return <div className="px-4 pb-5 text-[13px] text-muted">{empty}</div>;
  return (
    <ul className="divide-y divide-line border-t border-line">
      {items.map((q) => {
        const inner = (
          <>
            <span className="w-1 self-stretch rounded-full shrink-0" style={{ background: FILL[q.tone] }} aria-hidden />
            <span className="min-w-0 flex-1"><span className="block text-[13px] truncate">{q.label}</span>{q.detail && <span className="block text-[12px] text-muted truncate">{q.detail}</span>}</span>
            {q.count != null && <span className="tnum text-[15px] font-semibold">{q.count}</span>}
            {q.href && <ChevronRight size={14} className="text-muted shrink-0" />}
          </>
        );
        return (
          <li key={q.key}>
            {q.href ? <Link href={q.href} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2 transition-colors">{inner}</Link> : <div className="flex items-center gap-3 px-4 py-2.5">{inner}</div>}
          </li>
        );
      })}
    </ul>
  );
}

export type TimelineEntry = { id: string | number; action: string; record: string; user: string; at: string; href?: string };
export function Timeline({ items }: { items: TimelineEntry[] }) {
  return (
    <ol className="px-4 pb-1">
      {items.map((e, i) => {
        const body = (
          <>
            <div className="text-[13px]"><span className="font-medium">{e.action}</span> <span className="text-muted">{e.record}</span></div>
            <div className="text-muted text-[11.5px] tnum">{e.user} · {e.at}</div>
          </>
        );
        return (
          <li key={e.id} className="relative pl-5 pb-3.5 last:pb-2">
            <span className="absolute left-[3px] top-[7px] w-[7px] h-[7px] rounded-full border border-line-strong bg-surface" aria-hidden />
            {i < items.length - 1 && <span className="absolute left-[6px] top-[16px] bottom-0 w-px bg-line" aria-hidden />}
            {e.href ? <Link href={e.href} className="block hover:underline underline-offset-2 decoration-line-strong">{body}</Link> : body}
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
  return <div className="px-4 pb-4 space-y-3" aria-busy="true">{Array.from({ length: n }).map((_, i) => <div key={i} className="skeleton h-4" />)}</div>;
}
