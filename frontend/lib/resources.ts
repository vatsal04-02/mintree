// Config-driven definitions for master-data screens. Every table/form in Phase 0 renders from these.
export type Option = { value: string; label: string };
export type Field = {
  name: string; label: string;
  type: "text" | "textarea" | "number" | "select" | "ref" | "bool" | "email";
  options?: Option[]; ref?: { resource: string; label: (row: any) => string; params?: Record<string, string> };
  required?: boolean; placeholder?: string; help?: string; section: string; upper?: boolean;
  /** Field is only shown/sent when the user holds this permission (e.g. supplier bank details). */
  needsPerm?: string; step?: string;
};
export type Column = {
  key: string; label: string; kind?: "text" | "mono" | "number" | "money" | "status" | "bool" | "date" | "badge";
  tones?: Record<string, "success" | "warning" | "danger" | "info" | "neutral">; enumLabels?: Record<string, string>;
  digits?: number; width?: string;
};
export type ResourceDef = {
  key: string; title: string; singular: string; description: string; api: string; perm: string;
  fields: Field[]; columns: Column[]; filters?: { name: string; label: string; options: Option[] }[];
  exportable?: boolean; searchHint?: string; sort: string; sections: string[];
};

const opt = (m: Record<string, string>): Option[] => Object.entries(m).map(([value, label]) => ({ value, label }));

export const ITEM_KINDS = { RM: "Raw material", PM: "Packaging material", BULK: "Bulk", FG: "Finished goods" };
const SUPPLIER_CATEGORIES = { RM: "Raw material", PM: "Packaging", RM_PM: "RM & PM", SERVICES: "Services" };
const SUPPLIER_STATUS = { PENDING: "Pending", APPROVED: "Approved", SUSPENDED: "Suspended" };
const CUSTOMER_TYPES = { DISTRIBUTOR: "Distributor", BRAND: "Brand client", B2B: "B2B", MARKETPLACE: "Marketplace", DIRECT: "Direct" };
const WAREHOUSE_KINDS = {
  RM: "Raw materials", PM: "Packaging", QC_QUARANTINE: "QC quarantine", REJECTED: "Rejected", BULK: "Bulk",
  WIP: "Work in progress", FG: "Finished goods", DISPATCH: "Dispatch", OTHER: "Other",
};
const activeCol: Column = { key: "is_active", label: "Status", kind: "bool" };
const code = (label = "Code", name = "code"): Field => ({ name, label, type: "text", required: true, section: "General", upper: true });

