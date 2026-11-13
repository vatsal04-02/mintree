"use client";
/** Rapport building blocks shared by the dashboards: the card header with its "⋯" and "↗" controls, the coloured record card (DealCard),
 *  the progress donut used for plan attainment, and the stage-mix bar. Colours come from the four record variants in globals.css. */
import Link from "next/link";
import { ArrowUpRight, MoreHorizontal } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import { CountUp } from "./fx";

export type Variant = "black" | "orange" | "green" | "yellow" | "white";

/** Card header: title left, the two round ghost controls right ("⋯" opens the source list, "↗" the full screen). */
export function CardHead({ title, meta, href, more, children }: { title: string; meta?: React.ReactNode; href?: string; more?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 px-[18px] pt-[18px] pb-2">
      <div className="min-w-0">
        <h2 className="text-[0.875rem] font-medium leading-5">{title}</h2>
        {meta && <p className="t-support mt-0.5">{meta}</p>}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {children}
        {more && <Link href={more} className="ctl" aria-label={`More about ${title}`}><MoreHorizontal size={16} strokeWidth={1.5} /></Link>}
        {href && <Link href={href} className="ctl" aria-label={`Open ${title}`}><ArrowUpRight size={16} strokeWidth={1.5} /></Link>}
      </div>
    </div>
  );
}

/** The coloured record card: top row meta + ghost "⋯", title and subtitle, then the figure with its caption at the bottom. */
export function DealCard({ variant, meta, metaTone, valueTone, title, subtitle, value, caption, href, index = 0, layoutId, onOpen, hidden }: { variant: Variant; meta?: string; metaTone?: "danger" | "warning" | "neutral"; valueTone?: string; title: string; subtitle?: string; value: string; caption?: string; href?: string; index?: number; layoutId?: string; onOpen?: () => void; hidden?: boolean }) {
  const reduce = useReducedMotion();
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        {metaTone ? <span className={`pill pill-${metaTone}`}>{meta}</span> : <span className="meta truncate">{meta}</span>}
        <span className="ghost shrink-0" aria-hidden><MoreHorizontal size={14} strokeWidth={1.5} /></span>
      </div>
      <h3 className="mt-3 text-[1.0625rem] leading-[1.35rem] font-medium line-clamp-2">{title}</h3>
      {subtitle && <p className="meta mt-0.5 truncate">{subtitle}</p>}
      <div className="mt-auto pt-3">
        <p className="text-[1.375rem] leading-7 font-medium tabular-nums" style={valueTone ? { color: valueTone } : undefined}><CountUp text={value} /></p>
        {caption && <p className="text-[0.6875rem] opacity-70 truncate">{caption}</p>}
      </div>
    </>
  );
  const anim = reduce ? {} : { initial: { opacity: 0, y: 14, filter: "blur(8px)" }, animate: { opacity: 1, y: 0, filter: "blur(0px)" }, transition: { duration: 0.45, delay: index * 0.055, ease: [0.22, 1, 0.36, 1] as const } };
  /* With onOpen the whole card is a button that opens the record modal (RecordModal); `layoutId` lets the modal grow out of this exact rectangle.
     While its modal is open the card is hidden (not removed), so the grid keeps its shape. */
  if (onOpen) {
    return (
      <motion.div {...anim} layoutId={layoutId} role="button" tabIndex={0} aria-haspopup="dialog" aria-label={`${title}: ${value}${caption ? `, ${caption}` : ""}. Open details`}
        onClick={onOpen} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
        className={`deal deal-${variant}`} style={{ borderRadius: 22, visibility: hidden ? "hidden" : "visible" }}>{body}</motion.div>
    );
  }
  return href
    ? <motion.div {...anim}><Link href={href} className={`deal deal-${variant}`} aria-label={`${title}: ${value}${caption ? `, ${caption}` : ""}`}>{body}</Link></motion.div>
    : <motion.div {...anim} className={`deal deal-${variant}`}>{body}</motion.div>;
}

/** Progress donut: a yellow arc on a translucent track with the percentage in the middle. */
export function Donut({ pct, label, size = 150, tone = "dark" }: { pct: number; label: string; size?: number; tone?: "dark" | "light" }) {
  /* The arc is shortened by the stroke width so its rounded ends never meet, and started half a stroke past twelve o'clock so the gap
     that is left is exactly the unfinished share; the track stays visible behind it. */
  const reduce = useReducedMotion();
  const r = 42, sw = 8, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, pct));
  const arc = p <= 0 ? 0 : Math.max(0.01, (c * p) / 100 - (p >= 100 ? 0 : sw));
  const start = p >= 100 ? -90 : -90 + ((sw / 2) / c) * 360;
  return (
    <div className="relative mx-auto" style={{ width: size, height: size }} role="img" aria-label={`${Math.round(p)}% ${label}`}>
      <svg viewBox="0 0 100 100" className="w-full h-full" aria-hidden>
        <circle cx="50" cy="50" r={r} fill="none" stroke={tone === "light" ? "var(--chart-track)" : "rgb(255 255 255 / 0.16)"} strokeWidth={sw} />
        <motion.circle cx="50" cy="50" r={r} fill="none" stroke={tone === "light" ? "var(--acc-green)" : "#efc84b"} strokeWidth={sw} strokeLinecap={p >= 100 ? "butt" : "round"} transform={`rotate(${start} 50 50)`}
          initial={{ strokeDasharray: `${reduce ? arc : 0} ${c}` }} animate={{ strokeDasharray: `${arc} ${c}` }} transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }} />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div><p className="text-[1.75rem] leading-none font-medium tabular-nums"><CountUp text={`${Math.round(p)}%`} /></p><p className="text-[0.75rem] font-normal opacity-70 mt-1.5">{label}</p></div>
      </div>
    </div>
  );
}

/** Stage mix: one row of rounded segments sized by share, then a legend with count and share. */
const SEG = ["#efc84b", "#2b5c4e", "#c6431d", "var(--ink-r)", "var(--faint)", "rgb(43 92 78 / 0.5)"];
/** An item may carry its own colour (a status coloured by meaning); the rest take the palette in order. */
export function StageMix({ items }: { items: { label: string; value: number; href?: string; color?: string }[] }) {
  const col = (i: { color?: string }, k: number) => i.color ?? SEG[k % SEG.length];
  const total = items.reduce((a, i) => a + i.value, 0) || 1;
  const shown = items.filter((i) => i.value > 0);
  return (
    <div className="px-[18px] pb-[18px]">
      <div className="flex gap-1.5 h-11" aria-hidden>
        {shown.map((i) => <span key={i.label} className="rounded-[14px] min-w-[8px] bar-grow-x" style={{ flex: i.value, background: col(i, items.indexOf(i)) }} />)}
      </div>
      <ul className="mt-4 divide-y divide-line">
        {items.map((i, k) => (
          <li key={i.label}>
            <Link href={i.href ?? "#"} className="flex items-center justify-between gap-3 py-2 text-[0.8125rem] hover:opacity-80">
              <span className="inline-flex items-center gap-2 min-w-0"><span className="w-2 h-2 rounded-full shrink-0" style={{ background: col(i, k) }} aria-hidden /><span className="truncate">{i.label}</span></span>
              <span className="tabular-nums shrink-0"><span className="text-muted mr-3">{i.value > 0 && Math.round((i.value / total) * 100) === 0 ? "<1" : Math.round((i.value / total) * 100)}%</span>{i.value}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
