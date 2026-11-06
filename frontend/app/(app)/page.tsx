"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime, titleCase } from "@/lib/format";
import { MODULE_BY_ID } from "@/lib/modules";
import { Badge, PageHeader } from "@/components/ui";

type Counts = Record<string, number | null>;
type Audit = { id: number; occurred_at: string; user_email: string | null; action: string; entity: string; entity_id: string | null };

// Honest status per PRD module (see docs/PRD_COVERAGE.md). "Partial" means built and usable, with the stated limit.
const MODULES: { n: string; id: string; name: string; status: "live" | "partial"; note?: string }[] = [
  { n: "01", id: "production", name: "Production", status: "partial", note: "no capacity scheduling" },
  { n: "02", id: "procurement", name: "Purchase", status: "live" },
  { n: "03", id: "dispatch", name: "Dispatch", status: "partial", note: "whole-order shipments; courier is a stand-in" },
  { n: "04", id: "rnd", name: "R&D", status: "live" },
  { n: "05", id: "quality", name: "Quality", status: "partial", note: "no sampling plans" },
  { n: "06", id: "maintenance", name: "Maintenance", status: "live" },
  { n: "07", id: "accounts", name: "Accounts", status: "partial", note: "no GST filing / e-invoice" },
  { n: "08", id: "ecommerce", name: "E-commerce", status: "partial", note: "file & webhook import; live marketplace APIs pending" },
  { n: "09", id: "branding", name: "Meta & Branding", status: "partial", note: "publishing recorded manually" },
];

