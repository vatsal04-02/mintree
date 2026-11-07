import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

Code = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=40,
                                        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]

ItemKind = Literal["RM", "PM", "BULK", "FG"]
SupplierCategory = Literal["RM", "PM", "RM_PM", "SERVICES"]
SupplierStatus = Literal["PENDING", "APPROVED", "SUSPENDED"]
CustomerType = Literal["DISTRIBUTOR", "BRAND", "B2B", "MARKETPLACE", "DIRECT"]
WarehouseKind = Literal["RM", "PM", "QC_QUARANTINE", "REJECTED", "BULK", "WIP", "FG", "DISPATCH", "OTHER"]


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return None if isinstance(v, str) and v.strip() == "" else v


class _Audited(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime


class _TaxIds(BaseModel):
    """GSTIN / PAN format validation shared by suppliers and customers."""

    @field_validator("gstin", check_fields=False)
    @classmethod
    def _gstin(cls, v):
        if v is None:
            return v
        v = v.upper()
        if not GSTIN_RE.match(v):
            raise ValueError("Invalid GSTIN format")
        return v

    @field_validator("pan", check_fields=False)
    @classmethod
    def _pan(cls, v):
        if v is None:
            return v
        v = v.upper()
        if not PAN_RE.match(v):
            raise ValueError("Invalid PAN format")
        return v


# ---- Units of measure ----
class UomIn(_Base):
    code: Code
    name: Name
    is_active: bool = True


class UomUpdate(_Base):
    code: Code | None = None
    name: Name | None = None
    is_active: bool | None = None


class UomOut(_Audited, UomIn):
    pass


# ---- Categories ----
class CategoryIn(_Base):
    code: Code
    name: Name
    kind: ItemKind
    is_active: bool = True


class CategoryUpdate(_Base):
    code: Code | None = None
    name: Name | None = None
    kind: ItemKind | None = None
    is_active: bool | None = None


class CategoryOut(_Audited, CategoryIn):
    pass


# ---- Items ----
class ItemIn(_Base):
    item_code: Code
    name: Name
    description: str | None = None
    kind: ItemKind
    category_id: int | None = None
    uom_id: int
    hsn_code: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{4,8}$")] = None
    barcode: Annotated[str | None, StringConstraints(pattern=r"^[0-9A-Za-z\-]{6,32}$")] = None
    gst_rate: Annotated[Decimal | None, Field(ge=0, le=100, decimal_places=2)] = None
    tax_class_id: int | None = None
    storage_conditions: str | None = Field(None, max_length=200)
    shelf_life_days: int | None = Field(None, gt=0)
    reorder_level: Annotated[Decimal | None, Field(ge=0, decimal_places=3)] = None
    is_batch_tracked: bool = True
    is_active: bool = True


class ItemUpdate(_Base):
    item_code: Code | None = None
    name: Name | None = None
    description: str | None = None
    kind: ItemKind | None = None
    category_id: int | None = None
    uom_id: int | None = None
    hsn_code: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{4,8}$")] = None
    barcode: Annotated[str | None, StringConstraints(pattern=r"^[0-9A-Za-z\-]{6,32}$")] = None
    gst_rate: Annotated[Decimal | None, Field(ge=0, le=100, decimal_places=2)] = None
    tax_class_id: int | None = None
    storage_conditions: str | None = Field(None, max_length=200)
    shelf_life_days: int | None = Field(None, gt=0)
    reorder_level: Annotated[Decimal | None, Field(ge=0, decimal_places=3)] = None
    is_batch_tracked: bool | None = None
    is_active: bool | None = None


class ItemOut(_Audited, ItemIn):
    category_name: str | None = None
    uom_code: str


# ---- Suppliers ----
class SupplierIn(_TaxIds, _Base):
    code: Code
    name: Name
    gstin: str | None = None
    pan: str | None = None
    address_line1: str | None = Field(None, max_length=200)
    address_line2: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=80)
    state: str | None = Field(None, max_length=80)
    pincode: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{6}$")] = None
    contact_name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    category: SupplierCategory = "RM_PM"
    payment_terms_days: int | None = Field(None, ge=0)
    approval_status: SupplierStatus = "PENDING"
    rating: Annotated[Decimal | None, Field(ge=0, le=5, decimal_places=1)] = None
    bank_name: str | None = Field(None, max_length=120)
    bank_account_no: str | None = Field(None, max_length=40)
    bank_ifsc: str | None = None
    is_active: bool = True

    @field_validator("bank_ifsc")
    @classmethod
    def _ifsc(cls, v):
        if v is None:
            return v
        v = v.upper()
        if not IFSC_RE.match(v):
            raise ValueError("Invalid IFSC")
        return v


class SupplierUpdate(_TaxIds, _Base):
    code: Code | None = None
    name: Name | None = None
    gstin: str | None = None
    pan: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{6}$")] = None
    contact_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    category: SupplierCategory | None = None
    payment_terms_days: int | None = Field(None, ge=0)
    approval_status: SupplierStatus | None = None
    rating: Annotated[Decimal | None, Field(ge=0, le=5, decimal_places=1)] = None
    bank_name: str | None = None
    bank_account_no: str | None = None
    bank_ifsc: str | None = None
    is_active: bool | None = None

    @field_validator("bank_ifsc")
    @classmethod
    def _ifsc(cls, v):
        if v is None:
            return v
        v = v.upper()
        if not IFSC_RE.match(v):
            raise ValueError("Invalid IFSC")
        return v


