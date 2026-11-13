"""Customer invoice drafts: review what will be billed, validate it, then post it.

In this system a customer invoice is born when goods ship (one gapless, GST-correct invoice per shipment, posted atomically with the stock-out and the cost of goods).
A DRAFT is the reviewed step before that: it lists the eligible lines (ordered quantity not yet shipped), previews the exact taxable value and GST that posting will bill
(the same arithmetic as dispatch, `line_amounts`), and records nothing financial. Only POSTING does anything real, and it does it through the existing dispatch posting,
so the gapless invoice number, the balanced journal, revenue recognition, stock movements, shipment and audit are exactly those of an ordinary dispatch.

  DRAFT  --validate-->  VALIDATED  --post-->  POSTED (a real invoice exists, revenue and receivable are recognised)
  DRAFT/VALIDATED --cancel--> CANCELLED (nothing was ever posted)

A draft creates no journal entry, no invoice number and no revenue. At most one open draft exists per order; quantities can never exceed what is still unshipped."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.audit import service as audit
from app.core import activity
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.dispatch import service as dispatch
from app.dispatch.ext_models import InvoiceDraft, InvoiceDraftLine
from app.dispatch.models import SalesOrder

OPEN = ("DRAFT", "VALIDATED")
CREATABLE_FROM = ("CONFIRMED", "ALLOCATED", "PACKED", "PARTIALLY_DISPATCHED")
POSTABLE_FROM = ("PACKED", "PARTIALLY_DISPATCHED")


def _order(db: Session, oid: int, lock: bool = False) -> SalesOrder:
    q = select(SalesOrder).where(SalesOrder.id == oid)
    o = db.scalar(q.with_for_update() if lock else q)
    if not o:
        raise NotFound("Sales order")
    return o


def eligible_lines(db: Session, o: SalesOrder) -> list[dict]:
    """Order lines with quantity still to ship (and therefore still to bill)."""
    names = {r[0]: (r[1], r[2]) for r in db.execute(text("SELECT id, item_code, name FROM items WHERE id = ANY(:i)"), {"i": [l.item_id for l in o.items]})}
    return [{"so_item_id": l.id, "line_no": l.line_no, "item_id": l.item_id, "item_code": names[l.item_id][0], "item_name": names[l.item_id][1], "ordered": str(l.qty),
             "shipped": str(l.qty_shipped), "remaining": str(l.qty - l.qty_shipped), "unit_price": str(l.unit_price), "tax_rate": str(l.tax_rate)}
            for l in sorted(o.items, key=lambda x: x.line_no) if l.qty - l.qty_shipped > 0]


def _plan(o: SalesOrder, lines: list[dict] | None) -> dict[int, Decimal]:
    return dispatch._shipment_plan(o, lines)                       # the dispatch rules: known line, positive, never above what is unshipped


def create(db: Session, cu: CurrentUser, order_id: int, lines: list[dict] | None) -> dict:
    o = _order(db, order_id, lock=True)
    if o.status not in CREATABLE_FROM:
        raise BusinessConflict("invalid_status", f"An invoice cannot be drafted for an order in status {o.status}")
    if db.scalar(select(InvoiceDraft.id).where(InvoiceDraft.so_id == o.id, InvoiceDraft.status.in_(OPEN))):
        raise BusinessConflict("draft_exists", "This order already has an open invoice draft; post or cancel it first")
    plan = _plan(o, lines)
    by_id = {l.id: l for l in o.items}
    d = InvoiceDraft(so_id=o.id, customer_id=o.customer_id, status="DRAFT", subtotal=Decimal(0), tax_total=Decimal(0), total=Decimal(0), created_by=cu.id)
    sub = tax = Decimal(0)
    for so_item_id in sorted(plan, key=lambda i: by_id[i].line_no):
        l, take = by_id[so_item_id], plan[so_item_id]
        net, line_tax = dispatch.line_amounts(db, l, take)
        sub, tax = sub + net, tax + line_tax
        d.lines.append(InvoiceDraftLine(so_item_id=l.id, item_id=l.item_id, qty=take, unit_price=l.unit_price, tax_rate=l.tax_rate, net_amount=net, tax_amount=line_tax))
    d.subtotal, d.tax_total, d.total = sub, tax, sub + tax
    db.add(d)
    db.flush()
    audit.record(db, user=cu, action="create", entity="invoice_draft", entity_id=d.id, after={"order": o.number, "total": str(d.total)})
    activity.emit(db, "accounts.invoice.drafted", entity="invoice_draft", entity_id=d.id, number=f"Draft {d.id} for {o.number}", actor_id=cu.id, total=d.total)
    return draft_dict(db, d)


def _get(db: Session, did: int, lock: bool = False) -> InvoiceDraft:
    q = select(InvoiceDraft).where(InvoiceDraft.id == did)
    d = db.scalar(q.with_for_update() if lock else q)
    if not d:
        raise NotFound("Invoice draft")
    return d


def problems(db: Session, d: InvoiceDraft) -> tuple[list[str], list[str]]:
    """-> (errors, warnings). Errors would stop this draft being posted right now; warnings are things a person should know but that the law and the books do not require fixing."""
    out: list[str] = []
    warn: list[str] = []
    o = _order(db, d.so_id)
    if o.status not in POSTABLE_FROM:
        out.append(f"The order is {o.status}; it must be PACKED (or partly dispatched) before the invoice can be posted")
    by_id = {l.id: l for l in o.items}
    for ln in d.lines:
        il = by_id[ln.so_item_id]
        if ln.qty > il.qty - il.qty_shipped:
            out.append(f"Line {il.line_no}: {ln.qty} to invoice but only {il.qty - il.qty_shipped} is left unshipped")
    cust = db.execute(text("SELECT name, billing_address, gstin, state FROM customers WHERE id = :c"), {"c": d.customer_id}).mappings().one()
    if not (cust["billing_address"] or "").strip():
        # a tax invoice to a GST-registered buyer must carry the buyer's address; to an unregistered buyer it is advisable
        (out if cust["gstin"] else warn).append(f"{cust['name']} has no billing address on record" + ("" if cust["gstin"] else " (the invoice will show none)"))
    if not (cust["gstin"] or cust["state"]):
        warn.append(f"{cust['name']} has neither a GSTIN nor a state: the invoice is treated as intra-state (CGST + SGST)")
    # the figures must still be what posting will bill
    sub = tax = Decimal(0)
    for ln in d.lines:
        net, t = dispatch.line_amounts(db, by_id[ln.so_item_id], ln.qty)
        sub, tax = sub + net, tax + t
    if (sub, tax) != (d.subtotal, d.tax_total):
        out.append("Amounts have changed since the draft was created (another shipment was posted); create a new draft")
    return out, warn


def validate(db: Session, cu: CurrentUser, did: int) -> dict:
    d = _get(db, did, lock=True)
    if d.status not in OPEN:
        raise BusinessConflict("invalid_status", f"A {d.status.lower()} draft cannot be validated")
    errs, warns = problems(db, d)
    if errs:
        raise BusinessConflict("draft_not_valid", errs[0] + (f" (and {len(errs) - 1} more)" if len(errs) > 1 else ""), errors=errs, warnings=warns)
    d.status, d.validated_by, d.validated_at = "VALIDATED", cu.id, text("now()")
    audit.record(db, user=cu, action="validate", entity="invoice_draft", entity_id=d.id)
    db.flush()
    db.refresh(d)
    return {"valid": True, "errors": [], "warnings": warns, "draft": draft_dict(db, d)}


def post(db: Session, cu: CurrentUser, did: int, courier: str | None) -> dict:
    d = _get(db, did, lock=True)
    if d.status == "POSTED":
        raise BusinessConflict("already_posted", "This draft has already been posted", invoice_id=d.posted_invoice_id)
    if d.status != "VALIDATED":
        raise BusinessConflict("not_validated", "Validate the draft before posting it")
    errs, _ = problems(db, d)
    if errs:
        raise BusinessConflict("draft_not_postable", errs[0], errors=errs)
    o = _order(db, d.so_id, lock=True)
    res = dispatch.dispatch_order(db, cu, o.id, o.version, courier, [{"so_item_id": ln.so_item_id, "qty": ln.qty} for ln in d.lines])
    d.status, d.posted_invoice_id, d.posted_by, d.posted_at = "POSTED", res["invoice"]["id"], cu.id, text("now()")
    audit.record(db, user=cu, action="post", entity="invoice_draft", entity_id=d.id, after={"invoice": res["invoice"]["number"], "shipment_id": res["shipment_id"]})
    db.flush()
    db.refresh(d)
    return {"draft": draft_dict(db, d), "invoice": res["invoice"], "shipment_id": res["shipment_id"], "order_status": res["status"]}


def cancel(db: Session, cu: CurrentUser, did: int, reason: str | None) -> dict:
    d = _get(db, did, lock=True)
    if d.status not in OPEN:
        raise BusinessConflict("invalid_status", f"A {d.status.lower()} draft cannot be cancelled" + (" (the invoice is real; use a return or credit note)" if d.status == "POSTED" else ""))
    d.status = "CANCELLED"
    audit.record(db, user=cu, action="cancel", entity="invoice_draft", entity_id=d.id, reason=reason)
    activity.emit(db, "accounts.invoice.draft_cancelled", entity="invoice_draft", entity_id=d.id, number=f"Draft {d.id}", actor_id=cu.id)
    db.flush()
    return draft_dict(db, d)


def draft_dict(db: Session, d: InvoiceDraft) -> dict:
    o = db.execute(text("SELECT o.number, c.name FROM sales_orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = :o"), {"o": d.so_id}).mappings().one()
    items = {r[0]: (r[1], r[2]) for r in db.execute(text("SELECT id, item_code, name FROM items WHERE id = ANY(:i)"), {"i": [ln.item_id for ln in d.lines]})}
    inv = db.execute(text("SELECT number FROM sales_invoices WHERE id = :i"), {"i": d.posted_invoice_id}).scalar() if d.posted_invoice_id else None
    return {"id": d.id, "reference": f"DRAFT-{d.id}", "status": d.status, "order_id": d.so_id, "order_number": o["number"], "customer_id": d.customer_id, "customer": o["name"],
            "subtotal": str(d.subtotal), "tax_total": str(d.tax_total), "total": str(d.total), "posted_invoice_id": d.posted_invoice_id, "invoice_number": inv,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "lines": [{"so_item_id": ln.so_item_id, "item_code": items[ln.item_id][0], "item_name": items[ln.item_id][1], "qty": str(ln.qty), "unit_price": str(ln.unit_price),
                       "tax_rate": str(ln.tax_rate), "net_amount": str(ln.net_amount), "tax_amount": str(ln.tax_amount), "amount": str(ln.net_amount + ln.tax_amount)} for ln in d.lines],
            "note": "A draft is a preview: nothing is posted to the ledger and no invoice number is used until it is posted." if d.status in OPEN else None}


def listing(db: Session, status: str | None, page: int, page_size: int) -> dict:
    q = select(InvoiceDraft).order_by(InvoiceDraft.id.desc())
    if status:
        q = q.where(InvoiceDraft.status == status)
    total = db.scalar(select(text("count(*)")).select_from(InvoiceDraft).where(*([InvoiceDraft.status == status] if status else [])))
    rows = db.scalars(q.limit(page_size).offset((page - 1) * page_size)).unique().all()
    return {"items": [draft_dict(db, d) | {"line_count": len(d.lines)} for d in rows], "page": page, "total": total}
