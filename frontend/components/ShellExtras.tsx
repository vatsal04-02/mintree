"use client";
/** Shell pieces that are not the sidebar or the top bar: the keyboard-shortcut sheet with "What's new" (phase 2.7), and the phone bottom bar with its
 *  "More" sheet (phase 5.4). */
import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, Inbox, LayoutGrid, MoreHorizontal, ScanLine, X } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import { runShell } from "@/lib/actions";
import { WHATS_NEW } from "@/lib/whatsnew";
import { fmtDate } from "@/lib/format";
import { useTopLayer } from "./ui";
import { usePinned } from "./Dock";

const KEYS: { group: string; rows: [string[], string][] }[] = [
  { group: "Anywhere", rows: [[["Ctrl", "K"], "Search pages, actions and records"], [["Alt", "S"], "Scan a barcode"], [["Ctrl", "J"], "Open the assistant"], [["?"], "Show this sheet"], [["Esc"], "Close the top drawer, dialog or menu"], [["G", "then O"], "Go to Overview"], [["G", "then A"], "Go to Approvals"], [["G", "then N"], "Go to Notifications"]] },
  { group: "In a list", rows: [[["J"], "Move down a row"], [["K"], "Move up a row"], [["Enter"], "Open the highlighted row"], [["X"], "Select or clear the highlighted row"]] },
  { group: "In an open record", rows: [[["J"], "Next record"], [["K"], "Previous record"], [["↓"], "Next record"], [["↑"], "Previous record"]] },
];

