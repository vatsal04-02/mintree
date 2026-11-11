"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Calendar, MoreHorizontal, Plus, Search } from "lucide-react";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDay, fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { PageHeader, useToast } from "@/components/ui";

type Opp = { id: number; number: string; name: string; stage: string; probability: number; expected_value: string; expected_close: string | null; owner_id: number | null; version: number; customer_id: number | null; lead_id: number | null };

const COLUMNS = ["PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION", "WON"] as const;
const MOVE_TO = [...COLUMNS, "LOST"] as const;
/** One colour per stage, shared by the chart, its legend and the column headers: slate (early), blue (qualified), amber (in proposal), emerald (won). */
const STAGE_COLOR: Record<string, string> = { PROSPECTING: "#94a3b8", QUALIFIED: "#3b82f6", PROPOSAL: "#f59e0b", NEGOTIATION: "#d97706", WON: "#059669" };
const SERIES = [
  { key: "early", label: "Prospecting", stages: ["PROSPECTING"], color: STAGE_COLOR.PROSPECTING },
  { key: "qual", label: "Qualified", stages: ["QUALIFIED"], color: STAGE_COLOR.QUALIFIED },
  { key: "prop", label: "Proposal & negotiation", stages: ["PROPOSAL", "NEGOTIATION"], color: STAGE_COLOR.PROPOSAL },
  { key: "won", label: "Won", stages: ["WON"], color: STAGE_COLOR.WON },
];

