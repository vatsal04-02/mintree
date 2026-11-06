"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { LayoutGrid, Columns3, Plus, Rows3, Search, Trash2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, titleCase } from "@/lib/format";
import { defaultParam, toneFor, type Action, type Col, type Create, type Field, type Param, type RefKey, type Row, type Screen } from "@/lib/modules";
import { DataTable, type Col as TCol } from "./DataTable";
import { ScanButton, ScanDialog, resolveCode } from "./Scanner";
import { Badge, ConfirmDialog, Drawer, EmptyState, useToast } from "./ui";

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
  if (!poId) return <p className="text-muted text-[12.5px]">Choose a purchase order to see its lines.</p>;
  if (err) return <p className="text-danger text-[12.5px]">{err}</p>;
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
        <tr key={l.shipment_line_id}><td className="!max-w-[220px]">{l.item_code} {l.name}</td><td className="font-mono text-[12px]">{l.batch_no ?? "—"}</td><td className="num">{fmtNum(l.shipped, 3)}</td><td className="num">{fmtNum(l.returnable, 3)}</td>
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
                <label className="flex items-center gap-2 h-8 text-[13px] cursor-pointer"><Input f={f} id={f.name} value={vals[f.name]} onChange={(v) => setVals((s) => ({ ...s, [f.name]: v }))} />{f.label}</label>
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
              {err ? <p className="text-danger text-[11.5px] mt-1" role="alert">{err}</p> : f.help ? <p className="text-muted text-[11.5px] mt-1">{f.help}</p> : null}
            </div>
          );
        })}
      </form>
    </Drawer>
  );
}

