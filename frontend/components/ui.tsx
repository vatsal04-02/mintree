"use client";
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";

type Tone = "success" | "warning" | "danger" | "info" | "neutral" | "sage" | "peach" | "solid";
export function Badge({ tone = "neutral", children, live }: { tone?: Tone; children: React.ReactNode; live?: boolean }) {
  return <span className={`badge tone-${tone} ${live ? "badge-live" : ""}`}>{children}</span>;
}

export function PageHeader({ title, description, actions, eyebrow, tag, aside }: { title: string; description?: string; actions?: React.ReactNode; eyebrow?: string; tag?: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4 mb-6">
      <div className="min-w-0">
        <p className="eyebrow">{eyebrow ?? "Factory Plant 01 · Mintree Factory OS"}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5"><h1 className="t-page">{title}</h1>{tag}</div>
        {description && <p className="t-label mt-1.5 max-w-2xl">{description}</p>}
      </div>
      {(aside || actions) && <div className="flex flex-wrap items-center gap-2.5">{aside}{actions}</div>}
    </div>
  );
}

/** One-colour line drawings for empty states: they take the muted text colour, so they sit quietly in both appearances. */
const ART: Record<string, React.ReactNode> = {
  tray: <><path d="M10 34 18 14h28l8 20v14a3 3 0 0 1-3 3H13a3 3 0 0 1-3-3V34Z" /><path d="M10 34h13a2 2 0 0 1 2 2 5 5 0 0 0 5 5h4a5 5 0 0 0 5-5 2 2 0 0 1 2-2h13" /><path d="M26 8v-2m12 2v-2M32 8V4" /></>,
  check: <><circle cx="32" cy="32" r="20" /><path d="m22 33 7 7 14-15" /><path d="M32 6v4m22 22h4M6 32h4" /></>,
  search: <><circle cx="28" cy="28" r="15" /><path d="m39 39 15 15" /><path d="M21 28h14" /></>,
};
export function EmptyState({ title, hint, action, art = "tray" }: { title: string; hint?: string; action?: React.ReactNode; art?: "tray" | "check" | "search" }) {
  return (
    <div className="py-14 text-center">
      <svg viewBox="0 0 64 64" width="64" height="64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="mx-auto mb-3 text-muted opacity-70" aria-hidden>{ART[art]}</svg>
      <div className="font-medium">{title}</div>
      {hint && <div className="text-muted text-[0.75rem] mt-1">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* ---------- Toasts ---------- */
type Toast = { id: number; kind: "success" | "error"; text: string; undo?: () => void };
type ToastOpts = { undo?: () => void; ms?: number };
const ToastCtx = createContext<(kind: Toast["kind"], text: string, opts?: ToastOpts) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string, opts?: ToastOpts) => {
    const id = Date.now() + Math.random();
    const drop = () => setToasts((t) => t.filter((x) => x.id !== id));
    setToasts((t) => [...t, { id, kind, text, undo: opts?.undo ? () => { opts.undo!(); drop(); } : undefined }]);
    setTimeout(drop, opts?.ms ?? (kind === "error" ? 6000 : 4000));
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {/* black capsule, bottom centre, as in the reference; errors keep an orange mark and stay a little longer */}
      <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-[80] flex flex-col items-center gap-2 w-max max-w-[calc(100vw-32px)]" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="anim-slide flex items-center gap-2.5 min-h-[34px] pl-3 pr-4 py-1.5 rounded-full text-[0.75rem] text-white shadow-[0_18px_40px_-14px_rgb(21_23_23/0.55)]" style={{ background: "#151717" }}>
            {t.kind === "success" ? <CheckCircle2 size={15} strokeWidth={1.5} className="shrink-0 text-[#efc84b]" /> : <AlertCircle size={15} strokeWidth={1.5} className="shrink-0 text-[#f08a65]" />}
            <span>{t.text}</span>
            {t.undo && <button type="button" onClick={t.undo} className="pill pill-warning ml-1 !min-h-[1.5rem] !px-2.5 hover:opacity-90">Undo</button>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------- Drawer / Modal ---------- */
const drawerStack: string[] = [];   // only the topmost open drawer reacts to Escape (a scan dialog inside a counting drawer must not close both)

/** Registers an open layer (drawer, modal) on the shared stack and calls `onEscape` only while it is the topmost one. Focus returns to what had it before. */
export function useTopLayer(open: boolean, onEscape: () => void) {
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    drawerStack.push(id);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && drawerStack[drawerStack.length - 1] === id) { e.stopPropagation(); onEscape(); } };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); const i = drawerStack.lastIndexOf(id); if (i >= 0) drawerStack.splice(i, 1); prev?.focus?.(); };
  }, [open, id, onEscape]);
}

