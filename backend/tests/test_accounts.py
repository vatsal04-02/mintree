"""Accounts: balanced-by-construction journals, DB-level guarantees, GL postings from receipts, three-way match, payments."""
import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import engine
from app.core.uow import run_tx
from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_inventory_procurement import N, po_to_approved, receive
from app.core.clock import business_today  # noqa: E402


def integrity(db):
    bad = [c for c in run_checks(db.connection())["checks"] if not c["ok"]]
    assert not bad, bad


@pytest.fixture
def fin(login, admin):
    return {"buyer": login("purchase_manager"), "stores": login("stores_manager"), "acct": login("accounts_manager"), "admin": admin}


def tb(client):
    return {a["code"]: N(a["balance"]) for a in client.get("/api/accounts/reports/trial-balance").json()["accounts"]}


def test_unbalanced_journal_is_rejected_by_the_service(fin):
    r = fin["acct"].post("/api/accounts/journals", headers={"Idempotency-Key": "key-jv-unbal-0001"}, json={
        "entry_date": str(business_today()), "description": "unbalanced test", "lines": [
            {"account_code": "1000", "debit": "100"}, {"account_code": "3000", "credit": "99.99"}]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "unbalanced_journal"


def test_database_refuses_an_unbalanced_journal_even_if_application_code_is_wrong(db):
    """Bypass the service entirely: raw SQL inserts an unbalanced entry. The deferred trigger must stop the COMMIT."""
    with engine.connect() as c:
        c.execute(text("INSERT INTO journal_entries(number, entry_date, period_id, description, source_type, source_id, total_debit, total_credit) "
                       "SELECT 'JV/RAW/1', business_today(), id, 'raw', 'raw', 1, 100, 100 FROM accounting_periods WHERE business_today() BETWEEN start_date AND end_date"))
        c.execute(text("INSERT INTO journal_lines(entry_id, line_no, account_id, debit, credit) SELECT currval(pg_get_serial_sequence('journal_entries','id')), 1, id, 100, 0 FROM accounts WHERE code='1000'"))
        c.execute(text("INSERT INTO journal_lines(entry_id, line_no, account_id, debit, credit) SELECT currval(pg_get_serial_sequence('journal_entries','id')), 2, id, 0, 90 FROM accounts WHERE code='3000'"))
        with pytest.raises(DBAPIError, match="unbalanced"):
            c.commit()
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM journal_entries WHERE number='JV/RAW/1'")).scalar() == 0


def test_journals_are_immutable(fin, db):
    r = fin["acct"].post("/api/accounts/journals", headers={"Idempotency-Key": "key-jv-imm-0001"}, json={
        "entry_date": str(business_today()), "description": "immutable test", "lines": [{"account_code": "1000", "debit": "50"}, {"account_code": "3000", "credit": "50"}]})
    assert r.status_code == 201
    for sql in ("UPDATE journal_entries SET description='x'", "DELETE FROM journal_lines"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db.begin_nested():
                db.execute(text(sql))
    trg = {r[0] for r in db.execute(text("SELECT tgname FROM pg_trigger WHERE tgrelid IN ('journal_lines'::regclass,'journal_entries'::regclass)"))}
    assert {"journal_lines_no_truncate", "journal_entries_no_truncate", "journal_lines_balanced"} <= trg


def test_goods_receipt_posts_inventory_and_grni_and_valuation(fin, db):
    m = mk_master("ACC")
    before = tb(fin["acct"])
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty="100", price="12.50")
    assert receive(fin["stores"], po, m["wh1"], "100", "DN-AC-1", "key-acc-gr-0001").status_code == 201
    after = tb(fin["acct"])
    assert after.get("1300", 0) - before.get("1300", 0) == N("1250.00")                  # Dr Inventory RM/PM
    assert after.get("2100", 0) - before.get("2100", 0) == N("-1250.00")                 # Cr GRNI
    assert db.execute(text("SELECT qty, value FROM item_valuation WHERE item_id=:i"), {"i": m["item_id"]}).one() == (100, N("1250.00"))
    assert fin["acct"].get("/api/accounts/reports/trial-balance").json()["balanced"] is True
    integrity(db)


def test_supplier_invoice_three_way_match_variance_and_duplicates(fin, db):
    m = mk_master("ACC")
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty="100", price="10")
    receive(fin["stores"], po, m["wh1"], "60", "DN-AC-2", "key-acc-gr-0002")
    li = lambda q, p: {"po_id": po["id"], "supplier_invoice_no": "SUP-INV-77", "invoice_date": str(business_today()),
                       "lines": [{"po_item_id": po["items"][0]["id"], "qty": q, "unit_price": p, "tax_rate": "18"}]}
    over = fin["acct"].post("/api/accounts/supplier-invoices", headers={"Idempotency-Key": "key-acc-si-0001"}, json=li("61", "10"))
    assert over.status_code == 409 and over.json()["detail"]["code"] == "invoice_exceeds_receipt"          # received 60, billed 61
    b = tb(fin["acct"])
    ok = fin["acct"].post("/api/accounts/supplier-invoices", headers={"Idempotency-Key": "key-acc-si-0002"}, json=li("60", "10.50"))
    assert ok.status_code == 201, ok.text
    inv = ok.json()
    assert (N(inv["subtotal"]), N(inv["tax_total"]), N(inv["total"])) == (N("630.00"), N("113.40"), N("743.40"))
    a = tb(fin["acct"])
    d = lambda c: a.get(c, 0) - b.get(c, 0)
    assert d("2100") == N("600.00") and d("5300") == N("30.00") and d("1500") == N("113.40") and d("2000") == N("-743.40")   # variance 60 x 0.50
    assert receive(fin["stores"], po, m["wh1"], "20", "DN-AC-2b", "key-acc-gr-0002b").status_code == 201   # more received -> match not the blocker
    dup = fin["acct"].post("/api/accounts/supplier-invoices", headers={"Idempotency-Key": "key-acc-si-0003"}, json=li("1", "10"))
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "duplicate_supplier_invoice"
    replay = fin["acct"].post("/api/accounts/supplier-invoices", headers={"Idempotency-Key": "key-acc-si-0002"}, json=li("60", "10.50"))
    assert replay.json() == inv and replay.headers["idempotent-replayed"] == "true"
    integrity(db)


def test_payments_allocate_partially_then_fully_and_cannot_overpay(fin, db):
    m = mk_master("ACC")
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty="10", price="100")
    receive(fin["stores"], po, m["wh1"], "10", "DN-AC-3", "key-acc-gr-0003")
    inv = fin["acct"].post("/api/accounts/supplier-invoices", headers={"Idempotency-Key": "key-acc-si-0010"}, json={
        "po_id": po["id"], "supplier_invoice_no": "SUP-INV-90", "invoice_date": str(business_today()),
        "lines": [{"po_item_id": po["items"][0]["id"], "qty": "10", "unit_price": "100", "tax_rate": "0"}]}).json()
    pay = lambda amt, k, alloc=None: fin["acct"].post("/api/accounts/payments", headers={"Idempotency-Key": k}, json={
        "direction": "PAID", "party_id": m["supplier_id"], "amount": amt, "payment_date": str(business_today()), "method": "BANK_TRANSFER", "bank_account_code": "1100",
        "allocations": [{"invoice_id": inv["id"], "amount": alloc or amt}]})
    assert pay("400", "key-acc-pay-0001", "300").json()["detail"]["code"] == "allocation_mismatch"
    assert pay("400", "key-acc-pay-0002").status_code == 201
    assert fin["acct"].get(f"/api/accounts/supplier-invoices/{inv['id']}").json()["status"] == "PARTIAL"
    over = pay("700", "key-acc-pay-0003")
    assert over.status_code == 409 and over.json()["detail"]["code"] == "overpayment"
    assert pay("600", "key-acc-pay-0004").status_code == 201
    final = fin["acct"].get(f"/api/accounts/supplier-invoices/{inv['id']}").json()
    assert final["status"] == "PAID" and N(final["outstanding"]) == 0
    assert pay("1", "key-acc-pay-0005").json()["detail"]["code"] == "overpayment"
    integrity(db)


