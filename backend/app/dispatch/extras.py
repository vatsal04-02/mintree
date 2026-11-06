"""Dispatch extras: pick list, packing list, manifest/pickup, shipping charges, reverse pickup (3-phase, no network call inside a transaction),
dispatch dashboard and invoice PDF."""
import io
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.approvals.service import get_setting
from app.audit import service as audit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.core.uow import run_tx
from app.dispatch import courier
from app.dispatch.models import PickScan, SalesInvoice, SalesOrder, SalesReturn, Shipment
from app.core.clock import business_today

DELAY_DAYS = 5


def _order(db: Session, oid: int) -> SalesOrder:
    o = db.get(SalesOrder, oid)
    if not o:
        raise NotFound("Sales order")
    return o


def pick_list(db: Session, oid: int) -> dict:
    """What to pick, from where, in walking order (location, then earliest expiry). Derived from the ACTIVE reservations that allocation created, so it can never
    ask for stock that is not actually reserved for this order."""
    o = _order(db, oid)
    if o.status not in ("ALLOCATED", "PACKED"):
        raise BusinessConflict("invalid_status", f"A pick list exists for ALLOCATED or PACKED orders (this one is {o.status})")
    rows = db.execute(text("""
        SELECT it.item_code, it.name, r.qty, b.batch_no, b.expiry_date, loc.code AS location, wh.code AS warehouse
          FROM stock_reservations r JOIN stock_balances sb ON sb.id = r.balance_id JOIN items it ON it.id = r.item_id
          LEFT JOIN inventory_batches b ON b.id = sb.batch_id LEFT JOIN storage_locations loc ON loc.id = sb.location_id JOIN warehouses wh ON wh.id = sb.warehouse_id
         WHERE r.reference_type = 'sales_order' AND r.reference_id = :o AND r.status = 'ACTIVE'
         ORDER BY loc.code NULLS LAST, b.expiry_date NULLS LAST, it.item_code, r.id"""), {"o": str(oid)}).mappings().all()
    return {"order": o.number, "status": o.status, "lines": [{"item_code": r["item_code"], "item": r["name"], "qty": str(r["qty"]), "batch_no": r["batch_no"], "expiry_date": r["expiry_date"],
                                                         "location": r["location"], "warehouse": r["warehouse"]} for r in rows],
            "total_qty": str(sum((Decimal(r["qty"]) for r in rows), Decimal(0)))}


def packing_list(db: Session, oid: int) -> dict:
    o = _order(db, oid)
    if o.status not in ("PACKED", "DISPATCHED", "DELIVERED"):
        raise BusinessConflict("invalid_status", "A packing list exists once the order is packed")
    if o.status == "PACKED":
        lines = pick_list(db, oid)["lines"]
    else:
        lines = [{"item_code": r["item_code"], "item": r["name"], "qty": str(r["qty"]), "batch_no": r["batch_no"], "expiry_date": r["expiry_date"]} for r in db.execute(text(
            """SELECT it.item_code, it.name, sl.qty, b.batch_no, b.expiry_date FROM shipment_lines sl JOIN shipments s ON s.id = sl.shipment_id JOIN items it ON it.id = sl.item_id
                 LEFT JOIN inventory_batches b ON b.id = sl.batch_id WHERE s.so_id = :o ORDER BY sl.id"""), {"o": oid}).mappings()]
    return {"order": o.number, "customer_id": o.customer_id, "ship_to": o.shipping_address, "cartons": o.cartons, "weight_kg": None if o.weight_kg is None else str(o.weight_kg), "lines": lines}


def set_shipping_charge(db: Session, cu: CurrentUser, sid: int, amount: Decimal) -> dict:
    s = db.scalar(select(Shipment).where(Shipment.id == sid).with_for_update())
    if not s:
        raise NotFound("Shipment")
    before = s.shipping_charge
    s.shipping_charge = amount
    audit.record(db, user=cu, action="shipping_charge", entity="shipment", entity_id=s.id, before={"charge": None if before is None else str(before)}, after={"charge": str(amount)})
    return {"shipment_id": s.id, "shipping_charge": str(amount)}


