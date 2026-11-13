"use client";
import { use, useEffect, useState } from "react";
import { useLiveRevision } from "@/lib/live";

/** Module id -> the scope names the backend sends for it (core/activity.py). */
const EVENT_SCOPES: Record<string, string[]> = { procurement: ["purchase"], dispatch: ["orders", "accounts"], ecommerce: ["orders"], sales: ["sales", "orders"], accounts: ["accounts"], inventory: ["inventory"], production: ["production"], quality: ["quality"] };
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { MODULE_BY_ID } from "@/lib/modules";
import { screenHref } from "@/lib/summary";
import { EmptyState, PageHeader } from "@/components/ui";
import { ModuleDashboard } from "@/components/ModuleDashboard";
import { ManagementDashboard } from "@/components/ManagementDashboard";
import { CommercialInsights } from "@/components/insights";
import { GrowthSection } from "@/components/growth";
import { ControlChart } from "@/components/quality";
import { OmniScreen, ProductionScreen, PurchaseScreen } from "@/components/screens";
import { InventoryScreen } from "@/components/inventory";
import { FinanceScreen } from "@/components/finance";

/** Modules with a purpose-built screen (docs/DESIGN_SYSTEM.md screens 2-4) and the permission each needs; the rest use the generic dashboard. */
const SCREENS: Record<string, { perm: string; Screen: typeof ProductionScreen }> = {
  production: { perm: "production.order:read", Screen: ProductionScreen },
  procurement: { perm: "procurement.po:read", Screen: PurchaseScreen },
  inventory: { perm: "inventory.stock:read", Screen: InventoryScreen },
  accounts: { perm: "accounts.report:read", Screen: FinanceScreen },
  ecommerce: { perm: "ecommerce.order:read", Screen: OmniScreen },
};

export default function ModulePage({ params }: { params: Promise<{ module: string }> }) {
  const { module: id } = use(params);
  const mod = MODULE_BY_ID[id];
  const { can } = useAuth();
  const [dash, setDash] = useState<Record<string, unknown> | null>(null);
  const allowed = mod?.screens.filter((s) => can(s.perm)) ?? [];
  const custom = mod && SCREENS[mod.id] && can(SCREENS[mod.id].perm) ? SCREENS[mod.id] : null;
  const liveRev = useLiveRevision(["overview", mod?.id ?? "", ...(EVENT_SCOPES[mod?.id ?? ""] ?? [])]);        // the backend names event scopes by business area, which is not always the module id
  useEffect(() => {
    if (!custom && mod?.dashboard && can(mod.dashboard.perm)) api<Record<string, unknown>>(mod.dashboard.api).then(setDash).catch(() => setDash(null));
  }, [mod, can, custom, liveRev]);
  if (!mod) notFound();
  if (allowed.length === 0) return <EmptyState title="You don't have access to this area" hint="Ask an administrator if you need it." />;
  const isManagement = mod.id === "analytics" && can("analytics.dashboard:read");
  const to = (m: string, s: string) => screenHref(can, m, s);
  /* a bar or segment of a status breakdown opens the list it counts, already filtered to that status (the address is shareable) */
  const drill = (name: string, key: string) => {
    const stem = name.split("_by_")[0].replace(/_/g, "-");
    const scr = mod.screens.find((x) => x.list?.status && can(x.perm) && (x.id === stem || x.id === `${stem}s` || `${x.id}s` === stem));
    const opt = scr?.list?.status?.options.find((o) => o.toLowerCase() === key.toLowerCase().replace(/\s+/g, "_"));
    const href = scr && opt ? to(mod.id, scr.id) : undefined;
    return href ? `${href}?status=${opt}` : undefined;
  };
  return (
    <>
      {custom ? <custom.Screen to={to} />
        : isManagement ? <><ManagementDashboard /><div className="mt-6"><GrowthSection /><CommercialInsights /></div><h2 className="t-section mt-8 mb-3">Full reports</h2></>
        : <PageHeader title={mod.label} description={mod.description} />}
      {!custom && dash && Object.keys(dash).length > 0 && <ModuleDashboard data={dash} drill={drill} />}
      {mod.id === "quality" && can("quality.inspection:read") && <ControlChart />}
      {custom && <h2 className="label-caps mt-8 mb-3">All {mod.label.toLowerCase()} screens</h2>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {allowed.map((s) => (
          <Link key={s.id} href={`/m/${mod.id}/${s.id}`} className="panel panel-link p-4 group">
            <div className="flex items-center justify-between"><span className="font-semibold text-[0.875rem]">{s.label}</span><ArrowRight size={14} className="text-muted group-hover:text-primary" /></div>
            <p className="text-muted text-[0.75rem] mt-1">{s.description}</p>
          </Link>
        ))}
      </div>
    </>
  );
}