function monthKey(d: string | null) { return d ? d.slice(0, 7) : "none"; }
function monthLabel(k: string) { const [y, m] = k.split("-").map(Number); return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" }); }
/** ₹1.2Cr, ₹4.5L, ₹12k: for axis-free totals where the exact rupee does not matter. */
function lakhs(n: number) { return n >= 1e7 ? `₹${fmtNum(n / 1e7, 1)}Cr` : n >= 1e5 ? `₹${fmtNum(n / 1e5, 1)}L` : n >= 1e3 ? `₹${fmtNum(n / 1e3, 0)}k` : fmtRupees(n); }

export default function CrmBoard() {
  const { can } = useAuth();
  const toast = useToast();
  const [rows, setRows] = useState<Opp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [dragId, setDragId] = useState<number | null>(null);
  const [overCol, setOverCol] = useState<string | null>(null);
  const [menu, setMenu] = useState<number | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const canMove = can("crm.opportunity:update");

  const load = useCallback(() => {
    api<Page<Opp>>("/crm/opportunities", { params: { page_size: 100 } }).then((r) => setRows(r.items)).catch((e) => setError(e.message ?? "Could not load the pipeline"));
  }, []);
  useEffect(load, [load]);

  const shown = useMemo(() => (rows ?? []).filter((o) => !q || `${o.name} ${o.number}`.toLowerCase().includes(q.toLowerCase())), [rows, q]);

  async function move(o: Opp, stage: string) {
    if (o.stage === stage) return;
    let lost_reason: string | undefined;
    if (stage === "LOST") { lost_reason = window.prompt("Why was this deal lost?") ?? ""; if (!lost_reason) return; }
    try {
      await api(`/crm/opportunities/${o.id}/stage`, { method: "POST", body: { expected_version: o.version, stage, lost_reason } });
      toast?.("success", `${o.name} moved to ${titleCase(stage)}`);
      load();
    } catch (e) { toast?.("error", (e as Error).message); }
  }

  // chart: expected value by close month, stacked by stage group
  const months = useMemo(() => {
    const keys = Array.from(new Set((rows ?? []).filter((o) => o.stage !== "LOST" && o.expected_close).map((o) => monthKey(o.expected_close)))).sort().slice(-6);
    return keys.map((k) => ({ k, parts: SERIES.map((s) => ({ ...s, v: (rows ?? []).filter((o) => monthKey(o.expected_close) === k && s.stages.includes(o.stage)).reduce((a, o) => a + Number(o.expected_value), 0) })) }));
  }, [rows]);
  const max = Math.max(1, ...months.map((m) => m.parts.reduce((a, p) => a + p.v, 0)));

  const open = (rows ?? []).filter((o) => !["WON", "LOST"].includes(o.stage));
  const won = (rows ?? []).filter((o) => o.stage === "WON").length;
  const lost = (rows ?? []).filter((o) => o.stage === "LOST").length;
  const winRate = won + lost ? Math.round((won / (won + lost)) * 100) : null;
  const openValue = open.reduce((a, o) => a + Number(o.expected_value), 0);
  const weighted = open.reduce((a, o) => a + Number(o.expected_value) * (o.probability / 100), 0);
  const thisMonth = new Date().toISOString().slice(0, 7);
  const closingNow = open.filter((o) => monthKey(o.expected_close) === thisMonth);
  const overdue = open.filter((o) => o.expected_close && o.expected_close < new Date().toISOString().slice(0, 10)).length;

  return (
    <>
      <PageHeader eyebrow="Factory Plant 01 / CRM & Sales" title="Pipeline board" description="Open deals by stage. Drag a card, or use its menu, to move it; won needs an accepted quotation and lost needs a reason."
        actions={<div className="flex flex-wrap items-center gap-2">
          <label className="relative"><span className="sr-only">Search deals</span><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden /><input className="input !h-9 !pl-8 !w-56" placeholder="Search deal or number…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
          {can("crm.opportunity:create") && <a href="/m/sales/opportunities" className="btn btn-primary gap-1.5"><Plus size={15} aria-hidden />Add deal</a>}
        </div>} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] mb-4 items-stretch">
        <section aria-label="Expected value by close month" className="panel p-5 min-w-0 flex flex-col">
          <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
            <h2 className="t-section">Expected value by close month</h2>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 t-support">
              {SERIES.map((s) => <li key={s.key} className="inline-flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />{s.label}</li>)}
            </ul>
          </header>
          {months.length === 0 ? <p className="t-support mt-8 mb-6">No deals with an expected close date yet.</p> : (
            <div className="mt-5 flex-1 min-h-[180px] flex items-end gap-6 border-b border-line-strong px-2" role="group" aria-label="Stacked bars by month" onMouseLeave={() => setActive(null)}>
              {months.map((m) => {
                const total = m.parts.reduce((a, p) => a + p.v, 0);
                const on = active === m.k;
                return (
                  <button key={m.k} type="button" onMouseEnter={() => setActive(m.k)} onFocus={() => setActive(m.k)} onBlur={() => setActive(null)}
                    aria-label={`${monthLabel(m.k)}: ${m.parts.map((p) => `${p.label} ${fmtRupees(p.v)}`).join(", ")}`}
                    className="relative flex-1 min-w-0 h-full flex flex-col justify-end items-center max-w-[56px] mx-auto">
                    <span className="t-support font-semibold mb-1 tabular-nums">{lakhs(total)}</span>
                    <span className="relative w-full flex flex-col-reverse bar-grow" style={{ height: `${(total / max) * 82}%`, minHeight: 4 }}>
                      {m.parts.filter((p) => p.v > 0).map((p, i, a) => <span key={p.key} className={i === a.length - 1 ? "rounded-t-[3px]" : ""} style={{ background: p.color, flex: p.v, boxShadow: i ? "inset 0 -1px 0 #fff" : undefined }} />)}
                      {on && (
                        <span className="absolute z-10 left-1/2 bottom-full mb-7 -translate-x-1/2 w-max panel px-3 py-2 text-[0.75rem] text-left pointer-events-none" role="tooltip">
                          <span className="block font-semibold mb-1">{monthLabel(m.k)} · {fmtRupees(total)}</span>
                          {m.parts.filter((p) => p.v > 0).map((p) => <span key={p.key} className="flex items-center gap-2"><span className="w-2 h-2 rounded-sm" style={{ background: p.color }} />{p.label}<b className="ml-auto pl-3 tabular-nums">{fmtRupees(p.v)}</b></span>)}
                        </span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {months.length > 0 && <div className="flex gap-6 px-2 mt-1.5">{months.map((m) => <span key={m.k} className="flex-1 max-w-[56px] mx-auto text-center t-support">{monthLabel(m.k)}</span>)}</div>}
        </section>

        <div className="grid gap-4 grid-cols-2">
          <Stat label="Open deals" value={rows ? fmtNum(open.length) : "—"} note={rows ? `${lakhs(openValue)} expected` : undefined} />
          <Stat label="Weighted pipeline" value={rows ? lakhs(weighted) : "—"} note="Value × stage probability" />
          <Stat label="Win rate" value={winRate === null ? "—" : `${winRate}%`} note={rows ? `${won} won · ${lost} lost` : undefined} />
          <Stat label="Closing this month" value={rows ? fmtNum(closingNow.length) : "—"} note={rows ? (overdue ? `${overdue} past close date` : "None past close date") : undefined} tone={overdue ? "danger" : undefined} />
        </div>
      </div>

      {error && <p role="alert" className="t-support text-danger mb-3">{error}</p>}
      <div className="overflow-x-auto pb-2">
        <div className="grid grid-cols-5 gap-3 min-w-[64rem] items-start" role="list" aria-label="Pipeline stages">
          {COLUMNS.map((col) => {
            const items = shown.filter((o) => o.stage === col);
            const value = items.reduce((a, o) => a + Number(o.expected_value), 0);
            return (
              <section key={col} role="listitem" aria-label={`${titleCase(col)}, ${items.length} deals`}
                className="rounded-xl p-2 min-h-[200px] transition-colors"
                style={{ background: overCol === col ? "color-mix(in srgb, var(--brand) 10%, var(--tile))" : "var(--tile)" }}
                onDragOver={(e) => { if (dragId !== null) { e.preventDefault(); setOverCol(col); } }}
                onDragLeave={() => setOverCol((c) => (c === col ? null : c))}
                onDrop={(e) => { e.preventDefault(); const o = rows?.find((r) => r.id === dragId); setOverCol(null); setDragId(null); if (o && canMove) move(o, col); }}>
                <header className="flex items-center justify-between gap-2 px-1.5 pt-1 pb-2.5">
                  <h3 className="inline-flex items-center gap-2 text-[0.8125rem] font-semibold"><span className="w-2 h-2 rounded-full" style={{ background: STAGE_COLOR[col] }} aria-hidden />{titleCase(col)}<span className="t-support font-medium">{items.length}</span></h3>
                  <span className="t-support font-semibold tabular-nums">{lakhs(value)}</span>
                </header>
                <div className="grid gap-2">
                  {rows === null && !error && <div className="skeleton h-[104px] rounded-lg" />}
                  {items.map((o) => (
                    <article key={o.id} draggable={canMove} onDragStart={() => setDragId(o.id)} onDragEnd={() => { setDragId(null); setOverCol(null); }}
                      className={`panel p-3 ${canMove ? "cursor-grab active:cursor-grabbing" : ""} ${dragId === o.id ? "opacity-50" : ""}`}>
                      <div className="flex items-start justify-between gap-2">
                        <h4 className="text-[0.8125rem] font-semibold leading-5 line-clamp-2 min-w-0" title={o.name}>{o.name}</h4>
                        <div className="relative shrink-0 -mr-1 -mt-0.5">
                          <button type="button" className="icon-btn icon-btn-sm !border-transparent !bg-transparent" aria-label={`Move ${o.name}`} aria-haspopup="menu" aria-expanded={menu === o.id} disabled={!canMove} onClick={() => setMenu(menu === o.id ? null : o.id)}><MoreHorizontal size={16} aria-hidden /></button>
                          {menu === o.id && (
                            <ul role="menu" className="pop-in absolute right-0 top-8 z-20 w-44 rounded-lg p-1 panel">
                              {MOVE_TO.filter((s) => s !== o.stage).map((s) => <li key={s} role="none"><button role="menuitem" className="w-full text-left px-3 h-8 rounded-md text-[0.8125rem] hover:bg-[var(--brand-tint)] hover:text-[var(--brand-hover)]" onClick={() => { setMenu(null); move(o, s); }}>Move to {titleCase(s)}</button></li>)}
                            </ul>
                          )}
                        </div>
                      </div>
                      <p className="mt-1 flex items-center justify-between gap-2 text-[0.6875rem] text-muted"><span className="font-mono">{o.number}</span>{col !== "WON" && <span className="tabular-nums" title="Stage probability">{o.probability}%</span>}</p>
                      <div className="mt-2.5 pt-2 border-t border-line flex items-center justify-between gap-2 text-[0.75rem] whitespace-nowrap">
                        <span className="inline-flex items-center gap-1 text-muted"><Calendar size={12} aria-hidden />{o.expected_close ? fmtDay(o.expected_close) : "No date"}</span>
                        <span className="font-semibold tabular-nums">{fmtRupees(o.expected_value)}</span>
                      </div>
                    </article>
                  ))}
                  {rows !== null && items.length === 0 && <p className="t-support px-1.5 py-4">No deals in this stage.</p>}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </>
  );
}

function Stat({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "danger" }) {
  return (
    <section aria-label={label} className="panel p-4 flex flex-col">
      <h2 className="label-caps">{label}</h2>
      <p className="t-kpi mt-1.5">{value}</p>
      {note && <p className={`t-support mt-auto pt-1 ${tone === "danger" ? "text-danger font-semibold" : ""}`}>{note}</p>}
    </section>
  );
}
