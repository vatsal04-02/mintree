"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowUpRight, Boxes, CircleDollarSign, FileText, FlaskConical, Handshake, Download, PackageCheck, Receipt, ShieldCheck, ShoppingBag, ShoppingCart, Truck, Factory, type LucideIcon } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fmtAgo, fmtDateTime, fmtTimeIST } from "@/lib/format";
import { useLiveEvent, useLiveState } from "@/lib/live";
import { EmptyState } from "./ui";

export type ActivityItem = {
  id: number; event_id: string; type: string; module: string; title: string; number: string | null; link: string; at: string | null;
  actor: string | null; kind: "person" | "demo"; source: string | null;
};
const MODULES: { id: string; label: string }[] = [
  { id: "", label: "All" }, { id: "dispatch", label: "Sales & dispatch" }, { id: "sales", label: "CRM" }, { id: "procurement", label: "Purchase" }, { id: "inventory", label: "Inventory" },
  { id: "production", label: "Production" }, { id: "quality", label: "Quality" }, { id: "accounts", label: "Accounts" }, { id: "ecommerce", label: "E-commerce" },
];

/** One icon per kind of event, from its type ("sales.order.created", "accounts.payment.posted"), so the feed can be scanned by shape; the first matching rule wins. */
const ICONS: [RegExp, LucideIcon][] = [
  [/exception|recall|expiry|failed|rejected/, AlertTriangle],
  [/payment/, CircleDollarSign], [/einvoice|invoice|debit_note|credit_note/, Receipt],
  [/goods_receipt|purchase_return/, PackageCheck], [/order\.imported|imported/, Download],
  [/shipment|dispatch|delivery/, Truck], [/^sales\.quotation/, FileText], [/^sales\.order|^dispatch\.return/, ShoppingBag], [/^crm\./, Handshake],
  [/^procurement\./, ShoppingCart], [/^production\./, Factory], [/^quality\.inspection|coa|qc_decided/, FlaskConical], [/^quality\./, ShieldCheck],
  [/^inventory\./, Boxes],
];
const iconForType = (type: string): LucideIcon => ICONS.find(([re]) => re.test(type))?.[1] ?? FileText;

/** The live activity timeline: committed business events the signed-in person is allowed to see, newest first. New ones arrive over the event stream without a reload;
 *  after a dropped connection the list is re-read from the server, so nothing missed stays missing. Each event has an icon for its type; the top bar already says Demo mode, so rows carry no Demo pill. */
export function ActivityFeed({ limit = 40, compact = false }: { limit?: number; compact?: boolean }) {
  const [items, setItems] = useState<ActivityItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mod, setMod] = useState("");
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const [now, setNow] = useState(() => Date.now());
  const state = useLiveState();

  const load = useCallback(() => {
    api<{ items: ActivityItem[] }>("/activity", { params: { limit } }).then((d) => { setItems(d.items); setError(null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load activity"));
  }, [limit]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(t); }, []);

  useLiveEvent(["activity"], (e) => {
    const it = e.data as unknown as ActivityItem;
    setItems((cur) => (cur && cur.some((x) => x.id === it.id) ? cur : [it, ...(cur ?? [])].slice(0, 200)));
    setFresh((f) => new Set(f).add(it.id));
    setTimeout(() => setFresh((f) => { const n = new Set(f); n.delete(it.id); return n; }), 4000);
  });
  useLiveEvent(["hello"], (e) => { if (e.data.resync) load(); });                       // reconnected: re-read what may have been missed
  useEffect(() => { if (state === "live") load(); }, [state, load]);                     // the stream is back after a polling spell

  const shown = useMemo(() => (items ?? []).filter((i) => !mod || i.module === mod), [items, mod]);
  return (
    <section aria-label="Live activity" data-testid="activity-feed">
      {!compact && (
        <div className="flex flex-wrap gap-1.5 mb-4" role="group" aria-label="Filter by module">
          {MODULES.map((m) => (
            <button key={m.id} type="button" onClick={() => setMod(m.id)} aria-pressed={mod === m.id}
              className={`h-8 px-3 rounded-full border text-[0.8125rem] ${mod === m.id ? "border-line-strong bg-surface-2" : "border-line bg-surface text-muted hover:border-line-strong"}`}>{m.label}</button>
          ))}
        </div>
      )}
      {error && <p role="alert" className="text-[0.8125rem] text-[var(--acc-red)] mb-3">{error}</p>}
      {items === null && !error && <ul className="space-y-2" aria-busy="true">{[0, 1, 2, 3, 4].map((i) => <li key={i} className="h-12 rounded-xl bg-inset animate-pulse" />)}</ul>}
      {items !== null && shown.length === 0 && <EmptyState title="No activity yet" hint="Business actions appear here the moment they are saved." art="tray" />}
      {shown.length > 0 && (
        <ol className="divide-y divide-line border border-line rounded-2xl bg-surface overflow-hidden">
          {shown.map((i) => (
            <li key={i.id} data-testid="activity-item" data-type={i.type} className={`flex items-center gap-3 px-4 ${compact ? "py-2" : "py-3"} ${fresh.has(i.id) ? "anim-fade bg-surface-2" : ""}`}>
              <span className="shrink-0 w-7 h-7 rounded-full bg-inset grid place-items-center text-muted" aria-hidden>{(() => { const Icon = iconForType(i.type); return <Icon size={14} strokeWidth={1.5} />; })()}</span>
              <div className="min-w-0 flex-1">
                <p className={`text-[0.875rem] ${compact ? "line-clamp-2 leading-5" : "truncate"}`} title={i.title}>{i.title}</p>
                <p className="text-[0.75rem] text-muted truncate">{i.actor ?? (i.kind === "demo" ? "Demo system" : "System")} · <span className="capitalize">{i.module === "dispatch" ? "sales & dispatch" : i.module}</span></p>
              </div>
              <time dateTime={i.at ?? undefined} title={`${fmtDateTime(i.at)} (IST ${fmtTimeIST(i.at)})`} className="text-[0.75rem] text-muted shrink-0 tabular-nums text-right w-[4.75rem]">
                {fmtTimeIST(i.at)}<span className="block">{fmtAgo(i.at, now)}</span>
              </time>
              <Link href={i.link} className="icon-btn shrink-0" aria-label={`Open ${i.number ?? i.title}`}><ArrowUpRight size={15} /></Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
