from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.sales import service as svc
from app.sales.models import CustomerContact, CustomerPrice, FollowUp, SalesQuotation

router = APIRouter(prefix="/sales", tags=["sales"])
DB = Depends(get_db, scope="function")
Qty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContactIn(_In):
    name: str = Field(min_length=1, max_length=120)
    role: str | None = None
    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    is_primary: bool = False


class PriceIn(_In):
    customer_id: int
    item_id: int
    price: Money
    valid_from: date
    valid_to: date | None = None


class FollowUpIn(_In):
    subject: str = Field(min_length=3, max_length=200)
    due_date: date
    lead_id: int | None = None
    customer_id: int | None = None
    assigned_to: int
    notes: str | None = None


class FollowUpClose(_In):
    outcome: str | None = None
    cancel: bool = False


class QLine(_In):
    item_id: int
    qty: Qty
    unit_price: Money | None = None
    tax_rate: Annotated[Decimal, Field(ge=0, le=100)] | None = None


class QuotationIn(_In):
    customer_id: int
    valid_until: date
    notes: str | None = None
    lines: list[QLine] = Field(min_length=1, max_length=100)


class QAction(_In):
    expected_version: int = Field(ge=1)
    warehouse_id: int | None = None


@router.post("/customers/{cid}/contacts", status_code=201)
def add_contact(cid: int, body: ContactIn, db: Session = DB, cu: CurrentUser = Depends(require("sales.contact:create"))):
    return run_tx(db, lambda d: svc.add_contact(d, cu, cid, body.model_dump()), name="sales.contact.create")


@router.get("/customers/{cid}/contacts")
def contacts(cid: int, db: Session = DB, _: CurrentUser = Depends(require("sales.contact:read"))):
    return [svc.contact_dict(c) for c in db.scalars(select(CustomerContact).where(CustomerContact.customer_id == cid).order_by(CustomerContact.id))]


@router.get("/customers/{cid}/history")
def history(cid: int, db: Session = DB, _: CurrentUser = Depends(require("sales.pipeline:read"))):
    return svc.customer_history(db, cid)


@router.post("/prices", status_code=201)
def set_price(body: PriceIn, db: Session = DB, cu: CurrentUser = Depends(require("sales.pricing:update"))):
    return run_tx(db, lambda d: svc.set_price(d, cu, **body.model_dump()), name="sales.price.set")


@router.get("/prices")
def prices(customer_id: int | None = None, item_id: int | None = None, db: Session = DB, _: CurrentUser = Depends(require("sales.pricing:read"))):
    q = select(CustomerPrice).order_by(CustomerPrice.customer_id, CustomerPrice.item_id, CustomerPrice.valid_from.desc()).limit(500)
    if customer_id:
        q = q.where(CustomerPrice.customer_id == customer_id)
    if item_id:
        q = q.where(CustomerPrice.item_id == item_id)
    return [svc.price_dict(p) for p in db.scalars(q)]


@router.get("/prices/resolve")
def resolve(customer_id: int, item_id: int, on: date | None = None, db: Session = DB, _: CurrentUser = Depends(require("sales.pricing:read"))):
    p = svc.resolve_price(db, customer_id, item_id, on)
    return {"customer_id": customer_id, "item_id": item_id, "price": None if p is None else str(p), "source": "CUSTOMER_LIST" if p is not None else None}


@router.post("/followups", status_code=201)
def add_followup(body: FollowUpIn, db: Session = DB, cu: CurrentUser = Depends(require("sales.followup:create"))):
    return run_tx(db, lambda d: svc.add_followup(d, cu, body.model_dump()), name="sales.followup.create")


@router.get("/followups")
def followups(mine: bool = True, status: str | None = "OPEN", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), db: Session = DB,
              cu: CurrentUser = Depends(require("sales.followup:read"))):
    q = select(FollowUp).order_by(FollowUp.due_date, FollowUp.id).limit(page_size).offset((page - 1) * page_size)
    if mine:
        q = q.where(FollowUp.assigned_to == cu.id)
    if status:
        q = q.where(FollowUp.status == status)
    return {"items": [svc.followup_dict(f) for f in db.scalars(q)], "page": page}


@router.post("/followups/{fid}/close")
def close_followup(fid: int, body: FollowUpClose, db: Session = DB, cu: CurrentUser = Depends(require("sales.followup:update"))):
    return run_tx(db, lambda d: svc.complete_followup(d, cu, fid, body.outcome, body.cancel), name="sales.followup.close")


@router.post("/quotations", status_code=201)
def create_quotation(body: QuotationIn, db: Session = DB, cu: CurrentUser = Depends(require("sales.quotation:create"))):
    return run_tx(db, lambda d: svc.create_quotation(d, cu, customer_id=body.customer_id, valid_until=body.valid_until, notes=body.notes, lines=[l.model_dump() for l in body.lines]), name="sales.quotation.create")


@router.get("/quotations")
def quotations(status: str | None = None, customer_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB,
               _: CurrentUser = Depends(require("sales.quotation:read"))):
    q = select(SalesQuotation).order_by(SalesQuotation.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(SalesQuotation.status == status)
    if customer_id:
        q = q.where(SalesQuotation.customer_id == customer_id)
    return {"items": [svc.quotation_dict(x) for x in db.scalars(q)], "page": page}


@router.get("/quotations/{qid}")
def get_quotation(qid: int, db: Session = DB, _: CurrentUser = Depends(require("sales.quotation:read"))):
    return svc.quotation_dict(svc._q(db, qid))


def _action(name: str, perm: str):
    @router.post(f"/quotations/{{qid}}/{name}", name=f"quotation_{name}")
    def act(qid: int, body: QAction, db: Session = DB, cu: CurrentUser = Depends(require(perm, *(["dispatch.order:create"] if name == "convert" else [])))):
        return run_tx(db, lambda d: svc.quotation_action(d, cu, qid, name, expected_version=body.expected_version, warehouse_id=body.warehouse_id), name=f"sales.quotation.{name}")
    return act


for _n, _p in (("send", "sales.quotation:send"), ("accept", "sales.quotation:accept"), ("reject", "sales.quotation:accept"), ("convert", "sales.quotation:convert")):
    _action(_n, _p)


@router.get("/pipeline")
def pipeline(db: Session = DB, _: CurrentUser = Depends(require("sales.pipeline:read"))):
    return svc.pipeline(db)


@router.get("/quotations/{qid}/pdf")
def quotation_pdf_view(qid: int, db: Session = DB, _: CurrentUser = Depends(require("sales.quotation:read"))):
    """Printable document with the Mintree logo; amounts are recomputed from the stored lines and the PDF is refused if they disagree with the stored total."""
    from fastapi.responses import Response
    from app.core.print_docs import quotation_pdf
    number, pdf = quotation_pdf(db, qid)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{number.replace("/", "-")}.pdf"'})
