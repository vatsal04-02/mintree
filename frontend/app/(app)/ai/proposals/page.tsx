"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { fmtDateTime, titleCase } from "@/lib/format";
import { Badge, EmptyState, PageHeader, useToast } from "@/components/ui";

type Proposal = { id: number; kind: string; title: string; rationale: string; status: string; result: { entity: string; id: number; number?: string } | null; created_at: string };
const WHERE: Record<string, string> = { purchase_request: "/m/procurement/requests", followup: "/m/sales/followups" };

export default function ProposalsPage() {
  const toast = useToast();
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState<number | "gen" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => { api<Page>("/ai/proposals").then((d) => setItems(d.items)).catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load")); }, []);
  useEffect(() => { api<{ enabled: boolean }>("/ai/status").then((s) => setEnabled(s.enabled)); load(); }, [load]);
  type Page = { items: Proposal[] };

  const run = async (id: number | "gen", path: string, ok: string) => {
    setBusy(id); setError(null);
    try { await api(path, { method: "POST" }); toast("success", ok); load(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Failed"); load(); }
    finally { setBusy(null); }
  };

  return (
    <>
      <PageHeader title="Suggestions" description="Rule-based suggestions with the reason shown. Nothing happens until you accept; accepting creates an ordinary draft or task in your name, which still follows the normal approvals."
        actions={<button className="btn btn-primary" disabled={!enabled || busy === "gen"} onClick={() => run("gen", "/ai/proposals/generate", "Suggestions refreshed")} data-testid="ai-generate">Find suggestions</button>} />
      {!enabled && <div className="panel p-3 mb-4 text-[0.8125rem] text-muted">The AI features are switched off, so suggestions cannot be refreshed or accepted.</div>}
      {error && <div className="panel p-3 mb-4 text-danger text-[0.8125rem]" role="alert">{error}</div>}
      {items === null ? <div className="skeleton h-32" /> : items.length === 0 ? <div className="panel"><EmptyState title="No open suggestions" hint="Choose Find suggestions to check stock levels and overdue invoices." /></div> : (
        <ul className="space-y-3" data-testid="ai-proposals">
          {items.map((p) => (
            <li key={p.id} className="panel p-4 flex flex-wrap items-start gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1"><Badge tone="info">{titleCase(p.kind)}</Badge><span className="text-[0.75rem] text-muted">{fmtDateTime(p.created_at)}</span></div>
                <div className="font-medium text-[0.8125rem]">{p.title}</div>
                <p className="text-[0.75rem] text-muted mt-1 max-w-3xl">{p.rationale}</p>
              </div>
              <div className="flex gap-2">
                <button className="btn" disabled={!enabled || busy === p.id} onClick={() => run(p.id, `/ai/proposals/${p.id}/reject`, "Suggestion rejected")}>Reject</button>
                <button className="btn btn-primary" disabled={!enabled || busy === p.id} onClick={() => run(p.id, `/ai/proposals/${p.id}/accept`, "Created in your name")} data-testid={`ai-accept-${p.id}`}>Accept</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[0.75rem] text-muted mt-4">Looking for something you accepted? Purchase requests are under <Link className="underline" href={WHERE.purchase_request}>Purchase requests</Link>; follow-ups under <Link className="underline" href={WHERE.followup}>Follow-ups</Link>.</p>
    </>
  );
}
