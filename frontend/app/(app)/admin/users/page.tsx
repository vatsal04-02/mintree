"use client";
import { useCallback, useEffect, useState } from "react";
import { KeyRound, Plus, Search } from "lucide-react";
import { api, ApiError, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime } from "@/lib/format";
import { DataTable, type Col } from "@/components/DataTable";
import { Badge, Drawer, EmptyState, PageHeader, useDebounced, useToast } from "@/components/ui";

type Role = { id: number; code: string; name: string; permission_codes: string[] };
type User = { id: number; email: string; full_name: string; department_id: number | null; department_name: string | null; is_active: boolean;
  must_change_password: boolean; locked_until: string | null; last_login_at: string | null; roles: { id: number; name: string }[] };
type Dept = { id: number; name: string };

export default function UsersPage() {
  const { can, me } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState(""); const dq = useDebounced(q);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<User> | null>(null);
  const [loading, setLoading] = useState(true);
  const [roles, setRoles] = useState<Role[]>([]);
  const [depts, setDepts] = useState<Dept[]>([]);
  const [editing, setEditing] = useState<User | "new" | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api<Page<User>>("/admin/users", { params: { q: dq, page, page_size: 25 } }).then(setData).catch((e) => toast("error", e.message)).finally(() => setLoading(false));
  }, [dq, page, toast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [dq]);
  useEffect(() => {
    if (can("admin.role:read")) api<Role[]>("/admin/roles").then(setRoles).catch(() => {});
    if (can("admin.department:read")) api<Page<Dept>>("/admin/departments", { params: { page_size: 200 } }).then((p) => setDepts(p.items)).catch(() => {});
  }, [can]);

  if (!can("admin.user:read")) return <EmptyState title="You don't have access to this area" />;

  const cols: Col<User>[] = [
    { key: "name", label: "Name", render: (u) => <span className="font-medium">{u.full_name}</span> },
    { key: "email", label: "Email", render: (u) => u.email },
    { key: "dept", label: "Department", render: (u) => u.department_name ?? <span className="text-muted">—</span> },
    { key: "roles", label: "Roles", render: (u) => u.roles.length ? u.roles.map((r) => r.name).join(", ") : <span className="text-muted">None</span> },
    { key: "last", label: "Last sign-in", render: (u) => <span className="tnum text-muted">{fmtDateTime(u.last_login_at)}</span> },
    { key: "status", label: "Status", render: (u) => !u.is_active ? <Badge>Inactive</Badge> : u.locked_until && new Date(u.locked_until) > new Date() ? <Badge tone="danger">Locked</Badge> : u.must_change_password ? <Badge tone="warning">Password reset</Badge> : <Badge tone="success">Active</Badge> },
  ];

  return (
    <>
      <PageHeader title="Users" description="Accounts, departments and role assignments. Changes are audited and take effect immediately."
        actions={can("admin.user:create") && <button className="btn btn-primary" onClick={() => setEditing("new")}><Plus size={14} />New user</button>} />
      <div className="relative mb-3 w-72">
        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
        <input className="input !pl-8" placeholder="Search name or email…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
      </div>
      <DataTable columns={cols} rows={data?.items ?? []} loading={loading} page={page} pageSize={25} total={data?.total ?? 0} onPage={setPage} onRowClick={setEditing} />
      <UserForm target={editing} roles={roles} depts={depts} selfId={me?.id} canEdit={can("admin.user:update")} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
    </>
  );
}