export const RESOURCES: Record<string, ResourceDef> = {
  items: {
    key: "items", title: "Items", singular: "Item", api: "/masters/items", perm: "masters.item", exportable: true, sort: "item_code",
    description: "Raw materials, packaging, bulk and finished goods. One master record per item.",
    sections: ["General", "Inventory", "Tax"],
    fields: [
      { ...code("Item code", "item_code"), help: "Unique. Stored in upper case." },
      { name: "name", label: "Item name", type: "text", required: true, section: "General" },
      { name: "kind", label: "Type", type: "select", required: true, section: "General", options: opt(ITEM_KINDS) },
      { name: "category_id", label: "Category", type: "ref", section: "General", ref: { resource: "/masters/categories", label: (r) => r.name } },
      { name: "description", label: "Description", type: "textarea", section: "General" },
      { name: "uom_id", label: "Unit of measure", type: "ref", required: true, section: "Inventory", ref: { resource: "/masters/units-of-measure", label: (r) => `${r.code} — ${r.name}` } },
      { name: "reorder_level", label: "Reorder level", type: "number", section: "Inventory", step: "0.001" },
      { name: "shelf_life_days", label: "Shelf life (days)", type: "number", section: "Inventory", step: "1" },
      { name: "storage_conditions", label: "Storage conditions", type: "text", section: "Inventory", placeholder: "e.g. Cool, dry place below 25°C" },
      { name: "is_batch_tracked", label: "Track by batch", type: "bool", section: "Inventory" },
      { name: "hsn_code", label: "HSN code", type: "text", section: "Tax", help: "4–8 digits" },
      { name: "barcode", label: "Retail barcode", type: "text", section: "Tax", help: "EAN-13 / UPC printed on the pack (6–32 characters); scanning it finds this item" },
      { name: "gst_rate", label: "GST rate (%)", type: "number", section: "Tax", step: "0.01" },
    ],
    columns: [
      { key: "item_code", label: "Code", kind: "mono" }, { key: "name", label: "Name" },
      { key: "kind", label: "Type", kind: "badge", enumLabels: ITEM_KINDS, tones: { RM: "info", PM: "neutral", BULK: "warning", FG: "success" } },
      { key: "category_name", label: "Category" }, { key: "uom_code", label: "UoM", kind: "mono" },
      { key: "reorder_level", label: "Reorder lvl", kind: "number", digits: 3 }, { key: "shelf_life_days", label: "Shelf life (d)", kind: "number" },
      activeCol,
    ],
    filters: [{ name: "kind", label: "Type", options: opt(ITEM_KINDS) }],
  },
  categories: {
    key: "categories", title: "Categories", singular: "Category", api: "/masters/categories", perm: "masters.category", sort: "code",
    description: "Groups items by type, e.g. Actives, Bottles, Cartons.", sections: ["General"],
    fields: [code(), { name: "name", label: "Name", type: "text", required: true, section: "General" },
      { name: "kind", label: "Applies to", type: "select", required: true, section: "General", options: opt(ITEM_KINDS) }],
    columns: [{ key: "code", label: "Code", kind: "mono" }, { key: "name", label: "Name" },
      { key: "kind", label: "Applies to", kind: "badge", enumLabels: ITEM_KINDS, tones: { RM: "info", PM: "neutral", BULK: "warning", FG: "success" } }, activeCol],
    filters: [{ name: "kind", label: "Applies to", options: opt(ITEM_KINDS) }],
  },
  "units-of-measure": {
    key: "units-of-measure", title: "Units of measure", singular: "Unit", api: "/masters/units-of-measure", perm: "masters.uom", sort: "code",
    description: "Units used for stock, purchase and production quantities.", sections: ["General"],
    fields: [code(), { name: "name", label: "Name", type: "text", required: true, section: "General" }],
    columns: [{ key: "code", label: "Code", kind: "mono" }, { key: "name", label: "Name" }, activeCol],
  },
  suppliers: {
    key: "suppliers", title: "Suppliers", singular: "Supplier", api: "/masters/suppliers", perm: "masters.supplier", exportable: true, sort: "code",
    description: "Approved vendors for raw and packaging materials.", sections: ["General", "Contact", "Commercial", "Bank details"],
    fields: [
      code("Supplier code"), { name: "name", label: "Supplier name", type: "text", required: true, section: "General" },
      { name: "category", label: "Supplies", type: "select", required: true, section: "General", options: opt(SUPPLIER_CATEGORIES) },
      { name: "approval_status", label: "Approval status", type: "select", required: true, section: "General", options: opt(SUPPLIER_STATUS) },
      { name: "gstin", label: "GSTIN", type: "text", section: "General", upper: true, placeholder: "27AAPFU0939F1ZV" },
      { name: "pan", label: "PAN", type: "text", section: "General", upper: true },
      { name: "contact_name", label: "Contact person", type: "text", section: "Contact" },
      { name: "phone", label: "Phone", type: "text", section: "Contact" },
      { name: "email", label: "Email", type: "email", section: "Contact" },
      { name: "address_line1", label: "Address line 1", type: "text", section: "Contact" },
      { name: "address_line2", label: "Address line 2", type: "text", section: "Contact" },
      { name: "city", label: "City", type: "text", section: "Contact" }, { name: "state", label: "State", type: "text", section: "Contact" },
      { name: "pincode", label: "Pincode", type: "text", section: "Contact" },
      { name: "payment_terms_days", label: "Payment terms (days)", type: "number", section: "Commercial", step: "1" },
      { name: "rating", label: "Rating (0–5)", type: "number", section: "Commercial", step: "0.1" },
      { name: "bank_name", label: "Bank name", type: "text", section: "Bank details", needsPerm: "masters.supplier_bank:update" },
      { name: "bank_account_no", label: "Account number", type: "text", section: "Bank details", needsPerm: "masters.supplier_bank:update" },
      { name: "bank_ifsc", label: "IFSC", type: "text", section: "Bank details", upper: true, needsPerm: "masters.supplier_bank:update" },
    ],
    columns: [
      { key: "code", label: "Code", kind: "mono" }, { key: "name", label: "Name" }, { key: "gstin", label: "GSTIN", kind: "mono" },
      { key: "category", label: "Supplies", kind: "badge", enumLabels: SUPPLIER_CATEGORIES },
      { key: "approval_status", label: "Approval", kind: "badge", enumLabels: SUPPLIER_STATUS, tones: { APPROVED: "success", PENDING: "warning", SUSPENDED: "danger" } },
      { key: "city", label: "City" }, { key: "payment_terms_days", label: "Terms (d)", kind: "number" }, { key: "rating", label: "Rating", kind: "number", digits: 1 },
      activeCol,
    ],
    filters: [{ name: "approval_status", label: "Approval", options: opt(SUPPLIER_STATUS) }, { name: "category", label: "Supplies", options: opt(SUPPLIER_CATEGORIES) }],
  },
  customers: {
    key: "customers", title: "Customers", singular: "Customer", api: "/masters/customers", perm: "masters.customer", exportable: true, sort: "code",
    description: "Distributors, brand clients and other buyers. Commercial workflows stay separate by customer type.",
    sections: ["General", "Contact", "Credit"],
    fields: [
      code("Customer code"), { name: "name", label: "Customer name", type: "text", required: true, section: "General" },
      { name: "customer_type", label: "Type", type: "select", required: true, section: "General", options: opt(CUSTOMER_TYPES) },
      { name: "gstin", label: "GSTIN", type: "text", section: "General", upper: true }, { name: "pan", label: "PAN", type: "text", section: "General", upper: true },
      { name: "contact_name", label: "Contact person", type: "text", section: "Contact" }, { name: "phone", label: "Phone", type: "text", section: "Contact" },
      { name: "email", label: "Email", type: "email", section: "Contact" }, { name: "state", label: "State", type: "text", section: "Contact" },
      { name: "billing_address", label: "Billing address", type: "textarea", section: "Contact" },
      { name: "shipping_address", label: "Shipping address", type: "textarea", section: "Contact" },
      { name: "credit_limit", label: "Credit limit (₹)", type: "number", section: "Credit", step: "0.01" },
      { name: "credit_days", label: "Credit days", type: "number", section: "Credit", step: "1" },
    ],
    columns: [
      { key: "code", label: "Code", kind: "mono" }, { key: "name", label: "Name" },
      { key: "customer_type", label: "Type", kind: "badge", enumLabels: CUSTOMER_TYPES }, { key: "gstin", label: "GSTIN", kind: "mono" },
      { key: "state", label: "State" }, { key: "credit_limit", label: "Credit limit", kind: "money" }, { key: "credit_days", label: "Days", kind: "number" }, activeCol,
    ],
    filters: [{ name: "customer_type", label: "Type", options: opt(CUSTOMER_TYPES) }],
  },
  brands: {
    key: "brands", title: "Brands", singular: "Brand", api: "/masters/brands", perm: "masters.brand", sort: "code",
    description: "Mintree's own brands and brand-client labels.", sections: ["General"],
    fields: [code(), { name: "name", label: "Name", type: "text", required: true, section: "General" },
      { name: "owner_customer_id", label: "Owner (brand client)", type: "ref", section: "General", ref: { resource: "/masters/customers", label: (r) => r.name, params: { customer_type: "BRAND" } }, help: "Leave blank for Mintree-owned brands." },
      { name: "description", label: "Description", type: "textarea", section: "General" }],
    columns: [{ key: "code", label: "Code", kind: "mono" }, { key: "name", label: "Name" }, { key: "description", label: "Description" }, activeCol],
  },
  warehouses: {
    key: "warehouses", title: "Warehouses", singular: "Warehouse", api: "/masters/warehouses", perm: "masters.warehouse", sort: "code",
    description: "Proposed defaults — reconcile with Mintree's physical warehouse structure.", sections: ["General"],
    fields: [code(), { name: "name", label: "Name", type: "text", required: true, section: "General" },
      { name: "kind", label: "Purpose", type: "select", required: true, section: "General", options: opt(WAREHOUSE_KINDS) },
      { name: "address", label: "Address", type: "textarea", section: "General" }],
    columns: [{ key: "code", label: "Code", kind: "mono" }, { key: "name", label: "Name" },
      { key: "kind", label: "Purpose", kind: "badge", enumLabels: WAREHOUSE_KINDS, tones: { QC_QUARANTINE: "warning", REJECTED: "danger", FG: "success" } }, activeCol],
    filters: [{ name: "kind", label: "Purpose", options: opt(WAREHOUSE_KINDS) }],
  },
  locations: {
    key: "locations", title: "Storage locations", singular: "Location", api: "/masters/locations", perm: "masters.location", sort: "code",
    description: "Racks, bins and zones inside a warehouse.", sections: ["General"],
    fields: [{ name: "warehouse_id", label: "Warehouse", type: "ref", required: true, section: "General", ref: { resource: "/masters/warehouses", label: (r) => `${r.code} — ${r.name}` } },
      code(), { name: "name", label: "Description", type: "text", section: "General" }],
    columns: [{ key: "warehouse_code", label: "Warehouse", kind: "mono" }, { key: "code", label: "Location", kind: "mono" }, { key: "name", label: "Description" }, activeCol],
  },
  departments: {
    key: "departments", title: "Departments", singular: "Department", api: "/admin/departments", perm: "admin.department", sort: "code",
    description: "Organisational units used for access scope and approvals.", sections: ["General"],
    fields: [code(), { name: "name", label: "Name", type: "text", required: true, section: "General" }],
    columns: [{ key: "code", label: "Code", kind: "mono" }, { key: "name", label: "Name" }, activeCol],
  },
};
