"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, ExternalLink, Pause, Play, RotateCcw, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { fmtPct } from "@/lib/format";
import { useLiveEvent, useLiveState, useLiveTelemetry } from "@/lib/live";
import { Drawer, useToast } from "./ui";

type Status = { available: boolean; active?: boolean; paused?: boolean; speed?: 1 | 3; worker_alive?: boolean; counters?: Record<string, number>; controls?: boolean; last_action_at?: string | null };
type Metrics = { requests_per_s: number | null; p95_ms: number | null; error_rate: number | null; db_connections: number | null; in_flight: number | null; queue_depth: number | null };

const TRIGGERS: { kind: string; label: string }[] = [
  { kind: "order", label: "New order" }, { kind: "quality_alert", label: "Quality alert" },
  { kind: "big_payment", label: "Big payment" }, { kind: "low_stock", label: "Low stock" },
];
const LABELS: Record<string, string> = { order: "Orders", payment: "Payments", receipt: "Receipts", production: "Production steps", quality_alert: "Quality alerts", trigger: "Triggered" };

/** Small "Demo mode" pill for the top bar. Renders nothing unless Live Mode is available in this environment and switched on.
 *  For the demo administrator it opens the Demo controls drawer. Everything the mode creates goes through the real API and is tagged source="demo-live". */
