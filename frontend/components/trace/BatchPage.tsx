"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, titleCase } from "@/lib/format";
import { toneFor } from "@/lib/modules";
import { Badge, ConfirmDialog, PageHeader, useToast } from "@/components/ui";
import { Genealogy, type GraphData } from "./Genealogy";
import { KV, Pager, Section, Stat, State, Tabs, qty, useGet } from "./bits";

type Insp = { id: number; number: string; stage: string; status: string; decision: string | null; decision_reason: string | null; decided_at: string | null; decided_by: string | null; created_at: string;
  failed_results: number; retested: boolean; results: { test: string; value: string | null; passed: boolean | null; observation: string | null; tested_at: string; tested_by: string | null }[] };
type Detail = {
  batch: { id: number; batch_no: string; item_id: number; item_code: string; item_name: string; kind: string; kind_label: string; uom: string; supplier_lot_no: string | null; supplier_name: string | null; mfg_date: string | null; expiry_date: string | null;
    received_at: string; qc_status: string; qc_decided_at: string | null; qc_decided_by: string | null; source_type: string; business_status: string };
  source_receipt: { id: number; number: string; received_at: string; po_number: string | null; supplier_name: string | null; delivery_note_no: string | null } | null;
  production_order: { id: number; number: string; status: string; planned_qty: string; actual_qty: string; yield_pct: string | null; formulation: string | null; formulation_version: number | null; bom_version: number | null } | null;
  locations: { warehouse: string; warehouse_name: string; location: string | null; physical: string; reserved: string; blocked: string; available: string }[];
  reconciliation: { unit: string; quantities: Record<string, string>; checks: { name: string; expected: string; actual: string; difference: string; ok: boolean; note: string }[]; balanced: boolean; warnings: string[]; definitions: Record<string, string> };
  inspections: Insp[];
};
type Impact = {
  suspect: { id: number; batch_no: string; item_name: string; kind: string; unit: string; qc_status: string; physical: string; available: string; reserved: string; qc_held: string; supplier_lot_no: string | null };
  receipt: { gr_number: string | null; received_at: string | null; po_number: string | null; supplier_name: string | null; supplier_lot_no: string | null; qty: string } | null;
  orders: { id: number; number: string; status: string; consumed_suspect_qty: string; actual_qty: string; output_batch_no: string | null; href: string }[];
  affected_batches: { id: number; batch_no: string; item_name: string; kind: string; unit: string; qc_status: string; production_order: string | null; produced: string; physical: string; available: string; reserved: string; qc_held: string; dispatched: string; delivered: string; returned: string; href: string }[];
  customers: { customer_id: number; customer: string | null; sales_order: string; channel: string; shipment: string | null; shipment_status: string; batch_no: string; qty: string; delivered_at: string | null }[];
  totals_finished_goods: Record<string, string>; summary: Record<string, number | string>; truncated: boolean; basis: string;
};
type Tab = "movements" | "inspections" | "locations" | "history" | "impact";
const DEF_LABEL: [string, string][] = [["physical", "Physical"], ["reserved", "Reserved"], ["blocked", "Blocked (QC)"], ["available", "Available"], ["consumed_in_production", "Consumed"], ["dispatched", "Dispatched"], ["delivered", "Delivered"], ["returned_to_stock", "Returned"]];

