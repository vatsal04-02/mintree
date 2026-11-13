"""demo scenario: state of the scripted order-to-cash demonstration

Additive only. One control row (like demo_live_state); it holds no business data and is empty in production. The scenario itself runs through the real services.

Revision ID: 0029
Revises: 0028
"""
from alembic import op
import sqlalchemy as sa


revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('demo_scenario',
        sa.Column('id', sa.SmallInteger(), primary_key=True),
        sa.Column('status', sa.String(10), nullable=False, server_default='idle'),
        sa.Column('step', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('speed', sa.SmallInteger(), nullable=False, server_default='1'),
        sa.Column('ctx', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('log', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('next_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('id = 1', name='ck_demo_scenario_singleton'),
        sa.CheckConstraint("status IN ('idle','running','paused','done','failed')", name='ck_demo_scenario_status'),
        sa.CheckConstraint('speed IN (1, 3)', name='ck_demo_scenario_speed'))
    op.execute("INSERT INTO demo_scenario (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table('demo_scenario')
