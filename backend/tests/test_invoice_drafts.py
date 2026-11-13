"""Customer invoice drafts: draft -> validate -> post, with money rules.

A draft is a preview and never touches the ledger; posting runs the real dispatch posting (gapless number, balanced journal, stock out, shipment)."""
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_dispatch import advance, integrity, make_customer, make_fg, new_so, shop  # noqa: F401  (fixtures/helpers)
from tests.test_inventory_procurement import N
from tests.test_production import k, plant  # noqa: F401


def ready_customer(db):
    cust = make_customer()
    db.execute(text("UPDATE customers SET billing_address = '12 MG Road, Pune', gstin = '27ABCDE1234F1Z5' WHERE id = :c"), {"c": cust})
    return cust


def ledger(db):
    return (db.execute(text("SELECT count(*) FROM journal_entries")).scalar(), db.execute(text("SELECT count(*) FROM sales_invoices")).scalar(),
            db.execute(text("SELECT COALESCE(sum(credit - debit),0) FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = '4000'")).scalar())


def draft_for(shop, o, lines=None):
    r = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/invoice-drafts", json={} if lines is None else {"lines": lines})
    assert r.status_code == 201, r.text
    return r.json()


def test_drafting_changes_nothing_in_the_ledger_and_shows_exactly_what_posting_bills(shop, db):
    f, cust = make_fg(shop, 100, 10), ready_customer(db)
    o = new_so(shop, f, cust)                                              # 30 x 25 = 750 + 18% = 135 -> 885
    packed = advance(shop, o)
    el = shop["disp"].get(f"/api/dispatch/orders/{o['id']}/invoice-eligibility").json()
    assert el["can_draft"] and N(el["lines"][0]["remaining"]) == 30
    before = ledger(db)
    d = draft_for(shop, o)
    assert (d["status"], N(d["subtotal"]), N(d["tax_total"]), N(d["total"])) == ("DRAFT", 750, 135, 885)
    assert d["invoice_number"] is None and "nothing is posted" in d["note"]
    assert ledger(db) == before, "a draft creates no journal entry, no invoice number and no revenue"
    v = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/validate").json()
    assert v["valid"] and v["draft"]["status"] == "VALIDATED"
    p = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=k(), json={})
    assert p.status_code == 200, p.text
    body = p.json()
    assert body["invoice"]["number"].startswith("SI/") and N(body["invoice"]["total"]) == N(d["total"]), "the posted invoice is exactly the draft"
    assert body["draft"]["status"] == "POSTED" and body["draft"]["posted_invoice_id"] == body["invoice"]["id"] and body["order_status"] == "DISPATCHED"
    entries, invoices, revenue = ledger(db)
    assert invoices == before[1] + 1 and entries > before[0] and revenue == before[2] + 750, "revenue is recognised only on posting"
    t = shop["acct"].get("/api/accounts/reports/trial-balance").json()
    assert t["balanced"]
    pdf = shop["acct"].get(f"/api/dispatch/invoices/{body['invoice']['id']}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    integrity(db)


def test_a_posted_draft_cannot_be_posted_again_and_a_retry_replays(shop, db):
    f, cust = make_fg(shop, 100, 10), ready_customer(db)
    o = new_so(shop, f, cust)
    advance(shop, o)
    d = draft_for(shop, o)
    shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/validate")
    key = k()
    first = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=key, json={})
    replay = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=key, json={})
    assert first.status_code == 200 and replay.status_code == 200 and replay.json()["invoice"]["number"] == first.json()["invoice"]["number"]
    again = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=k(), json={})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_posted"
    assert db.execute(text("SELECT count(*) FROM sales_invoices WHERE so_id = :o"), {"o": o["id"]}).scalar() == 1, "no duplicate invoice"
    integrity(db)


def test_partial_invoicing_never_exceeds_the_unshipped_quantity_and_the_invoices_add_up_to_the_order(shop, db):
    f, cust = make_fg(shop, 100, 10), ready_customer(db)
    o = new_so(shop, f, cust)
    advance(shop, o)
    line = shop["disp"].get(f"/api/dispatch/orders/{o['id']}/invoice-eligibility").json()["lines"][0]
    over = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/invoice-drafts", json={"lines": [{"so_item_id": line["so_item_id"], "qty": "31"}]})
    assert over.status_code == 409 and over.json()["detail"]["code"] == "exceeds_remaining"
    d1 = draft_for(shop, o, [{"so_item_id": line["so_item_id"], "qty": "10"}])
    assert (N(d1["subtotal"]), N(d1["tax_total"]), N(d1["total"])) == (250, 45, 295)
    assert shop["disp"].post(f"/api/dispatch/orders/{o['id']}/invoice-drafts", json={}).json()["detail"]["code"] == "draft_exists", "one open draft per order"
    shop["disp"].post(f"/api/dispatch/invoice-drafts/{d1['id']}/validate")
    r1 = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d1['id']}/post", headers=k(), json={}).json()
    assert r1["order_status"] == "PARTIALLY_DISPATCHED" and N(r1["invoice"]["total"]) == 295
    el = shop["disp"].get(f"/api/dispatch/orders/{o['id']}/invoice-eligibility").json()
    assert N(el["lines"][0]["remaining"]) == 20
    d2 = draft_for(shop, o)
    assert N(d2["total"]) == 590, "the rest of the order: 20 x 25 + GST"
    shop["disp"].post(f"/api/dispatch/invoice-drafts/{d2['id']}/validate")
    r2 = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d2['id']}/post", headers=k(), json={}).json()
    assert r2["order_status"] == "DISPATCHED" and N(r1["invoice"]["total"]) + N(r2["invoice"]["total"]) == 885, "invoices add up to the order total"
    assert shop["disp"].get(f"/api/dispatch/orders/{o['id']}/invoice-eligibility").json()["lines"] == []
    integrity(db)


