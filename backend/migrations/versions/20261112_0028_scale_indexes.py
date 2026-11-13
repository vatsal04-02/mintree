"""indexes found missing by profiling at full demo scale (25k orders, 10k PO lines, 54k stock movements)

Evidence (pg_stat_user_tables after the bulk load): shipment_lines 169k sequential scans reading 1.56 billion tuples; external_orders 74k sequential scans;
external_order_items 35k with no index at all; inspections 24k; purchase_order_items 11k. Each index below serves a lookup the application makes on every
dispatch, import, QC or purchase screen. Additive only; `IF NOT EXISTS` so it is safe to re-run on a database that already has them.

Revision ID: 0028
Revises: 0027
"""
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_shipment_lines_shipment", "shipment_lines (shipment_id)"),
    ("ix_shipment_lines_so_item", "shipment_lines (so_item_id)"),
    ("ix_external_orders_sales_order", "external_orders (sales_order_id) WHERE sales_order_id IS NOT NULL"),
    ("ix_external_orders_account_received", "external_orders (account_id, received_at DESC)"),
    ("ix_external_order_items_order", "external_order_items (external_order_pk)"),
    ("ix_inspections_batch", "inspections (batch_id, created_at DESC)"),
    ("ix_purchase_order_items_item", "purchase_order_items (item_id, po_id)"),
    ("ix_sales_order_items_item", "sales_order_items (item_id)"),
    ("ix_sales_invoices_customer", "sales_invoices (customer_id, invoice_date DESC)"),
    ("ix_sales_invoices_open", "sales_invoices (invoice_date) WHERE status <> 'PAID'"),
]


def upgrade() -> None:
    for name, definition in INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")


def downgrade() -> None:
    for name, _ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
