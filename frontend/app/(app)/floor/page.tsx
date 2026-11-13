"use client";
/** Shop floor: the production screens an operator can use with gloves on. Big touch targets, one batch at a time, the next step always the biggest button.
 *  It calls the same production routes as the desk screens (same rules, permissions, idempotency and audit); nothing here is a shortcut around them. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, Factory, Gauge, PackageMinus, Play, RefreshCw, Square } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLiveRevision } from "@/lib/live";
import { PageHeader, EmptyState, useToast } from "@/components/ui";

type Stage = { seq: number; name: string; status: string };
type Mat = { item_id: number; required: string; issued: string };
type Order = { id: number; number: string; status: string; version: number; planned_qty: string; output_item_id: number; stages: Stage[]; materials: Mat[] };
type Returnable = { item_id: number; item_code: string; name: string; issued: string; returned: string; returnable: string };

const big = "min-h-[4.5rem] rounded-2xl px-5 text-[1.125rem] font-medium flex items-center justify-center gap-3 disabled:opacity-50";
const key = () => ({ "Idempotency-Key": crypto.randomUUID() });

export default function ShopFloorPage() {
  const { can } = useAuth();
  const toast = useToast();
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"work" | "reading" | "return" | "finish">("work");
  const rev = useLiveRevision(["production"]);

  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([api<{ items: Order[] }>("/production/orders", { params: { status: "IN_PROGRESS", page_size: 30 } }), api<{ items: Order[] }>("/production/orders", { params: { status: "RELEASED", page_size: 30 } })]);
      setOrders([...a.items, ...b.items]);
    } catch (e) { toast("error", e instanceof ApiError ? e.message : "Could not load the floor"); setOrders([]); }
  }, [toast]);
  useEffect(() => { load(); }, [load, rev]);

  const order = useMemo(() => orders?.find((o) => o.id === sel) ?? null, [orders, sel]);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); toast("success", ok); await load(); setMode("work"); }
    catch (e) { toast("error", e instanceof ApiError ? e.message : "That did not work"); }
    finally { setBusy(false); }
  };

  if (!can("production.order:read")) return <div><PageHeader title="Shop floor" /><EmptyState title="Not available" hint="Your role does not include production." art="search" /></div>;
  if (!order) return (
    <div data-testid="floor">
      <PageHeader title="Shop floor" description="Pick the batch you are working on." actions={<button className="btn" onClick={load}><RefreshCw size={14} />Refresh</button>} />
      {orders === null && <div className="h-40 rounded-2xl bg-inset animate-pulse" aria-busy="true" />}
      {orders?.length === 0 && <EmptyState title="Nothing on the floor" hint="Released and running batches show here." art="tray" />}
      <ul className="grid gap-3 md:grid-cols-2">
        {orders?.map((o) => {
          const run = o.stages.find((s) => s.status === "IN_PROGRESS"), nxt = o.stages.find((s) => s.status === "PENDING");
          return (
            <li key={o.id}>
              <button className="panel w-full text-left p-5 min-h-[7.5rem] flex flex-col gap-1" onClick={() => { setSel(o.id); setMode("work"); }} data-testid="floor-order">
                <span className="flex items-center justify-between"><span className="t-ref text-[1.125rem]">{o.number}</span><span className="tag">{o.status === "RELEASED" ? "Ready to start" : "Running"}</span></span>
                <span className="text-muted">Planned {Number(o.planned_qty)}</span>
                <span className="text-[1rem]">{run ? `Now: ${run.name}` : nxt ? `Next: ${nxt.name}` : o.status === "IN_PROGRESS" ? "All stages done: record the output" : "Materials not yet issued"}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );

  const running = order.stages.find((s) => s.status === "IN_PROGRESS");
  const next = order.stages.find((s) => s.status === "PENDING");
  const allDone = order.stages.every((s) => s.status === "DONE");
  const canExec = can("production.order:execute");
  return (
    <div data-testid="floor-order-detail">
      <button className="btn mb-4 !h-12" onClick={() => setSel(null)}><ChevronLeft size={18} />All batches</button>
      <PageHeader title={order.number} description={`Planned ${Number(order.planned_qty)} · ${order.status === "RELEASED" ? "ready to start" : "running"}`} />
      <ol className="flex flex-wrap gap-2 mb-6" aria-label="Stages">
        {order.stages.map((s) => (
          <li key={s.seq} className={`rounded-full px-4 h-10 inline-flex items-center gap-2 text-[0.9375rem] border ${s.status === "DONE" ? "border-transparent bg-[var(--brand-tint)]" : s.status === "IN_PROGRESS" ? "border-line-strong bg-surface-2 font-medium" : "border-line text-muted"}`}>
            {s.status === "DONE" && <Check size={14} aria-hidden />}{s.name}
          </li>
        ))}
      </ol>

      {mode === "work" && (
        <div className="grid gap-3 md:grid-cols-2">
          {order.status === "RELEASED" && can("production.order:issue") && <button className={`${big} btn-primary btn md:col-span-2`} disabled={busy} data-testid="floor-issue" onClick={() => act(() => api(`/production/orders/${order.id}/issue`, { method: "POST", headers: key() }), "Materials issued")}><PackageMinus size={22} />Issue materials</button>}
          {order.status === "IN_PROGRESS" && canExec && next && !running && <button className={`${big} btn btn-primary md:col-span-2`} disabled={busy} data-testid="floor-start" onClick={() => act(() => api(`/production/orders/${order.id}/stages/${next.seq}/start`, { method: "POST", body: {} }), `${next.name} started`)}><Play size={22} />Start {next.name}</button>}
          {running && canExec && <button className={`${big} btn btn-primary md:col-span-2`} disabled={busy} data-testid="floor-done" onClick={() => act(() => api(`/production/orders/${order.id}/stages/${running.seq}/complete`, { method: "POST", body: {} }), `${running.name} done`)}><Square size={20} />Finish {running.name}</button>}
          {running && can("quality.inspection:record") && <button className={`${big} btn`} onClick={() => setMode("reading")}><Gauge size={22} />Record a reading</button>}
          {order.status === "IN_PROGRESS" && can("production.order:issue") && <button className={`${big} btn`} onClick={() => setMode("return")}><PackageMinus size={22} />Return unused material</button>}
          {order.status === "IN_PROGRESS" && allDone && can("production.order:complete") && <button className={`${big} btn btn-primary md:col-span-2`} onClick={() => setMode("finish")} data-testid="floor-finish"><Factory size={22} />Record output and finish</button>}
        </div>
      )}
      {mode === "reading" && running && <Reading order={order} stage={running} busy={busy} onBack={() => setMode("work")} onSave={(b) => act(() => api(`/production/orders/${order.id}/stages/${running.seq}/checks`, { method: "POST", body: b }), "Reading saved")} />}
      {mode === "return" && <Return order={order} busy={busy} onBack={() => setMode("work")} onSave={(b) => act(() => api(`/production/orders/${order.id}/return-materials`, { method: "POST", body: b }), "Material returned to stock")} />}
      {mode === "finish" && <Finish order={order} busy={busy} onBack={() => setMode("work")} onSave={(b) => act(() => api(`/production/orders/${order.id}/complete`, { method: "POST", body: b, headers: key() }), "Batch finished: it is now in QC")} />}
    </div>
  );
}

const field = "input !h-14 !text-[1.125rem] !rounded-xl";

function Pad({ title, onBack, children }: { title: string; onBack: () => void; children: React.ReactNode }) {
  return <section className="panel p-5 max-w-xl" aria-label={title}><div className="flex items-center gap-3 mb-4"><button className="btn !h-12" onClick={onBack}><ChevronLeft size={18} />Back</button><h2 className="text-[1.25rem]">{title}</h2></div><div className="flex flex-col gap-4">{children}</div></section>;
}

function Reading({ stage, busy, onBack, onSave }: { order: Order; stage: Stage; busy: boolean; onBack: () => void; onSave: (b: unknown) => void }) {
  const [p, setP] = useState(""), [v, setV] = useState(""), [lo, setLo] = useState(""), [hi, setHi] = useState("");
  return (
    <Pad title={`Reading during ${stage.name}`} onBack={onBack}>
      <label>Parameter<input className={field} value={p} onChange={(e) => setP(e.target.value)} placeholder="e.g. Batch temperature (C)" /></label>
      <label>Reading<input className={field} inputMode="decimal" value={v} onChange={(e) => setV(e.target.value)} /></label>
      <div className="grid grid-cols-2 gap-3"><label>Lower limit<input className={field} inputMode="decimal" value={lo} onChange={(e) => setLo(e.target.value)} /></label><label>Upper limit<input className={field} inputMode="decimal" value={hi} onChange={(e) => setHi(e.target.value)} /></label></div>
      <button className={`${big} btn btn-primary`} disabled={busy || !p || !v} onClick={() => onSave({ parameter: p, value: v, min_value: lo || undefined, max_value: hi || undefined })}>Save reading</button>
    </Pad>
  );
}

function Return({ order, busy, onBack, onSave }: { order: Order; busy: boolean; onBack: () => void; onSave: (b: unknown) => void }) {
  const [rows, setRows] = useState<Returnable[] | null>(null);
  const [q, setQ] = useState<Record<number, string>>({});
  const [why, setWhy] = useState("");
  useEffect(() => { api<{ materials: Returnable[] }>(`/production/orders/${order.id}/returnable`).then((d) => setRows(d.materials)).catch(() => setRows([])); }, [order.id]);
  const lines = Object.entries(q).filter(([, v]) => Number(v) > 0).map(([id, v]) => ({ item_id: Number(id), qty: v }));
  return (
    <Pad title="Return unused material" onBack={onBack}>
      {rows === null && <div className="h-16 bg-inset rounded-xl animate-pulse" />}
      {rows?.map((m) => (
        <label key={m.item_id}>{m.item_code} · {m.name} <span className="text-muted">(up to {Number(m.returnable)})</span>
          <input className={field} inputMode="decimal" value={q[m.item_id] ?? ""} onChange={(e) => setQ({ ...q, [m.item_id]: e.target.value })} placeholder="0" /></label>
      ))}
      <label>Reason<input className={field} value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. line stopped early, drum not opened" /></label>
      <button className={`${big} btn btn-primary`} disabled={busy || !lines.length || why.length < 3} onClick={() => onSave({ lines, reason: why })}>Return to stock</button>
    </Pad>
  );
}

function Finish({ order, busy, onBack, onSave }: { order: Order; busy: boolean; onBack: () => void; onSave: (b: unknown) => void }) {
  const [out, setOut] = useState("");
  const [waste, setWaste] = useState(""), [why, setWhy] = useState("");
  const planned = Number(order.planned_qty), yieldPct = out ? (Number(out) / planned) * 100 : null;
  const low = yieldPct !== null && yieldPct < 95;
  return (
    <Pad title="Record the output" onBack={onBack}>
      <label>Good output<input className={field} inputMode="decimal" value={out} onChange={(e) => setOut(e.target.value)} /></label>
      {yieldPct !== null && <p className={low ? "text-[var(--acc-red)]" : "text-muted"}>Yield {Math.round(yieldPct * 10) / 10}% of {planned}{low ? ": record the wastage and why" : ""}</p>}
      {low && <><label>Wastage quantity<input className={field} inputMode="decimal" value={waste} onChange={(e) => setWaste(e.target.value)} /></label><label>Reason<input className={field} value={why} onChange={(e) => setWhy(e.target.value)} /></label></>}
      <button className={`${big} btn btn-primary`} disabled={busy || !out || (low && (!Number(waste) || why.length < 3))}
        onClick={() => onSave({ expected_version: order.version, actual_qty: out, wastage: low ? [{ category: "SPILLAGE", qty: waste, reason: why, department: "Production" }] : [] })}>Finish batch</button>
    </Pad>
  );
}
