"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, Bell, Check, FileText, Package, ShieldCheck, ShoppingCart } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fmtAgo, fmtDateTime } from "@/lib/format";
import { EmptyState, PageHeader, useToast } from "@/components/ui";

type N = { id: number; kind: string; title: string; body: string | null; link: string | null; priority: string; created_at: string; read: boolean };

/** An icon from the wording of the alert, so a purchase order, a quotation and a quality hold are told apart at a glance. */
const iconFor = (n: N) => {
  const t = `${n.kind} ${n.title}`.toLowerCase();
  return /(po\/|purchase|rfq|quotation|supplier)/.test(t) ? ShoppingCart : /(qc|quality|inspection|deviation|capa|quarantine)/.test(t) ? ShieldCheck
    : /(order|shipment|dispatch|stock|batch)/.test(t) ? Package : /(invoice|approval|document)/.test(t) ? FileText : Bell;
};

const groupName = (iso: string) => {
  const d = new Date(iso), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
  return d.toDateString() === now.toDateString() ? "Today" : d.toDateString() === y.toDateString() ? "Yesterday" : "Earlier";
};

export default function NotificationsPage() {
  const [data, setData] = useState<{ unread: number; items: N[] } | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const toast = useToast();
  const load = useCallback(() => { api<{ unread: number; items: N[] }>("/notifications", { params: { page_size: 100 } }).then(setData).catch((e) => toast("error", e instanceof ApiError ? e.message : "Failed to load")); }, [toast]);
  useEffect(() => { load(); }, [load]);
  async function read(id: number) { await api(`/notifications/${id}/read`, { method: "POST" }); load(); window.dispatchEvent(new Event("notifications-changed")); }
  async function readAll() { await api("/notifications/read-all", { method: "POST" }); load(); window.dispatchEvent(new Event("notifications-changed")); }

  const groups = useMemo(() => {
    const items = (data?.items ?? []).filter((n) => !unreadOnly || !n.read);
    const out: { name: string; items: N[] }[] = [];
    for (const n of items) {
      const name = groupName(n.created_at);
      const last = out[out.length - 1];
      if (last && last.name === name) last.items.push(n); else out.push({ name, items: [n] });
    }
    return out;
  }, [data, unreadOnly]);

  return (
    <>
      <PageHeader title="Notifications" description="Alerts caused by work in other modules."
        actions={data && (
          <>
            <div className="seg" role="tablist" aria-label="Filter notifications">
              <button type="button" role="tab" aria-selected={!unreadOnly} onClick={() => setUnreadOnly(false)}>All</button>
              <button type="button" role="tab" aria-selected={unreadOnly} onClick={() => setUnreadOnly(true)}>Unread{data.unread > 0 ? ` (${data.unread})` : ""}</button>
            </div>
            {data.unread > 0 && <button className="btn" onClick={readAll}><Check size={14} strokeWidth={1.5} />Mark all read</button>}
          </>
        )} />
      {!data ? <div className="skeleton h-32 !rounded-[28px]" /> : groups.length === 0 ? <div className="panel"><EmptyState title={unreadOnly ? "Nothing unread" : "You're all caught up"} hint={unreadOnly ? "Every alert has been read." : undefined} /></div> : (
        <div className="space-y-6 max-w-4xl">
          {groups.map((g) => (
            <section key={g.name} aria-label={g.name}>
              <h2 className="text-[0.75rem] font-medium text-muted mb-2 px-1">{g.name}</h2>
              <ul className="space-y-2">
                {g.items.map((n) => {
                  const Icon = iconFor(n);
                  const high = n.priority === "HIGH";
                  return (
                    <li key={n.id} className="flex items-center gap-3.5 rounded-[22px] px-4 py-3 transition-colors"
                      style={{ background: n.read ? "rgb(255 255 255 / 0.38)" : "rgb(255 255 255 / 0.82)", boxShadow: n.read ? "none" : "0 8px 24px -16px rgb(70 45 20 / 0.35)" }}>
                      <span className="relative grid place-items-center w-10 h-10 rounded-full shrink-0" style={{ background: high ? "rgb(198 67 29 / 0.12)" : "rgb(21 23 23 / 0.06)", color: high ? "var(--acc-orange)" : "var(--muted)" }} aria-hidden>
                        <Icon size={17} strokeWidth={1.5} />
                        {!n.read && <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-white" style={{ background: "var(--acc-orange)" }} />}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className={`relative text-[0.875rem] leading-5 line-clamp-2 ${n.read ? "text-muted" : "font-medium"}`}>
                          {n.title}{high && <span className="pill pill-danger ml-2 align-middle">High</span>}
                          {!n.read && <span className="sr-only"> (unread)</span>}
                        </p>
                        {n.body && <p className="text-muted text-[0.75rem] mt-0.5 line-clamp-2">{n.body}</p>}
                        <p className="text-[0.6875rem] text-muted mt-0.5" title={fmtDateTime(n.created_at)}>{fmtAgo(n.created_at)}</p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {n.link && <Link className="btn btn-sm gap-1" href={n.link.startsWith("/") ? n.link : "/"} onClick={() => { if (!n.read) read(n.id); }}>Open<ArrowUpRight size={13} strokeWidth={1.5} /></Link>}
                        {!n.read && <button className="ctl" onClick={() => read(n.id)} aria-label={`Mark "${n.title}" as read`} title="Mark read"><Check size={15} strokeWidth={1.5} /></button>}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}
