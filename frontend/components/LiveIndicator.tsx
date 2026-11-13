"use client";
import Link from "next/link";
import { useLiveState } from "@/lib/live";

/** A quiet connection indicator for the top bar: green while the live event stream is connected; amber while screens fall back to refreshing every 20 s. */
export function LiveIndicator() {
  const state = useLiveState();
  const tone = state === "live" ? "bg-[var(--acc-green)]" : state === "polling" ? "bg-[var(--acc-yellow)]" : "bg-[var(--muted)]";
  const label = state === "live" ? "Live" : state === "polling" ? "Reconnecting" : "Connecting";
  const hint = state === "live" ? "Connected: new activity appears as it happens" : state === "polling" ? "Live connection unavailable: refreshing every 20 seconds, reconnecting" : "Connecting to live updates";
  return (
    <Link href="/activity" className="hidden md:inline-flex items-center gap-1.5 h-8 px-2.5 rounded-full text-[0.75rem] text-muted hover:text-ink whitespace-nowrap" title={hint} aria-label={`${label}. ${hint}. Open live activity`} data-testid="live-indicator" data-state={state}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${tone}`} aria-hidden />{label}
    </Link>
  );
}
