"use client";
import { useCallback, useEffect, useState } from "react";
import { Check, CircleDashed, FastForward, Pause, Play, RotateCcw, SkipForward, Undo2, X, Zap } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fmtTimeIST } from "@/lib/format";
import { ActivityFeed } from "@/components/ActivityFeed";
import { EmptyState, PageHeader, useToast } from "@/components/ui";

type Status = { available: boolean; active?: boolean; paused?: boolean; speed?: 1 | 3; worker_alive?: boolean; controls?: boolean };
type Entry = { n: number; title: string; ok: boolean; skipped: boolean; message: string; seconds: number; at: string };
type Scenario = { status: "idle" | "running" | "paused" | "done" | "failed"; step: number; total: number; speed: 1 | 3; log: Entry[]; steps: string[] };
type Trigger = { id: number; kind: string; requested_at: string; done_at: string | null; result: string | null };

const GROUPS: { name: string; actions: { kind: string; label: string }[] }[] = [
  { name: "Sales & customer orders", actions: [{ kind: "b2b_order", label: "New B2B order" }, { kind: "order", label: "Marketplace order" }, { kind: "confirm_order", label: "Confirm an order" }, { kind: "cancel_order", label: "Cancel a demo order" }] },
  { name: "Procurement", actions: [{ kind: "purchase_request", label: "Create purchase request" }, { kind: "approve_pr", label: "Approve purchase request" }, { kind: "issue_po", label: "Issue purchase order" }, { kind: "receive_materials", label: "Receive materials" }, { kind: "delayed_delivery", label: "Simulate delayed delivery" }] },
  { name: "Production", actions: [{ kind: "start_production", label: "Start production" }, { kind: "production_step", label: "Advance one step" }, { kind: "return_material", label: "Return unused material" }, { kind: "complete_production", label: "Complete production" }, { kind: "rework_start", label: "Start rework of a held batch" }, { kind: "rework_complete", label: "Complete rework" }] },
  { name: "Quality", actions: [{ kind: "record_inspection", label: "Record inspection" }, { kind: "pass_inspection", label: "Pass inspection" }, { kind: "fail_inspection", label: "Fail inspection" }, { kind: "release_held_batch", label: "Release a held batch" }, { kind: "start_recall", label: "Start a batch recall" }, { kind: "resolve_recall", label: "Resolve and close the recall" }, { kind: "expiry_sweep", label: "Run expiry sweep" }] },
  { name: "Accounts", actions: [{ kind: "draft_invoice", label: "Create draft invoice" }, { kind: "post_invoice", label: "Post invoice" }, { kind: "invoice_pdf", label: "Generate invoice PDF" }, { kind: "einvoice", label: "Generate e-invoice (sandbox)" }, { kind: "payment", label: "Record & allocate payment" }, { kind: "big_payment", label: "Large customer payment" }, { kind: "credit_note", label: "Process return & credit note" }] },
  { name: "Dispatch", actions: [{ kind: "dispatch_order", label: "Dispatch an order" }, { kind: "tracking_update", label: "Add tracking update" }, { kind: "deliver", label: "Mark delivered" }, { kind: "delivery_exception", label: "Delivery exception" }] },
  { name: "Marketplace", actions: [{ kind: "market_amazon", label: "Import Amazon order" }, { kind: "market_flipkart", label: "Import Flipkart order" }, { kind: "market_myntra", label: "Import Myntra order" }, { kind: "market_nykaa", label: "Import Nykaa order" },
    { kind: "market_sync", label: "Demo synchronisation" }, { kind: "failed_import", label: "Failed import" }, { kind: "retry_import", label: "Retry failed import" }, { kind: "duplicate_import", label: "Duplicate delivery" }] },
];
const LABEL: Record<string, string> = Object.fromEntries(GROUPS.flatMap((g) => g.actions.map((a) => [a.kind, a.label])));

