/** The registry of quick actions: what the dock, the command palette and the mobile "More" sheet can offer. An action is shown only to people who hold its
 *  permission. `run` actions are carried out by the shell through a window event, so any component can trigger them. */
import { Boxes, ClipboardCheck, Factory, FilePlus2, Inbox, Package, ReceiptText, ScanLine, ShoppingCart, UserPlus, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type QuickAction = { id: string; label: string; icon: LucideIcon; perm?: string; href?: string; page?: boolean;   // page: a plain destination, pinnable to the dock but listed under Pages in the palette
   run?: "scan" | "palette" | "shortcuts"; shortcut?: string; keywords?: string };

export const ACTIONS: QuickAction[] = [
  { id: "scan", label: "Scan a barcode", icon: ScanLine, run: "scan", shortcut: "Alt S", keywords: "barcode qr label camera" },
  { id: "approvals", label: "Approvals", icon: Inbox, href: "/approvals", shortcut: "G A", keywords: "pending inbox approve decide" },
  { id: "new-po", label: "New purchase order", icon: ShoppingCart, href: "/m/procurement/orders?create=1", perm: "procurement.po:create", keywords: "buy order supplier" },
  { id: "new-pr", label: "New purchase request", icon: FilePlus2, href: "/m/procurement/requests?create=1", perm: "procurement.pr:create", keywords: "material request" },
  { id: "stocktake", label: "Start stocktake", icon: ClipboardCheck, href: "/m/inventory/stocktake?create=1", perm: "inventory.stocktake:count", keywords: "count stock warehouse" },
  { id: "new-prod", label: "New production order", icon: Factory, href: "/m/production/orders?create=1", perm: "production.order:create", keywords: "batch make manufacture" },
  { id: "new-order", label: "New sales order", icon: Package, href: "/m/dispatch/orders?create=1", perm: "dispatch.order:create", keywords: "dispatch customer order" },
  { id: "new-lead", label: "New lead", icon: UserPlus, href: "/m/sales/leads?create=1", perm: "crm.lead:create", keywords: "crm prospect" },
  { id: "new-payment", label: "Record payment", icon: ReceiptText, href: "/m/accounts/payments?create=1", perm: "accounts.payment:post", keywords: "receipt money bank" },
  { id: "customers", label: "Customers", icon: Users, href: "/crm/customers", page: true, perm: "masters.customer:read", keywords: "accounts directory" },
  { id: "stock", label: "Stock balances", icon: Boxes, href: "/m/inventory/stock", page: true, perm: "inventory.stock:read", keywords: "inventory quantity" },
];

export const DEFAULT_PINS = ["scan", "approvals", "new-po", "stocktake", "new-prod", "new-order"];
export const MAX_PINS = 8;

/** Ask the shell to do something (open the scanner, the palette, the shortcut sheet). */
export const runShell = (kind: "scan" | "palette" | "shortcuts") => window.dispatchEvent(new Event(`mintree:${kind}`));