class SupplierOut(_Audited, _Base):
    code: str
    name: str
    gstin: str | None
    pan: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    pincode: str | None
    contact_name: str | None
    phone: str | None
    email: str | None
    category: str
    payment_terms_days: int | None
    approval_status: str
    rating: Decimal | None
    bank_name: str | None
    bank_account_no: str | None
    bank_ifsc: str | None
    is_active: bool


# ---- Customers ----
class CustomerIn(_TaxIds, _Base):
    code: Code
    name: Name
    customer_type: CustomerType
    gstin: str | None = None
    pan: str | None = None
    billing_address: str | None = None
    shipping_address: str | None = None
    state: str | None = Field(None, max_length=80)
    contact_name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    credit_limit: Annotated[Decimal | None, Field(ge=0, decimal_places=2)] = None
    credit_days: int | None = Field(None, ge=0)
    is_active: bool = True


class CustomerUpdate(_TaxIds, _Base):
    code: Code | None = None
    name: Name | None = None
    customer_type: CustomerType | None = None
    gstin: str | None = None
    pan: str | None = None
    billing_address: str | None = None
    shipping_address: str | None = None
    state: str | None = None
    contact_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    credit_limit: Annotated[Decimal | None, Field(ge=0, decimal_places=2)] = None
    credit_days: int | None = Field(None, ge=0)
    is_active: bool | None = None


class CustomerOut(_Audited, CustomerIn):
    pass


# ---- Brands ----
class BrandIn(_Base):
    code: Code
    name: Name
    owner_customer_id: int | None = None
    description: str | None = None
    is_active: bool = True


class BrandUpdate(_Base):
    code: Code | None = None
    name: Name | None = None
    owner_customer_id: int | None = None
    description: str | None = None
    is_active: bool | None = None


class BrandOut(_Audited, BrandIn):
    pass


# ---- Warehouses & locations ----
class WarehouseIn(_Base):
    code: Code
    name: Name
    kind: WarehouseKind
    address: str | None = None
    is_active: bool = True


class WarehouseUpdate(_Base):
    code: Code | None = None
    name: Name | None = None
    kind: WarehouseKind | None = None
    address: str | None = None
    is_active: bool | None = None


class WarehouseOut(_Audited, WarehouseIn):
    pass


class LocationIn(_Base):
    warehouse_id: int
    code: Code
    name: str | None = Field(None, max_length=120)
    is_active: bool = True


class LocationUpdate(_Base):
    warehouse_id: int | None = None
    code: Code | None = None
    name: str | None = None
    is_active: bool | None = None


class LocationOut(_Audited, LocationIn):
    warehouse_code: str


# ---- Unit conversions / tax classes / payment methods / courier partners ----
PaymentKind = Literal["CASH", "BANK_TRANSFER", "UPI", "CARD", "CHEQUE", "MARKETPLACE", "OTHER"]
CourierKind = Literal["COURIER", "SURFACE", "AIR", "OWN_FLEET", "MARKETPLACE_LOGISTICS"]
IntegrationMode = Literal["MANUAL", "DEMO", "LIVE"]
Factor = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=8)]
Rate = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class ConversionIn(_Base):
    from_uom_id: int
    to_uom_id: int
    item_id: int | None = None
    factor: Factor
    is_active: bool = True


class ConversionUpdate(_Base):
    factor: Factor | None = None
    is_active: bool | None = None


class ConversionOut(_Audited, ConversionIn):
    pass


class TaxClassIn(_Base):
    code: Code
    name: Name
    hsn_prefix: str | None = Field(None, max_length=10)
    gst_rate: Rate
    cess_rate: Rate = Decimal(0)
    is_active: bool = True


class TaxClassUpdate(_Base):
    name: Name | None = None
    hsn_prefix: str | None = Field(None, max_length=10)
    gst_rate: Rate | None = None
    cess_rate: Rate | None = None
    is_active: bool | None = None


class TaxClassOut(_Audited, TaxClassIn):
    pass


class PaymentMethodIn(_Base):
    code: Code
    name: Name
    kind: PaymentKind
    ledger_account_code: str | None = Field(None, max_length=10)
    is_active: bool = True


class PaymentMethodUpdate(_Base):
    name: Name | None = None
    kind: PaymentKind | None = None
    ledger_account_code: str | None = Field(None, max_length=10)
    is_active: bool | None = None


class PaymentMethodOut(_Audited, PaymentMethodIn):
    pass


class CourierIn(_Base):
    code: Code
    name: Name
    kind: CourierKind
    integration_mode: IntegrationMode = "MANUAL"
    tracking_url_template: str | None = Field(None, max_length=300)
    is_active: bool = True


class CourierUpdate(_Base):
    name: Name | None = None
    kind: CourierKind | None = None
    integration_mode: IntegrationMode | None = None
    tracking_url_template: str | None = Field(None, max_length=300)
    is_active: bool | None = None


class CourierOut(_Audited, CourierIn):
    pass
