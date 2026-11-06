from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.uow import run_tx
from app.maintenance import service as svc
from app.maintenance.models import Equipment, MaintenanceWorkOrder

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
DB = Depends(get_db, scope="function")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EquipmentIn(_In):
    asset_id: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    category: str | None = None
    location: str | None = None
    calibration_required: bool = False
    calibration_due: date | None = None


class ScheduleIn(_In):
    equipment_id: int
    title: str = Field(min_length=3, max_length=160)
    frequency_days: int = Field(ge=1, le=3650)
    first_due: date


class BreakdownIn(_In):
    equipment_id: int
    description: str = Field(min_length=3)
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    production_impact: str | None = None


class AssignIn(_In):
    assignee_id: int


class CompleteIn(_In):
    root_cause: str | None = None
    repair_notes: str | None = None


class CalibrationIn(_In):
    calibrated_on: date
    due_date: date
    result: Literal["PASS", "FAIL"]
    certificate_ref: str | None = None


@router.post("/equipment", status_code=201)
def create_equipment(body: EquipmentIn, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.equipment:create"))):
    return run_tx(db, lambda d: svc.create_equipment(d, cu, body.model_dump()), name="maintenance.equipment.create")


@router.get("/equipment")
def list_equipment(status: str | None = None, db: Session = DB, _: CurrentUser = Depends(require("maintenance.equipment:read"))):
    q = select(Equipment).order_by(Equipment.asset_id)
    if status:
        q = q.where(Equipment.status == status)
    return [svc.equipment_dict(e) for e in db.scalars(q)]


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleIn, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.workorder:create"))):
    return run_tx(db, lambda d: svc.create_schedule(d, cu, body.equipment_id, body.title, body.frequency_days, body.first_due), name="maintenance.schedule.create")


@router.post("/schedules/generate")
def generate(as_of: date | None = None, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.workorder:create"))):
    return run_tx(db, lambda d: svc.generate_due(d, as_of or date.today(), cu.id), name="maintenance.schedule.generate")


@router.post("/breakdowns", status_code=201)
def breakdown(body: BreakdownIn, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.workorder:create"))):
    return run_tx(db, lambda d: svc.report_breakdown(d, cu, equipment_id=body.equipment_id, description=body.description, priority=body.priority,
                                                     production_impact=body.production_impact), name="maintenance.breakdown")


@router.get("/work-orders")
def work_orders(status: str | None = None, equipment_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB,
                _: CurrentUser = Depends(require("maintenance.workorder:read"))):
    q = select(MaintenanceWorkOrder).order_by(MaintenanceWorkOrder.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(MaintenanceWorkOrder.status == status)
    if equipment_id:
        q = q.where(MaintenanceWorkOrder.equipment_id == equipment_id)
    return {"items": [svc.wo_dict(w) for w in db.scalars(q)], "page": page}


@router.post("/work-orders/{wid}/assign")
def assign(wid: int, body: AssignIn, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.workorder:assign"))):
    return run_tx(db, lambda d: svc.wo_action(d, cu, wid, "assign", assignee_id=body.assignee_id), name="maintenance.wo.assign")


@router.post("/work-orders/{wid}/start")
def start(wid: int, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.workorder:execute"))):
    return run_tx(db, lambda d: svc.wo_action(d, cu, wid, "start"), name="maintenance.wo.start")


@router.post("/work-orders/{wid}/complete")
def complete(wid: int, body: CompleteIn, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.workorder:execute"))):
    return run_tx(db, lambda d: svc.wo_action(d, cu, wid, "complete", root_cause=body.root_cause, repair_notes=body.repair_notes), name="maintenance.wo.complete")


@router.post("/work-orders/{wid}/verify")
def verify(wid: int, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.workorder:verify"))):
    return run_tx(db, lambda d: svc.wo_action(d, cu, wid, "verify"), name="maintenance.wo.verify")


@router.post("/equipment/{eid}/calibrations", status_code=201)
def calibrate(eid: int, body: CalibrationIn, db: Session = DB, cu: CurrentUser = Depends(require("maintenance.calibration:create"))):
    return run_tx(db, lambda d: svc.record_calibration(d, cu, eid, body.calibrated_on, body.due_date, body.result, body.certificate_ref), name="maintenance.calibration")


@router.get("/equipment/{eid}/downtime")
def downtime(eid: int, db: Session = DB, _: CurrentUser = Depends(require("maintenance.equipment:read"))):
    rows = db.execute(text("SELECT id, started_at, ended_at, reason, EXTRACT(EPOCH FROM COALESCE(ended_at, now()) - started_at)/3600 AS hours FROM downtime_records WHERE equipment_id = :e ORDER BY id DESC"),
                      {"e": eid}).mappings().all()
    return {"equipment_id": eid, "total_hours": round(sum(float(r["hours"]) for r in rows), 2), "records": [dict(r) for r in rows]}
