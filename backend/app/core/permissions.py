"""Permission registry and default role matrix.

Permission codes look like ``masters.item:read``. The registry is the single source
of truth: the seed step syncs it into the ``permissions`` table, and routes refer to
codes via ``require("masters.item:read")``. Later phases append their resources here.
"""
from fnmatch import fnmatchcase

CRUD = ["read", "create", "update", "delete"]

# resource -> actions
RESOURCES: dict[str, list[str]] = {
    "admin.user": ["read", "create", "update"],
    "admin.role": ["read", "create", "update"],
    "admin.department": CRUD,
    "admin.audit": ["read"],
    "admin.jobs": ["read", "update"],  # outbox / background-job failure inspection and requeue
    "masters.item": CRUD + ["export"],
    "masters.category": CRUD,
    "masters.uom": CRUD,
    "masters.supplier": CRUD + ["export"],
    "masters.supplier_bank": ["read", "update"],  # restricted bank details
    "masters.customer": CRUD + ["export"],
    "masters.brand": CRUD,
    "masters.warehouse": CRUD,
    "masters.location": CRUD,
    "documents.document": CRUD + ["download"],
    # Inventory & warehouse
    "inventory.stock": ["read"],
    "inventory.batch": ["read", "qc_release"],
    "inventory.reservation": ["read", "create", "release"],
    "inventory.issue": ["post"],
    "inventory.transfer": ["post"],
    "inventory.adjustment": ["post"],
    "inventory.reversal": ["post"],
    # Procurement
    "procurement.pr": ["read", "create", "update", "approve"],
    "procurement.po": ["read", "create", "update", "approve", "cancel"],
    "procurement.receipt": ["read", "post"],
    "procurement.rfq": ["read", "create", "send", "quote", "award"],
    # Dispatch
    "dispatch.order": ["read", "create", "confirm", "allocate", "pack", "dispatch", "cancel"],
    "dispatch.shipment": ["read", "book"],
    "dispatch.return": ["read", "create", "receive", "decide"],
    "accounts.sales_invoice": ["read"],
    # Production
    "production.bom": ["read", "create", "approve"],
    "production.order": ["read", "create", "release", "issue", "execute", "complete", "cancel"],
    # R&D & Quality
    "rnd.formula": ["read", "create", "update", "approve"],
    "rnd.trial": ["read", "create", "update"],
    "quality.spec": ["read", "create"],
    "quality.inspection": ["read", "create", "record", "decide"],
    "quality.deviation": ["read", "create", "update", "close"],
    "quality.capa": ["read", "create", "update", "verify"],
    # Accounts
    "accounts.coa": ["read", "create"],
    "accounts.period": ["read", "close"],
    "accounts.journal": ["read", "post"],
    "accounts.report": ["read"],
    "accounts.supplier_invoice": ["read", "post"],
    "accounts.payment": ["read", "post"],
    "accounts.bank": ["read", "create", "reconcile"],
    "accounts.debit_note": ["read", "post"],
    "accounts.gst": ["read"],
    "accounts.settlement": ["post"],
    # Maintenance
    "maintenance.equipment": ["read", "create"],
    "maintenance.workorder": ["read", "create", "assign", "execute", "verify"],
    "maintenance.calibration": ["create"],
    # E-commerce
    "ecommerce.account": ["read", "create"],
    "ecommerce.mapping": ["read", "update"],
    "ecommerce.order": ["read", "ingest"],
    "ecommerce.settlement": ["create"],
    # CRM
    "crm.lead": ["read", "create", "update"],
    "crm.complaint": ["read", "create", "update"],
    # Branding / marketing
    "branding.campaign": ["read", "create"],
    "branding.content": ["read", "create", "approve", "publish"],
    # Approvals / settings
    "approvals.delegation": ["create"],
    "admin.settings": ["read", "update"],
    # Analytics
    "analytics.dashboard": ["read"],
}


def all_permission_codes() -> list[str]:
    return [f"{res}:{act}" for res, acts in RESOURCES.items() for act in acts]


def _expand(patterns: list[str]) -> set[str]:
    codes = all_permission_codes()
    out: set[str] = set()
    for p in patterns:  # evaluated in order; a leading "!" removes matches
        if p.startswith("!"):
            out.difference_update(c for c in codes if fnmatchcase(c, p[1:]))
        else:
            out.update(c for c in codes if fnmatchcase(c, p))
    return out


