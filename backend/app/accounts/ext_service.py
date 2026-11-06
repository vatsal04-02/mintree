"""Accounts extensions: banking + reconciliation, supplier debit notes, marketplace settlement posting.
Lock order (unchanged): documents -> ... -> journals -> document counter last. Debit notes lock the supplier invoice, then post the journal, then take the counter."""
import csv
import hashlib
import io
from datetime import date
from decimal import Decimal

from psycopg import errors as pgerr
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import service as acct
from app.accounts.ext_models import BankAccount, BankTransaction, DebitNote
from app.accounts.models import Account, SupplierInvoice
from app.audit import service as audit
from app.core import outbox
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable

M = acct.money


# ------------------------------------------------------------------ bank accounts + statements
def create_bank_account(db: Session, cu: CurrentUser, data: dict) -> dict:
    a = db.scalar(select(Account).where(Account.code == data["ledger_account_code"]))
    if not a or a.type != "ASSET" or not a.is_postable:
        raise Unprocessable("The ledger account must be an existing, postable asset account (create it in the chart of accounts first)")
    b = BankAccount(**data)
    db.add(b)
    try:
        db.flush()
    except IntegrityError as ex:
        if isinstance(ex.orig, pgerr.UniqueViolation):
            raise BusinessConflict("ledger_account_in_use", "That ledger account is already linked to a bank account") from ex
        raise
    audit.record(db, user=cu, action="create", entity="bank_account", entity_id=b.id, after={"ledger": b.ledger_account_code})
    return bank_dict(b)


def _bank(db: Session, bid: int, lock: bool = False) -> BankAccount:
    q = select(BankAccount).where(BankAccount.id == bid)
    b = db.scalar(q.with_for_update() if lock else q)
    if not b:
        raise NotFound("Bank account")
    return b


def parse_statement(content: str) -> list[dict]:
    """CSV columns: date (YYYY-MM-DD), description, reference, and EITHER amount (signed) OR credit + debit (money in / money out)."""
    rd = csv.DictReader(io.StringIO(content))
    cols = set(rd.fieldnames or [])
    if "date" not in cols or not ({"amount"} <= cols or {"credit", "debit"} <= cols):
        raise Unprocessable("Statement needs columns: date, and amount (or credit and debit); optional description, reference")
    out = []
    try:
        for n, r in enumerate(rd, 1):
            if "amount" in cols and (r.get("amount") or "").strip():
                amt = Decimal(r["amount"].replace(",", ""))
            else:
                amt = Decimal((r.get("credit") or "0").replace(",", "") or 0) - Decimal((r.get("debit") or "0").replace(",", "") or 0)
            out.append({"line_no": n, "txn_date": date.fromisoformat(r["date"].strip()), "description": (r.get("description") or "")[:300] or None,
                        "reference": (r.get("reference") or "")[:80] or None, "amount": M(amt)})
    except (ValueError, ArithmeticError, KeyError) as ex:
        raise Unprocessable(f"Invalid statement row {len(out) + 1}: {ex}") from ex
    if not out:
        raise Unprocessable("The statement contains no transactions")
    if any(o["amount"] == 0 for o in out):
        raise Unprocessable("Statement lines must be non-zero")
    return out


def import_statement(db: Session, cu: CurrentUser, bank_id: int, content: str) -> dict:
    b = _bank(db, bank_id, lock=True)                                            # serialises concurrent imports for the account
    rows = parse_statement(content)
    ref = hashlib.sha256(content.encode()).hexdigest()[:32]
    new = 0
    for r in rows:
        ins = db.execute(pg_insert(BankTransaction).values(bank_account_id=b.id, statement_ref=ref, **r).on_conflict_do_nothing(constraint="uq_bank_transactions_line")
                         .returning(BankTransaction.id)).scalar()
        new += 1 if ins else 0
    audit.record(db, user=cu, action="import_statement", entity="bank_account", entity_id=b.id, after={"statement": ref, "lines": len(rows), "new": new})
    return {"statement_ref": ref, "lines": len(rows), "imported": new, "duplicates": len(rows) - new}