export function Drawer({ open, onClose, title, subtitle, children, footer, width = 520, headerExtra }: {
  open: boolean; onClose: () => void; title: string; subtitle?: string; children: React.ReactNode; footer?: React.ReactNode; width?: number; headerExtra?: React.ReactNode;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    drawerStack.push(id);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && drawerStack[drawerStack.length - 1] === id) onClose(); };
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => { document.removeEventListener("keydown", onKey); const i = drawerStack.lastIndexOf(id); if (i >= 0) drawerStack.splice(i, 1); prev?.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* frosted drawer over a blurred, dimmed page (Rapport pattern 08) */}
      <div className="absolute inset-0 anim-fade" style={{ background: "rgb(21 23 23 / 0.18)", backdropFilter: "blur(18px)", WebkitBackdropFilter: "blur(18px)" }} onClick={onClose} aria-hidden />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={id}
        className="drawer-panel anim-slide relative h-full flex flex-col w-full sm:w-[92%] outline-none sm:rounded-l-[28px] overflow-hidden"
        style={{ maxWidth: width }}>
        <div className="flex items-start justify-between gap-3 px-6 pt-6 pb-3">
          <div className="min-w-0">
            <h2 id={id} className="text-[1.5rem] leading-7 font-medium tracking-[-0.02em]">{title}</h2>
            {subtitle && <p className="text-muted text-[0.75rem] mt-1">{subtitle}</p>}
          </div>
          <div className="flex items-center gap-2 shrink-0">{headerExtra}<button className="ctl shrink-0" onClick={onClose} aria-label="Close"><X size={16} strokeWidth={1.5} /></button></div>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-3">{children}</div>
        {footer && <div className="px-6 py-4 flex items-center justify-end gap-2 border-t border-line">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ open, title, body, confirmLabel, danger, busy, onConfirm, onCancel }: {
  open: boolean; title: string; body: React.ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[70] grid place-items-center p-4">
      <div className="absolute inset-0 bg-black/40 anim-fade" onClick={onCancel} />
      <div role="alertdialog" aria-modal="true" className="anim-fade relative panel w-full max-w-sm p-5 shadow-2xl">
        <h2 className="t-section">{title}</h2>
        <div className="text-muted mt-1.5 text-[0.8125rem]">{body}</div>
        <div className="flex justify-end gap-2 mt-5">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className={`btn ${danger ? "btn-primary !bg-danger !border-danger" : "btn-primary"}`} onClick={onConfirm} disabled={busy}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

/** Do something now on screen, but only tell the server after a short grace period, during which "Undo" cancels it. Because nothing has been sent yet,
 *  undoing needs no compensating call. If the server then refuses, the screen is put back and the reason is shown. Pending actions are sent at once when
 *  the tab is hidden or closed, so a decision is never lost. */
const pendingCommits = new Set<() => void>();
if (typeof window !== "undefined") {
  const flush = () => { [...pendingCommits].forEach((f) => f()); };
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", flush);
}
export function useUndoable() {
  const toast = useToast();
  return useCallback(({ message, apply, revert, commit, ms = 4000 }: { message: string; apply: () => void; revert: () => void; commit: () => Promise<unknown>; ms?: number }) => {
    apply();
    let settled = false;
    const send = async () => {
      if (settled) return; settled = true; pendingCommits.delete(send); clearTimeout(timer);
      try { await commit(); } catch (e) { revert(); toast("error", e instanceof Error ? e.message : "Could not save the change"); }
    };
    const timer = setTimeout(send, ms);
    pendingCommits.add(send);
    toast("success", message, { ms, undo: () => { if (settled) return; settled = true; pendingCommits.delete(send); clearTimeout(timer); revert(); } });
  }, [toast]);
}

export function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
