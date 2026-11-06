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
     "PO line qty_received equals goods-receipt line quantities minus purchase-return quantities and never exceeds qty_ordered",
     """SELECT poi.id, poi.qty_received, COALESCE(g.s,0) AS receipts_sum, COALESCE(r.s,0) AS returned_sum, poi.qty_ordered FROM purchase_order_items poi
          LEFT JOIN (SELECT po_item_id, sum(qty) s FROM goods_receipt_items GROUP BY po_item_id) g ON g.po_item_id = poi.id
          LEFT JOIN (SELECT gri.po_item_id, sum(prl.qty) s FROM purchase_return_lines prl JOIN goods_receipt_items gri ON gri.id = prl.gr_item_id GROUP BY gri.po_item_id) r ON r.po_item_id = poi.id
         WHERE poi.qty_received <> COALESCE(g.s,0) - COALESCE(r.s,0) OR poi.qty_received > poi.qty_ordered"""),
    ("purchase_returns_reconcile_with_ledger_cap_and_books",
     "Every purchase-return line has ledger rows summing to its quantity, returns never exceed the received quantity, and the return value equals its balanced journal",
     """SELECT 'line' AS kind, prl.id AS ref FROM purchase_return_lines prl JOIN purchase_returns pr ON pr.id = prl.return_id
          LEFT JOIN (SELECT source_id, source_line, sum(-qty_delta) s FROM stock_ledger WHERE source_type='purchase_return' GROUP BY 1,2) l
                 ON l.source_id = prl.return_id AND l.source_line = prl.line_no
         WHERE prl.qty <> COALESCE(l.s,0)
        UNION ALL
        SELECT 'cap', gri.id FROM goods_receipt_items gri JOIN (SELECT gr_item_id, sum(qty) s FROM purchase_return_lines GROUP BY 1) r ON r.gr_item_id = gri.id WHERE r.s > gri.qty
        UNION ALL
        SELECT 'value', pr.id FROM purchase_returns pr LEFT JOIN journal_entries e ON e.id = pr.journal_entry_id
         WHERE e.id IS NULL OR e.total_debit <> pr.total_value OR e.total_debit <> e.total_credit
            OR pr.total_value <> (SELECT COALESCE(sum(value),0) FROM purchase_return_lines WHERE return_id = pr.id)"""),
    ("crm_lead_opportunity_quotation_customer_chain_is_consistent",
     "Converted leads point at an active customer; an opportunity shares its customer with its lead and quotation; a WON opportunity's quotation is ACCEPTED or CONVERTED",
     """SELECT 'lead_customer' AS kind, l.id AS ref FROM leads l LEFT JOIN customers c ON c.id = l.converted_customer_id WHERE l.status = 'WON' AND c.id IS NULL
        UNION ALL SELECT 'opp_vs_lead', o.id FROM opportunities o JOIN leads l ON l.id = o.lead_id WHERE l.converted_customer_id IS NOT NULL AND o.customer_id IS DISTINCT FROM l.converted_customer_id
        UNION ALL SELECT 'opp_vs_quotation', o.id FROM opportunities o JOIN sales_quotations q ON q.id = o.quotation_id WHERE q.customer_id <> o.customer_id
        UNION ALL SELECT 'won_without_accepted_quote', o.id FROM opportunities o LEFT JOIN sales_quotations q ON q.id = o.quotation_id WHERE o.stage = 'WON' AND (q.id IS NULL OR q.status NOT IN ('ACCEPTED','CONVERTED'))
        UNION ALL SELECT 'stage_history_gap', o.id FROM opportunities o WHERE NOT EXISTS (SELECT 1 FROM opportunity_stage_history h WHERE h.opportunity_id = o.id AND h.to_stage = o.stage)"""),
    ("po_amendment_history_is_consistent",
     "Each PO has at most one pending amendment, decided amendments carry a decider, and approved amendments never leave a line below its received quantity",
     """SELECT 'pending' AS kind, po_id AS ref FROM po_amendments WHERE status='PENDING' GROUP BY po_id HAVING count(*) > 1
        UNION ALL SELECT 'undecided', id FROM po_amendments WHERE status <> 'PENDING' AND (decided_by IS NULL OR decided_at IS NULL)
        UNION ALL SELECT 'below_received', poi.id FROM purchase_order_items poi WHERE poi.qty_ordered < poi.qty_received"""),
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
     "GL 2000 Accounts Payable (credit - debit) equals the sum of outstanding supplier invoices (total - paid - debit notes)",
     """SELECT gl.bal, sub.outstanding FROM
          (SELECT COALESCE(sum(l.credit - l.debit),0) bal FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = '2000') gl,
          (SELECT COALESCE(sum(total - amount_paid - credited_amount),0) outstanding FROM supplier_invoices) sub WHERE gl.bal <> sub.outstanding"""),
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
    ("accounts_receivable_control_reconciles_with_open_sales_invoices",
     "GL 1200 Accounts Receivable (debit - credit) equals the sum of outstanding sales invoices (total - paid - credited)",
     """SELECT gl.bal, sub.outstanding FROM
          (SELECT COALESCE(sum(l.debit - l.credit),0) bal FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = '1200') gl,
          (SELECT COALESCE(sum(total - amount_paid - credited_amount),0) outstanding FROM sales_invoices) sub WHERE gl.bal <> sub.outstanding"""),
    ("invoice_and_credit_note_numbers_are_gapless",
     "Per fiscal year, sales-invoice and credit-note sequence numbers are exactly 1..n (statutory numbering)",
     """SELECT 'sales_invoice', fy, count(*), max(n) FROM (SELECT substring(number from 'SI/([0-9]+-[0-9]+)/') fy, substring(number from '/([0-9]+)$')::int n FROM sales_invoices) x GROUP BY fy HAVING count(*) <> max(n)
        UNION ALL SELECT 'credit_note', fy, count(*), max(n) FROM (SELECT substring(number from 'CN/([0-9]+-[0-9]+)/') fy, substring(number from '/([0-9]+)$')::int n FROM credit_notes) x GROUP BY fy HAVING count(*) <> max(n)"""),
    ("dispatch_documents_are_complete_and_consistent",
     "Each dispatched/delivered order has exactly one shipment and one invoice (equal total, with a journal); shipped qty equals ledger DISPATCH; returned qty equals return lines",
     """SELECT 'shipment', o.id FROM sales_orders o WHERE o.status IN ('DISPATCHED','DELIVERED') AND (SELECT count(*) FROM shipments s WHERE s.so_id = o.id) <> 1
        UNION ALL SELECT 'invoice', o.id FROM sales_orders o WHERE o.status IN ('DISPATCHED','DELIVERED') AND NOT EXISTS
              (SELECT 1 FROM sales_invoices i WHERE i.so_id = o.id AND i.total = o.total AND i.journal_entry_id IS NOT NULL)
        UNION ALL SELECT 'shipped', i.id FROM sales_order_items i LEFT JOIN (SELECT source_id, source_line, -sum(qty_delta) s FROM stock_ledger WHERE source_type='sales_order' AND movement_type='DISPATCH' GROUP BY 1,2) l
              ON l.source_id = i.so_id AND l.source_line = i.line_no WHERE i.qty_shipped <> COALESCE(l.s,0)
        UNION ALL SELECT 'returned', i.id FROM sales_order_items i LEFT JOIN (SELECT sl.so_item_id, sum(rl.qty) s FROM sales_return_lines rl JOIN shipment_lines sl ON sl.id = rl.shipment_line_id GROUP BY 1) r
              ON r.so_item_id = i.id WHERE i.qty_returned <> COALESCE(r.s,0)
        UNION ALL SELECT 'payments', i.id FROM sales_invoices i LEFT JOIN (SELECT invoice_id, sum(amount) s FROM payment_allocations WHERE invoice_type='sales_invoice' GROUP BY 1) a
              ON a.invoice_id = i.id WHERE i.amount_paid <> COALESCE(a.s,0)"""),
    ("marketplace_orders_and_sales_orders_agree",
     "Every SO_CREATED marketplace order points to exactly one MARKETPLACE sales order with its external ref; every MARKETPLACE sales order from an account has one external order; EXCEPTION orders have no sales order",
     """SELECT 'so_missing', e.id FROM external_orders e LEFT JOIN sales_orders o ON o.id = e.sales_order_id WHERE e.status = 'SO_CREATED' AND (o.id IS NULL OR o.channel <> 'MARKETPLACE')
        UNION ALL SELECT 'exception_has_so', e.id FROM external_orders e WHERE e.status <> 'SO_CREATED' AND e.sales_order_id IS NOT NULL
        UNION ALL SELECT 'so_without_external', o.id FROM sales_orders o JOIN marketplace_accounts a ON a.customer_id = o.customer_id
              WHERE o.channel = 'MARKETPLACE' AND o.external_ref LIKE a.platform || ':%' AND NOT EXISTS (SELECT 1 FROM external_orders e WHERE e.sales_order_id = o.id)"""),
    ("equipment_downtime_is_consistent",
     "Equipment in BREAKDOWN has exactly one open downtime record; no equipment has more than one open downtime; OPERATIONAL equipment has none",
     """SELECT 'breakdown_without_downtime', e.id FROM equipment e WHERE e.status = 'BREAKDOWN' AND NOT EXISTS (SELECT 1 FROM downtime_records d WHERE d.equipment_id = e.id AND d.ended_at IS NULL)
        UNION ALL SELECT 'operational_with_open_downtime', e.id FROM equipment e WHERE e.status = 'OPERATIONAL' AND EXISTS (SELECT 1 FROM downtime_records d WHERE d.equipment_id = e.id AND d.ended_at IS NULL)
        UNION ALL SELECT 'multiple_open', equipment_id FROM downtime_records WHERE ended_at IS NULL GROUP BY equipment_id HAVING count(*) > 1"""),
    ("content_publishing_is_consistent",
     "Published content has exactly one publishing record and an approver different from its author; no publishing record exists for unpublished content",
     """SELECT 'published_without_record', c.id FROM content_calendar c WHERE c.status = 'PUBLISHED' AND NOT EXISTS (SELECT 1 FROM publishing_records p WHERE p.content_id = c.id)
        UNION ALL SELECT 'record_without_published', p.content_id FROM publishing_records p JOIN content_calendar c ON c.id = p.content_id WHERE c.status <> 'PUBLISHED'
        UNION ALL SELECT 'self_approved', c.id FROM content_calendar c WHERE c.approved_by = c.created_by"""),
    ("supplier_debit_notes_reconcile_with_invoices",
     "Each supplier invoice's credited amount equals the sum of its debit notes; debit-note totals are subtotal + tax and gapless per fiscal year",
     """SELECT 'credited', i.id FROM supplier_invoices i LEFT JOIN (SELECT supplier_invoice_id, sum(total) s FROM debit_notes GROUP BY 1) d ON d.supplier_invoice_id = i.id
          WHERE i.credited_amount <> COALESCE(d.s, 0)
        UNION ALL SELECT 'debit_note_numbers', n FROM (SELECT substring(number from 'DN/([0-9]+-[0-9]+)/') fy, count(*) n, max(substring(number from '/([0-9]+)$')::int) mx FROM debit_notes GROUP BY 1) x WHERE n <> mx"""),
    ("bank_matches_are_consistent",
     "A matched bank transaction points to a journal line on the bank account's own ledger account with exactly the statement amount",
     """SELECT t.id FROM bank_transactions t JOIN bank_accounts b ON b.id = t.bank_account_id JOIN journal_lines l ON l.id = t.journal_line_id JOIN accounts a ON a.id = l.account_id
          WHERE t.status = 'MATCHED' AND (a.code <> b.ledger_account_code OR l.debit - l.credit <> t.amount)"""),
    ("sales_quotation_conversions_are_consistent",
     "A converted quotation links exactly one sales order; no other quotation does",
     """SELECT id FROM sales_quotations WHERE (status = 'CONVERTED') <> (sales_order_id IS NOT NULL)"""),
    ("completed_stages_have_no_failing_latest_process_check",
     "No production stage is DONE while the latest in-process reading of any of its parameters failed",
     """SELECT s.order_id, s.seq FROM production_stages s WHERE s.status = 'DONE' AND EXISTS
          (SELECT 1 FROM (SELECT DISTINCT ON (parameter) parameter, passed FROM process_checks c WHERE c.order_id = s.order_id AND c.stage_seq = s.seq ORDER BY parameter, id DESC) x WHERE NOT x.passed)"""),
    ("posted_marketplace_settlements_have_a_recorded_receipt",
     "A settlement posted to the ledger has its receipt amount recorded, and its payment when cash was received",
     """SELECT id FROM marketplace_settlements WHERE posted_at IS NOT NULL AND (received_amount IS NULL OR (received_amount > 0 AND payment_id IS NULL))"""),
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
                  ("stock_balances", "stock_ledger", "stock_reservations", "goods_receipts", "purchase_orders", "idempotency_keys", "outbox_events", "journal_entries", "sales_orders")}
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