def create_manifest(db: Session, cu: CurrentUser, courier_name: str) -> dict:
    """Groups every BOOKED, not-yet-manifested shipment of one courier under a manifest number and records the pickup request. (The pickup is *requested internally*:
    courier-side pickup scheduling needs the chosen provider's API.)"""
    ships = list(db.scalars(select(Shipment).where(Shipment.courier == courier_name, Shipment.status == "BOOKED", Shipment.manifest_no.is_(None)).order_by(Shipment.id).with_for_update(skip_locked=True)))
    if not ships:
        raise BusinessConflict("nothing_to_manifest", "No booked, un-manifested shipments for that courier")
    n = db.execute(text("SELECT nextval('manifest_seq')")).scalar_one()
    no = f"MF/{business_today():%y%m%d}/{n:05d}"
    for s in ships:
        s.manifest_no, s.pickup_requested_at = no, datetime.now(timezone.utc)
    audit.record(db, user=cu, action="manifest", entity="shipment", entity_id=no, after={"courier": courier_name, "shipments": [s.id for s in ships]})
    return {"manifest_no": no, "courier": courier_name, "shipments": len(ships)}


def get_manifest(db: Session, manifest_no: str) -> dict:
    rows = db.execute(text("""SELECT s.id, s.awb, s.courier, s.status, o.number, o.cartons, o.weight_kg, o.shipping_address, s.pickup_requested_at, s.handed_over_at
                                FROM shipments s JOIN sales_orders o ON o.id = s.so_id WHERE s.manifest_no = :m ORDER BY s.id"""), {"m": manifest_no}).mappings().all()
    if not rows:
        raise NotFound("Manifest")
    return {"manifest_no": manifest_no, "courier": rows[0]["courier"], "count": len(rows), "total_cartons": sum(r["cartons"] or 0 for r in rows),
            "total_weight_kg": str(sum((Decimal(r["weight_kg"] or 0) for r in rows), Decimal(0))), "handed_over": sum(1 for r in rows if r["handed_over_at"]),
            "shipments": [{"shipment_id": r["id"], "awb": r["awb"], "order": r["number"], "cartons": r["cartons"], "weight_kg": None if r["weight_kg"] is None else str(r["weight_kg"]),
                           "ship_to": r["shipping_address"], "status": r["status"]} for r in rows]}


def request_reverse_pickup(return_id: int, session_factory=SessionLocal) -> str:
    """claim (tx) -> courier call (no tx) -> record (tx). Safe to retry: REQUESTED returns are left alone; a dead worker's claim expires."""
    lease = get_settings().courier_booking_lease_s

    def claim(db: Session):
        r = db.scalar(select(SalesReturn).where(SalesReturn.id == return_id).with_for_update())
        if not r:
            raise NotFound("Return")
        if r.status != "REQUESTED":
            raise BusinessConflict("invalid_status", f"A reverse pickup is arranged for returns awaiting receipt (this one is {r.status})")
        stale = r.reverse_pickup_status == "REQUESTING" and r.reverse_claimed_at and (datetime.now(timezone.utc) - r.reverse_claimed_at).total_seconds() > lease
        if r.reverse_pickup_status in ("REQUESTED", "REQUESTING") and not stale:
            return None
        s = db.scalar(select(Shipment).where(Shipment.so_id == r.so_id))
        o = db.get(SalesOrder, r.so_id)
        r.reverse_pickup_status, r.reverse_claimed_at = "REQUESTING", datetime.now(timezone.utc)
        return {"courier": s.courier, "address": o.shipping_address}

    with session_factory() as db:
        c = run_tx(db, claim, name="dispatch.reverse.claim")
    if c is None:
        return "skipped"
    try:
        b = courier.get_courier(c["courier"]).reverse_pickup(client_ref=str(return_id), address=c["address"])   # network I/O, no tx open
    except Exception:
        def fail(db: Session):
            r = db.scalar(select(SalesReturn).where(SalesReturn.id == return_id).with_for_update())
            if r.reverse_pickup_status == "REQUESTING":
                r.reverse_pickup_status = "FAILED"
        with session_factory() as db:
            run_tx(db, fail, name="dispatch.reverse.fail")
        raise

    def record(db: Session):
        r = db.scalar(select(SalesReturn).where(SalesReturn.id == return_id).with_for_update())
        if r.reverse_pickup_status == "REQUESTED":
            return "already_requested"
        r.reverse_pickup_status, r.reverse_awb = "REQUESTED", b.awb
        return "requested"
    with session_factory() as db:
        return run_tx(db, record, name="dispatch.reverse.record")


