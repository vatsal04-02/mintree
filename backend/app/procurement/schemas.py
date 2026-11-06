from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Qty = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]
Money = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PRLine(_In):
    item_id: int
    qty: Qty


class PRCreate(_In):
    items: list[PRLine] = Field(min_length=1, max_length=200)
    required_by: date | None = None
    notes: str | None = Field(None, max_length=1000)
    department_id: int | None = None


class Transition(_In):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(None, max_length=500)


class POLine(_In):
    item_id: int
    qty: Qty
    unit_price: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] | None = None


class POCreate(_In):
    supplier_id: int
    items: list[POLine] = Field(min_length=1, max_length=200)
    expected_date: date | None = None
    notes: str | None = Field(None, max_length=1000)
    pr_id: int | None = None


class GRLine(_In):
    po_item_id: int
    qty: Qty
    supplier_lot_no: str | None = Field(None, max_length=60)
    mfg_date: date | None = None
    expiry_date: date | None = None
    location_id: int | None = None


class GRCreate(_In):
    po_id: int
    warehouse_id: int
    delivery_note_no: str = Field(min_length=1, max_length=60)
    lines: list[GRLine] = Field(min_length=1, max_length=200)
