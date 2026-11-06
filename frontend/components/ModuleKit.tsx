"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Search, Trash2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, titleCase } from "@/lib/format";
import { defaultParam, toneFor, type Action, type Col, type Create, type Field, type Param, type RefKey, type Row, type Screen } from "@/lib/modules";
import { DataTable, type Col as TCol } from "./DataTable";
import { Badge, ConfirmDialog, Drawer, EmptyState, useToast } from "./ui";

/* ------------------------------------------------------------------ reference pickers */
const REFS: Record<RefKey, { api: string; params?: Record<string, string>; label: (r: Row) => string }> = {
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
  if (f.type === "refs") {
    const sel: string[] = Array.isArray(value) ? value : [];
    return (
      <select id={id} className="input !h-auto min-h-[84px]" multiple value={sel} onChange={(e) => onChange(Array.from(e.target.selectedOptions).map((o) => o.value))}>
        {opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    );
  }
  return (
    <select id={id} className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select…</option>{opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
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
  if (v === "" || v === undefined || v === null) return undefined;
  switch (f.type) {
    case "number": return isIdName(f.name) || ["cartons", "from_version_no", "lead_time_days", "frequency_days", "quotation_id"].includes(f.name) ? Number(v) : String(v);
    case "ref": return Number(v);
    case "refs": return (v as string[]).map(Number);
    case "csv": return String(v).split(",").map((s) => s.trim()).filter(Boolean);
    case "checkbox": return !!v;
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
    if (f.required && (v === undefined || (Array.isArray(v) && v.length === 0))) { errors[f.name] = "Required"; continue; }
    if (f.type === "lines" && f.required) for (const r of v as Row[]) for (const c of f.lines!) if (c.required && r[c.name] === undefined) errors[f.name] = `Every line needs “${c.label}”`;
    if (v !== undefined) body[f.name] = v;
  }
  return { body, errors };
}

export function FormDrawer({ open, title, subtitle, fields, submitLabel, onClose, onSubmit }: {
  open: boolean; title: string; subtitle?: string; fields: Field[]; submitLabel: string; onClose: () => void; onSubmit: (body: Row, raw: Row) => Promise<void>;
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
    <Drawer open={open} onClose={onClose} title={title} subtitle={subtitle} width={fields.some((f) => f.type === "lines") ? 760 : 520}
      footer={<><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" form="module-form" disabled={saving}>{saving ? "Working…" : submitLabel}</button></>}>
      <form id="module-form" onSubmit={submit} noValidate className="grid grid-cols-2 gap-x-4 gap-y-3.5">
        {fields.map((f) => {
          const wide = f.type === "textarea" || f.type === "lines" || f.type === "refs";
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

export function CreateButton({ create, onDone, onResult }: { create: Create; onDone: () => void; onResult?: (r: any) => void }) {
  const [open, setOpen] = useState(false);
  const toast = useToast();
  return (
    <>
      <button className="btn btn-primary" onClick={() => setOpen(true)}><Plus size={14} />{create.label}</button>
      <FormDrawer open={open} title={create.title ?? create.label} fields={create.fields} submitLabel={create.title ? "Run" : "Create"} onClose={() => setOpen(false)}
        onSubmit={async (body) => {
          const res = await api<any>(create.path, { method: "POST", body: create.body ? create.body(body) : body, headers: create.idempotent ? { "Idempotency-Key": crypto.randomUUID() } : undefined });
          toast("success", create.title ? "Done" : `${(w => w.charAt(0).toUpperCase() + w.slice(1))(create.label.replace(/^(New|Add|Post|Raise|Log|Set|Start|Record|Create)\s+/i, ""))} saved`);
          setOpen(false); onDone(); if (create.title) onResult?.(res);
        }} />
    </>
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
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [view, setView] = useState<{ title: string; data: any } | null>(null);
  const [act, setAct] = useState<{ a: Action; row: Row } | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

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
  useEffect(() => { setPage(1); }, [q, status]);

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
    if (/\/(pdf|coa)$/.test(href)) { window.open(href, "_blank", "noopener"); return; }
    try { setView({ title: label, data: await api(href.replace(/^\/api/, "")) }); } catch (e) { toast("error", e instanceof ApiError ? e.message : "Could not open"); }
  }
  async function run(a: Action, row: Row, values?: Row) {
    setBusy(true);
    try {
      const body = a.body ? a.body(row, values ?? {}) : { ...(a.versioned ? { expected_version: row.version } : {}), ...(values ?? {}) };
      const res = await api<any>(a.path!(row), { method: a.method ?? (a.path!(row) ? "POST" : "POST"), body: Object.keys(body ?? {}).length || a.method === "PUT" ? body : undefined, headers: a.idempotent ? { "Idempotency-Key": crypto.randomUUID() } : undefined });
      toast("success", `${a.label} done`);
      setAct(null); load();
      if (res && typeof res === "object" && ["award", "convert", "plan", "manifest", "decide", "complete", "dispatch"].some((w) => a.label.toLowerCase().includes(w))) setView({ title: `${a.label} — result`, data: res });
    } catch (e) { toast("error", e instanceof ApiError ? e.message : "Action failed"); } finally { setBusy(false); }
  }
  async function openDetail(r: Row) {
    setView({ title: "Loading…", data: null });
    try { setView({ title: String(r.number ?? r.name ?? r.title ?? r.code ?? `#${r.id}`), data: def.detail ? await api(def.detail(r)) : r }); }
    catch (e) { setView(null); toast("error", e instanceof ApiError ? e.message : "Could not load"); }
  }
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
        {screen.create && can(screen.create.perm) && <CreateButton create={screen.create} onDone={load} onResult={(r) => setResult({ title: screen.create!.title ?? screen.label, data: r })} />}
      </div>
      {error ? <div className="panel p-4 text-danger text-[13px]" role="alert">{error}</div> : (
        <DataTable columns={columns} rows={pageRows as (Row & { id: number })[]} loading={loading} page={page} pageSize={pageSize} total={filtered.length} onPage={setPage}
          onRowClick={openDetail} empty={<EmptyState title="Nothing here yet" hint={screen.create ? `Use “${screen.create.label}” to add the first one.` : "Records appear here as work happens in other modules."} />}
          rowActions={def.actions ? (r) => (
            <div className="flex justify-end gap-1">
              {visible(r).map((a) => (
                <button key={a.label} className={`btn btn-sm ${a.danger ? "btn-danger" : ""}`}
                  onClick={() => (a.href ? openHref(a.href(r), a.label) : a.fields ? setAct({ a, row: r }) : a.confirm ? setAct({ a, row: r }) : run(a, r))}>{a.label}</button>
              ))}
            </div>
          ) : undefined} />
      )}

      <Drawer open={!!view} onClose={() => setView(null)} title={view?.title ?? ""} width={820}>{view?.data ? <ReportView data={view.data} /> : <div className="skeleton h-24" />}</Drawer>
      <Drawer open={!!result} onClose={() => setResult(null)} title={result?.title ?? ""} width={820}>{result && <ReportView data={result.data} />}</Drawer>
      <FormDrawer open={!!act?.a.fields} title={act?.a.label ?? ""} subtitle={act ? String(act.row.number ?? act.row.name ?? `#${act.row.id}`) : undefined} fields={act?.a.fields ?? []} submitLabel={act?.a.label ?? "Run"}
        onClose={() => setAct(null)} onSubmit={async (_body, raw) => { const { body } = buildBody(act!.a.fields!, raw); await run(act!.a, act!.row, body); }} />
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
