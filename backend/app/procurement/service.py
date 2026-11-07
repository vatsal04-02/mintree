"""Procurement domain service: purchase requests, purchase orders (approval, optimistic versions), goods receipts.

Receipts post stock by calling inventory.service in the SAME transaction; business rules live here, not in routers.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from psycopg import errors as pgerr

from app.accounts import service as acct
from app.approvals.models import ApprovalRecord
from app.approvals.service import current_cycle, effective_roles, get_setting, history as approval_history
from app.audit import service as audit
from app.notifications.service import notify
from app.core import outbox
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.inventory import service as inv
from app.quality import service as quality
from app.masters.models import Item, StorageLocation, Supplier, Warehouse
from app.procurement.models import (ApprovalPolicy, GoodsReceipt, GoodsReceiptItem, PurchaseOrder, PurchaseOrderItem,
                                    PurchaseRequest, PurchaseRequestItem)
from app.core.clock import business_today

MONEY = Decimal("0.01")


def _seq(db: Session, name: str) -> int:
    return db.execute(text(f"SELECT nextval('{name}')")).scalar_one()


def _version_conflict() -> BusinessConflict:
    return BusinessConflict("version_conflict", "The document was changed by someone else. Reload and try again.")


# ------------------------------------------------------------------ purchase requests
def create_pr(db: Session, cu: CurrentUser, *, items: list[dict], required_by: date | None, notes: str | None,
              department_id: int | None) -> dict:
    ids = {i["item_id"] for i in items}
    found = {r.id: r for r in db.scalars(select(Item).where(Item.id.in_(ids)))}
    for i in items:
        it = found.get(i["item_id"])
        if not it or not it.is_active or it.kind not in ("RM", "PM"):
            raise Unprocessable(f"Item {i['item_id']} is not an active raw/packaging material")
    pr = PurchaseRequest(number=f"PR/{business_today():%y}/{_seq(db, 'pr_number_seq'):06d}", requested_by=cu.id,
                         department_id=department_id, required_by=required_by, notes=notes)
    pr.items = [PurchaseRequestItem(line_no=n, item_id=i["item_id"], qty=i["qty"]) for n, i in enumerate(items, 1)]
    db.add(pr)
    db.flush()
    audit.record(db, user=cu, action="create", entity="purchase_request", entity_id=pr.id, after={"number": pr.number})
    return pr_dict(pr)


def pr_transition(db: Session, cu: CurrentUser, pr_id: int, action: str, expected_version: int) -> dict:
    rules = {"submit": ("DRAFT", "SUBMITTED"), "approve": ("SUBMITTED", "APPROVED"), "reject": ("SUBMITTED", "REJECTED")}
    frm, to = rules[action]
    pr = db.scalar(select(PurchaseRequest).where(PurchaseRequest.id == pr_id).with_for_update())
    if not pr:
        raise NotFound("Purchase request")
    if pr.version != expected_version:
        raise _version_conflict()
    if pr.status != frm:
        raise BusinessConflict("invalid_status", f"Cannot {action} a request in status {pr.status}")
    if action in ("approve", "reject") and pr.requested_by == cu.id:
        raise BusinessConflict("self_approval_not_allowed", "You cannot approve or reject your own request")
    pr.status, pr.version = to, pr.version + 1
    audit.record(db, user=cu, action=action, entity="purchase_request", entity_id=pr.id, before={"status": frm}, after={"status": to})
    return pr_dict(pr)


def pr_dict(pr: PurchaseRequest) -> dict:
    return {"id": pr.id, "number": pr.number, "status": pr.status, "version": pr.version, "required_by": pr.required_by,
            "notes": pr.notes, "items": [{"line_no": i.line_no, "item_id": i.item_id, "qty": str(i.qty)} for i in pr.items]}


# ------------------------------------------------------------------ purchase orders
def create_po(db: Session, cu: CurrentUser, *, supplier_id: int, items: list[dict], expected_date: date | None,
              notes: str | None, pr_id: int | None) -> dict:
    sup = db.get(Supplier, supplier_id)
    if not sup or not sup.is_active or sup.approval_status != "APPROVED":
        raise BusinessConflict("supplier_not_approved", "Purchase orders can only be raised on approved, active suppliers")
    found = {r.id: r for r in db.scalars(select(Item).where(Item.id.in_({i["item_id"] for i in items})))}
    total = Decimal(0)
    lines = []
    for n, i in enumerate(items, 1):
        it = found.get(i["item_id"])
        if not it or not it.is_active or it.kind not in ("RM", "PM"):
            raise Unprocessable(f"Item {i['item_id']} is not an active raw/packaging material")
        tax = Decimal(str(i.get("tax_rate") if i.get("tax_rate") is not None else (it.gst_rate or 0)))
        total += (Decimal(str(i["qty"])) * Decimal(str(i["unit_price"])) * (1 + tax / 100))
        lines.append(PurchaseOrderItem(line_no=n, item_id=i["item_id"], qty_ordered=i["qty"], unit_price=i["unit_price"], tax_rate=tax))
    po = PurchaseOrder(number=f"PO/{business_today():%y}/{_seq(db, 'po_number_seq'):06d}", supplier_id=supplier_id, pr_id=pr_id,
                       expected_date=expected_date, notes=notes, created_by=cu.id,
                       total_amount=total.quantize(MONEY, rounding=ROUND_HALF_UP))
    po.items = lines
    db.add(po)
    db.flush()
    audit.record(db, user=cu, action="create", entity="purchase_order", entity_id=po.id,
                 after={"number": po.number, "supplier_id": supplier_id, "total": str(po.total_amount)})
    return po_dict(po)


def _check_approval_authority(db: Session, cu: CurrentUser, po: PurchaseOrder) -> tuple[int | None, bool]:
    """-> (delegator id if the authority is borrowed, True). Uses the user's own roles plus any active delegation for purchase orders."""
    roles, via = effective_roles(db, cu, "purchase_order")
    pols = db.scalars(select(ApprovalPolicy).where(ApprovalPolicy.doc_type == "purchase_order", ApprovalPolicy.role_code.in_(roles or {""}))).all()
    ok = [p for p in pols if p.max_amount is None or p.max_amount >= po.total_amount]
    if not ok:
        raise BusinessConflict("approval_limit_exceeded", "This purchase order exceeds your approval limit", amount=str(po.total_amount))
    own = [p for p in ok if p.role_code in cu.role_codes]
    acting_for = None if own else via.get(ok[0].role_code)
    creators = {cu.id} | ({acting_for} if acting_for else set())
    if po.created_by in creators and not any(p.allow_self_approval for p in ok):
        raise BusinessConflict("self_approval_not_allowed", "You cannot approve a purchase order you created")
    return acting_for, True


