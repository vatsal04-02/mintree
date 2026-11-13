"use client";
/** Growth section of the Management page (Rapport pattern 10): what was invoiced each month and how much of it is collected, overdue or not yet due;
 *  revenue by sales channel; invoices raised per month. 3, 6 or 12 months, with a CSV of exactly what is on screen. Data: /analytics/growth.
 *  There is no win-rate trend: every stage change in the data falls in one month, so a trend would be a single dot. */
import { useEffect, useMemo, useState } from "react";
import { Download } from "lucide-react";
import { api } from "@/lib/api";
import { downloadCsv } from "@/lib/csv";
import { fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { Segmented } from "./blocks";
import { InView } from "./fx";
import { CardHead } from "./rapport";

type Month = { month: string; collected: number; overdue: number; open: number; invoices: number };
type Growth = { months: Month[]; by_channel: Record<string, number>; window_months: number };
const n = (v: unknown) => Number(v ?? 0) || 0;
const short = (v: number) => (v >= 1e7 ? `₹${fmtNum(v / 1e7, 2)}Cr` : v >= 1e5 ? `₹${fmtNum(v / 1e5, 1)}L` : v >= 1e3 ? `₹${fmtNum(v / 1e3, 0)}k` : fmtRupees(v));
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const label = (m: string) => `${MON[Number(m.slice(5, 7)) - 1]}${m.slice(5, 7) === "01" ? ` ${m.slice(2, 4)}` : ""}`;
const PARTS: { key: "collected" | "open" | "overdue"; name: string; color: string }[] = [
  { key: "collected", name: "Collected", color: "var(--acc-green)" }, { key: "open", name: "Not yet due", color: "var(--acc-yellow)" }, { key: "overdue", name: "Overdue", color: "var(--acc-orange)" },
];
/** Channel shares are not good or bad, so they are drawn in ink at falling strengths instead of in the meaning colours. */
const SHADE = [1, 0.62, 0.4, 0.26, 0.16];

export function GrowthSection() {
  const [months, setMonths] = useState<"3" | "6" | "12">("6");
  const [d, setD] = useState<Growth | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    setD(undefined);
    api<Growth>("/analytics/growth", { params: { months } }).then((r) => live && setD(r)).catch(() => live && setD(null));
    return () => { live = false; };
  }, [months]);

  const rows = d?.months ?? [];
  const max = Math.max(1, ...rows.map((r) => r.collected + r.open + r.overdue));
  const countMax = Math.max(1, ...rows.map((r) => r.invoices));
  const lastCount = rows.map((r) => r.invoices > 0).lastIndexOf(true);
  const channels = useMemo(() => Object.entries(d?.by_channel ?? {}).map(([k, v]) => [k, n(v)] as const).filter(([, v]) => v > 0), [d]);
  const chTotal = channels.reduce((a, [, v]) => a + v, 0);
  const total = rows.reduce((a, r) => a + r.collected + r.open + r.overdue, 0);

  function exportCsv() {
    downloadCsv(`growth-${months}m.csv`, ["Month", "Collected", "Not yet due", "Overdue", "Invoiced total", "Invoices"], rows.map((r) => [r.month, r.collected, r.open, r.overdue, r.collected + r.open + r.overdue, r.invoices]));
  }

  // donut geometry: segments in order, a small gap between them
  const R = 40, C = 2 * Math.PI * R;
  let acc = 0;

  return (
    <section className="mb-6" aria-label="Growth">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <h2 className="t-section">Growth</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented<"3" | "6" | "12"> label="Period" value={months} onChange={setMonths} options={[{ value: "3", label: "3M" }, { value: "6", label: "6M" }, { value: "12", label: "12M" }]} />
          <button type="button" className="btn gap-1.5" onClick={exportCsv} disabled={!rows.length}><Download size={14} strokeWidth={1.5} />Export CSV</button>
        </div>
      </div>
      {d === undefined ? <div className="skeleton h-72 !rounded-[28px]" /> : !d ? <p className="panel p-5 text-danger">The growth figures could not be loaded.</p> : (
        <div className="grid gap-4 xl:grid-cols-12 items-stretch">
          <section className="panel rise xl:col-span-8 flex flex-col min-w-0" aria-label="Invoiced by month">
            <CardHead title="Invoiced by month" meta={`${short(total)} incl. GST over ${months} months`} />
            <InView className="flex-1 flex flex-col">
              <div className="px-[18px] flex flex-wrap gap-x-4 gap-y-1 mb-2" aria-hidden>
                {PARTS.map((p) => <span key={p.key} className="t-support inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: p.color }} />{p.name}</span>)}
              </div>
              <div className="px-[18px] pb-[18px] flex-1">
                <div className="flex items-end gap-2 h-[240px]" role="img" aria-label={`Invoiced by month: ${rows.map((r) => `${label(r.month)} ${short(r.collected + r.open + r.overdue)}`).join(", ")}`}>
                  {rows.map((r, i) => {
                    const t = r.collected + r.open + r.overdue;
                    return (
                      <div key={r.month} className="flex-1 min-w-0 h-full flex flex-col items-center justify-end gap-1" title={`${label(r.month)}: ${short(t)} (collected ${short(r.collected)}, not yet due ${short(r.open)}, overdue ${short(r.overdue)})`}>
                        <span className="text-[0.6875rem] tabular-nums text-muted">{t ? short(t) : ""}</span>
                        <div className="w-full max-w-[44px] flex flex-col-reverse gap-px rounded-[14px] overflow-hidden bar-grow" style={{ height: `${(t / max) * 80}%`, animationDelay: `${i * 50}ms` }}>
                          {PARTS.map((p) => r[p.key] > 0 && <span key={p.key} style={{ flex: r[p.key], background: p.color, minHeight: 3 }} />)}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="flex gap-2 mt-1.5 border-t border-line pt-1.5" aria-hidden>{rows.map((r) => <span key={r.month} className="flex-1 min-w-0 text-center text-[0.6875rem] text-muted truncate">{label(r.month)}</span>)}</div>
              </div>
            </InView>
          </section>

          <section className="panel rise xl:col-span-4 flex flex-col min-w-0" aria-label="Revenue by channel">
            <CardHead title="Revenue by channel" meta="Invoiced, ex-tax" />
            {channels.length === 0 ? <p className="t-support px-[18px] pb-[18px]">No invoices in this period.</p> : (
              <div className="px-[18px] pb-[18px] flex-1 flex flex-col">
                <div className="relative w-[150px] h-[150px] mx-auto my-1" role="img" aria-label={`Revenue by channel: ${channels.map(([k, v]) => `${titleCase(k)} ${fmtNum((v / chTotal) * 100, 0)}%`).join(", ")}`}>
                  <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90" aria-hidden>
                    <circle cx="50" cy="50" r={R} fill="none" stroke="var(--chart-track)" strokeWidth="10" />
                    {channels.map(([k, v], i) => {
                      const len = (v / chTotal) * C, off = acc; acc += len;
                      return <circle key={k} cx="50" cy="50" r={R} fill="none" stroke="var(--ink-r)" strokeOpacity={SHADE[Math.min(i, SHADE.length - 1)]} strokeWidth="10" strokeDasharray={`${Math.max(len - 1.5, 0.5)} ${C}`} strokeDashoffset={-off} />;
                    })}
                  </svg>
                  <div className="absolute inset-0 grid place-items-center text-center"><div><p className="text-[0.9375rem] leading-5 font-semibold tracking-[-0.02em] tabular-nums">{short(chTotal)}</p><p className="t-support">ex-tax</p></div></div>
                </div>
                <ul className="mt-2 divide-y divide-line">
                  {channels.map(([k, v], i) => (
                    <li key={k} className="flex items-center justify-between gap-3 py-1.5 text-[0.8125rem]">
                      <span className="inline-flex items-center gap-2 min-w-0"><span className="w-2 h-2 rounded-full shrink-0" style={{ background: "var(--ink-r)", opacity: SHADE[Math.min(i, SHADE.length - 1)] }} aria-hidden /><span className="truncate">{titleCase(k)}</span></span>
                      <span className="tabular-nums shrink-0"><span className="text-muted mr-3">{fmtNum((v / chTotal) * 100, 0)}%</span>{short(v)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <section className="panel rise xl:col-span-12 min-w-0" aria-label="Invoices per month">
            <CardHead title="Invoices per month" meta="Sales invoices raised, latest month in orange" />
            <InView>
              <div className="px-[18px] pb-[18px]">
                <div className="flex items-end gap-2 h-[110px]" role="img" aria-label={`Invoices per month: ${rows.map((r) => `${label(r.month)} ${r.invoices}`).join(", ")}`}>
                  {rows.map((r, i) => (
                    <div key={r.month} className="flex-1 min-w-0 h-full flex flex-col items-center justify-end gap-1" title={`${label(r.month)}: ${r.invoices} invoices`}>
                      <span className="text-[0.6875rem] tabular-nums text-muted">{r.invoices || ""}</span>
                      <div className="w-full max-w-[44px] rounded-t-[10px] rounded-b-[3px] bar-grow" style={{ height: `${Math.max(r.invoices > 0 ? 4 : 0, (r.invoices / countMax) * 78)}%`, background: i === lastCount ? "var(--acc-orange)" : "var(--ink-r)", opacity: i === lastCount ? 1 : 0.78, animationDelay: `${i * 50}ms` }} />
                    </div>
                  ))}
                </div>
                <div className="flex gap-2 mt-1.5 border-t border-line pt-1.5" aria-hidden>{rows.map((r) => <span key={r.month} className="flex-1 min-w-0 text-center text-[0.6875rem] text-muted truncate">{label(r.month)}</span>)}</div>
              </div>
            </InView>
          </section>
        </div>
      )}
    </section>
  );
}
