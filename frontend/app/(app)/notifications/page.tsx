"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { Badge, EmptyState, PageHeader, useToast } from "@/components/ui";

type N = { id: number; kind: string; title: string; body: string | null; link: string | null; priority: string; created_at: string; read: boolean };

export default function NotificationsPage() {
  const [data, setData] = useState<{ unread: number; items: N[] } | null>(null);
  const toast = useToast();
  const load = useCallback(() => { api<{ unread: number; items: N[] }>("/notifications", { params: { page_size: 100 } }).then(setData).catch((e) => toast("error", e instanceof ApiError ? e.message : "Failed to load")); }, [toast]);
  useEffect(() => { load(); }, [load]);
  async function read(id: number) { await api(`/notifications/${id}/read`, { method: "POST" }); load(); window.dispatchEvent(new Event("notifications-changed")); }
  async function readAll() { await api("/notifications/read-all", { method: "POST" }); load(); window.dispatchEvent(new Event("notifications-changed")); }
  return (
    <>
      <PageHeader title="Notifications" description="Alerts caused by work in other modules." actions={data && data.unread > 0 ? <button className="btn" onClick={readAll}>Mark all read ({data.unread})</button> : undefined} />
      {!data ? <div className="skeleton h-32" /> : data.items.length === 0 ? <div className="panel"><EmptyState title="You're all caught up" /></div> : (
        <ul className="panel divide-y divide-line">
          {data.items.map((n) => (
            <li key={n.id} className={`px-4 py-3 flex items-start gap-3 ${n.read ? "" : "bg-primary/5"}`}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2"><span className="font-medium text-[0.8125rem]">{n.title}</span>{n.priority === "HIGH" && <Badge tone="danger">High</Badge>}</div>
                {n.body && <p className="text-muted text-[0.75rem] mt-0.5">{n.body}</p>}
                <p className="text-muted text-[0.75rem] mt-1">{fmtDateTime(n.created_at)}</p>
              </div>
              {n.link && <Link className="btn btn-sm" href={n.link.startsWith("/") ? n.link : "/"}>Open</Link>}
              {!n.read && <button className="btn btn-sm" onClick={() => read(n.id)}>Mark read</button>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
