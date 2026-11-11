"use client";
/** Boards for Returns to supplier, Production orders, Work centres and Bills of materials, and the Wastage & yield view. Each has four figures above the list
 *  and a card per record that says what the record is about, not just its number. Figures and rows come from the *-board endpoints in module_dashboards.py. */
import { fmtDate, fmtMoney, fmtNum, fmtPct, fmtRupees, titleCase } from "@/lib/format";
import { toneFor, type Action, type Row } from "@/lib/modules";

const EDGE: Record<string, string> = { success: "var(--brand)", warning: "var(--amber)", danger: "var(--danger)", info: "var(--info)", neutral: "var(--line-strong)" };
type Card = { label: string; value: string; chip?: string; tone?: string; foot: string; accent?: string };

function Strip({ cards }: { cards: Card[] | null }) {
  if (!cards) return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-[118px] !rounded-xl" />)}</div>;
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-4">
      {cards.map((c) => (
        <section key={c.label} className="panel kpi-card p-4 pt-[18px] flex flex-col" style={{ "--kpi-accent": c.accent ?? "var(--brand-bright)" } as React.CSSProperties} aria-label={c.label}>
          <span className="label-caps">{c.label}</span>
          <div className="mt-1.5 flex items-center gap-2.5 min-w-0"><span className="t-kpi tabular-nums">{c.value}</span>{c.chip && <span className={`badge tone-${c.tone ?? "neutral"} shrink-0`}>{c.chip}</span>}</div>
          <span className="t-support mt-1.5">{c.foot}</span>
        </section>
      ))}
    </div>
  );
}

function Buttons({ actions, onAction, r }: { actions: Action[]; onAction: (a: Action, r: Row) => void; r: Row }) {
  return <>{actions.map((a, i) => <button key={`${a.label}-${i}`} className={`btn btn-sm ${["Approve", "Release", "Complete"].includes(a.label) ? "btn-primary" : a.danger ? "btn-danger" : ""}`} onClick={() => onAction(a, r)}>{a.labelFn ? a.labelFn(r) : a.label}</button>)}</>;
}
const Line = ({ k, v }: { k: string; v: React.ReactNode }) => <div className="flex items-center justify-between gap-3 px-2 py-0.5"><span className="text-muted shrink-0">{k}</span><span className="min-w-0 truncate text-right">{v}</span></div>;

/* ------------------------------------------------------------------ returns to supplier */
const REASON: Record<string, string> = { QC_REJECTED: "QC rejected", DAMAGED: "Damaged", WRONG_ITEM: "Wrong item", EXCESS: "Excess", OTHER: "Other" };

export function ReturnsSummary({ summary: s }: { summary: Row | null | undefined }) {
  return <Strip cards={!s ? null : [
    { label: "Returns, 30 days", value: fmtNum(s.returns_30d), chip: fmtRupees(s.value_30d), tone: s.returns_30d ? "warning" : "success", foot: "Goods sent back after a receipt", accent: "var(--amber)" },
    { label: "Value returned", value: fmtRupees(s.value_total), chip: `${fmtNum(s.total)} return${s.total === 1 ? "" : "s"}`, tone: "neutral", foot: "Stock out and receipt accrual reversed", accent: "var(--brand-bright)" },
    { label: "Rejected at QC", value: s.total ? fmtPct((s.qc_rejected / s.total) * 100, 0) : "—", chip: `${fmtNum(s.qc_rejected)} of ${fmtNum(s.total)}`, tone: s.qc_rejected ? "warning" : "success", foot: "Returns caused by a failed incoming inspection", accent: "var(--danger)" },
    { label: "Suppliers affected", value: fmtNum(s.suppliers), chip: s.top_supplier ? "Most returned" : undefined, tone: "neutral", foot: s.top_supplier ? String(s.top_supplier) : "No returns yet", accent: "var(--info)" },
  ]} />;
}

