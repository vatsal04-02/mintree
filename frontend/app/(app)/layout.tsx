"use client";
import { Suspense } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/auth";
import { LiveProvider } from "@/lib/live";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading || !me || me.must_change_password) {
    return <div className="h-screen grid place-items-center text-muted text-[0.8125rem]" aria-busy="true">Loading…</div>;
  }
  return <LiveProvider><AppShell><Suspense fallback={null}>{children}</Suspense></AppShell></LiveProvider>;
}
