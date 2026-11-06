"use client";
import { ResourcePage } from "@/components/ResourcePage";
import { EmptyState } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { RESOURCES } from "@/lib/resources";

export default function DepartmentsPage() {
  const { can } = useAuth();
  if (!can("admin.department:read")) return <EmptyState title="You don't have access to this area" />;
  return <ResourcePage def={RESOURCES.departments} />;
}
