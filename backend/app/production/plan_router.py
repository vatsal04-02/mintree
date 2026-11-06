from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import Resource, build_router
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound, Unprocessable
from app.core.uow import run_tx
from app.masters.schemas import Code, Name, _Audited, _Base
from app.production import plan_service as svc
from app.production.models import ProductionOrder, ProductionStage
from app.production.plan_models import ProductionPlan, WorkCentre

router = APIRouter(prefix="/production", tags=["production"])
DB = Depends(get_db, scope="function")
Qty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]


class WcIn(_Base):
    code: Code
    name: Name
    kind: Literal["MIXING", "FILLING", "PACKING", "LAB", "STORAGE", "OTHER"]
    department_id: int | None = None
    capacity_per_day: Qty | None = None
    is_active: bool = True


class WcUpdate(_Base):
    name: Name | None = None
    capacity_per_day: Qty | None = None
    is_active: bool | None = None


class WcOut(_Audited, WcIn):
    pass


router.include_router(build_router(Resource("work_centre", WorkCentre, "production.work_centre", WcIn, WcUpdate, WcOut, ["code", "name"], ["kind"], default_sort="code"),
                                   prefix="/work-centres", tags=["production"]))


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlanLine(_In):
    bom_id: int
    planned_qty: Qty
    planned_date: date
    warehouse_id: int
    work_centre_id: int | None = None


class PlanIn(_In):
    name: str = Field(min_length=2, max_length=160)
    period_start: date
    period_end: date
    notes: str | None = None
    lines: list[PlanLine] = Field(min_length=1, max_length=200)


class ConfirmIn(_In):
    expected_version: int
    accept_overload_reason: str | None = Field(None, min_length=5, max_length=500)


class CloseIn(_In):
    cancel: bool = False
    reason: str | None = Field(None, max_length=500)


class StageWc(_In):
    work_centre_id: int


@router.post("/plans", status_code=201)
def create(body: PlanIn, db: Session = DB, cu: CurrentUser = Depends(require("production.plan:create"))):
    return run_tx(db, lambda d: svc.create_plan(d, cu, name=body.name, period_start=body.period_start, period_end=body.period_end, notes=body.notes,
                                                lines=[l.model_dump() for l in body.lines]), name="production.plan.create")


@router.get("/plans")
def plans(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB, _: CurrentUser = Depends(require("production.plan:read"))):
    q = select(ProductionPlan)
    if status:
        q = q.where(ProductionPlan.status == status)
    return {"items": [svc.plan_dict(p) for p in db.scalars(q.order_by(ProductionPlan.id.desc()).limit(page_size).offset((page - 1) * page_size))], "page": page}


@router.get("/plans/{pid}")
def get_plan(pid: int, db: Session = DB, _: CurrentUser = Depends(require("production.plan:read"))):
    p = db.get(ProductionPlan, pid)
    if not p:
        raise NotFound("Production plan")
    return svc.plan_dict(p)


@router.get("/plans/{pid}/material-check")
def material_check(pid: int, db: Session = DB, _: CurrentUser = Depends(require("production.plan:read"))):
    return svc.material_check(db, pid)


@router.get("/plans/{pid}/capacity-check")
def capacity_check(pid: int, db: Session = DB, _: CurrentUser = Depends(require("production.plan:read"))):
    return svc.capacity_check(db, pid)


@router.get("/plans/{pid}/attainment")
def attainment(pid: int, db: Session = DB, _: CurrentUser = Depends(require("production.plan:read"))):
    return svc.attainment(db, pid)


@router.post("/plans/{pid}/confirm")
def confirm(pid: int, body: ConfirmIn, db: Session = DB, cu: CurrentUser = Depends(require("production.plan:confirm"))):
    return run_tx(db, lambda d: svc.confirm_plan(d, cu, pid, body.expected_version, body.accept_overload_reason), name="production.plan.confirm")


@router.post("/plans/{pid}/lines/{line_no}/convert", status_code=201)
def convert(pid: int, line_no: int, db: Session = DB, cu: CurrentUser = Depends(require("production.order:create"))):
    return run_tx(db, lambda d: svc.convert_line(d, cu, pid, line_no), name="production.plan.convert")


@router.post("/plans/{pid}/convert-next", status_code=201)
def convert_next(pid: int, db: Session = DB, cu: CurrentUser = Depends(require("production.order:create"))):
    return run_tx(db, lambda d: svc.convert_next(d, cu, pid), name="production.plan.convert_next")


@router.post("/plans/{pid}/close")
def close(pid: int, body: CloseIn, db: Session = DB, cu: CurrentUser = Depends(require("production.plan:confirm"))):
    return run_tx(db, lambda d: svc.close_plan(d, cu, pid, body.cancel, body.reason), name="production.plan.close")


@router.put("/orders/{oid}/stages/{seq}/work-centre")
def stage_work_centre(oid: int, seq: int, body: StageWc, db: Session = DB, cu: CurrentUser = Depends(require("production.order:release"))):
    def work(d: Session):
        o = d.scalar(select(ProductionOrder).where(ProductionOrder.id == oid).with_for_update())
        if not o:
            raise NotFound("Production order")
        st = d.scalar(select(ProductionStage).where(ProductionStage.order_id == oid, ProductionStage.seq == seq))
        wc = d.get(WorkCentre, body.work_centre_id)
        if not st:
            raise NotFound("Stage")
        if not wc or not wc.is_active:
            raise Unprocessable("Work centre not found or inactive")
        if st.status != "PENDING":
            raise Unprocessable("A stage that has started keeps its work centre")
        st.work_centre_id = wc.id
        return {"order_id": oid, "seq": seq, "work_centre": wc.code}
    return run_tx(db, work, name="production.stage.work_centre")


@router.get("/work-centres/{wid}/utilisation")
def utilisation(wid: int, start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("production.work_centre:read"))):
    if end < start:
        raise Unprocessable("end is before start")
    return svc.utilisation(db, wid, start, end)
