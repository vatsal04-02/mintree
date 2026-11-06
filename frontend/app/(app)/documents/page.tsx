"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Download, FileText, Link2, Plus, Search, Upload, X } from "lucide-react";
import { api, ApiError, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime, titleCase } from "@/lib/format";
import { DataTable, type Col } from "@/components/DataTable";
import { Badge, ConfirmDialog, Drawer, EmptyState, PageHeader, useDebounced, useToast } from "@/components/ui";

type Version = { version_no: number; filename: string; size_bytes: number; sha256: string; note: string | null; uploaded_at: string };
type Doc = { id: number; title: string; doc_type: string; description: string | null; current_version_no: number; updated_at: string; versions: Version[]; links: { id: number; entity_type: string; entity_id: string }[] };

const TYPES = ["COA", "SUPPLIER_INVOICE", "SUPPLIER_DOCUMENT", "QUALITY_CERTIFICATE", "FORMULATION", "BATCH_RECORD", "INSPECTION_RECORD", "PRODUCT_IMAGE", "MARKETING_ASSET", "COURIER_LABEL", "CUSTOMER_DOCUMENT", "OTHER"];
const ENTITY_TYPES = ["supplier", "customer", "item", "brand", "warehouse"];
const size = (n: number) => n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.csv,.txt,.xlsx,.xls,.docx,.doc,.pptx";

