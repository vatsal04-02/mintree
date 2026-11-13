"use client";
/** One recall case: how far it has got, every batch it holds (release or reject each), every customer to contact, and the close button that only works when all of it is done. */
import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronLeft, Download } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime } from "@/lib/format";
import { Badge, EmptyState, PageHeader, useToast } from "@/components/ui";

type B = { batch_id: number; batch_no: string; item_code: string; item_name: string; role: string; prior_status: string; status_now: string; held_by_recall: boolean; qty_physical: string; qty_reserved: string; released_at: string | null };
type C = { id: number; customer: string; customer_code: string; order_number: string | null; awb: string | null; batch_no: string; qty: string; disposition: string; notified_at: string | null; note: string | null };
type Case = { id: number; number: string; status: string; severity: string; reason: string; created_at: string; closed_at: string | null; closure_note: string | null; summary: { orders_with_reserved_stock?: string[]; truncated?: boolean; root_batch_no?: string };
  progress: { customers_total: number; customers_pending: number; batches_total: number; batches_still_held: number; can_close: boolean }; batches: B[]; customers: C[] };

export default function RecallCasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { can } = useAuth();
  const toast = useToast();
  const [c, setC] = useState<Case | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const load = useCallback(() => { api<Case>(`/quality/recalls/${id}`).then(setC).catch((e) => toast("error", e instanceof ApiError ? e.message : "Could not load the recall")); }, [id, toast]);
  useEffect(() => { load(); }, [load]);

  const post = async (path: string, body: unknown, ok: string) => {
    setBusy(true);
    try { setC(await api<Case>(path, { method: "POST", body })); toast("success", ok); }
    catch (e) { toast("error", e instanceof ApiError ? e.message : "That did not work"); }
    finally { setBusy(false); }
  };
  if (!can("quality.recall:read")) return <EmptyState title="Not available" hint="Your role does not include recalls." art="search" />;
  if (!c) return <div className="h-40 rounded-2xl bg-inset animate-pulse" aria-busy="true" />;
  const open = c.status === "OPEN", p = c.progress;
  const needNote = note.trim().length < 3;
  return (
    <div data-testid="recall-case">
      <Link href="/m/quality/recalls" className="btn mb-4 inline-flex"><ChevronLeft size={16} />All recalls</Link>
      <PageHeader title={c.number} description={c.reason} tag={<Badge tone={c.status === "OPEN" ? "danger" : "success"}>{c.status}</Badge>}
        actions={<a className="btn" href={`/api/quality/recalls/${c.id}/notice.csv`}><Download size={14} />Call list (CSV)</a>} />

      <section className="grid gap-3 sm:grid-cols-3 mb-6" aria-label="Progress">
        {[["Batches still on hold", p.batches_still_held, p.batches_total], ["Customers still to deal with", p.customers_pending, p.customers_total], ["Severity", c.severity, ""]].map(([k, v, t]) => (
          <div key={String(k)} className="panel p-4"><p className="t-support">{k}</p><p className="t-kpi tabular-nums">{String(v)}{t !== "" && <span className="text-muted text-[0.875rem]"> of {String(t)}</span>}</p></div>
        ))}
      </section>
      {!!c.summary.orders_with_reserved_stock?.length && <p className="panel p-4 mb-6 text-[0.875rem]" role="note"><b>Re-allocate these orders:</b> they hold stock that is now on hold: {c.summary.orders_with_reserved_stock.slice(0, 12).join(", ")}{c.summary.orders_with_reserved_stock.length > 12 ? ` and ${c.summary.orders_with_reserved_stock.length - 12} more` : ""}.</p>}

      <section className="mb-8" aria-label="Batches">
        <h2 className="t-section mb-2">Batches</h2>
        <div className="panel overflow-x-auto"><table className="dt w-full"><thead><tr><th>Batch</th><th>Item</th><th>Role</th><th>Was</th><th>Now</th><th className="num">On hand</th><th /></tr></thead><tbody>
          {c.batches.map((b) => (
            <tr key={b.batch_id}><td className="t-ref">{b.batch_no}</td><td>{b.item_code}<span className="block text-muted text-[0.75rem]">{b.item_name}</span></td><td>{b.role === "SUSPECT" ? "Suspect" : "Affected"}</td><td>{b.prior_status}</td><td><Badge tone={b.status_now === "RELEASED" ? "success" : b.status_now === "REJECTED" ? "danger" : "warning"}>{b.status_now}</Badge></td><td className="num tabular-nums">{Number(b.qty_physical)}</td>
              <td className="text-right whitespace-nowrap">{open && b.held_by_recall && b.status_now === "HOLD" && can("quality.recall:close") && <>
                <button className="btn mr-2" disabled={busy || needNote} title={needNote ? "Write a note below first" : ""} onClick={() => post(`/quality/recalls/${c.id}/batches/${b.batch_id}/release`, { note }, "Batch released")}>Release</button>
                <button className="btn btn-danger" disabled={busy || needNote} title={needNote ? "Write a note below first" : ""} onClick={() => post(`/quality/recalls/${c.id}/batches/${b.batch_id}/reject`, { note }, "Batch rejected")}>Reject</button></>}</td></tr>
          ))}
        </tbody></table></div>
      </section>

      <section className="mb-8" aria-label="Customers">
        <div className="flex items-center justify-between mb-2"><h2 className="t-section">Customers to contact</h2>{open && p.customers_pending > 0 && can("quality.recall:notify") && <button className="btn" disabled={busy} onClick={() => post(`/quality/recalls/${c.id}/customers`, { disposition: "NOTIFIED", note: note.trim() || undefined }, "All pending customers marked notified")}>Mark all pending as notified</button>}</div>
        {c.customers.length === 0 ? <p className="text-muted">Nothing from these batches has been shipped.</p> : (
          <div className="panel overflow-x-auto"><table className="dt w-full"><thead><tr><th>Customer</th><th>Order</th><th>AWB</th><th>Batch</th><th className="num">Qty</th><th>Status</th><th /></tr></thead><tbody>
            {c.customers.map((r) => (
              <tr key={r.id}><td>{r.customer}<span className="block text-muted text-[0.75rem]">{r.customer_code}</span></td><td className="t-ref">{r.order_number}</td><td className="t-ref">{r.awb ?? "—"}</td><td className="t-ref">{r.batch_no}</td><td className="num tabular-nums">{Number(r.qty)}</td>
                <td><Badge tone={r.disposition === "PENDING" ? "warning" : "success"}>{r.disposition}</Badge>{r.notified_at && <span className="block text-muted text-[0.75rem]">{fmtDateTime(r.notified_at)}</span>}</td>
                <td className="text-right whitespace-nowrap">{open && can("quality.recall:notify") && (["NOTIFIED", "RETURNED", "CLEARED"] as const).filter((d) => d !== r.disposition).map((d) => (
                  <button key={d} className="btn ml-2" disabled={busy} onClick={() => post(`/quality/recalls/${c.id}/customers/${r.id}`, { disposition: d, note: note.trim() || undefined }, `Marked ${d.toLowerCase()}`)}>{d === "NOTIFIED" ? "Mark notified" : d === "RETURNED" ? "Stock returned" : "Cleared"}</button>))}</td></tr>
            ))}
          </tbody></table></div>
        )}
      </section>

      {open ? (
        <section className="panel p-5 max-w-2xl" aria-label="Note and close">
          <label className="field-label" htmlFor="recall-note">Note (used when you release, reject, mark a customer, or close)</label>
          <input id="recall-note" className="input mb-4" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was done, and by whom" />
          <button className="btn btn-primary" disabled={busy || !p.can_close || needNote} onClick={() => post(`/quality/recalls/${c.id}/close`, { note }, "Recall closed")} data-testid="recall-close">Close recall</button>
          {!p.can_close && <p className="text-[0.8125rem] text-muted mt-2">To close: every customer line dealt with and every held batch released or rejected.</p>}
        </section>
      ) : <p className="text-muted">Closed {fmtDateTime(c.closed_at)}{c.closure_note ? `: ${c.closure_note}` : ""}</p>}
    </div>
  );
}
