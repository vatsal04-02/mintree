"""Stock adjustments WITH their accounting: quantity (ledger + balance) and value (moving-average valuation + journal to 5200) in one transaction.

A loss is valued at the item's moving-average cost (Dr 5200 Inventory Adjustments / Cr inventory). A gain is valued at the same average (Dr inventory / Cr 5200);
with no cost history it enters at zero value and is flagged `unvalued`. Lock order: balance -> item valuation -> journal. Reversal is allowed for adjustments only: other
movements belong to documents (receipts, dispatches, returns) that must be reversed through their own workflow so valuation and the GL follow."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.core.errors import BusinessConflict, NotFound
from app.inventory import service as svc
from app.masters.models import Item
from app.core.clock import business_today


def _kind_account(db: Session, item_id: int) -> str:
    return acct.INVENTORY_ACCOUNT[db.scalar(select(Item.kind).where(Item.id == item_id))]


def post_adjustment(db: Session, *, balance_id: int, delta, reason: str, source_id: int, actor_id: int) -> dict:
    r = svc.adjust(db, balance_id=balance_id, delta=delta, reason=reason, source_id=source_id, actor_id=actor_id)
    row = db.execute(text("SELECT id, item_id FROM stock_ledger WHERE balance_id = :b AND source_type = 'adjustment' AND source_id = :s AND movement_type = 'ADJUSTMENT' ORDER BY id DESC LIMIT 1"),
                     {"b": balance_id, "s": source_id}).mappings().one()
    d = Decimal(str(delta))
    vrows = acct.lock_valuations(db, [row["item_id"]])
    inv_acct = _kind_account(db, row["item_id"])
    v = vrows[row["item_id"]]
    if d < 0:
        value = acct.val_consume(vrows, row["item_id"], -d)
        lines = [("5200", value, Decimal(0)), (inv_acct, Decimal(0), value)]
    else:
        value = acct.money(v.value / v.qty * d) if v.qty > 0 else Decimal("0.00")
        acct.val_receive(db, vrows, row["item_id"], d, value)
        lines = [(inv_acct, value, Decimal(0)), ("5200", Decimal(0), value)]
    if value:
        acct.post_journal(db, entry_date=business_today(), description=f"Stock adjustment: {reason[:120]}", source_type="stock_adjustment", source_id=row["id"], lines=lines, actor_id=actor_id)
    return r | {"value": str(value), "unvalued": d > 0 and value == 0}


def reverse_adjustment(db: Session, cu, *, ledger_id: int, reason: str) -> dict:
    row = db.execute(text("SELECT movement_type, item_id, qty_delta FROM stock_ledger WHERE id = :i"), {"i": ledger_id}).mappings().first()
    if not row:
        raise NotFound("Ledger entry")
    if row["movement_type"] != "ADJUSTMENT":
        raise BusinessConflict("document_reversal_required", "Only stock adjustments can be reversed here; reverse receipts, dispatches and returns through their own workflow so valuation and the ledger follow")
    out = svc.reverse(db, ledger_id=ledger_id, reason=reason, actor_id=cu.id)
    je = db.execute(text("SELECT id, total_debit FROM journal_entries WHERE source_type = 'stock_adjustment' AND source_id = :s AND reverses_entry_id IS NULL"), {"s": ledger_id}).mappings().first()
    if je:
        vrows = acct.lock_valuations(db, [row["item_id"]])
        d = Decimal(row["qty_delta"])
        v = vrows[row["item_id"]]
        if d < 0:                                             # the loss is undone: quantity and value come back
            acct.val_receive(db, vrows, row["item_id"], -d, je["total_debit"])
        else:                                                 # the gain is undone: remove the quantity and the value it carried
            take = min(d, v.qty)
            v.qty, v.value = v.qty - take, max(Decimal(0), v.value - je["total_debit"])
        acct.reverse_journal(db, cu, je["id"], reason)
    return out | {"valuation_reversed": bool(je)}


def post_opening_stock(db: Session, cu, *, warehouse_id: int, qc_status: str, note: str | None, lines: list[dict]) -> dict:
    """Loads existing stock. Per line: batch (for batch-tracked items), ledger + balance, valuation at the given unit cost; one journal Dr inventory / Cr 3000 Owner's Equity.
    RELEASED stock needs the QC-release permission (the same control as releasing a batch); QUARANTINE stock waits for the normal inspection."""
    from app.audit import service as audit
    from app.core.errors import Unprocessable
    from app.inventory.stocktake_models import OpeningStock
    from app.quality import service as quality
    if qc_status == "RELEASED" and not cu.can("inventory.batch:qc_release"):
        raise BusinessConflict("release_not_permitted", "Loading stock as already released needs the QC-release permission; load it as QUARANTINE for normal inspection")
    if len({l["item_id"] for l in lines}) != len(lines):
        raise Unprocessable("An item may appear once per posting")
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_([l["item_id"] for l in lines])))}
    n = db.execute(text("SELECT nextval('opening_stock_seq')")).scalar_one()
    doc = OpeningStock(number=f"OS/{business_today():%y}/{n:05d}", warehouse_id=warehouse_id, qc_status=qc_status, note=note, total_value=Decimal(0), posted_by=cu.id)
    db.add(doc)
    db.flush()
    out, valued = [], []
    for ln, l in enumerate(sorted(lines, key=lambda x: x["item_id"]), 1):
        it = items.get(l["item_id"])
        if not it or not it.is_active:
            raise Unprocessable(f"Item {l['item_id']} not found or inactive")
        qty, cost = Decimal(str(l["qty"])), Decimal(str(l["unit_cost"]))
        batch_id = None
        if it.is_batch_tracked:
            batch_id = svc.create_batch(db, item_id=it.id, supplier_id=None, supplier_lot_no=l.get("supplier_lot_no"), mfg_date=l.get("mfg_date"), expiry_date=l.get("expiry_date"),
                                        source_type="opening_stock", source_id=doc.id)
            if qc_status == "RELEASED":
                db.execute(text("UPDATE inventory_batches SET qc_status = 'RELEASED', qc_decided_at = now() WHERE id = :b"), {"b": batch_id})
            else:
                quality.auto_incoming(db, batch_id, it.id)
        bal = svc.receive(db, item_id=it.id, warehouse_id=warehouse_id, location_id=l.get("location_id"), batch_id=batch_id, qty=qty, source_type="opening_stock", source_id=doc.id,
                          source_line=ln, actor_id=cu.id, quarantine=it.is_batch_tracked and qc_status != "RELEASED", movement="RECEIPT")
        valued.append((it, qty, acct.money(qty * cost)))
        out.append({"line_no": ln, "item_id": it.id, "qty": str(qty), "batch_id": batch_id, "balance_id": bal["balance_id"]})
    vrows = acct.lock_valuations(db, [it.id for it, _, _ in valued])
    by_account: dict[str, Decimal] = {}
    for it, qty, value in valued:
        acct.val_receive(db, vrows, it.id, qty, value)
        code = acct.INVENTORY_ACCOUNT[it.kind]
        by_account[code] = by_account.get(code, Decimal(0)) + value
    total = sum(by_account.values(), Decimal(0))
    if total > 0:
        acct.post_journal(db, entry_date=business_today(), description=f"Opening stock {doc.number}", source_type="opening_stock", source_id=doc.id,
                          lines=[(c, v, Decimal(0)) for c, v in sorted(by_account.items())] + [("3000", Decimal(0), total)], actor_id=cu.id)
    doc.total_value = total
    audit.record(db, user=cu, action="post", entity="opening_stock", entity_id=doc.id, after={"number": doc.number, "lines": len(out), "value": str(total), "qc": qc_status})
    return {"id": doc.id, "number": doc.number, "total_value": str(total), "qc_status": qc_status, "lines": out}
