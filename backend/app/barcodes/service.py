"""Barcode support. Every label printed by the system encodes `MT:<KIND>:<id>` (Code128 + QR), which is unambiguous even when business codes repeat
(batch numbers are only unique per item). Scanners also read the *business* codes already on documents (item code, retail barcode, batch no., PO/SO numbers,
AWB, asset id, location code ...), so existing paperwork is scannable. `resolve` turns any scanned text into the records it identifies, filtered to what the
caller is allowed to read."""
import io
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.approvals.service import get_setting
from app.core.deps import CurrentUser
from app.core.errors import NotFound


@dataclass(frozen=True)
class Kind:
    code: str            # the KIND in MT:<KIND>:<id>
    label: str
    perm: str
    link: str            # front-end route; "?open=<id>" opens the record
    table: str
    by_id: str           # SQL selecting (id, label, status) for :i
    by_code: list[tuple[str, str]]   # (column, description) matched exactly against the scanned text


KINDS: dict[str, Kind] = {k.code: k for k in [
    Kind("ITEM", "Item", "masters.item:read", "/masters/items?q={label}", "items", "SELECT id, item_code || ' — ' || name, CASE WHEN is_active THEN 'ACTIVE' ELSE 'INACTIVE' END FROM items WHERE id = :i", [("item_code", "item code"), ("barcode", "retail barcode")]),
    Kind("BATCH", "Batch", "inventory.batch:read", "/m/inventory/batches?open={id}", "inventory_batches",
         "SELECT b.id, b.batch_no || ' · ' || i.item_code, b.qc_status FROM inventory_batches b JOIN items i ON i.id = b.item_id WHERE b.id = :i", [("batch_no", "batch number"), ("supplier_lot_no", "supplier lot")]),
    Kind("LOC", "Storage location", "masters.location:read", "/masters/locations?q={label}", "storage_locations", "SELECT id, code, 'ACTIVE' FROM storage_locations WHERE id = :i", [("code", "location code")]),
    Kind("WH", "Warehouse", "masters.warehouse:read", "/masters/warehouses?q={label}", "warehouses", "SELECT id, code || ' — ' || name, 'ACTIVE' FROM warehouses WHERE id = :i", [("code", "warehouse code")]),
    Kind("EQP", "Equipment", "maintenance.equipment:read", "/m/maintenance/equipment?open={id}", "equipment", "SELECT id, asset_id || ' — ' || name, status FROM equipment WHERE id = :i", [("asset_id", "asset id")]),
    Kind("PO", "Purchase order", "procurement.po:read", "/m/procurement/orders?open={id}", "purchase_orders", "SELECT id, number, status FROM purchase_orders WHERE id = :i", [("number", "PO number")]),
    Kind("PR", "Purchase request", "procurement.pr:read", "/m/procurement/requests?open={id}", "purchase_requests", "SELECT id, number, status FROM purchase_requests WHERE id = :i", [("number", "PR number")]),
    Kind("GR", "Goods receipt", "procurement.receipt:read", "/m/procurement/receipts?open={id}", "goods_receipts", "SELECT id, number, status FROM goods_receipts WHERE id = :i", [("number", "receipt number"), ("delivery_note_no", "delivery note")]),
    Kind("SO", "Sales order", "dispatch.order:read", "/m/dispatch/orders?open={id}", "sales_orders", "SELECT id, number, status FROM sales_orders WHERE id = :i", [("number", "order number"), ("external_ref", "customer / marketplace reference")]),
    Kind("SHIP", "Shipment", "dispatch.shipment:read", "/m/dispatch/shipments?open={id}", "shipments", "SELECT s.id, COALESCE(s.awb, 'shipment ' || s.id) , s.status FROM shipments s WHERE s.id = :i", [("awb", "AWB")]),
    Kind("INV", "Sales invoice", "accounts.sales_invoice:read", "/m/dispatch/invoices?open={id}", "sales_invoices", "SELECT id, number, status FROM sales_invoices WHERE id = :i", [("number", "invoice number")]),
    Kind("RET", "Sales return", "dispatch.return:read", "/m/dispatch/returns?open={id}", "sales_returns", "SELECT id, number, status FROM sales_returns WHERE id = :i", [("number", "return number")]),
    Kind("PRD", "Production order", "production.order:read", "/m/production/orders?open={id}", "production_orders", "SELECT id, number, status FROM production_orders WHERE id = :i", [("number", "production order")]),
    Kind("WO", "Maintenance work order", "maintenance.workorder:read", "/m/maintenance/work-orders?open={id}", "maintenance_work_orders", "SELECT id, number, status FROM maintenance_work_orders WHERE id = :i", [("number", "work order")]),
    Kind("INSP", "Inspection", "quality.inspection:read", "/m/quality/inspections?open={id}", "inspections", "SELECT id, number, status FROM inspections WHERE id = :i", [("number", "inspection number")]),
    Kind("STK", "Stocktake", "inventory.stocktake:count", "/m/inventory/stocktake?open={id}", "stocktakes", "SELECT id, number, status FROM stocktakes WHERE id = :i", [("number", "stocktake number")]),
    Kind("RFQ", "RFQ", "procurement.rfq:read", "/m/procurement/rfqs?open={id}", "rfqs", "SELECT id, number, status FROM rfqs WHERE id = :i", [("number", "RFQ number")]),
    Kind("QT", "Quotation", "sales.quotation:read", "/m/sales/quotations?open={id}", "sales_quotations", "SELECT id, number, status FROM sales_quotations WHERE id = :i", [("number", "quotation number")]),
]}
MT = re.compile(r"^MT:([A-Z]+):(\d{1,12})$")


