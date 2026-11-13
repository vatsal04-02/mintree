"use client";
/** Accounts landing screen (Rapport patterns 07 to 09): KPI chips, a cash-flow chart (money received, with money invoiced dashed), the receivables
 *  ageing as five tall bars, and the latest sales invoices; clicking an invoice opens it as a paper invoice in a frosted drawer.
 *  Figures come from /accounts/dashboard, /accounts/reports/ageing/receivables and /dispatch/invoices; the paper view from /dispatch/invoices/{id}/paper,
 *  which uses the same lines as the PDF. */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { Check, Copy, Download, Landmark, ReceiptText, TriangleAlert, Wallet, IndianRupee } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtNum, fmtRupees } from "@/lib/format";
import { Logo } from "./Logo";
import { Badge, Drawer, EmptyState, useToast } from "./ui";
import { Strip } from "./kpi";
import { AreaChart, InView } from "./fx";
import { CardHead } from "./rapport";
import { useShared, type Row } from "./command";

type To = (m: string, s: string) => string | undefined;
const n = (v: unknown) => Number(v ?? 0) || 0;
const short = (v: number) => (v >= 1e7 ? `₹${fmtNum(v / 1e7, 2)}Cr` : v >= 1e5 ? `₹${fmtNum(v / 1e5, 2)}L` : fmtRupees(v));
const BUCKETS: [string, string][] = [["current", "Current"], ["1-30", "1 to 30 days"], ["31-60", "31 to 60 days"], ["61-90", "61 to 90 days"], ["90+", "Over 90 days"]];
const STATUS: Record<string, { label: string; tone: "success" | "warning" | "danger" | "neutral" }> = {
  PAID: { label: "Paid", tone: "success" }, OPEN: { label: "Open", tone: "warning" }, PARTIALLY_PAID: { label: "Part paid", tone: "warning" }, OVERDUE: { label: "Overdue", tone: "danger" }, CANCELLED: { label: "Cancelled", tone: "neutral" },
};

/** Placeholder for the Accounts landing while its figures load: the same rows as the real page (key figures, cash flow beside ageing, invoice table), so nothing jumps when data arrives. */
function FinanceSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading accounts">
      <div className="grid gap-x-8 gap-y-4 [grid-template-columns:repeat(auto-fit,minmax(13.5rem,1fr))]">
        {[0, 1, 2, 3].map((i) => <div key={i} className="flex items-center gap-3"><div className="skeleton w-9 h-9 !rounded-full shrink-0" /><div className="flex-1 space-y-2"><div className="skeleton h-5 w-24" /><div className="skeleton h-3 w-32" /></div></div>)}
      </div>
      <div className="grid gap-4 xl:grid-cols-12 items-stretch">
        <section className="panel xl:col-span-8 p-[18px]"><div className="skeleton h-4 w-48 mb-4" /><div className="skeleton h-[190px] !rounded-[18px]" /></section>
        <section className="panel xl:col-span-4 p-[18px]"><div className="skeleton h-4 w-40 mb-4" /><div className="flex items-end gap-2.5 h-[150px]">{[40, 70, 55, 30, 85].map((h, i) => <div key={i} className="skeleton flex-1 !rounded-[18px]" style={{ height: `${h}%` }} />)}</div><div className="mt-4 space-y-2.5">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-3" />)}</div></section>
      </div>
      <section className="panel p-[18px]"><div className="skeleton h-4 w-44 mb-4" />{[0, 1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-3 mb-4 last:mb-0" style={{ width: `${96 - i * 3}%` }} />)}</section>
    </div>
  );
}

