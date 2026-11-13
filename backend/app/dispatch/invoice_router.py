"""Customer invoice drafts API (see invoice_drafts.py). Posting needs BOTH the invoice-post and the dispatch permission, because posting ships the goods."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.dispatch import invoice_drafts as svc
from app.dispatch.router import ShipLine

router = APIRouter(prefix="/dispatch", tags=["invoicing"])
DB = Depends(get_db, scope="function")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftIn(_In):
    lines: Annotated[list[ShipLine] | None, Field(min_length=1)] = None        # omit for every line still to ship


class PostIn(_In):
    courier: str | None = Field(None, max_length=30)


class CancelIn(_In):
    reason: str | None = Field(None, max_length=300)


@router.get("/orders/{oid}/invoice-eligibility")
def eligibility(oid: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.sales_invoice:read"))):
    o = svc._order(db, oid)
    return {"order_id": o.id, "order_number": o.number, "status": o.status, "lines": svc.eligible_lines(db, o), "can_draft": o.status in svc.CREATABLE_FROM}


@router.post("/orders/{oid}/invoice-drafts", status_code=201)
def create_draft(oid: int, body: DraftIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.sales_invoice:draft", "dispatch.order:read"))):
    return run_tx(db, lambda d: svc.create(d, cu, oid, [l.model_dump() for l in body.lines] if body.lines else None), name="invoicing.draft.create")


@router.get("/invoice-drafts")
def list_drafts(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB,
                _: CurrentUser = Depends(require("accounts.sales_invoice:read"))):
    return svc.listing(db, status, page, page_size)


@router.get("/invoice-drafts/{did}")
def get_draft(did: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.sales_invoice:read"))):
    return svc.draft_dict(db, svc._get(db, did))


@router.post("/invoice-drafts/{did}/validate")
def validate_draft(did: int, db: Session = DB, cu: CurrentUser = Depends(require("accounts.sales_invoice:draft"))):
    return run_tx(db, lambda d: svc.validate(d, cu, did), name="invoicing.draft.validate")


@router.post("/invoice-drafts/{did}/post")
def post_draft(did: int, body: PostIn, key: str = Depends(require_idempotency_key), db: Session = DB,
               cu: CurrentUser = Depends(require("accounts.sales_invoice:post", "dispatch.order:dispatch"))):
    return idempotent_post(db, cu, scope="invoicing.draft.post", key=key, payload={"id": did, **body.model_dump()},
                           effect=lambda d: (200, svc.post(d, cu, did, body.courier), "invoice_draft", did))


@router.post("/invoice-drafts/{did}/cancel")
def cancel_draft(did: int, body: CancelIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.sales_invoice:draft"))):
    return run_tx(db, lambda d: svc.cancel(d, cu, did, body.reason), name="invoicing.draft.cancel")
