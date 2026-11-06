"use client";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import { EmptyState } from "./ui";

export type Col<T> = {
  key: string; label: string; render: (row: T) => React.ReactNode; align?: "right"; sortKey?: string; title?: (row: T) => string | undefined;
};

export function DataTable<T extends { id: number | string }>({
  columns, rows, loading, sort, onSort, page, pageSize, total, onPage, onRowClick, empty, rowActions,
}: {
  columns: Col<T>[]; rows: T[]; loading: boolean; sort?: string; onSort?: (s: string) => void;
  page: number; pageSize: number; total: number; onPage: (p: number) => void;
  onRowClick?: (r: T) => void; empty?: React.ReactNode; rowActions?: (r: T) => React.ReactNode;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="panel overflow-hidden">
      <div className="overflow-auto max-h-[calc(100vh-320px)] min-h-[200px]">
        <table className="dt">
          <thead>
            <tr>
              {columns.map((c) => {
                const active = sort && c.sortKey && sort.replace("-", "") === c.sortKey;
                return (
                  <th key={c.key} className={c.align === "right" ? "num" : ""} aria-sort={active ? (sort!.startsWith("-") ? "descending" : "ascending") : undefined}>
                    {c.sortKey && onSort ? (
                      <button className="inline-flex items-center gap-1 hover:text-fg" onClick={() => onSort(active && !sort!.startsWith("-") ? `-${c.sortKey}` : c.sortKey!)}>
                        {c.label}{active && (sort!.startsWith("-") ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
                      </button>
                    ) : c.label}
                  </th>
                );
              })}
              {rowActions && <th className="w-px" />}
            </tr>
          </thead>
          <tbody>
            {loading && rows.length === 0 && Array.from({ length: 8 }).map((_, i) => (
              <tr key={i}>{columns.map((c) => <td key={c.key}><div className="skeleton h-3 w-3/4" /></td>)}{rowActions && <td />}</tr>
            ))}
            {rows.map((r) => (
              <tr key={r.id} className={onRowClick ? "cursor-pointer" : ""} onClick={() => onRowClick?.(r)}>
                {columns.map((c) => (
                  <td key={c.key} className={c.align === "right" ? "num" : ""} title={c.title?.(r)}>{c.render(r)}</td>
                ))}
                {rowActions && <td onClick={(e) => e.stopPropagation()} className="text-right !max-w-none">{rowActions(r)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && rows.length === 0 && (empty ?? <EmptyState title="No records found" hint="Try adjusting your search or filters." />)}
      </div>
      <div className="flex items-center justify-between gap-3 px-3 h-10 border-t border-line text-[12px] text-muted bg-surface">
        <span className="tnum">{total === 0 ? "0 records" : `${from}–${to} of ${total}`}</span>
        <div className="flex items-center gap-1">
          <button className="btn btn-ghost btn-sm !px-1.5" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page"><ChevronLeft size={15} /></button>
          <span className="tnum px-1">Page {page} / {pages}</span>
          <button className="btn btn-ghost btn-sm !px-1.5" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page"><ChevronRight size={15} /></button>
        </div>
      </div>
    </div>
  );
}
