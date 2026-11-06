"""Production extras: in-process quality checks (gate stage completion), plant dashboard, full batch record, BOM comparison, wastage report,
and material planning (aggregate requirement vs released stock, optionally raising a purchase request for the shortages)."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.notifications.service import notify
from app.production.models import Bom, ProcessCheck, ProductionOrder, ProductionStage
from app.core.clock import business_today


def record_check(db: Session, cu: CurrentUser, order_id: int, seq: int, *, parameter: str, value, min_value, max_value, passed: bool | None, observation: str | None,
                 corrective_action: str | None) -> dict:
    o = db.get(ProductionOrder, order_id)
    if not o:
        raise NotFound("Production order")
    st = db.scalar(select(ProductionStage).where(ProductionStage.order_id == order_id, ProductionStage.seq == seq))
    if not st:
        raise NotFound("Stage")
    if st.status != "IN_PROGRESS":
        raise BusinessConflict("stage_not_in_progress", "In-process checks are recorded while the stage is in progress")
    if value is not None and (min_value is not None or max_value is not None):
        ok = (min_value is None or value >= min_value) and (max_value is None or value <= max_value)          # computed, never client-supplied, when limits exist
    elif passed is None:
        raise Unprocessable("State pass/fail, or give a value with limits")
    else:
        ok = passed
    if not ok and not (corrective_action and corrective_action.strip()):
        raise BusinessConflict("corrective_action_required", "A failed in-process check needs its corrective action recorded")
    c = ProcessCheck(order_id=order_id, stage_seq=seq, parameter=parameter, value=value, min_value=min_value, max_value=max_value, passed=ok, observation=observation,
                     corrective_action=corrective_action, inspector_id=cu.id)
    db.add(c)
    db.flush()
    if not ok:
        notify(db, kind="process_check.failed", title=f"{o.number} stage {st.name}: {parameter} out of limits", link=f"/production/orders/{o.id}", priority="HIGH",
               permission="production.order:release", exclude_user_id=cu.id)
    audit.record(db, user=cu, action="process_check", entity="production_order", entity_id=o.id, after={"stage": seq, "parameter": parameter, "passed": ok})
    return check_dict(c)


def latest_failed(db: Session, order_id: int, seq: int) -> list[str]:
    rows = db.execute(text("""SELECT DISTINCT ON (parameter) parameter, passed FROM process_checks WHERE order_id = :o AND stage_seq = :s ORDER BY parameter, id DESC"""), {"o": order_id, "s": seq}).all()
    return [r[0] for r in rows if not r[1]]


def check_stage_can_complete(db: Session, order_id: int, seq: int) -> None:
    bad = latest_failed(db, order_id, seq)
    if bad:
        raise BusinessConflict("process_check_failed", "Re-check passing readings before completing this stage", parameters=bad)


def checks(db: Session, order_id: int) -> list[dict]:
    return [check_dict(c) for c in db.scalars(select(ProcessCheck).where(ProcessCheck.order_id == order_id).order_by(ProcessCheck.id))]


def check_dict(c: ProcessCheck) -> dict:
    return {"id": c.id, "stage_seq": c.stage_seq, "parameter": c.parameter, "value": None if c.value is None else str(c.value), "passed": c.passed, "observation": c.observation,
            "corrective_action": c.corrective_action, "inspector_id": c.inspector_id, "recorded_at": c.recorded_at}


def dashboard(db: Session) -> dict:
    by = {r[0]: r[1] for r in db.execute(text("SELECT status, count(*) FROM production_orders GROUP BY status"))}
    one = lambda sql: db.scalar(text(sql))
    waste = [dict(r) for r in db.execute(text("""SELECT w.category, sum(w.qty) AS qty, count(*) AS records FROM wastage_records w JOIN production_orders o ON o.id = w.order_id
                                                  WHERE o.completed_at > now() - interval '30 days' GROUP BY w.category ORDER BY 2 DESC""")).mappings()]
    return {"planned_today": one("SELECT count(*) FROM production_orders WHERE planned_date = business_today() AND status IN ('PLANNED','RELEASED')"),
            "active": by.get("RELEASED", 0) + by.get("IN_PROGRESS", 0), "awaiting_release": by.get("PLANNED", 0), "in_progress": by.get("IN_PROGRESS", 0),
            "awaiting_qc": one("""SELECT count(*) FROM production_orders o JOIN inventory_batches b ON b.id = o.output_batch_id WHERE o.status = 'COMPLETED' AND b.qc_status IN ('QUARANTINE','HOLD')"""),
            "completed_30d": one("SELECT count(*) FROM production_orders WHERE status = 'COMPLETED' AND completed_at > now() - interval '30 days'"),
            "delayed": one("SELECT count(*) FROM production_orders WHERE planned_date < business_today() AND status NOT IN ('COMPLETED','CANCELLED')"),
            "average_yield_pct_30d": None if (y := one("SELECT round(avg(yield_pct),2) FROM production_orders WHERE status = 'COMPLETED' AND completed_at > now() - interval '30 days'")) is None else str(y),
            "wastage_by_category_30d": [{**w, "qty": str(w["qty"])} for w in waste], "orders_by_status": by,
            "machine_availability": {r[0]: r[1] for r in db.execute(text("SELECT status, count(*) FROM equipment GROUP BY status"))}}


def batch_record(db: Session, batch_id: int) -> dict:
    """The complete record for a finished/bulk batch built from the live tables: what went in (with supplier lots), how it was made, what came out, and how it was released."""
    b = db.execute(text("""SELECT ib.id, ib.batch_no, ib.qc_status, ib.mfg_date, ib.expiry_date, ib.source_id, i.item_code, i.name FROM inventory_batches ib JOIN items i ON i.id = ib.item_id
                            WHERE ib.id = :b AND ib.source_type = 'production_order'"""), {"b": batch_id}).mappings().first()
    if not b:
        raise NotFound("Production batch")
    o = db.get(ProductionOrder, b["source_id"])
    mats = db.execute(text("""SELECT i.item_code, i.name, ib.batch_no, ib.supplier_lot_no, c.qty, c.cost FROM production_consumptions c JOIN items i ON i.id = c.item_id
                                LEFT JOIN inventory_batches ib ON ib.id = c.batch_id WHERE c.order_id = :o ORDER BY c.id"""), {"o": o.id}).mappings().all()
    stages = db.execute(text("""SELECT s.seq, s.name, s.status, u.full_name AS operator, e.asset_id AS equipment, s.started_at, s.completed_at, s.parameters, s.notes
                                  FROM production_stages s LEFT JOIN users u ON u.id = s.operator_id LEFT JOIN equipment e ON e.id = s.equipment_id WHERE s.order_id = :o ORDER BY s.seq"""), {"o": o.id}).mappings().all()
    waste = db.execute(text("SELECT category, qty, reason, responsible_department FROM wastage_records WHERE order_id = :o ORDER BY id"), {"o": o.id}).mappings().all()
    insp = db.execute(text("""SELECT n.number, n.stage, n.status, n.decision, n.decision_reason, n.decided_at FROM inspections n WHERE n.batch_id = :b ORDER BY n.id"""), {"b": batch_id}).mappings().all()
    return {"batch": {"id": b["id"], "batch_no": b["batch_no"], "product": f"{b['item_code']} {b['name']}", "mfg_date": b["mfg_date"], "expiry_date": b["expiry_date"], "qc_status": b["qc_status"]},
            "production_order": {"number": o.number, "bom_id": o.bom_id, "formulation_version_id": o.formulation_version_id, "planned_qty": str(o.planned_qty),
                                 "actual_qty": None if o.actual_qty is None else str(o.actual_qty), "yield_pct": None if o.yield_pct is None else str(o.yield_pct),
                                 "unit_cost": None if o.unit_cost is None else str(o.unit_cost), "started_at": o.started_at, "completed_at": o.completed_at},
            "materials_consumed": [{**{k: (str(v) if isinstance(v, Decimal) else v) for k, v in m.items()}} for m in mats],
            "stages": [dict(s) for s in stages], "process_checks": checks(db, o.id), "wastage": [{**w, "qty": str(w["qty"])} for w in waste],
            "quality_inspections": [dict(i) for i in insp], "final_disposition": b["qc_status"]}


def compare_boms(db: Session, a: int, b: int) -> dict:
    ba, bb = db.get(Bom, a), db.get(Bom, b)
    if not ba or not bb:
        raise NotFound("BOM")
    if ba.output_item_id != bb.output_item_id:
        raise Unprocessable("BOMs must produce the same item")
    per = lambda bom: {l.item_id: (l.qty / bom.output_qty) for l in bom.lines}          # quantity per unit of output so batch sizes compare fairly
    pa, pb = per(ba), per(bb)
    q = lambda v: str(v.quantize(Decimal("0.000001")))
    return {"from": {"id": ba.id, "version_no": ba.version_no, "status": ba.status, "output_qty": str(ba.output_qty)}, "to": {"id": bb.id, "version_no": bb.version_no, "status": bb.status, "output_qty": str(bb.output_qty)},
            "added": [{"item_id": k, "per_unit": q(v)} for k, v in pb.items() if k not in pa], "removed": [{"item_id": k, "per_unit": q(v)} for k, v in pa.items() if k not in pb],
            "changed": [{"item_id": k, "from_per_unit": q(pa[k]), "to_per_unit": q(pb[k])} for k in pa if k in pb and pa[k] != pb[k]],
            "formulation_version_changed": ba.formulation_version_id != bb.formulation_version_id}


def wastage_report(db: Session, start: date, end: date) -> dict:
    rows = db.execute(text("""SELECT w.category, w.responsible_department AS department, sum(w.qty) AS qty, count(*) AS records, round(sum(w.qty * COALESCE(o.unit_cost,0)), 2) AS value_at_cost
                                FROM wastage_records w JOIN production_orders o ON o.id = w.order_id WHERE o.completed_at::date BETWEEN :s AND :e GROUP BY 1, 2 ORDER BY value_at_cost DESC"""), {"s": start, "e": end}).mappings().all()
    yields = db.execute(text("""SELECT o.number, o.output_item_id, o.planned_qty, o.actual_qty, o.yield_pct FROM production_orders o WHERE o.status = 'COMPLETED' AND o.completed_at::date BETWEEN :s AND :e ORDER BY o.yield_pct NULLS LAST LIMIT 20"""), {"s": start, "e": end}).mappings().all()
    return {"period": {"start": start, "end": end}, "by_category": [{**dict(r), "qty": str(r["qty"]), "value_at_cost": str(r["value_at_cost"])} for r in rows],
            "lowest_yield_orders": [{**dict(y), "planned_qty": str(y["planned_qty"]), "actual_qty": None if y["actual_qty"] is None else str(y["actual_qty"]), "yield_pct": None if y["yield_pct"] is None else str(y["yield_pct"])} for y in yields],
            "total_value_at_cost": str(sum((Decimal(r["value_at_cost"]) for r in rows), Decimal(0)))}


def plan(db: Session, cu: CurrentUser, *, warehouse_id: int, lines: list[dict], raise_pr: bool, required_by: date | None) -> dict:
    """Aggregates raw/packaging requirements across the planned batches (only ACTIVE BOMs) against released, unexpired stock in the warehouse."""
    need: dict[int, Decimal] = {}
    outputs = []
    for l in lines:
        bom = db.get(Bom, l["bom_id"])
        if not bom:
            raise NotFound(f"BOM {l['bom_id']}")
        if bom.status != "ACTIVE":
            raise BusinessConflict("bom_not_active", f"BOM {bom.id} is {bom.status}; only approved (ACTIVE) BOMs can be planned")
        factor = Decimal(str(l["qty"])) / bom.output_qty
        outputs.append({"bom_id": bom.id, "output_item_id": bom.output_item_id, "planned_qty": str(l["qty"])})
        for bl in bom.lines:
            need[bl.item_id] = need.get(bl.item_id, Decimal(0)) + bl.qty * factor
    rows = db.execute(text("""SELECT sb.item_id, COALESCE(sum(sb.qty_available) FILTER (WHERE sb.batch_id IS NULL OR (ib.qc_status='RELEASED' AND (ib.expiry_date IS NULL OR ib.expiry_date > business_today()))),0) avail
                                FROM stock_balances sb LEFT JOIN inventory_batches ib ON ib.id = sb.batch_id WHERE sb.warehouse_id = :w AND sb.item_id = ANY(:ids) GROUP BY sb.item_id"""),
                      {"w": warehouse_id, "ids": list(need)}).all()
    have = {r[0]: Decimal(r[1]) for r in rows}
    open_po = {r[0]: Decimal(r[1]) for r in db.execute(text("""SELECT i.item_id, sum(i.qty_ordered - i.qty_received) FROM purchase_order_items i JOIN purchase_orders p ON p.id = i.po_id
                                                                 WHERE p.status IN ('APPROVED','PARTIALLY_RECEIVED') AND i.item_id = ANY(:ids) GROUP BY i.item_id"""), {"ids": list(need)})}
    out, shortages = [], []
    for item_id in sorted(need):
        short = max(Decimal(0), need[item_id] - have.get(item_id, Decimal(0)))
        out.append({"item_id": item_id, "required": str(need[item_id].quantize(Decimal("0.001"))), "available": str(have.get(item_id, Decimal(0))), "on_order": str(open_po.get(item_id, Decimal(0))),
                    "short": str(short.quantize(Decimal("0.001"))), "short_after_open_pos": str(max(Decimal(0), short - open_po.get(item_id, Decimal(0))).quantize(Decimal("0.001")))})
        if short > 0:
            shortages.append((item_id, short))
    pr = None
    if raise_pr and shortages:
        from app.procurement import service as proc
        pr = proc.create_pr(db, cu, items=[{"item_id": i, "qty": s.quantize(Decimal("0.001"), rounding="ROUND_UP")} for i, s in shortages], required_by=required_by,
                            notes="Raised from production plan shortages", department_id=None)
    return {"warehouse_id": warehouse_id, "planned_batches": outputs, "materials": out, "feasible": not shortages, "purchase_request": pr}
