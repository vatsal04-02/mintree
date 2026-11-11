"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { sharedGet } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { addDays, businessToday, daysBetween, formatName, fmtAgo, fmtDate, fmtDay, fmtDelta, fmtNum, fmtPct, fmtRupees, fyStart, titleCase } from "@/lib/format";
import { toneFor } from "@/lib/modules";
import { num, plural, screenHref, SHIPMENT_FINAL, sumStatus, usePeriod, useSummary } from "@/lib/summary";
import { Package, Receipt, TrendingUp, Factory } from "lucide-react";
import { Badge } from "@/components/ui";
import { InView, RankedBars, StackBar, type Point } from "@/components/fx";
import { AlertCards, BusinessHealth, CleanroomPanel, CommandHero, MarketplacePipeline, MetaPanel, ProductionLines, QUICK, ReceivableAgeing, ShareBars } from "@/components/command";
import { Block, EmptyState, ExceptionList, GroupedBars, Legend, LinkMore, MetricStrip, Panel, Pipeline, Segmented, Skeleton, TransactionTable, type Bucket, type Exception, type Metric, type PipelineStage, type TxRow } from "@/components/blocks";

type Approval = { type: string; id: number; number: string; since: string };
type PeriodKey = "30d" | "90d" | "fy" | "custom";
type Trends = { granularity: "day" | "week"; revenue_trend: Point[]; revenue_prev_trend: Point[]; collections_trend: Point[]; units_trend: Point[]; invoice_value_by_status: Record<string, number>; top_customers: Record<string, number>; stock_health: Record<string, number>; revenue_by_channel: Record<string, number>; top_products: Record<string, number>; receivable_ageing: Record<string, number> };

/** Audit entity and document kind -> the list screen that opens the record via ?open=<id>. */
const RECORD_SCREEN: Record<string, [string, string]> = {
  purchase_order: ["procurement", "orders"], rfq: ["procurement", "rfqs"], production_order: ["production", "orders"], bom: ["production", "boms"], shipment: ["dispatch", "shipments"],
  sales_order: ["dispatch", "orders"], sales_invoice: ["dispatch", "invoices"], sales_return: ["dispatch", "returns"], inspection: ["quality", "inspections"], deviation: ["quality", "deviations"],
  formulation_version: ["rnd", "formulations"], maintenance_work_order: ["maintenance", "work-orders"], content: ["branding", "content"],
};
const KIND_LABEL: Record<string, string> = { purchase_order: "Purchase order", production_order: "Production order", sales_order: "Sales order", shipment: "Shipment", sales_invoice: "Sales invoice" };

const p0 = (s: { production: { delayed_work_orders: number } }) => s.production.delayed_work_orders;

