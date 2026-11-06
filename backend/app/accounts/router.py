from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.accounts import schemas as s
from app.accounts import service as svc
from app.accounts.models import Account, AccountingPeriod, JournalEntry, SupplierInvoice
from app.audit import service as audit
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import BusinessConflict, NotFound
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx

router = APIRouter(prefix="/accounts", tags=["accounts"])
DB = Depends(get_db, scope="function")


@router.get("/chart")
def chart(db: Session = DB, _: CurrentUser = Depends(require("accounts.coa:read"))):
    return [{"id": a.id, "code": a.code, "name": a.name, "type": a.type, "is_postable": a.is_postable, "is_active": a.is_active}
            for a in db.scalars(select(Account).order_by(Account.code))]


@router.post("/chart", status_code=201)
def create_account(body: s.AccountIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.coa:create"))):
    def work(d):
        if d.scalar(select(Account.id).where(Account.code == body.code)):
            raise BusinessConflict("duplicate_account", "Account code already exists")
        a = Account(**body.model_dump())
        d.add(a)
        d.flush()
        audit.record(d, user=cu, action="create", entity="account", entity_id=a.id, after=body.model_dump())
        return {"id": a.id, "code": a.code}
    return run_tx(db, work, name="accounts.coa.create")


@router.get("/periods")
def periods(db: Session = DB, _: CurrentUser = Depends(require("accounts.period:read"))):
    return [{"id": p.id, "name": p.name, "start_date": p.start_date, "end_date": p.end_date, "status": p.status}
            for p in db.scalars(select(AccountingPeriod).order_by(AccountingPeriod.start_date))]


@router.post("/periods/{period_id}/close")
def close_period(period_id: int, db: Session = DB, cu: CurrentUser = Depends(require("accounts.period:close"))):
    def work(d):
        p = d.scalar(select(AccountingPeriod).where(AccountingPeriod.id == period_id).with_for_update())
        if not p:
            raise NotFound("Accounting period")
        if p.status == "CLOSED":
            raise BusinessConflict("already_closed", "Period is already closed")
        p.status, p.closed_at = "CLOSED", datetime.now(timezone.utc)
        audit.record(d, user=cu, action="period_close", entity="accounting_period", entity_id=p.id, before={"status": "OPEN"}, after={"status": "CLOSED"})
        return {"id": p.id, "status": p.status}
    return run_tx(db, work, name="accounts.period.close")


@router.get("/journals")
def journals(before_id: int | None = None, limit: int = Query(50, ge=1, le=200), db: Session = DB, _: CurrentUser = Depends(require("accounts.journal:read"))):
    q = select(JournalEntry).order_by(JournalEntry.id.desc()).limit(limit + 1)
    if before_id:
        q = q.where(JournalEntry.id < before_id)
    rows = db.scalars(q).unique().all()
    return {"items": [svc.journal_dict(j) for j in rows[:limit]], "next_before_id": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/journals/{entry_id}")
def journal(entry_id: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.journal:read"))):
    j = db.get(JournalEntry, entry_id)
    if not j:
        raise NotFound("Journal entry")
    return svc.journal_dict(j)


@router.post("/journals", status_code=201)
def manual_journal(body: s.ManualJournal, key: str = Depends(require_idempotency_key), db: Session = DB,
                   cu: CurrentUser = Depends(require("accounts.journal:post"))):
    def effect(d):
        sid = d.execute(text("SELECT nextval('manual_journal_seq')")).scalar_one()
        je = svc.post_journal(d, entry_date=body.entry_date, description=body.description, source_type="manual", source_id=sid,
                              lines=[(l.account_code, l.debit, l.credit) for l in body.lines], actor_id=cu.id)
        audit.record(d, user=cu, action="manual_journal", entity="journal_entry", entity_id=je.id, after={"number": je.number})
        return 201, svc.journal_dict(je), "journal_entry", je.id
    return idempotent_post(db, cu, scope="accounts.journal.manual", key=key, payload=body.model_dump(mode="json"), effect=effect)


@router.post("/journals/{entry_id}/reverse", status_code=201)
def reverse(entry_id: int, body: s.Reason, key: str = Depends(require_idempotency_key), db: Session = DB,
            cu: CurrentUser = Depends(require("accounts.journal:post"))):
    return idempotent_post(db, cu, scope="accounts.journal.reverse", key=key, payload={"id": entry_id, **body.model_dump()},
                           effect=lambda d: (201, svc.reverse_journal(d, cu, entry_id, body.reason), "journal_entry", entry_id))


@router.get("/reports/trial-balance")
def trial_balance(as_of: date | None = None, db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    return svc.trial_balance(db, as_of)


@router.get("/reports/ledger/{code}")
def account_ledger(code: str, before_id: int | None = None, limit: int = Query(100, ge=1, le=500), db: Session = DB,
                   _: CurrentUser = Depends(require("accounts.report:read"))):
    rows = db.execute(text("""SELECT e.id entry_id, e.number, e.entry_date, e.description, l.debit, l.credit FROM journal_lines l
                              JOIN journal_entries e ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id
                             WHERE a.code = :c AND (CAST(:b AS bigint) IS NULL OR e.id < :b) ORDER BY e.id DESC LIMIT :n"""),
                      {"c": code, "b": before_id, "n": limit + 1}).mappings().all()
    return {"items": [dict(r) for r in rows[:limit]], "next_before_id": rows[limit - 1]["entry_id"] if len(rows) > limit else None}


@router.get("/reports/payables")
def payables(db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    rows = db.execute(text("""SELECT i.id, i.number, i.supplier_id, i.invoice_date, i.total, i.amount_paid, i.total - i.amount_paid AS outstanding,
                                     (current_date - i.invoice_date) AS age_days FROM supplier_invoices i WHERE i.status <> 'PAID' ORDER BY i.invoice_date""")).mappings().all()
    return {"items": [dict(r) for r in rows], "total_outstanding": str(sum(r["outstanding"] for r in rows))}


# ---- supplier invoices & payments (idempotent postings)
@router.post("/supplier-invoices", status_code=201)
def post_supplier_invoice(body: s.SupplierInvoiceIn, key: str = Depends(require_idempotency_key), db: Session = DB,
                          cu: CurrentUser = Depends(require("accounts.supplier_invoice:post"))):
    return idempotent_post(db, cu, scope="accounts.supplier_invoice.post", key=key, payload=body.model_dump(mode="json"),
                           effect=lambda d: (lambda r: (201, r, "supplier_invoice", r["id"]))(
                               svc.post_supplier_invoice(d, cu, po_id=body.po_id, supplier_invoice_no=body.supplier_invoice_no,
                                                         invoice_date=body.invoice_date, lines=[l.model_dump() for l in body.lines])))


@router.get("/supplier-invoices/{inv_id}")
def get_supplier_invoice(inv_id: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.supplier_invoice:read"))):
    i = db.get(SupplierInvoice, inv_id)
    if not i:
        raise NotFound("Supplier invoice")
    return svc.supplier_invoice_dict(i)


@router.post("/payments", status_code=201)
def post_payment(body: s.PaymentIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("accounts.payment:post"))):
    return idempotent_post(db, cu, scope="accounts.payment.post", key=key, payload=body.model_dump(mode="json"),
                           effect=lambda d: (lambda r: (201, r, "payment", r["id"]))(svc.post_payment(
                               d, cu, direction=body.direction, party_id=body.party_id, amount=body.amount, payment_date=body.payment_date,
                               method=body.method, bank_account_code=body.bank_account_code, reference=body.reference,
                               allocations=[a.model_dump() for a in body.allocations])))