export function DemoPill() {
  const [st, setSt] = useState<Status | null>(null);
  const [open, setOpen] = useState(false);
  const load = useCallback(() => api<Status>("/demo/status").then(setSt).catch(() => setSt(null)), []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  if (!st?.available || !(st.active || st.paused)) return null;
  const label = st.paused ? "Demo mode · paused" : "Demo mode";
  const dot = <span className={`inline-block w-1.5 h-1.5 rounded-full ${st.paused ? "bg-[var(--acc-yellow)]" : "bg-[var(--acc-green)]"}`} aria-hidden />;
  const cls = "hidden sm:inline-flex items-center gap-1.5 h-8 px-3 rounded-full border border-line bg-surface text-[0.75rem] text-muted whitespace-nowrap";
  return <>
    {st.controls
      ? <button type="button" className={`${cls} hover:border-line-strong`} onClick={() => setOpen(true)} aria-label="Open demo controls" data-testid="demo-pill">{dot}{label}</button>
      : <span className={cls} data-testid="demo-pill">{dot}{label}</span>}
    {st.controls && <ControlsDrawer open={open} onClose={() => setOpen(false)} status={st} reload={load} />}
  </>;
}

function ControlsDrawer({ open, onClose, status, reload }: { open: boolean; onClose: () => void; status: Status; reload: () => void }) {
  const toast = useToast();
  const [m, setM] = useState<Metrics | null>(null);
  const [recent, setRecent] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const live = useLiveState();
  const tel = useLiveTelemetry();
  useEffect(() => {
    if (!open) return;
    let on = true;
    const tick = () => api<Metrics>("/demo/metrics").then((d) => on && setM(d)).catch(() => {});
    tick(); const t = setInterval(tick, 3000);
    return () => { on = false; clearInterval(t); };
  }, [open]);
  useLiveEvent(["order.created", "payment.received", "stock.changed", "batch.stage", "quality.alert"], (e) => {
    const d = e.data as Record<string, unknown>;
    const line = e.type === "order.created" ? `Order ${d.external_id} · ${d.qty} × ${d.item}` : e.type === "payment.received" ? `Payment ₹${d.amount} on invoice ${d.invoice_id}`
      : e.type === "stock.changed" ? `Goods received against PO ${d.po_id}` : e.type === "batch.stage" ? `${d.number}: ${d.step}` : `Quality alert on ${d.order}`;
    setRecent((r) => [line, ...r].slice(0, 6));
  });
  const send = async (path: string, body: unknown, ok: string) => {
    setBusy(true);
    try { await api(path, { method: "POST", body }); toast("success", ok); reload(); }
    catch (e) { toast("error", e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  };
  const reset = async () => {
    if (!window.confirm("Restore the golden snapshot? Everything created since it was taken is discarded. You stay signed in.")) return;
    await send("/demo/reset", {}, "Restored. Refreshing…");
    setTimeout(() => window.location.reload(), 800);
  };
  const c = status.counters ?? {};
  const speed = status.speed ?? 1;
  const tile = "rounded-2xl border border-line bg-surface px-3 py-2";
  return (
    <Drawer open={open} onClose={onClose} title="Demo controls" subtitle="Everything here runs through the real application and is tagged source “demo-live”." width={460}>
      <div className="flex flex-col gap-5 text-[0.8125rem]">
        <section aria-label="Run state" className="flex flex-wrap items-center gap-2">
          {status.paused
            ? <button className="btn btn-primary" disabled={busy} onClick={() => send("/demo/controls", { action: "resume" }, "Live resumed")}><Play size={14} />Resume live</button>
            : <button className="btn" disabled={busy} onClick={() => send("/demo/controls", { action: "pause" }, "Live paused")}><Pause size={14} />Pause live</button>}
          <div className="seg" role="group" aria-label="Speed">
            {([1, 3] as const).map((s) => <button key={s} type="button" aria-pressed={speed === s} className={speed === s ? "on" : ""} disabled={busy} onClick={() => send("/demo/controls", { action: "speed", speed: s }, `Speed ×${s}`)}>×{s}</button>)}
          </div>
          <span className="text-muted ml-auto">{live === "live" ? "Stream connected" : live === "polling" ? "Polling fallback" : "Connecting…"}</span>
        </section>

        <Link href="/admin/demo-control" onClick={onClose} className="btn justify-start" data-testid="open-control-centre"><ExternalLink size={14} />Open the Demo Control Centre (scenario and all actions)</Link>

        <section aria-label="Trigger a scenario">
          <h3 className="t-section mb-2">Trigger now</h3>
          <div className="grid grid-cols-2 gap-2">
            {TRIGGERS.map((t) => <button key={t.kind} className="btn justify-start" disabled={busy} onClick={() => send("/demo/trigger", { kind: t.kind }, `${t.label} queued`)}><Zap size={14} />{t.label}</button>)}
          </div>
        </section>

        <section aria-label="Live metrics">
          <h3 className="t-section mb-2 flex items-center gap-1.5"><Activity size={14} />Server</h3>
          <div className="grid grid-cols-3 gap-2">
            {[["Requests/s", m?.requests_per_s], ["p95 (ms)", m?.p95_ms], ["Error rate", m?.error_rate == null ? null : fmtPct(m.error_rate * 100)], ["DB connections", m?.db_connections], ["In flight", m?.in_flight], ["Queue depth", m?.queue_depth]].map(([k, v]) =>
              <div key={String(k)} className={tile}><div className="text-muted">{k}</div><div className="t-kpi tabular-nums">{v ?? "–"}</div></div>)}
          </div>
        </section>

        <section aria-label="Created so far">
          <h3 className="t-section mb-2">Created this session</h3>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-1">
            {Object.keys(LABELS).map((k) => <li key={k} className="flex justify-between"><span className="text-muted">{LABELS[k]}</span><span className="tabular-nums">{c[k] ?? 0}</span></li>)}
          </ul>
          {recent.length > 0 && <ul className="mt-3 flex flex-col gap-1 text-muted">{recent.map((r, i) => <li key={i} className="truncate" title={r}>{r}</li>)}</ul>}
        </section>

        {tel && tel.batches.length > 0 && (
          <section aria-label="Instrument readings (simulated)">
            <h3 className="t-section mb-2 flex items-center gap-2">Instruments <span className="tag tag-sim">Simulated</span></h3>
            <ul className="flex flex-col gap-1">{tel.batches.map((b) => <li key={b.order_id} className="flex justify-between gap-2"><span className="truncate">{b.number}</span>
              <span className="tabular-nums text-muted">{b.readings.temperature_c} °C · {b.readings.weigh_scale_kg} kg · {b.readings.line_speed_ppm} /min</span></li>)}</ul>
          </section>
        )}

        <section aria-label="Reset">
          <button className="btn btn-danger" disabled={busy} onClick={reset}><RotateCcw size={14} />Reset to golden snapshot</button>
        </section>
      </div>
    </Drawer>
  );
}
