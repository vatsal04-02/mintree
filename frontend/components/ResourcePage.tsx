"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, Plus, Search } from "lucide-react";
import { api, ApiError, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtMoney, fmtNum } from "@/lib/format";
import type { Column, Field, ResourceDef } from "@/lib/resources";
import { DataTable, type Col } from "./DataTable";
import { Badge, ConfirmDialog, Drawer, PageHeader, useDebounced, useToast } from "./ui";

type Row = { id: number } & Record<string, any>;

function cell(c: Column, r: Row): React.ReactNode {
  const v = r[c.key];
  if (c.kind === "bool") return <Badge tone={v ? "success" : "neutral"}>{v ? "Active" : "Inactive"}</Badge>;
  if (v === null || v === undefined || v === "") return <span className="text-muted">—</span>;
  switch (c.kind) {
    case "mono": return <span className="tnum font-medium text-[0.8125rem]">{v}</span>;
    case "number": return fmtNum(v, c.digits ?? 0);
    case "money": return fmtMoney(v);
    case "badge": return <Badge tone={c.tones?.[v] ?? "neutral"}>{c.enumLabels?.[v] ?? v}</Badge>;
    default: return v;
  }
}

export function ResourcePage({ def }: { def: ResourceDef }) {
  const { can } = useAuth();
  const toast = useToast();
  const params = useSearchParams();
  const canCreate = can(`${def.perm}:create`), canUpdate = can(`${def.perm}:update`), canDelete = can(`${def.perm}:delete`);

  const [q, setQ] = useState(params.get("q") ?? "");
  const dq = useDebounced(q);
  const [sort, setSort] = useState(def.sort);
  const [page, setPage] = useState(1);
  const [showInactive, setShowInactive] = useState(false);
  const [filters, setFilters] = useState<Record<string, string>>(
    () => Object.fromEntries((def.filters ?? []).filter((f) => params.get(f.name)).map((f) => [f.name, params.get(f.name)!])));
  const [data, setData] = useState<Page<Row> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const [confirm, setConfirm] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const pageSize = 25;

  const query = useMemo(() => ({ q: dq, sort, page, page_size: pageSize, include_inactive: showInactive ? "true" : "", ...filters }), [dq, sort, page, showInactive, filters]);

  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await api<Page<Row>>(def.api, { params: query })); setError(null); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Failed to load"); }
    finally { setLoading(false); }
  }, [def.api, query]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [dq, filters, showInactive]);
  useEffect(() => { if (params.get("new") === "1" && canCreate) setEditing("new"); }, [params, canCreate]);

  const columns: Col<Row>[] = def.columns.map((c) => ({
    key: c.key, label: c.label, sortKey: c.key === "is_active" || c.key === "category_name" || c.key === "uom_code" || c.key === "warehouse_code" ? undefined : c.key,
    align: c.kind === "number" || c.kind === "money" ? "right" : undefined, render: (r) => cell(c, r),
  }));

  async function setActive(r: Row, active: boolean) {
    setBusy(true);
    try {
      if (active) await api(`${def.api}/${r.id}`, { method: "PATCH", body: { is_active: true } });
      else await api(`${def.api}/${r.id}`, { method: "DELETE" });
      toast("success", `${def.singular} ${active ? "reactivated" : "deactivated"}`);
      setConfirm(null); load();
    } catch (e) { toast("error", e instanceof ApiError ? e.message : "Action failed"); } finally { setBusy(false); }
  }

  const exportHref = `/api${def.api}/export.csv?${new URLSearchParams({ q: dq, ...(showInactive ? { include_inactive: "true" } : {}), ...filters }).toString()}`;

  return (
    <>
      <PageHeader title={def.title} description={def.description} actions={<>
        {def.exportable && can(`${def.perm}:export`) && <a className="btn" href={exportHref}><Download size={14} />Export</a>}
        {canCreate && <button className="btn btn-primary" onClick={() => setEditing("new")}><Plus size={14} />New {def.singular.toLowerCase()}</button>}
      </>} />

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input className="input !pl-8 w-64" placeholder={`Search ${def.title.toLowerCase()}…`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
        </div>
        {def.filters?.map((f) => (
          <select key={f.name} className="input !w-auto" aria-label={f.label} value={filters[f.name] ?? ""}
            onChange={(e) => setFilters((s) => ({ ...s, [f.name]: e.target.value }))}>
            <option value="">{f.label}: all</option>
            {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        ))}
        <label className="flex items-center gap-1.5 text-[0.75rem] text-muted ml-1 cursor-pointer select-none">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show inactive
        </label>
      </div>

      {error && <div className="panel border-danger/40 text-danger px-3 py-2 mb-3 text-[0.8125rem]" role="alert">{error}</div>}
      <DataTable columns={columns} rows={data?.items ?? []} loading={loading} sort={sort} onSort={setSort}
        page={page} pageSize={pageSize} total={data?.total ?? 0} onPage={setPage} onRowClick={(r) => setEditing(r)}
        rowActions={canDelete || canUpdate ? (r) => (
          r.is_active
            ? canDelete && <button className="btn btn-ghost btn-sm text-muted hover:text-danger" onClick={() => setConfirm(r)}>Deactivate</button>
            : canUpdate && <button className="btn btn-ghost btn-sm" onClick={() => setActive(r, true)}>Reactivate</button>
        ) : undefined} />

      <ResourceForm def={def} target={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
      <ConfirmDialog open={!!confirm} title={`Deactivate ${def.singular.toLowerCase()}?`} danger busy={busy} confirmLabel="Deactivate"
        body={`${confirm?.[def.columns[1]?.key] ?? "This record"} will no longer be available for new transactions. Existing records and history are kept, and it can be reactivated.`}
        onConfirm={() => confirm && setActive(confirm, false)} onCancel={() => setConfirm(null)} />
    </>
  );
}

/* ---------------- Form ---------------- */
function RefSelect({ f, value, onChange, invalid, disabled }: { f: Field; value: any; onChange: (v: any) => void; invalid: boolean; disabled: boolean }) {
  const [opts, setOpts] = useState<Row[]>([]);
  useEffect(() => {
    api<Page<Row>>(f.ref!.resource, { params: { page_size: 500, is_active: "true", ...f.ref!.params } }).then((p) => setOpts(p.items)).catch(() => {});
  }, [f.ref]);
  return (
    <select id={f.name} className="input" value={value ?? ""} aria-invalid={invalid} disabled={disabled}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}>
      <option value="">{f.required ? "Select…" : "None"}</option>
      {opts.map((o) => <option key={o.id} value={o.id}>{f.ref!.label(o)}</option>)}
    </select>
  );
}

function ResourceForm({ def, target, onClose, onSaved }: { def: ResourceDef; target: Row | "new" | null; onClose: () => void; onSaved: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const isNew = target === "new";
  const readOnly = !isNew && !can(`${def.perm}:update`);
  const fields = def.fields.filter((f) => !f.needsPerm || can(f.needsPerm));
  const sections = def.sections.filter((s) => fields.some((f) => f.section === s));
  const [vals, setVals] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tab, setTab] = useState(sections[0]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!target) return;
    const init: Record<string, any> = {};
    for (const f of fields) init[f.name] = target === "new" ? (f.type === "bool" ? true : f.type === "select" && f.options?.length && f.name === "approval_status" ? "PENDING" : f.name === "category" && def.key === "suppliers" ? "RM_PM" : "") : (target[f.name] ?? (f.type === "bool" ? false : ""));
    setVals(init); setErrors({}); setTab(sections[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const local: Record<string, string> = {};
    const body: Record<string, any> = {};
    for (const f of fields) {
      let v = vals[f.name];
      if (typeof v === "string") v = v.trim();
      if (f.required && (v === "" || v === null || v === undefined)) { local[f.name] = "Required"; continue; }
      if (f.type === "number") v = v === "" || v === null ? null : String(v);
      else if (f.type === "bool") v = !!v;
      else if (v === "" || v === undefined) v = null;
      body[f.name] = v;
    }
    if (Object.keys(local).length) { setErrors(local); setTab(sections.find((s) => fields.some((f) => f.section === s && local[f.name])) ?? tab); return; }
    setSaving(true);
    try {
      if (isNew) await api(def.api, { method: "POST", body });
      else await api(`${def.api}/${(target as Row).id}`, { method: "PATCH", body });
      toast("success", `${def.singular} ${isNew ? "created" : "saved"}`);
      onSaved();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.fields && Object.keys(err.fields).length) {
          setErrors(err.fields);
          const first = Object.keys(err.fields)[0];
          const sec = fields.find((f) => f.name === first)?.section; if (sec) setTab(sec);
        } else toast("error", err.message);
      } else toast("error", "Could not save");
    } finally { setSaving(false); }
  }

  const sectionHasError = (s: string) => fields.some((f) => f.section === s && errors[f.name]);

  return (
    <Drawer open={!!target} onClose={onClose} width={560}
      title={isNew ? `New ${def.singular.toLowerCase()}` : readOnly ? def.singular : `Edit ${def.singular.toLowerCase()}`}
      subtitle={!isNew && target ? String((target as Row)[def.columns[0].key]) : undefined}
      footer={<>
        <button type="button" className="btn" onClick={onClose}>{readOnly ? "Close" : "Cancel"}</button>
        {!readOnly && <button className="btn btn-primary" form="resource-form" disabled={saving}>{saving ? "Saving…" : isNew ? "Create" : "Save changes"}</button>}
      </>}>
      <form id="resource-form" onSubmit={submit} noValidate>
        {sections.length > 1 && (
          <div role="tablist" className="flex gap-1 border-b border-line mb-4 -mt-1">
            {sections.map((s) => (
              <button type="button" role="tab" aria-selected={tab === s} key={s} onClick={() => setTab(s)}
                className={`px-3 h-9 text-[0.75rem] font-medium border-b-2 -mb-px ${tab === s ? "border-primary text-primary" : "border-transparent text-muted hover:text-fg"}`}>
                {s}{sectionHasError(s) && <span className="inline-block w-1.5 h-1.5 rounded-full bg-danger ml-1.5 align-middle" />}
              </button>
            ))}
          </div>
        )}
        <div className="grid grid-cols-2 gap-x-4 gap-y-3.5">
          {fields.filter((f) => f.section === tab).map((f) => {
            const wide = f.type === "textarea" || f.name === "name" || f.name.startsWith("address");
            const err = errors[f.name];
            const set = (v: any) => { setVals((s) => ({ ...s, [f.name]: f.upper && typeof v === "string" ? v.toUpperCase() : v })); if (err) setErrors((e) => ({ ...e, [f.name]: "" })); };
            return (
              <div key={f.name} className={wide ? "col-span-2" : ""}>
                {f.type === "bool" ? (
                  <label className="flex items-center gap-2 h-8 mt-5 text-[0.8125rem] cursor-pointer">
                    <input type="checkbox" checked={!!vals[f.name]} disabled={readOnly} onChange={(e) => set(e.target.checked)} />{f.label}
                  </label>
                ) : (
                  <>
                    <label htmlFor={f.name} className="field-label">{f.label}{f.required && <span className="text-danger"> *</span>}</label>
                    {f.type === "select" ? (
                      <select id={f.name} className="input" value={vals[f.name] ?? ""} disabled={readOnly} aria-invalid={!!err} onChange={(e) => set(e.target.value)}>
                        <option value="">Select…</option>{f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    ) : f.type === "ref" ? (
                      <RefSelect f={f} value={vals[f.name]} onChange={set} invalid={!!err} disabled={readOnly} />
                    ) : f.type === "textarea" ? (
                      <textarea id={f.name} className="input" value={vals[f.name] ?? ""} disabled={readOnly} onChange={(e) => set(e.target.value)} />
                    ) : (
                      <input id={f.name} className="input" type={f.type === "number" ? "number" : f.type === "email" ? "email" : "text"} step={f.step}
                        value={vals[f.name] ?? ""} placeholder={f.placeholder} disabled={readOnly} aria-invalid={!!err}
                        onChange={(e) => set(e.target.value)} />
                    )}
                    {err ? <p className="text-danger text-[0.75rem] mt-1" role="alert">{err}</p> : f.help ? <p className="text-muted text-[0.75rem] mt-1">{f.help}</p> : null}
                  </>
                )}
              </div>
            );
          })}
        </div>
      </form>
    </Drawer>
  );
}
