"use client";
/** Interactive genealogy diagram. Draws exactly what the traceability API returns (nodes and edges are database records; nothing is added here). Layered left to right:
 *  inputs on the left, the focus batch in the middle, what it became on the right. Pan by dragging, zoom with the wheel or buttons, search dims what does not match,
 *  a node's branch can be collapsed, and a table view lists the same nodes and edges for anyone who prefers it (or has a very large graph). */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight, Maximize2, Minus, Plus, Search, X } from "lucide-react";
import { Badge } from "@/components/ui";
import { toneFor } from "@/lib/modules";
import { fieldLabel, fmtDate, fmtDateTime, titleCase } from "@/lib/format";

export type GNode = { id: string; type: string; ref: string; label?: string | null; kind?: string; status?: string | null; date?: string | null; unit?: string | null; item_code?: string; href?: string | null; detail?: Record<string, unknown> };
export type GEdge = { source: string; target: string; qty: string | null; unit?: string | null; date?: string | null; label?: string | null };
export type GraphData = { focus: string; direction: string; nodes: GNode[]; edges: GEdge[]; truncated: boolean; limits: { max_depth: number; max_nodes: number } };

const W = 196, H = 68, GX = 112, GY = 16;
/** Node families: procurement, material batches, production, quality, stock, sales, returns. Colour is a hint; the type label and reference are always printed. */
const FAMILY: Record<string, { label: string; accent: string; tint: string }> = {
  supplier: { label: "Supplier", accent: "var(--g-procure)", tint: "transparent" }, purchase_order: { label: "Purchase order", accent: "var(--g-procure)", tint: "transparent" }, goods_receipt: { label: "Goods receipt", accent: "var(--g-procure)", tint: "transparent" },
  opening_stock: { label: "Opening stock", accent: "var(--g-procure)", tint: "transparent" },
  batch: { label: "Batch", accent: "var(--g-batch)", tint: "transparent" }, production_order: { label: "Production order", accent: "var(--g-prod)", tint: "transparent" },
  formulation: { label: "Formulation", accent: "var(--g-prod)", tint: "transparent" }, bom: { label: "BOM", accent: "var(--g-prod)", tint: "transparent" },
  qc: { label: "QC inspection", accent: "var(--g-qc)", tint: "transparent" }, warehouse: { label: "Warehouse", accent: "var(--g-stock)", tint: "transparent" }, reservation: { label: "Reservation", accent: "var(--g-stock)", tint: "transparent" },
  shipment: { label: "Shipment", accent: "var(--g-sales)", tint: "transparent" }, sales_order: { label: "Sales order", accent: "var(--g-sales)", tint: "transparent" }, customer: { label: "Customer", accent: "var(--g-sales)", tint: "transparent" },
  return: { label: "Return", accent: "var(--g-return)", tint: "transparent" },
};
const KIND = { RM: "Raw material", PM: "Packaging", BULK: "Bulk", FG: "Finished goods" } as Record<string, string>;
const fam = (n: GNode) => FAMILY[n.type] ?? { label: titleCase(n.type), accent: "var(--g-stock)", tint: "transparent" };
const nodeTitle = (n: GNode) => (n.type === "batch" ? `${KIND[n.kind ?? ""] ?? "Batch"}` : fam(n).label);

type Pos = { x: number; y: number; layer: number };

/** Longest-path layering over the edge direction, so a record always sits to the right of everything that feeds it. */
function layout(nodes: GNode[], edges: GEdge[], focus: string): Map<string, Pos> {
  const ids = new Set(nodes.map((n) => n.id));
  const out = new Map<string, string[]>(), indeg = new Map<string, number>();
  nodes.forEach((n) => { out.set(n.id, []); indeg.set(n.id, 0); });
  edges.forEach((e) => { if (ids.has(e.source) && ids.has(e.target)) { out.get(e.source)!.push(e.target); indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1); } });
  const layer = new Map<string, number>(); nodes.forEach((n) => layer.set(n.id, 0));
  const q = nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  const deg = new Map(indeg);
  let guard = 0;
  while (q.length && guard++ < 10000) {
    const u = q.shift()!;
    for (const v of out.get(u) ?? []) {
      layer.set(v, Math.max(layer.get(v) ?? 0, (layer.get(u) ?? 0) + 1));
      deg.set(v, (deg.get(v) ?? 1) - 1);
      if ((deg.get(v) ?? 0) === 0) q.push(v);
    }
  }
  const cols = new Map<number, GNode[]>();
  nodes.forEach((n) => { const l = layer.get(n.id) ?? 0; cols.set(l, [...(cols.get(l) ?? []), n]); });
  const order = ["supplier", "purchase_order", "goods_receipt", "opening_stock", "formulation", "bom", "batch", "production_order", "qc", "warehouse", "reservation", "shipment", "sales_order", "return", "customer"];
  const pos = new Map<string, Pos>();
  const tallest = Math.max(...[...cols.values()].map((c) => c.length), 1);
  [...cols.entries()].sort((a, b) => a[0] - b[0]).forEach(([l, list]) => {
    list.sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type) || (a.id === focus ? -1 : b.id === focus ? 1 : 0) || a.ref.localeCompare(b.ref));
    const colH = list.length * (H + GY) - GY, totalH = tallest * (H + GY) - GY;
    list.forEach((n, i) => pos.set(n.id, { x: l * (W + GX), y: (totalH - colH) / 2 + i * (H + GY), layer: l }));
  });
  return pos;
}

