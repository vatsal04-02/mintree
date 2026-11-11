"use client";
/** Deal pipeline (Rapport pattern 02): KPI chips, a search pill and "New deal", tinted stage columns of coloured deal cards, and two drop zones
 *  under the board for Won and Lost. Drag a card onto a column or a zone, or use its menu. Won needs an accepted quotation; lost asks for a reason. */
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { CalendarClock, MoreHorizontal, Plus, Search, Target, TrendingUp, Trophy, X } from "lucide-react";
import { api, type Page } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDay, fmtNum, fmtRupees, titleCase } from "@/lib/format";
import { useToast } from "@/components/ui";
import { Strip } from "@/components/kpi";

type Opp = { id: number; number: string; name: string; stage: string; probability: number; expected_value: string; expected_close: string | null; owner_id: number | null; version: number; customer_id: number | null; lead_id: number | null };

const COLUMNS = ["PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION"] as const;
const MOVE_TO = [...COLUMNS, "WON", "LOST"] as const;
/** Stage colours: early work is ink, then yellow, green and orange as a deal moves towards closing. */
const STAGE: Record<string, { dot: string; card: string; tint: string; text: string }> = {
  PROSPECTING: { dot: "#151717", card: "#151717", tint: "rgb(21 23 23 / 0.05)", text: "#fff" },
  QUALIFIED: { dot: "#efc84b", card: "#efc84b", tint: "rgb(239 200 75 / 0.14)", text: "#151717" },
  PROPOSAL: { dot: "#2b5c4e", card: "#2b5c4e", tint: "rgb(43 92 78 / 0.10)", text: "#fff" },
  NEGOTIATION: { dot: "#c6431d", card: "#c6431d", tint: "rgb(198 67 29 / 0.09)", text: "#fff" },
};
/** ₹1.2Cr, ₹4.5L, ₹12k: for totals where the exact rupee does not matter. */
function lakhs(n: number) { return n >= 1e7 ? `₹${fmtNum(n / 1e7, 1)}Cr` : n >= 1e5 ? `₹${fmtNum(n / 1e5, 1)}L` : n >= 1e3 ? `₹${fmtNum(n / 1e3, 0)}k` : fmtRupees(n); }
const monthKey = (d: string | null) => (d ? d.slice(0, 7) : "none");

