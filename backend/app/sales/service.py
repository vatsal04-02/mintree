"""Sales (CRM) service: contacts, customer-specific pricing, follow-ups, quotations (-> sales order), pipeline and customer history."""
from datetime import date
from decimal import Decimal

from psycopg import errors as pgerr
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.audit import service as audit
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.dispatch import service as dispatch
from app.masters.models import Customer, Item
from app.notifications.service import notify
from app.sales.models import CustomerContact, CustomerPrice, FollowUp, SalesQuotation, SalesQuotationItem
from app.core.clock import business_today

CHANNEL_BY_TYPE = {"DISTRIBUTOR": "DISTRIBUTOR", "BRAND": "BRAND"}


def _customer(db: Session, cid: int, lock: bool = False) -> Customer:
    q = select(Customer).where(Customer.id == cid)
    c = db.scalar(q.with_for_update() if lock else q)
    if not c or not c.is_active:
        raise NotFound("Customer")
    return c


# ------------------------------------------------------------------ contacts
def add_contact(db: Session, cu: CurrentUser, cid: int, data: dict) -> dict:
    _customer(db, cid, lock=True)                                      # serialises primary-contact switches per customer
    if data.get("is_primary"):
        db.execute(update(CustomerContact).where(CustomerContact.customer_id == cid, CustomerContact.is_primary.is_(True)).values(is_primary=False))
    c = CustomerContact(customer_id=cid, **data)
    db.add(c)
    db.flush()
    audit.record(db, user=cu, action="create", entity="customer_contact", entity_id=c.id)
    return contact_dict(c)


def contact_dict(c: CustomerContact) -> dict:
    return {"id": c.id, "customer_id": c.customer_id, "name": c.name, "role": c.role, "phone": c.phone, "email": c.email, "is_primary": c.is_primary}


# ------------------------------------------------------------------ pricing
def set_price(db: Session, cu: CurrentUser, *, customer_id: int, item_id: int, price: Decimal, valid_from: date, valid_to: date | None) -> dict:
    _customer(db, customer_id)
    it = db.get(Item, item_id)
    if not it or it.kind != "FG":
        raise Unprocessable("Customer prices apply to finished-goods items")
    cp = CustomerPrice(customer_id=customer_id, item_id=item_id, price=price, valid_from=valid_from, valid_to=valid_to, created_by=cu.id)
    db.add(cp)
    try:
        db.flush()
    except IntegrityError as ex:
        if isinstance(ex.orig, pgerr.ExclusionViolation):
            raise BusinessConflict("price_window_overlap", "A price for this customer and item already covers part of that period") from ex
        raise
    audit.record(db, user=cu, action="create", entity="customer_price", entity_id=cp.id, after={"customer": customer_id, "item": item_id, "price": str(price)})
    return price_dict(cp)


def resolve_price(db: Session, customer_id: int, item_id: int, on: date | None = None) -> Decimal | None:
    on = on or business_today()
    return db.scalar(select(CustomerPrice.price).where(CustomerPrice.customer_id == customer_id, CustomerPrice.item_id == item_id, CustomerPrice.valid_from <= on,
                                                       (CustomerPrice.valid_to.is_(None)) | (CustomerPrice.valid_to >= on)).order_by(CustomerPrice.valid_from.desc()).limit(1))


def price_dict(p: CustomerPrice) -> dict:
    return {"id": p.id, "customer_id": p.customer_id, "item_id": p.item_id, "price": str(p.price), "valid_from": p.valid_from, "valid_to": p.valid_to}


# ------------------------------------------------------------------ follow-ups
def add_followup(db: Session, cu: CurrentUser, data: dict) -> dict:
    if (data.get("lead_id") is None) == (data.get("customer_id") is None):
        raise Unprocessable("A follow-up concerns exactly one lead or one customer")
    f = FollowUp(**data, created_by=cu.id)
    db.add(f)
    try:
        db.flush()
    except IntegrityError as ex:
        raise Unprocessable("Lead, customer or assignee does not exist") from ex
    if f.assigned_to != cu.id:
        notify(db, kind="followup.assigned", title=f"Follow-up: {f.subject}", body=f"Due {f.due_date}", link=f"/sales/followups/{f.id}", user_ids=[f.assigned_to], exclude_user_id=cu.id)
    audit.record(db, user=cu, action="create", entity="followup", entity_id=f.id)
    return followup_dict(f)


