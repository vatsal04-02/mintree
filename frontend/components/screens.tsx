"use client";
/** The three module screens from the design spec (docs/DESIGN_SYSTEM.md, screens 2-4): Production & Batch Runs, Material Procurement & Stock Matrix,
 *  and Omni-Channel Dispatch & Invoicing. Every figure, row and name comes from the API; instrument readings, SLA percentages and the MRP note come from
 *  lib/demo-telemetry.ts and carry a visible "Simulated"/"sim" mark. */
import { useMemo, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ClipboardList, FileText, Package, Plus, Printer, Search, Truck } from "lucide-react";
import { fmtDate, fmtNum, fmtPct, fmtRupees, titleCase } from "@/lib/format";
import { Strip } from "./kpi";
import { Pipeline } from "./blocks";
import { COURIER_SLA, EQUIPMENT_TILES, PLANT, WEIGH_SCALE } from "@/lib/demo-telemetry";
import { Sim, useShared, type Row } from "./command";


type To = (m: string, s: string) => string | undefined;
const n = (v: unknown) => Number(v ?? 0) || 0;
const lakhs = (v: number) => (v >= 1e7 ? `₹${fmtNum(v / 1e7, 2)} Cr` : v >= 1e5 ? `₹${fmtNum(v / 1e5, 2)} Lakhs` : fmtRupees(v));
const short = (v: number) => (v >= 1e7 ? `₹${fmtNum(v / 1e7, 2)}Cr` : v >= 1e5 ? `₹${fmtNum(v / 1e5, 1)}L` : fmtRupees(v));
const Skel = ({ h = "h-40" }: { h?: string }) => <div className={`skeleton ${h} rounded-xl`} />;