def _line(db: Session, line_id: int, code: str):
    return db.execute(text("""SELECT l.id, l.debit - l.credit AS amt, a.code, e.entry_date, e.number, e.description FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                                JOIN journal_entries e ON e.id = l.entry_id WHERE l.id = :i"""), {"i": line_id}).mappings().first()


def match(db: Session, cu: CurrentUser, txn_id: int, journal_line_id: int) -> dict:
    t = db.scalar(select(BankTransaction).where(BankTransaction.id == txn_id).with_for_update())
    if not t:
        raise NotFound("Bank transaction")
    if t.status != "UNMATCHED":
        raise BusinessConflict("invalid_status", f"Transaction is already {t.status}")
    b = _bank(db, t.bank_account_id)
    ln = _line(db, journal_line_id, b.ledger_account_code)
    if not ln or ln["code"] != b.ledger_account_code:
        raise Unprocessable("That journal line is not on this bank account's ledger account")
    if M(ln["amt"]) != t.amount:
        raise BusinessConflict("amount_mismatch", "The ledger line amount differs from the statement amount", ledger=str(M(ln["amt"])), statement=str(t.amount))
    t.status, t.journal_line_id, t.matched_by, t.matched_at = "MATCHED", journal_line_id, cu.id, text("now()")
    try:
        db.flush()
    except IntegrityError as ex:
        if isinstance(ex.orig, pgerr.UniqueViolation):
            raise BusinessConflict("line_already_matched", "That ledger line is already matched to another statement line") from ex
        raise
    audit.record(db, user=cu, action="match", entity="bank_transaction", entity_id=t.id, after={"journal_line_id": journal_line_id})
    return txn_dict(t)


def unmatch(db: Session, cu: CurrentUser, txn_id: int) -> dict:
    t = db.scalar(select(BankTransaction).where(BankTransaction.id == txn_id).with_for_update())
    if not t:
        raise NotFound("Bank transaction")
    if t.status != "MATCHED":
        raise BusinessConflict("invalid_status", "Only matched transactions can be unmatched")
    t.status, t.journal_line_id, t.matched_by, t.matched_at = "UNMATCHED", None, None, None
    audit.record(db, user=cu, action="unmatch", entity="bank_transaction", entity_id=t.id)
    return txn_dict(t)


def book(db: Session, cu: CurrentUser, txn_id: int, counter_account_code: str, narration: str | None) -> dict:
    """A statement line with no ledger entry yet (bank charges, interest, ...): post the journal and match it in one transaction."""
    t = db.scalar(select(BankTransaction).where(BankTransaction.id == txn_id).with_for_update())
    if not t:
        raise NotFound("Bank transaction")
    if t.status != "UNMATCHED":
        raise BusinessConflict("invalid_status", f"Transaction is already {t.status}")
    b = _bank(db, t.bank_account_id)
    if counter_account_code == b.ledger_account_code:
        raise Unprocessable("The counter account must differ from the bank account")
    amt = abs(t.amount)
    lines = [(b.ledger_account_code, amt, Decimal(0)), (counter_account_code, Decimal(0), amt)] if t.amount > 0 else \
            [(counter_account_code, amt, Decimal(0)), (b.ledger_account_code, Decimal(0), amt)]
    je = acct.post_journal(db, entry_date=t.txn_date, description=narration or t.description or "Bank transaction", source_type="bank_transaction", source_id=t.id, lines=lines, actor_id=cu.id)
    line_id = db.scalar(text("SELECT l.id FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE l.entry_id = :e AND a.code = :c"), {"e": je.id, "c": b.ledger_account_code})
    t.status, t.journal_line_id, t.matched_by, t.matched_at = "MATCHED", line_id, cu.id, text("now()")
    audit.record(db, user=cu, action="book", entity="bank_transaction", entity_id=t.id, after={"journal": je.number, "counter": counter_account_code})
    return txn_dict(t) | {"journal_entry": je.number}


