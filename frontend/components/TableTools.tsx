"use client";
/** Table tools for list screens (phase 2.5): row density, which columns show, saved views, a totals row and CSV export, plus the floating bar that
 *  appears when rows are selected. The state lives in ListScreen; these are only the controls. Preferences are per user and per list. */
import { useRef, useState } from "react";
import { Bookmark, Check, Columns3, Download, Rows3, Sigma, Trash2, X } from "lucide-react";
import { useOutsideClick } from "@/lib/useOutsideClick";

export type SavedView = { name: string; status: string; q: string; sort: string; hidden: string[]; dense: boolean };

function Pop({ label, icon, children, active }: { label: string; icon: React.ReactNode; children: React.ReactNode; active?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutsideClick(ref, () => setOpen(false), open);
  return (
    <div className="relative" ref={ref}>
      <button type="button" className={`btn btn-sm gap-1.5 ${active ? "btn-primary" : ""}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{icon}<span className="max-lg:sr-only">{label}</span></button>
      {open && <div role="dialog" aria-label={label} className="anim-fade absolute right-0 mt-1.5 w-60 panel p-2 shadow-xl z-40">{children}</div>}
    </div>
  );
}

export function TableTools({ dense, onDense, cols, hidden, onHidden, views, onViews, current, onApply, totals, onTotals, canTotal, onExport, exportCount }: {
  dense: boolean; onDense: (v: boolean) => void;
  cols: { key: string; label: string }[]; hidden: string[]; onHidden: (h: string[]) => void;
  views: SavedView[]; onViews: (v: SavedView[]) => void; current: SavedView; onApply: (v: SavedView) => void;
  totals: boolean; onTotals: (v: boolean) => void; canTotal: boolean; onExport: () => void; exportCount: number;
}) {
  const [name, setName] = useState("");
  const save = () => { const n = name.trim(); if (!n) return; onViews([...views.filter((v) => v.name !== n), { ...current, name: n }]); setName(""); };
  return (
    <div className="flex items-center gap-1.5" role="group" aria-label="Table options">
      <button type="button" className="btn btn-sm gap-1.5" aria-pressed={dense} onClick={() => onDense(!dense)} title={dense ? "Comfortable rows" : "Compact rows"}><Rows3 size={14} strokeWidth={1.5} /><span className="max-lg:sr-only">{dense ? "Compact" : "Comfortable"}</span></button>
      <Pop label="Columns" icon={<Columns3 size={14} strokeWidth={1.5} />} active={hidden.length > 0}>
        <p className="t-support px-1 pb-1">Choose what the table shows</p>
        <ul className="max-h-64 overflow-y-auto">
          {cols.map((c) => { const on = !hidden.includes(c.key); return (
            <li key={c.key}><button type="button" role="checkbox" aria-checked={on} onClick={() => onHidden(on ? [...hidden, c.key] : hidden.filter((k) => k !== c.key))} className="w-full flex items-center gap-2.5 px-2 h-8 rounded-lg text-[0.8125rem] text-left hover:bg-inset">
              <span className="grid place-items-center w-4 h-4 rounded border border-line-strong" style={on ? { background: "var(--ink-r)", color: "var(--card)", borderColor: "transparent" } : undefined}>{on && <Check size={11} strokeWidth={2} />}</span><span className="truncate">{c.label}</span></button></li>
          ); })}
        </ul>
        {hidden.length > 0 && <button type="button" className="btn btn-sm w-full mt-1" onClick={() => onHidden([])}>Show all</button>}
      </Pop>
      <Pop label="Views" icon={<Bookmark size={14} strokeWidth={1.5} />} active={views.length > 0}>
        <p className="t-support px-1 pb-1">Saved views keep the filter, sort, columns and row size</p>
        {views.length === 0 ? <p className="text-[0.8125rem] px-1 pb-2 text-muted">None yet.</p> : (
          <ul className="mb-2">{views.map((v) => (
            <li key={v.name} className="flex items-center gap-1"><button type="button" className="flex-1 text-left px-2 h-8 rounded-lg text-[0.8125rem] hover:bg-inset truncate" onClick={() => onApply(v)}>{v.name}</button>
              <button type="button" className="icon-btn !w-7 !h-7" aria-label={`Delete view ${v.name}`} onClick={() => onViews(views.filter((x) => x.name !== v.name))}><Trash2 size={13} strokeWidth={1.5} /></button></li>
          ))}</ul>
        )}
        <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <input className="input !h-8 flex-1" placeholder="Name this view" value={name} onChange={(e) => setName(e.target.value)} aria-label="View name" maxLength={40} />
          <button type="submit" className="btn btn-sm btn-primary" disabled={!name.trim()}>Save</button>
        </form>
      </Pop>
      {canTotal && <button type="button" className="btn btn-sm gap-1.5" aria-pressed={totals} onClick={() => onTotals(!totals)}><Sigma size={14} strokeWidth={1.5} /><span className="max-lg:sr-only">Totals</span></button>}
      <button type="button" className="btn btn-sm gap-1.5" onClick={onExport} disabled={exportCount === 0} title="Download what is shown as a CSV file"><Download size={14} strokeWidth={1.5} /><span className="max-lg:sr-only">CSV</span></button>
    </div>
  );
}

/** The bar that rises when rows are selected. Selection only ever feeds export and copy: nothing here changes a record. */
export function BulkBar({ count, onExport, onCopy, onClear }: { count: number; onExport: () => void; onCopy: () => void; onClear: () => void }) {
  if (count === 0) return null;
  return (
    <div role="region" aria-label="Selected rows" className="anim-slide fixed left-1/2 -translate-x-1/2 bottom-20 md:bottom-6 z-40 flex items-center gap-1 rounded-full pl-4 pr-1.5 py-1.5 text-[0.8125rem]" style={{ background: "var(--ink-r)", color: "var(--card)", boxShadow: "0 24px 50px -18px rgb(21 23 23 / 0.6)" }}>
      <span className="tabular-nums mr-2" aria-live="polite">{count} selected</span>
      <button type="button" className="h-8 px-3 rounded-full hover:bg-white/15" onClick={onExport}>Export CSV</button>
      <button type="button" className="h-8 px-3 rounded-full hover:bg-white/15" onClick={onCopy}>Copy numbers</button>
      <button type="button" className="grid place-items-center w-8 h-8 rounded-full hover:bg-white/15" onClick={onClear} aria-label="Clear selection"><X size={15} strokeWidth={1.5} /></button>
    </div>
  );
}
