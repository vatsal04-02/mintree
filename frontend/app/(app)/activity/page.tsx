"use client";
import { ActivityFeed } from "@/components/ActivityFeed";
import { PageHeader } from "@/components/ui";
import { useLiveState } from "@/lib/live";

export default function ActivityPage() {
  const state = useLiveState();
  return (
    <div>
      <PageHeader title="Live activity" description="Every saved business action, as it happens: orders, purchasing, production, quality, dispatch, invoices and payments. Only what you are allowed to see is shown."
        tag={<span className="text-[0.75rem] text-muted" data-testid="activity-connection">{state === "live" ? "Live" : state === "polling" ? "Reconnecting, refreshing every 20 s" : "Connecting"}</span>} />
      <ActivityFeed limit={60} />
    </div>
  );
}
