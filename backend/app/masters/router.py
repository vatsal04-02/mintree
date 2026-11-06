from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require
from app.masters.conversion import conversion_factor

from app.core.crud import Resource, build_router
from app.core.deps import CurrentUser
from app.core.errors import Forbidden
from app.masters import models as m
from app.masters import schemas as s

BANK_FIELDS = ("bank_name", "bank_account_no", "bank_ifsc")


def _mask_account(v: str | None) -> str | None:
    return None if not v else "•" * max(len(v) - 4, 0) + v[-4:]


def supplier_read(data: dict, cu: CurrentUser) -> dict:
    """Bank details are only visible with masters.supplier_bank:read; even then the account number is masked
    unless the user can also update bank details."""
    if not cu.can("masters.supplier_bank:read"):
        for f in BANK_FIELDS:
            data[f] = None
    elif not cu.can("masters.supplier_bank:update"):
        data["bank_account_no"] = _mask_account(data["bank_account_no"])
    return data


def supplier_write(data: dict, cu: CurrentUser, creating: bool) -> None:
    # On update, even explicitly clearing a bank field counts as touching it.
    touched = any(data.get(f) is not None for f in BANK_FIELDS) if creating else any(f in data for f in BANK_FIELDS)
    if touched and not cu.can("masters.supplier_bank:update"):
        raise Forbidden("Missing permission: masters.supplier_bank:update")


router = APIRouter(prefix="/masters")

RESOURCES = [
    ("units-of-measure", Resource("uom", m.UnitOfMeasure, "masters.uom", s.UomIn, s.UomUpdate, s.UomOut,
                                  ["code", "name"], default_sort="code")),
    ("categories", Resource("category", m.ItemCategory, "masters.category", s.CategoryIn, s.CategoryUpdate,
                            s.CategoryOut, ["code", "name"], ["kind"], default_sort="code")),
    ("items", Resource("item", m.Item, "masters.item", s.ItemIn, s.ItemUpdate, s.ItemOut,
                       ["item_code", "name", "hsn_code"], ["kind", "category_id"], default_sort="item_code",
                       exportable=True)),
    ("suppliers", Resource("supplier", m.Supplier, "masters.supplier", s.SupplierIn, s.SupplierUpdate, s.SupplierOut,
                           ["code", "name", "gstin", "city"], ["category", "approval_status"], default_sort="code",
                           exportable=True, read_transform=supplier_read, write_guard=supplier_write)),
    ("customers", Resource("customer", m.Customer, "masters.customer", s.CustomerIn, s.CustomerUpdate, s.CustomerOut,
                           ["code", "name", "gstin"], ["customer_type"], default_sort="code", exportable=True)),
    ("brands", Resource("brand", m.Brand, "masters.brand", s.BrandIn, s.BrandUpdate, s.BrandOut,
                        ["code", "name"], default_sort="code")),
    ("warehouses", Resource("warehouse", m.Warehouse, "masters.warehouse", s.WarehouseIn, s.WarehouseUpdate,
                            s.WarehouseOut, ["code", "name"], ["kind"], default_sort="code")),
    ("unit-conversions", Resource("unit_conversion", m.UnitConversion, "masters.uom", s.ConversionIn, s.ConversionUpdate, s.ConversionOut,
                                  [], ["from_uom_id", "to_uom_id", "item_id"], has_active_flag=True)),
    ("tax-classes", Resource("tax_class", m.TaxClass, "masters.tax_class", s.TaxClassIn, s.TaxClassUpdate, s.TaxClassOut,
                             ["code", "name", "hsn_prefix"], default_sort="code")),
    ("payment-methods", Resource("payment_method", m.PaymentMethod, "masters.payment_method", s.PaymentMethodIn, s.PaymentMethodUpdate,
                                 s.PaymentMethodOut, ["code", "name"], ["kind"], default_sort="code")),
    ("courier-partners", Resource("courier_partner", m.CourierPartner, "masters.courier", s.CourierIn, s.CourierUpdate, s.CourierOut,
                                  ["code", "name"], ["kind", "integration_mode"], default_sort="code")),
    ("locations", Resource("location", m.StorageLocation, "masters.location", s.LocationIn, s.LocationUpdate,
                           s.LocationOut, ["code", "name"], ["warehouse_id"], default_sort="code")),
]

for path, res in RESOURCES:
    router.include_router(build_router(res, prefix=f"/{path}", tags=["masters"]))


@router.get("/convert")
def convert_qty(qty: Decimal, from_uom_id: int, to_uom_id: int, item_id: int | None = None, db: Session = Depends(get_db),
                _: CurrentUser = Depends(require("masters.uom:read"))):
    """Quantity conversion using the configured rules (exact Decimal result, returned as a string)."""
    f = conversion_factor(db, from_uom_id, to_uom_id, item_id)
    return {"qty": str(qty), "from_uom_id": from_uom_id, "to_uom_id": to_uom_id, "item_id": item_id, "factor": str(f), "result": str(qty * f)}
