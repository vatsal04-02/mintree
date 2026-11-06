"""Unit-of-measure conversion. Decimal only; never floats; no rounding unless the caller asks for a scale."""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.masters.models import UnitConversion


def conversion_factor(db: Session, from_uom_id: int, to_uom_id: int, item_id: int | None = None) -> Decimal:
    """Resolution order: item-specific direct, item-specific inverse, generic direct, generic inverse. 1 for identical units.
    Chaining through a third unit is deliberately NOT done: an ambiguous path is a data error, not something to guess."""
    if from_uom_id == to_uom_id:
        return Decimal(1)
    for scoped in ([item_id, None] if item_id is not None else [None]):
        q = select(UnitConversion).where(UnitConversion.is_active.is_(True), UnitConversion.item_id.is_(scoped) if scoped is None else UnitConversion.item_id == scoped)
        rows = list(db.scalars(q.where(UnitConversion.from_uom_id.in_([from_uom_id, to_uom_id]), UnitConversion.to_uom_id.in_([from_uom_id, to_uom_id]))))
        for r in rows:
            if r.from_uom_id == from_uom_id:
                return r.factor
        for r in rows:
            return Decimal(1) / r.factor
    raise Unprocessable("No unit conversion is defined between these units" + (" for this item" if item_id else ""))


def convert(db: Session, qty: Decimal, from_uom_id: int, to_uom_id: int, item_id: int | None = None, scale: int | None = None) -> Decimal:
    out = Decimal(qty) * conversion_factor(db, from_uom_id, to_uom_id, item_id)
    return out if scale is None else out.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
