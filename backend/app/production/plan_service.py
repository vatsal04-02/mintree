from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import outbox
from app.core.clock import business_today
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.production import service as prod
from app.production.models import Bom, ProductionOrder, ProductionStage
from app.production.plan_models import ProductionPlan, ProductionPlanLine, WorkCentre

D = Decimal


def create_plan(db: Session, cu: CurrentUser, *, name: str, period_start, period_end, notes: str | None, lines: list[dict]) -> dict:
    if period_end < period_start:
        raise Unprocessable("The plan period ends before it starts")
    boms = {b.id: b for b in db.scalars(select(Bom).where(Bom.id.in_({l["bom_id"] for l in lines})))}
    wcs = {w.id: w for w in db.scalars(select(WorkCentre).where(WorkCentre.id.in_({l["work_centre_id"] for l in lines if l.get("work_centre_id")} or {0})))}
    rows = []
    for n, l in enumerate(lines, 1):
        b = boms.get(l["bom_id"])
        if not b or b.status != "ACTIVE":
            raise Unprocessable(f"Line {n}: BOM {l['bom_id']} is not an ACTIVE bill of materials")
        if not (period_start <= l["planned_date"] <= period_end):
            raise Unprocessable(f"Line {n}: planned date {l['planned_date']} is outside the plan period")
        if l.get("work_centre_id") and (l["work_centre_id"] not in wcs or not wcs[l["work_centre_id"]].is_active):
            raise Unprocessable(f"Line {n}: work centre not found or inactive")
        if D(str(l["planned_qty"])) <= 0:
            raise Unprocessable(f"Line {n}: quantity must be positive")
        rows.append(ProductionPlanLine(line_no=n, bom_id=b.id, planned_qty=l["planned_qty"], planned_date=l["planned_date"], warehouse_id=l["warehouse_id"], work_centre_id=l.get("work_centre_id")))
    n = db.execute(text("SELECT nextval('production_plan_seq')")).scalar_one()
    p = ProductionPlan(number=f"PLAN/{business_today():%y}/{n:05d}", name=name, period_start=period_start, period_end=period_end, notes=notes, created_by=cu.id)
    p.lines = rows
    db.add(p)
    db.flush()
    audit.record(db, user=cu, action="create", entity="production_plan", entity_id=p.id, after={"number": p.number, "lines": len(rows)})
    return plan_dict(p)


def _lock(db: Session, pid: int, expected_version: int | None = None) -> ProductionPlan:
    p = db.scalar(select(ProductionPlan).where(ProductionPlan.id == pid).with_for_update())
    if not p:
        raise NotFound("Production plan")
    if expected_version is not None and p.version != expected_version:
        raise BusinessConflict("version_conflict", "The plan was changed by someone else. Reload and try again.")
    return p


def material_check(db: Session, pid: int) -> dict:
    """Combined demand of ALL open plan lines per warehouse against QC-released, unexpired, unreserved stock. A component shared by two lines is counted once against stock, not twice."""
    p = db.get(ProductionPlan, pid)
    if not p:
        raise NotFound("Production plan")
    boms = {b.id: b for b in db.scalars(select(Bom).where(Bom.id.in_({l.bom_id for l in p.lines})))}
    need: dict[tuple[int, int], Decimal] = defaultdict(D)                       # (warehouse, item) -> required
    for l in p.lines:
        if l.order_id:
            continue                                                             # already an order: its own reservation/issue covers it
        b = boms[l.bom_id]
        for c in b.lines:
            need[(l.warehouse_id, c.item_id)] += c.qty * l.planned_qty / b.output_qty
    out = []
    for (wh, item), req in sorted(need.items()):
        have = db.scalar(text("""SELECT COALESCE(sum(sb.qty_available) FILTER (WHERE sb.batch_id IS NULL OR (ib.qc_status='RELEASED' AND (ib.expiry_date IS NULL OR ib.expiry_date > business_today()))),0)
                                 FROM stock_balances sb LEFT JOIN inventory_batches ib ON ib.id = sb.batch_id WHERE sb.warehouse_id = :w AND sb.item_id = :i"""), {"w": wh, "i": item})
        out.append({"warehouse_id": wh, "item_id": item, "required": str(req.quantize(D("0.001"))), "available": str(have), "short": str(max(D(0), req - have).quantize(D("0.001")))})
    return {"plan_id": p.id, "ok": all(D(r["short"]) == 0 for r in out), "lines": out}