def ignore(db: Session, cu: CurrentUser, txn_id: int, reason: str) -> dict:
    t = db.scalar(select(BankTransaction).where(BankTransaction.id == txn_id).with_for_update())
    if not t:
        raise NotFound("Bank transaction")
    if t.status != "UNMATCHED":
        raise BusinessConflict("invalid_status", f"Transaction is already {t.status}")
    t.status = "IGNORED"
    audit.record(db, user=cu, action="ignore", entity="bank_transaction", entity_id=t.id, reason=reason)
    return txn_dict(t)


def suggestions(db: Session, bank_id: int, window_days: int = 7) -> list[dict]:
    """Unmatched statement lines with candidate ledger lines of EXACTLY the same signed amount within the window (ranked by date distance, then reference hit)."""
    b = _bank(db, bank_id)
    rows = db.execute(text("""
        SELECT t.id AS txn_id, t.txn_date, t.amount, t.reference, l.id AS line_id, e.number, e.entry_date, e.description,
               abs(e.entry_date - t.txn_date) AS gap,
               (t.reference IS NOT NULL AND (e.description ILIKE '%' || t.reference || '%' OR e.number ILIKE '%' || t.reference || '%')) AS ref_hit
          FROM bank_transactions t
          JOIN journal_lines l ON l.debit - l.credit = t.amount
          JOIN accounts a ON a.id = l.account_id AND a.code = :code
          JOIN journal_entries e ON e.id = l.entry_id
         WHERE t.bank_account_id = :b AND t.status = 'UNMATCHED' AND abs(e.entry_date - t.txn_date) <= :w
           AND NOT EXISTS (SELECT 1 FROM bank_transactions m WHERE m.journal_line_id = l.id)
         ORDER BY t.id, ref_hit DESC, gap, l.id"""), {"code": b.ledger_account_code, "b": bank_id, "w": window_days}).mappings().all()
    out: dict[int, dict] = {}
    for r in rows:
        s = out.setdefault(r["txn_id"], {"txn_id": r["txn_id"], "txn_date": r["txn_date"], "amount": str(r["amount"]), "reference": r["reference"], "candidates": []})
        s["candidates"].append({"journal_line_id": r["line_id"], "journal": r["number"], "entry_date": r["entry_date"], "description": r["description"], "date_gap_days": r["gap"], "reference_hit": r["ref_hit"]})
    return list(out.values())


def reconciliation(db: Session, bank_id: int) -> dict:
    b = _bank(db, bank_id)
    one = lambda sql, **p: db.execute(text(sql), {"b": bank_id, "c": b.ledger_account_code, **p}).scalar()
    ledger = M(one("SELECT COALESCE(sum(l.debit - l.credit),0) FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = :c"))
    stmt_txn = M(one("SELECT COALESCE(sum(amount),0) FROM bank_transactions WHERE bank_account_id = :b AND status <> 'IGNORED'"))
    statement_balance = M(b.opening_balance) + stmt_txn
    bank_unmatched = M(one("SELECT COALESCE(sum(amount),0) FROM bank_transactions WHERE bank_account_id = :b AND status = 'UNMATCHED'"))
    book_unmatched = M(one("""SELECT COALESCE(sum(l.debit - l.credit),0) FROM journal_lines l JOIN accounts a ON a.id = l.account_id
                               WHERE a.code = :c AND NOT EXISTS (SELECT 1 FROM bank_transactions t WHERE t.journal_line_id = l.id)"""))
    unexplained = (ledger - book_unmatched) - (statement_balance - bank_unmatched)
    return {"bank_account": bank_dict(b), "ledger_balance": str(ledger), "statement_balance": str(statement_balance),
            "statement_lines_not_in_books": {"count": one("SELECT count(*) FROM bank_transactions WHERE bank_account_id = :b AND status = 'UNMATCHED'"), "net": str(bank_unmatched)},
            "book_lines_not_on_statement": {"count": one("""SELECT count(*) FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = :c
                                                         AND NOT EXISTS (SELECT 1 FROM bank_transactions t WHERE t.journal_line_id = l.id)"""), "net": str(book_unmatched)},
            "unexplained_difference": str(unexplained), "reconciled": unexplained == 0 and bank_unmatched == 0 and book_unmatched == 0,
            "note": "unexplained = (ledger - unmatched book lines) - (statement - unmatched statement lines); non-zero means an opening-balance or data error"}


