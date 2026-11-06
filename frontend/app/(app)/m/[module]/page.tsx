"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { MODULE_BY_ID } from "@/lib/modules";
import { EmptyState, PageHeader } from "@/components/ui";
import { ReportView } from "@/components/ModuleKit";
import { ManagementDashboard } from "@/components/ManagementDashboard";

export default function ModulePage({ params }: { params: Promise<{ module: string }> }) {
  const { module: id } = use(params);
  const mod = MODULE_BY_ID[id];
  const { can } = useAuth();
  const [dash, setDash] = useState<Record<string, unknown> | null>(null);
  const allowed = mod?.screens.filter((s) => can(s.perm)) ?? [];
  useEffect(() => {
    if (mod?.dashboard && can(mod.dashboard.perm)) api<Record<string, unknown>>(mod.dashboard.api).then(setDash).catch(() => setDash(null));
  }, [mod, can]);
  if (!mod) notFound();
  if (allowed.length === 0) return <EmptyState title="You don't have access to this area" hint="Ask an administrator if you need it." />;
  const isManagement = mod.id === "analytics" && can("analytics.dashboard:read");
  const kpis = dash ? Object.fromEntries(Object.entries(dash).filter(([, v]) => v === null || typeof v !== "object")) : null;
  return (
    <>
      {isManagement ? <><ManagementDashboard /><h2 className="text-[14px] font-semibold mt-8 mb-3">Full reports</h2></> : <PageHeader title={mod.label} description={mod.description} />}
      {kpis && Object.keys(kpis).length > 0 && <div className="mb-6"><ReportView data={kpis} /></div>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {allowed.map((s) => (
          <Link key={s.id} href={`/m/${mod.id}/${s.id}`} className="panel panel-link p-4 group">
            <div className="flex items-center justify-between"><span className="font-medium text-[14px]">{s.label}</span><ArrowRight size={14} className="text-muted group-hover:text-primary" /></div>
            <p className="text-muted text-[12.5px] mt-1">{s.description}</p>
          </Link>
        ))}
      </div>
    </>
  );
}