export function ReturnCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  const lines = (r.lines as Row[]) ?? [];
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": EDGE.warning } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.number}`}>
        <div className="flex items-start justify-between gap-2"><span className="font-heading font-bold text-[0.9375rem] leading-5">#{r.number}</span><span className="badge tone-warning shrink-0">{REASON[r.reason_type] ?? titleCase(r.reason_type)}</span></div>
        <div>
          <p className="label-caps truncate">{r.supplier}{r.supplier_city ? ` · ${r.supplier_city}` : ""}</p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{r.item_name ?? "—"}{r.line_count > 1 && <span className="t-support font-medium"> · +{r.line_count - 1} more</span>}</h3>
          <p className="t-support mt-0.5">Posted {fmtDate(r.posted_at)}{r.posted_by ? ` by ${r.posted_by}` : ""}</p>
        </div>
        <p className="rounded-lg bg-[#fff8eb] text-[#92400e] px-3 py-2 text-[0.75rem] leading-4 break-words">{r.reason}</p>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          {lines.slice(0, 3).map((l, i) => <div key={i} className="flex items-center justify-between gap-3 px-2 py-0.5"><span className="truncate" title={l.name}>{l.name}</span><span className="shrink-0 font-mono"><b className="font-bold">{fmtNum(l.qty, 3)}</b> {String(l.uom ?? "").toLowerCase()}<span className="ml-2 font-sans text-[0.75rem] text-muted">@ {fmtMoney(l.unit_price)}</span></span></div>)}
          {lines.length > 3 && <p className="t-support px-2">+{lines.length - 3} more lines</p>}
        </div>
        <div className="t-support"><div className="flex items-center justify-between gap-2"><span>Value returned</span><b className="text-fg font-semibold font-mono">{fmtMoney(r.total_value)}</b></div><p className="font-mono mt-0.5 truncate">{r.receipt_number} · {r.po_number}</p></div>
      </button>
      {actions.length > 0 && <div className="mt-auto flex flex-wrap gap-1.5"><Buttons actions={actions} onAction={onAction} r={r} /></div>}
    </article>
  );
}

/* ------------------------------------------------------------------ production orders */
export function ProdSummary({ summary: s }: { summary: Row | null | undefined }) {
  return <Strip cards={!s ? null : [
    { label: "In progress", value: fmtNum(s.in_progress), chip: `${fmtNum(s.released)} released`, tone: "info", foot: `${fmtRupees(s.wip_value)} work in progress`, accent: "var(--brand-bright)" },
    { label: "Planned", value: fmtNum(s.planned), chip: s.late ? `${fmtNum(s.late)} past date` : "On schedule", tone: s.late ? "danger" : "success", foot: "Waiting to be released to the floor", accent: s.late ? "var(--danger)" : "var(--line-strong)" },
    { label: "Completed, 30 days", value: fmtNum(s.completed_30d), chip: `${fmtNum(s.units_30d)} units`, tone: "success", foot: "Finished batches and units made", accent: "var(--brand-bright)" },
    { label: "Average yield, 30 days", value: s.avg_yield_30d == null ? "—" : fmtPct(s.avg_yield_30d, 1), chip: s.avg_yield_30d != null && s.avg_yield_30d < 95 ? "Below 95%" : "On target", tone: s.avg_yield_30d != null && s.avg_yield_30d < 95 ? "warning" : "success", foot: "Actual output against planned", accent: "var(--info)" },
  ]} />;
}

