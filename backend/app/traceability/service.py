"""Product 360 and Batch 360: traceability read models.

Everything here is READ-ONLY and derived from the transactional tables that already exist; nothing is inferred from the BOM and no relationship is stored a second time:

    backward   finished / bulk batch  <- production_orders.output_batch_id <- production_consumptions (the batches ACTUALLY consumed) <- goods_receipt_items.batch_id <- goods receipt <- PO <- supplier
    forward    batch -> production_consumptions -> production order -> output batch -> shipment_lines.batch_id -> shipment -> sales order -> customer ; returns via sales_return_lines -> shipment_lines
    quantities stock_ledger (append-only) against stock_balances, production_consumptions, shipment_lines and sales_return_lines; any disagreement is reported, never corrected.

Queries are batched per generation of the genealogy (one query per relationship type per level), bounded by MAX_DEPTH and MAX_NODES, so the cost does not grow with the number of nodes.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import NotFound

MAX_DEPTH = 6
MAX_NODES = 300
RESTRICTED = "Restricted"
KIND_LABEL = {"RM": "Raw material", "PM": "Packaging", "BULK": "Bulk", "FG": "Finished goods"}


def D(v: Any) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def S(v: Any) -> str | None:
    return None if v is None else str(D(v).normalize() if D(v) == D(v).to_integral() else D(v))


def Q(v: Any) -> str:
    """A quantity as a plain decimal string with at most three places (the stock precision), never scientific notation."""
    return format(D(v).quantize(Decimal("0.001")).normalize(), "f") if v is not None else "0"


def rows(db: Session, sql: str, **p) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), p).mappings().all()]


def one(db: Session, sql: str, **p) -> dict | None:
    r = db.execute(text(sql), p).mappings().first()
    return dict(r) if r else None


class Mask:
    """Names of suppliers and customers are shown only to people who may read those masters."""

    def __init__(self, can_supplier: bool, can_customer: bool) -> None:
        self.supplier, self.customer = can_supplier, can_customer

    def sup(self, name: str | None) -> str | None:
        return name if (self.supplier or name is None) else RESTRICTED

    def cus(self, name: str | None) -> str | None:
        return name if (self.customer or name is None) else RESTRICTED


# ================================================================================================================================ batch identity
BATCH_SQL = """
SELECT b.id, b.batch_no, b.item_id, i.item_code, i.name AS item_name, i.kind, u.code AS uom, b.supplier_id, s.name AS supplier_name, b.supplier_lot_no,
       b.mfg_date, b.expiry_date, b.qc_status, b.source_type, b.source_id, b.created_at, b.qc_decided_at, du.full_name AS qc_decided_by_name
  FROM inventory_batches b JOIN items i ON i.id = b.item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id
  LEFT JOIN suppliers s ON s.id = b.supplier_id LEFT JOIN users du ON du.id = b.qc_decided_by
 WHERE {where}"""


def batch_row(db: Session, batch_id: int) -> dict:
    r = one(db, BATCH_SQL.format(where="b.id = :id"), id=batch_id)
    if not r:
        raise NotFound("Batch")
    return r


def _balances(db: Session, batch_id: int) -> list[dict]:
    return rows(db, """SELECT sb.id AS balance_id, w.id AS warehouse_id, w.code AS warehouse_code, w.name AS warehouse_name, l.code AS location_code,
                              sb.qty_physical, sb.qty_reserved, sb.qty_blocked, sb.qty_available
                         FROM stock_balances sb JOIN warehouses w ON w.id = sb.warehouse_id LEFT JOIN storage_locations l ON l.id = sb.location_id
                        WHERE sb.batch_id = :b ORDER BY w.code, l.code""", b=batch_id)


def _ledger_totals(db: Session, batch_id: int) -> dict[str, Decimal]:
    return {r["movement_type"]: D(r["q"]) for r in rows(db, "SELECT movement_type, sum(qty_delta) AS q FROM stock_ledger WHERE batch_id = :b GROUP BY 1", b=batch_id)}


def _inspections(db: Session, batch_ids: list[int]) -> list[dict]:
    """Every inspection ever raised for the batches, failed ones included, with its results. A later pass never hides an earlier fail."""
    insp = rows(db, """SELECT n.id, n.number, n.stage, n.batch_id, n.status, n.decision, n.decision_reason, n.decided_at, n.created_at, n.lot_qty, n.sample_size, u.full_name AS decided_by
                         FROM inspections n LEFT JOIN users u ON u.id = n.decided_by WHERE n.batch_id = ANY(:ids) ORDER BY n.created_at, n.id""", ids=batch_ids)
    if not insp:
        return []
    res = rows(db, """SELECT r.inspection_id, r.test_name, r.value, r.passed, r.observation, r.tested_at, u.full_name AS tested_by
                        FROM inspection_results r LEFT JOIN users u ON u.id = r.tested_by WHERE r.inspection_id = ANY(:ids) ORDER BY r.tested_at, r.id""", ids=[i["id"] for i in insp])
    by: dict[int, list[dict]] = {}
    for r in res:
        by.setdefault(r["inspection_id"], []).append({"test": r["test_name"], "value": r["value"], "passed": r["passed"], "observation": r["observation"],
                                                      "tested_at": r["tested_at"], "tested_by": r["tested_by"]})
    for i in insp:
        results = by.get(i["id"], [])
        i["results"] = results
        i["failed_results"] = sum(1 for x in results if x["passed"] is False)
        i["retested"] = len(results) > len({x["test"] for x in results})          # the same test recorded more than once: a retest; every attempt is kept
    return insp


def reconcile(db: Session, b: dict) -> dict:
    """Quantity reconciliation for one batch. Each check compares two independently recorded figures; a mismatch is returned as a warning and nothing is adjusted.

    stock ledger (all movements) = physical stock across balances                       -- the ledger is the record, the balance is its running total
    raw / packaging / bulk: production consumption records = ledger PRODUCTION_ISSUE   -- what orders say they consumed vs what stock says left
    produced batches:       production order actual quantity = ledger PRODUCTION_OUTPUT
    finished stock:         shipment lines = ledger DISPATCH;  return lines (decided) = ledger RETURN
    """
    bid = b["id"]
    led = _ledger_totals(db, bid)
    bal = _balances(db, bid)
    physical = sum((D(x["qty_physical"]) for x in bal), Decimal(0))
    reserved = sum((D(x["qty_reserved"]) for x in bal), Decimal(0))
    blocked = sum((D(x["qty_blocked"]) for x in bal), Decimal(0))
    available = sum((D(x["qty_available"]) for x in bal), Decimal(0))
    consumed = D(one(db, "SELECT coalesce(sum(qty), 0) AS q FROM production_consumptions WHERE batch_id = :b", b=bid)["q"])
    shipped = D(one(db, "SELECT coalesce(sum(qty), 0) AS q FROM shipment_lines WHERE batch_id = :b", b=bid)["q"])
    delivered = D(one(db, """SELECT coalesce(sum(sl.qty), 0) AS q FROM shipment_lines sl JOIN shipments sh ON sh.id = sl.shipment_id WHERE sl.batch_id = :b AND sh.delivered_at IS NOT NULL""", b=bid)["q"])
    ret_req = D(one(db, """SELECT coalesce(sum(rl.qty), 0) AS q FROM sales_return_lines rl JOIN shipment_lines sl ON sl.id = rl.shipment_line_id WHERE sl.batch_id = :b""", b=bid)["q"])
    ret_dec = D(one(db, """SELECT coalesce(sum(rl.qty), 0) AS q FROM sales_return_lines rl JOIN sales_returns sr ON sr.id = rl.return_id JOIN shipment_lines sl ON sl.id = rl.shipment_line_id
                            WHERE sl.batch_id = :b AND sr.status = 'DECIDED'""", b=bid)["q"])
    produced_rec = one(db, "SELECT actual_qty, number FROM production_orders WHERE output_batch_id = :b", b=bid)
    received = led.get("RECEIPT", Decimal(0))
    produced = led.get("PRODUCTION_OUTPUT", Decimal(0))
    issued = -led.get("PRODUCTION_ISSUE", Decimal(0))
    dispatched = -led.get("DISPATCH", Decimal(0))
    returned = led.get("RETURN", Decimal(0))
    purchase_returned = -led.get("ISSUE", Decimal(0))
    adjustments = led.get("ADJUSTMENT", Decimal(0)) + led.get("REVERSAL", Decimal(0))
    transfers_net = led.get("TRANSFER_OUT", Decimal(0)) + led.get("TRANSFER_IN", Decimal(0))
    checks: list[dict] = []

    def check(name: str, expected: Decimal, actual: Decimal, note: str) -> None:
        checks.append({"name": name, "expected": Q(expected), "actual": Q(actual), "difference": Q(actual - expected), "ok": expected == actual, "note": note})

    check("Stock ledger equals physical stock", sum(led.values(), Decimal(0)), physical, "Sum of every ledger movement for this batch against the running balances")
    if b["kind"] in ("RM", "PM", "BULK") or issued or consumed:
        check("Consumption records equal stock issues", consumed, issued, "production_consumptions against ledger PRODUCTION_ISSUE")
    if produced_rec is not None:
        check("Produced quantity equals production order output", D(produced_rec["actual_qty"]), produced, f"Production order {produced_rec['number']}")
    if shipped or dispatched:
        check("Shipment lines equal dispatch movements", shipped, dispatched, "shipment_lines against ledger DISPATCH")
    if ret_req or returned:
        check("Received returns equal return movements", ret_dec, returned, "return lines of decided returns (restocked or rejected: either way the units came back) against ledger RETURN")
    accounted = received + produced + returned + adjustments + transfers_net - issued - dispatched - purchase_returned
    check("Movement equation equals physical stock", accounted, physical, "received + produced + returned-to-stock + adjustments + net transfers - consumed - dispatched - returned to supplier; an unlisted movement type shows here")
    warnings = [f"{c['name']}: expected {c['expected']}, recorded {c['actual']} (difference {c['difference']})" for c in checks if not c["ok"]]
    return {
        "unit": b["uom"],
        "quantities": {
            "received": Q(received), "produced": Q(produced), "returned_to_stock": Q(returned),
            "consumed_in_production": Q(issued), "dispatched": Q(dispatched), "returned_to_supplier": Q(purchase_returned),
            "adjustments_net": Q(adjustments), "transfers_net": Q(transfers_net),
            "physical": Q(physical), "reserved": Q(reserved), "blocked": Q(blocked), "available": Q(available),
            "delivered": Q(delivered), "return_requested": Q(ret_req),
        },
        "equation": {"inflow": Q(received + produced + returned), "outflow": Q(issued + dispatched + purchase_returned), "adjustments_and_transfers": Q(adjustments + transfers_net), "accounted": Q(accounted), "physical": Q(physical)},
        "checks": checks, "balanced": not warnings, "warnings": warnings,
        "definitions": {
            "physical": "Units in the building for this batch (all warehouses and locations), whatever their quality status",
            "reserved": "Physical units promised to an order or production run but not yet issued or shipped",
            "blocked": "Physical units that may not be used or sold: QUARANTINE, HOLD and REJECTED batches, and the quantity covered by an open QC decision",
            "available": "physical - reserved - blocked: what can still be promised",
            "consumed_in_production": "Issued to production orders (ledger PRODUCTION_ISSUE, confirmed by production_consumptions)",
            "dispatched": "Left on a shipment (ledger DISPATCH, confirmed by shipment_lines); delivered is the part whose shipment has a delivery date",
            "returned_to_stock": "Customer returns received back into the building (ledger RETURN); a rejected return stays blocked, a restocked one is released by the QC decision",
        },
    }


def detail(db: Session, batch_id: int, mask: Mask) -> dict:
    b = batch_row(db, batch_id)
    bal = _balances(db, batch_id)
    rec = reconcile(db, b)
    src = None
    if b["source_type"] == "goods_receipt":
        src = one(db, """SELECT g.id, g.number, g.received_at, g.po_id, po.number AS po_number, g.supplier_id, s.name AS supplier_name, g.delivery_note_no
                           FROM goods_receipt_items gi JOIN goods_receipts g ON g.id = gi.gr_id LEFT JOIN purchase_orders po ON po.id = g.po_id LEFT JOIN suppliers s ON s.id = g.supplier_id
                          WHERE gi.batch_id = :b ORDER BY g.id LIMIT 1""", b=batch_id)
        if src:
            src["supplier_name"] = mask.sup(src["supplier_name"])
    prod = one(db, """SELECT po.id, po.number, po.status, po.planned_qty, po.actual_qty, po.yield_pct, po.unit_cost, po.completed_at, f.code AS formulation, fv.version_no AS formulation_version, bo.version_no AS bom_version
                        FROM production_orders po LEFT JOIN formulation_versions fv ON fv.id = po.formulation_version_id LEFT JOIN formulations f ON f.id = fv.formulation_id LEFT JOIN boms bo ON bo.id = po.bom_id
                       WHERE po.output_batch_id = :b""", b=batch_id)
    insp = _inspections(db, [batch_id])
    ident = {
        "id": b["id"], "batch_no": b["batch_no"], "item_id": b["item_id"], "item_code": b["item_code"], "item_name": b["item_name"], "kind": b["kind"], "kind_label": KIND_LABEL.get(b["kind"], b["kind"]),
        "uom": b["uom"], "supplier_lot_no": b["supplier_lot_no"], "supplier_name": mask.sup(b["supplier_name"]), "mfg_date": b["mfg_date"], "expiry_date": b["expiry_date"], "received_at": b["created_at"],
        "qc_status": b["qc_status"], "qc_decided_at": b["qc_decided_at"], "qc_decided_by": b["qc_decided_by_name"], "source_type": b["source_type"],
        "business_status": _business_status(b, rec),
    }
    locs = [{"warehouse": x["warehouse_code"], "warehouse_name": x["warehouse_name"], "location": x["location_code"], "physical": Q(x["qty_physical"]), "reserved": Q(x["qty_reserved"]),
             "blocked": Q(x["qty_blocked"]), "available": Q(x["qty_available"])} for x in bal]
    return {"batch": ident, "source_receipt": src, "production_order": prod, "locations": locs, "reconciliation": rec, "inspections": insp}


def _business_status(b: dict, rec: dict) -> str:
    q = rec["quantities"]
    if b["qc_status"] in ("REJECTED",):
        return "Rejected"
    if b["qc_status"] in ("QUARANTINE", "HOLD"):
        return "Awaiting QC release" if b["qc_status"] == "QUARANTINE" else "On QC hold"
    if D(q["physical"]) == 0:
        return "Fully consumed" if D(q["consumed_in_production"]) >= D(q["received"]) + D(q["produced"]) and D(q["consumed_in_production"]) > 0 else "Fully dispatched" if D(q["dispatched"]) > 0 else "No stock"
    return "Released, in stock" if D(q["available"]) > 0 else "Released, fully reserved"


def movements(db: Session, batch_id: int, page: int, page_size: int) -> dict:
    batch_row(db, batch_id)
    total = one(db, "SELECT count(*) AS n FROM stock_ledger WHERE batch_id = :b", b=batch_id)["n"]
    items = rows(db, """SELECT l.id, l.posted_at, l.movement_type, l.source_type, l.source_id, l.qty_delta, l.blocked_delta, l.reason, w.code AS warehouse, u.full_name AS actor
                          FROM stock_ledger l JOIN warehouses w ON w.id = l.warehouse_id LEFT JOIN users u ON u.id = l.actor_id WHERE l.batch_id = :b ORDER BY l.id DESC LIMIT :n OFFSET :o""",
                 b=batch_id, n=page_size, o=(page - 1) * page_size)
    for r in items:
        r["qty_delta"], r["blocked_delta"] = Q(r["qty_delta"]), Q(r["blocked_delta"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def history(db: Session, batch_id: int, limit: int = 100) -> list[dict]:
    """Audit trail for the batch and its inspections: who changed what and when."""
    ids = [r["id"] for r in rows(db, "SELECT id FROM inspections WHERE batch_id = :b", b=batch_id)]
    return rows(db, """SELECT occurred_at, user_email, action, entity, entity_id, reason FROM audit_logs
                        WHERE (entity = 'inventory_batch' AND entity_id = :b) OR (entity = 'inspection' AND entity_id = ANY(:ins)) OR (entity = 'batch_impact' AND entity_id = :b)
                        ORDER BY occurred_at DESC, id DESC LIMIT :n""", b=str(batch_id), ins=[str(i) for i in ids], n=limit)


# ================================================================================================================================ the graph
class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.truncated = False

    def node(self, nid: str, ntype: str, ref: str, label: str | None = None, **kw) -> bool:
        if nid in self.nodes:
            return True
        if len(self.nodes) >= MAX_NODES:
            self.truncated = True
            return False
        self.nodes[nid] = {"id": nid, "type": ntype, "ref": ref, "label": label, **kw}
        return True

    def edge(self, s: str, t: str, qty: Any = None, unit: str | None = None, date: Any = None, label: str | None = None) -> None:
        if s not in self.nodes or t not in self.nodes:
            return
        key = (s, t, label)
        if key in self.edges:
            if qty is not None and self.edges[key]["qty"] is not None:
                self.edges[key]["qty"] = Q(D(self.edges[key]["qty"]) + D(qty))
            return
        self.edges[key] = {"source": s, "target": t, "qty": None if qty is None else Q(qty), "unit": unit, "date": date, "label": label}

    def out(self, focus: str, direction: str) -> dict:
        return {"focus": focus, "direction": direction, "nodes": list(self.nodes.values()), "edges": list(self.edges.values()), "truncated": self.truncated,
                "limits": {"max_depth": MAX_DEPTH, "max_nodes": MAX_NODES}}


def _batch_node(g: Graph, r: dict) -> str:
    nid = f"batch:{r['id']}"
    g.node(nid, "batch", r["batch_no"], r["item_name"], kind=r["kind"], status=r["qc_status"], unit=r["uom"], item_code=r["item_code"], date=r.get("mfg_date"), href=f"/inventory/batches/{r['id']}/traceability")
    return nid


def _batches_by_id(db: Session, ids: list[int]) -> dict[int, dict]:
    return {r["id"]: r for r in rows(db, BATCH_SQL.format(where="b.id = ANY(:ids)"), ids=ids)}


def _qc_nodes(db: Session, g: Graph, ids: list[int]) -> None:
    for i in _inspections(db, ids):
        nid = f"qc:{i['id']}"
        if g.node(nid, "qc", i["number"], f"{i['stage'].title()} inspection", status=i["decision"] or i["status"], date=i["decided_at"] or i["created_at"],
                  href=f"/m/quality/inspections?open={i['id']}", detail={"failed_results": i["failed_results"], "retested": i["retested"]}):
            g.edge(f"batch:{i['batch_id']}", nid, label="inspected")


def backward(db: Session, batch_id: int, mask: Mask, max_depth: int = MAX_DEPTH) -> dict:
    """What this batch is made of and where it came from: only receipts and ACTUAL consumption records, level by level."""
    g = Graph()
    root = batch_row(db, batch_id)
    _batch_node(g, root)
    frontier, seen, depth = [batch_id], {batch_id}, 0
    while frontier and depth < max_depth:
        depth += 1
        info = _batches_by_id(db, frontier)
        for r in info.values():
            _batch_node(g, r)
        for r in rows(db, """SELECT gi.batch_id, sum(gi.qty) AS qty, g.id AS gr_id, g.number AS gr_no, g.received_at, g.status AS gr_status, g.po_id, po.number AS po_no, po.status AS po_status,
                                    g.supplier_id, s.name AS supplier_name
                               FROM goods_receipt_items gi JOIN goods_receipts g ON g.id = gi.gr_id LEFT JOIN purchase_orders po ON po.id = g.po_id LEFT JOIN suppliers s ON s.id = g.supplier_id
                              WHERE gi.batch_id = ANY(:ids) GROUP BY gi.batch_id, g.id, g.number, g.received_at, g.status, g.po_id, po.number, po.status, g.supplier_id, s.name""", ids=frontier):
            unit = info[r["batch_id"]]["uom"]
            if r["supplier_id"]:
                g.node(f"supplier:{r['supplier_id']}", "supplier", mask.sup(r["supplier_name"]) or "", "Supplier", href="/masters/suppliers" if mask.supplier else None)
            if r["po_id"]:
                g.node(f"po:{r['po_id']}", "purchase_order", r["po_no"], "Purchase order", status=r["po_status"], href=f"/m/procurement/orders?open={r['po_id']}")
                g.edge(f"supplier:{r['supplier_id']}", f"po:{r['po_id']}")
            g.node(f"gr:{r['gr_id']}", "goods_receipt", r["gr_no"], "Goods receipt", status=r["gr_status"], date=r["received_at"], href=f"/m/procurement/receipts?open={r['gr_id']}")
            g.edge(f"po:{r['po_id']}" if r["po_id"] else f"supplier:{r['supplier_id']}", f"gr:{r['gr_id']}", r["qty"], unit, r["received_at"], "delivered")
            g.edge(f"gr:{r['gr_id']}", f"batch:{r['batch_id']}", r["qty"], unit, r["received_at"], "received into")
        for r in rows(db, """SELECT b.id AS batch_id, b.source_id FROM inventory_batches b WHERE b.id = ANY(:ids) AND b.source_type = 'opening_stock'""", ids=frontier):
            op = one(db, "SELECT coalesce(sum(qty_delta), 0) AS q, min(posted_at) AS at FROM stock_ledger WHERE batch_id = :b AND source_type = 'opening_stock'", b=r["batch_id"])
            g.node(f"opening:{r['batch_id']}", "opening_stock", "Opening stock", "Loaded at go-live", date=op["at"])
            g.edge(f"opening:{r['batch_id']}", f"batch:{r['batch_id']}", op["q"], info[r["batch_id"]]["uom"], op["at"], "loaded")
        orders = rows(db, """SELECT po.id, po.number, po.status, po.output_batch_id, po.actual_qty, po.planned_qty, po.completed_at, po.yield_pct, fv.id AS fv_id, fv.version_no, fv.status AS fv_status,
                                    f.code AS fcode, bo.id AS bom_id, bo.version_no AS bom_ver
                               FROM production_orders po LEFT JOIN formulation_versions fv ON fv.id = po.formulation_version_id LEFT JOIN formulations f ON f.id = fv.formulation_id
                               LEFT JOIN boms bo ON bo.id = po.bom_id WHERE po.output_batch_id = ANY(:ids)""", ids=frontier)
        nxt: list[int] = []
        for o in orders:
            oid = f"order:{o['id']}"
            g.node(oid, "production_order", o["number"], "Production order", status=o["status"], date=o["completed_at"], href=f"/m/production/orders?open={o['id']}",
                   detail={"planned": Q(o["planned_qty"]), "actual": Q(o["actual_qty"]), "yield_pct": S(o["yield_pct"])})
            g.edge(oid, f"batch:{o['output_batch_id']}", o["actual_qty"], info[o["output_batch_id"]]["uom"], o["completed_at"], "produced")
            if o["fv_id"]:
                g.node(f"formulation:{o['fv_id']}", "formulation", f"{o['fcode']} v{o['version_no']}", "Formulation version", status=o["fv_status"], href="/m/rnd/formulations")
                g.edge(f"formulation:{o['fv_id']}", oid, label="recipe")
            if o["bom_id"]:
                g.node(f"bom:{o['bom_id']}", "bom", f"BOM v{o['bom_ver']}", "Bill of materials", href="/m/production/boms")
                g.edge(f"bom:{o['bom_id']}", oid, label="recipe")
        if orders:
            cons = rows(db, """SELECT pc.order_id, pc.batch_id, sum(pc.qty) AS qty FROM production_consumptions pc WHERE pc.order_id = ANY(:o) GROUP BY pc.order_id, pc.batch_id""", o=[o["id"] for o in orders])
            srcs = _batches_by_id(db, list({c["batch_id"] for c in cons}))                # one query for every consumed batch of this generation
            for c in cons:
                src = srcs[c["batch_id"]]
                if _batch_node(g, src) and g.nodes.get(f"order:{c['order_id']}"):
                    g.edge(f"batch:{c['batch_id']}", f"order:{c['order_id']}", c["qty"], src["uom"], None, "consumed")
                if c["batch_id"] not in seen:
                    seen.add(c["batch_id"])
                    nxt.append(c["batch_id"])
        _qc_nodes(db, g, frontier)
        frontier = nxt
    if frontier:
        g.truncated = True
    return g.out(f"batch:{batch_id}", "backward")


def _ship_ref(s: dict) -> str:
    """The reference people use for a shipment: the courier AWB when booked, otherwise its number. shipment_no is an integer column, so it is always converted to text."""
    return str(s["awb"]) if s.get("awb") else f"Shipment {s['shipment_no'] or s['shipment_id']}"


def _forward_collect(db: Session, batch_id: int, max_depth: int) -> dict:
    """Walk forward one generation at a time. Returns the raw rows; both the graph and the impact analysis are built from them."""
    seen, frontier, depth = {batch_id}, [batch_id], 0
    acc: dict[str, list] = {"batches": [], "consumptions": [], "orders": [], "shipments": [], "returns": [], "reservations": [], "balances": []}
    while frontier and depth < max_depth:
        depth += 1
        acc["batches"] += list(_batches_by_id(db, frontier).values())
        cons = rows(db, """SELECT pc.batch_id, pc.order_id, sum(pc.qty) AS qty FROM production_consumptions pc WHERE pc.batch_id = ANY(:ids) GROUP BY 1, 2""", ids=frontier)
        acc["consumptions"] += cons
        acc["shipments"] += rows(db, """SELECT sl.batch_id, sh.id AS shipment_id, sum(sl.qty) AS qty, sh.shipment_no, sh.awb, sh.status, sh.delivered_at, sh.created_at, sh.courier, sh.so_id, so.number AS so_no,
                                               so.channel, so.status AS so_status, so.customer_id, c.name AS customer_name
                                          FROM shipment_lines sl JOIN shipments sh ON sh.id = sl.shipment_id JOIN sales_orders so ON so.id = sh.so_id JOIN customers c ON c.id = so.customer_id
                                         WHERE sl.batch_id = ANY(:ids)
                                         GROUP BY sl.batch_id, sh.id, sh.shipment_no, sh.awb, sh.status, sh.delivered_at, sh.created_at, sh.courier, sh.so_id, so.number, so.channel, so.status, so.customer_id, c.name""", ids=frontier)
        acc["returns"] += rows(db, """SELECT sl.batch_id, sl.shipment_id, sr.id AS return_id, sr.number, sr.status, sr.decision, sum(rl.qty) AS qty, sr.created_at
                                        FROM sales_return_lines rl JOIN shipment_lines sl ON sl.id = rl.shipment_line_id JOIN sales_returns sr ON sr.id = rl.return_id
                                       WHERE sl.batch_id = ANY(:ids) GROUP BY sl.batch_id, sl.shipment_id, sr.id, sr.number, sr.status, sr.decision, sr.created_at""", ids=frontier)
        acc["reservations"] += rows(db, """SELECT sb.batch_id, r.id, r.qty, r.status, r.reference_type, r.reference_id, r.created_at FROM stock_reservations r JOIN stock_balances sb ON sb.id = r.balance_id
                                            WHERE sb.batch_id = ANY(:ids) AND r.status = 'ACTIVE'""", ids=frontier)
        acc["balances"] += rows(db, """SELECT sb.batch_id, w.id AS warehouse_id, w.code, w.name, sum(sb.qty_physical) AS physical, sum(sb.qty_reserved) AS reserved, sum(sb.qty_blocked) AS blocked
                                         FROM stock_balances sb JOIN warehouses w ON w.id = sb.warehouse_id WHERE sb.batch_id = ANY(:ids) AND sb.qty_physical > 0 GROUP BY sb.batch_id, w.id, w.code, w.name""", ids=frontier)
        nxt: list[int] = []
        if cons:
            orders = rows(db, """SELECT po.id, po.number, po.status, po.output_batch_id, po.actual_qty, po.planned_qty, po.completed_at, po.yield_pct FROM production_orders po WHERE po.id = ANY(:o)""", o=list({c["order_id"] for c in cons}))
            acc["orders"] += orders
            for o in orders:
                if o["output_batch_id"] and o["output_batch_id"] not in seen:
                    seen.add(o["output_batch_id"])
                    nxt.append(o["output_batch_id"])
        frontier = nxt
    acc["truncated"] = bool(frontier)
    return acc


def forward(db: Session, batch_id: int, mask: Mask, max_depth: int = MAX_DEPTH) -> dict:
    """Where this batch went: production orders and the batches they produced, then reservations, shipments, customers and returns."""
    root = batch_row(db, batch_id)
    acc = _forward_collect(db, batch_id, max_depth)
    g = Graph()
    for r in acc["batches"]:
        _batch_node(g, r)
    units = {r["id"]: r["uom"] for r in acc["batches"]}
    missing = [o["output_batch_id"] for o in acc["orders"] if o["output_batch_id"] and f"batch:{o['output_batch_id']}" not in g.nodes]
    for nb in _batches_by_id(db, missing).values():                                       # outputs of the last generation, fetched together
        _batch_node(g, nb)
        units[nb["id"]] = nb["uom"]
    for o in acc["orders"]:
        oid = f"order:{o['id']}"
        g.node(oid, "production_order", o["number"], "Production order", status=o["status"], date=o["completed_at"], href=f"/m/production/orders?open={o['id']}",
               detail={"planned": Q(o["planned_qty"]), "actual": Q(o["actual_qty"]), "yield_pct": S(o["yield_pct"])})
        if o["output_batch_id"]:
            g.edge(oid, f"batch:{o['output_batch_id']}", o["actual_qty"], units.get(o["output_batch_id"]), o["completed_at"], "produced")
    for c in acc["consumptions"]:
        g.edge(f"batch:{c['batch_id']}", f"order:{c['order_id']}", c["qty"], units.get(c["batch_id"]), None, "consumed")
    for s in acc["shipments"]:
        sid, so, cu = f"ship:{s['shipment_id']}", f"so:{s['so_id']}", f"cust:{s['customer_id']}"
        g.node(sid, "shipment", _ship_ref(s), s["courier"] or "Shipment", status=s["status"], date=s["delivered_at"] or s["created_at"],
               href=f"/m/dispatch/shipments?open={s['shipment_id']}", detail={"awb": s["awb"], "delivered_at": s["delivered_at"]})
        g.node(so, "sales_order", s["so_no"], f"{s['channel'].title()} order", status=s["so_status"], href=f"/m/dispatch/orders?open={s['so_id']}")
        g.node(cu, "customer", mask.cus(s["customer_name"]) or "", "Customer", href="/masters/customers" if mask.customer else None)
        g.edge(f"batch:{s['batch_id']}", sid, s["qty"], units.get(s["batch_id"]), s["created_at"], "dispatched")
        g.edge(sid, so, s["qty"], units.get(s["batch_id"]), None, "fulfils")
        g.edge(so, cu, s["qty"], units.get(s["batch_id"]), s["delivered_at"], "delivered to" if s["delivered_at"] else "for")
    for r in acc["returns"]:
        rid = f"ret:{r['return_id']}"
        g.node(rid, "return", r["number"], "Customer return", status=(r["decision"] or r["status"]), date=r["created_at"], href=f"/m/dispatch/returns?open={r['return_id']}")
        g.edge(f"ship:{r['shipment_id']}", rid, r["qty"], units.get(r["batch_id"]), r["created_at"], "returned")
    for r in acc["reservations"]:
        nid = f"res:{r['id']}"
        g.node(nid, "reservation", f"{r['reference_type'] or 'reservation'} {r['reference_id'] or ''}".strip(), "Stock reservation", status=r["status"], date=r["created_at"])
        g.edge(f"batch:{r['batch_id']}", nid, r["qty"], units.get(r["batch_id"]), r["created_at"], "reserved")
    for r in acc["balances"]:
        nid = f"wh:{r['warehouse_id']}"
        g.node(nid, "warehouse", r["code"], r["name"], href="/masters/warehouses")
        g.edge(f"batch:{r['batch_id']}", nid, r["physical"], units.get(r["batch_id"]), None, "stored")
    _qc_nodes(db, g, [r["id"] for r in acc["batches"]])
    if acc["truncated"]:
        g.truncated = True
    return g.out(f"batch:{batch_id}", "forward")


def graph(db: Session, batch_id: int, direction: str, mask: Mask, max_depth: int) -> dict:
    if direction == "backward":
        return backward(db, batch_id, mask, max_depth)
    if direction == "forward":
        return forward(db, batch_id, mask, max_depth)
    a, f = backward(db, batch_id, mask, max_depth), forward(db, batch_id, mask, max_depth)
    nodes = {n["id"]: n for n in a["nodes"] + f["nodes"]}
    edges = {(e["source"], e["target"], e["label"]): e for e in a["edges"] + f["edges"]}
    return {"focus": f"batch:{batch_id}", "direction": "both", "nodes": list(nodes.values()), "edges": list(edges.values()), "truncated": a["truncated"] or f["truncated"],
            "limits": a["limits"]}


# ================================================================================================================================ impact analysis
def impact(db: Session, batch_id: int, mask: Mask, max_depth: int = MAX_DEPTH) -> dict:
    """Everything downstream of one batch, with quantities by state. A finished batch that consumed any of the suspect batch is reported WHOLE (potentially affected):
    the system records which batches an order consumed, not how they mixed, so it never apportions."""
    root = batch_row(db, batch_id)
    acc = _forward_collect(db, batch_id, max_depth)
    ids = [r["id"] for r in acc["batches"]]
    info = {r["id"]: r for r in acc["batches"]}
    suspect_consumed: dict[int, Decimal] = {}
    for c in acc["consumptions"]:
        if c["batch_id"] == batch_id:
            suspect_consumed[c["order_id"]] = suspect_consumed.get(c["order_id"], Decimal(0)) + D(c["qty"])
    receipt = one(db, """SELECT g.number AS gr_number, g.received_at, po.number AS po_number, s.name AS supplier_name, b.supplier_lot_no, sum(gi.qty) AS qty
                           FROM inventory_batches b LEFT JOIN goods_receipt_items gi ON gi.batch_id = b.id LEFT JOIN goods_receipts g ON g.id = gi.gr_id LEFT JOIN purchase_orders po ON po.id = g.po_id
                           LEFT JOIN suppliers s ON s.id = g.supplier_id WHERE b.id = :b GROUP BY g.number, g.received_at, po.number, s.name, b.supplier_lot_no""", b=batch_id)
    if receipt:
        receipt["supplier_name"] = mask.sup(receipt["supplier_name"])
        receipt["qty"] = Q(receipt["qty"])
    produced_by = {o["output_batch_id"]: o for o in acc["orders"] if o["output_batch_id"]}
    led = {r["batch_id"]: r for r in rows(db, """SELECT batch_id, sum(CASE WHEN movement_type = 'PRODUCTION_OUTPUT' THEN qty_delta ELSE 0 END) AS produced, sum(CASE WHEN movement_type = 'RETURN' THEN qty_delta ELSE 0 END) AS returned
                                                   FROM stock_ledger WHERE batch_id = ANY(:ids) GROUP BY batch_id""", ids=ids)}
    bal = {r["batch_id"]: r for r in rows(db, """SELECT batch_id, sum(qty_physical) AS physical, sum(qty_reserved) AS reserved, sum(qty_blocked) AS blocked, sum(qty_available) AS available
                                                   FROM stock_balances WHERE batch_id = ANY(:ids) GROUP BY batch_id""", ids=ids)}
    shipped: dict[int, Decimal] = {}
    delivered: dict[int, Decimal] = {}
    cust: dict[tuple, dict] = {}
    for s in acc["shipments"]:
        shipped[s["batch_id"]] = shipped.get(s["batch_id"], Decimal(0)) + D(s["qty"])
        if s["delivered_at"]:
            delivered[s["batch_id"]] = delivered.get(s["batch_id"], Decimal(0)) + D(s["qty"])
        key = (s["customer_id"], s["so_id"], s["shipment_id"], s["batch_id"])
        cust[key] = {"customer_id": s["customer_id"], "customer": mask.cus(s["customer_name"]), "sales_order": s["so_no"], "channel": s["channel"], "shipment": _ship_ref(s),
                     "shipment_status": s["status"], "batch_no": info[s["batch_id"]]["batch_no"], "qty": Q(s["qty"]), "delivered_at": s["delivered_at"], "shipped_on": s["created_at"]}
    returned_by_batch: dict[int, Decimal] = {}
    for r in acc["returns"]:
        returned_by_batch[r["batch_id"]] = returned_by_batch.get(r["batch_id"], Decimal(0)) + D(r["qty"])
    affected = []
    totals = {k: Decimal(0) for k in ("produced", "available", "reserved", "qc_held", "dispatched", "delivered", "returned")}
    for bid, r in info.items():
        if bid == batch_id:
            continue
        b, l = bal.get(bid, {}), led.get(bid, {})
        row = {"id": bid, "batch_no": r["batch_no"], "item_code": r["item_code"], "item_name": r["item_name"], "kind": r["kind"], "unit": r["uom"], "qc_status": r["qc_status"],
               "production_order": produced_by[bid]["number"] if bid in produced_by else None,
               "produced": Q(l.get("produced")), "physical": Q(b.get("physical")), "available": Q(b.get("available")), "reserved": Q(b.get("reserved")), "qc_held": Q(b.get("blocked")),
               "dispatched": Q(shipped.get(bid)), "delivered": Q(delivered.get(bid)), "returned": Q(returned_by_batch.get(bid)), "href": f"/inventory/batches/{bid}/traceability"}
        affected.append(row)
        if r["kind"] == "FG":                                             # finished goods only: bulk batches are intermediate, counting them too would double count
            for k, v in (("produced", l.get("produced")), ("available", b.get("available")), ("reserved", b.get("reserved")), ("qc_held", b.get("blocked")),
                         ("dispatched", shipped.get(bid)), ("delivered", delivered.get(bid)), ("returned", returned_by_batch.get(bid))):
                totals[k] += D(v)
    own = bal.get(batch_id, {})
    orders_out = [{"id": o["id"], "number": o["number"], "status": o["status"], "consumed_suspect_qty": Q(suspect_consumed.get(o["id"])), "actual_qty": Q(o["actual_qty"]),
                   "output_batch_id": o["output_batch_id"], "output_batch_no": info[o["output_batch_id"]]["batch_no"] if o["output_batch_id"] in info else None, "href": f"/m/production/orders?open={o['id']}"}
                  for o in acc["orders"] if o["id"] in suspect_consumed or o["output_batch_id"] in info]
    summary = {"production_orders": len(orders_out), "finished_batches": sum(1 for a in affected if a["kind"] == "FG"), "batches_total": len(affected),
               "customers": len({c["customer_id"] for c in cust.values()}), "shipments": len({k[2] for k in cust}),
               "suspect_remaining": Q(own.get("physical")), "suspect_consumed": Q(sum(suspect_consumed.values(), Decimal(0))), "finished_goods_unit_note": "Finished quantities are in finished-goods units; the suspect batch is in its own unit"}
    return {
        "suspect": {"id": batch_id, "batch_no": root["batch_no"], "item_code": root["item_code"], "item_name": root["item_name"], "kind": root["kind"], "unit": root["uom"], "qc_status": root["qc_status"],
                    "supplier_lot_no": root["supplier_lot_no"], "physical": Q(own.get("physical")), "available": Q(own.get("available")), "reserved": Q(own.get("reserved")), "qc_held": Q(own.get("blocked"))},
        "receipt": receipt, "orders": orders_out, "affected_batches": affected,
        "customers": sorted(cust.values(), key=lambda c: (str(c["customer"]), str(c["sales_order"]))),
        "totals_finished_goods": {k: Q(v) for k, v in totals.items()}, "summary": summary, "truncated": acc["truncated"],
        "basis": "Derived from recorded consumption, production output, shipment and return rows. A finished batch that consumed any of the suspect batch is listed whole: the system does not record how batches mixed.",
    }


# ================================================================================================================================ lists and product 360
def batch_list(db: Session, q: str | None, item_id: int | None, kind: str | None, qc_status: str | None, page: int, page_size: int) -> dict:
    where, p = ["TRUE"], {"n": page_size, "o": (page - 1) * page_size}
    if q:
        where.append("(b.batch_no ILIKE :q OR i.item_code ILIKE :q OR i.name ILIKE :q OR b.supplier_lot_no ILIKE :q)")
        p["q"] = f"%{q.strip()}%"
    if item_id:
        where.append("b.item_id = :iid")
        p["iid"] = item_id
    if kind:
        where.append("i.kind = :kind")
        p["kind"] = kind
    if qc_status:
        where.append("b.qc_status = :qc")
        p["qc"] = qc_status
    w = " AND ".join(where)
    total = one(db, f"SELECT count(*) AS n FROM inventory_batches b JOIN items i ON i.id = b.item_id WHERE {w}", **{k: v for k, v in p.items() if k not in ("n", "o")})["n"]
    items = rows(db, f"""SELECT b.id, b.batch_no, i.item_code, i.name AS item_name, i.kind, u.code AS uom, b.qc_status, b.mfg_date, b.expiry_date, b.supplier_lot_no,
                                coalesce((SELECT sum(qty_physical) FROM stock_balances WHERE batch_id = b.id), 0) AS physical,
                                coalesce((SELECT sum(qty_available) FROM stock_balances WHERE batch_id = b.id), 0) AS available
                           FROM inventory_batches b JOIN items i ON i.id = b.item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id WHERE {w} ORDER BY b.id DESC LIMIT :n OFFSET :o""", **p)
    for r in items:
        r["physical"], r["available"] = Q(r["physical"]), Q(r["available"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def product_list(db: Session, q: str | None, kind: str | None, page: int, page_size: int) -> dict:
    where, p = ["i.is_active"], {"n": page_size, "o": (page - 1) * page_size}
    if q:
        where.append("(i.item_code ILIKE :q OR i.name ILIKE :q)")
        p["q"] = f"%{q.strip()}%"
    if kind:
        where.append("i.kind = :kind")
        p["kind"] = kind
    w = " AND ".join(where)
    total = one(db, f"SELECT count(*) AS n FROM items i WHERE {w}", **{k: v for k, v in p.items() if k not in ("n", "o")})["n"]
    items = rows(db, f"""SELECT i.id, i.item_code, i.name, i.kind, u.code AS uom,
                                (SELECT count(*) FROM inventory_batches WHERE item_id = i.id) AS batches,
                                coalesce((SELECT sum(qty_available) FROM stock_balances WHERE item_id = i.id), 0) AS available
                           FROM items i LEFT JOIN units_of_measure u ON u.id = i.uom_id WHERE {w} ORDER BY (i.kind = 'FG') DESC, i.item_code LIMIT :n OFFSET :o""", **p)
    for r in items:
        r["available"] = Q(r["available"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _item(db: Session, item_id: int) -> dict:
    r = one(db, """SELECT i.id, i.item_code, i.name, i.description, i.kind, i.hsn_code, i.gst_rate, i.shelf_life_days, i.storage_conditions, i.reorder_level, i.is_batch_tracked, i.is_active, u.code AS uom
                     FROM items i LEFT JOIN units_of_measure u ON u.id = i.uom_id WHERE i.id = :i""", i=item_id)
    if not r:
        raise NotFound("Product")
    return r


def product_summary(db: Session, item_id: int) -> dict:
    it = _item(db, item_id)
    stock = one(db, """SELECT coalesce(sum(qty_physical), 0) AS physical, coalesce(sum(qty_reserved), 0) AS reserved, coalesce(sum(qty_blocked), 0) AS blocked, coalesce(sum(qty_available), 0) AS available
                         FROM stock_balances WHERE item_id = :i""", i=item_id)
    bom = one(db, """SELECT bo.id, bo.version_no, bo.status, fv.id AS fv_id, fv.version_no AS fv_version, fv.status AS fv_status, f.code AS formulation_code, f.name AS formulation_name
                       FROM boms bo LEFT JOIN formulation_versions fv ON fv.id = bo.formulation_version_id LEFT JOIN formulations f ON f.id = fv.formulation_id
                      WHERE bo.output_item_id = :i AND bo.status = 'ACTIVE' ORDER BY bo.version_no DESC LIMIT 1""", i=item_id)
    prod = one(db, """SELECT count(*) AS orders, count(*) FILTER (WHERE status NOT IN ('COMPLETED', 'CANCELLED')) AS active,
                             coalesce(sum(actual_qty) FILTER (WHERE status = 'COMPLETED'), 0) AS made, avg(yield_pct) FILTER (WHERE status = 'COMPLETED') AS yield_avg,
                             avg(unit_cost) FILTER (WHERE status = 'COMPLETED' AND unit_cost > 0) AS cost_avg, count(*) FILTER (WHERE status = 'COMPLETED') AS completed
                        FROM production_orders WHERE output_item_id = :i""", i=item_id)
    sales = one(db, """SELECT coalesce(sum(sl.qty), 0) AS dispatched, count(DISTINCT sh.so_id) AS orders, count(DISTINCT so.customer_id) AS customers, max(sh.created_at) AS last_shipped
                         FROM shipment_lines sl JOIN shipments sh ON sh.id = sl.shipment_id JOIN sales_orders so ON so.id = sh.so_id WHERE sl.item_id = :i""", i=item_id)
    returned = one(db, """SELECT coalesce(sum(rl.qty), 0) AS q FROM sales_return_lines rl JOIN shipment_lines sl ON sl.id = rl.shipment_line_id WHERE sl.item_id = :i""", i=item_id)["q"]
    qc = one(db, """SELECT count(*) FILTER (WHERE n.decision = 'RELEASE') AS released, count(*) FILTER (WHERE n.decision = 'REJECT') AS rejected, count(*) AS total
                      FROM inspections n JOIN inventory_batches b ON b.id = n.batch_id WHERE b.item_id = :i AND n.stage = 'FINISHED'""", i=item_id)
    batches = one(db, "SELECT count(*) AS n, count(*) FILTER (WHERE qc_status <> 'RELEASED') AS not_released FROM inventory_batches WHERE item_id = :i", i=item_id)
    decided = (qc["released"] or 0) + (qc["rejected"] or 0)
    return {
        "product": {**it, "gst_rate": S(it["gst_rate"]), "reorder_level": S(it["reorder_level"]), "kind_label": KIND_LABEL.get(it["kind"], it["kind"])},
        "recipe": bom,
        "stock": {k: Q(v) for k, v in stock.items()},
        "metrics": {
            "manufactured_qty": Q(prod["made"]), "dispatched_qty": Q(sales["dispatched"]), "returned_qty": Q(returned), "qc_hold_qty": Q(stock["blocked"]),
            "production_orders": prod["orders"], "active_production_orders": prod["active"], "completed_orders": prod["completed"], "batches": batches["n"], "batches_not_released": batches["not_released"],
            "qc_rejection_rate_pct": None if decided == 0 else Q(Decimal(100) * (qc["rejected"] or 0) / decided), "finished_inspections_decided": decided,
            "avg_yield_pct": None if prod["yield_avg"] is None else Q(prod["yield_avg"]),
            "avg_unit_cost": None if prod["cost_avg"] is None else format(D(prod["cost_avg"]).quantize(Decimal("0.01")), "f"),
            "sales_orders": sales["orders"], "customers": sales["customers"], "last_shipped": sales["last_shipped"],
        },
        "definitions": {
            "manufactured_qty": "Actual quantity on COMPLETED production orders for this product",
            "dispatched_qty": "Quantity on shipment lines (left the building); returns are shown separately and not netted",
            "qc_hold_qty": "Physical stock that is blocked (quarantine, hold, rejected)",
            "qc_rejection_rate_pct": "Finished-goods inspections decided REJECT / decided (RELEASE + REJECT)",
            "avg_unit_cost": "Mean unit cost of completed production orders (input cost / output); only where a cost was posted",
            "avg_yield_pct": "Mean yield of completed orders; shown only when at least one order is completed",
        },
    }


def _page(db: Session, sql_total: str, sql_items: str, page: int, page_size: int, **p) -> dict:
    total = one(db, sql_total, **p)["n"]
    items = rows(db, sql_items + " LIMIT :n OFFSET :o", n=page_size, o=(page - 1) * page_size, **p)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def product_batches(db: Session, item_id: int, page: int, page_size: int) -> dict:
    _item(db, item_id)
    out = _page(db, "SELECT count(*) AS n FROM inventory_batches WHERE item_id = :i",
                """SELECT b.id, b.batch_no, b.qc_status, b.mfg_date, b.expiry_date, b.source_type,
                          coalesce((SELECT sum(qty_physical) FROM stock_balances WHERE batch_id = b.id), 0) AS physical,
                          coalesce((SELECT sum(qty_available) FROM stock_balances WHERE batch_id = b.id), 0) AS available,
                          coalesce((SELECT sum(qty_blocked) FROM stock_balances WHERE batch_id = b.id), 0) AS blocked,
                          coalesce((SELECT sum(qty) FROM shipment_lines WHERE batch_id = b.id), 0) AS dispatched
                     FROM inventory_batches b WHERE b.item_id = :i ORDER BY b.id DESC""", page, page_size, i=item_id)
    for r in out["items"]:
        for k in ("physical", "available", "blocked", "dispatched"):
            r[k] = Q(r[k])
    return out


def product_production(db: Session, item_id: int, page: int, page_size: int) -> dict:
    _item(db, item_id)
    out = _page(db, "SELECT count(*) AS n FROM production_orders WHERE output_item_id = :i",
                """SELECT po.id, po.number, po.status, po.planned_qty, po.actual_qty, po.yield_pct, po.unit_cost, po.input_cost, po.planned_date, po.completed_at, b.id AS batch_id, b.batch_no,
                          f.code AS formulation, fv.version_no AS formulation_version, bo.version_no AS bom_version,
                          (SELECT coalesce(sum(qty), 0) FROM wastage_records w WHERE w.order_id = po.id) AS wastage
                     FROM production_orders po LEFT JOIN inventory_batches b ON b.id = po.output_batch_id LEFT JOIN formulation_versions fv ON fv.id = po.formulation_version_id
                     LEFT JOIN formulations f ON f.id = fv.formulation_id LEFT JOIN boms bo ON bo.id = po.bom_id WHERE po.output_item_id = :i ORDER BY po.id DESC""", page, page_size, i=item_id)
    for r in out["items"]:
        for k in ("planned_qty", "actual_qty", "yield_pct", "wastage"):
            r[k] = Q(r[k]) if r[k] is not None else None
        for k in ("unit_cost", "input_cost"):
            r[k] = None if r[k] is None else format(D(r[k]).quantize(Decimal("0.01")), "f")
    return out


def product_materials(db: Session, item_id: int, mask: Mask) -> dict:
    """What was actually consumed to make this product: per material, the planned quantity, the consumed quantity, and the supplier lots it came from."""
    _item(db, item_id)
    mats = rows(db, """SELECT pm.item_id, i.item_code, i.name, i.kind, u.code AS uom, sum(pm.required_qty) FILTER (WHERE pm.issued_qty > 0) AS planned, sum(pm.issued_qty) AS issued, count(DISTINCT pm.order_id) FILTER (WHERE pm.issued_qty > 0) AS orders
                         FROM production_materials pm JOIN production_orders po ON po.id = pm.order_id JOIN items i ON i.id = pm.item_id LEFT JOIN units_of_measure u ON u.id = i.uom_id
                        WHERE po.output_item_id = :i GROUP BY pm.item_id, i.item_code, i.name, i.kind, u.code ORDER BY i.kind, i.item_code""", i=item_id)
    cons = rows(db, """SELECT pc.item_id, pc.batch_id, b.batch_no, b.supplier_lot_no, s.name AS supplier_name, b.supplier_id, sum(pc.qty) AS qty, count(DISTINCT pc.order_id) AS orders
                         FROM production_consumptions pc JOIN production_orders po ON po.id = pc.order_id JOIN inventory_batches b ON b.id = pc.batch_id LEFT JOIN suppliers s ON s.id = b.supplier_id
                        WHERE po.output_item_id = :i GROUP BY pc.item_id, pc.batch_id, b.batch_no, b.supplier_lot_no, s.name, b.supplier_id ORDER BY pc.item_id, b.batch_no""", i=item_id)
    by: dict[int, list[dict]] = {}
    for c in cons:
        by.setdefault(c["item_id"], []).append({"batch_id": c["batch_id"], "batch_no": c["batch_no"], "supplier_lot_no": c["supplier_lot_no"], "supplier": mask.sup(c["supplier_name"]),
                                                "qty": Q(c["qty"]), "orders": c["orders"]})
    out = []
    for m in mats:
        used = sum((D(x["qty"]) for x in by.get(m["item_id"], [])), Decimal(0))
        out.append({"item_id": m["item_id"], "item_code": m["item_code"], "name": m["name"], "kind": m["kind"], "uom": m["uom"], "planned": Q(m["planned"]), "issued": Q(m["issued"]),
                    "consumed": Q(used), "variance": Q(used - D(m["planned"])), "orders": m["orders"], "lots": by.get(m["item_id"], [])})
    return {"items": out, "note": "Planned is the order's required quantity for the lines that have been issued, so orders that have not started are not counted as a shortfall. Consumed and lots come only from recorded consumption, never from the BOM. Quantities are per material unit and are not added across materials."}


def product_formulations(db: Session, item_id: int) -> dict:
    _item(db, item_id)
    boms = rows(db, """SELECT bo.id, bo.version_no, bo.status, bo.output_qty, bo.created_at, f.id AS formulation_id, f.code AS formulation_code, fv.version_no AS formulation_version, fv.status AS formulation_status,
                              (SELECT count(*) FROM production_orders po WHERE po.bom_id = bo.id) AS orders
                         FROM boms bo LEFT JOIN formulation_versions fv ON fv.id = bo.formulation_version_id LEFT JOIN formulations f ON f.id = fv.formulation_id
                        WHERE bo.output_item_id = :i ORDER BY bo.version_no DESC, bo.id DESC""", i=item_id)
    forms = rows(db, """SELECT fv.id, f.code, f.name, fv.version_no, fv.status, fv.batch_size, fv.effective_date, fv.approved_at, fv.revision_note
                          FROM formulations f JOIN formulation_versions fv ON fv.formulation_id = f.id WHERE f.product_item_id = :i ORDER BY f.code, fv.version_no DESC""", i=item_id)
    for b in boms:
        b["output_qty"] = Q(b["output_qty"])
    for f in forms:
        f["batch_size"] = Q(f["batch_size"])
    return {"boms": boms, "formulation_versions": forms}


def product_quality(db: Session, item_id: int, page: int, page_size: int) -> dict:
    _item(db, item_id)
    out = _page(db, "SELECT count(*) AS n FROM inspections n JOIN inventory_batches b ON b.id = n.batch_id WHERE b.item_id = :i",
                """SELECT n.id, n.number, n.stage, n.status, n.decision, n.decision_reason, n.decided_at, n.created_at, b.id AS batch_id, b.batch_no, u.full_name AS decided_by,
                          (SELECT count(*) FROM inspection_results r WHERE r.inspection_id = n.id) AS results,
                          (SELECT count(*) FROM inspection_results r WHERE r.inspection_id = n.id AND r.passed = false) AS failed_results
                     FROM inspections n JOIN inventory_batches b ON b.id = n.batch_id LEFT JOIN users u ON u.id = n.decided_by WHERE b.item_id = :i ORDER BY n.id DESC""", page, page_size, i=item_id)
    return out


def product_sales(db: Session, item_id: int, mask: Mask, page: int, page_size: int) -> dict:
    _item(db, item_id)
    out = _page(db, "SELECT count(DISTINCT so.customer_id) AS n FROM shipment_lines sl JOIN shipments sh ON sh.id = sl.shipment_id JOIN sales_orders so ON so.id = sh.so_id WHERE sl.item_id = :i",
                """SELECT so.customer_id, c.name AS customer_name, count(DISTINCT so.id) AS orders, count(DISTINCT sh.id) AS shipments, sum(sl.qty) AS qty, max(sh.created_at) AS last_shipped,
                          sum(sl.qty) FILTER (WHERE sh.delivered_at IS NOT NULL) AS delivered, count(DISTINCT sl.batch_id) AS batches
                     FROM shipment_lines sl JOIN shipments sh ON sh.id = sl.shipment_id JOIN sales_orders so ON so.id = sh.so_id JOIN customers c ON c.id = so.customer_id
                    WHERE sl.item_id = :i GROUP BY so.customer_id, c.name ORDER BY sum(sl.qty) DESC""", page, page_size, i=item_id)
    for r in out["items"]:
        r["customer_name"] = mask.cus(r["customer_name"])
        r["qty"], r["delivered"] = Q(r["qty"]), Q(r["delivered"])
    return out


def product_history(db: Session, item_id: int, limit: int = 100) -> list[dict]:
    _item(db, item_id)
    return rows(db, """SELECT occurred_at, user_email, action, entity, entity_id, reason FROM audit_logs
                        WHERE (entity = 'item' AND entity_id = :i) OR (entity = 'inventory_batch' AND entity_id IN (SELECT id::text FROM inventory_batches WHERE item_id = :ii))
                        ORDER BY occurred_at DESC, id DESC LIMIT :n""", i=str(item_id), ii=item_id, n=limit)
