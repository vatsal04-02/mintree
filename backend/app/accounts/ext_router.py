from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.accounts import ext_service as svc
from app.accounts import reports
from app.accounts.ext_models import BankAccount, BankTransaction, DebitNote
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx

router = APIRouter(prefix="/accounts", tags=["accounts"])
DB = Depends(get_db, scope="function")
Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BankIn(_In):
    name: str = Field(min_length=2, max_length=120)
    bank_name: str | None = None
    account_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    ledger_account_code: str = Field(min_length=1, max_length=20)
    opening_balance: Decimal = Decimal(0)


class MatchIn(_In):
    journal_line_id: int


class BookIn(_In):
    counter_account_code: str
    narration: str | None = Field(default=None, max_length=300)


class IgnoreIn(_In):
    reason: str = Field(min_length=3)


class DebitNoteIn(_In):
    amount_ex_tax: Money
    tax_rate: Annotated[Decimal, Field(ge=0, le=100)] | None = None
    reason_type: Literal["PRICE_ADJUSTMENT", "QUALITY_CLAIM", "SHORT_SUPPLY"]
    reason: str = Field(min_length=5)


class PostSettlementIn(_In):
    bank_account_code: str = "1100"


# ---- banking
@router.post("/bank-accounts", status_code=201)
def create_bank(body: BankIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.bank:create"))):
    return run_tx(db, lambda d: svc.create_bank_account(d, cu, body.model_dump()), name="accounts.bank.create")


@router.get("/bank-accounts")
def banks(db: Session = DB, _: CurrentUser = Depends(require("accounts.bank:read"))):
    return [svc.bank_dict(b) for b in db.scalars(select(BankAccount).order_by(BankAccount.id))]


@router.post("/bank-accounts/{bid}/statements")
async def import_statement(bid: int, file: UploadFile = File(...), db: Session = DB, cu: CurrentUser = Depends(require("accounts.bank:reconcile"))):
    raw = await file.read(5_000_001)
    if len(raw) > 5_000_000:
        raise HTTPException(413, "Statement too large (5 MB max)")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "Statement must be UTF-8 CSV")
    return await run_in_threadpool(lambda: run_tx(db, lambda d: svc.import_statement(d, cu, bid, content), name="accounts.bank.import"))


@router.get("/bank-accounts/{bid}/transactions")
def transactions(bid: int, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = DB,
                 _: CurrentUser = Depends(require("accounts.bank:read"))):
    q = select(BankTransaction).where(BankTransaction.bank_account_id == bid).order_by(BankTransaction.txn_date, BankTransaction.id).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(BankTransaction.status == status)
    return {"items": [svc.txn_dict(t) for t in db.scalars(q)], "page": page}


@router.get("/bank-accounts/{bid}/suggestions")
def suggestions(bid: int, window_days: int = Query(7, ge=0, le=60), db: Session = DB, _: CurrentUser = Depends(require("accounts.bank:read"))):
    return svc.suggestions(db, bid, window_days)


@router.get("/bank-accounts/{bid}/reconciliation")
def reconciliation(bid: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.bank:read"))):
    return svc.reconciliation(db, bid)


@router.post("/bank-transactions/{tid}/match")
def match(tid: int, body: MatchIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.bank:reconcile"))):
    return run_tx(db, lambda d: svc.match(d, cu, tid, body.journal_line_id), name="accounts.bank.match")


@router.post("/bank-transactions/{tid}/unmatch")
def unmatch(tid: int, db: Session = DB, cu: CurrentUser = Depends(require("accounts.bank:reconcile"))):
    return run_tx(db, lambda d: svc.unmatch(d, cu, tid), name="accounts.bank.unmatch")


@router.post("/bank-transactions/{tid}/book")
def book(tid: int, body: BookIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.bank:reconcile", "accounts.journal:post"))):
    return run_tx(db, lambda d: svc.book(d, cu, tid, body.counter_account_code, body.narration), name="accounts.bank.book")


@router.post("/bank-transactions/{tid}/ignore")
def ignore(tid: int, body: IgnoreIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.bank:reconcile"))):
    return run_tx(db, lambda d: svc.ignore(d, cu, tid, body.reason), name="accounts.bank.ignore")


# ---- debit notes
@router.post("/supplier-invoices/{inv_id}/debit-notes", status_code=201)
def debit_note(inv_id: int, body: DebitNoteIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("accounts.debit_note:post"))):
    return idempotent_post(db, cu, scope="accounts.debit_note.post", key=key, payload={"inv": inv_id, **body.model_dump(mode="json")},
                           effect=lambda d: (201, (r := svc.post_debit_note(d, cu, invoice_id=inv_id, amount_ex_tax=body.amount_ex_tax, tax_rate=body.tax_rate,
                                                                            reason_type=body.reason_type, reason=body.reason)), "debit_note", r["id"]))


@router.get("/debit-notes")
def debit_notes(db: Session = DB, _: CurrentUser = Depends(require("accounts.debit_note:read"))):
    return [svc.debit_note_dict(d) for d in db.scalars(select(DebitNote).order_by(DebitNote.id.desc()).limit(200))]


# ---- marketplace settlement posting
@router.post("/settlements/{sid}/post")
def post_settlement(sid: int, body: PostSettlementIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("accounts.settlement:post"))):
    return idempotent_post(db, cu, scope="accounts.settlement.post", key=key, payload={"sid": sid, **body.model_dump()},
                           effect=lambda d: (200, svc.post_settlement(d, cu, sid, body.bank_account_code), "marketplace_settlement", sid))


# ---- reports
@router.get("/reports/ageing/{kind}")
def ageing(kind: Literal["receivables", "payables"], as_of: date | None = None, db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    return reports.ageing(db, kind, as_of or date.today())


@router.get("/reports/statement/{party_type}/{party_id}")
def statement(party_type: Literal["customer", "supplier"], party_id: int, start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    return reports.statement(db, party_type, party_id, start, end)


@router.get("/reports/gst-summary")
def gst(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("accounts.gst:read"))):
    return reports.gst_summary(db, start, end)


@router.get("/reports/profit-and-loss")
def pnl(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    return reports.profit_and_loss(db, start, end)


@router.get("/reports/balance-sheet")
def bs(as_of: date | None = None, db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    return reports.balance_sheet(db, as_of or date.today())


@router.get("/reports/product-costing")
def costing(start: date, end: date, db: Session = DB, _: CurrentUser = Depends(require("accounts.report:read"))):
    return reports.product_costing(db, start, end)
