from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.inventory import extras as svc

router = APIRouter(prefix="/inventory", tags=["inventory"])
DB = Depends(get_db, scope="function")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartIn(_In):
    warehouse_id: int
    note: str | None = Field(default=None, max_length=300)


class Count(_In):
    balance_id: int
    counted_qty: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)]
    note: str | None = None


class CountsIn(_In):
    counts: list[Count] = Field(min_length=1, max_length=2000)


@router.get("/reports/reorder")
def reorder(db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read"))):
    return svc.reorder_report(db)


@router.get("/reports/expiring")
def expiring(days: int | None = Query(None, ge=0, le=730), db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read"))):
    return svc.expiring(db, days)


@router.get("/reports/valuation")
def valuation(db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read", "accounts.report:read"))):
    return svc.valuation(db)


@router.get("/reports/slow-moving")
def slow(days: int = Query(90, ge=7, le=730), db: Session = DB, _: CurrentUser = Depends(require("inventory.stock:read"))):
    return svc.slow_moving(db, days)


@router.post("/stocktakes", status_code=201)
def start(body: StartIn, db: Session = DB, cu: CurrentUser = Depends(require("inventory.stocktake:count"))):
    return run_tx(db, lambda d: svc.start_stocktake(d, cu, body.warehouse_id, body.note), name="inventory.stocktake.start")


@router.get("/stocktakes/{sid}")
def get(sid: int, db: Session = DB, _: CurrentUser = Depends(require("inventory.stocktake:count"))):
    return svc.stocktake_dict(db, svc._st(db, sid))


@router.post("/stocktakes/{sid}/counts")
def counts(sid: int, body: CountsIn, db: Session = DB, cu: CurrentUser = Depends(require("inventory.stocktake:count"))):
    return run_tx(db, lambda d: svc.record_counts(d, cu, sid, [c.model_dump() for c in body.counts]), name="inventory.stocktake.count")


@router.post("/stocktakes/{sid}/approve")
def approve(sid: int, db: Session = DB, cu: CurrentUser = Depends(require("inventory.stocktake:approve", "inventory.adjustment:post"))):
    return run_tx(db, lambda d: svc.approve_stocktake(d, cu, sid), name="inventory.stocktake.approve")


@router.post("/stocktakes/{sid}/cancel")
def cancel(sid: int, db: Session = DB, cu: CurrentUser = Depends(require("inventory.stocktake:count"))):
    return run_tx(db, lambda d: svc.cancel_stocktake(d, cu, sid), name="inventory.stocktake.cancel")
