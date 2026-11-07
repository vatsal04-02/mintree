"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtMoney, fmtNum, titleCase } from "@/lib/format";
import { Badge, EmptyState, PageHeader, useToast } from "@/components/ui";

type Flag = { level: "ERROR" | "WARN" | "INFO"; code: string; message: string };
type Line = { description?: string | null; qty?: string | null; unit?: string | null; unit_price?: string | null };
type Draft = { po_id: number; supplier_invoice_no?: string; invoice_date?: string; delivery_note_no?: string; lines: { po_item_id: number; qty: string; unit_price?: string; tax_rate?: string }[] };
type Ext = {
  id: number; filename: string; kind: "INVOICE" | "DELIVERY_NOTE"; status: string; demo: boolean; confidence: string | null; blocking: string[]; flags: Flag[]; draft_payload: Draft | null;
  fields: { supplier_name?: string; supplier_gstin?: string; document_no?: string; document_date?: string; po_number?: string; subtotal?: string; tax_total?: string; total?: string; lines: Line[] };
  matched: { supplier: { name: string } | null; po: { id: number; number: string } | null; lines: { line: number; po_item: string | null }[] };
};

const hash = (s: string) => { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return (h >>> 0).toString(16); };
const tone = (l: Flag["level"]) => (l === "ERROR" ? "danger" : l === "WARN" ? "warning" : "info");

