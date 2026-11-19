"use client";
/** The shared UI pieces every screen uses instead of its own version (styles in app/system.css):
 *  Pill: the one status pill (five tones, tinted ground, darker text of the same hue).
 *  IconButton: a round icon-only control that always has a tooltip and an accessible name; a link when given `href`.
 *  SectionHead: a heading and subtitle for a group of cards that sits on the page ground (a section is never itself a card). */
import Link from "next/link";
import { forwardRef } from "react";

export type PillTone = "neutral" | "success" | "warning" | "danger" | "info";

export function Pill({ tone = "neutral", children, className = "", title }: { tone?: PillTone; children: React.ReactNode; className?: string; title?: string }) {
  return <span className={`pill pill-${tone} ${className}`} title={title}>{children}</span>;
}

type IconButtonProps = { label: string; href?: string; onClick?: () => void; children: React.ReactNode; className?: string; tooltip?: string } & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onClick" | "children">;
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton({ label, href, onClick, children, className = "", tooltip, ...rest }, ref) {
  if (href) return <Link href={href} className={`icon-btn ${className}`} aria-label={label} title={tooltip ?? label}>{children}</Link>;
  return <button ref={ref} type="button" className={`icon-btn ${className}`} aria-label={label} title={tooltip ?? label} onClick={onClick} {...rest}>{children}</button>;
});

export function SectionHead({ title, subtitle, action, id }: { title: string; subtitle?: React.ReactNode; action?: React.ReactNode; id?: string }) {
  return (
    <div className="section-head">
      <div className="min-w-0"><h2 id={id}>{title}</h2>{subtitle && <p className="t-support mt-0.5">{subtitle}</p>}</div>
      {action && <div className="flex items-center gap-2 shrink-0">{action}</div>}
    </div>
  );
}
