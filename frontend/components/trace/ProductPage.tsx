"use client";
import { useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, titleCase } from "@/lib/format";
import { toneFor } from "@/lib/modules";
import { Badge, PageHeader } from "@/components/ui";
import { KV, Pager, Section, Stat, State, Tabs, pct, qty, useGet } from "./bits";

type Summary = {
  product: { id: number; item_code: string; name: string; description: string | null; kind: string; kind_label: string; uom: string; hsn_code: string | null; gst_rate: string | null; shelf_life_days: number | null; storage_conditions: string | null; is_batch_tracked: boolean; is_active: boolean };
  recipe: { id: number; version_no: number; status: string; fv_version: number | null; fv_status: string | null; formulation_code: string | null; formulation_name: string | null } | null;
  stock: { physical: string; reserved: string; blocked: string; available: string };
  metrics: Record<string, string | number | null>;
  definitions: Record<string, string>;
};
type Tab = "overview" | "formulations" | "materials" | "production" | "batches" | "quality" | "inventory" | "sales" | "costing" | "documents" | "history";
const TABS: { id: Tab; label: string }[] = [{ id: "overview", label: "Overview" }, { id: "formulations", label: "Formulations & BOM" }, { id: "materials", label: "Raw materials" }, { id: "production", label: "Production" }, { id: "batches", label: "Batches" },
  { id: "quality", label: "Quality" }, { id: "inventory", label: "Inventory" }, { id: "sales", label: "Sales & customers" }, { id: "costing", label: "Costing" }, { id: "documents", label: "Documents" }, { id: "history", label: "History" }];
