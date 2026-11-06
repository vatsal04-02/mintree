"""Dispatch: sales orders -> allocation -> pack -> dispatch (ONE atomic posting: stock, COGS, GST invoice, shipment, outbox),
courier booking outside transactions, idempotent courier webhooks, returns with credit notes.

Lock order: customer (confirm only) -> order row -> stock balances (items ascending) -> item valuation -> journals -> document counter (last)."""
import hashlib
import hmac
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from psycopg import errors as pgerr
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.audit import service as audit
from app.core import outbox
from app.core.config import get_settings
from app.core.context import request_id_var
from app.core.db import SessionLocal
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, InsufficientStock, NotFound, Unprocessable
from app.core.platform_models import WebhookEvent
from app.core.uow import run_tx
from app.dispatch import courier
from app.dispatch.models import (CourierEvent, CreditNote, SalesInvoice, SalesOrder, SalesOrderItem, SalesReturn, SalesReturnLine, Shipment, ShipmentLine)
from app.inventory import service as inv
from app.masters.models import Customer, Item

log = logging.getLogger("mintree.dispatch")
C = Decimal("0.01")


def _lock_order(db: Session, order_id: int, expected_version: int | None = None) -> SalesOrder:
    o = db.scalar(select(SalesOrder).where(SalesOrder.id == order_id).with_for_update())
    if not o:
        raise NotFound("Sales order")
    if expected_version is not None and o.version != expected_version:
        raise BusinessConflict("version_conflict", "The order was changed by someone else. Reload and try again.")
    return o


