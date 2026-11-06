from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.barcodes import service as svc
from app.core.db import get_db
from app.core.deps import CurrentUser, current_user
from app.core.errors import Forbidden

router = APIRouter(tags=["barcodes"])
DB = Depends(get_db, scope="function")

LABEL_PERMS = {"batch": "inventory.batch:read", "item": "masters.item:read", "location": "masters.location:read", "equipment": "maintenance.equipment:read", "shipment": "dispatch.shipment:read",
               "order": "dispatch.order:read", "production-order": "production.order:read", "work-order": "maintenance.workorder:read", "goods-receipt": "procurement.receipt:read"}


@router.get("/scan/resolve")
def resolve(code: str = Query(min_length=1, max_length=120), db: Session = DB, cu: CurrentUser = Depends(current_user)):
    """Identify whatever a scanner (camera or keyboard-wedge) read: a Mintree label (MT:KIND:id) or any business code already on documents."""
    return svc.resolve(db, cu, code)


@router.get("/labels/{kind}/{id_}")
def label(kind: Literal["batch", "item", "location", "equipment", "shipment", "order", "production-order", "work-order", "goods-receipt"], id_: int, copies: int = Query(1, ge=1, le=100),
          db: Session = DB, cu: CurrentUser = Depends(current_user)):
    if not cu.can(LABEL_PERMS[kind]):
        raise Forbidden(f"Missing permission: {LABEL_PERMS[kind]}")
    name, pdf = svc.labels_pdf(db, kind, id_, copies)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="label-{name}.pdf"'})
