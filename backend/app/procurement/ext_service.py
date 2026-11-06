"""Purchase returns and PO amendments. Both run inside one caller-owned transaction (run_tx) and lock in a fixed order:
PO / receipt document -> its lines -> stock balances (id order) -> item valuation (item order)."""
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.approvals.service import effective_roles
from app.audit import service as audit
from app.core import outbox
from app.core.clock import business_today
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.inventory import repository as repo
from app.masters.models import Item
from app.notifications.service import notify
from app.procurement.ext_models import PoAmendment, PoAmendmentLine, PurchaseReturn, PurchaseReturnLine
from app.procurement.models import ApprovalPolicy, GoodsReceipt, GoodsReceiptItem, PurchaseOrder, PurchaseOrderItem
from app.procurement.service import MONEY, _seq

D = Decimal


def _q(v) -> Decimal:
    return D(str(v))


# ------------------------------------------------------------------ purchase returns
def post_purchase_return(db: Session, cu: CurrentUser, *, goods_receipt_id: int, reason_type: str, reason: str, lines: list[dict]) -> dict:
    """Send received goods back to the supplier. Atomically: stock out (blocked first, then available; never reserved stock) ->
    PO line received qty down (a replacement can be received against the same line) -> reverse the receipt accrual (Dr GRNI / Cr Inventory
    at PO price) -> valuation -> audit -> outbox. Only quantity NOT yet invoiced can be returned here: invoiced goods need a supplier debit note."""
    if not lines or len({l["gr_item_id"] for l in lines}) != len(lines):
        raise Unprocessable("A receipt line may appear once per return")
    gr = db.scalar(select(GoodsReceipt).where(GoodsReceipt.id == goods_receipt_id).with_for_update())
    if not gr:
        raise NotFound("Goods receipt")
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == gr.po_id).with_for_update())
    gris = {g.id: g for g in db.scalars(select(GoodsReceiptItem).where(GoodsReceiptItem.gr_id == gr.id).order_by(GoodsReceiptItem.id).with_for_update())}
    po_items = {i.id: i for i in db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.po_id == po.id).order_by(PurchaseOrderItem.id).with_for_update())}
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_({g.item_id for g in gris.values()})))}
    already = {r[0]: r[1] for r in db.execute(text("SELECT gr_item_id, COALESCE(sum(qty),0) FROM purchase_return_lines WHERE gr_item_id = ANY(:i) GROUP BY gr_item_id"),
                                              {"i": list(gris)})}

    plan = []
    for l in sorted(lines, key=lambda x: x["gr_item_id"]):
        g = gris.get(l["gr_item_id"])
        if not g:
            raise Unprocessable(f"Receipt line {l['gr_item_id']} does not belong to this goods receipt")
        qty = _q(l["qty"])
        if qty <= 0:
            raise Unprocessable("Return quantity must be positive")
        returnable = g.qty - already.get(g.id, D(0))
        if qty > returnable:
            raise BusinessConflict("exceeds_received", "Cannot return more than was received on this line", received=str(g.qty), already_returned=str(already.get(g.id, 0)), requested=str(qty))
        pi = po_items[g.po_item_id]
        if pi.qty_received - qty < pi.qty_invoiced:
            raise BusinessConflict("already_invoiced", "This quantity is covered by a supplier invoice; raise a supplier debit note instead",
                                   po_item_id=pi.id, received=str(pi.qty_received), invoiced=str(pi.qty_invoiced))
        plan.append((g, pi, qty))

    ret = PurchaseReturn(number=f"PRT/{business_today():%y}/{_seq(db, 'purchase_return_seq'):06d}", goods_receipt_id=gr.id, po_id=po.id, supplier_id=gr.supplier_id,
                         warehouse_id=gr.warehouse_id, reason_type=reason_type, reason=reason, total_value=D(0), posted_by=cu.id)
    db.add(ret)
    db.flush()

    bal_ids = []
    for g, _, _ in plan:
        bal_ids += [r[0] for r in db.execute(text("SELECT id FROM stock_balances WHERE item_id=:i AND warehouse_id=:w AND batch_id IS NOT DISTINCT FROM :b"),
                                             {"i": g.item_id, "w": gr.warehouse_id, "b": g.batch_id})]
    bals = repo.lock_balances(db, bal_ids)                                    # one ordered lock sweep
    valued, out = [], []
    for n, (g, pi, qty) in enumerate(plan, 1):
        left, leg = qty, 0
        for b in sorted((b for b in bals.values() if b["item_id"] == g.item_id and b["batch_id"] == g.batch_id), key=lambda x: x["id"]):
            free = b["qty_physical"] - b["qty_reserved"]                       # reserved stock is committed to someone else
            take = min(free, left)
            if take <= 0:
                continue
            from_blocked = min(b["qty_blocked"], take)
            repo.add_ledger(db, balance=b, qty_delta=-take, blocked_delta=-from_blocked, movement_type="ISSUE", source_type="purchase_return",
                            source_id=ret.id, source_line=n, leg=leg, actor_id=cu.id, reason=reason)
            repo.apply_delta(db, b["id"], physical=-take, blocked=-from_blocked)
            left, leg = left - take, leg + 1
            if left <= 0:
                break
        if left > 0:
            raise BusinessConflict("stock_not_on_hand", "The goods are no longer in stock (consumed, reserved or moved); they cannot be returned from here",
                                   item_id=g.item_id, short_by=str(left))
        value = acct.money(qty * pi.unit_price)
        pi.qty_received = pi.qty_received - qty
        db.add(PurchaseReturnLine(return_id=ret.id, line_no=n, gr_item_id=g.id, item_id=g.item_id, batch_id=g.batch_id, qty=qty, unit_price=pi.unit_price, value=value))
        valued.append((items[g.item_id], qty, value))
        out.append({"line_no": n, "gr_item_id": g.id, "item_id": g.item_id, "batch_id": g.batch_id, "qty": str(qty), "value": str(value)})

    vrows = acct.lock_valuations(db, [it.id for it, _, _ in valued])
    by_account: dict[str, Decimal] = {}
    for it, qty, value in valued:
        acct.val_receive(db, vrows, it.id, -qty, -value)
        code = acct.INVENTORY_ACCOUNT[it.kind]
        by_account[code] = by_account.get(code, D(0)) + value
    total = sum(by_account.values(), D(0))
    je = acct.post_journal(db, entry_date=business_today(), description=f"Purchase return {ret.number} of {gr.number}", source_type="purchase_return", source_id=ret.id,
                           lines=[("2100", total, D(0))] + [(c, D(0), v) for c, v in sorted(by_account.items())], actor_id=cu.id)
    ret.total_value, ret.journal_entry_id = total, je.id
    po.status = "RECEIVED" if all(i.qty_received >= i.qty_ordered for i in po_items.values()) else ("PARTIALLY_RECEIVED" if any(i.qty_received > 0 for i in po_items.values()) else "APPROVED")
    po.version += 1
    body = {"id": ret.id, "number": ret.number, "goods_receipt_id": gr.id, "po_id": po.id, "po_status": po.status, "total_value": str(total), "lines": out}
    audit.record(db, user=cu, action="post", entity="purchase_return", entity_id=ret.id, after={"number": ret.number, "total": str(total), "reason_type": reason_type}, reason=reason)
    outbox.add(db, event_type="procurement.purchase_return.posted", aggregate_type="purchase_return", aggregate_id=ret.id, payload=body)
    return body


