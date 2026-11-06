"""Imports every model so Base.metadata is complete (used by Alembic and tests)."""
from app.administration.models import (  # noqa: F401
    Department, Permission, Role, SessionRecord, User, role_permissions, user_roles,
)
from app.audit.models import AuditLog  # noqa: F401
from app.documents.models import Document, DocumentLink, DocumentVersion  # noqa: F401
from app.masters.models import (  # noqa: F401
    Brand, Customer, Item, ItemCategory, StorageLocation, Supplier, UnitOfMeasure, Warehouse,
)