export default function CrmBoard() {
  const { can } = useAuth();
  const toast = useToast();
  const reduce = useReducedMotion();
  const [rows, setRows] = useState<Opp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [dragId, setDragId] = useState<number | null>(null);
  const [overCol, setOverCol] = useState<string | null>(null);
  const [menu, setMenu] = useState<number | null>(null);
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
      toast?.("success", stage === "WON" ? `${o.name} won · ${fmtRupees(o.expected_value)}` : `${o.name} → ${titleCase(stage)}`);
      load();
    } catch (e) { toast?.("error", (e as Error).message); }
  }
  const drop = (stage: string) => (e: React.DragEvent) => { e.preventDefault(); const o = rows?.find((r) => r.id === dragId); setOverCol(null); setDragId(null); if (o && canMove) move(o, stage); };
  const over = (stage: string) => (e: React.DragEvent) => { if (dragId !== null) { e.preventDefault(); setOverCol(stage); } };

  const open = (rows ?? []).filter((o) => !["WON", "LOST"].includes(o.stage));
  const wonRows = (rows ?? []).filter((o) => o.stage === "WON");
  const lostRows = (rows ?? []).filter((o) => o.stage === "LOST");
  const openValue = open.reduce((a, o) => a + Number(o.expected_value), 0);
  const weighted = open.reduce((a, o) => a + Number(o.expected_value) * (o.probability / 100), 0);
  const thisMonth = new Date().toISOString().slice(0, 7);
  const closingNow = open.filter((o) => monthKey(o.expected_close) === thisMonth);
  const closingValue = closingNow.reduce((a, o) => a + Number(o.expected_value), 0);
  const wonValue = wonRows.reduce((a, o) => a + Number(o.expected_value), 0);
  const winRate = wonRows.length + lostRows.length ? Math.round((wonRows.length / (wonRows.length + lostRows.length)) * 100) : null;

  return (
    <>
      <div className="flex flex-wrap items-end gap-x-10 gap-y-4 pt-2 mb-5">
        <h1 className="t-page">Deal<br />pipeline</h1>
        <Strip className="" label="Pipeline figures" cards={rows ? [
          { label: "Open pipeline", value: lakhs(openValue), icon: Trophy },
          { label: "Weighted forecast", value: lakhs(weighted), icon: TrendingUp },
          { label: "Deals closing this month", value: fmtNum(closingNow.length), chip: closingNow.length ? lakhs(closingValue) : undefined, tone: "warning", icon: CalendarClock },
          { label: "Win rate, closed deals", value: winRate === null ? "—" : `${winRate}%`, icon: Target },
        ] : null} />
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <label className="relative"><span className="sr-only">Search deals</span><Search size={15} strokeWidth={1.5} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden /><input className="input !pl-9 !w-64" placeholder="Search deals…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <div className="flex-1" />
        <div className="seg" role="group" aria-label="View"><button type="button" aria-pressed="true">Board</button><Link href="/m/sales/opportunities" className="px-3.5 inline-flex items-center rounded-full text-[0.8125rem] text-muted hover:text-fg">List</Link></div>
        {can("crm.opportunity:create") && <Link href="/m/sales/opportunities" className="btn btn-primary gap-1.5"><Plus size={15} aria-hidden />New deal</Link>}
      </div>

      {error && <p role="alert" className="t-support text-danger mb-3">{error}</p>}
      <div className="overflow-x-auto pb-2 -mx-1 px-1">
        <div className="grid grid-cols-4 gap-3.5 min-w-[60rem] items-start" role="list" aria-label="Pipeline stages">
          {COLUMNS.map((col, ci) => {
            const st = STAGE[col];
            const items = shown.filter((o) => o.stage === col);
            const value = items.reduce((a, o) => a + Number(o.expected_value), 0);
            const hot = overCol === col;
            return (
              <section key={col} role="listitem" aria-label={`${titleCase(col)}, ${items.length} deals`}
                className="rounded-[26px] p-2.5 min-h-[260px] transition-[background-color,box-shadow] duration-200"
                style={{ background: hot ? "rgb(43 92 78 / 0.12)" : st.tint, boxShadow: hot ? "inset 0 0 0 1.5px rgb(43 92 78 / 0.55)" : "inset 0 0 0 1px rgb(255 255 255 / 0.6)" }}
                onDragOver={over(col)} onDragLeave={() => setOverCol((c) => (c === col ? null : c))} onDrop={drop(col)}>
                <header className="flex items-start justify-between gap-2 px-2 pt-1.5 pb-3">
                  <div>
                    <h3 className="inline-flex items-center gap-2 text-[0.875rem] font-medium"><span className="w-2.5 h-2.5 rounded-full" style={{ background: st.dot }} aria-hidden />{titleCase(col)}<span className="pill pill-neutral">{items.length}</span></h3>
                    <p className="t-support tabular-nums mt-0.5 ml-[18px]">{lakhs(value)}</p>
                  </div>
                  {can("crm.opportunity:create") && <Link href="/m/sales/opportunities" className="ctl !w-8 !h-8" aria-label={`New deal in ${titleCase(col)}`}><Plus size={15} strokeWidth={1.5} /></Link>}
                </header>
                <div className="grid gap-2 max-h-[640px] overflow-y-auto pr-0.5 [mask-image:linear-gradient(to_bottom,black_calc(100%-24px),transparent)] pb-6">
                  {rows === null && !error && <div className="skeleton h-[135px] !rounded-[22px]" />}
                  {items.map((o, i) => (
                    <motion.article key={o.id} draggable={canMove}
                      onDragStart={() => setDragId(o.id)} onDragEnd={() => { setDragId(null); setOverCol(null); }}
                      initial={reduce ? false : { opacity: 0, y: 14, filter: "blur(8px)" }} animate={{ opacity: dragId === o.id ? 0.9 : 1, y: 0, filter: "blur(0px)", scale: dragId === o.id ? 1.04 : 1, rotate: dragId === o.id ? -2 : 0 }}
                      transition={{ duration: 0.45, delay: reduce ? 0 : (ci * 3 + i) * 0.03, ease: [0.22, 1, 0.36, 1] }}
                      className={`relative rounded-[22px] p-3.5 min-h-[135px] flex flex-col ${canMove ? "cursor-grab active:cursor-grabbing" : ""}`}
                      style={{ background: st.card, color: st.text, boxShadow: dragId === o.id ? "0 30px 50px -18px rgb(21 23 23 / 0.5)" : "0 10px 24px -16px rgb(21 23 23 / 0.35)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[0.75rem] opacity-70">{o.expected_close ? `Closes ${fmtDay(o.expected_close)}` : "No close date"}</span>
                        <div className="relative">
                          <button type="button" className="w-[30px] h-[30px] rounded-full grid place-items-center" style={{ border: `1px solid ${st.text === "#fff" ? "rgb(255 255 255 / 0.3)" : "rgb(21 23 23 / 0.2)"}` }}
                            aria-label={`Move ${o.name}`} aria-haspopup="menu" aria-expanded={menu === o.id} disabled={!canMove} onClick={() => setMenu(menu === o.id ? null : o.id)}><MoreHorizontal size={14} strokeWidth={1.5} /></button>
                          {menu === o.id && (
                            <ul role="menu" className="pop-in absolute right-0 top-9 z-20 w-48 rounded-2xl p-1 panel !text-[var(--fg)]">
                              {MOVE_TO.filter((s) => s !== o.stage).map((s) => <li key={s} role="none"><button role="menuitem" className="w-full text-left px-3 h-9 rounded-xl text-[0.8125rem] hover:bg-[rgb(21_23_23/0.06)]" onClick={() => { setMenu(null); move(o, s); }}>Move to {titleCase(s)}</button></li>)}
                            </ul>
                          )}
                        </div>
                      </div>
                      <h4 className="mt-2 text-[1rem] leading-5 font-medium line-clamp-2" title={o.name}>{o.name}</h4>
                      <p className="text-[0.75rem] opacity-60 mt-0.5 font-mono">{o.number}</p>
                      <div className="mt-auto pt-2 flex items-end justify-between gap-2">
                        <span className="text-[1.125rem] font-medium tabular-nums">{fmtRupees(o.expected_value)}</span>
                        <span className="text-[0.6875rem] opacity-70">{o.probability}%</span>
                      </div>
                    </motion.article>
                  ))}
                  {rows !== null && items.length === 0 && <p className="t-support px-2 py-6">No deals in this stage.</p>}
                </div>
              </section>
            );
          })}
        </div>
      </div>

      <div className="grid sm:grid-cols-2 gap-3.5 mt-2">
        {([["WON", wonRows, Trophy, "Drop to win"], ["LOST", lostRows, X, "Drop to lose"]] as const).map(([stage, list, Icon, hint]) => {
          const hot = overCol === stage;
          return (
            <section key={stage} aria-label={`${titleCase(stage)} deals`} onDragOver={over(stage)} onDragLeave={() => setOverCol((c) => (c === stage ? null : c))} onDrop={drop(stage)}
              className="h-[65px] rounded-[24px] px-3 flex items-center gap-3 transition-colors duration-200"
              style={{ border: `1.5px ${hot ? "solid" : "dashed"} ${hot ? (stage === "WON" ? "#2b5c4e" : "#c6431d") : "rgb(21 23 23 / 0.18)"}`, background: hot ? "rgb(255 255 255 / 0.6)" : "rgb(255 255 255 / 0.35)" }}>
              <span className="w-10 h-10 rounded-full grid place-items-center shrink-0" style={stage === "WON" ? { background: "#2b5c4e", color: "#fff" } : { background: "rgb(21 23 23 / 0.07)", color: "var(--muted)" }} aria-hidden><Icon size={16} strokeWidth={1.5} /></span>
              <div className="min-w-0 flex-1">
                <p className="text-[0.875rem] font-medium">{titleCase(stage)}</p>
                <p className="t-support truncate">{stage === "WON" ? `${fmtNum(list.length)} won · ${lakhs(wonValue)}` : `${fmtNum(list.length)} closed lost`}</p>
              </div>
              <span className="t-support shrink-0">{hint}</span>
            </section>
          );
        })}
      </div>
    </>
  );
}