def return_dict(r: PurchaseReturn) -> dict:
    return {"id": r.id, "number": r.number, "goods_receipt_id": r.goods_receipt_id, "po_id": r.po_id, "supplier_id": r.supplier_id, "reason_type": r.reason_type,
            "reason": r.reason, "total_value": str(r.total_value), "posted_at": r.posted_at,
            "lines": [{"line_no": l.line_no, "gr_item_id": l.gr_item_id, "item_id": l.item_id, "batch_id": l.batch_id, "qty": str(l.qty), "value": str(l.value)} for l in r.lines]}


# ------------------------------------------------------------------ PO amendments
def _po_total(items: list[tuple[Decimal, Decimal, Decimal]]) -> Decimal:
    return sum((q * p * (1 + t / 100) for q, p, t in items), D(0)).quantize(MONEY)


def request_amendment(db: Session, cu: CurrentUser, *, po_id: int, reason: str, lines: list[dict]) -> dict:
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update())
    if not po:
        raise NotFound("Purchase order")
    if po.status not in ("APPROVED", "PARTIALLY_RECEIVED"):
        raise BusinessConflict("po_not_amendable", f"Only approved or partially received orders can be amended (status {po.status}); edit drafts directly")
    if db.scalar(select(PoAmendment.id).where(PoAmendment.po_id == po.id, PoAmendment.status == "PENDING")):
        raise BusinessConflict("amendment_pending", "This purchase order already has an amendment awaiting a decision")
    pis = {i.id: i for i in db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.po_id == po.id).order_by(PurchaseOrderItem.id))}
    if not lines or len({l["po_item_id"] for l in lines}) != len(lines):
        raise Unprocessable("List each purchase-order line at most once")
    new = {i.id: (i.qty_ordered, i.unit_price) for i in pis.values()}
    rows = []
    for l in lines:
        pi = pis.get(l["po_item_id"])
        if not pi:
            raise Unprocessable(f"Line {l['po_item_id']} does not belong to this purchase order")
        nq = _q(l["new_qty"]) if l.get("new_qty") is not None else pi.qty_ordered
        npr = _q(l["new_unit_price"]) if l.get("new_unit_price") is not None else pi.unit_price
        if nq < pi.qty_received:
            raise BusinessConflict("below_received", "The new quantity cannot be below what has already been received", po_item_id=pi.id, received=str(pi.qty_received))
        if npr != pi.unit_price and pi.qty_invoiced > 0:
            raise BusinessConflict("price_locked_by_invoice", "The price of an invoiced line cannot be amended; use a supplier debit note", po_item_id=pi.id)
        if nq == pi.qty_ordered and npr == pi.unit_price:
            raise Unprocessable(f"Line {pi.id} is unchanged")
        new[pi.id] = (nq, npr)
        rows.append(PoAmendmentLine(po_item_id=pi.id, old_qty=pi.qty_ordered, new_qty=nq, old_unit_price=pi.unit_price, new_unit_price=npr))
    after = _po_total([(new[i.id][0], new[i.id][1], i.tax_rate) for i in pis.values()])
    n = (db.scalar(text("SELECT COALESCE(max(amendment_no),0) FROM po_amendments WHERE po_id=:p"), {"p": po.id}) or 0) + 1
    am = PoAmendment(po_id=po.id, amendment_no=n, reason=reason, po_version_at_request=po.version, total_before=po.total_amount, total_after=after, requested_by=cu.id)
    am.lines = rows
    db.add(am)
    db.flush()
    audit.record(db, user=cu, action="amendment_request", entity="purchase_order", entity_id=po.id, after={"amendment_no": n, "total_before": str(po.total_amount), "total_after": str(after)}, reason=reason)
    notify(db, kind="po.amendment_requested", title=f"{po.number} amendment #{n} awaiting approval", link=f"/procurement/purchase-orders/{po.id}",
           permission="procurement.po:approve", exclude_user_id=cu.id)
    return amendment_dict(am)