# ------------------------------------------------------------------ order lifecycle
def create_order(db: Session, cu: CurrentUser, *, customer_id: int, warehouse_id: int, channel: str, external_ref: str | None, shipping_address: str | None,
                 lines: list[dict]) -> dict:
    cust = db.get(Customer, customer_id)
    if not cust or not cust.is_active:
        raise Unprocessable("Customer not found or inactive")
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_({l["item_id"] for l in lines})))}
    if len({l["item_id"] for l in lines}) != len(lines):
        raise Unprocessable("An item may appear once per order")
    sub = tax = Decimal(0)
    rows = []
    for n, l in enumerate(lines, 1):
        it = items.get(l["item_id"])
        if not it or not it.is_active or it.kind != "FG":
            raise Unprocessable(f"Item {l['item_id']} is not an active finished-goods item")
        rate = Decimal(str(l["tax_rate"] if l.get("tax_rate") is not None else (it.gst_rate or 0)))
        s = acct.money(Decimal(str(l["qty"])) * Decimal(str(l["unit_price"])))
        sub += s
        tax += acct.money(s * rate / 100)
        rows.append(SalesOrderItem(line_no=n, item_id=it.id, qty=l["qty"], unit_price=l["unit_price"], tax_rate=rate))
    n = db.execute(text("SELECT nextval('sales_order_seq')")).scalar_one()
    o = SalesOrder(number=f"SO/{date.today():%y}/{n:06d}", customer_id=customer_id, channel=channel, external_ref=external_ref, warehouse_id=warehouse_id,
                   shipping_address=shipping_address or cust.shipping_address, subtotal=sub, tax_total=tax, total=sub + tax, created_by=cu.id)
    o.items = rows
    db.add(o)
    try:
        db.flush()
    except IntegrityError as e:
        if isinstance(e.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_order", "An order with this channel and external reference already exists") from e
        raise
    audit.record(db, user=cu, action="create", entity="sales_order", entity_id=o.id, after={"number": o.number, "total": str(o.total)})
    return order_dict(o)


def exposure(db: Session, customer_id: int, exclude_order: int | None = None) -> Decimal:
    ar = db.execute(text("SELECT COALESCE(sum(total - amount_paid - credited_amount),0) FROM sales_invoices WHERE customer_id = :c"), {"c": customer_id}).scalar()
    open_orders = db.execute(text("SELECT COALESCE(sum(total),0) FROM sales_orders WHERE customer_id = :c AND status IN ('CONFIRMED','ALLOCATED','PACKED') AND id <> COALESCE(CAST(:x AS bigint), -1)"),
                             {"c": customer_id, "x": exclude_order}).scalar()
    return Decimal(ar) + Decimal(open_orders)


def confirm_order(db: Session, cu: CurrentUser, order_id: int, expected_version: int) -> dict:
    o = _lock_order(db, order_id, expected_version)
    if o.status != "DRAFT":
        raise BusinessConflict("invalid_status", f"Cannot confirm an order in status {o.status}")
    cust = db.scalar(select(Customer).where(Customer.id == o.customer_id).with_for_update(key_share=True))   # serialise credit checks per customer
    if cust.credit_limit is not None:
        exp = exposure(db, cust.id, o.id)
        if exp + o.total > cust.credit_limit:
            raise BusinessConflict("credit_limit_exceeded", "This order would take the customer over its credit limit",
                                   limit=str(cust.credit_limit), exposure=str(exp), order_total=str(o.total))
    o.status, o.version = "CONFIRMED", o.version + 1
    audit.record(db, user=cu, action="confirm", entity="sales_order", entity_id=o.id)
    return order_dict(o)


def allocate_order(db: Session, cu: CurrentUser, order_id: int, expected_version: int) -> dict:
    o = _lock_order(db, order_id, expected_version)
    if o.status != "CONFIRMED":
        raise BusinessConflict("invalid_status", f"Cannot allocate an order in status {o.status}")
    short = []
    for l in sorted(o.items, key=lambda x: x.item_id):
        try:
            inv.reserve(db, item_id=l.item_id, warehouse_id=o.warehouse_id, qty=l.qty, reference_type="sales_order", reference_id=str(o.id), actor_id=cu.id)
        except InsufficientStock as e:
            short.append({"item_id": l.item_id, "requested": str(l.qty), "available": e.detail["available"]})
    if short:
        raise BusinessConflict("insufficient_stock", "Not enough QC-released, unexpired stock to allocate this order", shortages=short)
    o.status, o.version = "ALLOCATED", o.version + 1
    audit.record(db, user=cu, action="allocate", entity="sales_order", entity_id=o.id)
    return order_dict(o)


def pack_order(db: Session, cu: CurrentUser, order_id: int, expected_version: int, cartons: int, weight_kg) -> dict:
    o = _lock_order(db, order_id, expected_version)
    if o.status != "ALLOCATED":
        raise BusinessConflict("invalid_status", f"Cannot pack an order in status {o.status}")
    o.cartons, o.weight_kg, o.status, o.version = cartons, weight_kg, "PACKED", o.version + 1
    audit.record(db, user=cu, action="pack", entity="sales_order", entity_id=o.id, after={"cartons": cartons})
    return order_dict(o)


def cancel_order(db: Session, cu: CurrentUser, order_id: int, reason: str) -> dict:
    o = _lock_order(db, order_id)
    if o.status in ("DISPATCHED", "DELIVERED", "CANCELLED"):
        raise BusinessConflict("cannot_cancel", f"An order in status {o.status} cannot be cancelled; use a return")
    ids = [r[0] for r in db.execute(text("SELECT id FROM stock_reservations WHERE reference_type='sales_order' AND reference_id=:r AND status='ACTIVE'"), {"r": str(o.id)})]
    if ids:
        inv.release_reservations(db, ids)
    o.status, o.version = "CANCELLED", o.version + 1
    audit.record(db, user=cu, action="cancel", entity="sales_order", entity_id=o.id, reason=reason)
    return order_dict(o)


# ------------------------------------------------------------------ dispatch: the atomic posting
def dispatch_order(db: Session, cu: CurrentUser, order_id: int, expected_version: int, courier_name: str | None) -> dict:
    o = _lock_order(db, order_id, expected_version)
    if o.status != "PACKED":
        raise BusinessConflict("invalid_status", f"Only PACKED orders can be dispatched (this one is {o.status})")
    courier_name = courier_name or get_settings().courier_default
    courier.get_courier(courier_name)     # fail early on an unknown provider (no network call here)
    legs: dict[int, list[dict]] = {}
    for l in sorted(o.items, key=lambda x: x.item_id):
        res = db.execute(text("SELECT id, qty FROM stock_reservations WHERE reference_type='sales_order' AND reference_id=:r AND item_id=:i AND status='ACTIVE' ORDER BY id"),
                         {"r": str(o.id), "i": l.item_id}).all()
        if not res or sum(r[1] for r in res) != l.qty:
            raise BusinessConflict("reservation_mismatch", "Reserved stock does not match the order; allocate again", item_id=l.item_id)
        legs[l.item_id] = inv.issue(db, item_id=l.item_id, warehouse_id=o.warehouse_id, qty=l.qty, source_type="sales_order", source_id=o.id, source_line=l.line_no,
                                    actor_id=cu.id, reservation_ids=[r[0] for r in res], movement="DISPATCH")
    vrows = acct.lock_valuations(db, list(legs))
    ship = Shipment(so_id=o.id, courier=courier_name)
    db.add(ship)
    db.flush()
    total_cost = Decimal(0)
    by_item = {l.item_id: l for l in o.items}
    for item_id in sorted(legs):
        for leg in legs[item_id]:
            q = Decimal(leg["qty"])
            cost = acct.val_consume(vrows, item_id, q)
            total_cost += cost
            db.add(ShipmentLine(shipment_id=ship.id, so_item_id=by_item[item_id].id, item_id=item_id, batch_id=leg["batch_id"], balance_id=leg["balance_id"], qty=q,
                                unit_cost=(cost / q).quantize(Decimal("0.0001"))))
    if total_cost:
        acct.post_journal(db, entry_date=date.today(), description=f"COGS {o.number}", source_type="shipment", source_id=ship.id, source_event="cogs",
                          lines=[("5000", total_cost, Decimal(0)), ("1320", Decimal(0), total_cost)], actor_id=cu.id)
    je = acct.post_journal(db, entry_date=date.today(), description=f"Sales invoice for {o.number}", source_type="sales_invoice", source_id=o.id, actor_id=cu.id,
                           lines=[("1200", o.total, Decimal(0)), ("4000", Decimal(0), o.subtotal), ("2200", Decimal(0), o.tax_total)])
    fy = acct.fiscal_year(date.today())
    seq = acct.next_gapless(db, f"sales_invoice:{fy}")                 # LAST: the hot counter row is held only until commit
    inv_row = SalesInvoice(number=f"SI/{fy}/{seq:06d}", customer_id=o.customer_id, so_id=o.id, invoice_date=date.today(), subtotal=o.subtotal, tax_total=o.tax_total,
                           total=o.total, journal_entry_id=je.id)
    db.add(inv_row)
    for l in o.items:
        l.qty_shipped = l.qty
    o.status, o.version = "DISPATCHED", o.version + 1
    db.flush()
    audit.record(db, user=cu, action="dispatch", entity="sales_order", entity_id=o.id, after={"invoice": inv_row.number, "shipment_id": ship.id, "cogs": str(total_cost)})
    outbox.add(db, event_type="dispatch.shipment.requested", aggregate_type="shipment", aggregate_id=ship.id, payload={"shipment_id": ship.id, "order": o.number, "courier": courier_name})
    outbox.add(db, event_type="dispatch.order.dispatched", aggregate_type="sales_order", aggregate_id=o.id,
               payload={"order_id": o.id, "number": o.number, "invoice": inv_row.number, "total": str(o.total)})
    return order_dict(o) | {"invoice": {"id": inv_row.id, "number": inv_row.number, "total": str(inv_row.total)}, "shipment_id": ship.id}


# ------------------------------------------------------------------ courier booking (NO network call inside a transaction)
def book_shipment(shipment_id: int, session_factory=SessionLocal) -> str:
    """claim (tx) -> adapter call (no tx) -> record (tx). Idempotent: a booked shipment is left alone; a dead worker's claim expires."""
    lease = get_settings().courier_booking_lease_s

    def claim(db: Session):
        s = db.scalar(select(Shipment).where(Shipment.id == shipment_id).with_for_update())
        if not s:
            raise NotFound("Shipment")
        stale = s.status == "BOOKING" and s.claimed_at and (datetime.now(timezone.utc) - s.claimed_at).total_seconds() > lease
        if s.status != "PENDING_BOOKING" and not stale:
            return None
        o = db.get(SalesOrder, s.so_id)
        s.status, s.claimed_at, s.booking_attempts = "BOOKING", datetime.now(timezone.utc), s.booking_attempts + 1
        return {"courier": s.courier, "address": o.shipping_address, "weight": None if o.weight_kg is None else float(o.weight_kg), "cartons": o.cartons}

    with session_factory() as db:
        c = run_tx(db, claim, name="dispatch.book.claim")
    if c is None:
        return "skipped"
    try:
        b = courier.get_courier(c["courier"]).book(client_ref=str(shipment_id), address=c["address"], weight_kg=c["weight"], cartons=c["cartons"])   # network I/O, no tx open
    except Exception as e:
        def fail(db: Session):
            s = db.scalar(select(Shipment).where(Shipment.id == shipment_id).with_for_update())
            if s.status == "BOOKING":
                s.status, s.last_error = "PENDING_BOOKING", f"{type(e).__name__}: {e}"[:500]
        with session_factory() as db:
            run_tx(db, fail, name="dispatch.book.fail")
        raise

    def record(db: Session):
        s = db.scalar(select(Shipment).where(Shipment.id == shipment_id).with_for_update())
        if s.status == "BOOKED" or s.awb:
            return "already_booked"
        s.awb, s.tracking_url, s.status, s.last_error = b.awb, b.tracking_url, "BOOKED", None
        db.add(CourierEvent(shipment_id=s.id, external_event_id=f"booked:{s.id}", event_type="BOOKED", occurred_at=datetime.now(timezone.utc), raw={"awb": b.awb}))
        return "booked"
    with session_factory() as db:
        return run_tx(db, record, name="dispatch.book.record")


# ------------------------------------------------------------------ courier webhooks (idempotent)
RANK = {"BOOKED": 0, "PICKED_UP": 1, "IN_TRANSIT": 2, "OUT_FOR_DELIVERY": 3, "DELIVERED": 4}


def verify_signature(body: bytes, signature: str | None) -> bool:
    expected = hmac.new(get_settings().courier_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def handle_courier_event(db: Session, provider: str, event: dict) -> dict:
    from fastapi.encoders import jsonable_encoder
    ins = db.execute(pg_insert(WebhookEvent).values(source=f"courier:{provider}", external_event_id=event["event_id"], payload=jsonable_encoder(event), request_id=request_id_var.get())
                     .on_conflict_do_nothing(constraint="uq_webhook_events_ext").returning(WebhookEvent.id)).scalar()
    if ins is None:
        return {"result": "duplicate"}                       # re-delivered webhook: acknowledged, no second effect
    s = db.scalar(select(Shipment).where(Shipment.awb == event["awb"]).with_for_update())
    if not s:
        db.execute(update(WebhookEvent).where(WebhookEvent.id == ins).values(status="IGNORED", error="unknown AWB"))
        return {"result": "ignored", "reason": "unknown awb"}
    et = event["event_type"]
    db.add(CourierEvent(shipment_id=s.id, external_event_id=event["event_id"], event_type=et, occurred_at=event["occurred_at"], raw=jsonable_encoder(event)))
    cur = RANK.get(s.status, -1)
    if et in RANK and RANK[et] > cur and s.status not in ("DELIVERED", "RTO"):
        s.status = et
        if et == "DELIVERED":
            s.delivered_at = event["occurred_at"]
            db.execute(update(SalesOrder).where(SalesOrder.id == s.so_id, SalesOrder.status == "DISPATCHED").values(status="DELIVERED"))
    elif et in ("FAILED_DELIVERY", "RTO") and s.status != "DELIVERED":
        s.status = et
    db.execute(update(WebhookEvent).where(WebhookEvent.id == ins).values(status="PROCESSED"))
    return {"result": "processed", "shipment_status": s.status}


# ------------------------------------------------------------------ returns
def create_return(db: Session, cu: CurrentUser, *, order_id: int, reason: str, lines: list[dict]) -> dict:
    o = _lock_order(db, order_id)
    if o.status not in ("DISPATCHED", "DELIVERED"):
        raise BusinessConflict("not_shipped", "Only shipped orders can be returned")
    n = db.execute(text("SELECT nextval('sales_return_seq')")).scalar_one()
    r = SalesReturn(number=f"RET/{date.today():%y}/{n:06d}", so_id=o.id, reason=reason, created_by=cu.id)
    if len({l["shipment_line_id"] for l in lines}) != len(lines):
        raise Unprocessable("A shipment line may appear once per return")
    for l in lines:
        sl = db.get(ShipmentLine, l["shipment_line_id"])
        so_item = db.get(SalesOrderItem, sl.so_item_id) if sl else None
        if not sl or so_item.so_id != o.id:
            raise Unprocessable(f"Shipment line {l['shipment_line_id']} does not belong to this order")
        prior = db.execute(text("SELECT COALESCE(sum(qty),0) FROM sales_return_lines WHERE shipment_line_id = :s"), {"s": sl.id}).scalar()
        if Decimal(str(l["qty"])) + prior > sl.qty:
            raise BusinessConflict("return_exceeds_shipped", "Return quantity exceeds what was shipped on this line", shipped=str(sl.qty), already_returned=str(prior))
        so_item.qty_returned = so_item.qty_returned + Decimal(str(l["qty"]))
        r.lines.append(SalesReturnLine(shipment_line_id=sl.id, qty=l["qty"]))
    db.add(r)
    db.flush()
    audit.record(db, user=cu, action="create", entity="sales_return", entity_id=r.id, after={"number": r.number}, reason=reason)
    return return_dict(r)


def receive_return(db: Session, cu: CurrentUser, return_id: int) -> dict:
    r = db.scalar(select(SalesReturn).where(SalesReturn.id == return_id).with_for_update())
    if not r:
        raise NotFound("Return")
    if r.status != "REQUESTED":
        raise BusinessConflict("invalid_status", f"Cannot receive a return in status {r.status}")
    o = db.get(SalesOrder, r.so_id)
    for n, l in enumerate(sorted(r.lines, key=lambda x: x.shipment_line_id), 1):
        sl = db.get(ShipmentLine, l.shipment_line_id)
        inv.receive(db, item_id=sl.item_id, warehouse_id=o.warehouse_id, location_id=None, batch_id=sl.batch_id, qty=l.qty, source_type="sales_return", source_id=r.id,
                    source_line=n, actor_id=cu.id, quarantine=True, movement="RETURN")      # blocked until Quality decides
    r.status = "RECEIVED"
    audit.record(db, user=cu, action="receive", entity="sales_return", entity_id=r.id)
    return return_dict(r)


def decide_return(db: Session, cu: CurrentUser, return_id: int, decision: str) -> dict:
    r = db.scalar(select(SalesReturn).where(SalesReturn.id == return_id).with_for_update())
    if not r:
        raise NotFound("Return")
    if r.status != "RECEIVED":
        raise BusinessConflict("invalid_status", "Only received returns can be decided")
    o = db.get(SalesOrder, r.so_id)
    invoice = db.scalar(select(SalesInvoice).where(SalesInvoice.so_id == o.id).with_for_update())
    sub = tax = cost = Decimal(0)
    units: list[tuple[ShipmentLine, Decimal]] = []
    for l in sorted(r.lines, key=lambda x: x.shipment_line_id):
        sl = db.get(ShipmentLine, l.shipment_line_id)
        it = db.get(SalesOrderItem, sl.so_item_id)
        s = acct.money(l.qty * it.unit_price)
        sub += s
        tax += acct.money(s * it.tax_rate / 100)
        cost += acct.money(l.qty * sl.unit_cost)
        units.append((sl, l.qty))
    total = sub + tax
    if invoice.amount_paid + invoice.credited_amount + total > invoice.total:
        raise BusinessConflict("refund_required", "The invoice is already settled beyond this credit; a refund flow would be needed (not implemented)")
    if decision == "RESTOCK":
        for n, (sl, q) in enumerate(units, 1):
            inv.unblock(db, balance_id=sl.balance_id, qty=q, source_type="sales_return", source_id=r.id, source_line=n, actor_id=cu.id, reason="restocked after return QC")
        vrows = acct.lock_valuations(db, [sl.item_id for sl, _ in units])
        for sl, q in units:
            acct.val_receive(db, vrows, sl.item_id, q, acct.money(q * sl.unit_cost))
        if cost:
            acct.post_journal(db, entry_date=date.today(), description=f"Return {r.number} restocked", source_type="sales_return", source_id=r.id, source_event="restock",
                              lines=[("1320", cost, Decimal(0)), ("5000", Decimal(0), cost)], actor_id=cu.id)
    je = acct.post_journal(db, entry_date=date.today(), description=f"Credit note for return {r.number}", source_type="sales_return", source_id=r.id, source_event="credit_note",
                           lines=[("4100", sub, Decimal(0)), ("2200", tax, Decimal(0)), ("1200", Decimal(0), total)], actor_id=cu.id)
    fy = acct.fiscal_year(date.today())
    seq = acct.next_gapless(db, f"credit_note:{fy}")
    cn = CreditNote(number=f"CN/{fy}/{seq:06d}", invoice_id=invoice.id, return_id=r.id, subtotal=sub, tax_total=tax, total=total, journal_entry_id=je.id)
    db.add(cn)
    invoice.credited_amount = invoice.credited_amount + total
    invoice.status = "PAID" if invoice.amount_paid + invoice.credited_amount == invoice.total else ("PARTIAL" if invoice.amount_paid + invoice.credited_amount else "OPEN")
    db.flush()
    r.status, r.decision, r.credit_note_id = "DECIDED", decision, cn.id
    audit.record(db, user=cu, action=f"return_{decision.lower()}", entity="sales_return", entity_id=r.id, after={"credit_note": cn.number, "total": str(total)})
    outbox.add(db, event_type="dispatch.return.decided", aggregate_type="sales_return", aggregate_id=r.id, payload={"return_id": r.id, "decision": decision, "credit_note": cn.number})
    return return_dict(r) | {"credit_note": {"number": cn.number, "total": str(total)}}


# ------------------------------------------------------------------ views
def order_dict(o: SalesOrder) -> dict:
    return {"id": o.id, "number": o.number, "status": o.status, "version": o.version, "customer_id": o.customer_id, "channel": o.channel, "external_ref": o.external_ref,
            "subtotal": str(o.subtotal), "tax_total": str(o.tax_total), "total": str(o.total),
            "items": [{"id": i.id, "line_no": i.line_no, "item_id": i.item_id, "qty": str(i.qty), "unit_price": str(i.unit_price), "tax_rate": str(i.tax_rate),
                       "qty_shipped": str(i.qty_shipped), "qty_returned": str(i.qty_returned)} for i in o.items]}


def return_dict(r: SalesReturn) -> dict:
    return {"id": r.id, "number": r.number, "so_id": r.so_id, "status": r.status, "decision": r.decision,
            "lines": [{"shipment_line_id": l.shipment_line_id, "qty": str(l.qty)} for l in r.lines]}
