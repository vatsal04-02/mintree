"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, Bell, BellOff, Check, Clock, FileText, Package, ShieldCheck, ShoppingCart } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fmtAgo, fmtDateTime } from "@/lib/format";
import { EmptyState, PageHeader, useToast, useUndoable } from "@/components/ui";
import { usePref } from "@/lib/prefs";

type N = { id: number; kind: string; title: string; body: string | null; link: string | null; priority: string; created_at: string; read: boolean };

/** An icon from the wording of the alert, so a purchase order, a quotation and a quality hold are told apart at a glance. */
const iconFor = (n: N) => {
  const t = `${n.kind} ${n.title}`.toLowerCase();
  return /(po\/|purchase|rfq|quotation|supplier)/.test(t) ? ShoppingCart : /(qc|quality|inspection|deviation|capa|quarantine)/.test(t) ? ShieldCheck
    : /(order|shipment|dispatch|stock|batch)/.test(t) ? Package : /(invoice|approval|document)/.test(t) ? FileText : Bell;
};

/** Alerts carry the address of the record in the API ("/procurement/purchase-orders/31"); the app shows that record on its list screen, opened by id. */
const SCREEN: [RegExp, string][] = [
  [/^\/procurement\/purchase-orders\/(\d+)/, "/m/procurement/orders"], [/^\/procurement\/purchase-requests\/(\d+)/, "/m/procurement/requests"], [/^\/procurement\/rfqs\/(\d+)/, "/m/procurement/rfqs"],
  [/^\/procurement\/receipts\/(\d+)/, "/m/procurement/receipts"], [/^\/dispatch\/shipments\/(\d+)/, "/m/dispatch/shipments"], [/^\/dispatch\/orders\/(\d+)/, "/m/dispatch/orders"],
  [/^\/dispatch\/returns\/(\d+)/, "/m/dispatch/returns"], [/^\/quality\/inspections\/(\d+)/, "/m/quality/inspections"], [/^\/quality\/deviations\/(\d+)/, "/m/quality/deviations"],
  [/^\/quality\/capa\/(\d+)/, "/m/quality/capa"], [/^\/production\/orders\/(\d+)/, "/m/production/orders"], [/^\/inventory\/stocktakes\/(\d+)/, "/m/inventory/stocktake"],
  [/^\/sales\/quotations\/(\d+)/, "/m/sales/quotations"], [/^\/crm\/leads\/(\d+)/, "/m/sales/leads"], [/^\/maintenance\/work-orders\/(\d+)/, "/m/maintenance/work-orders"],
];
const where = (link: string | null) => {
  if (!link) return null;
  for (const [re, screen] of SCREEN) { const m = re.exec(link); if (m) return `${screen}?open=${m[1]}`; }
  return /^\/(m|crm|inventory|approvals|documents|admin|masters|notifications)\b/.test(link) ? link : null;   // already an app address, or nothing we can open
};
type Row = { n: N; ids: number[]; unreadIds: number[]; count: number };
const nextMorning = () => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); return d.getTime(); };

const groupName = (iso: string) => {
  const d = new Date(iso), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
  return d.toDateString() === now.toDateString() ? "Today" : d.toDateString() === y.toDateString() ? "Yesterday" : "Earlier";
};

