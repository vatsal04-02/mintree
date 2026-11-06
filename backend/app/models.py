"""Imports every model so Base.metadata is complete (used by Alembic and tests)."""
from app.administration.models import (  # noqa: F401
    Department, Permission, Role, SessionRecord, User, role_permissions, user_roles,
)
from app.audit.models import AuditLog  # noqa: F401
from app.documents.models import Document, DocumentLink, DocumentVersion  # noqa: F401
from app.masters.models import (  # noqa: F401
    Brand, Customer, Item, ItemCategory, StorageLocation, Supplier, UnitOfMeasure, Warehouse,
)
from app.core.platform_models import (  # noqa: E402,F401
    IdempotencyKey, JobFailure, Notification, OutboxEvent, ProcessedEvent,
)
from app.inventory.models import InventoryBatch, StockBalance, StockLedger, StockReservation  # noqa: E402,F401
from app.procurement.models import (  # noqa: E402,F401
    ApprovalPolicy, GoodsReceipt, GoodsReceiptItem, PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem,
)