export function BatchPage({ id }: { id: number }) {
  const { can } = useAuth();
  const router = useRouter();
  const { data: d, error, loading } = useGet<Detail>(`/traceability/batches/${id}`);
  const [dir, setDir] = useState<"both" | "backward" | "forward">("both");
  const graph = useGet<GraphData>(`/traceability/batches/${id}/graph`, { direction: dir });
  const [tab, setTab] = useState<Tab>("movements");

  if (loading) return <div className="space-y-4 pt-6"><div className="skeleton h-10 w-72" /><div className="skeleton h-24" /><div className="skeleton h-[420px]" /></div>;
  if (error || !d) return <div className="pt-8"><PageHeader title="Batch traceability" /><p role="alert" className="panel p-5 text-danger">{error ?? "Batch not found"}</p></div>;
  const b = d.batch, r = d.reconciliation, q = r.quantities;
  const canImpact = can("inventory.batch:qc_release");
  const tabs: { id: Tab; label: string }[] = [{ id: "movements", label: "Movements" }, { id: "inspections", label: `Inspections (${d.inspections.length})` }, { id: "locations", label: "Locations" }, { id: "history", label: "Audit trail" }, ...(canImpact ? [{ id: "impact" as Tab, label: "Impact analysis" }] : [])];
  return (
    <div className="space-y-5 pb-10">
      <PageHeader title={b.batch_no} description={`${b.item_name} · ${b.item_code} · ${b.kind_label}`}
        actions={<div className="flex flex-wrap gap-2"><Link className="btn" href={`/inventory/products/${b.item_id}/overview`}>Product 360</Link><Link className="btn" href="/inventory/batches">All batches</Link></div>} />
      <div className="flex flex-wrap items-center gap-2 -mt-2">
        <Badge tone={toneFor(b.qc_status)}>{titleCase(b.qc_status)}</Badge><span className="badge tone-neutral">{b.business_status}</span>
        {b.expiry_date && <span className="badge tone-neutral">Expires {fmtDate(b.expiry_date)}</span>}
      </div>

      {!r.balanced && (
        <div role="alert" className="panel p-4 border-[var(--danger)] flex gap-3">
          <AlertTriangle size={20} className="text-danger shrink-0 mt-0.5" aria-hidden />
          <div><h2 className="t-section text-danger">Quantities do not reconcile</h2><p className="t-support mt-0.5">These records disagree. Nothing has been corrected; this page only reports what is recorded.</p>
            <ul className="mt-2 text-[0.8125rem] list-disc pl-5 space-y-0.5">{r.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
        </div>
      )}

      <section aria-label="Quantities" className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        {DEF_LABEL.map(([k, label]) => <Stat key={k} label={label} value={qty(q[k], r.unit)} note={r.definitions[k]} tone={k === "blocked" && Number(q[k]) > 0 ? "warn" : undefined} />)}
      </section>

      <div className="grid lg:grid-cols-2 gap-5">
        <Section title="Identity">
          <div className="px-4 pb-3"><KV single rows={[
            ["Batch type", b.kind_label], ["Product", <Link key="p" className="text-[var(--brand)] hover:underline" href={`/inventory/products/${b.item_id}/overview`}>{b.item_name}</Link>],
            ["Supplier", b.supplier_name], ["Supplier lot", b.supplier_lot_no],
            ["Goods receipt", d.source_receipt ? `${d.source_receipt.number} · ${fmtDate(d.source_receipt.received_at)}` : null], ["Purchase order", d.source_receipt?.po_number],
            ["Production order", d.production_order ? <Link key="o" className="text-[var(--brand)] hover:underline" href={`/m/production/orders?open=${d.production_order.id}`}>{d.production_order.number}</Link> : null],
            ["Recipe", d.production_order ? [d.production_order.formulation && `${d.production_order.formulation} v${d.production_order.formulation_version}`, d.production_order.bom_version && `BOM v${d.production_order.bom_version}`].filter(Boolean).join(" · ") : null],
            ["Manufactured / received", fmtDate(b.mfg_date ?? b.received_at)], ["Expiry", b.expiry_date ? fmtDate(b.expiry_date) : null],
            ["QC decision", b.qc_decided_at ? `${fmtDateTime(b.qc_decided_at)}${b.qc_decided_by ? ` · ${b.qc_decided_by}` : ""}` : "Not decided yet"],
          ]} /></div>
        </Section>
        <Section title="Quantity reconciliation" hint="Each line compares two figures that are recorded separately. A difference is shown, never adjusted.">
          <div className="overflow-auto"><table className="dt"><thead><tr><th>Check</th><th className="num">Expected</th><th className="num">Recorded</th><th>Result</th></tr></thead>
            <tbody>{r.checks.map((c) => <tr key={c.name} title={c.note}><td className="wrap">{c.name}</td><td className="num">{qty(c.expected, r.unit)}</td><td className="num">{qty(c.actual, r.unit)}</td><td>{c.ok ? <Badge tone="success">Balanced</Badge> : <Badge tone="danger">Off by {qty(c.difference, r.unit)}</Badge>}</td></tr>)}</tbody></table></div>
          <p className="t-support px-4 py-3 border-t border-line">Received {qty(q.received, r.unit)} · produced {qty(q.produced, r.unit)} · consumed {qty(q.consumed_in_production, r.unit)} · dispatched {qty(q.dispatched, r.unit)} · returned to stock {qty(q.returned_to_stock, r.unit)} · adjustments {qty(q.adjustments_net, r.unit)}</p>
        </Section>
      </div>

      <Section title="Genealogy" hint="Built from goods receipts, actual material consumption, production output, shipments and returns. Planned BOM items are not shown unless they were consumed."
        actions={<div className="seg" role="group" aria-label="Direction">{(["both", "backward", "forward"] as const).map((x) => <button key={x} type="button" aria-pressed={dir === x} onClick={() => setDir(x)}>{x === "both" ? "Both" : x === "backward" ? "Came from" : "Went to"}</button>)}</div>}>
        <State loading={graph.loading && !graph.data} error={graph.error}>{graph.data && <div className="px-3 pb-3"><Genealogy data={graph.data} onOpenBatch={(bid) => router.push(`/inventory/batches/${bid}/traceability`)} /></div>}</State>
      </Section>

      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      {tab === "movements" && <Movements id={id} unit={r.unit} />}
      {tab === "inspections" && <Inspections list={d.inspections} />}
      {tab === "locations" && (
        <Section title="Where the stock is" hint="One batch can be held in several warehouses and locations after a transfer; it remains one batch with one identity.">
          <State loading={false} error={null} empty={d.locations.length === 0 && "No stock is held for this batch."}>
            <div className="overflow-auto"><table className="dt"><thead><tr><th>Warehouse</th><th>Location</th><th className="num">Physical</th><th className="num">Reserved</th><th className="num">Blocked</th><th className="num">Available</th></tr></thead>
              <tbody>{d.locations.map((l, i) => <tr key={i}><td>{l.warehouse} · {l.warehouse_name}</td><td>{l.location ?? "—"}</td><td className="num">{qty(l.physical, r.unit)}</td><td className="num">{qty(l.reserved, r.unit)}</td><td className="num">{qty(l.blocked, r.unit)}</td><td className="num">{qty(l.available, r.unit)}</td></tr>)}</tbody></table></div>
          </State>
        </Section>
      )}
      {tab === "history" && <History id={id} />}
      {tab === "impact" && canImpact && <ImpactPanel id={id} />}
    </div>
  );
}

function Movements({ id, unit }: { id: number; unit: string }) {
  const [page, setPage] = useState(1);
  const { data, error, loading } = useGet<{ items: { id: number; posted_at: string; movement_type: string; source_type: string; source_id: number; qty_delta: string; blocked_delta: string; reason: string | null; warehouse: string; actor: string | null }[]; total: number }>(`/traceability/batches/${id}/movements`, { page, page_size: 15 });
  return (
    <Section title="Stock movements" hint="The append-only stock ledger for this batch, newest first.">
      <State loading={loading && !data} error={error} empty={data?.total === 0 && "No movements recorded."}>
        {data && <><div className="overflow-auto"><table className="dt"><thead><tr><th>When</th><th>Movement</th><th>Source</th><th>Warehouse</th><th className="num">Quantity</th><th>By</th></tr></thead>
          <tbody>{data.items.map((m) => <tr key={m.id}><td>{fmtDateTime(m.posted_at)}</td><td>{titleCase(m.movement_type)}</td><td className="text-muted">{titleCase(m.source_type)} {m.source_id}</td><td>{m.warehouse}</td><td className="num">{Number(m.qty_delta) > 0 ? "+" : ""}{qty(m.qty_delta, unit)}</td><td>{m.actor ?? "System"}</td></tr>)}</tbody></table></div>
          <Pager page={page} total={data.total} size={15} onPage={setPage} /></>}
      </State>
    </Section>
  );
}

function Inspections({ list }: { list: Insp[] }) {
  return (
    <Section title="Quality inspections" hint="Every inspection ever raised for this batch. A failed result stays visible after a retest passes.">
      <State loading={false} error={null} empty={list.length === 0 && "No inspection was raised for this batch."}>
        <div className="px-4 pb-4 space-y-4">
          {list.map((i) => (
            <article key={i.id} className="inset p-3">
              <div className="flex flex-wrap items-center gap-2"><Link href={`/m/quality/inspections?open=${i.id}`} className="font-semibold hover:underline">{i.number}</Link><span className="t-support">{titleCase(i.stage)}</span>
                <Badge tone={toneFor(i.decision ?? i.status)}>{titleCase(i.decision ?? i.status)}</Badge>{i.retested && <span className="badge tone-warning">Retested</span>}{i.failed_results > 0 && <span className="badge tone-danger">{i.failed_results} failed result{i.failed_results > 1 ? "s" : ""}</span>}
                <span className="t-support ml-auto">{i.decided_at ? `${fmtDateTime(i.decided_at)} · ${i.decided_by ?? ""}` : `Raised ${fmtDate(i.created_at)}`}</span></div>
              {i.decision_reason && <p className="t-support mt-1">Reason: {i.decision_reason}</p>}
              <table className="dt mt-2"><thead><tr><th>Test</th><th>Value</th><th>Result</th><th>Recorded</th><th>By</th></tr></thead>
                <tbody>{i.results.map((x, k) => <tr key={k}><td>{x.test}</td><td>{x.value ?? x.observation ?? "—"}</td><td>{x.passed === null ? "—" : x.passed ? <Badge tone="success">Pass</Badge> : <Badge tone="danger">Fail</Badge>}</td><td>{fmtDateTime(x.tested_at)}</td><td>{x.tested_by ?? "—"}</td></tr>)}</tbody></table>
            </article>
          ))}
        </div>
      </State>
    </Section>
  );
}

function History({ id }: { id: number }) {
  const { data, error, loading } = useGet<{ items: { occurred_at: string; user_email: string | null; action: string; entity: string; entity_id: string; reason: string | null }[] }>(`/traceability/batches/${id}/history`);
  return (
    <Section title="Audit trail" hint="Quality decisions, inspections and impact analyses recorded for this batch.">
      <State loading={loading} error={error} empty={data?.items.length === 0 && "No audited events yet."}>
        {data && <div className="overflow-auto"><table className="dt"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Reason</th></tr></thead>
          <tbody>{data.items.map((h, i) => <tr key={i}><td>{fmtDateTime(h.occurred_at)}</td><td>{h.user_email ?? "System"}</td><td>{titleCase(h.action)}</td><td className="text-muted">{titleCase(h.entity)} {h.entity_id}</td><td>{h.reason ?? ""}</td></tr>)}</tbody></table></div>}
      </State>
    </Section>
  );
}

function ImpactPanel({ id }: { id: number }) {
  const toast = useToast();
  const [data, setData] = useState<Impact | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState<Set<number>>(new Set());
  const [confirm, setConfirm] = useState(false);
  const [holding, setHolding] = useState(false);
  const run = async () => {
    setBusy(true); setError(null);
    try { setData(await api<Impact>(`/traceability/batches/${id}/impact-analysis`, { method: "POST" })); setPick(new Set()); }
    catch (e) { setError(e instanceof ApiError ? e.message : "The analysis could not be run"); } finally { setBusy(false); }
  };
  /** The only consequential action here, and only after the user confirms: place each chosen batch on QC HOLD through the existing QC decision workflow (its own permission, state rules and audit). */
  const hold = async () => {
    setHolding(true); let ok = 0; const failed: string[] = [];
    for (const bid of pick) {
      try { await api(`/inventory/batches/${bid}/qc-decision`, { method: "POST", headers: { "Idempotency-Key": `hold-${bid}-${Date.now()}` }, body: { decision: "HOLD", reason: `Placed on hold from batch impact analysis of ${data?.suspect.batch_no}` } }); ok++; }
      catch (e) { failed.push(`${data?.affected_batches.find((a) => a.id === bid)?.batch_no ?? bid}: ${e instanceof ApiError ? e.message : "failed"}`); }
    }
    setHolding(false); setConfirm(false);
    toast(failed.length ? "error" : "success", failed.length ? `${ok} placed on hold; ${failed.join("; ")}` : `${ok} batch${ok === 1 ? "" : "es"} placed on QC hold`);
    await run();
  };
  const holdable = (data?.affected_batches ?? []).filter((a) => ["RELEASED", "QUARANTINE"].includes(a.qc_status));
  return (
    <Section title="Batch impact analysis" hint="Finds every production order, finished batch, shipment and customer downstream of this batch. Running it changes no stock and no status; the run itself is recorded in the audit trail."
      actions={<button className="btn btn-primary btn-sm" onClick={run} disabled={busy}>{busy ? "Analysing…" : data ? "Run again" : "Run impact analysis"}</button>}>
      <State loading={false} error={error} empty={!data && "Not run yet. Use this when a supplier or a test reports a problem with this batch."}>
        {data && (
          <div className="px-4 pb-4 space-y-5">
            <p className="t-support">{data.basis}</p>
            <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
              <Stat label="Orders" value={String(data.summary.production_orders)} note="Production orders that consumed the suspect batch" /><Stat label="Finished batches" value={String(data.summary.finished_batches)} /><Stat label="Customers" value={String(data.summary.customers)} />
              <Stat label="Shipments" value={String(data.summary.shipments)} /><Stat label="Suspect left" value={qty(data.summary.suspect_remaining as string, data.suspect.unit)} note="Still in stock from the suspect batch" /><Stat label="Suspect used" value={qty(data.summary.suspect_consumed as string, data.suspect.unit)} note="Consumed in production" />
            </div>
            <h3 className="t-section -mb-2">Finished goods made from it, in finished-goods units</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
              {([["produced", "Produced"], ["available", "Available"], ["reserved", "Reserved"], ["qc_held", "QC held"], ["dispatched", "Dispatched"], ["delivered", "Delivered"], ["returned", "Returned"]] as const).map(([k, l]) => <Stat key={k} label={l} value={qty(data.totals_finished_goods[k])} />)}
            </div>
            {data.receipt && <p className="text-[0.8125rem]">Entered stock on <b>{data.receipt.gr_number}</b>{data.receipt.po_number && <> against <b>{data.receipt.po_number}</b></>}{data.receipt.supplier_name && <> from <b>{data.receipt.supplier_name}</b></>}{data.receipt.supplier_lot_no && <>, supplier lot <b>{data.receipt.supplier_lot_no}</b></>} ({qty(data.receipt.qty, data.suspect.unit)}).</p>}
            <div><h3 className="t-section mb-2">Production orders that consumed it</h3>
              <div className="overflow-auto panel"><table className="dt"><thead><tr><th>Order</th><th>Status</th><th className="num">Suspect consumed</th><th className="num">Order output</th><th>Resulting batch</th></tr></thead>
                <tbody>{data.orders.map((o) => <tr key={o.id}><td><Link className="font-medium hover:underline" href={o.href}>{o.number}</Link></td><td>{titleCase(o.status)}</td><td className="num">{qty(o.consumed_suspect_qty, data.suspect.unit)}</td><td className="num">{qty(o.actual_qty)}</td><td>{o.output_batch_no ?? "—"}</td></tr>)}{data.orders.length === 0 && <tr><td colSpan={5} className="text-muted">Not consumed by any production order.</td></tr>}</tbody></table></div></div>
            <div><div className="flex items-center justify-between mb-2"><h3 className="t-section">Affected batches (potentially, whole batch)</h3>
              {holdable.length > 0 && <button className="btn btn-sm" disabled={pick.size === 0} onClick={() => setConfirm(true)}><ShieldAlert size={15} aria-hidden />Prepare QC hold{pick.size ? ` (${pick.size})` : ""}</button>}</div>
              <div className="overflow-auto panel"><table className="dt"><thead><tr><th /><th>Batch</th><th>Product</th><th>QC</th><th className="num">Produced</th><th className="num">Available</th><th className="num">Reserved</th><th className="num">QC held</th><th className="num">Dispatched</th><th className="num">Delivered</th><th className="num">Returned</th></tr></thead>
                <tbody>{data.affected_batches.map((a) => <tr key={a.id}>
                  <td>{["RELEASED", "QUARANTINE"].includes(a.qc_status) && <input type="checkbox" aria-label={`Select ${a.batch_no} for QC hold`} checked={pick.has(a.id)} onChange={(e) => setPick((p) => { const n = new Set(p); e.target.checked ? n.add(a.id) : n.delete(a.id); return n; })} />}</td>
                  <td><Link className="font-medium hover:underline" href={`/inventory/batches/${a.id}/traceability`}>{a.batch_no}</Link></td><td>{a.item_name}</td><td><Badge tone={toneFor(a.qc_status)}>{titleCase(a.qc_status)}</Badge></td>
                  <td className="num">{qty(a.produced, a.unit)}</td><td className="num">{qty(a.available, a.unit)}</td><td className="num">{qty(a.reserved, a.unit)}</td><td className="num">{qty(a.qc_held, a.unit)}</td><td className="num">{qty(a.dispatched, a.unit)}</td><td className="num">{qty(a.delivered, a.unit)}</td><td className="num">{qty(a.returned, a.unit)}</td></tr>)}
                  {data.affected_batches.length === 0 && <tr><td colSpan={11} className="text-muted">No other batch was made from this one.</td></tr>}</tbody></table></div></div>
            <div><h3 className="t-section mb-2">Customers who received affected stock</h3>
              <div className="overflow-auto panel"><table className="dt"><thead><tr><th>Customer</th><th>Sales order</th><th>Channel</th><th>Shipment</th><th>Batch</th><th className="num">Quantity</th><th>Delivered</th></tr></thead>
                <tbody>{data.customers.map((c, i) => <tr key={i}><td className="font-medium">{c.customer}</td><td>{c.sales_order}</td><td>{titleCase(c.channel)}</td><td>{c.shipment ?? "—"} <span className="text-muted">{titleCase(c.shipment_status)}</span></td><td>{c.batch_no}</td><td className="num">{qty(c.qty)}</td><td>{c.delivered_at ? fmtDate(c.delivered_at) : "Not delivered"}</td></tr>)}{data.customers.length === 0 && <tr><td colSpan={7} className="text-muted">Nothing downstream has been shipped.</td></tr>}</tbody></table></div></div>
          </div>
        )}
      </State>
      <ConfirmDialog open={confirm} title={`Place ${pick.size} batch${pick.size === 1 ? "" : "es"} on QC hold?`} confirmLabel="Place on hold" danger busy={holding} onCancel={() => setConfirm(false)} onConfirm={hold}
        body={<div className="space-y-2"><p>Held stock cannot be issued, reserved or dispatched until Quality releases it again. This uses the normal QC decision workflow and is audited. No customer is contacted and no recall is started.</p>
          <ul className="list-disc pl-5">{[...pick].map((bid) => <li key={bid}>{data?.affected_batches.find((a) => a.id === bid)?.batch_no}</li>)}</ul></div>} />
    </Section>
  );
}
