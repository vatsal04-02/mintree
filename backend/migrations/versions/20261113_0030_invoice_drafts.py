"""customer invoice drafts: review, validate, then post

Additive only. A draft holds a preview of what a dispatch will bill; it has no journal entry, no invoice number and no revenue. Posting goes through the existing
dispatch posting. At most one open (DRAFT/VALIDATED) draft per order.

Revision ID: 0030
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa


revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('invoice_drafts',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('so_id', sa.BigInteger(), sa.ForeignKey('sales_orders.id'), nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='DRAFT'),
        sa.Column('subtotal', sa.Numeric(18, 2), nullable=False),
        sa.Column('tax_total', sa.Numeric(18, 2), nullable=False),
        sa.Column('total', sa.Numeric(18, 2), nullable=False),
        sa.Column('posted_invoice_id', sa.BigInteger(), sa.ForeignKey('sales_invoices.id'), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('validated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('posted_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('DRAFT','VALIDATED','POSTED','CANCELLED')", name='ck_invoice_drafts_status_valid'),
        sa.CheckConstraint('total = subtotal + tax_total', name='ck_invoice_drafts_total_consistent'))
    op.create_index('uq_invoice_drafts_open_order', 'invoice_drafts', ['so_id'], unique=True, postgresql_where=sa.text("status IN ('DRAFT','VALIDATED')"))
    op.create_index('ix_invoice_drafts_status', 'invoice_drafts', ['status', 'id'])
    op.create_table('invoice_draft_lines',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('draft_id', sa.BigInteger(), sa.ForeignKey('invoice_drafts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('so_item_id', sa.BigInteger(), sa.ForeignKey('sales_order_items.id'), nullable=False),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('items.id'), nullable=False),
        sa.Column('qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('unit_price', sa.Numeric(18, 4), nullable=False),
        sa.Column('tax_rate', sa.Numeric(5, 2), nullable=False),
        sa.Column('net_amount', sa.Numeric(18, 2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(18, 2), nullable=False),
        sa.CheckConstraint('qty > 0', name='ck_invoice_draft_lines_qty_positive'),
        sa.UniqueConstraint('draft_id', 'so_item_id', name='uq_invoice_draft_lines_item'))
    op.create_index('ix_invoice_draft_lines_draft', 'invoice_draft_lines', ['draft_id'])


def downgrade() -> None:
    op.drop_table('invoice_draft_lines')
    op.drop_table('invoice_drafts')
