from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.production import extras as svc

router = APIRouter(prefix="/production", tags=["production"])
DB = Depends(get_db, scope="function")
Num = Annotated[Decimal, Field(max_digits=18, decimal_places=4)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckIn(_In):
    parameter: str = Field(min_length=1, max_length=80)
    value: Num | None = None
    min_value: Num | None = None
    max_value: Num | None = None
    passed: bool | None = None
    observation: str | None = None
    corrective_action: str | None = None


class PlanLine(_In):
    bom_id: int
    qty: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]


class PlanIn(_In):
    warehouse_id: int
    lines: list[PlanLine] = Field(min_length=1, max_length=50)
    raise_purchase_request: bool = False
    required_by: date | None = None


@router.post("/orders/{oid}/stages/{seq}/checks", status_code=201)
def add_check(oid: int, seq: int, body: CheckIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.inspection:record"))):
    return run_tx(db, lambda d: svc.record_check(d, cu, oid, seq, **body.model_dump()), name="production.process_check")


@router.get("/orders/{oid}/checks")
def list_checks(oid: int, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    return svc.checks(db, oid)


@router.get("/dashboard")
def dashboard(db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    return svc.dashboard(db)


@router.get("/batches/{batch_id}/record")
def batch_record(batch_id: int, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    return svc.batch_record(db, batch_id)


@router.get("/bom-comparison")
def compare(a: int, b: int, db: Session = DB, _: CurrentUser = Depends(require("production.bom:read"))):
    return svc.compare_boms(db, a, b)


@router.get("/reports/wastage")
def wastage(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("production.order:read"))):
    return svc.wastage_report(db, start, end)


@router.post("/plan")
def plan(body: PlanIn, db: Session = DB, cu: CurrentUser = Depends(require("production.order:read"))):
    if body.raise_purchase_request and "procurement.pr:create" not in cu.permissions:
        from app.core.errors import Forbidden
        raise Forbidden("Missing permission: procurement.pr:create")
    return run_tx(db, lambda d: svc.plan(d, cu, warehouse_id=body.warehouse_id, lines=[l.model_dump() for l in body.lines], raise_pr=body.raise_purchase_request, required_by=body.required_by),
                  name="production.plan")
