"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLiveRevision } from "@/lib/live";
import { useRouter, useSearchParams } from "next/navigation";
import { LayoutGrid, Columns3, Plus, Rows3, Search, Trash2, ChevronUp, ChevronDown } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fieldLabel, fmtDate, fmtDateTime, fmtMoney, fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { defaultParam, toneFor, type Action, type Col, type Create, type Field, type Param, type RefKey, type Row, type Screen } from "@/lib/modules";
import { DataTable, type Col as TCol } from "./DataTable";
import { RowActions } from "./RowActions";
import { ScanButton, ScanDialog, resolveCode } from "./Scanner";
import { RfqCard, RfqSummary, bidsCsv } from "./rfq";
import { PrCard, PrSummary } from "./pr";
import { OrderCard, OrdersSummary } from "./orders";
import { PoCard, PoSummary } from "./po";
import { BOARDS, WastageView } from "./boards";
import { ModuleDashboard } from "./ModuleDashboard";
import { TableTools, BulkBar, type SavedView } from "./TableTools";
import { usePref } from "@/lib/prefs";
import { downloadCsv } from "@/lib/csv";
import { Badge, ConfirmDialog, Drawer, EmptyState, useDebounced, useToast, useUndoable } from "./ui";

/* ------------------------------------------------------------------ reference pickers */
const REFS: Record<RefKey, { api: string; params?: Record<string, string>; label: (r: Row) => string }> = {
  batches: { api: "/inventory/batches", params: { page_size: "200" }, label: (r) => `${r.batch_no} · ${r.item_code} (${r.qc_status})` },
  items: { api: "/masters/items", params: { page_size: "100" }, label: (r) => `${r.item_code} — ${r.name}` },
  rm: { api: "/masters/items", params: { page_size: "100", kind: "RM" }, label: (r) => `${r.item_code} — ${r.name}` },
  fg: { api: "/masters/items", params: { page_size: "100", kind: "FG" }, label: (r) => `${r.item_code} — ${r.name}` },
  suppliers: { api: "/masters/suppliers", params: { page_size: "100" }, label: (r) => `${r.code} — ${r.name}` },
  customers: { api: "/masters/customers", params: { page_size: "100" }, label: (r) => `${r.code} — ${r.name}` },
  warehouses: { api: "/masters/warehouses", params: { page_size: "100" }, label: (r) => `${r.code} — ${r.name}` },
  brands: { api: "/masters/brands", params: { page_size: "100" }, label: (r) => `${r.code} — ${r.name}` },
  users: { api: "/admin/users", params: { page_size: "100" }, label: (r) => `${r.full_name} (${r.email})` },
  equipment: { api: "/maintenance/equipment", label: (r) => `${r.asset_id} — ${r.name}` },
  leads: { api: "/crm/leads", label: (r) => `${r.name}${r.company ? ` (${r.company})` : ""}` },
  formulations: { api: "/rnd/formulations", label: (r) => `${r.code} — ${r.name}` },
  campaigns: { api: "/branding/campaigns", label: (r) => r.name },
  ecomAccounts: { api: "/ecommerce/accounts", label: (r) => `${r.platform} — ${r.name}` },
  boms: { api: "/production/boms", label: (r) => `#${r.id} ${r.item_code} v${r.version_no} (${r.status})` },
  pos: { api: "/procurement/purchase-orders", label: (r) => `${r.number} (${r.status})` },
  documents: { api: "/documents", label: (r) => r.title },
};
const cache = new Map<string, Row[]>();
const asRows = (d: any): Row[] => (Array.isArray(d) ? d : Array.isArray(d?.items) ? d.items : []);

function useRefOptions(ref: RefKey) {
  const [opts, setOpts] = useState<{ id: number; label: string }[]>([]);
  useEffect(() => {
    let live = true;
    const def = REFS[ref];
    const key = def.api + JSON.stringify(def.params);
    const apply = (rows: Row[]) => live && setOpts(rows.map((r) => ({ id: r.id, label: def.label(r) })));
    if (cache.has(key)) { apply(cache.get(key)!); return; }
    api<any>(def.api, { params: def.params }).then((d) => { const rows = asRows(d); cache.set(key, rows); apply(rows); }).catch(() => apply([]));
    return () => { live = false; };
  }, [ref]);
  return opts;
}

function RefSelect({ f, value, onChange, id }: { f: Field; value: any; onChange: (v: any) => void; id: string }) {
  const opts = useRefOptions(f.ref!);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanned, setScanned] = useState<{ id: number; label: string } | null>(null);
  const toast = useToast();
  const all = scanned && !opts.some((o) => o.id === scanned.id) ? [...opts, scanned] : opts;
  async function onCode(code: string) {
    try {
      const r = await resolveCode(code);
      const hit = r.matches.find((m) => m.type === f.scan);
      if (!hit) { toast("error", `“${code}” is not a ${f.scan === "PO" ? "purchase order" : f.scan === "EQP" ? "piece of equipment" : f.scan === "BATCH" ? "batch" : "valid"} code`); return; }
      setScanned({ id: hit.id, label: hit.label }); onChange(String(hit.id)); setScanOpen(false); toast("success", `Selected ${hit.label}`);
    } catch (e) { toast("error", e instanceof ApiError ? e.message : "Could not read the code"); }
  }
  const sel = f.type === "refs" ? (
    <select id={id} className="input !h-auto min-h-[84px]" multiple value={Array.isArray(value) ? value : []} onChange={(e) => onChange(Array.from(e.target.selectedOptions).map((o) => o.value))}>
      {all.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  ) : (
    <select id={id} className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select…</option>{all.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  );
  if (!f.scan) return sel;
  return (
    <>
      <div className="flex gap-2"><div className="flex-1 min-w-0">{sel}</div><ScanButton onClick={() => setScanOpen(true)} /></div>
      <ScanDialog open={scanOpen} onClose={() => setScanOpen(false)} onCode={onCode} title="Scan to select" />
    </>
  );
}

/** Purchase-order lines: pick a PO, get its lines with sensible defaults (open quantity to receive, or received quantity to invoice). */
function PoLines({ f, poId, value, onChange }: { f: Field; poId: string; value: Record<string, Row> | undefined; onChange: (v: Record<string, Row>) => void }) {
  const [po, setPo] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const items = useRefOptions("items");
  const label = (id: number) => items.find((o) => o.id === id)?.label ?? `Item ${id}`;
  useEffect(() => {
    setPo(null); setErr(null);
    if (!poId) return;
    let live = true;
    api<any>(`/procurement/purchase-orders/${poId}`).then((d) => {
      if (!live) return;
      setPo(d);
      onChange(Object.fromEntries(d.items.map((it: Row) => [it.id, f.mode === "invoice"
        ? { qty: String(Number(it.qty_received)), unit_price: it.unit_price, tax_rate: it.tax_rate }
        : { qty: String(Math.max(0, Number(it.qty_ordered) - Number(it.qty_received))) }])));
    }).catch((e) => live && setErr(e instanceof ApiError ? e.message : "Could not load the order"));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poId]);
  if (!poId) return <p className="text-muted text-[0.75rem]">Choose a purchase order to see its lines.</p>;
  if (err) return <p className="text-danger text-[0.75rem]">{err}</p>;
  if (!po) return <div className="skeleton h-16" />;
  const extra: Field[] = f.extra ?? [];
  const set = (id: number, k: string, v: string) => onChange({ ...(value ?? {}), [id]: { ...(value?.[id] ?? {}), [k]: v } });
  return (
    <div className="panel overflow-x-auto">
      <table className="dt">
        <thead><tr><th>Item</th><th className="num">Ordered</th><th className="num">Received</th><th>{f.mode === "invoice" ? "Invoice qty" : "Receive qty"}</th>{extra.map((c) => <th key={c.name}>{c.label}</th>)}</tr></thead>
        <tbody>{po.items.map((it: Row) => (
          <tr key={it.id}>
            <td className="!max-w-[220px]">{label(it.item_id)}</td><td className="num">{fmtNum(it.qty_ordered, 3)}</td><td className="num">{fmtNum(it.qty_received, 3)}</td>
            <td className="!py-1"><input className="input !w-28" type="number" step="any" min="0" aria-label={`Quantity for line ${it.line_no}`} value={value?.[it.id]?.qty ?? ""} onChange={(e) => set(it.id, "qty", e.target.value)} /></td>
            {extra.map((c) => <td key={c.name} className="!py-1"><input className="input !w-32" type={c.type === "date" ? "date" : c.type === "number" ? "number" : "text"} step="any" aria-label={`${c.label} for line ${it.line_no}`} value={value?.[it.id]?.[c.name] ?? ""} onChange={(e) => set(it.id, c.name, e.target.value)} /></td>)}
          </tr>))}</tbody>
      </table>
    </div>
  );
}

/** Return lines for a dispatched order: only what can still be returned. */
function ShipLines({ orderId, value, onChange }: { orderId: number; value: Record<string, Row> | undefined; onChange: (v: Record<string, Row>) => void }) {
  const [lines, setLines] = useState<Row[] | null>(null);
  useEffect(() => { api<Row[]>(`/dispatch/orders/${orderId}/shipment-lines`).then(setLines).catch(() => setLines([])); }, [orderId]);
  if (!lines) return <div className="skeleton h-16" />;
  return (
    <div className="panel overflow-x-auto"><table className="dt"><thead><tr><th>Item</th><th>Batch</th><th className="num">Shipped</th><th className="num">Returnable</th><th>Return qty</th></tr></thead>
      <tbody>{lines.map((l) => (
        <tr key={l.shipment_line_id}><td className="!max-w-[220px]">{l.item_code} {l.name}</td><td className="tnum font-medium text-[0.8125rem]">{l.batch_no ?? "—"}</td><td className="num">{fmtNum(l.shipped, 3)}</td><td className="num">{fmtNum(l.returnable, 3)}</td>
          <td className="!py-1"><input className="input !w-28" type="number" step="any" min="0" max={l.returnable} aria-label={`Return quantity for ${l.item_code}`} value={value?.[l.shipment_line_id]?.qty ?? ""} onChange={(e) => onChange({ ...(value ?? {}), [l.shipment_line_id]: { qty: e.target.value } })} /></td></tr>))}</tbody></table></div>
  );
}

/* ------------------------------------------------------------------ form fields */
function Input({ f, value, onChange, id }: { f: Field; value: any; onChange: (v: any) => void; id: string }) {
  if (f.type === "ref" || f.type === "refs") return <RefSelect f={f} value={value} onChange={onChange} id={id} />;
  if (f.type === "select") return <select id={id} className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)}><option value="">Select…</option>{f.options!.map((o) => <option key={o}>{o}</option>)}</select>;
  if (f.type === "textarea") return <textarea id={id} className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
  if (f.type === "checkbox") return <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  return <input id={id} className="input" type={f.type === "date" ? "date" : f.type === "number" ? "number" : "text"} step={f.step} value={value ?? ""} placeholder={f.placeholder} onChange={(e) => onChange(e.target.value)} />;
}