def code_for(kind: str, id_: int) -> str:
    return f"MT:{kind}:{id_}"


def _match(k: Kind, row) -> dict:
    return {"type": k.code, "type_label": k.label, "id": row[0], "label": row[1], "status": row[2], "link": k.link.format(id=row[0], label=row[1].split(" ")[0])}


def resolve(db: Session, cu: CurrentUser, raw: str) -> dict:
    code = (raw or "").strip()
    if not code or len(code) > 120:
        return {"code": code, "matches": [], "format": "invalid"}
    out: list[dict] = []
    m = MT.match(code.upper())
    if m:
        k = KINDS.get(m.group(1))
        if k and cu.can(k.perm):
            row = db.execute(text(k.by_id), {"i": int(m.group(2))}).first()
            if row:
                out.append(_match(k, row))
        return {"code": code, "matches": out, "format": "mintree"}
    for k in KINDS.values():
        if not cu.can(k.perm):
            continue
        for col, _desc in k.by_code:
            for (i,) in db.execute(text(f"SELECT id FROM {k.table} WHERE {col} = :c ORDER BY id DESC LIMIT 5"), {"c": code}).all():
                row = db.execute(text(k.by_id), {"i": i}).first()
                if row and not any(o["type"] == k.code and o["id"] == row[0] for o in out):
                    out.append(_match(k, row))
    return {"code": code, "matches": out, "format": "business-code"}


def resolve_ids(db: Session, raw: str, kind: str) -> list[int]:
    """Ids of records of ONE kind identified by a scan, ignoring read permissions. For workflows whose own permission already authorises the scan
    (e.g. packing may verify any batch it was handed) -- nothing about the record is returned to the caller beyond the verification result."""
    code = (raw or "").strip()
    k = KINDS[kind]
    m = MT.match(code.upper())
    if m:
        return [int(m.group(2))] if m.group(1) == kind and db.execute(text(k.by_id), {"i": int(m.group(2))}).first() else []
    ids: list[int] = []
    for col, _d in k.by_code:
        ids += [i for (i,) in db.execute(text(f"SELECT id FROM {k.table} WHERE {col} = :c ORDER BY id DESC LIMIT 5"), {"c": code}).all() if i not in ids]
    return ids


# ------------------------------------------------------------------ labels (PDF)
LABEL_W, LABEL_H = 90, 55          # mm


def _draw(c, mm, kind: str, id_: int, title: str, lines: list[str], company: str) -> None:
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import code128, qr
    from reportlab.graphics.shapes import Drawing
    val = code_for(kind, id_)
    c.setFont("Helvetica", 6.5)
    c.drawString(4 * mm, (LABEL_H - 5) * mm, company.upper())
    c.setFont("Helvetica-Bold", 11)
    c.drawString(4 * mm, (LABEL_H - 11) * mm, title[:34])
    c.setFont("Helvetica", 8)
    y = LABEL_H - 16
    for ln in lines[:4]:
        c.drawString(4 * mm, y * mm, ln[:46])
        y -= 4
    bc = code128.Code128(val, barHeight=13 * mm, barWidth=0.30 * mm, quiet=False)
    bc.drawOn(c, 4 * mm, 9 * mm)
    c.setFont("Helvetica", 7)
    c.drawString(4 * mm, 5 * mm, val)
    w = qr.QrCodeWidget(val)
    x0, y0, x1, y1 = w.getBounds()
    size = 22 * mm
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(w)
    renderPDF.draw(d, c, (LABEL_W - 4) * mm - size, (LABEL_H - 8) * mm - size)


