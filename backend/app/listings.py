"""Read-only list endpoints for documents that previously could only be fetched one by one. Paginated (newest first), permission-gated like their modules."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require

router = APIRouter(tags=["listings"])
DB = Depends(get_db, scope="function")

QUERIES = {
    "goods-receipts": ("/procurement/goods-receipts", "procurement.receipt:read",
        """SELECT g.id, g.number, g.status, g.delivery_note_no, g.received_at, po.number AS po_number, s.name AS supplier FROM goods_receipts g
             JOIN purchase_orders po ON po.id = g.po_id JOIN suppliers s ON s.id = g.supplier_id ORDER BY g.id DESC LIMIT :n OFFSET :o""", "goods_receipts"),
    "boms": ("/production/boms", "production.bom:read",
        """SELECT b.id, b.version_no, b.status, b.output_qty, i.item_code, i.name AS output_item, b.formulation_version_id, b.created_at FROM boms b JOIN items i ON i.id = b.output_item_id
            ORDER BY b.id DESC LIMIT :n OFFSET :o""", "boms"),
    "supplier-invoices": ("/accounts/supplier-invoices", "accounts.supplier_invoice:read",
        """SELECT i.id, i.number, i.supplier_invoice_no, i.invoice_date, i.total, i.amount_paid, i.credited_amount, i.total - i.amount_paid - i.credited_amount AS outstanding, i.status, i.supplier_id, s.name AS supplier
             FROM supplier_invoices i JOIN suppliers s ON s.id = i.supplier_id ORDER BY i.id DESC LIMIT :n OFFSET :o""", "supplier_invoices"),
    "payments": ("/accounts/payments", "accounts.payment:read",
        """SELECT p.id, p.number, p.direction, p.party_type, p.party_id, p.amount, p.payment_date, p.method, p.bank_account_code, p.reference FROM payments p ORDER BY p.id DESC LIMIT :n OFFSET :o""", "payments"),
    "returns": ("/dispatch/returns", "dispatch.return:read",
        """SELECT r.id, r.number, r.status, r.decision, r.reason, r.reverse_pickup_status, r.reverse_awb, o.number AS order_number FROM sales_returns r JOIN sales_orders o ON o.id = r.so_id
            ORDER BY r.id DESC LIMIT :n OFFSET :o""", "returns"),
    "shipments": ("/dispatch/shipments", "dispatch.shipment:read",
        """SELECT s.id, s.status, s.courier, s.awb, s.manifest_no, s.shipping_charge, s.created_at, o.number AS order_number FROM shipments s JOIN sales_orders o ON o.id = s.so_id
            ORDER BY s.id DESC LIMIT :n OFFSET :o""", "shipments"),
    "stocktakes": ("/inventory/stocktakes", "inventory.stocktake:count",
        """SELECT s.id, s.number, s.status, s.warehouse_id, w.code AS warehouse, s.created_at FROM stocktakes s JOIN warehouses w ON w.id = s.warehouse_id ORDER BY s.id DESC LIMIT :n OFFSET :o""", "stocktakes"),
    "bank-transactions": ("/accounts/bank-transactions", "accounts.bank:read",
        """SELECT t.id, t.bank_account_id, b.name AS bank, t.txn_date, t.description, t.reference, t.amount, t.status FROM bank_transactions t JOIN bank_accounts b ON b.id = t.bank_account_id
            ORDER BY (t.status = 'UNMATCHED') DESC, t.txn_date DESC, t.id DESC LIMIT :n OFFSET :o""", "bank_transactions"),
    "settlements": ("/ecommerce/settlements", "ecommerce.account:read",
        """SELECT s.id, s.reference, a.platform, a.name AS account, s.period_start, s.period_end, s.gross_sales, s.net_settlement, s.received_amount, s.posted_at,
                  CASE WHEN s.posted_at IS NOT NULL THEN 'POSTED' WHEN s.received_amount IS NOT NULL THEN 'RECEIVED' ELSE 'PENDING' END AS status
             FROM marketplace_settlements s JOIN marketplace_accounts a ON a.id = s.account_id ORDER BY s.id DESC LIMIT :n OFFSET :o""", "settlements"),
    "rnd-versions": ("/rnd/versions", "rnd.formula:read",
        """SELECT v.id, f.code, f.name, v.version_no, v.status, v.batch_size, v.revision_note, v.effective_date,
                  (SELECT count(*) FROM lab_trials t WHERE t.version_id = v.id AND t.status = 'PASSED') AS passed_trials, (SELECT count(*) FROM lab_trials t WHERE t.version_id = v.id) AS trials
             FROM formulation_versions v JOIN formulations f ON f.id = v.formulation_id ORDER BY v.id DESC LIMIT :n OFFSET :o""", "rnd_versions"),
    "rnd-trials": ("/rnd/trials", "rnd.trial:read",
        """SELECT t.id, f.code || ' v' || v.version_no AS formulation, t.trial_no, t.status, t.quantity, t.observations, t.created_at FROM lab_trials t JOIN formulation_versions v ON v.id = t.version_id
            JOIN formulations f ON f.id = v.formulation_id ORDER BY t.id DESC LIMIT :n OFFSET :o""", "rnd_trials"),
    "capa": ("/quality/capa", "quality.capa:read",
        """SELECT c.id, d.number AS deviation, c.kind, c.status, c.description, c.due_date, c.responsible_id FROM capa_actions c JOIN deviations d ON d.id = c.deviation_id ORDER BY c.id DESC LIMIT :n OFFSET :o""", "capa"),
    "contacts": ("/sales/contacts", "sales.contact:read",
        """SELECT c.id, c.customer_id, cu.name AS customer, c.name, c.role, c.phone, c.email, c.is_primary FROM customer_contacts c JOIN customers cu ON cu.id = c.customer_id ORDER BY c.id DESC LIMIT :n OFFSET :o""", "contacts"),
    "opening-stock": ("/inventory/opening-stock", "inventory.opening:post",
        """SELECT o.id, o.number, w.code AS warehouse, o.qc_status, o.total_value, o.note, o.posted_at FROM opening_stock_postings o JOIN warehouses w ON w.id = o.warehouse_id ORDER BY o.id DESC LIMIT :n OFFSET :o""", "opening_stock"),
    "sales-invoices": ("/dispatch/invoices", "accounts.sales_invoice:read",
        """SELECT i.id, i.number, i.invoice_date, i.total, i.amount_paid, i.credited_amount, i.total - i.amount_paid - i.credited_amount AS outstanding, i.status, i.customer_id, c.name AS customer
             FROM sales_invoices i JOIN customers c ON c.id = i.customer_id ORDER BY i.id DESC LIMIT :n OFFSET :o""", "sales_invoices"),
}


def _register(path: str, perm: str, sql: str, name: str):
    @router.get(path, name=f"list_{name}")
    def listing(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), db: Session = DB, _: CurrentUser = Depends(require(perm))):
        rows = db.execute(text(sql), {"n": page_size, "o": (page - 1) * page_size}).mappings().all()
        return {"items": [{k: (str(v) if hasattr(v, "quantize") else v) for k, v in dict(r).items()} for r in rows], "page": page, "page_size": page_size}
    return listing


for _path, _perm, _sql, _name in QUERIES.values():
    _register(_path, _perm, _sql, _name)