const money = (v: string | null | undefined) => (v === null || v === undefined ? "—" : `₹${Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

export function ProductPage({ id }: { id: number }) {
  const { can } = useAuth();
  const { data: p, error, loading } = useGet<Summary>(`/traceability/products/${id}`);
  const [tab, setTab] = useState<Tab>("overview");
  if (loading) return <div className="space-y-4 pt-6"><div className="skeleton h-10 w-80" /><div className="skeleton h-20" /><div className="skeleton h-[360px]" /></div>;
  if (error || !p) return <div className="pt-8"><PageHeader title="Product" /><p role="alert" className="panel p-5 text-danger">{error ?? "Product not found"}</p></div>;
  const m = p.metrics, it = p.product, u = it.uom;
  return (
    <div className="space-y-5 pb-10">
      <PageHeader title={it.name} description={`${it.item_code} · ${it.kind_label} · unit ${u}`}
        actions={<div className="flex flex-wrap gap-2">
          <Link className="btn" href="/m/inventory/stock">View stock</Link>
          {it.kind === "FG" && can("production.order:create") && <Link className="btn btn-primary" href="/m/production/orders">Create production order</Link>}
          {can("masters.item:update") && <Link className="btn" href="/masters/items">Edit product</Link>}
        </div>} />
      <div className="flex flex-wrap items-center gap-2 -mt-2">
        <Badge tone={it.is_active ? "success" : "neutral"}>{it.is_active ? "Active" : "Inactive"}</Badge>
        {p.recipe ? <span className="badge tone-neutral">{p.recipe.formulation_code ? `${p.recipe.formulation_code} v${p.recipe.fv_version} · ` : ""}BOM v{p.recipe.version_no}</span> : <span className="badge tone-warning">No approved BOM</span>}
        {Number(m.batches_not_released) > 0 && <span className="badge tone-warning">{m.batches_not_released} batch{Number(m.batches_not_released) === 1 ? "" : "es"} not released</span>}
      </div>

      <section aria-label="Key figures" className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        <Stat label="Available stock" value={qty(p.stock.available, u)} note="Physical minus reserved minus blocked" />
        <Stat label="Reserved" value={qty(p.stock.reserved, u)} note="Promised to orders, not yet shipped" />
        <Stat label="QC hold" value={qty(m.qc_hold_qty as string, u)} note={p.definitions.qc_hold_qty} tone={Number(m.qc_hold_qty) > 0 ? "warn" : undefined} />
        <Stat label="Manufactured" value={qty(m.manufactured_qty as string, u)} note={p.definitions.manufactured_qty} />
        <Stat label="Dispatched" value={qty(m.dispatched_qty as string, u)} note={p.definitions.dispatched_qty} />
        <Stat label="Orders" value={`${m.production_orders}`} note={`${m.active_production_orders} active, ${m.completed_orders} completed`} />
        <Stat label="QC rejects" value={pct(m.qc_rejection_rate_pct as string | null)} note={`${p.definitions.qc_rejection_rate_pct}. Decided: ${m.finished_inspections_decided}`} />
        <Stat label="Avg yield" value={pct(m.avg_yield_pct as string | null)} note={p.definitions.avg_yield_pct} />
      </section>

      <Tabs value={tab} onChange={setTab} tabs={TABS} />
      {tab === "overview" && <Overview p={p} id={id} go={setTab} />}
      {tab === "formulations" && <Formulations id={id} />}
      {tab === "materials" && <Materials id={id} />}
      {tab === "production" && <Production id={id} />}
      {tab === "batches" && <Batches id={id} unit={u} />}
      {tab === "quality" && <Quality id={id} />}
      {tab === "inventory" && <Inventory id={id} p={p} />}
      {tab === "sales" && <Sales id={id} />}
      {tab === "costing" && <Costing id={id} p={p} />}
      {tab === "documents" && <Documents id={id} />}
      {tab === "history" && <HistoryTab id={id} />}
    </div>
  );
}

function Overview({ p, id, go }: { p: Summary; id: number; go: (t: Tab) => void }) {
  const m = p.metrics, it = p.product;
  const prod = useGet<{ items: { id: number; number: string; status: string; actual_qty: string; planned_qty: string; batch_no: string | null; batch_id: number | null; completed_at: string | null }[] }>(`/traceability/products/${id}/production`, { page: 1, page_size: 5 });
  const batches = useGet<{ items: { id: number; batch_no: string; qc_status: string; available: string; mfg_date: string | null }[] }>(`/traceability/products/${id}/batches`, { page: 1, page_size: 5 });
  return (
    <div className="grid lg:grid-cols-2 gap-5">
      <Section title="Product"><div className="px-4 pb-3"><KV rows={[["Code", it.item_code], ["Type", it.kind_label], ["Unit", it.uom], ["HSN", it.hsn_code], ["GST rate", it.gst_rate ? `${it.gst_rate}%` : null], ["Shelf life", it.shelf_life_days ? `${it.shelf_life_days} days` : null],
        ["Batch tracked", it.is_batch_tracked ? "Yes" : "No"], ["Storage", it.storage_conditions], ["Current recipe", p.recipe ? `${p.recipe.formulation_code ?? "Manual BOM"} ${p.recipe.fv_version ? `v${p.recipe.fv_version}` : ""} · BOM v${p.recipe.version_no}` : "None approved"]]} /></div></Section>
      <Section title="Sales activity"><div className="px-4 pb-3"><KV rows={[["Sales orders", String(m.sales_orders)], ["Customers", String(m.customers)], ["Last shipped", m.last_shipped ? fmtDate(m.last_shipped as string) : "Never"], ["Dispatched", qty(m.dispatched_qty as string, it.uom)], ["Returned by customers", qty(m.returned_qty as string, it.uom)]]} /></div>
        <div className="px-4 pb-4"><button className="btn btn-sm" onClick={() => go("sales")}>See customers</button></div></Section>
      <Section title="Recent production" actions={<button className="btn btn-sm" onClick={() => go("production")}>All</button>}>
        <State loading={prod.loading} error={prod.error} empty={prod.data?.items.length === 0 && "No production orders yet."}>{prod.data && <div className="overflow-auto"><table className="dt"><thead><tr><th>Order</th><th>Status</th><th className="num">Output</th><th>Batch</th></tr></thead>
          <tbody>{prod.data.items.map((o) => <tr key={o.id}><td><Link className="font-medium hover:underline" href={`/m/production/orders?open=${o.id}`}>{o.number}</Link></td><td><Badge tone={toneFor(o.status)}>{titleCase(o.status)}</Badge></td><td className="num">{qty(o.actual_qty)} / {qty(o.planned_qty)}</td><td>{o.batch_id ? <Link className="hover:underline" href={`/inventory/batches/${o.batch_id}/traceability`}>{o.batch_no}</Link> : "—"}</td></tr>)}</tbody></table></div>}</State></Section>
      <Section title="Recent batches" actions={<button className="btn btn-sm" onClick={() => go("batches")}>All</button>}>
        <State loading={batches.loading} error={batches.error} empty={batches.data?.items.length === 0 && "No batches yet."}>{batches.data && <div className="overflow-auto"><table className="dt"><thead><tr><th>Batch</th><th>QC</th><th className="num">Available</th><th>Made</th></tr></thead>
          <tbody>{batches.data.items.map((b) => <tr key={b.id}><td><Link className="font-medium hover:underline" href={`/inventory/batches/${b.id}/traceability`}>{b.batch_no}</Link></td><td><Badge tone={toneFor(b.qc_status)}>{titleCase(b.qc_status)}</Badge></td><td className="num">{qty(b.available, it.uom)}</td><td>{b.mfg_date ? fmtDate(b.mfg_date) : "—"}</td></tr>)}</tbody></table></div>}</State></Section>
    </div>
  );
}

function Formulations({ id }: { id: number }) {
  const { data, error, loading } = useGet<{ boms: { id: number; version_no: number; status: string; output_qty: string; formulation_code: string | null; formulation_version: number | null; formulation_status: string | null; orders: number }[]; formulation_versions: { id: number; code: string; name: string; version_no: number; status: string; batch_size: string; approved_at: string | null; revision_note: string | null }[] }>(`/traceability/products/${id}/formulations`);
  return (
    <div className="space-y-5">
      <Section title="Bills of materials" hint="Each production order pins one BOM version; older versions stay on record."><State loading={loading} error={error} empty={data?.boms.length === 0 && "No BOM has been created for this product."}>{data && <div className="overflow-auto"><table className="dt"><thead><tr><th>BOM</th><th>Status</th><th>Formulation</th><th className="num">Batch output</th><th className="num">Orders using it</th></tr></thead>
        <tbody>{data.boms.map((b) => <tr key={b.id}><td>BOM v{b.version_no}</td><td><Badge tone={toneFor(b.status)}>{titleCase(b.status)}</Badge></td><td>{b.formulation_code ? `${b.formulation_code} v${b.formulation_version}` : "Manual BOM"}</td><td className="num">{qty(b.output_qty)}</td><td className="num">{b.orders}</td></tr>)}</tbody></table></div>}</State></Section>
      <Section title="Formulation versions" hint="Released versions are never overwritten."><State loading={loading} error={error} empty={data?.formulation_versions.length === 0 && "No formulation is linked to this product."}>{data && <div className="overflow-auto"><table className="dt"><thead><tr><th>Formulation</th><th>Version</th><th>Status</th><th className="num">Batch size</th><th>Approved</th><th>Revision note</th></tr></thead>
        <tbody>{data.formulation_versions.map((f) => <tr key={f.id}><td>{f.code} · {f.name}</td><td>v{f.version_no}</td><td><Badge tone={toneFor(f.status)}>{titleCase(f.status)}</Badge></td><td className="num">{qty(f.batch_size)}</td><td>{f.approved_at ? fmtDate(f.approved_at) : "—"}</td><td className="text-muted">{f.revision_note ?? ""}</td></tr>)}</tbody></table></div>}</State></Section>
    </div>
  );
}

function Materials({ id }: { id: number }) {
  const { data, error, loading } = useGet<{ items: { item_id: number; item_code: string; name: string; kind: string; uom: string; planned: string; issued: string; consumed: string; variance: string; orders: number; lots: { batch_id: number; batch_no: string; supplier_lot_no: string | null; supplier: string | null; qty: string; orders: number }[] }[]; note: string }>(`/traceability/products/${id}/materials`);
  return (
    <Section title="Raw and packaging materials actually used" hint={data?.note}>
      <State loading={loading} error={error} empty={data?.items.length === 0 && "No production has consumed materials for this product yet."}>
        {data && <div className="px-4 pb-4 space-y-4">{data.items.map((m) => (
          <article key={m.item_id} className="inset p-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="font-semibold">{m.name} <span className="font-normal text-muted">{m.item_code} · {m.kind}</span></h3>
              <span className="t-support">Planned {qty(m.planned, m.uom)} · consumed {qty(m.consumed, m.uom)} · variance <b className={Number(m.variance) !== 0 ? "text-warning" : ""}>{Number(m.variance) > 0 ? "+" : ""}{qty(m.variance, m.uom)}</b> · {m.orders} order{m.orders === 1 ? "" : "s"}</span></div>
            <table className="dt mt-2"><thead><tr><th>Batch</th><th>Supplier lot</th><th>Supplier</th><th className="num">Consumed</th><th className="num">Orders</th></tr></thead>
              <tbody>{m.lots.map((l) => <tr key={l.batch_id}><td><Link className="font-medium hover:underline" href={`/inventory/batches/${l.batch_id}/traceability`}>{l.batch_no}</Link></td><td>{l.supplier_lot_no ?? "—"}</td><td>{l.supplier ?? "—"}</td><td className="num">{qty(l.qty, m.uom)}</td><td className="num">{l.orders}</td></tr>)}
                {m.lots.length === 0 && <tr><td colSpan={5} className="text-muted">No consumption recorded.</td></tr>}</tbody></table>
          </article>))}</div>}
      </State>
    </Section>
  );
}

function usePaged<T>(path: string, size = 15) {
  const [page, setPage] = useState(1);
  const r = useGet<{ items: T[]; total: number }>(path, { page, page_size: size });
  return { ...r, page, setPage, size };
}

function Production({ id }: { id: number }) {
  const { data, error, loading, page, setPage, size } = usePaged<{ id: number; number: string; status: string; planned_qty: string; actual_qty: string; yield_pct: string | null; wastage: string; unit_cost: string | null; batch_id: number | null; batch_no: string | null; formulation: string | null; formulation_version: number | null; bom_version: number | null; completed_at: string | null }>(`/traceability/products/${id}/production`);
  return (
    <Section title="Production history"><State loading={loading && !data} error={error} empty={data?.total === 0 && "No production orders yet."}>{data && <><div className="overflow-auto"><table className="dt"><thead><tr><th>Order</th><th>Status</th><th>Recipe</th><th className="num">Planned</th><th className="num">Actual</th><th className="num">Yield</th><th className="num">Wastage</th><th>Batch</th><th>Completed</th></tr></thead>
      <tbody>{data.items.map((o) => <tr key={o.id}><td><Link className="font-medium hover:underline" href={`/m/production/orders?open=${o.id}`}>{o.number}</Link></td><td><Badge tone={toneFor(o.status)}>{titleCase(o.status)}</Badge></td><td>{o.formulation ? `${o.formulation} v${o.formulation_version}` : "Manual"} · BOM v{o.bom_version}</td><td className="num">{qty(o.planned_qty)}</td><td className="num">{qty(o.actual_qty)}</td><td className="num">{pct(o.yield_pct)}</td><td className="num">{qty(o.wastage)}</td><td>{o.batch_id ? <Link className="hover:underline" href={`/inventory/batches/${o.batch_id}/traceability`}>{o.batch_no}</Link> : "—"}</td><td>{o.completed_at ? fmtDate(o.completed_at) : "—"}</td></tr>)}</tbody></table></div><Pager page={page} total={data.total} size={size} onPage={setPage} /></>}</State></Section>
  );
}

function Batches({ id, unit }: { id: number; unit: string }) {
  const { data, error, loading, page, setPage, size } = usePaged<{ id: number; batch_no: string; qc_status: string; mfg_date: string | null; expiry_date: string | null; physical: string; available: string; blocked: string; dispatched: string }>(`/traceability/products/${id}/batches`);
  return (
    <Section title="Batch genealogy" hint="Open a batch to trace it back to its suppliers and forward to its customers."><State loading={loading && !data} error={error} empty={data?.total === 0 && "No batches yet."}>{data && <><div className="overflow-auto"><table className="dt"><thead><tr><th>Batch</th><th>QC</th><th>Made</th><th>Expiry</th><th className="num">Physical</th><th className="num">Available</th><th className="num">Blocked</th><th className="num">Dispatched</th></tr></thead>
      <tbody>{data.items.map((b) => <tr key={b.id}><td><Link className="font-medium hover:underline" href={`/inventory/batches/${b.id}/traceability`}>{b.batch_no}</Link></td><td><Badge tone={toneFor(b.qc_status)}>{titleCase(b.qc_status)}</Badge></td><td>{b.mfg_date ? fmtDate(b.mfg_date) : "—"}</td><td>{b.expiry_date ? fmtDate(b.expiry_date) : "—"}</td><td className="num">{qty(b.physical, unit)}</td><td className="num">{qty(b.available, unit)}</td><td className="num">{qty(b.blocked, unit)}</td><td className="num">{qty(b.dispatched, unit)}</td></tr>)}</tbody></table></div><Pager page={page} total={data.total} size={size} onPage={setPage} /></>}</State></Section>
  );
}

function Quality({ id }: { id: number }) {
  const { data, error, loading, page, setPage, size } = usePaged<{ id: number; number: string; stage: string; status: string; decision: string | null; decision_reason: string | null; decided_at: string | null; decided_by: string | null; batch_id: number; batch_no: string; results: number; failed_results: number }>(`/traceability/products/${id}/quality`);
  return (
    <Section title="Quality history" hint="Every inspection of every batch of this product, including failures."><State loading={loading && !data} error={error} empty={data?.total === 0 && "No inspections recorded."}>{data && <><div className="overflow-auto"><table className="dt"><thead><tr><th>Inspection</th><th>Stage</th><th>Batch</th><th>Decision</th><th className="num">Results</th><th className="num">Failed</th><th>Decided</th></tr></thead>
      <tbody>{data.items.map((i) => <tr key={i.id}><td><Link className="font-medium hover:underline" href={`/m/quality/inspections?open=${i.id}`}>{i.number}</Link></td><td>{titleCase(i.stage)}</td><td><Link className="hover:underline" href={`/inventory/batches/${i.batch_id}/traceability`}>{i.batch_no}</Link></td><td><Badge tone={toneFor(i.decision ?? i.status)}>{titleCase(i.decision ?? i.status)}</Badge></td><td className="num">{i.results}</td><td className="num">{i.failed_results}</td><td>{i.decided_at ? `${fmtDate(i.decided_at)} · ${i.decided_by ?? ""}` : "—"}</td></tr>)}</tbody></table></div><Pager page={page} total={data.total} size={size} onPage={setPage} /></>}</State></Section>
  );
}

function Inventory({ id, p }: { id: number; p: Summary }) {
  const u = p.product.uom;
  return (
    <div className="space-y-5">
      <Section title="Stock position" hint="Physical = in the building. Reserved = promised. Blocked = quarantine, hold or rejected. Available = physical − reserved − blocked.">
        <div className="px-4 pb-4 grid grid-cols-2 md:grid-cols-4 gap-3"><Stat label="Physical" value={qty(p.stock.physical, u)} /><Stat label="Reserved" value={qty(p.stock.reserved, u)} /><Stat label="Blocked" value={qty(p.stock.blocked, u)} tone={Number(p.stock.blocked) > 0 ? "warn" : undefined} /><Stat label="Available" value={qty(p.stock.available, u)} /></div>
      </Section>
      <Batches id={id} unit={u} />
    </div>
  );
}

function Sales({ id }: { id: number }) {
  const { data, error, loading, page, setPage, size } = usePaged<{ customer_id: number; customer_name: string | null; orders: number; shipments: number; qty: string; delivered: string; batches: number; last_shipped: string }>(`/traceability/products/${id}/sales`);
  return (
    <Section title="Customers who received this product" hint="From shipment lines, so it counts what actually left, not what was ordered."><State loading={loading && !data} error={error} empty={data?.total === 0 && "Nothing has been shipped yet."}>{data && <><div className="overflow-auto"><table className="dt"><thead><tr><th>Customer</th><th className="num">Orders</th><th className="num">Shipments</th><th className="num">Batches</th><th className="num">Shipped</th><th className="num">Delivered</th><th>Last shipped</th></tr></thead>
      <tbody>{data.items.map((c) => <tr key={c.customer_id}><td className="font-medium">{c.customer_name}</td><td className="num">{c.orders}</td><td className="num">{c.shipments}</td><td className="num">{c.batches}</td><td className="num">{qty(c.qty)}</td><td className="num">{qty(c.delivered)}</td><td>{fmtDate(c.last_shipped)}</td></tr>)}</tbody></table></div><Pager page={page} total={data.total} size={size} onPage={setPage} /></>}</State></Section>
  );
}

function Costing({ id, p }: { id: number; p: Summary }) {
  const { data, error, loading } = useGet<{ items: { id: number; number: string; completed_at: string | null; actual_qty: string; unit_cost: string | null; input_cost: string | null; yield_pct: string | null }[] }>(`/traceability/products/${id}/production`, { page: 1, page_size: 50 });
  const costed = (data?.items ?? []).filter((o) => o.unit_cost !== null && Number(o.unit_cost) > 0);
  return (
    <Section title="Manufacturing cost" hint={p.definitions.avg_unit_cost}>
      <State loading={loading} error={error} empty={costed.length === 0 && "No completed order has a posted cost yet."}>
        <div className="px-4 pb-3"><KV rows={[["Average unit cost", money(p.metrics.avg_unit_cost as string | null)], ["Completed orders costed", String(costed.length)]]} /></div>
        <div className="overflow-auto"><table className="dt"><thead><tr><th>Order</th><th>Completed</th><th className="num">Output</th><th className="num">Input cost</th><th className="num">Unit cost</th><th className="num">Yield</th></tr></thead>
          <tbody>{costed.map((o) => <tr key={o.id}><td>{o.number}</td><td>{o.completed_at ? fmtDate(o.completed_at) : "—"}</td><td className="num">{qty(o.actual_qty)}</td><td className="num">{money(o.input_cost)}</td><td className="num">{money(o.unit_cost)}</td><td className="num">{pct(o.yield_pct)}</td></tr>)}</tbody></table></div>
      </State>
    </Section>
  );
}

function Documents({ id }: { id: number }) {
  const { can } = useAuth();
  const allowed = can("documents.document:read");
  const { data, error, loading } = useGet<{ items: { id: number; title: string; doc_type: string; current_version_no: number; updated_at?: string }[]; total: number }>(allowed ? "/documents" : null, { entity_type: "item", entity_id: String(id), page_size: 50 });
  return (
    <Section title="Documents" hint="Files linked to this product in the document centre."><State loading={allowed && loading} error={error} empty={(!allowed && "You do not have access to the document centre.") || (data?.total === 0 && "No documents are linked to this product. Link one from the document centre.")}>
      {data && <ul className="px-4 pb-4 divide-y divide-line">{data.items.map((d) => <li key={d.id} className="py-2 flex items-baseline justify-between gap-3"><Link className="font-medium hover:underline" href="/documents">{d.title}</Link><span className="t-support">{titleCase(d.doc_type)} · v{d.current_version_no}</span></li>)}</ul>}
    </State></Section>
  );
}

function HistoryTab({ id }: { id: number }) {
  const { data, error, loading } = useGet<{ items: { occurred_at: string; user_email: string | null; action: string; entity: string; entity_id: string; reason: string | null }[] }>(`/traceability/products/${id}/history`);
  return (
    <Section title="Activity and audit history" hint="Changes to the product record and to its batches (quality decisions, impact analyses)."><State loading={loading} error={error} empty={data?.items.length === 0 && "No audited changes yet."}>{data && <div className="overflow-auto"><table className="dt"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Reason</th></tr></thead>
      <tbody>{data.items.map((h, i) => <tr key={i}><td>{fmtDateTime(h.occurred_at)}</td><td>{h.user_email ?? "System"}</td><td>{titleCase(h.action)}</td><td className="text-muted">{titleCase(h.entity)} {h.entity_id}</td><td>{h.reason ?? ""}</td></tr>)}</tbody></table></div>}</State></Section>
  );
}