export function ProdCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  const tone = r.late ? "danger" : toneFor(r.status);
  const uom = String(r.uom ?? "").toLowerCase();
  const open = ["PLANNED", "RELEASED", "IN_PROGRESS"].includes(r.status);
  const days = r.days_left as number | null;
  const urgency = !open || days == null ? null : days < 0 ? { text: `${-days} day${days === -1 ? "" : "s"} late`, tone: "danger" } : days <= 3 ? { text: days === 0 ? "Due today" : `${days} day${days === 1 ? "" : "s"} left`, tone: "warning" } : null;
  const stagePct = r.stages_total ? Math.round((r.stages_done / r.stages_total) * 100) : 0;
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": EDGE[tone] ?? EDGE.neutral } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.number}`}>
        <div className="flex items-start justify-between gap-2"><span className="font-heading font-bold text-[0.9375rem] leading-5">#{r.number}</span><span className={`badge tone-${toneFor(r.status)} shrink-0`}>{titleCase(r.status)}</span></div>
        <div>
          <p className="label-caps truncate">{r.item_code}</p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{r.item_name}</h3>
          <p className="t-support mt-0.5">Batch of <b className="text-fg font-semibold">{fmtNum(r.planned_qty, 3)} {uom}</b>{r.planned_date ? ` · Planned ${fmtDate(r.planned_date)}` : ""}</p>
        </div>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          {r.status === "COMPLETED" ? (
            <>
              <Line k="Made" v={<span className="font-mono font-bold">{fmtNum(r.actual_qty, 3)} {uom}</span>} />
              <Line k="Yield" v={<span className={`font-mono font-bold ${r.yield_pct != null && r.yield_pct < 95 ? "text-warning" : "text-success"}`}>{r.yield_pct != null ? fmtPct(r.yield_pct, 1) : "—"}</span>} />
              <Line k="Unit cost" v={<span className="font-mono font-bold">{r.unit_cost != null ? fmtMoney(r.unit_cost) : "—"}</span>} />
            </>
          ) : r.status === "CANCELLED" ? <p className="t-support px-2">This order was cancelled.</p> : (
            <>
              <div className="px-2"><div className="flex justify-between t-support"><span>Materials issued</span><span className="font-mono font-semibold text-fg">{fmtNum(r.materials_pct, 0)}%</span></div><div className="progress mt-1"><span style={{ width: `${Math.max(r.materials_pct > 0 ? 3 : 0, r.materials_pct)}%` }} /></div></div>
              <div className="px-2 pt-1"><div className="flex justify-between t-support"><span>{r.current_stage ? `Stage: ${titleCase(r.current_stage)}` : "Stages"}</span><span className="font-mono font-semibold text-fg">{r.stages_done} of {r.stages_total}</span></div><div className="progress mt-1"><span style={{ width: `${Math.max(stagePct > 0 ? 3 : 0, stagePct)}%` }} /></div></div>
            </>
          )}
        </div>
        <div className="flex items-center justify-between gap-2 t-support">
          <span>{r.operator ? `Operator: ${r.operator}` : r.completed_at ? `Completed ${fmtDate(r.completed_at)}` : `Raised ${fmtDate(r.created_at)}`}</span>
          {urgency ? <span className={`badge tone-${urgency.tone}`}>{urgency.text}</span> : null}
        </div>
      </button>
      {actions.length > 0 && <div className="mt-auto flex flex-wrap gap-1.5"><Buttons actions={actions} onAction={onAction} r={r} /></div>}
    </article>
  );
}

/* ------------------------------------------------------------------ work centres */
const KIND: Record<string, string> = { MIXING: "Mixing", FILLING: "Filling", PACKING: "Packing", LAB: "Laboratory", STORAGE: "Storage", OTHER: "Other" };

export function WcSummary({ summary: s }: { summary: Row | null | undefined }) {
  return <Strip cards={!s ? null : [
    { label: "Active work centres", value: fmtNum(s.active), chip: `of ${fmtNum(s.total)}`, tone: "neutral", foot: "Where production and testing happen", accent: "var(--brand-bright)" },
    { label: "Daily capacity", value: fmtNum(s.capacity_per_day), chip: s.unplanned ? `${fmtNum(s.unplanned)} not planned` : "All planned", tone: s.unplanned ? "warning" : "success", foot: "Output units per day, active centres", accent: "var(--info)" },
    { label: "Stages running now", value: fmtNum(s.running), chip: `${fmtNum(s.waiting)} waiting`, tone: "info", foot: "Booked to a work centre", accent: "var(--amber)" },
    { label: "Stages done, 30 days", value: fmtNum(s.done_30d), chip: undefined, foot: "Completed on these work centres", accent: "var(--brand-bright)" },
  ]} />;
}

export function WcCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": r.is_active ? "var(--brand-bright)" : "var(--line-strong)" } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open ${r.code}`}>
        <div className="flex items-start justify-between gap-2"><span className="font-heading font-bold text-[0.9375rem] leading-5 font-mono">{r.code}</span><span className={`badge tone-${r.is_active ? "success" : "neutral"} shrink-0`}>{r.is_active ? "Active" : "Inactive"}</span></div>
        <div>
          <p className="label-caps truncate">{KIND[r.kind] ?? titleCase(r.kind)}{r.department ? ` · ${r.department}` : ""}</p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{r.name}</h3>
        </div>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          <Line k="Capacity per day" v={<span className="font-mono font-bold">{r.capacity_per_day ? `${fmtNum(r.capacity_per_day)} units` : "Not planned"}</span>} />
          {r.running + r.waiting + r.done_30d === 0 ? <p className="t-support px-2 pt-1">No stages booked here in the last 30 days.</p> : (
            <>
              <Line k="Running now" v={<span className="font-mono font-bold">{fmtNum(r.running)}</span>} />
              <Line k="Waiting" v={<span className="font-mono font-bold">{fmtNum(r.waiting)}</span>} />
              <Line k="Stages done, 30 days" v={<span className="font-mono font-bold">{fmtNum(r.done_30d)}</span>} />
            </>
          )}
        </div>
      </button>
      {actions.length > 0 && <div className="mt-auto flex flex-wrap gap-1.5"><Buttons actions={actions} onAction={onAction} r={r} /></div>}
    </article>
  );
}

