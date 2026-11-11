"use client";
/** The purchase order board: four figures above the list, and one card per order showing the supplier, what was ordered and how much has arrived, where the
 *  order came from, the total, and how close the expected date is. Figures come from /procurement/po-board. */
import Link from "next/link";
import { fmtDate, fmtMoney, fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { toneFor, type Action, type Row } from "@/lib/modules";

const EDGE: Record<string, string> = { success: "var(--brand)", warning: "var(--amber)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--line-strong)" };
type Summary = { open_value: number; open_count: number; pending_count: number; pending_value: number; due_soon: number; overdue: number; received_30d: number; received_value_30d: number };

export function PoSummary({ summary }: { summary: Summary | null | undefined }) {
  if (!summary) return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-[118px] !rounded-xl" />)}</div>;
  const cards = [
    { label: "Open commitments", value: fmtRupees(summary.open_value), chip: `${fmtNum(summary.open_count)} open PO${summary.open_count === 1 ? "" : "s"}`, tone: "info", foot: "Approved and part-received orders", accent: "var(--info)" },
    { label: "Awaiting approval", value: fmtNum(summary.pending_count), chip: summary.pending_count ? fmtRupees(summary.pending_value) : "All clear", tone: summary.pending_count ? "warning" : "success", foot: "Orders waiting for a second approver", accent: "var(--amber)" },
    { label: "Due within 7 days", value: fmtNum(summary.due_soon), chip: summary.overdue ? `${fmtNum(summary.overdue)} overdue` : "None overdue", tone: summary.overdue ? "danger" : "success", foot: "Open orders by expected date", accent: "var(--brand-bright)" },
    { label: "Received, 30 days", value: fmtNum(summary.received_30d), chip: fmtRupees(summary.received_value_30d), tone: "success", foot: "Orders fully received", accent: "var(--brand-bright)" },
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

export function PoCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  const tone = r.late ? "danger" : toneFor(r.status);
  const lines = (r.lines as Row[]) ?? [];
  const shown = lines.slice(0, 3);
  const open = ["APPROVED", "PARTIALLY_RECEIVED"].includes(r.status);
  const days = r.days_left as number | null;
  const urgency = !open || days == null ? null : days < 0 ? { text: `${-days} day${days === -1 ? "" : "s"} late`, tone: "danger" } : days <= 7 ? { text: days === 0 ? "Due today" : `${days} day${days === 1 ? "" : "s"} left`, tone: "warning" } : null;
  const pill = r.status === "PARTIALLY_RECEIVED" ? `Part received · ${fmtNum(r.received_pct, 0)}%` : titleCase(r.status);
  const showProgress = ["PARTIALLY_RECEIVED", "RECEIVED", "APPROVED"].includes(r.status);
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": EDGE[tone] ?? EDGE.neutral } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.number}`}>
        <div className="flex items-start justify-between gap-2">
          <span className="font-heading font-bold text-[0.9375rem] leading-5">#{r.number}</span>
          <span className={`badge tone-${toneFor(r.status)} shrink-0`}>{pill}</span>
        </div>
        <div>
          <p className="label-caps truncate">{r.supplier}{r.supplier_city ? ` · ${r.supplier_city}` : ""}</p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{r.item_name ?? "No items"}{r.line_count > 1 && <span className="t-support font-medium"> · +{r.line_count - 1} more</span>}</h3>
          <p className="t-support mt-0.5">Raised {fmtDate(r.created_at)}{r.source ? <> · from <span className="font-mono">{r.source}</span></> : ""}</p>
        </div>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          {shown.length === 0 ? <p className="t-support">No lines on this order.</p> : shown.map((l) => {
            const uom = String(l.uom ?? "").toLowerCase();
            return (
              <div key={l.item_code} className="flex items-center justify-between gap-3 px-2 py-0.5">
                <span className="truncate" title={l.name}>{l.name}</span>
                <span className="shrink-0 font-mono"><b className="font-bold">{fmtNum(l.qty_ordered, 3)}</b> {uom}<span className="ml-2 font-sans text-[0.75rem] text-muted">@ {fmtMoney(l.unit_price)}</span></span>
              </div>
            );
          })}
          {lines.length > shown.length && <p className="t-support px-2">+{lines.length - shown.length} more line{lines.length - shown.length === 1 ? "" : "s"}</p>}
          {showProgress && (
            <div className="px-2 pt-1.5">
              <div className="flex justify-between t-support"><span>Received</span><span className="font-mono font-semibold text-fg">{fmtNum(r.received_pct, 0)}%</span></div>
              <div className="progress mt-1"><span style={{ width: `${Math.max(r.received_pct > 0 ? 3 : 0, r.received_pct)}%` }} /></div>
            </div>
          )}
        </div>
        {r.status === "CANCELLED" && r.rejection_reason && <p className="t-support line-clamp-2">Reason: {r.rejection_reason}</p>}
        <div className="flex items-center justify-between gap-2 t-support">
          <span>Order total: <b className="text-fg font-semibold font-mono">{fmtMoney(r.total_amount)}</b></span>
          {urgency ? <span className={`badge tone-${urgency.tone}`}>{urgency.text}</span> : r.expected_date ? <span>Expected {fmtDate(r.expected_date)}</span> : <span>No date set</span>}
        </div>
      </button>
      <div className="mt-auto flex flex-wrap gap-1.5">
        {actions.map((a, i) => <button key={`${a.label}-${i}`} className={`btn btn-sm ${a.label === "Approve" || a.label === "Submit" ? "btn-primary" : a.danger ? "btn-danger" : ""}`} onClick={() => onAction(a, r)}>{a.label}</button>)}
        {open && <Link href="/m/procurement/receipts" className="btn btn-sm">Receive goods</Link>}
      </div>
    </article>
  );
}
