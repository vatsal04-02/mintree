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
        acct.post_journal(db, entry_date=date.today(), description=f"Stock adjustment: {reason[:120]}", source_type="stock_adjustment", source_id=row["id"], lines=lines, actor_id=actor_id)
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
