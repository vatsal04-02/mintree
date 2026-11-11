"use client";
/** The RFQ board: four figures above the list, and one card per RFQ showing what a buyer needs at a glance: the item, who has quoted what, which quote is
 *  lowest, and what was paid last time. Awarded RFQs show the supplier, price and purchase order instead. All figures come from /procurement/rfq-board. */
import Link from "next/link";
import { CheckCircle2 } from "lucide-react";
import { fmtDate, fmtMoney, fmtNum, fmtPct, fmtRupees, titleCase } from "@/lib/format";
import { toneFor, type Action, type Row } from "@/lib/modules";

const EDGE: Record<string, string> = { success: "var(--brand)", warning: "var(--amber)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--line-strong)" };

type Summary = { awaited_quotes: number; open_rfqs: number; suppliers_invited: number; variance_pct: number | null; variance_sample: number; quotes_recorded: number; invitations: number; awarded_value: number; awarded_count: number };

export function RfqSummary({ summary }: { summary: Summary | null | undefined }) {
  if (!summary) return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-[118px] !rounded-xl" />)}</div>;
  const v = summary.variance_pct;
  const resp = summary.invitations ? (summary.quotes_recorded / summary.invitations) * 100 : null;
  const cards = [
    { label: "Pending vendor quotes", value: fmtNum(summary.awaited_quotes), chip: `${fmtNum(summary.open_rfqs)} open RFQ${summary.open_rfqs === 1 ? "" : "s"}`, tone: summary.awaited_quotes ? "warning" : "success", foot: `Across ${fmtNum(summary.suppliers_invited)} invited supplier${summary.suppliers_invited === 1 ? "" : "s"}`, accent: "var(--amber)" },
    { label: "Best quote vs last paid", value: v === null ? "—" : `${v > 0 ? "+" : ""}${fmtNum(v, 1)}%`, chip: v === null ? "No history" : v <= 0 ? "Below last price" : "Above last price", tone: v === null ? "neutral" : v <= 0 ? "success" : "danger", foot: v === null ? "Needs a quote and an earlier PO" : `Average over ${fmtNum(summary.variance_sample)} RFQ${summary.variance_sample === 1 ? "" : "s"}`, accent: "var(--brand-bright)" },
    { label: "Quote response", value: resp === null ? "—" : fmtPct(resp, 0), chip: `${fmtNum(summary.quotes_recorded)} of ${fmtNum(summary.invitations)}`, tone: resp !== null && resp < 50 ? "warning" : "success", foot: "Invitations answered by suppliers", accent: "var(--c-slate, #64748b)" },
    { label: "Awarded value", value: fmtRupees(summary.awarded_value), chip: `${fmtNum(summary.awarded_count)} awarded`, tone: "success", foot: "Purchase orders raised from awards", accent: "var(--brand-bright)" },
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

export function RfqCard({ r, actions, onAction, onOpen, poHref }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void; poHref?: string }) {
  const it = r.item as Row | null;
  const uom = String(it?.uom ?? "").toLowerCase();
  const tone = toneFor(r.status);
  const bids = (r.bids as Row[]) ?? [];
  const sorted = [...bids].sort((a, b) => (a.unit_price ?? Infinity) - (b.unit_price ?? Infinity));
  const awarded = r.status === "AWARDED" ? (r.award as Row | null) : null;
  const pill = r.status === "SENT" ? `Sent · ${r.bids_in}/${r.bids_total} bids` : titleCase(r.status);
  const vs = awarded && r.last_paid && awarded.unit_price ? ((awarded.unit_price - r.last_paid) / r.last_paid) * 100 : null;
  const shown = actions.filter((a) => !a.secondary);
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": EDGE[tone] ?? EDGE.neutral } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.number}`}>
        <div className="flex items-start justify-between gap-2">
          <span className="font-heading font-bold text-[0.9375rem] leading-5">#{r.number}</span>
          <span className={`badge tone-${tone} shrink-0`}>{r.status === "AWARDED" && <CheckCircle2 size={12} aria-hidden />}{pill}</span>
        </div>
        <div>
          <p className="label-caps">{it ? `${KIND[it.kind] ?? it.kind}${it.category ? ` · ${it.category}` : ` · ${it.item_code}`}` : "No items"}</p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{it?.name ?? "—"}{r.line_count > 1 && <span className="t-support font-medium"> · +{r.line_count - 1} more</span>}</h3>
          <p className="t-support mt-0.5">{it ? <>Target: <b className="text-fg font-semibold">{fmtNum(it.qty, 3)} {uom}</b> · {r.due_date ? `Quote by ${fmtDate(r.due_date)}` : `Raised ${fmtDate(r.created_at)}`}</> : "—"}</p>
        </div>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          {awarded ? (
            <>
              <Row2 k="Vendor" v={<b className="font-semibold truncate">{awarded.supplier ?? "—"}</b>} />
              <Row2 k="Unit cleared" v={<span className="font-mono font-bold">{awarded.unit_price != null ? `${fmtMoney(awarded.unit_price)}/${uom}` : "—"}{vs !== null && <span className={vs <= 0 ? "text-success" : "text-danger"}> ({vs > 0 ? "+" : ""}{fmtNum(vs, 1)}%)</span>}</span>} />
              <Row2 k="Commitment" v={<span className="font-mono font-bold">{awarded.commitment != null ? fmtRupees(awarded.commitment) : "—"}</span>} />
              <Row2 k="Purchase order" v={<span className="font-mono">{awarded.po_number ?? "—"}</span>} />
            </>
          ) : sorted.length === 0 ? <p className="t-support">No suppliers invited yet.</p> : sorted.map((b) => (
            <div key={b.supplier_id} className={`flex items-center justify-between gap-3 rounded-md px-2 py-1 ${b.recommended ? "bg-white shadow-[inset_0_0_0_1px_var(--line)]" : ""}`} title={b.city ?? undefined}>
              <span className={`inline-flex items-center gap-1.5 min-w-0 ${b.quoted ? "" : "text-muted"}`}>{b.recommended && <CheckCircle2 size={13} className="text-brand shrink-0" aria-label="Recommended" />}<span className={`truncate ${b.recommended ? "font-semibold" : ""}`}>{b.name}</span></span>
              {b.quoted ? <span className={`font-mono shrink-0 ${b.recommended ? "font-bold" : "font-semibold"}`}>{b.unit_price != null ? `${fmtMoney(b.unit_price)}/${uom}` : "Partial"}</span> : <span className="t-support shrink-0">Awaiting quote</span>}
            </div>
          ))}
        </div>
        <div className="flex items-center justify-between gap-2 t-support">
          <span>{awarded ? `Lead time: ${awarded.lead_time_days ?? "—"} days` : <>Lowest: <b className="text-fg font-semibold">{r.lowest_unit_price != null ? `${fmtMoney(r.lowest_unit_price)}/${uom}` : "—"}</b></>}</span>
          <span>Last paid: <b className="text-fg font-semibold">{r.last_paid != null ? `${fmtMoney(r.last_paid)}/${uom}` : "—"}</b></span>
        </div>
      </button>
      <div className="mt-auto flex flex-wrap gap-1.5">
        {shown.map((a, i) => <button key={`${a.label}-${i}`} className={`btn btn-sm ${a.label === "Award" || a.label === "Send" ? "btn-primary" : a.danger ? "btn-danger" : ""}`} onClick={() => onAction(a, r)}>{a.label}</button>)}
        {awarded?.po_id && poHref && <Link href={`${poHref}?open=${awarded.po_id}`} className="btn btn-sm">View PO</Link>}
        {r.status === "AWARDED" && <button className="btn btn-sm" onClick={() => onOpen(r)}>Comparison</button>}
      </div>
    </article>
  );
}

function Row2({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex items-center justify-between gap-3 px-2 py-0.5"><span className="text-muted shrink-0">{k}</span><span className="min-w-0 truncate text-right">{v}</span></div>;
}

/** Bids as a CSV: one line per invited supplier per RFQ, for the buyer's spreadsheet. */
export function bidsCsv(rows: Row[]) {
  const esc = (v: unknown) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const head = ["RFQ", "Status", "Item", "Quantity", "UOM", "Supplier", "Quoted", "Unit price", "Landed total", "Lead time (days)", "Recommended", "Awarded"];
  const lines = rows.flatMap((r) => ((r.bids as Row[]) ?? []).map((b) => [r.number, r.status, r.item?.name, r.item?.qty, r.item?.uom, b.name, b.quoted ? "Yes" : "No", b.unit_price, b.landed_total, b.lead_time_days, b.recommended ? "Yes" : "", b.awarded ? "Yes" : ""]));
  return [head, ...lines].map((l) => l.map(esc).join(",")).join("\n");
}