/* ================================================================== screen */
export function FinanceScreen({ to }: { to: To }) {
  const { can } = useAuth();
  const dash = useShared<Row>("/accounts/dashboard");
  const ageing = useShared<Row>("/accounts/reports/ageing/receivables");
  const canInv = can("accounts.sales_invoice:read");
  const invoices = useShared<{ items: Row[] }>(canInv ? "/dispatch/invoices" : null, { page_size: "10" });
  const params = useSearchParams(), router = useRouter();
  const openId = Number(params.get("invoice")) || null;
  const payments = to("accounts", "payments"), allInvoices = to("dispatch", "invoices");

  const buckets = useMemo(() => {
    const rows = ((ageing?.parties ?? []) as Row[]).flatMap((p) => (p.invoices ?? []) as Row[]);
    return BUCKETS.map(([key, label]) => { const inb = rows.filter((i) => i.bucket === key); return { key, label, count: inb.length, amount: inb.reduce((a, i) => a + n(i.outstanding), 0) }; });
  }, [ageing]);
  const maxB = Math.max(1, ...buckets.map((b) => b.amount));
  const received = dash?.payments_received_trend as { label: string; value: number }[] | undefined;
  const invoiced = dash?.invoiced_revenue_trend as { label: string; value: number }[] | undefined;
  const recTotal = (received ?? []).reduce((a, p) => a + p.value, 0), invTotal = (invoiced ?? []).reduce((a, p) => a + p.value, 0);

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 mb-5">
        <div className="min-w-0"><h1 className="t-page">Accounts</h1><p className="t-label mt-1">Receivables, payables, cash and the books.</p></div>
        <div className="flex flex-wrap gap-2">
          {to("accounts", "journals") && <Link href={to("accounts", "journals")!} className="btn">Journals</Link>}
          {payments && <Link href={payments} className="btn btn-primary">Record payment</Link>}
        </div>
      </div>

      {dash === undefined ? <FinanceSkeleton /> : !dash ? <p className="panel p-5 text-danger">The accounts summary could not be loaded.</p> : (
        <div className="space-y-4">
          <Strip label="Key figures" cards={[
            { label: "Receivables outstanding", value: short(n(dash.receivable_outstanding)), icon: Wallet, foot: `${fmtNum(n(dash.open_sales_invoices), 0)} open invoices` },
            { label: "Payables outstanding", value: short(n(dash.payable_outstanding)), icon: Landmark, foot: `${fmtNum(n(dash.open_supplier_invoices), 0)} supplier invoices` },
            { label: "Invoiced, last 30 days", value: short(n(dash.invoiced_revenue_30d)), icon: IndianRupee },
            { label: "Unmatched bank lines", value: fmtNum(n(dash.bank_lines_unmatched), 0), icon: n(dash.bank_lines_unmatched) ? TriangleAlert : ReceiptText, chip: n(dash.bank_lines_unmatched) ? "Needs matching" : "All matched", tone: n(dash.bank_lines_unmatched) ? "warning" : "success" },
          ]} />

          <div className="grid gap-4 xl:grid-cols-12 items-stretch">
            <section className="panel rise xl:col-span-8 flex flex-col min-w-0" aria-label="Cash flow">
              <CardHead title="Cash flow, last 30 days" meta={`${short(recTotal)} received · ${short(invTotal)} invoiced`} href={to("accounts", "payments")} />
              {received && received.length > 1
                ? <InView className="flex-1"><AreaChart points={received} compare={invoiced} height={190} name="Money received" color="var(--acc-green)" compareColor="var(--ink-r)" summary={false} label="Received" compareLabel="Invoiced" /></InView>
                : <p className="t-support px-[18px] pb-[18px]">Not enough days of payments to draw a chart.</p>}
            </section>

            <section className="panel rise xl:col-span-4 flex flex-col min-w-0" aria-label="Receivables ageing">
              <CardHead title="Receivables ageing" meta={ageing ? `${short(buckets.reduce((a, b) => a + b.amount, 0))} unpaid, by days overdue` : undefined} href={to("accounts", "ageing")} />
              {ageing === undefined ? <div className="skeleton mx-[18px] mb-[18px] h-48 !rounded-[22px]" /> : !ageing ? <p className="t-support px-[18px] pb-[18px]">The ageing report could not be loaded.</p> : (
                <div className="px-[18px] pb-[18px] flex-1 flex flex-col">
                  <div className="flex items-end gap-2.5 h-[150px]" role="img" aria-label={`Unpaid invoices by age: ${buckets.map((b) => `${b.label} ${short(b.amount)}`).join(", ")}`}>
                    {buckets.map((b, i) => (
                      <div key={b.key} className="flex-1 h-full rounded-[18px] flex items-end overflow-hidden" style={{ background: "var(--chart-track)" }} title={`${b.label}: ${short(b.amount)}`}>
                        <div className="w-full rounded-[18px] bar-grow" style={{ height: `${Math.max(b.amount > 0 ? 6 : 0, (b.amount / maxB) * 100)}%`, background: b.key === "90+" ? "var(--acc-orange)" : "var(--ink-r)", animationDelay: `${i * 70}ms` }} />
                      </div>
                    ))}
                  </div>
                  <ul className="mt-3 divide-y divide-line">
                    {buckets.map((b) => (
                      <li key={b.key} className="flex items-center justify-between gap-3 py-1.5 text-[0.8125rem]">
                        <span className="inline-flex items-center gap-2"><span className="w-2 h-2 rounded-full" style={{ background: b.key === "90+" ? "var(--acc-orange)" : "var(--ink-r)", opacity: b.key === "current" ? 0.45 : 1 }} aria-hidden />{b.label}</span>
                        <span className="tabular-nums"><span className="text-muted mr-3">{fmtNum(b.count, 0)} {b.count === 1 ? "invoice" : "invoices"}</span>{short(b.amount)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          </div>

          {canInv && (
            <section className="panel rise overflow-hidden" aria-label="Latest sales invoices">
              <CardHead title="Latest sales invoices" meta="Select one to open it as an invoice" href={allInvoices} />
              {invoices === undefined ? <div className="skeleton mx-[18px] mb-[18px] h-40 !rounded-[22px]" /> : !invoices || invoices.items.length === 0 ? <p className="t-support px-[18px] pb-[18px]">No invoices yet.</p> : (
                <div className="overflow-x-auto"><table className="dt dt-compact">
                  <thead><tr><th>Invoice</th><th>Customer</th><th>Date</th><th className="num">Total</th><th className="num">Outstanding</th><th>Status</th></tr></thead>
                  <tbody>{invoices.items.map((i) => {
                    const st = STATUS[String(i.status)] ?? { label: String(i.status), tone: "neutral" as const };
                    return (
                      <tr key={i.id} className="cursor-pointer" onClick={() => router.push(`?invoice=${i.id}`, { scroll: false })}>
                        <td className="font-mono text-[0.75rem]"><button type="button" className="hover:underline" onClick={(e) => { e.stopPropagation(); router.push(`?invoice=${i.id}`, { scroll: false }); }}>{i.number}</button></td>
                        <td className="max-w-[16rem] truncate" title={i.customer}>{i.customer}</td>
                        <td className="t-support whitespace-nowrap">{fmtDate(String(i.invoice_date))}</td>
                        <td className="num font-mono">{fmtRupees(n(i.total))}</td>
                        <td className="num font-mono">{n(i.outstanding) ? fmtRupees(n(i.outstanding)) : "—"}</td>
                        <td><Badge tone={st.tone}>{st.label}</Badge></td>
                      </tr>
                    );
                  })}</tbody>
                </table></div>
              )}
            </section>
          )}
        </div>
      )}
      <InvoiceDrawer id={openId} onClose={() => router.push("?", { scroll: false })} paymentsHref={payments} />
    </>
  );
}

/* ================================================================== paper invoice */
export function InvoiceDrawer({ id, onClose, paymentsHref }: { id: number | null; onClose: () => void; paymentsHref?: string }) {
  const toast = useToast();
  const [doc, setDoc] = useState<Row | null | undefined>(undefined);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!id) return;
    let live = true;
    setDoc(undefined);
    api<Row>(`/dispatch/invoices/${id}/paper`).then((d) => live && setDoc(d)).catch(() => live && setDoc(null));
    return () => { live = false; };
  }, [id]);
  const paid = doc?.status === "PAID";
  async function copy() {
    try { await navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}?invoice=${id}`); setCopied(true); toast("success", "Link copied"); setTimeout(() => setCopied(false), 2000); }
    catch { toast("error", "Could not copy the link"); }
  }
  return (
    <Drawer open={!!id} onClose={onClose} title={doc ? `Invoice ${doc.number}` : "Invoice"} subtitle={doc ? `Order ${doc.order_number}` : undefined} width={720}
      footer={doc ? (
        <>
          <button type="button" className="btn gap-1.5" onClick={copy}>{copied ? <Check size={14} strokeWidth={1.5} /> : <Copy size={14} strokeWidth={1.5} />}Copy link</button>
          <a className="btn gap-1.5" href={`/api/dispatch/invoices/${id}/pdf`} target="_blank" rel="noopener noreferrer"><Download size={14} strokeWidth={1.5} />Download PDF</a>
          {!paid && paymentsHref && <Link className="btn btn-primary" href={paymentsHref}>Record payment</Link>}
        </>
      ) : undefined}>
      {doc === undefined ? <div className="skeleton h-96 !rounded-[22px]" /> : !doc ? <EmptyState title="This invoice could not be loaded" hint="You may not have access to it." /> : (
        <article className="relative paper rounded-[18px] px-7 py-7 shadow-[0_24px_60px_-28px_rgb(70_45_20/0.35)] overflow-hidden" aria-label={`Invoice ${doc.number}`}>
          {paid && (
            <span className="absolute left-10 bottom-20 -rotate-[12deg] rounded-xl border-[3px] px-4 py-1 text-[1.5rem] tracking-[0.12em] pointer-events-none select-none" style={{ borderColor: "var(--acc-green)", color: "var(--acc-green)", opacity: 0.85 }} aria-label="Paid">PAID</span>
          )}
          <header className="flex items-start justify-between gap-4">
            <Logo height={24} />
            <div className="text-right"><p className="text-[1.125rem] leading-6">Tax invoice</p><p className="text-[0.75rem] opacity-60 font-mono">{doc.number}</p>
              <p className="text-[0.75rem] opacity-60">{fmtDate(String(doc.date))}{doc.due_date ? ` · due ${fmtDate(String(doc.due_date))}` : ""}</p></div>
          </header>
          <div className="mt-6 grid sm:grid-cols-2 gap-5 text-[0.8125rem]">
            <div><p className="text-[0.6875rem] opacity-55 mb-1">From</p><p className="font-medium">{doc.seller.name}</p>{doc.seller.gstin && <p className="opacity-65">GSTIN {doc.seller.gstin}</p>}{doc.seller.address && <p className="opacity-65">{doc.seller.address}</p>}</div>
            <div><p className="text-[0.6875rem] opacity-55 mb-1">Bill to</p><p className="font-medium">{doc.buyer.name}</p><p className="opacity-65">GSTIN {doc.buyer.gstin ?? "Unregistered"}</p>{doc.buyer.address && <p className="opacity-65">{doc.buyer.address}</p>}</div>
          </div>
          <div className="mt-6 overflow-x-auto"><table className="w-full text-[0.75rem]">
            <thead><tr className="text-left opacity-55 border-b border-current/10"><th className="py-2 font-normal">Item</th><th className="py-2 font-normal text-right">Qty</th><th className="py-2 font-normal text-right">Rate</th><th className="py-2 font-normal text-right">GST</th><th className="py-2 font-normal text-right">Amount</th></tr></thead>
            <tbody>{(doc.lines as Row[]).map((l, i) => (
              <tr key={i} className="border-b border-current/[0.07] align-top">
                <td className="py-2 pr-3"><span className="block">{l.name}</span><span className="block opacity-50 font-mono">HSN {l.hsn ?? "—"}</span></td>
                <td className="py-2 text-right tabular-nums">{fmtNum(n(l.qty), Number.isInteger(n(l.qty)) ? 0 : 2)}</td>
                <td className="py-2 text-right tabular-nums">{fmtRupees(n(l.rate))}</td>
                <td className="py-2 text-right tabular-nums">{fmtNum(n(l.tax_rate), 0)}%</td>
                <td className="py-2 text-right tabular-nums">{fmtRupees(n(l.amount))}</td>
              </tr>))}
            </tbody>
          </table></div>
          <dl className="mt-5 ml-auto w-full sm:w-64 text-[0.8125rem] space-y-1.5">
            <div className="flex justify-between"><dt className="opacity-60">Taxable value</dt><dd className="tabular-nums">{fmtRupees(n(doc.taxable))}</dd></div>
            {(doc.tax as Row[]).map((t) => <div key={t.label} className="flex justify-between"><dt className="opacity-60">{t.label}</dt><dd className="tabular-nums">{fmtRupees(n(t.amount))}</dd></div>)}
            <div className="flex justify-between border-t border-current/15 pt-2 text-[1rem]"><dt>Total</dt><dd className="tabular-nums">{fmtRupees(n(doc.total))}</dd></div>
            {n(doc.paid) > 0 && <div className="flex justify-between"><dt className="opacity-60">Paid</dt><dd className="tabular-nums">− {fmtRupees(n(doc.paid))}</dd></div>}
            {n(doc.credited) > 0 && <div className="flex justify-between"><dt className="opacity-60">Credit notes</dt><dd className="tabular-nums">− {fmtRupees(n(doc.credited))}</dd></div>}
            {n(doc.outstanding) > 0 && <div className="flex justify-between"><dt className="opacity-60">Balance due</dt><dd className="tabular-nums" style={{ color: "var(--acc-orange)" }}>{fmtRupees(n(doc.outstanding))}</dd></div>}
          </dl>
          <p className="mt-6 text-[0.6875rem] opacity-45">Computer-generated. E-invoice IRN and e-way bill are not attached (provider integration not configured).</p>
        </article>
      )}
    </Drawer>
  );
}
