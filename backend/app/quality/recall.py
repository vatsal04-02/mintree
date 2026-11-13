"""Batch recall: one action that finds everything a suspect batch touched, holds it, lists the customers to call, and tracks the case to closure.

What it uses, not duplicates: the traceability graph (`traceability.service.impact`) says which batches consumed the suspect one; shipment rows say which customers
received any of them; `inventory.set_qc_status` places each batch on HOLD through the same ledgered, audited path a QC hold uses. Nothing is deleted or reversed:
a recall only blocks stock and keeps the record. Stock already shipped is a customer matter (notify, collect, clear), tracked here per shipment.

Rules: a batch that is already HOLD or REJECTED is left as it is (recorded, not touched); a batch held by a recall can be released back only through this case, with a
note, and the case closes only when every customer is dealt with and every held batch is released or rejected. Reserved stock stays reserved but flagged: the case
lists the orders it affects so someone re-allocates them, rather than the system silently cancelling anything."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import activity
from app.core.context import source_var  # noqa: F401  (activity.emit reads it)
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.core.uow import run_tx
from app.inventory import service as inv
from app.notifications.service import notify
from app.tier1_models import RecallBatch, RecallCase, RecallCustomer
from app.traceability import service as trace

router = APIRouter(prefix="/quality/recalls", tags=["recall"])
DB = Depends(get_db, scope="function")
FULL = trace.Mask(True, True)
HOLDABLE = ("RELEASED", "QUARANTINE")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartIn(_In):
    batch_id: int
    reason: str = Field(min_length=5, max_length=500)
    severity: Literal["CRITICAL", "MAJOR", "MINOR"] = "MAJOR"


class NoteIn(_In):
    note: str = Field(min_length=3, max_length=500)


class DispositionIn(_In):
    disposition: Literal["NOTIFIED", "RETURNED", "CLEARED"]
    note: str | None = Field(None, max_length=500)


class CloseIn(_In):
    note: str = Field(min_length=5, max_length=500)


def _case(db: Session, cid: int, lock: bool = False) -> RecallCase:
    q = select(RecallCase).where(RecallCase.id == cid)
    c = db.scalar(q.with_for_update() if lock else q)
    if not c:
        raise NotFound("Recall case")
    return c


def _downstream(db: Session, root: int) -> tuple[set[int], bool]:
    """Every batch that consumed the root (directly or through bulk), plus the outputs of any rework that used one of them."""
    ids = {root}
    truncated = False
    frontier = {root}
    while frontier:
        new: set[int] = set()
        for b in sorted(frontier):
            imp = trace.impact(db, b, FULL)
            truncated = truncated or bool(imp.get("truncated"))
            new |= {a["id"] for a in imp["affected_batches"]}
        reworked = {r[0] for r in db.execute(text("SELECT output_batch_id FROM rework_orders WHERE source_batch_id = ANY(:i) AND output_batch_id IS NOT NULL"), {"i": sorted(ids | new)})}
        new |= reworked
        frontier = new - ids
        ids |= new
    return ids, truncated


def start(db: Session, cu: CurrentUser, batch_id: int, reason: str, severity: str) -> dict:
    root = trace.batch_row(db, batch_id)                                    # NotFound if it does not exist
    open_cover = db.execute(text("""SELECT c.number FROM recall_cases c JOIN recall_batches b ON b.case_id = c.id
                                     WHERE c.status = 'OPEN' AND b.batch_id = :b LIMIT 1"""), {"b": batch_id}).scalar()
    if open_cover:
        raise BusinessConflict("recall_exists", f"Batch {root['batch_no']} is already covered by open recall {open_cover}", number=open_cover)
    ids, truncated = _downstream(db, batch_id)
    n = db.execute(text("SELECT nextval('recall_number_seq')")).scalar_one()
    from app.core.clock import business_today
    case = RecallCase(number=f"RCL/{business_today():%y}/{n:05d}", root_batch_id=batch_id, reason=reason, severity=severity, created_by=cu.id)
    db.add(case)
    db.flush()
    bal = {r[0]: (r[1], r[2]) for r in db.execute(text("SELECT batch_id, COALESCE(sum(qty_physical),0), COALESCE(sum(qty_reserved),0) FROM stock_balances WHERE batch_id = ANY(:i) GROUP BY batch_id"), {"i": sorted(ids)})}
    held = 0
    for bid in sorted(ids):
        status = db.execute(text("SELECT qc_status FROM inventory_batches WHERE id = :i"), {"i": bid}).scalar_one()
        did_hold = status in HOLDABLE
        if did_hold:
            inv.set_qc_status(db, batch_id=bid, decision="HOLD", reason=f"Recall {case.number}: {reason}", actor_id=cu.id)
            held += 1
        phys, resv = bal.get(bid, (Decimal(0), Decimal(0)))
        db.add(RecallBatch(case_id=case.id, batch_id=bid, role="SUSPECT" if bid == batch_id else "AFFECTED", prior_status=status, held_by_recall=did_hold, qty_physical=phys, qty_reserved=resv))
    rows = db.execute(text("""SELECT o.customer_id, s.so_id, s.id AS shipment_id, sl.batch_id, sum(sl.qty) AS qty
                                FROM shipment_lines sl JOIN shipments s ON s.id = sl.shipment_id JOIN sales_orders o ON o.id = s.so_id
                               WHERE sl.batch_id = ANY(:i) GROUP BY o.customer_id, s.so_id, s.id, sl.batch_id ORDER BY o.customer_id, s.id"""), {"i": sorted(ids)}).mappings().all()
    for r in rows:
        db.add(RecallCustomer(case_id=case.id, customer_id=r["customer_id"], so_id=r["so_id"], shipment_id=r["shipment_id"], batch_id=r["batch_id"], qty=r["qty"]))
    reserved_orders = [r[0] for r in db.execute(text("""SELECT DISTINCT o.number FROM stock_reservations r JOIN stock_balances b ON b.id = r.balance_id
                                                        JOIN sales_orders o ON r.reference_type = 'sales_order' AND r.reference_id = o.id::text
                                                       WHERE b.batch_id = ANY(:i) AND r.status = 'ACTIVE' ORDER BY 1"""), {"i": sorted(ids)})]
    case.summary = {"batches": len(ids), "held_now": held, "customers": len({r["customer_id"] for r in rows}), "shipments": len({r["shipment_id"] for r in rows}),
                    "shipped_qty": str(sum((r["qty"] for r in rows), Decimal(0))), "orders_with_reserved_stock": reserved_orders, "truncated": truncated, "root_batch_no": root["batch_no"]}
    audit.record(db, user=cu, action="start", entity="recall_case", entity_id=case.id, after={"number": case.number, "batch": root["batch_no"], **{k: v for k, v in case.summary.items() if isinstance(v, (int, str))}}, reason=reason)
    activity.emit(db, "quality.recall.opened", entity="recall_case", entity_id=case.id, number=case.number, actor_id=cu.id, severity=severity, batches=len(ids), customers=case.summary["customers"])
    db.flush()
    notify(db, kind="quality.recall", title=f"Recall {case.number} opened on batch {root['batch_no']}", body=f"{len(ids)} batches held, {case.summary['customers']} customers to contact. {reason}",
           link=f"/quality/recalls/{case.id}", permission="quality.recall:read", priority="CRITICAL" if severity == "CRITICAL" else "HIGH", exclude_user_id=cu.id)
    return detail(db, case)


def detail(db: Session, c: RecallCase) -> dict:
    batches = db.execute(text("""SELECT rb.batch_id, b.batch_no, i.item_code, i.name AS item_name, rb.role, rb.prior_status, b.qc_status AS status_now, rb.held_by_recall, rb.qty_physical, rb.qty_reserved, rb.released_at
                                   FROM recall_batches rb JOIN inventory_batches b ON b.id = rb.batch_id JOIN items i ON i.id = b.item_id WHERE rb.case_id = :c ORDER BY rb.role DESC, b.id"""), {"c": c.id}).mappings().all()
    custs = db.execute(text("""SELECT rc.id, rc.customer_id, cu.name AS customer, cu.code AS customer_code, rc.so_id, o.number AS order_number, rc.shipment_id, s.awb, rb.batch_no, rc.qty, rc.disposition, rc.notified_at, rc.note
                                 FROM recall_customers rc JOIN customers cu ON cu.id = rc.customer_id LEFT JOIN sales_orders o ON o.id = rc.so_id LEFT JOIN shipments s ON s.id = rc.shipment_id
                                 JOIN inventory_batches rb ON rb.id = rc.batch_id WHERE rc.case_id = :c ORDER BY cu.name, rc.id"""), {"c": c.id}).mappings().all()
    pend_c = sum(1 for r in custs if r["disposition"] == "PENDING")
    held_open = sum(1 for r in batches if r["held_by_recall"] and r["released_at"] is None and r["status_now"] == "HOLD")
    return {"id": c.id, "number": c.number, "status": c.status, "severity": c.severity, "reason": c.reason, "root_batch_id": c.root_batch_id, "summary": c.summary,
            "created_at": c.created_at.isoformat() if c.created_at else None, "closed_at": c.closed_at.isoformat() if c.closed_at else None, "closure_note": c.closure_note,
            "progress": {"customers_total": len(custs), "customers_pending": pend_c, "batches_total": len(batches), "batches_still_held": held_open, "can_close": c.status == "OPEN" and pend_c == 0 and held_open == 0},
            "batches": [{**dict(r), "qty_physical": str(r["qty_physical"]), "qty_reserved": str(r["qty_reserved"]), "released_at": r["released_at"].isoformat() if r["released_at"] else None} for r in batches],
            "customers": [{**dict(r), "qty": str(r["qty"]), "notified_at": r["notified_at"].isoformat() if r["notified_at"] else None} for r in custs]}


def release_batch(db: Session, cu: CurrentUser, cid: int, batch_id: int, decision: str, note: str) -> dict:
    c = _case(db, cid, lock=True)
    if c.status != "OPEN":
        raise BusinessConflict("recall_closed", "This recall is closed")
    rb = db.scalar(select(RecallBatch).where(RecallBatch.case_id == cid, RecallBatch.batch_id == batch_id))
    if not rb or not rb.held_by_recall:
        raise BusinessConflict("not_held_by_recall", "Only a batch this recall placed on hold can be resolved here")
    now = db.execute(text("SELECT qc_status FROM inventory_batches WHERE id = :i"), {"i": batch_id}).scalar_one()
    if now != "HOLD":
        raise BusinessConflict("not_on_hold", f"The batch is {now}, not on hold")
    inv.set_qc_status(db, batch_id=batch_id, decision=decision, reason=f"Recall {c.number}: {note}", actor_id=cu.id)
    rb.released_at = datetime.now(timezone.utc)
    audit.record(db, user=cu, action=f"recall_{decision.lower()}", entity="recall_case", entity_id=cid, after={"batch_id": batch_id}, reason=note)
    activity.emit(db, "quality.recall.batch_resolved", entity="recall_case", entity_id=cid, number=c.number, actor_id=cu.id, decision=decision)
    db.flush()
    return detail(db, c)


def set_customer(db: Session, cu: CurrentUser, cid: int, rid: int, disposition: str, note: str | None) -> dict:
    c = _case(db, cid, lock=True)
    if c.status != "OPEN":
        raise BusinessConflict("recall_closed", "This recall is closed")
    rc = db.scalar(select(RecallCustomer).where(RecallCustomer.id == rid, RecallCustomer.case_id == cid).with_for_update())
    if not rc:
        raise NotFound("Recall customer line")
    rc.disposition, rc.note = disposition, note or rc.note
    if disposition == "NOTIFIED":
        rc.notified_at, rc.notified_by = datetime.now(timezone.utc), cu.id
    audit.record(db, user=cu, action=f"customer_{disposition.lower()}", entity="recall_case", entity_id=cid, after={"line": rid, "customer_id": rc.customer_id}, reason=note)
    db.flush()
    return detail(db, c)


def set_all_customers(db: Session, cu: CurrentUser, cid: int, disposition: str, note: str | None, only_pending: bool) -> dict:
    """One call for the common case: every customer line that is still pending (or every line) gets the same disposition."""
    c = _case(db, cid, lock=True)
    if c.status != "OPEN":
        raise BusinessConflict("recall_closed", "This recall is closed")
    q = select(RecallCustomer).where(RecallCustomer.case_id == cid).with_for_update()
    if only_pending:
        q = q.where(RecallCustomer.disposition == "PENDING")
    n = 0
    for rc in db.scalars(q):
        rc.disposition, rc.note = disposition, note or rc.note
        if disposition == "NOTIFIED":
            rc.notified_at, rc.notified_by = datetime.now(timezone.utc), cu.id
        n += 1
    audit.record(db, user=cu, action=f"customers_all_{disposition.lower()}", entity="recall_case", entity_id=cid, after={"lines": n}, reason=note)
    db.flush()
    return detail(db, c)


def close(db: Session, cu: CurrentUser, cid: int, note: str) -> dict:
    c = _case(db, cid, lock=True)
    if c.status != "OPEN":
        raise BusinessConflict("recall_closed", "This recall is already closed")
    d = detail(db, c)
    if d["progress"]["customers_pending"]:
        raise BusinessConflict("customers_pending", f"{d['progress']['customers_pending']} customer line(s) have not been dealt with")
    if d["progress"]["batches_still_held"]:
        raise BusinessConflict("batches_still_held", f"{d['progress']['batches_still_held']} batch(es) are still on hold: release or reject each one")
    c.status, c.closed_by, c.closed_at, c.closure_note = "CLOSED", cu.id, datetime.now(timezone.utc), note
    audit.record(db, user=cu, action="close", entity="recall_case", entity_id=cid, reason=note)
    activity.emit(db, "quality.recall.closed", entity="recall_case", entity_id=cid, number=c.number, actor_id=cu.id)
    db.flush()
    return detail(db, c)


# ------------------------------------------------------------------ routes
@router.post("", status_code=201)
def start_route(body: StartIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.recall:create", "inventory.batch:read", "inventory.batch:qc_release"))):
    return run_tx(db, lambda d: start(d, cu, body.batch_id, body.reason, body.severity), name="quality.recall.start")


@router.get("")
def list_route(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB, _: CurrentUser = Depends(require("quality.recall:read"))):
    q = select(RecallCase).order_by(RecallCase.id.desc())
    if status:
        q = q.where(RecallCase.status == status)
    rows = db.scalars(q.limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": [{"id": c.id, "number": c.number, "status": c.status, "severity": c.severity, "reason": c.reason, "created_at": c.created_at.isoformat(), "root_batch_no": (c.summary or {}).get("root_batch_no"),
                       "batches": (c.summary or {}).get("batches"), "customers": (c.summary or {}).get("customers")} for c in rows], "page": page}


@router.get("/{cid}")
def get_route(cid: int, db: Session = DB, _: CurrentUser = Depends(require("quality.recall:read"))):
    return detail(db, _case(db, cid))


@router.post("/{cid}/batches/{batch_id}/release")
def release_route(cid: int, batch_id: int, body: NoteIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.recall:close", "inventory.batch:qc_release"))):
    return run_tx(db, lambda d: release_batch(d, cu, cid, batch_id, "RELEASED", body.note), name="quality.recall.release")


@router.post("/{cid}/batches/{batch_id}/reject")
def reject_route(cid: int, batch_id: int, body: NoteIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.recall:close", "inventory.batch:qc_release"))):
    return run_tx(db, lambda d: release_batch(d, cu, cid, batch_id, "REJECTED", body.note), name="quality.recall.reject")


class BulkIn(_In):
    disposition: Literal["NOTIFIED", "RETURNED", "CLEARED"]
    note: str | None = Field(None, max_length=500)
    only_pending: bool = True


@router.post("/{cid}/customers")
def customers_all_route(cid: int, body: BulkIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.recall:notify"))):
    return run_tx(db, lambda d: set_all_customers(d, cu, cid, body.disposition, body.note, body.only_pending), name="quality.recall.customers_all")


@router.post("/{cid}/customers/{rid}")
def customer_route(cid: int, rid: int, body: DispositionIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.recall:notify"))):
    return run_tx(db, lambda d: set_customer(d, cu, cid, rid, body.disposition, body.note), name="quality.recall.customer")


@router.post("/{cid}/close")
def close_route(cid: int, body: CloseIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.recall:close"))):
    return run_tx(db, lambda d: close(d, cu, cid, body.note), name="quality.recall.close")


@router.get("/{cid}/notice.csv")
def notice_csv(cid: int, db: Session = DB, _: CurrentUser = Depends(require("quality.recall:read"))):
    """The call list: one row per shipment line to chase, with the customer's contact details, ready for the person making the calls."""
    d = detail(db, _case(db, cid))
    contacts = {r[0]: r[1:] for r in db.execute(text("SELECT customer_id, name, phone, email FROM customer_contacts WHERE is_primary"))} if _table(db, "customer_contacts") else {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Recall", "Customer", "Order", "AWB", "Batch", "Quantity", "Status", "Contact", "Phone", "Email"])
    for r in d["customers"]:
        ct = contacts.get(r["customer_id"], (None, None, None))
        w.writerow([d["number"], r["customer"], r["order_number"], r["awb"], r["batch_no"], r["qty"], r["disposition"], ct[0], ct[1], ct[2]])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{d["number"].replace("/", "-")}-call-list.csv"'})


def _table(db: Session, name: str) -> bool:
    return bool(db.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = 'is_primary'"), {"t": name}).first())
