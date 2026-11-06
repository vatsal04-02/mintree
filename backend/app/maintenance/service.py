"""Maintenance: equipment register, preventive schedules (idempotent generator), breakdowns with downtime, calibration.
Registers EQUIPMENT_GUARD with Production so a stage cannot start on broken or out-of-calibration equipment."""
from datetime import date, timedelta

from psycopg import errors as pgerr
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.maintenance.models import CalibrationRecord, DowntimeRecord, Equipment, MaintenanceSchedule, MaintenanceWorkOrder
from app.production import service as production


def _eq(db: Session, eid: int, lock: bool = False) -> Equipment:
    q = select(Equipment).where(Equipment.id == eid)
    e = db.scalar(q.with_for_update() if lock else q)
    if not e:
        raise NotFound("Equipment")
    return e


def equipment_guard(db: Session, equipment_id: int) -> None:
    e = _eq(db, equipment_id)
    if e.status != "OPERATIONAL":
        raise BusinessConflict("equipment_unavailable", f"Equipment {e.asset_id} is {e.status} and cannot be used for production")
    if e.calibration_required and (e.calibration_due is None or e.calibration_due < date.today()):
        raise BusinessConflict("calibration_overdue", f"Equipment {e.asset_id} has no valid calibration", due=str(e.calibration_due))


production.EQUIPMENT_GUARD = equipment_guard


