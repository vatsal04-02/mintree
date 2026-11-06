from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Qty = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]
Money = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)]
PosMoney = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=2)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountIn(_In):
    code: str = Field(pattern=r"^[0-9]{3,10}$")
    name: str = Field(min_length=1, max_length=120)
    type: Literal["ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"]
    is_postable: bool = True


class JLine(_In):
    account_code: str
    debit: Money = Decimal(0)
    credit: Money = Decimal(0)


class ManualJournal(_In):
    entry_date: date
    description: str = Field(min_length=3, max_length=300)
    lines: list[JLine] = Field(min_length=2, max_length=100)


class Reason(_In):
    reason: str = Field(min_length=5, max_length=300)


class SILine(_In):
    po_item_id: int
    qty: Qty
    unit_price: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] | None = None


class SupplierInvoiceIn(_In):
    po_id: int
    supplier_invoice_no: str = Field(min_length=1, max_length=60)
    invoice_date: date
    lines: list[SILine] = Field(min_length=1, max_length=200)


class Alloc(_In):
    invoice_id: int
    amount: PosMoney


class PaymentIn(_In):
    direction: Literal["PAID", "RECEIVED"]
    party_id: int
    amount: PosMoney
    payment_date: date
    method: Literal["BANK_TRANSFER", "CASH", "CHEQUE", "UPI"]
    bank_account_code: Literal["1000", "1100"]
    reference: str | None = Field(None, max_length=80)
    allocations: list[Alloc] = Field(min_length=1, max_length=100)
