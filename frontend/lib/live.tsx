"use client";
/** Real-time events from GET /api/events (Server-Sent Events), with auto-reconnect and a polling fallback.
 *
 *  Typed events: activity (a committed business event, permission-filtered by the server), order.created, stock.changed, batch.stage, payment.received, quality.alert, notification.new, kpi.invalidate, telemetry.
 *  Screens never reload: they call `useLiveRevision(scopes)` and put the number in their fetch effect's dependency list, so only the queries
 *  that care about the event run again (and the 10 s shared read cache is cleared first so the new figure is really fetched).
 *  `telemetry` is SIMULATED instrument data: it is shown labelled and is never stored or used in a calculation. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { clearShared } from "./api";

export type LiveType = "activity" | "order.created" | "stock.changed" | "batch.stage" | "payment.received" | "quality.alert" | "notification.new" | "kpi.invalidate" | "telemetry" | "hello";
export type LiveEvent = { type: LiveType; data: Record<string, unknown> };
export type Telemetry = { simulated: true; batches: { order_id: number; number: string; simulated: true; readings: { temperature_c: number; weigh_scale_kg: number; line_speed_ppm: number } }[] };
type State = "connecting" | "live" | "polling";

type Listener = (e: LiveEvent) => void;
const Ctx = createContext<{ subscribe: (l: Listener) => () => void; state: State; telemetry: Telemetry | null }>({ subscribe: () => () => {}, state: "polling", telemetry: null });

const TYPES: LiveType[] = ["activity", "order.created", "stock.changed", "batch.stage", "payment.received", "quality.alert", "notification.new", "kpi.invalidate", "telemetry", "hello"];
const POLL_MS = 20_000;
const REFRESH_DELAY_MS = 3300;               // > the server cache floor, so a refresh triggered by an event shows the new figure
const MIN_GAP_MS = 1500;                     // the UI never re-fetches more often than this per scope, however fast events arrive

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const [state, setState] = useState<State>("connecting");
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const subscribe = useCallback((l: Listener) => { listeners.current.add(l); return () => { listeners.current.delete(l); }; }, []);
  const dispatch = useCallback((e: LiveEvent) => { listeners.current.forEach((l) => l(e)); }, []);

  useEffect(() => {
    if (typeof EventSource === "undefined") { setState("polling"); return; }
    let es: EventSource | null = null, poll: ReturnType<typeof setInterval> | null = null, fails = 0, closed = false;
    const startPolling = () => {
      if (poll) return;
      setState("polling");
      poll = setInterval(() => { if (document.visibilityState === "visible") dispatch({ type: "kpi.invalidate", data: { scope: ["all"], polled: true } }); }, POLL_MS);
    };
    const stopPolling = () => { if (poll) { clearInterval(poll); poll = null; } };
    const open = () => {
      if (closed) return;
      es = new EventSource("/api/events");
      es.onopen = () => { fails = 0; stopPolling(); setState("live"); };
      es.onerror = () => {
        fails += 1;                                  // EventSource reconnects by itself (server sends retry: 3000); after 3 failures also poll so figures stay fresh
        if (fails >= 3) startPolling();
        if (es && es.readyState === EventSource.CLOSED) { es.close(); setTimeout(open, 5000); }
      };
      for (const t of TYPES) es.addEventListener(t, (m) => {
        let data: Record<string, unknown> = {};
        try { data = JSON.parse((m as MessageEvent).data); } catch { /* ignore a malformed frame */ }
        if (t === "telemetry") setTelemetry(data as unknown as Telemetry);
        dispatch({ type: t, data });
      });
    };
    open();
    return () => { closed = true; es?.close(); stopPolling(); };
  }, [dispatch]);

  const value = useMemo(() => ({ subscribe, state, telemetry }), [subscribe, state, telemetry]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useLiveState = () => useContext(Ctx).state;
export const useLiveTelemetry = () => useContext(Ctx).telemetry;

/** Run `fn` for events of the given types. */
export function useLiveEvent(types: LiveType[], fn: (e: LiveEvent) => void) {
  const { subscribe } = useContext(Ctx);
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; });
  const key = types.join("|");
  useEffect(() => subscribe((e) => { if (types.includes(e.type)) ref.current(e); }), [subscribe, key]);   // eslint-disable-line react-hooks/exhaustive-deps
}

/** A number that goes up when something this screen shows may have changed. Put it in the dependency list of the fetch effect.
 *  `scopes` are the names the backend sends in kpi.invalidate ("overview", "orders", "accounts", "inventory", "purchase", "production", "quality"). */
export function useLiveRevision(scopes: string[] = []): number {
  const [rev, setRev] = useState(0);
  const last = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const key = scopes.join("|");
  useLiveEvent(["kpi.invalidate"], (e) => {
    const sc = (e.data.scope as string[] | undefined) ?? ["all"];
    if (scopes.length && !sc.includes("all") && !sc.some((s) => scopes.includes(s))) return;
    if (timer.current) return;                                           // one pending refresh already covers this event
    // Wait just past the server's dashboard cache floor (3 s) so the re-read sees the write that caused the event, and never refresh more often than MIN_GAP_MS.
    const wait = Math.max(REFRESH_DELAY_MS, MIN_GAP_MS - (Date.now() - last.current));
    timer.current = setTimeout(() => { timer.current = null; last.current = Date.now(); clearShared(); setRev((r) => r + 1); }, wait);
  });
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, [key]);
  return rev;
}
