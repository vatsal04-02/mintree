"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { sharedGet } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { greeting, greetName } from "@/lib/persona";
import { addDays, businessToday, daysBetween, formatName, fmtAgo, fmtDate, fmtDay, fmtDelta, fmtNum, fmtPct, fmtRupees, fyStart, titleCase } from "@/lib/format";
import { toneFor } from "@/lib/modules";
import { num, plural, screenHref, SHIPMENT_FINAL, sumStatus, usePeriod, useSummary } from "@/lib/summary";
import { Badge } from "@/components/ui";
import { EmptyState, GroupedBars, Legend, Segmented, Skeleton, TransactionTable, type Bucket, type Exception, type PipelineStage, type TxRow } from "@/components/blocks";
import { Strip, type StripCard } from "@/components/kpi";
import { AreaChart, InView, type Point } from "@/components/fx";
import { CardHead, DealCard, Donut, StageMix } from "@/components/rapport";
import { ArrowUpRight, Factory, Package, Receipt, TrendingUp } from "lucide-react";

type Approval = { type: string; id: number; number: string; since: string };
type PeriodKey = "30d" | "90d" | "fy" | "custom";
type Trends = { granularity: "day" | "week"; revenue_trend: Point[]; revenue_prev_trend: Point[] };
/* record-card colour by severity: problems alternate orange and black, items to review are green, items to watch are yellow */
/* attention cards stay light; severity is a small pill, and only urgent counts take the orange */
const SEV: Record<string, { label: string; tone: "danger" | "warning" | "neutral" }> = { high: { label: "Act now", tone: "danger" }, medium: { label: "Review", tone: "warning" }, low: { label: "Monitor", tone: "neutral" } };

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

  /* ---- the four indicators: one strip, plain figures with one line of context ---- */
  const kpis: StripCard[] | null = useMemo(() => {
    if (!s) return null;
    const held = s.quality.batches_on_hold + s.quality.batches_quarantined;
    const attention = s.inventory.below_reorder_level + s.inventory.expired_with_stock + held;
    const delta = period ? fmtDelta(num(period.revenue.net_ex_tax), num(period.revenue.previous_net_ex_tax)) : null;
    const days = period ? daysBetween(period.previous.start, period.previous.end) + 1 : 0;
    return [
      { label: "Receivables outstanding", icon: Receipt, value: fmtRupees(s.sales.receivable_outstanding), foot: plural(s.finance_extended.open_sales_invoices, "open invoice", "open invoices") },
      { label: "Net revenue, ex-tax", icon: TrendingUp, value: period === undefined ? "…" : period ? fmtRupees(period.revenue.net_ex_tax) : "—",
        chip: period && delta ? `${delta} vs previous ${days} days` : undefined, tone: delta && delta.startsWith("−") ? "danger" : undefined,
        foot: period ? plural(period.revenue.invoices, "invoice", "invoices") : "Loading the period" },
      { label: "Active production orders", icon: Factory, value: fmtNum(s.production.active_orders), chip: s.production.delayed_work_orders ? `${fmtNum(s.production.delayed_work_orders)} past planned date` : "On schedule", tone: s.production.delayed_work_orders ? "danger" : undefined },
      { label: "Inventory alerts", icon: Package, value: fmtNum(attention),
        chip: attention ? [s.inventory.below_reorder_level && `${fmtNum(s.inventory.below_reorder_level)} below reorder`, held && `${fmtNum(held)} held`, s.inventory.expired_with_stock && `${fmtNum(s.inventory.expired_with_stock)} expired`].filter(Boolean).join(" · ") : "Nothing flagged",
        tone: s.inventory.expired_with_stock ? "danger" : attention ? "warning" : undefined },
    ];
  }, [s, period]);

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

  const revenueTotal = (trends?.revenue_trend ?? []).reduce((a, p) => a + p.value, 0);
  const attainment = totals.p > 0 ? (totals.a / totals.p) * 100 : 0;
  const card = "panel rise flex flex-col min-w-0";

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4 pt-2">
        <div className="min-w-0">
          {/* greeting by the viewer's clock; it waits for the signed-in user, which only exists in the browser, so server and client agree */}
          {me && <p className="t-label">{greeting()}, {greetName(me)}</p>}
          <h1 className="t-page whitespace-nowrap mt-0.5">Factory overview</h1>
          {/* one plain statement of the period; the as-of date is only added when it differs from the period's last day */}
          <p className="t-support mt-1.5">{s ? <>Showing {fmtDay(range.start)} – {fmtDate(range.end)}{String(s.as_of).slice(0, 10) !== String(range.end).slice(0, 10) && <span className="ml-3">Figures as of {fmtDate(s.as_of)}</span>}</> : rangeError ?? "Loading the plant position…"}</p>
        </div>
        {canSummary && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {key === "custom" && (
              <div className="flex items-center gap-1.5">
                <label className="sr-only" htmlFor="p-from">From</label><input id="p-from" type="date" className="input !w-[138px] !h-[34px]" value={custom.start} max={custom.end} onChange={(e) => setCustom((c) => ({ ...c, start: e.target.value }))} />
                <span className="t-support" aria-hidden>to</span>
                <label className="sr-only" htmlFor="p-to">To</label><input id="p-to" type="date" className="input !w-[138px] !h-[34px]" value={custom.end} min={custom.start} max={today} onChange={(e) => setCustom((c) => ({ ...c, end: e.target.value }))} />
              </div>
            )}
            <Segmented<PeriodKey> label="Reporting period" value={key} onChange={setKey} options={[{ value: "30d", label: "30 days" }, { value: "90d", label: "90 days" }, { value: "fy", label: "This FY" }, { value: "custom", label: "Custom" }]} />
            <Link href="/approvals" className="btn btn-primary gap-2">Approvals{mine > 0 && <span className="pill pill-warning">{mine}</span>}</Link>
          </div>
        )}
      </header>
      <p className="sr-only">{fmtDay(range.start)} – {fmtDate(range.end)}, {plural(daysBetween(range.start, range.end) + 1, "day", "days")}{s ? `, data as of ${fmtDate(s.as_of)}` : ""}</p>

      {error && <div role="alert" className="panel px-5 py-3 text-danger text-[0.8125rem]">{error} Reload to try again.</div>}
      {!canSummary && <p className="panel px-5 py-3 t-label">Company-wide figures need the management summary permission, which {me?.roles[0] ? `the ${me.roles[0]} role` : "your role"} does not include.</p>}

      {canSummary && <Strip cards={kpis} className="py-1" label="Key indicators" />}

      {canSummary && (
        <div className="grid gap-4 xl:grid-cols-12 items-stretch">
          <section className={`${card} xl:col-span-8`} aria-label="Revenue">
            <CardHead title="Revenue" href={to("accounts", "pnl")} more={to("dispatch", "invoices")} />
            <div className="px-[18px] -mt-1 flex flex-wrap items-baseline gap-x-2">
              <span className="t-stage tabular-nums">{trends ? fmtRupees(revenueTotal) : "…"}</span>
              <span className="t-support">invoiced, ex-tax · {plural(daysBetween(range.start, range.end) + 1, "day", "days")}</span>
            </div>
            {trends === undefined ? <div className="px-5 pb-5 pt-3"><Skeleton className="h-[220px]" /></div>
              : !trends ? <p className="t-support px-5 pb-5">Revenue could not be loaded.</p>
              : <div className="pt-1"><AreaChart points={trends.revenue_trend} compare={trends.revenue_prev_trend} weekly={trends.granularity === "week"} height={150} name="Invoiced revenue" color="#c6431d" compareColor="var(--fg)" summary={false} /></div>}
          </section>
          <section className="panel rise xl:col-span-4 flex flex-col p-4 min-w-0" aria-label="Plan attainment">
            <div className="flex items-start justify-between gap-3">
              <div><h2 className="text-[0.875rem] font-medium">Plan attainment</h2><p className="t-support mt-0.5">Completed orders in the period</p></div>
              {to("production", "orders") && <Link href={to("production", "orders")!} className="ctl shrink-0" aria-label="Open production orders"><ArrowUpRight size={16} strokeWidth={1.5} /></Link>}
            </div>
            <div className="flex-1 grid place-items-center py-3">{period === undefined ? <Skeleton className="w-[128px] h-[128px] !rounded-full" /> : <Donut pct={attainment} label="of plan" size={128} tone="light" />}</div>
            {enough ? (
              <dl className="grid grid-cols-3 text-center border-t border-line pt-3">
                <div><dt className="t-support">Made</dt><dd className="text-[0.9375rem] font-medium tabular-nums mt-0.5">{fmtNum(totals.a)}</dd></div>
                <div className="border-x border-line"><dt className="t-support">Planned</dt><dd className="text-[0.9375rem] font-medium tabular-nums mt-0.5">{fmtNum(totals.p)}</dd></div>
                <div><dt className="t-support">Short</dt><dd className="text-[0.9375rem] font-medium tabular-nums mt-0.5" style={totals.p - totals.a > 0 ? { color: "var(--acc-orange)" } : undefined}>{fmtNum(Math.max(0, totals.p - totals.a))}</dd></div>
              </dl>
            ) : <p className="t-support text-center">No completed production in this period</p>}
          </section>
        </div>
      )}

      {canSummary && (
        <section className={card} aria-label="Needs attention">
          <CardHead title="Needs attention" meta={loading ? undefined : exceptions.length ? `${plural(exceptions.length, "item", "items")}, most severe first` : undefined} href="/approvals" />
          <div className="px-[18px] pb-[18px]">
            {loading ? <div className="grid gap-[11px] sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-[165px] !rounded-[22px]" />)}</div>
              : exceptions.length === 0 ? <p className="t-support py-4">Nothing needs attention right now.</p>
              : <div className="grid gap-[11px] sm:grid-cols-2 lg:grid-cols-3">
                  {exceptions.slice(0, 6).map((e, i) => <DealCard key={e.key} index={i} variant="white" meta={(SEV[e.severity] ?? SEV.low).label} metaTone={(SEV[e.severity] ?? SEV.low).tone} valueTone={e.severity === "high" ? "var(--acc-orange)" : undefined} title={e.label} subtitle={e.detail} value={e.value ?? ""} href={e.href} />)}
                </div>}
          </div>
        </section>
      )}

      {canSummary && (
        <div className="grid gap-4 xl:grid-cols-12 items-stretch">
          <section className={`${card} xl:col-span-8`} aria-label="Production output">
            <CardHead title="Production output" meta={enough ? `${fmtNum(totals.a)} units made of ${fmtNum(totals.p)} planned${totals.p > 0 ? ` · ${fmtPct(attainment)} of plan` : ""}` : "Completed orders by week"} href={to("production", "orders")}>
              {enough && <Legend items={[{ label: "Planned", color: "var(--line-strong)" }, { label: "Actual", color: "var(--chart-actual)" }]} />}
            </CardHead>
            {period === undefined ? <div className="px-5 pb-5"><Skeleton className="h-[220px]" /></div>
              : !period ? <EmptyState title="Production output is unavailable" hint="The period figures could not be loaded." />
              : !enough ? <EmptyState title="No completed production yet" hint="Output is charted from completed production orders. None were completed in this period." />
              : (
                <>
                  <div className="px-2 flex flex-col flex-1 min-h-0"><GroupedBars buckets={buckets} labels={["Planned", "Actual"]} unit="units" minHeight={210} /></div>
                  {partialWeeks.length > 0 && <p className="px-5 pt-2 pb-4 t-support">Partial weeks: {partialWeeks.map((b) => `${fmtDay(b.from)} to ${fmtDay(b.to)}`).join("; ")}.</p>}
                </>
              )}
          </section>
          <section className={`${card} xl:col-span-4`} aria-label="Order to dispatch">
            <CardHead title="Order to dispatch" meta="Where work is waiting today" href={to("dispatch", "orders")} />
            <div className="px-[18px] pb-1 flex items-baseline gap-2"><span className="t-stage tabular-nums">{fmtNum(stages.reduce((a, x) => a + num(x.value), 0))}</span><span className="t-support">items across {stages.length} stages</span></div>
            {loading ? <div className="px-5 pb-5"><Skeleton className="h-40" /></div> : <InView><StageMix items={stages.map((x) => ({ label: x.label, value: num(x.value), href: x.href }))} /></InView>}
          </section>
        </div>
      )}

      <div className="grid xl:grid-cols-12 gap-4 items-start">
        {canSummary && (
          <section className={`${card} xl:col-span-8`} aria-label="Recent transactions">
            <CardHead title="Recent transactions" href={to("dispatch", "invoices")} />
            <div className="px-[18px] pb-3">
              {period === undefined ? <div className="pt-3 space-y-3">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-4" />)}</div>
                : <TransactionTable rows={tx} empty="No documents have been created yet." />}
            </div>
          </section>
        )}
        <section className={`${card} ${canSummary ? "xl:col-span-4" : "xl:col-span-12"}`} aria-label="Pending approvals">
          <CardHead title="Pending approvals" meta={approvals === null ? "Checking approvals…" : mine ? `${fmtNum(mine)} waiting for your decision, oldest first` : "Nothing is waiting for your decision"} href="/approvals" />
          <div className="px-[18px] pb-[18px]">
            {oldest.slice(0, 6).map((a) => <Link key={`${a.type}-${a.id}`} href="/approvals" className="flex items-baseline justify-between gap-3 py-2.5 border-t border-line first:border-t-0 text-[0.8125rem] hover:opacity-80"><span className="truncate"><span className="tabular-nums">{a.number}</span> <span className="text-muted">{titleCase(a.type)}</span></span><span className="t-support shrink-0 tabular-nums">{fmtAgo(a.since)}</span></Link>)}
            {mine > 6 && <p className="t-support pt-2.5 border-t border-line">and {fmtNum(mine - 6)} more waiting</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
