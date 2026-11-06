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
    IdempotencyKey, JobFailure, Notification, OutboxEvent, ProcessedEvent, WebhookEvent,
)
from app.inventory.models import InventoryBatch, StockBalance, StockLedger, StockReservation  # noqa: E402,F401
from app.procurement.models import (  # noqa: E402,F401
    ApprovalPolicy, GoodsReceipt, GoodsReceiptItem, PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem,
)
from app.accounts.models import (  # noqa: E402,F401
    Account, AccountingPeriod, DocumentCounter, ItemValuation, JournalEntry, JournalLine, Payment, PaymentAllocation,
    SupplierInvoice, SupplierInvoiceLine,
)
from app.rnd.models import Formulation, FormulationIngredient, FormulationVersion, LabTrial  # noqa: E402,F401
from app.quality.models import CapaAction, Deviation, Inspection, InspectionResult, QualitySpec, QualitySpecTest  # noqa: E402,F401
from app.production.models import (  # noqa: E402,F401
    Bom, BomLine, ProductionConsumption, ProductionMaterial, ProductionOrder, ProductionStage, WastageRecord,
)
from app.dispatch.models import (  # noqa: E402,F401
    CourierEvent, CreditNote, SalesInvoice, SalesOrder, SalesOrderItem, SalesReturn, SalesReturnLine, Shipment, ShipmentLine,
)
