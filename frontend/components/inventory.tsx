"use client";
/** Inventory landing screen: KPI chips, the weekly batch chart beside stock value by class, then small working tables (reorder watch, warehouses,
 *  latest movements, batches expiring soonest) and three compact breakdowns. Everything comes from /inventory/hub and /inventory/dashboard. */
import Link from "next/link";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Boxes, ClipboardCheck, IndianRupee, ShieldAlert } from "lucide-react";
import { fmtDate, fmtDateTime, fmtDay, fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { Strip } from "./kpi";
import { StageMix } from "./rapport";
import { TrendCard } from "./ModuleDashboard";
import { InView } from "./fx";
import { useShared, type Row } from "./command";

type To = (m: string, s: string) => string | undefined;
const n = (v: unknown) => Number(v ?? 0) || 0;
const short = (v: number) => (v >= 1e7 ? `₹${fmtNum(v / 1e7, 2)}Cr` : v >= 1e5 ? `₹${fmtNum(v / 1e5, 2)}L` : fmtRupees(v));
const qty = (v: unknown, uom: unknown) => `${fmtNum(n(v), Number.isInteger(n(v)) ? 0 : 1)} ${String(uom ?? "").toLowerCase()}`;
/** Names typed in capitals ("COCOA BUTTER") are shown in title case; anything already mixed-case is left as written. */
const nm = (v: unknown) => { const t = String(v ?? ""); return /[a-z]/.test(t) ? t : titleCase(t); };
const KIND: Record<string, string> = { RM: "Raw materials", PM: "Packaging", FG: "Finished goods", BULK: "Bulk", SFG: "Semi-finished" };
const KIND_COLOR: Record<string, string> = { FG: "var(--acc-green)", RM: "var(--ink-r)", PM: "var(--acc-yellow)", BULK: "var(--faint)", SFG: "var(--acc-orange)" };
const QC_COLOR: Record<string, string> = { RELEASED: "var(--acc-green)", QUARANTINE: "var(--acc-yellow)", HOLD: "var(--ink-r)", REJECTED: "var(--acc-orange)" };
const MOVE_COLOR = (t: string) => (/(RECEIPT|OUTPUT|RETURN|TRANSFER_IN|RELEASE)/.test(t) ? "var(--acc-green)" : /(ISSUE|DISPATCH|TRANSFER_OUT)/.test(t) ? "var(--ink-r)" : "var(--acc-yellow)");

function Panel({ title, meta, href, linkLabel, children, className = "" }: { title: string; meta?: string; href?: string; linkLabel?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`panel rise flex flex-col min-w-0 overflow-hidden ${className}`} aria-label={title}>
      <header className="flex items-start justify-between gap-3 px-[18px] pt-[18px] pb-2">
        <div className="min-w-0"><h2 className="text-[0.875rem] font-medium leading-5">{title}</h2>{meta && <p className="t-support mt-0.5">{meta}</p>}</div>
        {href && <Link href={href} className="text-[0.75rem] font-medium text-muted hover:text-fg inline-flex items-center gap-1 shrink-0">{linkLabel ?? "View all"}<ArrowUpRight size={13} strokeWidth={1.5} /></Link>}
      </header>
      {children}
    </section>
  );
}
const Empty = ({ text }: { text: string }) => <p className="t-support px-[18px] pb-[18px] pt-1">{text}</p>;

