import { LayoutDashboard, Package, Tags, Ruler, Truck, Users2, Tag, Warehouse, MapPin, FileText, UserCog, ShieldCheck, Building2, ScrollText, Inbox, Bell, type LucideIcon } from "lucide-react";
import { MODULES, MODULE_BY_ID, type Module } from "./modules";

/** Which sidebar group each module sits in; the admin tools module is appended to Administration below. */
const MODULE_GROUPS: [string, string[]][] = [
  ["Operations", ["procurement", "inventory", "production", "rnd", "quality", "dispatch", "maintenance"]],
  ["CRM", ["sales"]],
  ["Finance", ["accounts", "analytics"]],
  ["Integrations", ["ecommerce", "branding"]],
];
const moduleItem = (m: Module): NavItem => ({
  href: `/m/${m.id}`, label: m.label, icon: m.icon, anyPerm: m.screens.map((s) => s.perm), keywords: `${m.description} ${m.screens.map((s) => s.label).join(" ")}`,
  children: [...(m.dashboard ? [{ href: `/m/${m.id}`, label: "Dashboard", perm: m.dashboard.perm }] : []), ...m.screens.map((s) => ({ href: `/m/${m.id}/${s.id}`, label: s.label, perm: s.perm }))],
});

export type NavChild = { href: string; label: string; perm?: string };
export type NavItem = { href: string; label: string; icon: LucideIcon; plain?: boolean;   // plain: a simple link in a long list; shows no icon unless the sidebar is collapsed to its icon rail
  perm?: string; anyPerm?: string[]; keywords?: string; hidden?: boolean; children?: NavChild[] };
export type NavGroup = { label: string; items: NavItem[] };

export const NAV: NavGroup[] = [
  { label: "Workspace", items: [
    { href: "/", label: "Overview", icon: LayoutDashboard },
    { href: "/approvals", label: "Approvals", icon: Inbox, keywords: "pending inbox approve" },
    { href: "/notifications", label: "Notifications", icon: Bell, keywords: "alerts unread" },
  ] },
  ...MODULE_GROUPS.map(([label, ids]) => ({
    label,
    items: ids.flatMap((id) => (MODULE_BY_ID[id] ? [moduleItem(MODULE_BY_ID[id])] : [])),
  })),
  {
    label: "Master data",
    items: [
      { href: "/masters/items", label: "Items", icon: Package, perm: "masters.item:read", keywords: "raw material packaging rm pm" },
      { href: "/masters/categories", label: "Categories", icon: Tags, perm: "masters.category:read" },
      { href: "/masters/units-of-measure", label: "Units of measure", icon: Ruler, perm: "masters.uom:read", keywords: "uom kg litre" },
      { href: "/masters/suppliers", label: "Suppliers", icon: Truck, perm: "masters.supplier:read", keywords: "vendor gstin" },
      { href: "/masters/customers", label: "Customers", icon: Users2, perm: "masters.customer:read", keywords: "distributor brand client" },
      { href: "/masters/brands", label: "Brands", icon: Tag, perm: "masters.brand:read" },
      { href: "/masters/warehouses", label: "Warehouses", icon: Warehouse, perm: "masters.warehouse:read" },
      { href: "/masters/locations", label: "Storage locations", icon: MapPin, perm: "masters.location:read", keywords: "rack bin" },
    ],
  },
  { label: "Records", items: [{ href: "/documents", label: "Document centre", icon: FileText, perm: "documents.document:read", keywords: "coa invoice certificate files" }] },
  {
    label: "Administration",
    items: [
      { href: "/admin/users", label: "Users", icon: UserCog, perm: "admin.user:read" },
      { href: "/admin/roles", label: "Roles & permissions", icon: ShieldCheck, perm: "admin.role:read", keywords: "access matrix" },
      { href: "/admin/departments", label: "Departments", icon: Building2, perm: "admin.department:read" },
      { href: "/admin/audit", label: "Audit log", icon: ScrollText, perm: "admin.audit:read", keywords: "history changes" },
      ...(MODULE_BY_ID.admin ? [moduleItem(MODULE_BY_ID.admin)] : []),
    ],
  },
];

/** Primary navigation keeps its icons; the long flat lists do not need one per row. */
for (const g of NAV) if (["Master data", "Records", "Administration"].includes(g.label)) for (const i of g.items) if (!i.children) i.plain = true;

/** Individual module screens are searchable from the command palette but not listed in the sidebar (each module has a workspace page). */
const SCREEN_ITEMS: NavItem[] = MODULES.flatMap((m) => m.screens.map((s) => ({ href: `/m/${m.id}/${s.id}`, label: `${m.label}: ${s.label}`, icon: m.icon, perm: s.perm, keywords: s.description, hidden: true })));

export const ALL_NAV_ITEMS = [...NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label }))), ...SCREEN_ITEMS.map((i) => ({ ...i, group: "Screens" }))];
export const visible = (i: NavItem, can: (p: string) => boolean) => (!i.perm || can(i.perm)) && (!i.anyPerm || i.anyPerm.some(can));
