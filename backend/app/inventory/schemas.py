from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Qty = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QCDecision(_In):
    decision: Literal["RELEASED", "HOLD", "REJECTED"]
    reason: str | None = Field(None, max_length=500)


class ReserveIn(_In):
    item_id: int
    warehouse_id: int
    qty: Qty
    reference_type: str = Field(min_length=1, max_length=40)
    reference_id: str = Field(min_length=1, max_length=64)
    batch_id: int | None = None


class ReleaseIn(_In):
    reservation_ids: list[int] = Field(min_length=1, max_length=100)


class IssueIn(_In):
    item_id: int
    warehouse_id: int
    qty: Qty
    source_type: str = Field(min_length=1, max_length=40)
    source_id: int
    source_line: int = 0
    reservation_ids: list[int] | None = None
    batch_id: int | None = None


class TransferIn(_In):
    item_id: int
    batch_id: int | None = None
    from_warehouse_id: int
    to_warehouse_id: int
    qty: Qty
    source_id: int
    from_location_id: int | None = None
    to_location_id: int | None = None


class AdjustIn(_In):
    balance_id: int
    delta: Annotated[Decimal, Field(max_digits=15, decimal_places=3)]
    reason: str = Field(min_length=5, max_length=500)
    source_id: int


class ReverseIn(_In):
    reason: str = Field(min_length=5, max_length=500)
