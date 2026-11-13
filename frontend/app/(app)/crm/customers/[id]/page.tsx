"use client";
import { use } from "react";
import { useAuth } from "@/lib/auth";
import { EmptyState } from "@/components/ui";
import { CustomerDetail } from "@/components/customers";

export default function CustomerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { can } = useAuth();
  if (!can("masters.customer:read")) return <EmptyState title="You don't have access to this area" hint="Ask an administrator if you need it." />;
  return <CustomerDetail id={Number(id)} />;
}