def create_equipment(db: Session, cu: CurrentUser, data: dict) -> dict:
    e = Equipment(**data)
    db.add(e)
    try:
        db.flush()
    except IntegrityError as ex:
        if isinstance(ex.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_asset", "An equipment record with this asset ID already exists") from ex
        raise
    audit.record(db, user=cu, action="create", entity="equipment", entity_id=e.id, after={"asset_id": e.asset_id})
    return equipment_dict(e)


def create_schedule(db: Session, cu: CurrentUser, equipment_id: int, title: str, frequency_days: int, first_due: date) -> dict:
    _eq(db, equipment_id)
    s = MaintenanceSchedule(equipment_id=equipment_id, title=title, frequency_days=frequency_days, next_due=first_due)
    db.add(s)
    db.flush()
    audit.record(db, user=cu, action="create", entity="maintenance_schedule", entity_id=s.id)
    return {"id": s.id, "equipment_id": equipment_id, "title": title, "frequency_days": frequency_days, "next_due": s.next_due}


def _wo_number(db: Session) -> str:
    n = db.execute(text("SELECT nextval('maintenance_wo_seq')")).scalar_one()
    return f"MWO/{date.today():%y}/{n:06d}"


def generate_due(db: Session, as_of: date, actor_id: int | None = None) -> dict:
    """Idempotent preventive-maintenance generator: SKIP LOCKED so concurrent runs never double-process a schedule, and a unique
    (schedule, due_date) index so even a re-run over the same date cannot create a second work order."""
    created = 0
    for s in db.scalars(select(MaintenanceSchedule).where(MaintenanceSchedule.is_active.is_(True), MaintenanceSchedule.next_due <= as_of)
                        .order_by(MaintenanceSchedule.id).with_for_update(skip_locked=True)):
        for _ in range(12):                                   # catch up missed intervals, bounded
            if s.next_due > as_of:
                break
            n = db.execute(text("INSERT INTO maintenance_work_orders(number, equipment_id, kind, schedule_id, due_date, description, raised_by, priority) "
                                "VALUES (:n, :e, 'PREVENTIVE', :s, :d, :t, :u, 'MEDIUM') ON CONFLICT DO NOTHING"),
                           {"n": _wo_number(db), "e": s.equipment_id, "s": s.id, "d": s.next_due, "t": s.title, "u": actor_id or db.scalar(select(text("min(id)")).select_from(text("users")))}).rowcount
            created += n
            s.next_due = s.next_due + timedelta(days=s.frequency_days)
    return {"created": created, "as_of": as_of}


def report_breakdown(db: Session, cu: CurrentUser, *, equipment_id: int, description: str, priority: str, production_impact: str | None) -> dict:
    e = _eq(db, equipment_id, lock=True)
    if e.status == "RETIRED":
        raise BusinessConflict("equipment_retired", "Retired equipment cannot be maintained")
    wo = MaintenanceWorkOrder(number=_wo_number(db), equipment_id=e.id, kind="BREAKDOWN", description=description, priority=priority, production_impact=production_impact, raised_by=cu.id)
    db.add(wo)
    db.flush()
    e.status = "BREAKDOWN"
    if not db.scalar(select(DowntimeRecord.id).where(DowntimeRecord.equipment_id == e.id, DowntimeRecord.ended_at.is_(None))):
        db.add(DowntimeRecord(equipment_id=e.id, work_order_id=wo.id, started_at=func.now(), reason=description[:200]))
    audit.record(db, user=cu, action="breakdown", entity="equipment", entity_id=e.id, after={"work_order": wo.number, "priority": priority})
    return wo_dict(wo)


def _wo(db: Session, wid: int) -> MaintenanceWorkOrder:
    w = db.scalar(select(MaintenanceWorkOrder).where(MaintenanceWorkOrder.id == wid).with_for_update())
    if not w:
        raise NotFound("Work order")
    return w


def wo_action(db: Session, cu: CurrentUser, wid: int, action: str, *, assignee_id: int | None = None, root_cause: str | None = None, repair_notes: str | None = None) -> dict:
    w = _wo(db, wid)
    e = _eq(db, w.equipment_id, lock=True)
    if action == "assign":
        if w.status not in ("OPEN", "ASSIGNED"):
            raise BusinessConflict("invalid_status", f"Cannot assign a work order in status {w.status}")
        w.assigned_to, w.status = assignee_id, "ASSIGNED"
    elif action == "start":
        if w.status != "ASSIGNED":
            raise BusinessConflict("invalid_status", "Only assigned work orders can be started")
        if w.assigned_to != cu.id:
            raise BusinessConflict("not_assignee", "Only the assigned technician can start this work order")
        w.status = "IN_PROGRESS"
        if w.kind == "PREVENTIVE" and e.status == "OPERATIONAL":
            e.status = "UNDER_MAINTENANCE"
            db.add(DowntimeRecord(equipment_id=e.id, work_order_id=w.id, started_at=func.now(), reason=f"Preventive: {w.description}"[:200]))
    elif action == "complete":
        if w.status != "IN_PROGRESS":
            raise BusinessConflict("invalid_status", "Only work in progress can be completed")
        if w.assigned_to != cu.id:
            raise BusinessConflict("not_assignee", "Only the assigned technician can complete this work order")
        if w.kind == "BREAKDOWN" and not (root_cause and repair_notes):
            raise BusinessConflict("root_cause_required", "A breakdown needs its root cause and repair notes")
        w.status, w.root_cause, w.repair_notes, w.completed_at = "DONE", root_cause, repair_notes, func.now()
    elif action == "verify":
        if w.status != "DONE":
            raise BusinessConflict("invalid_status", "Only completed work can be verified")
        if w.assigned_to == cu.id:
            raise BusinessConflict("segregation_of_duties", "The technician cannot verify their own work")
        w.status, w.verified_by = "CLOSED", cu.id
        other_open = db.scalar(select(MaintenanceWorkOrder.id).where(MaintenanceWorkOrder.equipment_id == e.id, MaintenanceWorkOrder.id != w.id,
                                                                    MaintenanceWorkOrder.kind == "BREAKDOWN", MaintenanceWorkOrder.status != "CLOSED"))
        if not other_open:
            if e.status in ("BREAKDOWN", "UNDER_MAINTENANCE"):
                e.status = "OPERATIONAL"
            db.execute(text("UPDATE downtime_records SET ended_at = now() WHERE equipment_id = :e AND ended_at IS NULL"), {"e": e.id})
    audit.record(db, user=cu, action=f"wo_{action}", entity="maintenance_work_order", entity_id=w.id)
    return wo_dict(w)


def record_calibration(db: Session, cu: CurrentUser, equipment_id: int, calibrated_on: date, due_date: date, result: str, certificate_ref: str | None) -> dict:
    e = _eq(db, equipment_id, lock=True)
    db.add(CalibrationRecord(equipment_id=e.id, calibrated_on=calibrated_on, due_date=due_date, result=result, certificate_ref=certificate_ref, performed_by=cu.id))
    if result == "PASS":
        e.calibration_due = due_date
    elif e.status == "OPERATIONAL":
        e.status = "UNDER_MAINTENANCE"             # a failed calibration takes the instrument out of service
    audit.record(db, user=cu, action=f"calibration_{result.lower()}", entity="equipment", entity_id=e.id, after={"due": str(due_date)})
    return equipment_dict(e)


def equipment_dict(e: Equipment) -> dict:
    return {"id": e.id, "asset_id": e.asset_id, "name": e.name, "category": e.category, "location": e.location, "status": e.status,
            "calibration_required": e.calibration_required, "calibration_due": e.calibration_due}


def wo_dict(w: MaintenanceWorkOrder) -> dict:
    return {"id": w.id, "number": w.number, "equipment_id": w.equipment_id, "kind": w.kind, "status": w.status, "priority": w.priority, "description": w.description,
            "due_date": w.due_date, "assigned_to": w.assigned_to, "root_cause": w.root_cause}
