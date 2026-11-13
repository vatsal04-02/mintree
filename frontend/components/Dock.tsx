"use client";
/** Quick-action dock (phase 2.1): a slim ink capsule at the left edge of the content area holding the actions you pinned. Pins come from the registry in
 *  lib/actions.ts, only the ones you hold the permission for, and are remembered per user in this browser. On phones the same actions live in the
 *  "More" sheet of the bottom bar (MobileBar), so nothing sits over the content. */
import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, Pencil } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import { useAuth } from "@/lib/auth";
import { usePref } from "@/lib/prefs";
import { ACTIONS, DEFAULT_PINS, MAX_PINS, runShell, type QuickAction } from "@/lib/actions";

/** The actions this person may use, in the order they pinned them. */
export function usePinned() {
  const { can } = useAuth();
  const [pins, setPins] = usePref<string[]>("dock", DEFAULT_PINS);
  const allowed = ACTIONS.filter((a) => !a.perm || can(a.perm));
  const pinned = pins.map((id) => allowed.find((a) => a.id === id)).filter((a): a is QuickAction => !!a).slice(0, MAX_PINS);
  return { pinned, allowed, pins, setPins };
}

function Item({ a, tip }: { a: QuickAction; tip: "right" | "up" }) {
  const router = useRouter();
  const reduce = useReducedMotion();
  const body = (
    <motion.span whileHover={reduce ? undefined : { scale: 1.15 }} transition={{ duration: 0.12 }} className="grid place-items-center w-10 h-10 rounded-full text-white/85 hover:bg-white/10 hover:text-white">
      <a.icon size={18} strokeWidth={1.5} aria-hidden />
    </motion.span>
  );
  const tipCls = tip === "right" ? "left-full ml-3 top-1/2 -translate-y-1/2" : "bottom-full mb-2 left-1/2 -translate-x-1/2";
  return (
    <li className="relative group">
      {a.href
        ? <Link href={a.href} aria-label={a.label} className="block rounded-full focus-visible:outline-2 focus-visible:outline-white">{body}</Link>
        : <button type="button" aria-label={a.label} className="block rounded-full" onClick={() => (a.run ? runShell(a.run) : a.href && router.push(a.href))}>{body}</button>}
      <span role="tooltip" className={`pointer-events-none absolute z-30 ${tipCls} whitespace-nowrap rounded-full px-3 py-1.5 text-[0.75rem] opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity`} style={{ background: "var(--ink-r)", color: "var(--card)" }}>
        {a.label}{a.shortcut && <kbd className="ml-2 opacity-60">{a.shortcut}</kbd>}
      </span>
    </li>
  );
}

export function Dock() {
  const { pinned, allowed, pins, setPins } = usePinned();
  const [edit, setEdit] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const toggle = (id: string) => setPins((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < MAX_PINS ? [...p, id] : p));
  return (
    <div ref={box} className="hidden xl:block absolute left-3 top-1/2 -translate-y-1/2 z-20">
      <nav aria-label="Quick actions" className="w-[54px] rounded-full py-2 flex flex-col items-center gap-1" style={{ background: "var(--ink-r)", boxShadow: "0 24px 50px -20px rgb(21 23 23 / 0.55)" }}>
        <ul className="flex flex-col items-center gap-1">{pinned.map((a) => <Item key={a.id} a={a} tip="right" />)}</ul>
        <button type="button" onClick={() => setEdit((v) => !v)} aria-expanded={edit} aria-label={edit ? "Done choosing quick actions" : "Choose quick actions"} title="Choose quick actions"
          className="mt-1 grid place-items-center w-8 h-8 rounded-full text-white/55 hover:text-white hover:bg-white/10">{edit ? <Check size={14} strokeWidth={1.5} /> : <Pencil size={14} strokeWidth={1.5} />}</button>
      </nav>
      {edit && (
        <div role="dialog" aria-label="Choose quick actions" className="anim-fade absolute left-[66px] top-1/2 -translate-y-1/2 w-64 panel p-3 shadow-xl">
          <p className="text-[0.8125rem] font-medium">Quick actions</p>
          <p className="t-support mb-2">Pick up to {MAX_PINS}. Only actions you may use are listed.</p>
          <ul className="max-h-72 overflow-y-auto -mx-1">
            {allowed.map((a) => {
              const on = pins.includes(a.id);
              return (
                <li key={a.id}>
                  <button type="button" role="checkbox" aria-checked={on} onClick={() => toggle(a.id)} disabled={!on && pins.length >= MAX_PINS}
                    className="w-full flex items-center gap-2.5 px-2 h-9 rounded-xl text-[0.8125rem] text-left hover:bg-inset disabled:opacity-40">
                    <a.icon size={15} strokeWidth={1.5} aria-hidden /><span className="flex-1 truncate">{a.label}</span>
                    <span className="grid place-items-center w-4 h-4 rounded-full border border-line-strong" style={on ? { background: "var(--ink-r)", color: "var(--card)", borderColor: "transparent" } : undefined}>{on && <Check size={10} strokeWidth={2} />}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
