"""tier 1 operations: batch recall, material returns and rework, GST e-invoice records

Additive only. Recall cases hold a snapshot of what a recall touched and what was done about it; material returns and rework orders record stock that goes back
or is re-processed (the stock and ledger effects themselves go through the normal inventory and accounting postings); e-invoice rows record the provider's answer
for an invoice (IRN, acknowledgement) and whether it is SANDBOX or LIVE.

Revision ID: 0031
Revises: 0030
"""
from alembic import op
import sqlalchemy as sa


revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None

BIG, INT = sa.BigInteger, sa.Integer


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS recall_number_seq")
    op.execute("CREATE SEQUENCE IF NOT EXISTS rework_number_seq")
    op.create_table('recall_cases',
        sa.Column('id', BIG(), primary_key=True, autoincrement=True),
        sa.Column('number', sa.String(30), nullable=False, unique=True),
        sa.Column('root_batch_id', BIG(), sa.ForeignKey('inventory_batches.id'), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(10), nullable=False, server_default='MAJOR'),
        sa.Column('status', sa.String(10), nullable=False, server_default='OPEN'),
        sa.Column('summary', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_by', INT(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('closed_by', INT(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closure_note', sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('OPEN','CLOSED')", name='ck_recall_cases_status_valid'),
        sa.CheckConstraint("severity IN ('CRITICAL','MAJOR','MINOR')", name='ck_recall_cases_severity_valid'))
    op.create_index('ix_recall_cases_status', 'recall_cases', ['status', 'id'])
    op.create_table('recall_batches',
        sa.Column('id', BIG(), primary_key=True, autoincrement=True),
        sa.Column('case_id', BIG(), sa.ForeignKey('recall_cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('batch_id', BIG(), sa.ForeignKey('inventory_batches.id'), nullable=False),
        sa.Column('role', sa.String(10), nullable=False),
        sa.Column('prior_status', sa.String(12), nullable=False),
        sa.Column('held_by_recall', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('qty_physical', sa.Numeric(18, 3), nullable=False, server_default='0'),
        sa.Column('qty_reserved', sa.Numeric(18, 3), nullable=False, server_default='0'),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('SUSPECT','AFFECTED')", name='ck_recall_batches_role_valid'),
        sa.UniqueConstraint('case_id', 'batch_id', name='uq_recall_batches_case_batch'))
    op.create_index('ix_recall_batches_batch', 'recall_batches', ['batch_id'])
    op.create_table('recall_customers',
        sa.Column('id', BIG(), primary_key=True, autoincrement=True),
        sa.Column('case_id', BIG(), sa.ForeignKey('recall_cases.id', ondelete='CASCADE'), nullable=False),
        sa.Column('customer_id', INT(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('so_id', BIG(), sa.ForeignKey('sales_orders.id'), nullable=True),
        sa.Column('shipment_id', BIG(), sa.ForeignKey('shipments.id'), nullable=True),
        sa.Column('batch_id', BIG(), sa.ForeignKey('inventory_batches.id'), nullable=False),
        sa.Column('qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('disposition', sa.String(10), nullable=False, server_default='PENDING'),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notified_by', INT(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.CheckConstraint("disposition IN ('PENDING','NOTIFIED','RETURNED','CLEARED')", name='ck_recall_customers_disposition_valid'))
    op.create_index('ix_recall_customers_case', 'recall_customers', ['case_id'])

    op.create_table('material_returns',
        sa.Column('id', BIG(), primary_key=True, autoincrement=True),
        sa.Column('order_id', BIG(), sa.ForeignKey('production_orders.id'), nullable=False),
        sa.Column('item_id', INT(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('batch_id', BIG(), sa.ForeignKey('inventory_batches.id'), nullable=True),
        sa.Column('qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('cost', sa.Numeric(18, 2), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_by', INT(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('qty > 0', name='ck_material_returns_qty_positive'))
    op.create_index('ix_material_returns_order', 'material_returns', ['order_id'])

    op.create_table('rework_orders',
        sa.Column('id', BIG(), primary_key=True, autoincrement=True),
        sa.Column('number', sa.String(30), nullable=False, unique=True),
        sa.Column('source_batch_id', BIG(), sa.ForeignKey('inventory_batches.id'), nullable=False),
        sa.Column('item_id', INT(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('warehouse_id', INT(), sa.ForeignKey('warehouses.id'), nullable=False),
        sa.Column('input_qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('input_cost', sa.Numeric(18, 2), nullable=False, server_default='0'),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('process', sa.Text(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False, server_default='STARTED'),
        sa.Column('output_batch_id', BIG(), sa.ForeignKey('inventory_batches.id'), nullable=True),
        sa.Column('output_qty', sa.Numeric(18, 3), nullable=True),
        sa.Column('loss_qty', sa.Numeric(18, 3), nullable=True),
        sa.Column('added_cost', sa.Numeric(18, 2), nullable=False, server_default='0'),
        sa.Column('created_by', INT(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('STARTED','COMPLETED','CANCELLED')", name='ck_rework_orders_status_valid'),
        sa.CheckConstraint('input_qty > 0', name='ck_rework_orders_input_positive'))
    op.create_index('ix_rework_orders_source', 'rework_orders', ['source_batch_id'])
    op.create_index('ix_rework_orders_output', 'rework_orders', ['output_batch_id'])

    op.create_table('einvoices',
        sa.Column('id', BIG(), primary_key=True, autoincrement=True),
        sa.Column('invoice_id', BIG(), sa.ForeignKey('sales_invoices.id'), nullable=False, unique=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='GENERATED'),
        sa.Column('mode', sa.String(8), nullable=False, server_default='SANDBOX'),
        sa.Column('provider', sa.String(30), nullable=False),
        sa.Column('irn', sa.String(64), nullable=True),
        sa.Column('ack_no', sa.String(30), nullable=True),
        sa.Column('ack_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('signed_qr', sa.Text(), nullable=True),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('ewb_no', sa.String(20), nullable=True),
        sa.Column('ewb_valid_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_reason', sa.Text(), nullable=True),
        sa.Column('created_by', INT(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('GENERATED','CANCELLED','FAILED')", name='ck_einvoices_status_valid'),
        sa.CheckConstraint("mode IN ('SANDBOX','LIVE')", name='ck_einvoices_mode_valid'))
    op.create_index('uq_einvoices_irn', 'einvoices', ['irn'], unique=True, postgresql_where=sa.text('irn IS NOT NULL'))


def downgrade() -> None:
    for t in ('einvoices', 'rework_orders', 'material_returns', 'recall_customers', 'recall_batches', 'recall_cases'):
        op.drop_table(t)
    op.execute("DROP SEQUENCE IF EXISTS rework_number_seq")
    op.execute("DROP SEQUENCE IF EXISTS recall_number_seq")
