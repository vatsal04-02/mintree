"use client";
import { use } from "react";
import { notFound } from "next/navigation";
import { ResourcePage } from "@/components/ResourcePage";
import { RESOURCES } from "@/lib/resources";
import { useAuth } from "@/lib/auth";
import { EmptyState } from "@/components/ui";

export default function MasterPage({ params }: { params: Promise<{ resource: string }> }) {
  const { resource } = use(params);
  const def = RESOURCES[resource];
  const { can } = useAuth();
  if (!def || resource === "departments") notFound();
  if (!can(`${def.perm}:read`)) return <EmptyState title="You don't have access to this area" hint="Ask an administrator if you need it." />;
  return <ResourcePage key={def.key} def={def} />;
}