def labels_pdf(db: Session, kind: str, id_: int, copies: int = 1) -> tuple[str, bytes]:
    """One label page per copy (90 x 55 mm). GR labels print one page per received batch line; SO labels are carton tickets."""
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    company = str(get_setting(db, "company.name") or "Mintree")
    q = lambda sql, **p: db.execute(text(sql), p).mappings().first()
    pages: list[tuple[str, int, str, list[str]]] = []      # (kind, id, title, lines)
    if kind == "batch":
        r = q("""SELECT b.id, b.batch_no, b.expiry_date, b.mfg_date, b.qc_status, b.supplier_lot_no, i.item_code, i.name FROM inventory_batches b JOIN items i ON i.id = b.item_id WHERE b.id = :i""", i=id_)
        if not r:
            raise NotFound("Batch")
        pages = [("BATCH", r["id"], f"Batch {r['batch_no']}", [f"{r['item_code']} {r['name']}", f"Mfg {r['mfg_date'] or '-'}   Exp {r['expiry_date'] or '-'}", f"QC: {r['qc_status']}   Lot: {r['supplier_lot_no'] or '-'}"])]
    elif kind == "item":
        r = q("SELECT id, item_code, name, kind, barcode FROM items WHERE id = :i", i=id_)
        if not r:
            raise NotFound("Item")
        pages = [("ITEM", r["id"], r["item_code"], [r["name"], f"Kind {r['kind']}", f"Retail barcode: {r['barcode'] or '-'}"])]
    elif kind == "location":
        r = q("SELECT l.id, l.code, l.name, w.code AS wh FROM storage_locations l JOIN warehouses w ON w.id = l.warehouse_id WHERE l.id = :i", i=id_)
        if not r:
            raise NotFound("Location")
        pages = [("LOC", r["id"], f"Location {r['code']}", [r["name"] or "", f"Warehouse {r['wh']}"])]
    elif kind == "equipment":
        r = q("SELECT id, asset_id, name, location, serial_no FROM equipment WHERE id = :i", i=id_)
        if not r:
            raise NotFound("Equipment")
        pages = [("EQP", r["id"], f"Asset {r['asset_id']}", [r["name"], f"Location {r['location'] or '-'}", f"Serial {r['serial_no'] or '-'}"])]
    elif kind == "shipment":
        r = q("SELECT s.id, s.awb, s.courier, o.number, o.cartons, o.shipping_address FROM shipments s JOIN sales_orders o ON o.id = s.so_id WHERE s.id = :i", i=id_)
        if not r:
            raise NotFound("Shipment")
        pages = [("SHIP", r["id"], f"AWB {r['awb'] or 'pending'}", [f"Order {r['number']}  {r['courier']}", f"Cartons {r['cartons'] or '-'}", (r["shipping_address"] or "")[:46]])]
    elif kind == "order":
        r = q("SELECT o.id, o.number, o.cartons, o.shipping_address, c.name FROM sales_orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = :i", i=id_)
        if not r:
            raise NotFound("Order")
        pages = [("SO", r["id"], f"Order {r['number']}", [r["name"], (r["shipping_address"] or "")[:46], f"Cartons {r['cartons'] or '-'}"])]
    elif kind == "production-order":
        r = q("SELECT o.id, o.number, o.planned_qty, o.status, i.item_code, i.name FROM production_orders o JOIN items i ON i.id = o.output_item_id WHERE o.id = :i", i=id_)
        if not r:
            raise NotFound("Production order")
        pages = [("PRD", r["id"], f"Job {r['number']}", [f"{r['item_code']} {r['name']}", f"Planned {r['planned_qty']}", f"Status {r['status']}"])]
    elif kind == "work-order":
        r = q("SELECT w.id, w.number, w.kind, w.priority, e.asset_id FROM maintenance_work_orders w JOIN equipment e ON e.id = w.equipment_id WHERE w.id = :i", i=id_)
        if not r:
            raise NotFound("Work order")
        pages = [("WO", r["id"], f"Work order {r['number']}", [f"{r['kind']}  priority {r['priority']}", f"Asset {r['asset_id']}"])]
    elif kind == "goods-receipt":
        rows = db.execute(text("""SELECT b.id, b.batch_no, b.expiry_date, i.item_code, i.name, gi.qty AS qty_received FROM goods_receipt_items gi JOIN inventory_batches b ON b.id = gi.batch_id JOIN items i ON i.id = b.item_id
                                    WHERE gi.gr_id = :g ORDER BY gi.line_no"""), {"g": id_}).mappings().all()
        if not rows:
            raise NotFound("Goods receipt")
        pages = [("BATCH", r["id"], f"Batch {r['batch_no']}", [f"{r['item_code']} {r['name']}", f"Qty {r['qty_received']}   Exp {r['expiry_date'] or '-'}", "QC: QUARANTINE until released"]) for r in rows]
    else:
        raise NotFound("Label type")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(LABEL_W * mm, LABEL_H * mm))
    c.setTitle(f"Labels {kind} {id_}")
    for k, i, title, lines in pages:
        for _ in range(max(1, min(copies, 100))):
            _draw(c, mm, k, i, title, lines, company)
            c.showPage()
    c.save()
    return f"{kind}-{id_}", buf.getvalue()
