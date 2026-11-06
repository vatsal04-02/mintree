"""Production domain service. Lock order: order row -> stock balances (items in ascending id, FEFO within) -> item valuation
(ascending id, one batch) -> journal. Orders are pinned to the exact BOM and approved formulation version they were created from."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.audit import service as audit
from app.core import outbox
from app.core.context import request_id_var
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, InsufficientStock, NotFound, Unprocessable
from app.inventory import service as inv
from app.masters.models import Item
from app.production.models import (Bom, BomLine, ProductionConsumption, ProductionMaterial, ProductionOrder, ProductionStage, WastageRecord)
from app.quality import service as quality
from app.rnd.models import FormulationVersion, Formulation
from app.core.clock import business_today

DEFAULT_STAGES = ["MIXING", "PROCESSING", "IN_PROCESS_QC"]
LOW_YIELD_PCT = Decimal("95")
MAX_OVERYIELD = Decimal("1.10")
Q3 = Decimal("0.001")


# ------------------------------------------------------------------ BOMs
def _components(db: Session, lines: list[dict]) -> None:
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_({l["item_id"] for l in lines})))}
    for l in lines:
        it = items.get(l["item_id"])
        if not it or not it.is_active or it.kind not in ("RM", "PM", "BULK"):
            raise Unprocessable(f"Component {l['item_id']} must be an active RM, PM or BULK item")


def bom_from_formulation(db: Session, cu: CurrentUser, formulation_version_id: int) -> dict:
    v = db.get(FormulationVersion, formulation_version_id)
    if not v:
        raise NotFound("Formulation version")
    if v.status != "APPROVED":
        raise BusinessConflict("formulation_not_approved", "Only an approved formulation version can become a production BOM")
    f = db.get(Formulation, v.formulation_id)
    db.execute(text("SELECT id FROM items WHERE id = :i FOR NO KEY UPDATE"), {"i": f.product_item_id})   # serialise BOM versioning per item
    ver = (db.scalar(select(Bom.version_no).where(Bom.output_item_id == f.product_item_id).order_by(Bom.version_no.desc()).limit(1)) or 0) + 1
    db.execute(update(Bom).where(Bom.output_item_id == f.product_item_id, Bom.status == "ACTIVE").values(status="OBSOLETE"))
    b = Bom(output_item_id=f.product_item_id, output_qty=v.batch_size, formulation_version_id=v.id, version_no=ver, status="ACTIVE",
            created_by=cu.id, approved_by=v.approved_by)          # approval inherited from the approved formulation
    b.lines = [BomLine(line_no=i.line_no, item_id=i.item_id, qty=i.qty) for i in v.ingredients]
    db.add(b)
    db.flush()
    audit.record(db, user=cu, action="create", entity="bom", entity_id=b.id, after={"output_item_id": b.output_item_id, "version": ver, "formulation_version_id": v.id})
    return bom_dict(b)


def bom_manual(db: Session, cu: CurrentUser, output_item_id: int, output_qty, lines: list[dict]) -> dict:
    out = db.get(Item, output_item_id)
    if not out or out.kind not in ("BULK", "FG"):
        raise Unprocessable("A BOM produces a BULK or FG item")
    if len({l["item_id"] for l in lines}) != len(lines):
        raise Unprocessable("A component may appear once per BOM")
    _components(db, lines)
    ver = (db.scalar(select(Bom.version_no).where(Bom.output_item_id == output_item_id).order_by(Bom.version_no.desc()).limit(1)) or 0) + 1
    b = Bom(output_item_id=output_item_id, output_qty=output_qty, version_no=ver, created_by=cu.id)
    b.lines = [BomLine(line_no=n, item_id=l["item_id"], qty=l["qty"]) for n, l in enumerate(lines, 1)]
    db.add(b)
    db.flush()
    audit.record(db, user=cu, action="create", entity="bom", entity_id=b.id, after={"output_item_id": output_item_id, "version": ver})
    return bom_dict(b)


def bom_approve(db: Session, cu: CurrentUser, bom_id: int) -> dict:
    b = db.scalar(select(Bom).where(Bom.id == bom_id).with_for_update())
    if not b:
        raise NotFound("BOM")
    if b.status != "DRAFT":
        raise BusinessConflict("invalid_status", f"Cannot approve a BOM in status {b.status}")
    if b.created_by == cu.id:
        raise BusinessConflict("self_approval_not_allowed", "You cannot approve a BOM you created")
    db.execute(text("SELECT id FROM items WHERE id = :i FOR NO KEY UPDATE"), {"i": b.output_item_id})
    db.execute(update(Bom).where(Bom.output_item_id == b.output_item_id, Bom.status == "ACTIVE").values(status="OBSOLETE"))
    b.status, b.approved_by = "ACTIVE", cu.id
    db.flush()
    audit.record(db, user=cu, action="approve", entity="bom", entity_id=b.id)
    return bom_dict(b)


# ------------------------------------------------------------------ orders
def _check_specs(db: Session, bom: Bom) -> None:
    if bom.status != "ACTIVE":
        raise BusinessConflict("bom_not_approved", "Production cannot start without an approved (ACTIVE) BOM")
    if bom.formulation_version_id:
        st = db.scalar(select(FormulationVersion.status).where(FormulationVersion.id == bom.formulation_version_id))
        if st != "APPROVED":
            raise BusinessConflict("formula_superseded", "The formulation behind this BOM is no longer the approved version; regenerate the BOM")


def create_order(db: Session, cu: CurrentUser, *, bom_id: int, planned_qty, warehouse_id: int, planned_date: date | None, stages: list[str] | None) -> dict:
    bom = db.get(Bom, bom_id)
    if not bom:
        raise NotFound("BOM")
    _check_specs(db, bom)
    pq = Decimal(str(planned_qty))
    scale = pq / bom.output_qty
    n = db.execute(text("SELECT nextval('production_order_seq')")).scalar_one()
    o = ProductionOrder(number=f"PRD/{business_today():%y}/{n:06d}", bom_id=bom.id, formulation_version_id=bom.formulation_version_id, output_item_id=bom.output_item_id,
                        warehouse_id=warehouse_id, planned_qty=pq, planned_date=planned_date, created_by=cu.id, request_id=request_id_var.get())
    o.materials = [ProductionMaterial(line_no=l.line_no, item_id=l.item_id, required_qty=(l.qty * scale).quantize(Q3, rounding=ROUND_HALF_UP)) for l in bom.lines]
    o.stages = [ProductionStage(seq=i, name=s.upper()) for i, s in enumerate(stages or DEFAULT_STAGES, 1)]
    db.add(o)
    db.flush()
    audit.record(db, user=cu, action="create", entity="production_order", entity_id=o.id, after={"number": o.number, "bom_id": bom.id, "planned_qty": str(pq)})
    return order_dict(o)


def _lock_order(db: Session, order_id: int, expected_version: int | None = None) -> ProductionOrder:
    o = db.scalar(select(ProductionOrder).where(ProductionOrder.id == order_id).with_for_update())
    if not o:
        raise NotFound("Production order")
    if expected_version is not None and o.version != expected_version:
        raise BusinessConflict("version_conflict", "The order was changed by someone else. Reload and try again.")
    return o


def availability(db: Session, order_id: int) -> dict:
    o = db.get(ProductionOrder, order_id)
    if not o:
        raise NotFound("Production order")
    rows = db.execute(text("""SELECT sb.item_id, COALESCE(sum(sb.qty_available) FILTER (WHERE sb.batch_id IS NULL OR (ib.qc_status='RELEASED' AND (ib.expiry_date IS NULL OR ib.expiry_date > business_today()))),0) avail
                              FROM stock_balances sb LEFT JOIN inventory_batches ib ON ib.id = sb.batch_id WHERE sb.warehouse_id = :w AND sb.item_id = ANY(:ids) GROUP BY sb.item_id"""),
                      {"w": o.warehouse_id, "ids": [m.item_id for m in o.materials]}).all()
    have = {r[0]: r[1] for r in rows}
    lines = [{"item_id": m.item_id, "required": str(m.required_qty - m.issued_qty), "available": str(have.get(m.item_id, 0)),
              "short": str(max(Decimal(0), (m.required_qty - m.issued_qty) - have.get(m.item_id, Decimal(0))))} for m in o.materials]
    return {"order_id": o.id, "ok": all(Decimal(l["short"]) == 0 for l in lines), "lines": lines}


def release_order(db: Session, cu: CurrentUser, order_id: int, expected_version: int) -> dict:
    o = _lock_order(db, order_id, expected_version)
    if o.status != "PLANNED":
        raise BusinessConflict("invalid_status", f"Cannot release an order in status {o.status}")
    bom = db.get(Bom, o.bom_id)
    _check_specs(db, bom)
    short = []
    for m in sorted(o.materials, key=lambda x: x.item_id):             # ascending item id: same order for every writer
        try:
            inv.reserve(db, item_id=m.item_id, warehouse_id=o.warehouse_id, qty=m.required_qty, reference_type="production_order",
                        reference_id=str(o.id), actor_id=cu.id)
        except InsufficientStock as e:
            short.append({"item_id": m.item_id, "required": str(m.required_qty), "available": e.detail["available"]})
    if short:
        raise BusinessConflict("material_shortage", "Not enough released stock to cover this order", shortages=short)   # rolls back every reservation
    o.status, o.version = "RELEASED", o.version + 1
    audit.record(db, user=cu, action="release", entity="production_order", entity_id=o.id)
    return order_dict(o)


def issue_materials(db: Session, cu: CurrentUser, order_id: int) -> dict:
    o = _lock_order(db, order_id)
    if o.status != "RELEASED":
        raise BusinessConflict("invalid_status", f"Materials are issued to RELEASED orders (this one is {o.status})")
    legs_by_item: dict[int, list[dict]] = {}
    for m in sorted(o.materials, key=lambda x: x.item_id):
        res = db.execute(text("SELECT id, qty FROM stock_reservations WHERE reference_type='production_order' AND reference_id=:r AND item_id=:i AND status='ACTIVE' ORDER BY id"),
                         {"r": str(o.id), "i": m.item_id}).all()
        if not res or sum(r[1] for r in res) != m.required_qty - m.issued_qty:
            raise BusinessConflict("reservation_mismatch", "Reserved quantity does not match the requirement; release the order again", item_id=m.item_id)
        legs_by_item[m.item_id] = inv.issue(db, item_id=m.item_id, warehouse_id=o.warehouse_id, qty=m.required_qty - m.issued_qty, source_type="production_order",
                                            source_id=o.id, source_line=m.line_no, actor_id=cu.id, reservation_ids=[r[0] for r in res], movement="PRODUCTION_ISSUE")
    vrows = acct.lock_valuations(db, list(legs_by_item))
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(list(legs_by_item))))}
    credit_lines: dict[str, Decimal] = {}
    total = Decimal(0)
    for item_id in sorted(legs_by_item):
        for leg in legs_by_item[item_id]:
            cost = acct.val_consume(vrows, item_id, Decimal(leg["qty"]))
            db.add(ProductionConsumption(order_id=o.id, item_id=item_id, batch_id=leg["batch_id"], qty=Decimal(leg["qty"]), cost=cost))
            code = acct.INVENTORY_ACCOUNT[items[item_id].kind]
            credit_lines[code] = credit_lines.get(code, Decimal(0)) + cost
            total += cost
    for m in o.materials:
        m.issued_qty = m.required_qty
    _post_wip(db, cu, o, "1310", credit_lines, total, "issue")
    o.wip_cost, o.input_cost = o.wip_cost + total, o.input_cost + total
    o.status, o.started_at, o.version = "IN_PROGRESS", datetime.now(timezone.utc), o.version + 1
    audit.record(db, user=cu, action="issue_materials", entity="production_order", entity_id=o.id, after={"cost": str(total)})
    return order_dict(o)


def _post_wip(db: Session, cu: CurrentUser, o: ProductionOrder, debit: str, credit_lines: dict[str, Decimal], total: Decimal, event: str) -> None:
    """Debit `debit`, credit each account; same-account legs are netted so a journal never has a pointless Dr X / Cr X."""
    net: dict[str, Decimal] = {debit: total}
    for c, v in credit_lines.items():
        net[c] = net.get(c, Decimal(0)) - v
    lines = [(c, v, Decimal(0)) if v > 0 else (c, Decimal(0), -v) for c, v in sorted(net.items()) if v != 0]
    if total and len(lines) >= 2:
        acct.post_journal(db, entry_date=business_today(), description=f"Production {o.number} {event}", source_type="production_order", source_id=o.id,
                          source_event=event, lines=lines, actor_id=cu.id)


def stage_action(db: Session, cu: CurrentUser, order_id: int, seq: int, action: str, *, equipment_id: int | None, parameters: dict | None, notes: str | None) -> dict:
    o = _lock_order(db, order_id)
    if o.status != "IN_PROGRESS":
        raise BusinessConflict("invalid_status", "Stages run on IN_PROGRESS orders (materials must be issued first)")
    st = next((s for s in o.stages if s.seq == seq), None)
    if not st:
        raise NotFound("Stage")
    if action == "start":
        if st.status != "PENDING":
            raise BusinessConflict("invalid_status", f"Stage is {st.status}")
        if any(s.status != "DONE" for s in o.stages if s.seq < seq):
            raise BusinessConflict("previous_stage_open", "Complete the previous stage first")
        if equipment_id and EQUIPMENT_GUARD:
            EQUIPMENT_GUARD(db, equipment_id)
        st.status, st.started_at, st.operator_id, st.equipment_id = "IN_PROGRESS", datetime.now(timezone.utc), cu.id, equipment_id
    else:
        if st.status != "IN_PROGRESS":
            raise BusinessConflict("invalid_status", "Only a started stage can be completed")
        from app.production.extras import check_stage_can_complete
        check_stage_can_complete(db, order_id, seq)                  # a failing in-process reading blocks completion until a passing re-check
        st.status, st.completed_at, st.parameters, st.notes = "DONE", datetime.now(timezone.utc), parameters, notes
    audit.record(db, user=cu, action=f"stage_{action}", entity="production_order", entity_id=o.id, after={"stage": st.name})
    return order_dict(o)


EQUIPMENT_GUARD = None     # set by the maintenance module: callable(db, equipment_id) raising BusinessConflict if unusable


def complete_order(db: Session, cu: CurrentUser, order_id: int, expected_version: int, actual_qty, wastage: list[dict]) -> dict:
    o = _lock_order(db, order_id, expected_version)
    if o.status != "IN_PROGRESS":
        raise BusinessConflict("invalid_status", f"Cannot complete an order in status {o.status}")
    if any(m.issued_qty < m.required_qty for m in o.materials):
        raise BusinessConflict("materials_not_issued", "All materials must be issued before completion")
    if any(s.status != "DONE" for s in o.stages):
        raise BusinessConflict("stages_open", "All production stages must be completed")
    actual = Decimal(str(actual_qty)).quantize(Q3)
    if actual > o.planned_qty * MAX_OVERYIELD:
        raise BusinessConflict("output_exceeds_plan", "Output exceeds 110 % of the planned quantity; check the measurement", planned=str(o.planned_qty))
    yield_pct = (actual / o.planned_qty * 100).quantize(Decimal("0.01"))
    if yield_pct < LOW_YIELD_PCT and not wastage:
        raise BusinessConflict("wastage_reason_required", f"Yield is {yield_pct}% (< {LOW_YIELD_PCT}%): record the wastage with its reason")
    for w in wastage:
        db.add(WastageRecord(order_id=o.id, category=w["category"], qty=w["qty"], reason=w["reason"], responsible_department=w.get("department")))
    out = db.get(Item, o.output_item_id)
    expiry = business_today() + timedelta(days=out.shelf_life_days) if out.shelf_life_days else None
    batch_id = inv.create_batch(db, item_id=out.id, supplier_id=None, supplier_lot_no=None, mfg_date=business_today(), expiry_date=expiry,
                                source_type="production_order", source_id=o.id)
    inv.receive(db, item_id=out.id, warehouse_id=o.warehouse_id, location_id=None, batch_id=batch_id, qty=actual, source_type="production_order", source_id=o.id,
                source_line=0, actor_id=cu.id, quarantine=True, movement="PRODUCTION_OUTPUT")      # quarantined until Quality releases it
    total = o.wip_cost
    vrows = acct.lock_valuations(db, [out.id])
    acct.val_receive(db, vrows, out.id, actual, total)
    _post_wip(db, cu, o, acct.INVENTORY_ACCOUNT[out.kind], {"1310": total}, total, "output")
    o.status, o.actual_qty, o.yield_pct, o.output_batch_id, o.version = "COMPLETED", actual, yield_pct, batch_id, o.version + 1
    o.unit_cost = (total / actual).quantize(Decimal("0.0001")) if actual else None     # wastage is absorbed into unit cost
    o.wip_cost, o.completed_at = Decimal(0), datetime.now(timezone.utc)
    quality.auto_inspection(db, batch_id, out.id, "FINISHED")
    audit.record(db, user=cu, action="complete", entity="production_order", entity_id=o.id, after={"actual": str(actual), "yield_pct": str(yield_pct), "batch_id": batch_id})
    outbox.add(db, event_type="production.order.completed", aggregate_type="production_order", aggregate_id=o.id,
               payload={"order_id": o.id, "number": o.number, "output_batch_id": batch_id, "actual_qty": str(actual), "yield_pct": str(yield_pct)})
    return order_dict(o)


def cancel_order(db: Session, cu: CurrentUser, order_id: int, reason: str) -> dict:
    o = _lock_order(db, order_id)
    if o.status not in ("PLANNED", "RELEASED"):
        raise BusinessConflict("materials_issued", f"An order in status {o.status} cannot be cancelled")
    ids = [r[0] for r in db.execute(text("SELECT id FROM stock_reservations WHERE reference_type='production_order' AND reference_id=:r AND status='ACTIVE'"), {"r": str(o.id)})]
    if ids:
        inv.release_reservations(db, ids)
    o.status, o.version = "CANCELLED", o.version + 1
    audit.record(db, user=cu, action="cancel", entity="production_order", entity_id=o.id, reason=reason)
    return order_dict(o)


# ------------------------------------------------------------------ genealogy
def trace_back(db: Session, batch_id: int, depth: int = 0) -> dict:
    b = db.execute(text("""SELECT ib.id, ib.batch_no, ib.item_id, i.item_code, ib.supplier_id, ib.supplier_lot_no, ib.source_type, ib.source_id, ib.qc_status
                             FROM inventory_batches ib JOIN items i ON i.id = ib.item_id WHERE ib.id = :b"""), {"b": batch_id}).mappings().first()
    if not b:
        raise NotFound("Batch")
    node = dict(b) | {"inputs": []}
    if b["source_type"] == "production_order" and depth < 6:
        o = db.get(ProductionOrder, b["source_id"])
        node["production_order"] = {"id": o.id, "number": o.number, "bom_id": o.bom_id, "formulation_version_id": o.formulation_version_id, "yield_pct": None if o.yield_pct is None else str(o.yield_pct)}
        for c in db.scalars(select(ProductionConsumption).where(ProductionConsumption.order_id == o.id).order_by(ProductionConsumption.id)):
            if c.batch_id:
                node["inputs"].append(trace_back(db, c.batch_id, depth + 1) | {"consumed_qty": str(c.qty)})
    elif b["source_type"] == "goods_receipt":
        r = db.execute(text("SELECT gr.number, gr.delivery_note_no, po.number AS po_number FROM goods_receipts gr JOIN purchase_orders po ON po.id = gr.po_id WHERE gr.id = :g"), {"g": b["source_id"]}).mappings().first()
        node["goods_receipt"] = dict(r) if r else None
    return node


def where_used(db: Session, batch_id: int, depth: int = 0) -> dict:
    node = {"batch_id": batch_id, "used_in": []}
    if depth >= 6:
        return node
    for c in db.execute(select(ProductionConsumption.order_id, func_sum()).where(ProductionConsumption.batch_id == batch_id).group_by(ProductionConsumption.order_id)):
        o = db.get(ProductionOrder, c[0])
        entry = {"production_order": o.number, "consumed_qty": str(c[1]), "output_batch_id": o.output_batch_id, "status": o.status}
        if o.output_batch_id:
            entry["downstream"] = where_used(db, o.output_batch_id, depth + 1)
        node["used_in"].append(entry)
    return node


def func_sum():
    from sqlalchemy import func
    return func.sum(ProductionConsumption.qty)


# ------------------------------------------------------------------ views
def bom_dict(b: Bom) -> dict:
    return {"id": b.id, "output_item_id": b.output_item_id, "output_qty": str(b.output_qty), "version_no": b.version_no, "status": b.status,
            "formulation_version_id": b.formulation_version_id, "lines": [{"line_no": l.line_no, "item_id": l.item_id, "qty": str(l.qty)} for l in b.lines]}


def order_dict(o: ProductionOrder) -> dict:
    return {"id": o.id, "number": o.number, "status": o.status, "version": o.version, "bom_id": o.bom_id, "formulation_version_id": o.formulation_version_id,
            "output_item_id": o.output_item_id, "planned_qty": str(o.planned_qty), "actual_qty": None if o.actual_qty is None else str(o.actual_qty),
            "yield_pct": None if o.yield_pct is None else str(o.yield_pct), "input_cost": str(o.input_cost), "wip_cost": str(o.wip_cost),
            "unit_cost": None if o.unit_cost is None else str(o.unit_cost), "output_batch_id": o.output_batch_id,
            "materials": [{"item_id": m.item_id, "required": str(m.required_qty), "issued": str(m.issued_qty)} for m in o.materials],
            "stages": [{"seq": s.seq, "name": s.name, "status": s.status} for s in o.stages]}
