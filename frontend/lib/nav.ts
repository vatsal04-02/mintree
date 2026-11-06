import { LayoutDashboard, Package, Tags, Ruler, Truck, Users2, Tag, Warehouse, MapPin, FileText, UserCog, ShieldCheck, Building2, ScrollText, type LucideIcon } from "lucide-react";

export type NavItem = { href: string; label: string; icon: LucideIcon; perm?: string; keywords?: string };
export type NavGroup = { label: string; items: NavItem[] };

export const NAV: NavGroup[] = [
  { label: "Workspace", items: [{ href: "/", label: "Overview", icon: LayoutDashboard }] },
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
    ],
  },
];

export const ALL_NAV_ITEMS = NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })));