export default function Overview() {
  const { me, can } = useAuth();
  const canSummary = can("analytics.dashboard:read");
  const today = businessToday();
  const [key, setKey] = useState<PeriodKey>("30d");
  const [custom, setCustom] = useState({ start: addDays(today, -29), end: today });
  const range = key === "30d" ? { start: addDays(today, -29), end: today } : key === "90d" ? { start: addDays(today, -89), end: today } : key === "fy" ? { start: fyStart(today), end: today } : custom;
  const rangeError = range.end < range.start ? "The end date is before the start date." : daysBetween(range.start, range.end) > 399 ? "The period cannot exceed 400 days." : null;

  const { summary, error } = useSummary(canSummary);
  const period = usePeriod(canSummary && !rangeError, rangeError ? today : range.start, rangeError ? today : range.end);
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  useEffect(() => {
    sharedGet<{ items: Approval[] }>("/approvals/pending").then((d) => setApprovals(d.items)).catch(() => setApprovals([]));
  }, []);

  /* the series behind the revenue chart, the sparklines and the ring: revenue for the selected period against the one before it (one request, shared) */
  const [trends, setTrends] = useState<Trends | null | undefined>(undefined);
  useEffect(() => {
    if (!canSummary || rangeError) return;
    let live = true;
    sharedGet<Trends>("/analytics/overview-trends", { start: range.start, end: range.end }).then((d) => live && setTrends(d)).catch(() => live && setTrends(null));
    return () => { live = false; };
  }, [canSummary, rangeError, range.start, range.end]);

  const to = (m: string, s: string) => screenHref(can, m, s);
  const withQuery = (href: string | undefined, qs: string) => (href ? `${href}?${qs}` : undefined);
  const open = (kind: string, id: number | string) => { const t = RECORD_SCREEN[kind]; const h = t && to(t[0], t[1]); return h && /^\d+$/.test(String(id)) ? `${h}?open=${id}` : undefined; };
  const s = summary ?? null;
  const loading = canSummary && summary === undefined;

  /* ---- the four indicators ---- */
  const metrics: Metric[] = useMemo(() => {
    if (!s) return ["Receivables outstanding", "Net revenue, ex-tax", "Active production orders", "Inventory alerts"].map((label) => ({ label, value: "", loading: true }));
    const attention = s.inventory.below_reorder_level + s.inventory.expired_with_stock + s.quality.batches_on_hold + s.quality.batches_quarantined;
    const delta = period ? fmtDelta(num(period.revenue.net_ex_tax), num(period.revenue.previous_net_ex_tax)) : null;
    return [
      { label: "Receivables outstanding", icon: Receipt, accent: "var(--c-slate)", value: fmtRupees(s.sales.receivable_outstanding), note: plural(s.finance_extended.open_sales_invoices, "open invoice", "open invoices"), href: to("accounts", "ageing-r"),
        spark: trends ? { points: trends.collections_trend.map((p) => p.value), label: "Payments received over the period", caption: "Payments received" } : undefined },
      period === undefined ? { label: "Net revenue, ex-tax", value: "", loading: true, hero: true }
        : { label: `Net revenue, ex-tax`, hero: true, icon: TrendingUp, value: period ? fmtRupees(period.revenue.net_ex_tax) : "—", note: !period ? "Unavailable" : period.revenue.invoices ? plural(period.revenue.invoices, "invoice", "invoices") : "No invoices in this period", delta: period && delta ? { text: `${delta} vs prev ${daysBetween(period.previous.start, period.previous.end) + 1}d`, tone: /^[+−]0(\.0)?%$/.test(delta) ? "neutral" : delta.startsWith("+") ? "success" : "danger" } : undefined, href: to("accounts", "pnl"), spark: trends ? { points: trends.revenue_trend.map((p) => p.value), label: "Invoiced revenue over the period", caption: "Invoiced each " + (trends.granularity === "week" ? "week" : "day") } : undefined },
      { label: "Active production orders", icon: Factory, unit: "orders", accent: "var(--chart-fill)", value: fmtNum(s.production.active_orders), note: s.production.delayed_work_orders ? `${fmtNum(s.production.delayed_work_orders)} past planned date` : "On schedule", noteTone: s.production.delayed_work_orders ? "danger" : undefined, href: to("production", "orders"),
        spark: trends ? { points: trends.units_trend.map((p) => p.value), label: "Units made over the period", caption: "Units made" } : undefined },
      { label: "Inventory alerts", icon: Package, unit: "alerts", accent: "var(--amber)", value: fmtNum(attention), note: attention ? [s.inventory.below_reorder_level && `${fmtNum(s.inventory.below_reorder_level)} below reorder`, (s.quality.batches_on_hold + s.quality.batches_quarantined) && `${fmtNum(s.quality.batches_on_hold + s.quality.batches_quarantined)} held`, s.inventory.expired_with_stock && `${fmtNum(s.inventory.expired_with_stock)} expired`].filter(Boolean).join(" · ") : "Nothing flagged", noteTone: s.inventory.expired_with_stock ? "danger" : undefined, href: to("inventory", "reorder"),
        extra: trends ? <div><StackBar data={trends.stock_health} label="Batches by quality status" /><p className="t-support mt-1.5">{Object.entries(trends.stock_health).map(([k, v]) => `${fmtNum(v)} ${k.toLowerCase()}`).join(" · ")}</p></div> : undefined },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s, period, can, trends]);

  /* ---- exceptions: real, non-zero conditions only, most severe first. Act now / Review / Monitor; each says how long it has waited and what money is tied up where the data has it ---- */
  const exceptions: Exception[] = useMemo(() => {
    if (!s) return [];
    const q = s.quality, inv = s.inventory, pr = s.procurement, d = s.dispatch, p = s.production;
    const held = q.batches_on_hold + q.batches_quarantined;
    const od = period?.overdue_invoices, ex = period?.exposure;
    const days = (n?: number) => (n === undefined ? undefined : `${fmtNum(n)} ${n === 1 ? "day" : "days"}`);
    const list: Exception[] = [
      { key: "expired", label: "Expired batches still holding stock", value: fmtNum(inv.expired_with_stock), severity: "high", href: to("inventory", "expiring") },
      { key: "od", label: "Invoices past their payment terms", detail: od ? [`${fmtRupees(od.amount)} due`, od.oldest_days ? `oldest ${days(od.oldest_days)} overdue` : ""].filter(Boolean).join(" · ") : undefined, value: od ? fmtNum(od.count) : undefined, severity: "high", href: to("accounts", "ageing-r") },
      { key: "po-late", label: "Purchase orders past expected delivery", detail: ex ? [`${fmtRupees(ex.late_purchase_orders.amount)} on order`, ex.late_purchase_orders.oldest_days ? `oldest ${days(ex.late_purchase_orders.oldest_days)} late` : ""].filter(Boolean).join(" · ") : undefined, value: fmtNum(pr.late_deliveries), severity: "high", href: to("procurement", "delivery") },
      { key: "wo", label: "Production orders past planned date", detail: ex && ex.late_production.oldest_days ? `oldest ${days(ex.late_production.oldest_days)} late` : undefined, value: fmtNum(p.delayed_work_orders), severity: "high", href: to("production", "orders") },
      { key: "ship", label: "Shipments open longer than 5 days", detail: ex && ex.late_shipments.oldest_days ? `oldest ${days(ex.late_shipments.oldest_days)} open` : undefined, value: fmtNum(d.delayed_shipments), severity: "high", href: to("dispatch", "shipments") },
      { key: "fail", label: "Failed deliveries and returns to origin", value: fmtNum(d.failed_or_rto), severity: "high", href: to("dispatch", "shipments") },
      { key: "qc", label: "Batches on QC hold or in quarantine", value: fmtNum(held), severity: "medium", href: to("inventory", "batches") },
      { key: "dev", label: "Open deviations", value: fmtNum(q.open_deviations), severity: "medium", href: to("quality", "deviations") },
      { key: "pr", label: "Purchase requests awaiting approval", value: fmtNum(pr.purchase_requests_awaiting_approval), severity: "medium", href: withQuery(to("procurement", "requests"), "status=SUBMITTED") },
      ...s.low_stock.slice(0, 3).map((l): Exception => ({ key: `low-${l.item_code}`, label: formatName(l.name), detail: `${l.item_code} · below reorder level`, value: `${fmtNum(l.available, 1)} of ${fmtNum(l.reorder_level, 1)}`, severity: "medium", ratio: num(l.available) / (num(l.reorder_level) || 1), href: withQuery(to("inventory", "stock"), `q=${encodeURIComponent(l.item_code)}`) })),
      { key: "exp90", label: "Batches expiring within 90 days", value: fmtNum(inv.expiring_within_90_days), severity: "low", href: to("inventory", "expiring") },
      { key: "insp", label: "Inspections awaiting a decision", value: fmtNum(q.open_inspections), severity: "low", href: to("quality", "inspections") },
      { key: "po", label: "Purchase orders awaiting approval", value: fmtNum(pr.purchase_orders_awaiting_approval), severity: "low", href: withQuery(to("procurement", "orders"), "status=PENDING_APPROVAL") },
      { key: "ret", label: "Customer returns awaiting action", value: fmtNum(d.returns_open), severity: "low", href: to("dispatch", "returns") },
    ];
    return list.filter((e) => e.value !== undefined && e.value !== "0");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s, period, can]);

  /* ---- order to dispatch: one connected workflow. Every stage is a count of items waiting there, so the strip reads across; nothing here repeats an indicator above ---- */
  const stages: PipelineStage[] = useMemo(() => {
    if (!s) return [];
    const pr = s.procurement, q = s.quality, d = s.dispatch, inv = s.inventory;
    const queued = sumStatus(s.production_by_status, (x) => ["PLANNED", "RELEASED"].includes(x));
    const ready = sumStatus(s.orders_by_status, (x) => ["PACKED", "PARTIALLY_DISPATCHED"].includes(x));
    const transit = sumStatus(s.shipments_by_status, (x) => !SHIPMENT_FINAL.includes(x));
    const approvalsWaiting = pr.purchase_requests_awaiting_approval + pr.purchase_orders_awaiting_approval;
    return [
      { label: "Procurement", value: fmtNum(approvalsWaiting), note: "requests and orders awaiting approval", href: to("procurement", "orders") },
      { label: "Goods receipt", alert: pr.late_deliveries > 0 ? "danger" : undefined, value: fmtNum(pr.open_purchase_orders), note: "orders awaiting delivery", flag: pr.late_deliveries ? `${fmtNum(pr.late_deliveries)} late` : undefined, href: to("procurement", "receipts") },
      { label: "Materials", alert: inv.below_reorder_level > 0 ? "warn" : undefined, value: fmtNum(inv.below_reorder_level), note: "material shortages", flag: inv.below_reorder_level ? "below reorder level" : undefined, href: to("inventory", "reorder") },
      { label: "Production", alert: p0(s) > 0 ? "danger" : undefined, value: fmtNum(queued), note: "orders queued to start", flag: p0(s) ? `${fmtNum(p0(s))} past planned date` : undefined, href: to("production", "orders") },
      { label: "Quality", alert: q.open_deviations > 0 ? "warn" : undefined, value: fmtNum(q.open_inspections), note: "inspections open", flag: q.open_deviations ? `${fmtNum(q.open_deviations)} deviations open` : undefined, href: to("quality", "inspections") },
      { label: "Dispatch", alert: d.delayed_shipments > 0 ? "danger" : undefined, value: fmtNum(ready), note: `ready to ship · ${fmtNum(transit)} in transit`, flag: d.delayed_shipments ? `${fmtNum(d.delayed_shipments)} over 5 days` : undefined, href: to("dispatch", "orders") },
    ] as PipelineStage[];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s, can]);

  /* ---- production chart ---- */
  const buckets: Bucket[] = (period?.production.buckets ?? []).map((b) => ({ start: b.week_start, planned: b.planned === null ? null : num(b.planned), actual: b.actual === null ? null : num(b.actual), orders: b.orders, from: b.from, to: b.to, days: b.days, partial: b.partial }));
  const totals = buckets.reduce((a, b) => ({ p: a.p + (b.planned ?? 0), a: a.a + (b.actual ?? 0) }), { p: 0, a: 0 });
  const enough = (period?.production.weeks_with_data ?? 0) >= 1;

  const tx: TxRow[] = (period?.recent ?? []).map((r) => ({ key: `${r.kind}-${r.id}`, reference: r.reference, activity: KIND_LABEL[r.kind] ?? titleCase(r.kind), related: r.related ?? "—", date: fmtDate(r.created_at), href: open(r.kind, r.id),
    status: <Badge tone={toneFor(r.status)}>{titleCase(r.status)}</Badge> }));

  const mine = approvals?.length ?? 0;
  const oldest = [...(approvals ?? [])].sort((a, b) => a.since.localeCompare(b.since));   // the ones that have waited longest come first
  const partialWeeks = (period?.production.buckets ?? []).filter((b) => b.partial);

  return (
    <div className="space-y-4">
      <CommandHero
        status={s ? <>{period ? <>{fmtNum(totals.a)} of {fmtNum(totals.p)} planned units made this period{totals.p > 0 ? ` (${fmtPct((totals.a / totals.p) * 100)})` : ""}</> : "Loading the period"}{s.production.delayed_work_orders ? ` · ${fmtNum(s.production.delayed_work_orders)} production orders past planned date` : " · production on schedule"}</> : rangeError ?? "Loading the plant position…"}
        actions={[
          { label: "Create production order", href: to("production", "orders"), icon: QUICK.Factory, primary: true },
          { label: "Raise purchase request", href: to("procurement", "requests"), icon: QUICK.ShoppingCart },
          { label: "Dispatch orders", href: to("dispatch", "orders"), icon: QUICK.Truck },
          { label: "Log QC inspection", href: to("quality", "inspections"), icon: QUICK.ClipboardCheck },
        ]}
        controls={canSummary ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {key === "custom" && (
              <div className="flex items-center gap-1.5">
                <label className="sr-only" htmlFor="p-from">From</label><input id="p-from" type="date" className="input !w-[138px] !h-[30px]" value={custom.start} max={custom.end} onChange={(e) => setCustom((c) => ({ ...c, start: e.target.value }))} />
                <span className="t-support" aria-hidden>to</span>
                <label className="sr-only" htmlFor="p-to">To</label><input id="p-to" type="date" className="input !w-[138px] !h-[30px]" value={custom.end} min={custom.start} max={today} onChange={(e) => setCustom((c) => ({ ...c, end: e.target.value }))} />
              </div>
            )}
            <Segmented<PeriodKey> label="Reporting period" value={key} onChange={setKey} options={[{ value: "30d", label: "30 days" }, { value: "90d", label: "90 days" }, { value: "fy", label: "This FY" }, { value: "custom", label: "Custom" }]} />
            {mine > 0 && <Link href="/approvals" className="btn btn-sm btn-outline">Approvals <span className="inline-grid place-items-center h-5 min-w-5 px-1 rounded-full bg-ink text-[var(--fg-on-ink)] text-[0.75rem] font-semibold">{mine}</span></Link>}
          </div>
        ) : undefined}
      />
      <p className="sr-only">{fmtDay(range.start)} – {fmtDate(range.end)}, {plural(daysBetween(range.start, range.end) + 1, "day", "days")}{s ? `, data as of ${fmtDate(s.as_of)}` : ""}</p>

      {error && <div role="alert" className="panel px-4 py-3 text-danger text-[0.8125rem]">{error} Reload to try again.</div>}
      {!canSummary && <p className="panel px-4 py-3 t-label">Company-wide figures need the management summary permission, which {me?.roles[0] ? `the ${me.roles[0]} role` : "your role"} does not include.</p>}

      {canSummary && <MetricStrip label="Key indicators" items={metrics} />}

      {canSummary && s && <BusinessHealth s={s as unknown as Parameters<typeof BusinessHealth>[0]["s"]} crmEnabled={can("crm.lead:read")}
        hrefs={{ cash: to("accounts", "bank"), ageing: to("accounts", "ageing-r"), pnl: to("accounts", "pnl"), pipeline: "/crm/board", inventory: to("inventory", "valuation"), yield: to("production", "wastage") }} />}

      {canSummary && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] items-stretch">
          <div className="min-w-0 flex flex-col gap-4">
            {can("production.order:read") && <ProductionLines href={to("production", "orders")} />}
            <AlertCards lowStock={s?.low_stock[0] ? { name: formatName(s.low_stock[0].name), item_code: s.low_stock[0].item_code, available: s.low_stock[0].available, reorder_level: s.low_stock[0].reorder_level } : undefined} requestHref={withQuery(to("procurement", "requests"), "status=DRAFT")} />
          </div>
          <div className="min-w-0 space-y-4">
            <MarketplacePipeline enabled={can("ecommerce.order:read")} href={to("ecommerce", "orders")} />
            <MetaPanel enabled={can("branding.campaign:read")} href={to("branding", "campaigns")} />
            <CleanroomPanel />
          </div>
        </div>
      )}


      {canSummary && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,13fr)_minmax(0,7fr)] items-stretch">
          <Panel title="Production output" bodyClass="flex flex-col" meta="Completed orders by week; planned is the plan for those same orders." action={enough ? <Legend items={[{ label: "Planned", color: "var(--line-strong)" }, { label: "Actual", color: "var(--chart-actual)" }]} /> : undefined}>
            {period === undefined ? <div className="px-5 pb-5"><Skeleton className="h-[228px]" /></div>
              : !period ? <EmptyState title="Production output is unavailable" hint="The period figures could not be loaded." />
              : !enough ? <EmptyState title="No completed production yet" hint="Output is charted from completed production orders. None were completed in this period." action={to("production", "orders") ? <LinkMore href={to("production", "orders")!}>Production orders</LinkMore> : undefined} />
              : (
                <>
                  <p className="px-5 -mt-1 mb-2 t-label"><span className="t-value text-fg">{fmtNum(totals.a)}</span> units made of <span className="t-value text-fg">{fmtNum(totals.p)}</span> planned{totals.p > 0 && <> <span aria-hidden>·</span> {fmtPct((totals.a / totals.p) * 100)} of plan</>}</p>
                  <div className="px-2 flex flex-col flex-1 min-h-0"><GroupedBars buckets={buckets} labels={["Planned", "Actual"]} unit="units" minHeight={228} /></div>
                  <p className="px-5 pt-2 pb-4 t-support">
                    {partialWeeks.length > 0 && <>Partial weeks: {partialWeeks.map((b) => `week of ${fmtDay(b.week_start)} (${fmtDay(b.from)} to ${fmtDay(b.to)}${b.orders ? "" : ", no orders completed"})`).join("; ")}. </>}
                    A week with no completed orders has no bar.
                  </p>
                </>
              )}
          </Panel>
          <Panel title="Needs attention" meta={loading ? undefined : exceptions.length ? `${plural(exceptions.length, "item", "items")}, most severe first · as of today` : undefined} bodyClass="overflow-hidden">
            {loading ? <div className="px-5 pb-5 space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-4" />)}</div> : <ExceptionList items={exceptions} empty="Nothing needs attention right now." />}
          </Panel>
        </div>
      )}

      {canSummary && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Panel title="Revenue by channel" meta={`Invoiced, ex-tax, ${plural(daysBetween(range.start, range.end) + 1, "day", "days")}`}>
            {trends === undefined ? <div className="px-5 pb-5 space-y-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-4" />)}</div>
              : <InView><ShareBars data={trends?.revenue_by_channel ?? {}} empty="No invoices in this period." /></InView>}
          </Panel>
          <Panel title="Top products" meta="By invoiced value in the period">
            {trends === undefined ? <div className="px-5 pb-5 space-y-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-4" />)}</div>
              : <InView><ShareBars data={trends?.top_products ?? {}} names={{}} empty="No product sales in this period." /></InView>}
          </Panel>
          <Panel title="Receivables ageing" meta="What customers owe, by days past the due date" action={to("accounts", "ageing-r") ? <LinkMore href={to("accounts", "ageing-r")!}>Ageing</LinkMore> : undefined}>
            {trends === undefined ? <div className="px-5 pb-5"><Skeleton className="h-28" /></div>
              : <InView><ReceivableAgeing data={trends?.receivable_ageing ?? {}} /></InView>}
          </Panel>
        </div>
      )}

      {canSummary && (
        <Block bare title="Order to dispatch" meta="where work is waiting today">
          {loading ? <Skeleton className="h-[112px]" /> : <Pipeline stages={stages} />}
        </Block>
      )}

      <div className="grid xl:grid-cols-[minmax(0,13fr)_minmax(0,7fr)] gap-4">
        {canSummary && (
          <Block title="Recent transactions" action={to("dispatch", "invoices") ? <LinkMore href={to("dispatch", "invoices")!}>Sales invoices</LinkMore> : undefined} className="min-w-0">
            {period === undefined ? <div className="pt-3 space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-4" />)}</div>
              : <TransactionTable rows={tx} empty="No documents have been created yet." />}
          </Block>
        )}
        <div className={`min-w-0 self-start space-y-4 ${canSummary ? "" : "xl:col-span-2"}`}>
        {canSummary && (
          <Panel title="Top customers" meta={`By invoiced revenue, ${plural(daysBetween(range.start, range.end) + 1, "day", "days")}`}>
            {trends === undefined ? <div className="px-5 pb-5 space-y-3">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-4" />)}</div>
              : <InView><RankedBars data={trends?.top_customers ?? {}} empty="No invoices in this period." /></InView>}
          </Panel>
        )}
        <Block title="Waiting for your approval" className="min-w-0" action={<LinkMore href="/approvals">Approvals</LinkMore>}>
          <div className="pt-3">
            <p className="t-label mb-1">{approvals === null ? "Checking approvals…" : mine ? <>{fmtNum(mine)} waiting, oldest first. The oldest has waited since {fmtDate(oldest[0].since)}.</> : "Nothing is waiting for your decision."}</p>
            {oldest.slice(0, 5).map((a) => <Link key={`${a.type}-${a.id}`} href="/approvals" className="flex items-baseline justify-between gap-3 py-2 border-t border-line row-link -mx-2 px-2 rounded-md first:mt-2"><span className="truncate text-[0.8125rem]"><span className="tnum font-medium">{a.number}</span> <span className="text-muted">{titleCase(a.type)}</span></span><span className="t-support shrink-0 tnum">{fmtAgo(a.since)}</span></Link>)}
            {mine > 5 && <p className="t-support pt-2 border-t border-line -mx-2 px-2">and {fmtNum(mine - 5)} more waiting</p>}
          </div>
        </Block>
        </div>
      </div>
    </div>
  );
}
