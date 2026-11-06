from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.production import service as svc
from app.production.models import Bom, ProductionOrder

router = APIRouter(prefix="/production", tags=["production"])
DB = Depends(get_db, scope="function")
Q = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=4)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BomFromFormulation(_In):
    formulation_version_id: int


class BomLineIn(_In):
    item_id: int
    qty: Q


class BomManual(_In):
    output_item_id: int
    output_qty: Q
    lines: list[BomLineIn] = Field(min_length=1, max_length=100)


class OrderIn(_In):
    bom_id: int
    planned_qty: Q
    warehouse_id: int
    planned_date: date | None = None
    stages: list[str] | None = Field(None, max_length=12)


class Version(_In):
    expected_version: int = Field(ge=1)


class StageIn(_In):
    equipment_id: int | None = None
    parameters: dict | None = None
    notes: str | None = Field(None, max_length=500)


class Wastage(_In):
    category: str = Field(min_length=2, max_length=30)
    qty: Q
    reason: str = Field(min_length=3)
    department: str | None = None


class CompleteIn(_In):
    expected_version: int = Field(ge=1)
    actual_qty: Q
    wastage: list[Wastage] = []


class Reason(_In):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/boms", status_code=201)
def bom_from_formulation(body: BomFromFormulation, db: Session = DB, cu: CurrentUser = Depends(require("production.bom:create"))):
    return run_tx(db, lambda d: svc.bom_from_formulation(d, cu, body.formulation_version_id), name="production.bom.from_formulation")


@router.post("/boms/manual", status_code=201)
def bom_manual(body: BomManual, db: Session = DB, cu: CurrentUser = Depends(require("production.bom:create"))):
    return run_tx(db, lambda d: svc.bom_manual(d, cu, body.output_item_id, body.output_qty, [l.model_dump() for l in body.lines]), name="production.bom.manual")


@router.post("/boms/{bom_id}/approve")
def bom_approve(bom_id: int, db: Session = DB, cu: CurrentUser = Depends(require("production.bom:approve"))):
    return run_tx(db, lambda d: svc.bom_approve(d, cu, bom_id), name="production.bom.approve")


@router.get("/boms/{bom_id}")
def get_bom(bom_id: int, db: Session = DB, _: CurrentUser = Depends(require("production.bom:read"))):
    b = db.get(Bom, bom_id)
    if not b:
        raise NotFound("BOM")
    return svc.bom_dict(b)


@router.post("/orders", status_code=201)
def create_order(body: OrderIn, db: Session = DB, cu: CurrentUser = Depends(require("production.order:create"))):
    return run_tx(db, lambda d: svc.create_order(d, cu, bom_id=body.bom_id, planned_qty=body.planned_qty, warehouse_id=body.warehouse_id,
                                                 planned_date=body.planned_date, stages=body.stages), name="production.order.create")


@router.get("/orders")
def list_orders(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB,
                _: CurrentUser = Depends(require("production.order:read"))):
    q = select(ProductionOrder).order_by(ProductionOrder.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(ProductionOrder.status == status)
    return {"items": [svc.order_dict(o) for o in db.scalars(q).unique()], "page": page}


@router.get("/orders/{oid}")
def get_order(oid: int, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    o = db.get(ProductionOrder, oid)
    if not o:
        raise NotFound("Production order")
    return svc.order_dict(o)


@router.get("/orders/{oid}/availability")
def availability(oid: int, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    return svc.availability(db, oid)


@router.post("/orders/{oid}/release")
def release(oid: int, body: Version, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("production.order:release"))):
    return idempotent_post(db, cu, scope="production.order.release", key=key, payload={"id": oid, **body.model_dump()},
                           effect=lambda d: (200, svc.release_order(d, cu, oid, body.expected_version), "production_order", oid))


@router.post("/orders/{oid}/issue")
def issue(oid: int, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("production.order:issue"))):
    return idempotent_post(db, cu, scope="production.order.issue", key=key, payload={"id": oid},
                           effect=lambda d: (200, svc.issue_materials(d, cu, oid), "production_order", oid))


@router.post("/orders/{oid}/stages/{seq}/{action}")
def stage(oid: int, seq: int, action: str, body: StageIn, db: Session = DB, cu: CurrentUser = Depends(require("production.order:execute"))):
    if action not in ("start", "complete"):
        raise NotFound("Action")
    return run_tx(db, lambda d: svc.stage_action(d, cu, oid, seq, action, equipment_id=body.equipment_id, parameters=body.parameters, notes=body.notes), name=f"production.stage.{action}")


@router.post("/orders/{oid}/complete")
def complete(oid: int, body: CompleteIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("production.order:complete"))):
    return idempotent_post(db, cu, scope="production.order.complete", key=key, payload={"id": oid, **body.model_dump(mode="json")},
                           effect=lambda d: (200, svc.complete_order(d, cu, oid, body.expected_version, body.actual_qty, [w.model_dump() for w in body.wastage]), "production_order", oid))


@router.post("/orders/{oid}/cancel")
def cancel(oid: int, body: Reason, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("production.order:cancel"))):
    return idempotent_post(db, cu, scope="production.order.cancel", key=key, payload={"id": oid, **body.model_dump()},
                           effect=lambda d: (200, svc.cancel_order(d, cu, oid, body.reason), "production_order", oid))


@router.get("/batches/{batch_id}/genealogy")
def genealogy(batch_id: int, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    return svc.trace_back(db, batch_id)


@router.get("/batches/{batch_id}/where-used")
def where_used(batch_id: int, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    return svc.where_used(db, batch_id)
