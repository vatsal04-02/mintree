"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Calendar, MoreHorizontal, Plus, Search } from "lucide-react";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDay, fmtRupees, titleCase } from "@/lib/format";
import { useToast } from "@/components/ui";

type Opp = { id: number; number: string; name: string; stage: string; probability: number; expected_value: string; expected_close: string | null; owner_id: number | null; version: number; customer_id: number | null; lead_id: number | null };

const COLUMNS = ["PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION", "WON"] as const;
const MOVE_TO = [...COLUMNS, "LOST"] as const;
/** Chart series: teal, gold, clay, slate. They differ by lightness as well as hue, and every one is labelled. */
const SERIES = [
  { key: "early", label: "Prospecting", stages: ["PROSPECTING"], color: "#01877a" },
  { key: "qual", label: "Qualified", stages: ["QUALIFIED"], color: "#ebbf20" },
  { key: "prop", label: "Proposal and negotiation", stages: ["PROPOSAL", "NEGOTIATION"], color: "#c97a3e" },
  { key: "won", label: "Won", stages: ["WON"], color: "#6fa3cc" },
];

function monthKey(d: string | null) { return d ? d.slice(0, 7) : "none"; }
function monthLabel(k: string) { const [y, m] = k.split("-").map(Number); return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" }); }

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
  const hot = active ?? months[Math.floor(months.length / 2)]?.k ?? null;

  const priorityId = [...(rows ?? [])].filter((o) => o.stage === "NEGOTIATION").sort((a, b) => Number(b.expected_value) - Number(a.expected_value))[0]?.id;
  const open = (rows ?? []).filter((o) => !["WON", "LOST"].includes(o.stage));
  const won = (rows ?? []).filter((o) => o.stage === "WON").length;
  const lost = (rows ?? []).filter((o) => o.stage === "LOST").length;
  const winRate = won + lost ? Math.round((won / (won + lost)) * 100) : null;
  const openValue = open.reduce((a, o) => a + Number(o.expected_value), 0);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex-1 min-w-[240px]">
          <span className="sr-only">Search deals</span>
          <Search size={17} className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--muted)]" aria-hidden />
          <input className="input !h-[52px] !rounded-2xl !pl-11 !bg-[var(--card)]" placeholder="Search deals" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        {can("crm.opportunity:create") && <a href="/m/sales/opportunities" className="btn btn-primary !h-[52px] !rounded-[14px] !px-6"><Plus size={17} aria-hidden />Add deal</a>}
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <section aria-label="Expected value by close month" className="panel p-6 lg:col-span-2 min-w-0">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <h2 className="font-heading text-[28px] leading-9 font-normal tracking-[-0.02em]">Pipeline by month</h2>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 t-support">
              {SERIES.map((s) => <li key={s.key} className="inline-flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full" style={{ background: s.color }} aria-hidden />{s.label}</li>)}
            </ul>
          </div>
          {months.length === 0 ? <p className="t-support mt-10 mb-8">No deals with an expected close date yet.</p> : (
            <div className="mt-6 flex items-end gap-4 h-[220px] relative" role="group" aria-label="Stacked bars">
              {months.map((m) => {
                const total = m.parts.reduce((a, p) => a + p.v, 0);
                const on = hot === m.k;
                return (
                  <button key={m.k} type="button" onMouseEnter={() => setActive(m.k)} onFocus={() => setActive(m.k)} aria-label={`${monthLabel(m.k)}: ${m.parts.map((p) => `${p.label} ${fmtRupees(p.v)}`).join(", ")}`}
                    className="relative flex-1 min-w-0 h-full flex flex-col justify-end items-center gap-1 max-w-[62px] mx-auto">
                    <span className="relative w-full" style={{ height: `${(total / max) * 78}%` }}>
                    {on && (
                      <span className="absolute z-10 left-1/2 bottom-full mb-2 -translate-x-1/2 w-max rounded-xl px-3 py-2 text-[12px] text-left shadow-lg pointer-events-none" style={{ background: "var(--ink)", color: "#f2fbf8" }}>
                        {m.parts.filter((p) => p.v > 0).map((p) => <span key={p.key} className="flex items-center gap-2"><span className="w-2 h-2 rounded-full" style={{ background: p.color }} />{p.label} - {fmtRupees(p.v)}</span>)}
                      </span>
                    )}
                    <span className="absolute inset-0 flex flex-col-reverse gap-[3px] transition-opacity duration-150" style={{ opacity: on ? 1 : 0.45 }}>
                      {m.parts.filter((p) => p.v > 0).map((p) => <span key={p.key} className="rounded-[7px]" style={{ background: p.color, flex: p.v }} />)}
                    </span></span>
                    <span className="t-support mt-1">{monthLabel(m.k)}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <div className="grid gap-5 content-start">
          <Stat label="Open deals" value={rows ? String(open.length) : "-"} note={rows ? `${fmtRupees(openValue)} expected` : undefined} />
          <Stat label="Win rate" value={winRate === null ? "-" : `${winRate}%`} note={rows ? `${won} won, ${lost} lost` : undefined} />
        </div>
      </div>

      {error && <p role="alert" className="t-support text-[var(--danger)]">{error}</p>}
      <div className="flex gap-0 overflow-x-auto pb-4 -mx-1 px-1 items-start" role="list" aria-label="Pipeline stages">
        {COLUMNS.map((col, i) => {
          const items = shown.filter((o) => o.stage === col);
          return (
            <section key={col} role="listitem" aria-label={`${titleCase(col)}, ${items.length} deals`}
              className="w-[316px] shrink-0 px-4 min-h-[200px] rounded-2xl transition-colors"
              style={{ borderLeft: i ? "1px dashed var(--line-strong)" : undefined, background: overCol === col ? "color-mix(in srgb, var(--brand) 8%, transparent)" : undefined }}
              onDragOver={(e) => { if (dragId !== null) { e.preventDefault(); setOverCol(col); } }}
              onDragLeave={() => setOverCol((c) => (c === col ? null : c))}
              onDrop={(e) => { e.preventDefault(); const o = rows?.find((r) => r.id === dragId); setOverCol(null); setDragId(null); if (o && canMove) move(o, col); }}>
              <h3 className="font-heading text-[28px] leading-9 font-normal tracking-[-0.02em] mb-4">{titleCase(col)}<span className="t-support ml-2 align-middle">{items.length}</span></h3>
              <div className="grid gap-3">
                {rows === null && !error && <div className="skeleton h-[150px] rounded-[20px]" />}
                {items.map((o) => {
                  const dragging = dragId === o.id;
                  const hero = dragging || o.id === priorityId;
                  return (
                    <article key={o.id} draggable={canMove} onDragStart={() => setDragId(o.id)} onDragEnd={() => { setDragId(null); setOverCol(null); }}
                      className="relative rounded-[20px] p-[18px] transition-shadow duration-150"
                      style={hero ? { background: "var(--butter-grad)", color: "#ffffff", boxShadow: "0 20px 36px -24px rgb(0 0 0 / 0.35)" } : { background: "var(--card)", border: "1px solid var(--line)", boxShadow: "var(--shadow-md)" }}>
                      <div className="flex items-center justify-between">
                        <span className="badge" style={hero ? { background: "var(--gold)", color: "var(--gold-ink)" } : { background: "var(--brand-tint)", color: "#0b5f57" }}>{hero ? "Priority" : `${o.probability}%`}</span>
                        <div className="relative">
                          <button type="button" className="icon-btn icon-btn-sm !border-transparent !bg-transparent" aria-label={`Move ${o.name}`} aria-haspopup="menu" aria-expanded={menu === o.id} disabled={!canMove} onClick={() => setMenu(menu === o.id ? null : o.id)}><MoreHorizontal size={17} aria-hidden /></button>
                          {menu === o.id && (
                            <ul role="menu" className="absolute right-0 top-9 z-20 w-44 rounded-xl p-1 panel">
                              {MOVE_TO.filter((s) => s !== o.stage).map((s) => <li key={s} role="none"><button role="menuitem" className="w-full text-left px-3 h-9 rounded-lg text-[13px] hover:bg-[var(--inset)]" onClick={() => { setMenu(null); move(o, s); }}>Move to {titleCase(s)}</button></li>)}
                            </ul>
                          )}
                        </div>
                      </div>
                      <h4 className="font-heading text-[19px] leading-6 font-medium tracking-[-0.01em] mt-3">{o.name}</h4>
                      <p className="text-[12.5px] leading-[18px] mt-1" style={{ color: hero ? "#cfeee9" : "var(--muted)" }}>{o.number} · expected {fmtRupees(o.expected_value)}</p>
                      <div className="mt-4 pt-3 flex items-center justify-between text-[12.5px]" style={{ borderTop: `1px solid ${hero ? "rgb(255 255 255 / 0.22)" : "var(--line)"}` }}>
                        <span className="inline-flex items-center gap-1.5"><Calendar size={14} aria-hidden />{o.expected_close ? fmtDay(o.expected_close) : "No close date"}</span>
                        <span className="font-medium">{fmtRupees(o.expected_value)}</span>
                      </div>
                    </article>
                  );
                })}
                {rows !== null && items.length === 0 && <p className="t-support py-6">No deals in this stage.</p>}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <section aria-label={label} className="panel p-6 min-h-[132px] flex flex-col justify-between">
      <h2 className="font-heading text-[22px] leading-7 font-normal tracking-[-0.02em]">{label}</h2>
      <div className="flex items-end justify-between gap-3">
        <span className="font-heading text-[44px] leading-[48px] font-normal tracking-[-0.03em]" style={{ fontVariantNumeric: "proportional-nums lining-nums" }}>{value}</span>
        {note && <span className="badge" style={{ background: "var(--brand-tint)", color: "#0b5f57" }}>{note}</span>}
      </div>
    </section>
  );
}


