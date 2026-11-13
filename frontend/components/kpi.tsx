"use client";
/** KPI chips (Rapport pattern): a row of figures, each with an optional icon circle, the value, an optional coloured pill beside it (green for good
 *  news, yellow for attention, orange for a problem, grey for neutral context) and a small caption underneath. Numbers count up on first paint.
 *  A chip can also carry a small trend line (`spark`) and an arrow in its pill (`arrow`). Colour follows meaning, not direction: for revenue "up" is
 *  green, for overdue invoices "up" is orange, so the caller passes the tone. Both always come with a text equivalent for screen readers.
 *  It is a labelled region holding a list, so assistive technology announces how many figures there are. */
import type { LucideIcon } from "lucide-react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { CountUp } from "./fx";

export type StripCard = { label: string; value: string; chip?: string; tone?: string; foot?: string; accent?: string; icon?: LucideIcon;
  spark?: number[]; sparkBad?: boolean; sparkNote?: string; arrow?: "up" | "down" };

const PILL: Record<string, string> = { success: "pill-success", warning: "pill-warning", danger: "pill-danger" };

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
  if (!cards) return <div className={`skeleton h-[52px] !rounded-full ${className}`} />;
  return (
    <section className={className} aria-label={label}>
      <ul className="grid gap-x-8 gap-y-4 items-start [grid-template-columns:repeat(auto-fit,minmax(13.5rem,1fr))]">
        {cards.map((c) => {
          const Icon = c.icon;
          const Arrow = c.arrow === "up" ? ArrowUpRight : c.arrow === "down" ? ArrowDownRight : null;
          /* an attention chip (yellow or orange) says something needs doing; with nothing to do (the figure is zero) it is left out */
          const zero = /^[^\d]*0+(\.0+)?[^\d]*$/.test(c.value.trim());
          const chip = zero && (c.tone === "warning" || c.tone === "danger") ? undefined : c.chip;
          return (
            <li key={c.label} className="flex items-center gap-3 min-w-0" title={c.foot}>
              {Icon && <span className="kpi-ic" aria-hidden><Icon size={16} strokeWidth={1.5} /></span>}
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 min-w-0">
                  <span className="t-kpi tabular-nums whitespace-nowrap"><CountUp text={c.value} /></span>
                  {chip && <span className={`pill ${PILL[c.tone ?? ""] ?? "pill-neutral"} truncate max-w-[12rem] gap-0.5`}>{Arrow && <Arrow size={11} strokeWidth={2} aria-hidden />}{c.arrow && <span className="sr-only">{c.arrow === "up" ? "Up: " : "Down: "}</span>}{chip}</span>}
                </div>
                <p className="text-[0.6875rem] leading-4 text-muted line-clamp-2 mt-0.5 max-w-[16rem]">{c.label}{c.foot && !chip ? ` · ${c.foot}` : ""}</p>
              </div>
              {c.spark && <Spark points={c.spark} bad={c.sparkBad} note={c.sparkNote} />}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