export function InventoryScreen({ to }: { to: To }) {
  const hub = useShared<Row>("/inventory/hub");
  const dash = useShared<Row>("/inventory/dashboard");
  const stock = to("inventory", "stock"), ledger = to("inventory", "ledger"), reorderHref = to("inventory", "reorder"), expiringHref = to("inventory", "expiring");
  const batches = to("inventory", "batches"), valuation = to("inventory", "valuation"), stocktake = to("inventory", "stocktake"), pr = to("procurement", "requests");

  const vbk = (hub?.value_by_kind ?? {}) as Record<string, number>;
  const kinds = Object.entries(vbk).map(([k, v]) => [k, n(v)] as const).filter(([, v]) => v > 0);
  const totalValue = n(hub?.stock_value);
  const reorder = (hub?.reorder ?? []) as Row[];
  const below = reorder.filter((r) => r.state === "BELOW").length;
  const warehouses = ((hub?.warehouses ?? []) as Row[]).filter((w) => n(w.lines) > 0 || n(w.qty) > 0);
  const maxLines = Math.max(1, ...warehouses.map((w) => n(w.lines)));
  const allMoves = Object.entries((hub?.movements_by_type_30d ?? {}) as Record<string, number>).map(([k, v]) => [k, n(v)] as [string, number]);
  const moves = Object.fromEntries(allMoves.length > 6 ? [...allMoves.slice(0, 5), ["OTHER", allMoves.slice(5).reduce((a, [, v]) => a + v, 0)]] : allMoves) as Record<string, number>;
  const maxMove = Math.max(1, ...Object.values(moves).map(n));
  const qc = (dash?.batches_by_qc_status ?? {}) as Record<string, number>;
  const trend = dash?.batches_made_weekly_trend as { label: string; value: number }[] | undefined;

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 mb-5">
        <div className="min-w-0">
          <h1 className="t-page">Inventory</h1>
          <p className="t-label mt-1">Stock by batch and warehouse, the movement ledger, stocktakes and stock reports.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {ledger && <Link href={ledger} className="btn">Stock ledger</Link>}
          {stocktake && <Link href={stocktake} className="btn btn-primary gap-1.5"><ClipboardCheck size={15} strokeWidth={1.5} />Start stocktake</Link>}
        </div>
      </div>

      {hub === undefined || dash === undefined ? <div className="skeleton h-96 rounded-[28px]" /> : !hub ? <p className="panel p-5 text-danger">The inventory summary could not be loaded.</p> : (
        <div className="space-y-4">
          <Strip label="Key figures" cards={[
            { label: "Stock value", value: short(totalValue), icon: IndianRupee, foot: "Moving-average cost" },
            { label: "Active items", value: fmtNum(n(dash?.active_items)), icon: Boxes, foot: `${fmtNum(n(dash?.stocked_lines))} stocked lines` },
            { label: "Below reorder level", value: fmtNum(below), icon: AlertTriangle, chip: below ? "Needs a purchase request" : "All covered", tone: below ? "danger" : "success" },
            { label: "Batches in quarantine", value: fmtNum(n(dash?.batches_in_quarantine)), icon: ShieldAlert, chip: n(dash?.batches_in_quarantine) ? "Awaiting QC" : undefined, tone: "warning" },
          ]} />

          <div className="grid gap-4 xl:grid-cols-12 items-stretch">
            <div className="xl:col-span-8 flex min-w-0">
              {trend && trend.length > 0 ? <InView className="flex w-full"><TrendCard name="batches_made_weekly_trend" points={trend} hero chartH={200} className="w-full" /></InView> : <Panel title="Batches made" className="w-full"><Empty text="No batches recorded yet." /></Panel>}
            </div>
            <Panel title="Stock value by class" meta="Share of moving-average value" href={valuation} linkLabel="Valuation" className="xl:col-span-4">
              <div className="px-[18px] pb-[18px] flex-1 flex flex-col">
                <p className="t-stage tabular-nums">{short(totalValue)}</p>
                <div className="flex gap-1.5 h-10 mt-3" aria-hidden>
                  {kinds.map(([k, v]) => <span key={k} className="rounded-[12px] min-w-[14px] bar-grow-x" style={{ flex: v, background: KIND_COLOR[k] ?? "var(--faint)" }} />)}
                </div>
                <ul className="mt-3 divide-y divide-line">
                  {kinds.map(([k, v]) => (
                    <li key={k} className="flex items-center justify-between gap-3 py-2 text-[0.8125rem]">
                      <span className="inline-flex items-center gap-2 min-w-0"><span className="w-2 h-2 rounded-full shrink-0" style={{ background: KIND_COLOR[k] ?? "var(--faint)" }} aria-hidden /><span className="truncate">{KIND[k] ?? k}</span></span>
                      <span className="tabular-nums shrink-0"><span className="text-muted mr-3">{totalValue ? Math.round((v / totalValue) * 100) : 0}%</span>{short(v)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </Panel>
          </div>

          <div className="grid gap-4 xl:grid-cols-12 items-start">
            <Panel title="Reorder watch" meta="Items at or close to their reorder level" href={reorderHref} linkLabel="Reorder report" className="xl:col-span-7">
              {reorder.length === 0 ? <Empty text="Every item is comfortably above its reorder level." /> : (
                <div className="overflow-x-auto"><table className="dt dt-compact">
                  <thead><tr><th>Item</th><th>Cover</th><th className="num">On order</th><th>Status</th><th className="w-px" /></tr></thead>
                  <tbody>{reorder.map((r) => {
                    const pct = Math.min(100, (n(r.free) / Math.max(n(r.reorder_level), 1e-9)) * 100);
                    const bad = r.state === "BELOW";
                    return (
                      <tr key={r.id}>
                        <td className="max-w-[14rem]"><span className="block font-medium truncate" title={r.name}>{nm(r.name)}</span><span className="block t-support"><span className="font-mono">{r.item_code}</span> · {KIND[r.kind] ?? r.kind}</span></td>
                        <td className="min-w-[9rem]">
                          <span className="block text-[0.75rem] tabular-nums"><span className={bad ? "text-danger font-medium" : ""}>{qty(r.free, r.uom)}</span><span className="text-muted"> of {qty(r.reorder_level, r.uom)}</span></span>
                          <span className="block mt-1 h-1.5 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden><span className="block h-1.5 rounded-full" style={{ width: `${Math.max(pct, 3)}%`, background: bad ? "var(--acc-orange)" : "var(--acc-yellow)" }} /></span>
                        </td>
                        <td className="num font-mono">{n(r.on_order) ? qty(r.on_order, r.uom) : "—"}</td>
                        <td><span className={`pill ${bad ? "pill-danger" : "pill-warning"}`}>{bad ? "Below" : "Soon"}</span></td>
                        <td className="text-right">{pr && <Link href={pr} className="btn btn-sm">Request</Link>}</td>
                      </tr>
                    );
                  })}</tbody>
                </table></div>
              )}
            </Panel>

            <Panel title="Warehouses" meta="Stocked lines, quantity and blocked stock" href={stock} linkLabel="Stock balances" className="xl:col-span-5">
              {warehouses.length === 0 ? <Empty text="No stock is held yet." /> : (
                <div className="overflow-x-auto"><table className="dt dt-compact">
                  <thead><tr><th>Warehouse</th><th>Lines</th><th className="num">Items</th><th className="num">Blocked</th></tr></thead>
                  <tbody>{warehouses.map((w) => (
                    <tr key={w.id}>
                      <td className="max-w-[11rem]"><span className="block font-medium truncate" title={w.name}>{w.name}</span><span className="block t-support font-mono">{w.code}</span></td>
                      <td className="min-w-[7rem]">
                        <span className="block text-[0.75rem] tabular-nums">{fmtNum(n(w.lines))}</span>
                        <span className="block mt-1 h-1.5 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden><span className="block h-1.5 rounded-full" style={{ width: `${Math.max((n(w.lines) / maxLines) * 100, 3)}%`, background: "var(--ink-r)" }} /></span>
                      </td>
                      <td className="num font-mono">{fmtNum(n(w.items))}</td>
                      <td className={`num font-mono ${n(w.blocked) ? "text-warning" : "text-muted"}`}>{n(w.blocked) ? fmtNum(n(w.blocked), 0) : "—"}</td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
            </Panel>
          </div>

          <div className="grid gap-4 xl:grid-cols-12 items-start">
            <Panel title="Latest stock movements" meta={`${fmtNum(n(hub.movements_7d))} postings in the last 7 days`} href={ledger} linkLabel="Stock ledger" className="xl:col-span-7">
              {((hub.movements ?? []) as Row[]).length === 0 ? <Empty text="No stock has moved yet." /> : (
                <div className="overflow-x-auto"><table className="dt dt-compact">
                  <thead><tr><th>Movement</th><th>Item</th><th className="num">Qty</th><th>Where</th><th>Posted</th></tr></thead>
                  <tbody>{(hub.movements as Row[]).map((m) => {
                    const inward = n(m.qty_delta) >= 0;
                    return (
                      <tr key={m.id}>
                        <td><span className="inline-flex items-center gap-2 whitespace-nowrap"><span className="w-2 h-2 rounded-full shrink-0" style={{ background: MOVE_COLOR(String(m.movement_type)) }} aria-hidden />{titleCase(String(m.movement_type))}</span></td>
                        <td className="max-w-[12rem]"><span className="block truncate" title={m.name}>{nm(m.name)}</span><span className="block t-support font-mono">{m.batch_no ?? m.item_code}</span></td>
                        <td className="num font-mono whitespace-nowrap"><span className="inline-flex items-center gap-0.5" style={{ color: inward ? "var(--acc-green)" : "var(--acc-orange)" }}>{inward ? <ArrowDownRight size={12} aria-hidden /> : <ArrowUpRight size={12} aria-hidden />}{inward ? "+" : "−"}{qty(Math.abs(n(m.qty_delta)), m.uom)}</span></td>
                        <td className="font-mono text-[0.75rem]">{m.warehouse}</td>
                        <td className="t-support whitespace-nowrap" title={fmtDateTime(m.posted_at)}>{fmtDay(String(m.posted_at).slice(0, 10))}</td>
                      </tr>
                    );
                  })}</tbody>
                </table></div>
              )}
            </Panel>

            <Panel title="Expiring soonest" meta={`Batches holding stock · ${fmtNum(n(dash?.expiring_within_90_days))} within 90 days${n(dash?.expired_with_stock) ? `, ${fmtNum(n(dash?.expired_with_stock))} expired` : ""}`} href={expiringHref} linkLabel="Expiring stock" className="xl:col-span-5">
              {((hub.expiring ?? []) as Row[]).length === 0 ? <Empty text="No batch with stock has an expiry date." /> : (
                <div className="overflow-x-auto"><table className="dt dt-compact w-full table-fixed">
                  <colgroup><col /><col className="w-[6.5rem]" /><col className="w-[6.75rem]" /></colgroup>
                  <thead><tr><th>Batch</th><th className="num">Qty</th><th>Expires</th></tr></thead>
                  <tbody>{(hub.expiring as Row[]).map((b) => {
                    const days = n(b.days_left);
                    const tone = days < 0 ? "text-danger" : days <= 90 ? "text-warning" : "text-muted";
                    return (
                      <tr key={b.id}>
                        <td className="min-w-0"><span className="block font-mono text-[0.75rem] font-medium truncate">{batches ? <Link href={`${batches}?open=${b.id}`} className="hover:underline">{b.batch_no}</Link> : b.batch_no}</span><span className="block t-support truncate" title={b.name}>{nm(b.name)}</span></td>
                        <td className="num font-mono whitespace-nowrap">{qty(b.qty, b.uom)}</td>
                        <td className="whitespace-nowrap"><span className="block text-[0.75rem]">{fmtDate(b.expiry_date)}</span><span className={`block text-[0.6875rem] ${tone}`}>{days < 0 ? `${fmtNum(-days)} days ago` : `in ${fmtNum(days)} days`}</span></td>
                      </tr>
                    );
                  })}</tbody>
                </table></div>
              )}
            </Panel>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 items-start">
            <Panel title="Batches by QC status" meta={`${fmtNum(Object.values(qc).reduce((a, b) => a + n(b), 0))} batches`} href={batches} linkLabel="Batches">
              {Object.keys(qc).length === 0 ? <Empty text="No batches yet." /> : <InView><StageMix items={Object.entries(qc).map(([k, v]) => ({ label: titleCase(k), value: n(v), color: QC_COLOR[k] }))} /></InView>}
            </Panel>
            <Panel title="Movements by type" meta="Ledger postings, last 30 days" href={ledger} linkLabel="Ledger">
              {Object.keys(moves).length === 0 ? <Empty text="Nothing posted in the last 30 days." /> : (
                <ul className="px-[18px] pb-[18px] space-y-3">
                  {Object.entries(moves).map(([k, v], i) => (
                    <li key={k}>
                      <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]"><span className="truncate">{titleCase(k)}</span><span className="tabular-nums">{fmtNum(n(v))}</span></div>
                      <div className="mt-1.5 h-2 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden><div className="h-2 rounded-full bar-grow-x" style={{ width: `${Math.max((n(v) / maxMove) * 100, 3)}%`, background: MOVE_COLOR(k), animationDelay: `${i * 60}ms` }} /></div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <Panel title="Top items by value" meta="Where the stock value sits" href={valuation} linkLabel="Valuation">
              {((hub.top_value ?? []) as Row[]).length === 0 ? <Empty text="No valued stock yet." /> : (
                <table className="dt dt-compact w-full table-fixed">
                  <colgroup><col /><col className="w-[6rem]" /></colgroup>
                  <thead><tr><th>Item</th><th className="num">Value</th></tr></thead>
                  <tbody>{(hub.top_value as Row[]).map((t) => (
                    <tr key={t.id}>
                      <td className="min-w-0"><span className="block truncate" title={t.name}>{nm(t.name)}</span><span className="block t-support truncate">{KIND[t.kind] ?? t.kind} · {fmtNum(n(t.qty), 0)} units</span></td>
                      <td className="num font-mono whitespace-nowrap">{short(n(t.value))}</td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </Panel>
          </div>
        </div>
      )}
    </>
  );
}

