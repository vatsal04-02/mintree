"use client";
/** Customers (Rapport patterns 03 and 04). The directory is a table first, with a card grid as the alternative; the detail page shows the account at a
 *  glance and its latest orders. Everything is read from invoices, orders and opportunities (/crm/customers-board and /sales/customers/{id}/history):
 *  there is no score, tier or star, because the database has none. The thin bar under a card is the share of what was invoiced that is still unpaid
 *  (or of the credit limit used, when the customer has one): orange once it reaches 60%, green below that. */
import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Download, IndianRupee, Mail, Pencil, Phone, Plus, ReceiptText, Search, TriangleAlert, Users, Wallet } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { downloadCsv } from "@/lib/csv";
import { fmtDate, fmtDay, fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { PageHeader, EmptyState } from "./ui";
import { Strip } from "./kpi";
import { Segmented } from "./blocks";
import { CardHead, DealCard } from "./rapport";
import { Reveal } from "./motion";
import { useShared, type Row } from "./command";

const n = (v: unknown) => Number(v ?? 0) || 0;
const short = (v: number) => (v >= 1e7 ? `₹${fmtNum(v / 1e7, 2)}Cr` : v >= 1e5 ? `₹${fmtNum(v / 1e5, 2)}L` : fmtRupees(v));
const TYPE: Record<string, string> = { B2B: "B2B", DISTRIBUTOR: "Distributor", BRAND: "Brand", MARKETPLACE: "Marketplace" };
const initials = (name: string) => name.split(/[\s,]+/).filter(Boolean).map((p) => p[0]).slice(0, 2).join("").toUpperCase();

/** Share of the credit limit in use, or, with no limit, the share of invoiced revenue still unpaid. Both are 0 to 100. */
function exposure(r: Row) {
  const limit = n(r.credit_limit), out = n(r.outstanding), rev = n(r.revenue);
  return limit > 0 ? { pct: Math.min(100, (out / limit) * 100), of: "of credit limit" } : { pct: rev > 0 ? Math.min(100, (out / rev) * 100) : 0, of: "of invoiced unpaid" };
}
const Bar = ({ pct }: { pct: number }) => (
  <span className="block h-[3px] rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
    <span className="block h-[3px] rounded-full" style={{ width: `${Math.max(pct, 2)}%`, background: pct >= 60 ? "var(--acc-orange)" : "var(--acc-green)" }} />
  </span>
);
const Avatar = ({ name, size = 44 }: { name: string; size?: number }) => (
  <span className="grid place-items-center rounded-full shrink-0 text-[0.8125rem]" style={{ width: size, height: size, background: "rgb(21 23 23 / 0.07)", color: "var(--fg)" }} aria-hidden>{initials(name)}</span>
);
const Round = ({ href, label, children }: { href?: string; label: string; children: React.ReactNode }) =>
  href ? <a href={href} className="ctl !w-9 !h-9" aria-label={label} title={label}>{children}</a> : <span className="ctl !w-9 !h-9 opacity-35" aria-hidden>{children}</span>;

/* ================================================================== directory */
export function CustomerDirectory() {
  const { can } = useAuth();
  const d = useShared<Row>("/crm/customers-board");
  const [type, setType] = useState("ALL");
  const [q, setQ] = useState("");
  const [view, setView] = useState<"table" | "grid">("table");
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: "revenue", dir: -1 });
  const [page, setPage] = useState(0);
  const per = 24;

  const all = useMemo(() => (d?.items ?? []) as Row[], [d]);
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = all.filter((r) => (type === "ALL" || r.customer_type === type) && (!t || `${r.name} ${r.code} ${r.contact_name ?? ""} ${r.state ?? ""} ${r.email ?? ""}`.toLowerCase().includes(t)));
    const val = (r: Row) => (sort.key === "name" ? String(r.name) : sort.key === "exposure" ? exposure(r).pct : sort.key === "last_order" ? String(r.last_order ?? "") : n(r[sort.key]));
    return [...list].sort((a, b) => { const x = val(a), y = val(b); return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sort.dir; });
  }, [all, type, q, sort]);
  const pages = Math.max(1, Math.ceil(rows.length / per));
  const shown = rows.slice(page * per, page * per + per);
  const by = (key: string) => { setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === "name" ? 1 : -1 })); setPage(0); };
  const th = (key: string, label: string, num?: boolean) => (
    <th key={key} className={num ? "num" : ""} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
      <button type="button" className="inline-flex items-center gap-1 hover:text-fg" onClick={() => by(key)}>{label}{sort.key === key && <span aria-hidden>{sort.dir === 1 ? "↑" : "↓"}</span>}</button>
    </th>
  );
  const counts = (d?.by_type ?? {}) as Record<string, number>;
  const chips: [string, string, number][] = [["ALL", "All", all.length], ...Object.entries(counts).map(([k, v]) => [k, TYPE[k] ?? titleCase(k), n(v)] as [string, string, number])];

  function exportCsv() {
    downloadCsv("customers.csv", ["Code", "Name", "Type", "Contact", "Email", "Phone", "State", "Invoiced (ex tax)", "Outstanding", "Credit limit", "Open pipeline", "Last order"],
      rows.map((r) => [r.code, r.name, r.customer_type, r.contact_name, r.email, r.phone, r.state, r.revenue, r.outstanding, r.credit_limit, r.open_pipeline, r.last_order ? String(r.last_order).slice(0, 10) : ""]));
  }

  return (
    <>
      <PageHeader title="Customers" description="Accounts, what they have bought and what is still owed."
        actions={<>
          <button type="button" className="btn gap-1.5" onClick={exportCsv} disabled={!rows.length}><Download size={14} strokeWidth={1.5} />Export</button>
          {can("masters.customer:create") && <Link href="/masters/customers" className="btn btn-primary gap-1.5"><Plus size={15} strokeWidth={1.5} />New customer</Link>}
        </>} />

      {d === undefined ? <div className="skeleton h-96 !rounded-[28px]" /> : !d ? <p className="panel p-5 text-danger">The customer directory could not be loaded.</p> : (
        <div className="space-y-4">
          <Strip label="Key figures" cards={[
            { label: "Active customers", value: fmtNum(all.length), icon: Users, foot: `${fmtNum(n(d.active_buyers_90d))} ordered in 90 days` },
            { label: "Invoiced, ex-tax", value: short(n(d.total_revenue)), icon: IndianRupee },
            { label: "Outstanding", value: short(n(d.total_outstanding)), icon: Wallet, chip: n(d.total_revenue) ? `${fmtNum((n(d.total_outstanding) / n(d.total_revenue)) * 100, 0)}% of invoiced` : undefined, tone: "warning" },
            { label: "Open pipeline", value: short(n(d.open_pipeline)), icon: ReceiptText },
          ]} />

          <section className="panel rise overflow-hidden" aria-label="Customer directory">
            <div className="flex flex-wrap items-center justify-between gap-3 px-[18px] pt-[18px] pb-3">
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Customer type">
                {chips.map(([k, label, count]) => (
                  <button key={k} type="button" aria-pressed={type === k} onClick={() => { setType(k); setPage(0); }}
                    className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-[0.8125rem] border transition-colors ${type === k ? "bg-[var(--ink-r)] text-[var(--card)] border-transparent" : "border-line-strong hover:bg-white/60"}`}>
                    {label}<span className={type === k ? "opacity-70" : "text-muted"}>{count}</span>
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="relative"><span className="sr-only">Search customers</span><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
                  <input className="input !h-9 !pl-9 !w-56" placeholder="Search name, contact, state…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} /></label>
                <Segmented<"table" | "grid"> label="View" value={view} onChange={setView} options={[{ value: "table", label: "Table" }, { value: "grid", label: "Grid" }]} />
              </div>
            </div>

            {rows.length === 0 ? <EmptyState title="No customer matches" hint="Try another type or search." /> : view === "table" ? (
              <div className="overflow-x-auto"><table className="dt dt-compact">
                <thead><tr>{th("name", "Customer")}<th>Type</th><th>Contact</th>{th("revenue", "Invoiced", true)}{th("outstanding", "Outstanding", true)}{th("exposure", "Exposure")}{th("last_order", "Last order")}</tr></thead>
                <tbody>{shown.map((r) => {
                  const ex = exposure(r);
                  return (
                    <tr key={r.id}>
                      <td className="max-w-[18rem]"><Link href={`/crm/customers/${r.id}`} className="flex items-center gap-3 hover:opacity-80"><Avatar name={String(r.name)} size={32} />
                        <span className="min-w-0"><span className="block font-medium truncate" title={r.name}>{r.name}</span><span className="block t-support font-mono">{r.code}</span></span></Link></td>
                      <td><span className="tag">{TYPE[r.customer_type] ?? r.customer_type}</span></td>
                      <td className="max-w-[12rem]"><span className="block truncate">{r.contact_name ?? "—"}</span><span className="block t-support truncate">{r.state ?? ""}</span></td>
                      <td className="num font-mono">{short(n(r.revenue))}</td>
                      <td className={`num font-mono ${ex.pct >= 60 ? "text-danger" : ""}`}>{n(r.outstanding) ? short(n(r.outstanding)) : "—"}</td>
                      <td className="min-w-[7rem]"><span className="block text-[0.6875rem] text-muted tabular-nums mb-1">{fmtNum(ex.pct, 0)}% {ex.of}</span><Bar pct={ex.pct} /></td>
                      <td className="t-support whitespace-nowrap">{r.last_order ? fmtDate(String(r.last_order).slice(0, 10)) : "—"}</td>
                    </tr>
                  );
                })}</tbody>
              </table></div>
            ) : (
              <div className="px-[18px] pb-[18px] grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {shown.map((r, i) => {
                  const ex = exposure(r);
                  return (
                    <Reveal key={r.id} index={i % 12} radius={22}>
                      <article className="rounded-[22px] p-4 flex flex-col gap-3 h-full" style={{ background: "rgb(255 255 255 / 0.62)", boxShadow: "0 1px 2px rgb(70 45 20 / 0.04)", border: "1px solid rgb(21 23 23 / 0.06)" }}>
                        <div className="flex items-start gap-3">
                          <Avatar name={String(r.name)} />
                          <div className="min-w-0 flex-1">
                            <h3 className="text-[0.9375rem] leading-5 font-medium truncate"><Link href={`/crm/customers/${r.id}`} className="hover:underline" title={r.name}>{r.name}</Link></h3>
                            <p className="t-support truncate">{[r.contact_name, r.state].filter(Boolean).join(" · ") || r.code}</p>
                          </div>
                          <span className="tag shrink-0">{TYPE[r.customer_type] ?? r.customer_type}</span>
                        </div>
                        <dl className="rounded-2xl grid grid-cols-2 gap-px overflow-hidden" style={{ background: "rgb(21 23 23 / 0.06)" }}>
                          <div className="px-3 py-2.5" style={{ background: "rgb(255 255 255 / 0.55)" }}><dt className="text-[0.6875rem] text-muted">{n(r.open_pipeline) ? "Open pipeline" : "Invoiced"}</dt><dd className="text-[0.9375rem] font-medium tabular-nums">{short(n(r.open_pipeline) || n(r.revenue))}</dd></div>
                          <div className="px-3 py-2.5" style={{ background: "rgb(255 255 255 / 0.55)" }}><dt className="text-[0.6875rem] text-muted">Last order</dt><dd className="text-[0.9375rem] font-medium">{r.last_order ? fmtDay(String(r.last_order).slice(0, 10)) : "—"}</dd></div>
                        </dl>
                        <div><span className="flex justify-between text-[0.6875rem] text-muted mb-1"><span>{fmtNum(ex.pct, 0)}% {ex.of}</span><span className="tabular-nums">{n(r.outstanding) ? `${short(n(r.outstanding))} due` : "Nothing due"}</span></span><Bar pct={ex.pct} /></div>
                        <div className="flex items-center justify-end gap-2 mt-auto">
                          <Round href={r.email ? `mailto:${r.email}` : undefined} label={`Email ${r.name}`}><Mail size={15} strokeWidth={1.5} /></Round>
                          <Round href={r.phone ? `tel:${r.phone}` : undefined} label={`Call ${r.name}`}><Phone size={15} strokeWidth={1.5} /></Round>
                        </div>
                      </article>
                    </Reveal>
                  );
                })}
              </div>
            )}

            <footer className="flex flex-wrap items-center justify-between gap-2 px-[18px] py-3 border-t border-line">
              <span className="t-support">{rows.length ? `${page * per + 1}–${Math.min(rows.length, page * per + per)} of ${rows.length}` : "0"} customers</span>
              <div className="flex items-center gap-1.5">
                <button type="button" className="btn btn-sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
                <span className="t-support tnum px-1">Page {page + 1} / {pages}</span>
                <button type="button" className="btn btn-sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>Next</button>
              </div>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}

/* ================================================================== detail */
export function CustomerDetail({ id }: { id: number }) {
  const { can } = useAuth();
  const board = useShared<Row>("/crm/customers-board");
  const hist = useShared<Row>(`/sales/customers/${id}/history`);
  const r = ((board?.items ?? []) as Row[]).find((x) => x.id === id);
  const loading = board === undefined || hist === undefined;
  if (!loading && (!r || !hist)) return <><Link href="/crm/customers" className="btn gap-1.5 mb-4"><ArrowLeft size={14} strokeWidth={1.5} />Customers</Link><EmptyState title="Customer not found" hint="It may have been deactivated, or you may not have access." /></>;
  if (loading || !r || !hist) return <div className="skeleton h-96 !rounded-[28px]" />;

  const rev = n(hist.revenue_ex_tax), out = n(hist.outstanding);
  const paid = rev > 0 ? Math.max(0, Math.min(100, ((rev - out) / rev) * 100)) : 0;
  const ex = exposure(r);
  const orders = (hist.recent_orders ?? []) as Row[];
  const info: [string, string][] = [
    ["Contact", r.contact_name ?? "—"], ["Email", r.email ?? "—"], ["Phone", r.phone ?? "—"], ["State", r.state ?? "—"],
    ["Credit limit", n(r.credit_limit) ? fmtRupees(n(r.credit_limit)) : "None set"], ["Credit period", r.credit_days ? `${fmtNum(n(r.credit_days), 0)} days` : "—"],
  ];
  const C = 2 * Math.PI * 46;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <Link href="/crm/customers" className="btn gap-1.5"><ArrowLeft size={14} strokeWidth={1.5} />Customers</Link>
        <div className="flex gap-2">
          {r.email && <a href={`mailto:${r.email}`} className="btn gap-1.5"><Mail size={14} strokeWidth={1.5} />Email</a>}
          {r.phone && <a href={`tel:${r.phone}`} className="btn gap-1.5"><Phone size={14} strokeWidth={1.5} />Call</a>}
        </div>
      </div>

      <Strip label="Key figures" cards={[
        { label: "Invoiced, ex-tax", value: short(rev), icon: IndianRupee, foot: `${fmtNum(n(hist.invoices), 0)} invoices` },
        { label: "Outstanding", value: short(out), icon: Wallet, chip: out ? `${fmtNum(ex.pct, 0)}% ${ex.of}` : "Nothing due", tone: out ? (ex.pct >= 60 ? "danger" : "warning") : "success" },
        { label: "Open pipeline", value: short(n(r.open_pipeline)), icon: ReceiptText, foot: n(r.open_deals) ? `${fmtNum(n(r.open_deals), 0)} open deals` : undefined },
        { label: "Complaints", value: fmtNum(n(hist.complaints), 0), icon: TriangleAlert, chip: n(hist.complaints) ? "Logged" : undefined, tone: "warning" },
      ]} />

      <div className="grid gap-4 xl:grid-cols-12 mt-4 items-start">
        <section className="panel rise xl:col-span-4 p-5 flex flex-col items-center text-center" aria-label="Profile">
          <div className="relative w-[116px] h-[116px] mt-1" role="img" aria-label={`${fmtNum(paid, 0)}% of invoiced revenue paid`}>
            <svg viewBox="0 0 100 100" className="absolute inset-0 w-full h-full -rotate-90" aria-hidden>
              <circle cx="50" cy="50" r="46" fill="none" stroke="var(--chart-track)" strokeWidth="3" />
              <circle cx="50" cy="50" r="46" fill="none" stroke="var(--acc-green)" strokeWidth="3" strokeLinecap="round" strokeDasharray={`${(C * paid) / 100} ${C}`} className="fx-sweep" style={{ ["--c" as string]: C, ["--len" as string]: (C * paid) / 100, ["--gap" as string]: C }} />
            </svg>
            <span className="absolute inset-[9px] rounded-full grid place-items-center text-[1.5rem]" style={{ background: "rgb(21 23 23 / 0.07)" }} aria-hidden>{initials(String(r.name))}</span>
          </div>
          <h1 className="mt-3 text-[1.25rem] leading-6 font-medium">{r.name}</h1>
          <p className="t-support mt-0.5"><span className="font-mono">{r.code}</span> · <span className="tag">{TYPE[r.customer_type] ?? r.customer_type}</span></p>
          <p className="t-support mt-3">{fmtNum(paid, 0)}% of invoiced revenue paid</p>
        </section>

        <section className="panel rise xl:col-span-8" aria-label="Detailed information">
          <CardHead title="Detailed information">{can("masters.customer:update") && <Link href="/masters/customers" className="ctl" aria-label="Edit in master data" title="Edit in master data"><Pencil size={15} strokeWidth={1.5} /></Link>}</CardHead>
          <dl className="px-[18px] pb-[14px] grid sm:grid-cols-2 gap-x-8">
            {info.map(([k, v]) => <div key={k} className="flex items-baseline justify-between gap-3 py-2.5 border-t border-line min-w-0"><dt className="t-support shrink-0">{k}</dt><dd className="text-[0.8125rem] truncate text-right" title={v}>{v}</dd></div>)}
          </dl>
        </section>
      </div>

      <section className="panel rise mt-4" aria-label="Recent orders">
        <CardHead title="Recent orders" meta={orders.length ? `${orders.length} latest, newest first${hist.last_invoice_date ? ` · last invoice ${fmtDate(String(hist.last_invoice_date))}` : ""}` : undefined} />
        {orders.length === 0 ? <p className="t-support px-[18px] pb-[18px]">No orders yet.</p> : (
          <div className="px-[18px] pb-[18px] grid gap-[11px] sm:grid-cols-2 lg:grid-cols-4">
            {orders.slice(0, 8).map((o, i) => (
              <DealCard key={o.id} index={i} variant="white" meta={titleCase(String(o.status))} metaTone={o.status === "CANCELLED" ? "danger" : /DELIVERED|DISPATCHED/.test(String(o.status)) ? "neutral" : "warning"}
                title={String(o.number)} subtitle={`${titleCase(String(o.channel))} · ${fmtDate(String(o.created_at).slice(0, 10))}`} value={fmtRupees(n(o.total))} href="/m/dispatch/orders" />
            ))}
          </div>
        )}
      </section>
    </>
  );
}