function Lines({ f, value, onChange }: { f: Field; value: Row[]; onChange: (v: Row[]) => void }) {
  const rows = value?.length ? value : [{}];
  const set = (i: number, k: string, v: any) => onChange(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <div className="panel overflow-x-auto">
      <table className="dt">
        <thead><tr>{f.lines!.map((c) => <th key={c.name}>{c.label}{c.required && <span className="text-danger"> *</span>}</th>)}<th className="w-px" /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {f.lines!.map((c) => <td key={c.name} className="!py-1 !max-w-none"><Input f={c} id={`${f.name}-${i}-${c.name}`} value={r[c.name] ?? c.default} onChange={(v) => set(i, c.name, v)} /></td>)}
              <td><button type="button" className="btn btn-ghost btn-sm !px-1.5" aria-label="Remove line" onClick={() => onChange(rows.filter((_, j) => j !== i))} disabled={rows.length === 1}><Trash2 size={13} /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="p-2"><button type="button" className="btn btn-sm" onClick={() => onChange([...rows, {}])}><Plus size={13} />Add line</button></div>
    </div>
  );
}

const isIdName = (n: string) => /(^|_)id$/.test(n);
function convert(f: Field, v: any): any {
  if (v === "" || v === undefined || v === null) return f.type === "poLines" || f.type === "shipLines" ? [] : undefined;
  switch (f.type) {
    case "number": return isIdName(f.name) || ["cartons", "from_version_no", "lead_time_days", "frequency_days", "quotation_id"].includes(f.name) ? Number(v) : String(v);
    case "ref": return Number(v);
    case "refs": return (v as string[]).map(Number);
    case "csv": return String(v).split(",").map((s) => s.trim()).filter(Boolean);
    case "checkbox": return !!v;
    case "poLines": {
      const rows = Object.entries((v as Record<string, Row>) ?? {}).filter(([, r]) => Number(r.qty) > 0)
        .map(([id, r]) => Object.fromEntries([["po_item_id", Number(id)], ...Object.entries(r).filter(([, x]) => x !== "" && x !== undefined && x !== null)]));
      return rows;
    }
    case "shipLines": return Object.entries((v as Record<string, Row>) ?? {}).filter(([, r]) => Number(r.qty) > 0).map(([id, r]) => ({ shipment_line_id: Number(id), qty: String(r.qty) }));
    case "file": return v as File;
    case "lines": {
      const rows = (v as Row[]).map((r) => Object.fromEntries(f.lines!.map((c) => [c.name, convert(c, r[c.name])]).filter(([, x]) => x !== undefined))).filter((r) => Object.keys(r).length);
      return rows;
    }
    default: return String(v).trim();
  }
}
export function buildBody(fields: Field[], values: Row): { body: Row; errors: Record<string, string> } {
  const body: Row = {}, errors: Record<string, string> = {};
  for (const f of fields) {
    const raw = values[f.name] ?? f.default;
    const v = convert(f, raw);
    if (f.required && (v === undefined || (Array.isArray(v) && v.length === 0))) { errors[f.name] = f.type === "poLines" || f.type === "shipLines" ? "Enter a quantity on at least one line" : "Required"; continue; }
    if (f.type === "lines" && f.required) for (const r of v as Row[]) for (const c of f.lines!) if (c.required && r[c.name] === undefined) errors[f.name] = `Every line needs “${c.label}”`;
    if (v !== undefined) body[f.name] = v;
  }
  return { body, errors };
}

