"use client";
import { useEffect, useMemo, useState } from "react";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtAgo, fmtDate, fmtDateTime, fmtMoney, fmtNum, titleCase } from "@/lib/format";
import { KIND, num, plural, screenHref, SHIPMENT_FINAL, statusItems, sumStatus, useSummary } from "@/lib/summary";
import { PageHeader } from "@/components/ui";
import { BarRows, Flow, InlineNotice, Section, SectionLink, SkeletonRows, StackRow, StatStrip, StockWatch, Timeline, WorkQueue, type Cell, type QueueItem, type Stage } from "@/components/dash";

type Approval = { type: string; id: number; number: string; since: string };
type Audit = { id: number; occurred_at: string; user_email: string | null; action: string; entity: string; entity_id: string | null };

/** Audit entity -> list screen that opens the record via ?open=<id> (the same convention the approvals inbox uses). */
const RECORD_SCREEN: Record<string, string> = {
  purchase_order: "/m/procurement/orders", rfq: "/m/procurement/rfqs", production_order: "/m/production/orders", bom: "/m/production/boms", shipment: "/m/dispatch/shipments",
  sales_return: "/m/dispatch/returns", inspection: "/m/quality/inspections", deviation: "/m/quality/deviations", formulation_version: "/m/rnd/formulations",
  maintenance_work_order: "/m/maintenance/work-orders", content: "/m/branding/content",
};
/** Sign-ins and password events are security records, not business activity; they stay in the audit log. */
const NOISE = /^(login|logout|password|session)/;

