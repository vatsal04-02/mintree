from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.procurement import rfq as svc
from app.procurement.rfq_models import Rfq

router = APIRouter(prefix="/procurement", tags=["procurement"])
DB = Depends(get_db, scope="function")
Qty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RfqLine(_In):
    item_id: int
    qty: Qty


class RfqCreate(_In):
    items: list[RfqLine] = Field(min_length=1, max_length=200)
    supplier_ids: list[int] = Field(min_length=1, max_length=20)
    due_date: date | None = None
    notes: str | None = Field(None, max_length=1000)
    pr_id: int | None = None


class QuoteLine(_In):
    item_id: int
    unit_price: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] | None = None


class QuoteIn(_In):
    supplier_id: int
    lines: list[QuoteLine] = Field(min_length=1, max_length=200)
    lead_time_days: int | None = Field(None, ge=0, le=730)
    valid_until: date | None = None
    payment_terms_days: int | None = Field(None, ge=0, le=365)
    freight: Money = Decimal(0)
    notes: str | None = Field(None, max_length=1000)


class AwardIn(_In):
    quotation_id: int
    reason: str | None = Field(None, max_length=500)


@router.post("/rfqs", status_code=201)
def create(body: RfqCreate, db: Session = DB, cu: CurrentUser = Depends(require("procurement.rfq:create"))):
    return run_tx(db, lambda d: svc.create_rfq(d, cu, items=[i.model_dump() for i in body.items], supplier_ids=body.supplier_ids, due_date=body.due_date, notes=body.notes, pr_id=body.pr_id), name="procurement.rfq.create")


@router.get("/rfqs")
def listing(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB, _: CurrentUser = Depends(require("procurement.rfq:read"))):
    q = select(Rfq).order_by(Rfq.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(Rfq.status == status)
    return {"items": [svc.rfq_dict(r) for r in db.scalars(q)], "page": page}


@router.get("/rfqs/{rid}")
def get(rid: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.rfq:read"))):
    return svc.rfq_dict(svc._rfq(db, rid))


@router.post("/rfqs/{rid}/send")
def send(rid: int, db: Session = DB, cu: CurrentUser = Depends(require("procurement.rfq:send"))):
    return run_tx(db, lambda d: svc.send_rfq(d, cu, rid), name="procurement.rfq.send")


@router.post("/rfqs/{rid}/quotations", status_code=201)
def quote(rid: int, body: QuoteIn, db: Session = DB, cu: CurrentUser = Depends(require("procurement.rfq:quote"))):
    return run_tx(db, lambda d: svc.record_quotation(d, cu, rid, supplier_id=body.supplier_id, lines=[l.model_dump() for l in body.lines], lead_time_days=body.lead_time_days,
                                                     valid_until=body.valid_until, payment_terms_days=body.payment_terms_days, freight=body.freight, notes=body.notes), name="procurement.rfq.quote")


@router.get("/rfqs/{rid}/comparison")
def comparison(rid: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.rfq:read"))):
    return svc.compare(db, rid)


@router.post("/rfqs/{rid}/award")
def award(rid: int, body: AwardIn, db: Session = DB, cu: CurrentUser = Depends(require("procurement.rfq:award", "procurement.po:create"))):
    return run_tx(db, lambda d: svc.award(d, cu, rid, body.quotation_id, body.reason), name="procurement.rfq.award")


@router.post("/rfqs/{rid}/cancel")
def cancel(rid: int, db: Session = DB, cu: CurrentUser = Depends(require("procurement.rfq:send"))):
    return run_tx(db, lambda d: svc.cancel(d, cu, rid), name="procurement.rfq.cancel")


@router.get("/delivery-tracking")
def tracking(supplier_id: int | None = None, db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    return svc.delivery_tracking(db, supplier_id)


@router.get("/supplier-performance")
def performance(db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    return svc.supplier_performance(db)
