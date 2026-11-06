"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { fmtDateTime, fmtMoney, titleCase } from "@/lib/format";
import { Badge, EmptyState, PageHeader } from "@/components/ui";

type Item = { type: string; id: number; number: string; amount: string | null; since: string; summary: string };
const WHERE: Record<string, string> = {
  purchase_request: "/m/procurement/requests", purchase_order: "/m/procurement/orders", rfq: "/m/procurement/rfqs", formulation_version: "/m/rnd/formulations", bom: "/m/production/boms",
  inspection: "/m/quality/inspections", deviation: "/m/quality/deviations", content: "/m/branding/content", maintenance_work_order: "/m/maintenance/work-orders",
};

export default function ApprovalsPage() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<{ items: Item[] }>("/approvals/pending").then((d) => setItems(d.items)).catch((e) => setError(e instanceof ApiError ? e.message : "Failed to load")); }, []);
  return (
    <>
      <PageHeader title="Approvals" description="Everything waiting for your decision, oldest first. Authority limits, segregation of duties and delegation are enforced when you act." />
      {error ? <div className="panel p-4 text-danger text-[13px]" role="alert">{error}</div> : items === null ? <div className="skeleton h-32" /> : items.length === 0 ? <div className="panel"><EmptyState title="Nothing waiting for you" hint="New items appear here as colleagues submit work." /></div> : (
        <div className="panel overflow-hidden"><table className="dt"><thead><tr><th>Type</th><th>Reference</th><th>What</th><th className="num">Amount</th><th>Waiting since</th><th /></tr></thead>
          <tbody>{items.map((i) => (
            <tr key={`${i.type}-${i.id}`}><td><Badge tone="warning">{titleCase(i.type)}</Badge></td><td className="font-mono text-[12px]">{i.number}</td><td>{i.summary}</td><td className="num">{i.amount ? fmtMoney(i.amount) : "—"}</td><td>{fmtDateTime(i.since)}</td>
              <td className="text-right">{WHERE[i.type] && <Link className="btn btn-sm" href={WHERE[i.type]}>Open</Link>}</td></tr>
          ))}</tbody></table></div>
      )}
    </>
  );
}