# ------------------------------------------------------------------ supplier debit notes
def post_debit_note(db: Session, cu: CurrentUser, *, invoice_id: int, amount_ex_tax, tax_rate, reason_type: str, reason: str) -> dict:
    inv = db.scalar(select(SupplierInvoice).where(SupplierInvoice.id == invoice_id).with_for_update())
    if not inv:
        raise NotFound("Supplier invoice")
    sub = M(amount_ex_tax)
    rate = Decimal(str(tax_rate)) if tax_rate is not None else (inv.tax_total / inv.subtotal * 100 if inv.subtotal else Decimal(0))
    tax = M(sub * rate / 100)
    total = sub + tax
    outstanding = inv.total - inv.amount_paid - inv.credited_amount
    if total > outstanding:
        raise BusinessConflict("exceeds_outstanding", "A debit note cannot exceed what is still outstanding on the invoice", outstanding=str(outstanding), requested=str(total))
    lines = [("2000", total, Decimal(0)), ("5300", Decimal(0), sub)] + ([("1500", Decimal(0), tax)] if tax else [])
    je = acct.post_journal(db, entry_date=date.today(), description=f"Debit note on {inv.number} ({reason_type})", source_type="supplier_invoice", source_id=inv.id,
                           source_event=f"debit_note:{inv.credited_amount}", lines=lines, actor_id=cu.id)
    fy = acct.fiscal_year(date.today())
    seq = acct.next_gapless(db, f"debit_note:{fy}")                                  # counter last
    dn = DebitNote(number=f"DN/{fy}/{seq:06d}", supplier_invoice_id=inv.id, reason_type=reason_type, reason=reason, subtotal=sub, tax_total=tax, total=total,
                   journal_entry_id=je.id, created_by=cu.id)
    db.add(dn)
    inv.credited_amount = inv.credited_amount + total
    inv.status = "PAID" if inv.amount_paid + inv.credited_amount == inv.total else ("PARTIAL" if inv.amount_paid + inv.credited_amount > 0 else inv.status)
    db.flush()
    audit.record(db, user=cu, action="post", entity="debit_note", entity_id=dn.id, after={"number": dn.number, "total": str(total), "invoice": inv.number}, reason=reason)
    outbox.add(db, event_type="accounts.debit_note.posted", aggregate_type="debit_note", aggregate_id=dn.id, payload={"number": dn.number, "total": str(total)})
    return debit_note_dict(dn)