export default function Overview() {
  const { me, can } = useAuth();
  const [counts, setCounts] = useState<Counts>({});
  const [activity, setActivity] = useState<Audit[] | null>(null);

  useEffect(() => {
    const jobs: [string, string, Record<string, string>?, string?][] = [
      ["items", "/masters/items", {}, "masters.item:read"], ["suppliers", "/masters/suppliers", {}, "masters.supplier:read"],
      ["pendingSuppliers", "/masters/suppliers", { approval_status: "PENDING" }, "masters.supplier:read"],
      ["customers", "/masters/customers", {}, "masters.customer:read"], ["warehouses", "/masters/warehouses", {}, "masters.warehouse:read"],
      ["documents", "/documents", {}, "documents.document:read"], ["users", "/admin/users", { is_active: "true" }, "admin.user:read"],
    ];
    jobs.filter(([, , , perm]) => perm && can(perm)).forEach(([k, path, params]) =>
      api<Page<unknown>>(path, { params: { page_size: 1, ...params } }).then((p) => setCounts((c) => ({ ...c, [k]: p.total }))).catch(() => setCounts((c) => ({ ...c, [k]: null }))));
    if (can("admin.audit:read")) api<Page<Audit>>("/admin/audit-logs", { params: { page_size: 8 } }).then((p) => setActivity(p.items)).catch(() => setActivity([]));
  }, [can]);

  const kpis = [
    { key: "items", label: "Items", href: "/masters/items", perm: "masters.item:read" },
    { key: "suppliers", label: "Suppliers", href: "/masters/suppliers", perm: "masters.supplier:read" },
    { key: "customers", label: "Customers", href: "/masters/customers", perm: "masters.customer:read" },
    { key: "warehouses", label: "Warehouses", href: "/masters/warehouses", perm: "masters.warehouse:read" },
    { key: "documents", label: "Documents", href: "/documents", perm: "documents.document:read" },
    { key: "users", label: "Active users", href: "/admin/users", perm: "admin.user:read" },
  ].filter((k) => can(k.perm));

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  return (
    <>
      <PageHeader title={`${greeting}, ${me?.full_name.split(" ")[0]}`} description="Foundation workspace. Operational dashboards appear here as each module goes live." />

      {kpis.length > 0 && (
        <section aria-label="Master data" className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-6">
          {kpis.map((k) => (
            <Link key={k.key} href={k.href} className="panel p-3.5 group hover:border-primary/50 transition-colors">
              <div className="flex items-center justify-between text-muted text-[12px]">{k.label}<ArrowUpRight size={13} className="opacity-0 group-hover:opacity-100 transition-opacity" /></div>
              <div className="text-[26px] leading-9 font-semibold tnum mt-0.5">{counts[k.key] === undefined ? <span className="skeleton inline-block w-10 h-6 align-middle" /> : counts[k.key] ?? "—"}</div>
            </Link>
          ))}
        </section>
      )}

      <div className="grid lg:grid-cols-[1fr_380px] gap-5">
        <div className="space-y-5">
          {(counts.pendingSuppliers ?? 0) > 0 && (
            <section className="panel">
              <h2 className="px-4 py-3 text-[14px] font-semibold border-b border-line">Needs attention</h2>
              <Link href="/masters/suppliers?approval_status=PENDING" className="flex items-center justify-between px-4 h-11 hover:bg-surface-2 rounded-b-lg">
                <span className="text-[13px]">{counts.pendingSuppliers} supplier{counts.pendingSuppliers === 1 ? "" : "s"} awaiting approval</span>
                <Badge tone="warning">Pending</Badge>
              </Link>
            </section>
          )}
          <section className="panel">
            <div className="px-4 py-3 border-b border-line"><h2 className="text-[14px] font-semibold">Modules</h2><p className="text-muted text-[12px]">Every module is built; “Partial” names what is still missing (full detail in docs/PRD_COVERAGE.md).</p></div>
            <ul className="divide-y divide-line">
              <li className="flex items-center justify-between px-4 h-10 text-[13px]"><span><span className="text-muted tnum mr-2.5">00</span>Foundation — access, audit, master data, documents</span><Badge tone="success">Live</Badge></li>
              {MODULES.map((m) => {
                const mod = MODULE_BY_ID[m.id];
                const allowed = mod?.screens.some((sc) => can(sc.perm));
                const row = (
                  <>
                    <span><span className="text-muted tnum mr-2.5">{m.n}</span>{m.name}{m.note && <span className="text-muted text-[12px] ml-2">— {m.note}</span>}</span>
                    <Badge tone={m.status === "live" ? "success" : "info"}>{m.status === "live" ? "Live" : "Partial"}</Badge>
                  </>
                );
                return allowed
                  ? <li key={m.n}><Link href={`/m/${m.id}`} className="flex items-center justify-between px-4 h-10 text-[13px] hover:bg-primary/5">{row}</Link></li>
                  : <li key={m.n} className="flex items-center justify-between px-4 h-10 text-[13px] opacity-60">{row}</li>;
              })}
            </ul>
          </section>
        </div>

        <section className="panel self-start">
          <h2 className="px-4 py-3 text-[14px] font-semibold border-b border-line">Recent activity</h2>
          {!can("admin.audit:read") ? <p className="px-4 py-6 text-muted text-[13px]">Activity feed is available to auditors and administrators.</p>
            : activity === null ? <div className="p-4 space-y-3">{Array.from({ length: 5 }).map((_, i) => <div key={i} className="skeleton h-4" />)}</div>
            : activity.length === 0 ? <p className="px-4 py-6 text-muted text-[13px]">No activity yet.</p>
            : <ul className="divide-y divide-line">{activity.map((a) => (
              <li key={a.id} className="px-4 py-2.5">
                <div className="text-[13px]"><span className="font-medium">{titleCase(a.action)}</span> <span className="text-muted">{a.entity}{a.entity_id ? ` #${a.entity_id}` : ""}</span></div>
                <div className="text-muted text-[11.5px] tnum">{a.user_email ?? "system"} · {fmtDateTime(a.occurred_at)}</div>
              </li>))}</ul>}
          {can("admin.audit:read") && <Link href="/admin/audit" className="block px-4 py-2.5 border-t border-line text-[12.5px] text-primary hover:bg-surface-2 rounded-b-lg">View full audit log</Link>}
        </section>
      </div>
    </>
  );
}