def dashboard(db: Session) -> dict:
    so = {r[0]: r[1] for r in db.execute(text("SELECT status, count(*) FROM sales_orders GROUP BY status"))}
    sh = {r[0]: r[1] for r in db.execute(text("SELECT status, count(*) FROM shipments GROUP BY status"))}
    today = db.scalar(text("SELECT count(*) FROM sales_invoices WHERE invoice_date = business_today()"))
    delayed = db.scalar(text("""SELECT count(*) FROM shipments s WHERE s.status NOT IN ('DELIVERED','RTO','FAILED_DELIVERY') AND s.created_at < now() - make_interval(days => :d)"""), {"d": DELAY_DAYS})
    ret = {r[0]: r[1] for r in db.execute(text("SELECT status, count(*) FROM sales_returns GROUP BY status"))}
    charges = db.execute(text("SELECT COALESCE(sum(shipping_charge),0), count(*) FILTER (WHERE shipping_charge IS NULL AND status NOT IN ('PENDING_BOOKING','BOOKING')) FROM shipments")).one()
    return {"pending_orders": so.get("DRAFT", 0) + so.get("CONFIRMED", 0), "ready_to_pack": so.get("ALLOCATED", 0), "ready_to_dispatch": so.get("PACKED", 0),
            "dispatched_today": today, "in_transit": sum(sh.get(k, 0) for k in ("BOOKED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY")), "delivered": sh.get("DELIVERED", 0),
            "delayed": delayed, "delay_threshold_days": DELAY_DAYS, "failed_deliveries": sh.get("FAILED_DELIVERY", 0), "rto": sh.get("RTO", 0),
            "awaiting_courier_booking": sh.get("PENDING_BOOKING", 0), "returns_by_status": ret, "shipping_charges_recorded": str(charges[0]), "shipments_without_charge": charges[1],
            "orders_by_status": so}


