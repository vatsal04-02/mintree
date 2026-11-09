"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { addDays, businessToday, daysBetween, formatName, fmtAgo, fmtDate, fmtDateTime, fmtDay, fmtDelta, fmtNum, fmtPct, fmtRupees, fyStart, titleCase } from "@/lib/format";
import { toneFor } from "@/lib/modules";
import { num, plural, screenHref, SHIPMENT_FINAL, sumStatus, usePeriod, useSummary } from "@/lib/summary";
import { Package, Receipt, TrendingUp, Factory } from "lucide-react";
import { Badge } from "@/components/ui";
import { ActivityTimeline, Block, EmptyState, ExceptionList, GroupedBars, Legend, LinkMore, MetricStrip, Panel, Pipeline, Segmented, Skeleton, TransactionTable, type Bucket, type Exception, type Metric, type PipelineStage, type TxRow } from "@/components/blocks";

type Approval = { type: string; id: number; number: string; since: string };
type Audit = { id: number; occurred_at: string; user_email: string | null; action: string; entity: string; entity_id: string | null };
type PeriodKey = "30d" | "90d" | "fy" | "custom";

/** Audit entity and document kind -> the list screen that opens the record via ?open=<id>. */
const RECORD_SCREEN: Record<string, [string, string]> = {
  purchase_order: ["procurement", "orders"], rfq: ["procurement", "rfqs"], production_order: ["production", "orders"], bom: ["production", "boms"], shipment: ["dispatch", "shipments"],
  sales_order: ["dispatch", "orders"], sales_invoice: ["dispatch", "invoices"], sales_return: ["dispatch", "returns"], inspection: ["quality", "inspections"], deviation: ["quality", "deviations"],
  formulation_version: ["rnd", "formulations"], maintenance_work_order: ["maintenance", "work-orders"], content: ["branding", "content"],
};
const KIND_LABEL: Record<string, string> = { purchase_order: "Purchase order", production_order: "Production order", sales_order: "Sales order", shipment: "Shipment", sales_invoice: "Sales invoice" };
/** Sign-ins and password events are security records, not business activity; they stay in the audit log. */
const NOISE = /^(login|logout|password|session)/;

