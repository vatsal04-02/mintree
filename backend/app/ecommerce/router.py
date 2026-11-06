import json
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.ecommerce import service as svc
from app.ecommerce.models import ExternalOrder, MarketplaceAccount

router = APIRouter(prefix="/ecommerce", tags=["ecommerce"])
DB = Depends(get_db, scope="function")
MAX_IMPORT_BYTES = 5_000_000


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountIn(_In):
    platform: Literal["AMAZON", "FLIPKART", "MYNTRA", "NYKAA", "FILE"]
    name: str = Field(min_length=2, max_length=120)
    seller_id: str | None = None
    customer_id: int
    warehouse_id: int
    credentials_ref: str | None = Field(default=None, max_length=120, description="Reference to a secret-store entry; never the secret itself")


class MappingIn(_In):
    platform_sku: str = Field(min_length=1, max_length=80)
    item_id: int
    pack_multiplier: Decimal = Field(default=Decimal(1), gt=0)
    safety_stock: Decimal = Field(default=Decimal(0), ge=0)


class ItemIn(_In):
    sku: str
    qty: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class OrderIn(_In):
    order_id: str = Field(min_length=1, max_length=80)
    order_date: date | None = None
    customer_name: str | None = None
    shipping_address: str | None = None
    items: list[ItemIn] = Field(min_length=1)


class SettlementIn(_In):
    reference: str = Field(min_length=1, max_length=80)
    period_start: date
    period_end: date
    gross_sales: Decimal = Field(ge=0)
    commission: Decimal = Field(default=Decimal(0), ge=0)
    shipping_fees: Decimal = Field(default=Decimal(0), ge=0)
    ad_fees: Decimal = Field(default=Decimal(0), ge=0)
    returns_refunds: Decimal = Field(default=Decimal(0), ge=0)
    other_deductions: Decimal = Field(default=Decimal(0), ge=0)


class ReceivedIn(_In):
    received_amount: Decimal = Field(ge=0)


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.account:create"))):
    return run_tx(db, lambda d: svc.create_account(d, cu, body.model_dump()), name="ecommerce.account.create")


@router.get("/accounts")
def accounts(db: Session = DB, _: CurrentUser = Depends(require("ecommerce.account:read"))):
    return [svc.account_dict(a) for a in db.scalars(select(MarketplaceAccount).order_by(MarketplaceAccount.id))]


