"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

/** Fetch-on-mount with a visible loading, error and refetch state. Used by every lazily loaded tab so no page loads its whole history at once. */
export function useGet<T>(path: string | null, params?: Record<string, string | number | undefined>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const key = path ? path + JSON.stringify(params ?? {}) : "";
  useEffect(() => {
    if (!path) return;
    let live = true;
    setLoading(true); setError(null);
    api<T>(path, { params }).then((d) => live && setData(d)).catch((e) => live && setError(e instanceof ApiError ? (e.status === 403 ? "You do not have permission to see this." : e.message) : "Could not load this section")).finally(() => live && setLoading(false));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { data, error, loading };
}

export const qty = (v: string | number | null | undefined, unit?: string | null) => (v === null || v === undefined ? "—" : `${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 3 })}${unit ? ` ${unit}` : ""}`);

export const pct = (v: string | number | null | undefined) => (v === null || v === undefined ? "—" : `${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 1 })}%`);

export function Stat({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "warn" | "bad" }) {
  return (
    <div className="panel px-4 py-3 min-w-0" title={note}>
      <div className="t-support truncate">{label}</div>
      <div className={`text-[1.0625rem] leading-7 font-semibold tracking-[-0.01em] truncate ${tone === "bad" ? "text-danger" : tone === "warn" ? "text-warning" : ""}`}>{value}</div>
    </div>
  );
}

export function KV({ rows, single }: { rows: [string, React.ReactNode][]; single?: boolean }) {
  return (
    <dl className={`grid grid-cols-1 gap-x-8 ${single ? "" : "sm:grid-cols-2"}`}>
      {rows.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-3 py-2 border-b border-line"><dt className="t-label">{k}</dt><dd className="t-value text-right min-w-0 break-words">{v}</dd></div>
      ))}
    </dl>
  );
}

export function Pager({ page, total, size, onPage }: { page: number; total: number; size: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  if (total <= size) return <p className="t-support px-4 py-3">{total} {total === 1 ? "record" : "records"}</p>;
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 border-t border-line">
      <span className="t-support">{(page - 1) * size + 1}–{Math.min(page * size, total)} of {total}</span>
      <div className="flex gap-2"><button className="btn btn-sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button><button className="btn btn-sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button></div>
    </div>
  );
}

export function Section({ title, hint, children, actions }: { title: string; hint?: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <section className="panel overflow-hidden" aria-label={title}>
      <div className="flex items-start justify-between gap-3 px-4 pt-4 pb-3"><div className="min-w-0"><h2 className="t-section">{title}</h2>{hint && <p className="t-support mt-0.5">{hint}</p>}</div>{actions}</div>
      {children}
    </section>
  );
}

export function State({ loading, error, empty, children }: { loading: boolean; error: string | null; empty?: string | false; children: React.ReactNode }) {
  if (loading) return <div className="px-4 pb-4 space-y-2" aria-busy="true"><div className="skeleton h-5" /><div className="skeleton h-5 w-3/4" /><div className="skeleton h-5 w-1/2" /></div>;
  if (error) return <p role="alert" className="px-4 pb-4 text-[0.8125rem] text-danger">{error}</p>;
  if (empty) return <p className="px-4 pb-4 t-support">{empty}</p>;
  return <>{children}</>;
}

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (v: T) => void; tabs: { id: T; label: string }[] }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-line" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={`relative h-10 px-4 text-[0.8125rem] whitespace-nowrap transition-colors ${value === t.id ? "font-medium text-fg" : "text-muted hover:text-fg"}`}>
          {t.label}{value === t.id && <span className="absolute left-3 right-3 -bottom-px h-[2px] rounded-full bg-[var(--brand)]" />}
        </button>
      ))}
    </div>
  );
}