export default function Overview() {
  const { me, can } = useAuth();
  const canSummary = can("analytics.dashboard:read");
  const canAudit = can("admin.audit:read");
  const today = businessToday();
  const [key, setKey] = useState<PeriodKey>("30d");
  const [custom, setCustom] = useState({ start: addDays(today, -29), end: today });
  const range = key === "30d" ? { start: addDays(today, -29), end: today } : key === "90d" ? { start: addDays(today, -89), end: today } : key === "fy" ? { start: fyStart(today), end: today } : custom;
  const rangeError = range.end < range.start ? "The end date is before the start date." : daysBetween(range.start, range.end) > 399 ? "The period cannot exceed 400 days." : null;

  const { summary, error } = useSummary(canSummary);
  const period = usePeriod(canSummary && !rangeError, rangeError ? today : range.start, rangeError ? today : range.end);
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [activity, setActivity] = useState<Audit[] | null>(null);
  useEffect(() => {
    api<{ items: Approval[] }>("/approvals/pending").then((d) => setApprovals(d.items)).catch(() => setApprovals([]));
    if (canAudit) api<Page<Audit>>("/admin/audit-logs", { params: { page_size: 40 } }).then((p) => setActivity(p.items.filter((a) => !NOISE.test(a.action)).slice(0, 6))).catch(() => setActivity([]));
  }, [canAudit]);

  const to = (m: string, s: string) => screenHref(can, m, s);
  const open = (kind: string, id: number | string) => { const t = RECORD_SCREEN[kind]; const h = t && to(t[0], t[1]); return h && /^\d+$/.test(String(id)) ? `${h}?open=${id}` : undefined; };
  const s = summary ?? null;
  const loading = canSummary && summary === undefined;

  /* ---- the four indicators ---- */
  const metrics: Metric[] = useMemo(() => {
    if (!s) return ["Receivables outstanding", "Net revenue", "Production orders active", "Inventory needing attention"].map((label) => ({ label, value: "", loading: true }));
    const attention = s.inventory.below_reorder_level + s.inventory.expired_with_stock + s.quality.batches_on_hold + s.quality.batches_quarantined;
    const delta = period ? fmtDelta(num(period.revenue.net_ex_tax), num(period.revenue.previous_net_ex_tax)) : null;
    return [
      { label: "Receivables outstanding", icon: Receipt, value: fmtRupees(s.sales.receivable_outstanding), note: plural(s.finance_extended.open_sales_invoices, "open invoice", "open invoices"), href: to("accounts", "ageing-r") },
      period === undefined ? { label: "Net revenue, ex-tax", value: "", loading: true, hero: true }
        : { label: `Net revenue, ex-tax`, hero: true, icon: TrendingUp, value: period ? fmtRupees(period.revenue.net_ex_tax) : "—", note: !period ? "Unavailable" : period.revenue.invoices ? plural(period.revenue.invoices, "invoice", "invoices") : "No invoices in this period", delta: period && delta ? { text: `${delta} vs prev ${daysBetween(period.previous.start, period.previous.end) + 1}d`, tone: /^[+−]0(\.0)?%$/.test(delta) ? "neutral" : delta.startsWith("+") ? "success" : "danger" } : undefined, href: to("accounts", "pnl") },
      { label: "Production orders active", icon: Factory, value: fmtNum(s.production.active_orders), note: s.production.delayed_work_orders ? `${fmtNum(s.production.delayed_work_orders)} past planned date` : "On schedule", noteTone: s.production.delayed_work_orders ? "danger" : undefined, href: to("production", "orders") },
      { label: "Inventory needing attention", icon: Package, value: fmtNum(attention), note: attention ? [s.inventory.below_reorder_level && `${fmtNum(s.inventory.below_reorder_level)} below reorder`, (s.quality.batches_on_hold + s.quality.batches_quarantined) && `${fmtNum(s.quality.batches_on_hold + s.quality.batches_quarantined)} held`, s.inventory.expired_with_stock && `${fmtNum(s.inventory.expired_with_stock)} expired`].filter(Boolean).join(" · ") : "Nothing flagged", noteTone: s.inventory.expired_with_stock ? "danger" : undefined, href: to("inventory", "reorder") },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s, period, can]);

  /* ---- exceptions: real, non-zero conditions only, most severe first ---- */
  const exceptions: Exception[] = useMemo(() => {
    if (!s) return [];
    const q = s.quality, inv = s.inventory, pr = s.procurement, d = s.dispatch, p = s.production;
    const held = q.batches_on_hold + q.batches_quarantined;
    const od = period?.overdue_invoices;
    const list: Exception[] = [
      { key: "expired", label: "Expired batches still holding stock", value: fmtNum(inv.expired_with_stock), severity: "high", href: to("inventory", "expiring") },
      { key: "od", label: "Invoices past their payment terms", detail: od ? `${fmtRupees(od.amount)} outstanding` : undefined, value: od ? fmtNum(od.count) : undefined, severity: "high", href: to("accounts", "ageing-r") },
      { key: "wo", label: "Production orders past planned date", value: fmtNum(p.delayed_work_orders), severity: "high", href: to("production", "orders") },
      { key: "ship", label: "Shipments open longer than 5 days", value: fmtNum(d.delayed_shipments), severity: "high", href: to("dispatch", "shipments") },
      { key: "fail", label: "Failed deliveries and returns to origin", value: fmtNum(d.failed_or_rto), severity: "high", href: to("dispatch", "shipments") },
      { key: "po-late", label: "Purchase orders past expected delivery", value: fmtNum(pr.late_deliveries), severity: "high", href: to("procurement", "delivery") },
      { key: "qc", label: "Batches on QC hold or in quarantine", value: fmtNum(held), severity: "medium", href: to("inventory", "batches") },
      { key: "insp", label: "Inspections awaiting a decision", value: fmtNum(q.open_inspections), severity: "medium", href: to("quality", "inspections") },
      { key: "dev", label: "Open deviations", value: fmtNum(q.open_deviations), severity: "medium", href: to("quality", "deviations") },
      { key: "pr", label: "Purchase requests awaiting approval", value: fmtNum(pr.purchase_requests_awaiting_approval), severity: "medium", href: to("procurement", "requests") },
      { key: "po", label: "Purchase orders awaiting approval", value: fmtNum(pr.purchase_orders_awaiting_approval), severity: "medium", href: to("procurement", "orders") },
      { key: "ret", label: "Customer returns awaiting action", value: fmtNum(d.returns_open), severity: "medium", href: to("dispatch", "returns") },
      ...s.low_stock.slice(0, 3).map((l): Exception => ({ key: `low-${l.item_code}`, label: formatName(l.name), detail: `${l.item_code} · below reorder level`, value: `${fmtNum(l.available, 1)} of ${fmtNum(l.reorder_level, 1)}`, severity: "medium", ratio: num(l.available) / (num(l.reorder_level) || 1), href: to("inventory", "reorder") })),
    ];
    return list.filter((e) => e.value !== undefined && e.value !== "0").slice(0, 10);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s, period, can]);

  /* ---- pipeline: stage-specific counts that the strip does not show ---- */
  const stages: PipelineStage[] = useMemo(() => {
    if (!s) return [];
    const pr = s.procurement, q = s.quality, d = s.dispatch, inv = s.inventory;
    const queued = sumStatus(s.production_by_status, (x) => ["PLANNED", "RELEASED"].includes(x));
    const ready = sumStatus(s.orders_by_status, (x) => ["PACKED", "PARTIALLY_DISPATCHED"].includes(x));
    const open = sumStatus(s.shipments_by_status, (x) => !SHIPMENT_FINAL.includes(x));
    return [
      { label: "Procurement", active: pr.purchase_requests_awaiting_approval + pr.purchase_orders_awaiting_approval > 0, value: fmtNum(pr.purchase_requests_awaiting_approval + pr.purchase_orders_awaiting_approval), note: "awaiting approval", href: to("procurement", "orders") },
      { label: "Goods receipt", active: pr.open_purchase_orders > 0, alert: pr.late_deliveries > 0 ? "danger" : undefined, value: fmtNum(pr.open_purchase_orders), note: "orders awaiting delivery", flag: pr.late_deliveries ? `${fmtNum(pr.late_deliveries)} late` : undefined, href: to("procurement", "receipts") },
      { label: "Inventory", value: fmtRupees(inv.total_value), note: "stock on hand at cost", flag: inv.expiring_within_90_days ? `${fmtNum(inv.expiring_within_90_days)} batches expire within 90 days` : undefined, href: to("inventory", "valuation") },
      { label: "Production", active: queued > 0, value: fmtNum(queued), note: "queued to start", href: to("production", "orders") },
      { label: "Quality", active: q.open_inspections > 0, alert: q.open_deviations > 0 ? "warn" : undefined, value: fmtNum(q.open_inspections), note: "inspections open", flag: q.open_deviations ? `${fmtNum(q.open_deviations)} deviations open` : undefined, href: to("quality", "inspections") },
      { label: "Dispatch", active: ready > 0 || open > 0, alert: d.delayed_shipments > 0 ? "danger" : undefined, value: fmtNum(ready), note: `ready to ship · ${fmtNum(open)} in transit`, flag: d.delayed_shipments ? `${fmtNum(d.delayed_shipments)} over 5 days` : undefined, href: to("dispatch", "orders") },
    ] as PipelineStage[];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s, can]);

  /* ---- production chart ---- */
  const buckets: Bucket[] = (period?.production.buckets ?? []).map((b) => ({ start: b.week_start, planned: b.planned === null ? null : num(b.planned), actual: b.actual === null ? null : num(b.actual), orders: b.orders }));
  const totals = buckets.reduce((a, b) => ({ p: a.p + (b.planned ?? 0), a: a.a + (b.actual ?? 0) }), { p: 0, a: 0 });
  const enough = (period?.production.weeks_with_data ?? 0) >= 1;

  const tx: TxRow[] = (period?.recent ?? []).map((r) => ({ key: `${r.kind}-${r.id}`, reference: r.reference, activity: KIND_LABEL[r.kind] ?? titleCase(r.kind), related: r.related ?? "—", date: fmtDate(r.created_at), href: open(r.kind, r.id),
    status: <Badge tone={toneFor(r.status)}>{titleCase(r.status)}</Badge> }));

  const mine = approvals?.length ?? 0;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 pb-2">
        <div className="min-w-0">
          <h1 className="t-page">Factory overview</h1>
          <p className="t-label mt-1">
            {rangeError ? <span className="text-danger">{rangeError}</span> : <>{fmtDay(range.start)} – {fmtDate(range.end)} <span aria-hidden>·</span> {plural(daysBetween(range.start, range.end) + 1, "day", "days")}{s && <> <span aria-hidden>·</span> data as of {fmtDate(s.as_of)}{s.meta && <>, computed {fmtAgo(s.meta.generated_at)} (refreshed at most every {fmtNum(s.meta.max_age_s)} s)</>}</>}</>}
          </p>
        </div>
        {canSummary && (
          <div className="flex flex-wrap items-center gap-2">
            {key === "custom" && (
              <div className="flex items-center gap-1.5">
                <label className="sr-only" htmlFor="p-from">From</label><input id="p-from" type="date" className="input !w-[138px] !h-[30px]" value={custom.start} max={custom.end} onChange={(e) => setCustom((c) => ({ ...c, start: e.target.value }))} />
                <span className="t-support" aria-hidden>to</span>
                <label className="sr-only" htmlFor="p-to">To</label><input id="p-to" type="date" className="input !w-[138px] !h-[30px]" value={custom.end} min={custom.start} max={today} onChange={(e) => setCustom((c) => ({ ...c, end: e.target.value }))} />
              </div>
            )}
            <Segmented<PeriodKey> label="Reporting period" value={key} onChange={setKey} options={[{ value: "30d", label: "30 days" }, { value: "90d", label: "90 days" }, { value: "fy", label: "This FY" }, { value: "custom", label: "Custom" }]} />
            {mine > 0 && <Link href="/approvals" className="btn btn-sm">Approvals <span className="inline-grid place-items-center h-5 min-w-5 px-1 rounded-full bg-ink text-[var(--fg-on-ink)] text-[11px] font-semibold">{mine}</span></Link>}
          </div>
        )}
      </header>

      {error && <div role="alert" className="panel px-4 py-3 text-danger text-[13px]">{error} Reload to try again.</div>}
      {!canSummary && <p className="panel px-4 py-3 t-label">Company-wide figures need the management summary permission, which {me?.roles[0] ? `the ${me.roles[0]} role` : "your role"} does not include.</p>}

      {canSummary && <MetricStrip label="Key indicators" items={metrics} />}

      {canSummary && (
        <div className={`grid gap-4 ${(period?.production.weeks_with_data ?? 0) >= 3 ? "xl:grid-cols-[minmax(0,13fr)_minmax(0,7fr)]" : "xl:grid-cols-2"} ${enough ? "items-stretch" : "items-start"}`}>
          <Panel title="Production output" className={enough ? "" : "min-h-[220px]"} bodyClass="flex flex-col" meta="Completed orders by week, planned against actual units" action={enough ? <Legend items={[{ label: "Planned", color: "var(--butter-deep)" }, { label: "Actual", color: "var(--chart-actual)" }]} /> : undefined}>
            {period === undefined ? <div className="px-5 pb-5"><Skeleton className="h-[248px]" /></div>
              : !period ? <EmptyState title="Production output is unavailable" hint="The period figures could not be loaded." />
              : !enough ? <EmptyState title="No completed production yet" hint="Output is charted from completed production orders. None were completed in this period." action={to("production", "orders") ? <LinkMore href={to("production", "orders")!}>Production orders</LinkMore> : undefined} />
              : (
                <>
                  <p className="px-5 -mt-1 mb-2 t-label"><span className="t-value text-fg">{fmtNum(totals.a)}</span> units made of <span className="t-value text-fg">{fmtNum(totals.p)}</span> planned{totals.p > 0 && <> <span aria-hidden>·</span> {fmtPct((totals.a / totals.p) * 100)} of plan</>}</p>
                  <div className="px-2 pb-3 flex flex-col flex-1 min-h-0"><GroupedBars buckets={buckets} labels={["Planned", "Actual"]} unit="units" /></div>
                  {period.production.weeks_with_data === 1 && <p className="px-5 pb-4 t-support">Add one more completed week to see a trend.</p>}
                </>
              )}
          </Panel>
          <Panel title="Needs attention" meta={loading ? undefined : exceptions.length ? `${plural(exceptions.length, "item", "items")}, most severe first` : undefined} bodyClass="overflow-hidden">
            {loading ? <div className="px-5 pb-5 space-y-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-4" />)}</div> : <ExceptionList items={exceptions} empty="Nothing needs attention right now." />}
          </Panel>
        </div>
      )}

      {canSummary && (
        <Block bare className="pt-4" title="Order to dispatch" meta="where work is waiting">
          {loading ? <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[112px] !rounded-2xl" />)}</div> : <Pipeline stages={stages} />}
        </Block>
      )}

      <div className="grid xl:grid-cols-[minmax(0,13fr)_minmax(0,7fr)] gap-4 pt-4">
        {canSummary && (
          <Block title="Recent transactions" action={to("dispatch", "invoices") ? <LinkMore href={to("dispatch", "invoices")!}>Sales invoices</LinkMore> : undefined} className="min-w-0">
            {period === undefined ? <div className="pt-3 space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-4" />)}</div>
              : <TransactionTable rows={tx} empty="No documents have been created yet." />}
          </Block>
        )}
        <Block title="Approvals and activity" className={`min-w-0 ${canSummary ? "" : "xl:col-span-2"}`} action={<LinkMore href="/approvals">Approvals</LinkMore>}>
          <div className="pt-3">
            <p className="t-label mb-1">{approvals === null ? "Checking approvals…" : mine ? <><span className="badge tone-warning">{fmtNum(mine)} waiting for your decision, oldest since {fmtDate(approvals[0].since)}</span></> : "Nothing is waiting for your decision."}</p>
            {approvals && approvals.slice(0, 3).map((a) => <Link key={`${a.type}-${a.id}`} href="/approvals" className="flex items-baseline justify-between gap-3 py-1.5 border-t border-line row-link -mx-2 px-2 rounded-md first:mt-2"><span className="truncate text-[13px]"><span className="font-mono text-[12.5px]">{a.number}</span> <span className="text-muted">{titleCase(a.type)}</span></span><span className="t-support shrink-0">{fmtAgo(a.since)}</span></Link>)}
          </div>
          <h3 className="eyebrow mt-5 mb-2">Recent activity</h3>
          {!canAudit ? <p className="t-support">Activity is read from the audit log, which is limited to auditors and administrators.</p>
            : activity === null ? <div className="space-y-3 pt-1">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-4" />)}</div>
            : activity.length === 0 ? <p className="t-support">No business actions recorded yet.</p>
            : <ActivityTimeline items={activity.map((a) => ({ id: a.id, title: titleCase(a.action), record: `${titleCase(a.entity)}${a.entity_id ? ` #${a.entity_id}` : ""}`, who: a.user_email?.split("@")[0] ?? "system", ago: fmtAgo(a.occurred_at), at: fmtDateTime(a.occurred_at), href: a.entity_id ? open(a.entity, a.entity_id) : undefined }))} />}
        </Block>
      </div>
    </div>
  );
}
