"use client";
/** Record modal: the card you click grows into a centred modal in its own colour (shared-layout morph, ~450ms spring), the page behind blurs and
 *  dims, the content fades in 150ms later, and closing folds it back into the card. Only sections you pass are drawn, so a record with no
 *  stage track or no tiles simply has none; nothing is filled in for you.
 *
 *  How to use
 *    const [open, setOpen] = useState<RecordView | null>(null);
 *    <DealCard layoutId={`rec-${id}`} onOpen={() => setOpen(view)} ... />      // or any element with layoutId={recordLayoutId(id)}
 *    <RecordModal record={open} onClose={() => setOpen(null)} />
 *  Escape closes only the topmost layer, Tab stays inside the modal, and focus returns to the card. */
import { useEffect, useRef } from "react";
import Link from "next/link";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "framer-motion";
import { X } from "lucide-react";
import { useTopLayer } from "./ui";
import { EASE, SPRING } from "./motion";
import type { Variant } from "./rapport";

export const recordLayoutId = (id: string | number) => `rec-${id}`;

export type RecordView = {
  id: string | number; variant: Variant; kicker?: string; kickerTone?: "danger" | "warning" | "neutral"; title: string; subtitle?: string;
  figure?: string; figureTone?: string; figureCaption?: string;
  steps?: { label: string; state: "done" | "current" | "todo" }[];
  tiles?: { label: string; value: string }[];
  people?: { name: string }[]; note?: string;
  openHref?: string; openLabel?: string; primary?: { label: string; onClick: () => void; href?: string };
};

const initials = (n: string) => n.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();

