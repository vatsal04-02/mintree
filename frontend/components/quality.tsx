"use client";
/** QC control chart (phase 4.4): the readings of one numeric test in the order they were taken, with the centre line (their mean), control limits at three
 *  standard deviations (computed from the readings shown) and the specification limits the inspections were judged against. Readings outside the specification
 *  are orange. Everything is read from recorded inspection results (/quality/control-chart); inspectors enter these by hand, so nothing here is simulated. */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { Segmented } from "./blocks";
import { CardHead } from "./rapport";
import { InView } from "./fx";

type Pt = { value: number | string; passed: boolean; tested_at: string; inspection: string; batch_no?: string | null; min_value?: number | string | null; max_value?: number | string | null };
type Chart = { tests: string[]; test: string | null; unit?: string | null; points: Pt[]; mean?: number; sd?: number; lsl?: number | null; usl?: number | null };

export function ControlChart() {
  const [test, setTest] = useState<string | undefined>(undefined);
  const [d, setD] = useState<Chart | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    api<Chart>("/quality/control-chart", { params: test ? { test } : {} }).then((r) => live && setD(r)).catch(() => live && setD(null));
    return () => { live = false; };
  }, [test]);
  if (d === null || (d && d.points.length < 5)) return null;
  if (d === undefined) return <div className="skeleton h-72 !rounded-[28px] mb-6" />;

  const vals = d.points.map((p) => Number(p.value));
  const mean = d.mean ?? 0, sd = d.sd ?? 0;
  const ucl = mean + 3 * sd, lcl = mean - 3 * sd;
  const lo = Math.min(...vals, d.lsl ?? Infinity, lcl), hi = Math.max(...vals, d.usl ?? -Infinity, ucl);
  const pad = (hi - lo) * 0.08 || 1;
  const W = 1000, H = 260, X = (i: number) => (vals.length === 1 ? W / 2 : (i / (vals.length - 1)) * W);
  const Y = (v: number) => H - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * H;
  const out = d.points.filter((p, i) => (d.lsl != null && vals[i] < d.lsl) || (d.usl != null && vals[i] > d.usl) || p.passed === false).length;
  const ticks = [lo, (lo + hi) / 2, hi];
  const unit = d.unit ? ` ${d.unit}` : "";
  const line = (v: number, color: string, dash?: string, w = 1.5) => <line x1={0} x2={W} y1={Y(v)} y2={Y(v)} stroke={color} strokeWidth={w} strokeDasharray={dash} vectorEffect="non-scaling-stroke" />;

  return (
    <section className="panel rise mb-6" aria-label="QC control chart">
      <CardHead title={`Control chart: ${d.test}`} meta={`${d.points.length} readings · mean ${fmtNum(mean, 2)}${unit} · spread ${fmtNum(sd, 2)} · ${out ? `${out} outside specification` : "all within specification"}`}>
        {d.tests.length > 1 && <Segmented<string> label="Test" value={d.test ?? ""} onChange={(v) => { setD(undefined); setTest(v); }} options={d.tests.map((t) => ({ value: t, label: t }))} />}
      </CardHead>
      <InView>
        <div className="px-[18px] pb-[18px]">
          <div className="relative pl-12" style={{ height: H + 8 }} role="img" aria-label={`${d.test} readings in time order: mean ${fmtNum(mean, 2)}, ${out} outside specification${d.lsl != null ? `, lower limit ${d.lsl}` : ""}${d.usl != null ? `, upper limit ${d.usl}` : ""}`}>
            {ticks.map((t, i) => <span key={i} className="absolute left-0 w-10 text-right t-support leading-none -translate-y-1/2" style={{ top: `${(Y(t) / H) * 100}%` }} aria-hidden>{fmtNum(t, 1)}</span>)}
            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute left-12 right-0 top-0 h-[260px] w-[calc(100%-3rem)] overflow-visible" aria-hidden>
              {d.usl != null && line(d.usl, "var(--acc-orange)")}
              {d.lsl != null && line(d.lsl, "var(--acc-orange)")}
              {line(ucl, "var(--muted)", "4 6", 1)}{line(lcl, "var(--muted)", "4 6", 1)}
              {line(mean, "var(--ink-r)", "8 6")}
              <polyline points={vals.map((v, i) => `${Math.round(X(i))},${Math.round(Y(v))}`).join(" ")} fill="none" stroke="var(--ink-r)" strokeOpacity="0.45" strokeWidth="1.5" vectorEffect="non-scaling-stroke" className="fx-draw" pathLength={1} />
            </svg>
            <div className="absolute left-12 right-0 top-0 h-[260px]">
              {d.points.map((p, i) => {
                const bad = (d.lsl != null && vals[i] < d.lsl) || (d.usl != null && vals[i] > d.usl) || p.passed === false;
                return <span key={i} className="absolute w-2.5 h-2.5 -ml-[5px] -mt-[5px] rounded-full" style={{ left: `${(X(i) / W) * 100}%`, top: `${(Y(vals[i]) / H) * 100}%`, background: bad ? "var(--acc-orange)" : "var(--ink-r)", boxShadow: "0 0 0 2px var(--card)" }}
                  title={`${fmtDate(p.tested_at.slice(0, 10))} · ${p.inspection}${p.batch_no ? ` · ${p.batch_no}` : ""}: ${fmtNum(vals[i], 2)}${unit}${bad ? " (outside specification)" : ""}`} />;
              })}
            </div>
          </div>
          <div className="flex justify-between pl-12 mt-1 t-support" aria-hidden><span>{fmtDate(d.points[0].tested_at.slice(0, 10))}</span><span>{fmtDate(d.points[d.points.length - 1].tested_at.slice(0, 10))}</span></div>
          <p className="t-support mt-2 flex flex-wrap gap-x-4 gap-y-1" aria-hidden>
            <span className="inline-flex items-center gap-1.5"><span className="w-4 h-px" style={{ background: "var(--acc-orange)" }} />Specification limits</span>
            <span className="inline-flex items-center gap-1.5"><span className="w-4 border-t border-dashed" style={{ borderColor: "var(--ink-r)" }} />Mean</span>
            <span className="inline-flex items-center gap-1.5"><span className="w-4 border-t border-dotted" style={{ borderColor: "var(--muted)" }} />Control limits (mean ± 3 spreads)</span>
            <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: "var(--acc-orange)" }} />Outside specification</span>
          </p>
        </div>
      </InView>
    </section>
  );
}