# ------------------------------------------------------------------ marketplace settlement -> ledger
def post_settlement(db: Session, cu: CurrentUser, settlement_id: int, bank_account_code: str) -> dict:
    """Clears the marketplace customer's open invoices (oldest first) by (a) the cash actually received and (b) marketplace deductions
    (commission, shipping, ads, other, plus any shortfall vs the expected net), which are expensed to 5500. Returns/refunds are NOT deducted
    here: they already reduced AR through the returns workflow (credit notes). Posts once; locks invoices in id order."""
    from app.ecommerce.models import MarketplaceAccount, MarketplaceSettlement
    from app.dispatch.models import SalesInvoice
    st = db.scalar(select(MarketplaceSettlement).where(MarketplaceSettlement.id == settlement_id).with_for_update())
    if not st:
        raise NotFound("Settlement")
    if st.posted_at:
        raise BusinessConflict("already_posted", "This settlement has already been posted to the ledger")
    if st.received_amount is None:
        raise BusinessConflict("receipt_not_recorded", "Record the amount received before posting")
    if bank_account_code not in ("1000", "1100") and not db.scalar(select(BankAccount.id).where(BankAccount.ledger_account_code == bank_account_code)):
        raise Unprocessable("Settlements are received into Cash, Bank or a registered bank account")
    acc = db.get(MarketplaceAccount, st.account_id)
    fees = st.commission + st.shipping_fees + st.ad_fees + st.other_deductions
    shortfall = st.net_settlement - st.received_amount                              # positive = marketplace paid less than expected
    deductions = fees + max(shortfall, Decimal(0))
    excess = max(-shortfall, Decimal(0))
    if excess:
        raise BusinessConflict("received_exceeds_expected", "Amount received exceeds the settlement's expected net; correct the settlement before posting", excess=str(excess))
    to_clear = st.received_amount + deductions
    invs = list(db.scalars(select(SalesInvoice).where(SalesInvoice.customer_id == acc.customer_id, SalesInvoice.status != "PAID").order_by(SalesInvoice.id).with_for_update()))
    open_total = sum((i.total - i.amount_paid - i.credited_amount for i in invs), Decimal(0))
    if to_clear > open_total:
        raise BusinessConflict("exceeds_open_invoices", "The settlement clears more than the marketplace customer's open invoices", to_clear=str(to_clear), open_invoices=str(open_total))
    remaining_cash, remaining_ded = st.received_amount, deductions
    allocations = []
    for i in invs:
        out = i.total - i.amount_paid - i.credited_amount
        if out <= 0 or (remaining_cash <= 0 and remaining_ded <= 0):
            continue
        cash = min(out, remaining_cash)
        ded = min(out - cash, remaining_ded)
        remaining_cash -= cash
        remaining_ded -= ded
        if cash:
            allocations.append({"invoice_id": i.id, "amount": cash})
        if ded:
            i.credited_amount += ded
    payment = None
    if st.received_amount > 0:
        payment = acct.post_payment(db, cu, direction="RECEIVED", party_id=acc.customer_id, amount=st.received_amount, payment_date=date.today(), method="MARKETPLACE",
                                    bank_account_code=bank_account_code, reference=st.reference, allocations=allocations)
    if deductions > 0:
        acct.post_journal(db, entry_date=date.today(), description=f"Marketplace deductions {st.reference}", source_type="marketplace_settlement", source_id=st.id, source_event="deductions",
                          lines=[("5500", deductions, Decimal(0)), ("1200", Decimal(0), deductions)], actor_id=cu.id)
    for i in invs:
        i.status = "PAID" if i.amount_paid + i.credited_amount == i.total else ("PARTIAL" if i.amount_paid + i.credited_amount > 0 else i.status)
    st.posted_at, st.payment_id = text("now()"), payment["id"] if payment else None
    audit.record(db, user=cu, action="post", entity="marketplace_settlement", entity_id=st.id, after={"received": str(st.received_amount), "deductions": str(deductions)})
    return {"settlement_id": st.id, "cash_received": str(st.received_amount), "deductions_expensed": str(deductions), "invoices_cleared_value": str(to_clear),
            "payment": payment["number"] if payment else None}


def bank_dict(b: BankAccount) -> dict:
    return {"id": b.id, "name": b.name, "bank_name": b.bank_name, "account_last4": b.account_last4, "ledger_account_code": b.ledger_account_code, "opening_balance": str(b.opening_balance), "is_active": b.is_active}


def txn_dict(t: BankTransaction) -> dict:
    return {"id": t.id, "txn_date": t.txn_date, "description": t.description, "reference": t.reference, "amount": str(t.amount), "status": t.status, "journal_line_id": t.journal_line_id}


def debit_note_dict(d: DebitNote) -> dict:
    return {"id": d.id, "number": d.number, "supplier_invoice_id": d.supplier_invoice_id, "reason_type": d.reason_type, "subtotal": str(d.subtotal), "tax_total": str(d.tax_total), "total": str(d.total)}
