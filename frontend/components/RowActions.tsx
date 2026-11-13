"use client";
/** Row actions for tables: at most one primary button, everything else (and every print/download link) in a "⋯" menu, so nothing is clipped at the table edge.
 *  The menu is drawn in a portal with fixed positioning, because the table scrolls and would cut off an ordinary dropdown. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal } from "lucide-react";

export type RowAction = { key: string; label: string; danger?: boolean; link?: boolean; secondary?: boolean; prefer?: boolean; run: () => void };

export function RowActions({ actions, subject }: { actions: RowAction[]; subject?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const wrap = useRef<HTMLSpanElement>(null);
  useEffect(() => {      // the menu lives in a portal, so "outside" means outside both the button and the menu
    if (!open) return;
    const h = (e: MouseEvent) => { const t = e.target as Node; if (!wrap.current?.contains(t) && !menu.current?.contains(t)) close(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open, close]);

  /** Sits under the button, or above it when there is no room below; follows the button while the page or the table scrolls, and closes once the button is out of view. */
  const place = useCallback(() => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    if (r.bottom < 0 || r.top > window.innerHeight) { close(); return; }
    const h = menu.current?.offsetHeight ?? 0;
    const below = r.bottom + 4, flip = h > 0 && below + h > window.innerHeight - 8 && r.top - 4 - h >= 8;
    setPos({ top: flip ? r.top - 4 - h : below, right: Math.max(8, window.innerWidth - r.right) });
  }, [close]);
  const mounted = open && pos !== null;
  useLayoutEffect(() => { if (open) place(); }, [open, mounted, place]);   // again once the menu exists, so its height is known for the flip
  useEffect(() => {
    if (!open) return;
    window.addEventListener("scroll", place, true); window.addEventListener("resize", place);
    return () => { window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); };
  }, [open, place]);
  useEffect(() => { if (mounted) menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus(); }, [mounted]);

  if (actions.length === 0) return null;
  // the one button shown is the next step for the record: a preferred action, else the first that is not a print link, a destructive action or a secondary choice
  const primary = actions.find((a) => a.prefer) ?? actions.find((a) => !a.link && !a.danger && !a.secondary) ?? null;
  const rest = actions.filter((a) => a !== primary);
  return (
    <span ref={wrap} className="inline-flex items-center justify-end gap-1 whitespace-nowrap">
      {primary && <button type="button" className={`btn btn-sm ${primary.danger ? "btn-danger" : ""}`} onClick={primary.run}>{primary.label}</button>}
      {rest.length > 0 && (
        <>
          <button ref={btn} type="button" className="icon-btn" aria-haspopup="menu" aria-expanded={open} aria-label={`More actions${subject ? ` for ${subject}` : ""}`} title="More actions" onClick={() => setOpen((o) => !o)}>
            <MoreHorizontal size={16} strokeWidth={1.5} />
          </button>
          {open && pos && typeof document !== "undefined" && createPortal(
            <div ref={menu} role="menu" style={{ position: "fixed", top: pos.top, right: pos.right }}
              onKeyDown={(e) => {
                const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("[role=menuitem]")); const i = items.indexOf(document.activeElement as HTMLElement);
                if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
                if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
                if (e.key === "Escape") { close(); btn.current?.focus(); }
              }}
              className="anim-fade z-60 min-w-44 panel !bg-surface shadow-xl p-1.5 text-left">
              {rest.map((a) => (
                <button key={a.key} type="button" role="menuitem" onClick={() => { close(); a.run(); }}
                  className={`w-full text-left rounded-lg px-2.5 py-1.5 text-[0.8125rem] hover:bg-surface-2 focus-visible:bg-surface-2 ${a.danger ? "text-danger" : ""}`}>{a.label}</button>
              ))}
            </div>, document.body)}
        </>
      )}
    </span>
  );
}
