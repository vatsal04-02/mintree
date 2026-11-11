"use client";
/** The Overview's command-centre panels: the hero band, running production lines, the marketplace pipeline, Meta campaigns, the cleanroom environment and
 *  the two alert cards. Real records come from the API (shared, de-duplicated requests); the only simulated readings come from lib/demo-telemetry.ts and
 *  every panel that shows them says so. */
import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ClipboardCheck, Factory, FlaskConical, Megaphone, PackageSearch, ShoppingCart, Thermometer, Timer, Truck } from "lucide-react";
import { sharedGet } from "@/lib/api";
import { fmtDate, fmtNum, fmtPct, fmtRupees, titleCase } from "@/lib/format";
import { CLEANROOM, LINE_READINGS, currentShift } from "@/lib/demo-telemetry";

export type Row = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
export const asRows = (d: unknown): Row[] => (Array.isArray(d) ? d : Array.isArray((d as { items?: unknown })?.items) ? (d as { items: Row[] }).items : []);
export function useShared<T>(path: string | null, params?: Record<string, string>) {
  const [data, setData] = useState<T | null | undefined>(undefined);
  const key = path ? path + JSON.stringify(params ?? {}) : "";
  useEffect(() => {
    if (!path) return;
    let live = true;
    sharedGet<T>(path, params, 30_000).then((d) => live && setData(d)).catch(() => live && setData(null));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return path ? data : null;
}
export const Sim = () => <span className="tag tag-sim" title="Demo readings: this app has no sensor integration">Simulated</span>;

/* ------------------------------------------------------------------ hero band */
export function CommandHero({ status, actions, controls }: { status: React.ReactNode; actions: { label: string; href?: string; icon: React.ComponentType<{ size?: number }>; primary?: boolean }[]; controls?: React.ReactNode }) {
  const shift = currentShift();
  const today = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Kolkata" });
  return (
    <section className="hero px-5 py-4" aria-label="Command center">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="t-page !text-[1.25rem] !leading-7">Executive Command Center</h1>
            <span className="tag tag-dot">Live floor operations</span>
          </div>
          <p className="t-support mt-0.5">{today} · {shift.name} ({shift.from} – {shift.to} IST)</p>
        </div>
        {controls}
      </div>
      <div className="mt-3 pt-3 border-t border-line flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="t-label min-w-0">{status}</p>
        <div className="flex flex-wrap gap-2">
          {actions.filter((a) => a.href).map((a) => (
            <Link key={a.label} href={a.href!} className={`btn btn-sm ${a.primary ? "btn-primary" : "btn-outline"} gap-1.5`}><a.icon size={14} />{a.label}</Link>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ running production lines */
export function ProductionLines({ href }: { href?: string }) {
  const orders = useShared<Row>("/production/orders", { page_size: "100" });
  const items = useShared<Row>("/masters/items", { page_size: "200" });
  const names = new Map(asRows(items).map((i) => [i.id, i.name as string]));
  const rank: Record<string, number> = { IN_PROGRESS: 0, RELEASED: 1, PLANNED: 2 };
  const live = asRows(orders).filter((o) => o.status in rank).sort((a, b) => rank[a.status] - rank[b.status] || b.id - a.id).slice(0, 3);
  const running = asRows(orders).filter((o) => o.status === "IN_PROGRESS").length;
  return (
    <section className="panel p-5 flex-1" aria-label="Production lines">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><h2 className="t-section">Live Compounding & Packing Lines</h2><span className="badge tone-success">{running} of {live.length} running</span></div>
          <p className="t-support mt-0.5">Production orders on the floor, with their stage checklist and progress.</p>
        </div>
        {href && <Link href={href} className="btn btn-sm btn-outline">All production orders</Link>}
      </header>
      {orders === undefined ? <div className="mt-4 space-y-3">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-24 rounded-xl" />)}</div>
        : live.length === 0 ? <p className="t-label mt-4">No production order is planned, released or running.</p> : (
        <ul className="mt-4 space-y-3">
          {live.map((o, idx) => {
            const stages: Row[] = o.stages ?? [];
            const done = stages.filter((s) => s.status === "COMPLETED").length;
            const current = stages.find((s) => s.status === "IN_PROGRESS") ?? stages.find((s) => s.status === "PENDING");
            const pct = stages.length ? Math.round((done / stages.length) * 100) : 0;
            const tone = o.status === "IN_PROGRESS" ? "success" : o.status === "RELEASED" ? "info" : "neutral";
            const reading = LINE_READINGS[idx % LINE_READINGS.length];
            return (
              <li key={o.id} className="rounded-xl bg-[var(--tile)] p-4">
                <div className="flex flex-wrap items-start gap-3">
                  <span className="icon-sq bg-white" aria-hidden><FlaskConical size={17} /></span>
                  <div className="min-w-0 flex-1">
                    <p className="font-heading font-bold text-[0.875rem] leading-5 truncate">Line {String(idx + 1).padStart(2, "0")}: {names.get(o.output_item_id) ?? `Item #${o.output_item_id}`}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <span className="tag tag-mono bg-white">#{o.number}</span>
                      {current && <span className={`badge tone-${tone}`}>{titleCase(current.name)}</span>}
                      <span className={`badge tone-${tone}`}>{titleCase(o.status)}</span>
                    </div>
                    <p className="t-support mt-1.5">Batch size {fmtNum(Number(o.planned_qty))} units · stage {done} of {stages.length || "—"}{o.yield_pct ? ` · yield ${fmtPct(Number(o.yield_pct))}` : ""}</p>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    {o.status === "IN_PROGRESS" && <div className="text-right"><span className="tile-label">{reading.label} <span className="normal-case tracking-normal font-semibold text-[#8a5a00]">· sim</span></span><span className="font-mono font-bold text-[0.8125rem]">{reading.value}</span></div>}
                    <div className="w-28">
                      <div className="flex justify-between"><span className="tile-label">Progress</span><span className="font-mono text-[0.75rem] font-bold">{pct}%</span></div>
                      <div className="progress mt-1.5"><span className="bar-grow-x" style={{ width: `${pct}%` }} /></div>
                    </div>
                  </div>
                </div>
                {stages.length > 0 && (
                  <ol className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
                    {stages.map((st) => (
                      <li key={st.seq} className={`inline-flex items-center gap-1.5 text-[0.75rem] font-semibold ${st.status === "COMPLETED" ? "text-brand" : st.status === "IN_PROGRESS" ? "text-fg" : "text-muted"}`}>
                        {st.status === "COMPLETED" ? <CheckCircle2 size={13} aria-hidden /> : st.status === "IN_PROGRESS" ? <Timer size={13} aria-hidden /> : <span className="w-[11px] h-[11px] rounded-full border-[1.5px] border-current" aria-hidden />}
                        {titleCase(st.name)}
                      </li>
                    ))}
                  </ol>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ marketplace pipeline */
const PLATFORM: Record<string, { name: string; sub: string; bg: string; fg: string }> = {
  AMAZON: { name: "Amazon India", sub: "FBA & Seller Flex", bg: "#e0e7ff", fg: "#3730a3" },
  NYKAA: { name: "Nykaa Beauty", sub: "Warehouse B2B & Direct", bg: "#fce7f3", fg: "#9d174d" },
  FLIPKART: { name: "Flipkart", sub: "Flipkart Assured", bg: "#dbeafe", fg: "#1e40af" },
  MYNTRA: { name: "Myntra", sub: "Omni Express", bg: "#f3e8ff", fg: "#6b21a8" },
};
export function MarketplacePipeline({ enabled, href }: { enabled: boolean; href?: string }) {
  const d = useShared<Row>(enabled ? "/ecommerce/dashboard" : null);
  const ship = useShared<Row>(enabled ? "/dispatch/dashboard" : null);
  if (!enabled) return null;
  const counts: Record<string, number> = d?.orders_by_platform ?? {};
  const value: Record<string, number> = d?.order_value_by_platform ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const delivered = Number(ship?.delivered ?? 0), transit = Number(ship?.in_transit ?? 0);
  const handed = delivered + transit ? Math.round((delivered / (delivered + transit)) * 100) : 0;
  return (
    <section className="panel p-5" aria-label="Omnichannel pipeline">
      <header className="flex items-start justify-between gap-3">
        <div><h2 className="t-section">Omnichannel Pipeline</h2><p className="t-support mt-0.5">Marketplace orders imported, by channel</p></div>
        <span className="tag tag-mono">{fmtNum(total)} total</span>
      </header>
      {d === undefined ? <div className="mt-4 space-y-3">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-9 rounded-lg" />)}</div> : (
        <ul className="mt-4 space-y-3.5">
          {Object.entries(counts).map(([k, n]) => {
            const p = PLATFORM[k] ?? { name: titleCase(k), sub: "Marketplace", bg: "var(--tile)", fg: "var(--fg)" };
            return (
              <li key={k}>
                <Link href={href ?? "#"} className="flex items-center gap-3 rounded-lg -mx-2 px-2 py-1 hover:bg-[var(--card-hover)]">
                  <span className="avatar" style={{ background: p.bg, color: p.fg }}>{k.slice(0, 2)}</span>
                  <span className="min-w-0 flex-1"><span className="block font-heading font-bold text-[0.875rem] truncate">{p.name}</span><span className="block t-support truncate">{p.sub}</span></span>
                  <span className="text-right shrink-0"><span className="block font-heading font-bold text-[0.8125rem]">{fmtNum(n)} orders</span><span className="block t-support">{fmtRupees(value[k] ?? 0)} GMV</span></span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-4 tile">
        <div className="flex justify-between gap-3"><span className="tile-label">Courier handover</span><span className="tile-label !text-brand">{handed}% delivered</span></div>
        <div className="progress mt-2"><span className="bar-grow-x" style={{ width: `${handed}%` }} /></div>
        <p className="t-support mt-1.5">{fmtNum(delivered)} delivered · {fmtNum(transit)} in transit</p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ Meta campaigns */
export function MetaPanel({ enabled, href }: { enabled: boolean; href?: string }) {
  const d = useShared<Row>(enabled ? "/branding/dashboard" : null);
  if (!enabled) return null;
  const top = d?.spend_by_campaign_30d ? Object.keys(d.spend_by_campaign_30d)[0] : undefined;
  return (
    <section className="panel p-5" aria-label="Meta campaigns">
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5"><span className="icon-sq icon-sq-sm" aria-hidden><Megaphone size={15} /></span><div><h2 className="t-section">Meta Ads & D2C Growth</h2><p className="t-support">Campaign spend and results, 30 days</p></div></div>
        {d?.note && <Sim />}
      </header>
      {d === undefined ? <div className="mt-4 skeleton h-20 rounded-lg" /> : !d ? <p className="t-label mt-3">Campaign figures are unavailable.</p> : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2.5">
            <div className="tile"><span className="tile-label">Spend, 30 days</span><span className="block font-heading font-extrabold text-[1.0625rem] mt-0.5">{fmtRupees(d.spend_30d)}</span><span className="t-support">{fmtNum(d.campaigns_running)} campaigns running</span></div>
            <div className="tile"><span className="tile-label">Results</span><span className="block font-heading font-extrabold text-[1.0625rem] mt-0.5">{fmtNum(d.results_30d)}</span><span className="t-support">CTR {fmtPct(d.click_through_rate_pct ?? 0)}</span></div>
          </div>
          {top && <p className="t-support mt-3 flex items-center justify-between gap-2"><span className="truncate">Top campaign: <b className="text-fg font-semibold">{top}</b></span>{href && <Link href={href} className="text-brand font-semibold shrink-0">Details ›</Link>}</p>}
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ cleanroom environment (simulated) */
export function CleanroomPanel() {
  return (
    <section className="panel p-5" aria-label="Cleanroom environment">
      <header className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5"><span className="icon-sq icon-sq-sm" aria-hidden><Thermometer size={15} /></span><h2 className="label-caps !text-[0.75rem] !text-fg">Cleanroom environment</h2></div>
        <div className="flex items-center gap-1.5"><span className="tag tag-mono">{CLEANROOM.grade}</span><Sim /></div>
      </header>
      <div className="mt-3.5 grid grid-cols-3 gap-2">
        {CLEANROOM.readings.map((r) => <div key={r.label} className="tile"><span className="tile-label">{r.label}</span><span className="tile-value">{r.value}</span></div>)}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ alert cards */
export function AlertCards({ lowStock, batchesHref, requestHref }: { lowStock?: { name: string; item_code: string; available: string | number; reorder_level: string | number }; batchesHref?: string; requestHref?: string }) {
  const held = useShared<Row>("/inventory/batches", { qc_status: "QUARANTINE", page_size: "5" });
  const b = asRows(held)[0];
  if (!b && !lowStock) return null;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {b && (
        <section className="panel kpi-card p-5 flex flex-col" style={{ "--kpi-accent": "var(--danger)" } as React.CSSProperties} aria-label="Quality lab hold">
          <div className="flex items-center justify-between gap-2"><span className="badge tone-danger"><ClipboardCheck size={12} aria-hidden />Quality lab hold</span><span className="tag tag-mono">Lot #{b.batch_no}</span></div>
          <h3 className="font-heading font-bold text-[0.9375rem] mt-3">{b.item_code}</h3>
          <p className="t-support mt-1">In quarantine awaiting the QC decision. Made or received {fmtDate(b.mfg_date ?? b.created_at)}{b.expiry_date ? `, expires ${fmtDate(b.expiry_date)}` : ""}.</p>
          <div className="mt-auto pt-4"><div className="tile flex items-center justify-between gap-3"><span className="t-support">Released stock is blocked until a decision is recorded.</span><Link href={`/inventory/batches/${b.id}/traceability`} className="btn btn-sm btn-outline shrink-0">Open batch</Link></div></div>
        </section>
      )}
      {lowStock && (
        <section className="panel kpi-card p-5 flex flex-col" style={{ "--kpi-accent": "var(--amber)" } as React.CSSProperties} aria-label="Stock alert">
          <div className="flex items-center justify-between gap-2"><span className="badge tone-warning"><PackageSearch size={12} aria-hidden />Stock alert</span><span className="tag tag-mono">{lowStock.item_code}</span></div>
          <h3 className="font-heading font-bold text-[0.9375rem] mt-3">{lowStock.name}</h3>
          <p className="t-support mt-1">Below its reorder level: {fmtNum(Number(lowStock.available), 1)} available against a reorder point of {fmtNum(Number(lowStock.reorder_level), 1)}.</p>
          <div className="mt-auto pt-4"><div className="tile flex items-center justify-between gap-3"><span className="t-support">Raise a purchase request to cover the shortfall.</span>{requestHref && <Link href={requestHref} className="btn btn-sm btn-primary shrink-0">Raise request</Link>}</div></div>
          {batchesHref && <span className="sr-only">{batchesHref}</span>}
        </section>
      )}
    </div>
  );
}

export const QUICK = { Factory, ShoppingCart, Truck, ClipboardCheck };

/* ------------------------------------------------------------------ founder views */
type Summary = { sales: { receivable_outstanding: string | number }; finance: { payable_outstanding: string | number; cash_and_bank: string | number; inventory_value: string | number };
  finance_extended: { open_supplier_invoices: number; gross_margin_pct_30d: string | number | null; revenue_30d: string | number }; production: { average_yield_pct: string | number | null; wastage_qty_30d: string | number };
  quality_extended: { rejected_batches: number }; inventory: { value_by_kind?: { kind: string; value: string | number }[] } };
const num = (v: unknown) => Number(v ?? 0) || 0;
function lakh(v: number) { return v >= 1e7 ? `₹${fmtNum(v / 1e7, 2)} Cr` : v >= 1e5 ? `₹${fmtNum(v / 1e5, 1)} L` : fmtRupees(v); }

/** Business health: the six numbers a founder checks first. Every figure is from the management summary and the CRM summary. */
export function BusinessHealth({ s, crmEnabled, hrefs }: { s: Summary; crmEnabled: boolean; hrefs: Record<string, string | undefined> }) {
  const crm = useShared<Row>(crmEnabled ? "/crm/dashboard" : null);
  const cash = num(s.finance.cash_and_bank), payable = num(s.finance.payable_outstanding), receivable = num(s.sales.receivable_outstanding);
  const margin = s.finance_extended.gross_margin_pct_30d === null ? null : num(s.finance_extended.gross_margin_pct_30d);
  const yieldPct = s.production.average_yield_pct === null ? null : num(s.production.average_yield_pct);
  const fg = Array.isArray(s.inventory.value_by_kind) ? s.inventory.value_by_kind.find((k) => k.kind === "FG") : undefined;
  const cards: { label: string; value: string; sub: string; bar?: number; tone?: string; href?: string }[] = [
    { label: "Cash & bank", value: lakh(cash), sub: `Covers payables ${payable ? `${fmtNum(cash / payable, 1)}×` : "fully"}`, bar: cash + payable ? (cash / (cash + payable)) * 100 : 0, href: hrefs.cash },
    { label: "Net working position", value: lakh(cash + receivable - payable), sub: `Receivable ${lakh(receivable)} · payable ${lakh(payable)}`, href: hrefs.ageing },
    { label: "Gross margin, 30 days", value: margin === null ? "—" : fmtPct(margin), sub: `On ${lakh(num(s.finance_extended.revenue_30d))} revenue`, bar: margin ?? 0, tone: margin !== null && margin < 30 ? "var(--danger)" : undefined, href: hrefs.pnl },
    { label: "Sales pipeline", value: crm ? lakh(num(crm.open_pipeline_value)) : crm === undefined ? "…" : "—", sub: crm ? `Weighted ${lakh(num(crm.weighted_pipeline_value))} · win rate ${crm.win_rate_pct === null ? "—" : fmtPct(num(crm.win_rate_pct))}` : "Open opportunities", bar: crm && num(crm.open_pipeline_value) ? (num(crm.weighted_pipeline_value) / num(crm.open_pipeline_value)) * 100 : undefined, href: hrefs.pipeline },
    { label: "Inventory value", value: lakh(num(s.finance.inventory_value)), sub: fg ? `Finished goods ${lakh(num(fg.value))}` : "At moving-average cost", href: hrefs.inventory },
    { label: "Production yield", value: yieldPct === null ? "—" : fmtPct(yieldPct), sub: `${fmtNum(num(s.quality_extended.rejected_batches))} batches rejected · ${fmtNum(num(s.production.wastage_qty_30d))} units wasted (30 d)`, bar: yieldPct ?? 0, tone: yieldPct !== null && yieldPct < 90 ? "var(--amber)" : undefined, href: hrefs.yield },
  ];
  return (
    <section aria-label="Business health">
      <h2 className="label-caps mb-2">Business health</h2>
      <ul className="grid gap-3 grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
        {cards.map((c) => {
          const body = (
            <div className={`panel h-full p-3.5 flex flex-col ${c.href ? "panel-link" : ""}`}>
              <span className="label-caps !text-[0.625rem]">{c.label}</span>
              <span className="font-heading font-extrabold text-[1.125rem] leading-6 mt-1 tabular-nums truncate" title={c.value}>{c.value}</span>
              <span className="t-support mt-0.5 line-clamp-2">{c.sub}</span>
              {c.bar !== undefined && <div className="progress mt-auto pt-0 translate-y-1.5"><span className="bar-grow-x" style={{ width: `${Math.max(2, Math.min(100, c.bar))}%`, background: c.tone }} /></div>}
            </div>
          );
          return <li key={c.label} className="min-w-0">{c.href ? <Link href={c.href} className="block h-full rounded-[12px]">{body}</Link> : body}</li>;
        })}
      </ul>
    </section>
  );
}

const CHANNEL_NAME: Record<string, string> = { B2B: "B2B accounts", DISTRIBUTOR: "Distributors", BRAND: "Brand partners", MARKETPLACE: "Marketplaces", MANUAL: "Direct / manual" };
/** Ranked rupee bars with a share column: revenue by channel and top products. */
export function ShareBars({ data, names, empty }: { data: Record<string, number>; names?: Record<string, string>; empty: string }) {
  const entries = Object.entries(data).filter(([, v]) => v > 0);
  if (!entries.length) return <p className="t-support px-5 pb-5">{empty}</p>;
  const total = entries.reduce((a, [, v]) => a + v, 0), max = Math.max(...entries.map(([, v]) => v));
  return (
    <ul className="px-5 pb-5 space-y-3">
      {entries.map(([k, v], i) => (
        <li key={k}>
          <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]"><span className="truncate" title={k}>{(names ?? CHANNEL_NAME)[k] ?? k}</span><span className="shrink-0 tabular-nums"><b className="font-semibold">{lakh(v)}</b><span className="t-support ml-2 inline-block w-10 text-right">{fmtPct((v / total) * 100)}</span></span></div>
          <div className="progress mt-1.5"><span className="bar-grow-x" style={{ width: `${Math.max(2, (v / max) * 100)}%`, opacity: 1 - i * 0.12, animationDelay: `${i * 70}ms` }} /></div>
        </li>
      ))}
    </ul>
  );
}

const AGE_COLOUR = ["var(--brand-bright)", "#f59e0b", "#ea580c", "#e11d48", "#9f1239"];
/** Money customers owe, by how far past its due date it is. */
export function ReceivableAgeing({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort(([a], [b]) => a.localeCompare(b));
  const total = entries.reduce((a, [, v]) => a + v, 0);
  if (!total) return <p className="t-support px-5 pb-5">Nothing is owed by customers.</p>;
  const overdue = entries.filter(([k]) => !k.startsWith("0")).reduce((a, [, v]) => a + v, 0);
  return (
    <div className="px-5 pb-5">
      <p className="t-support">Owed <b className="text-fg">{lakh(total)}</b> · overdue <b className="text-danger">{lakh(overdue)}</b> ({fmtPct((overdue / total) * 100)})</p>
      <div className="mt-2.5 flex h-2.5 rounded-full overflow-hidden gap-[2px]" role="img" aria-label={`Receivables by age: ${entries.map(([k, v]) => `${k.slice(3)} ${lakh(v)}`).join(", ")}`}>
        {entries.map(([k, v]) => <span key={k} className="bar-grow-x h-full" style={{ width: `${(v / total) * 100}%`, background: AGE_COLOUR[Number(k[0])] ?? "#9f1239" }} />)}
      </div>
      <ul className="mt-3 space-y-2">
        {entries.map(([k, v]) => (
          <li key={k} className="flex items-center gap-2.5 text-[0.8125rem]">
            <span className="w-2.5 h-2.5 rounded-[3px] shrink-0" style={{ background: AGE_COLOUR[Number(k[0])] ?? "#9f1239" }} aria-hidden />
            <span className="flex-1 truncate">{k.slice(3)}</span>
            <b className="font-semibold tabular-nums">{lakh(v)}</b>
            <span className="t-support w-10 text-right tabular-nums">{fmtPct((v / total) * 100)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
