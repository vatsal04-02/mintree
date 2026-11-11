"use client";
/** The three module screens from the design spec (docs/DESIGN_SYSTEM.md, screens 2-4): Production & Batch Runs, Material Procurement & Stock Matrix,
 *  and Omni-Channel Dispatch & Invoicing. Every figure, row and name comes from the API; instrument readings, SLA percentages and the MRP note come from
 *  lib/demo-telemetry.ts and carry a visible "Simulated"/"sim" mark. */
import { useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Gauge, ShieldCheck, Boxes, CalendarClock, CheckCircle2, ClipboardList, Droplets, FileText, Layers, Package, Plus, Printer, Search, Settings2, Truck, User, Wrench, Zap } from "lucide-react";
import { fmtDate, fmtNum, fmtPct, fmtRupees, titleCase } from "@/lib/format";
import { CHANNEL_SLA, COURIER_SLA, DISPATCH_CUTOFF, EQUIPMENT_TILES, MRP_NOTE, PLANT, VESSEL_TILES, WEIGH_SCALE } from "@/lib/demo-telemetry";
import { Sim, useShared, type Row } from "./command";
import { InView, Ring } from "./fx";

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
function StatTile({ icon: Icon, label, value, extra, sim }: { icon: React.ComponentType<{ size?: number }>; label: string; value: React.ReactNode; extra?: React.ReactNode; sim?: boolean }) {
  return (
    <div className="panel px-3.5 py-3 flex items-center gap-3 min-w-[12rem]">
      <span className="icon-sq" aria-hidden><Icon size={17} /></span>
      <div className="min-w-0">
        <p className="label-caps flex items-center gap-1.5 whitespace-nowrap">{label}{sim && <span className="normal-case tracking-normal font-semibold text-[#92400e]">· sim</span>}</p>
        <p className="font-heading font-extrabold text-[1rem] leading-6">{value}{extra && <span className="ml-1.5 text-[0.75rem] font-bold text-success">{extra}</span>}</p>
      </div>
    </div>
  );
}