export default function Overview() {
  const { me, can } = useAuth();
  const canSummary = can("analytics.dashboard:read");
  const canAudit = can("admin.audit:read");
  const { summary, error } = useSummary(canSummary);
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [activity, setActivity] = useState<Audit[] | null>(null);

  useEffect(() => {
    api<{ items: Approval[] }>("/approvals/pending").then((d) => setApprovals(d.items)).catch(() => setApprovals([]));
    if (canAudit) api<Page<Audit>>("/admin/audit-logs", { params: { page_size: 40 } }).then((p) => setActivity(p.items.filter((a) => !NOISE.test(a.action)).slice(0, 7))).catch(() => setActivity([]));
  }, [canAudit]);

  const to = (m: string, s: string) => screenHref(can, m, s);

  const view = useMemo(() => {
    const s = summary;
    if (!s) return null;
    const q = s.quality, inv = s.inventory, pr = s.procurement, d = s.dispatch, p = s.production;
    const held = q.batches_on_hold + q.batches_quarantined;
    const shipmentsOpen = sumStatus(s.shipments_by_status, (st) => !SHIPMENT_FINAL.includes(st));
    const stages: Stage[] = [
      { label: "Procurement", count: pr.purchase_requests_awaiting_approval + pr.purchase_orders_awaiting_approval, unit: "awaiting approval", href: to("procurement", "orders"), flags: [] },
      { label: "Goods receipt", count: pr.open_purchase_orders, unit: "POs awaiting delivery", href: to("procurement", "receipts"), flags: [{ label: "late", n: pr.late_deliveries, tone: "danger" }] },
      { label: "Inventory", count: inv.below_reorder_level, unit: "below reorder", href: to("inventory", "reorder"),
        flags: [{ label: "expired in stock", n: inv.expired_with_stock, tone: "danger" }, { label: "held", n: held, tone: "warning" }, { label: "expiring in 90 days", n: inv.expiring_within_90_days, tone: "warning" }] },
      { label: "Production", count: p.active_orders, unit: "in progress", href: to("production", "orders"), flags: [{ label: "past planned date", n: p.delayed_work_orders, tone: "danger" }] },
      { label: "Quality", count: q.open_inspections, unit: "inspections open", href: to("quality", "inspections"),
        flags: [{ label: "deviations", n: q.open_deviations, tone: "warning" }, { label: "CAPA open", n: q.open_capa, tone: "warning" }] },
      { label: "Dispatch", count: shipmentsOpen, unit: "shipments open", href: to("dispatch", "shipments"),
        flags: [{ label: "over 5 days", n: d.delayed_shipments, tone: "danger" }, { label: "failed or RTO", n: d.failed_or_rto, tone: "danger" }, { label: "returns open", n: d.returns_open, tone: "warning" }] },
    ];
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
      { key: "returns", label: "Customer returns awaiting action", count: d.returns_open, tone: "warning", href: to("dispatch", "returns") },
    ] as QueueItem[]).filter((i) => (i.count ?? 0) > 0);
    const stock = s.low_stock.map((l) => ({ code: l.item_code, name: l.name, available: num(l.available), reorder: num(l.reorder_level) }));
    return { stages, queue, stock, held, shipmentsOpen, planned: num(p.planned_qty_30d), actual: num(p.actual_qty_30d) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary, can]);

  const mine = approvals?.length ?? 0;
  const loading = canSummary && summary === undefined;
  const s = summary ?? null;

  const money: Cell[] = s ? [
    { label: "Receivables outstanding", value: fmtMoney(s.sales.receivable_outstanding), hint: plural(s.finance_extended.open_sales_invoices, "open invoice", "open invoices"), href: to("accounts", "ageing-r") },
    { label: "Revenue, last 30 days", value: fmtMoney(s.finance_extended.revenue_30d), hint: s.finance_extended.gross_margin_pct_30d === null ? "No posted revenue yet" : `${s.finance_extended.gross_margin_pct_30d}% gross margin`, href: to("accounts", "pnl") },
    { label: "Purchases, last 30 days", value: fmtMoney(s.procurement.purchase_value_30d), hint: plural(s.procurement.open_purchase_orders, "open order", "open orders"), href: to("procurement", "orders") },
    { label: "Inventory value", value: fmtMoney(s.inventory.total_value), hint: `${s.inventory.value_by_kind.length} stock classes`, href: to("inventory", "valuation") },
  ] : [];
  const ops: Cell[] = [
    { label: "Awaiting your approval", value: approvals === null ? "—" : mine, loading: approvals === null, tone: mine ? "warning" : "neutral", hint: mine ? `Oldest ${fmtDate(approvals![0].since)}` : "Nothing waiting", href: "/approvals" },
    ...(canSummary ? [
      { label: "Production in progress", value: s?.production.active_orders ?? "—", loading, tone: s?.production.delayed_work_orders ? "danger" : "neutral", hint: s ? (s.production.delayed_work_orders ? `${s.production.delayed_work_orders} past planned date` : "On schedule") : undefined, href: to("production", "orders") },
      { label: "Inventory alerts", value: s ? s.inventory.below_reorder_level + s.inventory.expired_with_stock : "—", loading, tone: s?.inventory.expired_with_stock ? "danger" : s?.inventory.below_reorder_level ? "warning" : "neutral", hint: s ? `${s.inventory.below_reorder_level} below reorder, ${s.inventory.expired_with_stock} expired` : undefined, href: to("inventory", "reorder") },
      { label: "QC inspections open", value: s?.quality.open_inspections ?? "—", loading, tone: view?.held ? "warning" : "neutral", hint: s ? `${plural(view?.held ?? 0, "batch", "batches")} held` : undefined, href: to("quality", "inspections") },
      { label: "Shipments open", value: view?.shipmentsOpen ?? "—", loading, tone: s?.dispatch.delayed_shipments ? "danger" : "neutral", hint: s ? (s.dispatch.delayed_shipments ? `${s.dispatch.delayed_shipments} over 5 days` : "None over 5 days") : undefined, href: to("dispatch", "shipments") },
    ] as Cell[] : []),
  ];

  return (
    <>
      <PageHeader title="Factory overview" description={s ? `As of ${fmtDate(s.as_of)}` : `Signed in as ${me?.full_name}${me?.department_name ? `, ${me.department_name}` : ""}`} />

      {error && <div className="mb-4"><InlineNotice tone="danger">{error} Reload to try again.</InlineNotice></div>}
      {!canSummary && <div className="mb-4"><InlineNotice>Company-wide figures need the management summary permission, which your role does not include.</InlineNotice></div>}

      <div className="space-y-5">
        {canSummary && (loading ? <div className="panel h-[112px] skeleton" /> : s && <StatStrip label="Finance" cells={money} size="text-[22px] sm:text-[32px]" />)}
        <StatStrip label="Operations" cells={ops} />

        {canSummary && <Section title="Order to dispatch">{loading || !view ? <div className="skeleton h-24" /> : <Flow stages={view.stages} />}</Section>}

        <div className="grid xl:grid-cols-[minmax(0,1fr)_380px] gap-5 items-start">
          <div className="space-y-5 min-w-0">
            <Section title="Pending actions" flush action={mine > 0 ? <SectionLink href="/approvals">{mine} for your approval</SectionLink> : undefined}>
              {!canSummary ? <p className="px-5 pb-5 text-[13px] text-muted">{mine ? `${plural(mine, "item", "items")} waiting for your decision.` : "Nothing is waiting for your decision."}</p>
                : loading ? <SkeletonRows n={5} /> : <WorkQueue items={view?.queue ?? []} empty="No exceptions or approvals outstanding." />}
            </Section>
            {view && view.stock.length > 0 && (
              <Section title="Below reorder level" flush action={to("inventory", "reorder") ? <SectionLink href={to("inventory", "reorder")!}>Reorder report</SectionLink> : undefined}>
                <StockWatch rows={view.stock.slice(0, 6)} />
              </Section>
            )}
            {canSummary && (
              <>
                <Section title="Work by status">
                  {loading ? <SkeletonRows n={4} /> : (
                    <>
                      <StackRow label="Sales orders" items={statusItems(s?.orders_by_status)} href={to("dispatch", "orders")} empty="No sales orders yet." />
                      <StackRow label="Production orders" items={statusItems(s?.production_by_status)} href={to("production", "orders")} empty="No production orders yet." />
                      <StackRow label="Purchase orders" items={statusItems(s?.purchase_orders_by_status)} href={to("procurement", "orders")} empty="No purchase orders yet." />
                      <StackRow label="Shipments" items={statusItems(s?.shipments_by_status)} href={to("dispatch", "shipments")} empty="No shipments yet." />
                    </>
                  )}
                </Section>
                <Section title="Inventory value" description={s ? `By class, total ${fmtMoney(s.inventory.total_value)}` : undefined} action={to("inventory", "valuation") ? <SectionLink href={to("inventory", "valuation")!}>Valuation</SectionLink> : undefined}>
                  {loading ? <SkeletonRows n={2} /> : <BarRows rows={(s?.inventory.value_by_kind ?? []).map((k) => ({ label: KIND[String(k.kind).toUpperCase()] ?? titleCase(k.kind), value: num(k.value), display: fmtMoney(k.value) }))} />}
                </Section>
              </>
            )}
          </div>
          <div className="space-y-5 min-w-0">
            <Section title="Recent activity" flush action={canAudit ? <SectionLink href="/admin/audit">Audit log</SectionLink> : undefined}>
              {!canAudit ? <p className="px-5 pb-5 text-[13px] text-muted">Activity is read from the audit log, which is limited to auditors and administrators.</p>
                : activity === null ? <SkeletonRows n={5} />
                : activity.length === 0 ? <p className="px-5 pb-5 text-[13px] text-muted">No business actions recorded yet.</p>
                : <Timeline items={activity.map((a) => {
                    const screen = RECORD_SCREEN[a.entity];
                    return { id: a.id, action: titleCase(a.action), record: `${titleCase(a.entity)}${a.entity_id ? ` #${a.entity_id}` : ""}`, user: a.user_email?.split("@")[0] ?? "system", ago: fmtAgo(a.occurred_at), at: fmtDateTime(a.occurred_at),
                      href: screen && a.entity_id && /^\d+$/.test(a.entity_id) ? `${screen}?open=${a.entity_id}` : undefined };
                  })} />}
            </Section>
            {canSummary && (
              <Section title="Production output" description="Completed orders, last 30 days">
                {loading ? <SkeletonRows n={2} /> : !view || (view.planned === 0 && view.actual === 0) ? <p className="text-muted text-[12.5px]">No production order was completed in the last 30 days.</p> : (
                  <>
                    <BarRows rows={[{ label: "Planned", value: view.planned, display: fmtNum(view.planned, 0) }, { label: "Actual", value: view.actual, display: fmtNum(view.actual, 0), tone: view.actual >= view.planned ? "success" : "warning" }]} />
                    <p className="mt-4 text-[12px] text-muted tnum">{s?.production.completed_30d} orders · yield {s?.production.average_yield_pct == null ? "—" : `${fmtNum(s.production.average_yield_pct, 1)}%`} · wastage {fmtNum(s?.production.wastage_qty_30d, 1)}</p>
                  </>
                )}
              </Section>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