export default function NotificationsPage() {
  const [data, setData] = useState<{ unread: number; items: N[] } | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [snoozed, setSnoozed] = usePref<Record<string, number>>("snoozed", {});
  const [showSnoozed, setShowSnoozed] = useState(false);
  const [snoozeFor, setSnoozeFor] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());   // so a snooze ends on its own while the page stays open
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(t); }, []);
  const toast = useToast();
  const undoable = useUndoable();
  const load = useCallback(() => { api<{ unread: number; items: N[] }>("/notifications", { params: { page_size: 100 } }).then(setData).catch((e) => toast("error", e instanceof ApiError ? e.message : "Failed to load")); }, [toast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { window.addEventListener("notifications-changed", load); return () => window.removeEventListener("notifications-changed", load); }, [load]);   // a live alert arrived
  const changed = () => window.dispatchEvent(new Event("notifications-changed"));
  /* the alert looks read at once; the server hears about it after four seconds, and Undo cancels that */
  function read(ids: number[]) {
    const before = data, set = new Set(ids);
    undoable({ message: ids.length > 1 ? `${ids.length} alerts marked as read` : "Marked as read", apply: () => setData((d) => d && { ...d, unread: Math.max(0, d.unread - ids.length), items: d.items.map((n) => (set.has(n.id) ? { ...n, read: true } : n)) }), revert: () => setData(before),
      commit: async () => { await Promise.all(ids.map((id) => api(`/notifications/${id}/read`, { method: "POST" }))); changed(); } });
  }
  function readAll() {
    const before = data;
    undoable({ message: "All alerts marked as read", apply: () => setData((d) => d && { ...d, unread: 0, items: d.items.map((n) => ({ ...n, read: true })) }), revert: () => setData(before),
      commit: async () => { await api("/notifications/read-all", { method: "POST" }); changed(); } });
  }
  /** Opening an alert marks it read straight away: there is nothing to undo about following a link. */
  const markRead = (ids: number[]) => { Promise.all(ids.map((id) => api(`/notifications/${id}/read`, { method: "POST" }))).then(changed).catch(() => undefined); };

  const groups = useMemo(() => {
    /* newest first, so each day has one heading (the list is not always delivered in date order) */
    const items = (data?.items ?? []).filter((n) => (!unreadOnly || !n.read) && (showSnoozed || !(snoozed[n.id] > now))).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
    const out: { name: string; items: Row[] }[] = [];
    for (const n of items) {
      const name = groupName(n.created_at);
      let g = out[out.length - 1];
      if (!g || g.name !== name) { g = { name, items: [] }; out.push(g); }
      /* identical alerts of the same day (same kind and wording) collapse into one row at the place of the newest; the row stands for all of them */
      const twin = g.items.find((r) => r.n.kind === n.kind && r.n.title === n.title);
      if (twin) { twin.ids.push(n.id); twin.count++; if (!n.read) twin.unreadIds.push(n.id); } else g.items.push({ n, ids: [n.id], unreadIds: n.read ? [] : [n.id], count: 1 });
    }
    return out;
  }, [data, unreadOnly, snoozed, showSnoozed, now]);
  const hiddenCount = (data?.items ?? []).filter((n) => snoozed[n.id] > now).length;
  const snooze = (ids: number[], until: number, label: string) => {
    setSnoozed((s) => ({ ...s, ...Object.fromEntries(ids.map((id) => [id, until])) })); setSnoozeFor(null);
    toast("success", `Snoozed until ${label}`, { undo: () => setSnoozed((s) => { const n = { ...s }; ids.forEach((id) => delete n[id]); return n; }) });
  };

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
      {hiddenCount > 0 && <p className="t-support mb-3 flex items-center gap-2"><BellOff size={14} strokeWidth={1.5} aria-hidden />{hiddenCount} snoozed {hiddenCount === 1 ? "alert is" : "alerts are"} {showSnoozed ? "shown" : "hidden"}. <button type="button" className="underline" onClick={() => setShowSnoozed((v) => !v)}>{showSnoozed ? "Hide them" : "Show them"}</button></p>}
      {!data ? <div className="skeleton h-32 !rounded-[28px]" /> : groups.length === 0 ? <div className="panel"><EmptyState art="check" title={unreadOnly ? "Nothing unread" : "You're all caught up"} hint={unreadOnly ? "Every alert has been read." : undefined} /></div> : (
        <div className="space-y-6 max-w-4xl">
          {groups.map((g) => (
            <section key={g.name} aria-label={g.name}>
              <h2 className="text-[0.75rem] font-medium text-muted mb-2 px-1">{g.name}</h2>
              <ul className="space-y-2">
                {g.items.map(({ n, ids, unreadIds, count }) => {
                  const Icon = iconFor(n);
                  const high = n.priority === "HIGH";
                  const unread = unreadIds.length > 0;
                  return (
                    <li key={n.id} className="flex items-center gap-3.5 rounded-[22px] px-4 py-3 transition-colors"
                      style={{ background: !unread ? "rgb(255 255 255 / 0.38)" : "rgb(255 255 255 / 0.82)", boxShadow: !unread ? "none" : "0 8px 24px -16px rgb(70 45 20 / 0.35)" }}>
                      <span className="relative grid place-items-center w-10 h-10 rounded-full shrink-0" style={{ background: high ? "rgb(198 67 29 / 0.12)" : "rgb(21 23 23 / 0.06)", color: high ? "var(--acc-orange)" : "var(--muted)" }} aria-hidden>
                        <Icon size={17} strokeWidth={1.5} />
                        {unread && <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-white" style={{ background: "var(--acc-orange)" }} />}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className={`relative text-[0.875rem] leading-5 line-clamp-2 ${!unread ? "text-muted" : "font-medium"}`}>
                          {n.title}{count > 1 && <span className="tnum text-muted font-normal"> ×{count}</span>}{high && <span className="pill pill-danger ml-2 align-middle">High</span>}
                          {unread && <span className="sr-only"> ({unreadIds.length} unread)</span>}
                        </p>
                        {n.body && <p className="text-muted text-[0.75rem] mt-0.5 line-clamp-2">{n.body}</p>}
                        <p className="text-[0.6875rem] text-muted mt-0.5" title={fmtDateTime(n.created_at)}>{count > 1 ? `Latest ${fmtAgo(n.created_at)}` : fmtAgo(n.created_at)}</p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {where(n.link) && <Link className="btn btn-sm gap-1" href={where(n.link)!} onClick={() => { if (unread) markRead(unreadIds); }}>Open<ArrowUpRight size={13} strokeWidth={1.5} /></Link>}
                        <div className="relative">
                          <button type="button" className="ctl" onClick={() => setSnoozeFor(snoozeFor === n.id ? null : n.id)} aria-haspopup="menu" aria-expanded={snoozeFor === n.id} aria-label={`Snooze "${n.title}"${count > 1 ? ` (${count} alerts)` : ""}`} title="Snooze"><Clock size={15} strokeWidth={1.5} /></button>
                          {snoozeFor === n.id && (
                            <div role="menu" className="anim-fade absolute right-0 mt-1.5 w-44 panel shadow-xl p-1.5 z-30">
                              <button role="menuitem" className="w-full text-left px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-inset" onClick={() => snooze(ids, Date.now() + 3600e3, "in an hour")}>For an hour</button>
                              <button role="menuitem" className="w-full text-left px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-inset" onClick={() => snooze(ids, nextMorning(), "tomorrow, 9:00")}>Until tomorrow, 9:00</button>
                            </div>
                          )}
                        </div>
                        {unread && <button className="ctl" onClick={() => read(unreadIds)} aria-label={`Mark "${n.title}" as read${count > 1 ? ` (${unreadIds.length} alerts)` : ""}`} title="Mark read"><Check size={15} strokeWidth={1.5} /></button>}
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