function UserForm({ target, roles, depts, selfId, canEdit, onClose, onSaved }: {
  target: User | "new" | null; roles: Role[]; depts: Dept[]; selfId?: number; canEdit: boolean; onClose: () => void; onSaved: () => void;
}) {
  const toast = useToast();
  const { me } = useAuth();
  const isNew = target === "new";
  const [f, setF] = useState({ email: "", full_name: "", department_id: "" as string | number, is_active: true, role_ids: [] as number[], password: "" });
  const [err, setErr] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [reset, setReset] = useState<string | null>(null);

  useEffect(() => {
    if (!target) return;
    setErr({}); setReset(null);
    if (target === "new") setF({ email: "", full_name: "", department_id: "", is_active: true, role_ids: [], password: "" });
    else setF({ email: target.email, full_name: target.full_name, department_id: target.department_id ?? "", is_active: target.is_active, role_ids: target.roles.map((r) => r.id), password: "" });
  }, [target]);

  // A role can only be granted if you hold all its permissions (backend enforces; UI mirrors it).
  const mine = new Set(me?.permissions ?? []);
  const grantable = (r: Role) => r.permission_codes.every((p) => mine.has(p));
  const readOnly = !isNew && !canEdit;

  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr({});
    try {
      const dept = f.department_id === "" ? null : Number(f.department_id);
      if (isNew) await api("/admin/users", { body: { email: f.email.trim(), full_name: f.full_name, department_id: dept, role_ids: f.role_ids, temporary_password: f.password } });
      else await api(`/admin/users/${(target as User).id}`, { method: "PATCH", body: { full_name: f.full_name, department_id: dept, is_active: f.is_active, role_ids: f.role_ids } });
      toast("success", isNew ? "User created" : "User saved"); onSaved();
    } catch (e) {
      if (e instanceof ApiError) { if (e.fields) setErr(e.fields); else toast("error", e.message); }
    } finally { setBusy(false); }
  }

  async function resetPassword() {
    const pw = `Mt-${Math.random().toString(36).slice(2, 8)}${Math.random().toString(36).slice(2, 6).toUpperCase()}9`;
    try {
      await api(`/admin/users/${(target as User).id}/reset-password`, { body: { temporary_password: pw } });
      setReset(pw); toast("success", "Password reset. All sessions were signed out.");
    } catch (e) { toast("error", e instanceof ApiError ? e.message : "Reset failed"); }
  }

  const selfEdit = !isNew && (target as User | null)?.id === selfId;

  return (
    <Drawer open={!!target} onClose={onClose} title={isNew ? "New user" : readOnly ? "User" : "Edit user"} subtitle={!isNew && target ? (target as User).email : undefined}
      footer={<><button className="btn" onClick={onClose}>{readOnly ? "Close" : "Cancel"}</button>{!readOnly && <button className="btn btn-primary" form="user-form" disabled={busy}>{busy ? "Saving…" : isNew ? "Create user" : "Save changes"}</button>}</>}>
      <form id="user-form" onSubmit={save} noValidate className="space-y-3.5">
        <div><label className="field-label" htmlFor="u-name">Full name <span className="text-danger">*</span></label>
          <input id="u-name" className="input" value={f.full_name} disabled={readOnly} onChange={(e) => setF({ ...f, full_name: e.target.value })} aria-invalid={!!err.full_name} /></div>
        <div><label className="field-label" htmlFor="u-email">Email <span className="text-danger">*</span></label>
          <input id="u-email" className="input" type="email" value={f.email} disabled={!isNew} onChange={(e) => setF({ ...f, email: e.target.value })} aria-invalid={!!err.email} />
          {err.email && <p className="text-danger text-[0.75rem] mt-1">{err.email}</p>}</div>
        {isNew && <div><label className="field-label" htmlFor="u-pw">Temporary password <span className="text-danger">*</span></label>
          <input id="u-pw" className="input" type="text" autoComplete="off" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} aria-invalid={!!err.temporary_password} />
          <p className={`text-[0.75rem] mt-1 ${err.temporary_password ? "text-danger" : "text-muted"}`}>{err.temporary_password ?? "Min 10 chars, mixed case, a digit. The user must change it at first sign-in."}</p></div>}
        <div><label className="field-label" htmlFor="u-dept">Department</label>
          <select id="u-dept" className="input" value={f.department_id} disabled={readOnly} onChange={(e) => setF({ ...f, department_id: e.target.value })}>
            <option value="">None</option>{depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></div>
        <fieldset disabled={readOnly}>
          <legend className="field-label">Roles</legend>
          <div className="panel divide-y divide-line">
            {roles.map((r) => {
              const ok = grantable(r) || f.role_ids.includes(r.id);
              return (
                <label key={r.id} className={`flex items-center gap-2.5 px-3 h-9 text-[0.8125rem] ${ok ? "cursor-pointer" : "opacity-45"}`} title={ok ? undefined : "You cannot assign a role with permissions you don't hold"}>
                  <input type="checkbox" checked={f.role_ids.includes(r.id)} disabled={!ok || readOnly}
                    onChange={(e) => setF({ ...f, role_ids: e.target.checked ? [...f.role_ids, r.id] : f.role_ids.filter((x) => x !== r.id) })} />
                  <span className="flex-1">{r.name}</span><span className="text-muted text-[0.75rem] tnum">{r.permission_codes.length} perms</span>
                </label>
              );
            })}
          </div>
        </fieldset>
        {!isNew && !readOnly && (
          <label className={`flex items-center gap-2 text-[0.8125rem] ${selfEdit ? "opacity-50" : "cursor-pointer"}`}>
            <input type="checkbox" checked={f.is_active} disabled={selfEdit} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />
            Account active {selfEdit && <span className="text-muted text-[0.75rem]">(you can&apos;t deactivate yourself)</span>}
          </label>
        )}
        {!isNew && !readOnly && (
          <div className="pt-2 border-t border-line">
            <button type="button" className="btn btn-sm" onClick={resetPassword}><KeyRound size={13} />Reset password</button>
            {reset && <div className="mt-2.5 panel p-3 text-[0.75rem]" role="status">Temporary password (shown once): <code className="font-mono font-semibold select-all">{reset}</code></div>}
          </div>
        )}
      </form>
    </Drawer>
  );
}