export default function DemoControlPage() {
  const toast = useToast();
  const [st, setSt] = useState<Status | null>(null);
  const [sc, setSc] = useState<Scenario | null>(null);
  const [trig, setTrig] = useState<Trigger[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api<Status>("/demo/status").then(setSt).catch(() => setSt({ available: false })); }, []);
  const can = !!(st?.available && st.controls);
  const refresh = useCallback(() => {
    api<Scenario>("/demo/scenario").then(setSc).catch(() => {});
    api<{ items: Trigger[] }>("/demo/triggers", { params: { limit: 10 } }).then((d) => setTrig(d.items)).catch(() => {});
  }, []);
  useEffect(() => {
    if (!can) return;
    refresh();
    const t = setInterval(refresh, sc?.status === "running" || trig.some((x) => !x.done_at) ? 1500 : 6000);
    return () => clearInterval(t);
  }, [can, refresh, sc?.status, trig]);

  const post = async (path: string, body: unknown, ok?: string) => {
    setBusy(true);
    try { const r = await api(path, { method: "POST", body }); if (ok) toast("success", ok); refresh(); return r; }
    catch (e) { toast("error", e instanceof ApiError ? e.message : "That did not work"); }
    finally { setBusy(false); }
  };
  const scenario = (action: string, speed?: number) => post("/demo/scenario", { action, speed }, undefined);
  const resetAll = async () => {
    if (!window.confirm("Restore the golden snapshot? Everything created since it was taken is discarded. You stay signed in.")) return;
    await post("/demo/reset", {}, "Restored. Refreshing…");
    setTimeout(() => window.location.reload(), 800);
  };

  if (st && !can) return <div><PageHeader title="Demo Control Centre" /><EmptyState title="Not available here" hint="The Demo Control Centre only exists in the isolated demo environment, and only for the demo administrator." art="search" /></div>;
  if (!st || !sc) return <div><PageHeader title="Demo Control Centre" /><div className="h-40 rounded-2xl bg-inset animate-pulse" aria-busy="true" /></div>;

  const running = sc.status === "running", paused = sc.status === "paused", failed = sc.status === "failed", done = sc.status === "done", idle = sc.status === "idle";
  return (
    <div data-testid="demo-control">
      <PageHeader title="Demo Control Centre" eyebrow="Demo environment"
        description="Every button runs the real application as a demo user: the same screens' validations, ledger postings and permissions. Everything it creates is labelled Demo."
        tag={<span className="tag tag-sim">Demo</span>}
        actions={<button className="btn btn-danger" disabled={busy} onClick={resetAll}><RotateCcw size={14} />Reset demo data</button>} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="flex flex-col gap-6 min-w-0">
          <section className="panel p-5" aria-label="Scripted scenario" data-testid="scenario">
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <h2 className="t-section mr-auto">Order to cash, scripted</h2>
              {(idle || done || failed) && !failed && <button className="btn btn-primary" disabled={busy} onClick={() => (done ? scenario("reset").then(() => scenario("start")) : scenario("start"))} data-testid="scenario-start"><Play size={14} />{done ? "Run again" : "Start scenario"}</button>}
              {running && <button className="btn" disabled={busy} onClick={() => scenario("pause")} data-testid="scenario-pause"><Pause size={14} />Pause</button>}
              {paused && <button className="btn btn-primary" disabled={busy} onClick={() => scenario("resume")} data-testid="scenario-resume"><Play size={14} />Resume</button>}
              {(paused || running) && <button className="btn" disabled={busy} onClick={() => scenario("step")} title="Run the next step now"><SkipForward size={14} />Next step</button>}
              {failed && <button className="btn btn-primary" disabled={busy} onClick={() => scenario("retry")}><Undo2 size={14} />Retry step</button>}
              <div className="seg" role="group" aria-label="Scenario speed">
                {([1, 3] as const).map((s) => <button key={s} type="button" aria-pressed={sc.speed === s} className={sc.speed === s ? "on" : ""} disabled={busy} onClick={() => scenario("speed", s)}><FastForward size={12} className="inline mr-1" />×{s}</button>)}
              </div>
              {!idle && <button className="btn" disabled={busy} onClick={() => scenario("reset")} data-testid="scenario-reset"><RotateCcw size={14} />Reset scenario</button>}
            </div>
            <p className="text-[0.8125rem] text-muted mb-4" aria-live="polite" data-testid="scenario-status">
              {idle && "A new order arrives and is followed to cash through purchasing, production, QC, dispatch, invoicing and payment. Nothing is faked: every step changes the database."}
              {running && `Running: step ${Math.min(sc.step + 1, sc.total)} of ${sc.total}`}{paused && `Paused after step ${sc.step} of ${sc.total}`}
              {done && "Completed: every record below exists in the application."}{failed && "A step could not run. Read the reason below, then retry the step or reset."}
            </p>
            <ol className="flex flex-col gap-1.5">
              {sc.steps.map((title, i) => {
                const e = sc.log.find((x) => x.n === i + 1);
                const current = !e && i === sc.step && (running || paused);
                return (
                  <li key={title} className={`flex gap-3 rounded-xl px-3 py-2 ${e && !e.ok ? "bg-[color-mix(in_srgb,var(--acc-red)_10%,transparent)]" : e ? "" : current ? "bg-surface-2" : "opacity-60"}`} data-testid="scenario-step" data-state={e ? (e.ok ? (e.skipped ? "skipped" : "done") : "failed") : current ? "current" : "todo"}>
                    <span className="mt-0.5 shrink-0" aria-hidden>{e ? (e.ok ? <Check size={15} className="text-[var(--acc-green)]" /> : <X size={15} className="text-[var(--acc-red)]" />) : current ? <CircleDashed size={15} className="animate-spin text-muted" /> : <span className="inline-block w-[15px]" />}</span>
                    <div className="min-w-0">
                      <p className="text-[0.875rem]">{i + 1}. {title}{e?.skipped && <span className="text-muted"> · not needed this time</span>}</p>
                      {e && <p className={`text-[0.75rem] ${e.ok ? "text-muted" : "text-[var(--acc-red)]"}`}>{e.message} <span className="tabular-nums">· {e.seconds}s · {fmtTimeIST(e.at)}</span></p>}
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>

          <section aria-label="Actions" className="grid gap-4 md:grid-cols-2">
            {GROUPS.map((g) => (
              <div key={g.name} className="panel p-4" role="group" aria-label={g.name}>
                <h3 className="t-section mb-2">{g.name}</h3>
                <div className="flex flex-wrap gap-2">
                  {g.actions.map((a) => <button key={g.name + a.kind} className="btn" disabled={busy} data-testid={`act-${a.kind}`} onClick={() => post("/demo/trigger", { kind: a.kind })}><Zap size={13} />{a.label}</button>)}
                </div>
              </div>
            ))}
          </section>
        </div>

        <aside className="flex flex-col gap-6 min-w-0" aria-label="What just happened">
          <section className="panel p-4" aria-label="Recent actions">
            <h3 className="t-section mb-2">Your actions</h3>
            {trig.length === 0 ? <p className="text-[0.8125rem] text-muted">Nothing yet. Press any action.</p> : (
              <ul className="flex flex-col gap-2" data-testid="trigger-results">
                {trig.map((t) => (
                  <li key={t.id} className="text-[0.8125rem]">
                    <span className="font-medium">{LABEL[t.kind] ?? t.kind}</span>
                    <span className="block text-muted break-words">{t.done_at ? (t.result ?? "Done") : "Queued…"}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section aria-label="Live activity"><h3 className="t-section mb-2">Live activity</h3><ActivityFeed limit={25} compact /></section>
        </aside>
      </div>
    </div>
  );
}