export default function DocumentsPage() {
  const { can } = useAuth();
  const toast = useToast();
  const params = useSearchParams();
  const [q, setQ] = useState(""); const dq = useDebounced(q);
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Doc> | null>(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api<Page<Doc>>("/documents", { params: { q: dq, doc_type: type, page, page_size: 25 } }).then(setData).catch((e) => toast("error", e.message)).finally(() => setLoading(false));
  }, [dq, type, page, toast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [dq, type]);
  useEffect(() => { if (params.get("new") === "1" && can("documents.document:create")) setUploading(true); }, [params, can]);

  if (!can("documents.document:read")) return <EmptyState title="You don't have access to this area" />;
  const open = data?.items.find((d) => d.id === openId) ?? null;

  const cols: Col<Doc>[] = [
    { key: "t", label: "Title", render: (d) => <span className="inline-flex items-center gap-2 font-medium"><FileText size={14} className="text-muted" />{d.title}</span> },
    { key: "ty", label: "Type", render: (d) => <Badge>{titleCase(d.doc_type)}</Badge> },
    { key: "f", label: "Current file", render: (d) => { const v = d.versions.find((x) => x.version_no === d.current_version_no); return v ? <span className="text-muted">{v.filename} · {size(v.size_bytes)}</span> : null; } },
    { key: "v", label: "Version", align: "right", render: (d) => `v${d.current_version_no}` },
    { key: "l", label: "Linked to", render: (d) => d.links.length ? d.links.map((l) => `${l.entity_type} ${l.entity_id}`).join(", ") : <span className="text-muted">—</span> },
    { key: "u", label: "Updated", render: (d) => <span className="tnum text-muted">{fmtDateTime(d.updated_at)}</span> },
  ];

  return (
    <>
      <PageHeader title="Document centre" description="COAs, supplier invoices, certificates and batch records — versioned, access-controlled and linked to the records they support."
        actions={can("documents.document:create") && <button className="btn btn-primary" onClick={() => setUploading(true)}><Plus size={14} />Upload document</button>} />
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input className="input !pl-8 w-64" placeholder="Search documents…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" /></div>
        <select className="input !w-auto" value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
          <option value="">Type: all</option>{TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</select>
      </div>
      <DataTable columns={cols} rows={data?.items ?? []} loading={loading} page={page} pageSize={25} total={data?.total ?? 0} onPage={setPage} onRowClick={(d) => setOpenId(d.id)}
        empty={<EmptyState title="No documents yet" hint="Upload a COA, invoice or certificate to get started." />} />
      <UploadDrawer open={uploading} onClose={() => setUploading(false)} onDone={() => { setUploading(false); load(); }} />
      <DetailDrawer doc={open} onClose={() => setOpenId(null)} onChanged={load} />
    </>
  );
}

function UploadDrawer({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState(""); const [type, setType] = useState("OTHER"); const [desc, setDesc] = useState("");
  const [et, setEt] = useState(""); const [eid, setEid] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (open) { setTitle(""); setType("OTHER"); setDesc(""); setEt(""); setEid(""); setErr(null); } }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const f = file.current?.files?.[0];
    if (!f) { setErr("Choose a file"); return; }
    const fd = new FormData(); fd.append("file", f); fd.append("title", title.trim() || f.name); fd.append("doc_type", type);
    if (desc) fd.append("description", desc); if (et) { fd.append("entity_type", et); fd.append("entity_id", eid); }
    setBusy(true); setErr(null);
    try { await api("/documents", { form: fd }); toast("success", "Document uploaded"); onDone(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Upload failed"); } finally { setBusy(false); }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Upload document" width={480}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" form="upload-form" disabled={busy}><Upload size={14} />{busy ? "Uploading…" : "Upload"}</button></>}>
      <form id="upload-form" onSubmit={submit} className="space-y-3.5" noValidate>
        {err && <div role="alert" className="px-3 py-2 rounded-md text-[13px] text-danger" style={{ background: "color-mix(in srgb, var(--danger) 10%, transparent)" }}>{err}</div>}
        <div><label className="field-label" htmlFor="d-file">File *</label>
          <input id="d-file" ref={file} type="file" accept={ACCEPT} className="input !h-auto py-1.5" onChange={(e) => { const f = e.target.files?.[0]; if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, "")); }} />
          <p className="text-muted text-[11.5px] mt-1">PDF, images, Office files, CSV or text. Up to 25 MB.</p></div>
        <div><label className="field-label" htmlFor="d-title">Title *</label><input id="d-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div><label className="field-label" htmlFor="d-type">Type</label><select id="d-type" className="input" value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</select></div>
        <div><label className="field-label" htmlFor="d-desc">Description</label><textarea id="d-desc" className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
        <fieldset className="grid grid-cols-2 gap-3"><legend className="field-label">Link to a record (optional)</legend>
          <select className="input" value={et} onChange={(e) => setEt(e.target.value)} aria-label="Record type"><option value="">None</option>{ENTITY_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</select>
          <input className="input" placeholder="Record ID" value={eid} disabled={!et} onChange={(e) => setEid(e.target.value)} aria-label="Record ID" /></fieldset>
      </form>
    </Drawer>
  );
}

function DetailDrawer({ doc, onClose, onChanged }: { doc: Doc | null; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const vfile = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState(""); const [busy, setBusy] = useState(false);
  const [et, setEt] = useState("supplier"); const [eid, setEid] = useState("");
  const [del, setDel] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok: string) => { setBusy(true); try { await fn(); toast("success", ok); onChanged(); } catch (e) { toast("error", e instanceof ApiError ? e.message : "Failed"); } finally { setBusy(false); } };

  return (
    <Drawer open={!!doc} onClose={onClose} title={doc?.title ?? ""} subtitle={doc ? `${titleCase(doc.doc_type)} · version ${doc.current_version_no}` : undefined} width={560}>
      {doc && (
        <div className="space-y-6 text-[13px]">
          {doc.description && <p className="text-muted">{doc.description}</p>}
          <section>
            <h3 className="text-[12px] font-semibold text-muted mb-2">Versions</h3>
            <ul className="panel divide-y divide-line">
              {[...doc.versions].reverse().map((v) => (
                <li key={v.version_no} className="px-3 py-2.5 flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate">v{v.version_no} · {v.filename} {v.version_no === doc.current_version_no && <Badge tone="success">Current</Badge>}</div>
                    <div className="text-muted text-[11.5px] tnum">{size(v.size_bytes)} · {fmtDateTime(v.uploaded_at)}{v.note ? ` · ${v.note}` : ""}</div>
                    <div className="text-muted text-[10.5px] font-mono truncate" title={v.sha256}>SHA-256 {v.sha256.slice(0, 16)}…</div>
                  </div>
                  {can("documents.document:download") && <a className="btn btn-sm" href={`/api/documents/${doc.id}/download?version=${v.version_no}`}><Download size={13} />Download</a>}
                </li>
              ))}
            </ul>
            {can("documents.document:update") && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <input ref={vfile} type="file" accept={ACCEPT} className="input !h-auto py-1 !w-auto flex-1 min-w-48" aria-label="New version file" />
                <input className="input !w-40" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Version note" />
                <button className="btn btn-sm" disabled={busy} onClick={() => {
                  const f = vfile.current?.files?.[0]; if (!f) { toast("error", "Choose a file"); return; }
                  const fd = new FormData(); fd.append("file", f); if (note) fd.append("note", note);
                  run(() => api(`/documents/${doc.id}/versions`, { form: fd }).then(() => { setNote(""); if (vfile.current) vfile.current.value = ""; }), "New version uploaded");
                }}><Upload size={13} />Upload version</button>
              </div>
            )}
          </section>
          <section>
            <h3 className="text-[12px] font-semibold text-muted mb-2">Linked records</h3>
            {doc.links.length === 0 ? <p className="text-muted">Not linked to any record.</p> : (
              <ul className="flex flex-wrap gap-2">{doc.links.map((l) => (
                <li key={l.id} className="badge tone-info !h-6 gap-1.5"><Link2 size={11} />{titleCase(l.entity_type)} {l.entity_id}
                  {can("documents.document:update") && <button aria-label="Remove link" onClick={() => run(() => api(`/documents/${doc.id}/links/${l.id}`, { method: "DELETE" }), "Link removed")}><X size={12} /></button>}</li>))}</ul>)}
            {can("documents.document:update") && (
              <div className="flex gap-2 mt-3">
                <select className="input !w-36" value={et} onChange={(e) => setEt(e.target.value)} aria-label="Record type">{ENTITY_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</select>
                <input className="input !w-32" placeholder="Record ID" value={eid} onChange={(e) => setEid(e.target.value)} aria-label="Record ID" />
                <button className="btn btn-sm" disabled={!eid || busy} onClick={() => run(() => api(`/documents/${doc.id}/links`, { body: { entity_type: et, entity_id: eid.trim() } }).then(() => setEid("")), "Linked")}>Add link</button>
              </div>)}
          </section>
          {can("documents.document:delete") && (
            <section className="pt-4 border-t border-line">
              <button className="btn btn-sm btn-danger" onClick={() => setDel(true)}>Delete document</button>
              <p className="text-muted text-[11.5px] mt-1.5">Removes it from lists. Files and history are retained for traceability.</p>
            </section>)}
          <ConfirmDialog open={del} danger busy={busy} title="Delete this document?" confirmLabel="Delete" body="It will disappear from the document centre. This action is recorded in the audit log."
            onCancel={() => setDel(false)} onConfirm={() => run(() => api(`/documents/${doc.id}`, { method: "DELETE" }).then(() => { setDel(false); onClose(); }), "Document deleted")} />
        </div>
      )}
    </Drawer>
  );
}
