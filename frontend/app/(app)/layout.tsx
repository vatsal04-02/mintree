"use client";
import { Suspense } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/auth";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading || !me || me.must_change_password) {
    return <div className="h-screen grid place-items-center text-muted text-[13px]" aria-busy="true">Loading…</div>;
  }
  return <AppShell><Suspense fallback={null}>{children}</Suspense></AppShell>;
}