export function FormDrawer({ open, title, subtitle, fields, submitLabel, onClose, onSubmit, ctx }: {
  open: boolean; title: string; subtitle?: string; fields: Field[]; submitLabel: string; onClose: () => void; onSubmit: (body: Row, raw: Row) => Promise<void>; ctx?: Row;
}) {
  const [vals, setVals] = useState<Row>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  useEffect(() => { if (open) { setVals({}); setErrors({}); } }, [open]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const { body, errors: errs } = buildBody(fields, vals);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setSaving(true);
    try { await onSubmit(body, vals); }
    catch (err) {
      if (err instanceof ApiError && err.fields && Object.keys(err.fields).length) setErrors(Object.fromEntries(Object.entries(err.fields).map(([k, v]) => [k.split(".")[0], v])));
      toast("error", err instanceof ApiError ? err.message : "Could not save");
    } finally { setSaving(false); }
  }
  return (
    <Drawer open={open} onClose={onClose} title={title} subtitle={subtitle} width={fields.some((f) => f.type === "lines" || f.type === "poLines" || f.type === "shipLines") ? 820 : 520}
      footer={<><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" form="module-form" disabled={saving}>{saving ? "Working…" : submitLabel}</button></>}>
      <form id="module-form" onSubmit={submit} noValidate className="grid grid-cols-2 gap-x-4 gap-y-3.5">
        {fields.map((f) => {
          const wide = ["textarea", "lines", "refs", "poLines", "shipLines"].includes(f.type);
          const err = errors[f.name];
          return (
            <div key={f.name} className={wide ? "col-span-2" : ""}>
              {f.type === "checkbox" ? (
                <label className="flex items-center gap-2 h-8 text-[0.8125rem] cursor-pointer"><Input f={f} id={f.name} value={vals[f.name]} onChange={(v) => setVals((s) => ({ ...s, [f.name]: v }))} />{f.label}</label>
              ) : (
                <>
                  <label htmlFor={f.name} className="field-label">{f.label}{f.required && <span className="text-danger"> *</span>}</label>
                  {f.type === "lines"
                    ? <Lines f={f} value={vals[f.name]} onChange={(v) => setVals((s) => ({ ...s, [f.name]: v }))} />
                    : f.type === "poLines"
                    ? <PoLines f={f} poId={String(vals[f.poField ?? "po_id"] ?? "")} value={vals[f.name]} onChange={(v) => setVals((s) => ({ ...s, [f.name]: v }))} />
                    : f.type === "shipLines"
                    ? <ShipLines orderId={ctx!.id} value={vals[f.name]} onChange={(v) => setVals((s) => ({ ...s, [f.name]: v }))} />
                    : f.type === "file"
                    ? <input id={f.name} className="input" type="file" accept=".csv,text/csv" onChange={(e) => setVals((s) => ({ ...s, [f.name]: e.target.files?.[0] }))} />
                    : <Input f={f} id={f.name} value={vals[f.name] ?? f.default} onChange={(v) => setVals((s) => ({ ...s, [f.name]: v }))} />}
                </>
              )}
              {err ? <p className="text-danger text-[0.75rem] mt-1" role="alert">{err}</p> : f.help ? <p className="text-muted text-[0.75rem] mt-1">{f.help}</p> : null}
            </div>
          );
        })}
      </form>
    </Drawer>
  );
}

/* ------------------------------------------------------------------ read-only JSON view (dashboards, reports, record details) */
const MONEYISH = /(amount|total|value|revenue|cost|balance|price|profit|spend|payable|receivable|cogs|outstanding|debit|credit|charge|fees|freight|budget|margin$|taxable|cgst|sgst|igst|tax$|payment|paid|earnings|equity|liabilit|assets|income|expense|turnover)/i;
/** Whether a figure named `k` is a rupee amount. The module dashboards decide this from the name, exactly as the figure cards do. */
export const isMoneyKey = (k: string) => MONEYISH.test(k) && !/(qty|count|days)/i.test(k);
const AGEING_BUCKET = /^(current|\d+-\d+|\d+\+)$/i;           // receivables / payables ageing columns are amounts
function show(k: string, v: any, headline = false): React.ReactNode {
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  if (typeof v === "boolean") return <Badge tone={v ? "success" : "neutral"}>{v ? "Yes" : "No"}</Badge>;
  if (typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) && !/(^|_)(id|no|number|code|awb|ref)$/i.test(k) && v.length < 18)) {
    if (/pct|percent|rate$/i.test(k)) return `${fmtNum(v, headline ? 1 : 2)}%`;
    // headline figures (dashboard tiles) are read at a glance: whole rupees; record details keep the paise
    if ((MONEYISH.test(k) || AGEING_BUCKET.test(k)) && !/(qty|count|days)/i.test(k)) return headline ? fmtRupees(v) : fmtMoney(v);
    return fmtNum(v, 3);
  }
  if (typeof v === "string") {
    if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return fmtDateTime(v);
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return fmtDate(v);
    if (/^[A-Z_]{3,}$/.test(v)) return <Badge tone={toneFor(v)}>{titleCase(v)}</Badge>;
  }
  return String(v);
}
export function ReportView({ data, depth = 0, parent = "" }: { data: any; depth?: number; parent?: string }) {
  if (data === null || data === undefined) return null;
  if (Array.isArray(data)) {
    if (data.length === 0) return <p className="t-support">None.</p>;
    if (typeof data[0] !== "object" || data[0] === null) return <p className="text-[0.8125rem]">{data.map(String).join(", ")}</p>;
    const keys = Array.from(new Set(data.slice(0, 20).flatMap((r: Row) => Object.keys(r)))).filter((k) => typeof data.find((r: Row) => r[k] !== undefined && r[k] !== null)?.[k] !== "object").slice(0, 10);
    return (
      <div className="panel overflow-auto max-h-[420px]"><table className="dt"><thead><tr>{keys.map((k) => <th key={k} className={/(amount|total|value|qty|price|cost|balance|debit|credit|pct)/i.test(k) ? "num" : ""}>{fieldLabel(k)}</th>)}</tr></thead>
        <tbody>{data.slice(0, 200).map((r: Row, i: number) => <tr key={i}>{keys.map((k) => <td key={k} className={/(amount|total|value|qty|price|cost|balance|debit|credit|pct)/i.test(k) ? "num" : ""}>{show(k, r[k])}</td>)}</tr>)}</tbody></table></div>
    );
  }
  if (typeof data !== "object") return <p className="text-[0.8125rem]">{String(data)}</p>;
  const scalars = Object.entries(data).filter(([, v]) => v === null || typeof v !== "object");
  const nested = Object.entries(data).filter(([, v]) => v !== null && typeof v === "object");
  return (
    <div className={depth ? "space-y-3" : "space-y-5"}>
      {scalars.length > 0 && depth === 0 && (() => {
        /* headline figures: one panel divided by hairlines (1px gaps over the line colour), four to a row; blank cells close a short last row */
        const fill = (4 - (scalars.length % 4)) % 4;
        return (
          <dl className="grid grid-cols-2 md:grid-cols-4 gap-px bg-[var(--line)] border border-line rounded-[10px] overflow-hidden" style={{ boxShadow: "var(--shadow-md)" }}>
            {scalars.map(([k, v]) => {
              const numeric = typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) && !/(^|_)(id|no|number|code|awb|ref)$/i.test(k) && v.length < 18);
              const label = fieldLabel(k).replace(/ %$/, "").replace(/\bfollowups?\b/gi, (m) => (m.toLowerCase().endsWith("s") ? "follow-ups" : "follow-up")).replace(/\b(\d+)d\b/, "($1 days)");
              const shown = show(k, v, numeric);
              return (
                <div key={k} className="bg-[var(--card)] px-4 py-3 min-w-0">
                  <dt className="text-[0.75rem] font-medium text-muted truncate" title={label}>{label}</dt>
                  <dd className={`mt-1 truncate ${numeric ? "t-kpi tabular-nums" : "t-value"}`} title={String(shown)}>{shown}</dd>
                </div>
              );
            })}
            {Array.from({ length: fill }).map((_, i) => <div key={`fill-${i}`} className="bg-[var(--card)] hidden md:block" aria-hidden />)}
          </dl>
        );
      })()}
      {scalars.length > 0 && depth > 0 && (
        <dl className="grid gap-x-2.5 gap-y-0 grid-cols-2 md:grid-cols-3 [&>*]:mb-2.5">
          {scalars.map(([k, v]) => {
            const mk = MONEYISH.test(parent) && !/(count|qty|days)/i.test(parent) ? parent : k;   // children of a money block inherit its name
            const numeric = typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) && !/(^|_)(id|no|number|code|awb|ref)$/i.test(k) && v.length < 18);
            const label = fieldLabel(k);
            const shown = show(mk, v);
            return (
              <div key={k} className="panel p-4 min-w-0 grid grid-rows-subgrid row-span-2 gap-y-0 content-start">
                <dt className="label-caps line-clamp-2" title={label}>{label}</dt>
                <dd className={`${numeric ? "" : "t-value"} mt-2 break-words`}>{numeric ? <span className="kpi-fit"><span className="t-kpi" style={{ "--n": String(String(shown).length) } as React.CSSProperties} title={String(shown)}>{shown}</span></span> : shown}</dd>
              </div>
            );
          })}
        </dl>
      )}
      {nested.map(([k, v]) => (
        <section key={k}>
          <h3 className="text-[0.75rem] font-semibold text-muted mb-1.5">{fieldLabel(k)}</h3>
          <ReportView data={v} depth={depth + 1} parent={k} />
        </section>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ record detail (the side drawer) */
/** Foreign-key fields the API returns as bare numbers, and the lookup that turns each into a readable name. */
const ID_REF: Record<string, RefKey> = {
  supplier_id: "suppliers", customer_id: "customers", warehouse_id: "warehouses", from_warehouse_id: "warehouses", to_warehouse_id: "warehouses",
  item_id: "items", output_item_id: "items", component_item_id: "items", po_id: "pos", purchase_order_id: "pos", batch_id: "batches", brand_id: "brands",
  equipment_id: "equipment", bom_id: "boms", lead_id: "leads", campaign_id: "campaigns", formulation_id: "formulations",
  created_by: "users", approved_by: "users", assigned_to: "users", owner_id: "users", user_id: "users",
};
/** Internal keys with no meaning to a user: the row's own id, version counters, and any foreign key we cannot resolve to a name. */
const isHidden = (k: string) => /^(id|version|row_version)$/.test(k) || k === "number" || (/_id$/.test(k) && !ID_REF[k]);
const plainLabel = (k: string) => fieldLabel(k).replace(/\s+ID$/i, "");

function useIdLabels(data: any) {
  const [map, setMap] = useState<Record<string, { label: string; row: Row }>>({});
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let live = true;
    const need = new Map<RefKey, Set<number>>();
    const walk = (o: any, d = 0) => {
      if (!o || typeof o !== "object" || d > 4) return;
      if (Array.isArray(o)) { o.slice(0, 200).forEach((x) => walk(x, d + 1)); return; }
      for (const [k, v] of Object.entries(o)) {
        const rk = ID_REF[k];
        if (rk && typeof v === "number") { if (!need.has(rk)) need.set(rk, new Set()); need.get(rk)!.add(v); } else walk(v, d + 1);
      }
    };
    walk(data);
    (async () => {
      const out: Record<string, { label: string; row: Row }> = {};
      await Promise.all([...need].map(async ([rk, ids]) => {
        const def = REFS[rk];
        const key = def.api + JSON.stringify(def.params);
        let rows = cache.get(key);
        if (!rows) { try { rows = asRows(await api<any>(def.api, { params: def.params })); cache.set(key, rows); } catch { rows = []; } }
        const byId = new Map(rows.map((r) => [r.id, r]));
        await Promise.all([...ids].map(async (id) => {
          let r = byId.get(id);
          if (!r) { try { r = await api<Row>(`${def.api}/${id}`); } catch { /* not visible to this user: fall back to the bare id */ } }
          if (r) out[`${rk}:${id}`] = { label: def.label(r), row: r };
        }));
      }));
      if (live) { setMap(out); setReady(true); }
    })();
    return () => { live = false; };
  }, [data]);
  const hit = (k: string, v: any) => (ID_REF[k] ? map[`${ID_REF[k]}:${v}`] : undefined);
  return { ready, name: (k: string, v: any) => hit(k, v)?.label, row: (k: string, v: any) => hit(k, v)?.row };
}

type Namer = { ready: boolean; name: (k: string, v: any) => string | undefined; row: (k: string, v: any) => Row | undefined };
const ITEM_KEYS = ["item_id", "output_item_id", "component_item_id"];
const QTY_KEY = /(^|_)(qty|quantity)(_|$)/;
/** The unit of measure of the item a row refers to, so "100" reads "100 PCS". */
function unitOf(ctx: Row | undefined, n: Namer): string | undefined {
  if (!ctx) return undefined;
  for (const k of ITEM_KEYS) if (typeof ctx[k] === "number") return n.row(k, ctx[k])?.uom_code ?? undefined;
  return undefined;
}
function value(k: string, v: any, n: Namer, ctx?: Row): React.ReactNode {
  if (ID_REF[k] && typeof v === "number") {
    const nm = n.name(k, v);
    return nm ?? (n.ready ? <span className="text-muted">#{v}</span> : <span className="skeleton inline-block h-3.5 w-24 align-middle" aria-hidden />);
  }
  const shown = show(k, v);
  const unit = QTY_KEY.test(k) ? unitOf(ctx, n) : undefined;
  return unit ? <>{shown}<span className="text-muted ml-1 font-medium">{unit}</span></> : shown;
}
const isEmpty = (v: any) => v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0);
const NUMERIC_COL = /(amount|total|value|qty|quantity|price|cost|balance|debit|credit|pct|rate|weight|count)/i;

function RecordFields({ obj, n, depth }: { obj: Row; n: Namer; depth: number }) {
  const entries = Object.entries(obj).filter(([k, v]) => !isHidden(k) && !isEmpty(v));
  const scalars = entries.filter(([, v]) => typeof v !== "object").sort(([a], [b]) => Number(b === "status") - Number(a === "status"));   // status leads: it is the first thing people look for
  const nested = entries.filter(([, v]) => typeof v === "object");
  return (
    <div className="space-y-4">
      {scalars.length > 0 && (
        <dl className={`${depth === 0 ? "panel p-4" : ""} grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-3.5`}>
          {scalars.map(([k, v]) => (
            <div key={k} className={`min-w-0 ${typeof v === "string" && v.length > 48 ? "col-span-full" : ""}`}>
              <dt className="t-support">{plainLabel(k)}</dt>
              <dd className="t-value !font-semibold mt-0.5 break-words">{value(k, v, n, obj)}</dd>
            </div>
          ))}
        </dl>
      )}
      {nested.map(([k, v]) => (
        <section key={k}>
          <h3 className="text-[0.75rem] font-semibold mb-2 flex items-center gap-2">{plainLabel(k)}{Array.isArray(v) && <span className="badge tone-neutral">{v.length}</span>}</h3>
          {Array.isArray(v) ? <RecordTable rows={v} n={n} /> : <RecordFields obj={v as Row} n={n} depth={depth + 1} />}
        </section>
      ))}
    </div>
  );
}

function RecordTable({ rows, n }: { rows: any[]; n: Namer }) {
  if (typeof rows[0] !== "object" || rows[0] === null) return <p className="text-[0.8125rem]">{rows.map(String).join(", ")}</p>;
  const keys = Array.from(new Set(rows.slice(0, 30).flatMap((r: Row) => Object.keys(r)))).filter((k) => !isHidden(k) && rows.some((r: Row) => !isEmpty(r[k]) && typeof r[k] !== "object")).slice(0, 9);
  return (
    <div className="panel overflow-auto max-h-[420px]">
      <table className="dt">
        <thead><tr>{keys.map((k) => <th key={k} className={NUMERIC_COL.test(k) ? "num" : ""}>{plainLabel(k)}</th>)}</tr></thead>
        <tbody>{rows.slice(0, 200).map((r: Row, i: number) => <tr key={i}>{keys.map((k) => <td key={k} className={NUMERIC_COL.test(k) ? "num" : `!whitespace-normal !max-w-[280px] ${ID_REF[k] ? "min-w-[190px]" : ""}`}>{value(k, r[k], n, r)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

/** Record detail for the side drawer: compact label/value pairs in one card, names instead of database ids, lines as a table. */
export function RecordView({ data }: { data: any }) {
  const n = useIdLabels(data);
  if (!data || typeof data !== "object" || Array.isArray(data)) return <ReportView data={data} />;
  return <RecordFields obj={data} n={n} depth={0} />;
}

/* ------------------------------------------------------------------ lists */
function cell(c: Col, r: Row): React.ReactNode {
  const v = r[c.key];
  if (c.kind === "bool") return <Badge tone={v ? "warning" : "neutral"}>{v ? "Yes" : "No"}</Badge>;
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  switch (c.kind) {
    case "mono": return <span className="font-mono text-[0.78rem] font-semibold text-brand">{v}</span>;
    case "number": return fmtNum(v, 3);
    case "money": return fmtMoney(v);
    case "date": return fmtDate(v);
    case "datetime": return fmtDateTime(v);
    case "badge": return <Badge tone={toneFor(String(v))} live={/^(IN_PROGRESS|RUNNING|IN_TRANSIT|OUT_FOR_DELIVERY|PICKED_UP)$/.test(String(v))}>{titleCase(String(v))}</Badge>;
    default: return String(v);
  }
}

const hasFile = (fields: Field[]) => fields.some((f) => f.type === "file");
function toForm(body: Row): FormData { const fd = new FormData(); for (const [k, v] of Object.entries(body)) if (v !== undefined && v !== null) fd.append(k, v instanceof File ? v : String(v)); return fd; }

export function CreateButton({ create, onDone, onResult }: { create: Create; onDone: () => void; onResult?: (r: any) => void }) {
  const sp = useSearchParams();
  const router = useRouter();
  const [open, setOpen] = useState(() => sp.get("create") === "1");   // ?create=1 (from the quick-action dock or the palette) opens the form straight away
  const toast = useToast();
  useEffect(() => { if (sp.get("create") === "1") setOpen(true); }, [sp]);   // also when you are already on this screen and use the dock
  const close = () => { setOpen(false); if (sp.get("create")) router.replace(window.location.pathname, { scroll: false }); };
  return (
    <>
      <button className="btn btn-primary" onClick={() => setOpen(true)}><Plus size={14} />{create.label}</button>
      <FormDrawer open={open} title={create.title ?? create.label} fields={create.fields} submitLabel={create.title ? "Run" : "Create"} onClose={close}
        onSubmit={async (body) => {
          const payload = create.body ? create.body(body) : body;
          const path = typeof create.path === "function" ? create.path(body) : create.path;
          const res = await api<any>(path, { method: "POST", ...(hasFile(create.fields) ? { form: toForm(payload) } : { body: payload }), headers: create.idempotent ? { "Idempotency-Key": crypto.randomUUID() } : undefined });
          toast("success", create.title ? "Done" : `${(w => w.charAt(0).toUpperCase() + w.slice(1))(create.label.replace(/^(New|Add|Post|Raise|Log|Set|Start|Record|Create)\s+/i, ""))} saved`);
          setOpen(false); onDone(); if (create.title) onResult?.(res);
        }} />
    </>
  );
}

/** A scan session: show progress from the server, accept scans (camera, handheld or typed) and report each one's outcome immediately. */
function ScanSession({ open, onClose, title, hint, statusPath, postPath }: { open: boolean; onClose: () => void; title: string; hint?: string; statusPath: string; postPath: string }) {
  const [status, setStatus] = useState<any>(null);
  const [last, setLast] = useState<{ ok: boolean; text: string } | null>(null);
  const [qty, setQty] = useState("1");
  const load = useCallback(() => api<any>(statusPath).then(setStatus).catch(() => setStatus(null)), [statusPath]);
  useEffect(() => { if (open) { setLast(null); load(); } }, [open, load]);
  async function onCode(code: string) {
    try {
      const res = await api<any>(postPath, { body: { code, qty } });
      setStatus(res.lines ? res : status); setLast({ ok: true, text: res.scanned ? `${res.already_scanned ? "Already scanned" : "Scanned"}: ${res.scanned} (${res.handed_over}/${res.total})` : `Scanned ${code}` });
      if (!res.lines) load();
    } catch (e) { setLast({ ok: false, text: e instanceof ApiError ? `${e.message}` : "Scan failed" }); }
  }
  const top = postPath.includes("/orders/") ? <label className="text-[0.75rem] flex items-center gap-2 mb-3">Quantity per scan<input className="input !w-24" type="number" min="0" step="any" value={qty} onChange={(e) => setQty(e.target.value)} /></label> : null;
  return (
    <ScanDialog open={open} onClose={onClose} title={title} hint={hint} onCode={onCode} top={top}>
      {last && <div role="status" className={`rounded-md px-3 py-2 text-[0.8125rem] mb-3 ${last.ok ? "bg-success/10 text-success" : "bg-danger/10 text-danger"}`}>{last.text}</div>}
      {status && <ReportView data={status} />}
    </ScanDialog>
  );
}

/** Counts a stocktake: type counts per line, or scan a batch label to add one to the matching line. */
function StocktakeCounter({ open, onClose, stocktakeId, onSaved }: { open: boolean; onClose: () => void; stocktakeId: number; onSaved: () => void }) {
  const [st, setSt] = useState<any>(null);
  const [counts, setCounts] = useState<Record<number, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const toast = useToast();
  const load = useCallback(() => api<any>(`/inventory/stocktakes/${stocktakeId}`).then((d) => { setSt(d); setCounts(Object.fromEntries(d.lines.map((l: Row) => [l.balance_id, l.counted_qty ?? ""]))); }), [stocktakeId]);
  useEffect(() => { if (open) { setNote(null); load().catch(() => setSt(null)); } }, [open, load]);
  async function onCode(code: string) {
    try {
      const r = await resolveCode(code);
      const batch = r.matches.find((m) => m.type === "BATCH");
      const lines: Row[] = st?.lines ?? [];
      const line = batch ? lines.find((l) => batch.label.startsWith(l.batch_no ?? "\u0000")) : lines.find((l) => r.matches.some((m) => m.type === "ITEM" && m.label.startsWith(l.item_code)));
      if (!line) { setNote(`“${code}” is not on this count`); return; }
      setCounts((c) => ({ ...c, [line.balance_id]: String((Number(c[line.balance_id] || 0)) + 1) }));
      setNote(`+1 ${line.item_code}${line.batch_no ? ` · ${line.batch_no}` : ""}`);
    } catch { setNote("Could not read that code"); }
  }
  async function save() {
    const list = Object.entries(counts).filter(([, v]) => v !== "").map(([id, v]) => ({ balance_id: Number(id), counted_qty: String(v) }));
    try { await api(`/inventory/stocktakes/${stocktakeId}/counts`, { body: { counts: list } }); toast("success", "Counts saved"); onSaved(); onClose(); }
    catch (e) { toast("error", e instanceof ApiError ? e.message : "Could not save"); }
  }
  const [scanOpen, setScanOpen] = useState(false);
  return (
    <Drawer open={open} onClose={onClose} title={`Count ${st?.number ?? ""}`} subtitle="Enter counted quantities. Scan a batch label to add one to its line." width={760}
      footer={<><button className="btn" onClick={onClose}>Close</button><button className="btn btn-primary" onClick={save}>Save counts</button></>}>
      <div className="flex items-center gap-2 mb-3"><ScanButton onClick={() => setScanOpen(true)} label="Scan a label" />{note && <span className="text-[0.75rem] text-muted" role="status">{note}</span>}</div>
      <ScanDialog open={scanOpen} onClose={() => setScanOpen(false)} onCode={(c) => { onCode(c); }} title="Scan to count" hint="Each scan adds one to the matching line. Close when done." />
      {!st ? <div className="skeleton h-24" /> : (
        <div className="panel overflow-x-auto"><table className="dt"><thead><tr><th>Item</th><th>Batch</th><th className="num">System</th><th>Counted</th></tr></thead>
          <tbody>{st.lines.map((l: Row) => (
            <tr key={l.balance_id}><td className="tnum font-medium text-[0.8125rem]">{l.item_code}</td><td className="tnum font-medium text-[0.8125rem]">{l.batch_no ?? "—"}</td><td className="num">{fmtNum(l.system_qty, 3)}</td>
              <td className="!py-1"><input className="input !w-28" type="number" min="0" step="any" aria-label={`Counted ${l.item_code}`} value={counts[l.balance_id] ?? ""} onChange={(e) => setCounts((c) => ({ ...c, [l.balance_id]: e.target.value }))} /></td></tr>))}</tbody></table></div>
      )}
    </Drawer>
  );
}

const CARD_EDGE: Record<string, string> = { success: "var(--brand)", warning: "var(--amber)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--line-strong)" };
const VIEWS: { mode: "cards" | "table" | "board"; Icon: typeof LayoutGrid; label: string }[] = [{ mode: "cards", Icon: LayoutGrid, label: "Cards" }, { mode: "table", Icon: Rows3, label: "Table" }, { mode: "board", Icon: Columns3, label: "Board" }];

function RecordCard({ r, def, onOpen, actions, onAction, compact }: { r: Row; def: NonNullable<Screen["list"]>; onOpen: (r: Row) => void; actions: Action[]; onAction: (a: Action, r: Row) => void; compact?: boolean }) {
  /* Card anatomy: the reference (a mono chip when it is a code) with the status pill beside it, the key figure, the remaining fields as slate tiles, then the
     actions and a footer with the date. The status pill carries the colour; the card itself stays neutral. */
  const title = def.cols[0];
  const badge = def.cols.find((c) => c.kind === "badge" && c.key === "status") ?? def.cols.find((c) => c.kind === "badge" && c !== title);
  const rest = def.cols.filter((c) => c !== title && c !== badge);
  // a record id is a reference, not a figure to headline; and an empty figure is not worth the space
  const metric = rest.find((c) => (c.kind === "money" || c.kind === "number") && !/(^|_)id$/i.test(c.key) && r[c.key] !== null && r[c.key] !== undefined && r[c.key] !== "");
  const when = rest.find((c) => c.kind === "date" || c.kind === "datetime");
  const rows = rest.filter((c) => c !== metric && c !== when && !(/(^|_)id$/i.test(c.key) && (r[c.key] === null || r[c.key] === undefined))).slice(0, compact ? 2 : 4);
  const dated = when && r[when.key] ? when : undefined;
  const raw = String(r[title.key] ?? "");
  const isCode = /^[A-Z0-9#][A-Z0-9/#._-]{2,}$/i.test(raw) && !/\s/.test(raw) && /\d/.test(raw);
  const tone = badge && r[badge.key] ? toneFor(String(r[badge.key])) : "neutral";
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": CARD_EDGE[tone] ?? CARD_EDGE.neutral } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-3 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${raw || r.id}`}>
        <div className="flex items-start justify-between gap-2">
          {isCode ? <span className="tag tag-mono">#{raw}</span> : <span className="ref-title text-[0.9375rem] min-w-0 break-words">{cell(title, r)}</span>}
          {badge && <span className="shrink-0">{cell(badge, r)}</span>}
        </div>
        {metric && (
          <div>
            <span className="label-caps">{metric.label}</span>
            <div className="t-stage mt-0.5 truncate">{cell(metric, r)}</div>
          </div>
        )}
        {rows.length > 0 && (
          <dl className="grid grid-cols-2 gap-2">
            {rows.map((c) => <div key={c.key} className="tile"><dt className="tile-label truncate">{c.label}</dt><dd className="tile-value" style={c.kind === "mono" ? undefined : { fontFamily: "var(--font-sans)", fontWeight: 600 }} title={String(r[c.key] ?? "")}>{cell(c, r)}</dd></div>)}
          </dl>
        )}
      </button>
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {actions.map((a, i) => <button key={`${a.label}-${i}`} className={`btn btn-sm ${a.danger ? "btn-danger" : ""}`} onClick={() => onAction(a, r)}>{a.labelFn ? a.labelFn(r) : a.label}</button>)}
        </div>
      )}
      {dated && (
        <div className="card-foot !mt-auto">
          <span className="t-support truncate">{dated.label} · {cell(dated, r)}</span>
        </div>
      )}
    </article>
  );
}

export function ListScreen({ screen }: { screen: Screen }) {
  const def = screen.list!;
  const { can } = useAuth();
  const toast = useToast();
  const undoable = useUndoable();
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [pending, setPending] = useState<Record<string, string>>({});   // rows whose approve or reject is waiting out the undo window
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const sp = useSearchParams();
  const [status, setStatus] = useState(() => { const st = sp.get("status"); return st && def.status?.options.includes(st) ? st : ""; });   // ?status=SUBMITTED opens the list already filtered
  const [q, setQ] = useState(sp.get("q") ?? "");
  const dq = useDebounced(q, 300);
  const [page, setPage] = useState(1);
  const [view, setView] = useState<{ title: string; data: any } | null>(null);
  const [act, setAct] = useState<{ a: Action; row: Row } | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanAct, setScanAct] = useState<{ a: Action; row: Row } | null>(null);
  const [countRow, setCountRow] = useState<Row | null>(null);
  const [result, setResult] = useState<any>(null);
  const [view_, setViewMode] = useState<"table" | "cards" | "board">(def.view ?? "table");
  const [sort, setSort] = useState<string>("");
  const [shown, setShown] = useState(48);
  const [meta, setMeta] = useState<{ counts?: Record<string, number>; summary?: any } | null>(null);   // tab counts and the figures strip, when the endpoint sends them
  const listKey = def.api.replace(/\W+/g, "_");
  const [dense, setDense] = usePref<boolean>(`dense:${listKey}`, false);
  const [hidden, setHidden] = usePref<string[]>(`cols:${listKey}`, []);
  const [views, setViews] = usePref<SavedView[]>(`views:${listKey}`, []);
  const [totals, setTotals] = useState(false);
  const [selected, setSelected] = useState<Set<number | string>>(new Set());
  const [cursor, setCursor] = useState(-1);          // the keyboard cursor: an index into the rows on this page
  const [peekId, setPeekId] = useState<number | string | null>(null);   // the record open in the drawer, so J and K can step to its neighbours

  const load = useCallback(async (silent: boolean = false) => {
    if (silent !== true) setLoading(true);          // a live refresh swaps rows in place; only user-driven loads show the loading state
    try {
      const params: Record<string, string> = { ...(def.params ?? {}) };
      if (def.paged !== false && !params.page_size && !params.limit) params.page_size = "100";
      if (status && def.status?.param) params[def.status.param] = status;
      if (dq.trim() && def.paged !== false) params.q = dq.trim();          // server-side search: the list holds only the newest 100 rows, so searching must reach the older ones
      const res = await api<any>(def.api, { params });
      setMeta(res && !Array.isArray(res) && (res.counts || res.summary) ? { counts: res.counts, summary: res.summary } : null);
      setRows(asRows(res).map((r, i) => (r.id === undefined ? { id: i + 1, ...r } : r)));
      setError(null);
    } catch (e) { setError(e instanceof ApiError ? e.message : "Failed to load"); }
    finally { setLoading(false); }
  }, [def, status, dq]);
  const liveRev = useLiveRevision(["orders", "inventory", "purchase", "production", "quality", "accounts"]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (liveRev > 0) load(true); }, [liveRev]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setPage(1); setShown(48); setSelected(new Set()); setCursor(-1); }, [q, status]);

  const live = useMemo(() => (Object.keys(pending).length ? rows.map((x) => (pending[x.id] ? { ...x, status: pending[x.id] } : x)) : rows), [rows, pending]);
  const filtered = useMemo(() => {
    let r = live;
    if (status && !def.status?.param) r = r.filter((x) => x.status === status);
    if (q.trim()) { const t = q.toLowerCase(); r = r.filter((x) => JSON.stringify(x).toLowerCase().includes(t)); }
    if (sort) {
      const key = sort.replace(/^-/, ""), dir = sort.startsWith("-") ? -1 : 1;
      const num = (v: any) => (v === null || v === undefined || v === "" ? null : typeof v === "number" ? v : /^-?\d+(\.\d+)?$/.test(String(v)) ? Number(v) : null);
      r = [...r].sort((a, b) => {
        const x = a[key], y = b[key];
        if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
        const nx = num(x), ny = num(y);
        return (nx !== null && ny !== null ? nx - ny : String(x).localeCompare(String(y), "en-IN", { numeric: true })) * dir;
      });
    }
    return r;
  }, [live, q, status, def.status?.param, sort]);
  const pageSize = 50;
  const pageRows = filtered.slice((page - 1) * pageSize, page * pageSize);

  async function openHref(href: string, label: string) {
    if (!href.startsWith("/api")) { router.push(href); return; }
    if (/\/(pdf|coa|challan)$/.test(href) || href.startsWith("/api/labels/")) { window.open(href, "_blank", "noopener"); return; }
    try { setView({ title: label, data: await api(href.replace(/^\/api/, "")) }); } catch (e) { toast("error", e instanceof ApiError ? e.message : "Could not open"); }
  }
  async function run(a: Action, row: Row, values?: Row) {
    if (/^(Approve|Reject)\b/i.test(a.label) && a.path) {
      /* a decision: the row shows "Approving" or "Rejecting" at once and the server is told after four seconds, unless Undo is pressed */
      const approve = /^Approve/i.test(a.label);
      const clear = () => setPending((p) => { const n = { ...p }; delete n[row.id]; return n; });
      const name = String(row.number ?? row.name ?? row.title ?? `#${row.id}`);
      setAct(null);
      undoable({ message: `${approve ? "Approved" : "Rejected"} ${name}`, apply: () => setPending((p) => ({ ...p, [row.id]: approve ? "APPROVING" : "REJECTING" })), revert: clear,
        commit: async () => {
          const body = a.body ? a.body(row, values ?? {}) : { ...(a.versioned ? { expected_version: row.version } : {}), ...(values ?? {}) };
          try { await api<any>(a.path!(row), { method: a.method ?? "POST", body: body ?? {}, headers: a.idempotent ? { "Idempotency-Key": crypto.randomUUID() } : undefined }); } finally { clear(); load(); }
        } });
      return;
    }
    setBusy(true);
    try {
      const body = a.body ? a.body(row, values ?? {}) : { ...(a.versioned ? { expected_version: row.version } : {}), ...(values ?? {}) };
      const multipart = !!a.fields && hasFile(a.fields);
      const res = await api<any>(a.path!(row), { method: a.method ?? "POST", ...(multipart ? { form: toForm(body) } : { body: body ?? {} }), headers: a.idempotent ? { "Idempotency-Key": crypto.randomUUID() } : undefined });
      toast("success", `${a.labelFn ? a.labelFn(row) : a.label} done`);
      setAct(null); load();
      if (res && typeof res === "object" && ["award", "convert", "plan", "manifest", "decide", "complete", "dispatch"].some((w) => a.label.toLowerCase().includes(w))) setView({ title: `${a.label} — result`, data: res });
    } catch (e) { toast("error", e instanceof ApiError ? e.message : "Action failed"); } finally { setBusy(false); }
  }
  async function openDetail(r: Row) {
    setView({ title: "Loading…", data: null });
    setPeekId(r.id);
    try { setView({ title: String(r.number ?? r.name ?? r.title ?? r.code ?? (r.item_code ? `${r.item_code}${r.version_no ? ` · v${r.version_no}` : ""}` : `#${r.id}`)), data: def.detail ? await api(def.detail(r)) : r }); }
    catch (e) { setView(null); toast("error", e instanceof ApiError ? e.message : "Could not load"); }
  }
  async function exec(a: Action, r: Row) {
    setBusy(true);
    try { const msg = await a.exec!(r); toast("success", msg || `${a.label} done`); load(); } catch (e) { toast("error", e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Action failed"); } finally { setBusy(false); }
  }
  const trigger = (a: Action, r: Row) => (a.href ? openHref(a.href(r), a.label) : a.exec ? exec(a, r) : a.scanSession ? setScanAct({ a, row: r }) : a.counter ? setCountRow(r) : a.fields || a.confirm ? setAct({ a, row: r }) : run(a, r));
  const boardColumns = useMemo(() => { const known = def.status?.options ?? []; const extra = Array.from(new Set(filtered.map((r) => r.status))).filter((x) => x && !known.includes(x)); return [...known, ...extra]; }, [def.status?.options, filtered]);
  const openId = sp.get("open");
  const [opened, setOpened] = useState<string | null>(null);
  useEffect(() => {      // deep link from a barcode scan: /m/…?open=<id> opens that record's detail once the list has loaded
    if (!openId || opened === openId || loading) return;
    const hit = rows.find((r) => String(r.id) === openId);
    if (hit) { setOpened(openId); openDetail(hit); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId, rows, loading]);
  const visible = (r: Row) => (def.actions ?? []).filter((a) => (!a.perm || can(a.perm)) && (!a.when || a.when(r)));
  const columns: TCol<Row & { id: number }>[] = def.cols.filter((c) => !hidden.includes(c.key)).filter((c) => rows.length === 0 || c.kind === "badge" || rows.some((r) => r[c.key] !== null && r[c.key] !== undefined && r[c.key] !== "")).map((c) => ({ key: c.key, label: c.label, sortKey: c.key, render: (r) => cell(c, r), align: c.kind === "money" || c.kind === "number" ? "right" : undefined }));

  /* ---- table tools: totals, CSV, saved views, selection, keyboard ---- */
  const moneyCols = columns.filter((c) => def.cols.find((d) => d.key === c.key)?.kind === "money");
  const csvRows = (list: Row[]) => list.map((r) => def.cols.filter((c) => !hidden.includes(c.key)).map((c) => (r[c.key] ?? "") as string | number));
  const csvHead = def.cols.filter((c) => !hidden.includes(c.key)).map((c) => c.label);
  const exportView = () => downloadCsv(`${listKey}.csv`, csvHead, csvRows(filtered));
  const exportSelected = () => downloadCsv(`${listKey}-selected.csv`, csvHead, csvRows(filtered.filter((r) => selected.has(r.id))));
  const copyNumbers = () => { const t = filtered.filter((r) => selected.has(r.id)).map((r) => String(r.number ?? r.name ?? r.code ?? r.id)).join("\n"); navigator.clipboard?.writeText(t).then(() => toast("success", `${selected.size} copied`)).catch(() => toast("error", "Could not copy")); };
  const currentView: SavedView = { name: "", status, q, sort, hidden, dense };
  const applyView = (v: SavedView) => { setStatus(v.status); setQ(v.q); setSort(v.sort); setHidden(v.hidden); setDense(v.dense); setPage(1); };
  const toggleOne = (id: number | string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleAll = (ids: (number | string)[], on: boolean) => setSelected((s) => { const n = new Set(s); ids.forEach((i) => (on ? n.add(i) : n.delete(i))); return n; });
  const peekIdx = peekId === null ? -1 : filtered.findIndex((r) => r.id === peekId);
  const step = (d: number) => { const r = filtered[peekIdx + d]; if (r) openDetail(r); };
  useEffect(() => {
    /* J and K move the cursor down and up the rows, Enter opens the row, X selects it; with a record open, J/K or the arrows step to the next record */
    const k = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || t?.closest("input, textarea, select, [contenteditable=true]")) return;
      const key = e.key.toLowerCase();
      if (view) { if (key === "j" || e.key === "ArrowDown") { e.preventDefault(); step(1); } else if (key === "k" || e.key === "ArrowUp") { e.preventDefault(); step(-1); } return; }
      if (view_ !== "table" || t?.closest("[role=dialog]") || act || result || scanAct || countRow) return;
      if (key === "j") { e.preventDefault(); setCursor((c) => Math.min(c + 1, pageRows.length - 1)); }
      else if (key === "k") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
      else if (key === "x" && cursor >= 0 && pageRows[cursor]) { e.preventDefault(); toggleOne(pageRows[cursor].id); }
      else if (e.key === "Enter" && cursor >= 0 && pageRows[cursor] && document.activeElement === document.body) { e.preventDefault(); openDetail(pageRows[cursor]); }
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  });
  useEffect(() => { if (cursor >= 0) document.querySelector('tr[data-active="true"]')?.scrollIntoView({ block: "nearest" }); }, [cursor]);
  const totalsFooter = totals && moneyCols.length > 0 ? (
    <tfoot><tr className="dt-total">
      {columns.map((c, i) => <td key={c.key} className={c.align === "right" ? "num" : ""}>{i === 0 ? `Total of ${filtered.length} rows` : moneyCols.some((m) => m.key === c.key) ? fmtRupees(filtered.reduce((a, r) => a + (Number(r[c.key]) || 0), 0)) : ""}</td>)}
      {def.actions && <td />}
    </tr></tfoot>
  ) : null;

  const rfq = def.layout === "rfq";
  function exportBids() {
    const url = URL.createObjectURL(new Blob([bidsCsv(filtered)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `rfq-bids-${new Date().toISOString().slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(url);
  }
  const poHref = rfq ? "/m/procurement/orders" : undefined;

  return (
    <>
      {rfq && <RfqSummary summary={loading && !meta ? null : meta?.summary} />}
      {def.layout === "pr" && <PrSummary summary={loading && !meta ? null : meta?.summary} />}
      {def.layout === "orders" && <OrdersSummary summary={loading && !meta ? null : meta?.summary} />}
      {def.layout === "po" && <PoSummary summary={loading && !meta ? null : meta?.summary} />}
      {def.layout && BOARDS[def.layout] && (() => { const B = BOARDS[def.layout!]; return <B.Summary summary={loading && !meta ? null : meta?.summary} />; })()}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><input className={`input !pl-8 ${def.layout ? "w-44" : "w-64"}`} placeholder="Search this list…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" /></div>
        {def.status && (
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
            <button className={`btn btn-sm ${status === "" ? "btn-primary" : ""}`} onClick={() => setStatus("")}>All{meta?.counts?.ALL != null && <span className="ml-1.5 tnum opacity-80">{meta.counts.ALL}</span>}</button>
            {def.status.options.map((o) => <button key={o} className={`btn btn-sm ${status === o ? "btn-primary" : ""}`} onClick={() => setStatus(o)}>{titleCase(o)}{meta?.counts && <span className="ml-1.5 tnum opacity-80">{meta.counts[o] ?? 0}</span>}</button>)}
          </div>
        )}
        <div className="flex-1" />
        <div className="flex rounded-md border border-line overflow-hidden" role="group" aria-label="View">
          {VIEWS.filter((v) => v.mode !== "board" || def.status).map(({ mode, Icon, label }) => (
            <button key={mode} type="button" aria-pressed={view_ === mode} title={label} onClick={() => setViewMode(mode)}
              className={`h-8 px-2.5 inline-flex items-center gap-1.5 text-[0.75rem] ${view_ === mode ? "bg-fg text-bg" : "hover:bg-surface-2"}`}><Icon size={14} />{def.layout ? <span className="sr-only">{label}</span> : label}</button>
          ))}
        </div>
        {view_ === "table" && <TableTools dense={dense} onDense={setDense} cols={def.cols.map((c) => ({ key: c.key, label: c.label }))} hidden={hidden} onHidden={setHidden} views={views} onViews={setViews} current={currentView} onApply={applyView}
          totals={totals} onTotals={setTotals} canTotal={moneyCols.length > 0} onExport={exportView} exportCount={filtered.length} />}
        {rfq && <button type="button" className="btn btn-sm gap-1.5" onClick={exportBids} disabled={filtered.length === 0}>Export bids</button>}
        {screen.create && can(screen.create.perm) && <CreateButton create={screen.create} onDone={load} onResult={(r) => setResult({ title: screen.create!.title ?? screen.label, data: r })} />}
      </div>
      {error ? <div className="panel p-4 text-danger text-[0.8125rem]" role="alert">{error}</div> : view_ === "table" ? (
        <DataTable columns={columns} rows={pageRows as (Row & { id: number })[]} loading={loading} sort={sort} onSort={(v) => { setSort(v); setPage(1); }} page={page} pageSize={pageSize} total={filtered.length} onPage={setPage}
          dense={dense} selected={selected} onToggle={toggleOne} onToggleAll={toggleAll} activeId={cursor >= 0 ? pageRows[cursor]?.id : null} footer={totalsFooter}
          onRowClick={openDetail} empty={<EmptyState title="Nothing here yet" hint={screen.create ? `Use “${screen.create.label}” to add the first one.` : "Records appear here as work happens in other modules."} />}
          rowActions={def.actions ? (r) => (
            <RowActions subject={r.number ?? r.name ?? undefined}
              actions={visible(r).map((a, i) => ({ key: `${a.label}-${i}`, label: a.labelFn ? a.labelFn(r) : a.label, danger: a.danger, link: !!a.href, secondary: a.secondary, prefer: a.label === "Award" || a.label === "Send", run: () => trigger(a, r) }))} />
          ) : undefined} />
      ) : loading && rows.length === 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="skeleton h-36 !rounded-lg" />)}</div>
      ) : filtered.length === 0 ? (
        <div className="panel"><EmptyState title="Nothing here yet" hint={screen.create ? `Use “${screen.create.label}” to add the first one.` : "Records appear here as work happens in other modules."} /></div>
      ) : view_ === "board" ? (
        <div className="flex gap-3 overflow-x-auto pb-2 items-start">
          {boardColumns.map((col) => (
            <section key={col} className="w-[300px] shrink-0 rounded-2xl bg-inset border border-line p-2.5" aria-label={col}>
              <h3 className="px-1.5 py-1 text-[0.75rem] font-semibold text-muted flex items-center justify-between">{titleCase(col)}<span className="tnum">{filtered.filter((r) => r.status === col).length}</span></h3>
              <div className="space-y-2 max-h-[calc(100vh-290px)] overflow-y-auto">
                {filtered.filter((r) => r.status === col).map((r) => <RecordCard key={r.id} r={r} def={def} compact onOpen={openDetail} actions={visible(r)} onAction={trigger} />)}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.slice(0, shown).map((r) => rfq ? <RfqCard key={r.id} r={r} onOpen={openDetail} actions={visible(r)} onAction={trigger} poHref={poHref} /> : def.layout === "pr" ? <PrCard key={r.id} r={r} onOpen={openDetail} actions={visible(r)} onAction={trigger} /> : def.layout === "orders" ? <OrderCard key={r.id} r={r} onOpen={openDetail} actions={visible(r)} onAction={trigger} /> : def.layout === "po" ? <PoCard key={r.id} r={r} onOpen={openDetail} actions={visible(r)} onAction={trigger} /> : def.layout && BOARDS[def.layout] ? (() => { const B = BOARDS[def.layout!]; return <B.Card key={r.id} r={r} onOpen={openDetail} actions={visible(r)} onAction={trigger} />; })() : <RecordCard key={r.id} r={r} def={def} onOpen={openDetail} actions={visible(r)} onAction={trigger} />)}
          </div>
          {filtered.length > shown && <div className="text-center mt-4"><button className="btn" onClick={() => setShown((n) => n + 48)}>Show more ({filtered.length - shown} left)</button></div>}
        </>
      )}

      <Drawer open={!!view} onClose={() => { setView(null); setPeekId(null); }} title={view?.title ?? ""} width={820}
        headerExtra={peekIdx >= 0 && filtered.length > 1 ? (
          <div className="flex items-center gap-1.5" role="group" aria-label="Step through records">
            <span className="t-support tabular-nums mr-1" aria-live="polite">{peekIdx + 1} of {filtered.length}</span>
            <button type="button" className="ctl" onClick={() => step(-1)} disabled={peekIdx <= 0} aria-label="Previous record (K)" title="Previous (K)"><ChevronUp size={16} strokeWidth={1.5} /></button>
            <button type="button" className="ctl" onClick={() => step(1)} disabled={peekIdx >= filtered.length - 1} aria-label="Next record (J)" title="Next (J)"><ChevronDown size={16} strokeWidth={1.5} /></button>
          </div>
        ) : undefined}>{view?.data ? <RecordView data={view.data} /> : <div className="skeleton h-24" />}</Drawer>
      <BulkBar count={selected.size} onExport={exportSelected} onCopy={copyNumbers} onClear={() => setSelected(new Set())} />
      <Drawer open={!!result} onClose={() => setResult(null)} title={result?.title ?? ""} width={820}>{result && <ReportView data={result.data} />}</Drawer>
      <FormDrawer open={!!act?.a.fields} title={act?.a.label ?? ""} subtitle={act ? String(act.row.number ?? act.row.name ?? `#${act.row.id}`) : undefined} fields={act?.a.fields ?? []} submitLabel={act?.a.label ?? "Run"}
        ctx={act?.row} onClose={() => setAct(null)} onSubmit={async (_body, raw) => { const { body } = buildBody(act!.a.fields!, raw); await run(act!.a, act!.row, body); }} />
      {scanAct && <ScanSession open onClose={() => { setScanAct(null); load(); }} title={scanAct.a.scanSession!.title} hint={scanAct.a.scanSession!.hint} statusPath={scanAct.a.scanSession!.status(scanAct.row)} postPath={scanAct.a.scanSession!.post(scanAct.row)} />}
      {countRow && <StocktakeCounter open onClose={() => setCountRow(null)} stocktakeId={countRow.id} onSaved={load} />}
      <ConfirmDialog open={!!act && !act.a.fields} title={act?.a.label ?? ""} body={act?.a.confirm ?? ""} confirmLabel={act?.a.label ?? "Confirm"} danger={act?.a.danger} busy={busy}
        onConfirm={() => act && run(act.a, act.row)} onCancel={() => setAct(null)} />
    </>
  );
}

/* ------------------------------------------------------------------ reports */
export function ReportScreen({ screen, initial }: { screen: Screen; initial?: Record<string, string> }) {
  const def = screen.report!;
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries((def.params ?? []).map((p) => [p.name, initial?.[p.name] ?? defaultParam(p)])));
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const missing = (def.params ?? []).some((p) => (p.required || def.api.includes(`{${p.name}}`)) && !values[p.name]);
  const run = useCallback(async () => {
    if (missing) return;
    setLoading(true);
    try {
      let path = def.api;
      const query: Record<string, string> = {};
      for (const p of def.params ?? []) { if (path.includes(`{${p.name}}`)) path = path.replace(`{${p.name}}`, encodeURIComponent(values[p.name])); else if (values[p.name]) query[p.name] = values[p.name]; }
      setData(await api(path, { params: query })); setError(null);
    } catch (e) { setData(null); setError(e instanceof ApiError ? e.message : "Failed to load"); } finally { setLoading(false); }
  }, [def.api, def.params, values, missing]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { run(); }, []);
  return (
    <>
      {(def.params?.length ?? 0) > 0 && (
        <form className="flex flex-wrap items-end gap-3 mb-4" onSubmit={(e) => { e.preventDefault(); run(); }}>
          {def.params!.map((p: Param) => (
            <div key={p.name}><label className="field-label" htmlFor={p.name}>{p.label}</label>
              {p.type === "select" ? <select id={p.name} className="input" value={values[p.name]} onChange={(e) => setValues((s) => ({ ...s, [p.name]: e.target.value }))}><option value="">Select…</option>{p.options!.map((o) => <option key={o}>{o}</option>)}</select>
                : <input id={p.name} className="input" type={p.type === "date" ? "date" : "text"} value={values[p.name]} onChange={(e) => setValues((s) => ({ ...s, [p.name]: e.target.value }))} />}
            </div>
          ))}
          <button className="btn btn-primary" disabled={loading || missing}>{loading ? "Loading…" : "Run report"}</button>
        </form>
      )}
      {error ? <div className="panel p-4 text-danger text-[0.8125rem]" role="alert">{error}</div> : loading && !data ? <div className="skeleton h-40" /> : data ? (def.layout === "wastage" ? <WastageView data={data} /> : def.layout === "dashboard" ? <ModuleDashboard data={data} /> : <ReportView data={data} />) : missing ? <EmptyState title="Enter the details above" hint="Then run the report." /> : null}
    </>
  );
}

export function CreateOnlyScreen({ screen }: { screen: Screen }) {
  const [result, setResult] = useState<any>(null);
  return (
    <>
      <div className="mb-4"><CreateButton create={screen.create!} onDone={() => {}} onResult={setResult} /></div>
      {result ? <ReportView data={result} /> : <EmptyState title="Run it to see the result" hint="Results are computed from live records." />}
    </>
  );
}