def test_a_draft_must_be_validated_and_the_order_packed_before_posting(shop, db):
    f, cust = make_fg(shop, 100, 10), ready_customer(db)
    o = new_so(shop, f, cust)
    shop["disp"].post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": o["version"]})
    d = draft_for(shop, o)
    assert shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=k(), json={}).json()["detail"]["code"] == "not_validated"
    v = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/validate")
    assert v.status_code == 409 and v.json()["detail"]["code"] == "draft_not_valid" and any("PACKED" in e for e in v.json()["detail"]["errors"])
    assert shop["disp"].get(f"/api/dispatch/invoice-drafts/{d['id']}").json()["status"] == "DRAFT"


def test_validation_blocks_a_registered_buyer_without_an_address_and_only_warns_for_an_unregistered_one(shop, db):
    f = make_fg(shop, 100, 10)
    registered = make_customer()
    db.execute(text("UPDATE customers SET gstin = '27ABCDE1234F1Z5' WHERE id = :c"), {"c": registered})
    o = new_so(shop, f, registered)
    advance(shop, o)
    d = draft_for(shop, o)
    v = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/validate")
    assert v.status_code == 409 and any("billing address" in e for e in v.json()["detail"]["errors"]), "a tax invoice to a GST-registered buyer needs the buyer's address"
    plain = make_customer()                                                 # no GSTIN, no address: allowed, with a warning
    f2 = make_fg(shop, 100, 10)
    o2 = new_so(shop, f2, plain)
    advance(shop, o2)
    d2 = draft_for(shop, o2)
    ok = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d2['id']}/validate")
    assert ok.status_code == 200 and ok.json()["valid"] and any("billing address" in w for w in ok.json()["warnings"])


def test_a_cancelled_draft_posts_nothing_and_a_new_one_can_follow(shop, db):
    f, cust = make_fg(shop, 100, 10), ready_customer(db)
    o = new_so(shop, f, cust)
    advance(shop, o)
    before = ledger(db)
    d = draft_for(shop, o)
    c = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/cancel", json={"reason": "wrong quantity"})
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    assert ledger(db) == before
    assert shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=k(), json={}).status_code == 409
    assert shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/cancel", json={}).status_code == 409
    d2 = draft_for(shop, o)
    assert d2["id"] != d["id"]


def test_payment_after_posting_settles_the_invoice(shop, db):
    f, cust = make_fg(shop, 100, 10), ready_customer(db)
    o = new_so(shop, f, cust)
    advance(shop, o)
    d = draft_for(shop, o)
    shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/validate")
    inv = shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=k(), json={}).json()["invoice"]
    pay = shop["acct"].post("/api/accounts/payments", headers=k(), json={"direction": "RECEIVED", "party_id": cust, "amount": "885.00", "payment_date": "2026-11-12", "method": "UPI",
                                                                          "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": "885.00"}]})
    assert pay.status_code == 201, pay.text
    assert db.execute(text("SELECT status, amount_paid FROM sales_invoices WHERE id = :i"), {"i": inv["id"]}).one() == ("PAID", Decimal("885.00"))
    integrity(db)


def test_who_may_draft_and_post(shop, db, login):
    f, cust = make_fg(shop, 100, 10), ready_customer(db)
    o = new_so(shop, f, cust)
    advance(shop, o)
    operator = login("production_operator")
    assert operator.post(f"/api/dispatch/orders/{o['id']}/invoice-drafts", json={}).status_code == 403
    d = draft_for(shop, o)
    assert operator.get(f"/api/dispatch/invoice-drafts/{d['id']}").status_code == 403
    shop["disp"].post(f"/api/dispatch/invoice-drafts/{d['id']}/validate")
    sales = shop["sales"]                                                   # sales operators draft nothing and post nothing
    assert sales.post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=k(), json={}).status_code == 403
    assert shop["acct"].post(f"/api/dispatch/invoice-drafts/{d['id']}/post", headers=k(), json={}).status_code == 403, "accounts cannot ship goods, so they cannot post an invoice that ships them"
    assert shop["acct"].get(f"/api/dispatch/invoice-drafts/{d['id']}").status_code == 200


@pytest.mark.parametrize("path", ["invoice-drafts", "orders/1/invoice-eligibility"])
def test_drafts_need_a_session(anon, path):
    assert anon.get(f"/api/dispatch/{path}").status_code == 401