def required_levels(db: Session, po: PurchaseOrder) -> int:
    threshold = get_setting(db, "procurement.two_level_threshold")
    return 2 if threshold is not None and po.total_amount >= Decimal(str(threshold)) else 1


def po_transition(db: Session, cu: CurrentUser, po_id: int, action: str, expected_version: int, reason: str | None = None) -> dict:
    rules = {"submit": ({"DRAFT"}, "PENDING_APPROVAL"), "approve": ({"PENDING_APPROVAL"}, "APPROVED"),
             "reject": ({"PENDING_APPROVAL"}, "DRAFT"), "cancel": ({"DRAFT", "PENDING_APPROVAL", "APPROVED"}, "CANCELLED")}
    frm, to = rules[action]
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update())  # documents lock first
    if not po:
        raise NotFound("Purchase order")
    if po.version != expected_version:
        raise _version_conflict()
    if po.status not in frm:
        raise BusinessConflict("invalid_status", f"Cannot {action} a purchase order in status {po.status}")
    acting_for = None
    if action in ("approve", "reject"):
        acting_for, _ = _check_approval_authority(db, cu, po)
    if action == "approve":
        cycle = current_cycle(db, "purchase_order", po.id)
        prior = [r for r in approval_history(db, "purchase_order", po.id, cycle) if r.decision == "APPROVED"]
        if any(r.user_id == cu.id for r in prior):
            raise BusinessConflict("already_approved_by_you", "A second approval must come from a different person")
        level = len(prior) + 1
        db.add(ApprovalRecord(doc_type="purchase_order", doc_id=po.id, cycle=cycle, level=level, user_id=cu.id, acting_for_user_id=acting_for, decision="APPROVED"))
        if level < required_levels(db, po):
            po.version += 1                                   # still pending: first of two approvals recorded
            audit.record(db, user=cu, action="approve_level_1", entity="purchase_order", entity_id=po.id, after={"level": level, "of": required_levels(db, po)})
            notify(db, kind="po.second_approval", title=f"{po.number} needs a second approval", link=f"/procurement/purchase-orders/{po.id}",
                   permission="procurement.po:approve", exclude_user_id=cu.id)
            return {**po_dict(po), "approval_level": level, "approvals_required": required_levels(db, po)}
        po.approved_by, po.approved_at = cu.id, text("now()")
    if action == "reject":
        if not reason:
            raise Unprocessable("A rejection reason is required")
        po.rejection_reason = reason
        db.add(ApprovalRecord(doc_type="purchase_order", doc_id=po.id, cycle=current_cycle(db, "purchase_order", po.id), level=1, user_id=cu.id,
                              acting_for_user_id=acting_for, decision="REJECTED", reason=reason))
    if action == "cancel" and any(i.qty_received > 0 for i in po.items):
        raise BusinessConflict("po_has_receipts", "A purchase order with receipts cannot be cancelled")
    before = po.status
    po.status, po.version = to, po.version + 1
    audit.record(db, user=cu, action=action, entity="purchase_order", entity_id=po.id, before={"status": before}, after={"status": to},
                 reason=reason)
    if action == "submit":
        notify(db, kind="po.submitted", title=f"{po.number} awaiting approval", link=f"/procurement/purchase-orders/{po.id}", permission="procurement.po:approve", exclude_user_id=cu.id)
    if action in ("approve", "reject"):
        notify(db, kind=f"po.{action}d", title=f"{po.number} {action}d", body=reason, link=f"/procurement/purchase-orders/{po.id}", user_ids=[po.created_by], exclude_user_id=cu.id)
    if action == "approve":
        outbox.add(db, event_type="procurement.purchase_order.approved", aggregate_type="purchase_order", aggregate_id=po.id,
                   payload={"po_id": po.id, "number": po.number, "total": str(po.total_amount), "approved_by": cu.id})
    return po_dict(po)


