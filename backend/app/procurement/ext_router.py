from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.procurement import ext_service as svc
from app.procurement.ext_models import PoAmendment, PurchaseReturn

router = APIRouter(prefix="/procurement", tags=["procurement"])
DB = Depends(get_db, scope="function")
Qty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]
Price = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class ReturnLine(BaseModel):
    gr_item_id: int
    qty: Qty


class ReturnIn(BaseModel):
    reason_type: Literal["QC_REJECTED", "DAMAGED", "WRONG_ITEM", "EXCESS", "OTHER"]
    reason: str = Field(min_length=3, max_length=500)
    lines: list[ReturnLine] = Field(min_length=1)


class AmendLine(BaseModel):
    po_item_id: int
    new_qty: Qty | None = None
    new_unit_price: Price | None = None


class AmendIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    lines: list[AmendLine] = Field(min_length=1)


class DecisionIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


@router.post("/goods-receipts/{gr_id}/returns", status_code=201)
def post_return(gr_id: int, body: ReturnIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("procurement.return:post"))):
    def effect(d):
        r = svc.post_purchase_return(d, cu, goods_receipt_id=gr_id, reason_type=body.reason_type, reason=body.reason, lines=[l.model_dump() for l in body.lines])
        return 201, r, "purchase_return", r["id"]
    return idempotent_post(db, cu, scope="procurement.purchase_return.post", key=key, payload={"gr_id": gr_id, **body.model_dump(mode="json")}, effect=effect)


@router.get("/purchase-returns")
def list_returns(po_id: int | None = None, goods_receipt_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                 db: Session = DB, _: CurrentUser = Depends(require("procurement.return:read"))):
    stmt = select(PurchaseReturn)
    if po_id:
        stmt = stmt.where(PurchaseReturn.po_id == po_id)
    if goods_receipt_id:
        stmt = stmt.where(PurchaseReturn.goods_receipt_id == goods_receipt_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(PurchaseReturn.id.desc()).limit(page_size).offset((page - 1) * page_size)).unique().all()
    return {"items": [svc.return_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/purchase-returns/{rid}")
def get_return(rid: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.return:read"))):
    r = db.get(PurchaseReturn, rid)
    if not r:
        raise NotFound("Purchase return")
    return svc.return_dict(r)


@router.post("/purchase-orders/{po_id}/amendments", status_code=201)
def request_amendment(po_id: int, body: AmendIn, db: Session = DB, cu: CurrentUser = Depends(require("procurement.po:update"))):
    return run_tx(db, lambda d: svc.request_amendment(d, cu, po_id=po_id, reason=body.reason, lines=[l.model_dump() for l in body.lines]), name="procurement.po.amend.request")


@router.get("/purchase-orders/{po_id}/amendments")
def list_amendments(po_id: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    rows = db.scalars(select(PoAmendment).where(PoAmendment.po_id == po_id).order_by(PoAmendment.amendment_no)).unique().all()
    return {"items": [svc.amendment_dict(a) for a in rows]}


@router.post("/po-amendments/{aid}/{decision}")
def decide(aid: int, decision: Literal["approve", "reject"], body: DecisionIn, db: Session = DB, cu: CurrentUser = Depends(require("procurement.po:approve"))):
    return run_tx(db, lambda d: svc.decide_amendment(d, cu, amendment_id=aid, approve=decision == "approve", reason=body.reason), name=f"procurement.po.amend.{decision}")
