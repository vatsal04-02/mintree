"""RFQ -> supplier quotations -> comparison -> recommendation -> award (creates the purchase order). Also delivery tracking and supplier performance.

Comparison uses only what suppliers quoted. A quotation is *complete* when it prices every RFQ line. The recommendation is the complete quotation with the
lowest landed total (lines + tax + freight); ties go to the shorter lead time. Awarding anything other than the recommendation needs a written reason.
Award runs `create_po` in the same transaction, so there is exactly one PO per awarded RFQ (RFQ row is locked; status flips to AWARDED)."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from psycopg import errors as pgerr
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.masters.models import Item, Supplier
from app.notifications.service import notify
from app.procurement import service as proc
from app.procurement.rfq_models import Quotation, QuotationItem, Rfq, RfqItem, RfqSupplier
from app.core.clock import business_today

M = Decimal("0.01")


def _num(db: Session, seq: str, prefix: str) -> str:
    return f"{prefix}/{business_today():%y}/{db.execute(text('SELECT nextval(:s)'), {'s': seq}).scalar_one():06d}"


def _rfq(db: Session, rid: int, lock: bool = False) -> Rfq:
    q = select(Rfq).where(Rfq.id == rid)
    r = db.scalar(q.with_for_update() if lock else q)
    if not r:
        raise NotFound("RFQ")
    return r


def create_rfq(db: Session, cu: CurrentUser, *, items: list[dict], supplier_ids: list[int], due_date: date | None, notes: str | None, pr_id: int | None) -> dict:
    if len({i["item_id"] for i in items}) != len(items):
        raise Unprocessable("An item may appear once per RFQ")
    if len(set(supplier_ids)) != len(supplier_ids) or len(supplier_ids) < 1:
        raise Unprocessable("Invite at least one distinct supplier")
    found = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_([i["item_id"] for i in items])))}
    for i in items:
        it = found.get(i["item_id"])
        if not it or not it.is_active or it.kind not in ("RM", "PM"):
            raise Unprocessable(f"Item {i['item_id']} is not an active raw/packaging material")
    sups = {s.id: s for s in db.scalars(select(Supplier).where(Supplier.id.in_(supplier_ids)))}
    for sid in supplier_ids:
        s = sups.get(sid)
        if not s or not s.is_active or s.approval_status != "APPROVED":
            raise BusinessConflict("supplier_not_approved", f"Supplier {sid} is not an approved, active supplier")
    r = Rfq(number=_num(db, "rfq_seq", "RFQ"), pr_id=pr_id, due_date=due_date, notes=notes, created_by=cu.id)
    r.items = [RfqItem(line_no=n, item_id=i["item_id"], qty=i["qty"]) for n, i in enumerate(items, 1)]
    r.suppliers = [RfqSupplier(supplier_id=s) for s in supplier_ids]
    db.add(r)
    db.flush()
    audit.record(db, user=cu, action="create", entity="rfq", entity_id=r.id, after={"number": r.number, "suppliers": supplier_ids})
    return rfq_dict(r)


def send_rfq(db: Session, cu: CurrentUser, rid: int) -> dict:
    r = _rfq(db, rid, lock=True)
    if r.status != "DRAFT":
        raise BusinessConflict("invalid_status", f"Cannot send an RFQ in status {r.status}")
    r.status, r.version = "SENT", r.version + 1
    audit.record(db, user=cu, action="send", entity="rfq", entity_id=r.id)
    return rfq_dict(r)


def record_quotation(db: Session, cu: CurrentUser, rid: int, *, supplier_id: int, lines: list[dict], lead_time_days: int | None, valid_until: date | None,
                     payment_terms_days: int | None, freight: Decimal, notes: str | None) -> dict:
    r = _rfq(db, rid, lock=True)
    if r.status != "SENT":
        raise BusinessConflict("invalid_status", "Quotations can be recorded only while the RFQ is open (SENT)")
    if supplier_id not in {s.supplier_id for s in r.suppliers}:
        raise BusinessConflict("supplier_not_invited", "This supplier was not invited to the RFQ")
    by_item = {i.item_id: i for i in r.items}
    seen = set()
    rows = []
    for l in lines:
        ri = by_item.get(l["item_id"])
        if not ri:
            raise Unprocessable(f"Item {l['item_id']} is not on this RFQ")
        if ri.id in seen:
            raise Unprocessable("A line may be quoted once")
        seen.add(ri.id)
        rows.append(QuotationItem(rfq_item_id=ri.id, unit_price=l["unit_price"], tax_rate=l.get("tax_rate") or 0))
    if valid_until and valid_until < business_today():
        raise Unprocessable("The quotation has already expired")
    q = Quotation(number=_num(db, "quotation_seq", "QT"), rfq_id=r.id, supplier_id=supplier_id, lead_time_days=lead_time_days, valid_until=valid_until,
                  payment_terms_days=payment_terms_days, freight=freight, notes=notes, recorded_by=cu.id)
    q.items = rows
    db.add(q)
    try:
        db.flush()
    except IntegrityError as ex:
        if isinstance(ex.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_quotation", "This supplier already has a quotation on the RFQ") from ex
        raise
    audit.record(db, user=cu, action="quote", entity="rfq", entity_id=r.id, after={"quotation": q.number, "supplier_id": supplier_id})
    notify(db, kind="rfq.quotation", title=f"Quotation received on {r.number}", link=f"/procurement/rfqs/{r.id}", permission="procurement.rfq:award", exclude_user_id=cu.id)
    return {"id": q.id, "number": q.number, "supplier_id": supplier_id, "lines": len(rows)}


def compare(db: Session, rid: int) -> dict:
    r = _rfq(db, rid)
    qs = list(db.scalars(select(Quotation).where(Quotation.rfq_id == rid, Quotation.status != "REJECTED").order_by(Quotation.id)))
    qty = {i.id: i.qty for i in r.items}
    rows = []
    for q in qs:
        priced = {qi.rfq_item_id: qi for qi in q.items}
        lines, total = [], Decimal(0)
        for ri in r.items:
            qi = priced.get(ri.id)
            if qi:
                net = (qty[ri.id] * qi.unit_price).quantize(M, ROUND_HALF_UP)
                tot = net + (net * qi.tax_rate / 100).quantize(M, ROUND_HALF_UP)
                total += tot
                lines.append({"rfq_item_id": ri.id, "item_id": ri.item_id, "unit_price": str(qi.unit_price), "line_total": str(tot)})
            else:
                lines.append({"rfq_item_id": ri.id, "item_id": ri.item_id, "unit_price": None, "line_total": None})
        expired = bool(q.valid_until and q.valid_until < business_today())
        rows.append({"quotation_id": q.id, "number": q.number, "supplier_id": q.supplier_id, "complete": len(priced) == len(r.items), "expired": expired,
                     "lead_time_days": q.lead_time_days, "payment_terms_days": q.payment_terms_days, "freight": str(q.freight),
                     "landed_total": str(total + q.freight), "lines": lines, "_t": total + q.freight})
    eligible = [x for x in rows if x["complete"] and not x["expired"]]
    best = min(eligible, key=lambda x: (x["_t"], x["lead_time_days"] if x["lead_time_days"] is not None else 10**6, x["quotation_id"]), default=None)
    for x in rows:
        x.pop("_t")
    low = {}
    for ri in r.items:
        prices = [(Decimal(l["unit_price"]), x["supplier_id"]) for x in rows for l in x["lines"] if l["rfq_item_id"] == ri.id and l["unit_price"] is not None]
        low[ri.id] = min(prices)[1] if prices else None
    invited = {s.supplier_id for s in r.suppliers}
    return {"rfq": rfq_dict(r), "quotations": rows, "recommended_quotation_id": best["quotation_id"] if best else None,
            "lowest_price_supplier_by_rfq_item": low, "not_yet_quoted_supplier_ids": sorted(invited - {x["supplier_id"] for x in rows}),
            "basis": "landed total = line value + tax + freight; complete and unexpired quotations only; ties by shorter lead time"}


def award(db: Session, cu: CurrentUser, rid: int, quotation_id: int, reason: str | None) -> dict:
    r = _rfq(db, rid, lock=True)
    if r.status != "SENT":
        raise BusinessConflict("invalid_status", f"Cannot award an RFQ in status {r.status}")
    cmp = compare(db, rid)
    chosen = next((x for x in cmp["quotations"] if x["quotation_id"] == quotation_id), None)
    if not chosen:
        raise NotFound("Quotation")
    if not chosen["complete"] or chosen["expired"]:
        raise BusinessConflict("quotation_not_eligible", "Only a complete, unexpired quotation can be awarded")
    if quotation_id != cmp["recommended_quotation_id"] and not (reason and reason.strip()):
        raise BusinessConflict("reason_required", "Awarding a quotation other than the recommended one needs a written reason")
    q = db.get(Quotation, quotation_id)
    rfq_items = {i.id: i for i in r.items}
    po = proc.create_po(db, cu, supplier_id=q.supplier_id, pr_id=r.pr_id, expected_date=None, notes=f"Awarded from {r.number} / {q.number}",
                        items=[{"item_id": rfq_items[qi.rfq_item_id].item_id, "qty": rfq_items[qi.rfq_item_id].qty, "unit_price": qi.unit_price, "tax_rate": qi.tax_rate} for qi in q.items])
    if q.lead_time_days:
        from datetime import timedelta
        db.execute(text("UPDATE purchase_orders SET expected_date = :d WHERE id = :i"), {"d": business_today() + timedelta(days=q.lead_time_days), "i": po["id"]})
    for other in db.scalars(select(Quotation).where(Quotation.rfq_id == rid)):
        other.status = "AWARDED" if other.id == quotation_id else "REJECTED"
    r.status, r.awarded_quotation_id, r.awarded_po_id, r.award_reason, r.version = "AWARDED", quotation_id, po["id"], reason, r.version + 1
    audit.record(db, user=cu, action="award", entity="rfq", entity_id=r.id, after={"quotation": q.number, "po": po["number"], "recommended": quotation_id == cmp["recommended_quotation_id"]}, reason=reason)
    notify(db, kind="rfq.awarded", title=f"{r.number} awarded; {po['number']} drafted", link=f"/procurement/purchase-orders/{po['id']}", user_ids=[r.created_by], exclude_user_id=cu.id)
    return {"rfq": rfq_dict(r), "purchase_order": po}


def cancel(db: Session, cu: CurrentUser, rid: int) -> dict:
    r = _rfq(db, rid, lock=True)
    if r.status in ("AWARDED", "CANCELLED"):
        raise BusinessConflict("invalid_status", f"Cannot cancel an RFQ in status {r.status}")
    r.status, r.version = "CANCELLED", r.version + 1
    audit.record(db, user=cu, action="cancel", entity="rfq", entity_id=r.id)
    return rfq_dict(r)


def rfq_dict(r: Rfq) -> dict:
    return {"id": r.id, "number": r.number, "status": r.status, "version": r.version, "pr_id": r.pr_id, "due_date": r.due_date,
            "items": [{"id": i.id, "line_no": i.line_no, "item_id": i.item_id, "qty": str(i.qty)} for i in r.items],
            "supplier_ids": [s.supplier_id for s in r.suppliers], "awarded_po_id": r.awarded_po_id, "award_reason": r.award_reason}


# ------------------------------------------------------------------ delivery tracking + supplier performance (computed from receipts)
def delivery_tracking(db: Session, supplier_id: int | None = None) -> list[dict]:
    rows = db.execute(text("""
        SELECT p.id, p.number, p.supplier_id, p.status, p.expected_date,
               sum(i.qty_ordered) AS ordered, sum(i.qty_received) AS received,
               CASE WHEN p.expected_date IS NOT NULL AND p.expected_date < business_today() THEN business_today() - p.expected_date ELSE 0 END AS days_late
          FROM purchase_orders p JOIN purchase_order_items i ON i.po_id = p.id
         WHERE p.status IN ('APPROVED','PARTIALLY_RECEIVED') AND (CAST(:s AS bigint) IS NULL OR p.supplier_id = :s)
         GROUP BY p.id ORDER BY days_late DESC, p.expected_date NULLS LAST, p.id"""), {"s": supplier_id}).mappings().all()
    return [{"po_id": r["id"], "number": r["number"], "supplier_id": r["supplier_id"], "status": r["status"], "expected_date": r["expected_date"],
             "ordered": str(r["ordered"]), "received": str(r["received"]), "percent_received": float(round(100 * r["received"] / r["ordered"], 1)),
             "days_late": r["days_late"], "state": "LATE" if r["days_late"] else ("PARTIAL" if r["received"] > 0 else "AWAITING_DELIVERY")} for r in rows]


def supplier_performance(db: Session) -> list[dict]:
    """On-time = a receipt dated on/before the PO expected date; fill rate = received/ordered over closed and open POs with receipts; price variance is not guessed."""
    rows = db.execute(text("""
        WITH r AS (SELECT p.supplier_id, p.id po_id, p.expected_date, min(g.received_at)::date first_receipt
                     FROM purchase_orders p JOIN goods_receipts g ON g.po_id = p.id AND g.status = 'POSTED' GROUP BY p.id),
             q AS (SELECT p.supplier_id, sum(i.qty_ordered) ordered, sum(i.qty_received) received, count(DISTINCT p.id) pos
                     FROM purchase_orders p JOIN purchase_order_items i ON i.po_id = p.id WHERE p.status NOT IN ('DRAFT','PENDING_APPROVAL','CANCELLED') GROUP BY p.supplier_id)
        SELECT s.id, s.code, s.name, s.rating, q.pos, q.ordered, q.received,
               count(r.po_id) FILTER (WHERE r.expected_date IS NOT NULL) AS dated_receipts,
               count(r.po_id) FILTER (WHERE r.expected_date IS NOT NULL AND r.first_receipt <= r.expected_date) AS on_time
          FROM suppliers s JOIN q ON q.supplier_id = s.id LEFT JOIN r ON r.supplier_id = s.id
         GROUP BY s.id, q.pos, q.ordered, q.received ORDER BY s.name"""), {}).mappings().all()
    return [{"supplier_id": x["id"], "code": x["code"], "name": x["name"], "rating": None if x["rating"] is None else str(x["rating"]), "purchase_orders": x["pos"],
             "fill_rate_pct": float(round(100 * x["received"] / x["ordered"], 1)) if x["ordered"] else None,
             "on_time_pct": float(round(100 * x["on_time"] / x["dated_receipts"], 1)) if x["dated_receipts"] else None, "dated_receipts": x["dated_receipts"]} for x in rows]