/* ------------------------------------------------------------------ read-only JSON view (dashboards, reports, record details) */
const MONEYISH = /(amount|total|value|revenue|cost|balance|price|profit|spend|payable|receivable|cogs|outstanding|debit|credit|charge|fees|freight|budget|margin$|taxable|cgst|sgst|igst|tax$|payment|paid)/i;
function show(k: string, v: any): React.ReactNode {
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  if (typeof v === "boolean") return <Badge tone={v ? "success" : "neutral"}>{v ? "Yes" : "No"}</Badge>;
  if (typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) && !/(^|_)(id|no|number|code|awb|ref)$/i.test(k) && v.length < 18)) {
    if (/pct|percent|rate$/i.test(k)) return `${fmtNum(v, 2)}%`;
    if (MONEYISH.test(k) && !/(qty|count|days)/i.test(k)) return fmtMoney(v);
    return fmtNum(v, 3);
  }
  if (typeof v === "string") {
    if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return fmtDateTime(v);
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return fmtDate(v);
    if (/^[A-Z_]{3,}$/.test(v)) return <Badge tone={toneFor(v)}>{titleCase(v)}</Badge>;
  }
  return String(v);
}
export function ReportView({ data, depth = 0 }: { data: any; depth?: number }) {
  if (data === null || data === undefined) return null;
  if (Array.isArray(data)) {
    if (data.length === 0) return <p className="text-muted text-[12.5px]">None.</p>;
    if (typeof data[0] !== "object" || data[0] === null) return <p className="text-[13px]">{data.map(String).join(", ")}</p>;
    const keys = Array.from(new Set(data.slice(0, 20).flatMap((r: Row) => Object.keys(r)))).filter((k) => typeof data.find((r: Row) => r[k] !== undefined && r[k] !== null)?.[k] !== "object").slice(0, 10);
    return (
      <div className="panel overflow-auto max-h-[420px]"><table className="dt"><thead><tr>{keys.map((k) => <th key={k} className={/(amount|total|value|qty|price|cost|balance|debit|credit|pct)/i.test(k) ? "num" : ""}>{titleCase(k)}</th>)}</tr></thead>
        <tbody>{data.slice(0, 200).map((r: Row, i: number) => <tr key={i}>{keys.map((k) => <td key={k} className={/(amount|total|value|qty|price|cost|balance|debit|credit|pct)/i.test(k) ? "num" : ""}>{show(k, r[k])}</td>)}</tr>)}</tbody></table></div>
    );
  }
  if (typeof data !== "object") return <p className="text-[13px]">{String(data)}</p>;
  const scalars = Object.entries(data).filter(([, v]) => v === null || typeof v !== "object");
  const nested = Object.entries(data).filter(([, v]) => v !== null && typeof v === "object");
  return (
    <div className={depth ? "space-y-3" : "space-y-5"}>
      {scalars.length > 0 && (
        <dl className={`grid gap-2.5 ${depth === 0 ? "grid-cols-2 md:grid-cols-4" : "grid-cols-2 md:grid-cols-3"}`}>
          {scalars.map(([k, v]) => (
            <div key={k} className="panel px-3 py-2.5 min-w-0">
              <dt className="text-[11.5px] text-muted truncate">{titleCase(k)}</dt>
              <dd className="text-[15px] font-semibold mt-0.5 tabular-nums break-words">{show(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {nested.map(([k, v]) => (
        <section key={k}>
          <h3 className="text-[12.5px] font-semibold uppercase tracking-[0.04em] text-muted mb-1.5">{titleCase(k)}</h3>
          <ReportView data={v} depth={depth + 1} />
        </section>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ lists */
function cell(c: Col, r: Row): React.ReactNode {
  const v = r[c.key];
  if (c.kind === "bool") return <Badge tone={v ? "warning" : "neutral"}>{v ? "Yes" : "No"}</Badge>;
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  switch (c.kind) {
    case "mono": return <span className="font-mono text-[12px]">{v}</span>;
    case "number": return fmtNum(v, 3);
    case "money": return fmtMoney(v);
    case "date": return fmtDate(v);
    case "datetime": return fmtDateTime(v);
    case "badge": return <Badge tone={toneFor(String(v))}>{titleCase(String(v))}</Badge>;
    default: return String(v);
  }
}

const hasFile = (fields: Field[]) => fields.some((f) => f.type === "file");
function toForm(body: Row): FormData { const fd = new FormData(); for (const [k, v] of Object.entries(body)) if (v !== undefined && v !== null) fd.append(k, v instanceof File ? v : String(v)); return fd; }

export function CreateButton({ create, onDone, onResult }: { create: Create; onDone: () => void; onResult?: (r: any) => void }) {
  const [open, setOpen] = useState(false);
  const toast = useToast();
  return (
    <>
      <button className="btn btn-primary" onClick={() => setOpen(true)}><Plus size={14} />{create.label}</button>
      <FormDrawer open={open} title={create.title ?? create.label} fields={create.fields} submitLabel={create.title ? "Run" : "Create"} onClose={() => setOpen(false)}
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
  const top = postPath.includes("/orders/") ? <label className="text-[12px] flex items-center gap-2 mb-3">Quantity per scan<input className="input !w-24" type="number" min="0" step="any" value={qty} onChange={(e) => setQty(e.target.value)} /></label> : null;
  return (
    <ScanDialog open={open} onClose={onClose} title={title} hint={hint} onCode={onCode} top={top}>
      {last && <div role="status" className={`rounded-md px-3 py-2 text-[13px] mb-3 ${last.ok ? "bg-success/10 text-success" : "bg-danger/10 text-danger"}`}>{last.text}</div>}
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
      <div className="flex items-center gap-2 mb-3"><ScanButton onClick={() => setScanOpen(true)} label="Scan a label" />{note && <span className="text-[12.5px] text-muted" role="status">{note}</span>}</div>
      <ScanDialog open={scanOpen} onClose={() => setScanOpen(false)} onCode={(c) => { onCode(c); }} title="Scan to count" hint="Each scan adds one to the matching line. Close when done." />
      {!st ? <div className="skeleton h-24" /> : (
        <div className="panel overflow-x-auto"><table className="dt"><thead><tr><th>Item</th><th>Batch</th><th className="num">System</th><th>Counted</th></tr></thead>
          <tbody>{st.lines.map((l: Row) => (
            <tr key={l.balance_id}><td className="font-mono text-[12px]">{l.item_code}</td><td className="font-mono text-[12px]">{l.batch_no ?? "—"}</td><td className="num">{fmtNum(l.system_qty, 3)}</td>
              <td className="!py-1"><input className="input !w-28" type="number" min="0" step="any" aria-label={`Counted ${l.item_code}`} value={counts[l.balance_id] ?? ""} onChange={(e) => setCounts((c) => ({ ...c, [l.balance_id]: e.target.value }))} /></td></tr>))}</tbody></table></div>
      )}
    </Drawer>
  );
}

const VIEWS: { mode: "cards" | "table" | "board"; Icon: typeof LayoutGrid; label: string }[] = [{ mode: "cards", Icon: LayoutGrid, label: "Cards" }, { mode: "table", Icon: Rows3, label: "Table" }, { mode: "board", Icon: Columns3, label: "Board" }];

function RecordCard({ r, def, onOpen, actions, onAction, compact }: { r: Row; def: NonNullable<Screen["list"]>; onOpen: (r: Row) => void; actions: Action[]; onAction: (a: Action, r: Row) => void; compact?: boolean }) {
  const title = def.cols[0];
  const badge = def.cols.find((c) => c.kind === "badge" && c.key === "status") ?? def.cols.find((c) => c.kind === "badge" && c !== title);
  const fields = def.cols.filter((c) => c !== title && c !== badge).slice(0, compact ? 3 : 6);
  return (
    <article className="panel p-3.5 flex flex-col gap-2.5 hover:border-fg/30 transition-colors">
      <button type="button" className="text-left" onClick={() => onOpen(r)} aria-label={`Open ${String(r[title.key] ?? r.id)}`}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 font-semibold text-[14px] leading-5 break-words">{cell(title, r)}</div>
          {badge && <div className="shrink-0">{cell(badge, r)}</div>}
        </div>
        <dl className={`mt-2 grid gap-x-3 gap-y-1.5 ${compact ? "grid-cols-1" : "grid-cols-2"}`}>
          {fields.map((c) => (
            <div key={c.key} className="min-w-0"><dt className="text-[10.5px] uppercase tracking-[0.05em] text-muted">{c.label}</dt><dd className="text-[12.5px] truncate tabular-nums">{cell(c, r)}</dd></div>
          ))}
        </dl>
      </button>
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-2 mt-auto border-t border-line">
          {actions.map((a, i) => <button key={`${a.label}-${i}`} className={`btn btn-sm ${a.danger ? "btn-danger" : ""}`} onClick={() => onAction(a, r)}>{a.labelFn ? a.labelFn(r) : a.label}</button>)}
        </div>
      )}
    </article>
  );
}

export function ListScreen({ screen }: { screen: Screen }) {
  const def = screen.list!;
  const { can } = useAuth();
  const toast = useToast();
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const sp = useSearchParams();
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [page, setPage] = useState(1);
  const [view, setView] = useState<{ title: string; data: any } | null>(null);
  const [act, setAct] = useState<{ a: Action; row: Row } | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanAct, setScanAct] = useState<{ a: Action; row: Row } | null>(null);
  const [countRow, setCountRow] = useState<Row | null>(null);
  const [result, setResult] = useState<any>(null);
  const [view_, setViewMode] = useState<"table" | "cards" | "board">(def.view ?? "cards");
  const [shown, setShown] = useState(48);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { ...(def.params ?? {}) };
      if (def.paged !== false && !params.page_size && !params.limit) params.page_size = "100";
      if (status && def.status?.param) params[def.status.param] = status;
      setRows(asRows(await api<any>(def.api, { params })).map((r, i) => (r.id === undefined ? { id: i + 1, ...r } : r)));
      setError(null);
    } catch (e) { setError(e instanceof ApiError ? e.message : "Failed to load"); }
    finally { setLoading(false); }
  }, [def, status]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); setShown(48); }, [q, status]);

  const filtered = useMemo(() => {
    let r = rows;
    if (status && !def.status?.param) r = r.filter((x) => x.status === status);
    if (q.trim()) { const t = q.toLowerCase(); r = r.filter((x) => JSON.stringify(x).toLowerCase().includes(t)); }
    return r;
  }, [rows, q, status, def.status?.param]);
  const pageSize = 25;
  const pageRows = filtered.slice((page - 1) * pageSize, page * pageSize);

  async function openHref(href: string, label: string) {
    if (!href.startsWith("/api")) { router.push(href); return; }
    if (/\/(pdf|coa)$/.test(href) || href.startsWith("/api/labels/")) { window.open(href, "_blank", "noopener"); return; }
    try { setView({ title: label, data: await api(href.replace(/^\/api/, "")) }); } catch (e) { toast("error", e instanceof ApiError ? e.message : "Could not open"); }
  }
  async function run(a: Action, row: Row, values?: Row) {
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
    try { setView({ title: String(r.number ?? r.name ?? r.title ?? r.code ?? `#${r.id}`), data: def.detail ? await api(def.detail(r)) : r }); }
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
  const columns: TCol<Row & { id: number }>[] = def.cols.map((c) => ({ key: c.key, label: c.label, render: (r) => cell(c, r), align: c.kind === "money" || c.kind === "number" ? "right" : undefined }));

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><input className="input !pl-8 w-64" placeholder="Search this list…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" /></div>
        {def.status && (
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
            <button className={`btn btn-sm ${status === "" ? "btn-primary" : ""}`} onClick={() => setStatus("")}>All</button>
            {def.status.options.map((o) => <button key={o} className={`btn btn-sm ${status === o ? "btn-primary" : ""}`} onClick={() => setStatus(o)}>{titleCase(o)}</button>)}
          </div>
        )}
        <div className="flex-1" />
        <div className="flex rounded-md border border-line overflow-hidden" role="group" aria-label="View">
          {VIEWS.filter((v) => v.mode !== "board" || def.status).map(({ mode, Icon, label }) => (
            <button key={mode} type="button" aria-pressed={view_ === mode} title={label} onClick={() => setViewMode(mode)}
              className={`h-8 px-2.5 inline-flex items-center gap-1.5 text-[12px] ${view_ === mode ? "bg-fg text-bg" : "hover:bg-surface-2"}`}><Icon size={14} />{label}</button>
          ))}
        </div>
        {screen.create && can(screen.create.perm) && <CreateButton create={screen.create} onDone={load} onResult={(r) => setResult({ title: screen.create!.title ?? screen.label, data: r })} />}
      </div>
      {error ? <div className="panel p-4 text-danger text-[13px]" role="alert">{error}</div> : view_ === "table" ? (
        <DataTable columns={columns} rows={pageRows as (Row & { id: number })[]} loading={loading} page={page} pageSize={pageSize} total={filtered.length} onPage={setPage}
          onRowClick={openDetail} empty={<EmptyState title="Nothing here yet" hint={screen.create ? `Use “${screen.create.label}” to add the first one.` : "Records appear here as work happens in other modules."} />}
          rowActions={def.actions ? (r) => (
            <div className="flex justify-end gap-1">
              {visible(r).map((a, i) => (
                <button key={`${a.label}-${i}`} className={`btn btn-sm ${a.danger ? "btn-danger" : ""}`} onClick={() => trigger(a, r)}>{a.labelFn ? a.labelFn(r) : a.label}</button>
              ))}
            </div>
          ) : undefined} />
      ) : loading && rows.length === 0 ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="skeleton h-36 !rounded-lg" />)}</div>
      ) : filtered.length === 0 ? (
        <div className="panel"><EmptyState title="Nothing here yet" hint={screen.create ? `Use “${screen.create.label}” to add the first one.` : "Records appear here as work happens in other modules."} /></div>
      ) : view_ === "board" ? (
        <div className="flex gap-3 overflow-x-auto pb-2 items-start">
          {boardColumns.map((col) => (
            <section key={col} className="w-[290px] shrink-0 rounded-lg bg-surface-2 p-2" aria-label={col}>
              <h3 className="px-1.5 py-1 text-[11.5px] font-semibold uppercase tracking-[0.05em] text-muted flex items-center justify-between">{titleCase(col)}<span className="tnum">{filtered.filter((r) => r.status === col).length}</span></h3>
              <div className="space-y-2 max-h-[calc(100vh-290px)] overflow-y-auto">
                {filtered.filter((r) => r.status === col).map((r) => <RecordCard key={r.id} r={r} def={def} compact onOpen={openDetail} actions={visible(r)} onAction={trigger} />)}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.slice(0, shown).map((r) => <RecordCard key={r.id} r={r} def={def} onOpen={openDetail} actions={visible(r)} onAction={trigger} />)}
          </div>
          {filtered.length > shown && <div className="text-center mt-4"><button className="btn" onClick={() => setShown((n) => n + 48)}>Show more ({filtered.length - shown} left)</button></div>}
        </>
      )}

      <Drawer open={!!view} onClose={() => setView(null)} title={view?.title ?? ""} width={820}>{view?.data ? <ReportView data={view.data} /> : <div className="skeleton h-24" />}</Drawer>
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
      {error ? <div className="panel p-4 text-danger text-[13px]" role="alert">{error}</div> : loading && !data ? <div className="skeleton h-40" /> : data ? <ReportView data={data} /> : missing ? <EmptyState title="Enter the details above" hint="Then run the report." /> : null}
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
