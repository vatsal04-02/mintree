"use client";
/** KPI chips (Rapport pattern): a row of figures, each with an optional icon circle, the value, an optional coloured pill beside it (green for good
 *  news, yellow for attention, orange for a problem, grey for neutral context) and a small caption underneath. Numbers count up on first paint.
 *  A chip can also carry a small trend line (`spark`) and an arrow in its pill (`arrow`). Colour follows meaning, not direction: for revenue "up" is
 *  green, for overdue invoices "up" is orange, so the caller passes the tone. Both always come with a text equivalent for screen readers.
 *  It is a labelled region holding a list, so assistive technology announces how many figures there are. */
import type { LucideIcon } from "lucide-react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { CountUp } from "./fx";

export type StripCard = { label: string; value: string; chip?: string; tone?: string; foot?: string; accent?: string; icon?: LucideIcon;
  spark?: number[]; sparkBad?: boolean; sparkNote?: string; arrow?: "up" | "down"; href?: string };

const PILL: Record<string, string> = { success: "pill-success", warning: "pill-warning", danger: "pill-danger", info: "pill-info", neutral: "pill-neutral" };

/** A trend line in ink; the last point is orange when the latest movement is the wrong way, otherwise ink. */
function Spark({ points, bad, note }: { points: number[]; bad?: boolean; note?: string }) {
  if (points.length < 3 || points.every((v) => v === points[0])) return null;
  const W = 64, H = 26, max = Math.max(...points), min = Math.min(...points), span = max - min || 1;
  const xy = points.map((v, i): [number, number] => [(i / (points.length - 1)) * (W - 6) + 3, H - 4 - ((v - min) / span) * (H - 8)]);
  const last = xy[xy.length - 1];
  return (
    <span className="shrink-0 hidden min-[1600px]:block" role="img" aria-label={note ?? `Trend over the period, from ${points[0]} to ${points[points.length - 1]}`}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden>
        <polyline points={xy.map(([x, y]) => `${Math.round(x * 10) / 10},${Math.round(y * 10) / 10}`).join(" ")} fill="none" stroke="var(--ink-r)" strokeOpacity="0.55" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={last[0]} cy={last[1]} r="2.6" fill={bad ? "var(--acc-orange)" : "var(--ink-r)"} />
      </svg>
    </span>
  );
}

export function Strip({ cards, className = "mb-5", label = "Summary" }: { cards: StripCard[] | null; className?: string; label?: string }) {
  if (!cards) return <div className={`panel kpi-strip ${className}`} aria-busy="true"><div className="grid gap-x-8 gap-y-4 [grid-template-columns:repeat(auto-fit,minmax(13.5rem,1fr))]">{[0, 1, 2, 3].map((i) => <div key={i} className="flex items-center gap-3"><div className="skeleton w-10 h-10 !rounded-full shrink-0" /><div className="flex-1 space-y-2"><div className="skeleton h-6 w-28" /><div className="skeleton h-3 w-36" /></div></div>)}</div></div>;
  /* the indicators sit together in one card (app/system.css .kpi-strip): icon, figure, at most one chip, and a caption */
  return (
    <section className={`panel kpi-strip ${className}`} aria-label={label}>
      <ul className="grid gap-x-8 gap-y-4 items-start [grid-template-columns:repeat(auto-fit,minmax(13.5rem,1fr))]">
        {cards.map((c) => {
          const Icon = c.icon;
          const Arrow = c.arrow === "up" ? ArrowUpRight : c.arrow === "down" ? ArrowDownRight : null;
          /* an attention chip (yellow or orange) says something needs doing; with nothing to do (the figure is zero) it is left out */
          const zero = /^[^\d]*0+(\.0+)?[^\d]*$/.test(c.value.trim());
          const chip = zero && (c.tone === "warning" || c.tone === "danger") ? undefined : c.chip;
          return (
            /* three fixed rows, the same in every indicator: the figure, its label (one line), then one detail line holding either the chip or the
               supporting note, so figures, labels and chips each share a line across the strip. When there is a chip, the note is read to screen readers only. */
            <li key={c.label} className="relative flex items-start gap-3 min-w-0">
              {c.href && <Link href={c.href} className="absolute inset-0 -m-2 rounded-2xl hover:bg-white/40 transition-colors" aria-label={`${c.label}: ${c.value}. Open`} />}
              {Icon && <span className="kpi-ic mt-0.5" aria-hidden><Icon size={16} strokeWidth={1.5} /></span>}
              <div className="min-w-0 flex-1">
                <p className="t-kpi tabular-nums whitespace-nowrap"><CountUp text={c.value} /></p>
                <p className="t-support truncate mt-0.5" title={c.label}>{c.label}{chip && c.foot && <span className="sr-only">, {c.foot}</span>}</p>
                <div className="mt-1.5 h-6 flex items-center min-w-0">
                  {chip
                    ? <span className={`pill ${PILL[c.tone ?? ""] ?? "pill-neutral"} max-w-full truncate gap-0.5`}>{Arrow && <Arrow size={11} strokeWidth={2} aria-hidden />}{c.arrow && <span className="sr-only">{c.arrow === "up" ? "Up: " : "Down: "}</span>}{chip}</span>
                    : c.foot ? <span className="t-support truncate" title={c.foot}>{c.foot}</span> : null}
                </div>
              </div>
              {c.spark && <Spark points={c.spark} bad={c.sparkBad} note={c.sparkNote} />}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
