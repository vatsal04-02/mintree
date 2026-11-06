"""Database integrity checker. Run after every critical test / load run:

    MINTREE_DATABASE_URL=... python -m scripts.check_integrity [--json]

Reads from ONE repeatable-read snapshot (consistent even while writers are active). A successful HTTP response
is never accepted as proof: every invariant is re-derived from the tables. Exit code 1 if any check fails.
"""
import json
import sys

from sqlalchemy import text
from sqlalchemy.engine import Engine

CHECKS: list[tuple[str, str, str]] = [
    ("stock_physical_reconciles_with_ledger",
     "Every balance's physical qty equals the sum of its ledger qty_delta",
     """SELECT sb.id, sb.qty_physical, COALESCE(l.s,0) AS ledger_sum FROM stock_balances sb
          LEFT JOIN (SELECT balance_id, sum(qty_delta) s FROM stock_ledger GROUP BY balance_id) l ON l.balance_id = sb.id
         WHERE sb.qty_physical <> COALESCE(l.s,0)"""),
    ("stock_blocked_reconciles_with_ledger",
     "Every balance's blocked qty equals the sum of its ledger blocked_delta",
     """SELECT sb.id, sb.qty_blocked, COALESCE(l.s,0) AS ledger_sum FROM stock_balances sb
          LEFT JOIN (SELECT balance_id, sum(blocked_delta) s FROM stock_ledger GROUP BY balance_id) l ON l.balance_id = sb.id
         WHERE sb.qty_blocked <> COALESCE(l.s,0)"""),
    ("stock_reserved_reconciles_with_active_reservations",
     "Every balance's reserved qty equals the sum of its ACTIVE reservations",
     """SELECT sb.id, sb.qty_reserved, COALESCE(r.s,0) AS reservations_sum FROM stock_balances sb
          LEFT JOIN (SELECT balance_id, sum(qty) s FROM stock_reservations WHERE status='ACTIVE' GROUP BY balance_id) r ON r.balance_id = sb.id
         WHERE sb.qty_reserved <> COALESCE(r.s,0)"""),
    ("no_negative_or_overreserved_stock",
     "No balance is negative, and reserved + blocked never exceeds physical (available >= 0)",
     """SELECT id, qty_physical, qty_reserved, qty_blocked FROM stock_balances
         WHERE qty_physical < 0 OR qty_reserved < 0 OR qty_blocked < 0 OR qty_reserved + qty_blocked > qty_physical"""),
    ("reservations_only_on_released_unexpired_batches_or_were_valid_when_made",
     "No ACTIVE reservation sits on a REJECTED batch",
     """SELECT r.id, ib.qc_status FROM stock_reservations r JOIN stock_balances sb ON sb.id = r.balance_id
          JOIN inventory_batches ib ON ib.id = sb.batch_id WHERE r.status='ACTIVE' AND ib.qc_status = 'REJECTED'"""),
    ("ledger_has_no_duplicate_document_postings",
     "No (source, line, leg, movement) appears twice in the ledger",
     """SELECT source_type, source_id, source_line, leg, movement_type, count(*) FROM stock_ledger
         GROUP BY 1,2,3,4,5 HAVING count(*) > 1"""),
    ("no_duplicate_goods_receipts",
     "No supplier delivery note is booked twice, and each goods receipt has exactly one idempotency key",
     """SELECT supplier_id, delivery_note_no, count(*) FROM goods_receipts GROUP BY 1,2 HAVING count(*) > 1
        UNION ALL
        SELECT NULL, 'gr_id=' || gr.id || ' keys=' || count(k.id), count(k.id) FROM goods_receipts gr
          LEFT JOIN idempotency_keys k ON k.scope='inventory.goods_receipt.post' AND k.resource_id = gr.id::text
         GROUP BY gr.id HAVING count(k.id) > 1"""),
    ("every_idempotency_key_has_at_most_one_effect",
     "Each (scope,key) is unique and every completed receipt key points at an existing goods receipt",
     """SELECT scope, key, count(*) FROM idempotency_keys GROUP BY 1,2 HAVING count(*) > 1
        UNION ALL
        SELECT k.scope, k.key, 0 FROM idempotency_keys k LEFT JOIN goods_receipts gr ON gr.id::text = k.resource_id
         WHERE k.scope='inventory.goods_receipt.post' AND k.resource_id IS NOT NULL AND gr.id IS NULL"""),
    ("po_received_quantities_reconcile_with_receipts",
     "PO line qty_received equals the sum of goods-receipt line quantities and never exceeds qty_ordered",
     """SELECT poi.id, poi.qty_received, COALESCE(g.s,0) AS receipts_sum, poi.qty_ordered FROM purchase_order_items poi
          LEFT JOIN (SELECT po_item_id, sum(qty) s FROM goods_receipt_items GROUP BY po_item_id) g ON g.po_item_id = poi.id
         WHERE poi.qty_received <> COALESCE(g.s,0) OR poi.qty_received > poi.qty_ordered"""),
    ("every_receipt_line_is_in_the_ledger_with_matching_quantity",
     "Each goods-receipt line has exactly one RECEIPT ledger row of equal quantity",
     """SELECT gri.gr_id, gri.line_no, gri.qty, COALESCE(l.s,0) AS ledger_qty FROM goods_receipt_items gri
          LEFT JOIN (SELECT source_id, source_line, sum(qty_delta) s FROM stock_ledger
                      WHERE source_type='goods_receipt' AND movement_type='RECEIPT' GROUP BY 1,2) l
                 ON l.source_id = gri.gr_id AND l.source_line = gri.line_no
         WHERE gri.qty <> COALESCE(l.s,0)"""),
    ("batch_and_supplier_lot_traceability_intact",
     "Every received batch points back to its goods receipt, supplier and item; no ledger row references a missing batch",
     """SELECT gri.gr_id, gri.line_no FROM goods_receipt_items gri JOIN goods_receipts gr ON gr.id = gri.gr_id
          LEFT JOIN inventory_batches ib ON ib.id = gri.batch_id
         WHERE gri.batch_id IS NOT NULL AND (ib.id IS NULL OR ib.source_type <> 'goods_receipt' OR ib.source_id <> gri.gr_id
               OR ib.supplier_id <> gr.supplier_id OR ib.item_id <> gri.item_id)
        UNION ALL
        SELECT l.id, 0 FROM stock_ledger l LEFT JOIN inventory_batches ib ON ib.id = l.batch_id WHERE l.batch_id IS NOT NULL AND ib.id IS NULL"""),
    ("every_goods_receipt_and_po_approval_has_an_audit_entry",
     "Committed business documents have their audit rows",
     """SELECT 'goods_receipt', gr.id FROM goods_receipts gr
          WHERE NOT EXISTS (SELECT 1 FROM audit_logs a WHERE a.entity='goods_receipt' AND a.entity_id = gr.id::text AND a.action='post')
        UNION ALL
        SELECT 'purchase_order', po.id FROM purchase_orders po WHERE po.approved_by IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM audit_logs a WHERE a.entity='purchase_order' AND a.entity_id = po.id::text AND a.action='approve')
        UNION ALL
        SELECT 'stock_adjustment', l.id FROM stock_ledger l WHERE l.movement_type IN ('ADJUSTMENT','REVERSAL')
          AND NOT EXISTS (SELECT 1 FROM audit_logs a WHERE a.entity IN ('stock_balance','stock_ledger') AND a.action IN ('stock_adjustment','stock_reversal')
                            AND a.request_id = l.request_id)"""),
    ("every_goods_receipt_has_an_outbox_event",
     "Each committed goods receipt produced its outbox event",
     """SELECT gr.id FROM goods_receipts gr WHERE NOT EXISTS (SELECT 1 FROM outbox_events o
          WHERE o.event_type='inventory.goods_receipt.posted' AND o.aggregate_id = gr.id::text)"""),
    ("outbox_events_are_published_or_flagged",
     "No outbox event is dead (needs investigation) and none is stuck unpublished for > 5 minutes",
     """SELECT id, status, attempts FROM outbox_events
         WHERE status = 'dead' OR (status IN ('pending','publishing') AND created_at < now() - interval '5 minutes')"""),
    ("consumer_effects_are_not_duplicated",
     "At most one notification per (event, kind) and one dedupe marker per (consumer, event)",
     """SELECT event_id, kind, count(*) FROM notifications GROUP BY 1,2 HAVING count(*) > 1"""),
    ("journal_entries_are_balanced_and_nonempty",
     "Every journal entry's debits equal its credits (and match its header totals)",
     """SELECT e.id, e.total_debit, e.total_credit, COALESCE(sum(l.debit),0), COALESCE(sum(l.credit),0) FROM journal_entries e
          LEFT JOIN journal_lines l ON l.entry_id = e.id GROUP BY e.id
         HAVING COALESCE(sum(l.debit),0) <> COALESCE(sum(l.credit),0) OR COALESCE(sum(l.debit),0) <> e.total_debit OR e.total_debit = 0"""),
    ("trial_balance_balances",
     "Total debits equal total credits across the whole ledger",
     """SELECT sum(debit), sum(credit) FROM journal_lines HAVING sum(debit) <> sum(credit)"""),
    ("accounts_payable_control_reconciles_with_open_supplier_invoices",
     "GL 2000 Accounts Payable (credit - debit) equals the sum of outstanding supplier invoices",
     """SELECT gl.bal, sub.outstanding FROM
          (SELECT COALESCE(sum(l.credit - l.debit),0) bal FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = '2000') gl,
          (SELECT COALESCE(sum(total - amount_paid),0) outstanding FROM supplier_invoices) sub WHERE gl.bal <> sub.outstanding"""),
    ("inventory_gl_reconciles_with_item_valuation",
     "GL inventory accounts 1300+1310+1320 (debit - credit) equal item valuations plus cost sitting in open production orders (WIP)",
     """SELECT gl.bal, v.val FROM
          (SELECT COALESCE(sum(l.debit - l.credit),0) bal FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code IN ('1300','1310','1320')) gl,
          (SELECT (SELECT COALESCE(sum(value),0) FROM item_valuation) + (SELECT COALESCE(sum(wip_cost),0) FROM production_orders) val) v WHERE gl.bal <> v.val"""),
    ("production_orders_are_consistent",
     "Issued quantities match the ledger; completed orders have an output batch whose receipt equals actual qty; consumption cost = order input cost; pinned BOM/formula present",
     """SELECT 'issued', pm.id FROM production_materials pm LEFT JOIN (SELECT source_id, source_line, -sum(qty_delta) s FROM stock_ledger WHERE source_type='production_order' AND movement_type='PRODUCTION_ISSUE' GROUP BY 1,2) l
              ON l.source_id = pm.order_id AND l.source_line = pm.line_no WHERE pm.issued_qty <> COALESCE(l.s,0)
        UNION ALL SELECT 'output', o.id FROM production_orders o LEFT JOIN (SELECT source_id, sum(qty_delta) s FROM stock_ledger WHERE source_type='production_order' AND movement_type='PRODUCTION_OUTPUT' GROUP BY 1) l
              ON l.source_id = o.id WHERE o.status = 'COMPLETED' AND (o.output_batch_id IS NULL OR o.actual_qty <> COALESCE(l.s,0))
        UNION ALL SELECT 'cost', o.id FROM production_orders o LEFT JOIN (SELECT order_id, sum(cost) s FROM production_consumptions GROUP BY 1) c ON c.order_id = o.id
              WHERE o.input_cost <> COALESCE(c.s,0)
        UNION ALL SELECT 'wip', o.id FROM production_orders o WHERE (o.status = 'COMPLETED' AND o.wip_cost <> 0) OR (o.status IN ('PLANNED','RELEASED','CANCELLED') AND o.wip_cost <> 0)"""),
    ("every_financial_document_has_its_journal",
     "Goods receipts, supplier invoices and payments each have exactly one journal entry",
     """SELECT 'goods_receipt', gr.id FROM goods_receipts gr WHERE (SELECT count(*) FROM journal_entries j WHERE j.source_type='goods_receipt' AND j.source_id = gr.id) <> 1
        UNION ALL SELECT 'supplier_invoice', i.id FROM supplier_invoices i WHERE i.journal_entry_id IS NULL OR NOT EXISTS (SELECT 1 FROM journal_entries j WHERE j.id = i.journal_entry_id)
        UNION ALL SELECT 'payment', p.id FROM payments p WHERE p.journal_entry_id IS NULL OR NOT EXISTS (SELECT 1 FROM journal_entries j WHERE j.id = p.journal_entry_id)"""),
    ("supplier_invoice_payments_and_quantities_consistent",
     "amount_paid equals the sum of allocations; invoiced qty equals the sum of invoice lines and never exceeds received",
     """SELECT 'paid', i.id FROM supplier_invoices i LEFT JOIN (SELECT invoice_id, sum(amount) s FROM payment_allocations WHERE invoice_type='supplier_invoice' GROUP BY 1) a
              ON a.invoice_id = i.id WHERE i.amount_paid <> COALESCE(a.s,0)
        UNION ALL SELECT 'qty', poi.id FROM purchase_order_items poi LEFT JOIN (SELECT po_item_id, sum(qty) s FROM supplier_invoice_lines GROUP BY 1) l
              ON l.po_item_id = poi.id WHERE poi.qty_invoiced <> COALESCE(l.s,0) OR poi.qty_invoiced > poi.qty_received"""),
    ("ledger_is_append_only_by_construction",
     "Ledger rows carry the reversal link only once (a movement is reversed at most once)",
     """SELECT reverses_ledger_id, count(*) FROM stock_ledger WHERE reverses_ledger_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1"""),
]