/* ------------------------------------------------------------------ bills of materials */
export function BomSummary({ summary: s }: { summary: Row | null | undefined }) {
  return <Strip cards={!s ? null : [
    { label: "Active BOMs", value: fmtNum(s.active), chip: s.draft ? `${fmtNum(s.draft)} awaiting approval` : "None pending", tone: s.draft ? "warning" : "success", foot: "Formulas production orders can use", accent: "var(--brand-bright)" },
    { label: "Products covered", value: `${fmtNum(s.products_covered)} of ${fmtNum(s.products_total)}`, chip: s.products_total ? `${fmtNum(Math.round((s.products_covered / s.products_total) * 100))}%` : undefined, tone: s.products_covered < s.products_total ? "warning" : "success", foot: "Finished products with an active BOM", accent: "var(--info)" },
    { label: "Linked to a formula", value: fmtNum(s.from_formula), chip: `of ${fmtNum(s.active)} active`, tone: "neutral", foot: "Built from an approved formulation", accent: "var(--brand-bright)" },
    { label: "Average components", value: fmtNum(s.avg_components, 1), chip: undefined, foot: "Materials per active BOM", accent: "var(--amber)" },
  ]} />;
}

export function BomCard({ r, actions, onAction, onOpen }: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) {
  const lines = (r.lines as Row[]) ?? [];
  const tone = r.status === "ACTIVE" ? "success" : r.status === "DRAFT" ? "warning" : "neutral";
  return (
    <article className="panel panel-link kpi-card p-4 flex flex-col gap-3" style={{ "--kpi-accent": EDGE[tone] } as React.CSSProperties}>
      <button type="button" className="text-left flex flex-col gap-2.5 rounded-lg" onClick={() => onOpen(r)} aria-label={`Open BOM ${r.id}`}>
        <div className="flex items-start justify-between gap-2"><span className="font-heading font-bold text-[0.9375rem] leading-5">BOM #{r.id} · v{r.version_no}</span><span className={`badge tone-${tone} shrink-0`}>{titleCase(r.status)}</span></div>
        <div>
          <p className="label-caps truncate">{r.item_code}{r.from_formula ? " · From formula" : " · Manual"}</p>
          <h3 className="font-heading font-bold text-[0.9375rem] leading-5 mt-1">{r.output_item}</h3>
          <p className="t-support mt-0.5">Batch of <b className="text-fg font-semibold">{r.batch_label}</b> · {r.lines_label}</p>
        </div>
        <div className="tile !p-2.5 space-y-1 text-[0.8125rem]">
          {lines.slice(0, 4).map((l) => <div key={l.item_code} className="flex items-center justify-between gap-3 px-2 py-0.5"><span className="truncate" title={l.name}>{l.name}</span><span className="shrink-0 font-mono"><b className="font-bold">{fmtNum(l.qty, 4)}</b> {String(l.uom ?? "").toLowerCase()}</span></div>)}
          {lines.length > 4 && <p className="t-support px-2">+{lines.length - 4} more components</p>}
          {lines.length === 0 && <p className="t-support px-2">No components yet.</p>}
        </div>
        <div className="flex items-center justify-between gap-2 t-support">
          <span>Used in <b className="text-fg font-semibold">{fmtNum(r.orders_made)}</b> order{r.orders_made === 1 ? "" : "s"}</span>
          <span>{r.last_unit_cost != null ? <>Last unit cost <b className="text-fg font-semibold font-mono">{fmtMoney(r.last_unit_cost)}</b></> : r.approved_by ? `Approved by ${r.approved_by}` : "Not approved yet"}</span>
        </div>
      </button>
      {actions.length > 0 && <div className="mt-auto flex flex-wrap gap-1.5"><Buttons actions={actions} onAction={onAction} r={r} /></div>}
    </article>
  );
}

