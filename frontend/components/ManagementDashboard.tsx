"use client";
/** Management summary by department. Same endpoint as the Overview (GET /analytics/management-summary), laid out for review rather than triage:
 *  each section is one function's figures, with a link to the screen that lists the records behind them. */
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtNum, fmtPct, fmtRupees, titleCase } from "@/lib/format";
import { KIND, num, plural, screenHref, statusItems, useSummary } from "@/lib/summary";
import { BarRows, InlineNotice, Metric, MetricGrid, Section, SectionLink, StackRow, StatStrip, type Tone } from "./dash";
import { PageHeader } from "./ui";

const warn = (n: number, tone: Tone = "warning"): Tone => (n > 0 ? tone : "neutral");

export function ManagementDashboard() {
  const { can } = useAuth();
  const { summary: s, error } = useSummary(can("analytics.dashboard:read"));
  const to = (m: string, sc: string) => screenHref(can, m, sc);
  const open = (m: string, sc: string, label = "Open") => { const h = to(m, sc); return h ? <SectionLink href={h}>{label}</SectionLink> : undefined; };

  return (
    <>
      <PageHeader title="Management" description={s ? `Position as of ${fmtDate(s.as_of)}` : undefined} />
      {error && <div className="mb-4"><InlineNotice tone="danger">{error}</InlineNotice></div>}
      {s === undefined && <div className="space-y-5"><div className="panel h-[112px] skeleton" /><div className="grid lg:grid-cols-2 gap-5"><div className="panel h-56 skeleton" /><div className="panel h-56 skeleton" /></div></div>}
      {s && (
        <div className="space-y-5">
          <StatStrip label="Position" cells={[
            { label: "Net revenue, ex tax", value: fmtRupees(s.sales.net_revenue_ex_tax), hint: `${plural(s.sales.invoices, "invoice", "invoices")} raised`, href: to("accounts", "pnl") },
            { label: "Receivables", value: fmtRupees(s.sales.receivable_outstanding), hint: plural(s.finance_extended.open_sales_invoices, "open invoice", "open invoices"), href: to("accounts", "ageing-r") },
            { label: "Payables", value: fmtRupees(s.finance.payable_outstanding), hint: plural(s.finance_extended.open_supplier_invoices, "open supplier invoice", "open supplier invoices"), href: to("accounts", "ageing-p") },
            { label: "Cash and bank", value: fmtRupees(s.finance.cash_and_bank), href: to("accounts", "bank") },
            { label: "Inventory value", value: fmtRupees(s.inventory.total_value), href: to("inventory", "valuation") },
          ]} />

          <div className="grid lg:grid-cols-2 gap-5 items-start">
            <div className="space-y-5 min-w-0">
            <Section title="Sales and finance" action={open("accounts", "trial-balance", "Trial balance")}>
              <MetricGrid>
                <Metric label="Revenue, ex tax" value={fmtRupees(s.sales.revenue_ex_tax)} />
                <Metric label="Credit notes" value={fmtRupees(s.sales.credit_notes_ex_tax)} />
                <Metric label="Cost of goods sold" value={fmtRupees(s.sales.cogs)} />
                <Metric label="Revenue, 30 days" value={fmtRupees(s.finance_extended.revenue_30d)} />
                <Metric label="Gross margin, 30 days" value={fmtPct(s.finance_extended.gross_margin_pct_30d)} hint={s.finance_extended.gross_margin_pct_30d === null ? "No posted revenue" : undefined} />
                <Metric label="Open complaints" value={s.open_complaints} tone={warn(s.open_complaints)} href={to("sales", "complaints")} />
              </MetricGrid>
              <div className="mt-6 pt-5 border-t border-line">
                <StackRow label="Sales orders" items={statusItems(s.orders_by_status)} href={to("dispatch", "orders")} empty="No sales orders yet." />
              </div>
            </Section>
            <Section title="Inventory" action={open("inventory", "stock", "Stock")}>
              <MetricGrid>
                <Metric label="Below reorder level" value={s.inventory.below_reorder_level} tone={warn(s.inventory.below_reorder_level)} href={to("inventory", "reorder")} />
                <Metric label="Expiring in 90 days" value={s.inventory.expiring_within_90_days} tone={warn(s.inventory.expiring_within_90_days)} href={to("inventory", "expiring")} />
                <Metric label="Expired, still in stock" value={s.inventory.expired_with_stock} tone={warn(s.inventory.expired_with_stock, "danger")} />
                <Metric label="Held or quarantined qty" value={fmtNum(s.inventory.quarantined_or_held_qty, 1)} />
              </MetricGrid>
              <div className="mt-6 pt-5 border-t border-line">
                <BarRows rows={s.inventory.value_by_kind.map((k) => ({ label: KIND[String(k.kind).toUpperCase()] ?? titleCase(k.kind), value: num(k.value), display: fmtRupees(k.value) }))} />
              </div>
            </Section>
            <Section title="Quality" action={open("quality", "inspections")}>
              <MetricGrid>
                <Metric label="Inspections open" value={s.quality.open_inspections} tone={warn(s.quality.open_inspections)} href={to("quality", "inspections")} />
                <Metric label="Batches on hold" value={s.quality.batches_on_hold} tone={warn(s.quality.batches_on_hold)} />
                <Metric label="Batches quarantined" value={s.quality.batches_quarantined} tone={warn(s.quality.batches_quarantined)} />
                <Metric label="Open deviations" value={s.quality.open_deviations} tone={warn(s.quality.open_deviations)} href={to("quality", "deviations")} />
                <Metric label="Open CAPA" value={s.quality.open_capa} tone={warn(s.quality.open_capa)} href={to("quality", "capa")} />
                <Metric label="Rejections, 30 days" value={s.quality_extended.rejected_inspections_30d} tone={warn(s.quality_extended.rejected_inspections_30d, "danger")} hint={`${plural(s.quality_extended.rejected_batches, "rejected batch", "rejected batches")} in total`} />
              </MetricGrid>
            </Section>
            <Section title="Maintenance" action={open("maintenance", "equipment", "Equipment")}>
              <StackRow label="Equipment" items={statusItems(s.equipment_by_status)} href={to("maintenance", "equipment")} empty="No equipment registered yet." />
            </Section>
            </div>
            <div className="space-y-5 min-w-0">
            <Section title="Production" action={open("production", "orders")}>
              <MetricGrid>
                <Metric label="In progress" value={s.production.active_orders} href={to("production", "orders")} />
                <Metric label="Past planned date" value={s.production.delayed_work_orders} tone={warn(s.production.delayed_work_orders, "danger")} />
                <Metric label="Completed, 30 days" value={s.production.completed_30d} />
                <Metric label="Average yield" value={fmtPct(s.production.average_yield_pct)} />
                <Metric label="Wastage, 30 days" value={fmtNum(s.production.wastage_qty_30d, 1)} href={to("production", "wastage")} />
              </MetricGrid>
              {(num(s.production.planned_qty_30d) > 0 || num(s.production.actual_qty_30d) > 0) && (
                <div className="mt-6 pt-5 border-t border-line">
                  <BarRows rows={[{ label: "Planned, 30 days", value: num(s.production.planned_qty_30d), display: fmtNum(s.production.planned_qty_30d, 0) },
                    { label: "Actual, 30 days", value: num(s.production.actual_qty_30d), display: fmtNum(s.production.actual_qty_30d, 0), tone: num(s.production.actual_qty_30d) >= num(s.production.planned_qty_30d) ? "success" : "warning" }]} />
                </div>
              )}
              <div className="mt-6 pt-5 border-t border-line">
                <StackRow label="Production orders" items={statusItems(s.production_by_status)} href={to("production", "orders")} empty="No production orders yet." />
              </div>
            </Section>
            <Section title="Procurement" action={open("procurement", "orders", "Purchase orders")}>
              <MetricGrid>
                <Metric label="Requests awaiting approval" value={s.procurement.purchase_requests_awaiting_approval} tone={warn(s.procurement.purchase_requests_awaiting_approval)} href={to("procurement", "requests")} />
                <Metric label="Orders awaiting approval" value={s.procurement.purchase_orders_awaiting_approval} tone={warn(s.procurement.purchase_orders_awaiting_approval)} />
                <Metric label="Open orders" value={s.procurement.open_purchase_orders} href={to("procurement", "delivery")} />
                <Metric label="Late deliveries" value={s.procurement.late_deliveries} tone={warn(s.procurement.late_deliveries, "danger")} />
                <Metric label="Purchases, 30 days" value={fmtRupees(s.procurement.purchase_value_30d)} />
                <Metric label="Supplier on-time" value={fmtPct(s.procurement.supplier_on_time_pct)} hint={s.procurement.supplier_on_time_pct == null ? "No dated receipts yet" : undefined} href={to("procurement", "supplier-performance")} />
              </MetricGrid>
              <div className="mt-6 pt-5 border-t border-line">
                <StackRow label="Purchase orders" items={statusItems(s.purchase_orders_by_status)} href={to("procurement", "orders")} empty="No purchase orders yet." />
              </div>
            </Section>
            <Section title="Dispatch and marketplaces" action={open("dispatch", "shipments", "Shipments")}>
              <MetricGrid>
                <Metric label="Delivered" value={s.dispatch.delivered} />
                <Metric label="Open over 5 days" value={s.dispatch.delayed_shipments} tone={warn(s.dispatch.delayed_shipments, "danger")} />
                <Metric label="Failed or returned to origin" value={s.dispatch.failed_or_rto} tone={warn(s.dispatch.failed_or_rto, "danger")} />
                <Metric label="Customer returns open" value={s.dispatch.returns_open} tone={warn(s.dispatch.returns_open)} href={to("dispatch", "returns")} />
              </MetricGrid>
              <div className="mt-6 pt-5 border-t border-line">
                <StackRow label="Shipments" items={statusItems(s.shipments_by_status)} href={to("dispatch", "shipments")} empty="No shipments yet." />
                <StackRow label="Marketplace orders" items={statusItems(s.marketplace_orders_by_status)} href={to("ecommerce", "orders")} empty="No marketplace orders imported yet." />
              </div>
            </Section>
            </div>
          </div>
        </div>
      )}
      {s === null && !error && <InlineNotice>Management figures need the management summary permission, which your role does not include.</InlineNotice>}
    </>
  );
}