def test_closed_period_blocks_postings_and_rolls_everything_back(fin, db):
    m = mk_master("ACC")
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty="10", price="5")
    pid = next(p["id"] for p in fin["acct"].get("/api/accounts/periods").json()
               if p["start_date"] <= str(business_today()) <= p["end_date"])
    assert fin["acct"].post(f"/api/accounts/periods/{pid}/close").status_code == 200
    stock_before = db.execute(text("SELECT count(*) FROM stock_ledger")).scalar()
    r = receive(fin["stores"], po, m["wh1"], "10", "DN-AC-4", "key-acc-gr-0004")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "period_closed"
    assert db.execute(text("SELECT count(*) FROM stock_ledger")).scalar() == stock_before, "no stock may move into a closed period"
    assert fin["acct"].post(f"/api/accounts/periods/{pid}/close").json()["detail"]["code"] == "already_closed"


def test_manual_journal_permissions_and_reversal_once(fin, db):
    body = {"entry_date": str(business_today()), "description": "owner contribution", "lines": [{"account_code": "1100", "debit": "1000"}, {"account_code": "3000", "credit": "1000"}]}
    assert fin["buyer"].post("/api/accounts/journals", headers={"Idempotency-Key": "key-jv-perm-0001"}, json=body).status_code == 403
    j = fin["acct"].post("/api/accounts/journals", headers={"Idempotency-Key": "key-jv-perm-0002"}, json=body)
    assert j.status_code == 201
    jid = j.json()["id"]
    assert fin["acct"].post(f"/api/accounts/journals/{jid}/reverse", headers={"Idempotency-Key": "key-jv-rev-0001"}, json={"reason": "posted in error"}).status_code == 201
    again = fin["acct"].post(f"/api/accounts/journals/{jid}/reverse", headers={"Idempotency-Key": "key-jv-rev-0002"}, json={"reason": "second try"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "duplicate_posting"
    bad = fin["acct"].post("/api/accounts/journals", headers={"Idempotency-Key": "key-jv-perm-0003"}, json={**body, "lines": [{"account_code": "9999", "debit": "1"}, {"account_code": "3000", "credit": "1"}]})
    assert bad.status_code == 422
    integrity(db)


def test_gapless_counter_has_no_gaps_or_duplicates_under_concurrency_and_rollback(committed, uid):
    from app.accounts import service as acct
    name, got, errs = uid("ctr"), [], []

    def take(i):
        try:
            with committed() as db:
                def work(d):
                    n = acct.next_gapless(d, name)
                    if i % 5 == 0:
                        raise RuntimeError("abort after taking a number")       # rollback must hand the number back
                    return n
                try:
                    got.append(run_tx(db, work, name="t"))
                except RuntimeError:
                    pass
        except Exception as e:  # pragma: no cover
            errs.append(e)
    ths = [threading.Thread(target=take, args=(i,)) for i in range(40)]
    [t.start() for t in ths]; [t.join(30) for t in ths]
    assert not errs, errs
    assert sorted(got) == list(range(1, len(got) + 1)), "numbers committed are exactly 1..n: no gaps, no duplicates"
    assert len(got) == 32