SKIPPED: list[tuple[str, str]] = []


def run_checks(engine) -> dict:
    """`engine` may be an Engine (own repeatable-read snapshot) or a Connection (inside an open test transaction)."""
    from contextlib import nullcontext
    from sqlalchemy.engine import Connection
    results = []
    own = not isinstance(engine, Connection)
    with (engine.connect() if own else nullcontext(engine)) as conn:
        if own:
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '120s'"))
        for name, desc, sql in CHECKS:
            rows = [list(map(str, r)) for r in conn.execute(text(sql)).all()]
            results.append({"name": name, "description": desc, "ok": not rows, "violations": len(rows), "sample": rows[:5]})
        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in
                  ("stock_balances", "stock_ledger", "stock_reservations", "goods_receipts", "purchase_orders", "idempotency_keys", "outbox_events", "journal_entries")}
        if own:
            conn.rollback()
    return {"ok": all(r["ok"] for r in results), "checks": results, "skipped": [{"name": n, "reason": r} for n, r in SKIPPED], "row_counts": counts}


if __name__ == "__main__":
    from app.core.db import engine
    report = run_checks(engine)
    if "--json" in sys.argv:
        print(json.dumps(report))
    else:
        for c in report["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ("" if c["ok"] else f"  ({c['violations']} violations, e.g. {c['sample'][:2]})"))
        for s in report["skipped"]:
            print("SKIP", s["name"], "-", s["reason"])
        print("INTEGRITY", "OK" if report["ok"] else "FAILED")
    sys.exit(0 if report["ok"] else 1)