/* ================================================================== Production & Batch Runs */
const ORDER_EDGE: Record<string, string> = { IN_PROGRESS: "var(--brand-bright)", RELEASED: "#3b82f6", PLANNED: "var(--line-strong)" };
export function ProductionScreen({ to }: { to: To }) {
  const d = useShared<Row>("/production/floor");
  const [tab, setTab] = useState<"active" | "scheduled">("active");
  const [q, setQ] = useState("");
  const orders: Row[] = d?.orders ?? [];
  const active = orders.filter((o) => o.status !== "PLANNED"), scheduled = orders.filter((o) => o.status === "PLANNED");
  const shown = (tab === "active" ? active : scheduled).filter((o) => !q || `${o.number} ${o.item_name} ${o.item_code}`.toLowerCase().includes(q.toLowerCase()));
  const running = n(d?.counts?.IN_PROGRESS);
  const ordersHref = to("production", "orders");
  return (
    <>
      <Head eyebrow="Factory Plant 01 / Production & Batches"
        tags={<><span className="tag">GMP Grade ISO-22716</span><span className="tag tag-mono">Plant-01: Cleanroom ISO 7</span></>}
        title="Production & Batch Runs" description="Real-time supervision of active compounding vessels, automated filling lines and calibrated batch records."
        aside={<div className="grid gap-2.5 sm:grid-cols-3">
          <StatTile icon={Layers} label="Active batches" value={`${fmtNum(running)} Running`} />
          <StatTile icon={Gauge} label="Plant overall OEE" value={PLANT.oee} extra={PLANT.oeeDelta} sim />
          <StatTile icon={Droplets} label="USP water index" value={PLANT.waterIndex} sim />
        </div>} />

      <section className="panel p-3 mb-4 flex flex-wrap items-center gap-3 justify-between" aria-label="Batch filters">
        <div className="seg" role="tablist" aria-label="Batches">
          <button type="button" role="tab" aria-pressed={tab === "active"} aria-selected={tab === "active"} onClick={() => setTab("active")}>Active batch runs ({active.length})</button>
          <button type="button" role="tab" aria-pressed={tab === "scheduled"} aria-selected={tab === "scheduled"} onClick={() => setTab("scheduled")}>Scheduled work orders ({scheduled.length})</button>
          {ordersHref && <Link href={`${ordersHref}?status=COMPLETED`} className="px-3.5 inline-flex items-center text-[0.8125rem] font-medium text-muted hover:text-fg">Batch manufacturing records</Link>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative"><span className="sr-only">Filter batches</span><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden /><input className="input !h-9 !pl-8 !w-56" placeholder="Filter batch #, SKU or lot…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
          {ordersHref && <Link href={ordersHref} className="btn btn-primary gap-1.5"><Plus size={15} />New production batch</Link>}
        </div>
      </section>

      {d === undefined ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skel key={i} h="h-80" />)}</div>
        : shown.length === 0 ? <p className="panel p-6 t-label">No {tab === "active" ? "running or released" : "scheduled"} batch matches.</p> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((o, i) => {
            const stages: Row[] = o.stages ?? [];
            const done = stages.filter((s) => s.status === "COMPLETED").length;
            const cur = stages.find((s) => s.status === "IN_PROGRESS") ?? stages.find((s) => s.status === "PENDING");
            const issuePct = n(o.required_qty) ? Math.min(100, Math.round((n(o.issued_qty) / n(o.required_qty)) * 100)) : 0;
            const live = o.status === "IN_PROGRESS";
            const label = live ? "Live blending" : o.status === "RELEASED" ? "Released to floor" : `Scheduled${o.planned_date ? ` (${fmtDate(o.planned_date)})` : ""}`;
            return (
              <article key={o.id} className="panel kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": ORDER_EDGE[o.status] } as React.CSSProperties}>
                <div className="flex items-center gap-2">
                  <span className="tag tag-mono">#{o.number}</span>
                  <span className={`text-[0.75rem] font-bold ${live ? "text-success" : "text-muted"} inline-flex items-center gap-1.5`}>{live && <span className="w-1.5 h-1.5 rounded-full bg-[var(--brand-live)]" />}{label}</span>
                </div>
                <div>
                  <h3 className="font-heading font-bold text-[0.9375rem] leading-5">{o.item_name}</h3>
                  <p className="t-support">SKU: {o.item_code} · Batch vol: {fmtNum(n(o.planned_qty))} {o.uom ?? "units"}</p>
                </div>
                <div className="tile">
                  <div className="flex items-baseline justify-between gap-2"><span className="tile-label">Material issued</span><span className="font-mono text-[0.75rem] font-bold">{fmtNum(n(o.issued_qty), 1)} / {fmtNum(n(o.required_qty), 1)} ({issuePct}%)</span></div>
                  <div className="progress mt-2"><span className="bar-grow-x" style={{ width: `${issuePct}%` }} /></div>
                </div>
                <dl className="space-y-2 text-[0.8125rem]">
                  <div className="flex gap-2.5"><Zap size={14} className="text-muted mt-0.5 shrink-0" aria-hidden /><dt className="text-muted w-28 shrink-0">Active stage</dt><dd className="font-semibold min-w-0">{cur ? titleCase(cur.name) : "All stages complete"} <span className="text-muted font-medium">· {done} of {stages.length}</span></dd></div>
                  <div className="flex gap-2.5"><User size={14} className="text-muted mt-0.5 shrink-0" aria-hidden /><dt className="text-muted w-28 shrink-0">Operator</dt><dd className="font-semibold truncate">{cur?.operator ?? "Not yet assigned"}</dd></div>
                  <div className="flex gap-2.5"><Settings2 size={14} className="text-muted mt-0.5 shrink-0" aria-hidden /><dt className="text-muted w-28 shrink-0">Vessel & zone</dt><dd className="font-mono text-[0.75rem] font-semibold truncate">{cur?.equipment ?? cur?.work_centre ?? `BOM #${o.bom_id}`}</dd></div>
                  <div className="flex gap-2.5"><ShieldCheck size={14} className="text-muted mt-0.5 shrink-0" aria-hidden /><dt className="text-muted w-28 shrink-0">QC checkpoint</dt><dd>{o.yield_pct ? <span className="badge tone-success">Yield {fmtPct(n(o.yield_pct))}</span> : <span className={`badge ${live ? "tone-warning" : "tone-neutral"}`}>{live ? "In-process QC pending" : "Not started"}</span>}</dd></div>
                </dl>
                <div className="grid grid-cols-3 gap-2">
                  {(live ? VESSEL_TILES[i % VESSEL_TILES.length] : [{ label: "Planned", value: `${fmtNum(n(o.planned_qty))}` }, { label: "Materials", value: `${fmtNum(n(o.materials))} lines` }, { label: "Planned date", value: o.planned_date ? fmtDate(o.planned_date) : "—" }]).map((t) => (
                    <div key={t.label} className="tile !px-2.5 text-center min-w-0"><span className="tile-label truncate" title={t.label}>{t.label}</span><span className="tile-value !text-[0.8125rem]">{t.value}</span></div>
                  ))}
                </div>
                {live && <p className="t-support -mt-1 text-[#92400e]">Instrument readings above are simulated.</p>}
                <div className="card-foot !mt-auto">
                  {ordersHref && <Link href={`${ordersHref}?open=${o.id}`} className="inline-flex items-center gap-1.5 text-[0.8125rem] font-semibold text-brand hover:underline"><FileText size={14} />View batch record</Link>}
                  <a href={`/api/labels/production-order/${o.id}`} target="_blank" rel="noopener" className="btn btn-sm">Job card label</a>
                </div>
              </article>
            );
          })}
          {tab === "active" && !q && shown.length % 3 !== 0 && (
            <article className={`panel p-4 flex flex-col gap-3 ${shown.length % 3 === 1 ? "xl:col-span-2" : ""}`} aria-label="Next scheduled work orders">
              <div className="flex items-center justify-between gap-2"><span className="label-caps">Next scheduled</span><span className="tag tag-mono">{fmtNum(scheduled.length)} planned</span></div>
              {scheduled.length === 0 ? <p className="t-support">No work orders are scheduled. Plan one from production orders.</p> : (
                <ul className="divide-y divide-line">
                  {scheduled.slice(0, 5).map((o) => (
                    <li key={o.id} className="py-2.5">
                      <div className="flex items-center justify-between gap-2"><span className="tag tag-mono">#{o.number}</span><span className="t-support whitespace-nowrap inline-flex items-center gap-1"><CalendarClock size={12} aria-hidden />{o.planned_date ? fmtDate(o.planned_date) : "Not dated"}</span></div>
                      <p className="mt-1 text-[0.8125rem] font-semibold truncate" title={o.item_name}>{titleCase(String(o.item_name ?? ""))} <span className="t-support font-medium">· {fmtNum(n(o.planned_qty))} {o.uom ?? "units"}</span></p>
                    </li>
                  ))}
                </ul>
              )}
              <div className="card-foot !mt-auto">
                <button type="button" onClick={() => setTab("scheduled")} className="inline-flex items-center gap-1.5 text-[0.8125rem] font-semibold text-brand hover:underline">View all scheduled</button>
                {ordersHref && <Link href={ordersHref} className="btn btn-sm">Plan a batch</Link>}
              </div>
            </article>
          )}
        </div>
      )}

      {d && (d.equipment ?? []).length > 0 && (
        <div className="grid gap-4 md:grid-cols-3 mt-4">
          {(d.equipment as Row[]).map((e, i) => {
            const t = EQUIPMENT_TILES[i % EQUIPMENT_TILES.length];
            return (
              <article key={e.id} className="panel p-4 flex flex-col gap-3">
                <div className="flex items-start gap-3">
                  <span className="icon-sq" aria-hidden><Wrench size={16} /></span>
                  <div className="min-w-0 flex-1"><h3 className="font-heading font-bold text-[0.9375rem] leading-5 truncate" title={e.name}>{e.name}</h3><p className="t-support font-mono truncate">{e.asset_id}{e.location ? ` · ${e.location}` : ""}</p></div>
                </div>
                <div className="grid grid-cols-3 gap-2">{t.tiles.map((x) => <div key={x.label} className="tile !px-2.5 min-w-0"><span className="tile-label truncate" title={x.label}>{x.label}</span><span className="tile-value truncate">{x.value}</span></div>)}</div>
                <div className="mt-auto flex items-center justify-between gap-2 text-[0.8125rem]">
                  <span className="inline-flex items-center gap-1.5 text-muted"><CalendarClock size={14} aria-hidden />Next PM due</span>
                  <span className="font-mono font-semibold">{e.next_pm ? fmtDate(e.next_pm) : e.calibration_due ? `Calib. ${fmtDate(e.calibration_due)}` : "Not scheduled"}</span>
                </div>
                <div className="pt-3 border-t border-line flex items-center justify-between gap-2"><span className="t-support truncate">{t.health} <span className="text-[#92400e]">· sim</span></span><span className={`badge shrink-0 ${e.status === "OPERATIONAL" ? "tone-success" : e.status === "BREAKDOWN" ? "tone-danger" : "tone-warning"}`}>{titleCase(e.status)}</span></div>
              </article>
            );
          })}
        </div>
      )}

      {d && d.dispensing_order && (
        <section className="panel mt-4" aria-label="Dispensing and raw material traceability">
          <header className="flex flex-wrap items-start justify-between gap-3 p-5 pb-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2"><h2 className="t-section">Dispensing & Raw Material Traceability (Lot Allocation)</h2><span className="tag tag-mono">Active lot: #{d.dispensing_order.number}</span></div>
              <p className="t-support mt-0.5">Materials issued to {d.dispensing_order.item_name}, each against the lot it came from and the person who issued it.</p>
            </div>
            <div className="flex gap-2">
              <a href={`/api/labels/production-order/${d.dispensing_order.id}`} target="_blank" rel="noopener" className="btn btn-sm gap-1.5"><Printer size={14} />Print weighing slip</a>
              {ordersHref && <Link href={`${ordersHref}?open=${d.dispensing_order.id}`} className="btn btn-sm gap-1.5"><FileText size={14} />Open batch record</Link>}
            </div>
          </header>
          <div className="overflow-x-auto">
            <table className="dt">
              <thead><tr><th>Raw material / active ingredient</th><th>Lot & CoA code</th><th className="num">Required qty</th><th className="num">Dispensed qty</th><th>Dual-verification sign</th><th>Release status</th></tr></thead>
              <tbody>
                {(d.dispensing as Row[]).map((m) => {
                  const full = n(m.issued_qty) >= n(m.required_qty) && n(m.required_qty) > 0, some = n(m.issued_qty) > 0;
                  return (
                    <tr key={m.item_code}>
                      <td className="!whitespace-normal"><span className="flex items-start gap-2"><span className="w-1.5 h-1.5 rounded-full mt-2 shrink-0" style={{ background: full ? "var(--brand-live)" : some ? "var(--amber)" : "var(--line-strong)" }} aria-hidden /><span><span className="block font-semibold">{m.name}</span><span className="block t-support font-mono">{m.item_code} · {m.kind}</span></span></span></td>
                      <td><span className="block font-mono text-[0.75rem] font-semibold">{m.lots ?? "Not issued"}</span>{m.lot_qc && <span className={`block font-mono text-[0.6875rem] ${m.lot_qc.includes("RELEASED") ? "text-success" : "text-warning"}`}>CoA-{m.lot_qc.includes("RELEASED") ? "VALID-PASS" : m.lot_qc}</span>}</td>
                      <td className="num font-mono">{fmtNum(n(m.required_qty), 2)} {m.uom}</td>
                      <td className={`num font-mono font-semibold ${full ? "text-success" : ""}`}>{fmtNum(n(m.issued_qty), 2)} {m.uom}</td>
                      <td>{m.issued_by ? <span className="inline-flex items-center gap-1.5"><CheckCircle2 size={14} className="text-success" aria-hidden />{m.issued_by}</span> : <span className="text-muted">Awaiting issue</span>}</td>
                      <td><span className={`badge ${full ? "tone-success" : some ? "tone-warning" : "tone-neutral"}`}>{full ? "Weighed & staged" : some ? "Partially issued" : "Awaiting dispense"}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <footer className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-t border-line t-support">
            <span className="inline-flex items-center gap-1.5"><ShieldCheck size={14} className="text-success" aria-hidden />Every issue is written to the append-only stock ledger and audit log.</span>
            <Link href="/inventory/batches" className="font-semibold text-brand hover:underline">View full traceability log →</Link>
          </footer>
        </section>
      )}
    </>
  );
}

/* ================================================================== Material Procurement & Stock Matrix */
const STATE: Record<string, { label: string; tone: string }> = { BELOW: { label: "Below threshold", tone: "danger" }, SOON: { label: "Reorder soon", tone: "warning" }, HEALTHY: { label: "Healthy", tone: "success" } };
export function PurchaseScreen({ to }: { to: To }) {
  const d = useShared<Row>("/procurement/vault");
  const [kind, setKind] = useState<"ALL" | "RM" | "PM">("ALL");
  const [page, setPage] = useState(0);
  const per = 8;
  const rows: Row[] = useMemo(() => (d?.materials ?? []).filter((m: Row) => kind === "ALL" || m.kind === kind), [d, kind]);
  const pages = Math.max(1, Math.ceil(rows.length / per));
  const vbk = d?.value_by_kind ?? {}, cbk = d?.count_by_kind ?? {};
  const rmpm = n(vbk.RM) + n(vbk.PM);
  const req = to("procurement", "requests"), rec = to("procurement", "receipts");
  return (
    <>
      <Head eyebrow="Factory Plant 01 / Procurement & Supply Chain / PM & RM Vault" tags={<span className="tag">GMP batch reg. 2024</span>}
        title="Material Procurement & Stock Matrix" description="Raw materials (RM) and packaging materials (PM): capital, open commitments, replenishment and inbound receipts."
        actions={<div className="seg" role="tablist" aria-label="Material class">
          {([["ALL", "All materials"], ["RM", "Raw materials (RM)"], ["PM", "Packaging (PM)"]] as const).map(([k, l]) => <button key={k} type="button" role="tab" aria-pressed={kind === k} aria-selected={kind === k} onClick={() => { setKind(k); setPage(0); }}>{l}</button>)}
          <Link href="/masters/suppliers" className="px-3.5 inline-flex items-center text-[0.8125rem] font-medium text-muted hover:text-fg">Vendors</Link>
        </div>} />

      {d === undefined ? <Skel h="h-96" /> : !d ? <p className="panel p-5 text-danger">The materials summary could not be loaded.</p> : (
        <>
          <section className="hero px-5 py-4 mb-4 flex flex-wrap items-center justify-between gap-4" aria-label="Materials summary">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[0.8125rem]">
              <span className="inline-flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-[var(--brand-live)]" /><span className="label-caps">Inventory value</span><b className="font-heading font-extrabold text-[1rem]">{short(n(d.inventory_value))}</b></span>
              <span className="inline-flex items-center gap-2"><AlertTriangle size={14} className="text-danger" /><span className="label-caps">Low stock</span><span className="badge tone-danger">{fmtNum(n(d.below_reorder))} critical</span></span>
              <span className="inline-flex items-center gap-2"><Truck size={14} className="text-muted" /><span className="label-caps">In-transit</span><b>{fmtNum(n(d.open_orders))} POs</b><span className="text-muted">({fmtNum(n(d.arriving_this_week))} arriving this week)</span></span>
              <span className="inline-flex items-center gap-2"><ShieldCheck size={14} className="text-muted" /><span className="label-caps">Quarantine & QC</span><span className="badge tone-success">{fmtNum(n(d.quarantine_lots))} lots for CoA release</span></span>
            </div>
            <div className="flex gap-2">
              {to("inventory", "reorder") && <Link href={to("inventory", "reorder")!} className="btn gap-1.5"><Settings2 size={14} />Threshold config</Link>}
              {req && <Link href={req} className="btn btn-primary gap-1.5"><ClipboardList size={14} />Create purchase indent</Link>}
            </div>
          </section>

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">
            {[
              { label: "RM botanical & actives", value: short(n(vbk.RM)), chip: `${fmtNum(n(cbk.RM))} items`, foot: "Raw material stock at moving-average cost", bar: rmpm ? (n(vbk.RM) / rmpm) * 100 : 0, icon: Boxes, accent: "var(--brand-bright)" },
              { label: "PM packaging materials", value: short(n(vbk.PM)), chip: `${fmtNum(n(cbk.PM))} items`, foot: "Packaging stock at moving-average cost", bar: rmpm ? (n(vbk.PM) / rmpm) * 100 : 0, icon: Package, accent: "#64748b" },
              { label: "Open PO commitments", value: short(n(d.open_order_value)), chip: `${fmtNum(n(d.open_orders))} active POs`, foot: `${fmtNum(n(d.approved_suppliers))} approved suppliers`, bar: 60, icon: ClipboardList, accent: "var(--brand-bright)" },
              { label: "Inbound this week", value: `${fmtNum(n(d.arriving_this_week))}`, unit: "POs", chip: d.late_orders ? `${fmtNum(n(d.late_orders))} overdue` : "On schedule", chipTone: d.late_orders ? "danger" : "success", foot: "Due in the next 7 days", bar: n(d.open_orders) ? (n(d.arriving_this_week) / n(d.open_orders)) * 100 : 0, icon: Truck, accent: "#3b82f6" },
            ].map((k) => (
              <div key={k.label} className="panel kpi-card p-4 pt-[18px] flex flex-col" style={{ "--kpi-accent": k.accent } as React.CSSProperties}>
                <div className="flex items-start justify-between gap-3"><span className="label-caps">{k.label}</span><span className="icon-sq icon-sq-sm" aria-hidden><k.icon size={15} /></span></div>
                <p className="t-kpi mt-1.5">{k.value}{k.unit && <span className="t-label !font-semibold ml-1.5">{k.unit}</span>}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2"><span className={`badge tone-${k.chipTone ?? "success"}`}>{k.chip}</span><span className="t-support">{k.foot}</span></div>
                <div className="progress mt-auto pt-0 translate-y-1"><span className="bar-grow-x" style={{ width: `${Math.max(2, Math.min(100, k.bar))}%`, background: k.accent }} /></div>
              </div>
            ))}
          </div>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] mb-4 items-stretch">
            <section className="panel p-5 flex flex-col" aria-label="Priority replenishment">
              <header className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3"><span className="icon-sq !bg-[#ffe4e6] !text-[#e11d48]" aria-hidden><AlertTriangle size={17} /></span><div><h2 className="t-section">Priority Replenishment Indents Required</h2><p className="t-support">{d.priority.length ? `${d.priority.length} material${d.priority.length === 1 ? " has" : "s have"} breached the reorder point.` : "Every material is above its reorder point."}</p></div></div>
                {d.priority.length > 0 && <span className="badge tone-danger">Action needed</span>}
              </header>
              <div className="mt-4 grid gap-3 md:grid-cols-2 flex-1">
                {(d.priority as Row[]).map((m) => {
                  const pct = n(m.reorder_level) ? Math.min(100, (n(m.free) / n(m.reorder_level)) * 100) : 0;
                  return (
                    <div key={m.item_code} className="rounded-xl bg-[var(--tile)] p-4 flex flex-col">
                      <div className="flex items-start gap-3"><span className="icon-sq bg-white" aria-hidden>{m.kind === "PM" ? <Package size={16} /> : <Boxes size={16} />}</span><div className="min-w-0"><span className="tag tag-mono bg-white">{m.item_code}</span><h3 className="font-heading font-bold text-[0.9375rem] mt-1 leading-5">{m.name}</h3><p className="t-support">{m.kind === "PM" ? "Packaging material" : "Raw material"}</p></div></div>
                      <div className="mt-3 rounded-lg bg-white p-3 space-y-1.5 text-[0.8125rem]">
                        <div className="flex justify-between"><span className="text-muted">On-hand free stock</span><b className="text-danger font-mono">{fmtNum(n(m.free), 1)} {m.uom}</b></div>
                        <div className="flex justify-between"><span className="text-muted">Reorder point</span><b className="font-mono">{fmtNum(n(m.reorder_level), 1)} {m.uom}</b></div>
                        <div className="progress"><span style={{ width: `${Math.max(3, pct)}%`, background: "var(--danger)" }} /></div>
                        <div className="flex justify-between pt-1"><span className="text-muted">Shortfall to reorder point</span><b className="font-mono text-danger">{fmtNum(Math.max(0, n(m.reorder_level) - n(m.free)), 1)} {m.uom}</b></div>
                      </div>
                      <p className="mt-3 t-support">Vendor <b className="text-fg">{m.vendor ?? "No supplier yet"}</b></p>
                      {req && <div className="mt-auto pt-3"><Link href={req} className="btn btn-sm btn-urgent gap-1 w-full justify-center"><Zap size={13} />Generate urgent PR</Link></div>}
                    </div>
                  );
                })}
                {(d.priority as Row[]).length % 2 === 1 && (() => {
                  const watch = (d.materials as Row[]).filter((m) => m.state !== "BELOW" && n(m.reorder_level) > 0)
                    .map((m): Row => ({ ...m, ratio: n(m.free) / n(m.reorder_level) })).sort((a, b) => a.ratio - b.ratio).slice(0, 4);
                  return (
                    <div className="rounded-xl border border-line p-4 flex flex-col">
                      <div className="flex items-center justify-between gap-2"><p className="label-caps whitespace-nowrap">Watch list · next to breach</p><span className="t-support whitespace-nowrap">Cover</span></div>
                      {watch.length === 0 ? <p className="t-support mt-3">No other material has a reorder point set.</p> : (
                        <ul className="mt-2 divide-y divide-line">
                          {watch.map((m) => {
                            const cover = Math.min(100, (m.ratio / 3) * 100), tight = m.ratio < 1.5;
                            return (
                              <li key={m.item_code} className="py-2.5">
                                <div className="flex items-center justify-between gap-2 text-[0.8125rem]"><span className="font-semibold truncate" title={m.name}>{m.name}</span><span className={`font-mono font-bold shrink-0 ${tight ? "text-warning" : "text-success"}`}>{fmtNum(m.ratio, 1)}×</span></div>
                                <div className="mt-1 flex items-center gap-2"><span className="t-support font-mono shrink-0 w-24 truncate">{m.item_code}</span><div className="progress flex-1"><span style={{ width: `${Math.max(3, cover)}%`, background: tight ? "var(--amber)" : "var(--brand-bright)" }} /></div><span className="t-support font-mono shrink-0">{fmtNum(n(m.free), 0)} {m.uom}</span></div>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                      {to("inventory", "reorder") && <Link href={to("inventory", "reorder")!} className="mt-auto pt-3 text-[0.8125rem] font-semibold text-brand hover:underline">Review reorder levels</Link>}
                    </div>
                  );
                })()}
              </div>
            </section>

            <section className="panel p-5 flex flex-col" aria-label="Inbound goods receipt tracker">
              <header className="flex items-center justify-between gap-2"><h2 className="t-section inline-flex items-center gap-2 whitespace-nowrap"><Truck size={16} className="text-brand" />Live Inbound GRN Tracker</h2>{d.inbound && <span className="tag tag-mono">{d.inbound.number}</span>}</header>
              {!d.inbound ? <p className="t-label mt-3">No purchase order is waiting for delivery.</p> : (
                <div className="mt-3 rounded-xl bg-[var(--tile)] p-4 flex-1 flex flex-col">
                  <p className="label-caps">Vendor / consignor</p>
                  <p className="font-heading font-bold text-[1rem]">{d.inbound.supplier}</p>
                  <p className="t-support">Consignment: {d.inbound.first_item ? `${fmtNum(n(d.inbound.first_qty))} × ${d.inbound.first_item}` : "—"} · {fmtRupees(n(d.inbound.total_amount))}</p>
                  <ol className="mt-4 space-y-3.5 relative">
                    {[
                      { done: !!d.inbound.approved_at, title: "PO issued & approved", sub: d.inbound.approved_at ? `Approved ${fmtDate(d.inbound.approved_at)}` : "Awaiting approval" },
                      { done: n(d.inbound.receipts) > 0, title: n(d.inbound.receipts) > 0 ? "Partly received" : "In transit from supplier", sub: n(d.inbound.receipts) > 0 ? `${fmtNum(n(d.inbound.receipts))} receipt(s) posted` : d.inbound.city ? `Dispatching from ${d.inbound.city}` : "Awaiting dispatch" },
                      { done: false, title: d.inbound.expected_date ? `Arriving ${fmtDate(d.inbound.expected_date)}` : "Arrival date not set", sub: "Unloading at the receiving dock" },
                      { done: false, title: "Dock gate entry & CoA sampling", sub: "Quarantine hold until QC release" },
                    ].map((s, i) => (
                      <li key={i} className="flex gap-3">
                        <span className={`grid place-items-center w-6 h-6 rounded-full shrink-0 ${s.done ? "bg-[var(--brand)] text-white" : "bg-white border border-line text-muted"}`} aria-hidden>{s.done ? <CheckCircle2 size={14} /> : <span className="w-1.5 h-1.5 rounded-full bg-current" />}</span>
                        <div className="min-w-0"><p className="font-semibold text-[0.8125rem]">{s.title}</p><p className="t-support">{s.sub}</p></div>
                      </li>
                    ))}
                  </ol>
                  {rec && <div className="mt-auto pt-4"><Link href={rec} className="btn w-full justify-center">Prepare gate inward GRN</Link></div>}
                </div>
              )}
            </section>
          </div>

          <section className="panel mb-4" aria-label="Material inventory">
            <header className="flex flex-wrap items-start justify-between gap-3 p-5 pb-3">
              <div><h2 className="t-section">Material Inventory & Bill-of-Materials Allocation</h2><p className="t-support">Stock per material against production allocations and the reorder point, with the last supplier and price.</p></div>
              <span className="tag tag-mono">Sort: safety risk</span>
            </header>
            <div className="overflow-x-auto">
              <table className="dt">
                <thead><tr><th>Material</th><th className="num">On hand</th><th className="num">WIP</th><th className="num">Free</th><th className="num">Reorder at</th><th>Vendor</th><th className="num">Unit price</th><th>Status</th></tr></thead>
                <tbody>
                  {rows.slice(page * per, page * per + per).map((m) => {
                    const st = STATE[m.state] ?? STATE.HEALTHY;
                    return (
                      <tr key={m.id} style={m.state === "BELOW" ? { background: "#fff1f2" } : undefined}>
                        <td className="max-w-[16rem]"><span className="block font-semibold truncate" title={m.name}>{m.name}</span><span className="flex items-center gap-2 mt-0.5"><span className={`font-mono text-[0.6875rem] font-semibold ${m.state === "BELOW" ? "text-danger" : "text-brand"}`}>{m.item_code}</span><span className={`text-[0.625rem] font-bold uppercase tracking-wide ${m.kind === "RM" ? "text-success" : "text-[#1e40af]"}`}>{m.kind === "RM" ? "Raw" : "Pack"}</span></span></td>
                        <td className={`num font-mono font-semibold ${m.state === "BELOW" ? "text-danger" : ""}`}>{fmtNum(n(m.on_hand), 1)} {m.uom}</td>
                        <td className="num font-mono text-muted">{fmtNum(n(m.allocated), 1)} {m.uom}</td>
                        <td className={`num font-mono font-semibold ${m.state === "BELOW" ? "text-danger" : "text-success"}`}>{fmtNum(n(m.free), 1)} {m.uom}</td>
                        <td className="num font-mono text-muted">{m.reorder_level ? `${fmtNum(n(m.reorder_level), 1)} ${m.uom}` : "—"}</td>
                        <td className="max-w-[12rem]">{m.vendor ? <><span className="block font-semibold text-[0.8125rem] truncate" title={m.vendor}>{m.vendor}</span>{m.vendor_city && <span className="block t-support">{m.vendor_city}</span>}</> : <span className="text-muted">—</span>}</td>
                        <td className="num font-mono">{m.last_price ? `${fmtRupees(n(m.last_price))}/${m.uom}` : "—"}</td>
                        <td><span className={`badge tone-${st.tone} whitespace-nowrap`}>{st.label}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <footer className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-t border-line">
              <span className="t-support">Showing {rows.length ? page * per + 1 : 0}–{Math.min(rows.length, page * per + per)} of {rows.length} raw & packaging materials</span>
              <div className="flex items-center gap-1.5">
                <button type="button" className="btn btn-sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
                {Array.from({ length: pages }).map((_, i) => <button key={i} type="button" className={`btn btn-sm ${i === page ? "btn-primary" : ""}`} aria-current={i === page ? "page" : undefined} onClick={() => setPage(i)}>{i + 1}</button>)}
                <button type="button" className="btn btn-sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>Next</button>
              </div>
            </footer>
          </section>

          <div className="grid gap-4 md:grid-cols-3">
            <section className="panel p-5" aria-label="Vendor compliance">
              <header className="flex items-center justify-between"><h2 className="t-section">Vendor On-time Performance</h2><span className="badge tone-neutral">{fmtNum((d.vendors ?? []).length)} suppliers</span></header>
              <p className="t-support mt-0.5">Share of goods receipts that arrived on time.</p>
              <ul className="mt-3 space-y-3.5">{(d.vendors as Row[]).map((v) => { const pct = v.on_time_pct === null ? null : n(v.on_time_pct); return (
                <li key={v.name}>
                  <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]"><span className="truncate font-semibold">{v.name}</span><span className={`font-mono font-bold shrink-0 ${pct === null ? "text-muted" : pct >= 90 ? "text-success" : pct >= 60 ? "text-warning" : "text-danger"}`}>{pct === null ? "—" : fmtPct(pct)}</span></div>
                  <div className="progress mt-1.5"><span style={{ width: `${Math.max(pct ? 2 : 0, pct ?? 0)}%`, background: pct !== null && pct < 60 ? "var(--danger)" : pct !== null && pct < 90 ? "var(--amber)" : "var(--brand-bright)" }} /></div>
                  <p className="t-support mt-1">{fmtNum(n(v.receipts))} receipt{n(v.receipts) === 1 ? "" : "s"} · on or before the expected date</p>
                </li>); })}</ul>
            </section>
            <section className="panel" aria-label="Spend by classification">
              <header className="flex items-center justify-between p-5 pb-2"><h2 className="t-section">Spend by Classification</h2><span className="tag tag-mono">{short(n(d.spend_by_kind?.RM) + n(d.spend_by_kind?.PM))} ordered</span></header>
              <InView><Ring data={{ "Raw materials (RM)": n(d.spend_by_kind?.RM), "Packaging (PM)": n(d.spend_by_kind?.PM) }} centre="Ordered" /></InView>
            </section>
            <section className="panel p-5 flex flex-col" style={{ background: "linear-gradient(135deg,#eff6ff,#ffffff)" }} aria-label="Predictive MRP">
              <header className="flex items-center justify-between gap-2"><h2 className="t-section inline-flex items-center gap-2"><Gauge size={16} className="text-brand" />Predictive MRP Engine</h2><Sim /></header>
              <p className="t-label mt-2">{MRP_NOTE}</p>
              {to("production", "plan") && <Link href={to("production", "plan")!} className="btn btn-primary mt-auto self-start gap-1.5"><Zap size={14} />Run material plan</Link>}
            </section>
          </div>
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
      <Head eyebrow="Fulfilment hub · Warehouse Alpha" tags={<span className="tag tag-dot">Marketplace sync live</span>} title="Omni-Channel Dispatch & Invoicing"
        description="Multi-marketplace fulfilment: orders imported from each channel, GST invoicing, pick and pack, courier handover."
        actions={<div className="flex flex-wrap gap-2">
          {inv && <Link href={inv} className="btn gap-1.5"><FileText size={14} />GST invoice register</Link>}
          {ship && <Link href={ship} className="btn gap-1.5"><ClipboardList size={14} />Generate daily manifest</Link>}
          {ship && <Link href={ship} className="btn btn-primary gap-1.5"><Printer size={14} />Bulk print labels</Link>}
        </div>} />

      {d === undefined ? <Skel h="h-96" /> : !d ? <p className="panel p-5 text-danger">The fulfilment summary could not be loaded.</p> : (
        <>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,2.4fr)_minmax(0,1fr)] items-stretch mb-4">
            <section aria-label="Connected marketplaces" className="flex flex-col min-w-0">
              <div className="flex items-center justify-between h-6 mb-2"><p className="label-caps">Connected marketplaces & storefronts</p><span className="text-[0.75rem] font-semibold text-success">{fmtNum((d.channels as Row[]).filter((c) => c.status === "ACTIVE").length)} channels online</span></div>
              <div className="grid gap-3 grid-cols-2 lg:grid-cols-4 flex-1">
                {(d.channels as Row[]).map((c) => {
                  const p = CH[c.platform] ?? { name: titleCase(c.platform), color: "#64748b", bg: "#f1f5f9", fg: "#334155" };
                  return (
                    <Link key={c.platform} href={ord ?? "#"} className="panel panel-link kpi-card p-3.5 pt-4 flex flex-col" style={{ "--kpi-accent": p.color } as React.CSSProperties}>
                      <div className="flex items-center justify-between"><span className="badge !border-transparent" style={{ background: p.bg, color: p.fg, ["--tone-dot" as string]: p.color }}>{p.name}</span><span className={`w-2 h-2 rounded-full ${c.status === "ACTIVE" ? "bg-[var(--brand-live)]" : "bg-[var(--line-strong)]"}`} aria-label={c.status === "ACTIVE" ? "Connected" : "Paused"} /></div>
                      <p className="t-kpi mt-2">{fmtNum(n(c.pending))}</p>
                      <p className="t-support whitespace-nowrap">Pending of {fmtNum(n(c.orders))} orders</p>
                      <div className="mt-auto pt-2 border-t border-line flex items-center justify-between gap-2 t-support"><span className="whitespace-nowrap">On-time SLA <span className="text-[#92400e] font-medium">· sim</span></span><span className="font-semibold text-success shrink-0">{CHANNEL_SLA[c.platform] ?? "—"}</span></div>
                    </Link>
                  );
                })}
              </div>
            </section>
            <section className="flex flex-col min-w-0" aria-label="Dispatch velocity">
              <div className="flex items-center justify-between h-6 mb-2"><p className="label-caps">Dispatch velocity · 30d</p><span className="tag tag-mono">Cutoff {DISPATCH_CUTOFF}</span></div>
              <div className="panel p-4 flex-1 flex flex-col">
              <div className="grid grid-cols-2 gap-3">
                <div className="min-w-0"><p className="t-support">Gross value synced</p><p className="t-kpi">{lakhs(cur)}</p>{delta !== null && <p className={`text-[0.75rem] font-bold whitespace-nowrap ${delta >= 0 ? "text-success" : "text-danger"}`}>{delta >= 0 ? "+" : ""}{fmtNum(delta, 1)}% vs prev 30d</p>}</div>
                <div className="min-w-0"><p className="t-support">Parcels to dispatch</p><p className="t-kpi">{fmtNum(n(d.to_dispatch))} <span className="t-label">orders</span></p><p className="text-[0.75rem] font-bold text-muted whitespace-nowrap">{fmtNum(n(st.staged))} booked</p></div>
              </div>
              {(() => { const tot = n(st.pick_pack) + n(st.staged) || 1; const a = (n(st.staged) / tot) * 100; return (
                <><div className="mt-auto pt-3" /><div className="flex h-2 rounded-full overflow-hidden bg-[var(--chart-track)]"><span className="bar-grow-x" style={{ width: `${a}%`, background: "var(--brand-bright)" }} /><span style={{ width: `${100 - a}%`, background: "#a7f3d0" }} /></div>
                <div className="mt-1.5 flex justify-between t-support"><span>Booked: {fmtNum(n(st.staged))}</span><span>Packing: {fmtNum(n(st.pick_pack))}</span></div></>); })()}
              </div>
            </section>
          </div>

          <section className="panel p-4 mb-4" aria-label="Five-stage fulfilment pipeline">
            <header className="flex items-center justify-between mb-3"><h2 className="t-section inline-flex items-center gap-2"><Truck size={16} className="text-brand" />5-Stage Fulfilment & E-Way Dispatch Pipeline</h2><span className="t-support">Marketplace orders only</span></header>
            <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {pipeline.map((p, i) => (
                <li key={p.title} className={`step ${p.current ? "step-active" : ""} kpi-card px-3.5 pt-4 pb-3 flex flex-col`} style={{ "--kpi-accent": p.current ? "var(--brand-bright)" : "var(--line-strong)" } as React.CSSProperties}>
                  <div className="flex items-center justify-between"><span className={`label-caps ${p.current ? "!text-brand" : ""}`}>Stage {String(i + 1).padStart(2, "0")}{p.current ? " · Current" : ""}</span><p.icon size={15} className={p.current ? "text-brand" : "text-muted"} aria-hidden /></div>
                  <p className="font-heading font-bold text-[0.8125rem] leading-5 mt-1 min-h-[2.5rem] line-clamp-2" title={p.title}>{p.title}</p>
                  <div className="mt-auto pt-1 flex items-baseline justify-between gap-2"><span className={`t-stage ${p.current ? "!text-brand" : ""}`}>{fmtNum(p.value)}</span><span className={`text-[0.6875rem] font-bold ${p.current ? "text-success" : "text-muted"}`}>{p.note}</span></div>
                </li>
              ))}
            </ol>
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,2.4fr)_minmax(0,1fr)] items-start">
            <section className="panel" aria-label="Live invoicing and dispatch queue">
              <header className="flex flex-wrap items-center justify-between gap-2 p-5 pb-3"><h2 className="t-section inline-flex items-center gap-2"><ClipboardList size={16} className="text-brand" />Live Invoicing & Dispatch Queue <span className="badge tone-success badge-live">Real-time</span></h2>{ord && <Link href={ord} className="btn btn-sm">All marketplace orders</Link>}</header>
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
                          <td><span className="block font-mono text-[0.75rem] font-bold">#{o.external_order_id}</span><span className="badge !border-transparent mt-1" style={{ background: p.bg, color: p.fg, ["--tone-dot" as string]: p.color }}>{p.name}</span></td>
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
                <header className="flex items-center justify-between"><h2 className="t-section">Courier Carrier SLA Matrix</h2><Sim /></header>
                <ul className="mt-3 space-y-2.5">
                  {COURIER_SLA.map((c) => (
                    <li key={c.name} className="tile">
                      <div className="flex items-center justify-between gap-2"><span className="font-semibold text-[0.8125rem] truncate">{c.name}</span><span className="text-[0.75rem] font-bold text-success shrink-0">{fmtNum(c.sla, 1)}% SLA</span></div>
                      <div className="progress mt-1.5 !bg-white"><span style={{ width: `${c.sla}%` }} /></div>
                      <div className="mt-1 flex justify-between t-support"><span>Avg transit: {c.transit}</span><span>{fmtNum(c.active)} active parcels</span></div>
                    </li>
                  ))}
                </ul>
                <div className="mt-3 pt-3 border-t border-line">
                  <p className="label-caps">Couriers on record</p>
                  <ul className="mt-1.5 space-y-1">{(d.couriers as Row[]).map((c) => <li key={c.courier} className="flex justify-between t-support"><span className="font-semibold text-fg">{titleCase(String(c.courier))}</span><span>{fmtNum(n(c.active))} active · {fmtNum(n(c.delivered))} delivered</span></li>)}</ul>
                </div>
              </section>
              <section className="panel p-4" aria-label="Weigh-scale guard">
                <header className="flex flex-wrap items-center justify-between gap-2"><span className="label-caps !text-danger inline-flex items-center gap-1.5 whitespace-nowrap"><AlertTriangle size={13} />Weigh-scale guard</span><span className="flex gap-1.5"><span className="badge tone-danger">{WEIGH_SCALE.length} audits required</span><Sim /></span></header>
                <h3 className="font-heading font-bold text-[0.9375rem] mt-2">Dead-weight vs volumetric discrepancies</h3>
                <p className="t-support">Parcels more than 50 g away from the bill-of-materials weight before courier scanning.</p>
                <ul className="mt-3 space-y-2">{WEIGH_SCALE.map((w, i) => { const o = (d.queue as Row[])[i]; return (
                  <li key={i} className="tile flex items-center justify-between gap-2"><span><span className="block font-mono text-[0.75rem] font-bold">#{o?.external_order_id ?? `PARCEL-${i + 1}`}</span><span className="block t-support">BOM: {w.bom} | Actual: {w.actual} ({w.diff})</span></span>{ship && <Link href={ship} className="btn btn-sm shrink-0">{w.action}</Link>}</li>); })}</ul>
              </section>
            </div>
          </div>
        </>
      )}
    </>
  );
}
