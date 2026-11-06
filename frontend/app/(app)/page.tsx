"use client";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, titleCase } from "@/lib/format";
import { MODULE_BY_ID, toneFor } from "@/lib/modules";
import { PageHeader } from "@/components/ui";
import { BarRows, InlineNotice, KpiCard, Pipeline, Section, SectionLink, SkeletonRows, StatusBar, Timeline, WorkQueue, type QueueItem, type Stage, type Tone } from "@/components/dash";

/* Shape of GET /analytics/management-summary. Every figure below is read from it; nothing on this page is computed from constants. */
type Count = { status: string; n: number; value?: string | number };
type Summary = {
  as_of: string;
  sales: { receivable_outstanding: string | number };
  orders_by_status: Count[]; production_by_status: Count[]; purchase_orders_by_status: Count[]; shipments_by_status: Count[];
  quality: { open_inspections: number; batches_on_hold: number; batches_quarantined: number; open_deviations: number; open_capa: number };
  low_stock: { item_code: string; name: string; available: string | number; reorder_level: string | number }[];
  production: { completed_30d: number; planned_qty_30d: string; actual_qty_30d: string; active_orders: number; delayed_work_orders: number; wastage_qty_30d: string; average_yield_pct: string | number | null };
  inventory: { value_by_kind: { kind: string; value: string | number }[]; total_value: string | number; below_reorder_level: number; expiring_within_90_days: number; expired_with_stock: number };
  procurement: { purchase_requests_awaiting_approval: number; purchase_orders_awaiting_approval: number; open_purchase_orders: number; late_deliveries: number; purchase_value_30d: string };
  quality_extended: { rejected_inspections_30d: number; rejected_batches: number };
  dispatch: { delayed_shipments: number; delivered: number; failed_or_rto: number; returns_open: number };
  finance_extended: { open_sales_invoices: number; gross_margin_pct_30d: number | null; revenue_30d: string };
};
type Approval = { type: string; id: number; number: string; since: string };
type Audit = { id: number; occurred_at: string; user_email: string | null; action: string; entity: string; entity_id: string | null };

const SHIPMENT_FINAL = ["DELIVERED", "RTO", "FAILED_DELIVERY", "CANCELLED"];
/** Audit entity -> list screen that opens the record via ?open=<id> (the same convention the approvals inbox uses). */
const RECORD_SCREEN: Record<string, string> = {
  purchase_order: "/m/procurement/orders", rfq: "/m/procurement/rfqs", production_order: "/m/production/orders", bom: "/m/production/boms", shipment: "/m/dispatch/shipments",
  sales_return: "/m/dispatch/returns", inspection: "/m/quality/inspections", deviation: "/m/quality/deviations", formulation_version: "/m/rnd/formulations",
  maintenance_work_order: "/m/maintenance/work-orders", content: "/m/branding/content",
};
const KIND: Record<string, string> = { RM: "Raw materials", PM: "Packaging materials", FG: "Finished goods", SFG: "Semi-finished goods" };
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const num = (v: unknown) => (v === null || v === undefined || v === "" ? 0 : Number(v));
const sumStatus = (rows: Count[] | undefined, pick: (s: string) => boolean) => (rows ?? []).filter((r) => pick(r.status)).reduce((a, r) => a + r.n, 0);
const statusItems = (rows: Count[] | undefined) => (rows ?? []).map((r) => ({ label: titleCase(r.status), value: r.n, tone: toneFor(r.status) as Tone }));

