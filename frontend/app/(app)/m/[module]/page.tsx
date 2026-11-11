"use client";
import { use, useEffect, useState } from "react";
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
import { OmniScreen, ProductionScreen, PurchaseScreen } from "@/components/screens";

/** Modules with a purpose-built screen (docs/DESIGN_SYSTEM.md screens 2-4) and the permission each needs; the rest use the generic dashboard. */
const SCREENS: Record<string, { perm: string; Screen: typeof ProductionScreen }> = {
  production: { perm: "production.order:read", Screen: ProductionScreen },
  procurement: { perm: "procurement.po:read", Screen: PurchaseScreen },
  ecommerce: { perm: "ecommerce.order:read", Screen: OmniScreen },
};

export default function ModulePage({ params }: { params: Promise<{ module: string }> }) {
  const { module: id } = use(params);
  const mod = MODULE_BY_ID[id];
  const { can } = useAuth();
  const [dash, setDash] = useState<Record<string, unknown> | null>(null);
  const allowed = mod?.screens.filter((s) => can(s.perm)) ?? [];
  const custom = mod && SCREENS[mod.id] && can(SCREENS[mod.id].perm) ? SCREENS[mod.id] : null;
  useEffect(() => {
    if (!custom && mod?.dashboard && can(mod.dashboard.perm)) api<Record<string, unknown>>(mod.dashboard.api).then(setDash).catch(() => setDash(null));
  }, [mod, can, custom]);
  if (!mod) notFound();
  if (allowed.length === 0) return <EmptyState title="You don't have access to this area" hint="Ask an administrator if you need it." />;
  const isManagement = mod.id === "analytics" && can("analytics.dashboard:read");
  const to = (m: string, s: string) => screenHref(can, m, s);
  return (
    <>
      {custom ? <custom.Screen to={to} />
        : isManagement ? <><ManagementDashboard /><h2 className="t-section mt-8 mb-3">Full reports</h2></>
        : <PageHeader eyebrow={`Factory Plant 01 / ${mod.label}`} title={mod.label} description={mod.description} tag={dash ? <span className="tag tag-dot">Live data</span> : undefined} />}
      {!custom && dash && Object.keys(dash).length > 0 && <ModuleDashboard data={dash} />}
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
