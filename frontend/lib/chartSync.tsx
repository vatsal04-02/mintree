"use client";
/** Linked crosshair (phase 3.1): charts on one page share the date under the pointer, so hovering the revenue line also marks the production week it falls in
 *  and the other way round. Wrap the charts in <ChartSyncProvider>; a chart outside any provider simply behaves on its own. */
import { createContext, useContext, useMemo, useState } from "react";

type Sync = { date: string | null; set: (d: string | null) => void };
const Ctx = createContext<Sync | null>(null);

export function ChartSyncProvider({ children }: { children: React.ReactNode }) {
  const [date, set] = useState<string | null>(null);
  const v = useMemo(() => ({ date, set }), [date]);
  return <Ctx.Provider value={v}>{children}</Ctx.Provider>;
}
export const useChartSync = () => useContext(Ctx);
