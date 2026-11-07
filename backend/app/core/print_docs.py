"""Printable business documents that did not exist yet: purchase order, sales quotation and delivery challan. Same look as the invoice and certificate of analysis:
the authentic logo once in the header, plain tables, amounts computed from the stored lines in Decimal and refused if they disagree with the stored totals."""
from __future__ import annotations

import io
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.approvals.service import get_setting
from app.core.brand import header_flowable
from app.core.errors import NotFound, Unprocessable


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    st = getSampleStyleSheet()
    return colors, mm, st, st["BodyText"].clone("small", fontSize=8, leading=10)


def _table(rows, widths, right_from: int):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle
    t = Table(rows, repeatRows=1, colWidths=[w * mm for w in widths])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#126B61")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E4E8E4")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def _build(title: str, els: list) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=title).build(els)
    return buf.getvalue()


def _company(db: Session) -> dict:
    return {k: get_setting(db, f"company.{k}") for k in ("name", "gstin", "address")}


def _q(n: Decimal, d: int = 2) -> str:
    return f"{Decimal(n):.{d}f}"


def purchase_order_pdf(db: Session, po_id: int) -> tuple[str, bytes]:
    from reportlab.platypus import Paragraph, Spacer
    colors, mm, st, small = _styles()
    po = db.execute(text("SELECT p.number, p.status, p.expected_date, p.notes, p.total_amount, p.created_at, s.name AS supplier, s.gstin, s.address_line1, s.city, s.state, s.payment_terms_days "
                         "FROM purchase_orders p JOIN suppliers s ON s.id = p.supplier_id WHERE p.id = :i"), {"i": po_id}).mappings().first()
    if not po:
        raise NotFound("Purchase order")
    if po["status"] in ("DRAFT", "PENDING_APPROVAL", "CANCELLED"):
        raise Unprocessable(f"A purchase order is printed for the supplier only once approved (this one is {po['status']})")
    lines = db.execute(text("SELECT it.item_code, it.name, u.code AS uom, pi.qty_ordered, pi.unit_price, pi.tax_rate FROM purchase_order_items pi JOIN items it ON it.id = pi.item_id "
                            "JOIN units_of_measure u ON u.id = it.uom_id WHERE pi.po_id = :i ORDER BY pi.line_no"), {"i": po_id}).mappings().all()
    co = _company(db)
    rows, sub, tax = [["#", "Item", "Description", "Qty", "UoM", "Rate", "GST %", "Amount"]], Decimal(0), Decimal(0)
    for n, l in enumerate(lines, 1):
        amt = (Decimal(l["qty_ordered"]) * Decimal(l["unit_price"])).quantize(Decimal("0.01"))
        sub, tax = sub + amt, tax + (amt * Decimal(l["tax_rate"]) / 100).quantize(Decimal("0.01"))
        rows.append([n, l["item_code"], l["name"], _q(l["qty_ordered"], 3).rstrip("0").rstrip("."), l["uom"], _q(l["unit_price"]), _q(l["tax_rate"], 0), _q(amt)])
    if sub != Decimal(po["total_amount"]) and sub + tax != Decimal(po["total_amount"]):
        raise Unprocessable("Purchase order lines do not reconcile with the stored total; PDF refused")
    els = [header_flowable(co["name"] or "Mintree", "PURCHASE ORDER", po["number"]), Spacer(1, 6),
           Paragraph(f"<b>{co['name'] or 'Mintree'}</b><br/>GSTIN: {co['gstin'] or '-'}<br/>{co['address'] or ''}", small), Spacer(1, 6),
           Paragraph(f"<b>To:</b> {po['supplier']}<br/>GSTIN: {po['gstin'] or '-'}<br/>{po['address_line1'] or ''} {po['city'] or ''} {po['state'] or ''}<br/>"
                     f"Order date: {po['created_at']:%d %b %Y} &nbsp; Expected delivery: {po['expected_date'] or '-'} &nbsp; Payment terms: {po['payment_terms_days'] or '-'} days", small), Spacer(1, 8),
           _table(rows, [8, 24, 62, 18, 12, 20, 14, 24], 3), Spacer(1, 8),
           Paragraph(f"Taxable value {_q(sub)} &nbsp; GST {_q(tax)} &nbsp; <b>Total {_q(sub + tax)}</b>", small), Spacer(1, 8),
           Paragraph(po["notes"] or "", small), Spacer(1, 10),
           Paragraph("Goods are subject to incoming quality inspection. Quote the PO number on all delivery notes and invoices.", small)]
    return po["number"], _build(f"PO {po['number']}", els)


