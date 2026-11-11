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
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[80] flex flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="anim-slide panel shadow-lg flex items-start gap-2.5 px-3.5 py-2.5 max-w-sm text-[0.8125rem]">
            {t.kind === "success" ? <CheckCircle2 size={16} className="text-success mt-0.5 shrink-0" /> : <AlertCircle size={16} className="text-danger mt-0.5 shrink-0" />}
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
      <div className="absolute inset-0 bg-black/35 anim-fade" onClick={onClose} aria-hidden />
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={id}
        className="anim-slide relative bg-surface border-l border-line h-full flex flex-col w-full outline-none shadow-2xl" style={{ maxWidth: width }}>
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-line">
          <div className="min-w-0">
            <h2 id={id} className="t-section">{title}</h2>
            {subtitle && <p className="text-muted text-[0.75rem] mt-0.5">{subtitle}</p>}
          </div>
          <button className="btn btn-ghost btn-sm !px-1.5" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-line flex items-center justify-end gap-2 bg-surface-2/50">{footer}</div>}
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