export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<"keys" | "new">("keys");
  useTopLayer(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[70] grid place-items-center p-4">
      <div className="absolute inset-0 anim-fade" onClick={onClose} aria-hidden style={{ background: "rgb(21 23 23 / 0.32)", backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)" }} />
      <div role="dialog" aria-modal="true" aria-label="Keyboard shortcuts and what's new" className="anim-fade relative panel w-full max-w-lg max-h-[84vh] overflow-hidden flex flex-col shadow-2xl">
        <div className="flex items-center justify-between gap-3 px-5 pt-5 pb-3">
          <div className="seg" role="group" aria-label="Sheet">
            <button type="button" aria-pressed={tab === "keys"} onClick={() => setTab("keys")}>Shortcuts</button>
            <button type="button" aria-pressed={tab === "new"} onClick={() => setTab("new")}>What&apos;s new</button>
          </div>
          <button type="button" className="ctl" onClick={onClose} aria-label="Close"><X size={16} strokeWidth={1.5} /></button>
        </div>
        <div className="overflow-y-auto px-5 pb-5">
          {tab === "keys" ? KEYS.map((g) => (
            <section key={g.group} className="mb-4 last:mb-0">
              <h2 className="t-support mb-1">{g.group}</h2>
              <dl>{g.rows.map(([k, label]) => (
                <div key={k.join("+") + label} className="flex items-center justify-between gap-4 py-2 border-t border-line">
                  <dt className="text-[0.8125rem]">{label}</dt>
                  <dd className="flex gap-1 shrink-0">{k.map((x) => <kbd key={x} className="text-[0.6875rem] min-w-[1.5rem] text-center border border-line-strong rounded-md px-1.5 py-0.5 bg-inset">{x}</kbd>)}</dd>
                </div>
              ))}</dl>
            </section>
          )) : WHATS_NEW.map((r) => (
            <section key={r.title} className="mb-5 last:mb-0">
              <p className="t-support">{fmtDate(r.date)}</p>
              <h2 className="text-[0.9375rem] font-medium mt-0.5">{r.title}</h2>
              <ul className="mt-2 space-y-1.5 text-[0.8125rem] list-disc pl-4 marker:text-muted">{r.items.map((i) => <li key={i}>{i}</li>)}</ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Phones only: five places that matter most, with the scanner in the middle. "More" opens the pinned quick actions and the full navigation. */
export function MobileBar({ onNav }: { onNav: () => void }) {
  const path = usePathname();
  const [more, setMore] = useState(false);
  const reduce = useReducedMotion();
  const { pinned } = usePinned();
  useTopLayer(more, () => setMore(false));
  const tab = (href: string, label: string, Icon: typeof Bell) => {
    const on = href === "/" ? path === "/" : path.startsWith(href);
    return <Link href={href} aria-current={on ? "page" : undefined} className="flex-1 flex flex-col items-center justify-center gap-0.5 min-h-[56px] text-[0.625rem]" style={{ color: on ? "var(--fg)" : "var(--muted)" }}><Icon size={20} strokeWidth={1.5} aria-hidden />{label}</Link>;
  };
  return (
    <>
      <nav aria-label="Main" className="md:hidden fixed bottom-0 inset-x-0 z-40 flex items-stretch px-2 pb-[env(safe-area-inset-bottom)]" style={{ background: "rgb(246 244 239 / 0.92)", backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)", borderTop: "1px solid var(--line)" }}>
        {tab("/", "Overview", LayoutGrid)}
        {tab("/approvals", "Approvals", Inbox)}
        <button type="button" onClick={() => runShell("scan")} aria-label="Scan a barcode" className="flex-1 flex flex-col items-center justify-center gap-0.5 min-h-[56px] text-[0.625rem]" style={{ color: "var(--fg)" }}>
          <span className="grid place-items-center w-11 h-11 -mt-5 rounded-full" style={{ background: "var(--ink-r)", color: "var(--card)", boxShadow: "0 10px 24px -10px rgb(21 23 23 / 0.6)" }}><ScanLine size={20} strokeWidth={1.5} aria-hidden /></span>Scan
        </button>
        {tab("/notifications", "Alerts", Bell)}
        <button type="button" onClick={() => setMore(true)} aria-haspopup="dialog" className="flex-1 flex flex-col items-center justify-center gap-0.5 min-h-[56px] text-[0.625rem]" style={{ color: "var(--muted)" }}><MoreHorizontal size={20} strokeWidth={1.5} aria-hidden />More</button>
      </nav>
      <AnimatePresence>
        {more && (
          <div className="md:hidden fixed inset-0 z-[55]">
            <motion.div className="absolute inset-0" onClick={() => setMore(false)} aria-hidden initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} style={{ background: "rgb(21 23 23 / 0.32)", backdropFilter: "blur(10px)" }} />
            <motion.div role="dialog" aria-modal="true" aria-label="More" className="absolute bottom-0 inset-x-0 rounded-t-[28px] p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))]" style={{ background: "var(--card)" }}
              initial={reduce ? false : { y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }} transition={{ type: "spring", stiffness: 380, damping: 36 }}>
              <div className="flex items-center justify-between mb-3"><h2 className="text-[1rem] font-medium">Quick actions</h2><button type="button" className="ctl" onClick={() => setMore(false)} aria-label="Close"><X size={16} strokeWidth={1.5} /></button></div>
              <ul className="grid grid-cols-3 gap-2">
                {pinned.map((a) => (
                  <li key={a.id}>
                    {a.href
                      ? <Link href={a.href} onClick={() => setMore(false)} className="flex flex-col items-center gap-1.5 rounded-2xl py-3 px-1 text-center text-[0.75rem] min-h-[84px] justify-center" style={{ background: "var(--inset)" }}><a.icon size={20} strokeWidth={1.5} aria-hidden />{a.label}</Link>
                      : <button type="button" onClick={() => { setMore(false); if (a.run) runShell(a.run); }} className="w-full flex flex-col items-center gap-1.5 rounded-2xl py-3 px-1 text-center text-[0.75rem] min-h-[84px] justify-center" style={{ background: "var(--inset)" }}><a.icon size={20} strokeWidth={1.5} aria-hidden />{a.label}</button>}
                  </li>
                ))}
              </ul>
              <button type="button" className="btn w-full mt-4" onClick={() => { setMore(false); onNav(); }}>All screens</button>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </>
  );
}
