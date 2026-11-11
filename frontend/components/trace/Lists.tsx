"use client";
import { useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { fmtDate, titleCase } from "@/lib/format";
import { toneFor } from "@/lib/modules";
import { Badge, PageHeader, useDebounced } from "@/components/ui";
import { Pager, State, qty, useGet } from "./bits";

const KINDS: [string, string][] = [["", "All types"], ["FG", "Finished goods"], ["BULK", "Bulk"], ["RM", "Raw material"], ["PM", "Packaging"]];

export function BatchList() {
  const [q, setQ] = useState(""), [kind, setKind] = useState(""), [qc, setQc] = useState(""), [page, setPage] = useState(1);
  const dq = useDebounced(q, 300);
  const { data, error, loading } = useGet<{ items: { id: number; batch_no: string; item_code: string; item_name: string; kind: string; uom: string; qc_status: string; mfg_date: string | null; expiry_date: string | null; supplier_lot_no: string | null; physical: string; available: string }[]; total: number }>(
    "/traceability/batches", { q: dq || undefined, kind: kind || undefined, qc_status: qc || undefined, page, page_size: 20 });
  return (
    <div className="space-y-4 pb-10">
      <PageHeader title="Batch traceability" description="Open any batch to see where its material came from and where it went." />
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex-1 min-w-[220px] max-w-md"><span className="sr-only">Search batches</span><Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
          <input className="input !pl-10" placeholder="Batch number, product or supplier lot" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></label>
        <select className="input !w-auto" aria-label="Batch type" value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <select className="input !w-auto" aria-label="QC status" value={qc} onChange={(e) => { setQc(e.target.value); setPage(1); }}>{[["", "Any QC status"], ["RELEASED", "Released"], ["QUARANTINE", "Quarantine"], ["HOLD", "On hold"], ["REJECTED", "Rejected"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      </div>
      <div className="panel overflow-hidden"><State loading={loading && !data} error={error} empty={data?.total === 0 && "No batch matches."}>{data && <><div className="overflow-auto"><table className="dt"><thead><tr><th>Batch</th><th>Product</th><th>Type</th><th>QC</th><th>Made / received</th><th>Expiry</th><th className="num">Physical</th><th className="num">Available</th></tr></thead>
        <tbody>{data.items.map((b) => <tr key={b.id} className="row-link"><td><Link className="font-semibold hover:underline" href={`/inventory/batches/${b.id}/traceability`}>{b.batch_no}</Link></td><td>{b.item_name} <span className="text-muted">{b.item_code}</span></td><td>{b.kind}</td><td><Badge tone={toneFor(b.qc_status)}>{titleCase(b.qc_status)}</Badge></td><td>{b.mfg_date ? fmtDate(b.mfg_date) : "—"}</td><td>{b.expiry_date ? fmtDate(b.expiry_date) : "—"}</td><td className="num">{qty(b.physical, b.uom)}</td><td className="num">{qty(b.available, b.uom)}</td></tr>)}</tbody></table></div>
        <Pager page={page} total={data.total} size={20} onPage={setPage} /></>}</State></div>
    </div>
  );
}

export function ProductList() {
  const [q, setQ] = useState(""), [kind, setKind] = useState("FG"), [page, setPage] = useState(1);
  const dq = useDebounced(q, 300);
  const { data, error, loading } = useGet<{ items: { id: number; item_code: string; name: string; kind: string; uom: string; batches: number; available: string }[]; total: number }>("/traceability/products", { q: dq || undefined, kind: kind || undefined, page, page_size: 20 });
  return (
    <div className="space-y-4 pb-10">
      <PageHeader title="Product 360" description="Formulation, materials, production, batches, quality and customers for each product." />
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex-1 min-w-[220px] max-w-md"><span className="sr-only">Search products</span><Search size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
          <input className="input !pl-10" placeholder="Product name or code" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></label>
        <select className="input !w-auto" aria-label="Product type" value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      </div>
      <div className="panel overflow-hidden"><State loading={loading && !data} error={error} empty={data?.total === 0 && "No product matches."}>{data && <><div className="overflow-auto"><table className="dt"><thead><tr><th>Product</th><th>Code</th><th>Type</th><th className="num">Batches</th><th className="num">Available</th></tr></thead>
        <tbody>{data.items.map((p) => <tr key={p.id} className="row-link"><td><Link className="font-semibold hover:underline" href={`/inventory/products/${p.id}/overview`}>{p.name}</Link></td><td className="text-muted">{p.item_code}</td><td>{p.kind}</td><td className="num">{p.batches}</td><td className="num">{qty(p.available, p.uom)}</td></tr>)}</tbody></table></div>
        <Pager page={page} total={data.total} size={20} onPage={setPage} /></>}</State></div>
    </div>
  );
}