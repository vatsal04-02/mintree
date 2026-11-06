"use client";
import { Suspense, use } from "react";
import Link from "next/link";
import { notFound, useSearchParams } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { MODULE_BY_ID } from "@/lib/modules";
import { EmptyState, PageHeader } from "@/components/ui";
import { CreateOnlyScreen, ListScreen, ReportScreen } from "@/components/ModuleKit";

function Screen({ moduleId, screenId }: { moduleId: string; screenId: string }) {
  const mod = MODULE_BY_ID[moduleId];
  const screen = mod?.screens.find((s) => s.id === screenId);
  const { can } = useAuth();
  const sp = useSearchParams();
  if (!mod || !screen) notFound();
  if (!can(screen.perm)) return <EmptyState title="You don't have access to this area" hint="Ask an administrator if you need it." />;
  const initial = Object.fromEntries(sp.entries());
  return (
    <>
      <Link href={`/m/${mod.id}`} className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-fg mb-2"><ChevronLeft size={14} />{mod.label}</Link>
      <PageHeader title={screen.label} description={screen.description} />
      {screen.list ? <ListScreen key={screen.id} screen={screen} /> : screen.report ? <ReportScreen key={`${screen.id}-${sp.toString()}`} screen={screen} initial={initial} /> : screen.create ? <CreateOnlyScreen screen={screen} /> : null}
    </>
  );
}

export default function ScreenPage({ params }: { params: Promise<{ module: string; screen: string }> }) {
  const { module: m, screen } = use(params);
  return <Suspense fallback={<div className="skeleton h-40" />}><Screen moduleId={m} screenId={screen} /></Suspense>;
}