# role code -> (display name, description, permission patterns). Management gets no admin/audit/bank access.
ROLES: dict[str, tuple[str, str, list[str]]] = {
    "system_admin": ("System Administrator", "Technical configuration, users, security and audit.", ["*"]),
    "business_admin": (
        "Business Administrator",
        "Cross-department operations; no technical configuration.",
        ["masters.*", "!masters.supplier_bank:*", "documents.*", "admin.user:read", "admin.department:*",
         "admin.audit:read", "admin.role:read"],
    ),
    "management": ("Management / Owner", "Company-wide read visibility.", ["dispatch.*:read", "production.*:read", "rnd.*:read", "quality.*:read", "accounts.*:read", "inventory.*:read", "procurement.*:read", "maintenance.*:read", "ecommerce.*:read", "crm.*:read", "branding.*:read", "analytics.*:read", "masters.*:read", "masters.*:export", "!masters.supplier_bank:*", "documents.document:read",
                    "documents.document:download"]),
    "production_manager": ("Production Manager", "Production planning and execution.",
                           ["production.*", "rnd.formula:read", "inventory.stock:read", "inventory.batch:read", "inventory.reservation:*", "inventory.issue:post",
                            "procurement.pr:read", "procurement.pr:create", "masters.item:read", "masters.uom:read", "masters.category:read", "masters.warehouse:read",
                            "masters.location:read", "documents.document:read", "documents.document:create",
                            "documents.document:download"]),
    "production_operator": ("Production Operator", "Assigned production activities.",
                            ["production.order:read", "production.order:execute", "masters.item:read", "masters.uom:read"]),
    "purchase_manager": ("Purchase Manager", "Suppliers and procurement.",
                         ["procurement.*", "inventory.stock:read", "inventory.batch:read", "masters.supplier:*", "masters.supplier_bank:read", "masters.item:read",
                          "masters.category:read", "masters.uom:read", "documents.document:*"]),
    "stores_manager": ("Stores Manager", "Receipts, stock and warehouses.",
                       ["inventory.*", "!inventory.batch:qc_release", "procurement.receipt:*", "procurement.po:read", "masters.item:read", "masters.item:update", "masters.warehouse:*", "masters.location:*",
                        "masters.uom:read", "masters.category:read", "masters.supplier:read",
                        "documents.document:read", "documents.document:create", "documents.document:download"]),
    "quality_manager": ("Quality Manager", "Specifications, inspections and release.",
                        ["quality.*", "rnd.formula:read", "inventory.batch:*", "inventory.stock:read", "masters.item:read", "masters.supplier:read", "masters.uom:read",
                         "documents.document:*"]),
    "qc_inspector": ("QC Inspector", "Assigned inspections.",
                     ["quality.inspection:read", "quality.inspection:record", "quality.spec:read", "quality.deviation:create", "quality.deviation:read", "inventory.batch:read", "masters.item:read", "masters.uom:read", "documents.document:read",
                      "documents.document:create", "documents.document:download"]),
    "rnd_manager": ("R&D Manager", "Formulations and product development.",
                    ["rnd.*", "quality.spec:read", "masters.item:*", "masters.category:read", "masters.uom:read", "masters.supplier:read",
                     "documents.document:*"]),
    "maintenance_manager": ("Maintenance Manager", "Equipment and maintenance.",
                            ["maintenance.*", "production.order:read", "documents.document:read", "documents.document:create", "documents.document:download"]),
    "dispatch_manager": ("Dispatch Manager", "Fulfilment and delivery.",
                         ["dispatch.*", "ecommerce.*", "crm.*", "accounts.sales_invoice:read", "inventory.stock:read", "masters.item:read", "masters.customer:read", "masters.warehouse:read",
                          "masters.uom:read", "documents.document:read", "documents.document:create",
                          "documents.document:download"]),
    "accounts_manager": ("Accounts Manager", "Finance.",
                         ["accounts.*", "analytics.dashboard:read", "dispatch.order:read", "procurement.po:read", "procurement.receipt:read", "inventory.stock:read", "masters.supplier:*", "masters.supplier_bank:*", "masters.customer:*",
                          "masters.item:read", "masters.uom:read", "documents.document:*"]),
    "marketing_manager": ("Marketing Manager", "Brand and content.",
                          ["masters.brand:*", "branding.*", "crm.lead:*", "analytics.dashboard:read", "masters.item:read", "documents.document:*"]),
    "sales_operator": ("Sales / E-commerce Operator", "Orders and customers.",
                       ["ecommerce.*:read", "ecommerce.order:ingest", "crm.lead:*", "crm.complaint:*", "dispatch.order:read", "dispatch.order:create", "dispatch.order:confirm", "dispatch.shipment:read", "dispatch.return:read", "dispatch.return:create", "masters.customer:read", "masters.customer:create", "masters.customer:update",
                        "masters.item:read", "masters.brand:read", "documents.document:read"]),
}


# Everyone who can approve something may delegate their own authority; business admins can read company settings.
for _r in ("business_admin", "purchase_manager", "quality_manager", "rnd_manager", "production_manager", "accounts_manager", "marketing_manager", "maintenance_manager", "dispatch_manager"):
    ROLES[_r][2].append("approvals.delegation:create")
ROLES["business_admin"][2].append("admin.settings:read")


def role_permission_codes(role_code: str) -> set[str]:
    return _expand(ROLES[role_code][2])