/* ------------------------------------------------------------------ wastage & yield */
export function WastageView({ data: d }: { data: Row }) {
  const cats = (d.by_category as Row[]) ?? [];
  const low = (d.lowest_yield_orders as Row[]) ?? [];
  const maxV = Math.max(1, ...cats.map((c) => c.value));
  return (
    <>
      <Strip cards={[
        { label: "Wastage at cost", value: fmtRupees(d.total_value), chip: `${fmtNum(d.records)} record${d.records === 1 ? "" : "s"}`, tone: d.total_value ? "warning" : "success", foot: "Recorded against completed orders", accent: "var(--amber)" },
        { label: "Orders completed", value: fmtNum(d.orders_completed), chip: `${fmtNum(d.made_units)} units`, tone: "neutral", foot: `Of ${fmtNum(d.planned_units)} planned in the period`, accent: "var(--brand-bright)" },
        { label: "Average yield", value: d.avg_yield == null ? "—" : fmtPct(d.avg_yield, 1), chip: d.avg_yield != null && d.avg_yield < 95 ? "Below 95%" : "On target", tone: d.avg_yield != null && d.avg_yield < 95 ? "warning" : "success", foot: "Actual output against planned", accent: "var(--info)" },
        { label: "Output vs plan", value: d.planned_units ? fmtPct((d.made_units / d.planned_units) * 100, 1) : "—", chip: d.planned_units ? `${fmtNum(d.planned_units - d.made_units)} short` : undefined, tone: "neutral", foot: "Units made over units planned", accent: "var(--brand-bright)" },
      ]} />
      <div className="grid gap-4 items-start">
        <section className="panel p-5" aria-label="Wastage by category">
          <h2 className="t-section">Wastage by category</h2>
          <p className="t-support mt-0.5">Value at cost, from the wastage recorded on completed orders.</p>
          {cats.length === 0 ? <p className="t-support mt-4 rounded-lg bg-[var(--tile)] px-3 py-4">No wastage was recorded in this period.</p> : (
            <ul className="mt-4 space-y-3.5">{cats.map((c, i) => (
              <li key={`${c.category}-${i}`}>
                <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]"><span className="font-semibold truncate">{titleCase(c.category)}{c.department ? <span className="t-support font-normal"> · {c.department}</span> : ""}</span><span className="font-mono font-bold shrink-0">{fmtRupees(c.value)}</span></div>
                <div className="progress mt-1.5"><span style={{ width: `${Math.max(3, (c.value / maxV) * 100)}%`, background: "var(--amber)" }} /></div>
                <p className="t-support mt-1">{fmtNum(c.qty, 3)} units wasted · {c.records} record{c.records === 1 ? "" : "s"}</p>
              </li>))}
            </ul>
          )}
        </section>
        <section className="panel" aria-label="Lowest yield orders">
          <header className="p-5 pb-3"><h2 className="t-section">Lowest-yield orders</h2><p className="t-support mt-0.5">Completed orders in the period, worst first.</p></header>
          {low.length === 0 ? <p className="t-support px-5 pb-5">No orders were completed in this period.</p> : (
            <div className="overflow-x-auto"><table className="dt"><thead><tr><th>Order</th><th>Product</th><th className="num">Planned</th><th className="num">Made</th><th className="num">Yield</th></tr></thead>
              <tbody>{low.map((o) => (
                <tr key={o.number}>
                  <td className="font-mono text-[0.75rem] font-semibold text-brand">{o.number}</td>
                  <td className="max-w-[11rem] truncate" title={o.item_name}>{o.item_name ?? `Item #${o.output_item_id}`}</td>
                  <td className="num font-mono">{fmtNum(o.planned_qty, 3)}</td>
                  <td className="num font-mono">{o.actual_qty != null ? fmtNum(o.actual_qty, 3) : "—"}</td>
                  <td className="num"><span className={`badge ${o.yield_pct != null && Number(o.yield_pct) < 95 ? "tone-warning" : "tone-success"}`}>{o.yield_pct != null ? fmtPct(Number(o.yield_pct), 1) : "—"}</span></td>
                </tr>))}
              </tbody></table></div>
          )}
        </section>
      </div>
    </>
  );
}

export const BOARDS: Record<string, { Summary: (p: { summary: Row | null | undefined }) => React.ReactElement; Card: (p: { r: Row; actions: Action[]; onAction: (a: Action, r: Row) => void; onOpen: (r: Row) => void }) => React.ReactElement }> = {
  returns: { Summary: ReturnsSummary, Card: ReturnCard }, prod: { Summary: ProdSummary, Card: ProdCard }, wc: { Summary: WcSummary, Card: WcCard }, bom: { Summary: BomSummary, Card: BomCard },
};