def decide_amendment(db: Session, cu: CurrentUser, *, amendment_id: int, approve: bool, reason: str | None) -> dict:
    am = db.scalar(select(PoAmendment).where(PoAmendment.id == amendment_id).with_for_update())
    if not am:
        raise NotFound("Amendment")
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == am.po_id).with_for_update())
    if am.status != "PENDING":
        raise BusinessConflict("already_decided", f"Amendment is already {am.status}")
    if am.requested_by == cu.id:
        raise BusinessConflict("self_approval_not_allowed", "You cannot decide an amendment you requested")
    if not approve and not reason:
        raise Unprocessable("A rejection reason is required")
    if approve:
        roles, _ = effective_roles(db, cu, "purchase_order")
        pols = db.scalars(select(ApprovalPolicy).where(ApprovalPolicy.doc_type == "purchase_order", ApprovalPolicy.role_code.in_(roles or {""}))).all()
        if not [p for p in pols if p.max_amount is None or p.max_amount >= am.total_after]:
            raise BusinessConflict("approval_limit_exceeded", "The amended total exceeds your approval limit", amount=str(am.total_after))
        if po.version != am.po_version_at_request and po.status not in ("APPROVED", "PARTIALLY_RECEIVED"):
            raise BusinessConflict("po_changed", "The purchase order is no longer amendable")
        pis = {i.id: i for i in db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.po_id == po.id).order_by(PurchaseOrderItem.id).with_for_update())}
        for l in am.lines:                                                         # re-validate against what happened since the request
            pi = pis[l.po_item_id]
            if l.new_qty < pi.qty_received:
                raise BusinessConflict("below_received", "Goods were received since the request; the new quantity is now below the received quantity", po_item_id=pi.id)
            if l.new_unit_price != pi.unit_price and pi.qty_invoiced > 0:
                raise BusinessConflict("price_locked_by_invoice", "A line was invoiced since the request", po_item_id=pi.id)
        for l in am.lines:
            pi = pis[l.po_item_id]
            pi.qty_ordered, pi.unit_price = l.new_qty, l.new_unit_price
        db.flush()
        po.total_amount = _po_total([(i.qty_ordered, i.unit_price, i.tax_rate) for i in pis.values()])
        po.status = "RECEIVED" if all(i.qty_received >= i.qty_ordered for i in pis.values()) else ("PARTIALLY_RECEIVED" if any(i.qty_received > 0 for i in pis.values()) else "APPROVED")
        po.version += 1
    am.status, am.decided_by, am.decided_at, am.decision_reason = ("APPROVED" if approve else "REJECTED"), cu.id, text("now()"), reason
    db.flush()
    db.refresh(am)
    audit.record(db, user=cu, action="amendment_approve" if approve else "amendment_reject", entity="purchase_order", entity_id=po.id,
                 after={"amendment_no": am.amendment_no, "total_after": str(am.total_after)} if approve else {"amendment_no": am.amendment_no}, reason=reason)
    notify(db, kind="po.amendment_decided", title=f"{po.number} amendment #{am.amendment_no} {am.status.lower()}", link=f"/procurement/purchase-orders/{po.id}", user_ids=[am.requested_by], exclude_user_id=cu.id)
    if approve:
        outbox.add(db, event_type="procurement.purchase_order.amended", aggregate_type="purchase_order", aggregate_id=po.id,
                   payload={"po_id": po.id, "amendment_no": am.amendment_no, "total": str(po.total_amount)})
    return amendment_dict(am)


def amendment_dict(a: PoAmendment) -> dict:
    return {"id": a.id, "po_id": a.po_id, "amendment_no": a.amendment_no, "status": a.status, "reason": a.reason, "total_before": str(a.total_before),
            "total_after": str(a.total_after), "requested_by": a.requested_by, "decided_by": a.decided_by, "decision_reason": a.decision_reason,
            "lines": [{"po_item_id": l.po_item_id, "old_qty": str(l.old_qty), "new_qty": str(l.new_qty), "old_unit_price": str(l.old_unit_price), "new_unit_price": str(l.new_unit_price)} for l in a.lines]}
