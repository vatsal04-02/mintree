"use client";
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, X } from "lucide-react";

type Tone = "success" | "warning" | "danger" | "info" | "neutral";
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

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="py-14 text-center">
      <div className="font-medium">{title}</div>
      {hint && <div className="text-muted text-[0.75rem] mt-1">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* ---------- Toasts ---------- */
type Toast = { id: number; kind: "success" | "error"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 4000);
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
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------- Drawer / Modal ---------- */
const drawerStack: string[] = [];   // only the topmost open drawer reacts to Escape (a scan dialog inside a counting drawer must not close both)

export function Drawer({ open, onClose, title, subtitle, children, footer, width = 520 }: {
  open: boolean; onClose: () => void; title: string; subtitle?: string; children: React.ReactNode; footer?: React.ReactNode; width?: number;
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
        className="anim-slide relative h-full flex flex-col w-full sm:w-[92%] outline-none sm:rounded-l-[28px] overflow-hidden"
        style={{ maxWidth: width, background: "rgb(246 244 239 / 0.94)", backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)", boxShadow: "-30px 0 70px -30px rgb(70 45 20 / 0.35)" }}>
        <div className="flex items-start justify-between gap-3 px-6 pt-6 pb-3">
          <div className="min-w-0">
            <h2 id={id} className="text-[1.5rem] leading-7 font-medium tracking-[-0.02em]">{title}</h2>
            {subtitle && <p className="text-muted text-[0.75rem] mt-1">{subtitle}</p>}
          </div>
          <button className="ctl shrink-0" onClick={onClose} aria-label="Close"><X size={16} strokeWidth={1.5} /></button>
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

export function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
