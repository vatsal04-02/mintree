from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

MONEY = Numeric(18, 2)


class MarketplaceAccount(Base):
    __tablename__ = "marketplace_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(120))
    seller_id: Mapped[str | None] = mapped_column(String(80))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))           # the customer record that marketplace orders are booked against
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE", server_default="ACTIVE")
    credentials_ref: Mapped[str | None] = mapped_column(String(120))               # a reference to a secret store entry -- never the secret itself
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("platform", "seller_id", name="uq_marketplace_accounts_seller"),
                      CheckConstraint("platform IN ('AMAZON','FLIPKART','MYNTRA','NYKAA','FILE')", name="platform_valid"),
                      CheckConstraint("status IN ('ACTIVE','PAUSED')", name="status_valid"))


class SkuMapping(Base):
    __tablename__ = "sku_mappings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("marketplace_accounts.id"))
    platform_sku: Mapped[str] = mapped_column(String(80))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    pack_multiplier: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=1, server_default="1")    # units of the internal item per listed unit
    safety_stock: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=0, server_default="0")
    __table_args__ = (UniqueConstraint("account_id", "platform_sku", name="uq_sku_mappings_sku"), CheckConstraint("pack_multiplier > 0 AND safety_stock >= 0", name="positive"))


class ExternalOrder(Base):
    """Every inbound marketplace order is kept: mapped ones become sales orders, unmappable ones wait in the exception queue
    (status EXCEPTION). Nothing is silently discarded. unique(account, external id) makes ingestion idempotent."""

    __tablename__ = "external_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("marketplace_accounts.id"))
    external_order_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(12), default="RECEIVED", server_default="RECEIVED")
    order_date: Mapped[date | None] = mapped_column(Date)
    customer_name: Mapped[str | None] = mapped_column(String(160))
    shipping_address: Mapped[str | None] = mapped_column(Text)
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    exception_reason: Mapped[str | None] = mapped_column(Text)
    sales_order_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id"))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["ExternalOrderItem"]] = relationship(lazy="selectin")
    __table_args__ = (UniqueConstraint("account_id", "external_order_id", name="uq_external_orders_ext"),
                      CheckConstraint("status IN ('RECEIVED','EXCEPTION','SO_CREATED')", name="status_valid"),
                      Index("ix_external_orders_status", "status", "id"))


class ExternalOrderItem(Base):
    __tablename__ = "external_order_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_order_pk: Mapped[int] = mapped_column(ForeignKey("external_orders.id"))
    platform_sku: Mapped[str] = mapped_column(String(80))
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"),)


class MarketplaceSettlement(Base):
    __tablename__ = "marketplace_settlements"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("marketplace_accounts.id"))
    reference: Mapped[str] = mapped_column(String(80))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    gross_sales: Mapped[Decimal] = mapped_column(MONEY)
    commission: Mapped[Decimal] = mapped_column(MONEY, default=0)
    shipping_fees: Mapped[Decimal] = mapped_column(MONEY, default=0)
    ad_fees: Mapped[Decimal] = mapped_column(MONEY, default=0)
    returns_refunds: Mapped[Decimal] = mapped_column(MONEY, default=0)
    other_deductions: Mapped[Decimal] = mapped_column(MONEY, default=0)
    net_settlement: Mapped[Decimal] = mapped_column(MONEY)
    received_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    __table_args__ = (UniqueConstraint("account_id", "reference", name="uq_marketplace_settlements_ref"),
                      CheckConstraint("net_settlement = gross_sales - commission - shipping_fees - ad_fees - returns_refunds - other_deductions", name="net_consistent"),
                      CheckConstraint("period_end >= period_start", name="period_ordered"))
