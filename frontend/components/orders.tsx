"use client";
/** The marketplace orders board: four figures above the list, and one card per imported order showing the channel, the buyer, each line matched to our item
 *  (or flagged as unmapped), the total, and either the sales order it became or why it is stuck. Figures come from /ecommerce/orders-board. */
import Link from "next/link";
import { fmtDate, fmtMoney, fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { toneFor, type Action, type Row } from "@/lib/modules";

const EDGE: Record<string, string> = { success: "var(--brand)", warning: "var(--amber)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--line-strong)" };
const PLATFORM: Record<string, { name: string; color: string }> = {
  AMAZON: { name: "Amazon", color: "#f59e0b" }, NYKAA: { name: "Nykaa", color: "#ec4899" }, FLIPKART: { name: "Flipkart", color: "#3b82f6" }, MYNTRA: { name: "Myntra", color: "#d946ef" }, FILE: { name: "File import", color: "#64748b" },
};
type Summary = { received_30d: number; value_30d: number; exceptions: number; exception_value: number; converted: number; total: number; channels: number; unmapped_skus: number };

export function OrdersSummary({ summary }: { summary: Summary | null | undefined }) {
  if (!summary) return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-[118px] !rounded-xl" />)}</div>;
  const cards = [
    { label: "Orders received, 30 days", value: fmtNum(summary.received_30d), chip: `${fmtNum(summary.channels)} channel${summary.channels === 1 ? "" : "s"}`, tone: "info", foot: "Imported from connected marketplaces", accent: "var(--info)" },
    { label: "Order value, 30 days", value: fmtRupees(summary.value_30d), chip: "Excl. exceptions", tone: "neutral", foot: "Orders that can be fulfilled", accent: "var(--brand-bright)" },
    { label: "In exception queue", value: fmtNum(summary.exceptions), chip: summary.exceptions ? `${fmtRupees(summary.exception_value)} held` : "Nothing held", tone: summary.exceptions ? "danger" : "success", foot: summary.exceptions ? `${fmtNum(summary.unmapped_skus)} unmapped SKU${summary.unmapped_skus === 1 ? "" : "s"} to fix` : "Every order mapped", accent: "var(--danger)" },
    { label: "Converted to sales orders", value: `${fmtNum(summary.converted)} of ${fmtNum(summary.total)}`, chip: summary.total ? `${fmtNum(Math.round((summary.converted / summary.total) * 100))}%` : "—", tone: "success", foot: "Orders ready for picking and dispatch", accent: "var(--brand-bright)" },
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">
      {cards.map((c) => (
        <section key={c.label} className="panel kpi-card p-4 pt-[18px] flex flex-col" style={{ "--kpi-accent": c.accent } as React.CSSProperties} aria-label={c.label}>
          <span className="label-caps">{c.label}</span>
          <div className="mt-1.5 flex items-center gap-2.5 min-w-0"><span className="t-kpi tabular-nums">{c.value}</span><span className={`badge tone-${c.tone} shrink-0`}>{c.chip}</span></div>
          <span className="t-support mt-1.5">{c.foot}</span>
        </section>
      ))}
    </div>
  );
}

export function OrderCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  const tone = toneFor(r.status);
  const p = PLATFORM[r.platform] ?? { name: titleCase(String(r.platform ?? "")), color: "#64748b" };
  const lines = (r.lines as Row[]) ?? [];
  const shown = lines.slice(0, 3);
  const stuck = r.status === "EXCEPTION";
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": EDGE[tone] ?? EDGE.neutral } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.external_order_id}`}>
        <div className="flex items-start justify-between gap-2">
          <span className="font-heading font-bold text-[0.9375rem] leading-5 font-mono">#{r.external_order_id}</span>
          <span className={`badge tone-${tone} shrink-0`}>{r.status === "SO_CREATED" ? "Sales order created" : titleCase(r.status)}</span>
        </div>
        <div>
          <p className="label-caps inline-flex items-center gap-1.5 max-w-full"><span className="w-2 h-2 rounded-full shrink-0" style={{ background: p.color }} aria-hidden /><span className="truncate" title={r.account_name}>{p.name}</span></p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{r.buyer ?? "Customer not given"}</h3>
          <p className="t-support mt-0.5">{[r.city, r.order_date ? fmtDate(r.order_date) : `Received ${fmtDate(r.received_at)}`].filter(Boolean).join(" · ")}</p>
        </div>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          {shown.length === 0 ? <p className="t-support">No lines on this order.</p> : shown.map((l, i) => (
            <div key={`${l.sku}-${i}`} className="flex items-center justify-between gap-3 px-2 py-0.5">
              <span className="min-w-0 truncate" title={l.item_name ?? l.sku}>{l.item_name ?? <span className="font-mono text-[0.75rem]">{l.sku}</span>}{!l.item_name && <span className="ml-2 badge tone-danger">Unmapped</span>}</span>
              <span className="shrink-0 font-mono"><b className="font-bold">{fmtNum(l.qty, 3)}</b> × {fmtMoney(l.unit_price)}</span>
            </div>
          ))}
          {lines.length > shown.length && <p className="t-support px-2">+{lines.length - shown.length} more line{lines.length - shown.length === 1 ? "" : "s"}</p>}
        </div>
        {stuck && r.exception_reason && <p className="rounded-lg bg-[#fff1f2] text-[#9f1239] px-3 py-2 text-[0.75rem] leading-4 break-words">{r.exception_reason}</p>}
        <div className="flex items-center justify-between gap-2 t-support">
          <span>Order total: <b className="text-fg font-semibold font-mono">{r.total != null ? fmtMoney(r.total) : "—"}</b></span>
          {r.so_number && <span className="font-mono">{r.so_number}</span>}
        </div>
      </button>
      <div className="mt-auto flex flex-wrap gap-1.5">
        {actions.map((a, i) => <button key={`${a.label}-${i}`} className={`btn btn-sm ${a.danger ? "btn-danger" : ""}`} onClick={() => onAction(a, r)}>{a.label}</button>)}
        {stuck && <Link href="/m/ecommerce/accounts" className="btn btn-sm">Fix SKU mapping</Link>}
        {r.so_number && <Link href="/m/sales/orders" className="btn btn-sm">View sales order</Link>}
      </div>
    </article>
  );
}
