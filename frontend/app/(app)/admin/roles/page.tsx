"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Lock, Plus } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Drawer, EmptyState, PageHeader, useToast } from "@/components/ui";

type Role = { id: number; code: string; name: string; description: string | null; is_system: boolean; permission_codes: string[]; user_count: number };
type Perm = { id: number; code: string; resource: string; action: string };

const ACTIONS = ["read", "create", "update", "delete", "export", "download"];
const GROUP_LABEL: Record<string, string> = { admin: "Administration", masters: "Master data", documents: "Documents" };
const RES_LABEL = (r: string) => r.split(".")[1].replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export default function RolesPage() {
  const { can, me } = useAuth();
  const toast = useToast();
  const [roles, setRoles] = useState<Role[]>([]);
  const [perms, setPerms] = useState<Perm[]>([]);
  const [selId, setSelId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async (keep?: number) => {
    const [r, p] = await Promise.all([api<Role[]>("/admin/roles"), api<Perm[]>("/admin/permissions")]);
    setRoles(r); setPerms(p); setSelId((cur) => keep ?? cur ?? r[0]?.id ?? null);
  }, []);
  useEffect(() => { if (can("admin.role:read")) load().catch(() => {}); }, [can, load]);
  const sel = roles.find((r) => r.id === selId);
  useEffect(() => { if (sel) setDraft(new Set(sel.permission_codes)); }, [sel]);

  const mine = useMemo(() => new Set(me?.permissions ?? []), [me]);
  const byResource = useMemo(() => {
    const m = new Map<string, Set<string>>();
    perms.forEach((p) => { if (!m.has(p.resource)) m.set(p.resource, new Set()); m.get(p.resource)!.add(p.action); });
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [perms]);

  if (!can("admin.role:read")) return <EmptyState title="You don't have access to this area" />;
  const locked = !sel || sel.code === "system_admin" || !can("admin.role:update") || !sel.permission_codes.every((p) => mine.has(p));
  const dirty = sel && (draft.size !== sel.permission_codes.length || sel.permission_codes.some((p) => !draft.has(p)));

  async function save() {
    if (!sel) return; setBusy(true);
    try { await api(`/admin/roles/${sel.id}`, { method: "PATCH", body: { permission_codes: [...draft] } }); toast("success", "Role permissions saved"); await load(sel.id); }
    catch (e) { toast("error", e instanceof ApiError ? e.message : "Save failed"); } finally { setBusy(false); }
  }
  const toggle = (code: string) => setDraft((d) => { const n = new Set(d); if (n.has(code)) n.delete(code); else n.add(code); return n; });

  return (
    <>
      <PageHeader title="Roles & permissions" description="Permissions are enforced by the backend on every request. You can only grant permissions you hold yourself."
        actions={can("admin.role:create") && <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={14} />New role</button>} />
      <div className="grid md:grid-cols-[260px_1fr] gap-5 items-start">
        <ul className="panel divide-y divide-line overflow-hidden" aria-label="Roles">
          {roles.map((r) => (
            <li key={r.id}>
              <button onClick={() => setSelId(r.id)} className={`w-full text-left px-3.5 py-2.5 ${r.id === selId ? "bg-surface-2" : "hover:bg-surface-2/60"}`} aria-current={r.id === selId}>
                <div className="flex items-center justify-between gap-2"><span className="text-[13px] font-medium truncate">{r.name}</span>{r.is_system && <Lock size={11} className="text-muted shrink-0" aria-label="System role" />}</div>
                <div className="text-muted text-[11.5px] tnum">{r.permission_codes.length} permissions · {r.user_count} user{r.user_count === 1 ? "" : "s"}</div>
              </button>
            </li>
          ))}
        </ul>

        {sel && (
          <section className="panel overflow-hidden">
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line">
              <div className="min-w-0"><h2 className="t-section flex items-center gap-2">{sel.name}{sel.code === "system_admin" && <Badge tone="info">Always full access</Badge>}</h2>
                <p className="text-muted text-[12px] truncate">{sel.description}</p></div>
              {!locked && <button className="btn btn-primary" disabled={!dirty || busy} onClick={save}>{busy ? "Saving…" : "Save changes"}</button>}
            </div>
            {locked && sel.code !== "system_admin" && <p className="px-4 py-2 text-[12px] text-muted bg-surface-2/60 border-b border-line">Read-only for you: this role holds permissions above your own, or you lack permission to edit roles.</p>}
            <div className="overflow-auto">
              <table className="dt">
                <thead><tr><th>Resource</th>{ACTIONS.map((a) => <th key={a} className="!text-center capitalize">{a}</th>)}</tr></thead>
                <tbody>
                  {byResource.map(([res, acts], i) => {
                    const grp = res.split(".")[0];
                    const newGroup = i === 0 || byResource[i - 1][0].split(".")[0] !== grp;
                    return [
                      newGroup && <tr key={`g-${grp}`}><td colSpan={ACTIONS.length + 1} className="!h-8 bg-surface-2/60 text-[12px] text-muted font-medium">{GROUP_LABEL[grp] ?? grp}</td></tr>,
                      <tr key={res}>
                        <td>{RES_LABEL(res)}</td>
                        {ACTIONS.map((a) => {
                          const code = `${res}:${a}`;
                          return <td key={a} className="text-center">{acts.has(a) ? (
                            <input type="checkbox" aria-label={code} checked={draft.has(code)} disabled={locked || !mine.has(code)} onChange={() => toggle(code)} />
                          ) : <span className="text-line">–</span>}</td>;
                        })}
                      </tr>,
                    ];
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
      <NewRole open={creating} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); load(id); }} />
    </>
  );
}

function NewRole({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const toast = useToast();
  const [code, setCode] = useState(""); const [name, setName] = useState(""); const [desc, setDesc] = useState(""); const [err, setErr] = useState<Record<string, string>>({});
  useEffect(() => { if (open) { setCode(""); setName(""); setDesc(""); setErr({}); } }, [open]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try { const r = await api<Role>("/admin/roles", { body: { code, name, description: desc || null, permission_codes: [] } }); toast("success", "Role created — now choose its permissions"); onCreated(r.id); }
    catch (e) { if (e instanceof ApiError) { if (e.fields) setErr(e.fields); else toast("error", e.message); } }
  }
  return (
    <Drawer open={open} onClose={onClose} title="New role" width={440} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" form="role-form" disabled={!code || !name}>Create role</button></>}>
      <form id="role-form" onSubmit={submit} className="space-y-3.5" noValidate>
        <div><label className="field-label" htmlFor="r-name">Name *</label><input id="r-name" className="input" value={name} onChange={(e) => { setName(e.target.value); if (!code) setCode(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "")); }} /></div>
        <div><label className="field-label" htmlFor="r-code">Code *</label><input id="r-code" className="input font-mono" value={code} onChange={(e) => setCode(e.target.value)} aria-invalid={!!err.code} />
          <p className={`text-[11.5px] mt-1 ${err.code ? "text-danger" : "text-muted"}`}>{err.code ?? "Lowercase letters, digits and underscores. Cannot be changed later."}</p></div>
        <div><label className="field-label" htmlFor="r-desc">Description</label><textarea id="r-desc" className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></div>
      </form>
    </Drawer>
  );
}