def capacity_check(db: Session, pid: int) -> dict:
    """Per work centre and day: planned output of ALL non-cancelled plan lines and orders already booked there vs capacity."""
    p = db.get(ProductionPlan, pid)
    if not p:
        raise NotFound("Production plan")
    load: dict[tuple[int, object], Decimal] = defaultdict(D)
    for l in p.lines:
        if l.work_centre_id and not l.order_id:
            load[(l.work_centre_id, l.planned_date)] += l.planned_qty
    over = []
    for (wc_id, day), qty in sorted(load.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        wc = db.get(WorkCentre, wc_id)
        if wc.capacity_per_day is None:
            continue
        other = db.scalar(text("""SELECT COALESCE(sum(l.planned_qty),0) FROM production_plan_lines l JOIN production_plans pl ON pl.id = l.plan_id
                                   WHERE l.work_centre_id = :w AND l.planned_date = :d AND pl.id <> :p AND pl.status = 'CONFIRMED'"""), {"w": wc_id, "d": day, "p": pid})
        total = qty + other
        if total > wc.capacity_per_day:
            over.append({"work_centre": wc.code, "date": day.isoformat(), "planned": str(total), "capacity": str(wc.capacity_per_day), "over_by": str(total - wc.capacity_per_day)})
    return {"plan_id": p.id, "ok": not over, "overloads": over}


def confirm_plan(db: Session, cu: CurrentUser, pid: int, expected_version: int, accept_overload_reason: str | None) -> dict:
    p = _lock(db, pid, expected_version)
    if p.status != "DRAFT":
        raise BusinessConflict("invalid_status", f"Only a DRAFT plan can be confirmed (this one is {p.status})")
    if not p.lines:
        raise Unprocessable("A plan without lines cannot be confirmed")
    cap = capacity_check(db, pid)
    if not cap["ok"]:
        if not accept_overload_reason:
            raise BusinessConflict("capacity_exceeded", "A work centre is over capacity on at least one day; re-plan or confirm with a reason", overloads=cap["overloads"])
        p.overload_accepted_by, p.overload_reason = cu.id, accept_overload_reason
    p.status, p.version = "CONFIRMED", p.version + 1
    audit.record(db, user=cu, action="confirm", entity="production_plan", entity_id=p.id, after={"overloads_accepted": bool(p.overload_reason)}, reason=p.overload_reason)
    return plan_dict(p)


def convert_line(db: Session, cu: CurrentUser, pid: int, line_no: int) -> dict:
    p = _lock(db, pid)
    if p.status != "CONFIRMED":
        raise BusinessConflict("plan_not_confirmed", f"Lines are converted to production orders once the plan is CONFIRMED (this one is {p.status})")
    l = next((x for x in p.lines if x.line_no == line_no), None)
    if not l:
        raise NotFound("Plan line")
    if l.order_id:
        raise BusinessConflict("already_converted", "This plan line already has a production order", order_id=l.order_id)
    o = prod.create_order(db, cu, bom_id=l.bom_id, planned_qty=l.planned_qty, warehouse_id=l.warehouse_id, planned_date=l.planned_date, stages=None)
    l.order_id = o["id"]
    if l.work_centre_id:
        for st in db.scalars(select(ProductionStage).where(ProductionStage.order_id == o["id"])):
            st.work_centre_id = l.work_centre_id                                   # the order's stages inherit the planned work centre
    db.flush()
    audit.record(db, user=cu, action="convert_line", entity="production_plan", entity_id=p.id, after={"line_no": line_no, "order": o["number"]})
    outbox.add(db, event_type="production.plan.line_converted", aggregate_type="production_plan", aggregate_id=p.id, payload={"plan": p.number, "line_no": line_no, "order_id": o["id"]})
    return plan_dict(p) | {"order": o}


def convert_next(db: Session, cu: CurrentUser, pid: int) -> dict:
    """Convert the first plan line that has no production order yet (what a 'Convert next line' button needs)."""
    p = db.get(ProductionPlan, pid)
    if not p:
        raise NotFound("Production plan")
    nxt = next((l.line_no for l in p.lines if not l.order_id), None)
    if nxt is None:
        raise BusinessConflict("nothing_to_convert", "Every line of this plan already has a production order")
    return convert_line(db, cu, pid, nxt)


def close_plan(db: Session, cu: CurrentUser, pid: int, cancel: bool, reason: str | None) -> dict:
    p = _lock(db, pid)
    if cancel:
        if p.status not in ("DRAFT", "CONFIRMED"):
            raise BusinessConflict("invalid_status", f"A {p.status} plan cannot be cancelled")
        if any(l.order_id for l in p.lines):
            raise BusinessConflict("has_orders", "Orders were already created from this plan; cancel or complete them, then close the plan")
        if not reason:
            raise Unprocessable("A cancellation reason is required")
        p.status = "CANCELLED"
    else:
        if p.status != "CONFIRMED":
            raise BusinessConflict("invalid_status", f"Only a CONFIRMED plan can be closed (this one is {p.status})")
        open_lines = [l.line_no for l in p.lines if not l.order_id]
        if open_lines:
            raise BusinessConflict("lines_not_converted", "Convert every line to a production order before closing the plan", lines=open_lines)
        busy = db.scalar(text("SELECT count(*) FROM production_orders o JOIN production_plan_lines l ON l.order_id = o.id WHERE l.plan_id = :p AND o.status NOT IN ('COMPLETED','CANCELLED')"), {"p": pid})
        if busy:
            raise BusinessConflict("orders_open", "Some production orders of this plan are not finished yet", open_orders=busy)
        p.status = "CLOSED"
    p.version += 1
    audit.record(db, user=cu, action="cancel" if cancel else "close", entity="production_plan", entity_id=p.id, reason=reason)
    return plan_dict(p)


def attainment(db: Session, pid: int) -> dict:
    p = db.get(ProductionPlan, pid)
    if not p:
        raise NotFound("Production plan")
    rows = db.execute(text("""SELECT l.line_no, l.planned_qty, l.planned_date, o.number, o.status, o.actual_qty, o.yield_pct, o.completed_at
                                FROM production_plan_lines l LEFT JOIN production_orders o ON o.id = l.order_id WHERE l.plan_id = :p ORDER BY l.line_no"""), {"p": pid}).mappings().all()
    planned = sum((r["planned_qty"] for r in rows), D(0))
    made = sum((r["actual_qty"] or D(0) for r in rows if r["status"] == "COMPLETED"), D(0))
    return {"plan_id": pid, "planned_qty": str(planned), "completed_qty": str(made), "attainment_pct": None if not planned else str((made * 100 / planned).quantize(D("0.01"))),
            "lines": [{**{k: (str(v) if isinstance(v, Decimal) else v) for k, v in dict(r).items()}, "late": bool(r["status"] not in ("COMPLETED",) and r["planned_date"] < business_today())} for r in rows]}


def utilisation(db: Session, wc_id: int, start, end) -> dict:
    wc = db.get(WorkCentre, wc_id)
    if not wc:
        raise NotFound("Work centre")
    rows = db.execute(text("""SELECT business_date(s.completed_at) AS day, count(*) stages,
                                     round(sum(EXTRACT(EPOCH FROM (s.completed_at - s.started_at)) / 3600)::numeric, 2) AS hours
                                FROM production_stages s WHERE s.work_centre_id = :w AND s.status = 'DONE' AND business_date(s.completed_at) BETWEEN :a AND :b GROUP BY 1 ORDER BY 1"""),
                      {"w": wc_id, "a": start, "b": end}).mappings().all()
    return {"work_centre": wc.code, "from": start, "to": end, "days": [{"day": r["day"], "stages": r["stages"], "hours": str(r["hours"])} for r in rows],
            "total_hours": str(sum((r["hours"] for r in rows), D(0)))}


def plan_dict(p: ProductionPlan) -> dict:
    return {"id": p.id, "number": p.number, "name": p.name, "status": p.status, "version": p.version, "period_start": p.period_start, "period_end": p.period_end,
            "overload_reason": p.overload_reason,
            "lines": [{"line_no": l.line_no, "bom_id": l.bom_id, "planned_qty": str(l.planned_qty), "planned_date": l.planned_date, "warehouse_id": l.warehouse_id,
                       "work_centre_id": l.work_centre_id, "order_id": l.order_id} for l in p.lines]}