function Head({ eyebrow, tags, title, description, aside, actions }: { eyebrow?: string; tags?: React.ReactNode; title: string; description: string; aside?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap xl:flex-nowrap items-start justify-between gap-x-6 gap-y-4 mb-5">
      <div className="min-w-0 flex-1 basis-[420px]">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        {tags && <div className="flex flex-wrap items-center gap-2 mt-1.5">{tags}</div>}
        <h1 className="t-page mt-1.5">{title}</h1>
        <p className="t-label mt-1.5">{description}</p>
      </div>
      {(aside || actions) && <div className="flex flex-wrap items-start justify-end gap-2.5 shrink-0">{aside}{actions}</div>}
    </div>
  );
}
/* ================================================================== Production */
export function ProductionScreen({ to }: { to: To }) {
  const d = useShared<Row>("/production/floor");
  const [tab, setTab] = useState<"active" | "scheduled">("active");
  const [q, setQ] = useState("");
  const orders: Row[] = d?.orders ?? [];
  const active = orders.filter((o) => o.status !== "PLANNED"), scheduled = orders.filter((o) => o.status === "PLANNED");
  const shown = (tab === "active" ? active : scheduled).filter((o) => !q || `${o.number} ${o.item_name} ${o.item_code}`.toLowerCase().includes(q.toLowerCase()));
  const ordersHref = to("production", "orders");
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 mb-5">
        <div className="min-w-0">
          <h1 className="t-page">Production</h1>
          <p className="t-label mt-1">Batches on the floor, the work queued behind them, equipment and material dispensing.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {ordersHref && <Link href={`${ordersHref}?status=COMPLETED`} className="btn">Batch records</Link>}
          {ordersHref && <Link href={ordersHref} className="btn btn-primary gap-1.5"><Plus size={15} />New production batch</Link>}
        </div>
      </div>

      <Strip cards={d === undefined ? null : [
        { label: "Running now", value: fmtNum(n(d?.counts?.IN_PROGRESS)), foot: "Batches in progress on the floor" },
        { label: "Released", value: fmtNum(n(d?.counts?.RELEASED)), foot: "Ready to start" },
        { label: "Scheduled", value: fmtNum(scheduled.length), foot: scheduled[0]?.planned_date ? `Next on ${fmtDate(scheduled[0].planned_date)}` : "Nothing planned" },
        { label: "Plant OEE", value: PLANT.oee, chip: "Simulated reading", tone: "warning", foot: "Not connected to line telemetry" },
      ]} />

      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div className="seg" role="tablist" aria-label="Batches">
          <button type="button" role="tab" aria-pressed={tab === "active"} aria-selected={tab === "active"} onClick={() => setTab("active")}>On the floor ({active.length})</button>
          <button type="button" role="tab" aria-pressed={tab === "scheduled"} aria-selected={tab === "scheduled"} onClick={() => setTab("scheduled")}>Scheduled ({scheduled.length})</button>
        </div>
        <label className="relative"><span className="sr-only">Filter batches</span><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden /><input className="input !h-9 !pl-8 !w-56" placeholder="Batch number, product or SKU…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      </div>

      {d === undefined ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skel key={i} h="h-56" />)}</div>
        : shown.length === 0 ? <p className="panel p-6 t-label">No {tab === "active" ? "running or released" : "scheduled"} batch matches.</p> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((o) => {
            const stages: Row[] = o.stages ?? [];
            const done = stages.filter((s) => s.status === "COMPLETED").length;
            const cur = stages.find((s) => s.status === "IN_PROGRESS") ?? stages.find((s) => s.status === "PENDING");
            const issuePct = n(o.required_qty) ? Math.min(100, Math.round((n(o.issued_qty) / n(o.required_qty)) * 100)) : 0;
            const live = o.status === "IN_PROGRESS";
            return (
              <article key={o.id} className="panel p-4 flex flex-col gap-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[0.8125rem] font-semibold">{o.number}</span>
                  <span className={`badge tone-${live ? "success" : o.status === "RELEASED" ? "info" : "neutral"}`}>{live ? "In progress" : o.status === "RELEASED" ? "Released" : "Planned"}</span>
                </div>
                <div>
                  <h3 className="text-[0.9375rem] font-semibold leading-5">{o.item_name}</h3>
                  <p className="t-support mt-0.5">{o.item_code} · {fmtNum(n(o.planned_qty))} {String(o.uom ?? "units").toLowerCase()}{o.planned_date ? ` · planned ${fmtDate(o.planned_date)}` : ""}</p>
                </div>
                <div>
                  <div className="flex justify-between t-support"><span>Materials issued</span><span className="font-mono text-fg">{issuePct}%</span></div>
                  <div className="progress mt-1"><span style={{ width: `${issuePct}%` }} /></div>
                </div>
                <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[0.8125rem]">
                  <dt className="text-muted">Stage</dt><dd className="font-medium truncate">{cur ? titleCase(cur.name) : "All complete"} <span className="text-muted">· {done} of {stages.length}</span></dd>
                  <dt className="text-muted">Operator</dt><dd className="font-medium truncate">{cur?.operator ?? "Not assigned"}</dd>
                  <dt className="text-muted">Quality</dt><dd className="font-medium truncate">{o.yield_pct ? `Yield ${fmtPct(n(o.yield_pct))}` : live ? "In-process QC pending" : "Not started"}</dd>
                </dl>
                <div className="mt-auto pt-3 border-t border-line flex items-center justify-between gap-2">
                  {ordersHref && <Link href={`${ordersHref}?open=${o.id}`} className="text-[0.8125rem] font-medium text-brand hover:underline">Open batch record</Link>}
                  <a href={`/api/labels/production-order/${o.id}`} target="_blank" rel="noopener" className="btn btn-sm">Job card</a>
                </div>
              </article>
            );
          })}
          {tab === "active" && !q && shown.length % 3 !== 0 && (
            <article className={`panel p-4 flex flex-col ${shown.length % 3 === 1 ? "xl:col-span-2" : ""}`} aria-label="Next scheduled work orders">
              <div className="flex items-baseline justify-between gap-2"><h3 className="text-[0.9375rem] font-semibold">Next scheduled</h3><span className="t-support">{fmtNum(scheduled.length)} planned</span></div>
              {scheduled.length === 0 ? <p className="t-support mt-2">No work orders are scheduled.</p> : (
                <ul className="mt-2 divide-y divide-line">
                  {scheduled.slice(0, 5).map((o) => (
                    <li key={o.id} className="py-2 flex items-baseline justify-between gap-3 text-[0.8125rem]">
                      <span className="min-w-0 truncate"><span className="font-mono text-[0.75rem] text-muted mr-2">{o.number}</span>{titleCase(String(o.item_name ?? ""))}</span>
                      <span className="t-support shrink-0">{o.planned_date ? fmtDate(o.planned_date) : "Not dated"}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-auto pt-3 border-t border-line flex items-center justify-between gap-2">
                <button type="button" onClick={() => setTab("scheduled")} className="text-[0.8125rem] font-medium text-brand hover:underline">View all scheduled</button>
                {ordersHref && <Link href={ordersHref} className="btn btn-sm">Plan a batch</Link>}
              </div>
            </article>
          )}
        </div>
      )}

      {d && (d.equipment ?? []).length > 0 && (
        <section className="panel overflow-hidden mt-4" aria-label="Equipment">
          <header className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-3"><div><h2 className="t-section">Equipment</h2><p className="t-support">Status and the next preventive maintenance. Health readings are simulated.</p></div>{to("maintenance", "equipment") && <Link href={to("maintenance", "equipment")!} className="text-[0.8125rem] font-medium text-brand hover:underline shrink-0">All equipment</Link>}</header>
          <div className="overflow-x-auto"><table className="dt">
            <thead><tr><th>Equipment</th><th>Location</th><th>Status</th><th>Next maintenance</th><th>Health <span className="text-[#8a5a00] font-normal">· sim</span></th></tr></thead>
            <tbody>{(d.equipment as Row[]).map((e, i) => (
              <tr key={e.id}>
                <td><span className="block font-medium">{e.name}</span><span className="block t-support font-mono">{e.asset_id}</span></td>
                <td className="text-muted">{e.location ?? "—"}</td>
                <td><span className={`badge tone-${e.status === "OPERATIONAL" ? "success" : e.status === "BREAKDOWN" ? "danger" : "warning"}`}>{titleCase(e.status)}</span></td>
                <td>{e.next_pm ? fmtDate(e.next_pm) : e.calibration_due ? `Calibration ${fmtDate(e.calibration_due)}` : <span className="text-muted">Not scheduled</span>}</td>
                <td className="text-muted">{EQUIPMENT_TILES[i % EQUIPMENT_TILES.length].health}</td>
              </tr>))}
            </tbody></table></div>
        </section>
      )}

      {d && d.dispensing_order && (
        <section className="panel overflow-hidden mt-4" aria-label="Dispensing and lot traceability">
          <header className="flex flex-wrap items-start justify-between gap-3 px-5 pt-4 pb-3">
            <div className="min-w-0">
              <h2 className="t-section">Dispensing &amp; lot traceability</h2>
              <p className="t-support mt-0.5">Materials issued to {d.dispensing_order.item_name} ({d.dispensing_order.number}), each against its lot and the person who issued it.</p>
            </div>
            <div className="flex gap-2">
              <a href={`/api/labels/production-order/${d.dispensing_order.id}`} target="_blank" rel="noopener" className="btn btn-sm gap-1.5"><Printer size={14} />Weighing slip</a>
              {ordersHref && <Link href={`${ordersHref}?open=${d.dispensing_order.id}`} className="btn btn-sm">Batch record</Link>}
            </div>
          </header>
          <div className="overflow-x-auto">
            <table className="dt">
              <thead><tr><th>Material</th><th>Lot</th><th className="num">Required</th><th className="num">Dispensed</th><th>Issued by</th><th>Status</th></tr></thead>
              <tbody>
                {(d.dispensing as Row[]).map((m) => {
                  const full = n(m.issued_qty) >= n(m.required_qty) && n(m.required_qty) > 0, some = n(m.issued_qty) > 0;
                  return (
                    <tr key={m.item_code}>
                      <td><span className="block font-medium">{m.name}</span><span className="block t-support font-mono">{m.item_code}</span></td>
                      <td><span className="block font-mono text-[0.75rem]">{m.lots ?? "Not issued"}</span>{m.lot_qc && <span className={`block text-[0.6875rem] ${m.lot_qc.includes("RELEASED") ? "text-muted" : "text-warning"}`}>{m.lot_qc.includes("RELEASED") ? "CoA released" : titleCase(m.lot_qc)}</span>}</td>
                      <td className="num font-mono">{fmtNum(n(m.required_qty), 2)} {String(m.uom ?? "").toLowerCase()}</td>
                      <td className="num font-mono">{fmtNum(n(m.issued_qty), 2)} {String(m.uom ?? "").toLowerCase()}</td>
                      <td>{m.issued_by ?? <span className="text-muted">Awaiting issue</span>}</td>
                      <td><span className={`badge tone-${full ? "success" : some ? "warning" : "neutral"}`}>{full ? "Weighed & staged" : some ? "Partly issued" : "Awaiting"}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <footer className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-t border-line t-support">
            <span>Every issue is written to the append-only stock ledger and audit log.</span>
            <Link href="/inventory/batches" className="font-medium text-brand hover:underline">Traceability log</Link>
          </footer>
        </section>
      )}
    </>
  );
}

/* ================================================================== Purchase & Procurement */
const STATE: Record<string, { label: string; tone: string }> = { BELOW: { label: "Below reorder", tone: "danger" }, SOON: { label: "Reorder soon", tone: "warning" }, HEALTHY: { label: "Healthy", tone: "neutral" } };
const RISK: Record<string, number> = { BELOW: 0, SOON: 1, HEALTHY: 2 };
type MatSort = { key: string; dir: 1 | -1 };

export function PurchaseScreen({ to }: { to: To }) {
  const d = useShared<Row>("/procurement/vault");
  const [kind, setKind] = useState<"ALL" | "RM" | "PM">("ALL");
  const [q, setQ] = useState("");
  const [lowOnly, setLowOnly] = useState(false);
  const [sort, setSort] = useState<MatSort>({ key: "risk", dir: 1 });
  const [page, setPage] = useState(0);
  const per = 10;
  const rows: Row[] = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = (d?.materials ?? []).filter((m: Row) => (kind === "ALL" || m.kind === kind) && (!lowOnly || m.state !== "HEALTHY")
      && (!t || `${m.name} ${m.item_code} ${m.vendor ?? ""} ${m.category ?? ""}`.toLowerCase().includes(t)));
    const val = (m: Row) => (sort.key === "risk" ? RISK[m.state] ?? 3 : sort.key === "name" ? String(m.name) : sort.key === "vendor" ? String(m.vendor ?? "~") : n(m[sort.key]));
    return [...list].sort((a, b) => { const x = val(a), y = val(b); return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sort.dir; });
  }, [d, kind, q, lowOnly, sort]);
  const pages = Math.max(1, Math.ceil(rows.length / per));
  const vbk = d?.value_by_kind ?? {};
  const req = to("procurement", "requests"), orders = to("procurement", "orders"), reorder = to("inventory", "reorder");
  const by = (key: string) => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }));
  const th = (k: string, label: string, num?: boolean) => (
    <th key={k} className={num ? "num" : ""} aria-sort={sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
      <button type="button" className="inline-flex items-center gap-1 hover:text-fg" onClick={() => by(k)}>{label}{sort.key === k && <span aria-hidden>{sort.dir === 1 ? "↑" : "↓"}</span>}</button>
    </th>
  );
  const qty = (v: unknown, uom: unknown) => `${fmtNum(n(v), 1)} ${String(uom ?? "").toLowerCase()}`;
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 mb-5">
        <div className="min-w-0">
          <h1 className="t-page">Purchase &amp; Procurement</h1>
          <p className="t-label mt-1">Manage raw materials, packaging, suppliers and incoming purchases.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {reorder && <Link href={reorder} className="btn">Configure reorder levels</Link>}
          {req && <Link href={req} className="btn btn-primary gap-1.5"><Plus size={15} />Create purchase request</Link>}
        </div>
      </div>

      {d === undefined ? <Skel h="h-96" /> : !d ? <p className="panel p-5 text-danger">The materials summary could not be loaded.</p> : (
        <>
          <Strip cards={[
            { label: "Inventory valuation", value: short(n(d.inventory_value)), foot: `Raw ${short(n(vbk.RM))} · Packaging ${short(n(vbk.PM))}` },
            { label: "Low-stock materials", value: fmtNum(n(d.below_reorder)), chip: n(d.below_reorder) ? "Below reorder level" : "All above reorder level", tone: n(d.below_reorder) ? "danger" : undefined },
            { label: "Open purchase orders", value: fmtNum(n(d.open_orders)), foot: `${short(n(d.open_order_value))} committed` },
            { label: "Overdue inbound deliveries", value: fmtNum(n(d.late_orders)), chip: n(d.late_orders) ? "Past the expected date" : "None overdue", tone: n(d.late_orders) ? "danger" : undefined },
          ]} />

          <div className="grid gap-4 mb-4">
            <section className="panel overflow-hidden" aria-label="Priority replenishment">
              <header className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-3"><div><h2 className="t-section">Priority replenishment</h2><p className="t-support">Materials below or close to their reorder level.</p></div>{reorder && <Link href={reorder} className="text-[0.8125rem] font-medium text-brand hover:underline shrink-0">Reorder levels</Link>}</header>
              {(d.replenish as Row[] ?? []).length === 0 ? <p className="t-support px-5 pb-5">Every material is above its reorder level.</p> : (
                <div className="overflow-x-auto"><table className="dt">
                  <thead><tr><th>Material</th><th className="num">Available</th><th className="num">Reorder at</th><th className="num">On order</th><th>Supplier</th><th className="w-px" /></tr></thead>
                  <tbody>{(d.replenish as Row[]).map((m) => (
                    <tr key={m.id}>
                      <td className="max-w-[12rem]"><span className="block font-medium truncate" title={m.name}>{m.name}</span><span className="block t-support font-mono">{m.item_code} · {m.category ?? (m.kind === "PM" ? "Packaging" : "Raw")}</span></td>
                      <td className={`num font-mono ${m.state === "BELOW" ? "text-danger font-semibold" : ""}`}>{qty(m.free, m.uom)}</td>
                      <td className="num font-mono text-muted">{qty(m.reorder_level, m.uom)}</td>
                      <td className="num font-mono">{n(m.on_order) ? qty(m.on_order, m.uom) : "—"}</td>
                      <td className="max-w-[8rem] truncate" title={m.vendor ?? ""}>{m.vendor ?? <span className="text-muted">No supplier yet</span>}</td>
                      <td className="text-right">{req && <Link href={req} className="btn btn-sm">Request</Link>}</td>
                    </tr>))}
                  </tbody></table></div>
              )}
            </section>

            <section className="panel overflow-hidden" aria-label="Inbound purchases">
              <header className="flex items-baseline justify-between gap-3 px-5 pt-4 pb-3"><div><h2 className="t-section">Inbound purchases</h2><p className="t-support">Open purchase orders by expected date.</p></div>{orders && <Link href={orders} className="text-[0.8125rem] font-medium text-brand hover:underline shrink-0">All orders</Link>}</header>
              {(d.inbound_list as Row[] ?? []).length === 0 ? <p className="t-support px-5 pb-5">No purchase order is waiting for delivery.</p> : (
                <div className="overflow-x-auto"><table className="dt">
                  <thead><tr><th>Purchase order</th><th>Expected</th><th className="num">Received</th><th>Status</th></tr></thead>
                  <tbody>{(d.inbound_list as Row[]).map((p) => {
                    const late = n(p.days_late) > 0;
                    return (
                      <tr key={p.id}>
                        <td className="max-w-[12rem]"><span className="block font-mono text-[0.75rem] font-semibold">{orders ? <Link href={`${orders}?open=${p.id}`} className="text-brand hover:underline">{p.number}</Link> : p.number}</span><span className="block t-support truncate" title={p.supplier}>{p.supplier}</span></td>
                        <td className={late ? "text-danger" : ""}>{p.expected_date ? fmtDate(p.expected_date) : "—"}{late && <span className="block text-[0.6875rem]">{fmtNum(n(p.days_late))} days late</span>}</td>
                        <td className="num font-mono">{fmtNum(n(p.received), 0)} of {fmtNum(n(p.ordered), 0)}</td>
                        <td><span className={`badge tone-${p.status === "PARTIALLY_RECEIVED" ? "info" : late ? "danger" : "neutral"}`}>{p.status === "PARTIALLY_RECEIVED" ? "Part received" : late ? "Overdue" : "Awaiting"}</span></td>
                      </tr>);
                  })}</tbody></table></div>
              )}
            </section>
          </div>

          <section className="panel overflow-hidden" aria-label="Material matrix">
            <header className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4 pb-3">
              <div><h2 className="t-section">Material matrix</h2><p className="t-support">Stock per material against allocations and the reorder level, with the last supplier and price.</p></div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="seg" role="tablist" aria-label="Material class">
                  {([["ALL", "All materials"], ["RM", "Raw materials"], ["PM", "Packaging"]] as const).map(([k, l]) => <button key={k} type="button" role="tab" aria-pressed={kind === k} aria-selected={kind === k} onClick={() => { setKind(k); setPage(0); }}>{l}</button>)}
                  <Link href="/masters/suppliers" className="px-3 inline-flex items-center text-[0.8125rem] font-medium text-muted hover:text-fg">Suppliers</Link>
                </div>
                <label className="relative"><span className="sr-only">Search materials</span><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden /><input className="input !h-9 !pl-8 !w-52" placeholder="Search material, code, supplier…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} /></label>
                <label className="inline-flex items-center gap-2 text-[0.8125rem] text-muted"><input type="checkbox" checked={lowOnly} onChange={(e) => { setLowOnly(e.target.checked); setPage(0); }} />Low stock only</label>
              </div>
            </header>
            <div className="overflow-x-auto">
              <table className="dt dt-compact">
                <thead><tr>{th("name", "Material")}{th("on_hand", "On hand", true)}{th("allocated", "Allocated", true)}{th("free", "Available", true)}{th("reorder_level", "Reorder at", true)}{th("on_order", "On order", true)}{th("vendor", "Supplier")}{th("last_price", "Unit price", true)}{th("risk", "Status")}</tr></thead>
                <tbody>
                  {rows.length === 0 && <tr><td colSpan={9} className="text-center t-support !h-24">No material matches these filters.</td></tr>}
                  {rows.slice(page * per, page * per + per).map((m) => {
                    const st = STATE[m.state] ?? STATE.HEALTHY;
                    return (
                      <tr key={m.id}>
                        <td className="max-w-[15rem]"><span className="block font-medium truncate" title={m.name}>{m.name}</span><span className="block t-support"><span className="font-mono">{m.item_code}</span> · {m.category ?? (m.kind === "PM" ? "Packaging" : "Raw material")}</span></td>
                        <td className="num font-mono">{qty(m.on_hand, m.uom)}</td>
                        <td className="num font-mono text-muted">{qty(m.allocated, m.uom)}</td>
                        <td className={`num font-mono ${m.state === "BELOW" ? "text-danger font-semibold" : ""}`}>{qty(m.free, m.uom)}</td>
                        <td className="num font-mono text-muted">{m.reorder_level ? qty(m.reorder_level, m.uom) : "—"}</td>
                        <td className="num font-mono">{n(m.on_order) ? qty(m.on_order, m.uom) : "—"}</td>
                        <td className="max-w-[12rem]">{m.vendor ? <><span className="block truncate" title={m.vendor}>{m.vendor}</span>{m.vendor_city && <span className="block t-support">{m.vendor_city}</span>}</> : <span className="text-muted">—</span>}</td>
                        <td className="num font-mono">{m.last_price ? `${fmtRupees(n(m.last_price))}/${String(m.uom ?? "").toLowerCase()}` : "—"}</td>
                        <td><span className={`badge tone-${st.tone}`}>{st.label}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <footer className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-t border-line">
              <span className="t-support">{rows.length ? `${page * per + 1}–${Math.min(rows.length, page * per + per)} of ${rows.length}` : "0"} materials</span>
              <div className="flex items-center gap-1.5">
                <button type="button" className="btn btn-sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
                <span className="t-support tnum px-1">Page {page + 1} / {pages}</span>
                <button type="button" className="btn btn-sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>Next</button>
              </div>
            </footer>
          </section>
        </>
      )}
    </>
  );
}

/* ================================================================== Omni-Channel Dispatch & Invoicing */
const CH: Record<string, { name: string; color: string; bg: string; fg: string }> = {
  AMAZON: { name: "Amazon.in", color: "#f59e0b", bg: "#fef3c7", fg: "#b45309" }, NYKAA: { name: "Nykaa Retail", color: "#ec4899", bg: "#fce7f3", fg: "#be185d" },
  FLIPKART: { name: "Flipkart", color: "#3b82f6", bg: "#dbeafe", fg: "#1d4ed8" }, MYNTRA: { name: "Myntra Luxe", color: "#d946ef", bg: "#fae8ff", fg: "#a21caf" },
  SHOPIFY: { name: "Mintree Store", color: "#059669", bg: "#d1fae5", fg: "#065f46" },
};
const QSTATUS = (o: Row): { label: string; tone: string } => {
  if (o.import_status === "EXCEPTION") return { label: "Exception", tone: "danger" };
  const s = o.ship_status ?? o.so_status;
  const map: Record<string, { label: string; tone: string }> = { BOOKED: { label: "Ready for handover", tone: "success" }, IN_TRANSIT: { label: "In transit", tone: "info" }, DELIVERED: { label: "Delivered", tone: "success" },
    PACKED: { label: "Label printed", tone: "info" }, ALLOCATED: { label: "Pick & pack", tone: "warning" }, CONFIRMED: { label: "Confirmed", tone: "neutral" }, DRAFT: { label: "Awaiting invoice", tone: "neutral" }, DISPATCHED: { label: "Manifested", tone: "info" } };
  return map[s] ?? { label: titleCase(String(s ?? "Received")), tone: "neutral" };
};
export function OmniScreen({ to }: { to: To }) {
  const d = useShared<Row>("/ecommerce/omnihub");
  const [sel, setSel] = useState<Set<number>>(new Set());
  const inv = to("dispatch", "invoices"), ship = to("dispatch", "shipments"), ord = to("ecommerce", "orders");
  const st = d?.stages ?? {};
  const prev = n(d?.previous_gross_value_30d), cur = n(d?.gross_value_30d);
  const delta = prev ? ((cur - prev) / prev) * 100 : null;
  const pipeline = [
    { title: "Order ingestion & payment", value: n(st.ingested), note: `${n(st.ingested) ? fmtPct((n(st.ingested_ok) / n(st.ingested)) * 100) : "0%"} cleared`, icon: CheckCircle2 },
    { title: "Invoice & GST e-way bill", value: n(st.invoiced), note: `${fmtNum(n(st.to_invoice))} awaiting invoice`, icon: FileText },
    { title: "Pick & pack / scan", value: n(st.pick_pack), note: "Active packing", icon: Package, current: true },
    { title: "Manifest & courier staging", value: n(st.staged), note: "Booked, awaiting pickup", icon: ClipboardList },
    { title: "In-transit / delivered", value: n(st.in_transit) + n(st.delivered), note: "Live active parcels", icon: Truck },
  ];
  return (
    <>
      <Head title="E-commerce fulfilment"
        description="Marketplace orders from each channel through GST invoicing, pick and pack, and courier handover."
        actions={<div className="flex flex-wrap gap-2">
          {inv && <Link href={inv} className="btn">GST invoice register</Link>}
          {ship && <Link href={ship} className="btn">Daily manifest</Link>}
          {ship && <Link href={ship} className="btn btn-primary gap-1.5"><Printer size={14} />Print labels</Link>}
        </div>} />

      {d === undefined ? <Skel h="h-96" /> : !d ? <p className="panel p-5 text-danger">The fulfilment summary could not be loaded.</p> : (
        <>
          <Strip cards={[
            ...(d.channels as Row[]).map((c) => ({ label: `${(CH[c.platform]?.name ?? titleCase(c.platform))} pending`, value: fmtNum(n(c.pending)), foot: `of ${fmtNum(n(c.orders))} orders` })),
            { label: "Gross value, 30 days", value: lakhs(cur), chip: delta !== null ? `${delta >= 0 ? "+" : ""}${fmtNum(delta, 1)}% vs prior 30 days` : undefined, tone: delta !== null && delta < 0 ? "danger" : undefined, foot: "Synced from marketplaces" },
            { label: "Parcels to dispatch", value: fmtNum(n(d.to_dispatch)), foot: `${fmtNum(n(st.staged))} booked with couriers` },
          ]} />

          <div className="mb-4">
            <div className="flex items-baseline justify-between gap-3 mb-2"><h2 className="t-section">Fulfilment stages</h2><span className="t-support">Marketplace orders only</span></div>
            <Pipeline stages={pipeline.map((p) => ({ label: p.title, value: fmtNum(p.value), note: p.note }))} />
          </div>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,2.4fr)_minmax(0,1fr)] items-start">
            <section className="panel" aria-label="Live invoicing and dispatch queue">
              <header className="flex flex-wrap items-center justify-between gap-2 p-5 pb-3"><h2 className="t-section">Invoicing and dispatch queue</h2>{ord && <Link href={ord} className="btn btn-sm">All marketplace orders</Link>}</header>
              <div className="overflow-x-auto">
                <table className="dt table-fixed w-full min-w-[46rem]">
                  <colgroup><col className="w-10" /><col className="w-[8.75rem]" /><col /><col /><col className="w-[8.5rem]" /><col className="w-[10.5rem]" /></colgroup>
                  <thead><tr><th><span className="sr-only">Select</span></th><th>Order & channel</th><th>Customer</th><th>Items</th><th>Invoice & AWB</th><th>Status</th></tr></thead>
                  <tbody>
                    {(d.queue as Row[]).map((o) => {
                      const p = CH[o.platform] ?? { name: titleCase(o.platform), color: "#64748b", bg: "#f1f5f9", fg: "#334155" };
                      const [name, city] = String(o.customer_name ?? "—").split(",").map((x: string) => x.trim());
                      const s = QSTATUS(o);
                      return (
                        <tr key={o.id}>
                          <td><input type="checkbox" aria-label={`Select ${o.external_order_id}`} checked={sel.has(o.id)} onChange={(e) => setSel((prev) => { const nx = new Set(prev); if (e.target.checked) nx.add(o.id); else nx.delete(o.id); return nx; })} /></td>
                          <td><span className="block font-mono text-[0.75rem] font-bold">#{o.external_order_id}</span><span className="badge mt-1" style={{ color: "var(--muted)", ["--tone-dot" as string]: p.color } as React.CSSProperties}>{p.name}</span></td>
                          <td><span className="block font-semibold truncate" title={name}>{name}</span><span className="block t-support truncate">{city ?? ""}{o.order_date ? ` · ${fmtDate(o.order_date)}` : ""}</span></td>
                          <td><span className="block text-[0.8125rem] font-semibold truncate" title={o.items ?? ""}>{o.items ?? "—"}</span><span className="block t-support">{fmtRupees(n(o.total))}</span></td>
                          <td>{o.invoice ? <span className="block font-mono text-[0.75rem] font-semibold text-brand">{o.invoice}</span> : <span className="block t-support">Not invoiced</span>}<span className="block t-support font-mono truncate">{o.awb ? `${o.courier ? titleCase(o.courier) + " · " : ""}${o.awb}` : o.courier ? titleCase(o.courier) : o.so_number ?? "—"}</span></td>
                          <td><span className={`badge tone-${s.tone} whitespace-nowrap`}>{s.label}</span></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <footer className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-t border-line">
                <span className="t-support"><b className="text-fg">Selected: {fmtNum(sel.size)}</b> of {fmtNum((d.queue as Row[]).length)} orders shown</span>
                <div className="flex gap-2">{ship && <Link href={ship} className="btn btn-sm">Assign courier batch</Link>}{inv && <Link href={inv} className="btn btn-sm btn-primary">Authorize & print e-way slips</Link>}</div>
              </footer>
            </section>

            <div className="space-y-4">
              <section className="panel p-4" aria-label="Courier SLA matrix">
                <header className="flex items-center justify-between"><h2 className="t-section">Courier performance</h2><Sim /></header>
                <ul className="mt-2 divide-y divide-line">
                  {COURIER_SLA.map((c) => (
                    <li key={c.name} className="py-2 flex items-baseline justify-between gap-3 text-[0.8125rem]">
                      <span className="min-w-0"><span className="block font-medium truncate">{c.name}</span><span className="block t-support">Avg transit {c.transit} · {fmtNum(c.active)} active</span></span>
                      <span className="font-mono font-semibold shrink-0">{fmtNum(c.sla, 1)}%</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-3 pt-3 border-t border-line">
                  <p className="text-[0.75rem] font-medium text-muted">Couriers on record</p>
                  <ul className="mt-1.5 space-y-1">{(d.couriers as Row[]).map((c) => <li key={c.courier} className="flex justify-between t-support"><span className="font-semibold text-fg">{titleCase(String(c.courier))}</span><span>{fmtNum(n(c.active))} active · {fmtNum(n(c.delivered))} delivered</span></li>)}</ul>
                </div>
              </section>
              <section className="panel p-4" aria-label="Weigh-scale guard">
                <header className="flex flex-wrap items-center justify-between gap-2"><h2 className="t-section">Weigh-scale check</h2><span className="flex gap-1.5"><span className="badge tone-danger">{WEIGH_SCALE.length} audits required</span><Sim /></span></header>
                
                <p className="t-support">Parcels more than 50 g away from the bill-of-materials weight before courier scanning.</p>
                <ul className="mt-2">{WEIGH_SCALE.map((w, i) => { const o = (d.queue as Row[])[i]; return (
                  <li key={i} className="py-2 border-t border-line flex items-center justify-between gap-2"><span><span className="block font-mono text-[0.75rem] font-bold">#{o?.external_order_id ?? `PARCEL-${i + 1}`}</span><span className="block t-support">BOM: {w.bom} | Actual: {w.actual} ({w.diff})</span></span>{ship && <Link href={ship} className="btn btn-sm shrink-0">{w.action}</Link>}</li>); })}</ul>
              </section>
            </div>
          </div>
        </>
      )}
    </>
  );
}