export function Genealogy({ data, onOpenBatch }: { data: GraphData; onOpenBatch?: (id: number) => void }) {
  const [view, setView] = useState<"diagram" | "list">("diagram");
  const [selected, setSelected] = useState<string | null>(data.focus);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [tf, setTf] = useState({ x: 24, y: 24, k: 1 });
  const wrap = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  const adj = useMemo(() => {
    const parents = new Map<string, string[]>(), children = new Map<string, string[]>();
    data.edges.forEach((e) => { (children.get(e.source) ?? children.set(e.source, []).get(e.source)!).push(e.target); (parents.get(e.target) ?? parents.set(e.target, []).get(e.target)!).push(e.source); });
    return { parents, children };
  }, [data]);

  /** Visible set: start at the focus, walk to inputs on the left and to results on the right, and stop at collapsed nodes. Quality inspections stay attached to any visible batch. */
  const visible = useMemo(() => {
    const vis = new Set<string>([data.focus]);
    const leftQ = [data.focus], rightQ = [data.focus];
    while (leftQ.length) { const u = leftQ.shift()!; if (collapsed.has(u) && u !== data.focus) continue; for (const p of adj.parents.get(u) ?? []) if (!vis.has(p)) { vis.add(p); leftQ.push(p); } }
    while (rightQ.length) { const u = rightQ.shift()!; if (collapsed.has(u) && u !== data.focus) continue; for (const c of adj.children.get(u) ?? []) if (!vis.has(c)) { vis.add(c); rightQ.push(c); } }
    data.nodes.forEach((n) => { if (n.type === "qc") { const owner = (adj.parents.get(n.id) ?? [])[0]; if (owner && vis.has(owner)) vis.add(n.id); } });
    return vis;
  }, [data, adj, collapsed]);

  const nodes = useMemo(() => data.nodes.filter((n) => visible.has(n.id)), [data, visible]);
  const edges = useMemo(() => data.edges.filter((e) => visible.has(e.source) && visible.has(e.target)), [data, visible]);
  const pos = useMemo(() => layout(nodes, edges, data.focus), [nodes, edges, data.focus]);
  const size = useMemo(() => { let w = 0, h = 0; pos.forEach((p) => { w = Math.max(w, p.x + W); h = Math.max(h, p.y + H); }); return { w: w + 48, h: h + 48 }; }, [pos]);
  const match = useCallback((n: GNode) => !query.trim() || `${n.ref} ${n.label ?? ""} ${n.type} ${n.status ?? ""} ${n.item_code ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()), [query]);
  const hits = useMemo(() => (query.trim() ? nodes.filter(match).length : null), [nodes, match, query]);

  const fit = useCallback(() => {
    const el = wrap.current; if (!el) return;
    const k = Math.min(1, (el.clientWidth - 24) / size.w, (el.clientHeight - 24) / size.h);
    setTf({ k: Math.max(0.25, k), x: 12, y: Math.max(12, (el.clientHeight - size.h * k) / 2) });
  }, [size]);
  /** Start readable: 80% zoom centred on the focus batch. "Fit" shows the whole graph at once, which is only legible for small genealogies. */
  const focusView = useCallback(() => {
    const el = wrap.current, p = pos.get(data.focus); if (!el || !p) return;
    const k = size.w * 0.4 <= el.clientWidth ? Math.min(1, el.clientWidth / size.w) : 0.8;
    // backward: the focus sits at the right edge so its ancestors fill the view; forward: at the left edge; both: centred
    const x = data.direction === "backward" ? el.clientWidth - (p.x + W) * k - 28 : data.direction === "forward" ? 28 - p.x * k : el.clientWidth / 2 - (p.x + W / 2) * k;
    setTf({ k, x, y: el.clientHeight / 2 - (p.y + H / 2) * k });
  }, [pos, size, data.focus]);
  useEffect(() => { focusView(); }, [focusView]);
  useEffect(() => {                                                                    // wheel zoom needs a non-passive listener to stop the page scrolling
    const el = wrap.current; if (!el || view !== "diagram") return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
      setTf((t) => { const k = Math.min(2, Math.max(0.25, t.k * (e.deltaY < 0 ? 1.1 : 0.9))); return { k, x: cx - ((cx - t.x) / t.k) * k, y: cy - ((cy - t.y) / t.k) * k }; });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [view]);

  const sel = data.nodes.find((n) => n.id === selected) ?? null;
  const touching = useMemo(() => new Set(data.edges.filter((e) => e.source === selected || e.target === selected).flatMap((e) => [e.source, e.target])), [data, selected]);
  const toggle = (id: string) => setCollapsed((c) => { const n = new Set(c); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const hasMore = (id: string) => { const left = pos.get(id)!.layer <= (pos.get(data.focus)?.layer ?? 0); return (left ? adj.parents.get(id) : adj.children.get(id))?.some((x) => x !== data.focus) ?? false; };

  const path = (e: GEdge) => {
    const a = pos.get(e.source), b = pos.get(e.target); if (!a || !b) return null;
    const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
    return { d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, lx: mx, ly: (y1 + y2) / 2 };
  };

  return (
    <div className="panel overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
        <div className="seg" role="group" aria-label="Genealogy view">
          <button type="button" aria-pressed={view === "diagram"} onClick={() => setView("diagram")}>Diagram</button>
          <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>List</button>
        </div>
        <label className="relative flex-1 min-w-[180px] max-w-[320px]">
          <span className="sr-only">Search the genealogy</span>
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
          <input className="input !h-9 !pl-9" placeholder="Search batch, order, customer…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        {hits !== null && <span className="t-support">{hits} match{hits === 1 ? "" : "es"}</span>}
        <span className="t-support ml-auto">{nodes.length} of {data.nodes.length} records · {edges.length} links{data.truncated && " · limit reached, open a node to continue from it"}</span>
        {view === "diagram" && (
          <div className="flex gap-1" role="group" aria-label="Zoom">
            <button type="button" className="icon-btn icon-btn-sm" aria-label="Zoom out" onClick={() => setTf((t) => ({ ...t, k: Math.max(0.25, t.k * 0.85) }))}><Minus size={15} /></button>
            <button type="button" className="icon-btn icon-btn-sm" aria-label="Zoom in" onClick={() => setTf((t) => ({ ...t, k: Math.min(2, t.k * 1.18) }))}><Plus size={15} /></button>
            <button type="button" className="icon-btn icon-btn-sm" aria-label="Fit to view" onClick={fit}><Maximize2 size={15} /></button>
          </div>
        )}
      </div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_320px]">
        {view === "diagram" ? (
          <div ref={wrap} className="relative h-[560px] overflow-hidden bg-[var(--inset)] touch-none cursor-grab active:cursor-grabbing select-none" role="application" aria-label="Genealogy diagram. Drag to pan, scroll to zoom, select a record for its details."
            onPointerDown={(e) => { if ((e.target as HTMLElement).closest("[data-node]")) return; drag.current = { x: e.clientX, y: e.clientY, ox: tf.x, oy: tf.y }; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); }}
            onPointerMove={(e) => { const d = drag.current; if (d) setTf((t) => ({ ...t, x: d.ox + e.clientX - d.x, y: d.oy + e.clientY - d.y })); }}
            onPointerUp={() => { drag.current = null; }}>
            <div style={{ transform: `translate(${tf.x}px, ${tf.y}px) scale(${tf.k})`, transformOrigin: "0 0", width: size.w, height: size.h, position: "absolute" }}>
              <svg width={size.w} height={size.h} className="absolute inset-0 overflow-visible pointer-events-none" aria-hidden>
                {edges.map((e, i) => {
                  const p = path(e); if (!p) return null;
                  const on = selected && (e.source === selected || e.target === selected);
                  return (
                    <g key={i} opacity={query && !(match(data.nodes.find((n) => n.id === e.source)!) || match(data.nodes.find((n) => n.id === e.target)!)) ? 0.15 : 1}>
                      <path d={p.d} fill="none" stroke={on ? "var(--brand)" : "var(--faint)"} strokeWidth={on ? 2 : 1.25} />
                      {e.qty && <text x={p.lx} y={p.ly - 5} textAnchor="middle" fontSize="11" fontWeight="600" fill={on ? "var(--brand)" : "var(--muted)"} stroke="var(--inset)" strokeWidth="3" paintOrder="stroke">{Number(e.qty).toLocaleString("en-IN", { maximumFractionDigits: 3 })}{e.unit ? ` ${e.unit}` : ""}</text>}
                    </g>
                  );
                })}
              </svg>
              {nodes.map((n) => {
                const p = pos.get(n.id)!, f = fam(n), dim = (query && !match(n)) || (selected && !touching.has(n.id) && n.id !== selected && false);
                return (
                  <div key={n.id} data-node style={{ position: "absolute", left: p.x, top: p.y, width: W, height: H, opacity: dim ? 0.25 : 1 }} className="group">
                    <button type="button" onClick={() => setSelected(n.id)} onDoubleClick={() => n.type === "batch" && onOpenBatch?.(Number(n.id.split(":")[1]))} aria-pressed={selected === n.id}
                      aria-label={`${nodeTitle(n)} ${n.ref}${n.status ? `, ${titleCase(n.status)}` : ""}`}
                      className="h-full w-full text-left rounded-[12px] border bg-[var(--card)] text-[var(--fg)] px-3 py-2 overflow-hidden transition-[transform,opacity] duration-150 hover:-translate-y-0.5"
                      style={{ borderColor: selected === n.id ? "var(--brand)" : "var(--line)", borderLeft: `4px solid ${f.accent}`, boxShadow: selected === n.id ? "0 0 0 2px rgb(1 135 122 / 0.25)" : n.id === data.focus ? "0 6px 16px -10px rgb(1 70 62 / 0.5)" : "var(--shadow-sm)", background: n.id === data.focus ? `color-mix(in srgb, ${f.accent} 16%, var(--card))` : "var(--card)" }}>
                      <div className="flex items-center justify-between gap-2"><span className="text-[0.7188rem] font-medium" style={{ color: f.accent }}>{nodeTitle(n)}</span>{n.status && <span className="text-[0.6875rem] text-muted truncate">{titleCase(n.status)}</span>}</div>
                      <div className="text-[0.75rem] font-semibold truncate leading-5 mt-0.5">{n.ref}</div>
                      <div className="text-[0.75rem] text-muted truncate leading-4">{n.label ?? ""}{n.date ? ` · ${fmtDate(n.date)}` : ""}</div>
                    </button>
                    {n.id !== data.focus && hasMore(n.id) && (
                      <button type="button" aria-label={collapsed.has(n.id) ? `Expand ${n.ref}` : `Collapse ${n.ref}`} onClick={() => toggle(n.id)}
                        className="absolute -bottom-2 left-1/2 -translate-x-1/2 h-5 w-5 pointer-coarse:h-12 pointer-coarse:w-12 pointer-coarse:-bottom-5 grid place-items-center rounded-full bg-[var(--card)] border border-line text-muted hover:text-fg">
                        {collapsed.has(n.id) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            <ul className="absolute bottom-3 left-3 flex flex-wrap gap-x-3 gap-y-1 text-[0.7188rem] text-muted bg-[var(--card)] rounded-lg px-2.5 py-1.5 border border-line pointer-events-none" aria-label="Legend">
              {["batch", "production_order", "qc", "goods_receipt", "shipment", "return"].map((t) => <li key={t} className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: FAMILY[t].accent }} />{FAMILY[t].label}</li>)}
            </ul>
          </div>
        ) : (
          <GenealogyTable data={data} nodes={nodes} edges={edges} selected={selected} onSelect={setSelected} match={match} />
        )}
        <aside className="border-t lg:border-t-0 lg:border-l border-line p-4 max-h-[560px] overflow-y-auto" aria-label="Selected record">
          {sel ? <NodePanel node={sel} data={data} onClose={() => setSelected(null)} onSelect={setSelected} /> : <p className="t-support">Select a record in the diagram to see its reference, status, quantities in and out, and a link to the record itself.</p>}
        </aside>
      </div>
    </div>
  );
}

function NodePanel({ node, data, onClose, onSelect }: { node: GNode; data: GraphData; onClose: () => void; onSelect: (id: string) => void }) {
  const f = fam(node);
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const inE = data.edges.filter((e) => e.target === node.id), outE = data.edges.filter((e) => e.source === node.id);
  const row = (e: GEdge, other: string) => { const o = byId.get(other); return o && (
    <li key={`${e.source}-${e.target}-${e.label}`}>
      <button type="button" className="w-full text-left flex items-baseline justify-between gap-2 py-1.5 border-b border-line last:border-0 hover:text-[var(--brand)]" onClick={() => onSelect(other)}>
        <span className="min-w-0"><span className="block text-[0.75rem] font-medium truncate">{o.ref}</span><span className="block t-support truncate">{e.label ? `${e.label} · ` : ""}{nodeTitle(o)}</span></span>
        {e.qty && <span className="text-[0.75rem] font-semibold shrink-0">{Number(e.qty).toLocaleString("en-IN", { maximumFractionDigits: 3 })}{e.unit ? ` ${e.unit}` : ""}</span>}
      </button>
    </li>); };
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><div className="text-[0.75rem] font-medium" style={{ color: f.accent }}>{nodeTitle(node)}</div><h3 className="font-heading text-[1rem] leading-6 font-medium break-words">{node.ref}</h3>{node.label && <p className="t-support">{node.label}</p>}</div>
        <button type="button" className="icon-btn icon-btn-sm shrink-0" aria-label="Close details" onClick={onClose}><X size={15} /></button>
      </div>
      <div className="flex flex-wrap gap-1.5">{node.status && <Badge tone={toneFor(node.status)}>{titleCase(node.status)}</Badge>}{node.date && <span className="badge tone-neutral">{fmtDate(node.date)}</span>}</div>
      {node.detail && Object.keys(node.detail).length > 0 && (
        <dl className="text-[0.75rem] grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          {Object.entries(node.detail).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{fieldLabel(k)}</dt><dd className="font-medium text-right">{typeof v === "boolean" ? (v ? "Yes" : "No") : typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v) ? fmtDateTime(v) : String(v)}</dd></div>)}
        </dl>
      )}
      {inE.length > 0 && <div><h4 className="t-support font-medium mb-1">Came from</h4><ul>{inE.map((e) => row(e, e.source))}</ul></div>}
      {outE.length > 0 && <div><h4 className="t-support font-medium mb-1">Went to</h4><ul>{outE.map((e) => row(e, e.target))}</ul></div>}
      {node.href && <Link href={node.href} className="btn btn-sm w-full">{node.type === "batch" ? "Open this batch" : "Open record"}</Link>}
    </div>
  );
}

function GenealogyTable({ data, nodes, edges, selected, onSelect, match }: { data: GraphData; nodes: GNode[]; edges: GEdge[]; selected: string | null; onSelect: (id: string) => void; match: (n: GNode) => boolean }) {
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const shown = nodes.filter(match);
  return (
    <div className="max-h-[560px] overflow-auto">
      <table className="dt"><thead><tr><th>Type</th><th>Reference</th><th>Description</th><th>Status</th><th>Date</th><th className="num">Qty in</th><th className="num">Qty out</th></tr></thead>
        <tbody>
          {shown.map((n) => {
            const qin = edges.filter((e) => e.target === n.id && e.qty).reduce((a, e) => a + Number(e.qty), 0), qout = edges.filter((e) => e.source === n.id && e.qty).reduce((a, e) => a + Number(e.qty), 0);
            const unit = byId.get(n.id)?.unit ?? "";
            return (
              <tr key={n.id} className={`row-link cursor-pointer ${selected === n.id ? "bg-[var(--brand-tint)]" : ""}`} onClick={() => onSelect(n.id)}>
                <td>{nodeTitle(n)}</td><td className="font-medium">{n.ref}</td><td className="text-muted">{n.label ?? ""}</td><td>{n.status ? <Badge tone={toneFor(n.status)}>{titleCase(n.status)}</Badge> : ""}</td>
                <td>{n.date ? fmtDate(n.date) : ""}</td><td className="num">{qin ? `${qin.toLocaleString("en-IN", { maximumFractionDigits: 3 })} ${unit}` : ""}</td><td className="num">{qout ? `${qout.toLocaleString("en-IN", { maximumFractionDigits: 3 })} ${unit}` : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
