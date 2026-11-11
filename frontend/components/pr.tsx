"use client";
/** The purchase request board: four figures above the list, and one card per request showing who asked, what for and how much, whether stock would cover it,
 *  whether it has gone on to an RFQ or a purchase order, and how close the required-by date is. Figures come from /procurement/pr-board. */
import Link from "next/link";
import { fmtDate, fmtNum, titleCase } from "@/lib/format";
import { toneFor, type Action, type Row } from "@/lib/modules";
import { Strip } from "./kpi";

type Summary = { awaiting_approval: number; oldest_waiting_days: number; to_source: number; approved: number; due_soon: number; overdue: number; sourced: number };

export function PrSummary({ summary }: { summary: Summary | null | undefined }) {
  if (!summary) return <Strip cards={null} />;
  const cards = [
    { label: "Awaiting approval", value: fmtNum(summary.awaiting_approval), chip: summary.awaiting_approval ? `Oldest ${fmtNum(summary.oldest_waiting_days)} day${summary.oldest_waiting_days === 1 ? "" : "s"}` : "All clear", tone: summary.awaiting_approval ? "warning" : "success", foot: "Submitted requests waiting for a decision", accent: "var(--amber)" },
    { label: "Approved, not sourced", value: fmtNum(summary.to_source), chip: summary.to_source ? "Needs an RFQ or PO" : "All sourced", tone: summary.to_source ? "info" : "success", foot: `Of ${fmtNum(summary.approved)} approved request${summary.approved === 1 ? "" : "s"}`, accent: "var(--info)" },
    { label: "Needed within 7 days", value: fmtNum(summary.due_soon), chip: summary.overdue ? `${fmtNum(summary.overdue)} overdue` : "None overdue", tone: summary.overdue ? "danger" : "success", foot: "Open requests by required-by date", accent: "var(--brand-bright)" },
    { label: "Sourced so far", value: summary.approved ? `${fmtNum(summary.sourced)} of ${fmtNum(summary.approved)}` : "—", chip: summary.approved ? `${fmtNum(Math.round((summary.sourced / summary.approved) * 100))}% converted` : "No approvals", tone: "success", foot: "Approved requests with an RFQ or purchase order", accent: "var(--brand-bright)" },
  ];
  return <Strip cards={cards} />;
}


export function PrCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  /* Grid mode keeps only what a buyer scans: reference and status, the material, how much and what is in stock, when it is needed, and one next step. */
  const lines = (r.lines as Row[]) ?? [];
  const first = lines[0];
  const uom = String(first?.uom ?? "").toLowerCase();
  const open = ["SUBMITTED", "APPROVED"].includes(r.status);
  const days = r.days_left as number | null;
  const late = open && days != null && days < 0;
  const primary = actions.find((a) => !a.danger) ?? actions[0];
  return (
    <article className="panel panel-link p-4 flex flex-col gap-2.5">
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.number}`}>
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-[0.8125rem] font-semibold">{r.number}</span>
          <span className={`badge tone-${toneFor(r.status)}`}>{titleCase(r.status)}</span>
        </div>
        <div>
          <h3 className="text-[0.875rem] font-semibold leading-5 truncate" title={first?.name}>{first?.name ?? "No items"}{r.line_count > 1 && <span className="text-muted font-normal"> +{r.line_count - 1} more</span>}</h3>
          <dl className="mt-1.5 grid grid-cols-3 gap-2 text-[0.75rem]">
            <div><dt className="text-muted">Quantity</dt><dd className="font-mono font-semibold mt-0.5">{first ? `${fmtNum(first.qty, 3)} ${uom}` : "—"}</dd></div>
            <div><dt className="text-muted">In stock</dt><dd className={`font-mono font-semibold mt-0.5 ${first?.short ? "text-danger" : ""}`}>{first ? `${fmtNum(first.free, 1)} ${uom}` : "—"}</dd></div>
            <div><dt className="text-muted">Required by</dt><dd className={`font-semibold mt-0.5 ${late ? "text-danger" : ""}`}>{r.required_by ? fmtDate(r.required_by) : "Not set"}</dd></div>
          </dl>
        </div>
      </button>
      <div className="mt-auto pt-2.5 border-t border-line flex items-center justify-between gap-2">
        <span className="t-support truncate">{r.sourcing ? `Sourced on ${r.sourcing}` : late ? `${-days!} days overdue` : r.next_action ?? ""}</span>
        {primary ? <button className={`btn btn-sm shrink-0 ${primary.label === "Approve" || primary.label === "Submit" ? "btn-primary" : ""}`} onClick={() => onAction(primary, r)}>{primary.label}</button>
          : r.status === "APPROVED" && !r.sourcing ? <Link href="/m/procurement/rfqs" className="btn btn-sm shrink-0">Raise RFQ</Link> : null}
      </div>
    </article>
  );
}