# ------------------------------------------------------------------ invoice PDF
def invoice_pdf(db: Session, iid: int) -> tuple[str, bytes]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from app.accounts.reports import state_code

    inv = db.get(SalesInvoice, iid)
    if not inv:
        raise NotFound("Invoice")
    cust = db.execute(text("SELECT name, gstin, state, billing_address, shipping_address FROM customers WHERE id = :c"), {"c": inv.customer_id}).mappings().one()
    o = db.get(SalesOrder, inv.so_id)
    lines = db.execute(text("""SELECT it.name, it.hsn_code, soi.tax_rate, soi.unit_price, sum(sl.qty) AS qty FROM shipment_lines sl JOIN shipments s ON s.id = sl.shipment_id
                                 JOIN sales_order_items soi ON soi.id = sl.so_item_id JOIN items it ON it.id = sl.item_id WHERE s.so_id = :o
                                GROUP BY it.name, it.hsn_code, soi.tax_rate, soi.unit_price, soi.line_no ORDER BY soi.line_no"""), {"o": inv.so_id}).mappings().all()
    company = {k: get_setting(db, f"company.{k}") for k in ("name", "gstin", "address", "state_code")}
    intra = (state_code(cust["gstin"], cust["state"]) or str(company["state_code"])) == str(company["state_code"])
    st = getSampleStyleSheet()
    small = st["BodyText"].clone("small", fontSize=8, leading=10)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=f"Tax Invoice {inv.number}")
    els = [Paragraph(f"<b>{company['name'] or 'Mintree'}</b>", st["Title"]),
           Paragraph(f"GSTIN: {company['gstin'] or '-'}<br/>{company['address'] or ''}", small), Spacer(1, 6),
           Paragraph(f"<b>TAX INVOICE</b> &nbsp; No: <b>{inv.number}</b> &nbsp; Date: {inv.invoice_date} &nbsp; Order: {o.number}", st["Normal"]), Spacer(1, 6),
           Table([[Paragraph(f"<b>Bill to</b><br/>{cust['name']}<br/>GSTIN: {cust['gstin'] or 'Unregistered'}<br/>{cust['billing_address'] or ''}", small),
                   Paragraph(f"<b>Ship to</b><br/>{o.shipping_address or cust['shipping_address'] or ''}", small)]], colWidths=[91 * mm, 91 * mm]), Spacer(1, 8)]
    head = ["#", "Description", "HSN", "Qty", "Rate", "Taxable", "GST %", "GST", "Amount"]
    data, sub, tax = [head], Decimal(0), Decimal(0)
    for n, l in enumerate(lines, 1):
        taxable = (Decimal(l["qty"]) * Decimal(l["unit_price"])).quantize(Decimal("0.01"))
        gst = (taxable * Decimal(l["tax_rate"]) / 100).quantize(Decimal("0.01"))
        sub, tax = sub + taxable, tax + gst
        data.append([n, l["name"], l["hsn_code"] or "-", f"{l['qty']:.3f}".rstrip("0").rstrip("."), f"{l['unit_price']:.2f}", f"{taxable:.2f}", f"{l['tax_rate']:.0f}", f"{gst:.2f}", f"{taxable + gst:.2f}"])
    t = Table(data, repeatRows=1, colWidths=[8 * mm, 56 * mm, 18 * mm, 15 * mm, 18 * mm, 20 * mm, 12 * mm, 17 * mm, 20 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#126B61")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E4E8E4")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    els += [t, Spacer(1, 8)]
    split = [["Taxable value", f"{sub:.2f}"]] + ([["CGST", f"{tax / 2:.2f}"], ["SGST", f"{tax / 2:.2f}"]] if intra else [["IGST", f"{tax:.2f}"]]) + [["<b>Invoice total</b>", f"<b>{sub + tax:.2f}</b>"]]
    tt = Table([[Paragraph(a, small), Paragraph(b, small)] for a, b in split], colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    els += [tt, Spacer(1, 10), Paragraph("This is a computer-generated invoice. E-invoice IRN / e-way bill are not attached (provider integration not configured).", small)]
    doc.build(els)
    if sub + tax != inv.total:
        raise Unprocessable("Invoice lines do not reconcile with the stored invoice total; PDF refused")
    return inv.number, buf.getvalue()


# ------------------------------------------------------------------ barcode verification
def scan_progress(db: Session, oid: int) -> dict:
    """Per pick line (batch): reserved quantity vs quantity scanned so far."""
    rows = db.execute(text("""
        SELECT sb.batch_id, b.batch_no, it.item_code, it.name, sum(r.qty) AS required,
               COALESCE((SELECT sum(ps.qty) FROM pick_scans ps WHERE ps.so_id = :o AND ps.batch_id IS NOT DISTINCT FROM sb.batch_id AND ps.item_id = r.item_id), 0) AS picked
          FROM stock_reservations r JOIN stock_balances sb ON sb.id = r.balance_id JOIN items it ON it.id = r.item_id LEFT JOIN inventory_batches b ON b.id = sb.batch_id
         WHERE r.reference_type = 'sales_order' AND r.reference_id = :s AND r.status = 'ACTIVE' GROUP BY sb.batch_id, b.batch_no, it.item_code, it.name, r.item_id ORDER BY it.item_code, b.batch_no"""),
                      {"o": oid, "s": str(oid)}).mappings().all()
    lines = [{"batch_id": r["batch_id"], "batch_no": r["batch_no"], "item_code": r["item_code"], "item": r["name"], "required": str(r["required"]), "picked": str(r["picked"]),
              "remaining": str(max(Decimal(0), r["required"] - r["picked"])), "complete": r["picked"] >= r["required"]} for r in rows]
    return {"order_id": oid, "lines": lines, "complete": bool(lines) and all(l["complete"] for l in lines)}


def scan_pick(db: Session, cu: CurrentUser, oid: int, code: str, qty: Decimal) -> dict:
    """Records one scan against the order's allocation. A batch not on the pick list, or more than was reserved, is refused: that is the mis-pick the scan exists to catch."""
    from app.barcodes import service as bc
    o = db.scalar(select(SalesOrder).where(SalesOrder.id == oid).with_for_update())          # serialise concurrent scanners on one order
    if not o:
        raise NotFound("Sales order")
    if o.status != "ALLOCATED":
        raise BusinessConflict("invalid_status", f"Picking is verified on ALLOCATED orders (this one is {o.status})")
    cands = bc.resolve_ids(db, code, "BATCH")
    if not cands:
        item = bc.resolve_ids(db, code, "ITEM")
        raise BusinessConflict("not_a_batch", "Scan the batch label on the product" + (" (a product barcode was read, which cannot tell batches apart)" if item else ""))
    prog = scan_progress(db, oid)
    line = next((l for l in prog["lines"] if l["batch_id"] in cands), None)
    if not line:
        raise BusinessConflict("wrong_batch", "That batch is not on this order's pick list", scanned=code)
    if Decimal(line["picked"]) + qty > Decimal(line["required"]):
        raise BusinessConflict("over_pick", "That would pick more than was allocated for this batch", required=line["required"], picked=line["picked"], remaining=line["remaining"])
    item_id = db.scalar(text("SELECT item_id FROM inventory_batches WHERE id = :b"), {"b": line["batch_id"]})
    db.add(PickScan(so_id=oid, batch_id=line["batch_id"], item_id=item_id, qty=qty, scanned_by=cu.id))
    db.flush()
    audit.record(db, user=cu, action="pick_scan", entity="sales_order", entity_id=oid, after={"batch_id": line["batch_id"], "qty": str(qty)})
    return scan_progress(db, oid)


def scan_manifest(db: Session, cu: CurrentUser, manifest_no: str, code: str) -> dict:
    """Marks a shipment as handed to the courier when its AWB / label is scanned. Idempotent: scanning twice is harmless."""
    from app.barcodes import service as bc
    ships = list(db.scalars(select(Shipment).where(Shipment.manifest_no == manifest_no).order_by(Shipment.id).with_for_update()))
    if not ships:
        raise NotFound("Manifest")
    ids = set(bc.resolve_ids(db, code, "SHIP"))
    s = next((x for x in ships if x.id in ids), None)
    if not s:
        raise BusinessConflict("not_on_manifest", "That shipment is not on this manifest", scanned=code)
    already = s.handed_over_at is not None
    if not already:
        s.handed_over_at = datetime.now(timezone.utc)
        audit.record(db, user=cu, action="handover_scan", entity="shipment", entity_id=s.id, after={"manifest": manifest_no})
    return {"manifest_no": manifest_no, "scanned": s.awb, "already_scanned": already, "handed_over": sum(1 for x in ships if x.handed_over_at), "total": len(ships),
            "complete": all(x.handed_over_at for x in ships)}


def shipment_lines(db: Session, oid: int) -> list[dict]:
    """What left on this order and how much of each line can still be returned (drives the return form)."""
    _order(db, oid)
    rows = db.execute(text("""
        SELECT sl.id AS shipment_line_id, it.item_code, it.name, b.batch_no, sl.qty AS shipped,
               COALESCE((SELECT sum(rl.qty) FROM sales_return_lines rl WHERE rl.shipment_line_id = sl.id), 0) AS returned
          FROM shipment_lines sl JOIN shipments s ON s.id = sl.shipment_id JOIN items it ON it.id = sl.item_id LEFT JOIN inventory_batches b ON b.id = sl.batch_id WHERE s.so_id = :o ORDER BY sl.id"""), {"o": oid}).mappings().all()
    return [{**dict(r), "shipped": str(r["shipped"]), "returned": str(r["returned"]), "returnable": str(r["shipped"] - r["returned"])} for r in rows]
