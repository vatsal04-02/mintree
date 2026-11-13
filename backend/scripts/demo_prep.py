"""Guarantee the demo's PRECONDITIONS (docs/DEMO_RUNBOOK.md "Preconditions"), idempotently, through the real API. Run by `make demo-check` before the snapshot.

    python -m scripts.demo_prep

  * three deals in Negotiation whose quotation is already accepted ("Demo deal A/B/C"): the presenter drags one to Won (the system refuses Won without an accepted quotation)
  * one purchase order waiting for approval (Approvals count and a notification are never empty)
  * one finished-goods batch held in quarantine with an open inspection (Quality "Needs attention" is orange)
Existing records are left alone; re-running adds nothing."""
import logging
from datetime import timedelta

from sqlalchemy import text

from app.core.db import SessionLocal
from scripts.demo.core import Ctx, load_refs
from scripts.demo.guard import check_demo_target
from app.core.config import get_settings


PINS = {"27": "411001", "33": "600001", "29": "560001", "07": "110001", "24": "380001", "06": "122001", "36": "500001", "19": "700001", "09": "226001", "08": "302001", "32": "682001", "23": "452001"}


def prep_einvoice(db) -> int:
    """Demo data so the e-invoice flow can be shown: the company gets a valid GSTIN, address with a PIN code and state; the first customers get well-formed GSTINs (correct check
    character) and a billing address with a PIN. Master data only: nothing is invoiced or registered. Safe to repeat."""
    from app.accounts.einvoice import make_gstin
    from app.accounts.reports import state_code
    for k, v in (("company.gstin", make_gstin("27", "AAACM1234A")), ("company.state_code", "27"), ("company.address", "Plot 4, MIDC Industrial Area, Pune 411019")):
        db.execute(text("INSERT INTO company_settings (key, value) VALUES (:k, CAST(:v AS jsonb)) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"), {"k": k, "v": __import__("json").dumps(v)})
    n = 0
    for cid, state, addr in db.execute(text("SELECT id, state, billing_address FROM customers WHERE customer_type = 'B2B' AND is_active AND (gstin IS NULL OR gstin = '') ORDER BY id LIMIT 60")).all():
        code = state_code(None, state) or "27"
        pan = f"MN{chr(65 + cid % 26)}{chr(65 + (cid // 26) % 26)}{chr(65 + (cid // 676) % 26)}{cid % 10000:04d}{chr(65 + cid % 25)}"
        pin = PINS.get(code, "110001")
        db.execute(text("UPDATE customers SET gstin = :g, billing_address = :a WHERE id = :c"), {"g": make_gstin(code, pan), "a": f"{addr or 'Registered office'}, PIN {pin}" if " PIN " not in (addr or "") else addr, "c": cid})
        n += 1
    db.commit()
    return n


def main() -> None:
    s = get_settings()
    check_demo_target(s.environment, s.database_url)
    logging.disable(logging.ERROR)
    c = Ctx()
    load_refs(c)
    c.step_key = "prep"
    out = {}
    with SessionLocal() as db:
        q = lambda sql, **p: db.execute(text(sql), p).scalar()  # noqa: E731
        out["einvoice_parties"] = prep_einvoice(db)
        sales = c.u("sales")
        cust = q("SELECT id FROM customers WHERE code LIKE 'BK-CUS-%' ORDER BY id LIMIT 1") or q("SELECT id FROM customers ORDER BY id LIMIT 1")
        fg = q("SELECT id FROM items WHERE item_code = 'FG-BW-OUD-200'")
        have = q("SELECT count(*) FROM opportunities WHERE name LIKE 'Demo deal %' AND stage = 'NEGOTIATION'")
        made = 0
        import time as _t
        for k in range(max(0, 3 - have)):                      # top up to three deals waiting in Negotiation (earlier runs consume them)
            tag, value = f"{_t.strftime('%d%H%M')}-{k + 1}", (380000, 215000, 640000)[k % 3]
            o = c.ok(sales.post("/api/crm/opportunities", json={"name": f"Demo deal {tag}", "customer_id": cust, "expected_value": value, "expected_close": c.d(21)}))
            for stage in ("QUALIFIED", "PROPOSAL", "NEGOTIATION"):
                o = c.ok(sales.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": stage, "expected_version": o["version"]}))
            qt = c.ok(sales.post("/api/sales/quotations", json={"customer_id": cust, "valid_until": c.d(30), "notes": "Demo", "lines": [{"item_id": fg, "qty": "100", "unit_price": "560.00"}]}))
            o = c.ok(sales.post(f"/api/crm/opportunities/{o['id']}/quotation", json={"quotation_id": qt["id"], "expected_version": o["version"]}))
            qt = c.ok(sales.post(f"/api/sales/quotations/{qt['id']}/send", json={"expected_version": qt["version"]}))
            c.ok(sales.post(f"/api/sales/quotations/{qt['id']}/accept", json={"expected_version": qt["version"]}))
            made += 1
        out["deals_ready"] = have + made
        buyer = c.u("buyer")
        if not q("SELECT count(*) FROM purchase_orders WHERE status = 'PENDING_APPROVAL'"):
            sup = q("SELECT id FROM suppliers WHERE approval_status = 'APPROVED' AND is_active ORDER BY id LIMIT 1")
            iid = q("SELECT id FROM items WHERE item_code = 'RM-ALOE'")
            po = c.ok(buyer.post("/api/procurement/purchase-orders", json={"supplier_id": sup, "expected_date": c.d(6), "notes": "Demo: waiting for approval", "items": [{"item_id": iid, "qty": "250", "unit_price": "88.00"}]}))
            c.ok(buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
            out["po_pending"] = po["number"]
        if not q("SELECT count(*) FROM inventory_batches b JOIN items i ON i.id = b.item_id WHERE b.qc_status = 'QUARANTINE' AND i.kind = 'FG' AND b.created_at > now() - interval '400 days'"):
            from scripts.demo.catalogue import BOMS
            from scripts.demo.steps import run_order
            code = "FG-DETAN-60"
            res = run_order(c, c.refs["production_finished_goods"]["boms"][code], str(BOMS[code][0]), actual=str(BOMS[code][0] - 3))
            out["batch_in_qc"] = res["batch"]
    # A believable inbox: a person who has worked with the system for a year has read most alerts. The bulk history notified approvers of ~2,000 purchase orders that were approved
    # long ago; leave only each user's newest 12 unread, spread the older ones over the past two weeks (Today / Yesterday / Earlier), and mark them read.
    with SessionLocal() as db:
        n = db.execute(text("""
            WITH ranked AS (SELECT id, row_number() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn FROM user_notifications WHERE read_at IS NULL)
            UPDATE user_notifications u SET read_at = u.created_at + interval '5 minutes',
                   created_at = u.created_at - make_interval(hours => 6 + (r.rn % 330)::int)
              FROM ranked r WHERE r.id = u.id AND r.rn > 12""")).rowcount
        db.commit()
    out["notifications_marked_read"] = n
    print({"demo_prep": "ok", **out})


if __name__ == "__main__":
    main()
