"use client";
/** Commercial insights for the Management page: where revenue came from, which products earned it, how old the money owed is, and the top customers.
 *  These moved off the Overview so that page stays an operational summary. Figures come from /analytics/overview-trends for the last 30 days. */
import { useEffect, useState } from "react";
import { sharedGet } from "@/lib/api";
import { addDays, businessToday } from "@/lib/format";
import { InView, RankedBars } from "./fx";
import { ReceivableAgeing, ShareBars } from "./command";
import { Panel, Skeleton } from "./blocks";

type Trends = { top_customers: Record<string, number>; revenue_by_channel: Record<string, number>; top_products: Record<string, number>; receivable_ageing: Record<string, number> };

export function CommercialInsights() {
  const today = businessToday();
  const [t, setT] = useState<Trends | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    sharedGet<Trends>("/analytics/overview-trends", { start: addDays(today, -29), end: today }).then((d) => live && setT(d)).catch(() => live && setT(null));
    return () => { live = false; };
  }, [today]);
  if (t === null) return null;
  const wait = <div className="px-5 pb-5 space-y-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-4" />)}</div>;
  return (
    <section className="mb-6" aria-label="Commercial insights">
      <h2 className="t-section mb-3">Commercial, last 30 days</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Revenue by channel" meta="Invoiced, ex-tax">{t === undefined ? wait : <InView><ShareBars data={t.revenue_by_channel} empty="No invoices in this period." /></InView>}</Panel>
        <Panel title="Top products" meta="By invoiced value">{t === undefined ? wait : <InView><ShareBars data={t.top_products} names={{}} empty="No product sales in this period." /></InView>}</Panel>
        <Panel title="Receivables ageing" meta="What customers owe, by days past the due date">{t === undefined ? wait : <InView><ReceivableAgeing data={t.receivable_ageing} /></InView>}</Panel>
        <Panel title="Top customers" meta="By invoiced revenue">{t === undefined ? wait : <InView><RankedBars data={t.top_customers} empty="No invoices in this period." /></InView>}</Panel>
      </div>
    </section>
  );
}