@router.put("/accounts/{aid}/mappings")
def put_mapping(aid: int, body: MappingIn, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.mapping:update"))):
    return run_tx(db, lambda d: svc.upsert_mapping(d, cu, aid, body.platform_sku, body.item_id, body.pack_multiplier, body.safety_stock), name="ecommerce.mapping")


class OrdersIn(_In):
    orders: list[OrderIn] = Field(min_length=1, max_length=500)


@router.post("/accounts/{aid}/orders")
def import_orders(aid: int, body: OrdersIn, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.order:ingest"))):
    orders = [_order_dict(o) for o in body.orders]
    return run_tx(db, lambda d: svc.ingest_orders(d, cu, aid, orders), name="ecommerce.orders.import")


def _order_dict(o: OrderIn) -> dict:
    items = [{"sku": i.sku, "qty": i.qty, "unit_price": i.unit_price} for i in o.items]
    return {"order_id": o.order_id, "order_date": o.order_date, "customer_name": o.customer_name, "shipping_address": o.shipping_address,
            "items": items, "total": sum(i["qty"] * i["unit_price"] for i in items)}


@router.post("/accounts/{aid}/orders/import-csv")
async def import_csv(aid: int, file: UploadFile = File(...), db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.order:ingest"))):
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "Import file too large (5 MB max)")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "Import file must be UTF-8")
    orders = svc.parse_csv(content)
    return await run_in_threadpool(lambda: run_tx(db, lambda d: svc.ingest_orders(d, cu, aid, orders), name="ecommerce.orders.import_csv"))


@router.post("/accounts/{aid}/webhook")
async def order_webhook(aid: int, request: Request, db: Session = DB, x_signature: str | None = Header(default=None)):
    """Signed order-created webhook. Body: {event_id, order: {order_id, order_date, items:[{sku, qty, unit_price}], ...}}. Re-delivery is a no-op."""
    raw = await request.body()
    if not svc.verify_signature(raw, x_signature):
        raise HTTPException(401, "Invalid signature")
    try:
        ev = json.loads(raw)
        order = _order_dict(OrderIn.model_validate(ev["order"]))
        event_id = str(ev["event_id"])
        assert event_id
    except Exception:
        raise HTTPException(422, "Malformed webhook payload")

    def work(d: Session):
        return svc.handle_order_webhook(d, svc.SystemActor(), aid, {"event_id": event_id, "order": order})
    return await run_in_threadpool(lambda: run_tx(db, work, name="ecommerce.webhook"))


@router.get("/external-orders")
def external_orders(status: str | None = None, account_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                    db: Session = DB, _: CurrentUser = Depends(require("ecommerce.order:read"))):
    q = select(ExternalOrder).order_by(ExternalOrder.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(ExternalOrder.status == status)
    if account_id:
        q = q.where(ExternalOrder.account_id == account_id)
    return {"items": [svc.order_dict(e) for e in db.scalars(q)], "page": page}


@router.post("/external-orders/{eid}/retry")
def retry(eid: int, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.order:ingest"))):
    return run_tx(db, lambda d: svc.retry_exception(d, cu, eid), name="ecommerce.retry")


@router.get("/accounts/{aid}/inventory-sync-preview")
def preview(aid: int, db: Session = DB, _: CurrentUser = Depends(require("ecommerce.mapping:read"))):
    return svc.inventory_sync_preview(db, aid)


@router.post("/accounts/{aid}/settlements", status_code=201)
def settlement(aid: int, body: SettlementIn, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.settlement:create"))):
    return run_tx(db, lambda d: svc.record_settlement(d, cu, aid, body.model_dump()), name="ecommerce.settlement.create")


@router.post("/settlements/{sid}/received")
def received(sid: int, body: ReceivedIn, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.settlement:create"))):
    return run_tx(db, lambda d: svc.set_received(d, cu, sid, body.received_amount), name="ecommerce.settlement.received")


class ReturnLine(_In):
    sku: str
    qty: Decimal = Field(gt=0)


class ReturnIn(_In):
    account_id: int
    platform_return_ref: str = Field(min_length=1, max_length=80)
    external_order_id: str
    reason: str = Field(min_length=3)
    lines: list[ReturnLine] = Field(min_length=1, max_length=100)


class DisputeIn(_In):
    status: Literal["OPEN", "WON", "LOST"]
    note: str | None = None


@router.get("/accounts/{aid}/health")
def health(aid: int, db: Session = DB, _: CurrentUser = Depends(require("ecommerce.account:read"))):
    return svc.health(db, aid)


@router.get("/integration-logs")
def integration_logs(account_id: int | None = None, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = DB,
                     _: CurrentUser = Depends(require("ecommerce.account:read"))):
    from sqlalchemy import text
    rows = db.execute(text("""SELECT id, account_id, kind, status, message, counts, created_at FROM integration_logs
                               WHERE (CAST(:a AS bigint) IS NULL OR account_id = :a) AND (CAST(:s AS text) IS NULL OR status = :s) ORDER BY id DESC LIMIT :n OFFSET :o"""),
                      {"a": account_id, "s": status, "n": page_size, "o": (page - 1) * page_size}).mappings().all()
    return {"items": [dict(r) for r in rows], "page": page}


@router.post("/returns", status_code=201)
def create_return(body: ReturnIn, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.order:ingest", "dispatch.return:create"))):
    return run_tx(db, lambda d: svc.create_return(d, cu, account_id=body.account_id, platform_return_ref=body.platform_return_ref, external_order_id=body.external_order_id, reason=body.reason,
                                                  lines=[l.model_dump() for l in body.lines]), name="ecommerce.return.create")


@router.post("/returns/{rid}/dispute")
def dispute(rid: int, body: DisputeIn, db: Session = DB, cu: CurrentUser = Depends(require("ecommerce.order:ingest"))):
    return run_tx(db, lambda d: svc.set_dispute(d, cu, rid, body.status, body.note), name="ecommerce.return.dispute")


@router.get("/analytics")
def analytics(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("ecommerce.order:read"))):
    return svc.analytics(db, start, end)