def complete_followup(db: Session, cu: CurrentUser, fid: int, outcome: str | None, cancel: bool) -> dict:
    f = db.scalar(select(FollowUp).where(FollowUp.id == fid).with_for_update())
    if not f:
        raise NotFound("Follow-up")
    if f.status != "OPEN":
        raise BusinessConflict("invalid_status", f"Follow-up is already {f.status}")
    if f.assigned_to != cu.id and f.created_by != cu.id and not cu.can("sales.followup:manage"):
        raise BusinessConflict("not_yours", "Only the assignee, the creator or a manager can close this follow-up")
    if not cancel and not (outcome and outcome.strip()):
        raise BusinessConflict("outcome_required", "Record the outcome to complete a follow-up")
    f.status, f.outcome, f.completed_at = ("CANCELLED" if cancel else "DONE"), outcome, func.now()
    audit.record(db, user=cu, action="cancel" if cancel else "complete", entity="followup", entity_id=f.id)
    return followup_dict(f)


def followup_dict(f: FollowUp) -> dict:
    return {"id": f.id, "subject": f.subject, "due_date": f.due_date, "status": f.status, "lead_id": f.lead_id, "customer_id": f.customer_id, "assigned_to": f.assigned_to,
            "overdue": f.status == "OPEN" and f.due_date < business_today(), "outcome": f.outcome}


# ------------------------------------------------------------------ quotations
def _q(db: Session, qid: int, lock: bool = False) -> SalesQuotation:
    q = select(SalesQuotation).where(SalesQuotation.id == qid)
    r = db.scalar(q.with_for_update() if lock else q)
    if not r:
        raise NotFound("Quotation")
    return r


def create_quotation(db: Session, cu: CurrentUser, *, customer_id: int, valid_until: date, notes: str | None, lines: list[dict]) -> dict:
    cust = _customer(db, customer_id)
    if valid_until < business_today():
        raise Unprocessable("valid_until is in the past")
    if len({l["item_id"] for l in lines}) != len(lines):
        raise Unprocessable("An item may appear once per quotation")
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_([l["item_id"] for l in lines])))}
    rows, sub, tax = [], Decimal(0), Decimal(0)
    for n, l in enumerate(lines, 1):
        it = items.get(l["item_id"])
        if not it or not it.is_active or it.kind != "FG":
            raise Unprocessable(f"Item {l['item_id']} is not an active finished-goods item")
        if l.get("unit_price") is not None:
            price, src = Decimal(str(l["unit_price"])), "MANUAL"
        else:
            price = resolve_price(db, customer_id, it.id)
            if price is None:
                raise Unprocessable(f"No customer price is set for item {it.item_code}; enter a unit price")
            src = "CUSTOMER_LIST"
        rate = Decimal(str(l["tax_rate"] if l.get("tax_rate") is not None else (it.gst_rate or 0)))
        line = acct.money(Decimal(str(l["qty"])) * price)
        sub += line
        tax += acct.money(line * rate / 100)
        rows.append(SalesQuotationItem(line_no=n, item_id=it.id, qty=l["qty"], unit_price=price, tax_rate=rate, price_source=src))
    seq = db.execute(text("SELECT nextval('sales_quotation_seq')")).scalar_one()
    q = SalesQuotation(number=f"SQ/{business_today():%y}/{seq:06d}", customer_id=customer_id, valid_until=valid_until, notes=notes, subtotal=sub, tax_total=tax, total=sub + tax, created_by=cu.id)
    q.items = rows
    db.add(q)
    db.flush()
    audit.record(db, user=cu, action="create", entity="sales_quotation", entity_id=q.id, after={"number": q.number, "total": str(q.total), "customer": cust.code})
    return quotation_dict(q)


