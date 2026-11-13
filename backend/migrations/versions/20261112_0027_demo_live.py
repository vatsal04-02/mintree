"""demo live mode: control row, event stream/log, one-shot triggers

Additive only. These tables hold no business data; they are the control plane for the optional Demo Live Mode (DEMO_LIVE) and are empty in production.

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa


revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('demo_live_state',
        sa.Column('id', sa.SmallInteger(), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('paused', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('speed', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_action_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('counters', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('anchor_date', sa.Date(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('id = 1', name='ck_demo_live_state_singleton'),
        sa.CheckConstraint('speed IN (1, 3)', name='ck_demo_live_state_speed'))
    op.execute("INSERT INTO demo_live_state (id) VALUES (1)")
    op.create_table('demo_live_events',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('type', sa.String(40), nullable=False),
        sa.Column('source', sa.String(20), nullable=False, server_default='demo-live'),
        sa.Column('permission', sa.String(80), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table('demo_live_triggers',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.String(30), nullable=False),
        sa.Column('requested_by', sa.BigInteger(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result', sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_table('demo_live_triggers')
    op.drop_table('demo_live_events')
    op.drop_table('demo_live_state')
