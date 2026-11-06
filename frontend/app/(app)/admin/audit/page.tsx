"use client";
import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime, titleCase } from "@/lib/format";
import { DataTable, type Col } from "@/components/DataTable";
import { Badge, Drawer, EmptyState, PageHeader, useDebounced } from "@/components/ui";

type Entry = { id: number; occurred_at: string; user_email: string | null; action: string; entity: string; entity_id: string | null;
  before: Record<string, unknown> | null; after: Record<string, unknown> | null; reason: string | null; request_id: string | null; ip: string | null; source: string };

const TONE: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  create: "success", update: "info", deactivate: "warning", delete: "danger", login_failed: "danger", login_blocked_locked: "danger",
  password_reset: "warning", download: "neutral", export: "neutral",
};

export default function AuditPage() {
  const { can } = useAuth();
  const [q, setQ] = useState(""); const dq = useDebounced(q);
  const [entity, setEntity] = useState(""); const [action, setAction] = useState("");
  const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Entry> | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<Entry | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api<Page<Entry>>("/admin/audit-logs", { params: { q: dq, entity, action, date_from: from ? new Date(from).toISOString() : "", date_to: to ? new Date(`${to}T23:59:59`).toISOString() : "", page, page_size: 50 } })
      .then(setData).finally(() => setLoading(false));
  }, [dq, entity, action, from, to, page]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [dq, entity, action, from, to]);

  if (!can("admin.audit:read")) return <EmptyState title="You don't have access to this area" />;

  const cols: Col<Entry>[] = [
    { key: "t", label: "When", render: (e) => <span className="tnum">{fmtDateTime(e.occurred_at)}</span> },
    { key: "u", label: "User", render: (e) => e.user_email ?? <span className="text-muted">system</span> },
    { key: "a", label: "Action", render: (e) => <Badge tone={TONE[e.action] ?? "neutral"}>{titleCase(e.action)}</Badge> },
    { key: "e", label: "Record", render: (e) => <span>{titleCase(e.entity)}{e.entity_id && <span className="text-muted font-mono text-[11.5px]"> #{e.entity_id}</span>}</span> },
    { key: "c", label: "Changes", render: (e) => e.after ? <span className="text-muted">{Object.keys(e.after).slice(0, 4).join(", ")}{Object.keys(e.after).length > 4 ? "…" : ""}</span> : <span className="text-muted">—</span> },
    { key: "go", label: "", render: () => <ChevronRight size={14} className="text-muted" /> },
  ];

  return (
    <>
      <PageHeader title="Audit log" description="Append-only record of every sensitive action. Entries cannot be edited or deleted, even by administrators." />
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input className="input !pl-8 w-64" placeholder="Search user, record, action…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" /></div>
        <input className="input !w-40" placeholder="Record type (e.g. item)" value={entity} onChange={(e) => setEntity(e.target.value.trim().toLowerCase())} aria-label="Record type" />
        <select className="input !w-auto" value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">
          <option value="">Action: all</option>
          {["create", "update", "deactivate", "delete", "login", "login_failed", "logout", "password_reset", "password_changed", "download", "export", "new_version"].map((a) => <option key={a} value={a}>{titleCase(a)}</option>)}
        </select>
        <input type="date" className="input !w-auto" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From date" />
        <span className="text-muted text-[12px]">to</span>
        <input type="date" className="input !w-auto" value={to} onChange={(e) => setTo(e.target.value)} aria-label="To date" />
      </div>
      <DataTable columns={cols} rows={data?.items ?? []} loading={loading} page={page} pageSize={50} total={data?.total ?? 0} onPage={setPage} onRowClick={setOpen} />
      <Drawer open={!!open} onClose={() => setOpen(null)} title={open ? `${titleCase(open.action)} · ${titleCase(open.entity)}${open.entity_id ? ` #${open.entity_id}` : ""}` : ""} subtitle={open ? fmtDateTime(open.occurred_at) : undefined} width={620}>
        {open && (
          <div className="space-y-4 text-[13px]">
            <dl className="grid grid-cols-[110px_1fr] gap-y-1.5">
              <dt className="text-muted">User</dt><dd>{open.user_email ?? "system"}</dd>
              <dt className="text-muted">Source</dt><dd>{open.source}</dd>
              <dt className="text-muted">IP address</dt><dd className="font-mono text-[12px]">{open.ip ?? "—"}</dd>
              <dt className="text-muted">Request ID</dt><dd className="font-mono text-[12px] break-all">{open.request_id ?? "—"}</dd>
              {open.reason && <><dt className="text-muted">Reason</dt><dd>{open.reason}</dd></>}
            </dl>
            <DiffTable before={open.before} after={open.after} />
          </div>
        )}
      </Drawer>
    </>
  );
}

function DiffTable({ before, after }: { before: Record<string, unknown> | null; after: Record<string, unknown> | null }) {
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])].filter((k) => k !== "updated_at");
  if (!keys.length) return <p className="text-muted">No field data recorded.</p>;
  const show = (v: unknown) => v === null || v === undefined ? <span className="text-muted">—</span> : typeof v === "object" ? JSON.stringify(v) : String(v);
  return (
    <table className="dt panel">
      <thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
      <tbody>{keys.map((k) => <tr key={k}><td className="font-mono text-[12px]">{k}</td><td style={{ whiteSpace: "normal" }}>{before ? show(before[k]) : <span className="text-muted">—</span>}</td><td style={{ whiteSpace: "normal" }}>{show(after?.[k])}</td></tr>)}</tbody>
    </table>
  );
}
