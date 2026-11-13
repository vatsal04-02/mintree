"use client";
import { useAuth } from "@/lib/auth";
import { EmptyState } from "@/components/ui";
import { CustomerDirectory } from "@/components/customers";

export default function CustomersPage() {
  const { can } = useAuth();
  if (!can("masters.customer:read")) return <EmptyState title="You don't have access to this area" hint="Ask an administrator if you need it." />;
  return <CustomerDirectory />;
}