def quotation_action(db: Session, cu: CurrentUser, qid: int, action: str, *, expected_version: int, warehouse_id: int | None = None) -> dict:
    q = _q(db, qid, lock=True)
    if q.version != expected_version:
        raise BusinessConflict("version_conflict", "The quotation was changed by someone else. Reload and try again.")
    flow = {"send": ({"DRAFT"}, "SENT"), "accept": ({"SENT"}, "ACCEPTED"), "reject": ({"SENT"}, "REJECTED"), "convert": ({"ACCEPTED"}, "CONVERTED")}
    frm, to = flow[action]
    if q.status not in frm:
        raise BusinessConflict("invalid_status", f"Cannot {action} a quotation in status {q.status}")
    if action in ("accept", "convert") and q.valid_until < business_today():
        raise BusinessConflict("quotation_expired", "This quotation has expired; create a new one", valid_until=str(q.valid_until))
    if action == "convert":
        if not warehouse_id:
            raise Unprocessable("warehouse_id is required to convert")
        cust = _customer(db, q.customer_id)
        so = dispatch.create_order(db, cu, customer_id=q.customer_id, warehouse_id=warehouse_id, channel=CHANNEL_BY_TYPE.get(cust.customer_type, "B2B"), external_ref=q.number, shipping_address=None,
                                   lines=[{"item_id": i.item_id, "qty": i.qty, "unit_price": i.unit_price, "tax_rate": i.tax_rate} for i in q.items])
        q.sales_order_id = so["id"]
    q.status, q.version = to, q.version + 1
    audit.record(db, user=cu, action=action, entity="sales_quotation", entity_id=q.id, after={"status": to})
    return quotation_dict(q)


def quotation_dict(q: SalesQuotation) -> dict:
    return {"id": q.id, "number": q.number, "customer_id": q.customer_id, "status": q.status, "version": q.version, "valid_until": q.valid_until, "expired": q.status in ("DRAFT", "SENT") and q.valid_until < business_today(),
            "subtotal": str(q.subtotal), "tax_total": str(q.tax_total), "total": str(q.total), "sales_order_id": q.sales_order_id,
            "items": [{"line_no": i.line_no, "item_id": i.item_id, "qty": str(i.qty), "unit_price": str(i.unit_price), "tax_rate": str(i.tax_rate), "price_source": i.price_source} for i in q.items]}


# ------------------------------------------------------------------ pipeline + history
def pipeline(db: Session) -> dict:
    by_status = {r[0]: r[1] for r in db.execute(text("SELECT status, count(*) FROM leads GROUP BY status"))}
    won, lost = by_status.get("WON", 0), by_status.get("LOST", 0)
    quotes = {r[0]: {"count": r[1], "value": str(r[2])} for r in db.execute(text("SELECT status, count(*), COALESCE(sum(total),0) FROM sales_quotations GROUP BY status"))}
    by_owner = [dict(r) for r in db.execute(text("""SELECT COALESCE(u.full_name, 'Unassigned') AS owner, count(*) FILTER (WHERE l.status IN ('NEW','CONTACTED','QUALIFIED')) AS open_leads,
                                                           count(*) FILTER (WHERE l.status = 'WON') AS won FROM leads l LEFT JOIN users u ON u.id = l.owner_id GROUP BY u.full_name ORDER BY 1""")).mappings()]
    overdue = db.scalar(text("SELECT count(*) FROM followups WHERE status = 'OPEN' AND due_date < business_today()"))
    return {"leads_by_status": by_status, "lead_win_rate_pct": round(100 * won / (won + lost), 1) if won + lost else None, "quotations_by_status": quotes,
            "by_owner": by_owner, "overdue_followups": overdue}


def customer_history(db: Session, cid: int) -> dict:
    c = _customer(db, cid)
    orders = [dict(r) for r in db.execute(text("""SELECT id, number, status, channel, total, created_at FROM sales_orders WHERE customer_id = :c ORDER BY id DESC LIMIT 50"""), {"c": cid}).mappings()]
    inv = db.execute(text("""SELECT count(*) n, COALESCE(sum(subtotal),0) revenue, COALESCE(sum(total - amount_paid - credited_amount),0) outstanding, max(invoice_date) last_invoice
                               FROM sales_invoices WHERE customer_id = :c"""), {"c": cid}).mappings().one()
    complaints = db.scalar(text("SELECT count(*) FROM complaints WHERE customer_id = :c"), {"c": cid})
    return {"customer": {"id": c.id, "code": c.code, "name": c.name, "customer_type": c.customer_type, "credit_limit": None if c.credit_limit is None else str(c.credit_limit)},
            "invoices": inv["n"], "revenue_ex_tax": str(inv["revenue"]), "outstanding": str(inv["outstanding"]), "last_invoice_date": inv["last_invoice"], "complaints": complaints,
            "recent_orders": [{**o, "total": str(o["total"])} for o in orders]}
