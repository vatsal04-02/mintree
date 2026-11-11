"use client";
/** The purchase request board: four figures above the list, and one card per request showing who asked, what for and how much, whether stock would cover it,
 *  whether it has gone on to an RFQ or a purchase order, and how close the required-by date is. Figures come from /procurement/pr-board. */
import Link from "next/link";
import { fmtDate, fmtNum, titleCase } from "@/lib/format";
import { toneFor, type Action, type Row } from "@/lib/modules";

const EDGE: Record<string, string> = { success: "var(--brand)", warning: "var(--amber)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--line-strong)" };
type Summary = { awaiting_approval: number; oldest_waiting_days: number; to_source: number; approved: number; due_soon: number; overdue: number; sourced: number };

export function PrSummary({ summary }: { summary: Summary | null | undefined }) {
  if (!summary) return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-[118px] !rounded-xl" />)}</div>;
  const cards = [
    { label: "Awaiting approval", value: fmtNum(summary.awaiting_approval), chip: summary.awaiting_approval ? `Oldest ${fmtNum(summary.oldest_waiting_days)} day${summary.oldest_waiting_days === 1 ? "" : "s"}` : "All clear", tone: summary.awaiting_approval ? "warning" : "success", foot: "Submitted requests waiting for a decision", accent: "var(--amber)" },
    { label: "Approved, not sourced", value: fmtNum(summary.to_source), chip: summary.to_source ? "Needs an RFQ or PO" : "All sourced", tone: summary.to_source ? "info" : "success", foot: `Of ${fmtNum(summary.approved)} approved request${summary.approved === 1 ? "" : "s"}`, accent: "var(--info)" },
    { label: "Needed within 7 days", value: fmtNum(summary.due_soon), chip: summary.overdue ? `${fmtNum(summary.overdue)} overdue` : "None overdue", tone: summary.overdue ? "danger" : "success", foot: "Open requests by required-by date", accent: "var(--brand-bright)" },
    { label: "Sourced so far", value: summary.approved ? `${fmtNum(summary.sourced)} of ${fmtNum(summary.approved)}` : "—", chip: summary.approved ? `${fmtNum(Math.round((summary.sourced / summary.approved) * 100))}% converted` : "No approvals", tone: "success", foot: "Approved requests with an RFQ or purchase order", accent: "var(--brand-bright)" },
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

const KIND: Record<string, string> = { RM: "Raw material", PM: "Packaging material", FG: "Finished goods", SFG: "Bulk" };

export function PrCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  const tone = toneFor(r.status);
  const lines = (r.lines as Row[]) ?? [];
  const open = ["SUBMITTED", "APPROVED"].includes(r.status);
  const days = r.days_left as number | null;
  const urgency = !open || days == null ? null : days < 0 ? { text: `${-days} day${days === -1 ? "" : "s"} overdue`, tone: "danger" } : days <= 7 ? { text: days === 0 ? "Needed today" : `${days} day${days === 1 ? "" : "s"} left`, tone: "warning" } : null;
  const who = [r.requester, r.department].filter(Boolean).join(" · ");
  const shown = lines.slice(0, 3);
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": EDGE[tone] ?? EDGE.neutral } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.number}`}>
        <div className="flex items-start justify-between gap-2">
          <span className="font-heading font-bold text-[0.9375rem] leading-5">#{r.number}</span>
          <span className={`badge tone-${tone} shrink-0`}>{titleCase(r.status)}</span>
        </div>
        <div>
          <p className="label-caps truncate">{who ? `Requested by ${who}` : lines[0] ? `${KIND[lines[0].kind] ?? lines[0].kind}` : "Purchase request"}</p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{lines[0]?.name ?? "No items"}{r.line_count > 1 && <span className="t-support font-medium"> · +{r.line_count - 1} more</span>}</h3>
          <p className="t-support mt-0.5">{r.notes ? <span className="line-clamp-1" title={r.notes}>{r.notes}</span> : <>{r.lines_label} · Raised {fmtDate(r.created_at)}</>}</p>
        </div>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          {shown.length === 0 ? <p className="t-support">No lines on this request.</p> : shown.map((l) => {
            const uom = String(l.uom ?? "").toLowerCase();
            return (
              <div key={l.item_id} className="flex items-center justify-between gap-3 px-2 py-0.5">
                <span className="truncate" title={l.name}>{l.name}</span>
                <span className="shrink-0 font-mono"><b className="font-bold">{fmtNum(l.qty, 3)}</b> {uom}<span className={`ml-2 font-sans text-[0.75rem] ${l.short ? "text-danger font-semibold" : "text-muted"}`}>{l.short ? `${fmtNum(l.free, 1)} in stock` : "in stock"}</span></span>
              </div>
            );
          })}
          {lines.length > shown.length && <p className="t-support px-2">+{lines.length - shown.length} more line{lines.length - shown.length === 1 ? "" : "s"}</p>}
        </div>
        <div className="flex items-center justify-between gap-2 t-support">
          <span>Required by: <b className="text-fg font-semibold">{r.required_by ? fmtDate(r.required_by) : "Not set"}</b></span>
          {urgency ? <span className={`badge tone-${urgency.tone}`}>{urgency.text}</span> : r.sourcing ? <span className="font-mono">{r.sourcing}</span> : r.status === "APPROVED" ? <span>Not sourced yet</span> : null}
        </div>
      </button>
      <div className="mt-auto flex flex-wrap gap-1.5">
        {actions.map((a, i) => <button key={`${a.label}-${i}`} className={`btn btn-sm ${a.label === "Approve" || a.label === "Submit" ? "btn-primary" : a.danger ? "btn-danger" : ""}`} onClick={() => onAction(a, r)}>{a.label}</button>)}
        {r.status === "APPROVED" && !r.sourcing && <Link href="/m/procurement/rfqs" className="btn btn-sm">Raise RFQ</Link>}
        {r.sourced === "RFQ" && <Link href="/m/procurement/rfqs" className="btn btn-sm">View RFQ</Link>}
        {r.sourced === "PO" && <Link href="/m/procurement/orders" className="btn btn-sm">View PO</Link>}
      </div>
    </article>
  );
}