def po_dict(po: PurchaseOrder) -> dict:
    return {"id": po.id, "number": po.number, "status": po.status, "version": po.version, "supplier_id": po.supplier_id,
            "total_amount": str(po.total_amount), "expected_date": po.expected_date,
            "items": [{"id": i.id, "line_no": i.line_no, "item_id": i.item_id, "qty_ordered": str(i.qty_ordered),
                       "qty_received": str(i.qty_received), "qty_invoiced": str(i.qty_invoiced), "unit_price": str(i.unit_price), "tax_rate": str(i.tax_rate)} for i in po.items]}


# ------------------------------------------------------------------ goods receipt
def post_goods_receipt(db: Session, cu: CurrentUser, *, po_id: int, warehouse_id: int, delivery_note_no: str, lines: list[dict]) -> dict:
    """Atomically: validate -> lock -> goods receipt document -> batches -> ledger + balances -> PO progress -> audit -> outbox."""
    if len({l["po_item_id"] for l in lines}) != len(lines):
        raise Unprocessable("A purchase-order line may appear only once per receipt")
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update())      # lock 1: the PO
    if not po:
        raise NotFound("Purchase order")
    if po.status not in ("APPROVED", "PARTIALLY_RECEIVED"):
        raise BusinessConflict("po_not_receivable", f"Goods cannot be received against a purchase order in status {po.status}")
    wh = db.get(Warehouse, warehouse_id)
    if not wh or not wh.is_active:
        raise Unprocessable("Warehouse not found or inactive")
    po_items = {i.id: i for i in db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.po_id == po_id)
                                            .order_by(PurchaseOrderItem.id).with_for_update())}   # lock 2: its lines, id order
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_({po_items[l["po_item_id"]].item_id for l in lines if l["po_item_id"] in po_items})))}
    for l in lines:
        pi = po_items.get(l["po_item_id"])
        if not pi:
            raise Unprocessable(f"Line {l['po_item_id']} does not belong to this purchase order")
        qty = Decimal(str(l["qty"]))
        if pi.qty_received + qty > pi.qty_ordered:
            raise BusinessConflict("over_receipt", "Received quantity would exceed the ordered quantity",
                                   po_item_id=pi.id, ordered=str(pi.qty_ordered), already_received=str(pi.qty_received))
        if l.get("location_id"):
            loc = db.get(StorageLocation, l["location_id"])
            if not loc or loc.warehouse_id != warehouse_id:
                raise Unprocessable("Storage location does not belong to the receiving warehouse")
        it = items[pi.item_id]
        if it.shelf_life_days and it.is_batch_tracked and not l.get("expiry_date"):
            raise Unprocessable(f"Item {it.item_code} has a shelf life: an expiry date is required")

    gr = GoodsReceipt(number=f"GRN/{business_today():%y}/{_seq(db, 'gr_number_seq'):06d}", po_id=po_id, supplier_id=po.supplier_id,
                      warehouse_id=warehouse_id, delivery_note_no=delivery_note_no, posted_by=cu.id)
    db.add(gr)
    try:
        db.flush()
    except IntegrityError as e:
        if isinstance(e.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_delivery_note", "This supplier delivery note has already been received") from e
        raise

    out, valued = [], []
    for n, l in enumerate(sorted(lines, key=lambda x: x["po_item_id"]), 1):
        pi, qty = po_items[l["po_item_id"]], Decimal(str(l["qty"]))
        it = items[pi.item_id]
        batch_id = None
        if it.is_batch_tracked:
            batch_id = inv.create_batch(db, item_id=it.id, supplier_id=po.supplier_id, supplier_lot_no=l.get("supplier_lot_no"),
                                        mfg_date=l.get("mfg_date"), expiry_date=l.get("expiry_date"), source_type="goods_receipt",
                                        source_id=gr.id)
        bal = inv.receive(db, item_id=it.id, warehouse_id=warehouse_id, location_id=l.get("location_id"), batch_id=batch_id, qty=qty,
                          source_type="goods_receipt", source_id=gr.id, source_line=n, actor_id=cu.id, quarantine=it.is_batch_tracked)
        if batch_id:
            quality.auto_incoming(db, batch_id, it.id)       # INCOMING inspection when a specification exists (after the receipt, so the lot size is known)
        db.add(GoodsReceiptItem(gr_id=gr.id, line_no=n, po_item_id=pi.id, item_id=it.id, qty=qty, batch_id=batch_id,
                                location_id=l.get("location_id")))
        # `pi` was loaded under FOR UPDATE, so this read-modify-write is race-free; the CHECK constraint
        # (qty_received <= qty_ordered) is the final guard. (Do NOT also issue an UPDATE expression: that double-counted.)
        pi.qty_received = pi.qty_received + qty
        valued.append((it, qty, acct.money(qty * pi.unit_price)))
        out.append({"line_no": n, "po_item_id": pi.id, "item_id": it.id, "qty": str(qty), "batch_id": batch_id,
                    "balance_id": bal["balance_id"], "qc_status": "QUARANTINE" if it.is_batch_tracked else "N/A"})
    # Accounting in the SAME transaction: valuation (locked in item order) + Dr Inventory / Cr GRNI at PO price.
    vrows = acct.lock_valuations(db, [it.id for it, _, _ in valued])
    by_account: dict[str, Decimal] = {}
    for it, qty, value in valued:
        acct.val_receive(db, vrows, it.id, qty, value)
        code = acct.INVENTORY_ACCOUNT[it.kind]
        by_account[code] = by_account.get(code, Decimal(0)) + value
    acct.post_journal(db, entry_date=business_today(), description=f"Goods receipt {gr.number}", source_type="goods_receipt", source_id=gr.id,
                      lines=[(c, v, Decimal(0)) for c, v in sorted(by_account.items())] + [("2100", Decimal(0), sum(by_account.values()))], actor_id=cu.id)
    full = all(i.qty_received >= i.qty_ordered for i in po_items.values())
    po.status, po.version = ("RECEIVED" if full else "PARTIALLY_RECEIVED"), po.version + 1
    body = {"id": gr.id, "number": gr.number, "po_id": po_id, "po_status": po.status, "delivery_note_no": delivery_note_no, "lines": out}
    audit.record(db, user=cu, action="post", entity="goods_receipt", entity_id=gr.id, after={"number": gr.number, "po_id": po_id, "lines": len(out)})
    outbox.add(db, event_type="inventory.goods_receipt.posted", aggregate_type="goods_receipt", aggregate_id=gr.id,
               payload={"gr_id": gr.id, "number": gr.number, "po_id": po_id, "supplier_id": po.supplier_id, "lines": out})
    return body
