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
from app.maintenance.models import CalibrationRecord, DowntimeRecord, Equipment, MaintenanceSchedule, MaintenanceWorkOrder  # noqa: E402,F401
from app.ecommerce.models import ExternalOrder, ExternalOrderItem, MarketplaceAccount, MarketplaceSettlement, SkuMapping  # noqa: E402,F401
from app.crm.models import Complaint, Lead  # noqa: E402,F401
from app.branding.models import Campaign, ContentItem, PublishingRecord  # noqa: E402,F401

from app.approvals import models as _approvals_models  # noqa: E402,F401
from app.notifications import models as _notifications_models  # noqa: E402,F401
from app.procurement import rfq_models as _rfq_models  # noqa: E402,F401
from app.accounts import ext_models as _acct_ext_models  # noqa: E402,F401
from app.sales import models as _sales_models  # noqa: E402,F401
from app.inventory import stocktake_models as _stocktake_models  # noqa: E402,F401

from app.procurement.ext_models import PoAmendment, PoAmendmentLine, PurchaseReturn, PurchaseReturnLine  # noqa: E402,F401
from app.masters.models import CourierPartner, PaymentMethod, TaxClass, UnitConversion  # noqa: E402,F401
from app.crm.ext_models import CrmActivity, LeadSource, Opportunity, OpportunityStageHistory  # noqa: E402,F401
from app.crm.models import Complaint, Lead  # noqa: E402,F401
from app.dispatch.ext_models import DeliveryAttempt, ProofOfDelivery, ShipmentPackage  # noqa: E402,F401
from app.quality.ext_models import CertificateOfAnalysis, SamplingPlan  # noqa: E402,F401
from app.production.plan_models import ProductionPlan, ProductionPlanLine, WorkCentre  # noqa: E402,F401
from app.notifications.models import NotificationPreference, UserNotification  # noqa: E402,F401
from app.core.demo_models import DemoSeedStep  # noqa: E402,F401
from app.ai.models import AiBriefing, AiConversation, AiExtraction, AiMessage, AiProposal, AiToolCall, AiUsage  # noqa: E402,F401
