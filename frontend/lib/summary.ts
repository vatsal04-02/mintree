"use client";
/** GET /analytics/management-summary: the single source for the Overview and Management dashboards. Every figure either page shows is read from here. */
import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { MODULE_BY_ID, toneFor } from "./modules";
import { fmtNum, titleCase } from "./format";
import type { Tone } from "@/components/dash";

export type Count = { status: string; n: number; value?: string | number };
export type Summary = {
  as_of: string;
  sales: { revenue_ex_tax: string | number; credit_notes_ex_tax: string | number; net_revenue_ex_tax: string | number; invoices: number; receivable_outstanding: string | number; cogs: string | number };
  finance: { payable_outstanding: string | number; cash_and_bank: string | number; inventory_value: string | number };
  orders_by_status: Count[]; production_by_status: Count[]; purchase_orders_by_status: Count[]; shipments_by_status: Count[];
  equipment_by_status: Count[]; marketplace_orders_by_status: Count[];
  open_complaints: number;
  quality: { open_inspections: number; batches_on_hold: number; batches_quarantined: number; open_deviations: number; open_capa: number };
  low_stock: { item_code: string; name: string; available: string | number; reorder_level: string | number }[];
  production: { completed_30d: number; planned_qty_30d: string; actual_qty_30d: string; active_orders: number; delayed_work_orders: number; wastage_qty_30d: string; average_yield_pct: string | number | null };
  inventory: { value_by_kind: { kind: string; value: string | number; qty: string | number }[]; total_value: string | number; below_reorder_level: number; expiring_within_90_days: number; expired_with_stock: number; quarantined_or_held_qty: string };
  procurement: { purchase_requests_awaiting_approval: number; purchase_orders_awaiting_approval: number; open_purchase_orders: number; late_deliveries: number; purchase_value_30d: string; supplier_on_time_pct: string | null };
  quality_extended: { rejected_inspections_30d: number; rejected_batches: number };
  dispatch: { delayed_shipments: number; delivered: number; failed_or_rto: number; returns_open: number };
  finance_extended: { open_sales_invoices: number; open_supplier_invoices: number; gross_margin_pct_30d: number | null; revenue_30d: string };
};

/** undefined while loading, null when unavailable (no permission or the request failed; `error` says which). */
export function useSummary(enabled: boolean) {
  const [summary, setSummary] = useState<Summary | null | undefined>(enabled ? undefined : null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) { setSummary(null); return; }
    let live = true;
    api<Summary>("/analytics/management-summary")
      .then((s) => live && setSummary(s))
      .catch((e) => { if (live) { setSummary(null); setError(e instanceof ApiError ? e.message : "The summary could not be loaded."); } });
    return () => { live = false; };
  }, [enabled]);
  return { summary, error };
}

/** Link to a module screen only when the user may open it (the backend enforces the same permission). */
export const screenHref = (can: (p: string) => boolean, moduleId: string, screenId: string) => {
  const sc = MODULE_BY_ID[moduleId]?.screens.find((x) => x.id === screenId);
  return sc && can(sc.perm) ? `/m/${moduleId}/${screenId}` : undefined;
};

export const SHIPMENT_FINAL = ["DELIVERED", "RTO", "FAILED_DELIVERY", "CANCELLED"];
export const KIND: Record<string, string> = { RM: "Raw materials", PM: "Packaging", FG: "Finished goods", BULK: "Bulk", SFG: "Semi-finished goods" };
export const num = (v: unknown) => (v === null || v === undefined || v === "" ? 0 : Number(v));
export const plural = (n: number, one: string, many: string) => `${fmtNum(n)} ${n === 1 ? one : many}`;
export const sumStatus = (rows: Count[] | undefined, pick: (s: string) => boolean) => (rows ?? []).filter((r) => pick(r.status)).reduce((a, r) => a + r.n, 0);
export const statusItems = (rows: Count[] | undefined) => (rows ?? []).map((r) => ({ label: titleCase(r.status), value: r.n, tone: toneFor(r.status) as Tone }));