export default function Overview() {
  const { me, can } = useAuth();
  const canSummary = can("analytics.dashboard:read");
  const canAudit = can("admin.audit:read");
  const [summary, setSummary] = useState<Summary | null | undefined>(undefined);   // undefined: loading, null: unavailable
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [activity, setActivity] = useState<Audit[] | null>(null);

  useEffect(() => {
    api<{ items: Approval[] }>("/approvals/pending").then((d) => setApprovals(d.items)).catch(() => setApprovals([]));
    if (canSummary) api<Summary>("/analytics/management-summary").then(setSummary).catch((e) => { setSummary(null); setSummaryError(e instanceof ApiError ? e.message : "The summary could not be loaded."); });
    else setSummary(null);
    if (canAudit) api<Page<Audit>>("/admin/audit-logs", { params: { page_size: 8 } }).then((p) => setActivity(p.items)).catch(() => setActivity([]));
  }, [canSummary, canAudit]);

  /** Link to a module screen only when the user may open it (the backend enforces the same permission). */
  const to = (moduleId: string, screenId: string) => {
    const sc = MODULE_BY_ID[moduleId]?.screens.find((x) => x.id === screenId);
    return sc && can(sc.perm) ? `/m/${moduleId}/${screenId}` : undefined;
  };

  const view = useMemo(() => {
    const s = summary;
    if (!s) return null;
    const pendingShipments = sumStatus(s.shipments_by_status, (st) => !SHIPMENT_FINAL.includes(st));
    const q = s.quality, inv = s.inventory, pr = s.procurement, d = s.dispatch, p = s.production;
    const held = q.batches_on_hold + q.batches_quarantined;
    const stages: Stage[] = [
      { label: "Procurement", count: pr.purchase_requests_awaiting_approval + pr.purchase_orders_awaiting_approval, unit: "awaiting approval", href: to("procurement", "orders"), flags: [] },
      { label: "Goods receipt", count: pr.open_purchase_orders, unit: "POs awaiting delivery", href: to("procurement", "receipts"), flags: [{ label: "late deliveries", n: pr.late_deliveries, tone: "danger" }] },
      { label: "Inventory", count: inv.below_reorder_level, unit: "items below reorder", href: to("inventory", "reorder"),
        flags: [{ label: "expired with stock", n: inv.expired_with_stock, tone: "danger" }, { label: "held or quarantined", n: held, tone: "warning" }, { label: "expiring in 90 days", n: inv.expiring_within_90_days, tone: "warning" }] },
      { label: "Production", count: p.active_orders, unit: "in progress", href: to("production", "orders"), flags: [{ label: "past planned date", n: p.delayed_work_orders, tone: "danger" }] },
      { label: "Quality", count: q.open_inspections, unit: "inspections open", href: to("quality", "inspections"),
        flags: [{ label: "open deviations", n: q.open_deviations, tone: "warning" }, { label: "open CAPA", n: q.open_capa, tone: "warning" }] },
      { label: "Dispatch", count: pendingShipments, unit: "shipments open", href: to("dispatch", "shipments"),
        flags: [{ label: "older than 5 days", n: d.delayed_shipments, tone: "danger" }, { label: "failed or returned to origin", n: d.failed_or_rto, tone: "danger" }, { label: "returns awaiting action", n: d.returns_open, tone: "warning" }] },
    ];
    const weight = stages.map((st) => st.flags.reduce((a, f) => a + f.n, 0));
    const top = Math.max(...weight);
    const busiest = top > 0 ? stages[weight.indexOf(top)].label : null;

    const queue: QueueItem[] = ([
      { key: "expired", label: "Expired batches still holding stock", count: inv.expired_with_stock, tone: "danger", href: to("inventory", "expiring") },
      { key: "late", label: "Purchase orders past expected delivery", count: pr.late_deliveries, tone: "danger", href: to("procurement", "delivery") },
      { key: "delayed-wo", label: "Production orders past planned date", count: p.delayed_work_orders, tone: "danger", href: to("production", "orders") },
      { key: "ship-late", label: "Shipments open longer than 5 days", count: d.delayed_shipments, tone: "danger", href: to("dispatch", "shipments") },
      { key: "ship-fail", label: "Failed deliveries and returns to origin", count: d.failed_or_rto, tone: "danger", href: to("dispatch", "shipments") },
      { key: "qc-hold", label: "Batches on QC hold or quarantine", count: held, tone: "warning", href: to("inventory", "batches") },
      { key: "inspections", label: "Inspections awaiting a decision", count: q.open_inspections, tone: "warning", href: to("quality", "inspections") },
      { key: "dev", label: "Open deviations", count: q.open_deviations, tone: "warning", href: to("quality", "deviations") },
      { key: "pr", label: "Purchase requests awaiting approval", count: pr.purchase_requests_awaiting_approval, tone: "warning", href: to("procurement", "requests") },
      { key: "po", label: "Purchase orders awaiting approval", count: pr.purchase_orders_awaiting_approval, tone: "warning", href: to("procurement", "orders") },
      { key: "low", label: "Items below reorder level", count: inv.below_reorder_level, tone: "warning", href: to("inventory", "reorder"),
        detail: s.low_stock.slice(0, 3).map((l) => `${l.name} (${fmtNum(l.available, 1)} of ${fmtNum(l.reorder_level, 1)})`).join(" · ") || undefined },
      { key: "returns", label: "Customer returns awaiting action", count: d.returns_open, tone: "warning", href: to("dispatch", "returns") },
    ] as QueueItem[]).filter((i) => (i.count ?? 0) > 0);

    const planned = num(p.planned_qty_30d), actual = num(p.actual_qty_30d);
    return { stages, busiest, queue, pendingShipments, held, planned, actual };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary, can]);

  const myApprovals = approvals?.length ?? 0;
  const oldestApproval = approvals && approvals.length ? approvals[0].since : null;
  const loading = canSummary && summary === undefined;
  const s = summary ?? null;

  return (
    <>
      <PageHeader title="Factory overview" description={s ? `Operational summary as of ${fmtDate(s.as_of)}. Figures are calculated from live records.` : `Signed in as ${me?.full_name}${me?.department_name ? `, ${me.department_name}` : ""}.`} />

      {summaryError && <div className="mb-4"><InlineNotice tone="danger">{summaryError} Operational figures are unavailable until it loads; reload to try again.</InlineNotice></div>}
      {!canSummary && <div className="mb-4"><InlineNotice>Your role does not include the management summary, so company-wide figures are not shown. Your approvals and the areas you can open are available from the sidebar.</InlineNotice></div>}

      {/* KPI cards */}
      <section aria-label="Key figures" className="grid grid-cols-2 lg:grid-cols-3 gap-3 mb-5">
        <KpiCard label="Awaiting your approval" value={approvals === null ? "—" : myApprovals} loading={approvals === null}
          hint={myApprovals ? `Oldest since ${fmtDate(oldestApproval)}` : "Nothing waiting"} tone={myApprovals ? "warning" : "neutral"} href="/approvals" />
        {canSummary && (
          <>
            <KpiCard label="Active production orders" loading={loading} value={s ? s.production.active_orders : "—"} href={to("production", "orders")}
              hint={s ? (s.production.delayed_work_orders ? `${s.production.delayed_work_orders} past planned date` : "None past planned date") : undefined} tone={s?.production.delayed_work_orders ? "danger" : "neutral"} />
            <KpiCard label="Purchase requests pending" loading={loading} value={s ? s.procurement.purchase_requests_awaiting_approval : "—"} href={to("procurement", "requests")}
              hint={s ? `${plural(s.procurement.purchase_orders_awaiting_approval, "purchase order", "purchase orders")} awaiting approval` : undefined} tone={s?.procurement.purchase_requests_awaiting_approval ? "warning" : "neutral"} />
            <KpiCard label="Inventory alerts" loading={loading} value={s ? s.inventory.below_reorder_level + s.inventory.expired_with_stock : "—"} href={to("inventory", "reorder")}
              hint={s ? `${s.inventory.below_reorder_level} below reorder · ${s.inventory.expired_with_stock} expired with stock` : undefined} tone={s && s.inventory.expired_with_stock ? "danger" : s && s.inventory.below_reorder_level ? "warning" : "neutral"} />
            <KpiCard label="QC inspections open" loading={loading} value={s ? s.quality.open_inspections : "—"} href={to("quality", "inspections")}
              hint={s ? `${plural(view?.held ?? 0, "batch", "batches")} on hold or quarantine` : undefined} tone={view && view.held ? "warning" : "neutral"} />
            <KpiCard label="Shipments open" loading={loading} value={view ? view.pendingShipments : "—"} href={to("dispatch", "shipments")}
              hint={s ? (s.dispatch.delayed_shipments ? `${s.dispatch.delayed_shipments} older than 5 days` : "None older than 5 days") : undefined} tone={s?.dispatch.delayed_shipments ? "danger" : "neutral"} />
          </>
        )}
      </section>

      {canSummary && s && s.finance_extended && (
        <section aria-label="Finance" className="grid sm:grid-cols-3 gap-3 mb-5">
          <KpiCard label="Receivables outstanding" value={fmtMoney(s.sales.receivable_outstanding)} href={to("accounts", "ageing-r")} hint={`${plural(s.finance_extended.open_sales_invoices, "open sales invoice", "open sales invoices")}`} />
          <KpiCard label="Revenue, last 30 days" value={fmtMoney(s.finance_extended.revenue_30d)} href={to("accounts", "pnl")}
            hint={s.finance_extended.gross_margin_pct_30d === null ? "Gross margin needs posted revenue" : `Gross margin ${s.finance_extended.gross_margin_pct_30d}%`} />
          <KpiCard label="Purchases, last 30 days" value={fmtMoney(s.procurement.purchase_value_30d)} href={to("procurement", "orders")} hint="Approved and received purchase orders" />
        </section>
      )}

      {/* Prioritised work and activity */}
      <div className="grid xl:grid-cols-[minmax(0,1fr)_380px] gap-5 mb-5 items-start">
        <Section title="Pending actions" description="Highest severity first. Counts come straight from the underlying records." flush
          action={myApprovals > 0 ? <SectionLink href="/approvals">{myApprovals} for your approval</SectionLink> : undefined}>
          {loading ? <SkeletonRows n={5} />
            : !canSummary ? <p className="px-4 pb-4 text-[13px] text-muted">{myApprovals ? `${myApprovals} item${myApprovals === 1 ? "" : "s"} waiting for your decision in Approvals.` : "Nothing is waiting for your decision."}</p>
            : <WorkQueue items={view?.queue ?? []} empty="No exceptions or pending approvals right now." />}
        </Section>
        <Section title="Recent activity" flush action={canAudit ? <SectionLink href="/admin/audit">Audit log</SectionLink> : undefined}>
          {!canAudit ? <p className="px-4 pb-4 text-[13px] text-muted">The activity feed reads the audit log and is available to auditors and administrators.</p>
            : activity === null ? <SkeletonRows n={5} />
            : activity.length === 0 ? <p className="px-4 pb-4 text-[13px] text-muted">No audited actions have been recorded yet.</p>
            : <div className="pt-1"><Timeline items={activity.map((a) => {
                const screen = RECORD_SCREEN[a.entity];
                return { id: a.id, action: titleCase(a.action), record: `${titleCase(a.entity)}${a.entity_id ? ` #${a.entity_id}` : ""}`, user: a.user_email ?? "system", at: fmtDateTime(a.occurred_at),
                  href: screen && a.entity_id && /^\d+$/.test(a.entity_id) ? `${screen}?open=${a.entity_id}` : undefined };
              })} /></div>}
        </Section>
      </div>

      {/* Workflow pipeline */}
      {canSummary && (
        <Section title="Order-to-dispatch pipeline" description="Open work at each stage. “Most exceptions” marks the stage carrying the largest number of flagged records." className="mb-5">
          {loading || !view ? <div className="skeleton h-28" /> : <Pipeline stages={view.stages} bottleneck={view.busiest} />}
        </Section>
      )}

      {/* Distributions and comparisons that the API can support today */}
      {canSummary && (
        <>
          <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-5">
            <Section title="Production orders by status">{loading ? <SkeletonRows n={2} /> : <StatusBar items={statusItems(s?.production_by_status)} empty={{ text: "No production orders exist yet.", href: to("production", "orders"), cta: "Open production orders" }} />}</Section>
            <Section title="Purchase orders by status">{loading ? <SkeletonRows n={2} /> : <StatusBar items={statusItems(s?.purchase_orders_by_status)} empty={{ text: "No purchase orders exist yet.", href: to("procurement", "orders"), cta: "Open purchase orders" }} />}</Section>
            <Section title="Shipments by status">{loading ? <SkeletonRows n={2} /> : <StatusBar items={statusItems(s?.shipments_by_status)} empty={{ text: "No shipments have been created yet.", href: to("dispatch", "shipments"), cta: "Open shipments" }} />}</Section>
            <Section title="Sales orders by status">{loading ? <SkeletonRows n={2} /> : <StatusBar items={statusItems(s?.orders_by_status)} empty={{ text: "No sales orders exist yet.", href: to("dispatch", "orders"), cta: "Open dispatch orders" }} />}</Section>
            <Section title="Production output, last 30 days" description={s ? `${s.production.completed_30d} orders completed` : undefined}>
              {loading ? <SkeletonRows n={2} /> : !view || (view.planned === 0 && view.actual === 0) ? <p className="text-muted text-[12.5px]">No production order was completed in the last 30 days.</p> : (
                <>
                  <BarRows rows={[{ label: "Planned quantity", value: view.planned, display: fmtNum(view.planned, 1) }, { label: "Actual quantity", value: view.actual, display: fmtNum(view.actual, 1), tone: view.actual >= view.planned ? "success" : "warning" }]} />
                  <dl className="grid grid-cols-2 gap-3 mt-4 text-[12.5px]">
                    <div><dt className="text-muted">Average yield</dt><dd className="tnum font-medium">{s?.production.average_yield_pct == null ? "—" : `${fmtNum(s.production.average_yield_pct, 1)}%`}</dd></div>
                    <div><dt className="text-muted">Wastage</dt><dd className="tnum font-medium">{fmtNum(s?.production.wastage_qty_30d, 1)}</dd></div>
                  </dl>
                </>
              )}
            </Section>
            <Section title="Inventory value by kind" description={s ? `Total ${fmtMoney(s.inventory.total_value)}` : undefined}>
              {loading ? <SkeletonRows n={2} /> : <BarRows rows={(s?.inventory.value_by_kind ?? []).map((k) => ({ label: KIND[String(k.kind).toUpperCase()] ?? titleCase(k.kind), value: num(k.value), display: fmtMoney(k.value) }))} />}
            </Section>
          </div>
          <p className="text-muted text-[12px] mt-4">Trend charts (inventory movement, QC rejection rate, dispatch performance over time) appear once the API returns dated series; none are drawn from estimates.</p>
        </>
      )}
    </>
  );
}