export default function ExtractPage() {
  const { can } = useAuth();
  const toast = useToast();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [kind, setKind] = useState<"INVOICE" | "DELIVERY_NOTE">("INVOICE");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ext, setExt] = useState<Ext | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => { api<{ enabled: boolean }>("/ai/status").then((s) => setEnabled(s.enabled)).catch(() => setEnabled(false)); }, []);

  const read = async () => {
    if (!file) return;
    setBusy(true); setError(null); setExt(null); setDraft(null); setDone(null);
    const form = new FormData(); form.set("kind", kind); form.set("file", file);
    try { const r = await api<Ext>("/ai/extractions", { form }); setExt(r); setDraft(r.draft_payload ? structuredClone(r.draft_payload) : null); }
    catch (e) { setError(e instanceof ApiError ? e.message : "The document could not be read."); }
    finally { setBusy(false); }
  };

  const edits = useMemo(() => {
    const out: Record<string, [string, string]> = {};
    if (!ext?.draft_payload || !draft) return out;
    const o = ext.draft_payload;
    for (const k of ["supplier_invoice_no", "invoice_date"] as const) if (o[k] !== draft[k]) out[k] = [String(o[k] ?? ""), String(draft[k] ?? "")];
    draft.lines.forEach((l, i) => (["qty", "unit_price", "tax_rate"] as const).forEach((k) => { if (o.lines[i]?.[k] !== l[k]) out[`lines.${i}.${k}`] = [String(o.lines[i]?.[k] ?? ""), String(l[k] ?? "")]; }));
    return out;
  }, [ext, draft]);

  const canPost = !!ext && ext.kind === "INVOICE" && !!draft && can("accounts.supplier_invoice:post") && ext.status === "EXTRACTED" && ext.blocking.length === 0;
  const post = async () => {
    if (!ext || !draft) return;
    setBusy(true); setError(null);
    try {
      const inv = await api<{ id: number; number: string }>("/accounts/supplier-invoices", { body: draft, headers: { "Idempotency-Key": `ai-extract-${ext.id}-${hash(JSON.stringify(draft))}` } });
      await api(`/ai/extractions/${ext.id}/review`, { body: { outcome: "ACCEPTED", edits, posted: { entity: "supplier_invoice", id: inv.id } } });
      setDone(`Posted supplier invoice ${inv.number}.`); setExt({ ...ext, status: "ACCEPTED" }); toast("success", `Supplier invoice ${inv.number} posted`);
    } catch (e) { setError(e instanceof ApiError ? e.message : "The invoice could not be posted."); }
    finally { setBusy(false); }
  };
  const discard = async () => {
    if (!ext) return;
    try { await api(`/ai/extractions/${ext.id}/review`, { body: { outcome: "DISCARDED" } }); setExt({ ...ext, status: "DISCARDED" }); setDraft(null); } catch (e) { setError(e instanceof ApiError ? e.message : "Failed"); }
  };
  const setLine = (i: number, k: "qty" | "unit_price" | "tax_rate", v: string) => setDraft((d) => d && ({ ...d, lines: d.lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)) }));

  if (enabled === false) return <><PageHeader title="Document reader" /><div className="panel"><EmptyState title="The AI features are switched off" hint="An administrator can enable them in the server settings." /></div></>;

  return (
    <>
      <PageHeader title="Document reader" description="Upload a supplier invoice or delivery note. The reader copies what is printed; the system matches it to your suppliers and purchase orders and re-checks every sum. Nothing is posted until you review and confirm." />
      <div className="panel p-4 mb-5 flex flex-wrap items-end gap-3">
        <div><label className="field-label" htmlFor="kind">Document type</label>
          <select id="kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}><option value="INVOICE">Supplier invoice</option><option value="DELIVERY_NOTE">Delivery note</option></select></div>
        <div className="flex-1 min-w-[220px]"><label className="field-label" htmlFor="file">File (PDF, PNG, JPEG or text, up to 10 MB)</label>
          <input id="file" data-testid="ai-file" type="file" accept=".pdf,.png,.jpg,.jpeg,.txt,application/pdf,image/png,image/jpeg,text/plain" className="input !p-1.5" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
        <button className="btn btn-primary" onClick={read} disabled={!file || busy} data-testid="ai-read">{busy && !ext ? "Reading…" : "Read document"}</button>
      </div>
      {error && <div className="panel p-3 mb-4 text-danger text-[13px]" role="alert">{error}</div>}
      {done && <div className="panel p-3 mb-4 text-[13px]" role="status" data-testid="ai-done">{done}</div>}

      {ext && (
        <div className="grid gap-5 lg:grid-cols-2" data-testid="ai-result">
          <section className="panel p-4" aria-label="What was read">
            <div className="flex items-center gap-2 mb-3"><h2 className="t-section">What was read</h2>{ext.demo && <Badge tone="warning">Demo provider</Badge>}{ext.confidence && <Badge>Confidence {fmtNum(Number(ext.confidence) * 100)}%</Badge>}</div>
            <dl className="grid grid-cols-[130px_1fr] gap-y-1.5 text-[13px]">
              <dt className="text-muted">Supplier</dt><dd>{ext.fields.supplier_name ?? "—"}{ext.matched.supplier && <span className="text-muted"> → {ext.matched.supplier.name}</span>}</dd>
              <dt className="text-muted">Document no.</dt><dd className="font-mono">{ext.fields.document_no ?? "—"}</dd>
              <dt className="text-muted">Date</dt><dd>{ext.fields.document_date ?? "—"}</dd>
              <dt className="text-muted">PO</dt><dd className="font-mono">{ext.fields.po_number ?? "—"}{ext.matched.po && <span className="text-muted"> → matched</span>}</dd>
              <dt className="text-muted">Subtotal</dt><dd>{fmtMoney(ext.fields.subtotal)}</dd>
              <dt className="text-muted">Tax</dt><dd>{fmtMoney(ext.fields.tax_total)}</dd>
              <dt className="text-muted">Total</dt><dd className="font-medium">{fmtMoney(ext.fields.total)}</dd>
            </dl>
            <table className="dt mt-4"><thead><tr><th>#</th><th>Description</th><th className="num">Qty</th><th className="num">Price</th><th>PO line</th></tr></thead>
              <tbody>{ext.fields.lines.map((l, i) => <tr key={i}><td>{i + 1}</td><td>{l.description}</td><td className="num">{fmtNum(l.qty, 3)}</td><td className="num">{fmtMoney(l.unit_price)}</td><td className="font-mono text-[12px]">{ext.matched.lines[i]?.po_item ?? <span className="text-danger">no match</span>}</td></tr>)}</tbody></table>
          </section>

          <section className="panel p-4" aria-label="Checks and draft">
            <h2 className="t-section mb-3">Checks</h2>
            {ext.flags.length === 0 ? <p className="text-[13px] text-muted">No problems found. Still compare against the original document.</p> : (
              <ul className="space-y-1.5 mb-4" data-testid="ai-flags">{ext.flags.map((f, i) => <li key={i} className="flex gap-2 text-[13px]"><Badge tone={tone(f.level)}>{f.level === "ERROR" ? "Must fix" : "Check"}</Badge><span>{f.message}</span></li>)}</ul>
            )}
            {draft && ext.kind === "INVOICE" && ext.status === "EXTRACTED" && (
              <>
                <h2 className="t-section mb-2">Draft to post</h2>
                <div className="grid grid-cols-2 gap-3 mb-3">
                  <div><label className="field-label" htmlFor="d-no">Supplier invoice no.</label><input id="d-no" className="input" value={draft.supplier_invoice_no ?? ""} onChange={(e) => setDraft({ ...draft, supplier_invoice_no: e.target.value })} /></div>
                  <div><label className="field-label" htmlFor="d-date">Invoice date</label><input id="d-date" type="date" className="input" value={draft.invoice_date ?? ""} onChange={(e) => setDraft({ ...draft, invoice_date: e.target.value })} /></div>
                </div>
                <table className="dt"><thead><tr><th>PO line</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num">Tax %</th></tr></thead>
                  <tbody>{draft.lines.map((l, i) => <tr key={i}><td className="font-mono text-[12px]">{l.po_item_id}</td>
                    {(["qty", "unit_price", "tax_rate"] as const).map((k) => <td key={k} className="num"><input aria-label={`${k} line ${i + 1}`} className="input !h-7 w-24 text-right" inputMode="decimal" value={l[k] ?? ""} onChange={(e) => setLine(i, k, e.target.value)} /></td>)}</tr>)}</tbody></table>
                <p className="text-[12px] text-muted mt-2">Posting uses the normal supplier-invoice rules (three-way match, duplicate check, period lock) under your permissions.</p>
              </>
            )}
            {draft && ext.kind === "DELIVERY_NOTE" && <p className="text-[13px]">Matched to {ext.matched.po?.number}. Use these quantities when you post the goods receipt. <Link className="underline" href="/m/procurement/receipts">Open goods receipts</Link></p>}
            {!draft && <p className="text-[13px] text-muted">A draft could not be prepared. Fix the items above or enter the document by hand.</p>}
            {ext.status === "EXTRACTED" && (
              <div className="flex justify-end gap-2 mt-4">
                <button className="btn" onClick={discard} disabled={busy}>Discard</button>
                {ext.kind === "INVOICE" && <button className="btn btn-primary" onClick={post} disabled={!canPost || busy} data-testid="ai-post" title={ext.blocking.length ? "Resolve the items marked Must fix first" : !can("accounts.supplier_invoice:post") ? "You do not have permission to post supplier invoices" : undefined}>Post supplier invoice</button>}
              </div>
            )}
            {ext.status !== "EXTRACTED" && <p className="text-[12.5px] text-muted mt-3">{titleCase(ext.status)}.</p>}
          </section>
        </div>
      )}
    </>
  );
}
