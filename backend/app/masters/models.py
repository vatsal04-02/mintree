from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import AuditFieldsMixin

ITEM_KINDS = ("RM", "PM", "BULK", "FG")
SUPPLIER_CATEGORIES = ("RM", "PM", "RM_PM", "SERVICES")
SUPPLIER_STATUSES = ("PENDING", "APPROVED", "SUSPENDED")
CUSTOMER_TYPES = ("DISTRIBUTOR", "BRAND", "B2B", "MARKETPLACE", "DIRECT")
WAREHOUSE_KINDS = ("RM", "PM", "QC_QUARANTINE", "REJECTED", "BULK", "WIP", "FG", "DISPATCH", "OTHER")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class UnitOfMeasure(AuditFieldsMixin, Base):
    __tablename__ = "units_of_measure"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(15), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class ItemCategory(AuditFieldsMixin, Base):
    __tablename__ = "product_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (CheckConstraint(_in("kind", ITEM_KINDS), name="kind_valid"),)


class Item(AuditFieldsMixin, Base):
    """Item master: raw materials, packaging, bulk and finished goods."""

    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"))
    uom_id: Mapped[int] = mapped_column(ForeignKey("units_of_measure.id"))
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    storage_conditions: Mapped[str | None] = mapped_column(String(200))
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    reorder_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    is_batch_tracked: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    category: Mapped[ItemCategory | None] = relationship(lazy="joined")
    uom: Mapped[UnitOfMeasure] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint(_in("kind", ITEM_KINDS), name="kind_valid"),
        CheckConstraint("shelf_life_days IS NULL OR shelf_life_days > 0", name="shelf_life_positive"),
        CheckConstraint("reorder_level IS NULL OR reorder_level >= 0", name="reorder_non_negative"),
        CheckConstraint("gst_rate IS NULL OR (gst_rate >= 0 AND gst_rate <= 100)", name="gst_rate_range"),
    )

    @property
    def category_name(self) -> str | None:
        return self.category.name if self.category else None

    @property
    def uom_code(self) -> str:
        return self.uom.code


class Supplier(AuditFieldsMixin, Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    address_line1: Mapped[str | None] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    pincode: Mapped[str | None] = mapped_column(String(10))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(254))
    category: Mapped[str] = mapped_column(String(10), default="RM_PM", server_default="RM_PM")
    payment_terms_days: Mapped[int | None] = mapped_column(Integer)
    approval_status: Mapped[str] = mapped_column(String(12), default="PENDING", server_default="PENDING")
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    # Restricted: only readable/writable with masters.supplier_bank permissions.
    bank_name: Mapped[str | None] = mapped_column(String(120))
    bank_account_no: Mapped[str | None] = mapped_column(String(40))
    bank_ifsc: Mapped[str | None] = mapped_column(String(11))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        CheckConstraint(_in("category", SUPPLIER_CATEGORIES), name="category_valid"),
        CheckConstraint(_in("approval_status", SUPPLIER_STATUSES), name="status_valid"),
        CheckConstraint("rating IS NULL OR (rating >= 0 AND rating <= 5)", name="rating_range"),
        CheckConstraint("payment_terms_days IS NULL OR payment_terms_days >= 0", name="terms_non_negative"),
    )


class Customer(AuditFieldsMixin, Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    customer_type: Mapped[str] = mapped_column(String(12))
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    billing_address: Mapped[str | None] = mapped_column(Text)
    shipping_address: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(80))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(254))
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    credit_days: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        CheckConstraint(_in("customer_type", CUSTOMER_TYPES), name="type_valid"),
        CheckConstraint("credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_non_negative"),
        CheckConstraint("credit_days IS NULL OR credit_days >= 0", name="credit_days_non_negative"),
    )


class Brand(AuditFieldsMixin, Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Warehouse(AuditFieldsMixin, Base):
    __tablename__ = "warehouses"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(15))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (CheckConstraint(_in("kind", WAREHOUSE_KINDS), name="kind_valid"),)


class StorageLocation(AuditFieldsMixin, Base):
    __tablename__ = "storage_locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    warehouse: Mapped[Warehouse] = relationship(lazy="joined")
    __table_args__ = (UniqueConstraint("warehouse_id", "code"),)

    @property
    def warehouse_code(self) -> str:
        return self.warehouse.code
