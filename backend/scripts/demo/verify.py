"""Demo verification: the generic integrity suite PLUS checks that the seeded stories are what they claim to be, that dashboard figures equal values recomputed
from the base tables, and that role-based access is really enforced for the demo users."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from app.core.db import SessionLocal
from scripts.check_integrity import run_checks
from scripts.demo.core import Ctx

D = Decimal


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, bool(ok), detail))

    def eq(self, name: str, got, want, label: str = "") -> None:
        self.check(name, got == want, f"{label}got {got!r}, expected {want!r}" if got != want else str(got))


def verify_all(ctx: Ctx) -> int:
    r = Report()
    with SessionLocal() as s:
        q = lambda sql, **p: s.execute(text(sql), p).all()
        one = lambda sql, **p: s.execute(text(sql), p).scalar()

        # ---------- 1. the generic integrity suite (ledger vs balances, journals balance, documents link, ...)
        rep = run_checks(s.connection())
        for c in rep["checks"]:
            r.check(f"integrity: {c['name']}", c["ok"], "" if c["ok"] else f"{c.get('violations')} violation(s)")

        # ---------- 2. inventory and QC
        r.eq("inventory: no negative or over-reserved balance", one("SELECT count(*) FROM stock_balances WHERE qty_physical < 0 OR qty_reserved < 0 OR qty_blocked < 0 OR qty_reserved + qty_blocked > qty_physical"), 0)
        r.eq("inventory: ledger sums to the balance for every balance", one("""SELECT count(*) FROM stock_balances sb LEFT JOIN (SELECT balance_id, sum(qty_delta) q, sum(blocked_delta) b FROM stock_ledger GROUP BY 1) l ON l.balance_id = sb.id
                                                                          WHERE sb.qty_physical <> COALESCE(l.q,0) OR sb.qty_blocked <> COALESCE(l.b,0)"""), 0)
        r.eq("QC: quarantined / held / rejected batches contribute no available stock", one("""SELECT COALESCE(sum(sb.qty_available),0) FROM stock_balances sb JOIN inventory_batches b ON b.id = sb.batch_id WHERE b.qc_status <> 'RELEASED'"""), D(0))
        r.eq("QC: every dispatched unit came from a batch that was released", one("""SELECT count(*) FROM shipment_lines sl JOIN inventory_batches b ON b.id = sl.batch_id WHERE b.qc_status <> 'RELEASED'"""), 0)
        r.eq("QC: every released batch with an inspection has a DECIDED RELEASE decision", one("""SELECT count(*) FROM inventory_batches b JOIN inspections i ON i.batch_id = b.id
                                                                                          WHERE b.qc_status = 'RELEASED' AND NOT EXISTS (SELECT 1 FROM inspections j WHERE j.batch_id = b.id AND j.decision = 'RELEASE' AND j.status = 'DECIDED')"""), 0)
        r.eq("QC: the person who tested never released the same inspection", one("""SELECT count(*) FROM inspections i WHERE i.decided_by IN (SELECT tested_by FROM inspection_results WHERE inspection_id = i.id)"""), 0)
        r.check("QC: a failed-then-passed retest keeps BOTH results", one("SELECT count(*) FROM inspection_results WHERE inspection_id = :i AND test_name = 'Moisture'", i=ctx.refs["procurement_exceptions"]["retest_inspection"]) == 2)
        r.check("QC: a rejected lot has a deviation, a CAPA and a purchase return",
                one("SELECT qc_status FROM inventory_batches WHERE id = :b", b=ctx.refs["procurement_exceptions"]["vite_batch"]) == "REJECTED"
                and one("SELECT count(*) FROM capa_actions WHERE deviation_id = :d", d=ctx.refs["procurement_exceptions"]["vite_deviation"]) >= 1
                and one("SELECT count(*) FROM purchase_returns WHERE id = :i", i=ctx.refs["procurement_exceptions"]["vite_return"]) == 1)
        r.check("QC: a Certificate of Analysis exists only for RELEASE-decided inspections", one("""SELECT count(*) FROM certificates_of_analysis c JOIN inspections i ON i.id = c.inspection_id WHERE i.decision IS DISTINCT FROM 'RELEASE'""") == 0
                and one("SELECT count(*) FROM certificates_of_analysis") >= 1)

        # ---------- 3. procurement
        r.eq("procurement: no PO line is over-received or over-invoiced", one("SELECT count(*) FROM purchase_order_items WHERE qty_received > qty_ordered OR qty_invoiced > qty_received"), 0)
        r.check("procurement: partial receipt exists (PARTIALLY_RECEIVED PO)", one("SELECT count(*) FROM purchase_orders WHERE status = 'PARTIALLY_RECEIVED'") >= 1)
        r.check("procurement: the RFQ award produced a PO that points back to the winning quotation", one("""SELECT count(*) FROM rfqs r JOIN purchase_orders p ON p.id = r.awarded_po_id WHERE r.status = 'AWARDED'""") >= 1)
        r.check("procurement: an approved amendment changed the PO and its history is kept", one("SELECT count(*) FROM po_amendments a JOIN purchase_order_items i ON i.po_id = a.po_id WHERE a.status = 'APPROVED' AND i.qty_ordered = 240") >= 1)
        r.check("procurement: a late PO exists (approved, past expected date, nothing received)", one("SELECT count(*) FROM purchase_orders WHERE status = 'APPROVED' AND expected_date < business_today()") >= 1)
        r.check("procurement: a PO awaits approval", one("SELECT count(*) FROM purchase_orders WHERE status = 'PENDING_APPROVAL'") >= 1)

        # ---------- 4. R&D and production
        r.check("R&D: two formulation versions of the same formula exist, both approved", one("SELECT count(*) FROM formulation_versions WHERE formulation_id = (SELECT formulation_id FROM formulation_versions WHERE id = :v)", v=ctx.refs["rnd_to_production"]["v1"]) == 2)
        r.check("R&D: the old version's order is pinned to v1, the new one to v2 (history preserved)",
                one("SELECT formulation_version_id FROM production_orders WHERE id = :o", o=ctx.refs["rnd_to_production"]["bulk_order_v1"]) == ctx.refs["rnd_to_production"]["v1"]
                and one("SELECT formulation_version_id FROM production_orders WHERE id = :o", o=ctx.refs["rnd_to_production"]["bulk_order_v2"]) == ctx.refs["rnd_to_production"]["v2"])
        r.eq("production: yield% = actual / planned for every completed order", one("""SELECT count(*) FROM production_orders WHERE status = 'COMPLETED' AND yield_pct <> round(actual_qty * 100 / planned_qty, 2)"""), 0)
        r.eq("production: a low-yield order always has its wastage recorded", one("""SELECT count(*) FROM production_orders o WHERE status = 'COMPLETED' AND yield_pct < 95 AND NOT EXISTS (SELECT 1 FROM wastage_records w WHERE w.order_id = o.id)"""), 0)
        r.eq("production: issued materials = consumed lots for every order", one("""SELECT count(*) FROM production_materials m LEFT JOIN (SELECT order_id, item_id, sum(qty) q FROM production_consumptions GROUP BY 1,2) c
                                                                                 ON c.order_id = m.order_id AND c.item_id = m.item_id WHERE m.issued_qty <> COALESCE(c.q,0)"""), 0)
        r.eq("production: a completed order has its output batch and a BOM pin", one("SELECT count(*) FROM production_orders WHERE status = 'COMPLETED' AND (output_batch_id IS NULL OR bom_id IS NULL)"), 0)
        r.check("production: a delayed order exists (planned date passed, not completed)", one("SELECT count(*) FROM production_orders WHERE planned_date < business_today() AND status NOT IN ('COMPLETED','CANCELLED')") >= 1)
        r.check("production: a plan line converted to an order and another waits on materials", one("SELECT count(*) FROM production_plan_lines WHERE order_id IS NOT NULL") >= 1 and one("SELECT count(*) FROM production_plan_lines WHERE order_id IS NULL") >= 1)

        # ---------- 5. dispatch, partial shipments, delivery
        r.check("dispatch: an order shipped in two shipments with two invoices that sum to what shipped", q("""SELECT so_id FROM shipments GROUP BY so_id HAVING count(*) = 2""") != []
                and one("""SELECT count(*) FROM (SELECT s.so_id, sum(i.total) t FROM shipments s JOIN sales_invoices i ON i.shipment_id = s.id GROUP BY 1 HAVING count(*) = 2) x
                           JOIN sales_orders o ON o.id = x.so_id WHERE x.t <> o.total""") == 0)
        r.eq("dispatch: shipped quantity never exceeds ordered quantity", one("SELECT count(*) FROM sales_order_items WHERE qty_shipped > qty"), 0)
        r.check("dispatch: every delivered shipment is traceable through attempts", one("SELECT count(*) FROM shipments s WHERE status = 'DELIVERED' AND NOT EXISTS (SELECT 1 FROM delivery_attempts a WHERE a.shipment_id = s.id AND a.outcome = 'DELIVERED')") == 0)
        r.check("dispatch: a proof of delivery and a failed attempt are on record", one("SELECT count(*) FROM proofs_of_delivery") >= 1 and one("SELECT count(*) FROM delivery_attempts WHERE outcome = 'FAILED'") >= 1)
        r.check("dispatch: exceptions exist (delayed in transit, failed delivery)", one("SELECT count(*) FROM shipments WHERE status = 'IN_TRANSIT' AND created_at < now() - interval '5 days'") >= 1 and one("SELECT count(*) FROM shipments WHERE status = 'FAILED_DELIVERY'") >= 1)
        r.check("dispatch: work in progress for a live demo (packed, allocated, confirmed-but-blocked, draft)", all(one("SELECT count(*) FROM sales_orders WHERE status = :s", s=st) >= 1 for st in ("PACKED", "ALLOCATED", "CONFIRMED", "DRAFT")))

        # ---------- 6. accounts
        r.eq("accounts: every posted journal balances", one("SELECT count(*) FROM journal_entries WHERE total_debit <> total_credit OR total_debit <> (SELECT COALESCE(sum(debit),0) FROM journal_lines WHERE entry_id = journal_entries.id)"), 0)
        r.eq("accounts: GL payables control = unpaid supplier invoices", one("SELECT COALESCE(sum(l.credit - l.debit),0) FROM journal_lines l JOIN accounts a ON a.id = l.account_id WHERE a.code = '2000'"),
             one("SELECT COALESCE(sum(total - amount_paid - credited_amount),0) FROM supplier_invoices"))
        r.eq("accounts: sales invoice tax and totals reconcile", one("SELECT count(*) FROM sales_invoices WHERE total <> subtotal + tax_total"), 0)
        r.eq("accounts: payment allocations equal the payments they came from", one("""SELECT count(*) FROM payments p LEFT JOIN (SELECT payment_id, sum(amount) a FROM payment_allocations GROUP BY 1) x ON x.payment_id = p.id WHERE p.amount <> COALESCE(x.a,0)"""), 0)
        r.check("accounts: an overdue receivable, a half-paid payable, an unpaid payable and a credit note exist",
                one("SELECT count(*) FROM sales_invoices i JOIN customers c ON c.id = i.customer_id WHERE i.total - i.amount_paid - i.credited_amount > 0 AND business_today() > i.invoice_date + COALESCE(c.credit_days,0)") >= 1
                and one("SELECT count(*) FROM supplier_invoices WHERE status = 'PARTIAL'") >= 1 and one("SELECT count(*) FROM supplier_invoices WHERE amount_paid = 0") >= 1 and one("SELECT count(*) FROM credit_notes") >= 1)
        r.check("accounts: bank statement imported with matched and unmatched lines", one("SELECT count(*) FROM bank_transactions WHERE status = 'MATCHED'") >= 1 and one("SELECT count(*) FROM bank_transactions WHERE status = 'UNMATCHED'") >= 1)

        # ---------- 7. CRM and marketplace
        r.check("CRM: pipeline has won, open and lost opportunities and a converted customer with orders", all(one("SELECT count(*) FROM opportunities WHERE stage = :s", s=st) >= 1 for st in ("WON", "NEGOTIATION", "LOST"))
                and one("SELECT count(*) FROM leads l JOIN sales_orders o ON o.customer_id = l.converted_customer_id WHERE l.status = 'WON'") >= 1)
        r.eq("CRM: lost opportunities and lost leads all carry a reason", one("SELECT count(*) FROM opportunities WHERE stage = 'LOST' AND lost_reason IS NULL") + one("SELECT count(*) FROM leads WHERE status = 'LOST' AND lost_reason IS NULL"), 0)
        r.eq("marketplace: external order references are unique per account", one("SELECT count(*) FROM (SELECT account_id, external_order_id FROM external_orders GROUP BY 1,2 HAVING count(*) > 1) x"), 0)
        r.check("marketplace: all four platforms are connected as DEMO accounts", set(x[0] for x in q("SELECT platform FROM marketplace_accounts")) >= {"AMAZON", "FLIPKART", "MYNTRA", "NYKAA"}
                and one("SELECT count(*) FROM marketplace_accounts WHERE name NOT LIKE '[DEMO]%'") == 0 and one("SELECT count(*) FROM marketplace_accounts WHERE credentials_ref IS NOT NULL") == 0)
        r.check("marketplace: a settlement reconciles to its orders, fees and the bank credit", one("SELECT count(*) FROM marketplace_settlements WHERE net_settlement = received_amount AND payment_id IS NOT NULL") >= 1)
        r.check("marketplace: an unmapped-SKU order waits in the exception queue", one("SELECT count(*) FROM external_orders WHERE status <> 'SO_CREATED'") >= 1)

        # ---------- 8. maintenance, branding, approvals, honesty about integrations
        r.check("maintenance: completed preventive WO, an overdue one, a breakdown with downtime and spare-part use", one("SELECT count(*) FROM maintenance_work_orders WHERE kind = 'PREVENTIVE' AND status IN ('DONE','CLOSED')") >= 1
                and one("SELECT count(*) FROM maintenance_work_orders WHERE kind = 'PREVENTIVE' AND status NOT IN ('DONE','CLOSED') AND due_date < business_today()") >= 1
                and one("SELECT count(*) FROM downtime_records") >= 1 and one("SELECT count(*) FROM spare_part_usage") >= 2)
        r.check("branding: posts are recorded as MANUAL publishing (no Meta API), with engagement metrics", one("SELECT count(*) FROM publishing_records WHERE mode <> 'MANUAL'") == 0 and one("SELECT count(*) FROM publishing_records") >= 1
                and one("SELECT count(*) FROM campaign_metrics") >= 3 and one("SELECT count(*) FROM social_accounts WHERE credentials_ref IS NOT NULL") == 0)
        r.check("integrations: no courier is marked LIVE; only the in-process fake adapter is DEMO", one("SELECT count(*) FROM courier_partners WHERE integration_mode = 'LIVE'") == 0 and one("SELECT count(*) FROM courier_partners WHERE integration_mode = 'DEMO'") == 1)
        r.check("approvals/notifications: pending items exist for approvers and notifications reached users", one("SELECT count(*) FROM user_notifications") >= 10 and one("SELECT count(*) FROM purchase_requests WHERE status = 'SUBMITTED'") >= 1)
        r.check("audit: major documents have audit rows", all(one("SELECT count(*) FROM audit_logs WHERE entity = :e", e=e) >= 1 for e in ("purchase_order", "goods_receipt", "production_order", "sales_order", "purchase_return", "opportunity", "shipment")))
        r.check("outbox: events were written for postings", one("SELECT count(*) FROM outbox_events") >= 20)

        # ---------- 9. dashboards derived from real records (management summary recomputed independently)
        summ = ctx.ok(ctx.u("mgmt").get("/api/analytics/management-summary"))
        r.eq("dashboard: revenue ex-tax = sum of invoice subtotals", D(str(summ["sales"]["revenue_ex_tax"])), one("SELECT COALESCE(sum(subtotal),0) FROM sales_invoices"))
        r.eq("dashboard: receivable outstanding = sum(total - paid - credited)", D(str(summ["sales"]["receivable_outstanding"])), one("SELECT COALESCE(sum(total - amount_paid - credited_amount),0) FROM sales_invoices"))
        r.eq("dashboard: inventory value = item valuation total", D(str(summ["finance"]["inventory_value"])), one("SELECT COALESCE(sum(value),0) FROM item_valuation"))
        r.eq("dashboard: payable outstanding = unpaid supplier invoices", D(str(summ["finance"]["payable_outstanding"])), one("SELECT COALESCE(sum(total - amount_paid - credited_amount),0) FROM supplier_invoices"))
        r.eq("dashboard: orders by status match the table", {x["status"]: x["n"] for x in summ["orders_by_status"]}, {a: b for a, b in q("SELECT status, count(*) FROM sales_orders GROUP BY 1")})
        r.eq("dashboard: below-reorder items match a fresh calculation", sorted(x["item_code"] for x in summ["low_stock"]),
             sorted(a for (a,) in q("""SELECT i.item_code FROM items i LEFT JOIN stock_balances sb ON sb.item_id = i.id WHERE i.is_active AND i.reorder_level > 0 GROUP BY i.id HAVING COALESCE(sum(sb.qty_available),0) < i.reorder_level""")))
        r.eq("dashboard: open deviations match", summ["quality"]["open_deviations"], one("SELECT count(*) FROM deviations WHERE status <> 'CLOSED'"))
        r.eq("dashboard: late deliveries match", summ["procurement"]["late_deliveries"], one("SELECT count(*) FROM purchase_orders WHERE status IN ('APPROVED','PARTIALLY_RECEIVED') AND expected_date < business_today()"))

    # ---------- 10. role-based access really is enforced (HTTP, as the demo users)
    deny = [("op", "get", "/api/accounts/reports/trial-balance"), ("op", "get", "/api/procurement/purchase-orders"), ("sales", "get", "/api/accounts/reports/trial-balance"),
            ("stores", "get", "/api/crm/opportunities"), ("qi", "get", "/api/accounts/payments"), ("mgmt", "post", "/api/production/work-centres"), ("mkt", "get", "/api/inventory/balances")]
    allow = [("mgmt", "/api/analytics/management-summary"), ("acct", "/api/accounts/reports/trial-balance"), ("buyer", "/api/procurement/purchase-orders"), ("pm", "/api/production/orders"),
             ("sales", "/api/crm/opportunities"), ("disp", "/api/dispatch/orders"), ("qa", "/api/quality/inspections"), ("maint", "/api/maintenance/work-orders")]
    bad_deny = [f"{u} {m.upper()} {p}" for u, m, p in deny if getattr(ctx.u(u), m)(p, **({"json": {}} if m == "post" else {})).status_code != 403]
    bad_allow = [f"{u} GET {p}" for u, p in allow if ctx.u(u).get(p).status_code != 200]
    r.check("permissions: restricted roles get 403 on other departments' data", not bad_deny, "; ".join(bad_deny))
    r.check("permissions: each role can open its own department's data", not bad_allow, "; ".join(bad_allow))
    bad_self = ctx.u("buyer").post(f"/api/procurement/purchase-orders/{ctx.refs['procurement_baseline']['po_pending_approval']}/approve", json={"expected_version": 2})
    r.check("permissions: a purchase manager cannot approve the PO they created", bad_self.status_code in (403, 409), f"status {bad_self.status_code}")

    failed = [x for x in r.rows if not x[1]]
    print(f"Verification: {len(r.rows) - len(failed)}/{len(r.rows)} checks pass")
    for name, ok, detail in r.rows:
        if not ok:
            print(f"  FAIL  {name}  -- {detail}")
    return 1 if failed else 0