def quotation_pdf(db: Session, qid: int) -> tuple[str, bytes]:
    from reportlab.platypus import Paragraph, Spacer
    colors, mm, st, small = _styles()
    q = db.execute(text("SELECT q.number, q.status, q.valid_until, q.notes, q.subtotal, q.tax_total, q.total, q.created_at, c.name AS customer, c.gstin, c.billing_address "
                        "FROM sales_quotations q JOIN customers c ON c.id = q.customer_id WHERE q.id = :i"), {"i": qid}).mappings().first()
    if not q:
        raise NotFound("Quotation")
    lines = db.execute(text("SELECT it.item_code, it.name, qi.qty, qi.unit_price, qi.tax_rate FROM sales_quotation_items qi JOIN items it ON it.id = qi.item_id WHERE qi.quotation_id = :i ORDER BY qi.line_no"), {"i": qid}).mappings().all()
    co = _company(db)
    rows, sub, tax = [["#", "Item", "Description", "Qty", "Rate", "GST %", "Amount"]], Decimal(0), Decimal(0)
    for n, l in enumerate(lines, 1):
        amt = (Decimal(l["qty"]) * Decimal(l["unit_price"])).quantize(Decimal("0.01"))
        sub, tax = sub + amt, tax + (amt * Decimal(l["tax_rate"]) / 100).quantize(Decimal("0.01"))
        rows.append([n, l["item_code"], l["name"], _q(l["qty"], 3).rstrip("0").rstrip("."), _q(l["unit_price"]), _q(l["tax_rate"], 0), _q(amt)])
    if sub + tax != Decimal(q["total"]):
        raise Unprocessable("Quotation lines do not reconcile with the stored total; PDF refused")
    els = [header_flowable(co["name"] or "Mintree", "QUOTATION", q["number"]), Spacer(1, 6),
           Paragraph(f"<b>{co['name'] or 'Mintree'}</b><br/>GSTIN: {co['gstin'] or '-'}<br/>{co['address'] or ''}", small), Spacer(1, 6),
           Paragraph(f"<b>For:</b> {q['customer']}<br/>GSTIN: {q['gstin'] or 'Unregistered'}<br/>{q['billing_address'] or ''}<br/>Date: {q['created_at']:%d %b %Y} &nbsp; Valid until: {q['valid_until'] or '-'} &nbsp; Status: {q['status']}", small), Spacer(1, 8),
           _table(rows, [8, 26, 70, 18, 22, 14, 24], 3), Spacer(1, 8),
           Paragraph(f"Taxable value {_q(sub)} &nbsp; GST {_q(tax)} &nbsp; <b>Total {_q(sub + tax)}</b>", small), Spacer(1, 8), Paragraph(q["notes"] or "", small), Spacer(1, 10),
           Paragraph("Prices are exclusive of GST unless stated. This quotation is not an order; acceptance is recorded in the system by our sales team.", small)]
    return q["number"], _build(f"Quotation {q['number']}", els)


def challan_pdf(db: Session, shipment_id: int) -> tuple[str, bytes]:
    from reportlab.platypus import Paragraph, Spacer
    colors, mm, st, small = _styles()
    s = db.execute(text("SELECT s.id, s.shipment_no, s.awb, s.courier, s.status, s.created_at, o.number AS order_no, o.shipping_address, c.name AS customer, c.gstin, c.shipping_address AS cust_addr "
                        "FROM shipments s JOIN sales_orders o ON o.id = s.so_id JOIN customers c ON c.id = o.customer_id WHERE s.id = :i"), {"i": shipment_id}).mappings().first()
    if not s:
        raise NotFound("Shipment")
    lines = db.execute(text("SELECT it.item_code, it.name, b.batch_no, b.expiry_date, sl.qty FROM shipment_lines sl JOIN items it ON it.id = sl.item_id LEFT JOIN inventory_batches b ON b.id = sl.batch_id "
                            "WHERE sl.shipment_id = :i ORDER BY sl.id"), {"i": shipment_id}).mappings().all()
    pk = db.execute(text("SELECT package_no, weight_kg, contents FROM shipment_packages WHERE shipment_id = :i ORDER BY package_no"), {"i": shipment_id}).mappings().all()
    co = _company(db)
    number = f"{s['order_no']}/S{s['shipment_no']}" if s["shipment_no"] else f"SHP-{s['id']}"
    rows = [["#", "Item", "Description", "Batch", "Expiry", "Qty"]] + [[n, l["item_code"], l["name"], l["batch_no"] or "-", l["expiry_date"] or "-", _q(l["qty"], 3).rstrip("0").rstrip(".")] for n, l in enumerate(lines, 1)]
    els = [header_flowable(co["name"] or "Mintree", "DELIVERY CHALLAN", number), Spacer(1, 6),
           Paragraph(f"<b>{co['name'] or 'Mintree'}</b><br/>GSTIN: {co['gstin'] or '-'}<br/>{co['address'] or ''}", small), Spacer(1, 6),
           Paragraph(f"<b>Deliver to:</b> {s['customer']}<br/>{s['shipping_address'] or s['cust_addr'] or ''}<br/>Order: {s['order_no']} &nbsp; Courier: {s['courier'] or '-'} &nbsp; AWB: {s['awb'] or '-'} &nbsp; Date: {s['created_at']:%d %b %Y}", small), Spacer(1, 8),
           _table(rows, [8, 26, 66, 30, 24, 28], 5), Spacer(1, 8)]
    if pk:
        els += [Paragraph("Packages: " + "; ".join(f"#{p['package_no']} {p['weight_kg'] or '-'} kg" for p in pk), small), Spacer(1, 8)]
    els += [Paragraph("Goods supplied on delivery challan; the tax invoice is issued separately. Receiver's signature: ______________________", small)]
    return number, _build(f"Challan {number}", els)
