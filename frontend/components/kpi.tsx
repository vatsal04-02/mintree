"use client";
/** KPI chips (Rapport pattern): a row of figures, each with an optional icon circle, the value, an optional coloured pill beside it (green for good
 *  news, yellow for attention, orange for a problem, grey for neutral context) and a small caption underneath. Numbers count up on first paint.
 *  It is a labelled region holding a list, so assistive technology announces how many figures there are. */
import type { LucideIcon } from "lucide-react";
import { CountUp } from "./fx";

export type StripCard = { label: string; value: string; chip?: string; tone?: string; foot?: string; accent?: string; icon?: LucideIcon };

const PILL: Record<string, string> = { success: "pill-success", warning: "pill-warning", danger: "pill-danger" };

export function Strip({ cards, className = "mb-5", label = "Summary" }: { cards: StripCard[] | null; className?: string; label?: string }) {
  if (!cards) return <div className={`skeleton h-[52px] !rounded-full ${className}`} />;
  return (
    <section className={className} aria-label={label}>
      <ul className="flex flex-wrap gap-x-9 gap-y-3.5">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <li key={c.label} className="flex items-center gap-3 min-w-0" title={c.foot}>
              {Icon && <span className="kpi-ic" aria-hidden><Icon size={16} strokeWidth={1.5} /></span>}
              <div className="min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="t-kpi tabular-nums whitespace-nowrap"><CountUp text={c.value} /></span>
                  {c.chip && <span className={`pill ${PILL[c.tone ?? ""] ?? "pill-neutral"} truncate max-w-[12rem]`}>{c.chip}</span>}
                </div>
                <p className="text-[0.6875rem] leading-4 text-muted truncate mt-0.5 max-w-[16rem]">{c.label}{c.foot && !c.chip ? ` · ${c.foot}` : ""}</p>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