export function RecordModal({ record, onClose }: { record: RecordView | null; onClose: () => void }) {
  const reduce = useReducedMotion();
  const box = useRef<HTMLDivElement>(null);
  useTopLayer(!!record, onClose);
  useEffect(() => { if (record) box.current?.focus(); }, [record]);
  // keep Tab inside the modal while it is open
  function trap(e: React.KeyboardEvent) {
    if (e.key !== "Tab" || !box.current) return;
    const f = [...box.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")];
    if (f.length === 0) { e.preventDefault(); return; }
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  const dark = (v: Variant) => v !== "white" && v !== "yellow";
  return (
    <LayoutGroup>
      <AnimatePresence>
        {record && (
          <div className="fixed inset-0 z-[65] grid place-items-center p-4">
            <motion.div className="absolute inset-0" aria-hidden onClick={onClose}
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}
              style={{ background: "rgb(21 23 23 / 0.32)", backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)" }} />
            <motion.div ref={box} layoutId={recordLayoutId(record.id)} role="dialog" aria-modal="true" aria-label={record.title} tabIndex={-1} onKeyDown={trap}
              transition={reduce ? { duration: 0 } : { layout: SPRING, duration: 0.45 }}
              className={`deal deal-${record.variant} deal-flat relative w-full max-w-[480px] !min-h-0 outline-none focus-visible:!outline-none`}
              style={{ borderRadius: 28, padding: 24, boxShadow: "0 40px 90px -30px rgb(21 23 23 / 0.55)" }}>
              <motion.div initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.1 } }} transition={{ duration: 0.3, delay: reduce ? 0 : 0.15, ease: EASE }}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    {record.kicker && (record.kickerTone ? <span className={`pill pill-${record.kickerTone}`}>{record.kicker}</span> : <p className="meta text-[0.75rem] opacity-70 font-mono">{record.kicker}</p>)}
                    <h2 className="mt-2 text-[1.5rem] leading-[1.75rem] font-medium tracking-[-0.02em]">{record.title}</h2>
                    {record.subtitle && <p className="mt-1 text-[0.8125rem] opacity-70">{record.subtitle}</p>}
                  </div>
                  <button type="button" className="ghost !w-9 !h-9 shrink-0 rounded-full grid place-items-center" onClick={onClose} aria-label="Close"
                    style={{ border: "1px solid color-mix(in srgb, currentColor 30%, transparent)" }}><X size={16} strokeWidth={1.5} /></button>
                </div>

                {record.figure && (
                  <div className="mt-6">
                    <p className="text-[2.25rem] leading-[2.5rem] font-medium tabular-nums tracking-[-0.02em]" style={record.figureTone ? { color: record.figureTone } : undefined}>{record.figure}</p>
                    {record.figureCaption && <p className="text-[0.75rem] opacity-70 mt-1">{record.figureCaption}</p>}
                  </div>
                )}

                {record.steps && record.steps.length > 0 && (
                  <ol className="mt-6 flex items-center" aria-label="Stage">
                    {record.steps.map((s, i) => (
                      <li key={s.label} className="flex items-center flex-1 last:flex-none" aria-current={s.state === "current" ? "step" : undefined}>
                        <span className="flex flex-col items-center gap-1.5">
                          <span className="w-3 h-3 rounded-full" style={{ background: s.state === "todo" ? "transparent" : "currentColor", border: "1.5px solid currentColor", opacity: s.state === "todo" ? 0.4 : 1, boxShadow: s.state === "current" ? "0 0 0 4px color-mix(in srgb, currentColor 25%, transparent)" : "none" }} />
                          <span className={`text-[0.625rem] leading-3 ${s.state === "current" ? "" : "opacity-65"}`}>{s.label}</span>
                        </span>
                        {i < record.steps!.length - 1 && <span className="flex-1 h-px mx-1.5 mb-4" style={{ background: "currentColor", opacity: s.state === "done" ? 0.7 : 0.25 }} />}
                      </li>
                    ))}
                  </ol>
                )}

                {record.tiles && record.tiles.length > 0 && (
                  <dl className="mt-6 grid gap-px rounded-2xl overflow-hidden" style={{ gridTemplateColumns: `repeat(${record.tiles.length}, minmax(0, 1fr))`, background: "color-mix(in srgb, currentColor 16%, transparent)" }}>
                    {record.tiles.map((t) => (
                      <div key={t.label} className="px-3 py-3" style={{ background: "color-mix(in srgb, currentColor 8%, transparent)" }}>
                        <dt className="text-[0.6875rem] opacity-65">{t.label}</dt>
                        <dd className="text-[0.9375rem] font-medium tabular-nums mt-0.5">{t.value}</dd>
                      </div>
                    ))}
                  </dl>
                )}

                {record.people && record.people.length > 0 && (
                  <div className="mt-5 flex -space-x-2" aria-label={record.people.map((p) => p.name).join(", ")}>
                    {record.people.map((p) => <span key={p.name} title={p.name} className="w-8 h-8 rounded-full grid place-items-center text-[0.6875rem] ring-2" style={{ background: "rgb(255 255 255 / 0.85)", color: "#151717", ["--tw-ring-color" as string]: "rgb(21 23 23 / 0.1)" }}>{initials(p.name)}</span>)}
                  </div>
                )}
                {record.note && <p className="mt-5 text-[0.8125rem] leading-5 opacity-80">{record.note}</p>}

                {(record.openHref || record.primary) && (
                  <div className="mt-7 flex flex-wrap gap-2">
                    {record.openHref && <Link href={record.openHref} className="btn" style={dark(record.variant) ? { background: "transparent", borderColor: "rgb(255 255 255 / 0.4)", color: "inherit" } : undefined}>{record.openLabel ?? "Open"}</Link>}
                    {record.primary && (record.primary.href
                      ? <Link href={record.primary.href} className="btn btn-primary" style={dark(record.variant) ? { background: "#fff", borderColor: "#fff", color: "#151717" } : undefined}>{record.primary.label}</Link>
                      : <button type="button" className="btn btn-primary" onClick={record.primary.onClick} style={dark(record.variant) ? { background: "#fff", borderColor: "#fff", color: "#151717" } : undefined}>{record.primary.label}</button>)}
                  </div>
                )}
              </motion.div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </LayoutGroup>
  );
}
