import json
from decimal import Decimal
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.dispatch import service as svc
from app.dispatch.models import SalesInvoice, SalesOrder, SalesReturn, Shipment

router = APIRouter(prefix="/dispatch", tags=["dispatch"])
DB = Depends(get_db, scope="function")
Qty = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]
Money = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SOLine(_In):
    item_id: int
    qty: Qty
    unit_price: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] | None = None


class SOIn(_In):
    customer_id: int
    warehouse_id: int
    channel: Literal["B2B", "DISTRIBUTOR", "BRAND", "MARKETPLACE", "MANUAL"] = "B2B"
    external_ref: str | None = Field(None, max_length=80)
    shipping_address: str | None = None
    lines: list[SOLine] = Field(min_length=1, max_length=200)


class Version(_In):
    expected_version: int = Field(ge=1)


class PackIn(_In):
    expected_version: int = Field(ge=1)
    cartons: int = Field(ge=1, le=10000)
    weight_kg: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=3)]


class DispatchIn(_In):
    expected_version: int = Field(ge=1)
    courier: str | None = Field(None, max_length=30)


class Reason(_In):
    reason: str = Field(min_length=3, max_length=300)


class RetLine(_In):
    shipment_line_id: int
    qty: Qty


class ReturnIn(_In):
    reason: str = Field(min_length=3, max_length=300)
    lines: list[RetLine] = Field(min_length=1, max_length=100)


class Decision(_In):
    decision: Literal["RESTOCK", "REJECT"]


@router.post("/orders", status_code=201)
def create(body: SOIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.order:create"))):
    return run_tx(db, lambda d: svc.create_order(d, cu, customer_id=body.customer_id, warehouse_id=body.warehouse_id, channel=body.channel, external_ref=body.external_ref,
                                                 shipping_address=body.shipping_address, lines=[l.model_dump() for l in body.lines]), name="dispatch.order.create")


@router.get("/orders")
def list_orders(status: str | None = None, customer_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB,
                _: CurrentUser = Depends(require("dispatch.order:read"))):
    q = select(SalesOrder).order_by(SalesOrder.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(SalesOrder.status == status)
    if customer_id:
        q = q.where(SalesOrder.customer_id == customer_id)
    return {"items": [svc.order_dict(o) for o in db.scalars(q).unique()], "page": page}


@router.get("/orders/{oid}")
def get_order(oid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.order:read"))):
    o = db.get(SalesOrder, oid)
    if not o:
        raise NotFound("Sales order")
    return svc.order_dict(o)


@router.post("/orders/{oid}/confirm")
def confirm(oid: int, body: Version, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.order:confirm"))):
    return run_tx(db, lambda d: svc.confirm_order(d, cu, oid, body.expected_version), name="dispatch.order.confirm")


@router.post("/orders/{oid}/allocate")
def allocate(oid: int, body: Version, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("dispatch.order:allocate"))):
    return idempotent_post(db, cu, scope="dispatch.order.allocate", key=key, payload={"id": oid, **body.model_dump()},
                           effect=lambda d: (200, svc.allocate_order(d, cu, oid, body.expected_version), "sales_order", oid))


@router.post("/orders/{oid}/pack")
def pack(oid: int, body: PackIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.order:pack"))):
    return run_tx(db, lambda d: svc.pack_order(d, cu, oid, body.expected_version, body.cartons, body.weight_kg), name="dispatch.order.pack")


@router.post("/orders/{oid}/dispatch")
def dispatch(oid: int, body: DispatchIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("dispatch.order:dispatch"))):
    return idempotent_post(db, cu, scope="dispatch.order.dispatch", key=key, payload={"id": oid, **body.model_dump()},
                           effect=lambda d: (200, svc.dispatch_order(d, cu, oid, body.expected_version, body.courier), "sales_order", oid))


@router.post("/orders/{oid}/cancel")
def cancel(oid: int, body: Reason, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("dispatch.order:cancel"))):
    return idempotent_post(db, cu, scope="dispatch.order.cancel", key=key, payload={"id": oid, **body.model_dump()},
                           effect=lambda d: (200, svc.cancel_order(d, cu, oid, body.reason), "sales_order", oid))


@router.get("/shipments/{sid}")
def shipment(sid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.shipment:read"))):
    s = db.get(Shipment, sid)
    if not s:
        raise NotFound("Shipment")
    return {"id": s.id, "so_id": s.so_id, "courier": s.courier, "status": s.status, "awb": s.awb, "tracking_url": s.tracking_url, "attempts": s.booking_attempts,
            "last_error": s.last_error, "lines": [{"id": l.id, "item_id": l.item_id, "batch_id": l.batch_id, "qty": str(l.qty), "unit_cost": str(l.unit_cost)} for l in s.lines]}


@router.post("/shipments/{sid}/book")
def book(sid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.shipment:book"))):
    from contextlib import nullcontext
    # The request session is reused (it commits between steps); the courier call itself happens with NO transaction open.
    return {"result": svc.book_shipment(sid, lambda: nullcontext(db))}


@router.get("/invoices/{iid}")
def invoice(iid: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.sales_invoice:read"))):
    i = db.get(SalesInvoice, iid)
    if not i:
        raise NotFound("Invoice")
    return {"id": i.id, "number": i.number, "customer_id": i.customer_id, "so_id": i.so_id, "subtotal": str(i.subtotal), "tax_total": str(i.tax_total), "total": str(i.total),
            "amount_paid": str(i.amount_paid), "credited_amount": str(i.credited_amount), "outstanding": str(i.total - i.amount_paid - i.credited_amount), "status": i.status}


@router.post("/webhooks/courier/{provider}")
async def courier_webhook(provider: str, request: Request, db: Session = DB, x_signature: str | None = Header(default=None)):
    """Public endpoint secured by an HMAC signature. Re-delivery of the same event_id is acknowledged without any second effect."""
    raw = await request.body()
    if not svc.verify_signature(raw, x_signature):
        raise HTTPException(401, "Invalid signature")
    try:
        ev = json.loads(raw)
        ev["occurred_at"] = datetime.fromisoformat(ev["occurred_at"])
        assert ev["event_id"] and ev["awb"] and ev["event_type"]
    except Exception:
        raise HTTPException(422, "Malformed webhook payload")
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(lambda: run_tx(db, lambda d: svc.handle_courier_event(d, provider, ev), name="dispatch.courier.webhook"))


@router.post("/returns", status_code=201)
def create_return(body: ReturnIn, order_id: int, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("dispatch.return:create"))):
    return idempotent_post(db, cu, scope="dispatch.return.create", key=key, payload={"order_id": order_id, **body.model_dump(mode="json")},
                           effect=lambda d: (lambda r: (201, r, "sales_return", r["id"]))(svc.create_return(d, cu, order_id=order_id, reason=body.reason, lines=[l.model_dump() for l in body.lines])))


@router.post("/returns/{rid}/receive")
def receive_return(rid: int, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("dispatch.return:receive"))):
    return idempotent_post(db, cu, scope="dispatch.return.receive", key=key, payload={"id": rid}, effect=lambda d: (200, svc.receive_return(d, cu, rid), "sales_return", rid))


@router.post("/returns/{rid}/decide")
def decide_return(rid: int, body: Decision, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("dispatch.return:decide"))):
    return idempotent_post(db, cu, scope="dispatch.return.decide", key=key, payload={"id": rid, **body.model_dump()},
                           effect=lambda d: (200, svc.decide_return(d, cu, rid, body.decision), "sales_return", rid))
