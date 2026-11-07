"""Accounting domain service. Runs inside the caller's transaction (run_tx); never commits.

Lock order for anything touching money: documents -> stock balances -> item_valuation (ascending item_id, one batch)
-> journal inserts (leaf) -> document_counters (LAST: gapless numbering holds a hot row, keep the hold short)."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from psycopg import errors as pgerr
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.models import (Account, AccountingPeriod, ItemValuation, JournalEntry, JournalLine, Payment, PaymentAllocation,
                                 SupplierInvoice, SupplierInvoiceLine)
from app.audit import service as audit
from app.core import observability as obs
from app.core import outbox
from app.core.context import request_id_var
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.procurement.models import PurchaseOrder, PurchaseOrderItem
from app.core.clock import business_today

C = Decimal("0.01")
INVENTORY_ACCOUNT = {"RM": "1300", "PM": "1300", "BULK": "1310", "FG": "1320"}


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(C, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ periods & accounts
def period_for(db: Session, d: date) -> AccountingPeriod:
    p = db.scalar(select(AccountingPeriod).where(AccountingPeriod.start_date <= d, AccountingPeriod.end_date >= d))
    if not p:
        raise BusinessConflict("no_accounting_period", f"No accounting period covers {d}")
    if p.status != "OPEN":
        raise BusinessConflict("period_closed", f"Accounting period {p.name} is closed")
    return p


def _accounts(db: Session, codes: set[str]) -> dict[str, Account]:
    rows = {a.code: a for a in db.scalars(select(Account).where(Account.code.in_(codes)))}
    for c in codes:
        a = rows.get(c)
        if not a or not a.is_active or not a.is_postable:
            raise Unprocessable(f"Account {c} does not exist, is inactive or is not postable")
    return rows


# ------------------------------------------------------------------ journals
def post_journal(db: Session, *, entry_date: date, description: str, source_type: str, source_id: int, source_event: str = "post",
                 lines: list[tuple[str, Decimal, Decimal]], actor_id: int | None, reverses_entry_id: int | None = None) -> JournalEntry:
    """lines = [(account_code, debit, credit)]. Balanced by construction here AND by a deferred DB trigger."""
    lines = [(c, money(d), money(cr)) for c, d, cr in lines if money(d) or money(cr)]
    if len(lines) < 2:
        raise Unprocessable("A journal needs at least two non-zero lines")
    td, tc = sum(d for _, d, _ in lines), sum(c for _, _, c in lines)
    if td != tc:
        raise BusinessConflict("unbalanced_journal", f"Debits {td} do not equal credits {tc}")
    period = period_for(db, entry_date)
    accts = _accounts(db, {c for c, _, _ in lines})
    n = db.execute(text("SELECT nextval('journal_number_seq')")).scalar_one()
    je = JournalEntry(number=f"JV/{entry_date:%y}/{n:07d}", entry_date=entry_date, period_id=period.id, description=description[:300],
                      source_type=source_type, source_id=source_id, source_event=source_event, total_debit=td, total_credit=tc,
                      reverses_entry_id=reverses_entry_id, created_by=actor_id, request_id=request_id_var.get())
    je.lines = [JournalLine(line_no=i, account_id=accts[c].id, debit=d, credit=cr) for i, (c, d, cr) in enumerate(lines, 1)]
    db.add(je)
    try:
        db.flush()
    except IntegrityError as e:
        if isinstance(e.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_posting", "This document has already been posted to the ledger",
                                   source=f"{source_type}:{source_id}:{source_event}") from e
        raise
    obs.JOURNALS.inc()
    return je


def reverse_journal(db: Session, cu: CurrentUser, entry_id: int, reason: str) -> dict:
    orig = db.get(JournalEntry, entry_id)
    if not orig:
        raise NotFound("Journal entry")
    if orig.reverses_entry_id:
        raise BusinessConflict("cannot_reverse_reversal", "A reversal cannot be reversed")
    rev = post_journal(db, entry_date=business_today(), description=f"Reversal of {orig.number}: {reason}", source_type="journal_reversal",
                       source_id=orig.id, lines=[(db.get(Account, l.account_id).code, l.credit, l.debit) for l in orig.lines],
                       actor_id=cu.id, reverses_entry_id=orig.id)
    audit.record(db, user=cu, action="journal_reversal", entity="journal_entry", entity_id=orig.id, after={"reversal": rev.number}, reason=reason)
    return journal_dict(rev)


def journal_dict(je: JournalEntry) -> dict:
    return {"id": je.id, "number": je.number, "entry_date": je.entry_date, "description": je.description, "source": f"{je.source_type}:{je.source_id}",
            "total": str(je.total_debit), "reverses_entry_id": je.reverses_entry_id,
            "lines": [{"line_no": l.line_no, "account_id": l.account_id, "debit": str(l.debit), "credit": str(l.credit)} for l in je.lines]}


# ------------------------------------------------------------------ valuation (moving average)
def lock_valuations(db: Session, item_ids: list[int]) -> dict[int, ItemValuation]:
    ids = sorted(set(item_ids))
    for i in ids:
        db.execute(text("INSERT INTO item_valuation(item_id) VALUES (:i) ON CONFLICT DO NOTHING"), {"i": i})
    rows = db.execute(select(ItemValuation).where(ItemValuation.item_id.in_(ids)).order_by(ItemValuation.item_id).with_for_update())
    return {r.item_id: r for r in rows.scalars()}


def val_receive(db: Session, rows: dict[int, ItemValuation], item_id: int, qty: Decimal, value: Decimal) -> None:
    r = rows[item_id]
    r.qty = r.qty + qty
    r.value = r.value + money(value)


def val_consume(rows: dict[int, ItemValuation], item_id: int, qty: Decimal) -> Decimal:
    """Value of `qty` at moving-average cost. Stock without a valuation (e.g. opening balances) carries zero cost."""
    r = rows[item_id]
    take = min(qty, r.qty)
    if take <= 0:
        return Decimal("0.00")
    v = r.value if take == r.qty else money(r.value * take / r.qty)
    r.qty = r.qty - take
    r.value = r.value - v
    return v


# ------------------------------------------------------------------ gapless numbering
def next_gapless(db: Session, name: str) -> int:
    db.execute(text("INSERT INTO document_counters(name, last_no) VALUES (:n, 0) ON CONFLICT DO NOTHING"), {"n": name})
    return db.execute(text("UPDATE document_counters SET last_no = last_no + 1 WHERE name = :n RETURNING last_no"), {"n": name}).scalar_one()


def fiscal_year(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


# ------------------------------------------------------------------ supplier invoice (three-way match)
def post_supplier_invoice(db: Session, cu: CurrentUser, *, po_id: int, supplier_invoice_no: str, invoice_date: date, lines: list[dict]) -> dict:
    if len({l["po_item_id"] for l in lines}) != len(lines):
        raise Unprocessable("A PO line may appear once per invoice")
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update())     # documents first
    if not po:
        raise NotFound("Purchase order")
    items = {i.id: i for i in db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.po_id == po_id).order_by(PurchaseOrderItem.id).with_for_update())}
    grni = sub = tax = Decimal(0)
    inv_lines = []
    for n, l in enumerate(sorted(lines, key=lambda x: x["po_item_id"]), 1):
        pi = items.get(l["po_item_id"])
        if not pi:
            raise Unprocessable(f"Line {l['po_item_id']} is not on this purchase order")
        q, price = Decimal(str(l["qty"])), money(l["unit_price"])
        if pi.qty_invoiced + q > pi.qty_received:       # three-way match: PO ↔ receipt ↔ invoice
            raise BusinessConflict("invoice_exceeds_receipt", "Invoiced quantity would exceed the quantity received",
                                   po_item_id=pi.id, received=str(pi.qty_received), already_invoiced=str(pi.qty_invoiced))
        rate = Decimal(str(l.get("tax_rate") if l.get("tax_rate") is not None else pi.tax_rate))
        grni += money(q * pi.unit_price)
        s = money(q * price)
        sub += s
        tax += money(s * rate / 100)
        pi.qty_invoiced = pi.qty_invoiced + q
        inv_lines.append(SupplierInvoiceLine(line_no=n, po_item_id=pi.id, qty=q, unit_price=price, tax_rate=rate))
    total = sub + tax
    variance = sub - grni                               # + = paid more than PO price
    n = db.execute(text("SELECT nextval('supplier_invoice_seq')")).scalar_one()
    inv = SupplierInvoice(number=f"SINV/{invoice_date:%y}/{n:06d}", supplier_id=po.supplier_id, po_id=po_id, supplier_invoice_no=supplier_invoice_no,
                          invoice_date=invoice_date, subtotal=sub, tax_total=tax, total=total, created_by=cu.id)
    inv.lines = inv_lines
    db.add(inv)
    try:
        db.flush()
    except IntegrityError as e:
        if isinstance(e.orig, pgerr.UniqueViolation):
            raise BusinessConflict("duplicate_supplier_invoice", "This supplier invoice number has already been booked") from e
        raise
    je = post_journal(db, entry_date=invoice_date, description=f"Supplier invoice {supplier_invoice_no}", source_type="supplier_invoice",
                      source_id=inv.id, actor_id=cu.id,
                      lines=[("2100", grni, Decimal(0)), ("5300", variance if variance > 0 else Decimal(0), -variance if variance < 0 else Decimal(0)),
                             ("1500", tax, Decimal(0)), ("2000", Decimal(0), total)])
    inv.journal_entry_id = je.id
    audit.record(db, user=cu, action="post", entity="supplier_invoice", entity_id=inv.id, after={"number": inv.number, "total": str(total)})
    outbox.add(db, event_type="accounts.supplier_invoice.posted", aggregate_type="supplier_invoice", aggregate_id=inv.id,
               payload={"id": inv.id, "number": inv.number, "total": str(total), "po_id": po_id})
    return supplier_invoice_dict(inv)


def supplier_invoice_dict(i: SupplierInvoice) -> dict:
    return {"id": i.id, "number": i.number, "supplier_id": i.supplier_id, "po_id": i.po_id, "supplier_invoice_no": i.supplier_invoice_no,
            "subtotal": str(i.subtotal), "tax_total": str(i.tax_total), "total": str(i.total), "amount_paid": str(i.amount_paid),
            "outstanding": str(i.total - i.amount_paid), "status": i.status, "journal_entry_id": i.journal_entry_id}


# ------------------------------------------------------------------ payments
def post_payment(db: Session, cu: CurrentUser, *, direction: str, party_id: int, amount, payment_date: date, method: str,
                 bank_account_code: str, reference: str | None, allocations: list[dict]) -> dict:
    amount = money(amount)
    if sum(money(a["amount"]) for a in allocations) != amount:
        raise BusinessConflict("allocation_mismatch", "Allocations must add up to the payment amount")
    if len({a["invoice_id"] for a in allocations}) != len(allocations):
        raise Unprocessable("An invoice may appear once per payment")
    if bank_account_code not in ("1000", "1100") and not db.execute(text("SELECT 1 FROM bank_accounts WHERE ledger_account_code = :c AND is_active"), {"c": bank_account_code}).first():
        raise Unprocessable("Payments settle through Cash (1000), Bank (1100) or a registered bank account")
    supplier = direction == "PAID"
    if supplier:
        model, inv_type, ctl = SupplierInvoice, "supplier_invoice", "2000"
    else:
        from app.dispatch.models import SalesInvoice
        model, inv_type, ctl = SalesInvoice, "sales_invoice", "1200"
    ids = sorted(a["invoice_id"] for a in allocations)
    invoices = {i.id: i for i in db.scalars(select(model).where(model.id.in_(ids)).order_by(model.id).with_for_update())}   # id order
    for a in allocations:
        inv = invoices.get(a["invoice_id"])
        if not inv or (inv.supplier_id if supplier else inv.customer_id) != party_id:
            raise Unprocessable(f"Invoice {a['invoice_id']} not found for this party")
        amt = money(a["amount"])
        credited = getattr(inv, "credited_amount", Decimal(0))
        outstanding = inv.total - inv.amount_paid - credited
        if amt > outstanding:
            raise BusinessConflict("overpayment", "Allocation exceeds the outstanding amount", invoice_id=inv.id, outstanding=str(outstanding))
        inv.amount_paid = inv.amount_paid + amt
        inv.status = "PAID" if inv.amount_paid + credited == inv.total else "PARTIAL"
    n = db.execute(text("SELECT nextval('payment_number_seq')")).scalar_one()
    pay = Payment(number=f"PAY/{payment_date:%y}/{n:06d}", direction=direction, party_type="supplier" if supplier else "customer", party_id=party_id,
                  amount=amount, payment_date=payment_date, method=method,
                  payment_method_id=db.scalar(text("SELECT id FROM payment_methods WHERE kind = :k AND is_active ORDER BY code LIMIT 1"), {"k": method}),
                  bank_account_code=bank_account_code, reference=reference, created_by=cu.id)
    db.add(pay)
    db.flush()
    db.add_all(PaymentAllocation(payment_id=pay.id, invoice_type=inv_type, invoice_id=a["invoice_id"], amount=money(a["amount"])) for a in allocations)
    lines = [(ctl, amount, Decimal(0)), (bank_account_code, Decimal(0), amount)] if supplier else [(bank_account_code, amount, Decimal(0)), (ctl, Decimal(0), amount)]
    je = post_journal(db, entry_date=payment_date, description=f"{'Payment to supplier' if supplier else 'Receipt from customer'} {pay.number}",
                      source_type="payment", source_id=pay.id, lines=lines, actor_id=cu.id)
    pay.journal_entry_id = je.id
    audit.record(db, user=cu, action="post", entity="payment", entity_id=pay.id, after={"number": pay.number, "amount": str(amount)})
    outbox.add(db, event_type="accounts.payment.posted", aggregate_type="payment", aggregate_id=pay.id, payload={"id": pay.id, "number": pay.number, "amount": str(amount)})
    return {"id": pay.id, "number": pay.number, "amount": str(amount), "journal_entry_id": je.id,
            "allocations": [{"invoice_id": a["invoice_id"], "amount": str(money(a["amount"]))} for a in allocations]}


# ------------------------------------------------------------------ reports (read-only)
def trial_balance(db: Session, as_of: date | None = None) -> dict:
    rows = db.execute(text("""
        SELECT a.code, a.name, a.type, COALESCE(sum(l.debit),0) d, COALESCE(sum(l.credit),0) c
          FROM accounts a LEFT JOIN journal_lines l ON l.account_id = a.id
          LEFT JOIN journal_entries e ON e.id = l.entry_id AND (CAST(:d AS date) IS NULL OR e.entry_date <= :d)
         WHERE (l.id IS NULL OR e.id IS NOT NULL) GROUP BY a.id ORDER BY a.code"""), {"d": as_of}).mappings().all()
    out = [{"code": r["code"], "name": r["name"], "type": r["type"], "debit": str(r["d"]), "credit": str(r["c"]), "balance": str(r["d"] - r["c"])}
           for r in rows if r["d"] or r["c"]]
    td, tc = sum(r["d"] for r in rows), sum(r["c"] for r in rows)
    return {"as_of": as_of, "accounts": out, "total_debit": str(td), "total_credit": str(tc), "balanced": td == tc}
